"""A new dispatch shape does not widen or corrupt EC12 parent eligibility."""
import copy

import pytest
from test_passive_sources_app import (  # noqa: F401
    accept,
    passive_attack_db,
    passive_plan,
)

from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.passive_sources import _parent_proof

# ruff: noqa: F811


@pytest.mark.parametrize("element,blocked", [("cryo", False), ("anemo", True)])
def test_provider_multi_shape_never_borrows_single_e_passive_qualification(passive_plan, element, blocked):
    plan, db = passive_plan
    accept(plan, db)
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "ready"
    working = copy.deepcopy(plan)
    working["mechanics_model"] = copy.deepcopy(report["mechanics_model"])
    template = copy.deepcopy(report["plan"]["resolved_template"])
    query = report["passive_compilations"][0]["source"]
    records = copy.deepcopy(report["action_event_compilations"])
    baseline = _parent_proof(working, template, "a", query, report["attack_metadata_compilations"], records)
    assert baseline[0] == [] and baseline[1]
    # Directly exercise the finalized-parent guard, not DB dispatch recognition:
    # a second same-provider multi shape is already materialized by its compiler.
    working["action_event_profiles"].append({"id": "other.multi", "kind": "calibrated_multi_contact_v1", "channels": []})
    records.append({"profile_id": "other.multi", "actor": "a", "kind": "calibrated_multi_contact_v1", "status": "ready"})
    template["hits"].append({"id": "other.prototype", "character": "a", "element": element})
    working["mechanics_model"]["elemental_model"]["profiles"].append({"id": "other.app", "application": "apply", "element": element})
    working["mechanics_model"]["hit_patterns"].append({"id": "other.pattern", "prototype_hit_id": "other.prototype",
        "action_id": "cast", "application": {"profile_id": "other.app", "target_id": "enemy", "landed": True}})
    errors, parents, dependencies = _parent_proof(working, template, "a", query, report["attack_metadata_compilations"], records)
    assert parents == baseline[1] and dependencies == baseline[2]
    assert bool(errors) is blocked
    if blocked:
        assert len(errors) == 1 and "无受审父链的风附着" in errors[0]
