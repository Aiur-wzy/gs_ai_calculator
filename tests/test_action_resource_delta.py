"""B2 pure candidate tests; caller materials never grant source authority."""
import copy

import pytest

from genshin_db.runtime_capacity_delta import CapacityError, project_source_delta
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
    load_material,
)

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def case(name="Shenhe_Constellation_1", target=None):
    inventory = load_material("action-capacity__real-source-inventory.json")
    found = [row for row in inventory["candidates"] if row["definition"]["exact_key"] == name
             and (target is None or row["normalized_operation"]["skillID"] == target)]
    row = copy.deepcopy(found[0])
    declaration = row["declarations"][0]
    return inventory, row, declaration


def claim(inventory, row, declaration, *, status="reviewed"):
    from genshin_db.runtime_capacity_delta import source_fact_claim
    return {"id": "engineering-review", "revision": 1, "kind": "charge_capacity_bonus",
            "status": status, "claim": source_fact_claim(inventory, row, declaration),
            "review_scope": "exact_source_capacity_delta_only",
            "assumptions": ["Unknown raw flag is not globally decoded"],
            "evidence_tier": "same_snapshot_raw_and_explicit_original_description"}


@pytest.mark.parametrize("name,base", [("Shenhe_Constellation_1", 1), ("Sucrose_Constellation_1", 1), ("Xiao_Constellation_1", 2)])
def test_three_real_sources_have_distinct_source_bound_addition(name, base):
    inv, row, declaration = case(name)
    result = project_source_delta(inv, row, declaration, claim(inv, row, declaration), constellation=declaration["constellation_rank"])
    assert result["status"] == "known_delta"
    assert result["base_capacity"] == base and result["amount"] == 1
    assert result["candidate_projected_capacity"] == base + 1
    assert result["capacity"] is None and result["capacity_closure_status"] == "not_evaluated"
    assert result["requires_fresh_db_admission"] is True
    assert result["verification_scope"] == "materialized_consistency_no_source_authority"


def test_inactive_constellation_does_not_grant_increment():
    inv, row, declaration = case()
    result = project_source_delta(inv, row, declaration, claim(inv, row, declaration), constellation=0)
    assert result["status"] == "inactive" and result["amount"] == 0
    assert result["capacity"] is None


@pytest.mark.parametrize("status", ["pending", "revoked", "stale"])
def test_unapproved_fact_cannot_become_known_from_its_literal_value(status):
    inv, row, declaration = case()
    result = project_source_delta(inv, row, declaration, claim(inv, row, declaration, status=status), constellation=1)
    assert result["status"] == "pending" and result["amount"] is None


@pytest.mark.parametrize("value", [True, 1.0, "1", -1, 2**100])
def test_invalid_delta_does_not_coerce(value):
    inv, row, declaration = case()
    row["normalized_operation"]["pointDelta"] = value
    with pytest.raises(CapacityError):
        project_source_delta(inv, row, declaration, claim(inv, row, declaration), constellation=1)


def test_raw_parent_change_with_unchanged_node_is_rejected():
    inv, row, declaration = case()
    fact = claim(inv, row, declaration)
    inv["sources"][row["source_path"]]["raw"]["unrelated"] = []
    with pytest.raises(CapacityError):
        project_source_delta(inv, row, declaration, fact, constellation=1)


def test_wrong_skill_depot_relation_is_rejected():
    inv, row, declaration = case()
    fact = claim(inv, row, declaration)
    declaration["depot"]["record"]["skills"][1] = 999
    with pytest.raises(CapacityError):
        project_source_delta(inv, row, declaration, fact, constellation=1)


def test_distinct_xiao_target_operations_do_not_collapse_by_equal_amount():
    values = []
    for target in (10262, 10263):
        inv, row, declaration = case("Xiao_Constellation_1", target)
        values.append(project_source_delta(inv, row, declaration, claim(inv, row, declaration), constellation=1))
    assert values[0]["operation_identity"] != values[1]["operation_identity"]
    assert [value["target_skill_id"] for value in values] == [10262, 10263]
    assert all(value["shared_cooldown_group"] is None for value in values)


def test_result_and_claim_do_not_alias_inputs():
    inv, row, declaration = case()
    fact = claim(inv, row, declaration)
    before = copy.deepcopy((inv, row, declaration, fact))
    result = project_source_delta(inv, row, declaration, fact, constellation=1)
    result["source_fact"]["claim"]["amount"] = 99
    assert (inv, row, declaration, fact) == before
