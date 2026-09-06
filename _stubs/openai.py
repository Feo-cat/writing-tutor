"""openai SDK 的假替身——控制流测试用，0 次网络请求、0 个 key。

除了原有的 chat.completions.create，本版多了 **with_raw_response**：
真 SDK 靠它拿响应头，而 writing-tutor 需要读网关写回来的三个头
（X-GW-Model 实际作答的型号 / X-GW-Tier 档位 / X-GW-Degraded 有没有降级）。

所以这个替身必须能演两种世界，缺一不可：
  ① 有网关：响应里带 X-GW-* 头，客户端读得到「谁真答了这一题」；
  ② 直连 provider，或老版本 SDK 没有 with_raw_response：一个头都没有。
② 比 ① 更要紧——读不到头时必须报「查不了」，**绝不许当成「没降级」**。
把「不知道」记成「没问题」，是这一整套留痕机制唯一能出的致命错。

头名故意写成 `X-Gw-Model` 这种混合大小写：Go 的 http.Header.Set 会把
`X-GW-Model` 规范化成 `X-Gw-Model`，线上客户端看到的就是这个形状。
真 httpx 不区分大小写，拿 dict 硬接的代码会当场漏读——所以这里照实还原。

环境变量（只在测试里用）：
  STUB_MODE      原有：search_once / always_search
  STUB_GW        JSON：{请求的model: {"model": 实际作答的, "tier": 档位, "degraded": true}}
                 不设 = 响应里没有任何 X-GW-* 头（直连场景）
  STUB_NO_RAW=1  把 with_raw_response 整个拿掉（老版本 SDK 场景）
"""
import json
import os

_state = {"n": 0}


class _FuncCall:
    def __init__(self, args): self.name = "web_search"; self.arguments = args


class _ToolCall:
    def __init__(self, args): self.id = f"call_{_state['n']}"; self.type = "function"; self.function = _FuncCall(args)


class _Msg:
    def __init__(self, content="", tool_calls=None): self.content = content; self.tool_calls = tool_calls


class _Choice:
    def __init__(self, msg): self.message = msg; self.finish_reason = "stop"


class _Resp:
    def __init__(self, msg):
        self.choices = [_Choice(msg)]
        self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 20})()


class _Completions:
    def create(self, **kw):
        _state["n"] += 1
        mode = os.getenv("STUB_MODE", "search_once")
        has_tools = "tools" in kw
        if mode == "always_search" and has_tools:
            return _Resp(_Msg("", [_ToolCall('{"query":"q"}')]))          # 永远要搜 → 测熔断
        if mode == "search_once" and has_tools and _state["n"] == 1:
            return _Resp(_Msg("", [_ToolCall('{"query":"prompt caching"}')]))  # 先搜一次
        return _Resp(_Msg("素材简报：\n1. 事实A\n2. 事实B"))               # 收尾 / 无工具调用


def _gw_headers(model: str) -> dict:
    """按 STUB_GW 造网关响应头。没配就返回空 dict——那是直连 provider 的样子。"""
    try:
        conf = json.loads(os.getenv("STUB_GW", "") or "{}").get(model)
    except Exception:
        conf = None
    if not conf:
        return {}
    h = {"X-Gw-Model": conf.get("model", model)}       # 注意大小写：Go 规范化后就是这个形状
    if conf.get("tier"):
        h["X-Gw-Tier"] = conf["tier"]
    if conf.get("degraded"):
        h["X-Gw-Degraded"] = "true"
    return h


class _RawResp:
    """仿 openai 的 LegacyAPIResponse：.headers 拿头，.parse() 拿正常的响应对象。"""
    def __init__(self, resp, headers):
        self._resp = resp
        self.headers = headers

    def parse(self):
        return self._resp


class _RawCompletions:
    def __init__(self, inner): self._inner = inner

    def create(self, **kw):
        r = self._inner.create(**kw)
        return _RawResp(r, _gw_headers(kw.get("model", "")))


class _Chat:
    def __init__(self):
        self.completions = _Completions()
        if os.getenv("STUB_NO_RAW") != "1":
            # 老版本 SDK 没有这个属性。STUB_NO_RAW=1 就是在演那种环境——
            # 客户端必须原样跑通，只是拿不到留痕。
            self.completions.with_raw_response = _RawCompletions(self.completions)


class OpenAI:
    def __init__(self, base_url=None, api_key=None):
        self.base_url = base_url
        self.api_key = api_key
        self.chat = _Chat()
