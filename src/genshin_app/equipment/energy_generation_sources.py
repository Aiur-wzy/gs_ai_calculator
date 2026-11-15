"""Bind fresh, reviewed particle sources to existing accepted initial hits."""
from __future__ import annotations

import copy
import json
import sqlite3
from decimal import Decimal
from itertools import pairwise

from genshin_app.task_capability_producer import emit_task_missing

from .energy_generation_schema import COMPILER, LIMITS, PART, PREFIX
from .files import fingerprint

MAX_RECEIPT_BYTES = LIMITS["max_receipt_bytes"]
MAX_PARENT_LINKS = LIMITS["max_parent_links"]


class _Pending(ValueError):
    pass


def _ticks(value):
    if type(value) not in (int, float) or not 0 <= value <= 1000000000:
        raise _Pending("时点须为有限非负数")
    number = Decimal(str(value)) * 1000000
    if number != number.to_integral_value():
        raise _Pending("时间必须精确到整数微秒；未自动量化")
    return int(number)


def _one(rows, key, value, name):
    selected = [row for row in rows if row.get(key) == value]
    if len(selected) != 1:
        raise _Pending(name + "缺失或不唯一")
    return selected[0]


def parent_inputs(plan, template, binding, records, parameters, metadata):
    """Build a semantic identity from fresh compiler output, never display names."""
    profile = _one(plan.get("action_event_profiles", []), "id", binding["action_event_profile_id"], "父命中档案")
    record = _one(records, "profile_id", profile["id"], "父派发回执")
    if (profile.get("kind") != "calibrated_single_contact_v1" or record.get("status") != "ready"
            or not record.get("profile_fingerprint")
            or profile.get("accepted_profile_fingerprint") != record["profile_fingerprint"]):
        raise _Pending("产球需要已接受的单次E初始命中档案")
    prototype = _one(template["hits"], "id", profile["prototype_hit_id"], "命中原型")
    parameter = _one(parameters, "hit_id", prototype["id"], "参数来源")
    if parameter.get("status") != "ready":
        raise _Pending("父命中倍率来源尚未就绪")
    selection = parameter["resolved_selection"]
    if selection.get("constellation") != 0 or parameter["level"]["effective_level"] != 10 or "skill" not in selection.get("matching_slots", []):
        raise _Pending("产球首片仅接受明确C0、有效10级E参数身份")
    actor = prototype["character"]
    if record.get("actor") != actor or parameter.get("actor") != actor:
        raise _Pending("父命中与参数提供者不一致")
    from .passive_sources import _identities
    for identity in _identities(plan, actor):
        if (identity.get("character_id", selection["character_id"]) != selection["character_id"]
                or identity.get("version_line", parameter["version_line"]) != parameter["version_line"]
                or identity.get("constellation", 0) != 0):
            raise _Pending("同提供者显式角色、资料线或命座来源矛盾")
    gear = plan.get("equipment_inputs", {}).get(actor, {}).get("character_ref")
    if gear and (gear["db_character_id"] != selection["character_id"] or gear["version_line"] != parameter["version_line"]):
        raise _Pending("产球角色与装备角色引用矛盾")
    meta = [r for r in metadata if r["action_event_profile_id"] == profile["id"]]
    if len(meta) > 1 or meta and meta[0].get("status") != "ready":
        raise _Pending("父攻击元信息缺失或尚待核实")
    meta = meta[0] if meta else None
    model = plan["mechanics_model"]["elemental_model"]
    application = _one(model["profiles"], "id", profile["application_profile_id"], "附着档案")
    contact = _one(model.get("contact_profiles", []), "id", profile["contact_profile_id"], "接触档案")
    identity = {"version_line": parameter["version_line"], "snapshot_id": parameter["snapshot"]["snapshot_id"],
        "selection": {**{k: selection[k] for k in ("character_id", "skill_depot_id", "skill_id", "constellation")}, "talent_level": 10},
        "param_index": parameter["param_index"], "metadata_candidate_id": None}
    if meta:
        candidate = meta.get("candidate_id") or (meta.get("query", {}).get("selected_candidate") or {}).get("candidate_id")
        if not candidate or meta.get("snapshot") != parameter["snapshot"]:
            raise _Pending("元信息精确候选或同快照关系缺失")
        identity["metadata_candidate_id"] = candidate
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


def _parent_links(plan, template, actor, profile, record, certificate):
    model, rotation = plan["mechanics_model"], plan["rotation_model"]
    actions = {a["id"]: a for a in rotation["actions"]}
    abilities = {a["id"]: a for a in rotation["abilities"]}
    patterns = {p["id"]: p for p in model["hit_patterns"]}
    hits = {h["id"]: h for h in template["hits"]}
    links, covered = [], set()
    generated = record.get("generated") or []
    if not generated or len(generated) > MAX_PARENT_LINKS:
        raise _Pending("实际初始命中为空或超过映射限额")
    if certificate["variant"] != profile["variant"]:
        raise _Pending("所选产球叶与实际技能变体不一致")
    for item in generated:
        action, ability = actions.get(item["action_id"]), abilities.get(item["ability_id"])
        pattern = patterns.get(item["pattern_id"])
        expected = {"profile_id": profile["application_profile_id"], "target_id": model["elemental_model"]["target"]["id"],
                    "landed": item["landed"]}
        if (not action or not ability or not pattern or action["ability_id"] != ability["id"]
                or ability["character"] != actor or ability["action_type_id"] != "action.elemental_skill"
                or ability.get("variant") != profile["variant"] or action["repeat"] != {"mode": "once"}
                or pattern["action_id"] != action["id"] or pattern["prototype_hit_id"] != profile["prototype_hit_id"]
                or pattern["offsets"] != [profile["calibration"]["offset_seconds"]] or pattern["orders"] != [item["order"]]
                or pattern.get("application") != expected
                or pattern.get("contact") != {"kind": "profile", "profile_id": profile["contact_profile_id"]}
                or item["hit_id"] != pattern["id"] + ":1" or type(item["landed"]) is not bool):
            raise _Pending("实际初始命中与已接受档案/动作/附着/接触不一致")
        tick = _ticks(action["start"]) + _ticks(pattern["offsets"][0])
        if tick != _ticks(item["t"]) or item["action_id"] in covered:
            raise _Pending("初始命中时点或每动作唯一关系不一致")
        covered.add(item["action_id"])
        links.append({**copy.deepcopy(item), "absolute_tick": tick, "source_cycle": 0,
                      "pattern_fingerprint": fingerprint(pattern)})
    allowed_patterns = {i["pattern_id"] for i in links}
    for action in rotation["actions"]:
        ability = abilities[action["ability_id"]]
        if ability["character"] == actor and ability["action_type_id"] == "action.elemental_skill" and action["id"] not in covered:
            raise _Pending("同提供者存在未覆盖或另一变体的E动作，无法排除共享控制器重入")
    for kind in ("hit_events", "hit_patterns", "application_events"):
        for item in model.get(kind, []):
            action = actions.get(item.get("action_id"), {})
            ability = abilities.get(action.get("ability_id"), {})
            hit = hits.get(item.get("hit_id", item.get("prototype_hit_id")), {})
            owner = hit.get("character", ability.get("character"))
            relevant = ability.get("action_type_id") == "action.elemental_skill" or "damage.elemental_skill" in (hit.get("damage_type_ids") or [])
            if owner == actor and relevant and (kind != "hit_patterns" or item["id"] not in allowed_patterns):
                raise _Pending("同提供者存在未受审E命中/附着，不能忽略控制器影响")
    ordered = sorted(links, key=lambda item: (item["absolute_tick"], item["order"], item["hit_id"]))
    limit = certificate["isolation"]["isolation_ticks"]
    if type(limit) is not int or limit < 0 or any(b["absolute_tick"] - a["absolute_tick"] <= limit for a, b in pairwise(ordered)):
        raise _Pending("实际初始命中未严格超过共享控制器隔离界；未模拟抑制或按动作重置")
    return ordered


def _migration(plan, binding, links):
    rotation, model = plan["rotation_model"], plan["mechanics_model"]
    events = {e["id"]: e for e in rotation["events"]}
    actions = {a["id"]: a for a in rotation["actions"]}
    covered = {r["action_id"] for r in links}
    selected = set(binding["replaces_spawn_ids"])
    for eid in selected:
        old = events.get(eid)
        if not old or old["type"] != "energy_spawn" or old.get("action_id") not in covered:
            raise _Pending("接管清单仅可包含当前受管动作的原生energy_spawn")
    existing = {e["id"] for e in events.values() if e["type"] == "energy_spawn" and e.get("action_id") in covered}
    if existing != selected:
        raise _Pending("同动作有未明确接管的手工产球；未按数值或名字猜测同源")
    removed = selected | {e["id"] for e in events.values() if e["type"] == "energy_credit" and e["spawn_id"] in selected}
    for owner in model.get("resource_ownership", []):
        owned_rule = next((r for r in model["rules"] if r["id"] == owner["rule_id"]), {})
        particles = any(r["kind"] == "particles" for r in owned_rule.get("resources", []))
        if (owner["action_id"] in covered and particles) or set(owner["replaces_event_ids"]) & removed:
            raise _Pending("受管动作已有资源所有权，须先解决已有手工/自动交付")
    for rule in model["rules"]:
        if not any(r["kind"] == "particles" for r in rule.get("resources", [])):
            continue
        trigger = rule["trigger"]
        if ((not trigger.get("action_ids") or covered & set(trigger["action_ids"]))
                and (not trigger.get("characters") or any(r.get("actor") in trigger["characters"] for r in links)
                     or rule["owner"] == links[0].get("actor"))):
            raise _Pending("已有particles规则可能覆盖本动作，不能靠不同source_part重复交付")
    affected = set()
    for action in actions.values():
        for outcome in action.get("rule_outcomes", []):
            ids = set(outcome.get("event_ids", []))
            if ids & removed:
                if not ids <= removed:
                    raise _Pending("旧规则结果仅被部分接管，不能留下悬空执行声明")
                affected.add((action["ability_id"], outcome["rule_id"]))
    for action in actions.values():
        for outcome in action.get("rule_outcomes", []):
            if (action["ability_id"], outcome["rule_id"]) in affected and not set(outcome.get("event_ids", [])) <= removed:
                raise _Pending("同能力其他动作仍依赖被接管旧规则，不能全局删除")
    return {"spawn_ids": sorted(selected), "event_ids": sorted(removed),
            "events": [copy.deepcopy(events[eid]) for eid in sorted(removed)]}


def _delivery(binding, actor, links, projection, migration):
    rid, sid = PREFIX + binding["id"], binding["id"] + ":" + PART
    calibration = binding["calibration"]
    source = "确定产球有限参考；零卡肉/完整初始命中；" + binding["scenario"]["source"] + "；" + calibration["source"]
    rule = {"id": rid, "name_zh": "初始战技命中产球", "owner": actor, "source_id": sid,
        "trigger": {"event": "hit", "phase": "post", "event_ids": [i["hit_id"] for i in links],
                    "characters": [actor], "owner_presence": "frontline"},
        "beneficiaries": {"mode": "self", "scope": "locked"},
        "timing": {"delay": 0, "duration": 0, "cooldown": 0, "cooldown_anchor": "success"},
        "stacking": {"max": 1, "add": 1, "policy": "replace"}, "effects": [],
        "resources": [{"id": "particles", "kind": "particles", "delay": calibration["spawn_delay_seconds"],
            "travel_time": calibration["travel_seconds"], "count": projection["count"], "element": projection["element"],
            "particle_type": projection["particle_type"], "source_part": sid}], "status": "assumed", "source": source}
    initial = {"rule_id": rid, "cooldown_remaining": 0, "instances": []}
    ownership = [{"rule_id": rid, "action_id": i["action_id"], "source_part": sid,
        "replaces_event_ids": [e["id"] for e in migration["events"] if e["type"] == "energy_spawn" and e.get("action_id") == i["action_id"]]} for i in links]
    part = {"part_id": PART, "name_zh": "确定粒子派发", "rule": {"kind": "mechanics", "rule_id": rid},
            "delivery": {"kind": "mechanics"}}
    return {"parts": [part], "rules": [rule], "initial_rules": [initial], "resource_ownership": ownership}


def compile_energy_generation_sources(plan, template, db_path, *, action_event_compilations=(),
        attack_metadata_compilations=(), parameter_compilations=(), missing,
        source_provider=None, projection_provider=None):
    """Compile source-bound particle delivery.

    ``source_provider`` and ``projection_provider`` are internal offline replay
    hooks, never plan/API fields.  The latter exists for exact, allow-listed
    historical compiler records whose reference snapshot is no longer the
    active runtime snapshot; callers must validate that record before replay.
    """
    bindings = plan.get("energy_generation_sources", [])
    if not bindings:
        return None, [], []
    from genshin_engine.runtime_energy_trigger_compiler import (
        compile_energy_trigger_source,
    )
    if source_provider is None:
        from genshin_db.runtime_energy_dispatch import get_energy_dispatch_context
        source_provider = lambda binding, identity: get_energy_dispatch_context(
            db_path, origin_reference_id=binding["origin_reference_id"], parent_identity=identity).public_projection
    records = [{"id": b["id"], "action_event_profile_id": b["action_event_profile_id"], "actor": None,
        "origin_reference_id": b["origin_reference_id"],
        "compiler": COMPILER, "status": "pending", "source_bundle": None, "source_fingerprint": None,
        "whole_source_status": "partial", "dispatch_mode": "calibrated_reference", "required_confirmation": None,
        "accepted_fingerprint": b["accepted_fingerprint"], "parent_links": [], "migration": None,
        "parts": [], "rules": [], "initial_rules": [], "resource_ownership": [], "issues": []} for b in bindings]
    seen_actors, links_total = set(), 0
    for binding, record in zip(bindings, records, strict=True):
        def issue(code, text, *, record=record, binding=binding):
            path = "energy_generation_sources." + binding["id"]
            record["issues"].append({"code": code, "path": path, "message": str(text)[:1024]})
            receipt_limit = code == "energy_generation_receipt_limit"
            emit_task_missing(
                missing, code, path, str(text)[:1024],
                "review_energy_generation",
                consumer="energy_generation:" + binding["id"],
                category=("engine_capability_missing" if receipt_limit
                          else "knowledge_missing"),
                impact="critical" if receipt_limit else "high",
                blocking_scope=("engine_capability" if receipt_limit
                                else "agent_materialization"),
                retryable=not receipt_limit,
                repair_hint=(
                    "缩小产球来源批次或扩展受审回执承载能力"
                    if receipt_limit else
                    "核对产球父动作、精确来源、时间校准、迁移清单和确认指纹"
                ),
            )
        try:
            if not binding["origin_reference_id"] or not binding["action_event_profile_id"]:
                raise _Pending("选择精确产球来源和现有父档案后再预览")
            actor, profile, parent, identity, semantics = parent_inputs(plan, template, binding,
                action_event_compilations, parameter_compilations, attack_metadata_compilations)
            record.update(actor=actor, parent_identity=identity, parent_semantics=semantics)
            if actor in seen_actors:
                raise _Pending("同一提供者首片只能绑定一种固定产球变体")
            seen_actors.add(actor)
            bundle = source_provider(binding, identity)
            record["source_bundle"] = copy.deepcopy(bundle)
            if (bundle.get("parent_identity") != identity or bundle.get("snapshot", {}).get("snapshot_id") != identity["snapshot_id"]
                    or bundle.get("source_query", {}).get("selection", {}).get("origin_reference_id") != binding["origin_reference_id"]):
                raise _Pending("产球补证与fresh父参数身份/快照不一致")
            projected = (
                projection_provider(binding, identity)
                if projection_provider is not None
                else None
            )
            if projected is None:
                projected = compile_energy_trigger_source(
                    bundle,
                    parent_semantics=semantics,
                    scenario=binding["scenario"],
                )
            else:
                projected = copy.deepcopy(projected)
            record["projection"] = copy.deepcopy(projected)
            for key in ("source_fingerprint", "source_status", "trigger_status", "trigger_fingerprint", "trigger_certificate", "resource_projection", "semantic_dependency"):
                record[key] = copy.deepcopy(projected.get(key))
            if projected.get("status") != "ready":
                raise _Pending("产球来源或触发参考范围尚未就绪：" + "; ".join(i.get("message", i.get("code", "未知")) for i in projected.get("issues", [])))
            calibration = binding["calibration"]
            if calibration["status"] not in ("assumed", "confirmed") or not calibration["source"].strip():
                raise _Pending("产出/旅行时间及记录精度需要明确校准依据")
            for field in ("spawn_delay_seconds", "travel_seconds", "precision_seconds"):
                _ticks(calibration[field])
            links = _parent_links(plan, template, actor, profile, parent, projected["trigger_certificate"])
            for link in links:
                link["actor"] = actor
            links_total += len(links)
            if links_total > MAX_PARENT_LINKS:
                raise _Pending("全部产球来源的实际命中关联超过限额")
            migration = _migration(plan, binding, links)
            stage = _delivery(binding, actor, links, projected["resource_projection"], migration)
            sid = stage["rules"][0]["source_id"]
            if any(r.get("source_id") == sid for r in [*plan["mechanics_model"]["rules"],
                    *plan["mechanics_model"].get("fixed_effect_sources", [])]):
                raise _Pending("产球来源分项标识与已有收益来源重复")
            record.update(parent_links=links, migration=migration, calibration=copy.deepcopy(calibration),
                          scenario=copy.deepcopy(binding["scenario"]))
            acceptance = fingerprint({"compiler": COMPILER, "trigger_fingerprint": projected["trigger_fingerprint"],
                "calibration": calibration, "migration": migration})
            scope = {"actor": actor, "parent_identity": identity, "parent_links": links, "migration": migration,
                     "controller_key": projected["trigger_certificate"]["controller_key"],
                     "isolation": projected["trigger_certificate"]["isolation"]}
            record.update(scope=scope, acceptance_fingerprint=acceptance,
                execution_fingerprint=fingerprint({"scope": scope, "delivery": stage}),
                required_confirmation={"accepted_fingerprint": acceptance}, **stage)
            if binding["accepted_fingerprint"] != acceptance:
                issue("energy_generation_confirmation_required", "请核对完整产球来源、零卡肉参考、时间校准与迁移清单后接受")
            else:
                record["status"] = "ready"
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            issue("energy_generation_pending", exc)
        if len(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) > MAX_RECEIPT_BYTES:
            # Preserve all native identities, not a partial large source masquerading
            # as a usable receipt. Even a single ready item must be rolled back.
            for row in records:
                identity = {k: row[k] for k in ("id", "action_event_profile_id", "origin_reference_id", "actor", "compiler", "accepted_fingerprint")}
                row.clear()
                row.update(identity, status="pending", source_bundle=None, source_fingerprint=None,
                    whole_source_status="partial", dispatch_mode="calibrated_reference", required_confirmation=None,
                    parent_links=[], migration=None, parts=[], rules=[], initial_rules=[], resource_ownership=[], issues=[])
            issue("energy_generation_receipt_limit", "完整产球来源超过总回执限额；未交付截断资料")
            for tail in records[records.index(record) + 1:]:
                tail["issues"].append({"code": "energy_generation_peer_limit", "message": "前项超限，未继续读取此来源"})
            break
    if any(r["status"] != "ready" for r in records):
        for record in records:
            if record["status"] == "ready":
                record["issues"].append({"code": "energy_generation_peer_pending", "message": "其他产球来源待处理，整组未交付"})
            record["status"] = "pending"
            record["preview_rules"] = record["rules"]
            record.update(parts=[], rules=[], initial_rules=[], resource_ownership=[])
        return None, records, []
    model = copy.deepcopy(plan["mechanics_model"])
    for record in records:
        model["rules"].extend(record["rules"])
        model["initial_state"]["rules"].extend(record["initial_rules"])
        model.setdefault("resource_ownership", []).extend(record["resource_ownership"])
    sources = [{"kind": "energy_generation", "id": r["id"], "source_fingerprint": r["source_fingerprint"],
                "accepted_fingerprint": r["accepted_fingerprint"], "execution_fingerprint": r["execution_fingerprint"]} for r in records]
    return model, records, sources
