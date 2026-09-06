"""fact_checker.py —— 写作助手 V2 · CRAG 简化版事实核查器

简化版=既定降级路线：置信度打分 + query 改写 + web search 兜底，**砍 multi-hop**。
流程（每条 claim 走一遍）：
    拆 claim → 置信度打分（对照素材）
        → 高置信（≥ 阈值）：直接放行，来源=素材
        → 低置信：query 改写 → ddgs 搜索（复用研究员的检索函数）→ 依据证据裁决
产出：每条 claim 一个裁决 {claim, confidence, verdict, evidence, reason,
                          checked, degraded, evidence_raw, search_status,
                          search_queries, evidence_enriched, stage_failures}
    verdict ∈ support（属实）/ refute（有误）/ unverifiable（查证不到）
    checked  = 结论是否基于检索到的外部证据（区分「查过」与「没查、看着像真的」）
    degraded = 业务回落路径：search_miss / thin_evidence / weak_source / unfounded_refute /
               premature_future_refute / judge_failure / bare_failure / ""（正常）。
    stage_failures = 哪个 LLM 步骤没产出合法结构；它是工具故障，不和正常业务回落混桶。
    search_status = not_run / found / empty / error；没有信息和后端错误在结构上分开。
    search_queries = 本次实际尝试过的查询词；用于区分「一次命中」和「重试后救回」。
    evidence_enriched = 是否成功把官方页面正文摘录补进搜索摘要。


开发命令：uv run python fact_checker.py 草稿路径 [素材路径]"""

import json
import os
import re
import sys
import unicodedata

from writing_assistant import (
    _create, _enrich_search_evidence, _fetch_page_text, _guarded_text,
    _relevant_excerpt, TIERS, _web_search,
)

CONF_THRESHOLD = 0.7   # 置信度阈值：低于它才触发检索兜底（调参点，eval 时可扫 0.5~0.9）
MAX_CLAIMS = 12        # 单篇最多核查的 claim 数（控成本；超出取前 N 条）
EVIDENCE_RAW_CAP = int(os.getenv("FC_EVIDENCE_RAW_CAP", "6000"))


_STAGE_RETRY = int(os.getenv("FC_STAGE_RETRY", "2"))
_STAGE_TOKENS_CAP = int(os.getenv("FC_STAGE_TOKENS_CAP", "8000"))
_SCORE_TOKENS = int(os.getenv("FC_SCORE_TOKENS", "1200"))
_REWRITE_TOKENS = int(os.getenv("FC_REWRITE_TOKENS", "3200"))
_JUDGE_TOKENS = int(os.getenv("FC_JUDGE_TOKENS", "4000"))
_BARE_TOKENS = int(os.getenv("FC_BARE_TOKENS", "2400"))

# run_eval 会把这里覆盖成 EVAL_SUT_MODEL。日常 fact_checker 仍走 sloop 档，接口不变；
# 评测则能发具体型号，避开网关档位的自动降级链。
CHECK_MODEL = os.getenv("FC_MODEL") or TIERS["sloop"]

# 检索后端在「失败」和「空结果」时不抛异常，而是把提示语当正常返回值吐回来
# （writing_assistant._web_search 的设计）——这在研究员的 ReAct 循环里是对的：
# 模型看到「换个查询词」就自己换词再搜，循环不崩。但在核查器里是错的：这段提示语
# 会被当成「搜索证据」读进裁决员，裁决员按纪律判 unverifiable，最后写在报告上的是
# 「证据不足」——而真相是**根本没搜成**。查了没查到 vs 压根没查，是两件事；
# 混在一起，ddgs 一限流就能让 V2 悄悄退化成 V1，而评测毫无察觉。
# 复用的是函数，没复用的是语境——所以差异在调用方这一侧补，别去动研究员那边。
_SEARCH_MISS_PREFIXES = ("[搜索失败", "[没搜到")
_VERDICTS = ("support", "refute", "unverifiable")
_FUTURE_MARKERS = (
    "承诺", "计划", "预计", "拟于", "路线图", "未来", "尚未发布",
    " will ", "roadmap", "planned", "expected to",
)
_FUTURE_JIANG = re.compile(r"将(?:在|于|把|被|会|支持|删除|取消|发布|成为|设为|彻底)")
_STATISTICAL_MARKERS = (
    "平均", "约有", "约占", "占比", "比例", "百分之", "%", "多数", "大多数",
    "超过", "至少", "至多", " on average ", "percent", "percentage", "majority",
)
_CAUSAL_MARKERS = (
    "导致", "使得", "能让", "会让", "提升", "下降", "减少", "增加", "改善", "降低",
    "cause", "increase", "decrease", "reduce", "improve",
)
_PRIMARY_EVIDENCE_MARKER = "【官方/一手】"
_OFFICIAL_QUERY_HINTS = (
    (re.compile(r"\bddgs\b", re.I), "site:pypi.org/project/ddgs DDGS text"),
    (re.compile(r"\bopenai\b|function calling", re.I),
     "site:developers.openai.com function calling parallel tool calls"),
    (re.compile(r"\bhexo\b", re.I), "site:hexo.io Node.js"),
    (re.compile(r"\bhttp\b.*\b204\b|\b204\b.*\bhttp\b", re.I),
     "site:rfc-editor.org RFC 9110 204 cannot contain content trailers"),
    (re.compile(r"\bhttp\b.*\bput\b|\bput\b.*\bhttp\b", re.I),
     "site:rfc-editor.org RFC 9110 PUT idempotent"),
    (re.compile(r"\bhttp\b.*\bpatch\b|\brfc\s*5789\b", re.I),
     "site:rfc-editor.org RFC 5789 PATCH safe idempotent"),
    (re.compile(r"\btls\s*1[.]3\b|\brfc\s*8446\b", re.I),
     "site:rfc-editor.org RFC 8446 static RSA Diffie-Hellman removed AEAD"),
    (re.compile(r"\bpkce\b|\brfc\s*7636\b", re.I),
     "site:rfc-editor.org RFC 7636 code_verifier authorization token request"),
    (re.compile(r"\bdoh\b|\brfc\s*8484\b", re.I),
     "site:rfc-editor.org RFC 8484 application/dns-message DNS wire format RFC1035"),
    (re.compile(r"\brfc\s*\d+|\boauth\b", re.I), "site:rfc-editor.org"),
    (re.compile(r"\bpython\b|\bgil\b|free-thread", re.I), "site:docs.python.org"),
    (re.compile(r"\bkubernetes\b|\bk8s\b", re.I), "site:kubernetes.io"),
    (re.compile(r"\bredis\b", re.I), "site:redis.io"),
    (re.compile(r"\bpostgres(?:ql)?\b", re.I), "site:postgresql.org"),
    (re.compile(r"\bsqlite\b", re.I), "site:sqlite.org"),
)
_DIRECT_OFFICIAL_SOURCES = (
    (re.compile(r"\bhttp\b.*\b204\b|\b204\b.*\bhttp\b", re.I),
     "RFC 9110 · HTTP Semantics", "https://www.rfc-editor.org/rfc/rfc9110.txt",
     "204 response cannot contain content trailers"),
    (re.compile(r"\bhttp\b.*\bput\b|\bput\b.*\bhttp\b", re.I),
     "RFC 9110 · HTTP Semantics", "https://www.rfc-editor.org/rfc/rfc9110.txt",
     "PUT idempotent method"),
    (re.compile(r"\bhttp\b.*\bpatch\b|\brfc\s*5789\b", re.I),
     "RFC 5789 · PATCH Method", "https://www.rfc-editor.org/rfc/rfc5789.txt",
     "PATCH neither safe nor idempotent can be issued idempotent"),
    (re.compile(r"\btls\s*1[.]3\b|\brfc\s*8446\b", re.I),
     "RFC 8446 · TLS 1.3", "https://www.rfc-editor.org/rfc/rfc8446.txt",
     "static RSA Diffie-Hellman cipher suites removed AEAD record protection"),
    (re.compile(r"\bpkce\b|\brfc\s*7636\b", re.I),
     "RFC 7636 · PKCE", "https://www.rfc-editor.org/rfc/rfc7636.txt",
     "code_verifier authorization request token request code_challenge"),
    (re.compile(r"\bdoh\b|\brfc\s*8484\b", re.I),
     "RFC 8484 · DNS over HTTPS", "https://www.rfc-editor.org/rfc/rfc8484.txt",
     "application/dns-message DNS on-the-wire format RFC1035"),
)
_FUTURE_REFUTE_DECISIVE = re.compile(
    r"不会|不再|没有.{0,10}计划|无.{0,10}计划|取消.{0,10}(?:计划|发布)|"
    r"明确.{0,10}(?:拒绝|否定)|已(?:经)?(?:正式)?发布|现已发布|"
    r"will\s+not|won't|no\s+plans?|not\s+planned|will\s+remain|will\s+continue|"
    r"(?:was|has\s+been)\s+(?:officially\s+)?released|officially\s+launched",
    re.IGNORECASE,
)


def _is_future_claim(claim: str) -> bool:
    text = f" {claim.lower()} "
    return bool(any(marker in text for marker in _FUTURE_MARKERS) or _FUTURE_JIANG.search(text))


def _future_refute_is_decisive(basis: str) -> bool:
    """未来断言只能被明确的未来否定，或已经发生的官方事实推翻。

    “当前仍是 X / 尚未宣布 Y”不在白名单里：它描述的是现状，不是未来不会变化的证据。
    """
    return bool(_FUTURE_REFUTE_DECISIVE.search(basis or ""))


def _requires_strong_evidence(claim: str) -> bool:
    """未来路线图、全称统计、量化因果不能靠模型记忆或二手摘要定论。

    这是一个刻意偏窄的硬闸，不试图理解所有自然语言：它只拦真实 eval 已暴露的
    高风险形态，避免把 HTTP 状态码、TLS 版本号等稳定技术事实一并变成全都不敢判。
    """
    text = f" {claim.lower()} "
    has_number = bool(re.search(r"\d|[一二三四五六七八九十百千万亿]+", text))
    if _is_future_claim(claim):
        return True
    statistical = any(marker in text for marker in _STATISTICAL_MARKERS)
    causal = any(marker in text for marker in _CAUSAL_MARKERS)
    return bool((statistical and has_number) or (causal and has_number))


def _has_primary_evidence(evidence: str) -> bool:
    return _PRIMARY_EVIDENCE_MARKER in (evidence or "")


def _normalize_quote(text: str) -> str:
    """保留词序和字词本身，只忽略引用时无语义的大小写、空白、标点差异。"""
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[\s`*_~\-–—/\\|\"'“”‘’「」『』()（）\[\]{}<>，,。.!！?？:：;；]+", "", normalized)


def _basis_has_primary_or_material_source(basis: str, evidence: str, material: str) -> bool:
    """basis 必须来自第一手搜索块或作者素材，不能拿同页另一条官方链接“洗白”二手引文。"""
    needle = _normalize_quote(basis)
    if needle and needle in _normalize_quote(material):
        return True
    blocks = re.split(r"\n\n(?=【)", evidence or "")
    return any(block.startswith(_PRIMARY_EVIDENCE_MARKER)
               and needle in _normalize_quote(block) for block in blocks if needle)


# ── 工具：宽容地从 LLM 输出里抠 JSON（不同 provider 的 JSON 纪律参差）────────
def _json_object(text: str) -> dict | None:
    """从输出里取一个 JSON object；数组等合法 JSON 也不能冒充目标 schema。"""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text or "", re.DOTALL)
        if m:
            try:
                value = json.loads(m.group())
            except json.JSONDecodeError:
                return None
        else:
            return None
    return value if isinstance(value, dict) else None


def _loads_loose(text: str, fallback: dict) -> dict:
    """先直接 loads；失败则找第一个 {...} 再试；再失败返回 fallback（保守值）。"""
    return _json_object(text) or dict(fallback)


def _guarded_value(messages: list[dict], model: str, max_tokens: int,
                   stage: str, who: str, parser, create=None, json_mode: bool = True):
    """结构化步骤守卫：空 / 截断 / schema 错误都会绕缓存重试，失败原因显式返回。

    parser 返回 None 表示内容不符合该步骤的 schema。与 _guarded_text 最大的差异是：
    重试耗尽后绝不把 reasoning_content 当成结果交付。调用方会走原有保守兜底，
    同时把这里返回的 failure 写进 stage_failures——保守可以，静默不可以。
    """
    call = create or _create
    last_problem = "empty"
    for attempt in range(_STAGE_RETRY + 1):
        choice = call(
            model, messages, max_tokens, no_cache=attempt > 0,
            response_format={"type": "json_object"} if json_mode else None,
        ).choices[0]
        content = (choice.message.content or "").strip()
        reasoning = (getattr(choice.message, "reasoning_content", None) or "").strip()
        truncated = getattr(choice, "finish_reason", None) == "length"
        value = parser(content) if content else None
        if value is not None and not truncated:
            return value, ""

        last_problem = "truncated" if truncated else "empty" if not content else "unparsed"
        if attempt == _STAGE_RETRY:
            break

        old_tokens = max_tokens
        # 明确撞顶，或正文为空但推理区有内容，都说明预算被推理吃掉了；原预算重抽只会复现。
        if truncated or (not content and reasoning):
            max_tokens = min(max_tokens * 2, _STAGE_TOKENS_CAP)
        detail = f"，token 预算 {old_tokens}→{max_tokens}" if max_tokens != old_tokens else ""
        print(f"  ↻ {who}：{last_problem}{detail}，绕开缓存重试……")

    print(f"  ⚠ {who}连续 {_STAGE_RETRY + 1} 次没有产出合法结果：{last_problem}")
    return None, f"{stage}_{last_problem}"


# ── ① 拆 claim：把草稿拆成可核查的事实性陈述 ────────────────────────────────
EXTRACT_SYS = """你是事实核查前置员。给你一篇技术博客草稿，抽取其中【可核查的事实性陈述】：
- 只要客观可验证的（技术机制、数字、时间、归属），不要观点、感受、比喻；
- 每条改写成一句【独立完整】的陈述（脱离上下文也能看懂）；
- 一行一条，不带序号和其他内容；最多 12 条，按重要性排。"""


_EXTRACT_TOKENS = int(os.getenv("EXTRACT_TOKENS", "2500"))
# 短于这个长度的稿子，「一条断言都没有」是**可能的正常输出**，不告警。
_EXTRACT_MIN_DRAFT = 200


def extract_claims(draft: str) -> list[str]:
    # 走**共用**守卫，不再裸调 _create：空输出绕开缓存重抽、撞顶翻倍预算重来。
    # （这个仓已经为「直接调 _create、没套守卫」吃过两次亏了：研究员收尾一次，这里一次。）
    raw = _guarded_text(
        [{"role": "system", "content": EXTRACT_SYS}, {"role": "user", "content": draft}],
        CHECK_MODEL, _EXTRACT_TOKENS, who="抽取员")
    claims = [ln.strip().lstrip("-*0123456789. ") for ln in raw.splitlines() if ln.strip()]
    if not claims and len(draft.strip()) > _EXTRACT_MIN_DRAFT:
        # **空结果该不该告警，取决于它是不是预期内可能发生的。这一个不是。**
        # 一篇上千字的技术稿抽不出一条事实性陈述，几乎只能是抽取员自己坏了。
        # 而「0 条」在下游的表现是最坏的一种静默失败——它长得跟「全查过了，没问题」一模一样。
        raise RuntimeError(
            f"抽取员从 {len(draft)} 字的草稿里一条断言都没抽出来。这在技术稿上不是正常输出，"
            f"多半是它自己出了问题（撞预算顶 / 正文为空），而不是稿子里真的没有事实陈述。"
            f"最后一次原样输出的前 200 字：\n{raw[:200]}")
    return claims[:MAX_CLAIMS]


SCORE_SYS = """你是事实核查的置信度评估员。给你一条陈述和一份参考素材（可能为空），
评估【不查网络时】这条陈述为真的置信度。按下面的分层信任 rubric 打分：

【分层信任】
- 0.8~1.0：素材里有直接依据（能指出对应句子）；
- 0.5~0.8：素材没提，但属于稳固、无争议的技术常识；
- 0.0~0.5：素材没提、也不是稳固常识（新近变化、小众细节、传闻）。

【数字铁律】陈述里含具体数字/版本号/日期，而素材中没有出现同样的数字
→ 置信度一律 ≤ 0.5（数字是幻觉重灾区，必须进检索核实）。

reason 用一句话说明你落在哪一层、为什么（给人 debug 看，不用讨好下游）。

例：
- 「SSE 是单向推送」+ 素材未提 → {"confidence": 0.75, "reason": "稳固常识：SSE 单向，素材未提"}
- 「Python 3.13 默认移除 GIL」+ 素材未提 → {"confidence": 0.3, "reason": "含版本号且素材无依据，数字铁律封顶"}

只输出 JSON（不要包 markdown 代码块）：{"confidence": 0.0~1.0, "reason": "一句话"}"""


def score_claim(claim: str, material: str) -> dict:
    def parse_score(text: str):
        parsed = _json_object(text)
        if parsed is None:
            return None
        try:
            float(parsed.get("confidence"))
        except (TypeError, ValueError):
            return None
        return parsed

    parsed, failure = _guarded_value(
        [{"role": "system", "content": SCORE_SYS},
         {"role": "user", "content": f"陈述：{claim}\n\n参考素材：\n{material or '（无）'}"}],
        CHECK_MODEL, _SCORE_TOKENS, "score", "置信度打分员", parse_score,
    )
    # 解析失败 → 置信度按 0 处理（保守：宁可多查一次，不可漏放一条）
    if parsed is None:
        parsed = {"confidence": 0.0, "reason": "打分输出解析失败，保守触发检索"}
    try:
        parsed["confidence"] = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        parsed["confidence"] = 0.0
    parsed["stage_failures"] = [failure] if failure else []
    return parsed


# ── ③ query 改写：把 claim 变成适合搜索引擎的查询词 ─────────────────────────
REWRITE_SYS = """把给你的陈述改写成【一个】适合搜索引擎的中立查询词：
- 抽出核心实体和关系，去掉判断性措辞（别把结论喂给搜索引擎）；
- 8~20 个字/词为宜；只输出查询词本身。"""


def _rewrite_query(claim: str) -> tuple[str, list[str]]:
    q, failure = _guarded_value(
        [{"role": "system", "content": REWRITE_SYS}, {"role": "user", "content": claim}],
        CHECK_MODEL, _REWRITE_TOKENS, "rewrite", "query 改写员",
        lambda text: text.strip() or None, json_mode=False,
    )
    return (q or claim[:40]).strip().strip('"「」') or claim[:40], ([failure] if failure else [])


def rewrite_query(claim: str) -> str:
    """保留原有字符串接口；完整管线通过 _rewrite_query 额外取得失败留痕。"""
    return _rewrite_query(claim)[0]


JUDGE_SYS = """你是事实核查裁决员。给你一条陈述、一份参考素材（可能为空）和一批搜索结果片段，据此判断：
- support：证据明确支持陈述为真；
- refute：证据明确显示陈述有误（说明错在哪）；
- unverifiable：证据不足以判断（证据没提到 ≠ 陈述为假）。

【判假必须有据】要判 refute，你必须能从证据里**摘出明确与陈述矛盾的那一句原文**，
填进 refute_basis。摘不出来就只能判 unverifiable。
**「搜索结果里没有提到它」不是矛盾，是没有信息。** 尤其注意这几类：
尚未发生的事、未来的时间点、小众或私有的东西——搜索引擎本来就不会有它们的记录，
「查无此事」和「确认为假」是两件完全不同的事，混起来就是拿沉默当证词。

【素材优先】素材是作者提供的第一手上下文（自己的项目、自己的实验记录、自己的配置），
搜索引擎不可能知道这些。素材与搜索结果冲突时以素材为准；素材已经能直接定论的，
就按素材判 support / refute，不要因为「网上没搜到」而退成 unverifiable。

【来源层级】搜索结果前有“官方/一手、普通来源、社区二手”标记。版本状态、未来路线图、
全称统计和量化因果这类易错陈述，单靠社区文章、新闻转述或传闻不能判 support / refute；
必须有官方文档、标准、原始公告、论文或作者提供的第一手素材。来源冲突时优先一手来源。

只输出 JSON：{"verdict": "support|refute|unverifiable", "reason": "一句话，引证据要点",
"refute_basis": "判 refute 时必填：证据里与陈述矛盾的那句原文；其余情况留空"}"""


def judge_with_evidence(claim: str, evidence: str, material: str = "") -> dict:
    def parse_verdict(text: str):
        parsed = _json_object(text)
        return parsed if parsed and parsed.get("verdict") in _VERDICTS else None

    parsed, failure = _guarded_value(
        [{"role": "system", "content": JUDGE_SYS},
         {"role": "user", "content": f"陈述：{claim}\n\n参考素材：\n{material or '（无）'}"
                                     f"\n\n搜索证据：\n{evidence}"}],
        CHECK_MODEL, _JUDGE_TOKENS, "judge", "证据裁决员", parse_verdict,
    )
    if parsed is None:
        parsed = {"verdict": "unverifiable", "reason": "裁决输出解析失败"}
    parsed["stage_failures"] = [failure] if failure else []


    if parsed["verdict"] == "refute":
        basis = (parsed.get("refute_basis") or "").strip()
        # 不只检查「它填了八个字」，还检查这句话真的存在于它看到的证据/素材里。
        # 否则模型可以自己编一句 basis 绕过闸，结构字段有值却仍然没有证据。
        context = _normalize_quote(f"{material}\n{evidence}")
        if len(basis) < 8 or _normalize_quote(basis) not in context:
            parsed["verdict"] = "unverifiable"
            parsed["unfounded_refute"] = True
        elif (_is_future_claim(claim)
              and not _basis_has_primary_or_material_source(basis, evidence, material)):
            # 证据列表里“同时存在某条官方链接”不等于二手博客里的 basis 就升级成了一手证据。
            # 必须确认打脸原文本身住在【官方/一手】块，避免 Sohu 传闻借旁边的官方链接过闸。
            parsed["verdict"] = "unverifiable"
            parsed["weak_source_refute"] = True
        elif _is_future_claim(claim) and not _future_refute_is_decisive(basis):
            # “3.16 alpha 当前仍默认带 GIL”不能证明“3.16 最终不会改变”；
            # “官方没宣布”同理。只有明确否定未来计划，或官方事实已经发生，才算反证。
            parsed["verdict"] = "unverifiable"
            parsed["premature_future_refute"] = True
    return parsed


FALLBACK_SYS = """检索证据不足以裁决下面的陈述，现在请凭你自己的知识直接判断：
- support：陈述属实；refute：陈述有误；unverifiable：你也无法确定。
只输出 JSON（不要包 markdown 代码块）：{"verdict": "support|refute|unverifiable", "reason": "一句话"}"""


def bare_judge(claim: str) -> dict:
    def parse_verdict(text: str):
        parsed = _json_object(text)
        return parsed if parsed and parsed.get("verdict") in _VERDICTS else None

    parsed, failure = _guarded_value(
        [{"role": "system", "content": FALLBACK_SYS}, {"role": "user", "content": claim}],
        CHECK_MODEL, _BARE_TOKENS, "bare", "裸判回落", parse_verdict,
    )
    if parsed is None:
        parsed = {"verdict": "unverifiable", "reason": "裸判输出解析失败"}
    parsed["stage_failures"] = [failure] if failure else []
    return parsed


def _result(claim: str, conf: float, verdict: str, evidence: str, reason: str,
            *, checked: bool, degraded: str = "", evidence_raw: str = "",
            search_status: str = "not_run", search_queries: list[str] | None = None,
            evidence_enriched: bool = False,
            stage_failures: list[str] | None = None) -> dict:
    raw = (evidence_raw or "")[:EVIDENCE_RAW_CAP]
    if evidence_raw and len(evidence_raw) > EVIDENCE_RAW_CAP:
        raw += "…（截断）"
    return {"claim": claim, "confidence": conf, "verdict": verdict,
            "evidence": evidence, "reason": reason,
            "checked": checked, "degraded": degraded, "evidence_raw": raw,
            "search_status": search_status, "search_queries": list(search_queries or []),
            "evidence_enriched": bool(evidence_enriched),
            "stage_failures": list(stage_failures or [])}


def _search_status(raw: str) -> str:
    if raw.startswith(_SEARCH_MISS_PREFIXES[0]):
        return "error"
    elif not raw or raw.startswith(_SEARCH_MISS_PREFIXES[1]):
        return "empty"
    return "found"


def _official_query(query: str, claim: str) -> str:
    text = f"{query} {claim}"
    if "site:" in query.lower():
        return ""
    for pattern, hint in _OFFICIAL_QUERY_HINTS:
        if pattern.search(text):
            return f"{query} {hint}"
    return ""


def _merge_evidence(*raw_values: str) -> str:
    """官方备用查询排前，按链接去重；同一页面的两份摘要不重复灌给裁决员。"""
    blocks, seen = [], set()
    for raw in raw_values:
        for block in (raw or "").split("\n\n"):
            if not block or _search_status(block) != "found":
                continue
            match = re.search(r"\]\((https?://[^)]+)\)", block)
            key = match.group(1) if match else block[:120]
            if key not in seen:
                seen.add(key)
                blocks.append(block)
    return "\n\n".join(blocks)


def _direct_official_evidence(query: str, claim: str) -> tuple[str, bool]:
    """搜索供应商失灵时，从少量明确实体直达权威标准；没有匹配就安静放弃。"""
    text = f"{query} {claim}"
    for pattern, title, url, anchors in _DIRECT_OFFICIAL_SOURCES:
        if not pattern.search(text):
            continue
        page_text = _fetch_page_text(url)
        excerpt = _relevant_excerpt(page_text, f"{query} {anchors}", claim)
        if excerpt:
            return f"【官方/一手】[{title}]({url})\n【页面正文摘录】\n{excerpt}", True
        return "", False
    return "", False


def _search_evidence(query: str, claim: str = "") -> dict:
    """最多三次免费检索：原查询 → 官方域偏置 → 原始 claim；正文增强只抓一手来源。"""
    max_attempts = max(1, int(os.getenv("FC_SEARCH_ATTEMPTS", "3")))
    candidates = [query, _official_query(query, claim), claim[:160].strip()]
    queries, found, misses = [], [], []
    enriched = False
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate or candidate in queries or len(queries) >= max_attempts:
            continue
        queries.append(candidate)
        raw = _web_search(candidate) or ""
        status = _search_status(raw)
        if status == "found":
            raw, did_enrich = _enrich_search_evidence(raw, candidate)
            enriched = enriched or did_enrich
            # 只有真正抓到了正文才停。搜索摘要里虽有官方链接、但正文抓取失败时，
            # 仍需继续官方域偏置查询；否则 #21 这类空洞摘要会再次过早收工。
            if _has_primary_evidence(raw) and did_enrich:
                return {"status": "found", "raw": raw, "queries": queries,
                        "enriched": enriched}
            found.append(raw)
        else:
            misses.append((status, raw))

    direct_raw, direct_enriched = _direct_official_evidence(query, claim)
    if direct_raw:
        merged = _merge_evidence(direct_raw, *reversed(found))
        return {"status": "found", "raw": merged, "queries": queries,
                "enriched": direct_enriched or enriched}
    if found:
        # 官方偏置查询（若有）是后发生的，把它排前，再用原查询补多样性。
        merged = _merge_evidence(*reversed(found))
        return {"status": "found", "raw": merged, "queries": queries,
                "enriched": enriched}
    status = "error" if any(kind == "error" for kind, _ in misses) else "empty"
    raw = next((value for kind, value in reversed(misses) if kind == status), "")
    return {"status": status, "raw": raw, "queries": queries, "enriched": False}


def check_claim(claim: str, material: str = "") -> dict:
    s = score_claim(claim, material)
    stage_failures = list(s.get("stage_failures", []))
    strong_evidence_required = _requires_strong_evidence(claim)
    if s["confidence"] >= CONF_THRESHOLD and not (strong_evidence_required and not material.strip()):
        # checked=False：高置信放行是「模型自己觉得没问题」，不是「查过」。
        # 这条分支是管线结构上的盲区——自信且错的陈述从这里原样穿过去，
        # 数字铁律只挡住了带数字的那一半（「Hexo 是 Python 写的」一个数字都没有）。
        return _result(claim, s["confidence"], "support",
                       "素材/常识（未触发检索）", s.get("reason", ""), checked=False,
                       stage_failures=stage_failures)

    # 低置信 → CRAG 兜底：改写 → 搜索 → 裁决
    query, rewrite_failures = _rewrite_query(claim)
    stage_failures.extend(rewrite_failures)
    search = _search_evidence(query, claim)   # ← 复用研究员检索，并给核查场景加官方域/正文增强
    evidence = search["raw"]
    search_meta = {"search_queries": search["queries"],
                   "evidence_enriched": search["enriched"]}

    if search["status"] != "found":
        # 未来承诺 / 全称统计 / 量化因果没有强证据时，裸判只是在复制模型记忆和猜测，
        # 正是 30 条真实 eval 里把 5 条 unverifiable 翻错的路径。此类直接保守停住。
        if strong_evidence_required:
            return _result(
                claim, s["confidence"], "unverifiable",
                f"web_search({query}) 未取到证据 → 高风险断言不裸判",
                "未来/全称/量化因果断言缺少可核查的一手证据",
                checked=False, degraded="search_miss", evidence_raw=evidence,
                search_status=search["status"], stage_failures=stage_failures, **search_meta,
            )
        # 普通稳定事实仍保留 Corrective 回落，省掉一次必然判 unverifiable 的裁决调用。
        fb = bare_judge(claim)
        stage_failures.extend(fb.get("stage_failures", []))
        return _result(claim, s["confidence"], fb["verdict"],
                       f"web_search({query}) 未取到证据 → 裸判回落", fb.get("reason", ""),
                       checked=False, degraded="search_miss", evidence_raw=evidence,
                       search_status=search["status"], stage_failures=stage_failures, **search_meta)

    j = judge_with_evidence(claim, evidence, material)
    judge_failures = list(j.get("stage_failures", []))
    stage_failures.extend(judge_failures)
    unfounded = bool(j.pop("unfounded_refute", False))
    weak_source_refute = bool(j.pop("weak_source_refute", False))
    premature = bool(j.pop("premature_future_refute", False))
    if j["verdict"] == "unverifiable":       # V2.1：证据定不了论 → 回落裸判
        why = ("裁决失败" if judge_failures else
               "裁决引用二手来源反驳未来断言 → 按无据处理" if weak_source_refute else
               "裁决拿现状冒充未来反证 → 按无据处理" if premature else
               "裁决判假但交不出打脸原文 → 按无据处理" if unfounded else "证据不足")
        if strong_evidence_required:
            # 高风险断言的「证据不足」本身就是正确终态，不应再降级成模型裸猜。
            degraded = ("judge_failure" if judge_failures else
                        "weak_source" if weak_source_refute else
                        "premature_future_refute" if premature else
                        "unfounded_refute" if unfounded else "")
            return _result(
                claim, s["confidence"], "unverifiable",
                f"web_search({query}) {why} → 高风险断言保守不裸判",
                j.get("reason", "") or "缺少足以定论的一手证据",
                checked=not bool(judge_failures), degraded=degraded,
                evidence_raw=evidence, search_status="found", stage_failures=stage_failures,
                **search_meta,
            )
        fb = bare_judge(claim)
        bare_failures = list(fb.get("stage_failures", []))
        stage_failures.extend(bare_failures)
        failure_degraded = "judge_failure" if judge_failures else "bare_failure" if bare_failures else ""
        if fb["verdict"] != "unverifiable":
            return _result(claim, s["confidence"], fb["verdict"],
                           f"web_search({query}) {why} → 裸判回落", fb.get("reason", ""),
                           checked=False,
                           degraded=failure_degraded or ("unfounded_refute" if unfounded else "thin_evidence"),
                           evidence_raw=evidence, search_status="found",
                           stage_failures=stage_failures, **search_meta)
        # 证据定不了、裸判也定不了 → 这才是真 unverifiable（两道都问过，留痕）
        return _result(claim, s["confidence"], "unverifiable",
                       f"web_search({query}) {why} → 裸判也不确定", fb.get("reason", ""),
                       checked=not bool(judge_failures),
                       degraded=failure_degraded or ("unfounded_refute" if unfounded else ""),
                       evidence_raw=evidence, search_status="found",
                       stage_failures=stage_failures, **search_meta)

    if strong_evidence_required and not material.strip() and not _has_primary_evidence(evidence):
        # prompt 里的“来源层级”是软约束；这里才是硬保证。二手文章可以提供检索线索，
        # 但不能单独把未来路线图或“所有公司平均下降 35%”判成真/假。
        return _result(
            claim, s["confidence"], "unverifiable",
            f"web_search({query}) 只有非一手来源 → 高风险断言保守不定论",
            "检索结果缺少官方文档、原始公告、论文或其他一手证据",
            checked=True, degraded="weak_source", evidence_raw=evidence,
            search_status="found", stage_failures=stage_failures, **search_meta,
        )

    return _result(claim, s["confidence"], j["verdict"],
                   f"web_search({query})", j.get("reason", ""),
                   checked=True, evidence_raw=evidence, search_status="found",
                   stage_failures=stage_failures, **search_meta)


def check_draft(draft: str, material: str = "") -> list[dict]:
    results = []
    for i, claim in enumerate(extract_claims(draft), 1):
        r = check_claim(claim, material)
        icon = {"support": "✅", "refute": "❌", "unverifiable": "❓"}[r["verdict"]]
        mark = "🔍" if r["checked"] else "⚪"
        warn = f" ⚠️{r['degraded']}" if r["degraded"] else ""
        if r["stage_failures"]:
            warn += " ⚠️stage=" + ",".join(r["stage_failures"])
        print(f"{icon}{mark} [{i}] conf={r['confidence']:.2f} {r['verdict']}{warn}: {claim}\n    ↳ {r['reason']}")
        results.append(r)
    return results


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    draft = open(sys.argv[1], encoding="utf-8").read()
    material = open(sys.argv[2], encoding="utf-8").read() if len(sys.argv) > 2 else ""
    results = check_draft(draft, material)
    bad = [r for r in results if r["verdict"] == "refute"]
    checked = [r for r in results if r["checked"]]
    degraded = [r for r in results if r["degraded"]]
    stage_failed = [r for r in results if r["stage_failures"]]
    # 分数不写百分比：n 通常只有个位数，百分比会把样本量藏起来。
    # 「已验证 / 未验证放行」也不合并：verdict=support 里混着两种东西——
    # 查过且证据支持的，和压根没查、模型看着像真的。后者不该记在核查器的功劳簿上。
    print(f"\n📋 共核查 {len(results)} 条：✅ {sum(r['verdict']=='support' for r in results)} "
          f"/ ❌ {len(bad)} / ❓ {sum(r['verdict']=='unverifiable' for r in results)}")
    print(f"   🔍 检索验证 {len(checked)}/{len(results)}"
          f" · ⚪ 未验证放行 {len(results)-len(checked)}/{len(results)}")
    if degraded:
        kinds = {}
        for r in degraded:
            kinds[r["degraded"]] = kinds.get(r["degraded"], 0) + 1
        detail = "、".join(f"{k} {v} 条" for k, v in sorted(kinds.items()))
        print(f"   ⚠️ 降级 {len(degraded)}/{len(results)}（{detail}）"
              f"{'：检索后端没搜成，这批结论其实等同裸判' if 'search_miss' in kinds else ''}")
    if stage_failed:
        kinds = {}
        for r in stage_failed:
            for failure in r["stage_failures"]:
                kinds[failure] = kinds.get(failure, 0) + 1
        detail = "、".join(f"{k} {v} 条" for k, v in sorted(kinds.items()))
        print(f"   ⛔ 阶段故障 {len(stage_failed)}/{len(results)}（{detail}）"
              "：这批结果不能当作完整 CRAG 运行")
    if bad:
        print("⚠️ 待修改：")
        for r in bad:
            print(f"  - {r['claim']}\n    ↳ {r['reason']}")


if __name__ == "__main__":
    main()
