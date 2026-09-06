"""项目复盘的共用提示词与文件续写工具。
导入时将写作引擎切换为访谈模式；server 仅在配置本地素材时导入。
保留 run_with_material 作为内部回归检查所用的串行驱动，不提供独立命令行入口。"""

import json
import os
import sys

import writing_assistant as wa

# ── making-of 版策划：作者是亲历者，排「决策/踩坑/取舍」的弧线，不排科普 ──────────
wa.MAKINGOF = True   # 让 _tutor_rounds 选「决策/踩坑/取舍」那句兜底问，而不是学习模式那句
wa.PLANNER_SYS = """你是技术博客策划。作者是这个项目的【亲历者与作者】——他做过、踩过、决策过，
下面给你的素材就是他自己的真实经历。这是一篇 making-of / 复盘文章，不是科普。据素材排提纲：
- 3-5 节，走「钩子/起因 → 怎么做 → 代价与踩坑 → 什么时候值得（收）」的弧；
- 每节都要能引出作者【讲他自己的决策、踩的坑、权衡与数字】，不是讲通用概念；
- 每节**只输出一行**，格式：`节标题 | 这节要请作者讲清的 1-2 个点（他的决策/踩坑/代价/数字）`；
- 紧扣素材里的真实事实（技术名、数字、事件），别引入素材里没有的东西；
- 只输出提纲本身，别写开场白或解释。"""

# ── making-of 版访谈员（顶替导师）：作者是专家，任务是把经历问出来，别反过来教他 ──
wa.TUTOR_SYS = """你是技术博客的访谈员，正在就某一节访谈【这个项目的作者本人】。
他是这件事的专家（他做的），你的任务是把他脑子里的【经历、理由、代价、数字】问出来，
好让他用自己的话写成这一节。【绝对不要】反过来给他讲基础概念——他比你更懂这个项目。

每一轮先判断、再决定：
- 看作者上一次的回答，判断这节要点（他的决策 / 踩坑 / 权衡 / 数字）是否已经问够、够他写成一段扎实的话；
- 够了 → 【只输出一行】：`【本节已掌握】<一句话说这节素材已问齐>`，别再问；
- 没够 → 严格按这个格式（别的都不要）：
讲：<用一句话把这节要挖的点摆出来（基于素材点，不展开教学、不复述一大段）>
问：<请作者讲清这个点——为什么这么决定 / 当时具体怎么做 / 代价和踩坑是什么 / 有没有数字。要能引出一段话，不要是非题>
判定已问齐时，【只输出】那一行 `【本节已掌握】...`，不要带"讲：/问："。"""

from writing_assistant import plan_outline, tutor_section, edit_section, critique  # noqa: E402


def _sidecar(out_path: str | None, suffix: str) -> str | None:
    """由草稿路径派生边车文件名（提纲缓存 / transcript）。out_path 为空 → 无边车。"""
    return f"{out_path}.{suffix}" if out_path else None


def _load_or_make_outline(topic: str, material: str, cache_path: str | None) -> list[dict]:
    """提纲固定：有缓存就复用（重跑不漂移），没有就排一版并存下来。想重排 → 删缓存文件。"""
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            outline = json.load(f)
        print(f"🗂 提纲：复用已固定的 {cache_path}（{len(outline)} 节，不重排；想重排就删它）")
        return outline
    outline = plan_outline(topic, material)
    if cache_path and outline:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(outline, f, ensure_ascii=False, indent=2)
        print(f"🗂 提纲：新排并固定到 {cache_path}（下次重跑直接复用，不再漂移）")
    return outline


def _done_titles(out_path: str | None) -> set[str]:
    """扫已有草稿里的 ## 节标题 = 已完成的节（断点续跑靠它跳过）。"""
    done: set[str] = set()
    if out_path and os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("## "):
                    done.add(ln[3:].strip())
    return done


def _has_critique(out_path: str | None) -> bool:
    """草稿里是否已追加过终审意见（避免续跑时重复终审、重复追加）。"""
    return bool(out_path) and os.path.exists(out_path) \
        and "## 🔍 终审意见" in open(out_path, encoding="utf-8").read()


def run_with_material(topic: str, material: str, out_path: str | None) -> tuple[str, bool]:
    """返回 (整份草稿文本, 是否全节答齐)。"""
    print(f"\n📌 选题：{topic}\n{'─' * 56}")
    print("📄 素材：手写 making-of 素材（跳过研究员上网搜）")
    outline = _load_or_make_outline(topic, material, _sidecar(out_path, "outline.json"))
    if not outline:
        print("⚠️ 策划没解析出提纲，检查 material 或重跑。")
        sys.exit(1)
    print("🗂 提纲：")
    for s in outline:
        print(f"  - {s['title']}  ｜ {s['points']}")

    done = _done_titles(out_path)                       # 断点续跑：已写进草稿的节
    if out_path and not os.path.exists(out_path):       # 只在【首次】落标题，续跑时追加、不清空
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# {topic}\n\n")
    if done:
        # 按提纲顺序显示，不按 set 的顺序：set 是哈希序，同样两节每次跑打出来的先后都不一样，
        # 让人对着两次输出 diff 时凭空多出一行假差异（跳过逻辑本身只看 in，不看序，一直是对的）。
        print(f"↩️  续跑：已完成 {len(done)} 节，将跳过 → "
              f"{'、'.join(s['title'] for s in outline if s['title'] in done)}")
    transcript = _sidecar(out_path, "transcript.md")

    for i, s in enumerate(outline, 1):
        if s["title"] in done:
            print(f"\n⏭  第 {i}/{len(outline)} 节已在草稿里，跳过：{s['title']}")
            continue
        print(f"\n{'═' * 56}\n📝 第 {i}/{len(outline)} 节：{s['title']}")
        records = tutor_section(s, material, transcript_path=transcript)   # 访谈这一节（交互 + 逐句落盘）
        body = f"## {s['title']}\n\n{edit_section(s, records)}"
        if out_path:                                    # 每答完一节即追加 → Ctrl+C 不丢
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(body + "\n\n")
            print(f"  💾 第 {i} 节已存入 {out_path}")

    all_done = all(s["title"] in _done_titles(out_path) for s in outline) if out_path else True
    draft = open(out_path, encoding="utf-8").read() if (out_path and os.path.exists(out_path)) else f"# {topic}\n\n"
    return draft, all_done
