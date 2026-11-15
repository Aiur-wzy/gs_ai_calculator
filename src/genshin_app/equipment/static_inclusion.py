"""S1 material consistency only; the caller cannot admit a source with this receipt.

This isolated candidate performs no database access, numeric application, source
exclusion, or dynamic-ledger registration. S2 must freshly materialize the exact
database source and actual selection before using the structural declaration.
"""
from __future__ import annotations

import copy
import json
import math
import re

from genshin_app.equipment.panel_calculation import PROP_MAP
from genshin_db.runtime_catalog import dumps, fingerprint

from . import static_inclusion_profiles as _profiles

PROFILE = _profiles.PROFILE
PROFILE_FINGERPRINT = _profiles.PROFILE_FINGERPRINT
PROFILE_V2 = _profiles.PROFILE_V2
PROFILE_V2_FINGERPRINT = _profiles.PROFILE_V2_FINGERPRINT
_REGISTERED_PROFILES = _profiles._REGISTERED_PROFILES

_UNSET = object()
PARENT_PATH = "ExcelBinOutput/ReliquarySetExcelConfigData.json"
AFFIX_PATH = "ExcelBinOutput/EquipAffixExcelConfigData.json"
HEX = re.compile(r"[0-9a-f]{64}\Z")
POINTER = re.compile(r"/(0|[1-9][0-9]{0,8})\Z")
MAX_BYTES = 256 * 1024
MAX_NODES = 8000


class _Pending(ValueError):
    def __init__(self, code, path):
        self.code, self.path = code, path
        super().__init__(code + ": " + path)


def _need(condition, code, path):
    if not condition:
        raise _Pending(code, path)


def _keys(value, required, optional=(), *, code="material_shape", path="material"):
    _need(type(value) is dict, code, path)
    _need(set(required) <= set(value) <= set(required) | set(optional), code, path)


def _int(value, lo=0, hi=2**53 - 1):
    return type(value) is int and lo <= value <= hi


def _text(value, maximum=4096):
    return type(value) is str and 0 < len(value) <= maximum and value.strip() == value


def _hex(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def _bounded(value):
    count, string_bytes = 0, 0
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop(); count += 1
        _need(count <= MAX_NODES and depth <= 24, "material_limit", "material")
        if type(item) is dict:
            _need(count + len(stack) + len(item) <= MAX_NODES, "material_limit", "object")
            _need(all(type(k) is str and len(k) <= 256 for k in item), "material_shape", "keys")
            string_bytes += sum(len(k.encode("utf8")) for k in item)
            stack.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            _need(count + len(stack) + len(item) <= MAX_NODES, "material_limit", "list")
            stack.extend((v, depth + 1) for v in item)
        elif type(item) is str:
            _need(len(item) <= 32768, "material_limit", "string")
            string_bytes += len(item.encode("utf8"))
        elif type(item) is int:
            _need(abs(item) <= 2**53 - 1, "material_number", "integer")
        elif type(item) is float:
            _need(math.isfinite(item) and not (item == 0 and math.copysign(1, item) < 0),
                  "material_number", "float")
        else:
            _need(item is None or type(item) is bool, "material_shape", "value")
        _need(string_bytes <= MAX_BYTES, "material_limit", "string_bytes")
    _need(len(dumps(value).encode("utf8")) <= MAX_BYTES, "material_limit", "bytes")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_json_key", "parent.payload_json")
        result[key] = value
    return result


def _digest(value, path):
    _need(_hex(value.get("content_fingerprint")), "material_fingerprint", path)
    actual = fingerprint({k: v for k, v in value.items() if k != "content_fingerprint"})
    _need(actual == value["content_fingerprint"], "material_fingerprint", path)


def _origin(value, expected, path):
    _keys(value, ("source_path", "json_pointer", "source_sha256"), path=path)
    _need(value["source_path"] == expected and _hex(value["source_sha256"])
          and type(value["json_pointer"]) is str and POINTER.fullmatch(value["json_pointer"]),
          "material_origin", path)


def _snapshot(snap, profile):
    scope = profile.get("snapshot_scope", {})
    extras = ("normalization_state", "source_capability_contract", "source_capability_state", "source_capabilities")
    _keys(snap, ("version_line", "state", "index_state", "schema_version", "upstream_commit",
                "snapshot_id", "execution_support", "built_at", "profile_id", "profile_version",
                "indexer_version", "manifest_fingerprint", "counts", "reference_states", "normalization"),
          tuple(key for key in extras if key in scope),
          path="snapshot")
    _need(snap["version_line"] in ("release", "beta") and snap["state"] == snap["index_state"] == "ready"
          and type(snap["schema_version"]) is int and snap["schema_version"] == 1
          and snap["indexer_version"] == profile["indexer_version"]
          and snap["upstream_commit"] == profile["upstream_commit"]
          and snap["profile_id"] == "upstream-combat-catalog-v1"
          and type(snap["profile_version"]) is int and snap["profile_version"] == 1
          and snap["execution_support"] == "not_evaluated" and _text(snap["built_at"])
          and _hex(snap["snapshot_id"]) and _hex(snap["manifest_fingerprint"]),
          "snapshot_profile", "snapshot")
    for key in ("counts", "reference_states"):
        _need(type(snap[key]) is dict and all(_text(k) and _int(v) for k, v in snap[key].items()),
              "snapshot_profile", key)
    normalization = snap["normalization"]
    _keys(normalization, ("mapping_fingerprint", "normalization_version", "profile_id", "upstream_commit"),
          path="normalization")
    _need(_hex(normalization["mapping_fingerprint"])
          and type(normalization["normalization_version"]) is int and normalization["normalization_version"] == 2
          and normalization["profile_id"] == scope.get("normalization", {}).get(
              "profile_id", "upstream-34208429-reviewed-schema-v1")
          and normalization["upstream_commit"] == snap["upstream_commit"], "snapshot_profile", "normalization")


    if "snapshot_scope" in profile:
        scope = profile["snapshot_scope"]
        _need(dumps({key: snap[key] for key in scope}) == dumps(scope),
              "snapshot_profile", "snapshot.exact_scope")


def _registered():
    """Validate fixed registrations; neither callers nor capability copies enroll one."""
    _need(fingerprint(PROFILE) == PROFILE_FINGERPRINT
          and dict(PROP_MAP) == PROFILE["property_mapping"], "profile_changed", "profile")
    _need(type(_REGISTERED_PROFILES) is tuple and 1 <= len(_REGISTERED_PROFILES) <= 16,
          "profile_registry", "profiles")
    found, scopes, legacy = set(), set(), 0
    for entry in _REGISTERED_PROFILES:
        _need(type(entry) is tuple and len(entry) == 2, "profile_registry", "entry")
        profile, expected = entry
        _need(type(profile) is dict and _hex(expected) and fingerprint(profile) == expected,
              "profile_changed", "registered_profile")
        if expected == PROFILE_FINGERPRINT:
            legacy += 1
            scope_key = "legacy6"
        else:
            _need(set(PROFILE) | {"snapshot_scope"} <= set(profile)
                  <= set(PROFILE) | {"snapshot_scope", "review_evidence"}
                  and type(profile["version"]) is int and profile["version"] >= 2
                  and profile["id"] == f"equipment-static-included-v{profile['version']}"
                  and _text(profile["indexer_version"], 64), "profile_registry", "versioned_profile")
            for key in set(PROFILE) - {"id", "version", "indexer_version", "upstream_commit"}:
                _need(dumps(profile[key]) == dumps(PROFILE[key]), "profile_registry", key)
            scope = profile["snapshot_scope"]
            _keys(scope, ("version_line", "upstream_commit", "schema_version", "indexer_version",
                          "snapshot_id", "manifest_fingerprint", "normalization"),
                  ("normalization_state", "source_capability_contract", "source_capability_state", "source_capabilities"),
                  path="profile.scope")
            _need(scope["version_line"] in ("release", "beta")
                  and scope["upstream_commit"] == profile["upstream_commit"]
                  and type(profile["upstream_commit"]) is str
                  and re.fullmatch(r"[0-9a-f]{40}", profile["upstream_commit"]) is not None
                  and scope["indexer_version"] == profile["indexer_version"]
                  and type(scope["schema_version"]) is int and scope["schema_version"] == 1
                  and _hex(scope["snapshot_id"]) and _hex(scope["manifest_fingerprint"]),
                  "profile_registry", "scope")
            normal = scope["normalization"]
            _keys(normal, ("mapping_fingerprint", "normalization_version", "profile_id", "upstream_commit"),
                  path="profile.normalization")
            _need(_hex(normal["mapping_fingerprint"])
                  and type(normal["normalization_version"]) is int and normal["normalization_version"] == 2
                  and _text(normal["profile_id"], 128)
                  and normal["upstream_commit"] == profile["upstream_commit"],
                  "profile_registry", "normalization")
            if profile["version"] == 2:
                _need(profile["upstream_commit"] == PROFILE["upstream_commit"]
                      and profile["indexer_version"] == "7"
                      and normal["profile_id"] == "upstream-34208429-reviewed-schema-v1",
                      "profile_registry", "legacy_version2")
            else:
                review = profile.get("review_evidence")
                _keys(review, ("contract", "reference", "fingerprint"), path="profile.review_evidence")
                _need(review["contract"] == "static-inclusion-profile-review-v1"
                      and _text(review["reference"], 256) and _hex(review["fingerprint"]),
                      "profile_registry", "review_evidence")
            scope_key = dumps(scope)
        identity = (profile["id"], profile["version"], expected)
        _need(identity not in found and scope_key not in scopes, "profile_registry", "duplicate_scope")
        found.add(identity)
        scopes.add(scope_key)
    _need(legacy == 1, "profile_registry", "legacy_profile")
    return _REGISTERED_PROFILES


def _identity(profile, expected):
    return {"profile_id": profile["id"], "profile_version": profile["version"],
            "profile_fingerprint": expected}


def _select(profile_id, profile_version, profile_fingerprint):
    registry = _registered()
    if all(value is _UNSET for value in (profile_id, profile_version, profile_fingerprint)):
        return PROFILE, PROFILE_FINGERPRINT
    _need(_text(profile_id, 128) and _int(profile_version, 1) and _hex(profile_fingerprint),
          "profile_selection", "profile_identity")
    matches = [(p, fp) for p, fp in registry
               if (p["id"], p["version"], fp) == (profile_id, profile_version, profile_fingerprint)]
    _need(len(matches) == 1, "profile_selection", "profile_identity")
    return matches[0]


def static_inclusion_profile_capabilities():
    try:
        rows = [{**_identity(p, fp), "profile": copy.deepcopy(p)} for p, fp in _registered()]
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
        return {"supported_profiles": [], "profile_registry_state": "pending"}
    return {"supported_profiles": rows, "profile_registry_state": "ready",
            "fresh_selection": "同一只读事务精确匹配已审核快照；无唯一匹配保持待补，不按索引号推断"}


def select_static_inclusion_profile(snapshot):
    """Select an internal registration, without granting source or DB authority."""
    try:
        _bounded(snapshot)
        matches = []
        for profile, expected in _registered():
            try:
                _snapshot(snapshot, profile)
            except _Pending:
                continue
            matches.append(_identity(profile, expected))
        _need(len(matches) == 1, "profile_selection", "snapshot.no_unique_reviewed_profile")
        return {"status": "selected", **matches[0]}
    except _Pending as exc:
        issue = {"code": exc.code, "path": exc.path}
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
        issue = {"code": "profile_selection", "path": "snapshot.malformed"}
    return {"status": "pending", "profile_id": None, "profile_version": None,
            "profile_fingerprint": None, "issues": [issue]}


def _raw_parent(raw):
    _keys(raw, ("setId", "EquipAffixId", "setNeedNum", "containsList", "textList", "rankLevel", "setIcon"),
          ("bagSortValue", "dungeonGroup", "disableFilter"), code="raw_parent_shape", path="parent.record")
    _need(_int(raw["setId"], 1) and _int(raw["EquipAffixId"], 1)
          and _int(raw["rankLevel"], 1, 5) and _text(raw["setIcon"], 256), "raw_parent_value", "parent.record")
    needed = raw["setNeedNum"]
    _need(type(needed) is list and 1 <= len(needed) <= 5 and all(_int(x, 1, 5) for x in needed)
          and needed == sorted(set(needed)), "raw_parent_value", "setNeedNum")
    _need(type(raw["containsList"]) is list and len(raw["containsList"]) == 5
          and all(_int(x, 1) for x in raw["containsList"])
          and len(set(raw["containsList"])) == 5, "raw_parent_value", "containsList")
    _need(type(raw["textList"]) is list and len(raw["textList"]) == len(needed)
          and all(_int(x, 0, 2**32 - 1) for x in raw["textList"]), "raw_parent_value", "textList")
    if "bagSortValue" in raw:
        _need(_int(raw["bagSortValue"]), "raw_parent_value", "bagSortValue")
    if "disableFilter" in raw:
        _need(_int(raw["disableFilter"], 0, 1), "raw_parent_value", "disableFilter")
    if "dungeonGroup" in raw:
        value = raw["dungeonGroup"]
        _need(type(value) is list and 1 <= len(value) <= 32 and all(_int(x, 1) for x in value)
              and len(set(value)) == len(value), "raw_parent_value", "dungeonGroup")


def _raw_affix(raw):
    _keys(raw, ("affixId", "id", "nameTextMapHash", "descTextMapHash", "paramList", "addProps"),
          ("level",), code="raw_affix_shape", path="affix.record")
    _need(_int(raw["affixId"], 1) and _int(raw["id"], 1)
          and _int(raw.get("level", 0), 0, 4)
          and all(_int(raw[k], 0, 2**32 - 1) for k in ("nameTextMapHash", "descTextMapHash")),
          "raw_affix_value", "affix.record")
    params = raw["paramList"]
    _need(type(params) is list and len(params) == 8
          and all(type(x) in (int, float) and x == 0 for x in params), "raw_parameters", "paramList")
    props = raw["addProps"]
    _need(type(props) is list and len(props) == 3, "raw_properties", "addProps")
    rows, seen = [], set()
    for index, p in enumerate(props):
        if type(p) is dict and not p:
            continue
        _keys(p, ("propType", "value"), code="raw_properties", path=f"addProps/{index}")
        prop, value = p["propType"], p["value"]
        _need(type(prop) is str and prop in PROP_MAP and prop not in seen, "raw_properties", f"addProps/{index}")
        _need(type(value) in (int, float) and value > 0 and math.isfinite(value), "raw_properties", f"addProps/{index}/value")
        seen.add(prop)
        stat, mode = PROP_MAP[prop]
        ratio = mode == "percent" or stat in ("crit_rate", "crit_dmg", "energy_recharge") or stat.startswith("dmg_bonus:")
        rows.append({"source_index": index, "prop_type": prop, "stat": stat,
                     "unit": "ratio" if ratio else "flat", "value": value,
                     "application": "base_fraction_already_in_panel" if mode == "percent" else "already_in_panel"})
    _need(rows, "raw_properties", "addProps/no_actual_properties")
    return rows


def _normalized(normal, raw, snap):
    _keys(normal, ("affix_id", "affix_group_id", "source_level", "name", "name_text_map_hash", "name_source", "name_status",
                   "desc_template", "desc_source", "desc_status", "open_config", "version_line", "param_list", "add_props",
                   "source_missing_fields", "source_refs", "annotations"), path="normalized")
    expected = {"affix_id": raw["affixId"], "affix_group_id": raw["id"], "source_level": raw.get("level", 0),
                "open_config": None, "version_line": snap["version_line"], "param_list": raw["paramList"],
                "add_props": raw["addProps"], "source_missing_fields": [k for k in ("level", "openConfig") if k not in raw],
                "name_text_map_hash": str(raw["nameTextMapHash"]), "annotations": []}
    for key, value in expected.items():
        _need(dumps(normal[key]) == dumps(value), "normalized_mismatch", "normalized/" + key)
    refs = [{"table": "EquipAffixExcelConfigData", "key": {"affixId": raw["affixId"]},
             "field": None, "upstream_commit": snap["upstream_commit"]}]
    _need(dumps(normal["source_refs"]) == dumps(refs), "normalized_mismatch", "source_refs")
    for prefix, value_key, raw_hash in (("name", "name", "nameTextMapHash"), ("desc", "desc_template", "descTextMapHash")):
        _need(type(normal[value_key]) is str and len(normal[value_key]) <= 8192
              and normal[prefix + "_status"] in ("present", "missing"), "normalized_mismatch", prefix)
        expected_source = {"table": "EquipAffixExcelConfigData", "record_id": raw["affixId"],
                           "text_map_hash": str(raw[raw_hash]), "language": "CHS", "upstream_commit": snap["upstream_commit"]}
        _need(dumps(normal[prefix + "_source"]) == dumps(expected_source), "normalized_mismatch", prefix + "_source")


def _compile(m, profile):
    _bounded(m)
    _keys(m, ("contract", "parent", "source", "relation", "normalized", "selection"))
    _need(m["contract"] == "static-inclusion-material-v1", "material_contract", "contract")
    p, s, selection, relation = m["parent"], m["source"], m["selection"], m["relation"]
    _keys(p, ("snapshot", "definition", "references", "payload_json", "source", "content_fingerprint"), path="parent")
    _keys(s, ("bundle_version", "status", "issues", "source", "snapshot", "param_list", "affix", "talent", "talent_candidates",
              "abilities", "references", "reference_closed", "execution_support", "closure_fingerprint", "normalization", "content_fingerprint"), path="source")
    _digest(p, "parent"); _digest(s, "source")
    _snapshot(p["snapshot"], profile); snap = p["snapshot"]
    _need(dumps(s["snapshot"]) == dumps(snap) and s["normalization"] == snap["normalization"], "snapshot_mismatch", "source.snapshot")
    _need(type(s["bundle_version"]) is int and s["bundle_version"] == 1 and s["status"] == "ready"
          and s["issues"] == [] and s["references"] == p["references"] == []
          and s["reference_closed"] is True and s["talent"] is None and s["talent_candidates"] == s["abilities"] == []
          and s["execution_support"] == "not_evaluated" and _hex(s["closure_fingerprint"]), "source_not_closed", "source")
    _origin(p["source"], PARENT_PATH, "parent.source")
    definition = p["definition"]
    _keys(definition, ("definition_id", "definition_kind", "owner_definition_id", "source_path", "json_pointer", "variant_key",
                       "namespace_key", "node_sha256", "exact_key"), path="parent.definition")
    _need(_hex(definition["definition_id"]) and definition["definition_kind"] == "excel_record"
          and definition["owner_definition_id"] is None and definition["variant_key"] is None
          and definition["namespace_key"] == "excel" and definition["source_path"] == PARENT_PATH
          and definition["json_pointer"] == p["source"]["json_pointer"], "parent_definition", "parent.definition")
    _need(type(p["payload_json"]) is str, "raw_parent_shape", "payload_json")
    raw_parent = json.loads(p["payload_json"], object_pairs_hook=_pairs)
    _bounded(raw_parent); _raw_parent(raw_parent)
    _need(definition["node_sha256"] == fingerprint(raw_parent)
          and dumps(definition["exact_key"]) == dumps({"setId": raw_parent["setId"]}), "parent_definition", "parent.node")
    affix = s["affix"]
    _keys(affix, ("record", "source_path", "json_pointer", "source_sha256"), path="source.affix")
    _origin({k: v for k, v in affix.items() if k != "record"}, AFFIX_PATH, "source.affix")
    raw = affix["record"]; rows = _raw_affix(raw)
    level = raw.get("level", profile["default_absent_affix_level"])
    _need(raw["id"] == raw_parent["EquipAffixId"] and level < len(raw_parent["setNeedNum"]), "relation_mismatch", "affix.level")
    required = raw_parent["setNeedNum"][level]
    identity = {"kind": "equip_affix", "affix_id": raw["affixId"], "affix_group_id": raw["id"], "source_level": level,
                "version_line": snap["version_line"], "snapshot_id": snap["snapshot_id"], "upstream_commit": snap["upstream_commit"]}
    _need(dumps(s["source"]) == dumps(identity) and dumps(s["param_list"]) == dumps(raw["paramList"]), "relation_mismatch", "source.identity")
    expected_relation = {"source_index": level, "required_count": required, "relation_status": "present"}
    _need(dumps(relation) == dumps(expected_relation), "relation_mismatch", "relation")
    _normalized(m["normalized"], raw, snap)
    _keys(selection, ("kind", "provider", "set_id", "count", "required_count", "mode", "panel_policy", "panel_fingerprint", "data_version"), path="selection")
    _need(selection["kind"] == "artifact_set" and _text(selection["provider"], 128)
          and _int(selection["set_id"], 1) and selection["set_id"] == raw_parent["setId"]
          and _int(selection["count"], required, 5) and _int(selection["required_count"], 1, 5)
          and selection["required_count"] == required and selection["mode"] == "panel"
          and selection["panel_policy"] == "static_included" and _hex(selection["panel_fingerprint"]), "selection_mismatch", "selection")
    version = selection["data_version"]
    _keys(version, ("version_line", "upstream_commit", "equipment_schema_version", "built_at"), path="selection.data_version")
    _need(version["version_line"] == snap["version_line"] and version["upstream_commit"] == snap["upstream_commit"]
          and type(version["equipment_schema_version"]) is int and version["equipment_schema_version"] == 1
          and _text(version["built_at"]), "selection_mismatch", "selection.data_version")
    declaration = {"delivery": "already_in_given_panel", "numeric_application": False,
                   "basis_authority": "caller_given_panel_policy", "nonextra_authority": "not_inferred",
                   "provider": selection["provider"], "kind": "artifact_set", "set_id": raw_parent["setId"],
                   "count": selection["count"], "required_count": required, "source_index": level,
                   "affix_id": raw["affixId"], "affix_group_id": raw["id"], "properties": rows,
                   "panel_fingerprint": selection["panel_fingerprint"], "panel_policy": selection["panel_policy"],
                   "provenance": {"snapshot_id": snap["snapshot_id"], "upstream_commit": snap["upstream_commit"],
                                  "version_line": snap["version_line"], "parent": copy.deepcopy(p["source"]),
                                  "parent_node_sha256": definition["node_sha256"],
                                  "affix": {k: v for k, v in affix.items() if k != "record"},
                                  "affix_node_sha256": fingerprint(raw),
                                  "parent_content_fingerprint": p["content_fingerprint"],
                                  "source_content_fingerprint": s["content_fingerprint"],
                                  "normalized_fingerprint": fingerprint(m["normalized"])}}
    return declaration, fingerprint(m)


def compile_static_inclusion(material, *, profile_id=_UNSET, profile_version=_UNSET,
                             profile_fingerprint=_UNSET):
    """Return a bounded consistency receipt, never executable source authority."""
    result = {"profile_id": "equipment-static-included-v1", "profile_version": 1,
              "profile_fingerprint": PROFILE_FINGERPRINT,
              "verification_scope": "materialized_consistency_no_source_authority",
              "source_authority": "not_granted", "requires_fresh_db_admission": True,
              "status": "pending", "issues": []}
    if not all(value is _UNSET for value in (profile_id, profile_version, profile_fingerprint)):
        result.update(profile_id=None, profile_version=None, profile_fingerprint=None)
    try:
        profile, expected = _select(profile_id, profile_version, profile_fingerprint)
        result.update(_identity(profile, expected))
        declaration, material_fp = _compile(material, profile)
    except _Pending as exc:
        result["issues"] = [{"code": exc.code, "path": exc.path}]
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
        result["issues"] = [{"code": "material_malformed", "path": "material"}]
    else:
        result.update(status="shape_ready", declaration=declaration, material_fingerprint=material_fp)
        result["certificate_fingerprint"] = fingerprint(result)
    return result
