"""Independent L2a math/budget cases, no queue or game-source assertion."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_engine.attribute_graph import AttributeGraphLedger
from genshin_engine.attribute_ledger import AttributeLedgerError
from tests.test_attribute_graph import expr, frame, model, node, put, read


@pytest.mark.parametrize("change", ["bool_factor", "extra_field", "unknown_input", "nan"])
def test_closed_linear_grammar_is_not_a_free_formula_channel(change):
    data = model()
    value = expr()
    if change == "bool_factor": value["factor"] = True
    elif change == "extra_field": value["operator"] = "eval"
    elif change == "unknown_input": value["input"]["value"] = 200
    else: value["factor"] = float("nan")
    put(data, node("bad", value=value))
    with pytest.raises(AttributeLedgerError):
        AttributeGraphLedger(data)


def test_zero_factor_or_finite_cap_cannot_solve_a_total_self_cycle():
    data = model()
    put(data, node("self", value=expr("a", "total", factor=0, max=1)))
    result = read(AttributeGraphLedger(data))
    assert result["status"] == "pending" and "value" not in result
    assert result["issues"][0]["code"] == "attribute_dependency_cycle"
    assert "self" in {row.get("contribution_id") for row in result["issues"][0]["dependency_path"]}


def test_next_origin_cannot_be_borrowed_even_when_time_epoch_are_smaller():
    data = model()
    data["frames"] = [frame("now", 5, 5), {**frame("other", 1, 1), "origin": 1, "run_id": "1:other"}]
    put(data, node("bad", value=expr(sample="other")), fid="now")
    service = AttributeGraphLedger(data)
    assert read(service, fid="now")["issues"][0]["code"] == "attribute_forward_frame"
    assert read(service, basis="nonextra", fid="now")["value"] == 100


def test_missing_dependency_retry_catalog_and_receipt_hash_remain_immutable():
    data = model()
    data["frames"] = [frame("now", 2, 2)]
    put(data, node("dependent", value=expr(sample="past")), fid="now")
    service = AttributeGraphLedger(data)
    first = read(service, fid="now")
    frozen = copy.deepcopy(first)
    service.adopt_frame(frame("past", 1, 1))
    second = read(service, fid="now")
    assert second["value"] == 140 and first == frozen
    catalog = service.receipt_catalog()
    ids = {row["receipt_id"] for row in catalog}
    assert first["receipt_id"] in ids and second["receipt_id"] in ids
    assert second["contributions"][0]["input_receipt_id"] in ids
    for receipt in catalog:
        raw = {key: value for key, value in receipt.items() if key != "receipt_id"}
        digest = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()
        assert digest == receipt["receipt_id"]
    second["contributions"].clear()
    assert len(read(service, fid="now")["contributions"]) == 1


def test_graph_registration_and_query_share_single_execution_budget(monkeypatch):
    limits = {'execution_work': 5}
    data = model()
    put(data, node("gain"))
    service = AttributeGraphLedger(data, limits=limits)
    service.reserve_capture_work(3)
    service.adopt_frame(frame("other", 1, 1))
    result = read(service)
    assert result["status"] == "pending" and "value" not in result
    assert result["issues"][0]["code"] == "attribute_work_limit"
    usage = service.usage()
    assert usage["execution_work"] == usage["query_work"] + usage["registration_work"] + usage["capture_work"]
    assert usage["execution_work"] <= 5


def test_nonextra_branch_pruning_cannot_discard_static_duplicate_delivery():
    data = model()
    value = node("duplicate", value=expr(sample="missing"))
    put(data, value)
    data["initials"][0]["included_components"] = [{"component": copy.deepcopy(value["component"]),
        "value": 40, "nonextra_inclusion": "excluded"}]
    result = read(AttributeGraphLedger(data), basis="nonextra")
    assert result["status"] == "invalid" and result["issues"][0]["code"] == "attribute_duplicate_delivery"


def test_graph_failure_does_not_leave_dfs_stack_or_block_unrelated_known_closure():
    data = model()
    put(data, node("a_to_b", "b", expr("c", "total")), node("b_to_c", "c", expr("b", "total")))
    service = AttributeGraphLedger(data)
    assert read(service, "b")["status"] == "pending"
    assert service._stack == []
    assert read(service)["value"] == 100
