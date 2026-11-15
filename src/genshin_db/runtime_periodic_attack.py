"""Finite parameter-linked Gadget attacks; action creation remains unproven."""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from importlib import resources

from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    dumps,
    escape,
    fingerprint,
    resolve_pointer,
)
from genshin_db.runtime_gadget_source import _Reader
from genshin_db.validation import QueryValidationError
from genshin_runtime.normalization import Profile
from genshin_runtime.periodic_attack_identity import (
    periodic_candidate_identity,
    periodic_projection_payload,
    periodic_source_fingerprint,
    periodic_source_payload,
)

PERIODIC_LIMITS = {"max_bytes": 524288, "max_node_bytes": 262144, "max_candidates": 32,
    "max_selected": 8, "max_parameter_targets": 32, "max_configs": 32, "max_gadgets": 32,
    "max_origins": 32, "max_source_nodes": 64, "max_edges": 256, "max_issues": 128,
    "max_tree_nodes": 8192, "max_depth": 64, "max_cached_selections": 32}
_CACHE = "periodic-attack-fresh-v1"
_ATTACK_TAG_CLASSIFICATION = {
    "Elemental_Art": "damage.elemental_skill",
    "Elemental_Burst": "damage.elemental_burst",
    "Normal_Attack": "damage.normal_attack",
    "Charged_Attack": "damage.charged_attack",
    "Plunging_Attack": "damage.plunging_attack",
}


@dataclass(frozen=True)
class PeriodicAttackContext:
    public_projection: dict
    parameter_context: dict
    gadget_profile: dict | None
    attenuation_profiles: dict
    selected_parameter_projections: dict


@lru_cache(maxsize=1)
def _recipe():
    value = json.loads(resources.files("genshin_runtime").joinpath("resources/periodic_attack_profiles.json").read_text("utf8"))
    value["recipe_fingerprint"] = fingerprint(value)
    return value


def periodic_attack_capabilities():
    recipe = _recipe()
    return {"projection_version": 1, "recipe_id": recipe["recipe_id"], "recipe_version": recipe["recipe_version"],
        "recipe_fingerprint": recipe["recipe_fingerprint"], "limits": dict(PERIODIC_LIMITS),
        "modes": ["origin_discovery", "channel_discovery", "selected"],
        "scope": "精确命座与1–15级有效天赋等级；精确参数目标和Gadget挂载交集；不自动选择来源",
        "channel_kinds": ["periodic_callback_attack", "ability_start_attack"],
        "whole_source_status": "partial", "action_creation_association": "unproven",
        "dispatch_semantics_status": "not_proven", "execution_support": "not_evaluated",
        "shared_identity": "not_inferred", "timer": "正数精确微秒；不推出首次/次数/末次或终结启动"}


def _limit(kind):
    raise RuntimeConfigError("周期来源超过完整查询预算，未截断原文或候选", "response_too_large", 413, {"limit_kind": kind})


def _invalid(message):
    raise RuntimeConfigError(message, "dataset_invalid", 500)


def _selection(selection, origin, candidates):
    from genshin_db.runtime_queries import _identifier, _integer
    from genshin_db.validation import version_line
    allowed = {"character_id", "slot", "skill_id", "skill_depot_id", "talent_level", "constellation", "version_line", "snapshot_id"}
    if selection.keys() - allowed or "character_id" not in selection:
        raise QueryValidationError("非法周期来源选择字段")
    args = dict(selection)
    _integer(args["character_id"], "character_id")
    if (args.get("slot") is None) == (args.get("skill_id") is None):
        raise QueryValidationError("slot与skill_id必须且只能提供一个")
    if args.get("slot") is not None and args["slot"] not in ("normal", "skill", "burst"):
        raise QueryValidationError("slot非法")
    for key in ("skill_id", "skill_depot_id"):
        if args.get(key) is not None:
            _integer(args[key], key)
    if args.get("talent_level") is not None:
        _integer(args["talent_level"], "talent_level", 1, 15)
    _integer(args.setdefault("constellation", 0), "constellation", 0, 6)
    version_line(args.setdefault("version_line", "release"))
    if args.get("snapshot_id") is not None:
        _identifier(args["snapshot_id"], "snapshot_id")
    if origin is not None and (not isinstance(origin, str) or not re.fullmatch(r"[0-9a-f]{64}", origin)):
        raise QueryValidationError("origin_reference_id必须为64位小写hex")
    if candidates is not None:
        if origin is None or not isinstance(candidates, list) or not 1 <= len(candidates) <= PERIODIC_LIMITS["max_selected"]:
            raise QueryValidationError("候选选择需要origin和1–8个ID")
        for value in candidates:
            _identifier(value, "candidate_id")
        if len(candidates) != len(set(candidates)):
            raise QueryValidationError("candidate_ids重复")
    return args


def _walk(value):
    stack, count = [("", value, 0)], 0
    while stack:
        pointer, node, depth = stack.pop()
        count += 1
        if count > PERIODIC_LIMITS["max_tree_nodes"] or depth > PERIODIC_LIMITS["max_depth"]:
            _limit("tree")
        yield pointer, node
        if isinstance(node, dict):
            stack.extend((pointer + "/" + escape(k), v, depth + 1) for k, v in node.items())
        elif isinstance(node, list):
            stack.extend((pointer + "/" + str(i), v, depth + 1) for i, v in enumerate(node))


def _number(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value)) and 0 <= value <= 10**15


class _PeriodicReader(_Reader):
    def __init__(self, conn, state):
        super().__init__(conn, state)
        self.visited = {k: set() for k in ("parameter_targets", "configs", "gadgets", "origins")}
        self.all_edges, self.blocked = {}, []

    def visit(self, kind, ident):
        if not isinstance(ident, str) or not ident:
            _invalid("周期图身份缺失或类型错误")
        self.visited[kind].add(ident)
        if len(self.visited[kind]) > PERIODIC_LIMITS["max_" + kind]:
            _limit(kind)

    def entry(self, row, key="payload"):
        if row["definition_id"] not in self.entries and len(self.entries) >= PERIODIC_LIMITS["max_source_nodes"]:
            _limit("source_nodes")
        value = super().entry(row, key)
        if len(dumps(value).encode("utf8")) > PERIODIC_LIMITS["max_node_bytes"]:
            _limit("node_bytes")
        return value

    def edge(self, ref, scope="object"):
        ident = (scope, ref["reference_id"])
        self.all_edges[ident] = ref
        if len(self.all_edges) > PERIODIC_LIMITS["max_edges"]:
            _limit("edges")

    def object_refs(self, row):
        remaining = PERIODIC_LIMITS["max_edges"] - len(self.all_edges)
        # Count all unseen IDs before any parent raw is loaded. A prefix made of
        # previously seen edges must not hide unbounded unseen siblings.
        seen = [key[1] for key in self.all_edges if key[0] == "object"]
        exclusion = " AND reference_id NOT IN (" + ",".join("?" for _ in seen) + ")" if seen else ""
        ids = self.conn.execute("SELECT reference_id FROM runtime_object_reference WHERE version_line=? AND from_definition_id=? LIMIT ?",
            (self.line, row["definition_id"], PERIODIC_LIMITS["max_edges"] + 1)).fetchall()
        unseen = self.conn.execute("SELECT reference_id FROM runtime_object_reference WHERE version_line=? AND from_definition_id=?" + exclusion + " LIMIT ?",
            (self.line, row["definition_id"], *seen, remaining + 1)).fetchall()
        if len(ids) > PERIODIC_LIMITS["max_edges"] or len(unseen) > remaining:
            _limit("edges")
        refs = super().object_refs(row)
        for ref in refs:
            self.edge(ref)
        return refs

    def block(self, stage, ref, reason=None):
        value = {"stage": stage, "reference_id": ref.get("reference_id"), "json_pointer": ref.get("json_pointer"),
            "from_definition_id": ref.get("from_definition_id"), "reason": reason or ref.get("resolution_status"),
            "candidate_ids": copy.deepcopy(ref.get("candidate_ids", []))}
        if value not in self.blocked:
            self.blocked.append(value)
        if len(self.blocked) > PERIODIC_LIMITS["max_issues"]:
            _limit("diagnostics")

    def legacy_refs(self, ident):
        from genshin_db.runtime_queries import _references
        ids = self.conn.execute("SELECT reference_id FROM runtime_reference WHERE version_line=? AND from_definition_id=? LIMIT ?",
            (self.line, ident, PERIODIC_LIMITS["max_edges"] + 1)).fetchall()
        remaining = PERIODIC_LIMITS["max_edges"] - len(self.all_edges)
        if len(ids) > PERIODIC_LIMITS["max_edges"] or sum(("legacy", r[0]) not in self.all_edges for r in ids) > remaining:
            _limit("edges")
        refs = _references(self.conn, self.line, ident, self.nodes)
        for ref in refs:
            self.edge(ref, "legacy")
        return refs

    def incoming(self, row, relation):
        from genshin_db.runtime_queries import _row
        remaining = PERIODIC_LIMITS["max_edges"] - len(self.all_edges)
        stage = {"gadget_ability_mount": "configs", "gadget_config_name": "gadgets", "gadget_id_reference": "origins"}[relation]
        field = "legacy_reference_id" if stage == "origins" else "from_definition_id"
        seen = sorted(self.visited[stage])
        exclusion = " AND " + field + " NOT IN (" + ",".join("?" for _ in seen) + ")" if seen else ""
        left = PERIODIC_LIMITS["max_" + stage] - len(seen)
        upcoming = self.conn.execute("SELECT DISTINCT " + field + " FROM runtime_object_reference WHERE version_line=? AND target_kind=? AND target_key_json=? AND relation_kind=?" + exclusion + " LIMIT ?",
            (self.line, row["definition_kind"], row["exact_key_json"], relation, *seen, left + 1)).fetchall()
        if len(upcoming) > left:
            _limit(stage)
        for ident in upcoming:
            self.visit(stage, ident[0])
        records = self.conn.execute("SELECT * FROM runtime_object_reference WHERE version_line=? AND target_kind=? AND target_key_json=? AND relation_kind=? ORDER BY reference_id LIMIT ?",
            (self.line, row["definition_kind"], row["exact_key_json"], relation, PERIODIC_LIMITS["max_edges"] + 1)).fetchall()
        if len(records) > PERIODIC_LIMITS["max_edges"] or sum(("object", r["reference_id"]) not in self.all_edges for r in records) > remaining:
            _limit("edges")
        result = []
        for stored in records:
            parent = _row(self.conn, self.line, stored["from_definition_id"])
            refs = self.object_refs(parent)
            ref = next((r for r in refs if r["reference_id"] == stored["reference_id"]), None)
            if ref is None:
                _invalid("反向关系与原文重建不一致")
            kind = {"gadget_ability_mount": "parameter_targets", "gadget_config_name": "configs", "gadget_id_reference": "gadgets"}[relation]
            for ident in ref["candidate_ids"]:
                self.visit(kind, ident)
            self.candidates(ref, row["definition_kind"])  # also reconstruct every ambiguous target identity
            if ref["resolution_status"] != "resolved":
                self.block(relation, ref)
            elif ref["resolved_definition_id"] == row["definition_id"]:
                result.append((parent, ref))
        return result


def _targets(reader, facts):
    from genshin_db.runtime_queries import _row
    result = {}
    for binding in facts["parameter_bindings"]:
        if binding["role"] != "skill_level":
            continue
        target = binding["target"]
        for ident in target["candidate_ids"]:
            reader.visit("parameter_targets", ident)
            reader.entry(_row(reader.conn, reader.line, ident))
        if target["resolution_status"] != "resolved" or len(target["candidate_ids"]) != 1:
            reader.block("parameter_target", {"json_pointer": binding["operation_pointer"],
                "candidate_ids": target["candidate_ids"], "resolution_status": target["resolution_status"]})
        else:
            result.setdefault(target["candidate_ids"][0], []).append(binding)
    return result


def _discover(reader, targets):
    from genshin_db.runtime_queries import _row
    candidates = {}
    for ident in targets:
        ability = _row(reader.conn, reader.line, ident)
        for config, _mount in reader.incoming(ability, "gadget_ability_mount"):
            reader.visit("configs", config["definition_id"])
            for record, _name in reader.incoming(config, "gadget_config_name"):
                reader.visit("gadgets", record["definition_id"])
                gadget_id = reader.entry(record)["payload"]["id"]
                for parent, creation in reader.incoming(record, "gadget_id_reference"):
                    origin = creation["legacy_reference_id"]
                    reader.visit("origins", origin)
                    reader.legacy_refs(parent["definition_id"])
                    proof = reader.origin(origin, gadget_id, record["definition_id"])
                    row = candidates.setdefault(origin, {"origin_reference_id": origin, "gadget_id": gadget_id,
                        "json_name": reader.entry(record)["payload"].get("jsonName"),
                        "association": "parameter_related_only", "origin": proof,
                        "parameter_target_ids": []})
                    if ident not in row["parameter_target_ids"]:
                        row["parameter_target_ids"].append(ident)
    return sorted(candidates.values(), key=lambda c: c["origin_reference_id"])


def _local(state, reader, recipe):
    if state["upstream_commit"] != recipe["commit"] or reader.nodes.normalization is None:
        return None
    base = reader.nodes.normalization
    return Profile(recipe["recipe_id"], (recipe["commit"],), {**base.fields, **recipe["fields"]}, {**base.types, **recipe["types"]})


def _normalized_shape(view, kind):
    """Replace only verified data fields/identities; all other controls stay exact."""
    value = copy.deepcopy(view)
    if not isinstance(value.get("abilityName"), str):
        raise TypeError("missing ability identity")
    value["abilityName"] = "<ability>"
    specials = value.get("abilitySpecials")
    if not isinstance(specials, dict) or len(specials) != 1:
        raise ValueError("one scalar parameter required")
    symbol, default = next(iter(specials.items()))
    if not _number(default):
        raise ValueError("parameter default unsupported")
    value["abilitySpecials"] = {"<parameter>": "<scalar>"}
    if kind == "periodic_callback_attack":
        mods = value.get("modifiers")
        if not isinstance(mods, dict) or len(mods) != 1:
            raise ValueError("one modifier required")
        name, mod = next(iter(mods.items()))
        added = value.get("onAdded")
        if not isinstance(added, list) or len(added) != 1 or added[0].get("modifierName") != name:
            raise ValueError("modifier entry not proven")
        added[0]["modifierName"] = "<modifier>"
        mod["thinkInterval"] = "<timer>"
        value["modifiers"] = {"<modifier>": mod}
    properties = [(p, v) for p, v in _walk(value) if p.endswith("/attackProperty") and isinstance(v, dict)]
    if len(properties) != 1 or properties[0][1].get("damagePercentage") != symbol:
        raise ValueError("single parameter-linked attack required")
    prop = properties[0][1]
    prop["damagePercentage"] = "<parameter>"
    prop["elementDurability"] = "<gauge>"
    prop["elementType"] = "<element>"
    info = resolve_pointer(value, properties[0][0].rsplit("/", 1)[0])
    info["attenuationGroup"] = "<group>"
    # attenuationTag carries data identity rather than control-tree shape.  Its
    # presence is projected separately and must not make an otherwise reviewed
    # attack component fail the structural comparison.
    info.pop("attenuationTag", None)
    return value


def _origin_admitted(bundle, local, recipe):
    origin = bundle["origin"]
    if origin["parent"]["definition"]["definition_kind"] != "ability":
        return False
    def shape(raw):
        view = local.normalize(raw, kind="ability")
        view["abilityName"] = "<ability>"
        for _, node in _walk(view):
            if isinstance(node, dict) and node.get("$type") == "TriggerBullet":
                if type(node.get("bulletID")) is not int:
                    raise ValueError("invalid bullet identity")
                node["bulletID"] = "<gadget>"
        return view
    template = recipe["evidence"]["contexts"][1]["raw"]
    if shape(origin["parent"]["payload"]) != shape(template):
        return False
    operation = local.normalize(origin["operation"]["raw"], kind="operation")
    sample = local.normalize(recipe["evidence"]["creation_links"][0]["current_operation"], kind="operation")
    operation["bulletID"], sample["bulletID"] = "<gadget>", "<gadget>"
    if operation != sample:
        return False  # conditional sibling creation is never admitted
    selected = bundle["config"]["selected"]
    if selected is None:
        return False
    def gadget_shape(payload):
        value = local.normalize({"entry": payload}, kind="gadget_config")["entry"]
        if not isinstance(value.get("abilities"), list):
            raise TypeError("gadget mount list missing")
        for mount in value["abilities"]:
            if not isinstance(mount, dict) or not isinstance(mount.get("abilityName"), str):
                raise TypeError("gadget mount identity missing")
            mount["abilityName"] = "<ability>"
        return value
    main = recipe["evidence"]["creation_links"][0]["gadget_pointer"].removeprefix("/")
    template = next(g["raw"] for g in recipe["evidence"]["gadgets"] if g["name"] == "gadget:" + main)
    return gadget_shape(selected["payload"]) == gadget_shape(template)


def _source(entry, relative, raw):
    return {"source_path": entry["source_path"], "source_sha256": entry["source_sha256"],
        "json_pointer": entry["json_pointer"] + relative, "node_sha256": fingerprint(raw),
        "definition": copy.deepcopy(entry["definition"])}


def _project_channel(entry, mount, bindings, context, local, recipe, bundle, attenuation):
    from genshin_db.runtime_action_parameter import project_action_parameter
    raw, projection, issues, scalar = entry["payload"], {}, [], None
    view = local.normalize(raw, kind="ability")
    periodic = isinstance(view.get("modifiers"), dict) and any("thinkInterval" in m for m in view["modifiers"].values() if isinstance(m, dict))
    kind = "periodic_callback_attack" if periodic else "ability_start_attack"
    paths = [(p, v) for p, v in _walk(view) if p.endswith("/attackProperty") and isinstance(v, dict)]
    identity = {"origin_path": bundle["origin"]["parent"]["source_path"],
        "origin_pointer": bundle["origin"]["operation"]["id_pointer"], "gadget_id": bundle["selection"]["gadget_id"],
        "mount_pointer": mount["mount_pointer"], "source_path": entry["source_path"],
        "ability_pointer": entry["json_pointer"], "ability_name": view.get("abilityName"),
        "variant": entry["definition"]["variant_key"], "attack_paths": sorted(p for p, _ in paths)}
    candidate = {"candidate_id": periodic_candidate_identity(identity), "channel_kind": kind, "status": "pending",
        "parent_refs": {"container": "creation_bundle", "definition_id": entry["definition"]["definition_id"],
                        "source": _source(entry, "", raw), "mount_pointer": mount["mount_pointer"]},
        "projection": projection, "issues": issues}
    def issue(code):
        issues.append({"code": code})
    for key in ("parameter", "element", "gauge", "classification", "attenuation", "timer"):
        projection[key] = {"status": "pending"}
    projection["application_identity"] = {"status": "pending", "tag": None, "source": None,
        "evidence": "attenuation_tag_missing",
        "next_action": "核实该具体周期攻击通道的attenuationTag及共享范围；不得按爆发类别或同技能其他段猜测"}
    if len(bindings) != 1:
        issue("periodic_additional_parameter_writer_unsupported")
    template = recipe["evidence"]["contexts"][2 if periodic else 3]["raw"]
    try:
        if _normalized_shape(view, kind) != _normalized_shape(local.normalize(template, kind="ability"), kind):
            issue("periodic_complete_parent_unsupported")
    except RuntimeConfigError:
        raise
    except (ValueError, KeyError, TypeError):
        issue("periodic_complete_parent_unsupported")
    if len(paths) != 1:
        issue("periodic_attack_count_unsupported")
    else:
        path, prop = paths[0]
        info = resolve_pointer(view, path.rsplit("/", 1)[0])
        def origin(pointer, value):
            return _source(entry, local.raw_pointer(raw, pointer, kind="ability"), value)
        element, durability = prop.get("elementType"), prop.get("elementDurability")
        if element in ("Fire", "Ice", "Water", "Wind"):
            projection["element"] = {"status": "ready", "value": {"Fire": "pyro", "Ice": "cryo", "Water": "hydro", "Wind": "anemo"}[element],
                "source": origin(path + "/elementType", element)}
        else:
            issue("periodic_element_unsupported")
        gu = Decimal(str(durability)) / 25 if _number(durability) else None
        projection["gauge"] = {"status": "pending", "value": None, "raw_durability": durability,
            "tax_applied": False, "unit_contract": copy.deepcopy(recipe["unit_contract"])}
        if gu in (Decimal(1), Decimal(2), Decimal(4)):
            projection["gauge"].update(status="ready", value=int(gu), source=origin(path + "/elementDurability", durability))
        else:
            issue("periodic_gauge_unsupported")
        attack_tag = info.get("attackTag")
        if attack_tag in _ATTACK_TAG_CLASSIFICATION:
            projection["classification"] = {"status": "ready", "value": _ATTACK_TAG_CLASSIFICATION[attack_tag], "raw_value": attack_tag,
                "source": origin(path.rsplit("/", 1)[0] + "/attackTag", info["attackTag"])}
        else:
            issue("periodic_classification_unsupported")
        matches = [b for b in bindings if b["parameter_name"] == prop.get("damagePercentage")]
        if len(matches) != 1:
            issue("periodic_parameter_not_unique")
        else:
            binding = matches[0]
            talent = next((t for t in context["facts"]["talents"] if t["role"] == "skill_level" and t["source_path"] == binding["source_path"]
                and binding["operation_pointer"].startswith(t["json_pointer"] + "/")), None)
            suffix = binding["operation_pointer"][len(talent["json_pointer"]):] if talent else None
            op = resolve_pointer(talent["operations_normalized"], suffix) if talent else None
            expression = binding["raw_expression"]
            token = re.fullmatch(r"%([1-9][0-9]?)", expression) if isinstance(expression, str) else None
            if (not isinstance(op, dict) or op.keys() != {"$type", "CLGIKOPIKBC", "abilityName", "paramDelta", "paramSpecial"}
                    or op.get("$type") != "ModifyAbility" or op.get("CLGIKOPIKBC") is not True or not token
                    or op.get("abilityName") != view.get("abilityName") or int(token[1]) > 64):
                issue("periodic_parameter_operation_unsupported")
            elif not _number(binding.get("parameter_input")):
                issue("periodic_parameter_scalar_pending")
            else:
                scalar = project_action_parameter(context, int(token[1]) - 1)
                value = scalar["scalar_projection"]["value"]
                operation_raw = resolve_pointer(talent["operations_raw"], suffix)
                projection["parameter"] = {"status": scalar["status"], "param_index": scalar["param_index"], "value": value,
                    "source": scalar["source"], "parameter_descriptions": scalar["parameter_descriptions"],
                    "symbol": prop["damagePercentage"], "operation": {"raw": operation_raw,
                        "source": {"source_path": talent["source_path"], "source_sha256": talent["source_sha256"],
                            "json_pointer": binding["operation_pointer"], "node_sha256": fingerprint(operation_raw)}}}
                if scalar["status"] != "ready" or not _number(value):
                    issue("periodic_parameter_scalar_pending")
        if periodic:
            timers = [(p, v) for p, v in _walk(view) if p.endswith("/thinkInterval")]
            if len(timers) == 1 and _number(timers[0][1]) and timers[0][1] > 0:
                tpath, seconds = timers[0]
                ticks = Decimal(str(seconds)) * 1000000
                if ticks == ticks.to_integral_value() and 0 < ticks <= 10**15:
                    projection["timer"] = {"status": "ready", "kind": "constant_modifier_interval", "raw_seconds": seconds,
                        "seconds": seconds, "ticks": int(ticks), "tick_unit": "microsecond", "quantization": "exact_only",
                        "quantization_error_seconds": 0, "source": origin(tpath, seconds),
                        "first_callback": "not_proven", "terminal_callback": "not_proven", "count": "not_proven"}
            if projection["timer"]["status"] != "ready":
                issue("periodic_timer_not_exact")
        else:
            projection["timer"] = {"status": "not_applicable", "kind": "calibration_required", "ticks": None, "seconds": None}
        group = info.get("attenuationGroup")
        projection["attenuation"].update(group=group, profile=None, shared_identity="not_inferred")
        # A non-exact callback timer is independent from the attack's
        # attenuation identity.  Reviewed finite topologies may own all
        # occurrence timing, so keep resolving the otherwise complete source
        # instead of manufacturing a second, unrelated attenuation failure.
        # Every other diagnostic still blocks admission and preserves the
        # fail-closed behaviour for malformed parents, parameters and gauges.
        attenuation_blockers = [row for row in issues
                                if row.get("code") != "periodic_timer_not_exact"]
        if not attenuation_blockers and isinstance(group, str) and group.strip():
            try:
                profile = attenuation(group)
                projection["attenuation"].update(status=profile["execution"]["status"], profile=profile,
                    source=origin(path.rsplit("/", 1)[0] + "/attenuationGroup", group))
                if profile["execution"]["status"] != "ready":
                    issue("periodic_attenuation_pending")
            except RuntimeConfigError as exc:
                if exc.reason not in {"not_found", "unsupported_source", "ambiguous_reference"}:
                    raise
                issue("periodic_attenuation_unavailable")
        else:
            issue("periodic_attenuation_not_admitted")
        attenuation_tag = info.get("attenuationTag")
        if isinstance(attenuation_tag, str) and attenuation_tag.strip():
            projection["application_identity"] = {"status": "ready", "tag": attenuation_tag,
                "source": origin(path.rsplit("/", 1)[0] + "/attenuationTag", attenuation_tag),
                "evidence": "upstream_attenuation_tag", "next_action": None}
        elif "attenuationTag" in info:
            projection["application_identity"].update(evidence="invalid_attenuation_tag",
                next_action="上游attenuationTag为空或类型异常；需逐周期攻击通道复核原始节点")
            issue("periodic_attenuation_tag_invalid")
    candidate["status"] = "pending" if issues else "ready"
    candidate["semantic_dependency"] = {"identity": identity, "parent_refs": candidate["parent_refs"], "projection": periodic_projection_payload(projection)}
    candidate["source_fingerprint"] = fingerprint(candidate["semantic_dependency"])
    return candidate, scalar


def _graph_nodes(reader, context, bundle):
    from genshin_db.runtime_queries import _row
    nodes = []
    facts = context["facts"]
    for record in facts["records"].values():
        row = _row(reader.conn, reader.line, record["definition"]["definition_id"])
        raw, source = reader.nodes.node(row)
        key = json.loads(row["exact_key_json"])
        if not isinstance(key, dict) or any(raw.get(k) != v or type(raw.get(k)) is not type(v) for k, v in key.items()):
            _invalid("周期角色参数入口索引与原文ID不一致")
        nodes.append({"source": {**source, "definition": copy.deepcopy(record["definition"]), "node_sha256": fingerprint(raw)}, "raw": raw})
    for talent in facts["talents"]:
        nodes.append({"source": {k: copy.deepcopy(talent[k]) for k in ("source_path", "source_sha256", "json_pointer", "definition")},
            "raw": copy.deepcopy(talent["operations_raw"])})
        nodes[-1]["source"]["node_sha256"] = fingerprint(nodes[-1]["raw"])
        for ref in talent["references"]:
            reader.edge(ref, "legacy")
    bundled = {n["definition"]["definition_id"] for n in bundle["sources"]} if bundle else set()
    for ident, value in reader.entries.items():
        if ident not in bundled:
            nodes.append({"source": {k: copy.deepcopy(v) for k, v in value.items() if k != "payload"},
                          "raw": copy.deepcopy(value["payload"])})
    identities = set(reader.entries) | {n["source"]["definition"]["definition_id"] for n in nodes}
    if bundle:
        identities.update(n["definition"]["definition_id"] for n in bundle["sources"])
    if len(identities) > PERIODIC_LIMITS["max_source_nodes"]:
        _limit("source_nodes")
    for node in nodes:
        if len(dumps(node).encode("utf8")) > PERIODIC_LIMITS["max_node_bytes"]:
            _limit("node_bytes")
    return nodes


def _account_bundle(reader, bundle):
    """Nested public bundles do not get a separate raw-node or edge allowance."""
    from genshin_db.runtime_queries import _row
    for source in bundle["sources"]:
        row = _row(reader.conn, reader.line, source["definition"]["definition_id"])
        reader.entry(row)
        reader.object_refs(row)
    parent = bundle["origin"].get("parent")
    if parent:
        ident = parent["definition"]["definition_id"]
        # Bound the complete legacy parent before materializing all references.
        reader.legacy_refs(ident)


def get_periodic_attack_context(db_path, *, origin_reference_id=None, candidate_ids=None, **selection):
    from genshin_db.equipment_queries import borrow_read_cache
    from genshin_db.runtime_action_parameter import get_action_parameter_context
    from genshin_db.runtime_attenuation import get_runtime_attenuation_profile
    from genshin_db.runtime_gadget_source import get_runtime_gadget_source_profile
    from genshin_db.runtime_queries import _read, _row
    args = _selection(selection, origin_reference_id, candidate_ids)
    recipe = _recipe()
    with _read(db_path, args["version_line"], args.get("snapshot_id")) as (conn, state):
        if int(state["indexer_version"]) < 5:
            raise RuntimeConfigError("周期来源需要Gadget精确对象索引", "gadget_index_upgrade_required", 409)
        cache = borrow_read_cache(db_path, _CACHE)
        key = ("parameters", fingerprint(args))
        if key not in cache:
            if sum(k[0] == "parameters" for k in cache) >= PERIODIC_LIMITS["max_cached_selections"]:
                _limit("cached_selections")
            cache[key] = copy.deepcopy(get_action_parameter_context(db_path, **args))
        context = copy.deepcopy(cache[key])
        facts = context["facts"]
        reader, issues, prepared, scalars = _PeriodicReader(conn, state), [], {}, {}
        targets = _targets(reader, facts)
        result = {"projection_version": 1, "recipe": {"id": recipe["recipe_id"], "version": recipe["recipe_version"],
            "fingerprint": recipe["recipe_fingerprint"], "unit_contract_id": recipe["unit_contract"]["id"]},
            "snapshot": copy.deepcopy(state), "selection": copy.deepcopy(facts["selection"]), "level": copy.deepcopy(facts["level"]),
            "mode": "origin_discovery" if origin_reference_id is None else "selected" if candidate_ids is not None else "channel_discovery",
            "status": "pending", "whole_source_status": "partial", "source_relationship_status": "pending", "numeric_projection_status": "pending",
            "action_creation_association": "unproven", "dispatch_semantics_status": "not_proven", "execution_support": "not_evaluated",
            "source_graph": {"nodes": [], "edges": [], "creation_bundle": None, "branch_inventory": []},
            "creation_candidates": [], "candidates": [], "selected_candidates": [], "omitted_candidates": [],
            "descriptions": copy.deepcopy(context["descriptions"]), "issues": issues, "limits": dict(PERIODIC_LIMITS)}
        local = _local(state, reader, recipe)
        if facts["level"].get("effective_level") not in range(1, 16):
            issues.append({"code": "periodic_selection_scope_unsupported"})
        if local is None:
            issues.append({"code": "periodic_mapping_unreviewed"})
        else:
            result["recipe"]["mapping_fingerprint"] = local.mapping_fingerprint
        bundle = None
        if origin_reference_id is None:
            result["creation_candidates"] = _discover(reader, targets)
            result["discovery"] = {"status": "selection_required" if result["creation_candidates"] else
                "no_fully_resolved_creation" if reader.blocked else "no_parameter_related_creation",
                "counts": {**{k: len(v) for k, v in reader.visited.items()}, "edges": len(reader.all_edges), "blocked_paths": len(reader.blocked)},
                "blocked_paths": reader.blocked}
            issues.append({"code": "periodic_origin_selection_required"})
        else:
            stored = conn.execute("SELECT * FROM runtime_reference WHERE version_line=? AND reference_id=?", (args["version_line"], origin_reference_id)).fetchone()
            if stored is None:
                raise RuntimeConfigError("创建来源未收录", "not_found", 404)
            parent = _row(conn, args["version_line"], stored["from_definition_id"])
            links = [r for r in reader.object_refs(parent) if r["legacy_reference_id"] == origin_reference_id]
            if len(links) != 1 or type(links[0]["target_key"]) is not int:
                raise RuntimeConfigError("不是受支持的创建来源", "unsupported_source", 409)
            gid = links[0]["target_key"]
            bkey = ("gadget", args["version_line"], origin_reference_id)
            if bkey not in cache:
                cache[bkey] = get_runtime_gadget_source_profile(db_path, gadget_id=gid, origin_reference_id=origin_reference_id,
                    version_line=args["version_line"], snapshot_id=state["snapshot_id"])
            bundle = copy.deepcopy(cache[bkey])
            _account_bundle(reader, bundle)
            result["source_graph"]["creation_bundle"] = bundle
            if bundle["status"] != "ready":
                issues.append({"code": "periodic_creation_chain_pending"})
            if local:
                try:
                    if not _origin_admitted(bundle, local, recipe):
                        issues.append({"code": "periodic_creation_control_unverified"})
                except RuntimeConfigError:
                    raise
                except (ValueError, KeyError, TypeError):
                    issues.append({"code": "periodic_creation_control_unverified"})
            def attenuation(group):
                k = ("attenuation", args["version_line"], group, "application")
                if k not in cache:
                    cache[k] = get_runtime_attenuation_profile(db_path, group=group, channel="application",
                        version_line=args["version_line"], snapshot_id=state["snapshot_id"])
                prepared[(group, "application")] = copy.deepcopy(cache[k])
                return copy.deepcopy(cache[k])
            for mount in bundle["mounted_abilities"]:
                entry = mount["selected"]
                ident = entry["definition"]["definition_id"] if entry else None
                result["source_graph"]["branch_inventory"].append({"mount_pointer": mount["mount_pointer"], "status": mount["status"],
                    "parameter_related": ident in targets, "execution": "not_evaluated"})
                if ident in targets and local:
                    try:
                        candidate, scalar = _project_channel(entry, mount, targets[ident], context, local, recipe, bundle, attenuation)
                    except RuntimeConfigError:
                        raise
                    except ValueError:
                        issues.append({"code": "periodic_normalization_collision", "source_path": entry["source_path"], "json_pointer": entry["json_pointer"]})
                        continue
                    classification = candidate["projection"]["classification"]
                    required_slot = {"damage.normal_attack": "normal", "damage.charged_attack": "normal",
                        "damage.plunging_attack": "normal", "damage.elemental_skill": "skill",
                        "damage.elemental_burst": "burst"}.get(classification.get("value"))
                    if classification.get("status") == "ready" and required_slot not in facts["selection"]["matching_slots"]:
                        candidate["issues"].append({"code": "periodic_classification_selection_mismatch"})
                        candidate["status"] = "pending"
                    result["candidates"].append(candidate)
                    if scalar is not None:
                        scalars[candidate["candidate_id"]] = scalar
            if len(result["candidates"]) > PERIODIC_LIMITS["max_candidates"]:
                _limit("candidates")
        result["source_graph"]["nodes"] = _graph_nodes(reader, context, bundle)
        result["source_graph"]["edges"] = [copy.deepcopy(r) for _, r in sorted(reader.all_edges.items())]
        if "discovery" in result:
            result["discovery"]["counts"]["edges"] = len(reader.all_edges)
            for candidate in result["creation_candidates"]:
                for key in ("parent", "owner_ability"):
                    entry = candidate["origin"].get(key)
                    if entry:
                        candidate["origin"][key] = {k: v for k, v in entry.items() if k != "payload"}
        internal = PeriodicAttackContext(result, context, bundle, prepared, scalars)
        public = project_periodic_attack(internal, candidate_ids)
        # Keep only server-local contexts belonging to the explicit selection.
        # A candidate may be globally pending because GU/attenuation is unknown,
        # while a downstream reviewed no_application declaration legitimately
        # needs only its parameter/element/classification projection.
        optional_for_declared_mode = {"periodic_gauge_unsupported", "periodic_attenuation_pending",
            "periodic_attenuation_unavailable", "periodic_attenuation_not_admitted",
            "periodic_attenuation_tag_invalid", "periodic_timer_not_exact"}
        selected_rows = public["selected_candidates"]
        # Retain private numeric contexts only when every remaining candidate
        # diagnostic is an elemental dimension that a reviewed downstream
        # no_icd/no_application mode may make irrelevant.  A missing callback
        # timer is also retained privately because an independently reviewed
        # finite topology can own every occurrence time.  Ordinary periodic
        # and explicit-offset consumers still require positive timer evidence
        # and therefore cannot use this retention to execute.  Structural,
        # parent-chain or parameter failures still withdraw the complete group.
        reusable = (not public.get("issues") and selected_rows
                    and all({i.get("code") for i in c.get("issues", [])} <= optional_for_declared_mode
                            for c in selected_rows))
        if reusable:
            selected = {c["candidate_id"] for c in selected_rows}
            scalars = {k: v for k, v in scalars.items() if k in selected}
            groups = {(group, "application") for c in selected_rows
                      if isinstance((group := (c.get("projection") or {}).get("attenuation", {}).get("group")), str)}
            prepared = {k: v for k, v in prepared.items() if k in groups}
        else:
            scalars, prepared = {}, {}
        return PeriodicAttackContext(public, context, bundle, prepared, scalars)


def project_periodic_attack(context, candidate_ids=None):
    if not isinstance(context, PeriodicAttackContext):
        raise ValueError("需要本次服务端周期上下文")  # noqa: TRY004 - public validation convention
    result = copy.deepcopy(context.public_projection)
    prior = [c["candidate_id"] for c in result["selected_candidates"]]
    if candidate_ids is not None:
        if not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= PERIODIC_LIMITS["max_selected"] or any(not isinstance(c, str) for c in candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("非法周期候选ID列表")
        if prior and set(prior) != set(candidate_ids):
            raise ValueError("不能重解释已选周期上下文")
        wanted = set(candidate_ids)
        if not prior:
            found = [c for c in result["candidates"] if c["candidate_id"] in wanted]
            if any("projection" not in c for c in found):
                raise ValueError("发现摘要不能重新解释为已准备来源，请重新读取选择")
            result["selected_candidates"] = copy.deepcopy(found)
        if len(result["selected_candidates"]) != len(wanted):
            result["issues"].append({"code": "periodic_candidate_not_found"})
        result["mode"] = "selected"
    elif not prior and result["mode"] == "channel_discovery":
        result["issues"].append({"code": "periodic_candidate_selection_required"})
    selected = {c["candidate_id"] for c in result["selected_candidates"]}
    result["omitted_candidates"] = [c["candidate_id"] for c in result["candidates"] if c["candidate_id"] not in selected]
    numeric = bool(selected) and all(c["status"] == "ready" for c in result["selected_candidates"])
    result["numeric_projection_status"] = "ready" if numeric else "pending"
    bundle = result["source_graph"]["creation_bundle"]
    result["source_relationship_status"] = "ready" if bundle and bundle["status"] == "ready" else "pending"
    result["status"] = "ready" if numeric and not result["issues"] else "pending"
    # Public discovery summaries avoid repeating complete per-candidate raw sources.
    result["candidates"] = [{**{k: copy.deepcopy(c[k]) for k in ("candidate_id", "channel_kind", "status", "issues")},
        "projection_summary": {k: {f: v for f, v in p.items() if f in
        ("status", "value", "param_index", "group", "tag", "evidence",
         "next_action", "ticks", "seconds", "kind")} for k, p in c["projection"].items()}}
        if "projection" in c else c for c in result["candidates"]]
    issue_count = len(result["issues"]) + len(result.get("discovery", {}).get("blocked_paths", [])) + sum(len(c["issues"]) for c in result["candidates"])
    if issue_count > PERIODIC_LIMITS["max_issues"]:
        _limit("diagnostics")
    result["semantic_dependency"] = periodic_source_payload(result)
    result["source_fingerprint"] = periodic_source_fingerprint(result)
    result.pop("content_fingerprint", None)
    result["content_fingerprint"] = fingerprint(result)
    if len(dumps(result).encode("utf8")) > PERIODIC_LIMITS["max_bytes"]:
        _limit("response_bytes")
    return result


def get_runtime_periodic_attack_profile(db_path, *, origin_reference_id=None, candidate_ids=None, **selection):
    return get_periodic_attack_context(db_path, origin_reference_id=origin_reference_id, candidate_ids=candidate_ids, **selection).public_projection
