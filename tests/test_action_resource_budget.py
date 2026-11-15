"""Preallocation limits; original complete materials and usage remain identical."""
import copy
import sys

import pytest

from genshin_db.runtime_catalog import fingerprint
from genshin_runtime import action_root_semantics as roots
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
    load_material,
)
from tests.test_action_resource_materials import weak_case

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def observed(value):
    peak = 0

    def trace(frame, event, arg):
        nonlocal peak
        if frame.f_code is roots.bounded.__code__ and event == "line":
            peak = max(peak, len(frame.f_locals.get("stack", [])))
        return trace

    previous = sys.gettrace()
    result, code = None, None
    try:
        sys.settrace(trace)
        result = roots.bounded(value)
    except roots.BaseBindingError as error:
        code = error.code
    finally:
        sys.settrace(previous)
    return result, code, peak


def test_same_published_limits():
    assert (roots.MAX_WORK, roots.MAX_DEPTH, roots.MAX_SAFE_BYTES, roots.MAX_BYTES) == (
        200000, 64, 8 * 1024 * 1024, 2 * 1024 * 1024)


def test_original_large_width_refuses_before_allocating_children():
    result, code, peak = observed([None] * 200001)
    assert result is None and code == "binding_budget" and peak == 1


@pytest.mark.parametrize("value,work", [([None] * 4, 5), ({"a": None, "b": None}, 5),
                                        ([None, [None] * 4], 7),
                                        ([None, {"a": [None, None]}], 7)])
def test_exact_work_and_one_over_include_pending_siblings_and_dict_keys(monkeypatch, value, work):
    monkeypatch.setattr(roots, "MAX_WORK", work)
    out, code, peak = observed(value)
    assert code is None and out["visited_nodes"] == work and peak <= work
    monkeypatch.setattr(roots, "MAX_WORK", work - 1)
    out, code, peak = observed(value)
    assert out is None and code == "binding_budget" and peak <= work - 1


def test_pending_sibling_not_deducted_twice_or_ignored(monkeypatch):
    monkeypatch.setattr(roots, "MAX_WORK", 6)
    out, code, peak = observed([None, [None] * 4])
    assert out is None and code == "binding_budget"
    assert peak == 3, "nested batch must not be pushed while a sibling is pending"


@pytest.mark.parametrize("depth", [0, 1, 8, 64])
def test_depth_boundary_without_reserving_nonexistent_children(monkeypatch, depth):
    monkeypatch.setattr(roots, "MAX_DEPTH", depth)
    value = None
    for _ in range(depth):
        value = [value]
    out, code, peak = observed(value)
    assert code is None and out["visited_nodes"] == depth + 1
    assert peak <= depth + 1
    out, code, peak = observed([value])
    assert out is None and code == "binding_budget" and peak <= depth + 1


def test_empty_container_at_maximum_depth_remains_legal(monkeypatch):
    monkeypatch.setattr(roots, "MAX_DEPTH", 0)
    assert roots.bounded([])["visited_nodes"] == 1
    assert roots.bounded({})["visited_nodes"] == 1


def test_known_container_bytes_checked_before_batch_enqueue(monkeypatch):
    monkeypatch.setattr(roots, "MAX_SAFE_BYTES", 9)
    monkeypatch.setattr(roots.json, "dumps", lambda *_a, **_k: pytest.fail("oversize value serialized"))
    out, code, peak = observed([None] * 4)  # container alone has estimate 10
    assert out is None and code == "binding_budget" and peak == 1


@pytest.mark.parametrize("value,safe_bytes", [("冰" * 4, 26), ([None], 9), ({"a": None}, 17)])
def test_safe_encoding_exact_boundary_then_one_over(monkeypatch, value, safe_bytes):
    monkeypatch.setattr(roots, "MAX_SAFE_BYTES", safe_bytes)
    assert roots.bounded(value)["safe_encoding_bytes"] == safe_bytes
    monkeypatch.setattr(roots, "MAX_SAFE_BYTES", safe_bytes - 1)
    monkeypatch.setattr(roots.json, "dumps", lambda *_a, **_k: pytest.fail("oversize value serialized"))
    with pytest.raises(roots.BaseBindingError) as error:
        roots.bounded(value)
    assert error.value.code == "binding_budget"


def test_exact_serialized_limit_not_replaced_by_estimate(monkeypatch):
    monkeypatch.setattr(roots, "MAX_BYTES", 2)
    assert roots.bounded([])["serialized_bytes"] == 2
    monkeypatch.setattr(roots, "MAX_BYTES", 1)
    with pytest.raises(roots.BaseBindingError) as error:
        roots.bounded([])
    assert error.value.code == "binding_budget"


def test_cycle_still_rejected_and_shared_acyclic_subtrees_counted_per_occurrence():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(roots.BaseBindingError) as error:
        roots.bounded(cycle)
    assert error.value.code == "binding_shape"
    shared = [None]
    assert roots.bounded([shared, shared])["visited_nodes"] == 5


@pytest.mark.parametrize("value", [{1: None}, float("nan"), 2**64, (1,)])
def test_strict_json_shape_still_rejected(value):
    with pytest.raises(roots.BaseBindingError) as error:
        roots.bounded(value)
    assert error.value.code == "binding_shape"


@pytest.mark.parametrize("index", range(4))
def test_four_complete_archived_related_materials_and_usage_exactly_unchanged(index):
    expected = load_material("related-root-r1-expected.json")["cases"][index]
    material, record, head = weak_case(index)
    inputs = (material, record, head)
    before = copy.deepcopy(inputs)
    assert fingerprint(inputs) == expected["input_fingerprint"]
    result = roots.match_related_root_material(*inputs)
    assert result == expected["output"]
    assert fingerprint(result) == expected["output_fingerprint"]
    assert before == inputs
