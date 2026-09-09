"""Web HTTP regression checks using the real framework and a fake model SDK.

Run in the test environment with: uv run python _test_web.py
No real credentials, model calls or search requests are used.
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "_stubs"))
from fastapi.testclient import TestClient


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        clean = {name: "" for name in (
            "LLM_API_KEY", "OPENAI_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
            "MODEL_RAFT", "MODEL_SLOOP", "MODEL_GALLEON", "MODEL_ARK",
            "AUTHOR_PROFILE", "MAKINGOF_MATERIAL", "MAKINGOF_OUT", "GW_LEDGER",
            "LLM_TEMPERATURE", "LLM_SEED", "LLM_MIN_OUTPUT_TOKENS",
            "LLM_CONNECT_TIMEOUT_SECONDS",
        )}
        clean.update(LOCAL_ARTIFACTS_DIR=self.temp.name, TOKEN_PARAM="max_tokens")
        self.env = patch.dict(os.environ, clean)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("writing_assistant", "server", "makingof"):
            sys.modules.pop(name, None)
        self.wa = importlib.import_module("writing_assistant")
        self.server = importlib.import_module("server")
        self.client = TestClient(self.server.app)
        self.addCleanup(self.client.close)
        self.assertEqual(Path(sys.modules["openai"].__file__).resolve().parent, HERE / "_stubs")

    def configured(self):
        os.environ["LLM_API_KEY"] = "test-only-key-never-return-this"
        os.environ["LLM_BASE_URL"] = "https://model.example.test/v1"
        self.wa.TIERS.update({name: "test-model" for name in self.wa.TIERS})

    def events(self, path, body):
        response = self.client.post(path, json=body)
        self.assertEqual(response.status_code, 200)
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def saved_post(self):
        path = Path(self.wa.blog_artifact_path("sample.md"))
        path.write_text("# Sample\n\n## Section\n\nSample text\n", encoding="utf-8")
        return path

    def test_empty_configuration_opens_and_blocks_calls(self):
        self.assertIsNone(self.wa.client)
        with patch.object(self.wa, "OpenAI", side_effect=AssertionError("Unexpected SDK construction")) as factory:
            response = self.client.get("/api/config")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["ready"])
            self.assertEqual(len(response.json()["checks"]), 3)
            self.assertEqual(self.client.get("/api/archive").json(), {"posts": []})
            events = self.events("/api/start", {"topic": "Configuration check"})
            self.assertEqual([e["type"] for e in events], ["error", "end"])
            self.assertEqual(self.server.SESSIONS, {})
            self.assertFalse(self.client.post("/api/critique", json={"draft": "Test draft"}).json()["ok"])
            factory.assert_not_called()

    def test_legacy_demo_parameter_is_rejected_before_billing(self):
        self.configured()
        with patch.object(self.wa, "research_topic") as research:
            for value in (True, False):
                response = self.client.post("/api/start", json={"topic": "Old client", "stub": value})
                self.assertEqual(response.status_code, 422)
            research.assert_not_called()
            self.assertEqual(self.server.SESSIONS, {})

    def test_configuration_is_private_and_client_is_lazy(self):
        self.configured()
        config = self.client.get("/api/config")
        self.assertTrue(config.json()["ready"])
        self.assertNotIn(os.environ["LLM_API_KEY"], config.text)
        self.assertNotIn(os.environ["LLM_BASE_URL"], config.text)
        self.assertIsNone(self.wa.client)
        factory = self.wa.OpenAI
        with patch.object(self.wa, "OpenAI", wraps=factory) as create:
            for _ in range(2):
                result = self.wa._create("test-model", [{"role": "user", "content": "Test fixture"}], 20)
                self.assertTrue(result.choices)
            create.assert_called_once_with(base_url="https://model.example.test/v1", api_key=os.environ["LLM_API_KEY"])

    def test_invalid_config_and_empty_topic_do_not_call_model(self):
        self.configured()
        with patch.object(self.wa, "research_topic") as research:
            events = self.events("/api/start", {"topic": "  "})
            self.assertEqual(events[0]["type"], "error")
            for url in ("invalid-url", "https://user:private-marker@example.test/v1", "http://localhost:bad/v1"):
                os.environ["LLM_BASE_URL"] = url
                response = self.client.get("/api/config")
                self.assertFalse(response.json()["ready"])
                self.assertNotIn("private-marker", response.text)
                self.assertEqual(self.events("/api/start", {"topic": "Test"})[0]["type"], "error")
            os.environ["LLM_BASE_URL"] = ""
            self.wa._TOKEN_PARAM = "unsupported"
            self.assertFalse(self.client.get("/api/config").json()["ready"])
            research.assert_not_called()

    def test_optional_tiers_and_missing_makingof_material(self):
        self.configured()
        self.wa.TIERS["raft"] = self.wa.TIERS["ark"] = ""
        self.assertTrue(self.client.get("/api/config").json()["ready"])
        self.server.MAKINGOF_MATERIAL = str(Path(self.temp.name) / "missing-material.md")
        config = self.client.get("/api/config").json()
        self.assertFalse(config["ready"])
        self.assertEqual(config["mode"], "makingof")
        self.assertEqual(self.events("/api/start", {"topic": "Test"})[0]["type"], "error")
        self.assertIsNone(self.wa.client)

    def test_real_web_flow_saves_answers_and_uses_replanned_outline(self):
        self.configured()
        first = [{"title": "Original", "points": "Point"}]
        revised = [{"title": "Section one", "points": "One"}, {"title": "Section two", "points": "Two"}]
        script = iter(["讲：Lesson one\n问：Question one?", self.wa.DONE_TAG,
                       "讲：Lesson two\n问：Question two?", self.wa.DONE_TAG])

        def tutor(messages):
            text = next(script)
            messages.append({"role": "assistant", "content": text})
            return text

        with patch.object(self.wa, "research_topic", return_value="Test research material") as research, \
             patch.object(self.wa, "plan_outline", side_effect=[first, revised]) as plan, \
             patch.object(self.wa, "tutor_turn", side_effect=tutor) as teacher, \
             patch.object(self.wa, "edit_section", side_effect=lambda section, records: records[0]["a"]), \
             patch.object(self.wa, "critique", return_value="Test review") as critic:
            events = self.events("/api/start", {"topic": "Test article"})
            meta = next(e for e in events if e["type"] == "meta")
            sid = meta["sessionId"]
            self.assertTrue(any(e["type"] == "awaiting_outline" for e in events))
            teacher.assert_not_called()
            events = self.events("/api/replan", {"sessionId": sid, "feedback": "Use two concrete sections"})
            self.assertEqual(plan.call_args.args[2:], ("Use two concrete sections", first))
            self.assertEqual(next(e for e in events if e["type"] == "meta")["outline"], [s["title"] for s in revised])
            self.assertEqual(research.call_count, 1)
            events = self.events("/api/begin", {"sessionId": sid})
            self.assertTrue(any(e["type"] == "lesson" for e in events))
            self.events("/api/answer", {"sessionId": sid, "answer": "Test answer one\nSecond line"})
            events = self.events("/api/answer", {"sessionId": sid, "answer": "Test answer two"})
            done = next(e for e in events if e["type"] == "done")
            self.assertIn("Test answer one\nSecond line", done["draft"])
            self.assertIn("Test answer two", done["draft"])
            self.assertEqual(done["review"], "Test review")
            self.assertEqual(critic.call_args.kwargs["outline"], revised)
            self.assertEqual(teacher.call_count, 4)
        posts = self.client.get("/api/archive").json()["posts"]
        self.assertEqual(len(posts), 1)
        detail = self.client.get(f"/api/archive/{posts[0]['slug']}").json()
        self.assertIn("Test answer one\nSecond line", detail["parts"]["transcript"])
        self.assertEqual(detail["receipt"]["ledger_status"], "not_configured")
        self.assertIsNone(detail["receipt"]["cost"])
        self.assertEqual(detail["receipt"]["rounds"], 2)

    def test_ledger_states_distinguish_unknown_and_zero_cost(self):
        self.saved_post()
        def receipt():
            return self.client.get("/api/archive/sample").json()["receipt"]
        self.assertEqual(receipt()["ledger_status"], "not_configured")
        ledger = Path(self.temp.name)/"ledger.jsonl"
        self.wa.GW_LEDGER = str(ledger)
        self.assertEqual(receipt()["ledger_status"], "unavailable")
        ledger.write_text("", encoding="utf-8")
        self.assertEqual(receipt()["ledger_status"], "no_records")
        row = {"source": f"web-{self.wa.post_key('Sample')}", "model": "test-model"}
        ledger.write_text(json.dumps(row)+"\n", encoding="utf-8")
        self.assertEqual(receipt()["ledger_status"], "available")
        self.assertIsNone(receipt()["cost"])
        row["cost"] = 0
        ledger.write_text(json.dumps(row)+"\n", encoding="utf-8")
        self.assertEqual(receipt()["cost"], 0)
        self.assertEqual(receipt()["by_model"]["test-model"]["cost"], 0)

    def test_provider_error_does_not_echo_raw_response(self):
        self.configured()
        error_type = type("AuthenticationError", (Exception,), {})
        output = io.StringIO()
        with patch.object(self.wa, "research_topic", side_effect=error_type("private-provider-response")), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            events = self.events("/api/start", {"topic": "Test error"})
        message = next(e["message"] for e in events if e["type"] == "error")
        self.assertIn("密钥", message)
        self.assertNotIn("private-provider-response", json.dumps(events)+output.getvalue())
        self.assertEqual(events[-1]["type"], "end")


if __name__ == "__main__":
    unittest.main(verbosity=2)
