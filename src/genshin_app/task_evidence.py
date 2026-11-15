"""Optional task/source notes projected against actual input, never admission."""
from __future__ import annotations

import copy
import re

from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.task_evidence_scope import calculation_currency, scope_verified

DISPOSITIONS = ("pending", "adopted", "implemented_elsewhere", "not_applicable",
                "user_excluded", "not_implemented", "approximation")


def _pointer(value, pointer):
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise KeyError(pointer)
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def resolve_reference(reference, plan, resources=None):
    """Resolve only explicit local references. URLs remain caller-provided sources."""
    resources = resources or {}
    if isinstance(reference, str):
        reference = {"reference": reference}
    if not isinstance(reference, dict):
        return {"state": "unresolved"}
    name = reference.get("reference", reference.get("id", ""))
    pointer = reference.get("pointer", "")
    try:
        if reference.get("kind") == "task_text":
            return {"state": "supplied_text", "reference": name,
                    "origin": reference.get("origin", "author_declared"),
                    "note": "原文出处记录，不是用户批准或机制正确性证明。"}
        if name in resources:
            value = _pointer(resources[name], pointer)
        elif isinstance(name, str) and name.startswith("/"):
            if plan is None:
                return {"state": "unbound", "reference": name}
            value = _pointer(input_view(plan), name)
        elif name in ("input", "candidate"):
            if plan is None:
                return {"state": "unbound", "reference": name, "pointer": pointer}
            value = _pointer(input_view(plan), pointer)
        elif isinstance(name, str) and name.startswith(("https://", "http://")):
            return {"state": "external_unverified", "reference": name,
                    "note": "调用方提供的外部出处，未由本地研究账证明已读取。"}
        else:
            return {"state": "unresolved", "reference": name}
        resource = resources.get(name)
        source_version = resource.get("source_version") if isinstance(resource, dict) else None
        digest = fingerprint({"value": value, "source_version": source_version}) if source_version else fingerprint(value)
        expected = reference.get("fingerprint")
        return {"state": "resolved" if expected in (None, digest) else "changed",
                "reference": name, "pointer": pointer, "fingerprint": digest}
    except (KeyError, TypeError, ValueError, IndexError):
        return {"state": "unresolved", "reference": name, "pointer": pointer}


def prepare_record(record, plan, resources=None, *, task_source_origin=None, scope=None):
    value = copy.deepcopy(record)
    refs = value.get("input_refs", [])
    value["basis"] = {"input_fingerprint": fingerprint(input_view(plan)) if plan is not None else None,
                      "dependencies": [resolve_reference(ref, plan, resources) for ref in refs]}
    if scope is not None:
        value["basis"]["scope"] = copy.deepcopy(scope)
    part = value.get("source_part")
    if isinstance(part, dict) and isinstance(part.get("source_ref"), dict):
        source = resolve_reference(part["source_ref"], plan, resources)
        if source.get("state") == "resolved":
            part["source_ref"]["fingerprint"] = source["fingerprint"]
    if value.get("type") == "task_source":
        origin = task_source_origin or "author_declared"
        value["basis"] = {"text_fingerprint": fingerprint(value.get("description", "")), "source_origin": origin}
        for ref in value.get("source_refs", []):
            ref["origin"] = origin
    # This is author content, including text copied from a prompt. No approval authority.
    return value


def source_record(text, reference, *, authority="caller_provided"):
    """Store exact text; neither a CLI file nor a user message grants write approval."""
    return {"id": "task." + fingerprint({"text": text, "reference": reference})[:24],
            "type": "task_source", "description": text,
            "source_refs": [{"kind": "task_text", "reference": reference,
                             "text_fingerprint": fingerprint(text), "origin": authority}]}


def record_task_source(session, text, reference, *, origin="dsh_user_message"):
    """Host calls after accepting a real user message; idempotent auxiliary write."""
    from genshin_app.agent_chat.tools import _record
    record = source_record(text, reference, authority=origin)
    if any(row.get("id") == record["id"] for row in session.findings):
        return {"id": record["id"], "record_count": len(session.findings), "already_recorded": True}
    return {"id": record["id"], **_record(session, {"record": record}, task_source_origin=origin)}


def compact_review(value):
    """Routine feedback carries differences, never repeats the full source notebook."""
    result = {key: copy.deepcopy(value[key]) for key in (
        "contract", "authority", "acceptance_effect", "task_id", "source_version",
        "input_fingerprint", "status", "note", "reason", "binding", "execution_intent",
        "latest_task_text_fingerprint", "report_basis") if key in value}
    rows = value.get("differences", [])
    # Arbitrary source prose and raw references stay in the paged view. This
    # projection is consumed when DSH assembles its bounded default context.
    result.update(differences=[{"id": row["id"], "summary": row["summary"][:512],
                                "actionable": bool(row.get("actionable"))} for row in rows[:12]], difference_count=len(rows),
                  details_ref={"resource": "task_evidence"})
    basis = result.get("report_basis")
    if isinstance(basis, dict):
        for key in ("approximations", "not_completed"):
            basis[key] = [item[:512] for item in basis.get(key, []) if isinstance(item, str) and item]
    return result


def review_records(records, plan, *, resources=None, task_id=None, calculated=None,
                   evaluate_observations=True, scope=None, trusted_result=False):
    resources = dict(resources or {})
    for record in records:
        resources.setdefault(record["id"], record)
        resources.setdefault("finding:" + record["id"], record)
    identity = fingerprint(input_view(plan)) if plan is not None else None
    superseded = {old for r in records for old in r.get("supersedes", [])}
    active = [r for r in records if r["id"] not in superseded]
    # Author supersession can retire an interpretation, never erase original
    # task text from the comparison view. Later user updates remain separate.
    sources = [r for r in records if r["type"] == "task_source"]
    source_ids = {ref for source in sources for ref in (source["id"], "finding:" + source["id"])}
    latest = sources[-1] if sources else None
    execution_intent = None
    if latest:
        for row in reversed(active):
            if row.get("execution_intent") and any(isinstance(ref, dict) and ref.get("reference", ref.get("id")) in (
                    latest["id"], "finding:" + latest["id"]) for ref in row.get("source_refs", [])):
                execution_intent = row["execution_intent"]
                break
    requirements, notes, differences = [], [], []
    for record in active:
        basis = record.get("basis", {})
        dependencies = basis.get("dependencies", [])
        stale = record["type"] != "task_source" and basis.get("input_fingerprint") not in (None, identity)
        if stale and dependencies:
            now = [resolve_reference(ref, plan, resources) for ref in dependencies]
            stale = (any(ref.get("state") != "resolved" or not isinstance(ref.get("fingerprint"), str)
                         or len(ref["fingerprint"]) != 64 for ref in dependencies)
                     or any(v.get("state") != "resolved" for v in now))
        saved_scope = basis.get("scope")
        scoped_record = record["type"] != "task_source" and plan is not None
        scope_known = scope_verified(scope) and scope_verified(saved_scope)
        scope_changed = scoped_record and scope_known and saved_scope != scope
        scope_unverified = scoped_record and (not scope_known or not basis.get("input_fingerprint"))
        stale = stale or scope_changed
        refs = [resolve_reference(ref, plan, resources) for ref in record.get("source_refs", [])]
        source_part = record.get("source_part")
        if source_part and source_part.get("source_ref"):
            refs.append(resolve_reference(source_part["source_ref"], plan, resources))
        adopted = [resolve_reference(ref, plan, resources) for ref in record.get("adopted_refs", [])]
        disposition = record.get("disposition", "legacy_unspecified")
        exclusion_unreferenced = disposition == "user_excluded" and not any(
            state["state"] == "resolved" and state.get("reference") in source_ids for state in refs)
        freshness = ("stale" if stale else "unverified" if scope_unverified
                     else "current" if basis and (plan is not None or record["type"] == "task_source") else "unbound")
        row = {**copy.deepcopy(record), "reference_states": refs, "adoption_states": adopted,
               "disposition": disposition,
               "freshness": freshness,
               "observation_state": "not_checked", "source_coverage": "declared_part" if source_part else "unspecified"}
        if disposition == "user_excluded":
            row["exclusion_reference"] = "missing" if exclusion_unreferenced else "task_text_linked"
        (requirements if record["type"] == "requirement" else notes).append(row)
        missing = (record["type"] == "requirement" and disposition in ("pending", "not_implemented", "legacy_unspecified")
                   or source_part and disposition in ("pending", "not_implemented"))
        unresolved = any(ref["state"] in ("unresolved", "changed") for ref in refs + adopted)
        if missing or stale or unresolved or exclusion_unreferenced:
            why = ("资料库或执行政策已变更，需要复核" if scope_changed else "输入已变更，需要复核" if stale
                   else "引用尚未解析" if unresolved else "未提供用户不计要求的原文引用" if exclusion_unreferenced
                   else "该来源子项尚待处理" if source_part else "原要求尚未落实")
            evidence_refs = list(record.get("source_refs", []))
            if source_part and source_part.get("source_ref"):
                evidence_refs.append(source_part["source_ref"])
            differences.append({"id": record["id"], "summary": f"{why}：{record['description']}",
                "actionable": bool(sources), "evidence_refs": copy.deepcopy(evidence_refs),
                "recovery": {"tool": "gs_read_context", "resource": "task_evidence",
                    "cli": "workflow workspace --audit AUDIT" + (" --plan PLAN" if plan is not None else ""),
                    "instruction": "核对原题和当前输入；修正或说明不适用，不能把辅助提示当计算门。"}})
    observations = []
    declared = [r for r in active if r["type"] == "observation" and r.get("expectation")]
    if sources and not requirements and not declared and plan is not None:
        differences.append({"id": "system.task_review_not_declared", "state": "unknown",
            "summary": "原题已保留，尚无原题落实对照；请核对原文与结果后说明，不代表已确认存在漏项。",
            "actionable": True, "evidence_refs": [r["id"] for r in sources],
            "recovery": {"tool": "gs_read_context", "resource": "task_evidence", "pointer": "/task_sources",
                "instruction": "按需核对原题；不要求填表，不影响已接纳结果或普通问答。"}})
    currency = calculation_currency(calculated, plan, scope, trusted_result=trusted_result)
    if declared:
        requests = []
        for record in declared:
            if currency != "current":
                observations.append({"id": record["id"], "status": "unknown",
                                     "reason": currency})
                continue
            if not evaluate_observations:
                observations.append({"id": record["id"], "status": "unknown", "reason": "not_requested",
                                     "detail_ref": {"resource": "task_evidence", "pointer": "/observations"}})
                continue
            try:
                from genshin_app.declared_observations import validate_observations
                request = {**record["expectation"], "id": record["id"]}
                validate_observations([request])
                requests.append(request)
            except (ValueError, TypeError) as exc:
                observations.append({"id": record["id"], "status": "unknown",
                                     "reason": getattr(exc, "reason", type(exc).__name__)})
        # One bounded inspection per batch, not one full result scan per note.
        for start in range(0, len(requests), 200):
            batch = requests[start:start + 200]
            try:
                from genshin_app.calculation_assistance import (
                    inspect_calculation_assistance,
                )
                observed = inspect_calculation_assistance(calculated,
                    {"view": "coverage", "observations": batch, "limit": 200}, plan=plan)
                observations.extend(observed["declared_observations"]["items"])
            except Exception as exc:
                from genshin_engine.execution_control import ExecutionCancelled
                if isinstance(exc, ExecutionCancelled):
                    raise
                observations.extend({"id": request["id"], "status": "unknown",
                    "reason": getattr(exc, "reason", type(exc).__name__)} for request in batch)
        for row in observations:
            if row["status"] != "observed_match":
                differences.append({"id": row["id"], "summary": "预期尚未由当前计算证实，请核对相应观察。",
                    "actionable": bool(sources), "evidence_refs": [],
                    "recovery": {"tool": "gs_read_context", "resource": "task_evidence", "pointer": "/observations"}})
    by_observation = {row["id"]: row for row in observations}
    for row in notes + requirements:
        if row.get("observation_refs"):
            row["observation_links"] = [copy.deepcopy(by_observation.get(ref, {"id": ref, "status": "unknown",
                "reason": "observation_reference_missing"})) for ref in row["observation_refs"]]
            row["observation_state"] = "see_linked_observations"
    if plan is None and execution_intent == "execute":
        differences.append({"id": "system.task_plan_unbound", "state": "unbound", "actionable": True,
            "summary": "已记录本轮执行任务，目前还没有关联方案。请按任务要求建立或关联方案，并说明仍缺的资料。",
            "evidence_refs": [latest["id"]],
            "recovery": {"cli": "workflow bind --plan PLAN --audit AUDIT",
                         "instruction": "在现有工作区创建方案后显式关联。"}})
    return {"contract": "genshin-task-evidence-v1", "authority": "advisory_only",
            "acceptance_effect": "none", "task_id": task_id if sources else None,
            "source_version": fingerprint(sources), "input_fingerprint": identity,
            "binding": "bound" if plan is not None else "unbound", "execution_intent": execution_intent,
            "latest_task_text_fingerprint": fingerprint(latest["description"].replace("\r\n", "\n").strip(" \t\n")) if latest else None,
            "report_basis": report_basis(plan, calculated, notes, requirements, differences, currency,
                                         trusted_result=trusted_result),
            "task_sources": copy.deepcopy(sources), "requirements": requirements, "notes": notes, "observations": observations,
            "differences": differences, "superseded_ids": sorted(superseded),
            "status": "differences" if differences else "no_declared_differences" if sources else "no_task_source",
            "note": "仅比较已记录要求和引用；作者处置不是游戏事实认证，空差异不代表原题全部完成。"}


def report_basis(plan, calculated, notes, requirements, differences, currency, *, trusted_result=False):
    current = currency == "current"
    receipt = calculated["result_receipt"] if current else {}
    scope = calculated["result"].get("validation_scope") if current else None
    completion = {}
    if current and trusted_result:
        completion = calculated.get("completion") or {}
    elif current:
        from genshin_app.calculation_completion import calculation_completion

        # The input/result identities above do not authenticate a caller's
        # top-level completion or report declarations. Describe execution
        # from the sealed result and leave formal admission to its reader.
        completion = calculation_completion({"result": calculated["result"], "result_receipt": receipt})
        completion.pop("task_admission")
    return {"binding": "bound" if plan is not None else "unbound", "calculation_currency": currency,
            "result_fingerprint": receipt.get("result_fingerprint"),
            "calculation_status": completion.get("calculation_status"),
            "task_admission": completion.get("task_admission"),
            "validation_summary": scope.get("summary") if isinstance(scope, dict) else None,
            "task_difference_count": len(differences),
            "approximations": [r["description"] for r in notes + requirements if r["disposition"] == "approximation"][:8],
            "not_completed": [r["description"] for r in requirements if r["disposition"] in ("pending", "not_implemented")][:8]}


def session_evidence_scope(session, *, database_current=None):
    from genshin_app.task_evidence_scope import evidence_scope

    if database_current is None and hasattr(session, "_db_snapshot"):
        with session._read_db(check=False):
            snapshot = session._db_snapshot()
    else:
        snapshot = session.database_snapshot if database_current else None
    return evidence_scope(snapshot, database_path=getattr(session, "db_path", None))


def session_review(session, *, observe=False, database_current=None):
    try:
        from genshin_app.agent_chat.candidate import CandidateSession

        scope = session_evidence_scope(session, database_current=database_current) if hasattr(session, "database_snapshot") else None
        # The App validates this result slot on both execution and restore.
        # Status reads can reuse that proof without re-serializing long axes.
        return review_records(session.findings, session.candidate,
                              resources=getattr(session, "resources", {}), task_id=session.session_id,
                              calculated=getattr(session, "calculated", None), evaluate_observations=observe, scope=scope,
                              trusted_result=isinstance(session, CandidateSession))
    except Exception as exc:
        from genshin_engine.execution_control import ExecutionCancelled
        if isinstance(exc, ExecutionCancelled):
            raise
        return {"contract": "genshin-task-evidence-v1", "authority": "advisory_only",
                "acceptance_effect": "none", "status": "unavailable", "task_id": None,
                "differences": [], "reason": type(exc).__name__}


def source_notebook(value, *, reference):
    """Lossless query receipt plus editable interpretation scaffold; no semantics guessed."""
    from genshin_app.task_source_sections import source_blocks

    sections, parameters = [], []
    def visit(node, path):
        if isinstance(node, dict):
            for key, child in node.items():
                pointer = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
                if key == "parameter_references" and isinstance(child, dict):
                    parameters.append({"pointer": pointer, "status": child.get("status")})
                visit(child, pointer)
        elif isinstance(node, list):
            for i, child in enumerate(node):
                visit(child, path + "/" + str(i))
        elif isinstance(node, str) and node:
            sections.append({"pointer": path, "text": node})
    visit(value, "")
    return {"contract": "genshin-source-notebook-v1", "reference": reference,
            "fingerprint": fingerprint(value), "raw": copy.deepcopy(value), "text_sections": sections,
            "parameter_sections": parameters,
            "source_blocks": source_blocks(value),
            "source_parts": {"status": "unspecified", "entries": [],
                "note": "可为同一来源分别记录多项效果及其条件；正文段落不代表独立效果已经识别。"},
            "interpretation": {key: None for key in ("trigger", "holder", "beneficiaries", "foreground",
                "damage_family", "duration", "refresh", "units", "sampling", "replacement", "adopted_refs")},
            "interpretation_authority": "author_only", "execution_authority": "none",
            "note": "raw完整保存查询回执；text_sections是字符串索引，不代表所有机制都已解释。"}


def notebook_filename(label):
    return re.sub(r"[^\w.-]+", "_", label, flags=re.UNICODE)[:100] or "source"
