"""真实 OpenAI SDK + 模拟 HTTP：检查思考模型的请求序列，不访问模型或搜索服务。

在测试机执行：uv run --locked python _test_reasoning.py
所有正文、思考字段、搜索结果和密钥均为测试夹具。
"""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
import openai
from fastapi.testclient import TestClient


MISSING = object()
THOUGHT = "\nPRIVATE-REASONING-FIXTURE\n"


def tool(call_id="call_1", query="test query", *, arguments=None, name="web_search"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps({"query": query}) if arguments is None else arguments,
    }}


def response(content="", *, reasoning=MISSING, calls=None, finish="stop"):
    message = {"role": "assistant", "content": content}
    if reasoning is not MISSING:
        message["reasoning_content"] = reasoning
    if calls is not None:
        message["tool_calls"] = calls
    return {"id": "chatcmpl-fixture", "object": "chat.completion", "created": 0,
            "model": "deepseek-v4-pro", "choices": [
                {"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}


class ReasoningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        clean = {name: "" for name in (
            "OPENAI_API_KEY", "MODEL_RAFT", "MODEL_SLOOP", "MODEL_GALLEON", "MODEL_ARK",
            "AUTHOR_PROFILE", "MAKINGOF_MATERIAL", "MAKINGOF_OUT", "GW_LEDGER",
            "GW_CACHE", "GW_SOURCE", "LLM_TEMPERATURE", "LLM_SEED",
        )}
        clean.update(LLM_API_KEY="test-only-key", LLM_BASE_URL="https://model.example.test/v1",
                     LLM_MODEL="deepseek-v4-pro", TOKEN_PARAM="max_tokens",
                     LLM_MIN_OUTPUT_TOKENS="0", CHAT_RETRY="2", CHAT_TOKENS_CAP="16000",
                     RESEARCH_MAX_ROUNDS="4", LOCAL_ARTIFACTS_DIR=self.temp.name)
        env = patch.dict(os.environ, clean)
        env.start()
        self.addCleanup(env.stop)
        for name in ("writing_assistant", "server", "makingof"):
            sys.modules.pop(name, None)
        # 即使测试机已有 .env，也不读取其中的私人配置。
        with patch("dotenv.load_dotenv"):
            self.wa = importlib.import_module("writing_assistant")
        self.assertNotEqual(Path(openai.__file__).resolve().parent.name, "_stubs")
        self.wa._GW_HEADERS_ON = True
        self.output = io.StringIO()
        stdout = contextlib.redirect_stdout(self.output)
        stdout.__enter__()
        self.addCleanup(stdout.__exit__, None, None, None)
        search = patch.object(self.wa, "_web_search", return_value="Test source snippet and link")
        self.search = search.start()
        self.addCleanup(search.stop)

    def use_responses(self, *responses):
        if self.wa.client is not None:
            self.wa.client.close()
        pending = list(responses)
        self.requests = []
        self.headers = []

        def handle(request):
            self.assertEqual(str(request.url), "https://model.example.test/v1/chat/completions")
            self.requests.append(json.loads(request.content))
            self.headers.append(request.headers)
            self.assertTrue(pending, "More model calls than the fixture allows")
            return httpx.Response(200, json=pending.pop(0))

        self.wa.client = openai.OpenAI(
            api_key="test-only-key", base_url="https://model.example.test/v1", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        )
        self.addCleanup(self.wa.client.close)

    def text(self, budget=1500):
        return self.wa._guarded_text([{"role": "user", "content": "Fixture prompt"}],
                                     "deepseek-v4-pro", budget)

    def test_multiple_search_rounds_preserve_all_reasoning(self):
        first = response(None, reasoning=THOUGHT, calls=[tool()], finish="tool_calls")
        first["choices"][0]["message"]["unrelated_provider_field"] = "not-history"
        second = response("Checking sources", reasoning="Second thought", calls=[
            tool("call_2", " query two "), tool("call_3", "query three")], finish="tool_calls")
        self.use_responses(first, second, response("Final source brief", reasoning="Final thought"))
        self.assertEqual(self.wa.research_topic("Test topic"), "Final source brief")
        history = self.requests[2]["messages"]
        assistants = [m for m in history if m["role"] == "assistant"]
        self.assertEqual([m["reasoning_content"] for m in assistants], [THOUGHT, "Second thought"])
        self.assertEqual(assistants[1]["tool_calls"], second["choices"][0]["message"]["tool_calls"])
        self.assertNotIn("unrelated_provider_field", assistants[0])
        self.assertEqual([m["tool_call_id"] for m in history if m["role"] == "tool"],
                         ["call_1", "call_2", "call_3"])
        self.assertEqual([c.args[0] for c in self.search.call_args_list],
                         ["test query", "query two", "query three"])
        self.assertNotIn(THOUGHT.strip(), self.output.getvalue())
        self.assertEqual([r["max_tokens"] for r in self.requests], [1500, 1500, 1500])

    def test_empty_reasoning_is_preserved_and_absent_field_stays_absent(self):
        for raw in (True, False):
            for reasoning in (MISSING, None, ""):
                with self.subTest(raw=raw, reasoning=repr(reasoning)):
                    self.wa._GW_HEADERS_ON = raw
                    self.use_responses(response(reasoning=reasoning, calls=[tool()]), response("Brief"))
                    self.assertEqual(self.wa.research_topic("Test"), "Brief")
                    assistant = self.requests[1]["messages"][2]
                    self.assertEqual("reasoning_content" in assistant, reasoning == "")
                    if reasoning == "":
                        self.assertEqual(assistant["reasoning_content"], "")

    def test_reasoning_only_response_retries_without_polluting_history(self):
        self.use_responses(response(reasoning=THOUGHT, finish="length"),
                           response(reasoning="Valid thought", calls=[tool()], finish="tool_calls"),
                           response("Brief"))
        self.assertEqual(self.wa.research_topic("Test"), "Brief")
        self.assertEqual(self.requests[0]["messages"], self.requests[1]["messages"])
        self.assertEqual([r["max_tokens"] for r in self.requests], [1500, 3000, 1500])
        self.assertEqual(self.headers[1]["X-GW-Cache"], "off")
        self.assertNotIn(THOUGHT.strip(), json.dumps(self.requests[2]))
        self.search.assert_called_once_with("test query")

    def test_invalid_tool_batch_never_partially_executes(self):
        bad_calls = [tool("bad", arguments="{"), tool("bad", arguments="[]"),
                     tool("bad", query=["not a string"]), tool("bad", query=" "),
                     tool("bad", name="unknown_tool"), tool("call_1"), tool("")]
        for bad in bad_calls:
            with self.subTest(bad=bad):
                self.search.reset_mock()
                failed = response(reasoning=THOUGHT, calls=[tool(), bad], finish="tool_calls")
                self.use_responses(failed, failed, failed)
                with self.assertRaises(self.wa.ModelOutputError):
                    self.wa.research_topic("Test")
                self.assertEqual(len(self.requests), 3)
                self.assertTrue(all(len(r["messages"]) == 2 for r in self.requests))
                self.search.assert_not_called()

    def test_truncated_tool_arguments_are_not_executed_even_if_json_is_valid(self):
        self.use_responses(response(calls=[tool(query="partial query")], finish="length"),
                           response(calls=[tool(query="complete query")], finish="tool_calls"),
                           response("Brief"))
        self.assertEqual(self.wa.research_topic("Test"), "Brief")
        self.search.assert_called_once_with("complete query")
        self.assertNotIn("partial query", json.dumps(self.requests[2]))

    def test_search_round_limit_forces_text_summary(self):
        self.wa.RESEARCH_MAX_ROUNDS = 2
        self.use_responses(response(reasoning=THOUGHT, calls=[tool()]),
                           response(reasoning="Second thought", calls=[tool("call_2")]),
                           response("Summary"))
        self.assertEqual(self.wa.research_topic("Test"), "Summary")
        self.assertEqual(self.search.call_count, 2)
        self.assertNotIn("tools", self.requests[-1])
        self.assertEqual([m["reasoning_content"] for m in self.requests[-1]["messages"]
                          if m["role"] == "assistant"], [THOUGHT, "Second thought"])

    def test_text_never_delivers_thoughts_truncation_or_failed_finish(self):
        failures = [response(reasoning=THOUGHT), response("Partial body", finish="length"),
                    response("Filtered fragment", finish="content_filter"), response()]
        for failed in failures:
            with self.subTest(response=failed):
                self.use_responses(failed, failed, failed)
                with self.assertRaises(self.wa.ModelOutputError) as error:
                    self.text()
                self.assertEqual(len(self.requests), 3)
                self.assertEqual(self.requests[0]["messages"], self.requests[2]["messages"])
                self.assertNotIn(THOUGHT.strip(), self.wa.public_error(error.exception))
        self.assertNotIn(THOUGHT.strip(), self.output.getvalue())
        self.search.assert_not_called()

    def test_text_recovery_keeps_only_complete_content(self):
        self.use_responses(response(reasoning=THOUGHT), response("Partial", finish="length"),
                           response("Complete body", reasoning=THOUGHT))
        self.assertEqual(self.text(), "Complete body")
        self.assertEqual([r["max_tokens"] for r in self.requests], [1500, 3000, 6000])

    def test_editor_failure_keeps_author_answers(self):
        failed = response("Partial model body", reasoning=THOUGHT, finish="length")
        self.use_responses(failed, failed, failed)
        records = [{"lesson": "Fixture lesson", "q": "Fixture question", "a": answer}
                   for answer in ("Author line one\nAuthor line two", "Another author answer")]
        body = self.wa.edit_section({"title": "Fixture section"}, records)
        self.assertEqual(body, "\n\n".join(r["a"] for r in records))
        self.assertIn("已保留作者原回答", self.output.getvalue())
        self.assertNotIn(THOUGHT.strip(), body + self.output.getvalue())

    def test_output_floor_and_retry_cap_apply_to_research_and_text(self):
        os.environ["LLM_MIN_OUTPUT_TOKENS"] = "4096"
        for research in (True, False):
            with self.subTest(research=research):
                failed = response(reasoning=THOUGHT, finish="length")
                self.use_responses(failed, failed, failed)
                with self.assertRaises(self.wa.ModelOutputError):
                    self.wa.research_topic("Test") if research else self.text(1000)
                self.assertEqual([r["max_tokens"] for r in self.requests], [4096, 8192, 16000])
        self.use_responses(response("Body"))
        self.assertEqual(self.text(20000), "Body")
        self.assertEqual(self.requests[0]["max_tokens"], 16000)
        self.search.assert_not_called()

    def test_invalid_output_floor_blocks_before_model_request(self):
        self.use_responses()
        for value in ("-1", "16001", "many", "1.5"):
            with self.subTest(value=value):
                os.environ["LLM_MIN_OUTPUT_TOKENS"] = value
                self.assertFalse(self.wa.configuration_status()["ready"])
                with self.assertRaises(self.wa.ConfigurationError):
                    self.text()
        for value in ("", "0"):
            os.environ["LLM_MIN_OUTPUT_TOKENS"] = value
            self.assertTrue(self.wa.configuration_status()["ready"])
        self.assertEqual(self.requests, [])

    def test_failed_research_is_visible_on_web_and_does_not_save_material(self):
        failed = response(reasoning=THOUGHT, finish="length")
        self.use_responses(failed, failed, failed)
        server = importlib.import_module("server")
        with TestClient(server.app) as client, patch.object(self.wa, "plan_outline") as planner:
            result = client.post("/api/start", json={"topic": "Test failed research"})
            self.assertEqual(result.status_code, 200)
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
            self.assertEqual(events[-1]["type"], "end")
            error = next(e for e in events if e["type"] == "error")
            self.assertIn("有限重试", error["message"])
            self.assertNotIn(THOUGHT.strip(), result.text + self.output.getvalue())
            self.assertEqual(client.get("/api/archive").json(), {"posts": []})
            planner.assert_not_called()
        self.assertEqual(list(Path(self.temp.name).rglob("*.md")), [])
        self.assertEqual(len(self.requests), 3)
        self.search.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
