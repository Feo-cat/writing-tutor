"""run_eval 评分层与回归判定的控制流验证（不碰真 API / key / 网络）。
跑：python _test_eval.py

这个文件测的是**评测工具自己**，不是被测系统。评测工具出错最阴险：它不会报错，
只会给出一个看着像那么回事的分数，然后那个分数被抄进结果报告。所以这里每一条断言
问的都是同一个问题——「这个数字是怎么来的，来得对不对」。

覆盖：
  评分员换档（评分员 ≠ 被测档位，同一个模型给自己打分是自评不是评测）
  锚定回避（评分员看不到「它答对了没有」这个结论）
  解析失败单列 unparsed（不冒充 unsupported，也不混进 lucky）
  蒙对 / 判错但理由站得住 的统计口径
  用例 schema 与切片（60×2 多轴 taxonomy、V1→V2 分类型报告）
  用例冻结（三类平衡、语义指纹、样本数不同时基线作废）
  跑批完整性（调用异常 / CRAG 阶段故障不得判绿、不得记基线）
  基线容差（没基线放行、掉 1 条放行、掉 2 条判红、容差读基线里记的那份）
  结论行的 tag 分割（V1-material → V1）与「不吐百分比」
  跨模块接缝：fact_checker 的 verdict 全集 × run_eval 的 V2_MAP 键集
  V1 基线不许拿到素材（拿到了它就不是基线了）"""
import io
import hashlib
import json
import os
import re
import sys
import contextlib
import importlib
import inspect
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stubs"))


sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_stubs_fallback"))

os.environ.setdefault("LLM_MODEL", "stub-model")   # 让 TIERS 能建起来
os.environ.pop("LLM_TEMPERATURE", None)
os.environ.pop("LLM_SEED", None)
# 把外面带进来的分档配置全清掉。A5 / A5b / A6 / A7 的前提是「各档回落到同一个串」，
# 而开发机上现在四档全是显式配的——shell 里 export 过一个 MODEL_SLOOP，
# 那几条的前提就被外部环境掀翻了，测试却照样打绿。**测试必须能证明自己的前提成立**，
# 靠外面没设过某个变量来成立的前提，不算成立。（同 A0 那条对 dotenv 壳的断言。）
for _k in [k for k in os.environ if k.startswith("MODEL_")]:
    os.environ.pop(_k, None)
os.environ.pop("EVAL_GRADER_TIER", None)
os.environ.pop("EVAL_SUT_MODEL", None)
os.environ.pop("EVAL_GRADER_MODEL", None)
os.environ.pop("EVAL_REQUIRE_PINNED_MODELS", None)

import writing_assistant as wa
import fact_checker as fc
import eval_schema as es
import run_eval as ev


def reload_all():
    """TIERS 在 writing_assistant import 时就建好了，改环境变量得重新 import 才生效。"""
    global wa, ev
    wa = importlib.reload(wa)
    ev = importlib.reload(ev)
    return ev

RESULTS = []
def check(name, cond):
    print(("✅" if cond else "❌"), name)
    RESULTS.append(bool(cond))


class _FR:  # 假响应对象
    def __init__(self, content):
        self.choices = [type("C", (), {
            "message": type("M", (), {"content": content})(),
            "finish_reason": "stop",
        })()]


CALLS = []          # [(model, messages, max_tokens), ...]

def stub_create(reply):
    """把 run_eval 里的 _create 换成假的，记录调用参数、返回指定内容。"""
    def _c(model, messages, max_tokens, **kw):
        CALLS.append((model, messages, max_tokens))
        if isinstance(reply, Exception):
            raise reply
        return _FR(reply)
    ev._create = _c

_ORIG_CREATE = ev._create


def row(**kw):
    """造一条 run() 会产出的行。默认值刻意填满，好让「少了哪个键就炸」暴露出来。"""
    base = {"id": 1, "label": "correct", "got": "correct", "ok": True,
            "statement": "陈述S", "confidence": 0.9, "checked": False,
            "degraded": "", "evidence": "证据E", "evidence_raw": "原始证据R",
            "search_status": "not_run", "search_queries": [], "evidence_enriched": False,
            "stage_failures": [],
            "split": "dev", "domain": "web_protocol",
            "claim_type": ["protocol_semantics"], "evidence_mode": "public_snapshot",
            "evidence_relation": "supports", "retrieval_need": "not_needed",
            "freshness": "stable", "difficulty": "easy", "risk": "standard",
            "group_id": None, "material_id": None,
            "reason": "理由Z", "error": "", "elapsed_s": 0.1}
    base.update(kw)
    return base


def case(**kw):
    """造一条合法 taxonomy 用例；run() 的直接单测也不该靠缺字段的旧格式过关。"""
    base = {"id": 1, "label": "correct", "statement": "陈述S",
            "split": "dev", "domain": "web_protocol",
            "claim_type": ["protocol_semantics"], "evidence_mode": "public_snapshot",
            "evidence_relation": "supports", "retrieval_need": "not_needed",
            "freshness": "stable", "difficulty": "easy", "risk": "standard",
            "group_id": None, "source": "https://example.test/source"}
    base.update(kw)
    return base


print("\n══ A. 评分员必须换档 ══")


check("A0：dotenv 已被 stub，.env 泄不进测试（A5~A7 的前提就靠这个）",
      getattr(wa.load_dotenv, "_is_stub", False) is True)

check("A1：默认评分员档位不是被测档位（sloop）", ev.GRADER_TIER != "sloop")
check("A2：评分员档位在 TIERS 里存在（配置本身立得住）", ev.GRADER_TIER in ev.TIERS)

# ⚠️ A3 必须在两档指向【不同模型串】的环境里断言。
# 只配 LLM_MODEL 时各档全部回落到同一个串，那时 `发出去的 == TIERS[GRADER_TIER]` 恒真——
# 就算代码写死用 sloop 也照样绿。变异测试逮到过这一条：把 TIERS[GRADER_TIER] 换成
# TIERS["sloop"]，64 条全绿。**断言在退化环境里会自动成立，就等于没断言。**
os.environ["MODEL_GALLEON"] = "stronger-stub-model"
ev = reload_all()
CALLS.clear(); stub_create('{"grade": "sound", "why": "ok"}')
ev.grade_row(row())
check("A3：grade_row 发的是评分员那一档的模型，不是被测的 sloop（两档真配了不同模型时才测得出来）",
      bool(CALLS) and CALLS[0][0] == ev.TIERS[ev.GRADER_TIER] != ev.TIERS["sloop"])
check("A4：给评分员单独配了模型后，自评标记为假（它比的是模型串，不是档名）",
      ev.GRADER_SELFGRADE is False)

# ↓ 这一组是本轮测试逮到的真问题：档名不等于模型不同。
# _tier() 在没配 MODEL_GALLEON 时会回落到 LLM_MODEL，于是「评分员 galleon / 被测 sloop」
# 两句话可能指着同一个模型串——LLM-as-judge 悄悄退化成自评，而分数照样漂亮地打出来。
os.environ.pop("MODEL_GALLEON", None)
ev = reload_all()
_ORIG_CREATE = ev._create
check("A5：只配了 LLM_MODEL 时（各档回落到同一个模型），GRADER_SELFGRADE 检出「这是自评」",
      ev.GRADER_SELFGRADE is True and ev.TIERS[ev.GRADER_TIER] == ev.TIERS["sloop"])


os.environ["MODEL_GALLEON"] = os.environ["LLM_MODEL"]
ev = reload_all()
check("A5b：MODEL_GALLEON 配了、但和 LLM_MODEL 指着同一个模型时，照样认定为自评",
      ev.GRADER_SELFGRADE is True)
os.environ.pop("MODEL_GALLEON", None)
ev = reload_all()
_ORIG_CREATE = ev._create

with contextlib.redirect_stdout(io.StringIO()) as o:
    r0 = {"tag": "T", "hits": 0, "total": 1, "degraded": 0, "rows": [row()]}
    ev.grade_row = lambda _r: {"grade": "sound", "why": "w"}
    ev.grade_run(r0)
check("A6：自评时会当场吵出来，不闷声出分", "自评" in o.getvalue())
check("A7：自评这件事写进落盘 meta（三个月后翻出来还看得出这份结果算不算数）",
      ev._meta(False, True)["grader_selfgrade"] is True)
check("A8：没开评分（--no-judge）时不该标自评——那一轮压根没有评分层",
      ev._meta(False, False)["grader_selfgrade"] is False)

os.environ["EVAL_SUT_MODEL"] = "sloop"
os.environ["EVAL_GRADER_MODEL"] = "galleon"
ev = reload_all()
check("A9：真实评测默认拒绝网关档名，避免一轮里自动降级混入多个模型",
      len(ev._model_pin_issues(True)) == 2)
os.environ["EVAL_SUT_MODEL"] = "deepseek-v4-pro"
os.environ["EVAL_GRADER_MODEL"] = "claude-sonnet-4-6"
ev = reload_all()
check("A10：固定到两个具体型号后模型隔离闸放行",
      ev._model_pin_issues(True) == [] and ev.SUT_MODEL != ev.GRADER_MODEL)
check("A11：V2 内部阶段与 V1 共用同一个固定被测型号",
      fc.CHECK_MODEL == ev.SUT_MODEL == "deepseek-v4-pro")
os.environ.pop("EVAL_SUT_MODEL")
os.environ.pop("EVAL_GRADER_MODEL")
ev = reload_all()
_ORIG_CREATE = ev._create


print("\n══ B. 锚定回避：评分员不能知道「它答对了没有」 ══")

CALLS.clear(); stub_create('{"grade": "sound", "why": "ok"}')
ev.grade_row(row(ok=True))
ev.grade_row(row(ok=False))
u_true = CALLS[0][1][1]["content"]
u_false = CALLS[1][1][1]["content"]
check("B1：只有 ok 不同的两行，送给评分员的提示词逐字节相同（结论没泄露）",
      u_true == u_false)
check("B2：提示词里没有 ok / checked 这类「已经算好的结论」字段",
      "ok" not in u_true.lower().replace("标签", "") or ("True" not in u_true and "False" not in u_true))
check("B3：金标准、系统标签、系统理由、证据 四样都进了提示词",
      all(s in u_true for s in ("correct", "陈述S", "理由Z", "原始证据R")))
check("B4：system 位放的是评分员人设（LLM-as-judge），不是被测系统的人设",
      "评分员" in CALLS[0][1][0]["content"] and "LLM-as-judge" in CALLS[0][1][0]["content"])


print("\n══ C. 评分员输出失败：单列 unparsed，不冒充理由不合格 ══")

stub_create('{"grade": "sound", "why": "有具体证据支撑"}')
check("C1：合法的 sound 原样通过", ev.grade_row(row())["grade"] == "sound")

stub_create('{"grade": "unsupported", "why": "理由没有证据"}')
check("C2：评分员明确给出的 unsupported 原样通过",
      ev.grade_row(row())["grade"] == "unsupported")

stub_create('{"grade": "excellent", "why": "编了个白名单外的等级"}')
check("C3：白名单外的等级 → unparsed（量尺失败，不冒充理由差）",
      ev.grade_row(row())["grade"] == ev.UNPARSED_GRADE)

stub_create("评分员今天心情不好，说了一堆自然语言，没有 JSON")
check("C4：完全解析不出来 → unparsed",
      ev.grade_row(row())["grade"] == ev.UNPARSED_GRADE)

stub_create('{"why": "只给了理由没给等级"}')
check("C5：缺 grade 字段 → unparsed", ev.grade_row(row())["grade"] == ev.UNPARSED_GRADE)

stub_create('[{"grade": "sound", "why": "最外层错成数组"}]')
check("C6：JSON 能解析但最外层不是对象 → unparsed",
      ev.grade_row(row())["grade"] == ev.UNPARSED_GRADE)

stub_create('前面有废话 {"grade": "wrong", "why": "理由自相矛盾"} 后面也有废话')
check("C7：JSON 裹在废话里也能捞出来（沿用管线同一套宽容解析）",
      ev.grade_row(row())["grade"] == "wrong")

named = set(re.findall(r"\b(sound|unsupported|wrong)\b", ev.GRADER_SYS))
check("C8：提示词里写给评分员的等级名，和代码白名单 GRADES 完全对齐",
      named == set(ev.GRADES))
check("C9：unparsed 是工具故障桶，不是提示给评分员选择的第四档",
      ev.UNPARSED_GRADE not in named)


print("\n══ D. 证据取用 ══")

CALLS.clear(); stub_create('{"grade": "sound", "why": "x"}')
ev.grade_row(row(evidence_raw="原文证据", evidence="摘要证据"))
check("D1：优先送原始证据（evidence_raw），不送已经被截断/改写过的摘要",
      "原文证据" in CALLS[0][1][1]["content"])

CALLS.clear(); ev.grade_row(row(evidence_raw="", evidence="只有摘要"))
check("D2：没有原文时退用摘要", "只有摘要" in CALLS[0][1][1]["content"])

CALLS.clear(); ev.grade_row(row(evidence_raw="", evidence=""))
check("D3：两样都没有时给占位符，不把 None/空串喂给评分员",
      "（无检索证据）" in CALLS[0][1][1]["content"])


print("\n══ E. 蒙对 / 判错但理由站得住 的统计口径 ══")

def graded_run(pairs):
    """pairs = [(ok, grade), ...] —— 直接注入评分结果，测的是统计而不是评分。"""
    r = {"tag": "T", "hits": sum(p[0] for p in pairs), "total": len(pairs),
         "elapsed_s": 0, "degraded": 0,
         "rows": [row(id=i, ok=ok) for i, (ok, _) in enumerate(pairs)]}
    grades = [g for _, g in pairs]
    it = iter(grades)
    ev.grade_row = lambda _r: {"grade": next(it), "why": "w"}
    with contextlib.redirect_stdout(io.StringIO()):
        ev.grade_run(r)
    return r

r = graded_run([(True, "sound"), (True, "unsupported"), (False, "sound"),
                (False, "wrong"), (True, "sound"),
                (True, ev.UNPARSED_GRADE), (False, ev.UNPARSED_GRADE)])
check("E1：理由合格数 = grade 为 sound 的条数", r["sound"] == 3)
check("E2：蒙对 = 标签对了但理由撑不住（这几条不该算进能力里）", r["lucky"] == 1)
check("E3：判错但理由站得住 = 标签错了理由却成立（多半是用例标注有争议）", r["argued"] == 1)
check("E4：蒙对与理由合格是两个独立口径，不会互相顶替",
      r["lucky"] + r["sound"] != r["total"])
check("E5：评分失败独立计数", r["unparsed"] == 2)
check("E6：理由合格的分母只算真正完成评分的条目", r["graded_total"] == 5)
check("E7：标签答对但评分失败，不得混进 lucky", r["lucky"] == 1)

boom = {"tag": "T", "hits": 2, "total": 2, "elapsed_s": 0, "degraded": 0,
        "rows": [row(id=1, ok=True), row(id=2, ok=True)]}
def _boom(_r):
    raise RuntimeError("评分员超时")
ev.grade_row = _boom
with contextlib.redirect_stdout(io.StringIO()):
    ev.grade_run(boom)
check("E8：评分员炸了不带走整批，逐条兜住", len(boom["rows"]) == 2)
check("E9：评分异常 → unparsed，且异常信息留在行里（不静默）",
      all(x["grade"] == ev.UNPARSED_GRADE and "评分异常" in x["grade_why"] for x in boom["rows"]))
check("E10：评分层失败单列，不伪造成蒙对",
      boom["unparsed"] == 2 and boom["graded_total"] == 0 and boom["lucky"] == 0)

ev = reload_all()          # 恢复被 monkeypatch 掉的 grade_row
_ORIG_CREATE = ev._create


print("\n══ F. 基线与容差 ══")

tmp = tempfile.mkdtemp()
ev.RESULTS_DIR = tmp
ev.BASELINE_PATH = os.path.join(tmp, "baseline.json")

def mkrun(tag, hits, total=10, sound=None, unparsed=0):
    return {"tag": tag, "hits": hits, "total": total, "elapsed_s": 0,
            "degraded": 0, "stage_failed": 0, "rows": [],
            "slices": {},
            "sound": sound if sound is not None else hits,
            "graded_total": total - unparsed, "unparsed": unparsed}

poisoned = mkrun("V2", 1, total=1)
poisoned.update(degraded=1, stage_failed=1, rows=[
    row(degraded="exception", error="APIConnectionError: Connection error",
        stage_failures=["run_exception"])
])
with contextlib.redirect_stdout(io.StringIO()) as o:
    poisoned_check = ev.check_baseline([poisoned])
check("F0a：没有基线也不能放行调用异常——2/10 可能只是异常兜底碰巧撞中 unverifiable",
      poisoned_check is False and "没有完整跑完" in o.getvalue() and "还没有基线" not in o.getvalue())
try:
    ev.save_baseline([poisoned], {"utc": "2026-08-25T00:00:00Z"})
    poisoned_saved = True
except ValueError as exc:
    poisoned_saved = False
    poisoned_error = str(exc)
check("F0b：save_baseline 自身也硬拒绝异常跑批（不只靠 main 的 if）",
      poisoned_saved is False and "拒绝" in poisoned_error)

stage_broken = mkrun("V2", 1, total=1)
stage_broken.update(stage_failed=1, rows=[row(stage_failures=["score_unparsed"])])
with contextlib.redirect_stdout(io.StringIO()) as o:
    stage_check = ev.check_baseline([stage_broken])
check("F0c：CRAG 阶段故障同样拒绝判绿，即使最后标签碰巧正确",
      stage_check is False and "阶段故障" in o.getvalue())

grader_broken = mkrun("V2", 1, total=1, unparsed=1)
with contextlib.redirect_stdout(io.StringIO()) as o:
    grader_check = ev.check_baseline([grader_broken])
check("F0c2：评分员仍有 unparsed → 完整评测门禁拒绝判绿（标签分仍可落盘供查看）",
      grader_check is False and "评分失败" in o.getvalue())

ordinary_fallback = mkrun("V2", 1, total=1)
ordinary_fallback.update(degraded=1, rows=[row(degraded="thin_evidence")])
with contextlib.redirect_stdout(io.StringIO()) as o:
    fallback_check = ev.check_baseline([ordinary_fallback])
check("F0d：正常设计的 thin_evidence 不误判为工具故障；没基线时仍按首次运行处理",
      fallback_check is True and "还没有基线" in o.getvalue())

with contextlib.redirect_stdout(io.StringIO()) as o:
    first = ev.check_baseline([mkrun("V2", 8)])
check("F1：还没有基线时放行（第一次跑不该判红）", first is True)
check("F2：并且明确提示去 --set-baseline，而不是默默通过", "--set-baseline" in o.getvalue())

meta = {"utc": "2026-07-28T00:00:00Z"}
p = ev.save_baseline([mkrun("V1", 6, sound=5), mkrun("V2", 8, sound=7)], meta)
saved = json.load(open(p, encoding="utf-8"))
check("F3：基线落盘记了标签 / 理由分母、评分失败和切片快照",
      saved["runs"]["V2"] == {"hits": 8, "total": 10, "sound": 7,
                               "graded_total": 10, "unparsed": 0, "stage_failed": 0,
                               "slices": {}})
check("F4：基线把当时的容差一起冻进去（以后改环境变量不会回头改判旧基线）",
      saved["tolerance"] == ev.EVAL_TOLERANCE)

with contextlib.redirect_stdout(io.StringIO()):
    up = ev.check_baseline([mkrun("V2", 9)])
    same = ev.check_baseline([mkrun("V2", 8)])
    down1 = ev.check_baseline([mkrun("V2", 7)])
    down2 = ev.check_baseline([mkrun("V2", 6)])
check("F5：涨了 → 放行", up is True)
check("F6：持平 → 放行", same is True)
check("F7：掉 1 条（容差 1）→ 放行。容差 0 的门禁会被一条边界用例翻面搞红，红多了人就不看了",
      down1 is True)
check("F8：掉 2 条 → 判红（这才是效果回归）", down2 is False)

with contextlib.redirect_stdout(io.StringIO()) as o:
    unknown = ev.check_baseline([mkrun("V9-新加的", 0)])
check("F9：基线里没记过的 tag → 跳过、放行（新增用例集不该把门禁搞红）", unknown is True)
check("F10：跳过要说出来，不闷声跳", "跳过" in o.getvalue())

saved["tolerance"] = 0
json.dump(saved, open(ev.BASELINE_PATH, "w", encoding="utf-8"))
with contextlib.redirect_stdout(io.StringIO()):
    tight = ev.check_baseline([mkrun("V2", 7)])
check("F11：容差读的是基线文件里记的那份，不是当前环境变量（旧基线按旧标准判）",
      tight is False)

src = inspect.getsource(ev)
check("F12：main 里 --check-baseline 判红会 sys.exit(1)（CI 才看得见红）",
      re.search(r'--check-baseline.*not check_baseline\(runs\):\s*\n\s*sys\.exit\(1\)', src, re.S) is not None)

with contextlib.redirect_stdout(io.StringIO()) as o:
    different_total = ev.check_baseline([mkrun("V2", 8, total=11)])
check("F13：同一个 tag 的样本数变了 → 基线作废，不能把 8/10 和 8/11 当持平",
      different_total is False and "数量不同" in o.getvalue() and "基线作废" in o.getvalue())


print("\n══ G. 结论行 ══")

with contextlib.redirect_stdout(io.StringIO()) as o:
    ev._verdict_line([mkrun("V1-material", 3, 10, sound=2), mkrun("V2-material", 7, 10, sound=6)])
out = o.getvalue()
check("G1：V1-material / V2-material 能被认成 V1 / V2（tag 分割没写死全等）", "结论" in out)
check("G2：报的是分数，不是百分比——n=10 时一条就是十个百分点", "%" not in out)
check("G3：标签和理由两个口径分别报，不合并成一个「准确率」",
      "3/10" in out and "7/10" in out and "2/10" in out and "6/10" in out)
check("G4：顺带报蒙对条数", "蒙对" in out)

with contextlib.redirect_stdout(io.StringIO()) as o:
    ev._verdict_line([mkrun("V1", 3, 10, sound=2, unparsed=3),
                      mkrun("V2", 7, 10, sound=6, unparsed=1)])
unparsed_out = o.getvalue()
check("G5：理由合格分母排除 unparsed，评分失败另行示警",
      "2/7" in unparsed_out and "6/9" in unparsed_out and "评分失败" in unparsed_out)

with contextlib.redirect_stdout(io.StringIO()) as o:
    ev._verdict_line([mkrun("V2", 8)])
check("G6：只跑了 V2（--v2-only）时不硬凑对比，安静退出", o.getvalue().strip() == "")

d = mkrun("V2", 8); d["degraded"] = 3
with contextlib.redirect_stdout(io.StringIO()) as o:
    ev._verdict_line([mkrun("V1", 6), d])
check("G7：有降级条目就示警「这轮别拿去写材料」（降级必须留痕）",
      "降级" in o.getvalue() and "别拿去写材料" in o.getvalue())

check("G8：源码里没有「除以总数再乘 100」这类把样本量抹掉的写法",
      "* 100" not in src and "*100" not in src and ":.0%" not in src and ":.1%" not in src)

main_src = inspect.getsource(ev.main)
check("G9：main 里先落盘、再打结论行——收尾那行格式化挂掉不该毁掉一轮真花了钱的跑批",
      main_src.index("_dump(") < main_src.index("_verdict_line("))
check("G10：--set-baseline / --check-baseline 都排在落盘之后（无论判红判绿，明细都已经存下）",
      main_src.index("_dump(") < main_src.rindex("--set-baseline")
      and main_src.index("_dump(") < main_src.rindex("--check-baseline"))

slice_v1 = mkrun("V1", 1, total=2)
slice_v2 = mkrun("V2", 2, total=2)
slice_v1["slices"] = es.slice_metrics([
    row(ok=True, evidence_mode="public_snapshot", retrieval_need="not_needed"),
    row(id=2, ok=False, evidence_mode="supplied_material", retrieval_need="material_only"),
])
slice_v2["slices"] = es.slice_metrics([
    row(ok=True, evidence_mode="public_snapshot", retrieval_need="not_needed"),
    row(id=2, ok=True, evidence_mode="supplied_material", retrieval_need="material_only"),
])
with contextlib.redirect_stdout(io.StringIO()) as o:
    ev._verdict_line([slice_v1, slice_v2])
slice_out = o.getvalue()
check("G11：结论行展开 V1→V2 切片，不再只给一个无法定位原因的总分",
      "切片对比" in slice_out and "evidence_mode" in slice_out
      and "supplied_material 0/1→1/1" in slice_out)
check("G12：切片完整结构保留 hits/total，claim_type 等多值维度也能独立计数",
      slice_v2["slices"]["claim_type"]["protocol_semantics"] == {"hits": 2, "total": 2})


print("\n══ H. 跨模块接缝 ══")

vocab = set(fc._VERDICTS)
check("H1：fact_checker 的 verdict 白名单不是空的", len(vocab) == 3)
check("H2：V2_MAP 的键 == fact_checker 能吐出的 verdict 全集"
      "（哪天加了第四种裁决，judge_v2 会 KeyError，这条先炸）",
      set(ev.V2_MAP) == vocab)
check("H3：V2_MAP 的值落在用例合法标签里",
      set(ev.V2_MAP.values()) == {"correct", "incorrect", "unverifiable"})

cases = json.load(open(ev.PUBLIC_CASES_PATH, encoding="utf-8"))
allc = cases["cases"] + cases.get("cases_material", [])
check("H3b：公开 smoke sample 完整通过多轴 schema",
      cases.get("_sample_only") is True and es.validate_eval_data(cases) == [])
check("H4：用例里的 label 全是合法标签（写错一个字，那条永远判错还查不出来）",
      all(c["label"] in ("correct", "incorrect", "unverifiable") for c in allc))
ids = [c["id"] for c in allc]
check("H5：用例 id 不重复（重复 id 会让落盘明细对不上号）", len(ids) == len(set(ids)))
check("H6：主用例集三类都有（缺一类，那一类的错就永远测不出来）",
      len({c["label"] for c in cases["cases"]}) == 3)
check("H7：素材用例集三类都有", len({c["label"] for c in cases["cases_material"]}) == 3)

def balanced(items, total, per_label):
    return len(items) == total and all(
        sum(c["label"] == label for c in items) == per_label
        for label in ("correct", "incorrect", "unverifiable")
    )

check("H8：公开主集 sample 是 6 条且三类各 2 条",
      balanced(cases["cases"], 6, 2))
check("H9：公开素材集 sample 同样是 6 条且三类各 2 条",
      balanced(cases["cases_material"], 6, 2))
check("H10：主集所有可判定题都有一手来源，金标准能人工复核",
      all(c.get("source") for c in cases["cases"] if c["label"] != "unverifiable"))
check("H11：主集所有 unverifiable 题都带 as_of，未来重跑时知道它冻结在哪一天",
      all(c.get("as_of") for c in cases["cases"] if c["label"] == "unverifiable"))
check("H12：公开 sample 只保留 dev 冒烟题，不泄露 held-out test",
      all(c["split"] == "dev" for c in allc))
check("H13：公共稳定 / 实时 Web / 证据不足 / 项目素材四种证据来源都已落到 schema",
      {c["evidence_mode"] for c in allc}
      == {"public_snapshot", "live_web", "insufficient", "supplied_material"})
_relation_for_label = {"correct": "supports", "incorrect": "contradicts",
                       "unverifiable": "insufficient"}
check("H14：可判定题必须是支持/矛盾；unverifiable 可来自不足、局部、无关或冲突证据",
      all(c["evidence_relation"] == _relation_for_label[c["label"]]
          if c["label"] != "unverifiable"
          else c["evidence_relation"] in {"insufficient", "partial", "irrelevant", "conflicting"}
          for c in allc))
check("H14b：公开素材 sample 的引用都能在它自身解析",
      all(c.get("material_id") in cases["materials"] for c in cases["cases_material"]))

with open(ev.MANIFEST_PATH, encoding="utf-8") as f:
    manifest = json.load(f)
with open(ev.PUBLIC_CASES_PATH, "rb") as f:
    public_hash = hashlib.sha256(f.read()).hexdigest()
check("H14c：manifest 公开了完整集规模与公开 sample 指纹，但不泄露全量题面",
      manifest["full_dataset"]["visibility"] == "local_only"
      and manifest["full_dataset"]["total"] == 120
      and manifest["public_sample"]["total"] == 12
      and manifest["public_sample"]["sha256"] == public_hash)

full_ok = True
if os.path.exists(ev.LOCAL_CASES_PATH):
    with open(ev.LOCAL_CASES_PATH, encoding="utf-8") as f:
        full = json.load(f)
    with open(ev.LOCAL_CASES_PATH, "rb") as f:
        full_hash = hashlib.sha256(f.read()).hexdigest()
    fullc = full["cases"] + full["cases_material"]
    expected_packs = manifest["full_dataset"]["material_packs"]
    full_ok = (
        es.validate_eval_data(full) == []
        and full_hash == manifest["full_dataset"]["sha256"]
        and balanced(full["cases"], 60, 20)
        and balanced(full["cases_material"], 60, 20)
        and all(sum(c["split"] == split for c in suite) == 30
                for suite in (full["cases"], full["cases_material"])
                for split in ("dev", "test"))
        and all(sum(c.get("material_id") == mid for c in full["cases_material"]) == count
                for mid, count in expected_packs.items())
        and len(fullc) == manifest["full_dataset"]["total"]
    )
check("H14d：本机完整集若存在，schema / 指纹 / 60×2 平衡 / dev-test 冻结全部一致",
      full_ok)

broken_schema = json.loads(json.dumps(cases))
del broken_schema["cases"][0]["difficulty"]
broken_schema["cases"][1]["retrieval_need"] = "随便搜搜"
_schema_issues = es.validate_eval_data(broken_schema)
check("H15：schema 一次报告全部问题，不让扩容时修一个字段再跑一轮",
      len(_schema_issues) == 2 and "缺字段" in _schema_issues[0]
      and any("retrieval_need" in issue for issue in _schema_issues))


print("\n══ I. V1 是基线，就不许拿到素材 ══")

CALLS.clear(); stub_create('{"label": "correct", "reason": "r"}')
ev.judge_v1("陈述S", "素材里有答案：这是 SECRET_MATERIAL")
sent = json.dumps(CALLS[0][1], ensure_ascii=False)
check("I1：judge_v1 收下 material 形参但绝不发出去——"
      "「作者写自己项目时记岔了」这类错，靠世界知识查不出来，这正是被测点",
      "SECRET_MATERIAL" not in sent)
check("I2：V1 走的是被测档位 sloop（和 V2 同档，才比得出管线的功劳）",
      CALLS[0][0] == ev.TIERS["sloop"])

stub_create("裸判也说不清，没有 JSON")
_bad_v1 = ev.judge_v1("x")
check("I3：V1 解析失败 → unverifiable（保守），不是随便挑一个标签",
      _bad_v1["label"] == "unverifiable")
check("I3b：V1 自己解析失败也进入 stage_failures，不能靠两条 unverifiable 蒙出 2 分",
      _bad_v1["stage_failures"] == ["v1_unparsed"])
stub_create('{"label": "maybe", "reason": "白名单外"}')
check("I4：V1 吐出白名单外的标签 → unverifiable", ev.judge_v1("x")["label"] == "unverifiable")
stub_create('{"label": "unverifiable", "reason": "知识截止点后无法确认"}')
_valid_unverifiable = ev.judge_v1("x")
check("I5：合法 unverifiable 原样通过，不被结构守卫误判为解析失败",
      _valid_unverifiable["label"] == "unverifiable" and
      _valid_unverifiable["stage_failures"] == [])

ev._create = _ORIG_CREATE


print("\n══ J. run() 的韧性 ══")

def _explode(statement, material=""):
    raise RuntimeError("供应商 500")

with contextlib.redirect_stdout(io.StringIO()):
    rr = ev.run("V2", _explode, [case(statement="s"), case(id=2, statement="s2")])
check("J1：一条炸了不带走整批（前面几条的额度已经花掉了）", rr["total"] == 2)
check("J2：炸掉的条目记 degraded=exception 并留错误原文", rr["degraded"] == 2 and
      all(x["degraded"] == "exception" and "RuntimeError" in x["error"] for x in rr["rows"]))
check("J3：炸掉的条目保守记 unverifiable，不白送一个 hit", rr["hits"] == 0)
check("J3b：调用异常同时进入 stage_failures，回归门禁看得见这轮没跑完整",
      rr["stage_failed"] == 2 and all(x["stage_failures"] == ["run_exception"] for x in rr["rows"]))

need = {"statement", "label", "got", "reason", "evidence_raw", "evidence",
        "search_status", "search_queries", "evidence_enriched", "stage_failures",
        "split", "domain", "claim_type",
        "evidence_mode", "retrieval_need", "difficulty", "risk", "group_id", "material_id"}
check("J4：接缝——run() 产出的每一行都带齐 grade_row() 要读的键",
      need <= set(rr["rows"][0]))
check("J5：run() 把多轴切片和每条明细一起落盘，后续失败可定位到具体类型",
      rr["slices"]["domain"]["web_protocol"] == {"hits": 0, "total": 2})

_seen_materials = []
def _record_material(statement, material=""):
    _seen_materials.append((statement, material))
    return {"label": "correct", "reason": "r", "confidence": 0.9,
            "checked": False, "degraded": "", "evidence": "e", "evidence_raw": "",
            "search_status": "not_run", "search_queries": [], "evidence_enriched": False,
            "stage_failures": []}

with contextlib.redirect_stdout(io.StringIO()):
    routed = ev.run("V2-material-dev", _record_material, [
        case(id="a", statement="A", domain="project_internal",
             evidence_mode="supplied_material", evidence_relation="supports",
             retrieval_need="material_only", source=None, material_id="pack-a"),
        case(id="b", statement="B", domain="project_internal",
             evidence_mode="supplied_material", evidence_relation="supports",
             retrieval_need="material_only", source=None, material_id="pack-b"),
    ], {"pack-a": "素材 A", "pack-b": "素材 B"})
check("J6：run() 按每条 material_id 路由素材，不再把一份顶层 material 喂给所有私有题",
      _seen_materials == [("A", "素材 A"), ("B", "素材 B")])
check("J7：material_id 会进入结果行与切片，错路由能定位到具体材料包",
      routed["rows"][0]["material_id"] == "pack-a"
      and routed["slices"]["material_id"]["pack-b"]["total"] == 1)


print("\n══ K. 网关留痕：配置串不同 ≠ 真的换了模型 ══")
# 这一段测的是第二层自评检测。第一层（A 段）比的是 .env 里两档写的模型串；
# 可一旦改成网关档位名（MODEL_SLOOP=sloop / MODEL_GALLEON=galleon），两个串永远不同，
# 第一层就永远说「不是自评」——而网关的降级链 galleon→sloop 能把两边并到同一个型号上。
# **多加一层抽象顺手把警报关掉**，就是这一段在守的东西。

ev._create = _ORIG_CREATE


def gw_env(**kw):
    """摆好 STUB_GW / STUB_NO_RAW，重新 import，并清掉上一轮的留痕。"""
    global ev
    for k in ("STUB_GW", "STUB_NO_RAW"):
        os.environ.pop(k, None)
    for k, v in kw.items():
        os.environ[k] = v
    ev = reload_all()
    wa._GW_LOG.clear()
    return ev


# —— 场景一：网关把 galleon 降级到了 sloop 的型号（两边最后同一个模型）——
# 这正是「改用档位名」以后新开的那个洞：.env 里 sloop / galleon 两个串明摆着不同，
# 网关一降级，答题的却是同一个型号。
os.environ["MODEL_GALLEON"] = "grader-model"
ev = gw_env(STUB_GW=json.dumps({
    "stub-model":   {"model": "deepseek-v4-pro", "tier": "sloop"},
    "grader-model": {"model": "deepseek-v4-pro", "tier": "sloop", "degraded": True},
}))
check("K0：前提——两档配的是不同的串，第一层自评检测放行（正是它看不见的那个盲区）",
      ev.TIERS["sloop"] != ev.TIERS["galleon"] and ev.GRADER_SELFGRADE is False)

ev._create(ev.TIERS["sloop"], [{"role": "user", "content": "被测的一次调用"}], 10)
ev._create(ev.TIERS[ev.GRADER_TIER], [{"role": "user", "content": "评分员的一次调用"}], 10)
g = ev.gw_selfgrade()
check("K1：读到了 X-Gw-Model（Go 规范化后的混合大小写，客户端得抹平大小写才取得到）",
      g["headers_seen"] == 2 and g["served_sut"] == ["deepseek-v4-pro"])
check("K2：网关降级次数照实记（X-GW-Degraded）", g["degraded_calls"] == 1)
check("K3：两档配置串不同、但最后由同一个模型作答 → 判定为【实际自评】",
      g["effective_selfgrade"] is True and g["overlap"] == ["deepseek-v4-pro"])

with contextlib.redirect_stdout(io.StringIO()) as o:
    ev._gw_verdict()
out = o.getvalue()
check("K4：这件事会当场吵出来，不闷声出分", "实际自评" in out and "降级" in out)

check("K5：网关判定整个结构写进落盘 meta（三个月后要能分清「查了没事」和「没查成」）",
      ev._meta(False, True)["gateway"]["effective_selfgrade"] is True)

# —— 场景二：真的是两个不同的模型在答 —— 不许误报
ev = gw_env(STUB_GW=json.dumps({
    "stub-model":   {"model": "deepseek-v4-pro", "tier": "sloop"},
    "grader-model": {"model": "claude-sonnet-4-6", "tier": "galleon"},
}))
ev._create(ev.TIERS["sloop"], [{"role": "user", "content": "x"}], 10)
ev._create(ev.TIERS[ev.GRADER_TIER], [{"role": "user", "content": "y"}], 10)
g = ev.gw_selfgrade()
check("K6：两边确实由不同模型作答时不误报（会误报的警报，三次之后就没人看了）",
      g["effective_selfgrade"] is False and g["checkable"] is True and g["degraded_calls"] == 0)

# —— 场景三：直连 provider，一个 X-GW-* 头都没有 ——
ev = gw_env()          # 不设 STUB_GW = 响应里没有任何网关头
ev._create(ev.TIERS["sloop"], [{"role": "user", "content": "x"}], 10)
ev._create(ev.TIERS[ev.GRADER_TIER], [{"role": "user", "content": "y"}], 10)
g = ev.gw_selfgrade()
check("K7：读不到头时记「查不了」，**不记「没降级」**——"
      "把「不知道」当成「没问题」是这套留痕唯一能出的致命错",
      g["calls"] == 2 and g["headers_seen"] == 0
      and g["checkable"] is False and g["effective_selfgrade"] is False)
with contextlib.redirect_stdout(io.StringIO()) as o:
    ev._gw_verdict()
check("K8：而且要说出「查不了」，不是打一个✅了事",
      "查不了" in o.getvalue() and "✅" not in o.getvalue())

# —— 场景四：老版本 SDK 压根没有 with_raw_response ——
ev = gw_env(STUB_NO_RAW="1")
check("K9：SDK 没有 with_raw_response 时照常跑通，不因为拿不到留痕就炸",
      hasattr(ev._create(ev.TIERS["sloop"], [{"role": "user", "content": "x"}], 10), "choices"))
check("K10：这种调用也要记一笔（记成「查不了」），不能因为没头就整条不记——"
      "调用数对不上，后面所有比例都是错的",
      ev.gw_selfgrade()["calls"] == 1 and ev.gw_selfgrade()["headers_seen"] == 0)

os.environ.pop("MODEL_GALLEON", None)
os.environ.pop("STUB_GW", None)
os.environ.pop("STUB_NO_RAW", None)
ev = reload_all()


print("\n══ L. 换了模型的两次跑分不能比（基线作废）══")


ev.RESULTS_DIR = tmp
ev.BASELINE_PATH = os.path.join(tmp, "baseline.json")

def set_base(meta, hits=8):
    ev.save_baseline([mkrun("V2", hits)], meta)

def run_check(hits=8):
    with contextlib.redirect_stdout(io.StringIO()) as o:
        r = ev.check_baseline([mkrun("V2", hits)])
    return r, o.getvalue()

# 老基线：只有时间戳，没记过任何模型。基线旧不是罪——
# 因为版本升级就把人判红，这门禁三次之后就没人看了。
set_base({"utc": "2026-07-28T00:00:00Z"})
ok, out = run_check(8)
check("L1：老基线（没记模型）照常比分数，不因为查不到模型就判红", ok is True and "基线作废" not in out)

now_meta = ev._meta(False, True)
check("L2：落盘 meta 里带整张档位表（阶梯会重排，今天的 ark 就是明天的评分员）",
      now_meta.get("tiers") == dict(ev.TIERS) and set(now_meta["tiers"]) == {"raft", "sloop", "galleon", "ark"})

# 被测那一档换了模型 —— 必须先判作废
set_base({**now_meta, "model_sloop": "老型号-flash"})
ok, out = run_check(8)
check("L3：被测(sloop)换了模型 → 基线作废，不再拿旧分数判红绿",
      ok is False and "基线作废" in out and "老型号-flash" in out)
check("L4：而且要说清怎么修（重记一次基线），不是甩个红就走",
      "--set-baseline" in out and "不能比" in out)


ok, out = run_check(10)
check("L5：换了模型之后分数涨了也判作废——涨分不比掉分安全，它会被抄进结果报告",
      ok is False and "基线作废" in out and "10/10" not in out)

# 评分员那一档换了模型 —— 同样作废（评分员换了，sound 的口径就变了）
set_base({**now_meta, "grader_model": "老评分员"})
ok, out = run_check(8)
check("L6：评分员那一档换了模型 → 同样作废", ok is False and "老评分员" in out)

# 只有评测调不到的档变了 —— 不许判红
set_base({**now_meta, "tiers": {**now_meta["tiers"], "raft": "老型号-raft", "ark": "老型号-ark"}})
ok, out = run_check(8)
check("L7：只有 raft / ark 变了（评测一次都调不到它们）→ 照常比分数，不判红。"
      "拿证明不影响分数的变化判红就是误报，而会误报的门禁三次之后没人看",
      ok is True and "基线作废" not in out)
check("L8：但也不能闷声吞掉——变了就提一句，信息留着", "raft" in out and "老型号-raft" in out)

# 一套没变的档位 —— 不许误报
set_base(now_meta)
ok, out = run_check(8)
check("L9：档位一个没动时不误报，正常进入分数对比",
      ok is True and "基线作废" not in out and "8/10" in out)

check("L10：先判能不能比，再判掉没掉分——顺序反了，打印出来的红绿就是假的",
      inspect.getsource(ev.check_baseline).index("_tier_drift")
      < inspect.getsource(ev.check_baseline).index('base.get("tolerance"'))


print("\n══ M. 用例语义指纹：换题必须让旧基线作废 ══")

case_path = os.path.join(tmp, "fingerprint-cases.json")
case_doc = {
    "_schema_version": es.SCHEMA_VERSION,
    "_freeze_version": "test-freeze-v1",
    "cases": [
        case(id=1, label="correct", statement="主集事实",
             claim_type=["protocol_semantics", "negation"], source="https://old"),
        case(id=2, label="unverifiable", statement="未来传闻",
             domain="llm_platform", claim_type=["future"], evidence_mode="live_web",
             evidence_relation="insufficient", retrieval_need="required", freshness="as_of",
             difficulty="hard", risk="false_refute", source=None, as_of="2026-08-24"),
    ],
    "materials": {"fixture-a": "素材版本 A"},
    "cases_material": [
        case(id=11, label="correct", statement="素材内事实", domain="project_internal",
             claim_type=["configuration"], evidence_mode="supplied_material",
             retrieval_need="material_only", source=None, material_id="fixture-a"),
    ],
}

def write_case_doc(doc):
    with open(case_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

original_cases_path = ev.CASES_PATH
write_case_doc(case_doc)
ev.CASES_PATH = case_path

main_frozen = ev._case_meta(False)
audit_only = json.loads(json.dumps(case_doc))
audit_only["cases"][0]["source"] = "https://new"
audit_only["cases"][0]["note"] = "补了一段人工审计说明"
write_case_doc(audit_only)
check("M1：只改 source / note 不改变指纹（审计元数据不参与被测输入和金标准）",
      ev._case_meta(False)["case_fingerprint"] == main_frozen["case_fingerprint"])

semantic_change = json.loads(json.dumps(case_doc))
semantic_change["cases"][0]["statement"] = "主集事实改过了"
write_case_doc(semantic_change)
check("M2：题面变化会改变主集指纹", ev._case_meta(False)["case_fingerprint"] != main_frozen["case_fingerprint"])

write_case_doc(case_doc)
material_frozen = ev._case_meta(True)
material_change = json.loads(json.dumps(case_doc))
material_change["materials"]["fixture-a"] = "素材版本 B"
write_case_doc(material_change)
check("M3：素材变化会改变素材集指纹（同一题拿到的证据已经不同）",
      ev._case_meta(True)["case_fingerprint"] != material_frozen["case_fingerprint"])
check("M4：素材变化不改变主集指纹（主集本来就拿不到 material）",
      ev._case_meta(False)["case_fingerprint"] == main_frozen["case_fingerprint"])

write_case_doc(case_doc)
frozen_meta = ev._meta(False, False)
check("M5：落盘 meta 带集合名、题数、指纹、schema 和人工 freeze version",
      frozen_meta["case_set"] == "main" and frozen_meta["case_count"] == 2
      and len(frozen_meta["case_fingerprint"]) == 64
      and frozen_meta["freeze_version"] == "test-freeze-v1"
      and frozen_meta["schema_version"] == es.SCHEMA_VERSION
      and frozen_meta["dimensions"]["split"] == {"dev": 2})

ev.save_baseline([mkrun("V2", 1, total=2)], frozen_meta)
write_case_doc(semantic_change)
with contextlib.redirect_stdout(io.StringIO()) as o:
    changed_cases = ev.check_baseline([mkrun("V2", 2, total=2)])
check("M6：即使换题后分数上涨，指纹不同也先判基线作废",
      changed_cases is False and "基线作废" in o.getvalue() and "指纹" in o.getvalue())

write_case_doc(audit_only)
with contextlib.redirect_stdout(io.StringIO()) as o:
    audit_still_comparable = ev.check_baseline([mkrun("V2", 1, total=2)])
check("M7：只补审计元数据仍可与旧基线比较，不制造误报",
      audit_still_comparable is True and "基线作废" not in o.getvalue())

taxonomy_change = json.loads(json.dumps(case_doc))
taxonomy_change["cases"][0]["difficulty"] = "hard"
write_case_doc(taxonomy_change)
check("M8：难度 / 证据模式等 taxonomy 改变会改变指纹——切片语义变了就不能沿用旧基线",
      ev._case_meta(False)["case_fingerprint"] != main_frozen["case_fingerprint"])

order_only = json.loads(json.dumps(case_doc))
order_only["cases"].reverse()
order_only["cases"][1]["claim_type"].reverse()
write_case_doc(order_only)
check("M9：只调整用例顺序或 claim_type 标签顺序不改变指纹（集合没变就不是新量尺）",
      ev._case_meta(False)["case_fingerprint"] == main_frozen["case_fingerprint"])

split_doc = json.loads(json.dumps(case_doc))
split_doc["cases"][1]["split"] = "test"
write_case_doc(split_doc)
check("M10：选择 dev/test 时只冻结本 split，默认 CLI 也是 dev",
      ev._case_meta(False, "dev")["case_count"] == 1
      and ev._case_meta(False, "test")["case_count"] == 1
      and ev._case_meta(False, "dev")["case_fingerprint"]
      != ev._case_meta(False, "test")["case_fingerprint"]
      and ev._split_arg([]) == "dev")
check("M11：所有 split 与公共/素材各用独立基线路径，不会后跑的一套覆盖前一套",
      len({ev._baseline_path(material, split)
           for material in (False, True) for split in ("dev", "test", "all")}) == 6)

ev.CASES_PATH = original_cases_path


print("\n" + "=" * 48)
print(f"{sum(RESULTS)}/{len(RESULTS)} 通过")
sys.exit(0 if all(RESULTS) else 1)
