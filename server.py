"""本地单用户 Web 后端：FastAPI 接口与 SSE 事件流。
React 页面位于 web/。MAKINGOF_MATERIAL 指定本地素材时启用项目复盘模式，
复用 makingof 的提示词和提纲、续写、终审识别函数。"""

import json
import os
import traceback
import uuid

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import writing_assistant as wa
from writing_assistant import MAX_ROUNDS


os.environ.setdefault("GW_SOURCE", "web")

# 守卫动作的收集口：设上它，wa 里那些「撞顶翻倍 / 绕缓存重抽 / 格式不合重抽」
# 就不再只 print 到终端，而是能被排空成 SSE 事件推给前端。
# ⚠️ 模块级共享一个 list，成立的前提是**本地单用户、请求串行**——和 GW_SOURCE 同一个前提。
wa._WARN_SINK = []


def _stamp(sess: dict) -> str:
    """按选题生成稳定的来源戳，用于可选网关账本按文章汇总。"""
    return wa.stamp_source("web", wa.post_key(sess.get("topic") or ""))


def _warns():
    """把引擎收集的重试、截断等提示转成 SSE 事件。"""
    for m in wa.drain_warnings():
        yield sse({"type": "warn", "text": m})

MAKINGOF_MATERIAL = os.getenv("MAKINGOF_MATERIAL", "").strip()
MAKINGOF_OUT = (os.getenv("MAKINGOF_OUT", "").strip()
                or os.path.join(wa.BLOG_ARTIFACTS_DIR, "draft-makingof.md"))
if MAKINGOF_MATERIAL:
    import makingof as df


def _artifact(filename: str) -> str:
    """所有 Web 创作产物只落本地私有目录；API 对外仍只暴露 basename。"""
    return wa.blog_artifact_path(filename)

app = FastAPI(title="写作助手")


@app.get("/api/config")
def api_config():
    status = wa.configuration_status()
    if MAKINGOF_MATERIAL and not (os.path.isfile(MAKINGOF_MATERIAL) and os.access(MAKINGOF_MATERIAL, os.R_OK)):
        status["issues"].append("项目复盘素材无法读取，请检查 MAKINGOF_MATERIAL 的文件路径与权限。")
        status["ready"] = False
    status["mode"] = "makingof" if MAKINGOF_MATERIAL else "learn"
    return status


def _require_configuration():
    status = api_config()
    if not status["ready"]:
        raise wa.ConfigurationError(" ".join(status["issues"]))

# ── 会话存储：本地单用户，内存里一个 dict 就够 ──────────────────────────────────
# 每个会话保存以下写作状态：
#   {topic, messages, material, outline, sec_idx, parts, records, pending, draft}
SESSIONS: dict[str, dict] = {}


def gen_material(topic: str) -> str:
    if MAKINGOF_MATERIAL:                       # making-of：手写素材顶替研究员，绝不上网搜
        return open(MAKINGOF_MATERIAL, encoding="utf-8").read()
    return wa.research_topic(topic)


def gen_outline(topic: str, material: str, feedback: str = "",
                previous: list[dict] | None = None) -> list[dict]:
    """feedback ＝ 作者对上一版提纲的意见（只有重排那条路会给）。
    带着意见重排时**必须连上一版一起给策划**，理由见 wa._replan_block。"""
    if MAKINGOF_MATERIAL and not (feedback or previous):
        return df._load_or_make_outline(topic, material, MAKINGOF_OUT + ".outline.json")


    cache = (MAKINGOF_OUT + ".outline.json") if MAKINGOF_MATERIAL else _artifact(f"{wa.slugify(topic)}.outline.json")
    if not (feedback or previous) and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("topic") == topic and cached.get("outline"):
                print(f"  ♻ 复用已缓存的提纲（{len(cached['outline'])} 节）：{cache}")
                return cached["outline"]
        except Exception as e:                  # noqa: BLE001
            print(f"  ⚠ 提纲缓存读不了（{e}），重排一次")
    outline = wa.plan_outline(topic, material, feedback, previous)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(outline if MAKINGOF_MATERIAL else {"topic": topic, "outline": outline},
                  f, ensure_ascii=False, indent=2)
    return outline


def tutor_turn(messages: list[dict]) -> str:
    """导师这一轮：要么「讲+问」，要么吐暗号判定掌握。"""


    return wa.tutor_turn(messages)


def gen_body(section: dict, records: list[dict]) -> str:
    return wa.edit_section(section, records)


def gen_review(draft: str, sess: dict | None = None) -> str:
    """生成终审意见，并提供选题、提纲；项目复盘模式还提供本地素材。"""
    if sess is None:
        return wa.critique(draft)
    return wa.critique(
        draft,
        topic=sess.get("topic", ""),
        outline=sess.get("outline") or None,
        material=sess.get("material", "") if MAKINGOF_MATERIAL else "",
    )


# ── SSE 小工具：把一个 dict 编成一帧 text/event-stream（前端 api.ts 按 \n\n 切帧解析）──
def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _build_section_messages(s: dict, material: str, covered: list[str] | None = None) -> list[dict]:
    """构造本节教学消息，提供素材、可选作者背景和已覆盖内容。"""


    return wa.section_messages(s, material, covered)


def _start_section(sess: dict) -> None:
    """建这一节的对话记忆 + 拿导师第一轮（讲+问），存进 pending。"""
    s = sess["outline"][sess["sec_idx"]]
    sess["messages"] = _build_section_messages(s, sess["material"], sess.get("parts"))
    sess["records"] = []
    out = tutor_turn(sess["messages"])   # 它自己会记进 messages
    sess["pending"] = out


def _split_pending(sess: dict) -> tuple[str, str]:
    """拆 pending 为（讲, 问）。making-of 的兜底问扣本节要点；学习模式保持「复述」兜底。"""
    return wa.tutor_split(sess["pending"], sess["outline"][sess["sec_idx"]],
                          bool(MAKINGOF_MATERIAL))


def _lesson_event(sess: dict) -> str:
    """把 pending 拆成 讲/问，编成一个 lesson 事件（前端负责逐字打出来）。"""
    lesson, question = _split_pending(sess)
    return sse({"type": "lesson", "lesson": lesson, "question": question,
                "secIdx": sess["sec_idx"], "total": len(sess["outline"])})


def _ensure_draft_header(topic: str) -> None:
    """making-of：草稿文件不存在才落标题（续写只追加，不清空已有内容）。"""
    if not os.path.exists(MAKINGOF_OUT):
        os.makedirs(os.path.dirname(os.path.abspath(MAKINGOF_OUT)), exist_ok=True)
        with open(MAKINGOF_OUT, "w", encoding="utf-8") as f:
            f.write(f"# {topic}\n\n")


def _finalize(sess: dict) -> tuple[str, str]:
    """收尾：making-of 以草稿【文件】为准（含上一次网页完成的节），终审幂等；
    学习模式维持原来的内存拼接。返回 (draft, review)。"""
    if MAKINGOF_MATERIAL:
        draft = sess["draft"] = open(MAKINGOF_OUT, encoding="utf-8").read()
        if df._has_critique(MAKINGOF_OUT):
            return draft, "（终审意见此前已生成，就在草稿文件末尾，未重复调用。）"
        review = gen_review(draft, sess)
        with open(MAKINGOF_OUT, "a", encoding="utf-8") as f:
            f.write("\n---\n\n## 🔍 终审意见\n\n" + review + "\n")
        return draft, review
    slug = sess.get("slug") or wa.slugify(sess["topic"])
    _p = _artifact(f"{slug}.md")
    if os.path.exists(_p):
        # **以文件为准**，跟 making-of 一个规矩：续跑时文件里含着此前那次写好的节，
        # 而内存里的 parts 只有这一次跑的（加上启动时回填的）。文件才是完整的那份。
        with open(_p, encoding="utf-8") as f:
            draft = f.read().strip()
    else:
        draft = f"# {sess['topic']}\n\n" + "\n\n".join(sess["parts"])
    sess["draft"] = draft
    review = gen_review(draft, sess)
    with open(_artifact(f"{slug}.review.md"), "w", encoding="utf-8") as f:
        f.write(f"# 终审意见 · {sess['topic']}\n\n{review}\n")
    return draft, review


# ── SSE 流 1：开篇 —— 研究员 → 策划 → 进第一节、导师第一轮 ──────────────────────
def start_stream(topic: str, confirm_outline: bool = True):
    _require_configuration()
    if not topic.strip():
        raise wa.ConfigurationError("请先输入选题。")
    if MAKINGOF_MATERIAL:
        os.makedirs(os.path.dirname(os.path.abspath(MAKINGOF_OUT)), exist_ok=True)
    sid = uuid.uuid4().hex
    sess = SESSIONS[sid] = {"topic": topic, "sec_idx": 0, "parts": []}

    # 学习模式的边车文件名先算出来——下面研究员那步就要用它判断能不能复用。
    sess["slug"] = wa.slugify(topic) if not MAKINGOF_MATERIAL else ""
    _stamp(sess)

    yield sse({"type": "phase", "phase": "researching"})    # making-of 模式这步秒过：读手写素材
    _mat = _artifact(f"{sess['slug']}.material.md") if sess["slug"] else ""
    if _mat and os.path.exists(_mat):


        with open(_mat, encoding="utf-8") as f:
            sess["material"] = f.read()
        print(f"  ♻ 复用已缓存素材（{len(sess['material'])} 字）：{_mat}")
    else:
        sess["material"] = gen_material(topic)
        yield from _warns()


        if _mat:
            with open(_mat, "w", encoding="utf-8") as f:
                f.write(sess["material"])

    yield sse({"type": "phase", "phase": "planning"})       # 策划分节（making-of：提纲固定 + 复用）
    outline = sess["outline"] = gen_outline(topic, sess["material"])
    yield from _warns()

    if MAKINGOF_MATERIAL:                      # 断点续跑：草稿里已有的节直接跳过
        _ensure_draft_header(topic)
        done = df._done_titles(MAKINGOF_OUT)
        while sess["sec_idx"] < len(outline) and outline[sess["sec_idx"]]["title"] in done:
            sess["sec_idx"] += 1
    else:


        draft_path = _artifact(f"{sess['slug']}.md") if sess.get("slug") else ""
        if draft_path and os.path.exists(draft_path):
            with open(draft_path, encoding="utf-8") as f:
                _, done_secs = wa.parse_draft_md(f.read())
            done = {x["title"] for x in done_secs}
            while sess["sec_idx"] < len(outline) and outline[sess["sec_idx"]]["title"] in done:
                # 已写过的节，正文也要塞回 parts——否则最终稿会缺章，
                # 而且后面几节的导师看不到「这些讲过了」。
                with open(draft_path, encoding="utf-8") as f:
                    _md = f.read()
                _t = outline[sess["sec_idx"]]["title"]
                _i = _md.find(f"## {_t}")
                if _i >= 0:
                    _j = _md.find("\n## ", _i + 1)
                    sess["parts"].append(_md[_i:_j if _j > 0 else len(_md)].strip())
                sess["sec_idx"] += 1
            if sess["sec_idx"]:
                print(f"  ♻ 断点续跑：前 {sess['sec_idx']} 节已在 {draft_path} 里，跳过")

    yield sse({"type": "meta", "sessionId": sid,
               "outline": [s["title"] for s in outline],
               "secIdx": sess["sec_idx"], "total": len(outline)})


    if confirm_outline and sess["sec_idx"] < len(outline):
        yield sse({"type": "awaiting_outline",
                   "outline": [{"title": x["title"], "points": x.get("points", "")}
                               for x in outline]})
        yield sse({"type": "end"})
        return

    yield from _begin_stream(sess)


def _begin_stream(sess: dict):
    """确认提纲之后真正开跑：各节答齐就直接收尾，否则进第一节、导师第一轮。"""
    _require_configuration()
    outline = sess["outline"]
    if sess["sec_idx"] >= len(outline):                     # 各节早已答齐 → 直接收尾（终审幂等）
        yield sse({"type": "phase", "phase": "reviewing"})
        draft, review = _finalize(sess)
        yield sse({"type": "done", "draft": draft, "review": review})
        yield sse({"type": "end"})
        return

    yield sse({"type": "phase", "phase": "teaching"})       # 导师备课、组织第一轮
    _stamp(sess)
    _start_section(sess)
    yield from _warns()
    yield _lesson_event(sess)
    yield sse({"type": "end"})


# ── SSE 流 2：你答一轮 —— 双出口（暗号 / 熔断）；收尾则编辑+终审 ─────────────────
def answer_stream(sid: str, answer: str):
    sess = SESSIONS.get(sid)
    if not sess:
        yield sse({"type": "error", "message": "会话已过期，请刷新页面重来。"})
        return

    _require_configuration()
    if not answer.strip():
        raise wa.ConfigurationError("请先填写你的回答。")
    if "pending" not in sess or sess["sec_idx"] >= len(sess["outline"]):
        raise wa.ConfigurationError("当前没有等待回答的问题，请先确认提纲或重新打开选题。")
    # 记下你这轮的回答（pending 是你正在答的那一「讲+问」）
    lesson, question = _split_pending(sess)
    sess["records"].append({"lesson": lesson, "q": question, "a": answer})


    _tp = (MAKINGOF_OUT + ".transcript.md") if MAKINGOF_MATERIAL else (
        _artifact(f"{sess['slug']}.transcript.md") if sess.get("slug") else "")
    wa.append_transcript(_tp, sess["outline"][sess["sec_idx"]], len(sess["records"]),
                         lesson, question, answer)
    wa.tutor_feed(sess["messages"], answer)                 # 共用

    yield sse({"type": "phase", "phase": "teaching"})       # 导师在听、想下一步
    _stamp(sess)
    out = tutor_turn(sess["messages"])   # 它自己会记进 messages
    yield from _warns()

    # 双出口：① 导师吐暗号判定掌握（硬约束：没问过至少一轮不许判）② 撞 MAX_ROUNDS 熔断
    if wa.tutor_mastered(out, len(sess["records"])) or len(sess["records"]) >= MAX_ROUNDS:
        s = sess["outline"][sess["sec_idx"]]
        body = f"## {s['title']}\n\n{gen_body(s, sess['records'])}"     # 编辑串成段
        yield from _warns()
        sess["parts"].append(body)
        if MAKINGOF_MATERIAL:                  # 每完成一节即追加进草稿文件（续跑之锚）
            with open(MAKINGOF_OUT, "a", encoding="utf-8") as f:
                f.write(body + "\n\n")
        elif sess.get("slug"):


            _p = _artifact(f"{sess['slug']}.md")
            if not os.path.exists(_p):
                with open(_p, "w", encoding="utf-8") as f:
                    f.write(f"# {sess['topic']}\n\n")
            with open(_p, "a", encoding="utf-8") as f:
                f.write(body + "\n\n")
        sess["sec_idx"] += 1
        if sess["sec_idx"] < len(sess["outline"]):         # 还有下一节 → 进下一节、教第一轮
            yield sse({"type": "section_advance", "secIdx": sess["sec_idx"],
                       "total": len(sess["outline"]),
                       "title": sess["outline"][sess["sec_idx"]]["title"]})
            yield sse({"type": "phase", "phase": "teaching"})
            _start_section(sess)
            yield _lesson_event(sess)
        else:                                              # 全篇收尾 → 拼初稿 + 终审
            yield sse({"type": "phase", "phase": "reviewing"})
            draft, review = _finalize(sess)
            yield from _warns()
            yield sse({"type": "done", "draft": draft, "review": review})
    else:                                                  # 没收 → 换成新的「讲+问」
        sess["pending"] = out
        yield _lesson_event(sess)
    yield sse({"type": "end"})


# ── HTTP 接口 ─────────────────────────────────────────────────────────────────
class StartReq(BaseModel):
    topic: str
    model_config = {"extra": "forbid"}
    confirmOutline: bool = True     # 提纲出来先停一下给人看，默认开


class AnswerReq(BaseModel):
    sessionId: str
    answer: str


class SessionReq(BaseModel):
    sessionId: str
    feedback: str = ""      # 只有 /api/replan 用：作者对上一版提纲的意见，可空


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _guard_stream(gen):
    """把生成器异常转换为 error 和 end 事件，避免页面无限等待。"""
    try:
        yield from gen
    except Exception as e:                                  # noqa: BLE001
        # 保留代码位置，避免将服务商原始响应或个人正文回显到日志和网页。
        traceback.print_tb(e.__traceback__)
        print(f"{type(e).__name__}: {wa.public_error(e)}")
        yield sse({"type": "error", "message": wa.public_error(e)})
        yield sse({"type": "end"})


@app.post("/api/start")
def api_start(req: StartReq):
    return StreamingResponse(_guard_stream(start_stream(req.topic.strip(), req.confirmOutline)),
                             media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/api/answer")
def api_answer(req: AnswerReq):
    return StreamingResponse(_guard_stream(answer_stream(req.sessionId, req.answer)),
                             media_type="text/event-stream", headers=_SSE_HEADERS)


def _sess_or_err(sid: str):
    sess = SESSIONS.get(sid)
    if sess:
        return sess, None

    def gone():
        yield sse({"type": "error", "message": "会话已过期，请刷新页面重来。"})
    return None, gone()


@app.post("/api/begin")
def api_begin(req: SessionReq):
    """确认提纲、开始带教。"""
    sess, err = _sess_or_err(req.sessionId)
    gen = err if err is not None else _guard_stream(_begin_stream(sess))
    return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/api/replan")
def api_replan(req: SessionReq):
    """提纲不满意 → 根据反馈重排并更新缓存。**素材不重搜**（那是两分钟真金白银），
    只重跑策划那一次调用。"""
    sess, err = _sess_or_err(req.sessionId)
    if err is not None:
        return StreamingResponse(err, media_type="text/event-stream", headers=_SSE_HEADERS)

    def gen():
        _require_configuration()
        if sess.get("sec_idx", 0) or sess.get("pending") or sess.get("parts"):
            raise wa.ConfigurationError("已经开始写作的提纲不能直接重排，请先保存作品并使用新的选题。")
        yield sse({"type": "phase", "phase": "planning"})
        _prev = sess.get("outline")        # 先留住上一版：意见是相对它说的
        outline = sess["outline"] = gen_outline(
            sess["topic"], sess["material"], req.feedback, _prev)
        sess["sec_idx"] = 0
        sess["parts"] = []
        yield sse({"type": "meta", "sessionId": req.sessionId,
                   "outline": [x["title"] for x in outline],
                   "secIdx": 0, "total": len(outline)})
        yield sse({"type": "awaiting_outline",
                   "outline": [{"title": x["title"], "points": x.get("points", "")}
                               for x in outline]})
        yield sse({"type": "end"})
    return StreamingResponse(_guard_stream(gen()), media_type="text/event-stream", headers=_SSE_HEADERS)


class CritiqueReq(BaseModel):
    draft: str = ""          # 直接粘正文
    draftPath: str = ""      # 或者点名工作目录下的一个 .md
    material: str = ""       # 直接粘素材
    materialPath: str = ""


def _read_local(name: str) -> str:
    """只读本地博客产物目录下的文件。拒绝路径分隔符——这是本机自用服务，但**「本机自用」不是
    不做输入校验的理由**：一个 ../ 就能把任意文件读出来回显到页面上。"""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"非法文件名：{name!r}（只接受博客产物目录下的文件名）")
    path = _artifact(name)
    if not os.path.exists(path):
        raise ValueError(f"找不到文件：{name}")
    with open(path, encoding="utf-8") as f:
        return f.read()


@app.get("/api/drafts")
def api_drafts():
    """列出本地博客产物目录下可审的 .md。有了它前端只要一个下拉框，不用做文件上传——
    multipart 那一整套（路由 / file input / 大小限制 / 错误处理）换来的能力，
    一个下拉框加一个粘贴框就覆盖了。"""
    mds = sorted(f for f in os.listdir(wa.blog_artifacts_dir())
                 if f.endswith(".md") and not f.endswith(".review.md"))
    return {"drafts": mds}


# ── 档案：把「跑过的东西」变成看得见的 ────────────────────────────────────────
# 一次真跑二十分钟，而演示窗口可能只有三分钟——**所以真正该做的不是让跑起来更好看，
# 是让已经跑过的东西看得见**。这条链的每一步产物本来就都在盘上，缺的只是一个入口。
_ARCHIVE_PARTS = (("material", ".material.md"), ("outline", ".outline.json"),
                  ("transcript", ".transcript.md"), ("draft", ".md"), ("review", ".review.md"))


def _archive_slugs() -> list[str]:
    """凡是有 .md 的都算一篇（.md 是这条链的主产物）。"""
    return sorted(f[:-3] for f in os.listdir(wa.blog_artifacts_dir())
                  if f.endswith(".md") and not any(
                      f.endswith(x) for _k, x in _ARCHIVE_PARTS if x != ".md"))


@app.get("/api/archive")
def api_archive():
    """列出跑过的每一篇，以及它各留下了哪几样产物。

    **「有没有」和「有多大」都报出来**：一篇只有 draft、没有 transcript 的，
    和五样齐全的，在这里一眼分得开——而不是都显示成一行标题。"""
    out = []
    for slug in _archive_slugs():
        parts, topic = {}, slug
        for key, ext in _ARCHIVE_PARTS:
            f = _artifact(slug + ext)
            parts[key] = os.path.getsize(f) if os.path.exists(f) else 0
        try:                                    # 标题以初稿里的 # 为准，文件名只是 slug
            head = open(_artifact(slug + ".md"), encoding="utf-8").readline().strip()
            if head.startswith("# "):
                topic = head[2:].strip()
        except OSError:
            pass
        out.append({"slug": slug, "topic": topic, "parts": parts,
                    "mtime": os.path.getmtime(_artifact(slug + ".md"))
                    if os.path.exists(_artifact(slug + ".md")) else 0})
    out.sort(key=lambda x: -x["mtime"])
    return {"posts": out}


@app.get("/api/archive/{slug}")
def api_archive_one(slug: str):
    """一篇的全链路产物 + 它在网关账本上的制作凭据。

    ⚠️ 账本查不到时如实返回空，**绝不拿全局总量顶上**——
    和「没有存档 ≠ 那天没东西」是同一条纪律。查不到就是查不到。"""
    try:
        _read_local(slug + ".md")               # 借它做路径校验：一个 ../ 就能读任意文件
    except ValueError as e:
        return {"error": str(e)}
    data = {"slug": slug, "parts": {}}
    for key, ext in _ARCHIVE_PARTS:
        try:
            data["parts"][key] = _read_local(slug + ext)
        except ValueError:
            data["parts"][key] = ""
    head = (data["parts"]["draft"] or "").splitlines()
    data["topic"] = head[0][2:].strip() if head and head[0].startswith("# ") else slug
    ledger_status, rows = wa.ledger_snapshot(f"web-{wa.post_key(data['topic'])}")
    by = {}
    for r in rows:
        d = by.setdefault(r.get("model") or "（未知）", {"n": 0, "tin": 0, "tout": 0, "cost": 0.0, "ms": 0})
        d["n"] += 1
        d["tin"] += r.get("in") or 0
        d["tout"] += r.get("out") or 0
        if isinstance(r.get("cost"), (int, float)) and not isinstance(r.get("cost"), bool):
            if d["cost"] is not None:
                d["cost"] += r["cost"]
        else:
            d["cost"] = None
        d["ms"] += r.get("took_ms") or 0
    answers = [x.split("\n\n")[0].strip()
               for x in (data["parts"]["transcript"] or "").split("\n答：")[1:]]
    data["receipt"] = {
        "ledger_status": ledger_status,
        "source": f"web-{wa.post_key(data['topic'])}",
        "calls": len(rows), "by_model": by,
        "cost": sum(d["cost"] for d in by.values()) if rows and all(d["cost"] is not None for d in by.values()) else None,
        "ms": sum(r.get("took_ms") or 0 for r in rows),
        "rounds": len(answers), "author_chars": sum(len(a) for a in answers),
    }
    return data


@app.post("/api/critique")
def api_critique(req: CritiqueReq):
    try:
        _require_configuration()
    except wa.ConfigurationError as e:
        return {"ok": False, "error": str(e)}
    try:
        draft = req.draft.strip() or _read_local(req.draftPath)
        material = req.material.strip() or (_read_local(req.materialPath) if req.materialPath else "")
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not draft.strip():
        return {"ok": False, "error": "没有稿子可审：draft 和 draftPath 都是空的。"}

    topic, outline = wa.parse_draft_md(draft)
    try:
        review = wa.critique(draft, topic=topic, outline=outline, material=material)
    except Exception as e:
        return {"ok": False, "error": wa.public_error(e)}
    return {
        "ok": True,
        "topic": topic,
        "sections": [x["title"] for x in outline],
        # basis 由**代码**算，不由模型说——这一行的全部价值就在于它必须准确。
        "basis": wa.critique_basis(draft, topic, outline, material),
        "review": review,
    }


# ── 生产/单进程模式：若前端已 `npm run build`，uvicorn 顺便把 dist 当静态站托管 ──────
# dev 时走 vite（带热更 + 代理），不经过这里；这段只为「一条 uvicorn 命令跑全部」兜底。
_DIST = os.path.join(os.path.dirname(__file__), "web", "dist")
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="static")
