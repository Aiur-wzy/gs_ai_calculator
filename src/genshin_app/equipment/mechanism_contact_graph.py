"""Pure relations between fresh source receipts and actual contact routes.

This is not a dispatcher or an execution qualification. Single-contact scope is
rebuilt using the dispatcher's own portable acceptance helper. Callers supply
complete source receipts from one fresh server compose, or materialized evidence
verified during replay; full execution replay remains the receipt layer's job.
"""
from __future__ import annotations

import copy
import json
import math

from .action_events import (
    PREFIX,
    _source_identity,
    _ticks,
    _without_id,
    build_single_action_scope,
)
from .files import fingerprint
from .mechanism_parent_graph import SNAPSHOT_FIELDS, _snapshot_equal
from .periodic_events import (
    _refresh_targets_profile,
    patterns_for_record,
    periodic_instance_payload,
)
from .periodic_receipt import _sources as _verify_periodic_sources

MAX_PARENTS = 512
MAX_GRAPH_BYTES = 2 * 1024 * 1024
# The existing dispatcher already bounds its complete receipt at 40,000. Only
# scope-related contacts consume this graph's narrower 512-parent allowance.
MAX_DISPATCH_CONTACTS = 40000
SINGLE = "calibrated_single_contact_v1"
MULTI = "calibrated_multi_contact_v1"
TAG_TYPES = {"Elemental_Art": "damage.elemental_skill",
             "Elemental_Art_Hold": "damage.elemental_skill",
             "Elemental_Art_Extra": "damage.elemental_skill",
             "Elemental_Burst": "damage.elemental_burst"}


def _need(value, message):
    if not value:
        raise ValueError(message)


def _one(rows, key, value):
    found = [r for r in rows if r.get(key) == value]
    _need(len(found) == 1, "相关凭证或声明缺失/重复：" + key + "=" + str(value))
    return found[0]


def _index(rows, key="id"):
    result = {r[key]: r for r in rows}
    _need(len(result) == len(rows), "实际输入身份重复：" + key)
    return result


def _possible(scope, hit, ability):
    values = {"action_type_ids": [ability.get("action_type_id")],
              "damage_type_ids": hit.get("damage_type_ids"), "elements": [hit.get("element")]}
    for key, allowed in scope.items():
        known = [v for v in values[key] or [] if isinstance(v, str) and v]
        if allowed and known and not set(known).intersection(allowed):
            return False
    return True  # Unknown metadata is not proof that a contact is irrelevant.


def _same_snapshot(actual, expected):
    _need(isinstance(actual, dict) and all(k in actual and k in expected for k in SNAPSHOT_FIELDS)
          and _snapshot_equal(actual, expected), "相关来源不是同一完整快照")


def _database_rule_group(record):
    """Return the database attenuation group behind an elemental receipt.

    Elemental compilation receipts now expose ``rule_group`` as a structured
    status/value pair.  A locally adapted timer/counter rule also keeps its
    effective group there and preserves the source database group separately.
    Contact qualification must compare attack metadata with that database
    baseline, not with the derived runtime group.
    """
    current = record.get("rule_group", record.get("attenuation_group"))
    database = record.get("database_rule_group")
    if database is not None:
        _need(isinstance(current, dict) and current.get("status") == "adapter_derived"
              and isinstance(current.get("group"), str) and current["group"],
              "附着适配回执缺少有效规则组身份")
        _need(isinstance(database, str) and database, "附着适配回执缺少数据库基线规则组")
        return database
    if isinstance(current, dict):
        _need(current.get("status") == "database_confirmed"
              and isinstance(current.get("group"), str) and current["group"],
              "附着回执的数据库规则组状态无效")
        return current["group"]
    # Detached receipts created before the structured status field remain
    # replayable under the same exact compilation fingerprint checks.
    _need(isinstance(current, str) and current, "附着回执缺少数据库规则组")
    return current


def _parameter(record, hit, actor, snapshot):
    _same_snapshot(record.get("snapshot"), snapshot)
    _need(record.get("status") == "ready" and record.get("actor") == actor
          and record.get("compiler") == "database-hit-parameter-v1"
          and record.get("compilation_fingerprint") == fingerprint({k: v for k, v in record.items() if k != "compilation_fingerprint"}),
          "父参数凭证未就绪或与物化内容不一致")
    scalar, source, index = record["scalar_projection"], record["source"], record["param_index"]
    value = scalar.get("value")
    _need(type(index) is int and 0 <= index <= 63 and type(value) in (int, float) and math.isfinite(value)
          and scalar.get("status") == "ready" and value == record.get("multiplier") == hit.get("multiplier")
          and record.get("scaling_stat") == hit.get("scaling_stat")
          and source["parameter_pointer"] == source["json_pointer"] + f"/paramList/{index}"
          and source["node_sha256"] == source["definition"]["node_sha256"], "实际倍率或精确参数位置不一致")
    _need(isinstance(record.get("resolved_selection"), dict) and isinstance(record.get("level"), dict), "父参数缺少精确技能选择/等级")


def _metadata(record, dispatch, channel, parameter, hit, app, snapshot):
    from genshin_db.runtime_attack_metadata import _semantic_projection

    _same_snapshot(record.get("snapshot"), snapshot)
    query, projection = record["query"], record["projection"]
    candidate = query["selected_candidate"]
    dep = query["semantic_dependency"]
    _need(record["status"] == query["status"] == candidate["status"] == "ready"
          and record["actor"] == hit["character"] and record["prototype_hit_id"] == hit["id"]
          and record["application_profile_id"] == channel["application_profile_id"]
          and query["snapshot"] == record["snapshot"]
          and query["selection"] == parameter["resolved_selection"]
          and candidate["projection"] == projection and candidate["candidate_id"] == record["candidate_id"]
          and candidate["semantic_dependency"] == dep == record["semantic_dependency"]["database"]
          and fingerprint(dep) == query["source_fingerprint"] == candidate["source_fingerprint"] == record["source_fingerprint"]
          and fingerprint(record["semantic_dependency"]) == record["metadata_dependency_fingerprint"]
          and dep["projection"] == _semantic_projection(projection)
          and dispatch["metadata_dependency_fingerprint"] == record["metadata_dependency_fingerprint"]
          and dispatch["metadata_source_fingerprint"] == record["source_fingerprint"], "攻击元信息身份/依赖不一致")
    _need(all(projection[k]["status"] == "ready" for k in ("parameter", "element", "gauge", "attenuation"))
          and projection["parameter"]["source"] == parameter["source"]
          and projection["parameter"]["value"] == parameter["multiplier"]
          and projection["parameter"]["param_index"] == parameter["param_index"]
          and dep["level"] == parameter["level"] and dep["selection"] == parameter["resolved_selection"]
          and projection["element"]["value"] == hit["element"] == app["element"]
          and record["compiled_fields"]["element"] == hit["element"]
          and record["compiled_fields"]["gauge_units"] == projection["gauge"]["value"] == app.get("gauge_units"),
          "攻击元信息与实际参数/附着值不一致")
    attenuation = projection["attenuation"]["profile"]
    _same_snapshot(attenuation["snapshot"], snapshot)
    expected_icd = {"kind": "sequence",
                    "counter_tag": record["compiled_fields"]["counter_tag"],
                    "rule_group": record["compiled_fields"]["rule_group"],
                    "reset_seconds": attenuation["projection"]["reset_cycle_seconds_rounded"],
                    "sequence": attenuation["projection"]["durability_sequence"]}
    _need(attenuation["execution"]["status"] == "ready" and app.get("icd") == expected_icd,
          "实际附着序列与元信息来源不同")


def _classification(metadata, hit):
    result = {"basis": "declared_profile", "source_proven": False, "sources": []}
    if metadata is None:
        return result
    # Use the existing commit-bound local mapping, not an E slot or actor name.
    from genshin_db.runtime_attack_metadata import _recipe
    from genshin_db.runtime_catalog import resolve_pointer

    query, recipe = metadata["query"], _recipe()
    dep, candidate = query["semantic_dependency"], query["selected_candidate"]
    _need(query["snapshot"]["upstream_commit"] == recipe["commit"]
          and dep["recipe"]["fingerprint"] == recipe["recipe_fingerprint"], "攻击分类的局部字段映射不匹配")
    ability = dep["ability"]
    node = _one(query["source_graph"], "source", ability)
    _need(fingerprint(node["raw"]) == ability["node_sha256"], "攻击完整父节点指纹不一致")
    parent = candidate["parent"]
    base = ability["json_pointer"]
    _need(dep["parent_chain"] == [parent] and parent["json_pointer"].startswith(base + "/")
          and all(parent[k] == ability[k] for k in ("owner_definition_id", "source_path", "source_sha256"))
          and fingerprint(resolve_pointer(node["raw"], parent["json_pointer"][len(base):])) == parent["node_sha256"],
          "攻击完整选中父结构与来源链不一致")
    tags, sources = [], []
    tag_keys = {"attackTag", *(k for k, v in recipe["fields"].items() if v == "attackTag")}
    branches = candidate["branches"]
    _need(branches and [b["source"] for b in branches] == dep["branches"], "攻击分支来源不完整")
    for branch in branches:
        origin = branch["source"]
        base, pointer = ability["json_pointer"], origin["json_pointer"]
        _need(all(origin[k] == ability[k] for k in ("owner_definition_id", "source_path", "source_sha256"))
              and pointer.startswith(base + "/"), "攻击分支不属于完整父节点")
        relative = pointer[len(base):]
        _need(resolve_pointer(node["raw"], relative) == branch["raw"]
              and fingerprint(branch["raw"]) == origin["node_sha256"], "攻击分支原文不一致")
        info = resolve_pointer(node["raw"], relative.rsplit("/", 1)[0])
        keys = tag_keys.intersection(info)
        if len(keys) != 1:
            return result
        key = next(iter(keys))
        tags.append(info[key])
        sources.append({**{k: origin[k] for k in ("source_path", "source_sha256")},
                        "json_pointer": pointer.rsplit("/", 1)[0] + "/" + key,
                        "node_sha256": fingerprint(info[key]), "raw": info[key]})
    kinds = [TAG_TYPES.get(tag) for tag in tags]
    if kinds and all(kind == kinds[0] and kind is not None for kind in kinds):
        result = {"basis": "attack_tag_projection", "source_proven": hit.get("damage_type_ids") == [kinds[0]],
                  "damage_type_id": kinds[0], "sources": sources}
    return result


def _related_channel(scope, hit, ability, profile, channel, metadata_records, periodic_records, cache):
    # A user's damage tag is not evidence that an otherwise relevant source
    # contact is outside a raw-tag-based callback. Test independent dimensions
    # first so unrelated physical contacts need no unused metadata proof.
    if not _possible({**scope, "damage_type_ids": []}, hit, ability):
        return False
    if not scope["damage_type_ids"]:
        return True
    key = (profile["id"], channel["channel_id"])
    if key in cache:
        proven_kind = cache[key]
        return proven_kind is None or proven_kind in scope["damage_type_ids"]
    proven_kind = None
    if profile["kind"] == SINGLE:
        records = [r for r in metadata_records if r.get("action_event_profile_id") == profile["id"]]
        if len(records) == 1 and records[0].get("status") == "ready":
            proven_kind = _classification(records[0], hit).get("damage_type_id")
    elif profile["kind"] == MULTI:
        records = [r for r in periodic_records if r.get("action_event_profile_id") == profile["id"]]
        if len(records) == 1 and records[0].get("status") == "ready":
            _verify_periodic_sources(records)
            source_channel = _one(records[0]["channels"], "channel_id", channel["channel_id"])
            proven_kind = source_channel["projection"]["classification"]["value"]
    cache[key] = proven_kind
    return proven_kind is None or proven_kind in scope["damage_type_ids"]


def _single_pattern(profile, item, target, action, plan):
    pattern = {"id": item["pattern_id"], "prototype_hit_id": profile["prototype_hit_id"],
               "action_id": item["action_id"], "offsets": [profile["calibration"]["offset_seconds"]],
               "orders": [item["order"]], "application": {"profile_id": profile["application_profile_id"],
               "target_id": target, "landed": item["landed"]},
               "contact": {"kind": "profile", "profile_id": profile["contact_profile_id"]}}
    if profile["self_sampling"] == "action_start":
        pattern["self_snapshot_event"] = action["id"]
    elif profile["self_sampling"] == "action_capture":
        from .action_capture import prepare_action_captures, resolve_action_capture
        captures, _ = prepare_action_captures([action], plan["rotation_model"], plan["mechanics_model"])
        capture = resolve_action_capture(action, item["expected_time_ticks"], item["order"], captures)
        _need(item.get("self_snapshot_event") == capture["id"]
              and item.get("snapshot_capture_fingerprint") == fingerprint(capture), "父取样引用不一致")
        pattern["self_snapshot_event"] = capture["id"]
    else:
        _need(profile["self_sampling"] == "current", "未知父取样方式")
    return pattern


def _build(plan, template, *, actor, source_snapshot, scope, action_records, parameter_records,
           metadata_records, periodic_records, elemental_records, reaction_records):
    _need(set(scope) == {"action_type_ids", "damage_type_ids", "elements"}
          and all(isinstance(v, list) and all(isinstance(x, str) and x for x in v) for v in scope.values()), "命中范围形状无效")
    model, rotation = plan["mechanics_model"], plan["rotation_model"]
    elemental = model.get("elemental_model") or {}
    target = (elemental.get("target") or {}).get("id")
    _need(isinstance(target, str) and bool(target), "相关接触需要明确目标")
    hits, actions, abilities = _index(template["hits"]), _index(rotation["actions"]), _index(rotation["abilities"])
    apps, contacts = _index(elemental.get("profiles", [])), _index(elemental.get("contact_profiles", []))
    patterns = _index(model.get("hit_patterns", []))
    _index(plan.get("action_event_profiles", []))
    parents, deps, admitted = [], [], {}
    seen_hits, bytes_used, classification_cache = set(), 0, {}

    def reserve(value):
        nonlocal bytes_used
        bytes_used += len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) + 1
        _need(bytes_used <= MAX_GRAPH_BYTES, "父接触图超过字节预算")

    for profile in plan.get("action_event_profiles", []):
        kind, pid = profile["kind"], profile["id"]
        channels = profile.get("channels", []) if kind == MULTI else [{**profile, "channel_id": None}]
        binding_ids = sorted(b["ability_id"] for b in plan.get("action_event_bindings", []) if b["profile_id"] == pid)
        selected = sorted(
            (a for a in actions.values() if a["ability_id"] in binding_ids),
            key=lambda a: (_ticks(a["start"]), a["order"], a["id"]),
        )
        relevant = [c for c in channels if hits.get(c.get("prototype_hit_id"), {}).get("character", actor) == actor
                    and any(abilities.get(a["ability_id"], {}).get("character") == actor
                            and _related_channel(scope, hits.get(c.get("prototype_hit_id"), {}), abilities[a["ability_id"]],
                                                 profile, c, metadata_records, periodic_records, classification_cache) for a in selected)]
        if not relevant:
            continue
        _need(kind in (SINGLE, MULTI), "相关派发档案种类尚未支持")
        record = _one(action_records, "profile_id", pid)
        _need(record.get("status") == "ready" and record.get("actor") == actor and record.get("kind") == kind
              and record.get("ability_ids") == binding_ids and len(binding_ids) == len(set(binding_ids))
              and bool(record.get("source_dependency_fingerprint")) and bool(record.get("profile_fingerprint"))
              and profile.get("accepted_profile_fingerprint") == record["profile_fingerprint"] == record.get("accepted_profile_fingerprint"),
              "相关父档案缺少已接受的完整就绪凭证")
        generated = record.get("generated") or []
        relevant_ids = {c["channel_id"] for c in relevant}
        _need(generated and len(generated) <= MAX_DISPATCH_CONTACTS, "父派发为空或超过既有派发预算")
        _need(len(parents) + sum(g.get("channel_id") in relevant_ids for g in generated) <= MAX_PARENTS,
              "相关父接触数量超过预算")
        periodic, captures = None, {}
        if kind == MULTI:
            periodic = _one(periodic_records, "action_event_profile_id", pid)
            _verify_periodic_sources([periodic])
            native_source = _one(plan.get("periodic_attack_sources", []), "action_event_profile_id", pid)
            _need(all(periodic[k] == native_source[k] for k in ("version_line", "selection", "origin_reference_id"))
                  and periodic["semantic_dependency"]["interpretation"] ==
                  {k: native_source[k] for k in ("purpose", "status", "source")}
                  and {c["channel_id"]: (c["candidate_id"], c.get("counter_tag") or c.get("shared_group"))
                       for c in native_source["channels"]} ==
                  {c["channel_id"]: (c["candidate_id"], c["compiled_fields"].get("counter_tag", c["compiled_fields"].get("shared_group")))
                       for c in periodic["channels"]},
                  "周期来源与原生来源声明不一致")
            _same_snapshot(periodic["snapshot"], source_snapshot)
            _need(periodic["actor"] == actor and record["channels"] == profile["channels"]
                  and record["target_id"] == target and fingerprint(record["scope"]) == record["profile_fingerprint"]
                  and record["scope"]["dependencies"] == periodic["semantic_dependency"]
                  and record["source_dependency_fingerprint"] == periodic["periodic_dependency_fingerprint"]
                  and record["scope"]["variant"] == profile["variant"]
                  and record["scope"]["lifecycle"] == profile["lifecycle"], "周期来源与接受语义不一致")
            overrides = _index(plan.get("action_event_overrides", []), "action_id")
            refresh_declarations = [
                row for row in plan.get("periodic_refresh_triggers", [])
                if _refresh_targets_profile(row, pid, binding_ids)
            ]
            instance = periodic_instance_payload(
                profile=profile, record=record, selected=selected,
                overrides=overrides, period=rotation["period"],
                refresh_declarations=refresh_declarations,
            )
            _need(record["instance_payload"] == instance and fingerprint(instance) == record["instance_fingerprint"], "周期实例与实际动作不一致")
            if any(c["self_sampling"] == "action_capture" for c in channels):
                from .action_capture import prepare_action_captures
                captures, calibrations = prepare_action_captures(selected, rotation, model)
                _need(record["scope"].get("snapshot_capture_calibrations") == calibrations, "周期父取样校准与接受语义不一致")
            expected_patterns = _index(patterns_for_record(record))
        else:
            _need(record["prototype_hit_id"] == profile["prototype_hit_id"], "单接触原型不一致")
            instance = {"profile_fingerprint": record["profile_fingerprint"], "generated": generated,
                        "actions": [actions[g["action_id"]] for g in generated], "period": rotation["period"]}
            _need(fingerprint(instance) == record["instance_fingerprint"], "单接触实例与实际动作不一致")
            expected_patterns = {g["pattern_id"]: _single_pattern(profile, g, target, actions[g["action_id"]], plan) for g in generated}
        _need(len(expected_patterns) == len(generated), "父派发模式重复")
        if kind == MULTI:
            from .periodic_lifecycle import CONTRACT as LIFECYCLE_CONTRACT

            projection = record["lifecycle_projection"]
            expected_lifecycle = {
                "duration_ticks": (
                    _ticks(profile["lifecycle"]["instance_end_offset_seconds"])
                    - _ticks(profile["lifecycle"]["establishment_offset_seconds"])
                ),
                "refresh_policy": (
                    profile["lifecycle"].get("replacement_policy") or "none"
                ),
                "pause_resume_policy": "none",
                "off_field_policy": "continue",
            }
            expected_summary = {
                "generations": len(projection["generations"]),
                "materialized_occurrences": len(projection["occurrences"]),
                "cancelled_occurrences": len(projection["cancelled"]),
                "deferred_intervals": len(projection["deferred"]),
                "skipped_controls": len(projection["skipped"]),
            }
            _need(
                set(projection) == {
                    "contract", "status", "lifecycle", "occurrences",
                    "generations", "controls", "cancelled", "deferred",
                    "skipped", "summary",
                }
                and projection["contract"] == LIFECYCLE_CONTRACT
                and projection["status"] == "ready"
                and projection["lifecycle"] == expected_lifecycle
                and projection["summary"] == expected_summary,
                "周期生命周期投影回执不完整或与档案不一致",
            )
            projected = projection["occurrences"]
            actual_projection = {
                (g.get("instance_trigger_id", g["action_id"]), g["action_id"],
                 g["channel_id"], g["ordinal"]):
                    (g["expected_time_ticks"], g["order"])
                for g in generated
            }
            expected_projection = {
                (row["trigger_id"], row["root_action_id"],
                 row["channel_id"], row["ordinal"]):
                    (row["time_ticks"], row["order"])
                for row in projected
            }
            actual_pairs = set(actual_projection)
            expected_pairs = set(expected_projection)
            _need(actual_projection == expected_projection,
                  "周期生命周期投影与实际命中时点/order不一致")
        else:
            actual_pairs = {
                (g["action_id"], g.get("channel_id"), g.get("ordinal", 1))
                for g in generated
            }
            expected_pairs = {
                (a["id"], channel["channel_id"], 1)
                for channel in channels for a in selected
            }
        _need(len(generated) <= MAX_DISPATCH_CONTACTS
              and len(generated) == len(actual_pairs)
              and actual_pairs == expected_pairs,
              "实际动作或通道/序号未完整覆盖")
        for channel in relevant:
            cid, proto = channel["channel_id"], channel["prototype_hit_id"]
            hit = hits[proto]
            raw = _one(plan["manual_template"]["hits"], "id", proto)
            _need(all(hit.get(k, 1 if k == "count" else None) == raw.get(k, 1 if k == "count" else None)
                      for k in ("character", "damage_type_ids", "damage_system", "scaling_stat", "count")),
                  "实际原型与接受的原生形状不一致")
            parameter = _one(parameter_records, "hit_id", proto)
            _parameter(parameter, hit, actor, source_snapshot)
            app, contact = apps[channel["application_profile_id"]], contacts[channel["contact_profile_id"]]
            _need(app.get("status") != "pending" and app.get("application") in ("apply", "none")
                  and contact.get("status") != "pending" and contact.get("freeze_interaction") == "none", "相关附着或接触语义未知")
            metadata_matches = [r for r in metadata_records if r.get("action_event_profile_id") == pid]
            _need(len(metadata_matches) <= 1, "元信息凭证重复")
            metadata = metadata_matches[0] if metadata_matches else None
            if kind == SINGLE:
                _need(metadata is not None, "相关单接触需要完整攻击元信息，手工元素不能代替来源")
                native_source = _one(plan.get("attack_metadata_sources", []), "action_event_profile_id", pid)
                raw_mode = native_source.get("application_mode")
                mode = "pending" if raw_mode in (None, "unknown") else raw_mode
                expected_interpretation = {k: copy.deepcopy(native_source[k]) for k in ("purpose", "status", "source")}
                expected_interpretation["application_mode"] = mode
                if mode == "sequence":
                    expected_interpretation["counter_tag"] = native_source.get("counter_tag") or native_source.get("shared_group")
                if "verification" in native_source:
                    expected_interpretation["verification"] = copy.deepcopy(native_source["verification"])
                if "shared_group" in native_source:
                    expected_interpretation["legacy_counter_tag_field"] = "shared_group"
                _need(all(metadata[k] == native_source[k] for k in ("version_line", "selection", "candidate_id"))
                      and metadata["semantic_dependency"]["interpretation"] == expected_interpretation,
                      "攻击元信息与原生来源声明不一致")
            classification = {"basis": "declared_profile", "source_proven": False, "sources": []}
            if metadata:
                _metadata(metadata, record, channel, parameter, hit, app, source_snapshot)
                classification = _classification(metadata, hit)
                _need("damage_type_id" not in classification or classification["source_proven"],
                      "原文攻击类别与实际声明不同，不能以手填标签排除相关接触")
            if kind == SINGLE:
                elemental_record = _one(elemental_records, "profile_id", app["id"])
                attenuation = metadata["projection"]["attenuation"]["profile"]
                _same_snapshot(elemental_record.get("snapshot"), source_snapshot)
                _need(elemental_record.get("status") == "ready"
                      and elemental_record.get("compiler") == "database-icd-v1"
                      and elemental_record.get("compilation_fingerprint") == fingerprint(
                          {k: v for k, v in elemental_record.items() if k != "compilation_fingerprint"})
                      and all(elemental_record[k] == attenuation[k] for k in ("source", "snapshot", "projection", "execution"))
                      and elemental_record["version_line"] == native_source["version_line"]
                      and _database_rule_group(elemental_record) == metadata["compiled_fields"].get(
                          "database_rule_group", metadata["compiled_fields"]["rule_group"])
                      and elemental_record.get("counter_tag", elemental_record.get("shared_group")) == (
                          native_source.get("counter_tag") or native_source.get("shared_group"))
                      and elemental_record["binding_basis"] == native_source["source"]
                      and elemental_record["compiled_icd"] == app["icd"], "单接触缺完整一致的数据库附着来源")
                declaration = {"param_index": parameter["param_index"],
                               **{k: native_source[k] for k in ("purpose", "status", "source")}}
                _need(parameter["declared_purpose"] == declaration["purpose"]
                      and parameter["interpretation_status"] == declaration["status"]
                      and parameter["interpretation_basis"] == declaration["source"]
                      and raw.get("element", hit["element"]) == hit["element"], "单接触参数用途或元素与原生声明不一致")
                # Metadata owns element in the private compose copy; native
                # prototypes omit it. Recreate that same single owned field.
                single_scope, captures = build_single_action_scope(profile,
                    raw_hit={**raw, "element": hit["element"]}, hit=hit, parameter=parameter,
                    declaration=declaration, application=app, contact=contact,
                    metadata=metadata, elemental_record=elemental_record,
                    reaction_compilations=reaction_records, selected_actions=selected,
                    rotation=rotation, model=model)
                _need(fingerprint(single_scope) == record["profile_fingerprint"]
                      and fingerprint(single_scope["dependencies"]) == record["source_dependency_fingerprint"],
                      "单接触完整来源或校准语义与已接受指纹不一致")
            if periodic:
                row = _one(periodic["channels"], "channel_id", cid)
                unit = _one(record["scope"]["channels"], "candidate_id", row["candidate_id"])
                semantic_hit = {k: hit.get(k, 1 if k == "count" else None) for k in
                                ("damage_type_ids", "damage_system", "element", "scaling_stat", "count", "multiplier")}
                expected_parameter = {**_source_identity(parameter), "selection": parameter["resolved_selection"], "level": 10,
                    "param_index": parameter["param_index"], "purpose": parameter["declared_purpose"],
                    "interpretation_status": parameter["interpretation_status"], "interpretation_basis": parameter["interpretation_basis"]}
                _need(row["prototype_hit_id"] == proto and row["application_profile_id"] == app["id"]
                      and periodic["query"]["selection"] == parameter["resolved_selection"]
                      and unit["prototype"] == semantic_hit and unit["parameter"] == expected_parameter
                      and unit["application"] == _without_id(app) and unit["contact"] == _without_id(contact)
                      and all(unit[k] == channel[k] for k in ("schedule", "default_landed", "self_sampling"))
                      and row["projection"]["parameter"]["source"] == parameter["source"], "周期通道语义配对与实际来源不一致")
                classification = {"basis": "periodic_source_projection", "source_proven": True,
                                  "damage_type_id": row["projection"]["classification"]["value"],
                                  "sources": [copy.deepcopy(row["projection"]["classification"])]}
                _need(hit.get("damage_type_ids") == [classification["damage_type_id"]], "周期攻击分类与实际原型不同")
            dep = {"profile_id": pid, "profile_kind": kind, "channel_id": cid, "prototype_hit_id": proto,
                "profile_fingerprint": record["profile_fingerprint"], "instance_fingerprint": record["instance_fingerprint"],
                "source_dependency_fingerprint": record["source_dependency_fingerprint"],
                "selection": parameter["resolved_selection"], "level": parameter["level"],
                "damage_classification": classification,
                "parameter": {k: parameter[k] for k in ("param_index", "source", "entry_sources", "compilation_fingerprint")},
                "metadata_source_fingerprint": (metadata or {}).get("source_fingerprint"),
                "metadata_dependency_fingerprint": (metadata or {}).get("metadata_dependency_fingerprint"),
                "periodic_source_fingerprint": (periodic or {}).get("source_fingerprint"),
                "periodic_dependency_fingerprint": (periodic or {}).get("periodic_dependency_fingerprint")}
            reserve(dep)
            deps.append(copy.deepcopy(dep))
            for item in generated:
                if item.get("channel_id") != cid:
                    continue
                action, ab = actions[item["action_id"]], abilities[item["ability_id"]]
                pattern = patterns[item["pattern_id"]]
                _need(action["ability_id"] == ab["id"] and ab["character"] == actor
                      and ab["id"] in binding_ids and ab.get("variant") == profile["variant"]
                      and ab["action_type_id"] in ("action.elemental_skill", "action.elemental_burst")
                      and action["repeat"] == {"mode": "once"}, "父动作能力、变体或重复身份不一致")
                _need(item["prototype_hit_id"] == proto and type(item["landed"]) is bool
                      and type(item["order"]) is int and type(pattern.get("report", True)) is bool
                      and {k: v for k, v in pattern.items() if k != "report"} == expected_patterns[pattern["id"]], "父派发与实际命中模式不一致")
                ordinal = item.get("ordinal", 1)
                emission = [kind, action["id"], cid, ordinal] if kind == MULTI else [kind, action["id"], "primary-contact"]
                _need(item["pattern_id"] == PREFIX + fingerprint(emission) and item["hit_id"] == pattern["id"] + ":1"
                      and item["hit_id"] not in seen_hits, "实际父事件身份重复或与派发协议不一致")
                start, end, tick = _ticks(action["start"]), _ticks(action["occupation_end"]), _ticks(item["t"])
                _need(start <= end and start <= tick < _ticks(rotation["period"])
                      and type(item["offset_ticks"]) is int and type(item["expected_time_ticks"]) is int
                      and tick == item["expected_time_ticks"] == start + item["offset_ticks"]
                      and _ticks(pattern["offsets"][0]) == item["offset_ticks"], "父接触时点与实际偏移不一致")
                if kind == MULTI:
                    schedule = channel["schedule"]
                    offset = _ticks(schedule["first_offset_seconds"]) + (ordinal - 1) * row["projection"]["timer"]["ticks"] if schedule["kind"] == "periodic" else _ticks(schedule["offset_seconds"])
                    _need(item["offset_ticks"] == offset, "周期原文间隔与校准路由不一致")
                    if channel["self_sampling"] == "action_capture":
                        from .action_capture import resolve_action_capture
                        capture = resolve_action_capture(action, tick, item["order"], captures)
                        _need(item.get("self_snapshot_event") == capture["id"]
                              and item.get("snapshot_capture_fingerprint") == fingerprint(capture), "周期父取样引用不一致")
                seen_hits.add(item["hit_id"])
                admitted[pattern["id"]] = fingerprint(pattern)
                _need(len(parents) < MAX_PARENTS, "父接触数量超过预算")
                parent = {**item, "profile_id": pid, "profile_kind": kind, "channel_id": cid, "ordinal": ordinal,
                    "action_tick": start, "occupation_end_tick": end, "absolute_tick": tick, "action_order": action["order"],
                    "prototype": hit, "application": app, "contact": contact, "report": pattern.get("report", True),
                    "pattern_fingerprint": fingerprint(pattern), "dependency_fingerprint": fingerprint(dep),
                    "damage_classification": classification}
                reserve(parent)
                parents.append(copy.deepcopy(parent))
    for field in ("hit_events", "hit_patterns", "application_events"):
        for row in model.get(field, []):
            action = actions.get(row.get("action_id"), {})
            ab = abilities.get(action.get("ability_id"), {})
            hit = hits.get(row.get("hit_id", row.get("prototype_hit_id")), {})
            if hit.get("character", ab.get("character")) != actor or not _possible(scope, hit, ab):
                continue
            if field == "application_events":
                contact = row.get("contact", {})
                known = contact.get("kind") == "none" and contact.get("status") in ("assumed", "confirmed")
                if contact.get("kind") == "profile":
                    ref = contacts.get(contact.get("profile_id"), {})
                    known = ref.get("status") in ("assumed", "confirmed") and ref.get("freeze_interaction") == "none"
                _need(known, "独立附着存在未知接触，不能当作已认证命中")
                continue  # An application is not a hit, including successful applications.
            _need(field == "hit_patterns" and admitted.get(row["id"]) == fingerprint(row), "整轴存在相关手工旁路或未认证通道：" + field)
    _need(parents, "没有满足范围的实际父接触")
    return {"status": "ready", "verification_scope": "fresh_receipt_contact_relations",
            "execution_status": "not_evaluated", "actor": actor, "target_id": target,
            "parents": sorted(parents, key=lambda r: (r["absolute_tick"], r["order"], r["hit_id"])),
            "dependencies": sorted(deps, key=lambda r: (r["profile_id"], r["channel_id"] or "")), "issues": []}


def build_mechanism_contact_graph(plan, template, *, actor, source_snapshot, scope, action_records,
                                  parameter_records, metadata_records, periodic_records,
                                  elemental_records=(), reaction_records=()):
    """All-or-nothing bounded graph; does not mutate inputs or authorize execution."""
    try:
        result = _build(plan, template, actor=actor, source_snapshot=source_snapshot, scope=scope,
                        action_records=action_records, parameter_records=parameter_records,
                        metadata_records=metadata_records, periodic_records=periodic_records,
                        elemental_records=elemental_records, reaction_records=reaction_records)
        result["graph_fingerprint"] = fingerprint(result)
        _need(len(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) <= MAX_GRAPH_BYTES,
              "完整父接触图超过字节预算")
        return result
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        elemental = (plan.get("mechanics_model") or {}).get("elemental_model") or {}
        return {"status": "pending", "verification_scope": "fresh_receipt_contact_relations",
                "execution_status": "not_evaluated", "actor": actor,
                "target_id": (elemental.get("target") or {}).get("id"),
                "parents": [], "dependencies": [], "graph_fingerprint": None,
                "issues": [{"code": "mechanism_contact_pending", "message": str(exc)[:1024]}]}
