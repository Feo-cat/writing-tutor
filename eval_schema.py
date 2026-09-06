"""CRAG eval 用例的多轴 schema 与校验器。

业务结论只有 correct / incorrect / unverifiable 三类；下面这些维度描述的是
「这道题为什么难、证据从哪里来、预期需要怎样核查」。把两者分开后，扩容时既能
保持 verdict 接口稳定，也能按失败机制切片，而不是把所有东西揉进一个总准确率。
"""

from __future__ import annotations

from collections import Counter


SCHEMA_VERSION = "2026-08-25-v2"
LABELS = ("correct", "incorrect", "unverifiable")

# 顺序同时决定终端切片报告的展示顺序。claim_type 是唯一多值维度：一条声明可以
# 同时是「版本事实 + 数字 + 否定」。group_id 允许为空；它把同一事实家族的相关题
# 绑在一起，避免把三条最小变体当作三个完全独立样本夸大有效规模。
DIMENSIONS = {
    "split": ("dev", "test"),
    "domain": (
        "ai_research", "llm_platform", "programming_runtime", "web_protocol",
        "developer_tooling", "cloud_platform", "data_system", "security",
        "industry_statistics", "project_internal", "other_technical",
    ),
    "claim_type": (
        "definition", "capability", "implementation", "configuration",
        "protocol_semantics", "versioned", "numeric", "temporal", "statistical",
        "future", "causal", "comparison", "enumeration", "negation",
    ),
    "evidence_mode": ("public_snapshot", "live_web", "supplied_material", "insufficient"),
    "evidence_relation": (
        "supports", "contradicts", "insufficient", "partial", "irrelevant", "conflicting",
    ),
    "retrieval_need": ("not_needed", "recommended", "required", "material_only"),
    "freshness": ("stable", "time_sensitive", "as_of"),
    "difficulty": ("easy", "medium", "hard"),
    "risk": ("standard", "false_support", "false_refute", "bidirectional"),
}

REQUIRED_FIELDS = (
    "id", "label", "statement", "split", "domain", "claim_type", "evidence_mode",
    "evidence_relation", "retrieval_need", "freshness", "difficulty", "risk", "group_id",
)

# 这些字段改变后，旧的按切片分数已经失去可比性，所以必须进入语义指纹。
# source / note 仍是人工审计元数据：换链接或补解释不应让基线作废。
FINGERPRINT_FIELDS = REQUIRED_FIELDS + ("as_of", "material_id")

# 全量切片会落进结果 JSON；终端只展示最有诊断价值的几轴，避免规模扩大后刷满屏。
SLICE_DIMENSIONS = tuple(DIMENSIONS) + ("label", "group_id", "material_id")
TERMINAL_SLICE_DIMENSIONS = (
    "split", "label", "evidence_mode", "retrieval_need", "difficulty", "risk", "claim_type",
    "material_id",
)


def case_metadata(case: dict) -> dict:
    """取一条用例的切片元数据，供 run_eval 原样带进行结果行。"""
    return {
        name: case.get(name)
        for name in tuple(DIMENSIONS) + ("group_id", "material_id")
    }


def fingerprint_case(case: dict) -> dict:
    """取影响可比性的字段；多值标签按集合归一化，纯重排不制造一份“新量尺”。"""
    out = {field: case[field] for field in FINGERPRINT_FIELDS if field in case}
    if "claim_type" in out:
        out["claim_type"] = sorted(out["claim_type"])
    return out


def slice_metrics(rows: list[dict]) -> dict:
    """按所有维度汇总 hits/total；多值 claim_type 会分别计入每个命中的桶。"""
    out = {}
    for dimension in SLICE_DIMENSIONS:
        buckets: dict[str, dict[str, int]] = {}
        for row in rows:
            raw = row.get(dimension)
            values = raw if isinstance(raw, list) else [raw]
            for value in values:
                if value in (None, ""):
                    continue
                bucket = buckets.setdefault(str(value), {"hits": 0, "total": 0})
                bucket["total"] += 1
                bucket["hits"] += int(bool(row.get("ok")))
        if buckets:
            out[dimension] = dict(sorted(buckets.items()))
    return out


def dimension_counts(cases: list[dict]) -> dict:
    """只统计数据集构成，不看模型结果；用于审计扩容后有没有新的偏科。"""
    rows = [{**case, "ok": False} for case in cases]
    metrics = slice_metrics(rows)
    return {dimension: {value: bucket["total"] for value, bucket in buckets.items()}
            for dimension, buckets in metrics.items()}


def validate_eval_data(data: dict) -> list[str]:
    """返回全部 schema 问题；调用方一次看到所有错，不必修一个再跑一次。"""
    issues: list[str] = []
    if data.get("_schema_version") != SCHEMA_VERSION:
        issues.append(
            f"_schema_version 应为 {SCHEMA_VERSION!r}，收到 {data.get('_schema_version')!r}"
        )

    materials = data.get("materials")
    if not isinstance(materials, dict) or not materials:
        issues.append("materials 必须是非空对象（material_id → 素材正文）")
        materials = {}
    else:
        for material_id, text in materials.items():
            if not isinstance(material_id, str) or not material_id.strip():
                issues.append("materials 的键必须是非空字符串")
            if not isinstance(text, str) or not text.strip():
                issues.append(f"materials[{material_id!r}] 必须是非空字符串")

    seen_ids = Counter()
    for suite in ("cases", "cases_material"):
        items = data.get(suite)
        if not isinstance(items, list):
            issues.append(f"{suite} 必须是数组")
            continue
        for index, case in enumerate(items):
            where = f"{suite}[{index}]"
            if not isinstance(case, dict):
                issues.append(f"{where} 必须是对象")
                continue
            missing = [field for field in REQUIRED_FIELDS if field not in case]
            if missing:
                issues.append(f"{where} 缺字段：{', '.join(missing)}")
                continue

            case_id = case["id"]
            if not isinstance(case_id, (int, str)):
                issues.append(f"{where}.id 必须是整数或字符串")
            else:
                seen_ids[case_id] += 1
            if case["label"] not in LABELS:
                issues.append(f"{where}.label 非法：{case['label']!r}")
            if not isinstance(case["statement"], str) or not case["statement"].strip():
                issues.append(f"{where}.statement 不能为空")

            for dimension, allowed in DIMENSIONS.items():
                value = case.get(dimension)
                values = value if dimension == "claim_type" and isinstance(value, list) else [value]
                if dimension == "claim_type" and (not isinstance(value, list) or not value):
                    issues.append(f"{where}.claim_type 必须是非空数组")
                    continue
                if len(values) != len(set(map(repr, values))):
                    issues.append(f"{where}.{dimension} 含重复值")
                invalid = [item for item in values if item not in allowed]
                if invalid:
                    issues.append(f"{where}.{dimension} 非法：{invalid!r}")

            group_id = case.get("group_id")
            if group_id is not None and (not isinstance(group_id, str) or not group_id.strip()):
                issues.append(f"{where}.group_id 只能是非空字符串或 null")

            mode = case.get("evidence_mode")
            if mode == "public_snapshot" and not case.get("source"):
                issues.append(f"{where} 使用 public_snapshot 时必须带 source")
            if case.get("freshness") == "as_of" and not case.get("as_of"):
                issues.append(f"{where} freshness=as_of 时必须带 as_of")
            if suite == "cases_material" and mode != "supplied_material":
                issues.append(f"{where} 属于素材集，evidence_mode 必须是 supplied_material")
            if suite == "cases" and mode == "supplied_material":
                issues.append(f"{where} 属于公共集，不能声明 supplied_material")
            material_id = case.get("material_id")
            if suite == "cases_material":
                if not isinstance(material_id, str) or not material_id.strip():
                    issues.append(f"{where}.material_id 必须是非空字符串")
                elif material_id not in materials:
                    issues.append(f"{where}.material_id 引用了不存在的素材：{material_id!r}")
            elif material_id is not None:
                issues.append(f"{where} 属于公共集，不能声明 material_id")

    duplicates = [str(case_id) for case_id, count in seen_ids.items() if count > 1]
    if duplicates:
        issues.append("用例 id 重复：" + ", ".join(sorted(duplicates)))
    return issues


def require_valid_eval_data(data: dict) -> dict:
    issues = validate_eval_data(data)
    if issues:
        raise ValueError("评测集 schema 校验失败：\n- " + "\n- ".join(issues))
    return data
