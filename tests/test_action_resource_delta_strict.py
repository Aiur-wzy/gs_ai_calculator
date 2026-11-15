"""Additional malformed JSON material attacks, not new game-source approval."""
import copy

import pytest

from genshin_db.runtime_capacity_delta import (
    LIMITS,
    Budget,
    CapacityError,
    project_source_delta,
)
from genshin_db.runtime_catalog import fingerprint
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
)
from tests.test_action_resource_delta import case, claim

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


@pytest.mark.parametrize("location", ["operation", "raw_operations"])
@pytest.mark.parametrize("value", [True, 1.0])
def test_saved_raw_integer_mismatch_rejected(location, value):
    inv, row, dec = case()
    fact = claim(inv, row, dec)
    row[location] = copy.deepcopy(row[location])
    op = row[location][0] if location == "raw_operations" else row[location]
    op["PDMDLPLJFPL"] = value
    with pytest.raises(CapacityError, match="原始操作"):
        project_source_delta(inv, row, dec, fact, constellation=1)


@pytest.mark.parametrize("location", ["operation", "raw_operations"])
@pytest.mark.parametrize("value", [1, 1.0])
def test_saved_raw_flag_mismatch_rejected(location, value):
    inv, row, dec = case()
    fact = claim(inv, row, dec)
    row[location] = copy.deepcopy(row[location])
    op = row[location][0] if location == "raw_operations" else row[location]
    op["GBPFBNHLOFD"] = value
    with pytest.raises(CapacityError):
        project_source_delta(inv, row, dec, fact, constellation=1)


def rehash(record):
    record["node_sha256"] = fingerprint(record["record"])
    record["definition"]["node_sha256"] = record["node_sha256"]


@pytest.mark.parametrize("path,field", [("avatar", "id"), ("avatar", "skillDepotId"),
                                      ("depot", "id"), ("talent", "talentId"), ("skill", "id")])
def test_rehashed_float_record_id_cannot_join_integer_selection(path, field):
    inv, row, dec = case()
    material = row["target_skill"] if path == "skill" else dec[path]
    material["record"][field] = float(material["record"][field])
    rehash(material)
    with pytest.raises(CapacityError):
        claim(inv, row, dec)


@pytest.mark.parametrize("field", ["skills", "subSkills", "talents"])
def test_rehashed_float_list_id_is_not_exact_membership(field):
    inv, row, dec = case()
    values = dec["depot"]["record"][field]
    values[0] = float(values[0])
    rehash(dec["depot"])
    with pytest.raises(CapacityError):
        claim(inv, row, dec)


@pytest.mark.parametrize("value", [True, 1.0])
def test_fact_amount_cannot_use_python_numeric_equality(value):
    inv, row, dec = case()
    fact = claim(inv, row, dec)
    fact["claim"]["amount"] = value
    result = project_source_delta(inv, row, dec, fact, constellation=1)
    assert result["status"] == "pending" and result["amount"] is None


@pytest.mark.parametrize("payload", [float("nan"), float("inf"), {1: "bad"}, (1,), "\ud800"])
def test_non_json_material_is_rejected(payload):
    inv, row, dec = case()
    row["unknown_material"] = payload
    with pytest.raises(CapacityError):
        claim(inv, row, dec)


def test_cyclic_material_is_rejected():
    inv, row, dec = case()
    row["cycle"] = row
    with pytest.raises(CapacityError):
        claim(inv, row, dec)


def test_work_budget_is_cumulative_across_scans():
    budget = Budget()
    budget.charge(LIMITS["work"] - 1)
    budget.scan(None)
    with pytest.raises(CapacityError):
        budget.scan(None)


def test_depth_budget_has_no_recursive_python_failure():
    value = None
    for _ in range(LIMITS["depth"] + 1):
        value = [value]
    with pytest.raises(CapacityError):
        Budget().scan(value)


def test_bytes_budget_before_encoding_large_string():
    with pytest.raises(CapacityError):
        Budget().scan("a" * LIMITS["bytes"])
    # Compact UTF-8 does not sixfold-expand ordinary ASCII, but control
    # characters still require JSON escaping and must hit the real byte cap.
    with pytest.raises(CapacityError):
        Budget().scan("\0" * (LIMITS["bytes"] // 6 + 1))
