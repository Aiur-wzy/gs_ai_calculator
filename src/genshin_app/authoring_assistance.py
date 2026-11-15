"""Read-only recipes that produce ordinary native inputs, not another runtime."""
from __future__ import annotations

import copy
import re

from .equipment.files import fingerprint, input_view
from .equipment.schema import PlanError, _json_guard

CONTRACT = "genshin-native-authoring-assistance-v1"
KINDS = ("catalog", "attack_bundle", "mechanics_fragment", "materialization_entries",
         "special_damage", "binding_refresh", "batch", "action_move")


def authoring_request_schema():
    """Only the small request envelope is new; payloads name existing inputs."""
    return {"type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"enum": list(KINDS)}, "version": {"const": 1},
                "request_id": {"type": "string", "minLength": 1, "maxLength": 96,
                    "description": "ASCII letter/digit first, then letters/digits or _ . : -; stable request identity."},
                "expected_revision": {"type": "integer", "minimum": 0},
                "expected_input_fingerprint": {"type": "string", "minLength": 64, "maxLength": 64,
                    "description": "Current lowercase SHA-256 from the candidate input, exactly 64 hexadecimal characters."},
                "note": {"type": "string"}, "payload": {"type": "object",
                    "description": "Existing native fields per handler_matrix; no execution authority is added."}},
            "required": ["kind", "version", "request_id", "expected_revision"]}


def handler_matrix():
    from .equipment.agent_execution_materialization import (
        agent_execution_materialization_capabilities,
    )

    materialization = agent_execution_materialization_capabilities()
    root = "/schema/properties/mechanics_model/anyOf/0/properties/"
    plan_root = "/schema/properties/"
    def ref(pointer):
        return {"resource": "capabilities", "pointer": pointer}
    return {
        "attack_bundle": {
            "payload_fields": ["prototype_hit", "hit_pattern", "application_profile", "mechanics_model", "rotation_events"],
            "handler": "native hit_patterns + elemental profiles + declared mechanics rules",
            "schema": {"resource": "capabilities", "pointer": root + "hit_patterns/items"},
            "payload_schema_refs": {
                "prototype_hit": ref(plan_root + "manual_template/properties/hits/items"),
                "hit_pattern": ref(root + "hit_patterns/items"),
                "application_profile": ref(root + "elemental_model/properties/profiles/items"),
                "mechanics_model": ref("/schema/properties/mechanics_model/anyOf/0"),
                "rotation_events": ref(plan_root + "rotation_model/anyOf/0/properties/events")},
            "generated": "Stable missing IDs, prototype/action links, application profile reference, unreviewed manual rule bindings; ordinary native patch.",
            "limits": "Offsets must be supplied. Does not invent timing, target state, ICD, resource conditions or role facts.",
            "manual_equivalent": ["manual_template.hits", "mechanics_model.hit_patterns", "mechanics_model.elemental_model.profiles"]},
        "mechanics_fragment": {
            "payload_fields": ["mechanics_model", "rotation_events", "rotation_actions", "rotation_abilities"],
            "handler": "existing author mechanics schema, attribute graph and resource/event handlers",
            "schema": {"resource": "capabilities", "pointer": "/schema/properties/mechanics_model/anyOf/0"},
            "payload_schema_refs": {
                "target_status_events": ref(root + "target_status_events"),
                "target_status_rules": ref(root + "target_status_rules"),
                "hit_events": ref(root + "hit_events"),
                "rules": ref(root + "rules"),
                "rotation_events": ref(plan_root + "rotation_model/anyOf/0/properties/events")},
            "current_optional_inputs": ["target_status_events/target_status_rules", "hit_events[].target_id",
                "rules[].effects[].filter.event_refs", "rotation_events[type=cooldown_change].operation=restore_rebase"],
            "generated": "Append-only unique identified declarations; missing rule bindings remain unreviewed.",
            "limits": "Non-array model conflicts require explicit ordinary editing; no inference from source text.",
            "manual_equivalent": ["mechanics_model", "rotation_model.events", "effect_bindings"]},
        "materialization_entries": {
            "payload_fields": ["sources", "entries"],
            "handler": "agent_execution_materialization current family/version registry",
            "capability": {"resource": "capabilities", "pointer": "/agent_execution_materialization_contract"},
            "families": {name: {key: value.get(key) for key in (
                "handler_id", "supported_handler_versions", "accepted_node_kinds", "runtime_execution")}
                for name, value in materialization["families"].items()},
            "generated": "Current plan/topology envelope around supplied typed entries, without changing evidence status.",
            "limits": "Uses supplied event topology. resource@1 does not become particles; use native resource rules for particle spawn/travel/credit.",
            "manual_equivalent": ["extensions.agent_execution_materialization", "mechanics_model.rules[].resources", "rotation_model.events"]},
        "special_damage": {
            "payload_fields": ["special_reaction_events", "remove_hit_ids", "mechanics_model"],
            "handler": "existing special reaction event/profile binding and execution",
            "schema": {"resource": "capabilities", "pointer": root + "special_reaction_events/items"},
            "generated": "Typed special events; optional exact removal of ordinary manual hit plus its direct hit_event.",
            "limits": "No runtime layers or guessed factors. Derived/prototype replacement requires its owner input or existing calculation override; no silent ownership release.",
            "manual_equivalent": ["mechanics_model.special_reaction_events", "calculation_event_overrides"]},
        "binding_refresh": {
            "payload_fields": ["binding_ids", "materialization"],
            "handler": "current composition effect_requirements or explicitly requested current materialization envelope",
            "generated": "Exact current source/envelope fingerprints only. Changed source fingerprint resets coverage to unreviewed; source verification and rule status are never promoted.",
            "limits": "Requires a matching current composition report; not an approval, confirmation or database correction.",
            "manual_equivalent": ["effect_bindings[].source_fingerprint", "effect_bindings[].coverage.source_fingerprint"]},
        "batch": {
            "payload_fields": ["steps"],
            "steps": "1..128 {id,kind,payload}; id唯一；kind为已有配方（不能嵌套batch/catalog）。按顺序在内存副本展开。",
            "generated": "One ordinary edit_request with one revision/input fingerprint check; failure produces no partial input write.",
            "limits": "Not a runtime, approval or global transaction for external scripts. Stale source confirmations remain stale.",
            "example": {"steps": [{"id": "resources", "kind": "mechanics_fragment",
                                   "payload": {"rotation_events": []}}]},
            "manual_equivalent": ["one equipment edit / gs_edit_input containing all generated operations"]},
        "action_move": {
            "payload_fields": ["action_id", "start", "relative_bindings"],
            "generated": "Move one exact action and preserve its occupation/switch offsets. Existing relative hit patterns follow their declared anchor at recalculation.",
            "relative_bindings": "optional [{collection,id,fields:[field,...]}]; explicit numeric time fields shift by the action delta; IDs must be unique within their collection",
            "example": {"action_id": "skill.cast", "start": 2,
                "relative_bindings": [{"collection": "/rotation_model/events", "id": "skill.particles", "fields": ["t"]}]},
            "limits": "Absolute hit/credit/buff times are NOT guessed. Known untouched absolute action references are returned for review. No game timing is invented.",
            "manual_equivalent": ["rotation_model.actions", "explicit numeric timing fields through equipment edit / gs_edit_input"]},
    }


def authoring_intents():
    """Map common author intentions to existing handlers, not character rules."""
    return [
        {"intent": "建立动作并同时安排命中、附着、状态、收益与资源", "kind": "batch",
         "route": "mechanics_fragment先声明rotation_abilities/rotation_actions，再组合attack_bundle、mechanics_fragment或materialization_entries",
         "boundary": "每一步提供明确游戏输入；未知数值保持待补，不凭资料正文自动编译。"},
        {"intent": "有限多段或固定间隔后台攻击", "kind": "attack_bundle",
         "route": "prototype_hit + hit_pattern(action_id, offsets, orders)，可附application_profile及原生资源规则",
         "example_payload": {"prototype_hit": {"id": "example.hit", "t": 0, "character": "actor",
             "skill": "工程周期技能", "hit_name": "工程命中", "damage_type_ids": ["damage.elemental_skill"],
             "damage_system": "classic", "element": "cryo", "multiplier": 1},
             "hit_pattern": {"id": "example.ticks", "prototype_hit_id": "example.hit", "action_id": "actual.cast",
                             "offsets": [.2, 1.2, 2.2], "orders": [10, 11, 12]}},
         "boundary": "样例只是结构，actor/actual.cast须替换为当前输入的真实身份；倍率和时点没有游戏事实权威。"},
        {"intent": "重建、替换、暂停、恢复或销毁持续来源", "kind": "materialization_entries",
         "route": "按当前family/version目录选择periodic_channel的生命周期字段及state/mechanics_rule_bundle条目；仍使用已有原生处理器",
         "boundary": "先查询该family的实际字段和支持动作；不把不支持的操作伪装成周期次数。"},
        {"intent": "声明产球前提、接球和共享触发冷却", "kind": "mechanics_fragment",
         "route": "mechanics_model.rules[].resources 和 trigger/timing.cooldown，或明确rotation_events；动态相依属性仍走原生attribute_model",
         "boundary": "成功触发口径不免除前台、伤害命中、共享冷却等作者提供的条件；这里不推断游戏规则。"},
        {"intent": "修改一个动作及明确关联的时间", "kind": "action_move",
         "route": "action_id/start + relative_bindings；多动作通过batch",
         "boundary": "只移动明确声明关系，绝对时点未绑定项在gaps中提示。"},
        {"intent": "普通命中转特殊直伤", "kind": "special_damage",
         "route": "remove_hit_ids + special_reaction_events；已有派生所有者需要先在原生编辑中明确调整",
         "boundary": "不同时保留被替换的普通伤害；不写运行时领域层数或伪造能力来源。"},
        {"intent": "改完输入后刷新技术绑定", "kind": "binding_refresh",
         "route": "重新compose当前输入，再明确binding_ids；materialization:true只重绑当前输入与拓扑",
         "boundary": "技术指纹更新不等于游戏事实确认；变化的覆盖确认会回到unreviewed。"},
    ]


def authoring_assistance(request, *, plan, composition_report=None):
    from genshin_engine.rotation_port import collect_schema_errors

    from .authoring_fragments import build_fragment
    from .input_editing import (
        execution_issues,
        prepare_input_edit,
        validate_editable_input,
    )

    _json_guard(request)
    issues = []
    collect_schema_errors(request, authoring_request_schema(), "request", issues)
    if issues:
        raise PlanError("作者辅助请求格式不正确；读取同一入口的schema后修正。", "authoring_request_invalid", issues)
    for field, pattern, expected in (
        ("request_id", r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", "1至96位ASCII字母/数字及_.:-，首位为字母或数字"),
        ("expected_input_fingerprint", r"[0-9a-f]{64}", "当前输入回执中的64位小写十六进制SHA-256"),
    ):
        if field in request and re.fullmatch(pattern, request[field]) is None:
            raise PlanError("作者请求身份格式不正确；未创建修改。", "authoring_request_invalid",
                            {"path": f"request.{field}", "actual": request[field], "expected": expected})
    validate_editable_input(plan)
    if request["expected_revision"] != plan["revision"]:
        raise PlanError("当前候选修订已变化，请重新读取后生成修改。", "agent_stale_revision",
                        {"expected_revision": request["expected_revision"], "actual_revision": plan["revision"]})
    basis = fingerprint(input_view(plan))
    if request.get("expected_input_fingerprint", basis) != basis:
        raise PlanError("当前候选内容已变化，未生成可提交修改。", "agent_stale_revision")
    base = {"contract": CONTRACT, "authority": "authoring_only", "plan_modified": False,
            "database_access": False, "calculation_executed": False, "game_facts_verified": False,
            "input_identity": {"plan_id": plan.get("plan_id"), "revision": plan["revision"],
                               "input_fingerprint": basis},
            "handler_matrix": handler_matrix(), "intent_routes": authoring_intents()}
    if request["kind"] == "catalog":
        return {**base, "request_schema": authoring_request_schema(), "edit_request": None}
    if plan.get("kind") != "genshin-plan" or plan.get("plan_version") != 3:
        raise PlanError("本配方生成原生v3输入；白板可继续编辑或通过已有原生导入路径准备。",
                        "authoring_native_plan_required", {"manual_equivalent": "existing whiteboard/native input edit"})
    generated, gaps = build_fragment(plan, request, composition_report=composition_report)
    operations = [{"op": "replace" if key in plan else "add", "path": "/" + key, "value": value}
                  for key, value in generated.items() if value != plan.get(key)]
    edit = {"mode": "patch", "expected_revision": plan["revision"], "expected_input_fingerprint": basis,
            "reason": request.get("note") or "采用明确声明的通用作者输入", "operations": operations}
    preview = prepare_input_edit(plan, edit) if operations else copy.deepcopy(plan)
    validation = execution_issues(preview)
    return {**base, "edit_request": edit if operations else None, "fragment": generated,
            "preview_input": input_view(preview), "execution_validation": {
                "status": "issues" if validation else "structurally_valid", "issues": validation,
                "semantic_calculation": "not_run"},
            "gaps": gaps, "changed_paths": [row["path"] for row in operations],
            "binding_changes": _binding_changes(plan, preview) if request["kind"] in {"binding_refresh", "batch"} else [],
            "counts": _counts(plan, preview),
            "next_action": "通过现有编辑入口采用edit_request，再组合核对来源并计算；辅助本身不写入、不批准。"}


def _binding_changes(before, after):
    old = {row["binding_id"]: row for row in before.get("effect_bindings", [])}
    changes = []
    for row in after.get("effect_bindings", []):
        previous = old.get(row["binding_id"], {})
        for path, a, b in (
            ("source_fingerprint", previous.get("source_fingerprint"), row.get("source_fingerprint")),
            ("coverage", previous.get("coverage"), row.get("coverage")),
        ):
            if a != b:
                changes.append({"binding_id": row["binding_id"], "field": path,
                                "before": copy.deepcopy(a), "after": copy.deepcopy(b)})
    for name in ("plan_binding", "topology_binding", "handler_set_fingerprint"):
        a = before.get("extensions", {}).get("agent_execution_materialization", {}).get(name)
        b = after.get("extensions", {}).get("agent_execution_materialization", {}).get(name)
        if a != b:
            changes.append({"field": "agent_execution_materialization." + name,
                            "before": copy.deepcopy(a), "after": copy.deepcopy(b)})
    return changes


def _counts(before, after):
    def counts(plan):
        model = plan.get("mechanics_model") or {}
        rotation = plan.get("rotation_model") or {}
        return {"actions": len(rotation.get("actions", [])), "prototype_hits": len(plan["manual_template"].get("hits", [])),
                "hit_patterns": len(model.get("hit_patterns", [])), "rules": len(model.get("rules", [])),
                "resource_events": len(rotation.get("events", [])),
                "special_events": len(model.get("special_reaction_events", []))}
    a, b = counts(before), counts(after)
    return {"before": a, "after": b, "delta": {key: b[key] - a[key] for key in a}}
