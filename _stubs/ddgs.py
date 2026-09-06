"""ddgs 的假替身——只为 stub 测试存在，让 writing_assistant 的 `from ddgs import DDGS` 能 import 成功。

为什么要有这个：核查器测试要在 CI 里 0 次网络、0 次 API 地跑完。真装 ddgs 也行，
但那样 CI 就依赖一个会限流的第三方站点——门禁一随机变红，人就不看门禁了。
真正的检索行为在测试里是直接 monkeypatch `fact_checker._web_search` 的，
这个文件只负责让 import 不炸。

行为由 DDGS_STUB 环境变量控制：
    hits（默认）—— 返回两条假结果
    empty       —— 返回空列表（触发 _web_search 的「[没搜到结果…]」分支）
    boom        —— 抛异常（触发 _web_search 的「[搜索失败…]」分支）
"""
import os


class DDGS:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def text(self, query, max_results=5, **kw):
        mode = os.getenv("DDGS_STUB", "hits")
        if mode == "boom":
            raise RuntimeError("stub: 供应商限流")
        if mode == "empty":
            return []
        return [
            {"title": f"假结果1 · {query}", "href": "https://example.invalid/1", "body": "假摘要一。"},
            {"title": f"假结果2 · {query}", "href": "https://example.invalid/2", "body": "假摘要二。"},
        ][:max_results]
