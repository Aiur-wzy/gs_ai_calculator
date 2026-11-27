"""Pure assembly of existing native input fields for the authoring helper."""
from __future__ import annotations

import copy

from .equipment.files import fingerprint, input_view
from .equipment.schema import PlanError


def _error(message, code="authoring_fragment_invalid", **details):
    raise PlanError(message, code, details)


def _append(rows, additions, path, *, identity="id"):
    if not isinstance(rows, list) or not isinstance(additions, list):
        _error("此原生字段须为数组。", path=path)
    if any(not isinstance(row, dict) or not isinstance(row.get(identity), str) for row in rows):
        _error("已有原生数组包含无身份条目；先用普通编辑补齐该数组。", "authoring_existing_input_invalid", path=path)
    seen = {row[identity]: row for row in rows}
    for row in additions:
        if not isinstance(row, dict) or not isinstance(row.get(identity), str) or not row[identity]:
            _error("声明缺少稳定身份，未猜测或替换已有条目。", path=path, identity_field=identity)
        if row[identity] in seen:
            if row != seen[row[identity]]:
                _error("相同ID已有不同输入；使用普通编辑明确修改该条，辅助没有覆盖它。",
                       "authoring_identity_conflict", path=path, id=row[identity],
                       manual_equivalent="equipment edit / gs_edit_input stable-ID selector")
            continue
        rows.append(copy.deepcopy(row))
        seen[row[identity]] = row


def _merge(target, supplied, path="mechanics_model"):
    if not isinstance(supplied, dict):
        _error("原生片段须为对象。", path=path)
    for key, value in supplied.items():
        destination = f"{path}.{key}"
        if key in target and target[key] == value:
            continue
        if key not in target or target[key] is None:
            target[key] = copy.deepcopy(value)
        elif isinstance(value, list):
            identity = {
                "mechanics_model.initial_state.rules": "rule_id",
                "mechanics_model.hit_events": "hit_id",
            }.get(destination, "id")
            if destination.endswith("resource_ownership"):
                if not isinstance(target[key], list):
                    _error("已有资源拥有者字段不是数组，先经普通编辑修正。", path=destination)
                for row in value:
                    if row not in target[key]:
                        target[key].append(copy.deepcopy(row))
            else:
                _append(target[key], value, destination, identity=identity)
        elif isinstance(value, dict) and isinstance(target[key], dict):
            _merge(target[key], value, destination)
        elif target[key] != value:
            _error("片段与已有模型字段不同；请经普通编辑显式替换。", "authoring_fragment_conflict",
                   path=destination, manual_equivalent="equipment edit / gs_edit_input")


def _bindings(plan, new_rules, namespace, *, fixed=False):
    bindings = plan.setdefault("effect_bindings", [])
    key = "fixed_source_id" if fixed else "rule_id"
    field = "fixed_effect_sources" if fixed else "rules"
    kind = "fixed_effect_source" if fixed else "mechanics"
    owned = {part.get("rule", {}).get(key) for row in bindings for part in row.get("parts", [])
             if isinstance(part.get("rule"), dict)}
    existing_ids = {row.get("binding_id") for row in bindings}
    created = {}
    for index, rule in enumerate(new_rules):
        if not isinstance(rule.get("id"), str):
            continue  # Ordinary execution-schema diagnostics retain the incomplete rule.
        if rule.get("id") in owned:
            continue
        owner, source = rule.get("owner"), rule.get("source")
        if not isinstance(owner, str) or not isinstance(source, str) or not source:
            continue
        declared_source = rule.get("source_id")
        if isinstance(declared_source, str) and ":" in declared_source:
            binding_id, part_id = declared_source.split(":", 1)
            if not binding_id or not part_id:
                _error("规则source_id须明确绑定及部件身份。", path="rules.source_id")
        else:
            binding_id, part_id = f"{namespace}.binding.{index + 1}", f"part.{index + 1}"
            actual = next(row for row in plan["mechanics_model"][field] if row["id"] == rule["id"])
            actual["source_id"] = f"{binding_id}:{part_id}"
        if binding_id in existing_ids and binding_id not in created:
            _error("新绑定ID与已有绑定冲突。", "authoring_identity_conflict", id=binding_id)
        binding = {"binding_id": binding_id, "provider_character_id": owner,
            "owner": {"kind": "manual", "note": source},
            "source_refs": [{"kind": "manual", "note": source}], "source_fingerprint": None,
            "coverage": {"state": "unreviewed", "reviewed_by": None, "source_fingerprint": None, "note": None},
            "parts": [{"part_id": part_id, "label": rule.get("name_zh", rule["id"]),
                "state": "resolved", "rule": {"kind": kind, key: rule["id"]},
                "delivery": {"kind": kind}, "conditions": [],
                "dependencies": {"mode": "source_only", "context_fingerprint": None,
                                 "note": "作者声明的完整规则；来源仍须组合后核对"}, "reason": None}]}
        if binding_id in created:
            parent = created[binding_id]
            if parent["provider_character_id"] != owner or parent["source_refs"] != binding["source_refs"]:
                _error("同一新来源绑定包含不一致的提供者或事实说明。", "authoring_identity_conflict", id=binding_id)
            _append(parent["parts"], binding["parts"], "effect_bindings.parts", identity="part_id")
        else:
            bindings.append(binding)
            created[binding_id] = binding
        existing_ids.add(binding_id)


def _native_fragment(plan, payload, namespace):
    from genshin_engine.mechanics_schema import empty_mechanics_model

    supplied = payload.get("mechanics_model", {})
    mechanics = plan.get("mechanics_model")
    if mechanics is None:
        mechanics = plan["mechanics_model"] = empty_mechanics_model()
        rotation = plan.get("rotation_model") or {}
        actions = rotation.get("actions", [])
        extra_actions = payload.get("rotation_actions", [])
        if (isinstance(supplied, dict) and "manual_buff_repeat" not in supplied
                and rotation.get("damage_report") and plan["manual_template"].get("buffs")
                and isinstance(actions, list) and isinstance(extra_actions, list)
                and (actions or extra_actions)
                and all(isinstance(row, dict) and isinstance(row.get("repeat"), dict)
                        and row["repeat"].get("mode") == "once"
                        for row in actions + extra_actions)):
            # A fresh explicit action list already enumerates all game rounds.
            # Keep this choice in the authored model so reopening is repeatable.
            mechanics["manual_buff_repeat"] = "once"
    _merge(mechanics, supplied)
    _bindings(plan, supplied.get("rules", []), namespace)
    _bindings(plan, supplied.get("fixed_effect_sources", []), namespace + ".fixed", fixed=True)
    for field in ("events", "abilities", "actions"):
        additions = payload.get("rotation_" + field, [])
        if not additions:
            continue
        if not isinstance(plan.get("rotation_model"), dict):
            _error("已有原生动作模型缺失；资源事件不能猜测角色或初态。", path="rotation_model")
        _append(plan["rotation_model"].setdefault(field, []), additions, "rotation_model." + field)


def _attack(plan, payload, namespace):
    prototype = copy.deepcopy(payload.get("prototype_hit"))
    pattern = copy.deepcopy(payload.get("hit_pattern"))
    if not isinstance(prototype, dict) or not isinstance(pattern, dict):
        _error("攻击配方需要现有原生prototype_hit与hit_pattern字段。")
    prototype.setdefault("id", namespace + ".prototype")
    pattern.setdefault("id", namespace + ".pattern")
    pattern.setdefault("prototype_hit_id", prototype["id"])
    if pattern["prototype_hit_id"] != prototype["id"]:
        _error("模式与本次提供的命中原型身份不一致。", path="hit_pattern.prototype_hit_id")
    actions = (plan.get("rotation_model") or {}).get("actions", [])
    matched = [row for row in actions if row.get("id") == pattern.get("action_id")]
    if len(matched) != 1:
        _error("攻击模式须引用一个真实动作，辅助不创建虚构施放。", path="hit_pattern.action_id")
    _append(plan["manual_template"].setdefault("hits", []), [prototype], "manual_template.hits")
    _append(plan["mechanics_model"].setdefault("hit_patterns", []), [pattern], "mechanics_model.hit_patterns")
    profile = copy.deepcopy(payload.get("application_profile"))
    if profile is not None:
        if not isinstance(profile, dict):
            _error("application_profile须为现有原生附着器对象。", path="application_profile")
        elemental = plan["mechanics_model"].get("elemental_model")
        if not isinstance(elemental, dict) or not isinstance(elemental.get("target"), dict):
            _error("附着配方需要明确元素规则集和目标初态；可在mechanics_model.elemental_model提供。",
                   "authoring_elemental_context_missing", manual_equivalent="mechanics_model.elemental_model")
        profile.setdefault("id", namespace + ".application")
        _append(elemental.setdefault("profiles", []), [profile], "mechanics_model.elemental_model.profiles")
        binding = pattern.get("application")
        if not isinstance(binding, dict):
            _error("请在hit_pattern.application明确目标与landed；辅助不会猜测命中条件。",
                   path="hit_pattern.application")
        binding.setdefault("profile_id", profile["id"])
        if binding["profile_id"] != profile["id"]:
            _error("攻击附着与本次提供的profile不一致。", path="hit_pattern.application.profile_id")
        # _append copied the declaration; update that exact newly authored row.
        for row in plan["mechanics_model"]["hit_patterns"]:
            if row["id"] == pattern["id"]:
                row["application"] = copy.deepcopy(binding)


def _materialization(plan, payload, namespace):
    from .equipment.agent_execution_materialization import (
        lint_agent_execution_materialization,
        new_agent_execution_materialization,
    )

    extensions = plan.setdefault("extensions", {})
    topology = extensions.get("event_topology")
    if not isinstance(topology, dict):
        _error("材料化族需要当前事件拓扑；简单有限命中可直接使用attack_bundle，无需拓扑。",
               "agent_materialization_topology_required", manual_equivalent="mechanics_model.hit_patterns / equipment topology")
    old = extensions.get("agent_execution_materialization")
    if isinstance(old, dict):
        from .equipment.agent_execution_materialization import plan_binding_fingerprint
        if old.get("plan_binding", {}).get("input_fingerprint") != plan_binding_fingerprint(plan):
            _error("已有材料化输入绑定已过期；先明确复核原输入，再请求binding_refresh的materialization=true。",
                   "authoring_materialization_stale")
    document = new_agent_execution_materialization(plan, topology,
        old["materialization_id"] if isinstance(old, dict) else namespace + ".materialization")
    if isinstance(old, dict):
        document["sources"] = copy.deepcopy(old["sources"])
        document["entries"] = copy.deepcopy(old["entries"])
    _append(document["sources"], payload.get("sources", []), "materialization.sources", identity="source_id")
    _append(document["entries"], payload.get("entries", []), "materialization.entries", identity="entry_id")
    extensions["agent_execution_materialization"] = document
    return lint_agent_execution_materialization(plan, topology, document)


def _special(plan, payload):
    ids = payload.get("remove_hit_ids", [])
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
        _error("remove_hit_ids须为不重复的普通命中ID。")
    model = plan["mechanics_model"]
    for identity in ids:
        hits = plan["manual_template"].get("hits", [])
        if len([r for r in hits if r.get("id") == identity]) != 1:
            _error("替换目标不存在或不唯一；未删除任何事件。", path="remove_hit_ids", id=identity)
        owners = [f"mechanics_model.hit_patterns[{i}]" for i, row in enumerate(model.get("hit_patterns", []))
                  if row.get("prototype_hit_id") == identity]
        owners += [f"action_event_profiles[{i}]" for i, row in enumerate(plan.get("action_event_profiles", []))
                   if row.get("prototype_hit_id") == identity]
        owners += [f"hit_parameter_sources[{i}]" for i, row in enumerate(plan.get("hit_parameter_sources", []))
                   if row.get("hit_id") == identity]
        if owners:
            _error("该命中还受模式或来源管理，须显式修改拥有者或使用已发布覆盖接口。",
                   "authoring_replacement_owner_required", id=identity,
                   owners=owners,
                   manual_equivalent="mechanics_model.hit_patterns / calculation_event_overrides")
        plan["manual_template"]["hits"] = [row for row in hits if row.get("id") != identity]
        model["hit_events"] = [row for row in model.get("hit_events", []) if row.get("hit_id") != identity]
    _append(model.setdefault("special_reaction_events", []), payload.get("special_reaction_events", []),
            "mechanics_model.special_reaction_events")


def _refresh(plan, payload, report):
    original_basis = fingerprint(input_view(plan))
    if payload.get("materialization") is True:
        from .equipment.agent_execution_materialization import (
            new_agent_execution_materialization,
        )
        extensions = plan.get("extensions", {})
        old = extensions.get("agent_execution_materialization")
        topology = extensions.get("event_topology")
        if not isinstance(old, dict) or not isinstance(topology, dict):
            _error("没有可重新绑定的材料化输入或当前事件拓扑。", "agent_materialization_topology_required")
        updated = new_agent_execution_materialization(plan, topology, old["materialization_id"])
        updated.update(sources=copy.deepcopy(old["sources"]), entries=copy.deepcopy(old["entries"]))
        extensions["agent_execution_materialization"] = updated
        if not payload.get("binding_ids"):
            return
    if not isinstance(report, dict) or not isinstance(report.get("plan"), dict):
        _error("请先组合当前候选，绑定辅助需要真实当前报告。", "authoring_current_composition_required")
    if fingerprint(input_view(report["plan"])) != original_basis:
        _error("组合报告对应的输入已变化，请先重新组合；未回填旧指纹。", "authoring_composition_stale")
    ids = payload.get("binding_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
        _error("请明确本次要恢复的binding_ids，不批量确认未指定来源。")
    for identity in ids:
        bindings = [row for row in plan["effect_bindings"] if row.get("binding_id") == identity]
        requirements = [row for row in report.get("effect_requirements", []) if row.get("binding_id") == identity]
        if len(bindings) != 1 or len(requirements) != 1:
            _error("当前绑定与报告条目不能唯一匹配。", path="binding_ids", id=identity)
        current = requirements[0]["source_fingerprint"]
        if bindings[0]["coverage"].get("source_fingerprint") != current:
            bindings[0]["coverage"].update(state="unreviewed", reviewed_by=None)
        bindings[0]["source_fingerprint"] = current
        bindings[0]["coverage"]["source_fingerprint"] = current


def build_fragment(original, request, *, composition_report=None):
    plan = copy.deepcopy(original)
    payload = request.get("payload", {})
    kind, namespace = request["kind"], request["request_id"]
    if kind in {"batch", "action_move"}:
        from .authoring_operations import build_operation
        return build_operation(original, request, composition_report=composition_report)
    allowed = {
        "attack_bundle": {"prototype_hit", "hit_pattern", "application_profile", "mechanics_model", "rotation_events"},
        "mechanics_fragment": {"mechanics_model", "rotation_events", "rotation_actions", "rotation_abilities"},
        "materialization_entries": {"sources", "entries"},
        "special_damage": {"special_reaction_events", "remove_hit_ids", "mechanics_model"},
        "binding_refresh": {"binding_ids", "materialization"},
    }[kind]
    if set(payload) - allowed:
        _error("作者片段包含不支持的字段；请查看该配方handler_matrix。", fields=sorted(set(payload) - allowed))
    for name in ("mechanics_model", "prototype_hit", "hit_pattern", "application_profile"):
        if name in payload and not isinstance(payload[name], dict):
            _error("此原生片段字段须为对象。", path=f"payload.{name}")
    for name in ("rotation_events", "rotation_actions", "rotation_abilities", "sources", "entries", "special_reaction_events"):
        if name in payload and (not isinstance(payload[name], list) or any(not isinstance(row, dict) for row in payload[name])):
            _error("此原生片段字段须为对象数组。", path=f"payload.{name}")
    model = payload.get("mechanics_model", {})
    for field in ("rules", "fixed_effect_sources"):
        if field in model and (not isinstance(model[field], list) or any(not isinstance(row, dict) for row in model[field])):
            _error("规则须为原生规则对象数组。", path=f"payload.mechanics_model.{field}")
    gaps = []
    if kind in {"attack_bundle", "mechanics_fragment", "special_damage"}:
        _native_fragment(plan, payload, namespace)
    if kind == "attack_bundle":
        _attack(plan, payload, namespace)
    elif kind == "materialization_entries":
        report = _materialization(plan, payload, namespace)
        gaps = report.get("issues", [])
    elif kind == "special_damage":
        _special(plan, payload)
    elif kind == "binding_refresh":
        _refresh(plan, payload, composition_report)
    return {key: value for key, value in plan.items() if value != original.get(key)}, gaps
