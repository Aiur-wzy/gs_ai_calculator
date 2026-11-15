"""Join a reviewed finite benefit to fresh action parents and the actual axis."""
from __future__ import annotations

import copy
import json
import sqlite3

from genshin_app.task_capability_producer import emit_task_missing
from genshin_engine.beneficiary_selection import possible_recipients
from genshin_engine.registry import BENEFIT_TYPES

from .energy_generation_sources import _one, _Pending, _ticks
from .files import fingerprint
from .limited_hit_benefit_schema import COMPILER, LIMITS, PART, PREFIX

MAX_RECEIPT_BYTES = LIMITS["max_receipt_bytes"]
MAX_PARENT_LINKS = LIMITS["max_parent_links"]
DAMAGE_TYPES = {"damage.normal_attack", "damage.charged_attack", "damage.plunging_attack", "damage.elemental_skill", "damage.elemental_burst"}


def parent_inputs(plan, template, binding, records, parameters, metadata):
    profile = _one(plan.get("action_event_profiles", []), "id", binding["action_event_profile_id"], "父命中档案")
    record = _one(records, "profile_id", profile["id"], "父派发回执")
    if (profile.get("kind") != "calibrated_single_contact_v1" or record.get("status") != "ready"
            or not record.get("profile_fingerprint")
            or profile.get("accepted_profile_fingerprint") != record["profile_fingerprint"]):
        raise _Pending("有限次数收益需要已接受的单次战技初始命中档案")
    prototype = _one(template["hits"], "id", profile["prototype_hit_id"], "命中原型")
    parameter = _one(parameters, "hit_id", prototype["id"], "参数来源")
    if parameter.get("status") != "ready":
        raise _Pending("父命中倍率来源尚未就绪")
    selection, actor = parameter["resolved_selection"], prototype["character"]
    if (selection.get("constellation") != 0 or parameter["level"]["effective_level"] != 10
            or "skill" not in selection.get("matching_slots", [])):
        raise _Pending("有限次数收益首片仅接受明确C0、有效10级战技参数")
    if record.get("actor") != actor or parameter.get("actor") != actor:
        raise _Pending("父命中与参数提供者不一致")
    from .passive_sources import _identities
    for other in _identities(plan, actor):
        if (other.get("character_id", selection["character_id"]) != selection["character_id"]
                or other.get("version_line", parameter["version_line"]) != parameter["version_line"]
                or other.get("constellation", 0) != 0):
            raise _Pending("提供者显式角色、资料线或命座来源矛盾")
    gear = plan.get("equipment_inputs", {}).get(actor, {}).get("character_ref")
    if gear and (gear["db_character_id"] != selection["character_id"] or gear["version_line"] != parameter["version_line"]):
        raise _Pending("收益提供者与装备角色引用矛盾")
    meta = [r for r in metadata if r["action_event_profile_id"] == profile["id"]]
    if len(meta) > 1 or meta and (meta[0].get("status") != "ready" or meta[0].get("snapshot") != parameter["snapshot"]):
        raise _Pending("父攻击元信息未就绪或不属于同快照")
    meta = meta[0] if meta else None
    model = plan["mechanics_model"]["elemental_model"]
    application = _one(model["profiles"], "id", profile["application_profile_id"], "附着档案")
    contact = _one(model.get("contact_profiles", []), "id", profile["contact_profile_id"], "接触档案")
    identity = {"version_line": parameter["version_line"], "snapshot_id": parameter["snapshot"]["snapshot_id"],
        "selection": {**{k: selection[k] for k in ("character_id", "skill_depot_id", "skill_id", "constellation")}, "talent_level": 10},
        "param_index": parameter["param_index"]}
    semantics = {"kind": profile["kind"], "variant": profile["variant"],
        "selection": copy.deepcopy(identity["selection"]), "param_index": parameter["param_index"],
        "profile_fingerprint": record["profile_fingerprint"], "source_dependency_fingerprint": record["source_dependency_fingerprint"],
        "metadata_source_fingerprint": meta["source_fingerprint"] if meta else None,
        "metadata_dependency_fingerprint": meta["metadata_dependency_fingerprint"] if meta else None,
        "hit_semantics": {k: prototype.get(k, 1 if k == "count" else None) for k in
                          ("damage_system", "scaling_stat", "count", "element", "damage_type_ids")},
        "application_semantics": {k: copy.deepcopy(v) for k, v in application.items() if k != "id"},
        "contact_semantics": {k: copy.deepcopy(v) for k, v in contact.items() if k != "id"}}
    return actor, profile, record, identity, semantics


def _parent_links(plan, template, actor, profile, record, calibration):
    model, rotation = plan["mechanics_model"], plan["rotation_model"]
    actions = {a["id"]: a for a in rotation["actions"]}
    abilities = {a["id"]: a for a in rotation["abilities"]}
    patterns = {p["id"]: p for p in model["hit_patterns"]}
    hits = {h["id"]: h for h in template["hits"]}
    generated = record.get("generated") or []
    if not generated or len(generated) > MAX_PARENT_LINKS:
        raise _Pending("实际父动作为空或超过关联限额")
    delay, horizon = _ticks(calibration["activation_offset_seconds"]), _ticks(rotation["period"])
    links, covered = [], set()
    for item in generated:
        action, ability = actions.get(item["action_id"]), abilities.get(item["ability_id"])
        pattern = patterns.get(item["pattern_id"])
        application = {"profile_id": profile["application_profile_id"],
                       "target_id": model["elemental_model"]["target"]["id"], "landed": item["landed"]}
        if (not action or not ability or not pattern or action["ability_id"] != ability["id"]
                or ability["character"] != actor or ability["action_type_id"] != "action.elemental_skill"
                or ability.get("variant") != profile["variant"] or action["repeat"] != {"mode": "once"}
                or pattern["action_id"] != action["id"] or pattern["prototype_hit_id"] != profile["prototype_hit_id"]
                or pattern["offsets"] != [profile["calibration"]["offset_seconds"]] or pattern["orders"] != [item["order"]]
                or pattern.get("application") != application
                or pattern.get("contact") != {"kind": "profile", "profile_id": profile["contact_profile_id"]}
                or item["hit_id"] != pattern["id"] + ":1" or type(item["landed"]) is not bool):
            raise _Pending("父档案、实际战技动作、命中或接触关联不一致")
        start, end = _ticks(action["start"]), _ticks(action["occupation_end"])
        tick, activation = start + _ticks(pattern["offsets"][0]), start + delay
        if (tick != _ticks(item["t"]) or action["id"] in covered or not start <= activation <= end
                or activation >= horizon):
            raise _Pending("实际初始命中/生效时点超出动作或窗口，或父动作重复")
        # Positive delays are causal children: on equal ticks the plain hit wins
        # at equal order. Zero delay executes synchronously in action/pre.
        if activation > tick or activation == tick and (delay > 0 and action["order"] >= item["order"]):
            raise _Pending("当前队列顺序不能证明收益先于战技初始命中；请校准时点或明确事件顺序")
        covered.add(action["id"])
        links.append({**copy.deepcopy(item), "action_tick": start, "activation_tick": activation,
            "absolute_tick": tick, "action_order": action["order"], "source_cycle": 0,
            "pattern_fingerprint": fingerprint(pattern)})
    allowed = {link["pattern_id"] for link in links}
    for action in actions.values():
        ability = abilities[action["ability_id"]]
        if ability["character"] == actor and ability["action_type_id"] == "action.elemental_skill" and action["id"] not in covered:
            raise _Pending("同提供者存在未覆盖或另一变体的战技动作")
    for kind in ("hit_events", "hit_patterns", "application_events"):
        for item in model.get(kind, []):
            action = actions.get(item.get("action_id"), {})
            ability = abilities.get(action.get("ability_id"), {})
            hit = hits.get(item.get("hit_id", item.get("prototype_hit_id")), {})
            owner = hit.get("character", ability.get("character"))
            relevant = ability.get("action_type_id") == "action.elemental_skill" or "damage.elemental_skill" in (hit.get("damage_type_ids") or [])
            if owner == actor and relevant and (kind != "hit_patterns" or item["id"] not in allowed):
                raise _Pending("同提供者还有未受审战技命中或附着；先完整声明所属动作")
    return sorted(links, key=lambda link: (link["action_tick"], link["action_order"], link["action_id"]))


def _provider_atk_dependencies(plan, template, actor):
    """Reject only potential provider ATK writes that D20._ordinary skips."""
    paths = []
    matches = {tuple(sorted(row["effect_ref"].items())) for row in plan.get("rotation_tuning", {}).get("buff_hit_matches", [])}
    for buff in template.get("buffs", []):
        if buff.get("target") not in (None, actor):
            continue
        for effect in buff["effects"]:
            if effect["zone"] in ("stat_add", "stat_pct") and effect["stat"] == "atk":
                ref = tuple(sorted({"buff_id": buff["id"], "effect_id": effect["id"]}.items()))
                if effect.get("applies_to", {}).get("mode", "all") != "all" or ref in matches:
                    paths.append(f"buffs.{buff['id']}.{effect['id']}")
    for rule in plan["mechanics_model"]["rules"]:
        possible = possible_recipients(rule, [actor])
        if actor not in possible:
            continue
        for effect in rule["effects"]:
            info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
            if (info.get("zone") in ("stat_add", "stat_pct") and info.get("stat") == "atk"
                    and (isinstance(effect["value"], dict) or "filter" in effect or rule.get("quota"))):
                paths.append(f"rules.{rule['id']}.{effect['id']}")
    if paths:
        raise _Pending("提供者当前攻击力含现引擎不能完整取样的贡献：" + ", ".join(paths))


def _consumer_scope(plan, template):
    hits = {h["id"]: h for h in template["hits"]}
    model = plan["mechanics_model"]
    consumers = []
    for field in ("hit_events", "hit_patterns"):
        for item in model.get(field, []):
            hit = hits[item["hit_id"] if field == "hit_events" else item["prototype_hit_id"]]
            if hit["element"] != "cryo":
                continue
            tags = hit.get("damage_type_ids") or []
            if hit["damage_system"] != "classic" or hit.get("count", 1) != 1 or not tags or not set(tags) <= DAMAGE_TYPES:
                raise _Pending("潜在冰翎受益伤害须为独立、单次经典命中并标明受审伤害种类：" + hit["id"])
            consumers.append({"kind": field, "hit_id": hit["id"], "character": hit["character"],
                              "descriptor": copy.deepcopy(item),
                              "hit_fingerprint": fingerprint(hit)})
    return consumers


def _duplicates(plan, template, binding, actor, identity):
    sid = binding["id"] + ":" + PART
    paths = []
    for rule in plan["mechanics_model"]["rules"]:
        if rule.get("source_id") == sid or rule["id"].startswith(PREFIX):
            paths.append("rules." + rule["id"])
    for buff in template.get("buffs", []):
        ext = buff.get("extensions", {})
        if ext.get("source_id") == sid:
            paths.append("buffs." + buff["id"])
    for old in plan["effect_bindings"]:
        owner = old["owner"]
        if (old["provider_character_id"] != actor or owner.get("kind") != "character_skill"
                or owner.get("character_id") != identity["selection"]["character_id"]):
            continue
        # A typed skill source with a base-add part is known competing delivery.
        # Unlabelled manual constants are not identified by name or magnitude.
        for part in old["parts"]:
            if part["state"] == "excluded":
                continue
            delivery = part.get("delivery") or {}
            if (delivery.get("effect") or {}).get("benefit_type_id") == "benefit.base_add.all":
                paths.append(f"effect_bindings.{old['binding_id']}.{part['part_id']}")
            rid = (part.get("rule") or {}).get("rule_id")
            rule = next((r for r in plan["mechanics_model"]["rules"] if r["id"] == rid), {})
            if any(e["benefit_type_id"] == "benefit.base_add.all" for e in rule.get("effects", [])):
                paths.append(f"effect_bindings.{old['binding_id']}.{part['part_id']}")
    if paths:
        raise _Pending("已知同源收益可能重复，先明确保留哪份交付；未自动移除：" + ", ".join(sorted(set(paths))))


def _delivery(binding, actor, links, projection):
    rid, sid = PREFIX + binding["id"], binding["id"] + ":" + PART
    rule = copy.deepcopy(projection["rule_template"])
    rule.update(id=rid, owner=actor, source_id=sid)
    rule["trigger"].update(action_ids=[link["action_id"] for link in links], characters=[actor])
    initial = copy.deepcopy(projection["initial_rule_template"])
    initial["rule_id"] = rid
    part = {"part_id": PART, "name_zh": "冰翎有限次数加算", "rule": {"kind": "mechanics", "rule_id": rid},
            "delivery": {"kind": "mechanics"}}
    return {"parts": [part], "rules": [rule], "initial_rules": [initial]}


def compile_limited_hit_benefit_sources(plan, template, db_path, *, action_event_compilations=(),
        attack_metadata_compilations=(), parameter_compilations=(), missing, source_provider=None):
    """The source provider is a private offline-verification hook, not native input."""
    bindings = plan.get("limited_hit_benefit_sources", [])
    if not bindings:
        return None, [], []
    from genshin_engine.runtime_limited_hit_benefit_compiler import (
        compile_limited_hit_benefit_source,
    )
    if source_provider is None:
        from genshin_db.runtime_limited_hit_benefit import (
            get_limited_hit_benefit_context,
        )
        source_provider = lambda binding, identity: get_limited_hit_benefit_context(db_path,
            **identity["selection"], version_line=identity["version_line"], snapshot_id=identity["snapshot_id"],
            param_index=identity["param_index"]).public_projection
    records = []
    for binding in bindings:
        record = {"id": binding["id"], "action_event_profile_id": binding["action_event_profile_id"], "actor": None,
            "compiler": COMPILER, "status": "pending", "source_bundle": None, "source_fingerprint": None,
            "whole_source_status": "partial", "gameplay_interpretation": "reference_adapted_not_decoded",
            "accepted_fingerprint": binding["accepted_fingerprint"], "required_confirmation": None,
            "parent_links": [], "parts": [], "rules": [], "initial_rules": [], "issues": []}
        records.append(record)

        def issue(code, message, *, binding=binding, record=record):
            path = "limited_hit_benefit_sources." + binding["id"]
            record["issues"].append({"code": code, "path": path, "message": str(message)[:2048]})
            receipt_limit = code == "limited_hit_benefit_receipt_limit"
            emit_task_missing(
                missing, code, path, str(message)[:2048],
                "review_limited_hit_benefit",
                consumer="limited_hit_benefit:" + binding["id"],
                category=("engine_capability_missing" if receipt_limit
                          else "knowledge_missing"),
                impact="critical" if receipt_limit else "high",
                blocking_scope=("engine_capability" if receipt_limit
                                else "agent_materialization"),
                retryable=not receipt_limit,
                repair_hint=(
                    "缩小有限收益来源批次或扩展受审回执承载能力"
                    if receipt_limit else
                    "核对父动作、受益范围、次数、属性取样、场景校准和确认指纹"
                ),
            )

        try:
            if len(bindings) > LIMITS["max_sources"]:
                raise _Pending("有限次数收益首片最多一个提供者")
            if not binding["action_event_profile_id"]:
                raise _Pending("请先选择已接受的父动作档案")
            actor, profile, parent, identity, semantics = parent_inputs(plan, template, binding,
                action_event_compilations, parameter_compilations, attack_metadata_compilations)
            record.update(actor=actor, parent_identity=identity, parent_semantics=semantics)
            bundle = source_provider(binding, identity)
            record["source_bundle"] = copy.deepcopy(bundle)
            if (bundle.get("snapshot", {}).get("snapshot_id") != identity["snapshot_id"]
                    or bundle.get("snapshot", {}).get("version_line") != identity["version_line"]):
                raise _Pending("收益来源与新鲜父参数的快照或资料线不一致")
            projection = compile_limited_hit_benefit_source(bundle, parent_semantics=semantics,
                scenario=binding["scenario"], calibration=binding["calibration"])
            record["projection"] = copy.deepcopy(projection)
            for key in ("source_fingerprint", "source_status", "benefit_status", "benefit_fingerprint", "benefit_certificate", "semantic_dependency"):
                record[key] = copy.deepcopy(projection.get(key))
            if projection.get("status") != "ready":
                raise _Pending("有限次数收益来源尚未就绪：" + "; ".join(i.get("message", i.get("code", "未知")) for i in projection.get("issues", [])))
            links = _parent_links(plan, template, actor, profile, parent, binding["calibration"])
            _provider_atk_dependencies(plan, template, actor)
            _duplicates(plan, template, binding, actor, identity)
            consumers = _consumer_scope(plan, template)
            stage = _delivery(binding, actor, links, projection)
            acceptance = fingerprint({"compiler": COMPILER, "benefit_fingerprint": projection["benefit_fingerprint"]})
            scope = {"actor": actor, "parent_identity": identity, "parent_links": links, "consumers": consumers,
                     "provider_stats": copy.deepcopy(_one(template["characters"], "id", actor, "提供者面板")["stats"]),
                     "identity_coverage": "explicit_typed_sources_and_declared_no_external_changes"}
            record.update(parent_links=links, calibration=copy.deepcopy(binding["calibration"]),
                scenario=copy.deepcopy(binding["scenario"]), scope=scope, acceptance_fingerprint=acceptance,
                execution_fingerprint=fingerprint({"scope": scope, "delivery": stage}),
                required_confirmation={"accepted_fingerprint": acceptance}, **stage)
            if binding["accepted_fingerprint"] != acceptance:
                issue("limited_hit_benefit_confirmation_required", "请核对原文分支、每人次数、当前攻击力取样、场景与生效校准后接受")
            else:
                record["status"] = "ready"
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            issue("limited_hit_benefit_pending", exc)
        if len(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) > MAX_RECEIPT_BYTES:
            keep = {key: record[key] for key in ("id", "action_event_profile_id", "actor", "compiler", "accepted_fingerprint")}
            record.clear()
            record.update(keep, status="pending", source_bundle=None, source_fingerprint=None,
                whole_source_status="partial", gameplay_interpretation="reference_adapted_not_decoded",
                required_confirmation=None, parent_links=[], parts=[], rules=[], initial_rules=[], issues=[])
            issue("limited_hit_benefit_receipt_limit", "完整收益来源超过回执限额；未交付截断资料或规则")
    if any(record["status"] != "ready" for record in records):
        for record in records:
            record.update(status="pending", preview_rules=record["rules"], parts=[], rules=[], initial_rules=[])
        return None, records, []
    model = copy.deepcopy(plan["mechanics_model"])
    for record in records:
        model["rules"].extend(record["rules"])
        model["initial_state"]["rules"].extend(record["initial_rules"])
    sources = [{"kind": "limited_hit_benefit", "id": record["id"], "source_fingerprint": record["source_fingerprint"],
                "accepted_fingerprint": record["accepted_fingerprint"], "execution_fingerprint": record["execution_fingerprint"]} for record in records]
    return model, records, sources
