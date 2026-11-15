"""App lifecycle authority and finite cross-resource exclusion graph."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.state_runtime import prepare_state_runtime
from tests.test_state_runtime_mechanics import model


def graph():
    data = model()
    rules = {r["id"]: r for r in data["rules"]}
    return data, rules, set(rules), {"buff": ("source", "field")}


def invoke(data, rules, active, linked, *, excluded=(), reviews=None, pools=None,
           pool_adoptions=(), pool_disabled_rules=()):
    issues = []
    if reviews is None:
        reviews = {r["source_id"]: {"mode": "manual_context", "context_fingerprint": "current", "note": "工程全图复核"} for r in rules.values()}
    result = prepare_state_runtime(data, rules, active, linked, set(excluded), lambda *args: issues.append(args),
        source_reviews=reviews, context_fingerprint="current", pools=pools,
        pool_adoptions=list(pool_adoptions), pool_disabled_rules=set(pool_disabled_rules))
    return *result, issues


def test_current_full_context_adopts_without_mutating_native_graph():
    args = graph()
    before = copy.deepcopy(args)
    state, pools, disabled, records, pool_records, issues = invoke(*args)
    assert state == args[0]["state_runtime"] and pools is None and not disabled and not issues
    assert all(r["own_source_status"] == r["effective_status"] == "ready" for r in records)
    assert args == before and pool_records == []


@pytest.mark.parametrize("review", [{"mode": "source_only"}, {"mode": "manual_context", "context_fingerprint": "old", "note": "旧复核"}, {}])
def test_old_source_identity_cannot_approve_state_graph(review):
    args = graph()
    state, _, disabled, records, _, issues = invoke(*args, reviews={"source:field": review})
    assert state is None and disabled == {"buff"} and issues
    assert all(r["effective_status"] == "pending" for r in records)


def test_explicit_exclusion_removes_full_group_and_owned_rule():
    args = graph()
    args[2].clear()
    state, _, disabled, records, _, issues = invoke(*args, excluded={"buff"})
    assert state is None and disabled == {"buff"} and not issues
    assert all(r["effective_status"] == "excluded" for r in records)


def test_excluded_group_cannot_hide_wrong_owner():
    args = graph()
    args[0]["state_runtime"]["lifecycles"][0]["owner"] = "b"
    with pytest.raises(PlanError): invoke(*args, excluded={"buff"})


def test_pool_pending_rule_dependency_is_not_mislabeled_as_exclusion():
    args = graph()
    records = [{"kind": "consumers", "id": "unavailable", "status": "pending"}]
    state, _, _, adopted, _, _ = invoke(*args, pool_disabled_rules={"buff"}, pool_adoptions=records)
    assert state is None
    assert all(r["own_source_status"] == "ready" and r["effective_status"] == "dependency_pending" for r in adopted)
