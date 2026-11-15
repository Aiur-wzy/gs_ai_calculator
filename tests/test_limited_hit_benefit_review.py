"""Independent EC18 DB and App scope checks; GUI author assertions live elsewhere."""
from __future__ import annotations

import copy
import json

import pytest
from test_runtime_limited_hit_benefit import (  # noqa: F401
    benefit_db,
    benefit_repo,
    query,
)

from genshin_app.equipment import limited_hit_benefit_sources as app
from genshin_db import runtime_limited_hit_benefit as dbmod
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_runtime.limited_hit_benefit_identity import (
    LIMITS,
    fingerprint,
    limited_hit_benefit_source_payload,
    verify_limited_hit_benefit_projection,
)
from tests.test_pipeline import fake_repo  # noqa: F401

# ruff: noqa: F811


def test_review_source_origins_point_to_distinct_proud_slots_and_unique_writers(benefit_db):
    value = query(benefit_db).public_projection
    assert value["status"] == "ready"
    entries = {row["definition"]["definition_id"]: row for row in value["source_bundle"]["entries"]}
    for name, index, expected in (("coefficient", 2, .8218079805374146), ("duration_seconds", 4, 15), ("capacity", 6, 7)):
        projection = value["projection"][name]
        origin = projection["origin"]
        assert origin["param_index"] == index
        proud, talent = entries[origin["parameter_entry_id"]], entries[origin["writer_entry_id"]]
        assert origin["parameter_pointer"] == proud["json_pointer"] + f"/paramList/{index}"
        assert proud["payload"]["paramList"][index] == expected == projection["value"]
        writer_index = int(origin["writer_pointer"].rsplit("/", 1)[1])
        writer = talent["payload"][writer_index]
        assert writer["PCABAADALND"] == f"%{index + 1}"
        assert writer["PEOJKHKHAKL"] == origin["special"]
    assert value["whole_source_status"] == "partial"
    assert value["execution_support"] == "not_evaluated"


@pytest.mark.parametrize("change", [
    {"character_id": True}, {"param_index": True}, {"param_index": -1},
    {"constellation": None}, {"talent_level": False}, {"slot": "skill", "skill_id": 10632},
])
def test_review_invalid_selection_cannot_enter_database(monkeypatch, change):
    from genshin_db import runtime_queries
    def forbidden(*args, **kwargs):
        pytest.fail("invalid selection opened database")
    monkeypatch.setattr(runtime_queries, "_read", forbidden)
    with pytest.raises(ValueError):
        query("does-not-exist.db", **change)


@pytest.mark.parametrize("kind", ["wrong_param_use", "missing_modifier", "duplicate_reference", "changed_parent_scope"])
def test_review_rehashed_source_still_requires_complete_parent_and_use(kind):
    from pathlib import Path
    profile = json.loads((Path(__file__).parent / "fixtures/runtime_limited_hit_benefit_current.json").read_text("utf8"))
    if kind == "wrong_param_use":
        profile["projection"]["capacity"]["origin"] = copy.deepcopy(profile["projection"]["duration_seconds"]["origin"])
    elif kind == "missing_modifier":
        omitted = profile["source_bundle"]["entry_ids"]["modifiers"].pop()
        profile["source_bundle"]["entries"] = [row for row in profile["source_bundle"]["entries"] if row["definition"]["definition_id"] != omitted]
    elif kind == "duplicate_reference":
        profile["source_bundle"]["references"][1] = copy.deepcopy(profile["source_bundle"]["references"][0])
    else:
        entry = next(row for row in profile["source_bundle"]["entries"] if row["definition"]["definition_kind"] == "modifier")
        entry["definition"]["owner_definition_id"] = "bad"
    profile["semantic_dependency"] = limited_hit_benefit_source_payload(profile)
    profile["source_fingerprint"] = fingerprint(profile["semantic_dependency"])
    profile.pop("content_fingerprint")
    profile["content_fingerprint"] = fingerprint(profile)
    with pytest.raises(ValueError):
        verify_limited_hit_benefit_projection(profile)


def test_review_reference_budget_rejects_before_full_reference_materialization(benefit_db, monkeypatch):
    from genshin_db import runtime_queries
    monkeypatch.setitem(LIMITS, "max_edges", 0)
    def forbidden(*args, **kwargs):
        pytest.fail("over-budget reference set was materialized")
    monkeypatch.setattr(runtime_queries, "_references", forbidden)
    with pytest.raises(RuntimeConfigError) as error:
        query(benefit_db)
    assert error.value.reason == "response_too_large"


def test_review_node_budget_rejects_before_next_raw_node(benefit_db, monkeypatch):
    from genshin_db.runtime_queries import _Nodes
    monkeypatch.setitem(LIMITS, "max_nodes", 0)
    monkeypatch.setattr(_Nodes, "node", lambda *args, **kwargs: pytest.fail("over-budget node decompressed"))
    with pytest.raises(RuntimeConfigError) as error:
        query(benefit_db)
    assert error.value.reason == "response_too_large"


def scope(hit_type="damage.normal_attack"):
    hit = {"id": "ice", "character": "receiver", "element": "cryo", "damage_type_ids": [hit_type],
           "damage_system": "classic", "count": 1}
    plan = {"mechanics_model": {"hit_events": [{"hit_id": "ice", "action_id": "cast", "order": 10}],
                                "hit_patterns": [], "rules": []},
            "rotation_tuning": {"buff_hit_matches": []}, "effect_bindings": []}
    return plan, {"hits": [hit], "buffs": []}


@pytest.mark.parametrize("kind", ["normal_attack", "charged_attack", "plunging_attack", "elemental_skill", "elemental_burst"])
def test_review_all_five_actual_registry_categories_are_supported(kind):
    plan, template = scope("damage." + kind)
    assert app._consumer_scope(plan, template)[0]["hit_id"] == "ice"


@pytest.mark.parametrize("key,value", [("count", 2), ("damage_system", "transformative"),
                                      ("damage_type_ids", ["damage.other"]), ("damage_type_ids", [])])
def test_review_potential_consumer_is_not_silently_ignored(key, value):
    plan, template = scope()
    template["hits"][0][key] = value
    with pytest.raises(ValueError):
        app._consumer_scope(plan, template)


def test_review_typed_constant_buff_duplicate_cannot_hide_in_nested_effect():
    plan, template = scope()
    plan["effect_bindings"] = [{"binding_id": "old", "provider_character_id": "owner",
        "owner": {"kind": "character_skill", "character_id": 10000063},
        "parts": [{"part_id": "base", "state": "resolved", "rule": {"kind": "constant", "value": 1},
                   "delivery": {"kind": "buff", "effect": {"benefit_type_id": "benefit.base_add.all"}}}]}]
    with pytest.raises(ValueError):
        app._duplicates(plan, template, {"id": "new"}, "owner", {"selection": {"character_id": 10000063}})
    plan["effect_bindings"][0]["parts"][0]["state"] = "excluded"
    app._duplicates(plan, template, {"id": "new"}, "owner", {"selection": {"character_id": 10000063}})


@pytest.mark.parametrize("shape", ["filter", "quota", "conversion"])
def test_review_current_provider_rejects_skipped_atk_dependency_but_other_character_is_unrelated(shape):
    plan, template = scope()
    effect = {"id": "atk", "benefit_type_id": "benefit.stat_add.atk", "value": 500}
    rule = {"id": "panel", "owner": "owner", "beneficiaries": {"mode": "characters", "characters": ["owner"]}, "effects": [effect]}
    if shape == "filter":
        effect["filter"] = {"event": "hit", "phase": "pre"}
    elif shape == "quota":
        rule["quota"] = {"capacity": 7}
    else:
        effect["value"] = {"kind": "stat", "stat": "atk"}
    plan["mechanics_model"]["rules"] = [rule]
    with pytest.raises(ValueError):
        app._provider_atk_dependencies(plan, template, "owner")
    rule["beneficiaries"]["characters"] = ["unrelated"]
    app._provider_atk_dependencies(plan, template, "owner")


def test_review_manual_all_constant_remains_supported_but_local_hit_scope_is_pending():
    plan, template = scope()
    effect = {"id": "atk", "zone": "stat_add", "stat": "atk", "value": 500, "applies_to": {"mode": "all"}}
    template["buffs"] = [{"id": "manual", "effects": [effect], "target": "owner"}]
    app._provider_atk_dependencies(plan, template, "owner")
    effect["applies_to"] = {"mode": "hits", "hit_ids": ["ice"]}
    with pytest.raises(ValueError):
        app._provider_atk_dependencies(plan, template, "owner")
    template["buffs"][0]["target"] = "unrelated"
    app._provider_atk_dependencies(plan, template, "owner")


def test_review_public_caps_explicitly_keep_execution_unproven():
    caps = dbmod.limited_hit_benefit_capabilities()
    assert caps["source_ready_is_execution_ready"] is False
    assert caps["whole_source_status"] == "partial"
    assert caps["execution_support"] == "not_evaluated"
