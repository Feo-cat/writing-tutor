"""fact_checker 控制流验证（不碰真 API / key / 网络）——stub + monkeypatch 只验逻辑。
跑：python _test_hardening_fc.py

覆盖：JSON 专用守卫与阶段故障留痕 / 打分保守回落与夹取 / claim 抽取去序号 / query 改写空回落 /
     高置信不触发检索（checked=False）/ 搜索没搜成走降级（不浪费裁决调用）/
     搜索状态结构化 / 证据薄回落裸判 / 真 unverifiable / evidence_raw 留痕与截断 / 素材进裁决 /
     查询重试链 / 官方页面正文增强 / _SEARCH_MISS_PREFIXES 与检索后端真实返回值对齐 /
     采样参数默认不发。

为什么单独一个文件而不并进 _test_hardening.py：那个文件 import server，要 fastapi；
这个文件只要 stub，能在最干净的环境里跑。CI 里两个都跑。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stubs"))


sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stubs_fallback"))

# 测试只使用 _stubs 中的模型 SDK，不读取或使用个人服务配置。
os.environ["LLM_API_KEY"] = "test-only-key"
os.environ["LLM_BASE_URL"] = "https://model.example.test/v1"
os.environ["TOKEN_PARAM"] = "max_tokens"
os.environ["LLM_MIN_OUTPUT_TOKENS"] = "0"
os.environ.setdefault("LLM_MODEL", "stub-model")   # 让 TIERS 能建起来
os.environ.pop("LLM_TEMPERATURE", None)            # 采样参数测试要求初始干净
os.environ.pop("LLM_SEED", None)

import writing_assistant as wa
import fact_checker as fc

RESULTS = []
def check(name, cond):
    print(("✅" if cond else "❌"), name)
    RESULTS.append(bool(cond))


class _FR:  # 假响应对象
    def __init__(self, content, finish="stop", reasoning=""):
        self.choices = [type("C", (), {
            "message": type("M", (), {"content": content, "reasoning_content": reasoning})(),
            "finish_reason": finish,
        })()]


# ── 路由式 stub：按 system prompt 认出这是管线里哪一步，回对应的假输出 ──────────
# 顺序有讲究：FALLBACK_SYS 里有「裁决下面的陈述」但没有「裁决员」，所以先匹配
# 「裁决员」的才是 judge，落到最后的才是 bare。认错了整组测试会静默测错东西。
_ROUTE = (("置信度评估员", "score"), ("前置员", "extract"), ("改写成", "rewrite"), ("裁决员", "judge"))
_DEFAULTS = {
    "score":   '{"confidence": 0.2, "reason": "默认低置信"}',
    "extract": "陈述一\n陈述二",
    "rewrite": "默认查询词",
    "judge":   '{"verdict": "support", "reason": "证据支持"}',
    "bare":    '{"verdict": "support", "reason": "裸判支持"}',
}
CALLS = []          # [(kind, user_message), ...] —— 用来断言「哪一步被调了 / 没被调」
NO_CACHE = []       # 结构化守卫重试必须绕缓存，否则会原样取回坏响应
RESPONSE_FORMATS = []

def _kind(system: str) -> str:
    for key, kind in _ROUTE:
        if key in system:
            return kind
    return "bare"

def route(**responses):
    """装上假 _create，并清空调用记录。responses 里只写要覆盖的那几步。"""
    def _fake(model, messages, max_tokens, tools=None, no_cache=False, response_format=None):
        k = _kind(messages[0]["content"])
        CALLS.append((k, messages[1]["content"]))
        NO_CACHE.append(no_cache)
        RESPONSE_FORMATS.append(response_format)
        return _FR(responses.get(k, _DEFAULTS[k]))
    fc._create = _fake
    # _guarded_text 的替身直接委托给上面这个假 _create——于是 route() 里写的响应
    # 对「走守卫的那几步」同样生效，不必再维护第二份路由表。
    # （fact_checker 是 `from ... import` 拿到的**独立绑定**，打 fc._create 打不到
    #   writing_assistant 里那个；不这么接，extract_claims 会绕过桩去真调模型。）
    fc._guarded_text = lambda msgs, model, max_tokens, who="模型": (
        _fake(model, msgs, max_tokens).choices[0].message.content or "")
    CALLS.clear(); NO_CACHE.clear(); RESPONSE_FORMATS.clear()

def kinds():
    return [k for k, _ in CALLS]

_ORIG_CREATE, _ORIG_SEARCH, _ORIG_GUARD = fc._create, fc._web_search, fc._guarded_text
_ORIG_ENRICH = fc._enrich_search_evidence
_ORIG_DIRECT, _ORIG_FC_FETCH = fc._direct_official_evidence, fc._fetch_page_text
# 控制流单测里的「官方链接」是假的，不能因此访问公网；正文抓取在 11b 用确定性桩单测。
fc._enrich_search_evidence = lambda raw, query: (raw, False)
fc._direct_official_evidence = lambda query, claim: ("", False)


# ── 1. _loads_loose：不同 provider 的 JSON 纪律参差，得兜住 ────────────────────
check("_loads_loose 直解析", fc._loads_loose('{"a": 1}', {})["a"] == 1)
check("_loads_loose 从 markdown 代码块里抠出 JSON",
      fc._loads_loose('```json\n{"verdict": "refute"}\n```', {})["verdict"] == "refute")
check("_loads_loose 彻底解析不出 → 回落保守值",
      fc._loads_loose("这不是 JSON", {"verdict": "unverifiable"})["verdict"] == "unverifiable")
check("_loads_loose 最外层是数组 → 回落（合法 JSON 不等于符合 object schema）",
      fc._loads_loose('[{"verdict":"support"}]', {"verdict": "unverifiable"})["verdict"] == "unverifiable")


# ── 2. score_claim：解析失败必须落到 0（宁可多查一次，不可漏放一条）────────────
route(score="模型今天不讲 JSON 纪律")
_bad_score = fc.score_claim("c", "")
check("score_claim 解析失败 → confidence=0（保守触发检索）", _bad_score["confidence"] == 0.0)
check("score_claim 解析失败不再静默：stage_failures=score_unparsed",
      _bad_score["stage_failures"] == ["score_unparsed"])
check("结构化步骤失败会重试两次，且重试全部绕开缓存",
      kinds() == ["score", "score", "score"] and NO_CACHE == [False, True, True])

route(score='{"confidence": 7, "reason": "手滑写成 7"}')
check("score_claim 越界值被夹回 [0,1]", fc.score_claim("c", "")["confidence"] == 1.0)

route(score='{"confidence": "高", "reason": "给了个字符串"}')
check("score_claim 非数字置信度 → 0", fc.score_claim("c", "")["confidence"] == 0.0)

# 又空又 length，说明预算被推理吃光；这时必须加预算，不能拿同一个 1200 原地重抽。
_seq = iter([_FR("", finish="length", reasoning="推理草稿"),
             _FR('{"confidence": 0.6, "reason": "重试成功"}')])
_budgets, _flags = [], []
def _sequence_create(model, messages, max_tokens, tools=None, no_cache=False, response_format=None):
    _budgets.append(max_tokens); _flags.append(no_cache)
    return next(_seq)
fc._create = _sequence_create
_retried = fc.score_claim("c", "")
check("撞顶后加倍预算重试，拿到合法 JSON", _retried["confidence"] == 0.6
      and _budgets == [fc._SCORE_TOKENS, fc._SCORE_TOKENS * 2])
check("成功重试后不留 stage_failure，第二次调用绕缓存",
      _retried["stage_failures"] == [] and _flags == [False, True])

route(score='{"confidence": 0.6, "reason": "ok"}')
fc.score_claim("c", "")
check("JSON 分类步骤显式请求 response_format=json_object",
      RESPONSE_FORMATS == [{"type": "json_object"}])


# ── 3. extract_claims / rewrite_query ────────────────────────────────────────
route(extract="1. 第一条\n- 第二条\n\n* 第三条")
_cl = fc.extract_claims("草稿")
check("extract_claims 去掉序号/项目符号并跳空行", _cl == ["第一条", "第二条", "第三条"])

route(extract="\n".join(f"第{i}条陈述" for i in range(1, 20)))
check(f"extract_claims 截断到 MAX_CLAIMS={fc.MAX_CLAIMS}", len(fc.extract_claims("草稿")) == fc.MAX_CLAIMS)


route(extract="")
_long_draft = "这是一篇足够长的技术草稿，讲的是插件系统怎么留扩展点。" * 12
try:
    fc.extract_claims(_long_draft)
    _raised = False
except RuntimeError as _e:
    _raised = "一条断言都没抽出来" in str(_e)
check("长草稿抽出 0 条 → 抛异常（0 条在下游长得跟「全查过、没问题」一样）", _raised)
check("短草稿抽出 0 条 → 允许返回空表（它本来就可能没有事实陈述）",
      fc.extract_claims("嗯") == [])

route(rewrite="")
_long = "这是一条很长的陈述" * 10
_q, _q_failures = fc._rewrite_query(_long)
check("rewrite_query 空输出 → 回落 claim 前 40 字", _q == _long[:40])
check("query 回落不再静默：记录 rewrite_empty", _q_failures == ["rewrite_empty"])

route(rewrite='  "带引号的查询词"  ')
check("rewrite_query 剥掉引号和空白", fc.rewrite_query("c") == "带引号的查询词")


# ── 4. B1：高置信直接放行——verdict 是 support，但 checked 必须是 False ────────
# 「没查、模型看着像真的」和「查过、证据支持」在旧返回值里长得一模一样，
# 汇总时也就分不开，核查器的功劳簿里混进了它根本没核查的条目。
route(score='{"confidence": 0.9, "reason": "素材里有直接依据"}')
fc._web_search = lambda q: (_ for _ in ()).throw(AssertionError("高置信分支不该触发检索"))
_hi = fc.check_claim("一条陈述", "素材")
check("高置信 → support 且 checked=False（未验证放行）",
      _hi["verdict"] == "support" and _hi["checked"] is False)
check("高置信 → 只花一次调用（只打了分，没改写没搜没裁）", kinds() == ["score"])
check("高置信 → degraded 为空（没查不等于降级）", _hi["degraded"] == "")
check("高置信未检索 → search_status=not_run", _hi["search_status"] == "not_run")
check("高置信未检索 → 查询链为空、正文未增强",
      _hi["search_queries"] == [] and _hi["evidence_enriched"] is False)


# ── 5. 低置信 + 证据正常：完整管线，checked=True ──────────────────────────────
route(score='{"confidence": 0.2, "reason": "含版本号，数字铁律封顶"}',
      rewrite="查询词",

      judge='{"verdict": "refute", "reason": "证据显示是 Node.js",'
            ' "refute_basis": "Hexo is a fast, simple blog framework powered by Node.js"}')
_hexo_basis = "Hexo is a fast, simple blog framework powered by Node.js"
fc._web_search = lambda q: f"【官方/一手】[某标题](https://hexo.io)\n{_hexo_basis}"
fc._enrich_search_evidence = lambda raw, query: (raw, True)
_lo = fc.check_claim("Hexo 是 Python 写的")
fc._enrich_search_evidence = lambda raw, query: (raw, False)
check("低置信 → 走完 打分→改写→检索→裁决", kinds() == ["score", "rewrite", "judge"])
check("低置信裁决出 refute 且 checked=True", _lo["verdict"] == "refute" and _lo["checked"] is True)
check("判 refute 且交得出打脸原文 → 不降级", _lo["degraded"] == "")
check("A5：evidence_raw 留下了证据原文（不再只记 query）", _hexo_basis in _lo["evidence_raw"])
check("evidence 仍记 query（可审计）", "查询词" in _lo["evidence"])
check("取得正常搜索结果 → search_status=found", _lo["search_status"] == "found")
check("一次命中也留下实际查询词和正文增强标记",
      _lo["search_queries"] == ["查询词"] and _lo["evidence_enriched"] is True)

route(judge='{"verdict":"refute","reason":"PUT 是幂等的",'
            '"refute_basis":"put method is idempotent"}')
_casefold_quote = fc.judge_with_evidence(
    "PUT method is not idempotent", "【官方/一手】[RFC](https://rfc-editor.org/)\nPUT METHOD IS IDEMPOTENT.")
check("反证引用闸：只改大小写/句末标点仍认作证据原文，不误降级",
      _casefold_quote["verdict"] == "refute" and not _casefold_quote.get("unfounded_refute"))

route(judge='{"verdict":"refute","reason":"声称可重复",'
            '"refute_basis":"Repeated requests always have the same effect"}')
_paraphrased_quote = fc.judge_with_evidence(
    "PUT method is not idempotent", "【官方/一手】[RFC](https://rfc-editor.org/)\nPUT is idempotent.")
check("反证引用闸：语义相近的改写仍不能冒充原文引用",
      _paraphrased_quote["verdict"] == "unverifiable"
      and _paraphrased_quote.get("unfounded_refute") is True)


route(score='{"confidence": 0.2, "reason": "含年份和数字"}', rewrite="GPT-6 上下文窗口",
      judge='{"verdict": "refute", "reason": "搜索结果里没有任何关于 GPT-6 的信息"}',
      bare='{"verdict": "unverifiable", "reason": "尚未发生的事，无法确认"}')
fc._web_search = lambda q: "[某标题](https://x.invalid)\n讲的是别的模型"
_u = fc.check_claim("2027 年发布的 GPT-6 将支持一千万 token 的上下文窗口。")
check("#9：判假交不出打脸原文 → 不许判 refute", _u["verdict"] != "refute")
check("#9：退回 unverifiable（跟裸判一致，也就是对的那个答案）", _u["verdict"] == "unverifiable")
check("#9：留痕 degraded=unfounded_refute（降级必须看得见）",
      _u["degraded"] == "unfounded_refute")
check("#9：高风险断言不再调用裸判（裸判正是这轮把正确答案翻错的路径）", "bare" not in kinds())
check("#9：evidence 写明了为什么保守停住", "保守不裸判" in _u["evidence"])

# 闸只朝一个方向开：只会把「假」降成「查不到」，绝不会把「查不到」升成「假」。
# 判错方向要选的话，选不敢下结论，不选冤枉一句话是假的。
route(score='{"confidence": 0.2, "reason": "低"}', rewrite="q",
      judge='{"verdict": "support", "reason": "证据支持"}')
fc._web_search = lambda q: "[t](https://x.invalid)\n摘要"
_s = fc.check_claim("一条会被证据支持的陈述")
check("闸单向：support 不受 refute_basis 影响",
      _s["verdict"] == "support" and _s["degraded"] == "")


# ── 6. A4：搜索压根没搜成 —— 这是本次修的核心 ────────────────────────────────
# 旧行为：一段「[搜索失败：…]」被当证据喂给裁决员 → 裁决员按纪律判 unverifiable
# → 报告上写「证据不足」。真相是根本没搜。ddgs 一限流，V2 就悄悄退化成 V1，
# 而评测毫无察觉。新行为：认出来，直接裸判，标 degraded，并且省掉那次必然无效的裁决。
for _stub_out, _why, _status in (("[搜索失败：供应商限流]", "失败", "error"),
                                 ("[没搜到结果，换个更宽泛的查询词试试。]", "空结果", "empty")):
    route(score='{"confidence": 0.2, "reason": "低"}', rewrite="q",
          bare='{"verdict": "refute", "reason": "凭知识判断有误"}')
    fc._web_search = lambda q, _o=_stub_out: _o
    _miss = fc.check_claim("一条陈述")
    check(f"A4（{_why}）→ degraded=search_miss", _miss["degraded"] == "search_miss")
    check(f"A4（{_why}）→ 跳过裁决员，直接裸判（省一次必然无效的调用）",
          kinds() == ["score", "rewrite", "bare"])
    check(f"A4（{_why}）→ checked=False 且裸判结论生效", _miss["checked"] is False and _miss["verdict"] == "refute")
    check(f"A4（{_why}）→ search_status={_status}（后端错误与空结果在结构上分开）",
          _miss["search_status"] == _status)

route(score='{"confidence": 0.9, "reason": "模型自信但没有一手材料"}', rewrite="q",
      bare='{"verdict": "refute", "reason": "凭印象猜的"}')
fc._web_search = lambda q: "[没搜到结果，换个更宽泛的查询词试试。]"
_future_miss = fc.check_claim("Python 3.17 将彻底删除带 GIL 的构建。")
check("高风险断言即使打分员高置信也必须检索；搜索空时保守 unverifiable",
      _future_miss["verdict"] == "unverifiable" and kinds() == ["score", "rewrite"])
check("高风险搜索空仍准确留痕 search_miss / checked=False",
      _future_miss["degraded"] == "search_miss" and _future_miss["checked"] is False)
check("中文介词‘将’不误当未来：‘将字符串转换为字节’仍是普通稳定事实",
      fc._requires_strong_evidence("该函数将字符串转换为字节序列。") is False)
check("真正的未来谓词仍被硬闸识别",
      fc._requires_strong_evidence("Kubernetes 1.40 将删除 Opaque Secret。") is True)
check("版本号 + ‘任何/所有’不冒充统计高风险（#7/#46 应允许普通事实回落）",
      fc._requires_strong_evidence("Python 3.13 无需任何特殊构建。") is False
      and fc._requires_strong_evidence("RFC 5789 规定所有 PATCH 请求都必须幂等。") is False)


# ── 7. 证据薄 → 回落裸判（V2.1 既有行为，现在留痕）──────────────────────────
route(score='{"confidence": 0.2, "reason": "低"}', rewrite="q",
      judge='{"verdict": "unverifiable", "reason": "证据没提到"}',
      bare='{"verdict": "support", "reason": "凭知识属实"}')
fc._web_search = lambda q: "一些不相干的摘要"
_thin = fc.check_claim("一条陈述")
check("证据薄 → degraded=thin_evidence 且裸判结论生效",
      _thin["degraded"] == "thin_evidence" and _thin["verdict"] == "support")
check("证据薄 → checked=False（结论不是证据给的）", _thin["checked"] is False)
check("证据薄 → 四步全走（裁决过了才回落）", kinds() == ["score", "rewrite", "judge", "bare"])

route(score='{"confidence": 0.2, "reason": "未来版本"}', rewrite="q",
      judge='{"verdict": "unverifiable", "reason": "官方资料没有作出这个承诺"}',
      bare='{"verdict": "refute", "reason": "凭印象说不会"}')
fc._web_search = lambda q: "【官方/一手】[PEP](https://peps.python.org/)\n只说明当前阶段，不承诺 3.17。"
_future_thin = fc.check_claim("Python 3.17 将彻底删除带 GIL 的构建。")
check("未来断言证据不足是正确终态：unverifiable 且不裸判",
      _future_thin["verdict"] == "unverifiable" and "bare" not in kinds())
check("正常查过但不足不冒充降级：checked=True、degraded 为空",
      _future_thin["checked"] is True and _future_thin["degraded"] == "")

route(score='{"confidence": 0.2, "reason": "量化因果"}', rewrite="q",
      judge='{"verdict": "support", "reason": "一篇博客这么说"}')
fc._web_search = lambda q: "【社区二手】[博客](https://blog.csdn.net/x)\n迁移后事故平均下降 35%。"
_weak = fc.check_claim("迁移到 Redis Streams 能让所有公司的事故平均下降 35%。")
check("高风险断言只有社区二手来源 → weak_source + unverifiable",
      _weak["verdict"] == "unverifiable" and _weak["degraded"] == "weak_source")

route(score='{"confidence": 0.2, "reason": "量化因果"}', rewrite="q",
      judge='{"verdict": "support", "reason": "原始论文支持"}')
fc._web_search = lambda q: "【官方/一手】[原始研究](https://example.edu/paper)\n受控研究报告事故下降 35%。"
_primary = fc.check_claim("迁移到 Redis Streams 能让所有公司的事故平均下降 35%。")
check("高风险断言有一手来源且裁决明确 → 允许 support，不误伤",
      _primary["verdict"] == "support" and _primary["degraded"] == "")

route(score='{"confidence": 0.2, "reason": "未来版本"}', rewrite="q",
      judge='{"verdict": "refute", "reason": "当前仍是默认构建",'
            ' "refute_basis": "Python 3.16.0a0 still calls it the default GIL-enabled build"}',
      bare='{"verdict": "refute", "reason": "凭现状猜未来"}')
fc._web_search = lambda q: (
    "【官方/一手】[Python docs](https://docs.python.org/3.16/)\n"
    "Python 3.16.0a0 still calls it the default GIL-enabled build")
_premature = fc.check_claim("Python 3.16 将删除所有带 GIL 的构建。")
check("未来断言：当前状态不是未来反证 → unverifiable 且不裸判",
      _premature["verdict"] == "unverifiable" and "bare" not in kinds())
check("未来过早判假单独留痕 premature_future_refute",
      _premature["degraded"] == "premature_future_refute")

route(score='{"confidence": 0.2, "reason": "未来版本"}', rewrite="q",
      judge='{"verdict": "refute", "reason": "官方明确否定计划",'
            ' "refute_basis": "The Steering Council has no plan to remove GIL builds in Python 3.17"}')
fc._web_search = lambda q: (
    "【官方/一手】[公告](https://python.org/)\n"
    "The Steering Council has no plan to remove GIL builds in Python 3.17")
_decisive_future = fc.check_claim("Python 3.17 将删除所有带 GIL 的构建。")
check("未来断言：官方明确说 no plan 时仍允许 refute",
      _decisive_future["verdict"] == "refute" and _decisive_future["degraded"] == "")

route(score='{"confidence": 0.2, "reason": "未来产品"}', rewrite="q",
      judge='{"verdict": "refute", "reason": "二手文章称已经发布",'
            ' "refute_basis": "GPT-6 已于 2026 年正式发布"}')
fc._web_search = lambda q: (
    "【社区二手】[传闻](https://sohu.com/x)\nGPT-6 已于 2026 年正式发布\n\n"
    "【官方/一手】[OpenAI models](https://developers.openai.com/models)\nGPT-5.6")
_laundered = fc.check_claim("GPT-6 将于 2027 年发布。")
check("二手 basis 不会因列表里另有无关官方链接而被‘洗白’",
      _laundered["verdict"] == "unverifiable" and _laundered["degraded"] == "weak_source")

# 证据定不了、裸判也定不了 —— 这才是真 unverifiable，两道都问过了
route(score='{"confidence": 0.2, "reason": "低"}', rewrite="q",
      judge='{"verdict": "unverifiable", "reason": "证据没提到"}',
      bare='{"verdict": "unverifiable", "reason": "我也不确定"}')
fc._web_search = lambda q: "一些不相干的摘要"
_unv = fc.check_claim("一条陈述")
check("真 unverifiable：verdict=unverifiable 且不标降级",
      _unv["verdict"] == "unverifiable" and _unv["degraded"] == "")
check("真 unverifiable：evidence 留痕「裸判也不确定」", "裸判也不确定" in _unv["evidence"])


# ── 7b. 任一步骤自己的量尺坏了，不能伪装成正常 thin_evidence ──────────────
route(score="不是 JSON", rewrite="q", judge='{"verdict":"support","reason":"证据支持"}')
fc._web_search = lambda q: "明确证据"
_score_failed = fc.check_claim("一条陈述")
check("打分失败后即使后续裁决成功，最终结果仍带 score_unparsed",
      _score_failed["stage_failures"] == ["score_unparsed"] and _score_failed["checked"] is True)

route(score='{"confidence":0.2}', rewrite="q", judge="裁决员没有交 JSON",
      bare='{"verdict":"support","reason":"裸判支持"}')
fc._web_search = lambda q: "明确证据"
_judge_failed = fc.check_claim("一条陈述")
check("裁决员失败 → 裸判回落并标 judge_failure，不冒充 thin_evidence",
      _judge_failed["degraded"] == "judge_failure" and _judge_failed["checked"] is False
      and _judge_failed["stage_failures"] == ["judge_unparsed"])

route(score='{"confidence":0.2}', rewrite="q",
      judge='{"verdict":"unverifiable","reason":"证据不足"}', bare="裸判也没交 JSON")
fc._web_search = lambda q: "不完整证据"
_bare_failed = fc.check_claim("一条陈述")
check("裸判失败 → 标 bare_failure；不能声称两道判断都正常完成",
      _bare_failed["degraded"] == "bare_failure"
      and _bare_failed["stage_failures"] == ["bare_unparsed"])


# ── 8. B3b：素材必须进裁决员 ────────────────────────────────────────────────
# 旧管线里 material 只喂给打分，裁决时已经被丢掉了——于是「对照素材核查」实际上
# 只决定了**要不要查**，不决定**怎么判**。一条被素材直接打脸的陈述，低置信送去搜索，
# 而互联网压根不知道你的私有项目，只会回 unverifiable。核查器最该抓的那类错
# （作者写自己项目时记岔了），恰好是它结构上抓不到的。
route(score='{"confidence": 0.2, "reason": "低"}', rewrite="q",
      judge='{"verdict": "refute", "reason": "素材说是 4 档"}')
fc._web_search = lambda q: "搜到的无关内容"
fc.check_claim("模型路由一共有五档", "备忘：模型路由共 4 档 raft/sloop/galleon/ark")
_judge_user = next(u for k, u in CALLS if k == "judge")
check("B3b：素材进了裁决员的输入", "共 4 档" in _judge_user)
check("B3b：搜索证据也还在裁决员的输入里", "搜到的无关内容" in _judge_user)
_score_user = next(u for k, u in CALLS if k == "score")
check("素材同时仍进打分（原有行为未破）", "共 4 档" in _score_user)


# ── 9. A5：evidence_raw 按 cap 截断，别把日志撑爆 ────────────────────────────
route(score='{"confidence": 0.2, "reason": "低"}', rewrite="q")
fc._web_search = lambda q: "长" * (fc.EVIDENCE_RAW_CAP + 500)
_big = fc.check_claim("一条陈述")
check(f"evidence_raw 截断到 {fc.EVIDENCE_RAW_CAP} 字并标记",
      len(_big["evidence_raw"]) <= fc.EVIDENCE_RAW_CAP + 6 and _big["evidence_raw"].endswith("（截断）"))


# ── 10. 裁决员吐了非法 verdict → 收敛到 unverifiable ────────────────────────
route(judge='{"verdict": "probably_true", "reason": "自创了一个档"}')
check("judge_with_evidence 非法 verdict → unverifiable",
      fc.judge_with_evidence("c", "e")["verdict"] == "unverifiable")
route(bare='{"verdict": "yes", "reason": "自创"}')
check("bare_judge 非法 verdict → unverifiable", fc.bare_judge("c")["verdict"] == "unverifiable")


# ── 11. 接缝测试：_SEARCH_MISS_PREFIXES 必须和检索后端的真实返回值对齐 ────────
# 这是这个文件里最重要的一条。前缀是硬编码字符串，而它要匹配的那句话住在
# writing_assistant._web_search 里——两处一旦漂移，A4 就变成永不命中的死代码，
# 而且死得悄无声息：管线还是照跑，只是又开始把「没搜成」当「证据不足」了。
fc._create, fc._web_search, fc._guarded_text = _ORIG_CREATE, _ORIG_SEARCH, _ORIG_GUARD   # 用真的 _web_search（ddgs 是 stub）
os.environ["DDGS_STUB"] = "boom"
_real_fail = wa._web_search("任意查询")
os.environ["DDGS_STUB"] = "empty"
_real_empty = wa._web_search("任意查询")
os.environ["DDGS_STUB"] = "hits"
_real_hits = wa._web_search("任意查询")
check("接缝：检索异常时的真实返回值命中 _SEARCH_MISS_PREFIXES",
      _real_fail.startswith(fc._SEARCH_MISS_PREFIXES))
check("接缝：空结果时的真实返回值命中 _SEARCH_MISS_PREFIXES",
      _real_empty.startswith(fc._SEARCH_MISS_PREFIXES))
check("接缝：正常结果不会被误判成「没搜成」",
      not _real_hits.startswith(fc._SEARCH_MISS_PREFIXES) and "假摘要一" in _real_hits)
check("来源分类：官方文档优先、社区文章靠后",
      wa._source_class("https://peps.python.org/pep-0779/") == 0
      and wa._source_class("https://hexo.io/docs/") == 0
      and wa._source_class("https://blog.csdn.net/x") == 2)
_ranked = wa._rank_search_hits([
    {"href": "https://blog.csdn.net/x"}, {"href": "https://example.com/x"},
    {"href": "https://kubernetes.io/docs/x"},
])
check("来源排序稳定地把官方/一手结果提到最前",
      _ranked[0]["href"].startswith("https://kubernetes.io"))
check("搜索结果把来源等级显式交给裁决员", "【普通来源】" in _real_hits)
fc._web_search = lambda q: _real_fail
check("结构化接缝：搜索异常 → error", fc._search_evidence("q")["status"] == "error")
fc._web_search = lambda q: _real_empty
check("结构化接缝：空结果 → empty", fc._search_evidence("q")["status"] == "empty")
fc._web_search = lambda q: _real_hits
check("结构化接缝：正常摘要 → found", fc._search_evidence("q")["status"] == "found")


# ── 11b. 原查询 → 官方域 → claim 的重试链，以及官方页面正文增强 ─────────────
_attempted = []
def _retry_search(query):
    _attempted.append(query)
    if "site:rfc-editor.org" in query:
        return ("【官方/一手】[RFC 8446](https://www.rfc-editor.org/rfc/rfc8446)\n"
                "TLS 1.3 specifies its cipher suites.")
    return "[搜索失败：临时超时]"

fc._web_search = _retry_search
fc._enrich_search_evidence = lambda raw, query: (raw, "site:rfc-editor.org" in query)
_rescued = fc._search_evidence("TLS 1.3 cipher suites", "TLS 1.3 removed static RSA")
fc._enrich_search_evidence = lambda raw, query: (raw, False)
check("查询链：原查询失败后自动追加官方域偏置查询并救回",
      _rescued["status"] == "found" and len(_attempted) == 2
      and "site:rfc-editor.org" in _attempted[1])
check("查询链：实际尝试顺序完整落在结构化元数据里",
      _rescued["queries"] == _attempted)
check("官方域查询不是只加 site：TLS/DoH/HTTP 204 都补决定性标准锚点",
      "RFC 8446" in fc._official_query("TLS 1.3 static RSA", "")
      and "RFC 8484" in fc._official_query("DoH wire format", "")
      and "RFC 9110" in fc._official_query("HTTP 204 trailer", ""))

_attempted.clear()
def _primary_but_thin(query):
    _attempted.append(query)
    if "site:rfc-editor.org" in query:
        return ("【官方/一手】[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110)\n"
                "A 204 response cannot contain content or trailers.")
    return ("【官方/一手】[MDN](https://developer.mozilla.org/status/204)\n"
            "Specification: HTTP Semantics # status.204")

fc._web_search = _primary_but_thin
fc._enrich_search_evidence = lambda raw, query: (
    (raw + "\n【页面正文摘录】\nA 204 response cannot contain content or trailers.", True)
    if "site:rfc-editor.org" in query else (raw, False))
_primary_retry = fc._search_evidence("HTTP 204 trailer", "HTTP 204 cannot contain trailers")
check("官方摘要但正文抓取失败时不早停：继续官方标准查询，直到正文增强成功",
      len(_attempted) == 2 and _primary_retry["enriched"] is True
      and "RFC 9110" in _attempted[1])
fc._enrich_search_evidence = lambda raw, query: (raw, False)

_attempted.clear()
def _claim_fallback(query):
    _attempted.append(query)
    if query == "原始 claim 里有更完整的专有名词":
        return "【普通来源】[命中](https://example.com/hit)\n完整专有名词的说明"
    return "[没搜到结果，换个更宽泛的查询词试试。]"

fc._web_search = _claim_fallback
_claim_hit = fc._search_evidence("过短查询", "原始 claim 里有更完整的专有名词")
check("查询链：没有官方域提示时，最后用原始 claim 补救",
      _claim_hit["status"] == "found" and _attempted == ["过短查询", "原始 claim 里有更完整的专有名词"])

fc._web_search = lambda query: "[搜索失败：三个搜索后端都超时]"
fc._direct_official_evidence = lambda query, claim: (
    "【官方/一手】[RFC 8484](https://www.rfc-editor.org/rfc/rfc8484.txt)\n"
    "【页面正文摘录】\nThe payload is a DNS on-the-wire format message.", True)
_direct_rescue = fc._search_evidence("DoH wire format", "DoH 使用 DNS wire format")
check("搜索供应商全挂时，明确协议可由权威标准直连救回",
      _direct_rescue["status"] == "found" and _direct_rescue["enriched"] is True
      and "RFC 8484" in _direct_rescue["raw"])
fc._direct_official_evidence = lambda query, claim: ("", False)

_direct_urls = []
fc._fetch_page_text = lambda url: (_direct_urls.append(url) or (
    "The data payload is a single message of the DNS on-the-wire format defined in RFC1035.\n"
    "This second paragraph is intentionally long but irrelevant to the requested wire format definition."))
_direct_raw, _direct_ok = _ORIG_DIRECT("DoH wire format", "DoH invented an incompatible format")
check("标准直连路由：DoH 只取硬编码的一手 RFC 8484，并抽相关正文",
      _direct_ok and _direct_urls == ["https://www.rfc-editor.org/rfc/rfc8484.txt"]
      and "DNS on-the-wire format" in _direct_raw)
fc._fetch_page_text = _ORIG_FC_FETCH

_fetched = []
_orig_fetch = wa._fetch_page_text
wa._fetch_page_text = lambda url: (_fetched.append(url) or (
    "This navigation paragraph is intentionally verbose but irrelevant to the protocol semantics.\n"
    "RFC 5789 defines the PATCH method, and a PATCH request is not necessarily idempotent.\n"
    "This footer paragraph is also long enough to be considered but contains no useful query terms."))
_raw = ("【官方/一手】[RFC 5789](https://www.rfc-editor.org/rfc/rfc5789)\nPATCH method\n\n"
        "【普通来源】[Blog](https://example.com/post)\nordinary summary")
_enriched_raw, _did_enrich = wa._enrich_search_evidence(_raw, "PATCH method idempotent")
check("正文增强：只抓官方页面，并把与查询相关的关键段落补进证据",
      _did_enrich and _fetched == ["https://www.rfc-editor.org/rfc/rfc5789"]
      and "not necessarily idempotent" in _enriched_raw
      and "【页面正文摘录】" in _enriched_raw)

wa._fetch_page_text = lambda url: ""
_unchanged, _did_enrich = wa._enrich_search_evidence(_raw, "PATCH")
check("正文增强：抓取失败保留原搜索摘要，不伪报增强成功",
      _unchanged == _raw and _did_enrich is False)
wa._fetch_page_text = _orig_fetch

_orig_stream = wa.httpx.stream
wa.httpx.stream = lambda *args, **kwargs: (_ for _ in ()).throw(
    AssertionError("非 allowlist 地址不应发 HTTP 请求"))
check("正文抓取：非官方地址在网络调用前就被拒绝",
      wa._fetch_page_text("http://127.0.0.1/private") == "")
wa.httpx.stream = _orig_stream
check("RFC info 页抓取前规范化为同号纯文本，其他官方 URL 不改写",
      wa._page_fetch_url("https://www.rfc-editor.org/info/rfc9110/")
      == "https://www.rfc-editor.org/rfc/rfc9110.txt"
      and wa._page_fetch_url("https://developer.mozilla.org/en-US/docs/Web/HTTP")
      == "https://developer.mozilla.org/en-US/docs/Web/HTTP")

_parser = wa._VisibleText()
_parser.feed("<style>.secret{}</style><p>Visible protocol text for evidence extraction.</p><script>alert(1)</script>")
_visible = "".join(_parser.parts)
check("HTML 转文本：保留可见正文，丢弃 style/script",
      "Visible protocol text" in _visible and "secret" not in _visible and "alert" not in _visible)


# ── 12. A3：采样参数默认不发（不发 = 用 provider 默认 = 全仓一直以来的行为）──
_cap = {}
_orig_sdk = wa._get_client().chat.completions.create
def _capture(**kw):
    _cap.clear(); _cap.update(kw)
    return _FR("x")
wa._get_client().chat.completions.create = _capture

wa._create("m", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], 100)
check("A3：没设 LLM_TEMPERATURE 时不发 temperature 键（行为向后兼容）", "temperature" not in _cap)

os.environ["LLM_TEMPERATURE"] = "0"
os.environ["LLM_SEED"] = "42"
wa._create("m", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], 100)
check("A3：设了就发，且是惰性读的（import 之后设也生效）",
      _cap.get("temperature") == 0.0 and _cap.get("seed") == 42)
check("A3：token 上限参数名仍收敛在一处", wa._TOKEN_PARAM in _cap)
wa._create("m", [{"role": "user", "content": "u"}], 100,
           response_format={"type": "json_object"})
check("结构化调用的 response_format 能从统一出口透传到 SDK",
      _cap.get("response_format") == {"type": "json_object"})
os.environ.pop("LLM_TEMPERATURE"); os.environ.pop("LLM_SEED")
wa._get_client().chat.completions.create = _orig_sdk


print(f"\n{'='*48}\n{sum(RESULTS)}/{len(RESULTS)} 通过")
sys.exit(0 if all(RESULTS) else 1)
