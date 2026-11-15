"""Independent source/parent rejection tests, with no formal database access."""
from __future__ import annotations

import copy
import json

import pytest
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_energy_dispatch import (  # noqa: F401
    context,
    identity,
    origin,
    trigger_db,
    trigger_repo,
)
from test_runtime_object_index import build, write

from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_energy_dispatch import get_energy_dispatch_context
from genshin_engine.runtime_energy_trigger_compiler import compile_energy_trigger_source
from genshin_runtime.energy_trigger_identity import LIMITS, recipe

# ruff: noqa: F811


def assumptions():
    return {"target_kind": "ordinary_non_animal_monster", "drop_allowed": True, "initial_controller": "inactive",
        "pickup": "all_at_once", "hitlag_policy": "zero_hitlag_reference", "no_external_controller_changes": True,
        "all_party_alive": True, "status": "assumed", "source": "Independent zero-hitlag engineering reference"}


def parent(bundle):
    identity = bundle["parent_identity"]
    local = identity["selection"]["character_id"] == 10000063
    return {"kind": "calibrated_single_contact_v1", "variant": "hold" if local else "tap",
        "selection": copy.deepcopy(identity["selection"]), "param_index": 1 if local else 0,
        "profile_fingerprint": "1" * 64, "source_dependency_fingerprint": "2" * 64,
        "metadata_source_fingerprint": None if local else "3" * 64,
        "metadata_dependency_fingerprint": None if local else "4" * 64,
        "hit_semantics": {"damage_system": "classic", "scaling_stat": "atk", "count": 1,
                          "element": "cryo" if local else "anemo", "damage_type_ids": ["damage.elemental_skill"]},
        "application_semantics": {"element": "cryo" if local else "anemo", "application": "none"},
        "contact_semantics": {"freeze_interaction": "none", "status": "assumed", "source": "Independent contact declaration"}}


@pytest.mark.parametrize("field,value", [("count", True), ("count", 2), ("scaling_stat", "hp"),
    ("damage_type_ids", ["damage.elemental_burst"]), ("damage_system", "transformative"), ("element", "cryo")])
def test_review_same_source_cannot_bind_another_damage_meaning(trigger_db, field, value):
    bundle = context(trigger_db).public_projection
    semantics = parent(bundle)
    semantics["hit_semantics"][field] = value
    out = compile_energy_trigger_source(bundle, parent_semantics=semantics, scenario=assumptions())
    assert out["status"] == "pending"
    assert out["trigger_status"] == "pending"
    assert out["issues"]


@pytest.mark.parametrize("field,value", [("drop_allowed", 1), ("no_external_controller_changes", None),
    ("all_party_alive", 1), ("hitlag_policy", None), ("initial_controller", "unknown")])
def test_review_unknown_or_boolean_lookalike_scene_preserves_only_numeric_source(trigger_db, field, value):
    bundle = context(trigger_db).public_projection
    scene = assumptions()
    scene[field] = value
    out = compile_energy_trigger_source(bundle, parent_semantics=parent(bundle), scenario=scene)
    assert out["source_status"] == "ready" and out["resource_projection"]["count"] == 3
    assert out["status"] == out["trigger_status"] == "pending"
    assert out["whole_source_status"] == "partial"


@pytest.mark.parametrize("mismatch", ["character", "param", "metadata_variant", "no_metadata"])
def test_review_parent_bridge_uses_exact_identity_not_value_or_label(trigger_db, mismatch):
    ident = identity(trigger_db)
    if mismatch == "character":
        ident = identity(trigger_db, "hold", True)
    elif mismatch == "param":
        ident["param_index"] = 1
    elif mismatch == "metadata_variant":
        ident["metadata_candidate_id"] = identity(trigger_db, "hold")["metadata_candidate_id"]
    else:
        ident["metadata_candidate_id"] = None
    out = get_energy_dispatch_context(trigger_db, origin_reference_id=origin(trigger_db), parent_identity=ident).public_projection
    assert out["status"] == "pending" and out["issues"]


@pytest.mark.parametrize("mutation", ["second_writer", "predicate_field", "attack_order"])
def test_review_full_shenhe_parent_and_writer_list_are_semantic_units(trigger_db, trigger_repo, mutation):
    resource = recipe()
    if mutation == "second_writer":
        item = resource["fixture_evidence"]["talents"][1]
        changed = copy.deepcopy(item["raw"])
        changed.append(copy.deepcopy(changed[1]))
        write(trigger_repo, item["source_path"], {item["name"]: changed})
    else:
        path = resource["parents"]["local_hold"]["source_path"]
        content = json.loads((trigger_repo / path).read_text("utf8"))
        original = content[0]["Default"]
        if mutation == "predicate_field":
            opaque = original["GPPBBDEICKP"]["Shenhe_ElementalArt_ElemBall_Handler"]["LPMFNGFGMCE"][0]["MJKPCGCAPLK"][1]
            assert opaque["$type"] == "BIMGEEAOLLH"
            opaque["BDHBNCEEPPK"] = "Caster"
        else:
            # This moves the real attack relative to all its peer operations.
            original["ECFFDLHMCLK"].reverse()
        write(trigger_repo, path, content)
    build(trigger_db, trigger_repo, upstream_commit=resource["commit"])
    out = context(trigger_db, "hold", True).public_projection
    assert out["status"] == "pending"


def test_review_byte_budget_has_exact_inclusive_boundary(trigger_db, monkeypatch):
    bundle = context(trigger_db).public_projection
    encoded = len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf8"))
    monkeypatch.setitem(LIMITS, "max_bytes", encoded)
    assert compile_energy_trigger_source(bundle, parent_semantics=parent(bundle), scenario=assumptions())["status"] == "ready"
    monkeypatch.setitem(LIMITS, "max_bytes", encoded - 1)
    failed = compile_energy_trigger_source(bundle, parent_semantics=parent(bundle), scenario=assumptions())
    assert failed["status"] == "pending" and failed["trigger_certificate"] is None


def test_review_wrong_snapshot_is_fatal_not_other_source_pending(trigger_db):
    ident = identity(trigger_db)
    ident["snapshot_id"] = "0" * 64
    with pytest.raises(RuntimeConfigError) as error:
        get_energy_dispatch_context(trigger_db, origin_reference_id=origin(trigger_db), parent_identity=ident)
    assert error.value.status == 409


def test_review_edge_limit_precedes_full_reference_expansion(trigger_db, monkeypatch):
    from genshin_db import runtime_queries
    from genshin_db.runtime_energy_dispatch import _Reader
    bundle = context(trigger_db).public_projection
    query = bundle["source_query"]
    owner = query["origin"]["owner"] or query["origin"]["parent"]
    with runtime_queries._read(trigger_db, "release", None) as (conn, state):
        reader = _Reader(conn, state, query)
        monkeypatch.setitem(LIMITS, "max_edges", 1)
        monkeypatch.setattr(runtime_queries, "_references", lambda *a, **k: pytest.fail("expanded refs before budget"))
        with pytest.raises(RuntimeConfigError) as error:
            reader.references(owner["definition"]["definition_id"])
        assert error.value.reason == "response_too_large"


def test_review_reference_edge_budget_is_cumulative_across_parents(trigger_db, monkeypatch):
    from genshin_db import runtime_queries
    from genshin_db.runtime_energy_dispatch import _Reader
    bundle = context(trigger_db).public_projection
    query = bundle["source_query"]
    owner = query["origin"]["owner"] or query["origin"]["parent"]
    with runtime_queries._read(trigger_db, "release", None) as (conn, state):
        reader = _Reader(conn, state, query)
        refs = reader.references(owner["definition"]["definition_id"])
        # Each new request must include already-read identities in its budget.
        reader.refs["unrelated-prior-edge"] = {"reference_id": "unrelated-prior-edge"}
        monkeypatch.setitem(LIMITS, "max_edges", len(refs))
        with pytest.raises(RuntimeConfigError) as error:
            reader.references(owner["definition"]["definition_id"])
        assert error.value.reason == "response_too_large"
