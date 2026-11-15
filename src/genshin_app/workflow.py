"""One semantic audit record, several delivery views; never a second executable rotation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import uuid4

from genshin_app.equipment.files import fingerprint, input_view, publish_files
from genshin_app.equipment.schema import PlanError, _json_guard

RECORD_TYPES = ("lookup", "issue", "decision", "task_source", "requirement", "source_note", "observation")
GAP_TYPES = ("missing", "not_found", "not_adopted", "interface", "conflicting", "not_required")
_HEADER_FIELDS = {"schema_version", "task_id", "binding", "plan_id", "revision", "input_fingerprint", "audited_input_fingerprint",
                  "review_status", "note"}
RECORD_FIELDS = frozenset({
    "id", "type", "description", "gap_type", "local_checked", "source_refs",
    "adopted_refs", "not_adopted_reason", "disposition", "supersedes", "input_refs", "basis", "expectation",
    "source_part", "observation_refs", "execution_intent",
})
SOURCE_PART_FIELDS = ("id", "label", "source_ref", "trigger", "holder", "beneficiaries", "damage_family",
                      "sampling", "duration", "refresh", "relationship", "parameters")


def audit_record_schema():
    """Return the Agent-facing record schema anchored to audit constants."""
    text = {"type": "string", "minLength": 1, "maxLength": 4096}
    texts = {"type": "array", "maxItems": 128, "items": text}
    sources = {"type": "array", "minItems": 1, "maxItems": 128,
               "items": {"type": "object"}}

    # Keep one closed object shape for the production DSH schema compiler.
    # It intentionally rejects ambiguous ``anyOf`` branches with the same
    # root type.  Lookup-specific required fields and adopted/not-adopted
    # exclusivity remain authoritative in ``validate_audit`` and are also
    # stated in the tool description/examples shown to the model.
    return {
        "type": "object",
        "properties": {
            "id": text,
            "type": {"type": "string", "enum": list(RECORD_TYPES)},
            "description": {"type": "string", "minLength": 1},
            "gap_type": {"type": "string", "enum": list(GAP_TYPES)},
            "local_checked": texts,
            "source_refs": sources,
            "adopted_refs": {**texts, "minItems": 1},
            "not_adopted_reason": text,
            "disposition": {"type": "string", "enum": ["pending", "adopted", "implemented_elsewhere", "not_applicable", "user_excluded", "not_implemented", "approximation"]},
            "supersedes": texts, "input_refs": texts, "expectation": {"type": "object"},
            "observation_refs": texts,
            "execution_intent": {"type": "string", "enum": ["execute", "answer_only"]},
            "source_part": {"type": "object", "required": ["id", "label"], "additionalProperties": False,
                "properties": {key: {"type": "object"} if key == "source_ref" else text
                               for key in SOURCE_PART_FIELDS}},
        },
        "required": ["id", "type", "description"],
        "additionalProperties": False,
        "examples": [
            {"id": "missing.skill-fact", "type": "issue",
             "description": "本地资料未提供该动作的命中频率。"},
            {"id": "lookup.skill-fact", "type": "lookup",
             "description": "核实持续攻击频率。", "gap_type": "missing",
             "local_checked": ["query-1"], "source_refs": [{"kind": "web", "id": "research-1"}],
             "adopted_refs": ["finding:skill-fact"]},
        ],
    }


def new_audit(plan):
    return {"schema_version": 1, "plan_id": plan["plan_id"],
            "input_fingerprint": fingerprint(input_view(plan)), "records": [],
            "scope": {"included": [], "excluded": [], "pending": []}}


def audit_binding(plan):
    if plan is None:
        return {"state": "unbound"}
    return {"state": "bound", "plan_id": plan["plan_id"], "input_fingerprint": fingerprint(input_view(plan))}


def new_workspace_audit(plan=None):
    return {"schema_version": 2, "task_id": "task." + uuid4().hex, "binding": audit_binding(plan),
            "records": [], "scope": {"included": [], "excluded": [], "pending": []}}


def saved_audit_binding(audit):
    if audit["schema_version"] == 2:
        return audit["binding"]
    return {"state": "bound", "plan_id": audit["plan_id"], "input_fingerprint": audit["input_fingerprint"]}


def upgrade_workspace_audit(audit):
    if audit["schema_version"] == 2:
        return audit
    return {"schema_version": 2, "task_id": audit["plan_id"], "binding": saved_audit_binding(audit),
            "records": audit["records"], "scope": audit.get("scope", {})}


def validate_audit(audit):
    _json_guard(audit, "audit")
    if not isinstance(audit, dict) or type(audit.get("schema_version")) is not int or audit["schema_version"] not in (1, 2):
        raise PlanError("不支持的工作记录版本", "invalid_audit")
    headers = {"plan_id", "input_fingerprint"} if audit["schema_version"] == 1 else {"task_id", "binding"}
    if set(audit) - {"schema_version", "records", "scope"} - headers:
        raise PlanError("工作记录包含未知字段", "invalid_audit")
    if audit["schema_version"] == 2:
        binding = audit.get("binding")
        if not isinstance(binding, dict) or binding.get("state") not in ("bound", "unbound"):
            raise PlanError("工作记录需有明确绑定状态", "invalid_audit")
        fields = {"state", "plan_id", "input_fingerprint"} if binding["state"] == "bound" else {"state"}
        if set(binding) != fields or any(not isinstance(binding[key], str) or not binding[key].strip() for key in fields):
            raise PlanError("工作记录绑定身份无效", "invalid_audit")
        text_headers = ("task_id",)
    else:
        text_headers = ("plan_id", "input_fingerprint")
    for key in text_headers:
        if not isinstance(audit.get(key), str) or not audit[key].strip():
            raise PlanError(f"工作记录缺少 {key}", "invalid_audit")
    records = audit.get("records")
    if not isinstance(records, list) or len(records) > 2000:
        raise PlanError("工作记录应为不超过2000项的列表", "invalid_audit")
    ids = set()
    for index, record in enumerate(records):
        path = f"records[{index}]"
        if not isinstance(record, dict):
            raise PlanError(f"{path} 需要对象", "invalid_audit")
        if set(record) - RECORD_FIELDS:
            raise PlanError(f"{path} 包含未知字段", "invalid_audit")
        if record.keys() & _HEADER_FIELDS:
            raise PlanError(f"{path} 不得覆盖系统的版本、指纹或审核状态字段", "invalid_audit")
        for key in ("id", "type", "description"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise PlanError(f"{path}.{key} 需要非空文本", "invalid_audit")
        if record["id"] in ids or record["id"].startswith("system.") or record["type"] not in RECORD_TYPES:
            raise PlanError(f"{path} 身份重复或类型不支持", "invalid_audit")
        ids.add(record["id"])
        if "not_adopted_reason" in record and (not isinstance(record["not_adopted_reason"], str) or not record["not_adopted_reason"].strip()):
            raise PlanError(f"{path}.not_adopted_reason 需要非空文本", "invalid_audit")
        for key in ("adopted_refs", "local_checked", "supersedes", "input_refs", "observation_refs"):
            values = record.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise PlanError(f"{path}.{key} 需要文本列表", "invalid_audit")
        from genshin_app.task_evidence import DISPOSITIONS
        if "disposition" in record and record["disposition"] not in DISPOSITIONS:
            raise PlanError(f"{path}.disposition不支持", "invalid_audit")
        if any(key in record and not isinstance(record[key], dict) for key in ("basis", "expectation")):
            raise PlanError(f"{path}.basis/expectation需要对象", "invalid_audit")
        if "source_part" in record:
            part = record["source_part"]
            if (record["type"] != "source_note" or not isinstance(part, dict) or set(part) - set(SOURCE_PART_FIELDS)
                    or not {"id", "label"} <= part.keys()
                    or any(not isinstance(value, dict) or not value for key, value in part.items() if key == "source_ref")
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 4096
                           for key, value in part.items() if key != "source_ref")):
                raise PlanError(f"{path}.source_part需要有效的来源子项", "invalid_audit")
        sources = record.get("source_refs", [])
        if not isinstance(sources, list) or any(not isinstance(s, dict) or not s for s in sources):
            raise PlanError(f"{path}.source_refs 需要来源对象列表", "invalid_audit")
        if "execution_intent" in record and (record["type"] != "decision"
                or record["execution_intent"] not in ("execute", "answer_only") or not sources):
            raise PlanError(f"{path}.execution_intent需要decision记录及原题引用", "invalid_audit")
        if record["type"] == "lookup":
            if record.get("gap_type") not in GAP_TYPES or "local_checked" not in record:
                raise PlanError(f"{path} 需要缺口分类及本地查询记录", "invalid_audit")
            if not sources:
                raise PlanError(f"{path} 需要实际来源或尚未返回的查询说明", "invalid_audit")
            if not record.get("adopted_refs") and not record.get("not_adopted_reason"):
                raise PlanError(f"{path} 需要采用位置或未采用原因", "invalid_audit")
    for record in records:
        if any(old == record["id"] or old not in ids for old in record.get("supersedes", [])):
            raise PlanError("supersedes必须引用其他已存在记录", "invalid_audit")
    edges = {r["id"]: r.get("supersedes", []) for r in records}
    # Iterative colour DFS avoids quadratic walks and recursion limits for long tasks.
    colours = {}
    for node in edges:
        stack = [(node, False)]
        while stack:
            current, exiting = stack.pop()
            if exiting:
                colours[current] = 2
                continue
            if colours.get(current) == 2:
                continue
            if colours.get(current) == 1:
                raise PlanError("supersedes不能形成循环", "invalid_audit")
            colours[current] = 1
            stack.append((current, True))
            stack.extend((old, False) for old in edges[current])
    scope = audit.get("scope", {})
    if not isinstance(scope, dict) or set(scope) - {"included", "excluded", "pending"}:
        raise PlanError("scope 只接受included/excluded/pending", "invalid_audit")
    for key, values in scope.items():
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise PlanError(f"scope.{key} 需要文本列表", "invalid_audit")
    return audit


def audit_views(audit, plan):
    """Missing/stale audit evidence stays visible; rendering never asserts game truth."""
    validate_audit(audit)
    binding = saved_audit_binding(audit)
    if binding.get("plan_id") != plan["plan_id"]:
        raise PlanError("工作记录属于另一方案", "audit_plan_mismatch")
    current = fingerprint(input_view(plan))
    stale = current != binding["input_fingerprint"]
    header = {"schema_version": 1, "plan_id": plan["plan_id"], "revision": plan["revision"],
              "input_fingerprint": current, "audited_input_fingerprint": binding["input_fingerprint"],
              "review_status": "stale" if stale else "declared",
              "note": "记录与当前输入绑定不等于游戏事实已核实；机械流水来自引擎，不由本记录重建。"}
    records = copy.deepcopy(audit["records"])
    lookups = [r for r in records if r["type"] == "lookup"]
    issues = [r for r in records if r["type"] == "issue"]
    if stale:
        issues.append({"id": "system.audit_stale", "type": "issue", "description":
                       "方案已变更，需复核来源采用和范围；旧记录保留，不能直接声称已核对最新方案。"})
    text = ["# 排轴来源与范围", "", f"方案：{plan['plan_id']} · 修订 {plan['revision']}", "",
            "输入已变更，来源采用待复核。" if stale else "工作记录对应当前输入；机制执行另见计算流水。", ""]
    for key, label in (("included", "计入范围"), ("excluded", "明确不计"), ("pending", "待确认")):
        text.extend([f"## {label}", ""])
        values = audit.get("scope", {}).get(key, [])
        text.extend([f"- {value}" for value in values] or ["尚无记录。"])
        text.append("")
    text.extend(["## 采用与决策", ""])
    for r in records:
        text.append(f"- {r['id']}：{r['description']}")
        if r.get("adopted_refs"):
            text.append("  采用位置：" + "、".join(r["adopted_refs"]))
        elif r.get("not_adopted_reason"):
            text.append("  未采用：" + str(r["not_adopted_reason"]))
    from genshin_app.task_evidence import review_records
    return {"task-evidence.json": review_records(records, plan, task_id=audit.get("task_id", plan["plan_id"])),
            "issues.json": {**header, "items": issues},
            "web_findings.json": {**header, "items": lookups},
            "lookups.jsonl": [{**r, **header} for r in lookups],
            "rotation.md": "\n".join(text) + "\n"}


def export_audit(audit, plan, output_dir, *, overwrite=False):
    views = audit_views(audit, plan)
    outputs = []
    for name, value in views.items():
        if name.endswith(".jsonl"):
            content = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in value)
        elif name.endswith(".json"):
            content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        else:
            content = value
        outputs.append((Path(output_dir) / name, content.encode("utf-8")))
    return {"review_status": views["issues.json"]["review_status"],
            **publish_files(outputs, overwrite=overwrite)}


def workflow_capabilities():
    return {"schema_version": 1, "record_types": list(RECORD_TYPES), "gap_types": list(GAP_TYPES),
            "record_required": ["id", "type", "description"],
            "workspace_schema_version": 2,
            "workspace_operations": ["view", "append", "bind"],
            "lookup_required": ["gap_type", "local_checked", "source_refs",
                                "adopted_refs 或 not_adopted_reason"],
            "scope": ["included", "excluded", "pending"],
            "exports": ["issues.json", "lookups.jsonl", "web_findings.json", "rotation.md", "task-evidence.json"],
            "task_evidence": {"authority": "advisory_only", "acceptance_effect": "none",
                "record_types": ["task_source", "requirement", "source_note", "observation"],
                "request_fields": ["operation", "records", "source_files"],
                "native_read": {"resource": "task_evidence"}, "cli": "workflow workspace --audit AUDIT [--plan PLAN] [--request REQUEST]"},
            "policy": "一次记录生成交付视图；不保存第二条轴。变更输入后旧采用记录显示待复核。"}
