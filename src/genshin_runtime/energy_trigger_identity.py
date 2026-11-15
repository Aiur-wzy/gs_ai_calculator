"""Pure finite trigger identities; no database connection or caller authorization."""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from importlib import resources

LIMITS = {"max_bytes": 524288, "max_nodes": 16, "max_edges": 64,
          "max_candidates": 8, "max_issues": 64}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()


def pointer(value, path):
    for part in path[1:].split("/") if path else []:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


@lru_cache(maxsize=1)
def recipe():
    value = json.loads(resources.files("genshin_runtime").joinpath(
        "resources/energy_trigger_profiles.json").read_text("utf8"))
    value["recipe_fingerprint"] = fingerprint(value)
    return value


def reference_contract():
    r = recipe()
    return copy.deepcopy({k: r[k] for k in ("profile_id", "profile_version", "compiler", "compiler_version",
        "commit", "old_commit", "evidence_tier", "recipe_fingerprint", "primary_sources", "reference_policy")})


def generation_source_payload(profile):
    """EC16a's unchanged pure identity, reproduced without importing DB code."""
    origin, material = profile["origin"], profile["material"]
    def node(value):
        return None if value is None else {k: value[k] for k in
            ("definition", "source_path", "source_sha256", "json_pointer")} | {"raw_sha256": fingerprint(value["payload"])}
    return {"projection_version": profile["projection_version"], "graph_scope": profile["graph_scope"],
        "snapshot": {k: profile["snapshot"][k] for k in
            ("version_line", "snapshot_id", "upstream_commit", "indexer_version", "normalization") if k in profile["snapshot"]},
        "selection": profile["selection"], "reference_contract": profile["reference_contract"],
        "origin": {"reference": origin["reference"], "material_reference": origin["material_reference"],
            "parent": node(origin["parent"]), "owner": node(origin["owner"]),
            "operation_pointer": origin["operation"]["raw_pointer"], "operation_raw_sha256": fingerprint(origin["operation"]["raw"])},
        "material": {"status": material["status"], "candidates": [node(c) for c in material["candidates"]]},
        "reference_projection": profile["reference_projection"]}


def all_entries(bundle):
    query = bundle["source_query"]
    values = [query["origin"]["parent"], query["origin"]["owner"], *query["material"]["candidates"],
              *bundle["source_bundle"]["entries"]]
    result = {}
    for entry in values:
        if entry is None:
            continue
        ident = entry["definition"]["definition_id"]
        if ident in result and result[ident] != entry:
            raise ValueError("同一来源定义出现不一致完整原文")
        result[ident] = entry
    return result


def energy_trigger_source_payload(bundle):
    entries = all_entries(bundle)
    return copy.deepcopy({"projection_version": bundle["projection_version"], "graph_scope": bundle["graph_scope"],
        "snapshot": {k: bundle["snapshot"][k] for k in ("version_line", "snapshot_id", "upstream_commit",
            "indexer_version", "normalization") if k in bundle["snapshot"]},
        "parent_identity": bundle["parent_identity"], "selection": bundle["selection"],
        "source_query": generation_source_payload(bundle["source_query"]),
        "source_bundle": {"entries": [{k: e[k] for k in ("definition", "source_path", "source_sha256", "json_pointer", "node_sha256")}
                                       for _, e in sorted(entries.items())],
            "references": bundle["source_bundle"]["references"], "parent_bridge": bundle["source_bundle"]["parent_bridge"],
            "source_closure": bundle["source_bundle"]["source_closure"]},
        "reference_contract": bundle["reference_contract"]})


def energy_trigger_source_fingerprint(bundle):
    return fingerprint(energy_trigger_source_payload(bundle))


def family_for(owner_raw, attack_raw, operation_relative):
    r = recipe()
    found = [f for f in r["families"] if owner_raw == r["parents"][f["controller"]]["new_raw"]
        and attack_raw == r["parents"][f["parent"]]["new_raw"] and operation_relative == f["generate_path"]]
    return copy.deepcopy(found[0]) if len(found) == 1 else None


def build_bridge(family, *, owner, attack, talent, proud, param_index, writer_index, metadata_candidate_id, mounting):
    """Reconstructed from full guarded nodes; no caller-supplied bridge fields."""
    did = lambda e: e["definition"]["definition_id"]
    ap, cp = attack["json_pointer"], owner["json_pointer"]
    paths = [ap + p for p in family["attack_paths"]]
    info = pointer(attack["payload"], family["attack_paths"][0] + family["attack_info_suffix"])
    prop = info["EHKJIDFINEK"]
    controller = {"owner_ability_entry_id": did(owner), "listener_pointer": cp + family["listener_path"],
        "generator_modifier_pointer": cp + family["generator_modifier_path"],
        "selected_generate_pointer": cp + family["generate_path"],
        "controller_key": fingerprint({"owner": {k: owner[k] for k in ("source_path", "source_sha256", "json_pointer", "node_sha256")},
                                       "modifier_path": family["generator_modifier_path"]}),
        "raw_duration": family["duration"]}
    mark = family["mark"]
    if mark is not None:
        mark = {"key": mark["key"], "value": mark["value"], "writer_pointer": ap + mark["writer_path"],
            "writer_attach_pointer": ap + mark["writer_attach_path"], "attack_attach_pointer": ap + mark["attack_attach_path"],
            "read_pointer": cp + mark["read_path"], "related_accesses": "full_guarded_owner_and_attack"}
    return {"status": "ready", "kind": "metadata_initial_attack" if family["metadata_required"] else "scalar_target_unique_attack",
        "trigger_family": family["id"], "variant": family["variant"],
        "parameter": {"param_index": param_index, "special": family["parameter_special"],
            "writer_entry_id": did(talent), "writer_pointer": talent["json_pointer"] + "/" + str(writer_index),
            "parameter_entry_id": did(proud), "parameter_pointer": proud["json_pointer"] + "/paramList/" + str(param_index),
            "target_ability_entry_id": did(attack)},
        "attack": {"entry_id": did(attack), "operation_pointer": paths[0],
            "attack_info_pointer": paths[0] + family["attack_info_suffix"],
            "attack_property_pointer": paths[0] + family["attack_info_suffix"] + family["property_suffix"],
            "attack_tag": info["IAFHAPFBBOB"], "attack_type": prop["PEPDKCDHNIM"],
            "equivalent_branch_pointers": paths, "metadata_candidate_id": metadata_candidate_id},
        "controller": controller, "mark": mark, "mounting": mounting,
        "predicate_semantics": family["predicate_semantics"],
        "identity_scope": "source_identity_only_not_full_damage_semantics"}
