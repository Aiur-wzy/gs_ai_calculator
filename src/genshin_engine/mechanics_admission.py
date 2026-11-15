"""mechanics admission in the single Mechanics execution pipeline."""

from __future__ import annotations

from genshin_engine.mechanics_execution_context import MechanicsExecutionContext
from genshin_engine.mechanics_execution_services import MechanicsExecutionServices


def admit_basic_contract(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> bool:
    run.output = {"status": "invalid", "issues": [], "result": None, "resolved_template": None,
              "rotation_check": services.rotation_result("not_checked"), "mechanics_trace": [], "sampling_trace": [], "capabilities_version": 1}
    validation = services.validate_mechanics_model(run.request.mechanics_model)
    run.output["issues"].extend(validation["issues"])
    rv = services.validate_rotation_model(run.request.rotation_model)
    run.output["issues"].extend(rv["issues"])
    if run.output["issues"]:
        run.output["status"] = "invalid" if any(i["severity"] == "error" for i in run.output["issues"]) else "pending"
        return False
    missing = []
    if run.request.rotation_model.get("period") is None or not run.request.rotation_model.get("initial_frontline"):
        missing.append("rotation_model.period/initial_frontline")
    missing.extend("rotation_model.actions."+a["id"] for a in run.request.rotation_model["actions"] if a.get("start") is None or a.get("occupation_end") is None)
    if missing:
        run.output.update(status="pending", issues=[services.issue("missing_information", "动作投影需要明确周期、初始前台及动作开始/结束。", path, severity="pending") for path in missing])
        return False
    return True


def admit_sources_and_captures(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> bool:
    from . import action_capacity_audit as capacity_audit
    from . import automatic_energy as auto_energy
    if any(capacity_audit.bound(a) for a in run.request.rotation_model["abilities"]):
        for path in capacity_audit.namespace_conflicts({"rotation_model": run.request.rotation_model,
                "mechanics_model": run.request.mechanics_model, "template": run.request.template, "matches": run.request.matches}):
            run.output["issues"].append(services.issue("action_capacity_namespace", "容量消费审计不可用作输入身份或事件引用。", path))
        if run.output["issues"]:
            return False
    automatic_ids = {a["id"] for a in run.request.rotation_model["abilities"] if auto_energy.automatic(a)}
    if automatic_ids:
        for path in auto_energy.namespace_conflicts({"rotation_model": run.request.rotation_model,
                "mechanics_model": run.request.mechanics_model, "template": run.request.template, "matches": run.request.matches}):
            run.output["issues"].append(services.issue("automatic_energy_namespace", "自动扣能审计不可用作输入身份或事件引用。", path))
        actions_by_id = {a["id"]: a for a in run.request.rotation_model["actions"]}
        for event in run.request.rotation_model["events"]:
            if (event["type"] == "energy_spend"
                    and actions_by_id.get(event.get("action_id"), {}).get("ability_id") in automatic_ids):
                run.output["issues"].append(services.issue("duplicate_delivery", "自动扣能动作的旧事件不能通过资源接管移除后绕过双交付检查。", event["id"]))
        if run.output["issues"]:
            return False
    services.validate_template(run.request.template, allow_empty_hits=services.is_swirl_v2(run.request.mechanics_model) and bool(run.request.mechanics_model.get("application_events")))
    run.captures = run.request.mechanics_model.get("snapshot_captures", [])
    actions = {a["id"]: a for a in run.request.rotation_model["actions"]}
    run.capture_ids = {c["id"] for c in run.captures}
    typed_ids = {r["id"] for r in run.request.mechanics_model["rules"]} | {s["id"] for s in run.request.mechanics_model.get("fixed_effect_sources", [])}
    typed_ids.update(b["id"] for b in run.request.template.get("buffs", []))
    typed_ids.update(p["id"] for p in run.request.mechanics_model["hit_patterns"])
    for capture in run.captures:
        action = actions.get(capture["action_id"])
        if action is None:
            run.output["issues"].append(services.issue("reference", "捕获的实际父动作不存在。", capture["id"]))
            continue
        if capture["id"] in typed_ids or "~" in capture["id"] or capture["id"].startswith("@"):
            run.output["issues"].append(services.issue("duplicate_id", "捕获ID占用已有或系统派生身份。", capture["id"]))
        at = services.tick(action["start"]) + services.tick(capture["offset"])
        if at > services.tick(action["occupation_end"]) or at >= services.tick(run.request.rotation_model["period"]):
            run.output["issues"].append(services.issue("snapshot_scope_unsupported", "捕获须位于父动作占用内且早于窗口右端。", capture["id"], severity="pending"))
        if capture["status"] == "pending" or not capture["source"].strip():
            run.output["issues"].append(services.issue("snapshot_calibration_pending", "捕获校准尚未明确。", capture["id"], severity="pending"))
    if run.output["issues"]:
        run.output["status"] = "invalid" if any(i["severity"] == "error" for i in run.output["issues"]) else "pending"
        return False
    return True

