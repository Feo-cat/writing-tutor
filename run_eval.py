"""run_eval.py —— fact-check mini eval：V1（裸判基线）vs V2（CRAG 简化版）

V1 = 让模型直接判断陈述真伪（无素材、无检索）——这就是「没有 CRAG 时」的基线。
V2 = fact_checker 管线（置信度打分 → 低置信走 query 改写 + ddgs 检索 → 裁决 → 裸判回落）。
用例：默认优先读本地 `local_artifacts/eval/eval_cases.full.json`；本机没有完整集时
回落到公开的 `eval_cases.sample.json`（只供 schema / 管线冒烟，不是可引用的 benchmark）。
correct / incorrect / unverifiable 三类业务标签及多轴 taxonomy 的枚举真源在
eval_schema.py。

两层评估，评的是两件不同的事：
  ① 标签比对   —— 系统给的 label 和人工金标准对不对得上。这是准确率。
  ② LLM-as-judge —— 另一个（更高档位的）模型评【理由】站不站得住。这是可信度。
     为什么要有②：n 只有十几条，**「蒙对」和「推对」在标签上长得一模一样**。
     一条 unverifiable 用例，系统因为「我不知道」判对了，和因为「证据确实定不了论」判对了，
     准确率里都记一分——可这两件事的价值差着一个量级。只看标签的评测，
     会把运气记成能力，而且样本越小记得越狠。
     评分员刻意用比被测档位更高的一档（默认 galleon，被测走 sloop）：
     **同一个模型给自己打分是自评，不是评测。**

产出：① 终端上的分数（写成 8/10，不写 80%）；② 本地
`local_artifacts/eval/results/` 下的 JSON（每条明细 + 跑批参数）。

关于「为什么不吐百分比」：n 只有 10，百分比会把样本量藏起来。8/10 一眼看得出
「一条 = 十个百分点」，80% 看不出；等这行数字被复制进结果报告，样本量就彻底没了。
分数自带样本量，百分比藏样本量——小样本下写分数是诚实，写百分比是化妆。

用法：
    uv run python run_eval.py                    # 主用例集 dev：V1 + V2 + 评分员
    uv run python run_eval.py --split test       # 冻结测试集；只在阶段验收时显式运行
    uv run python run_eval.py --split all        # dev + test 全量（最贵）
    uv run python run_eval.py --v1-only          # 只跑基线（省额度分开跑）
    uv run python run_eval.py --v2-only
    uv run python run_eval.py --with-material    # 素材对照用例集（V1 不给素材，那正是被测的能力）
    uv run python run_eval.py --no-judge         # 跳过 LLM-as-judge（只要标签，最省）
    uv run python run_eval.py --set-baseline     # 把这次分数记成基线
    uv run python run_eval.py --check-baseline   # 跟基线比，掉超过容差就退出非零（CI 回归用）
      └ 换过模型（MODEL_SLOOP / 评分员那一档）之后，它会先判「基线作废」再谈分数：
        **换了模型的两次跑分不能比**，掉分可能只是换了个便宜档，涨分也不是管线变好了。
        重记一次基线（--set-baseline）就好——这是确定性的红，不是会抽风的红。
    LLM_TEMPERATURE=0 uv run python run_eval.py  # 确定性模式，两次跑分才可比

环境变量：
    LOCAL_ARTIFACTS_DIR   本地私有产物根目录（默认 <repo>/local_artifacts）
    EVAL_CASES_PATH      显式指定评测集（默认优先完整本地集）
    EVAL_RESULTS_DIR     显式指定原始结果目录
    EVAL_TOLERANCE=1      基线容差（掉几条才判回归。默认 1，给模型抖动留的余量）
    EVAL_SUT_MODEL=your-sut-model       被测具体型号；不要填 sloop 等网关档名
    EVAL_GRADER_MODEL=your-grader-model  评分员具体型号；不要填 galleon 等网关档名
    EVAL_REQUIRE_PINNED_MODELS=1          默认拒绝用网关档名跑评测，防自动降级污染
    EVAL_GRADER_TIER=galleon   兼容旧配置：没给 EVAL_GRADER_MODEL 时从哪档取型号
    EVAL_V1_TOKENS=4800   V1 结构化回答的初始 token 预算（截断时守卫自动加倍）
    EVAL_GRADER_TOKENS=8000    理由评分员的初始 token 预算（同样受结构守卫保护）
    GW_READ_HEADERS=0     关掉网关留痕（读 X-GW-* 响应头）。默认开，出意外时的逃生阀
    EVAL_GW_CACHE=on      让评测走网关缓存。**默认 off，基本不该改**——评测跑的是固定
                          用例集，同一份 prompt 一字不差，30 分钟内跑第二遍就是在回放
                          上一遍的答案：门禁看着绿，其实一道题都没重跑。

自评检测有两层，问的是两个不同的问题：
  第一层 GRADER_SELFGRADE —— 你**打算**让谁评？比的是 .env 里两档的模型串。
  第二层 gw_selfgrade()   —— 最后**是**谁评的？比的是网关 X-GW-Model 里实际作答的型号。
只有第一层是不够的：一旦 .env 从具体型号改成网关档位名（MODEL_SLOOP=sloop /
MODEL_GALLEON=galleon），两个串永远不同，第一层就永远说「不是自评」；
而网关的降级链 galleon→sloop 完全可以把两边并到同一个型号上。
**多加一层抽象，顺手把警报关掉**，是这类改动最典型的暗伤。"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import fact_checker
import eval_schema
import writing_assistant
from writing_assistant import _create, TIERS
from fact_checker import check_claim

# 评测流量默认**绕开网关缓存**。用 EVAL_GW_CACHE 这个自己的名字、不复用全局 GW_CACHE：
# writing_assistant 在 import 期就 load_dotenv() 过了，.env 里若写着 GW_CACHE=on，
# 复用同一个名字就得跟它抢，抢输了还没人告诉你。这里直接覆写本进程的 GW_CACHE，
# 优先级写死：EVAL_GW_CACHE（shell 或 .env）> 默认 off > 全局 GW_CACHE（评测不看）。
#
# 放在 import 之后是安全的：writing_assistant._extra_headers() 每次调用现读环境变量，
# 不在 import 期快照——所以这几行不怕被工具重排到 import 上下。
# 为什么默认 off，见 writing_assistant._extra_headers 上面那段。
EVAL_GW_CACHE = os.getenv("EVAL_GW_CACHE") or "off"
os.environ["GW_CACHE"] = EVAL_GW_CACHE


os.environ["GW_SOURCE"] = os.getenv("EVAL_GW_SOURCE") or "eval"

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC_CASES_PATH = os.path.join(HERE, "eval_cases.sample.json")
MANIFEST_PATH = os.path.join(HERE, "eval_manifest.json")
LOCAL_EVAL_DIR = os.path.join(writing_assistant.LOCAL_ARTIFACTS_DIR, "eval")
LOCAL_CASES_PATH = os.path.join(LOCAL_EVAL_DIR, "eval_cases.full.json")
_CONFIGURED_CASES_PATH = os.getenv("EVAL_CASES_PATH", "").strip()
CASES_PATH = (os.path.abspath(os.path.expanduser(_CONFIGURED_CASES_PATH))
              if _CONFIGURED_CASES_PATH
              else LOCAL_CASES_PATH if os.path.exists(LOCAL_CASES_PATH)
              else PUBLIC_CASES_PATH)
RESULTS_DIR = os.path.abspath(os.path.expanduser(
    os.getenv("EVAL_RESULTS_DIR", "").strip() or os.path.join(LOCAL_EVAL_DIR, "results")
))
BASELINE_PATH = os.path.join(RESULTS_DIR, "baseline.json")

# 掉几条才算回归。给 1 条容差不是心虚，是承认模型输出有抖动：
# 容差设 0 的门禁会因为一条边界用例翻面就变红，红多了人就不看了——
# 和「eval 不进 push 门禁」是同一个理由的两种剂量。
EVAL_TOLERANCE = int(os.getenv("EVAL_TOLERANCE", "1"))
GRADER_TIER = os.getenv("EVAL_GRADER_TIER", "galleon")
SUT_MODEL = os.getenv("EVAL_SUT_MODEL") or TIERS["sloop"]
GRADER_MODEL = os.getenv("EVAL_GRADER_MODEL") or TIERS[GRADER_TIER]
V1_TOKENS = int(os.getenv("EVAL_V1_TOKENS", "4800"))
GRADER_TOKENS = int(os.getenv("EVAL_GRADER_TOKENS", "8000"))
REQUIRE_PINNED_MODELS = os.getenv("EVAL_REQUIRE_PINNED_MODELS", "1") != "0"
_TIER_ALIASES = {"raft", "sloop", "galleon", "ark"}

# V2 内部五个阶段都读同一个可覆盖出口；否则 run_eval 的 V1 固定了型号，V2 却仍会
# 偷偷走 sloop 档，表面是 A/B，实际上被测条件已经不同。
fact_checker.CHECK_MODEL = SUT_MODEL

# 档名不等于模型不同。TIERS 的每一档在没配 MODEL_GALLEON 时都会回落到 LLM_MODEL——
# 也就是说「评分员走 galleon、被测走 sloop」这句话可能两边指着同一个模型串。
# 那样这一层就从 LLM-as-judge 退化成了**自评**：被考的人兼任监考。
# 不阻断（他可能就只有一个模型可用），但必须吵，而且必须写进落盘文件——
# 三个月后翻出这份结果，得能一眼看出它当时算不算数。
GRADER_SELFGRADE = GRADER_MODEL == SUT_MODEL
_SELFGRADE_WARN = (
    f"⚠️ 评分员({GRADER_MODEL})和被测({SUT_MODEL})指向同一个模型——"
    "这一轮是自评，不是评测。给 EVAL_GRADER_MODEL 配一个不同且更强的具体型号才作数。"
)


def _model_pin_issues(judged: bool) -> list[str]:
    """评测只接受具体型号；档名会触发网关降级链，使同一轮混入多个真实模型。"""
    if not REQUIRE_PINNED_MODELS:
        return []
    requested = {"被测 EVAL_SUT_MODEL": SUT_MODEL}
    if judged:
        requested["评分员 EVAL_GRADER_MODEL"] = GRADER_MODEL
    return [f"{name}={model!r}" for name, model in requested.items()
            if (model or "").strip().lower() in _TIER_ALIASES]


def gw_selfgrade() -> dict:
    """第二层自评检测：比【真正作答的模型】，不比配置里写的串。

    为什么需要第二层。改用网关的档位名以后，.env 里写的是 MODEL_SLOOP=sloop、
    MODEL_GALLEON=galleon——两个串明摆着不同，上面那层 GRADER_SELFGRADE 当然说
    「不是自评」。可网关有降级链 galleon→sloop：上游一挂，两边都由 sloop 那个型号作答。
    **配置上不是自评，实际上是自评**，分数照出，一点痕迹没有。

    换句话说，第一层查的是「你打算让谁评」，这一层查的是「最后是谁评的」。
    只有第一层的时候，把 .env 从具体型号改成档位名，反而会让自评检测**从会响变成不会响**——
    多加一层抽象顺手把警报关了，是这类改动最典型的暗伤。

    三种状态必须分开，不许合并：没走网关 / 走了但查不到 / 查到了。
    读不到响应头就是**查不了**，不是「没降级」。"""
    rep = writing_assistant.gw_report()
    sut, grd = rep.get(SUT_MODEL, {}), rep.get(GRADER_MODEL, {})
    served_sut = sorted(set(sut.get("served", [])))
    served_grd = sorted(set(grd.get("served", [])))
    return {
        "calls": sum(d["calls"] for d in rep.values()),
        "headers_seen": sum(d["seen"] for d in rep.values()),
        "degraded_calls": sum(d["degraded"] for d in rep.values()),
        # 发了 X-GW-Cache: off 不等于网关照办。这个计数才是证据，上面那个只是意图。
        "cached_calls": sum(d.get("cached", 0) for d in rep.values()),
        "served_sut": served_sut,
        "served_grader": served_grd,
        # 两边都读到了才谈得上比。只读到一边就下结论，等于拿半张账本结账。
        "checkable": bool(served_sut and served_grd),
        "overlap": sorted(set(served_sut) & set(served_grd)),
        "effective_selfgrade": bool(served_sut and served_grd
                                    and set(served_sut) & set(served_grd)),
    }


def _gw_verdict() -> None:
    """网关那一层的结论行。四种情况各有各的说法，唯独不许有「默认没事」这一种。"""
    g = gw_selfgrade()
    if not g["calls"]:
        return
    if g["degraded_calls"]:
        print(f"⚠️ 网关降级 {g['degraded_calls']}/{g['calls']} 次调用（X-GW-Degraded）"
              "——这些题不是你点名的那一档答的，别把它们算进那一档的功劳里。")
    if g["cached_calls"]:
        print(f"⚠️ 这一轮有 {g['cached_calls']}/{g['calls']} 次回答是网关**缓存回放**"
              "（X-GW-Cache: hit）——回放的题等于没重跑，这个分数不能当回归门禁用。\n"
              "   评测默认已经发了 X-GW-Cache: off，还能命中说明旁路没生效"
              "（网关版本太老？），先修那个再谈分数。")
    if g["effective_selfgrade"]:
        print("⚠️ 实际自评：评分员和被测最后由**同一个模型**作答（"
              + "、".join(g["overlap"]) + "）。配置里两档写的是不同的串，"
              "是网关降级把它们并到了一起——这一轮的 LLM-as-judge 不作数。")
    elif g["checkable"]:
        print("✅ 网关留痕：被测由 " + "、".join(g["served_sut"])
              + " 作答、评分员由 " + "、".join(g["served_grader"]) + " 作答，确实是两个模型。")
    elif g["headers_seen"] == 0:
        print("ℹ️ 一次 X-GW-* 响应头都没读到（直连 provider，或 SDK 太老没有 with_raw_response）"
              "——「最后是谁作答的」这一轮**查不了**。查不了不等于没问题。")
    elif g["headers_seen"] == g["calls"]:
        # 头一次不落地都读到了，只是被测和评分员没同时出场（--no-judge / --v1-only 这类）。
        # 这跟「留痕不全」是两回事，别混着报：报错了原因，人就会去查根本没坏的那根管子。
        print("ℹ️ 网关的头全读到了，但这一轮被测和评分员没同时出场"
              "（多半是 --no-judge / --v1-only）——"
              "「两边是不是同一个模型」这一轮**没有可比的两边**，不是查不了。")
    else:
        print("ℹ️ 网关留痕不全（只有部分调用带回了 X-GW-* 头）——"
              "「最后是谁作答的」这一轮查不全。查不全同样不等于没问题。")

# verdict → label 映射（V2 输出对齐用例标签）
V2_MAP = {"support": "correct", "refute": "incorrect", "unverifiable": "unverifiable"}
GRADES = ("sound", "unsupported", "wrong")
UNPARSED_GRADE = "unparsed"   # 评分员没产出合法等级，不冒充「理由不合格」


def _load_case_data() -> dict:
    """在任何真实调用发生前校验整份用例；扩容时一处拼错不能烧完额度才被发现。"""
    with open(CASES_PATH, encoding="utf-8") as f:
        return eval_schema.require_valid_eval_data(json.load(f))


def _select_cases(data: dict, with_material: bool, split: str = "all") -> list[dict]:
    """选择本轮真正参与评测的题；CLI 默认 dev，但内部显式支持 all。"""
    if split not in ("dev", "test", "all"):
        raise ValueError(f"--split 只接受 dev/test/all，收到 {split!r}")
    key = "cases_material" if with_material else "cases"
    cases = data.get(key, [])
    return list(cases) if split == "all" else [c for c in cases if c["split"] == split]


def _case_meta(with_material: bool, split: str = "all") -> dict:
    """返回本轮用例集的冻结信息。

    指纹覆盖真正影响被测输入、金标准或切片语义的字段：题面 / label / as_of / taxonomy；
    素材集再加入本轮实际引用的 material packs。source / note 是人工审计信息，修链接、
    补注释不该让一份分数凭空失去可比性。反过来，只要题面、标签、分类或引用素材
    变了，旧基线就必须作废——同一个 8/10 被换到不同难度或风险桶里，也已经不是
    同一份量尺。
    """
    data = _load_case_data()
    cases = _select_cases(data, with_material, split)
    semantic_cases = sorted(
        (eval_schema.fingerprint_case(case) for case in cases),
        key=lambda case: str(case["id"]),
    )
    frozen = {"schema_version": data.get("_schema_version"), "cases": semantic_cases}
    if with_material:
        material_ids = sorted({case["material_id"] for case in cases})
        frozen["materials"] = {
            material_id: data["materials"][material_id] for material_id in material_ids
        }
    canonical = json.dumps(
        frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "case_set": "material" if with_material else "main",
        "sample_only": bool(data.get("_sample_only")),
        "split": split,
        "case_count": len(cases),
        "case_fingerprint": hashlib.sha256(canonical).hexdigest(),
        "schema_version": data.get("_schema_version"),
        "freeze_version": data.get("_freeze_version"),
        "dimensions": eval_schema.dimension_counts(cases),
    }

V1_SYS = """判断下面这条陈述的真伪，凭你自己的知识直接判：
- correct：陈述属实；incorrect：陈述有误；unverifiable：你无法确定真伪。
只输出 JSON：{"label": "correct|incorrect|unverifiable", "reason": "一句话"}"""

GRADER_SYS = """你是评测评分员（LLM-as-judge）。给你一条陈述、人工标注的正确标签、
被测系统给出的标签、它给出的理由，以及它当时看到的检索证据（可能为空）。

**只评理由，不评标签。** 标签对错已经由标签比对算过了，你要回答的是另一个问题：
它给出的这条理由，能不能支撑它给出的那个标签？

- sound：理由具体、与证据/事实一致，并且真的能推出它给的那个标签；
- unsupported：理由空泛、答非所问、复述陈述本身，或声称的依据在证据里找不到；
- wrong：理由里有明确的事实错误，或与它自己给出的标签自相矛盾。

【关键】标签蒙对但理由站不住，仍然判 unsupported 或 wrong——
把运气和能力分开，正是这一层存在的全部意义。
【关键】理由说「证据里没提到，所以无法判断」，在标签是 unverifiable 时是站得住的（sound）；
但如果证据里其实提到了，那就是 wrong。

只输出 JSON：{"grade": "sound|unsupported|wrong", "why": "一句话，指出具体问题或具体支撑"}"""


def judge_v1(statement: str, material: str = "") -> dict:
    """基线：不给素材、不给检索，纯裸判。

    material 形参只为与 judge_v2 同签名，**故意不使用**——V1 拿不到素材这件事本身就是被测点：
    「作者写自己项目时记岔了」这类错，靠模型的世界知识是不可能查出来的。"""
    def parse_label(text: str):
        parsed = fact_checker._json_object(text)
        return parsed if parsed and parsed.get("label") in (
            "correct", "incorrect", "unverifiable"
        ) else None

    parsed, failure = fact_checker._guarded_value(
        [{"role": "system", "content": V1_SYS}, {"role": "user", "content": statement}],
        SUT_MODEL, V1_TOKENS, "v1", "V1 裸判", parse_label, create=_create,
    )
    if parsed is None:
        parsed = {"label": "unverifiable", "reason": "V1 裸判输出解析失败"}
    return {"label": parsed["label"],
            "reason": parsed.get("reason", ""), "confidence": None, "checked": False,
            "degraded": "", "evidence": "裸判（无素材无检索）", "evidence_raw": "",
            "search_status": "not_run", "search_queries": [], "evidence_enriched": False,
            "stage_failures": [failure] if failure else []}


def judge_v2(statement: str, material: str = "") -> dict:
    r = check_claim(statement, material=material)
    return {"label": V2_MAP[r["verdict"]], "reason": r.get("reason", ""),
            "confidence": r.get("confidence"), "checked": r.get("checked", False),
            "degraded": r.get("degraded", ""), "evidence": r.get("evidence", ""),
            "evidence_raw": r.get("evidence_raw", ""),
            "search_status": r.get("search_status", "not_run"),
            "search_queries": list(r.get("search_queries", [])),
            "evidence_enriched": bool(r.get("evidence_enriched", False)),
            "stage_failures": r.get("stage_failures", [])}


# ── ① 标签比对 ────────────────────────────────────────────────────────────
def run(tag: str, judge, cases: list[dict], materials: dict[str, str] | None = None) -> dict:
    """跑一遍用例集，回一个可直接落盘的 dict。

    以前这里只 return acc（一个 float），判错明细打印完就丢——想复盘只能重跑，
    重跑既花额度又未必复现（温度没锁）。现在每条的裁决、置信度、证据、耗时全留下来。"""
    rows, hits = [], 0
    t0 = time.time()
    for c in cases:
        t1 = time.time()
        material = (materials or {}).get(c.get("material_id", ""), "")
        try:
            out = judge(c["statement"], material)
            err = ""
        except Exception as e:            # 一条炸了不该带走整批——前面几条的额度已经花掉了
            out = {"label": "unverifiable", "reason": f"判定异常：{e}", "confidence": None,
                   "checked": False, "degraded": "exception", "evidence": "", "evidence_raw": "",
                   "search_status": "not_run", "search_queries": [], "evidence_enriched": False,
                   "stage_failures": ["run_exception"]}
            err = f"{type(e).__name__}: {e}"
        ok = out["label"] == c["label"]
        hits += ok
        rows.append({"id": c["id"], "label": c["label"], "got": out["label"], "ok": ok,
                     "statement": c["statement"], "confidence": out["confidence"],
                     **eval_schema.case_metadata(c),
                     "checked": out["checked"], "degraded": out["degraded"],
                     "search_status": out.get("search_status", "not_run"),
                     "search_queries": list(out.get("search_queries", [])),
                     "evidence_enriched": bool(out.get("evidence_enriched", False)),
                     "stage_failures": list(out.get("stage_failures", [])),
                     "evidence": out["evidence"], "evidence_raw": out["evidence_raw"],
                     "reason": out["reason"], "error": err,
                     "elapsed_s": round(time.time() - t1, 2)})
        mark = "🔍" if out["checked"] else "⚪"
        warn = f" ⚠️{out['degraded']}" if out["degraded"] else ""
        print(f"  {'✓' if ok else '✗'}{mark} #{c['id']} 期望 {c['label']:<12} 得到 {out['label']}{warn}")

    elapsed = round(time.time() - t0, 1)
    degraded = [r for r in rows if r["degraded"]]
    stage_failed = [r for r in rows if r["stage_failures"]]
    print(f"\n{tag} 标签准确率：{hits}/{len(cases)}（耗时 {elapsed:.0f}s）")
    if degraded:
        kinds = {}
        for r in degraded:
            kinds[r["degraded"]] = kinds.get(r["degraded"], 0) + 1
        print("  ⚠️ 降级 " + f"{len(degraded)}/{len(cases)}："
              + "、".join(f"{k} {v} 条" for k, v in sorted(kinds.items()))
              + "——这批结论没真走完管线，别把它们算进 CRAG 的功劳里")
    if stage_failed:
        kinds = {}
        for row in stage_failed:
            for failure in row["stage_failures"]:
                kinds[failure] = kinds.get(failure, 0) + 1
        print("  ⛔ 阶段故障 " + f"{len(stage_failed)}/{len(cases)}："
              + "、".join(f"{k} {v} 条" for k, v in sorted(kinds.items()))
              + "——兜底给出了结果，但这轮不是完整 CRAG 运行")
    for r in rows:
        if not r["ok"]:
            print(f"  ✗ #{r['id']} {r['label']}→{r['got']}：{r['statement'][:40]}…")
    return {"tag": tag, "hits": hits, "total": len(cases), "elapsed_s": elapsed,
            "degraded": len(degraded), "stage_failed": len(stage_failed),
            "slices": eval_schema.slice_metrics(rows), "rows": rows}


# ── ② LLM-as-judge：评理由，不评标签 ──────────────────────────────────────
def grade_row(row: dict) -> dict:
    """单条评分。评分员看得到金标准和系统的理由，但**看不到「它答对了没有」这个结论**——
    告诉评分员「这条答对了」会诱导它顺着找理由（锚定效应），评出来的就不是理由质量了。"""
    ev = row.get("evidence_raw") or row.get("evidence") or "（无检索证据）"
    user = (f"陈述：{row['statement']}\n"
            f"人工标注的正确标签：{row['label']}\n"
            f"被测系统给出的标签：{row['got']}\n"
            f"被测系统给出的理由：{row.get('reason') or '（空）'}\n"
            f"它当时看到的证据：\n{ev}")
    def parse_grade(text: str):
        parsed = fact_checker._json_object(text)
        return parsed if parsed and parsed.get("grade") in GRADES else None

    g, failure = fact_checker._guarded_value(
        [{"role": "system", "content": GRADER_SYS}, {"role": "user", "content": user}],
        GRADER_MODEL, GRADER_TOKENS, "grader", "理由评分员", parse_grade, create=_create,
    )
    # 08-02 的 250 不够评分员把 JSON 写完，40 次里 9 次被截断；后来虽提到 800，
    # 推理型模型仍可能把预算烧在 reasoning。现在基础预算可配，且同样走 JSON 守卫。
    if g is None:
        return {"grade": UNPARSED_GRADE,
                "why": f"评分员没有产出合法 JSON（{failure or 'unknown'}）"}
    return {"grade": g["grade"], "why": g.get("why", "")}


def grade_run(r: dict) -> dict:
    """给一整轮的每条打分，把统计塞回 run dict。"""
    print(f"\n  ── LLM-as-judge（评分员：{GRADER_MODEL}，被测：{SUT_MODEL}）评 {r['tag']} 的理由 ──")
    if GRADER_SELFGRADE:
        print("  " + _SELFGRADE_WARN)
    total = len(r["rows"])
    for index, row in enumerate(r["rows"], 1):
        try:
            g = grade_row(row)
        except Exception as e:
            g = {"grade": UNPARSED_GRADE, "why": f"评分异常：{e}"}
        row["grade"], row["grade_why"] = g["grade"], g["why"]
        if index == 1 or index % 5 == 0 or index == total:
            print(f"  · 评分进度 {index}/{total}")

    rows = r["rows"]
    sound = sum(x["grade"] == "sound" for x in rows)
    unparsed = [x for x in rows if x["grade"] == UNPARSED_GRADE]           # 评分失败，不评价理由
    lucky = [x for x in rows if x["ok"] and x["grade"] in ("unsupported", "wrong")]
    argued = [x for x in rows if not x["ok"] and x["grade"] == "sound"]   # 标签错了，理由却站得住
    graded_total = len(rows) - len(unparsed)
    r["sound"] = sound
    r["graded_total"] = graded_total
    r["unparsed"] = len(unparsed)
    r["lucky"] = len(lucky)
    r["argued"] = len(argued)

    print(f"  {r['tag']} 理由合格：{sound}/{graded_total}")
    if unparsed:
        print(f"  ⚠️ 评分失败 {len(unparsed)}/{len(rows)}——单列 unparsed，"
              "不冒充理由不合格，也不计入蒙对：")
        for x in unparsed:
            print(f"     #{x['id']} {x['grade_why'][:60]}")
    if lucky:
        print(f"  🎲 蒙对 {len(lucky)}/{len(rows)}——标签算它对，但理由撑不住，"
              f"**这几条不该算进能力里**：")
        for x in lucky:
            print(f"     #{x['id']} [{x['grade']}] {x['grade_why'][:60]}")
    if argued:
        print(f"  🤔 判错但理由站得住 {len(argued)}/{len(rows)}——多半是用例标注本身有争议，回头看看这几条：")
        for x in argued:
            print(f"     #{x['id']} {x['label']}→{x['got']}：{x['grade_why'][:60]}")
    return r


# ── ③ 基线与回归判定 ──────────────────────────────────────────────────────
def _load_baseline(path: str = BASELINE_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _run_integrity_issues(runs: list[dict]) -> list[str]:
    """列出使评测失效的调用与结构化阶段故障。
    正常的证据不足单独统计；工具失败不得判为通过或写入有效基线。"""
    issues = []
    for r in runs:
        rows = r.get("rows") or []
        exceptions = [x for x in rows if x.get("degraded") == "exception" or x.get("error")]
        stage_failed = [x for x in rows if any(
            failure != "run_exception" for failure in (x.get("stage_failures") or [])
        )]
        if exceptions:
            issues.append(f"{r.get('tag', '?')} 有 {len(exceptions)}/{r.get('total', '?')} 条调用异常")
        if stage_failed:
            issues.append(f"{r.get('tag', '?')} 有 {len(stage_failed)}/{r.get('total', '?')} 条阶段故障")
        if r.get("unparsed", 0):
            issues.append(f"{r.get('tag', '?')} 有 {r['unparsed']}/{r.get('total', '?')} 条评分失败")
    return issues


def save_baseline(runs: list[dict], meta: dict) -> str:
    issues = _run_integrity_issues(runs)
    if issues:
        raise ValueError("拒绝把不完整跑批记成基线：" + "；".join(issues))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = _baseline_path(bool(meta.get("with_material")), meta.get("split", "all"))
    data = {"utc": meta["utc"], "tolerance": EVAL_TOLERANCE, "meta": meta,
            "runs": {r["tag"]: {"hits": r["hits"], "total": r["total"],
                                "sound": r.get("sound"),
                                "graded_total": r.get("graded_total"),
                                "unparsed": r.get("unparsed"),
                                "stage_failed": r.get("stage_failed", 0),
                                "slices": r.get("slices", {})} for r in runs}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _tier_drift(base: dict) -> tuple[list[str], list[str]]:
    """比较基线和本次实际参与评测的模型。
    被测模型与评分员改变时基线不可比较；其他未参与调用的档位只记录提示。
    旧基线缺失的字段不参与比较。"""
    m = base.get("meta") or {}
    hard = {"被测": (m.get("model_sloop"), SUT_MODEL),
            "评分员": (m.get("grader_model"), GRADER_MODEL)}
    bt, nt = m.get("tiers") or {}, dict(TIERS)
    soft = {f"{k}（本轮没参与评测）": (bt.get(k), nt.get(k))
            for k in sorted(set(bt) | set(nt)) if k not in ("sloop", GRADER_TIER)}

    def diff(d):
        return [f"{k}：{was} → {now}" for k, (was, now) in d.items()
                if was is not None and was != now]

    return diff(hard), diff(soft)


def _run_scope(runs: list[dict]) -> tuple[bool, str]:
    """从 run tag 还原集合与 split；旧 tag 没写 split 时按 all 兼容。"""
    token_sets = [set(r.get("tag", "").split("-")) for r in runs]
    with_material = bool(token_sets and all("material" in tokens for tokens in token_sets))
    splits = {name for tokens in token_sets for name in ("dev", "test", "all")
              if name in tokens}
    return with_material, next(iter(splits)) if len(splits) == 1 else "all"


def _baseline_path(with_material: bool, split: str) -> str:
    """公共/素材 × dev/test/all 各有自己的量尺；公共 all 兼容旧路径。"""
    if split == "all":
        if not with_material:
            return BASELINE_PATH
        stem, ext = os.path.splitext(BASELINE_PATH)
        return f"{stem}-material-all{ext}"
    stem, ext = os.path.splitext(BASELINE_PATH)
    suite = "material" if with_material else "main"
    return f"{stem}-{suite}-{split}{ext}"


def check_baseline(runs: list[dict]) -> bool:
    """True = 没有回归。基线里没记过的 tag 一律放行（第一次跑不该判红）。"""
    issues = _run_integrity_issues(runs)
    if issues:
        print("\n❌ 这一轮没有完整跑完：" + "；".join(issues) + "。\n"
              "   明细仍会落盘供排障，但回归门禁拒绝判绿；也不能拿兜底碰巧命中的标签记基线。")
        return False

    # 比分数之前先问一句「这一轮到底跑了没有」。缓存回放的答案和上一轮一字不差，
    # 拿它跟基线比，永远是 ✅ ——这是这套门禁能出的最安静的一种坏。
    replayed = gw_selfgrade()["cached_calls"]
    if replayed:
        print(f"\n❌ 这一轮有 {replayed} 次回答来自网关缓存回放，没有真跑。"
              "回归门禁拒绝为这种结果判绿。")
        return False

    with_material, split = _run_scope(runs)
    baseline_path = _baseline_path(with_material, split)
    base = _load_baseline(baseline_path)
    if not base:
        print(f"\nℹ️ 还没有基线（{os.path.relpath(baseline_path, HERE)}）。"
              "先跑一次 --set-baseline 记下来。")
        return True

    # 分数能比较的第一前提是「答的是同一套题」。只比 hits 会让 8/10 和 9/15
    # 看起来像提升了一条，实际连分母都换了。新基线用语义指纹精确识别换题；
    # 老基线没有指纹时仍可读，下面再用每轮 total 守住最基本的样本数一致性。
    current_cases = _case_meta(with_material, split)
    base_case_fingerprint = (base.get("meta") or {}).get("case_fingerprint")
    if (base_case_fingerprint is not None
            and base_case_fingerprint != current_cases["case_fingerprint"]):
        print(f"\n❌ 基线作废：{current_cases['case_set']} 用例集已经改变。\n"
              f"   基线指纹 {base_case_fingerprint[:12]}…，当前指纹 "
              f"{current_cases['case_fingerprint'][:12]}…。\n"
              "   先 review 并冻结新用例，再跑一次 --set-baseline；换题前后的分数不能比较。")
        return False

    # 先问「这两次还比得了吗」，再问「掉没掉分」。顺序反了，打印出来的红绿就是假的。
    blocking, noted = _tier_drift(base)
    if blocking:
        print(f"\n── 基线对比（记于 {base.get('utc', '?')}）──")
        for d in blocking + noted:
            print(f"  · {d}")
        print("\n❌ 基线作废：这次答题的模型和基线那次不是同一套。\n"
              "   **换了模型的两次跑分不能比**——拿旧基线判新模型，红绿都是假的：\n"
              "   掉分可能只是换了个更便宜的档，涨分也不代表管线变好了（而涨分会被抄进结果报告）。\n"
              "   确认上面这套档位就是你要的，跑一次 --set-baseline 重记基线，之后的对比才作数。")
        return False        # 故意判红。但修法只有一条命令，是确定性的红，不是会抽风的红
    if noted:
        print("\nℹ️ 有档位换了模型，但这一轮评测没调用到它们，分数照比：" + "；".join(noted))

    total_mismatches = []
    for r in runs:
        b = (base.get("runs") or {}).get(r["tag"])
        if b and b.get("total") != r["total"]:
            total_mismatches.append(
                f"{r['tag']}：基线 {b.get('total', '?')} 条，当前 {r['total']} 条"
            )
    if total_mismatches:
        print("\n❌ 基线作废：参与比较的用例数量不同（" + "；".join(total_mismatches) + "）。\n"
              "   样本集合不同，hits 的增减没有可比性；冻结后用 --set-baseline 重记。")
        return False

    tol = base.get("tolerance", EVAL_TOLERANCE)
    print(f"\n── 基线对比（记于 {base.get('utc', '?')}，容差 {tol} 条）──")
    ok = True
    for r in runs:
        b = base["runs"].get(r["tag"])
        if not b:
            print(f"  · {r['tag']}：基线里没有这一项，跳过")
            continue
        d = r["hits"] - b["hits"]
        sign = "＋" if d > 0 else "－" if d < 0 else "＝"
        line = f"  · {r['tag']}：{b['hits']}/{b['total']} → {r['hits']}/{r['total']}（{sign}{abs(d) or ''}）"
        if d < -tol:
            ok = False
            print(line + f"  ❌ 掉了 {abs(d)} 条，超过容差 {tol}")
        else:
            print(line + "  ✅")
    if not ok:
        print("\n❌ 效果回归：分数掉出容差。先看本地 results/ 里最新那份明细的判错条目，"
              "再决定是代码退步了还是用例该改。")
    return ok


# ── ④ 落盘 ────────────────────────────────────────────────────────────────
def _meta(with_material: bool, judged: bool, split: str = "all") -> dict:
    """跑批参数一并落盘——不然三周后翻出一个 8/10，说不清它是哪个阈值、哪个档位、哪个温度下跑出来的。"""
    return {
        **_case_meta(with_material, split),
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_sloop": SUT_MODEL,
        "sut_model": SUT_MODEL,
        "grader_tier": GRADER_TIER,
        "grader_model": GRADER_MODEL,
        "require_pinned_models": REQUIRE_PINNED_MODELS,
        # 整张档位表都记，不只记参与评测的那两档：阶梯是会重排的
        # （今天的 ark 可能就是明天的评分员），_tier_drift 得看得见那次重排。
        "tiers": dict(TIERS),
        "llm_as_judge": judged,
        # 这一轮打算不打算走缓存。记的是**发出的意图**——发了头不等于网关照办，
        # 真凭实据在下面 gateway.cached_calls 那个计数里。
        "gw_cache": EVAL_GW_CACHE,
        "grader_selfgrade": bool(judged and GRADER_SELFGRADE),   # True = 这份结果的评分层不算数
        # 第二层：网关说最后是谁作答的。配置串不同 ≠ 真的换了模型（降级链会把两档并到一起）。
        # 落盘存的是整个判定结构，不是一个 bool——三个月后翻出来，得能分清
        # 「查了，没问题」和「压根没查成」，这两件事看起来都像绿灯。
        "gateway": gw_selfgrade(),
        "conf_threshold": fact_checker.CONF_THRESHOLD,
        "temperature": os.getenv("LLM_TEMPERATURE") or "(provider 默认)",
        "seed": os.getenv("LLM_SEED") or "(未设)",
        "search_max_results": os.getenv("SEARCH_MAX_RESULTS", "5"),
        "token_param": writing_assistant._TOKEN_PARAM,
        "with_material": with_material,
        "tolerance": EVAL_TOLERANCE,
    }


def _dump(meta: dict, runs: list[dict]) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = meta["utc"].replace(":", "").replace("-", "")
    suffix = "-material" if meta["with_material"] else ""
    path = os.path.join(RESULTS_DIR, f"eval-{stamp}{suffix}-{meta['split']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "runs": runs}, f, ensure_ascii=False, indent=2)
    return path


def _verdict_line(runs: list[dict]) -> None:
    """结论行。只报分数和「多对/少对几条」，不报百分点——n=10 时一条就是十个百分点，
    写成「+15 个百分点」这种落不到格子上的数，等于自己承认没测过。"""
    by = {r["tag"].split("-")[0]: r for r in runs}
    v1, v2 = by.get("V1"), by.get("V2")
    if not (v1 and v2):
        return
    d = v2["hits"] - v1["hits"]
    word = "多对" if d > 0 else "少对" if d < 0 else "持平"
    line = (f"\n📊 结论：标签 V1 {v1['hits']}/{v1['total']} → V2 {v2['hits']}/{v2['total']}"
            f"（{word}{abs(d) if d else ''} 条）")
    if v1.get("sound") is not None and v2.get("sound") is not None:
        # 用 .get 而不是 v2['lucky']：这一行是**收尾的打印**，它挂掉不该毁掉一轮真花了钱的跑批。
        # （落盘也已经挪到它前面了，双保险。）
        graded_v1 = v1.get("graded_total", v1["total"])
        graded_v2 = v2.get("graded_total", v2["total"])
        line += (f"；理由合格 V1 {v1['sound']}/{graded_v1} → V2 {v2['sound']}/{graded_v2}"
                 f"（V2 蒙对 {v2.get('lucky', 0)} 条）")
    print(line)
    _slice_comparison(v1, v2)
    unparsed_v1, unparsed_v2 = v1.get("unparsed", 0), v2.get("unparsed", 0)
    if unparsed_v1 or unparsed_v2:
        print(f"⚠️ 评分失败：V1 {unparsed_v1}/{v1['total']}，V2 {unparsed_v2}/{v2['total']}。"
              "这些条目未进入理由质量与蒙对统计。")
    if GRADER_SELFGRADE and v2.get("sound") is not None:
        print("⚠️ " + _SELFGRADE_WARN)
    if v2["degraded"]:
        print(f"⚠️ V2 有 {v2['degraded']}/{v2['total']} 条走了降级路径，这轮分数别拿去写材料。")
    if v1.get("stage_failed", 0) or v2.get("stage_failed", 0):
        print(f"⛔ 阶段故障：V1 {v1.get('stage_failed', 0)}/{v1['total']}，"
              f"V2 {v2.get('stage_failed', 0)}/{v2['total']}。这轮不是完整评测。")


def _slice_comparison(v1: dict, v2: dict) -> None:
    """把同一批题的 V1→V2 变化按高信号维度摊开；全量维度仍保存在结果 JSON。"""
    left, right = v1.get("slices") or {}, v2.get("slices") or {}
    if not (left or right):
        return
    lines = []
    for dimension in eval_schema.TERMINAL_SLICE_DIMENSIONS:
        lb, rb = left.get(dimension, {}), right.get(dimension, {})
        values = sorted(set(lb) | set(rb))
        chunks = []
        for value in values:
            a, b = lb.get(value), rb.get(value)
            if not (a and b) or a["total"] != b["total"]:
                chunks.append(f"{value} 样本不一致")
                continue
            chunks.append(
                f"{value} {a['hits']}/{a['total']}→{b['hits']}/{b['total']}"
            )
        if chunks:
            lines.append(f"  · {dimension}：" + "；".join(chunks))
    if lines:
        print("\n── 切片对比（V1→V2；完整维度已写入结果 JSON）──")
        print("\n".join(lines))


def _split_arg(argv: list[str]) -> str:
    """读取 --split dev|test|all 与 --split=test；默认只跑可调的 dev。"""
    values = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--split=")]
    if "--split" in argv:
        index = argv.index("--split")
        if index + 1 >= len(argv):
            raise ValueError("--split 后必须跟 dev、test 或 all")
        values.append(argv[index + 1])
    if len(values) > 1:
        raise ValueError("--split 只能指定一次")
    split = values[0] if values else "dev"
    if split not in ("dev", "test", "all"):
        raise ValueError(f"--split 只接受 dev/test/all，收到 {split!r}")
    return split


def main() -> None:
    try:
        data = _load_case_data()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"⛔ 评测集不可用，尚未发出任何模型调用：{exc}")
        sys.exit(2)
    argv = sys.argv[1:]
    with_material = "--with-material" in argv
    judged = "--no-judge" not in argv
    try:
        split = _split_arg(argv)
    except ValueError as exc:
        print(f"⛔ 参数错误，尚未发出任何模型调用：{exc}")
        sys.exit(2)
    cases = _select_cases(data, with_material, split)
    tag_suffix = f"-{'material-' if with_material else ''}{split}"

    if data.get("_sample_only"):
        print("⚠️ 当前使用公开 smoke sample：它只用于验证 schema 和跑通管线，"
              "不能用来宣称效果。")
        print(f"   完整评测集应放在：{os.path.relpath(LOCAL_CASES_PATH, HERE)}")
        if split == "test":
            print("⛔ 公开 sample 没有 held-out test；拒绝伪装成阶段验收。")
            sys.exit(2)
        if "--set-baseline" in argv or "--check-baseline" in argv:
            print("⛔ 公开 sample 不能设置或检查效果基线。")
            sys.exit(2)

    pin_issues = _model_pin_issues(judged)
    if pin_issues:
        print("⛔ 评测模型未固定到具体型号，尚未发出任何模型调用：" + "；".join(pin_issues) + "\n"
              "   网关档名会走自动降级链，让同一轮混入多个真实模型。请设置例如：\n"
              "   EVAL_SUT_MODEL=deepseek-v4-pro\n"
              "   EVAL_GRADER_MODEL=claude-sonnet-4-6\n"
              "   只有临时排障时才用 EVAL_REQUIRE_PINNED_MODELS=0 关闭这道闸。")
        sys.exit(2)

    if with_material:
        # 素材用例单独跑、单独报分，**绝不并进主用例集**——
        # 公共知识与私有材料评估的是不同能力，必须分开跑、分开报告和维护基线。
        if not cases:
            print(f"{os.path.basename(CASES_PATH)} 的 cases_material 里没有 split={split} 的用例，跳过。")
            return
        print(f"\n═══ 素材对照用例 · {split}（{len(cases)} 条）═══")
        print("   V1 照常裸判、拿不到素材——**这不是让 V1 吃亏，这正是被测的那项能力**：")
        print("   「作者写自己项目时记岔了」这类错，靠模型的世界知识不可能查出来。")
        runs = []
        if "--v2-only" not in argv:
            print(f"\n─── V1 裸判基线 ───")
            runs.append(run("V1" + tag_suffix, judge_v1, cases))
        if "--v1-only" not in argv:
            print(f"\n─── V2 CRAG（带素材）───")
            runs.append(run("V2" + tag_suffix, judge_v2, cases, data["materials"]))
    else:
        if not cases:
            print(f"{os.path.basename(CASES_PATH)} 的 cases 里没有 split={split} 的用例，跳过。")
            return
        runs = []
        if "--v2-only" not in argv:
            print(f"\n═══ V1 裸判基线 · {split}（{len(cases)} 条）═══")
            runs.append(run("V1" + tag_suffix, judge_v1, cases))
        if "--v1-only" not in argv:
            print(f"\n═══ V2 CRAG 简化版 · {split}（{len(cases)} 条，低置信会真搜索、较慢）═══")
            runs.append(run("V2" + tag_suffix, judge_v2, cases))

    if judged:
        if GRADER_SELFGRADE:
            print("\n" + "!" * 60 + "\n" + _SELFGRADE_WARN + "\n" + "!" * 60)
        for r in runs:
            grade_run(r)

    # 先落盘，再打印结论行。顺序是有代价的教训：一轮 eval 是真花钱真花时间的，
    # 收尾那行格式化字符串要是 KeyError 了，整轮数据就跟着没了。
    # **打印可以挂，数据不能丢。**
    meta = _meta(with_material, judged, split)
    path = _dump(meta, runs)
    _verdict_line(runs)
    _gw_verdict()
    print(f"💾 明细已落盘：{os.path.relpath(path, HERE)}")
    print("   （引用这个分数时连样本量一起引：写 8/10，别写 80%。）")

    if "--set-baseline" in argv:
        # 基线比单次跑分更值得护：一份被缓存回放污染的基线会一直骗后面**每一次**对比，
        # 而且越往后越没人记得它是怎么来的。
        if gw_selfgrade()["cached_calls"]:
            print("⛔ 这一轮有缓存回放，拒绝记成基线。先让它真跑一遍再记。")
        elif _run_integrity_issues(runs):
            print("⛔ 这一轮有调用异常或阶段故障，拒绝记成基线。明细已落盘，先修故障。")
        else:
            baseline_path = save_baseline(runs, meta)
            print(f"📌 已记为基线：{os.path.relpath(baseline_path, HERE)}")
    if "--check-baseline" in argv and not check_baseline(runs):
        sys.exit(1)


if __name__ == "__main__":
    main()
