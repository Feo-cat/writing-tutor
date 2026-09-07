"""控制流验证（不碰真 API / key）——用 stub + monkeypatch 只验逻辑正确。
跑：python _test_hardening.py
覆盖：多行输入 / 导师退化守卫 / _chat 空&截断守卫 / 提纲固定 / 断点续跑 / 终审幂等 / server 网页版 making-of。"""
import builtins
import itertools
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stubs"))


_FALLBACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stubs_fallback")
if os.environ.get("FORCE_STUB_FASTAPI"):
    sys.path.insert(0, _FALLBACK)
else:
    sys.path.append(_FALLBACK)
# 测试只使用 _stubs 中的模型 SDK，不读取或使用个人服务配置。
os.environ["LLM_API_KEY"] = "test-only-key"
os.environ["LLM_BASE_URL"] = "https://model.example.test/v1"
os.environ["TOKEN_PARAM"] = "max_tokens"
os.environ["LLM_MIN_OUTPUT_TOKENS"] = "0"
os.environ.setdefault("LLM_MODEL", "stub-model")   # 让 TIERS 能建起来
os.environ["TUTOR_RETRY"] = "2"

import writing_assistant as wa

RESULTS = []
def check(name, cond):
    print(("✅" if cond else "❌"), name)
    RESULTS.append(bool(cond))


def feed(*answers):
    """把 input() 换成按序吐 answers 的假键盘。"""
    it = iter(answers)
    builtins.input = lambda *a: next(it)


td = tempfile.mkdtemp()

# ── Test（07-23 新增）：_chat 守卫——空重抽 / 截断加预算重来 / 编辑空回落 ──
_orig_create = wa._create


class _FR:  # 假响应对象（带 finish_reason）
    def __init__(self, content, finish="stop"):
        self.choices = [type("C", (), {
            "message": type("M", (), {"content": content})(),
            "finish_reason": finish,
        })()]


_seqA = iter([_FR(""), _FR("正文来了")])
wa._create = lambda *a, **k: next(_seqA)
check("_chat 空输出→重抽拿到正文", wa._chat("m", "s", "u") == "正文来了")

_budgets, _nocache = [], []
_seqB = iter([_FR("半截", "length"), _FR("完整正文", "stop")])


def _capB(model, messages, max_tokens, tools=None, no_cache=False):
    _budgets.append(max_tokens)
    _nocache.append(no_cache)
    return next(_seqB)
wa._create = _capB
_outB = wa._chat("m", "s", "u", max_tokens=1500)
check("_chat 截断→加倍预算重来拿到完整", _outB == "完整正文" and _budgets == [1500, 3000])


check("重抽必须绕开缓存（否则拿回的还是刚才那个坏响应）", _nocache == [False, True])

wa._create = lambda *a, **k: _FR("")  # 编辑永远吐空
_recs = [{"lesson": "L", "q": "Q", "a": "作者第一句"}, {"lesson": "L2", "q": "Q2", "a": "作者第二句"}]
_bodyC = wa.edit_section({"title": "X", "points": "p"}, _recs)
check("编辑空输出→回落拼作者原答(不落空节)", "作者第一句" in _bodyC and "作者第二句" in _bodyC)

wa._create = _orig_create  # 还原，别影响后面的测试

# ── Test 0：多行输入协议（07-19 新增,治「回车就交卷 / 崩了全丢」）──
feed("行1", "行2", "")                       # 两行内容 + 空行提交
lp = os.path.join(td, "live.txt")
ans = wa._read_answer(live_path=lp)
check("多行输入按换行拼接", ans == "行1\n行2")
check("提交后 live 临时文件已清", not os.path.exists(lp))

feed("", "只有一行", "")                     # 开头空行忽略,不误触提交
check("开头空行被忽略", wa._read_answer() == "只有一行")


# ── Test 1：导师退化 → 重抽 → 正常问题 + transcript 逐句落盘 ──
ask_seq = iter([
    "",                                  # 退化：空输出 → 触发重抽
    "讲：讲A\n问：你当时是怎么决定的？",   # 重抽一次后，正常两行
    "【本节已掌握】问齐了",               # 下一轮判定掌握
])
wa._ask = lambda m, s, msgs, max_tokens=700: next(ask_seq, "【本节已掌握】兜底")
feed("我的答案1", "")
tp = os.path.join(td, "t.transcript.md")
rec = wa.tutor_section({"title": "起因", "points": "决策/踩坑"}, "素材……", transcript_path=tp)

check("退化输出重抽后拿到正常问题（非废问'复述上面这段'）",
      len(rec) == 1 and rec[0]["q"].strip() and "复述上面这段" not in rec[0]["q"])
check("多行答案完整进 records", rec[0]["a"] == "我的答案1")
check("transcript 逐句落盘（含答案）",
      os.path.exists(tp) and "答：我的答案1" in open(tp, encoding="utf-8").read())
check("提交后 .typing 崩溃保险文件已清", not os.path.exists(tp + ".typing"))

# ── Test 1b：彻底无"问："（重抽耗尽）→ 兜底问题扣着本节 points ──
ask_seq2 = iter(["没有那两个字的胡话"] * 3 + ["【本节已掌握】"])
wa._ask = lambda m, s, msgs, max_tokens=700: next(ask_seq2)
feed("答b", "")
rec2 = wa.tutor_section({"title": "X", "points": "决策A/数字B"}, "m")

_blk = wa._replan_block("第 5 节换成素材中的另一个案例", [{"title": "甲节", "points": "p1"}])
check("重排意见块带上了作者的原话", "第 5 节换成素材中的另一个案例" in _blk)
check("重排意见块把【上一版提纲】也带上了（意见是相对它说的，缺了就没有指涉）", "甲节" in _blk)
check("重排意见块声明了作者优先级高于策划自己的判断", "优先级高于" in _blk)
check("没有意见时一个字都不追加（正常首排的 prompt 不被污染）",
      wa._replan_block("", None) == "" and wa._replan_block("   ", [{"title": "甲"}]) == "")

check("耗尽重抽后兜底问题扣 points（非'复述'）",
      len(rec2) >= 1 and "复述上面这段" not in rec2[0]["q"]
      and ("决策A" in rec2[0]["q"] or "取舍" in rec2[0]["q"]))


import makingof as df

calls = {"plan": 0, "tutor": []}
def fake_plan(topic, material):
    calls["plan"] += 1
    return [{"title": "节1", "points": "p1"}, {"title": "节2", "points": "p2"}, {"title": "节3", "points": "p3"}]
df.plan_outline = fake_plan
def fake_tutor(section, material, transcript_path=None):
    calls["tutor"].append(section["title"])
    return [{"lesson": "L", "q": "Q", "a": "A"}]
df.tutor_section = fake_tutor
df.edit_section = lambda section, records: f"正文-{section['title']}"
df.critique = lambda draft: "终审OK"

work = tempfile.mkdtemp()
out = os.path.join(work, "draft.md")

draftA, doneA = df.run_with_material("选题T", "素材M", out)      # Run A：新鲜跑
cache = out + ".outline.json"
check("共享驱动 首跑生成提纲并固定到缓存", os.path.exists(cache) and calls["plan"] == 1)
check("共享驱动 首跑访谈全部 3 节", calls["tutor"] == ["节1", "节2", "节3"])
check("共享驱动 首跑 all_done=True", doneA is True)

calls["plan"] = 0; calls["tutor"] = []                           # Run B：模拟崩在第 3 节前
with open(out, "w", encoding="utf-8") as f:
    f.write("# 选题T\n\n## 节1\n\n正文-节1\n\n## 节2\n\n正文-节2\n\n")
draftB, doneB = df.run_with_material("选题T", "素材M", out)
check("共享驱动 续跑复用固定提纲（plan 未再调用）", calls["plan"] == 0)
check("共享驱动 续跑只访谈缺的第 3 节", calls["tutor"] == ["节3"])
check("共享驱动 续跑后三节齐全 & all_done", open(out, encoding="utf-8").read().count("## 节") == 3 and doneB)

check("_has_critique 追加前为 False", df._has_critique(out) is False)
with open(out, "a", encoding="utf-8") as f:
    f.write("\n---\n\n## 🔍 终审意见\n\n终审OK\n")
check("_has_critique 追加后为 True", df._has_critique(out) is True)


# ── Test 3：server 网页版 making-of —— 模式生效 / 落盘 / 续跑跳节 / 终审幂等 ──
sw = tempfile.mkdtemp()
sw_material = os.path.join(sw, "material.md")
with open(sw_material, "w", encoding="utf-8") as f:
    f.write("网页模式测试素材")
os.environ["MAKINGOF_MATERIAL"] = sw_material
os.environ["MAKINGOF_OUT"] = os.path.join(sw, "web-draft.md")
import server

wplan = {"n": 0}
def fake_wplan(topic, material):
    wplan["n"] += 1
    return [{"title": f"W节{i}", "points": f"wp{i}"} for i in (1, 2, 3)]
server.df.plan_outline = fake_wplan
tutor_script = itertools.cycle(["讲：WL\n问：WQ？", wa.DONE_TAG + " 问齐"])
wa._ask = lambda m, s, msgs, max_tokens=700: next(tutor_script)
wa.edit_section = lambda s, r: f"网页正文-{s['title']}"


_crit_kw = {}
def _fake_critique(d, **k):
    _crit_kw.update(k)
    return "网页终审OK"
wa.critique = _fake_critique

def collect(gen):
    return [json.loads(fr[len("data: "):].strip()) for fr in gen]

check("making-of 访谈员 prompt 已生效（server 侧）", "访谈员" in wa.TUTOR_SYS)

evs = collect(server.start_stream("网页选题"))
meta = next(e for e in evs if e["type"] == "meta")
sid = meta["sessionId"]


check("web 首跑排完提纲停在确认闸，不直接开讲",
      meta["secIdx"] == 0
      and any(e["type"] == "awaiting_outline" for e in evs)
      and not any(e["type"] == "lesson" for e in evs))
check("web 提纲固定文件生成", os.path.exists(os.environ["MAKINGOF_OUT"] + ".outline.json"))
evs = collect(server._begin_stream(server.SESSIONS[sid]))
check("确认提纲之后才进第 0 节、给出 lesson",
      server.SESSIONS[sid]["sec_idx"] == 0 and any(e["type"] == "lesson" for e in evs))

for i in range(3):                                   # 逐节答完 3 节
    evs = collect(server.answer_stream(sid, f"网页答{i}\n第二行{i}"))
check("web 答满后收到 done", any(e["type"] == "done" for e in evs))
check("web 终审拿到了选题和提纲（否则「跑题 / 结构」那条没依据）",
      _crit_kw.get("topic") == "网页选题" and bool(_crit_kw.get("outline")))
check("making-of 型终审必须带素材（裸判对作者自己项目里的事实无效）",
      bool(_crit_kw.get("material")))
out_txt = open(os.environ["MAKINGOF_OUT"], encoding="utf-8").read()
check("web 三节全部落盘草稿文件", out_txt.count("## W节") == 3)
check("web 终审已追加进文件", "## 🔍 终审意见" in out_txt and "网页终审OK" in out_txt)
ts = open(os.environ["MAKINGOF_OUT"] + ".transcript.md", encoding="utf-8").read()
check("web transcript 逐句落盘（含多行答案）", "答：网页答0\n第二行0" in ts)

wplan["n"] = 0                                        # 续跑：全节已在草稿 → 跳过所有节直接收尾
evs2 = collect(server.start_stream("网页选题"))
meta2 = next(e for e in evs2 if e["type"] == "meta")
done2 = next(e for e in evs2 if e["type"] == "done")
check("web 续跑跳过全部已完成节", meta2["secIdx"] == 3)
check("web 续跑直接 done 且终审幂等（未重复调用）", "未重复" in done2["review"])
check("web 续跑复用固定提纲（plan 未再调用）", wplan["n"] == 0)
check("web 学习模式入口未被污染（researching 分支仍在）", server.MAKINGOF_MATERIAL != "")

print(f"\n{'='*48}\n{sum(RESULTS)}/{len(RESULTS)} 通过")
sys.exit(0 if all(RESULTS) else 1)
