"""Shared immutable observations and compact condition receipts.

This is an evidence projection, not a second target simulator. Legacy freeze
receipts remain readable; callers can expand new references on demand.
"""
from __future__ import annotations

import copy
import hashlib
import json


def compact_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8"))


def compact_condition(runtime, event, receipt, sample, *, evaluated):
    row = {key: value for key, value in receipt.items()
           if key not in ("source", "predicate", "target_sample")}
    row["contract"] = "target-condition-observation-v2"
    row["source_ref"] = {"fixed_source_id": receipt["fixed_source_id"], "effect_id": receipt["effect_id"]}
    new_sample = None
    if evaluated and sample is not None:
        refs = event.setdefault("target_observation_refs", {})
        profile = sample["profile_id"]
        if profile not in refs:
            wire = json.dumps(sample, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            refs[profile] = "target-sample:" + hashlib.sha256(wire.encode("utf8")).hexdigest()
        ref = refs[profile]
        row["target_sample_ref"] = ref
        if ref not in runtime.target_state_samples:
            new_sample = (ref, copy.deepcopy(sample))
        # Small familiar fields keep existing shallow readers useful; the
        # actual state and provenance are stored once in the shared catalog.
        row["target_sample"] = {key: sample.get(key) for key in
                                ("profile_id", "phase", "status", "auras", "statuses", "revision")}
    return row, new_sample


def resolve_condition_observation(receipt, samples, sources=()):
    """Expand one public receipt for review; never mutate stored evidence."""
    result = copy.deepcopy(receipt)
    if receipt.get("target_sample_ref"):
        sample = samples.get(receipt["target_sample_ref"])
        result["target_sample"] = copy.deepcopy(sample) if sample is not None else None
        if sample is None:
            result["observation_status"] = "missing_reference"
    source = next((row for row in sources if row.get("id") == receipt.get("fixed_source_id")), None)
    if source is not None:
        result["source"] = source.get("source")
        effect = next((row for row in source.get("effects", []) if row.get("id") == receipt.get("effect_id")), None)
        if effect is not None:
            result["predicate"] = copy.deepcopy(effect.get("target_predicate"))
    return result


def attach_target_observations(output, executor):
    if not executor.target_state_samples:
        return
    catalog = copy.deepcopy(executor.target_state_samples)
    output["target_state_samples"] = catalog
    if isinstance(output.get("result"), dict):
        output["result"]["target_state_samples"] = catalog
