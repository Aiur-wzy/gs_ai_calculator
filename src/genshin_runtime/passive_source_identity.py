"""Pure EC12 source identity helpers shared by queries and source compilers."""
from __future__ import annotations

import copy
import hashlib
import json

UNLOCK_ALGORITHM = "explicit-passive-threshold-v1"


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()


def _snapshot_identity(state):
    result = {k: copy.deepcopy(state[k]) for k in ("version_line", "snapshot_id", "upstream_commit", "indexer_version",
               "schema_version", "normalization_version", "mapping_fingerprint") if k in state}
    if "normalization" in state:
        result["normalization"] = {k: copy.deepcopy(state["normalization"][k]) for k in (
            "profile_id", "normalization_version", "mapping_fingerprint", "upstream_commit") if k in state["normalization"]}
    return result


def _text_dependency(item):
    return {k: copy.deepcopy(item[k]) for k in ("text", "text_map_hash", "hash_origin", "text_origin", "requested_pointer") if k in item}


def stable_source_payload(result):
    """Canonical source digest, not a substitute for fresh DB/admission checks.

    Deliberately excludes progression inputs, unlock results and display issues.
    Raw records/operations are preserved whole, including unknown raw keys.
    """
    bundle = copy.deepcopy(result.get("source_bundle"))
    if bundle is not None:
        bundle["snapshot"] = _snapshot_identity(bundle["snapshot"])
        bundle.pop("closure_fingerprint", None)
    value = {"projection_version": result["projection_version"], "bundle_version": result["bundle_version"],
             "mode": result["mode"], "snapshot": _snapshot_identity(result["snapshot"]),
             "selection": result["selection"], "entry_records": result["entry_records"],
             "opening_inventory": [{k: row[k] for k in ("index", "raw", "origin")} for row in result["opening_inventory"]],
             "source_bundle": bundle,
             "candidates": [{k: row[k] for k in ("proud_skill_id", "proud_skill_group_id", "proud_level", "opening_index",
                                                   "opening_origin", "proud_origin")} | {"name": _text_dependency(row["name"])}
                            for row in result["candidates"]],
             "descriptions": {k: _text_dependency(result["descriptions"][k]) for k in ("name", "description", "unlock_description")}}
    return copy.deepcopy(value)


def passive_unlock_payload(result):
    unlock = result["unlock"]
    return copy.deepcopy({"algorithm": UNLOCK_ALGORITHM, "source_fingerprint": result["source_fingerprint"],
                        **{k: unlock[k] for k in ("supplied", "requirement", "selected_promote_record", "checks", "status")}})


def passive_source_fingerprint(profile):
    return _fingerprint(stable_source_payload(profile))


def passive_unlock_fingerprint(profile):
    return _fingerprint(passive_unlock_payload(profile))
