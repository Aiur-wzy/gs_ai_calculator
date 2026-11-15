"""The pure certificate checks sources and explicit reference assumptions."""
import copy

import pytest

from genshin_engine.runtime_energy_trigger_compiler import (
    compile_energy_trigger_source,
    energy_trigger_compiler_caps,
)
from genshin_runtime.energy_trigger_identity import (
    energy_trigger_source_payload,
    fingerprint,
    generation_source_payload,
)
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_energy_dispatch import (  # noqa: F401
    context,
    trigger_db,
    trigger_repo,
)

# ruff: noqa: F811


def semantics(bundle):
    ident = bundle["parent_identity"]
    bridge = bundle["source_bundle"]["parent_bridge"]
    metadata = ident["metadata_candidate_id"] is not None
    element = bundle["source_query"]["reference_projection"]["unit"]["element"]
    return {"kind": "calibrated_single_contact_v1", "variant": bridge["variant"],
        "selection": copy.deepcopy(ident["selection"]), "param_index": ident["param_index"],
        "profile_fingerprint": "a" * 64, "source_dependency_fingerprint": "b" * 64,
        "metadata_source_fingerprint": "c" * 64 if metadata else None,
        "metadata_dependency_fingerprint": "d" * 64 if metadata else None,
        "hit_semantics": {"damage_system": "classic", "scaling_stat": "atk", "count": 1,
            "element": element, "damage_type_ids": ["damage.elemental_skill"]},
        "application_semantics": {"element": element, "gauge": 1},
        "contact_semantics": {"contact": "direct", "status": "assumed", "source": "独立工程接触"}}


def scenario():
    return {**energy_trigger_compiler_caps()["default_scenario"], "hitlag_policy": "zero_hitlag_reference",
            "status": "assumed", "source": "普通单目标、零hitlag工程域"}


def resign(bundle):
    bundle["semantic_dependency"] = energy_trigger_source_payload(bundle)
    bundle["source_fingerprint"] = fingerprint(bundle["semantic_dependency"])
    bundle["content_fingerprint"] = fingerprint({k: v for k, v in bundle.items() if k != "content_fingerprint"})


@pytest.mark.parametrize("variant,local,ticks", [("tap", False, 200001), ("hold", True, 500000), ("hold", False, 200001)])
def test_full_source_certificate(trigger_db, variant, local, ticks):
    bundle = context(trigger_db, variant, local).public_projection
    out = compile_energy_trigger_source(bundle, parent_semantics=semantics(bundle), scenario=scenario())
    assert out["status"] == "ready", out["issues"]
    assert out["trigger_certificate"]["isolation"]["isolation_ticks"] == ticks
    assert out["trigger_certificate"]["isolation"]["comparison"] == "strict_greater"
    assert out["whole_source_status"] == "partial"


def test_hitlag_must_be_explicit(trigger_db):
    bundle = context(trigger_db).public_projection
    out = compile_energy_trigger_source(bundle, parent_semantics=semantics(bundle),
                                        scenario=energy_trigger_compiler_caps()["default_scenario"])
    assert out["source_status"] == "ready" and out["status"] == "pending"
    assert out["issues"][0]["code"] == "energy_trigger_scenario_pending"


@pytest.mark.parametrize("change", ["fingerprint", "controller", "variant", "count", "parent", "unknown_scene"])
def test_tampering_and_scope_are_not_accepted(trigger_db, change):
    bundle = context(trigger_db, "hold", True).public_projection
    parent, scene = semantics(bundle), scenario()
    if change == "fingerprint": bundle["source_fingerprint"] = "a" * 64
    if change == "controller":
        bundle["source_bundle"]["parent_bridge"]["controller"]["raw_duration"] = 0
        resign(bundle)
    if change == "variant": parent["variant"] = "tap"
    if change == "count": parent["hit_semantics"]["count"] = 2
    if change == "parent": parent["selection"]["skill_id"] += 1
    if change == "unknown_scene": scene["source"] = ""
    out = compile_energy_trigger_source(bundle, parent_semantics=parent, scenario=scene)
    assert out["status"] == "pending"


def test_nan_is_controlled_and_no_input_mutation(trigger_db):
    bundle = context(trigger_db).public_projection
    before = copy.deepcopy(bundle)
    parent = semantics(bundle)
    parent["application_semantics"]["gauge"] = float("nan")
    out = compile_energy_trigger_source(bundle, parent_semantics=parent, scenario=scenario())
    assert out["status"] == "pending" and bundle == before


def test_relabelled_material_and_declared_hit_cannot_change_raw_attack_element(trigger_db):
    bundle = context(trigger_db, "hold", True).public_projection
    parent = semantics(bundle)
    query = bundle["source_query"]
    material = query["material"]["candidates"][0]
    material["payload"]["itemUse"][0]["useParam"][0] = "7"
    material["node_sha256"] = material["definition"]["node_sha256"] = fingerprint(material["payload"])
    query["reference_projection"]["unit"]["element"] = "anemo"
    query["source_fingerprint"] = fingerprint(generation_source_payload(query))
    query["content_fingerprint"] = fingerprint({k: v for k, v in query.items() if k != "content_fingerprint"})
    parent["hit_semantics"]["element"] = "anemo"
    parent["application_semantics"]["element"] = "anemo"
    resign(bundle)
    out = compile_energy_trigger_source(bundle, parent_semantics=parent, scenario=scenario())
    assert out["source_status"] == "ready"
    assert out["status"] == "pending"
    assert "同元素" in out["issues"][0]["message"]
