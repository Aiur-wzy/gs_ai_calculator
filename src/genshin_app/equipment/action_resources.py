"""Fresh base-resource compilation into an isolated, explicitly supplied action model.

No event is generated and no variant semantics are inferred from names or numbers.
This module is deliberately independent of composer registration.
"""
from __future__ import annotations

import copy
import sqlite3

from genshin_app.task_capability_producer import emit_task_missing
from genshin_db.equipment import EquipmentError
from genshin_db.runtime_catalog import fingerprint

from .rotation_authoring import (
    ENERGY_DELIVERY,
    compiler_field_error_details,
    has_compiler_field,
)

COMPILER = "database-action-resource-v1"
PURPOSE = "ordinary_fixed_burst_base_resources"
CONFIRMATIONS = ("expected_snapshot_id", "expected_resource_fingerprint", "expected_scope_fingerprint")


def _reject(message, reason="invalid_plan", path=None, details=None):
    from .schema import PlanError
    merged = {**({"path": path} if path else {}), **(details or {})}
    raise PlanError(message, reason, merged or None)


def validate_action_resource_bindings(plan):
    """Validate the new envelope without reading DB or trusting serialized receipts."""
    for index, ability in enumerate((plan.get("rotation_model") or {}).get("abilities", [])):
        if has_compiler_field(ability, ENERGY_DELIVERY):
            _reject("原生方案不能携带私有energy.delivery；适用时请使用action_resource_sources[].energy_delivery，范围外使用公开手工能量事件。",
                    "private_execution_field", ability.get("id"),
                    compiler_field_error_details(ENERGY_DELIVERY, index, ability.get("id")))
    if "action_resource_sources" not in plan:
        return
    if plan.get("plan_version") != 3:
        _reject("动作资源来源需要原生方案版本3")
    bindings = plan["action_resource_sources"]
    if not isinstance(bindings, list) or len(bindings) > 64:
        _reject("动作资源来源必须为最多64项的数组")
    required = {"ability_id", "version_line", "selection", "purpose", "variant", "status", "source", *CONFIRMATIONS}
    seen = set()
    from genshin_db.runtime_action_resource import validate_selection
    for binding in bindings:
        if not isinstance(binding, dict) or not required <= binding.keys() or binding.keys() - required - {"energy_delivery"}:
            _reject("动作资源来源缺少字段或包含未知字段")
        if binding.get("energy_delivery", "existing_events") not in ("existing_events", "action_start"):
            _reject("扣能交付模式只支持existing_events或action_start")
        for key in ("ability_id", "variant", "source"):
            if not isinstance(binding[key], str) or not binding[key].strip():
                _reject("动作资源身份、变体和用途依据不能为空白")
        if binding["ability_id"] in seen:
            _reject("同一能力不能绑定多项动作资源来源", "duplicate_delivery")
        seen.add(binding["ability_id"])
        if binding["version_line"] not in ("release", "beta") or binding["purpose"] not in (PURPOSE, "unresolved_resource_mode"):
            _reject("动作资源资料线或用途模式无效")
        if binding["status"] not in ("confirmed", "assumed", "pending"):
            _reject("动作资源用途状态无效")
        for key in CONFIRMATIONS:
            if binding[key] is not None and (not isinstance(binding[key], str) or not binding[key].strip()):
                _reject("动作资源确认须为null或非空字符串")
        selection = binding["selection"]
        if (not isinstance(selection, dict) or not {"character_id", "constellation"} <= selection.keys()
                or selection.keys() - {"character_id", "constellation", "slot", "skill_id", "skill_depot_id"}
                or type(selection["constellation"]) is not int or not 0 <= selection["constellation"] <= 6):
            _reject("动作资源必须明确角色、0..6命座与精确技能选择")
        try:
            validate_selection(**{k: v for k, v in selection.items() if k != "constellation"})
        except ValueError as exc:
            _reject(str(exc))
        if any(value is None for value in selection.values()):
            _reject("动作资源可选选择字段应省略，不接受显式null")


def _identity_evidence(plan, abilities):
    """Only typed, explicit associations enter identity checks or scope receipts."""
    identities, depots, evidence = {}, {}, {}

    def add(actor, char, line, constellation, origin, depot=None):
        current = identities.setdefault(actor, {"character_id": char, "version_line": line, "constellation": constellation})
        if current["character_id"] != char or current["version_line"] != line or (
                current["constellation"] is not None and constellation is not None and current["constellation"] != constellation):
            incoming = {"character_id": char, "version_line": line, "constellation": constellation}
            differences = [{"field": key, "actual": incoming[key], "expected": current[key]}
                           for key in incoming if incoming[key] is not None and current[key] is not None
                           and incoming[key] != current[key]]
            _reject("动作资源声明与当前配装身份不一致；请修正声明或使用公开手工资源路径，不要修改用户命座。",
                    "character_identity", origin, {"actor": actor, "differences": differences,
                    "previous_sources": copy.deepcopy(evidence.get(actor, [])), "user_action": None})
        if constellation is not None:
            current["constellation"] = constellation
        if depot is not None and depots.setdefault(actor, depot) != depot:
            _reject("同一方案角色明确选择了不同技能组", "character_identity", origin)
        evidence.setdefault(actor, []).append({"origin": origin, "character_id": char, "version_line": line,
                                               "constellation": constellation, "skill_depot_id": depot})

    for actor, gear in plan.get("equipment_inputs", {}).items():
        ref = gear.get("character_ref")
        if ref:
            add(actor, ref["db_character_id"], ref["version_line"],
                (gear.get("progression") or {}).get("constellation"), "equipment_inputs." + actor)
    hits = {h["id"]: h for h in plan["manual_template"]["hits"]}
    for binding in plan.get("hit_parameter_sources", []):
        hit = hits.get(binding["hit_id"])
        if hit:
            s = binding["selection"]
            add(hit["character"], s["character_id"], binding["version_line"], s["constellation"],
                "hit_parameter_sources." + binding["hit_id"], s.get("skill_depot_id"))
    for binding in plan.get("effect_bindings", []):
        owner = binding["owner"]
        if owner["kind"] == "character_skill":
            add(binding["provider_character_id"], owner["character_id"], owner["version_line"], None,
                "effect_bindings." + binding["binding_id"])
    for binding in plan["action_resource_sources"]:
        ability = abilities.get(binding["ability_id"])
        if ability is None:
            _reject("动作资源只能关联已有能力", "reference", binding["ability_id"])
        s = binding["selection"]
        add(ability["character"], s["character_id"], binding["version_line"], s["constellation"],
            "action_resource_sources." + binding["ability_id"], s.get("skill_depot_id"))
    return evidence, depots


def compile_action_resources(plan, db_path, missing, *, native_plan=None):
    """Return (detached rotation, fresh records, source receipts).

    The composer owns the encompassing read session and refuses any pending plan.
    An absent binding leaves old manual plans and their execution path untouched.
    """
    # The composer can layer independently owned compiler outputs in one
    # detached working model.  Validate private input fields and build the
    # acknowledgement scope from the original native plan, while applying
    # this compiler to the layered working model.  Direct callers keep the
    # strict native-input behaviour by default.
    native_plan = plan if native_plan is None else native_plan
    validate_action_resource_bindings(native_plan)
    bindings = plan.get("action_resource_sources", [])
    if not bindings:
        return None, [], []
    from genshin_engine import automatic_energy as auto_energy
    if any(b.get("energy_delivery") == "action_start" for b in bindings):
        roots = {k: plan[k] for k in ("rotation_model", "mechanics_model", "manual_template", "rotation_tuning") if k in plan}
        conflicts = auto_energy.namespace_conflicts(roots)
        if conflicts:
            _reject("自动扣能保留身份不能作为用户事件或引用", "automatic_energy_namespace", conflicts[0])
    rotation = copy.deepcopy(plan.get("rotation_model"))
    records, sources, cache = [], [], {}
    if rotation is None:
        for binding in bindings:
            emit_task_missing(
                missing, "missing_input", "rotation_model",
                "动作资源来源需要已有动作模型", "review_action_resource",
                consumer="action_resource:" + binding["ability_id"],
                category="core_data_missing",
                repair_hint="先建立包含该能力的动作模型，再重新编译动作资源",
            )
        return None, [{"ability_id": b["ability_id"], "status": "pending", "compiler": COMPILER,
                       "issues": [{"code": "missing_input", "message": "动作模型缺失"}]} for b in bindings], []
    abilities = {a["id"]: a for a in rotation["abilities"]}
    identities, depots = _identity_evidence(plan, abilities)
    actors = {c["id"]: c for c in rotation["characters"]}
    template_actors = {c["id"] for c in plan["manual_template"]["characters"]}
    native_abilities = {a["id"]: a for a in native_plan["rotation_model"]["abilities"]}

    def issue(record, code, message, details=None):
        record["issues"].append({"code": code, "message": message, **(details or {})})
        emit_task_missing(
            missing, code,
            "action_resource_sources." + record["ability_id"], message,
            "review_action_resource",
            consumer="action_resource:" + record["ability_id"],
            category=("request_error" if code == "source_changed" else
                      "knowledge_missing" if code == "resource_interpretation_pending" else "core_data_missing"),
            repair_hint=("重新compose，按ability_id读取action_resource_compilations[].required_confirmation；"
                         "这是本次输入绑定，不是用户审批，也不是技能资料指纹。" if code == "source_changed" else
                         "核对当前能力、动作、资源来源与用途后重新编译；非C0常规Q可用公开手工能耗与扣能事件。"),
            diagnostics=details,
        )

    from genshin_db.runtime_action_resource import get_runtime_action_resource_profile
    for binding in bindings:
        aid, selection = binding["ability_id"], binding["selection"]
        ability = abilities[aid]
        actor = ability["character"]
        if actor not in actors or actor not in template_actors:
            _reject("动作资源角色不在动作模型或伤害模板中", "character_identity", aid)
        record = {"ability_id": aid, "actor": actor, "status": "pending", "compiler": COMPILER,
                  "selection": copy.deepcopy(selection), "version_line": binding["version_line"],
                  "purpose": binding["purpose"], "variant": binding["variant"],
                  "interpretation_status": binding["status"], "interpretation_basis": binding["source"],
                  "gameplay_interpretation": "declared_not_verified", "identity_coverage": "explicit_sources_only",
                  "actor_identity": "matches_explicit_sources", "action_spend_links": [], "target_paths": [],
                  "known_aliases": [], "explicit_modifiers": [], "compiled_values": None, "issues": []}
        records.append(record)
        automatic_delivery = binding.get("energy_delivery") == "action_start"
        if automatic_delivery:
            record["energy_delivery"] = {"mode": "action_start", "contract": auto_energy.CONTRACT,
                                         "phase": "action_admission", "links": []}
        cooldown, energy = ability.get("cooldown"), ability.get("energy")
        if isinstance(cooldown, dict) and ({"duration", "charges"} & cooldown.keys()) or (
                isinstance(energy, dict) and {"required", "amount"} & energy.keys()):
            _reject("动作资源与手填数字不能同时交付；原生目标数字须省略", "duplicate_delivery", aid)
        if not isinstance(cooldown, dict) or not isinstance(energy, dict):
            issue(record, "missing_input", "基础资源绑定需保留cooldown与energy对象，不能把null解释为固定规则")
        if not isinstance(cooldown, dict) or any(not isinstance(cooldown.get(k), str) or not cooldown[k].strip() for k in ("group", "start", "recovery")):
            issue(record, "missing_input", "必须明确冷却组、起点和恢复模式")
        if not ability.get("variant"):
            issue(record, "missing_input", "能力变体须明确声明")
        elif ability["variant"] != binding["variant"]:
            _reject("动作资源声明变体与已有能力不符", "resource_interpretation", aid)
        selected_actions = [a for a in rotation["actions"] if a["ability_id"] == aid]
        if not selected_actions:
            issue(record, "missing_input", "被绑定能力尚没有实际动作")
        record["target_paths"] = [f"rotation_model.abilities.{aid}.{field}" for field in
                                  ("cooldown.duration", "cooldown.charges", "energy.required", "energy.amount")]
        if automatic_delivery:
            record["target_paths"].append(f"rotation_model.abilities.{aid}.energy.delivery")
        for action in selected_actions:
            spends = [e for e in rotation["events"] if e["type"] == "energy_spend" and e.get("action_id") == action["id"]]
            if automatic_delivery:
                if spends:
                    _reject("自动扣能动作不能保留任何手工energy_spend，资源接管也不能消除该冲突", "duplicate_delivery", action["id"])
                record["energy_delivery"]["links"].append({"action_id": action["id"], "ability_id": aid,
                                                         "receipt_id": auto_energy.receipt_id(action["id"])})
                continue
            if not spends:
                issue(record, "missing_input", f"动作{action['id']}缺少显式扣能事件；不会自动生成事件")
                continue
            if len(spends) != 1:
                _reject("绑定动作必须恰好一条扣能事件", "duplicate_delivery", action["id"])
            spend = spends[0]
            if spend["character"] != actor:
                _reject("动作资源扣能事件角色与能力不符", "character_identity", spend["id"])
            if {"required", "amount"} & spend.keys():
                _reject("绑定动作扣能事件的两个数字也须省略，不能混合手填", "duplicate_delivery", spend["id"])
            if any(spend["id"] in owner["replaces_event_ids"] for owner in (plan.get("mechanics_model") or {}).get("resource_ownership", [])):
                _reject("机制资源接管不能移除基础资源绑定的扣能事件", "duplicate_delivery", spend["id"])
            record["action_spend_links"].append({"action_id": action["id"], "spend_id": spend["id"]})
            record["target_paths"].extend(f"rotation_model.events.{spend['id']}.{field}" for field in ("required", "amount"))
        if automatic_delivery:
            record["energy_delivery"]["links"].sort(key=lambda item: item["action_id"])
        group = (cooldown or {}).get("group")
        record["explicit_modifiers"] = [{"id": e["id"], "type": e["type"]} for e in rotation["events"]
            if (e["type"] == "cooldown_change" and e.get("character") == actor and e.get("group") == group)
            or (e["type"] == "energy_direct" and actor in e.get("amounts", {}))
            or (e["type"] == "energy_spawn" and e.get("action_id") in {a["id"] for a in selected_actions})]
        query = {k: v for k, v in selection.items() if k != "constellation"}
        key = fingerprint({"selection": query, "version_line": binding["version_line"]})
        try:
            if key not in cache:
                cache[key] = get_runtime_action_resource_profile(db_path, **query, version_line=binding["version_line"])
            profile = cache[key]
        except (EquipmentError, ValueError, OSError, sqlite3.Error) as exc:
            issue(record, "resource_source_unavailable", str(exc))
            continue
        for field in ("snapshot", "identity_sources", "projection", "descriptions", "resource_fingerprint"):
            record[field] = copy.deepcopy(profile[field])
        resolved = record["resolved_selection"] = copy.deepcopy(profile["selection"])
        if depots.setdefault(actor, resolved["skill_depot_id"]) != resolved["skill_depot_id"]:
            _reject("动作资源技能组与同角色显式来源矛盾", "character_identity", aid)
        if (ability["action_type_id"] != "action.elemental_burst" or "burst" not in resolved["matching_slots"]
                or selection["constellation"] != 0 or binding["purpose"] != PURPOSE):
            issue(record, "resource_mode_pending", "当前仅编译C0常规固定耗能Q；E、命座或未知资源模式尚未证明适用")
        if binding["status"] == "pending":
            issue(record, "resource_interpretation_pending", "来源确认不能代替基础资源用途声明")
        projection = profile["projection"]
        if profile["status"] != "ready":
            issue(record, "unsupported_resource_source", "基础资源或中文原文未完整就绪，已知数字仅供预览")
        values = {name: p["value"] for name, p in projection.items()}
        if values["base_charge_count"] != 1 or values["base_energy_cost"] is None or values["base_energy_cost"] <= 0:
            issue(record, "resource_mode_pending", "当前仅支持单次充能和正值固定耗能；不会补缺失次数或能耗")
        if actors[actor].get("element") is None:
            issue(record, "missing_input", "角色能量元素需要显式声明")
        elif values["cost_element"] is not None and actors[actor]["element"] != values["cost_element"]:
            _reject("基础能耗元素与动作角色元素不符", "character_identity", aid)

    # Resolve all explicit aliases before writing any values or confirming scope.
    by_id = {r["ability_id"]: r for r in records}
    for record in records:
        if "resolved_selection" not in record:
            continue
        aid, actor = record["ability_id"], record["actor"]
        binding = next(b for b in bindings if b["ability_id"] == aid)
        resolved, ability = record["resolved_selection"], abilities[aid]
        aliases = {r["ability_id"]: "action_resource_sources" for r in records if r is not record
                   and r["actor"] == actor and r.get("resolved_selection", {}).get("skill_id") == resolved["skill_id"]}
        for effect in plan.get("effect_bindings", []):
            if effect["provider_character_id"] != actor:
                continue
            for ref in effect.get("upstream_recipe", {}).get("ability_refs", []):
                if ref["ability_id"] == aid and ref["skill_id"] != resolved["skill_id"]:
                    _reject("已有精确能力引用与动作资源技能身份不符", "character_identity", aid)
                if ref["skill_id"] == resolved["skill_id"] and ref["ability_id"] != aid:
                    aliases[ref["ability_id"]] = "effect_bindings." + effect["binding_id"]
        for alias_id, origin in sorted(aliases.items()):
            alias = abilities.get(alias_id)
            if alias is None or alias["character"] != actor or alias["action_type_id"] != ability["action_type_id"]:
                _reject("显式同源能力别名不存在或角色/动作类型不符", "character_identity", alias_id)
            left, right = ability.get("cooldown") or {}, alias.get("cooldown") or {}
            if any(left.get(k) != right.get(k) for k in ("group", "start", "recovery")):
                _reject("同源动作别名不能拆分冷却组、起点或恢复模式", "resource_alias_conflict", alias_id)
            if alias_id not in by_id:
                expected = {"duration": record["projection"]["base_cooldown_seconds"]["value"],
                            "charges": record["projection"]["base_charge_count"]["value"]}
                cost = record["projection"]["base_energy_cost"]["value"]
                if any(right.get(k) != v for k, v in expected.items()) or any((alias.get("energy") or {}).get(k) != cost for k in ("required", "amount")):
                    _reject("显式同源手工别名的冷却或耗能与基础资源不符", "resource_alias_conflict", alias_id)
            record["known_aliases"].append({"ability_id": alias_id, "origin": origin})
        semantic_binding = {k: v for k, v in binding.items() if k not in CONFIRMATIONS}
        if semantic_binding.get("energy_delivery") == "existing_events":
            semantic_binding.pop("energy_delivery")
        scope = {"compiler": COMPILER, "binding": semantic_binding,
                 "ability": native_abilities[aid], "identity_evidence": identities.get(actor, []),
                 "resolved_selection": resolved, "action_spend_links": record["action_spend_links"],
                 "known_aliases": [{**a, "ability": native_abilities[a["ability_id"]]} for a in record["known_aliases"]]}
        if "energy_delivery" in record:
            scope["energy_delivery"] = record["energy_delivery"]
        record["scope_fingerprint"] = fingerprint(scope)
        confirmation = {"expected_snapshot_id": record["snapshot"]["snapshot_id"],
                        "expected_resource_fingerprint": record["resource_fingerprint"],
                        "expected_scope_fingerprint": record["scope_fingerprint"]}
        record["required_confirmation"] = confirmation
        if any(binding[k] != v for k, v in confirmation.items()):
            index = next(i for i, item in enumerate(bindings) if item["ability_id"] == aid)
            record_index = records.index(record)
            differences = [{"field": key, "actual": binding[key], "expected": value,
                            "input_ref": f"/action_resource_sources/{index}/{key}",
                            "expected_ref": f"/action_resource_compilations/{record_index}/required_confirmation/{key}"}
                           for key, value in confirmation.items() if binding[key] != value]
            issue(record, "source_changed", "当前资源绑定值不匹配；重新组合后按本能力读取required_confirmation，核对并更新对应字段。",
                  {"ability_id": aid, "differences": differences,
                   "matched_fields": [key for key, value in confirmation.items() if binding[key] == value],
                   "user_action": None, "engine_execution": "not_run"})
        if record["issues"]:
            continue
        p = record["projection"]
        values = {"cooldown": {"duration": p["base_cooldown_seconds"]["value"], "charges": p["base_charge_count"]["value"]},
                  "energy": {"required": p["base_energy_cost"]["value"], "amount": p["base_energy_cost"]["value"]}}
        if "energy_delivery" in record:
            values["energy"]["delivery"] = "action_start"
        ability["cooldown"].update(values["cooldown"])
        ability["energy"].update(values["energy"])
        spend_ids = {link["spend_id"] for link in record["action_spend_links"]}
        for event in rotation["events"]:
            if event["id"] in spend_ids:
                event.update(values["energy"])
        record.update(status="ready", compiled_values=copy.deepcopy(values))
        record["compilation_fingerprint"] = fingerprint(record)
        sources.append({"kind": "action_resource", "ability_id": aid,
                        "snapshot_id": record["snapshot"]["snapshot_id"], "resource_fingerprint": record["resource_fingerprint"],
                        "scope_fingerprint": record["scope_fingerprint"], "compilation_fingerprint": record["compilation_fingerprint"]})
    return rotation, records, sources
