"""One fresh calculation, four offline artifacts, and a bounded publication receipt.

The package is a historical consistency record, not a signed or current-DB context.
Only the file entry point performs a fresh calculation; the builder never reads DB.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from time import perf_counter

from genshin_app.equipment.verification_scope import verification_scope
from genshin_app.presentation import display_names
from genshin_engine.execution_control import ExecutionCancelled

from .calculation_wire import PACKAGE_KIND, PACKAGE_SCHEMA, calculated_package_bytes
from .files import fingerprint, input_view, json_bytes
from .schema import PlanError, validate_plan

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = ("plan", "result", "result_receipt", "report", "equipment_summary", "display_context")
DELIVERY_ROLES = ("native_plan", "calculated", "result_workbook", "formula_workbook")


def normalize_delivery_roles(roles=None):
    """Select independent products; preserve the existing four-product default."""
    if roles is None:
        return DELIVERY_ROLES
    if (not isinstance(roles, (list, tuple)) or not roles
            or any(not isinstance(role, str) or role not in DELIVERY_ROLES for role in roles)
            or len(set(roles)) != len(roles)):
        raise PlanError("请选择不重复的有效交付产物", "invalid_request",
                        {"path": "roles", "allowed": list(DELIVERY_ROLES)})
    return tuple(role for role in DELIVERY_ROLES if role in roles)


def _invalid(path):
    raise PlanError("完整计算包的字段或回执不一致", "invalid_calculation_package", {"path": path})


def _same_json(actual, expected):
    # Python's True == 1 is not an exact JSON receipt identity. Avoid allocating
    # a second encoding of large report/plan subtrees just to compare their types.
    if type(actual) is not type(expected):
        return False
    if actual is expected:
        return True
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_same_json(v, expected[k]) for k, v in actual.items())
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_same_json(a, b) for a, b in zip(actual, expected, strict=True))
    return actual == expected


def _equal(actual, expected, path):
    if not _same_json(actual, expected):
        _invalid(path)


def _limit(content, limit, role):
    if len(content) > limit:
        raise PlanError("完整交付超过产物字节预算", "delivery_size_limit",
                        {"role": role, "bytes": len(content), "limit": limit})


def _limits():
    from genshin_app.calculation_delivery_files import LIMITS
    return LIMITS


def _native(plan):
    from .files import native_input
    return native_input(plan)


@verification_scope
def validate_delivery_calculated(calculated, *, _session=None):
    """Strict generic + existing specialized offline checks; no authenticity claim.

    Return the one canonical encoding and native projection for the private builder.
    Expanded caches are deliberately not subject to the native plan's 8 MiB limit.
    """
    if not isinstance(calculated, dict):
        _invalid("calculated")
    for key in _FIELDS:
        if not isinstance(calculated.get(key), dict):
            _invalid(key)
    if not isinstance(calculated.get("presentation_issues"), list):
        _invalid("presentation_issues")
    try:
        encoded = json_bytes(calculated)
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise PlanError("完整计算包必须是有限的 JSON 数据", "invalid_calculation_package") from exc
    _limit(encoded, _limits()["calculated_bytes"], "calculated")
    plan, report = calculated["plan"], calculated["report"]
    result, receipt = calculated["result"], calculated["result_receipt"]
    from .energy_policy import validate_energy_policy_binding
    validate_energy_policy_binding(result, receipt,
        require_current=plan.get('direct_energy_policy') == 'energy-direct-base-er-v1')
    native = _native(plan)
    from .schema import validate_executed_plan
    validate_executed_plan(plan)
    _equal(report.get("plan"), plan, "report.plan")
    _equal(report.get("state"), "ready", "report.state")
    resolution = plan.get("resolution")
    if not isinstance(resolution, dict) or resolution.get("state") != "ready":
        _invalid("plan.resolution")
    if not isinstance(plan.get("resolved_template"), dict) or not result:
        _invalid("result/resolved_template")
    for key in ("plan_id", "revision"):
        _equal(receipt.get(key), native[key], f"result_receipt.{key}")
    for key, value in (("input_fingerprint", input_view(plan)),
                       ("output_fingerprint", plan["resolved_template"])):
        expected = fingerprint(value)
        for parent, source in (("result_receipt", receipt), ("report", report), ("resolution", resolution)):
            _equal(source.get(key), expected, f"{parent}.{key}")
    from genshin_app.formula_workbook import validate_formula_calculation
    try:
        # The formula reader already checks the result binding, resource and
        # mechanism proofs. Require the formal package fields in that same pass.
        validate_formula_calculation(calculated, _formal_required=True, _session=_session)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError as exc:
        raise PlanError(str(exc), "invalid_calculation_package") from exc
    if "buff_state_timeline" in result:
        from genshin_engine.buff_state_timeline import build_buff_state_timeline

        model = report.get("mechanics_model")
        rotation = result.get("rotation_check")
        if not isinstance(model, dict) or not isinstance(rotation, dict):
            _invalid("buff_state_timeline.execution_context")
        # 0.44 and earlier receipts may contain a shared-pool result while their
        # timeline predates both gate metadata and resource-balance slicing.
        # Later working receipts can contain gate metadata without balance
        # slices.  Select the reconstruction solely from fields already saved;
        # never upgrade a historical receipt during strict comparison.
        saved_timeline = result["buff_state_timeline"]
        saved_states = saved_timeline.get("states", [])
        gate_aware = any(isinstance(state, dict) and "resource_gate" in state
                         for state in saved_states)
        resource_aware = any(isinstance(state, dict) and (
            "resource_balance" in state
            or any(isinstance(row, dict) and "resource_balance" in row
                   for row in state.get("slices", []))) for state in saved_states)
        shared_pool_trace = ((result.get("shared_pools") or {}).get("trace")
                             if resource_aware else None)
        # Reporting can end before the input's state/energy audit. The window
        # proof above independently binds this actual execution rotation; never
        # use a saved timeline's own duration to validate itself.
        execution_duration = (result["damage_report_execution"]["rotation_model"]["period"]
            if "damage_report_window" in result else plan["resolved_template"]["loop_duration"])
        expected_states = build_buff_state_timeline(
            model, result.get("mechanics_trace", []), rotation,
            duration=execution_duration,
            execution_status=result.get("mechanics_status"),
            template=report.get("mechanics_input_template"),
            shared_pool_trace=shared_pool_trace,
            include_resource_gate=gate_aware or resource_aware)
        _equal(result["buff_state_timeline"], expected_states, "result.buff_state_timeline")
    if "special_state_timeline" in result:
        from genshin_engine.special_state_timeline import build_special_state_timeline

        window = result.get("buff_state_timeline")
        special = result.get("special_reactions")
        if not isinstance(window, dict) or not isinstance(special, dict):
            _invalid("special_state_timeline.execution_context")
        expected_special = build_special_state_timeline(
            special.get("trace", []), duration=window["duration"],
            execution_limit=window["execution_limit"],
            execution_status=result.get("mechanics_status"))
        _equal(result["special_state_timeline"], expected_special, "result.special_state_timeline")
    source_fp = receipt.get("source_fingerprint")
    if not isinstance(source_fp, str) or not _SHA.fullmatch(source_fp):
        _invalid("result_receipt.source_fingerprint")
    for parent, source in (("report", report), ("resolution", resolution)):
        _equal(source.get("source_fingerprint"), source_fp, f"{parent}.source_fingerprint")
    _equal(receipt.get("template_version"), 2, "result_receipt.template_version")
    if (not isinstance(receipt.get("engine_version"), str) or not receipt["engine_version"].strip()
            or not isinstance(report.get("composer_version"), str) or not report["composer_version"].strip()):
        _invalid("producer versions")
    _equal(resolution.get("composer_version"), report["composer_version"], "resolution.composer_version")
    stamp = receipt.get("calculated_at")
    try:
        if not isinstance(stamp, str) or datetime.fromisoformat(stamp).tzinfo is None:
            _invalid("result_receipt.calculated_at")
    except ValueError as exc:
        raise PlanError("完整计算包缺少有效计算时间", "invalid_calculation_package") from exc
    for key in ("data_versions", "assumptions"):
        if not isinstance(receipt.get(key), list):
            _invalid(f"result_receipt.{key}")
        _equal(receipt[key], report.get(key), f"report.{key}")
        _equal(receipt[key], resolution.get(key), f"resolution.{key}")
    _equal(calculated["equipment_summary"], report.get("equipment_summary"), "equipment_summary")
    _equal(calculated["presentation_issues"], report.get("presentation_issues"), "presentation_issues")
    context = calculated["display_context"]
    if not isinstance(context.get("catalog"), dict) or not isinstance(context.get("warnings"), list):
        _invalid("display_context")
    display_receipt = context.get("receipt")
    if not isinstance(display_receipt, dict):
        _invalid("display_context.receipt")
    _equal(display_receipt.get("catalog_fingerprint"), fingerprint(context["catalog"]), "catalog_fingerprint")
    _equal(receipt.get("display_source"), display_receipt, "result_receipt.display_source")
    return {"native": native, "calculated_bytes": encoded, "integrity": {
        "calculated_fingerprint": hashlib.sha256(encoded).hexdigest(),
        "report_fingerprint": fingerprint(report),
        "display_context_fingerprint": fingerprint(context),
        "equipment_summary_fingerprint": fingerprint(calculated["equipment_summary"])}}


def _state(calculated, *, energy_intent=None, candidate_session=None):
    result = calculated["result"]
    rotation = result.get("rotation_check") or {}
    interpretation = rotation.get("damage_interpretation") or {}
    materialization = result.get("action_materialization_receipt")
    materialization_summary = None
    if isinstance(materialization, dict):
        materialization_status = materialization.get("status")
        materialization_class = materialization.get("calculation_class")
        # ``unknown`` means the engine has no reviewed character-dispatch
        # manifest.  The staged workflow keeps character semantics explicitly
        # Agent-declared, so absence of such a manifest is informational.  A
        # declared-but-incomplete materialization remains diagnostic.
        summary = materialization.get("summary") or {}
        materialization_summary = {
            "status": materialization_status,
            "calculation_class": materialization_class,
            **{key: summary[key] for key in (
                "complete_actions", "incomplete_actions", "unknown_actions",
            ) if key in summary},
        }
    interpretation_summary = {k: interpretation[k] for k in
        ("kind", "complete_first_cycle", "reported_hit_count") if k in interpretation}
    reaction_verification = interpretation.get("reaction_verification")
    if isinstance(reaction_verification, dict):
        interpretation_summary["reaction_verification"] = {k: reaction_verification[k] for k in
            ("status", "mode", "declared_reaction_hit_count", "omitted_hit_count")
            if k in reaction_verification}
    from genshin_app.calculation_completion import calculation_completion
    completion = calculation_completion(calculated)
    from genshin_app.calculation_qualification import qualification_for_claim
    qualification = qualification_for_claim(calculated, energy_intent=energy_intent, trusted_context=candidate_session)
    state = {"calculation_status": completion['calculation_status'],
            'completion': completion,
            'qualification': qualification,
            "rotation_status": rotation.get("status"), "mechanics_status": result.get("mechanics_status"),
            "damage_interpretation": interpretation_summary,
            "complete_input_window": completion['complete_input_window']}
    if not qualification["can_accept"]:
        state["calculation_status"] = "diagnostic"
    if materialization_summary is not None:
        state["action_materialization"] = materialization_summary
    return state


@verification_scope
def build_calculation_delivery(
    calculated, *, input_file_sha, candidate_session=None, _session=None, roles=None, energy_intent=None,
):
    """Build requested products without paths, DB reads or publication writes."""
    selected = normalize_delivery_roles(roles)
    if not isinstance(input_file_sha, str) or not _SHA.fullmatch(input_file_sha):
        _invalid("input_file_sha")
    from genshin_app.formula_workbook import (
        build_formula_workbook,
        formula_workbook_support,
    )

    from .service import result_bytes

    started = perf_counter()
    checked = validate_delivery_calculated(calculated, _session=_session)
    native_bytes = json_bytes(checked["native"])
    _limit(native_bytes, _limits()["native_plan_bytes"], "native_plan")
    receipt, report = calculated["result_receipt"], calculated["report"]
    producer = {"app_version": receipt["engine_version"], "composer_version": report["composer_version"]}
    # Preserve the already canonical calculated bytes; do not reserialize them just
    # to create its wrapper or calculate the same payload hash again.
    artifacts = {}
    if "native_plan" in selected:
        artifacts["native_plan"] = native_bytes
    if "calculated" in selected:
        package = calculated_package_bytes(calculated, encoded=checked["calculated_bytes"], integrity=checked["integrity"])
        _limit(package, _limits()["calculated_bytes"], "calculated")
        artifacts["calculated"] = package
    if "result_workbook" in selected:
        names = display_names(calculated["plan"], calculated["display_context"]["catalog"])
        result_book = result_bytes(
            calculated["result"], receipt, "xlsx", names,
            calculated["equipment_summary"], candidate_session=candidate_session,
            _session=_session,
        )
        _limit(result_book, _limits()["workbook_bytes"], "result_workbook")
        artifacts["result_workbook"] = result_book
    support = None
    if "formula_workbook" in selected:
        support = formula_workbook_support(calculated, _session=_session)
        formula_book = build_formula_workbook(
            calculated, candidate_session=candidate_session, _session=_session,
        )
        _limit(formula_book, _limits()["workbook_bytes"], "formula_workbook")
        artifacts["formula_workbook"] = formula_book
    if "result_workbook" in selected or "formula_workbook" in selected:
        _equal(fingerprint(calculated), checked["integrity"]["calculated_fingerprint"], "export mutated calculated")
    status = _state(calculated, energy_intent=energy_intent, candidate_session=candidate_session)
    summary = calculated["result"].get("calculation_totals") or calculated["result"].get("summary", {})
    rotation = calculated["result"].get("rotation_check") or {}
    return {"artifacts": artifacts,
            "metadata": {"package_schema": PACKAGE_SCHEMA, "producer": producer,
                **{k: receipt[k] for k in ("plan_id", "revision", "calculated_at", "input_fingerprint",
                                          "output_fingerprint", "source_fingerprint", "result_fingerprint", "data_versions")},
                "input_file_sha": input_file_sha, "native_input_fingerprint": receipt["input_fingerprint"],
                "calculated_fingerprint": checked["integrity"]["calculated_fingerprint"], **status,
                "requested_artifacts": list(selected),
                "formula_support": {"status": support["status"] if support else "not_requested",
                                    "limitation_count": len(support["limitations"]) if support else 0}},
            "summary": {"total_damage": summary.get("total_damage"), "dps": summary.get("dps"),
                        "reported_hits": len(calculated["result"].get("hits", [])),
                        "issue_counts": {"presentation": len(calculated["presentation_issues"]),
                            "mechanics": len(calculated["result"].get("mechanics_issues", [])),
                            "rotation": len(rotation.get("issues", []))}},
            "timings": {"validate_build": perf_counter() - started},
            "diagnostic": status["calculation_status"] == "diagnostic"}


def _read_input(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(_limits()["native_plan_bytes"] + 1)
    _limit(raw, _limits()["native_plan_bytes"], "input_plan")
    try:
        plan = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PlanError("无法读取原生方案 JSON", "invalid_plan") from exc
    validate_plan(plan)
    return plan, hashlib.sha256(raw).hexdigest()


@verification_scope
def deliver_plan_file(
    plan_path, db_path, output_dir, *, overwrite=False, roles=None, energy_intent=None,
    _session=None,
):
    """Fresh file-only service. Keep all render work outside the DB read session."""
    from genshin_app.calculation_delivery_files import (
        prepare_delivery_directory,
        publish_calculation_delivery,
    )
    from genshin_runtime.operation_timing import timed_call

    from .service import run_plan_calc

    selected = normalize_delivery_roles(roles)
    path, database = Path(plan_path).absolute(), Path(db_path).resolve()
    protected = [path, database, *(database.with_name(database.name + suffix)
                                  for suffix in ("-wal", "-shm", "-journal")),
                 database.with_name(f".{database.name}.write.lock")]
    phase = "preflight"
    try:
        state = prepare_delivery_directory(output_dir, overwrite=overwrite, protected_paths=protected)
        phase = "read_input"
        plan, source_sha = timed_call("read_plan", _read_input, path)
        phase = "fresh_calculation"
        started = perf_counter()
        calculated = timed_call("calculate", run_plan_calc, copy.deepcopy(plan), database,
                                capture_display=True, _session=_session)
        fresh_seconds = perf_counter() - started
        phase = "build"
        built = timed_call("serialize_delivery", build_calculation_delivery,
            calculated, input_file_sha=source_sha, roles=selected, energy_intent=energy_intent,
            _session=_session,
        )
        built["timings"]["fresh_calc"] = fresh_seconds
        phase = "publish"
        return timed_call("publish_delivery", publish_calculation_delivery,
                          state, built, input_path=path, input_file_sha=source_sha)
    except Exception as exc:
        # Existing fresh errors can carry megabytes of sources. The dedicated
        # command never echoes that tree or claims an output it has not published.
        if phase == "publish":
            raise
        cause = getattr(exc, "reason", None)
        details = {"phase": phase, "index_committed": False,
                   "cause_reason": cause[:128] if isinstance(cause, str) else type(exc).__name__}
        source_details = getattr(exc, "details", None)
        if isinstance(source_details, dict):
            for key in ("role", "limit", "bytes", "attempted"):
                value = source_details.get(key)
                if isinstance(value, (int, float)) or isinstance(value, str) and len(value) < 128:
                    details[key] = value
            if isinstance(source_details.get("missing_inputs"), list):
                details["missing_input_count"] = len(source_details["missing_inputs"])
        raise PlanError("完整交付未发布；请按阶段检查输入或来源", "delivery_failed", details) from exc


def delivery_capabilities():
    return {"command": "equipment deliver", "package_schema": PACKAGE_SCHEMA,
            "kind": PACKAGE_KIND, "roles": ["native_plan", "calculated", "result_workbook", "formula_workbook"],
            "limits": copy.deepcopy(_limits()), "fresh_calculations": 1,
            "publication": "immutable_generation_then_index_cas", "default_output": "bounded_summary",
            "native_cache": "pending_resolution_and_null_resolved_template",
            "product_selection": "可显式选择一个或多个roles；默认全部。仅App方案可选native_plan/calculated，不生成Excel。",
            "qualification": "有明确动作/机制或energy_intent声明时复用共同执行资格；普通公式保留公式模式。",
            "offline_proof": "internal_consistency_not_signature_or_current_database",
            "unsupported": ["historical_package_input", "gui_delivery", "automatic_open", "inspection"]}
