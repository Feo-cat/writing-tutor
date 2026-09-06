"""Web 写作引擎：研究员检索、策划提纲、导师带学、编辑整理作者回答、终审提出建议。
模型调用使用 OpenAI 兼容协议，研究员通过 ddgs 执行客户端检索循环。"""

import hashlib
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from openai import OpenAI
from dotenv import load_dotenv

try:                       # 这个搜索包 2024 后从 duckduckgo_search 改名 ddgs；两个名都兜住
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

load_dotenv()

_HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_ARTIFACTS_DIR = os.path.abspath(
    os.getenv("LOCAL_ARTIFACTS_DIR") or os.path.join(_HERE, "local_artifacts")
)
BLOG_ARTIFACTS_DIR = os.path.join(LOCAL_ARTIFACTS_DIR, "blogs")


def blog_artifacts_dir() -> str:
    """返回只在本机保存的博客产物目录，并确保它存在。"""
    os.makedirs(BLOG_ARTIFACTS_DIR, exist_ok=True)
    return BLOG_ARTIFACTS_DIR


def blog_artifact_path(filename: str) -> str:
    """把单个产物文件名收进本地目录，避免草稿散落仓库根目录后被误提交。"""
    if not filename or os.path.basename(filename) != filename or filename.startswith("."):
        raise ValueError(f"非法博客产物文件名：{filename!r}")
    return os.path.join(blog_artifacts_dir(), filename)

# OpenAI 兼容客户端：base_url / key 都从 .env 读，换 provider 不改代码。
# base_url 不设 = 官方 OpenAI；key 不设 = 回落到 OPENAI_API_KEY。
client = OpenAI(
    base_url=os.getenv("LLM_BASE_URL") or None,
    api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
)

# 新版 OpenAI 直连的 GPT-5 系列要用 max_completion_tokens；多数兼容 provider 用 max_tokens。
# 默认 max_tokens（最通用）；OpenAI 直连若报错，就在 .env 设 TOKEN_PARAM=max_completion_tokens。
_TOKEN_PARAM = os.getenv("TOKEN_PARAM", "max_tokens")


def _sampling() -> dict:
    """按需读取 temperature 和 seed；未设置时使用服务端默认值。
    惰性读取使评测工具可以在导入后配置采样。服务端是否支持参数由使用者确认。"""
    kw = {}
    t = os.getenv("LLM_TEMPERATURE", "")
    if t.strip():
        kw["temperature"] = float(t)
    s = os.getenv("LLM_SEED", "")
    if s.strip():
        kw["seed"] = int(s)
    return kw


# ── 网关留痕：把 X-GW-* 响应头收下来 ──────────────────────────────────────
# 为什么客户端要读响应头：LLM_BASE_URL 指向自建 Go 网关时，客户端发的可能是**档位名**
# （sloop / galleon），由网关翻成具体型号；上游挂了它还会沿 ark→galleon→sloop→raft
# 往下降级。也就是说——**「我请求了 galleon」和「galleon 真的答了这一题」是两件事。**
#
# 网关那边已经认账了（markGateway 写 X-GW-Model / X-GW-Tier / X-GW-Degraded），
# 客户端不读，就等于把网关刚删掉的那个静默 fallback 又请了回来，只是换了个体面的名字。
# 对 run_eval 尤其要命：评分员配 galleon、被测配 sloop，配置串不同，第一层自评检测
# （GRADER_SELFGRADE）当然说「不是自评」；可网关一降级两边都落到同一个型号，
# **配置上不是自评，实际上是自评**，而分数照出、一点痕迹没有。
#
# 一条铁律写在这里：读不到头 → 记「查不了」，绝不记「没降级」。
# 把「不知道」记成「没问题」，是这套留痕机制唯一能出的致命错。
_GW_LOG: list[dict] = []
_GW_HEADERS_ON = os.getenv("GW_READ_HEADERS", "1") != "0"   # 出意外时的逃生阀


def _gw_note(requested: str, headers) -> None:
    """记一笔：这次请求的是谁、网关说实际由谁作答、有没有降级。"""
    h = {}
    try:
        # 全部转小写再取：Go 的 Header.Set 会把 X-GW-Model 规范化成 X-Gw-Model，
        # 拿原样键名硬查会漏读。httpx.Headers 本身不区分大小写，普通 dict 区分——
        # 统一在这里抹平，两种都吃得下。
        for k, v in (headers.items() if hasattr(headers, "items") else []):
            if str(k).lower().startswith("x-gw-"):
                h[str(k).lower()] = str(v)
    except Exception:
        h = {}
    _GW_LOG.append({
        "requested": requested,
        "served": h.get("x-gw-model", ""),          # 空 = 没读到，不代表没降级
        "tier": h.get("x-gw-tier", ""),
        "degraded": h.get("x-gw-degraded", "").lower() == "true",
        # 网关只在**命中**时写 X-GW-Cache: hit，没命中时这个头压根不存在。
        # 所以 False 有两种来源：真没命中、和根本没读到头——别单看它下结论，
        # 得和同一条里的 seen 一起看。
        "cached": h.get("x-gw-cache", "").lower() == "hit",
        "seen": bool(h),                            # 这次到底读没读到网关的头
    })


def gw_report() -> dict:
    """把本进程所有调用按【请求的 model】归档：谁真答的、降级几次、命中几次缓存、读到几次头。

    评测用它做第二层自评检测——第一层比配置串，这一层比**真正作答的模型**。"""
    by: dict[str, dict] = {}
    for e in _GW_LOG:
        d = by.setdefault(e["requested"],
                          {"calls": 0, "served": [], "degraded": 0, "seen": 0, "cached": 0})
        d["calls"] += 1
        d["degraded"] += bool(e["degraded"])
        d["seen"] += bool(e["seen"])
        d["cached"] += bool(e["cached"])
        if e["served"] and e["served"] not in d["served"]:
            d["served"].append(e["served"])
    return by


def _extra_headers() -> dict:
    h = {"X-GW-Cache": "off"} if os.getenv("GW_CACHE") == "off" else {}


    src = (os.getenv("GW_SOURCE") or "").strip().lower()
    if src and len(src) <= 16 and all(
        "a" <= c <= "z" or "0" <= c <= "9" or c in "_-" for c in src
    ):
        h["X-GW-Source"] = src
    return h


GW_LEDGER = os.getenv("GW_LEDGER", "").strip()

# 网关那边的来源白名单：a-z0-9_- 且 ≤16 字符（见 _extra_headers 那段注释）。
_SRC_OK = lambda x: len(x) <= 16 and all(  # noqa: E731
    "a" <= c <= "z" or "0" <= c <= "9" or c in "_-" for c in x)


_WARN_SINK: list | None = None


def _warn(msg: str) -> None:
    print(msg, flush=True)
    if _WARN_SINK is not None:
        _WARN_SINK.append(msg.strip())


def drain_warnings() -> list[str]:
    """取走并清空攒下的守卫动作。没设 sink 时返回空表。"""
    if _WARN_SINK is None:
        return []
    got, _WARN_SINK[:] = list(_WARN_SINK), []
    return got


def post_key(topic: str) -> str:
    """由选题算出的稳定短标识：`s` + sha256 前 6 位。

    两个用处，而且它们是同一个需求的两面：
      · **会话号**——同一个选题永远是同一个号，所以进程重启后接得回来；
      · **账本来源戳的后缀**——于是每一行账都追得到具体哪一篇。
    用哈希不用 slug，是因为 slug 里有中文，过不了网关那道 a-z0-9_- 的白名单。"""
    return "s" + hashlib.sha256((topic or "").strip().encode("utf-8")).hexdigest()[:6]


def stamp_source(prefix: str, key: str = "") -> str:
    """给后续调用设置账本来源戳，返回实际设置的值。
    例如 web-s4a8a7d。不合规时逐级退回到前缀或不标注。
    使用进程级环境变量，仅适用于本地单用户串行请求；并发需改为请求级参数。"""
    for cand in (f"{prefix}-{key}" if key else "", prefix):
        if cand and _SRC_OK(cand):
            os.environ["GW_SOURCE"] = cand
            return cand
    return ""


def ledger_rows(src: str, path: str = "") -> list[dict]:
    """从网关账本里捞出这个来源的所有行。

    为什么读账本而不用 gw_report()：**那个读的是进程内存**（`_GW_LOG`），
    进程一死就没了，事后拿不出来。账本是只追加的文件——**它才是事后可查的物证**。"""
    path = path or GW_LEDGER
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if src not in ln:          # 先做一次廉价子串筛，再解析
                    continue
                try:
                    r = json.loads(ln)
                except json.JSONDecodeError:
                    continue               # 账本被写坏的那一行跳过，别让它拖垮整份凭据
                if r.get("source") == src:
                    out.append(r)
    except OSError:
        return []
    return out


def _create(model: str, messages: list[dict], max_tokens: int, tools: list | None = None,
            no_cache: bool = False, response_format: dict | None = None):
    """所有 LLM 调用的唯一出口：把 token 上限参数名收敛到一处，顺带可选 tools + 采样参数。
    no_cache=True 时给这一笔单独盖 X-GW-Cache: off——重抽专用，理由见下面那段注释。"""
    kw = {"model": model, "messages": messages, _TOKEN_PARAM: max_tokens, **_sampling()}
    if tools:
        kw["tools"] = tools
    if response_format:
        # 结构化小任务显式要求 JSON object。网关的 OpenAI 兼容上游会原样透传，
        # 不支持该参数的 adapter 可在网关侧丢弃；普通正文调用不传，行为不变。
        kw["response_format"] = response_format
    hdrs = _extra_headers()
    if no_cache:
        # 重抽必须绕开缓存：重抽请求和上一次 byte-identical，缓存必然命中，
        # 拿回的就是刚才那个坏响应——**重抽等于没抽**。
        # 缓存的前提是「相同输入必然相同输出」，重试的前提是「再试一次可能不一样」，
        # 两者在设计上是冲突的；在有缓存的路径上做无变化的重试，自相矛盾。
        hdrs["X-GW-Cache"] = "off"
    if hdrs:
        kw["extra_headers"] = hdrs

    # 先看 SDK 给不给响应头，再决定走哪条路——**不用 try/except 去试**：
    # 真调用抛出来的异常要是被这里吞掉再走一遍普通 create，那就是同一个问题收两次钱。
    raw = getattr(client.chat.completions, "with_raw_response", None) if _GW_HEADERS_ON else None
    if raw is not None:
        resp = raw.create(**kw)
        _gw_note(model, getattr(resp, "headers", None))
        parse = getattr(resp, "parse", None)
        return parse() if callable(parse) else resp

    _gw_note(model, None)   # 拿不到头也要留一条：记录的是「查不了」，不是「没降级」
    return client.chat.completions.create(**kw)


_DEFAULT_MODEL = os.getenv("LLM_MODEL")


def _tier(name: str) -> str:
    m = os.getenv(f"MODEL_{name.upper()}") or _DEFAULT_MODEL
    if not m:
        raise RuntimeError(
            f"未配置模型：在 .env 里设 LLM_MODEL（全局默认）或 MODEL_{name.upper()}（单档覆盖）。见 .env.example"
        )
    return m


TIERS = {name: _tier(name) for name in ("raft", "sloop", "galleon", "ark")}
# raft=机械批量·最便宜 · sloop=平衡默认（研究员/策划）· galleon=质量敏感（导师/编辑/终审）· ark=顶配预留


AUTHOR_PROFILE = os.getenv("AUTHOR_PROFILE", "").strip()


_COVERED_CAP = 220


def _covered_block(covered: list[str] | None) -> str:
    """截取已完成各节正文，向导师提供已覆盖的主题。
    限制上下文长度，避免重复讲解和以先前表现代替本节掌握情况。"""
    if not covered:
        return ""
    items = []
    for body in covered:
        b = (body or "").strip()
        if len(b) > _COVERED_CAP:
            b = b[:_COVERED_CAP] + "…（后略）"
        if b:
            items.append(b)
    if not items:
        return ""
    return ("\n\n【前面几节已经讲过的内容】\n"
            "**下面这些不要重复讲**——你这一节的任务是在它们之上往前推进；\n"
            "如果发现本节要点和前面高度重叠，就挑一个前面没展开过的角度来讲。\n\n"
            + "\n\n".join(items))


_CHAT_RETRY = int(os.getenv("CHAT_RETRY", "2"))


_CHAT_TOKENS_CAP = int(os.getenv("CHAT_TOKENS_CAP", "16000"))


def _chat(model: str, system: str, user: str, max_tokens: int = 1024) -> str:
    """一次性调用模型，空输出时重试，截断时增加预算。"""


    return _guarded_text([{"role": "system", "content": system},
                          {"role": "user", "content": user}], model, max_tokens)


RESEARCHER_SYS = """你是技术博客的研究员。围绕给定选题上网查证，产出一份"素材简报"，
供后续的策划和访谈使用。要求：
- 列出 5-8 条与选题相关的关键事实 / 概念 / 数据，每条尽量带上来源；
- 只输出简报本身，不要写成文章、不要寒暄。"""


SEARCH_TOOL = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "上网搜索一个查询词，返回前若干条结果（标题 + 摘要 + 链接）。需要事实 / 数据 / 最新信息时调用，可多次。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索查询词"}},
            "required": ["query"],
        },
    },
}]
RESEARCH_MAX_ROUNDS = int(os.getenv("RESEARCH_MAX_ROUNDS", "4"))   # 熔断：最多几轮搜索，防死循环 / 烧钱


_PRIMARY_SOURCE_HOSTS = (
    "rfc-editor.org", "ietf.org", "datatracker.ietf.org", "w3.org",
    "developer.mozilla.org", "python.org", "docs.python.org", "peps.python.org", "pypi.org",
    "kubernetes.io", "postgresql.org", "sqlite.org", "redis.io", "git-scm.com", "hexo.io",
    "go.dev", "rust-lang.org", "docker.com", "docs.aws.amazon.com",
    "opentelemetry.io", "developers.openai.com", "platform.openai.com", "openai.com",
    "docs.anthropic.com", "docs.github.com",
)
_COMMUNITY_SOURCE_HOSTS = (
    "blog.csdn.net", "zhihu.com", "juejin.cn", "cnblogs.com", "sohu.com",
    "baijiahao.baidu.com", "cloud.tencent.com", "developer.aliyun.com", "runebook.dev",
)
_SOURCE_LABELS = {0: "官方/一手", 1: "普通来源", 2: "社区二手"}


def _source_class(url: str) -> int:
    """0=官方/一手，1=普通，2=社区二手；只用于排序和留痕，不直接裁决真假。"""
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return 1

    def belongs(suffixes: tuple[str, ...]) -> bool:
        return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)

    if belongs(_PRIMARY_SOURCE_HOSTS) or host.endswith(".gov") or host.endswith(".edu"):
        return 0
    if belongs(_COMMUNITY_SOURCE_HOSTS):
        return 2
    return 1


def _rank_search_hits(hits: list[dict]) -> list[dict]:
    """稳定地把官方/一手来源提到前面；同等级保持搜索引擎原顺序。"""
    return sorted(hits, key=lambda hit: _source_class(hit.get("href", "")))


class _VisibleText(HTMLParser):
    """把官方 HTML 压成可检索纯文本；脚本、样式和 SVG 不进证据。"""
    _SKIP = {"script", "style", "svg", "noscript"}
    _BREAK = {"p", "div", "section", "article", "main", "li", "tr", "br", "pre", "h1", "h2", "h3"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self.skip_depth += 1
        elif not self.skip_depth and tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(data)


def _page_fetch_url(url: str) -> str:
    """RFC info HTML 改取同号纯文本；其他官方 URL 原样返回，非官方返回空。"""
    if _source_class(url) != 0:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    info_match = re.fullmatch(r"/info/rfc(\d+)/?", parsed.path, re.I)
    return (parsed._replace(path=f"/rfc/rfc{info_match.group(1)}.txt", query="", fragment="").geturl()
            if info_match else url)


def _fetch_page_text(url: str) -> str:
    """只抓官方/一手 allowlist 的 200 文本响应；不跟重定向，避免搜索结果变成 SSRF 跳板。"""
    # RFC Editor 的 info HTML 会嵌入整份标准并混入大量导航，且大 RFC 的关键章节可能
    # 落在字节 cap 之后；同号 .txt 更小、更干净，也更适合逐段摘录。
    fetch_url = _page_fetch_url(url)
    if not fetch_url:
        return ""
    timeout = float(os.getenv("SEARCH_PAGE_TIMEOUT", "8"))
    byte_cap = int(os.getenv("SEARCH_PAGE_BYTES", "600000"))
    try:
        with httpx.stream(
            "GET", fetch_url, timeout=timeout, follow_redirects=False,
            headers={"User-Agent": "writing-tutor-fact-checker/1.0"},
        ) as response:
            if response.status_code != 200:
                return ""
            content_type = (response.headers.get("content-type") or "").lower()
            if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml")):
                return ""
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                remaining = byte_cap - size
                if remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                size += min(len(chunk), remaining)
            payload = b"".join(chunks)
            encoding = response.encoding or "utf-8"
    except Exception:
        return ""            # 正文增强失败不能把已有搜索摘要一起拖垮

    decoded = payload.decode(encoding, errors="replace")
    if "html" not in content_type:
        return decoded
    parser = _VisibleText()
    try:
        parser.feed(decoded)
    except Exception:
        return ""
    return "".join(parser.parts)


_EVIDENCE_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "method", "default",
    "rfc", "http", "https", "tls", "dns", "protocol", "version", "official", "editor",
    "官方", "文档", "请求", "规定", "支持", "使用", "方法", "是否", "用于",
}


def _relevant_excerpt(page_text: str, query: str, seed: str = "") -> str:
    """从页面正文里挑与 query/摘要最相关的段落，避免整页导航和版权脚注灌进 LLM。"""
    cap = int(os.getenv("SEARCH_PAGE_CHARS", "3500"))
    paragraphs = [re.sub(r"\s+", " ", p).strip()
                  for p in re.split(r"[\r\n]+", page_text or "") if len(p.strip()) >= 40]
    if not paragraphs:
        return ""
    term_source = f"{query} {seed}".lower()
    terms = {t.lower() for t in re.findall(r"[a-z][a-z0-9_.-]{2,}|\d+(?:\.\d+)+", term_source, re.I)}
    terms.difference_update(_EVIDENCE_STOPWORDS)
    term_docs = {
        term: sum(term in paragraph.lower() for paragraph in paragraphs)
        for term in terms
    }
    ranked = []
    for index, paragraph in enumerate(paragraphs):
        lowered = paragraph.lower()
        # 页面标题/目录会反复出现 TLS、RFC 等泛词；罕见概念（static RSA、AEAD、
        # application/dns-message）更能定位真正回答 claim 的标准正文。
        score = sum(
            min(lowered.count(term), 3) * (4 if term_docs[term] <= 3 else 2 if term_docs[term] <= 12 else 1)
            for term in terms
        )
        if score:
            ranked.append((-score, index, paragraph))
    best = sorted(ranked)[:12] if ranked else [(0, i, p) for i, p in enumerate(paragraphs[:8])]
    chosen = sorted(best,
                    key=lambda item: item[1])
    out = []
    size = 0
    for _, _, paragraph in chosen:
        remaining = cap - size
        if remaining <= 0:
            break
        out.append(paragraph[:remaining])
        size += min(len(paragraph), remaining) + 1
    return "\n".join(out)


def _enrich_search_evidence(raw: str, query: str) -> tuple[str, bool]:
    """给前几个官方搜索块补相关页面正文；失败时原摘要原样返回。"""
    max_pages = max(0, int(os.getenv("SEARCH_PAGE_FETCHES", "2")))
    if max_pages == 0:
        return raw, False
    blocks = (raw or "").split("\n\n")
    enriched = False
    fetched = 0
    for index, block in enumerate(blocks):
        if fetched >= max_pages or not block.startswith("【官方/一手】"):
            continue
        match = re.search(r"\]\((https?://[^)]+)\)", block)
        if not match or _source_class(match.group(1)) != 0:
            continue
        fetched += 1
        page_text = _fetch_page_text(match.group(1))
        excerpt = _relevant_excerpt(page_text, query, block)
        if excerpt:
            blocks[index] = block + "\n【页面正文摘录】\n" + excerpt
            enriched = True
    return "\n\n".join(blocks), enriched


def _web_search(query: str, k: int | None = None) -> str:
    """检索后端：DuckDuckGo（ddgs，免费、免 key）。异常 / 空结果都兜底成文本，别让循环崩。"""
    k = k or int(os.getenv("SEARCH_MAX_RESULTS", "5"))
    fetch_multiplier = max(1, int(os.getenv("SEARCH_FETCH_MULTIPLIER", "3")))
    try:
        with DDGS() as ddgs:
            # 先多取一些再排来源质量；否则官方文档在第 k+1 位时永远没有入选机会。
            hits = list(ddgs.text(query, max_results=min(k * fetch_multiplier, 20)))
    except Exception as e:                        # 搜索供应商偶发限流：吞掉，让模型换词重试
        return f"[搜索失败：{e}。换个查询词，或就用已有信息作答。]"
    if not hits:
        return "[没搜到结果，换个更宽泛的查询词试试。]"
    hits = _rank_search_hits(hits)[:k]
    return "\n\n".join(
        f"【{_SOURCE_LABELS[_source_class(h.get('href', ''))]}】"
        f"[{h.get('title', '')}]({h.get('href', '')})\n{h.get('body', '')}" for h in hits
    )


def research_topic(topic: str) -> str:
    """研究员（手写 client-side ReAct 检索循环）：
    模型决定搜什么 → 我的代码调 ddgs → 把结果塞回 → while 到模型不再要搜、给出简报。
    双保险：轮数熔断（RESEARCH_MAX_ROUNDS）+ 熔断后强制收尾。⚠️ 这一档模型必须支持 function calling。"""
    messages = [
        {"role": "system", "content": RESEARCHER_SYS},
        {"role": "user", "content": f"选题：{topic}\n请先上网查证（可多次搜索），再给我一份素材简报。"},
    ]
    for _ in range(RESEARCH_MAX_ROUNDS):
        _choice = _create(TIERS["sloop"], messages, 1500, tools=SEARCH_TOOL).choices[0]
        # 带 tools 的这次不能用 _guarded_text（它只管纯文本），但截断必须留痕：
        # tool_calls 的 arguments 被砍断就是一段非法 JSON，下面 json.loads 会回落成拿选题去搜，
        # 那时看到的现象是「它搜的词莫名其妙」，而真凶在这里。
        if getattr(_choice, "finish_reason", None) == "length":
            print("  ⚠ 研究员这轮被 max_tokens 截断（1500）——工具参数可能不完整")
        msg = _choice.message
        if not msg.tool_calls:                    # 模型不再要搜 → 它这轮给的就是最终简报
            return (msg.content or "").strip()
        # 把这轮 assistant（含 tool_calls）原样入历史，再逐个执行工具、把结果塞回去
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            try:
                query = json.loads(tc.function.arguments or "{}").get("query", topic)
            except json.JSONDecodeError:
                query = topic
            print(f"🔎 研究员搜索：{query}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": _web_search(query)})
    # 撞熔断：不再给 tools，逼它用手头信息直接收尾（防它无限想搜）
    messages.append({"role": "user", "content": "别再搜了，就用上面已有的信息，现在直接给出素材简报。"})


    return _guarded_text(messages, TIERS["sloop"], 1500, who="研究员")


# ── 🗂 策划：选题 → 分节提纲 ──────────────────────────────────────────────
PLANNER_SYS = """你是技术博客的策划，帮助作者把对主题的理解写成自己的文章。
依据作者提供的背景安排深度，不预设其语言偏好、职业或经验；不要要求作者编造经历。
给你选题和素材，输出一份分节提纲：
- 3-5 节，遵循"钩子 → 概念 → 层层深入 → 收尾"的节奏；
- 钩子可以是困惑、好奇点或作者明确提供的经历；不要虚构亲历事件；
- 每节**只输出一行**，格式：`节标题 | 这节要【讲给作者听、并检查他懂没懂】的 1-2 个概念点`
- 要点是【要教会他的概念】，不是【要他凭经验回答的问题】；
- **不要单独排一节「小结 / 总结 / 收尾」**——收尾由最后一节的正文自然带出。
  单独的小结节天然只能重述前面，作者还得为它再答一遍，纯浪费。
- 只输出提纲本身，别写开场白或解释。

【输出格式 · 必须照做】把提纲放进定界符里，**定界符之外的任何内容都会被丢弃**：
<提纲>
节标题 | 这节要讲清的 1-2 个概念点
节标题 | 这节要讲清的 1-2 个概念点
</提纲>
你可以在 <提纲> 之前随便思考、打草稿，那些不会被读取。"""


_PLAN_NUDGE = (
    "你上一条没按格式。请**只**输出提纲本身，一节一行，用竖线分隔，别写开场白、别写解释、别加标题：\n"
    "节标题 | 这节要讲清的 1-2 个概念点\n"
    "节标题 | 这节要讲清的 1-2 个概念点"
)
PLAN_RETRY = int(os.getenv("PLAN_RETRY", "2"))     # 格式不合时的额外重抽次数


_TITLE_MAX = 40                  # 节标题超过这个长度，基本可以断定不是标题
_META_WORDS = ("要求：", "格式是", "所以需要", "只输出", "每节只输出", "输出一份",
               "我们需要", "但要求", "节标题 |", "提纲本身", "1-2 个概念点", "1-2个概念点")


def _looks_like_section(title: str, points: str) -> bool:
    """过滤过长标题和提示词碎片，避免把含竖线的推理文字误认成提纲。"""
    if not title or not points:
        return False
    if len(title) > _TITLE_MAX:                    # 真节标题不会有四十多个字
        return False
    if any(w in title or w in points for w in _META_WORDS):   # 复述格式要求的元话语
        return False
    return True


def _parse_outline(raw: str) -> list[dict]:
    """定界符优先：只读 <提纲>…</提纲> 之间的内容；找不到定界符才退回全文扫描。

    「边界要显式，不能靠猜」——这跟 NDJSON 用换行符定帧是同一个道理：
    没有明确边界的时候，解析器只能靠启发式，而启发式一定会在某个输入上翻车。"""
    body = raw
    i, j = raw.find("<提纲>"), raw.rfind("</提纲>")
    if i >= 0 and j > i:
        body = raw[i + len("<提纲>"):j]
    sections = []
    for line in body.splitlines():
        line = line.strip().lstrip("-*0123456789. ")  # 去掉可能的项目符号/序号
        if "|" in line:
            title, points = line.split("|", 1)
            title, points = title.strip(), points.strip()
            if _looks_like_section(title, points):
                sections.append({"title": title, "points": points})
    return sections


_SUMMARY_WORDS = ("小结", "总结", "收尾", "结语", "写在最后", "结束语", "尾声")


def _drop_summary_sections(sections: list[dict]) -> list[dict]:
    """移除重复的小结节，但至少保留一节；代码保证与提示词约束并用。"""
    kept = [s for s in sections
            if not any(w in s["title"] for w in _SUMMARY_WORDS)]
    if not kept:
        return sections
    if len(kept) < len(sections):
        dropped = [s["title"] for s in sections if s not in kept]
        print(f"  ✂ 砍掉小结类节（它只会重述前面）：{'、'.join(dropped)}")
    return kept


def _replan_block(feedback: str, previous: list[dict] | None) -> str:
    """将作者反馈与上一版提纲一起交给策划，使“修改第几节”有明确指向。"""
    fb = (feedback or "").strip()
    if not fb:
        return ""
    blk = "\n\n【上一版提纲】\n"
    blk += ("\n".join(f"  {i}. {x['title']}｜{x.get('points', '')}"
                      for i, x in enumerate(previous, 1)) if previous else "（没留存）")
    blk += ("\n\n【作者对上一版的意见】\n" + fb +
            "\n\n请据此重排。**这是作者本人的要求，优先级高于你自己的判断。**\n"
            "他没提到的节可以保留原样；但不要把上一版整个原样交回来。")
    return blk


def plan_outline(topic: str, material: str, feedback: str = "",
                 previous: list[dict] | None = None) -> list[dict]:
    """返回 [{title, points}, ...]；逐行解析竖线分隔的提纲。
    格式不符时提示并重试；耗尽后抛异常，防止空提纲直接进入成稿。"""
    user = f"选题：{topic}\n\n素材：{material}" + _replan_block(feedback, previous)
    messages_note = ""
    for attempt in range(PLAN_RETRY + 1):
        raw = _chat(TIERS["sloop"], PLANNER_SYS, AUTHOR_PROFILE + "\n\n" + user + messages_note)
        sections = _drop_summary_sections(_parse_outline(raw))


        if sections and 2 <= len(sections) <= 8:
            return sections
        if sections:
            _warn(f"  ↻ 策划排出 {len(sections)} 节（要的是 3-5 节），"
                  f"多半是把推理或多个版本都吐进来了，重抽……")
            sections = []
        if attempt < PLAN_RETRY:
            _warn(f"  ↻ 策划这轮没按「标题 | 要点」格式（{len(raw)} 字，0 节），提示后重抽……")
            messages_note = "\n\n" + _PLAN_NUDGE
    raise RuntimeError(
        f"策划连续 {PLAN_RETRY + 1} 次都没输出「标题 | 要点」格式，解析出 0 节。"
        f"最后一次原样输出的前 200 字：\n{raw[:200]}"
    )


TUTOR_SYS = """你是技术写作导师，正在带作者搞懂博客的某一节，好让他能用自己的话把它写出来。
依据作者提供的背景和当前回答调整讲解深度，不预设其经验水平。
没有提供真实经历时，用概念、假设例子和推演检查理解，不要求编造事故或案例。

【每一轮先判断、再决定怎么做】：
- 看作者上一次的回答，判断他对这节关键概念【懂了没 / 够不够自己写出来】；
- 若这节的关键点他已经能用自己的话说清楚了 → 【只输出一行】：`【本节已掌握】<一句话说他已经懂了什么>`，别再讲、也别再问；
- 若还没到 → 继续教：答得含糊就【换个说法把同一个点再讲一遍】，答对了就【讲下一个小点】，然后按下面格式【讲】+【问】。

继续教时严格按这个格式（别的都不要）：
讲：<依据素材，把【一个】小概念讲清楚——人话、简短、最好带个小例子>
问：<从这三类里挑一个，要能引出一段话，不要是非题：
   ①【复述】请作者用自己的话把刚讲的说一遍；
   ②【推演】基于刚讲的往前推一步——"如果 X 会怎样""按这个逻辑，Y 该怎么处理""为什么不能反过来"。
      **不需要作者的项目，只需要他真的理解刚讲的东西**；
   ③【对照】请作者把刚讲的和他自己的项目对照一下。
   ⚠️ **配比硬规定：一节里【对照】最多问一次，其余轮次只能用【复述】或【推演】。**
   理由：对照型问题问的是作者【已经知道】的东西，问多了他学不到新东西，
   而且会把整篇文章的重心从【主题】拉回【作者的项目】——那是 making-of 文章该干的事，
   不是学习型文章该干的。默认优先【推演】，它最能检验是不是真懂了。>
判定掌握时，【只输出】那一行 `【本节已掌握】...`，不要带"讲：/问："。"""


DONE_TAG = "【本节已掌握】"
MAX_ROUNDS = 5               # 熔断：最多讲+问 5 轮，防导师判断失灵、跟你纠缠没完
TUTOR_RETRY = int(os.getenv("TUTOR_RETRY", "2"))  # 导师某轮退化（空 / 无"问："）时，提示格式后最多重抽几次


def _guarded_text(msgs: list[dict], model: str, max_tokens: int, who: str = "模型") -> str:
    """纯文本调用守卫：空输出重试，截断时增加预算，重试绕过网关缓存。"""
    content = ""
    for attempt in range(_CHAT_RETRY + 1):
        # attempt>0 ＝ 这是重抽：必须绕开缓存，否则拿回的还是刚才那个坏响应。
        choice = _create(model, msgs, max_tokens, no_cache=attempt > 0).choices[0]
        content = (choice.message.content or "").strip()
        truncated = getattr(choice, "finish_reason", None) == "length"
        if not content:
            # 空的时候必须报告「那这些 token 去哪了」。推理型模型会把话全说进
            # reasoning_content，content 一直是空——那样翻多少倍预算都没用，
            # 修法和「预算不够」完全相反。**「我没看到内容」不等于「模型没输出」。**
            _rc = getattr(choice.message, "reasoning_content", None) or ""
            _keys = sorted(k for k in vars(choice.message)) if hasattr(choice.message, "__dict__") else []
            print(f"     ↳ 空输出诊断：finish_reason={getattr(choice, 'finish_reason', None)!r}"
                  f"  reasoning_content={len(_rc)} 字"
                  + (f"  字段={_keys}" if _keys else ""))
        if attempt == _CHAT_RETRY:
            # 重试用尽。content 仍空但推理里有货 → **降级交付，不静默返回空**。
            # 同 edit_section 那条回落（编辑吐空就拼作者原话）：宁可给一个标记过的
            # 降级产物，也不要让上游拿到一个「什么都没发生」的空字符串。
            if not content:
                _rc = (getattr(choice.message, "reasoning_content", None) or "").strip()
                if _rc:
                    _warn(f"  ⚠ {who}正文始终为空，降级交付推理草稿（{len(_rc)} 字）")
                    return ("〔⚠ 降级产物：这是模型的**推理草稿**，不是成品。\n"
                            "  它在推理阶段就耗尽了输出预算，正文一个字都没写出来。\n"
                            "  下面的内容格式不受控，请当草稿看。〕\n\n" + _rc)
            break                                    # 用手头这份
        if truncated:


            bumped = min(max_tokens * 2, _CHAT_TOKENS_CAP)
            why = "输出为空且撞顶（预算全烧在推理上？）" if not content else "输出被截断（length）"
            _warn(f"  ↻ {who}{why}，token 预算 {max_tokens}→{bumped} 重来……")
            max_tokens = bumped
            continue
        if not content:                              # 纯空（没撞顶）→ 绕开缓存重来
            _warn(f"  ↻ {who}返回空，重抽（绕开缓存）……")
            continue
        break                                        # 非空、未截断 → 收
    return content


def _ask(model: str, system: str, messages: list[dict], max_tokens: int = 3200) -> str:
    """发送完整的本节消息历史，复用空输出与截断守卫。"""
    return _guarded_text([{"role": "system", "content": system}, *messages],
                        model, max_tokens, who="导师")


def _split_lesson_question(text: str, fallback_q: str = "用你自己的话，把上面这段复述一遍？") -> tuple[str, str]:
    """把导师一轮的输出拆成（讲解, 问题）；找不到"问："就用 fallback_q 兜底。
    fallback_q 由调用方按本节 points 传入，避免退化成一句无意义的"复述上面这段"。"""
    if "问：" in text:
        lesson, question = text.split("问：", 1)
        return lesson.replace("讲：", "", 1).strip(), question.strip()
    return text.strip(), fallback_q


_FORMAT_NUDGE = (
    "你上一条没按格式（空了、或缺『问：』那行）。请严格按两行重出这一轮：\n"
    "讲：<一句话>\n问：<一个能引出一段话的问题>\n"
    "若本节确实已问齐，就只回『【本节已掌握】…』一行。"
)


def _points_fallback_q(section: dict) -> str:
    """**making-of 模式**的退化兜底问：扣着本节要点，而不是无意义的「复述上面这段」。
    只在作者是【亲历者】时用——问的是决策/踩坑/取舍，学习模式下他答不上来。"""
    return f"围绕这一节要讲清的点（{section['points']}），说说你的决策 / 踩坑 / 取舍，有没有具体数字。"


def _points_fallback_q_learn(section: dict) -> str:
    """学习模式的兜底问题：围绕本节概念检查理解，不要求作者提供未经确认的经历。"""
    return (f"围绕这一节要讲清的点（{section['points']}），用你自己的话讲讲你的理解；"
            f"其中哪一处你觉得最不直观、或者第一次看的时候最容易想岔？")


MAKINGOF = False


def _read_answer(live_path: str | None = None) -> str:
    """多行安全输入（07-19 加）：回车＝换行，【空一行再回车】＝提交，Ctrl+D 也算提交。
    起因：真人答题要换行组织段落，而裸 input() 一回车就交卷；粘贴多行还会把后面的问题「自动答掉」。
    live_path 非空时每行即时追加落盘——终端整个崩掉也最多丢正在打的那一行（正常提交后清掉）。"""
    print("✍️  你（回车＝换行，空一行再回车＝提交）：")
    lines: list[str] = []
    while True:
        try:
            ln = input()
        except EOFError:                      # Ctrl+D：也算提交
            break
        if not ln.strip():
            if lines:                         # 空行且已有内容 → 提交
                break
            continue                          # 开头的空行忽略，继续等正文
        lines.append(ln)
        if live_path:                         # 逐行落盘（崩溃保险）
            with open(live_path, "a", encoding="utf-8") as f:
                f.write(ln + "\n")
    if live_path and lines:
        try:
            os.remove(live_path)
        except OSError:
            pass
    return "\n".join(lines).strip()


def section_messages(section: dict, material: str, covered: list[str] | None = None) -> list[dict]:
    """构造本节导师消息，提供素材、可选作者背景和已完成节的摘要。"""
    return [{"role": "user", "content": (
        AUTHOR_PROFILE + "\n\n"
        f"这一节标题：{section['title']}\n要讲到的要点：{section['points']}\n\n"
        f"可用素材（研究员搜来的，讲解据此、别编）：\n{material}"
        + _covered_block(covered) + "\n\n"
        "开始第一轮：先讲，再问（第一轮还没问过我，别直接判定掌握）。"
    )}]


def tutor_turn(messages: list[dict]) -> str:
    """生成导师一轮，并将最终采用的输出追加到 messages。
    空输出或格式不符时提示格式、增加预算并重试；调用方不要重复追加。"""
    out = _ask(TIERS["galleon"], TUTOR_SYS, messages)
    tries, budget = 0, 3200          # 与 _ask 的默认预算保持一致
    while (not out.strip() or (DONE_TAG not in out and "问：" not in out)) and tries < TUTOR_RETRY:
        budget = min(budget * 2, _CHAT_TOKENS_CAP)
        _warn(f"  ↻ 导师这轮没按格式，提示后重抽（预算 →{budget}）……")
        messages.append({"role": "assistant", "content": out})
        messages.append({"role": "user", "content": _FORMAT_NUDGE})
        out = _ask(TIERS["galleon"], TUTOR_SYS, messages, budget)
        tries += 1
    messages.append({"role": "assistant", "content": out})
    return out


def tutor_mastered(out: str, n_records: int) -> bool:
    """出口①：导师吐了暗号才算掌握。

    `n_records > 0` 是硬约束——防它第一轮还没问就判「掌握」。
    没问过就说人家懂了，那不是判断，是省事。"""
    return DONE_TAG in out and n_records > 0


def tutor_split(out: str, section: dict, makingof: bool) -> tuple[str, str]:
    """拆「讲 / 问」。兜底问按模式选，**不能退化成「把上面那段复述一遍」**：
    making-of 问决策 / 踩坑 / 取舍；学习模式问「你的理解 ＋ 哪里最不直观」——
    后者作者一定答得上来，而且能引出真话而不是复述。"""
    fb = _points_fallback_q(section) if makingof else _points_fallback_q_learn(section)
    return _split_lesson_question(out, fb)


def tutor_feed(messages: list[dict], answer: str) -> None:
    """把作者的回答喂回去，并提醒导师：下一轮先判懂没懂，再决定收还是继续。"""
    messages.append({"role": "user", "content": (
        f"作者答：{answer}\n"
        f"请先判断我懂没懂这节关键点：够了就回「{DONE_TAG} + 一句理由」，没够就继续先讲、再问。"
    )})


def append_transcript(path: str | None, section: dict, round_no: int,
                      lesson: str, question: str, answer: str) -> None:
    """逐句落盘。path 为空就什么都不做（调用方不必自己判）。

    这是整条链上**唯一一份「作者本人说过什么」的物证**——终端 scrollback 会滚没、
    内存会随进程死掉，它不会。站 6 那个「代笔防不住、只能留痕」的留痕，指的就是它。"""
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"### {section['title']} · 第 {round_no} 问\n"
                f"讲：{lesson}\n问：{question}\n答：{answer}\n\n")


def _tutor_rounds(section: dict, material: str, transcript_path: str | None = None,
                  covered: list[str] | None = None):
    """导师问答生成器：yield 讲解与问题，send 接收作者答案。
    结束时通过 StopIteration.value 返回 records；输入方式与教学逻辑分离。"""
    messages = section_messages(section, material, covered)   # ← 三个驱动共用这一份
    records: list[dict] = []
    mastered = False
    for i in range(MAX_ROUNDS):                       # 护栏：最多转这么多轮（出口②的上限）
        out = tutor_turn(messages)                    # 共用：含格式守卫 + 预算翻倍 + 记进历史
        if tutor_mastered(out, len(records)):         # 出口①：导师吐暗号（且已问过至少一轮）
            print(f"\n✅ 导师判定本节已掌握：{out.replace(DONE_TAG, '').strip()}")
            mastered = True
            break

        lesson, question = tutor_split(out, section, MAKINGOF)


        answer = yield {"round": i + 1, "lesson": lesson, "question": question, "records": records}
        # 驱动方没 send（比如直接 next()）会拿到 None；当空答处理，绝不让 "None" 落进 transcript
        answer = (answer or "").strip()
        append_transcript(transcript_path, section, i + 1, lesson, question, answer)
        tutor_feed(messages, answer)
        records.append({"lesson": lesson, "q": question, "a": answer})

    if not mastered:                                  # 没从 break 出来 = 撞了熔断（出口②）
        _warn(f"⏱ 到第 {MAX_ROUNDS} 轮仍没判定掌握，本节先到这（熔断保护）。")
    return records


def tutor_section(section: dict, material: str, transcript_path: str | None = None,
                  covered: list[str] | None = None) -> list[dict]:
    """保留供内部回归检查使用的键盘输入适配器；教学循环由 _tutor_rounds 负责。"""
    rounds = _tutor_rounds(section, material, transcript_path, covered)
    live: list[dict] = []            # 指向生成器里那个 records（同一对象），中途出事也拿得到
    try:
        step = next(rounds)
        while True:
            live = step["records"]
            print(f"\n🧑‍🏫 讲：{step['lesson']}")
            print(f"❓ 第 {step['round']} 问：{step['question']}")
            answer = _read_answer(live_path=f"{transcript_path}.typing" if transcript_path else None)
            step = rounds.send(answer)
    except StopIteration as stop:
        # 正常收尾：generator 的 return 值即 records。stop.value 理论上不会是 None，
        # 但真是 None 时也不能把已答的几轮丢了 → 回落到 live。
        return stop.value if stop.value is not None else live


# ── ✍️ 编辑：以【作者的回答】为主串成正文，拿讲解校准事实，保住作者声音 ────────
EDITOR_SYS = """你是编辑。给你一节的标题、导师的讲解、以及作者用自己的话作答的记录。
把【作者的回答】串成通顺的中文段落，作为这节正文：
- 以作者自己的说法和语气为主——这是他的博客，要保住他的声音；
- 可参考导师讲解把事实订正准确、把作者漏掉的关键点轻轻补上，但**不要整段替他重写、不要灌入他没表达过的观点**；
- 不堆华丽辞藻。
只输出正文段落，**不要输出任何 # 开头的标题行**（节标题由程序统一加）。"""


def edit_section(section: dict, records: list[dict]) -> str:
    log = "\n\n".join(
        f"导师讲：{r['lesson']}\n问：{r['q']}\n作者答：{r['a']}" for r in records
    )


    body = _chat(TIERS["galleon"], EDITOR_SYS, f"节标题：{section['title']}\n\n{log}", max_tokens=2000)
    # 硬约束：prompt 劝它别加标题，但模型偶尔不听 → 代码里把 # 开头的行直接砍掉，防重复
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#")).strip()
    if not body:
        body = "\n\n".join(r["a"] for r in records if r.get("a", "").strip())
    return body


# ── 🔍 终审 critic：定稿前最后一道把关，只挑错给建议、绝不替作者改写 ──────────
# 终审使用一次模型调用，提出意见并交回作者决定。
CRITIC_SYS = """你是技术博客的终审，在文章定稿前做最后一道把关。给你一篇已经写好的初稿，
你只做一件事：【挑问题、给修改建议】——【绝不替作者改写】，所有建议都交回作者自己改。

作者是【正在学这个主题的开发者】，写的是学习博客；别要求他补生产实战经历，只就【已经写出来的内容】把关。

守则：【保真 > 好看，先核后饰】——先挑硬伤，再挑软的，严格按这个顺序输出：
1.【事实 / 逻辑】有没有说错、自相矛盾、或没讲通的地方（最重要）；
2.【清晰】哪句话读者会看不懂、或太含糊；
3.【结构】前后有没有重复、跑题、或顺序别扭。

每条都要：① 引一小段原文（让作者能定位到哪）；② 说清问题；③ 给一个可操作的改法建议。
某个方面没问题就【明说"这块没问题"】，不要为了凑数硬找毛病。
只输出这份审稿意见，别重写正文、别寒暄。"""


def slugify(topic: str, cap: int = 40) -> str:
    """将选题转换为本地作品文件名。"""
    return "".join(c if (c.isalnum() or c in "-_") else "-" for c in topic)[:cap].strip("-") or "run"


def parse_draft_md(md: str) -> tuple[str, list[dict]]:
    """从初稿 markdown 里反解出选题（# 一级标题）和提纲（## 二级标题）。
    有了它，单独跑终审时只要给一个 .md 文件就够，不用再单独喂选题和提纲。"""
    topic, titles = "", []
    for line in md.splitlines():
        t = line.strip()
        if t.startswith("# ") and not topic:
            topic = t[2:].strip()
        elif t.startswith("## "):
            titles.append(t[3:].strip())
    return topic, [{"title": x, "points": ""} for x in titles]


def critique_basis(draft: str, topic: str, outline, material: str) -> str:
    """由代码声明终审是否获得素材依据，区分有材料可对照与仅基于正文判断。"""
    mark = lambda x: "✓" if x else "✗"      # noqa: E731
    line = ("〔本次审阅依据：初稿 " + mark(draft) + " ｜ 选题 " + mark(topic)
            + " ｜ 提纲 " + mark(outline) + " ｜ 素材 " + mark(material) + "〕")
    if not material:
        line += ("\n〔⚠ 未提供素材——「事实 / 逻辑」这一条是**裸判**：模型凭自身知识判断，"
                 "对文章涉及的私有内容（你自己的项目 / 实验记录 / 配置）无效。〕")
    if not outline:
        line += "\n〔⚠ 未提供提纲——「跑题 / 结构」这一条只能凭正文的小标题推断。〕"
    return line


def critique(draft: str, topic: str = "", outline: list[dict] | None = None,
             material: str = "") -> str:
    """终审返回建议，不修改正文。
    传入选题与提纲以判断结构；项目复盘还需传入素材。输出附有代码生成的依据声明。"""
    parts = [f"以下是待终审的初稿：\n\n{draft}"]
    if topic:
        parts.append(f"\n\n【原定选题】{topic}")
    if outline:
        parts.append("\n\n【原定提纲】\n" + "\n".join(
            f"- {s['title']}" + (f"｜{s['points']}" if s.get("points") else "") for s in outline))
    if material:
        parts.append(f"\n\n【可用素材】（判断事实性问题时以此为准；素材没提到的，"
                     f"说「素材未覆盖」而不是断言它错）\n{material}")
    else:
        parts.append("\n\n【注意】本次**没有提供素材**。判断事实性问题时你只能凭自身知识，"
                     "遇到只有作者本人才知道的细节（他的项目结构 / 实验数据 / 配置），"
                     "请明说「这一条我无法核实」，**不要凭感觉断言它对或错**。")
    # 8000 不是 3000：终审要出三个维度、每条引原文 + 说问题 + 给改法，是典型的
    # 「长结构化输出」；而模型会先推理、推理也吃预算。3000 起步必然白花一次调用。
    review = _chat(TIERS["galleon"], CRITIC_SYS, "".join(parts), max_tokens=8000)
    return critique_basis(draft, topic, outline, material) + "\n\n" + review
