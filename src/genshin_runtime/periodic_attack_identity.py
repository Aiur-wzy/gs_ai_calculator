"""Pure source identities for finite periodic projections; never trusted contexts."""
from __future__ import annotations

import copy
import hashlib
import json


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf8")).hexdigest()


def periodic_candidate_identity(payload):
    """Hash exact path/relationship identity; callers exclude snapshot/local names."""
    return "periodic." + _hash(payload)


def _snapshot_identity(value):
    return {k: copy.deepcopy(value[k]) for k in
        ("version_line", "snapshot_id", "upstream_commit", "indexer_version", "normalization") if k in value}


def periodic_projection_payload(projection):
    """Shared channel identity excludes downstream display-only snapshot fields."""
    value = copy.deepcopy(projection)
    attenuation = value.get("attenuation", {}).get("profile")
    if attenuation is not None:
        value["attenuation"]["profile"] = {k: copy.deepcopy(attenuation[k]) for k in
            ("group", "channel", "source", "raw_record", "projection", "content_fingerprint") if k in attenuation}
        value["attenuation"]["profile"]["snapshot"] = _snapshot_identity(attenuation["snapshot"])
    return value


def periodic_source_payload(profile):
    """Rebuild semantic evidence rather than trusting an echoed dependency/hash."""
    snapshot = profile["snapshot"]

    graph = copy.deepcopy(profile["source_graph"])
    bundle = graph.get("creation_bundle")
    if bundle:
        graph["creation_bundle"] = {k: copy.deepcopy(bundle[k]) for k in
            ("projection_version", "graph_scope", "selection", "origin", "record", "config", "mounted_abilities")}
        graph["creation_bundle"]["snapshot"] = _snapshot_identity(bundle["snapshot"])
    return {"projection_version": profile["projection_version"], "recipe": copy.deepcopy(profile["recipe"]),
        "snapshot": {k: copy.deepcopy(snapshot[k]) for k in
            ("version_line", "snapshot_id", "upstream_commit", "indexer_version", "normalization") if k in snapshot},
        "selection": copy.deepcopy(profile["selection"]), "level": copy.deepcopy(profile["level"]),
        # Full original graph stays in the public response once. Rehash its actual
        # contents here; the caller's echoed fingerprint is never an authority.
        "source_graph_fingerprint": _hash(graph),
        "selected_candidates": [{**{k: copy.deepcopy(c[k]) for k in
            ("candidate_id", "channel_kind", "parent_refs")}, "projection_fingerprint": _hash(periodic_projection_payload(c["projection"]))}
            for c in sorted(profile.get("selected_candidates", []), key=lambda c: c["candidate_id"])],
        "action_creation_association": profile["action_creation_association"],
        "dispatch_semantics_status": profile["dispatch_semantics_status"]}


def periodic_source_fingerprint(profile):
    return _hash(periodic_source_payload(profile))
