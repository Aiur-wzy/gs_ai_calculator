"""Explicit file workspace over the same optional records used by native tools."""
from __future__ import annotations

import copy
from pathlib import Path

from genshin_app.equipment.files import (
    file_sha,
    fingerprint,
    input_file_errors,
    json_bytes,
    publish_files,
    read_input_json,
    read_input_text,
    read_plan,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.task_evidence import prepare_record, review_records
from genshin_app.workflow import (
    audit_binding,
    new_workspace_audit,
    saved_audit_binding,
    upgrade_workspace_audit,
    validate_audit,
)


def workspace(plan_path, audit_path, request=None, *, task_file=None, calculated_path=None, data_dir=None,
              protected_paths=()):
    request = request if request is not None else {"operation": "append" if task_file else "view"}
    if not isinstance(request, dict) or set(request) - {"operation", "records", "source_files", "publication_usage"}:
        raise PlanError("工作资料请求只接受 operation、records、source_files 和 publication_usage", "invalid_audit")
    operation = request.get("operation", "view")
    if operation not in ("view", "append", "bind"):
        raise PlanError("operation 请选择 view、append 或 bind；用 supersedes 保留替代历史", "invalid_audit")
    plan = read_plan(plan_path)["plan"] if plan_path else None
    audit_path = Path(audit_path)
    if plan_path and audit_path.resolve() == Path(plan_path).resolve():
        raise PlanError("工作记录不能覆盖原生方案", "invalid_audit")
    with input_file_errors(field="/audit"):
        observed = file_sha(audit_path)
    audit = read_input_json(audit_path, field="/audit") if observed else new_workspace_audit(plan)
    validate_audit(audit)
    binding = saved_audit_binding(audit)
    if "publication_usage" in request:
        from genshin_app.publication_usage import prepare_publication_usage
        if set(request) - {"operation", "publication_usage"} or operation != "view" or task_file or calculated_path:
            raise PlanError("资料采用提示是独立的只读工作资料查询", "invalid_publication_usage")
        if observed is None:
            raise PlanError("请先保存原题或已有工作记录，建立稳定任务 ID", "publication_usage_task_missing")
        if data_dir is None:
            raise PlanError("当前没有可读取的数据库，无法核对所列实体", "database_missing")
        return {"publication_usage": prepare_publication_usage(Path(data_dir) / "genshin.db",
                request["publication_usage"], task_id=audit.get("task_id", binding.get("plan_id")),
                records=audit["records"])}
    if plan and binding.get("plan_id") not in (None, plan["plan_id"]):
        raise PlanError("工作记录属于另一方案", "audit_plan_mismatch")
    if operation == "bind" and plan is None:
        raise PlanError("明确关联需要 --plan", "audit_plan_required")
    if plan is None or (binding["state"] == "unbound" and operation != "bind"):
        if calculated_path:
            raise PlanError("工作资料尚未提供已绑定方案，不能核对计算结果", "audit_plan_unbound")
        plan = None
    scope = None
    if data_dir is not None:
        from genshin_app.task_evidence_scope import database_snapshot, evidence_scope
        database = Path(data_dir) / "genshin.db"
        scope = evidence_scope(database_snapshot(database), database_path=database)
    records = request.get("records", [])
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise PlanError("records 需要记录对象列表", "invalid_audit")
    records = copy.deepcopy(records)
    source_files = request.get("source_files", {})
    if not isinstance(source_files, dict) or len(source_files) > 128 or any(
            not isinstance(name, str) or not name or not isinstance(path, str) or not path
            for name, path in source_files.items()):
        raise PlanError("source_files 需要资料名到明确路径的映射", "invalid_audit")
    resources, source_read_issues = {}, []
    for name, path in source_files.items():
        try:
            resources[name] = read_input_json(path, field="/source_files")
        except PlanError as error:
            source_read_issues.append({"reference": name, "reason": error.reason})
    task_record = None
    if task_file:
        from genshin_app.task_evidence import source_record
        task_record = source_record(read_input_text(task_file, field="/task_file"),
                                    str(Path(task_file).resolve()), authority="caller_provided")
    if (records or task_record) and operation != "append":
        raise PlanError("写入记录或原题须明确 operation=append", "invalid_audit")
    receipt = {}
    if operation != "view":
        audit = upgrade_workspace_audit(audit)
    if operation == "bind":
        audit["binding"] = binding = audit_binding(plan)
        validate_audit(audit)
    elif operation == "append":
        if not records and not task_record:
            raise PlanError("append 需要 records 或 --task-file", "invalid_audit")
        if task_record and not any(row["id"] == task_record["id"] for row in audit["records"]):
            audit["records"].append(prepare_record(task_record, plan, task_source_origin="caller_provided"))
        audit["records"].extend(prepare_record(r, plan, resources, scope=scope) for r in records)
        validate_audit(audit)
    calculated = None
    if calculated_path:
        from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES
        from genshin_app.calculation_review import read_bounded_json
        calculated = read_bounded_json(calculated_path, CALCULATED_ARTIFACT_BYTES)
        if isinstance(calculated, dict):
            calculated = calculated.get("calculated", calculated)
    review = review_records(audit["records"], plan, task_id=audit.get("task_id", binding.get("plan_id")),
                            calculated=calculated, scope=scope, resources=resources)
    if operation != "view":
        receipt = publish_files([(audit_path, json_bytes(audit))],
                                expected_shas={audit_path: observed},
                                input_paths=[audit_path, *(path for path in (plan_path, task_file, calculated_path) if path),
                                             *protected_paths, *source_files.values()])
    from genshin_app.agent_chat.skill_context import recommend_focus
    binding_current = plan is not None and binding == audit_binding(plan)
    return {"operation": operation, "record_count": len(audit["records"]), **receipt,
            "workspace_binding": binding, "workspace_schema_version": audit["schema_version"],
            "workspace_binding_current": binding_current,
            "source_read_issues": source_read_issues,
            "task_review": review, "work_card_recommendation": recommend_focus({"task_review": review})}


def export_sources(value, output_dir, *, overwrite=False, protected_paths=()):
    """One entity receipt plus editable notes; user edits never overwritten implicitly."""
    from genshin_app.task_evidence import notebook_filename, source_notebook
    items = value.get("items") if isinstance(value, dict) else None
    entities = items if isinstance(items, list) and all(isinstance(i, dict) and "entity_type" in i for i in items) else [value]
    outputs, index = [], []
    for position, item in enumerate(entities):
        label = notebook_filename(f"{position}-{item.get('entity_type', 'source')}-{item.get('id', 'receipt')}")
        notebook = source_notebook(item, reference=label)
        raw = Path(output_dir) / (label + ".source.json")
        notes = Path(output_dir) / (label + ".notes.json")
        snapshot = value.get("snapshot")
        # A query-wide fingerprint also covers unrelated entities. Keep the
        # actual release metadata and this selected item as the note basis.
        identity = {key: value for key, value in snapshot.items() if key != "fingerprint"} if isinstance(snapshot, dict) else snapshot
        digest = notebook["fingerprint"] if identity is None else fingerprint({"snapshot": identity, "raw": item})
        outputs.append((raw, json_bytes({"snapshot": snapshot, **notebook, "source_version": digest})))
        notes_preserved = notes.exists()
        notes_freshness = "new"
        if not notes_preserved:
            outputs.append((notes, json_bytes({"source_file": raw.name,
                "source_fingerprint": notebook["fingerprint"], "interpretation": notebook["interpretation"],
                "source_version": digest, "source_parts": [],
                "authority": "author_only", "note": "解释由Agent填写；后续导出不会覆盖本文件。"})))
        else:
            try:
                previous = read_input_json(notes, field="/notes")
                prior_digest = previous.get("source_version", previous.get("source_fingerprint")) if isinstance(previous, dict) else None
            except PlanError:
                prior_digest = None
            notes_freshness = "unverified" if not prior_digest else "current" if prior_digest == digest else "needs_review"
        index.append({"source": raw.name, "notes": notes.name, "fingerprint": notebook["fingerprint"],
                      "source_version": digest, "notes_preserved": notes_preserved, "notes_freshness": notes_freshness,
                      "source_block_count": len(notebook["source_blocks"])})
    outputs.append((Path(output_dir) / "sources.index.json", json_bytes({
        "contract": "genshin-source-notebook-index-v1", "items": index, "execution_authority": "none"})))
    return {"sources": index, **publish_files(outputs, overwrite=overwrite, input_paths=protected_paths)}
