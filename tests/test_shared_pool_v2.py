"""Versioned scaled accounting; does not certify a continuous game clock."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.shared_pools import SharedPoolError, validate_shared_pools
from tests.test_shared_pools import config, consume, ledger


def scaled(amount=8, policy="up_to_available", initial=3):
    c = config()
    c["schema_version"] = 2
    c["pools"][0].update(unit_scale=10, capacity=1000, initial={"team": initial})
    c["operations"][0].update(operation="spend", amount=amount, spend_policy=policy)
    return c


def spend(l, *, t=0, run="0:tick", keys=("team",), eligible=True):
    return l.apply_operation("refill", event_id="tick", run_id=run, t_us=t, phase="post",
                             event_kind="elemental_reaction", keys=keys, eligible=eligible)


def test_scaled_partial_consumption_reports_only_actual_atoms():
    l = ledger(scaled())
    first = spend(l)[0]
    second = spend(l, t=100_000, run="0:tick-2")[0]
    assert (first["before"], first["requested"], first["accepted"], first["after"]) == (3, 8, 3, 0)
    assert first["unit_scale"] == 10 and first["unfulfilled"] == 5
    assert first["reason"] == "spent" and first["succeeded"]
    assert second["accepted"] == 0 and second["reason"] == "empty" and not second["succeeded"]
    assert sum(r["accepted"] for r in l.receipts()) == 3


def test_whole_spend_does_not_mistake_insufficient_balance_for_partial():
    l = ledger(scaled(policy="all_or_nothing"))
    row = spend(l)[0]
    assert row["reason"] == "insufficient" and not row["succeeded"]
    assert row["accepted"] == 0 and row["after"] == 3


def test_all_available_is_resolved_at_execution_for_each_key():
    c = scaled(amount="all_available")
    c["pools"][0].update(scope="per_character", initial={"a": 3, "b": 16})
    l = ledger(c)
    rows = spend(l, keys=["b", "a"])
    assert [(r["key"], r["requested"], r["accepted"], r["after"]) for r in rows] == [("a", 3, 3, 0), ("b", 16, 16, 0)]
    assert spend(l, t=100_000, run="1:tick", keys=["a", "b"])[1]["accepted"] == 0


@pytest.mark.parametrize("anchor,expected", [("attempt", "cooldown"), ("success", "insufficient")])
def test_spend_cooldown_uses_actual_success(anchor, expected):
    c = scaled(policy="all_or_nothing")
    c["operations"][0]["cooldown"] = {"seconds": .1, "anchor": anchor}
    l = ledger(c)
    assert spend(l)[0]["reason"] == "insufficient"
    assert spend(l, t=99_999, run="0:later")[0]["reason"] == expected


def test_multikey_partial_success_cooldown_does_not_depend_on_last_key():
    c = scaled()
    c["pools"][0].update(scope="per_character", initial={"a": 3, "b": 0})
    c["operations"][0]["cooldown"] = {"seconds": .1, "anchor": "success"}
    l = ledger(c)
    first = spend(l, keys=["b", "a"])
    assert first[0]["accepted"] == 3 and first[1]["accepted"] == 0
    assert {r["cooldown_until_us"] for r in first} == {100_000}
    assert all(r["reason"] == "cooldown" for r in spend(l, t=1, run="0:later", keys=["a", "b"]))


def test_consumer_and_spend_share_one_scaled_balance():
    l = ledger(scaled(initial=10))
    before = consume(l, t=0)
    row = spend(l)[0]
    assert before["accepted"] == 1 and row["before"] == 9 and row["after"] == 1
    assert consume(l, t=0) == before
    with pytest.raises(SharedPoolError): spend(l)
    assert l.snapshot()["balances"]["pool"]["team"] == 1


def test_consumer_amount_and_receipt_use_atoms_not_display_units():
    c = scaled(initial=10)
    c["consumers"][0]["amount"] = 5
    l = ledger(c)
    row = consume(l, t=0)
    assert (row["accepted"], row["after"], row["unit_scale"], row["quantity_unit"]) == (5, 5, 10, "atoms")
    assert l.snapshot()["unit_scales"] == {"pool": 10} and l.snapshot()["schema_version"] == 2
    assert "unit_scale" not in consume(ledger()) and "unit_scales" not in ledger().snapshot()


def test_whole_policy_is_per_key_with_deterministic_shared_success_cd():
    c = scaled(policy="all_or_nothing")
    c["pools"][0].update(scope="per_character", initial={"a": 3, "b": 16})
    c["operations"][0]["cooldown"] = {"seconds": .1, "anchor": "success"}
    first = spend(ledger(c), keys=["b", "a"])
    c["pools"][0]["initial"] = {"b": 16, "a": 3}
    assert spend(ledger(c), keys=["a", "b"]) == first
    assert [(r["accepted"], r["after"], r["reason"]) for r in first] == [(0, 3, "insufficient"), (8, 8, "spent")]
    assert {r["cooldown_until_us"] for r in first} == {100_000}


@pytest.mark.parametrize("anchor,expected", [("attempt", "cooldown"), ("success", "spent")])
def test_ineligible_delivered_attempt_cd_matches_v1_policy(anchor, expected):
    c = scaled()
    c["operations"][0]["cooldown"] = {"seconds": .1, "anchor": anchor}
    l = ledger(c)
    row = spend(l, eligible=False)[0]
    assert row["reason"] == "ineligible" and row["accepted"] == 0
    assert spend(l, t=1, run="0:next")[0]["reason"] == expected


def test_all_available_zero_is_not_success_and_rechecks_after_grant():
    c = scaled(amount="all_available", initial=0)
    c["operations"][0]["cooldown"] = {"seconds": .1, "anchor": "success"}
    grant = copy.deepcopy(c["operations"][0])
    grant.update(id="grant", operation="grant", amount=13, order=1)
    grant.pop("spend_policy")
    c["operations"].append(grant)
    l = ledger(c)
    empty = spend(l)[0]
    assert empty["requested"] == 0 and empty["reason"] == "empty" and empty["cooldown_until_us"] == 0
    l.apply_operation("grant", event_id="tick", run_id="0:tick", t_us=0, phase="post",
                      event_kind="elemental_reaction", keys=["team"], eligible=True)
    actual = spend(l, t=1, run="0:next")[0]
    assert actual["requested"] == actual["accepted"] == 13


def test_set_decrease_and_capped_grant_are_never_spend():
    c = scaled(initial=999)
    op = c["operations"][0]
    op.update(operation="grant", cooldown={"seconds": .1, "anchor": "success"})
    op.pop("spend_policy")
    row = spend(ledger(c))[0]
    assert (row["accepted"], row["wasted"], row["after"]) == (1, 7, 1000)
    assert row["operation"] == "grant" and "unfulfilled" not in row
    c["pools"][0]["initial"] = {"team": 1000}
    row = spend(ledger(c))[0]
    assert row["accepted"] == 0 and row["succeeded"] and row["cooldown_until_us"] == 100_000
    op.update(operation="set", amount=1)
    row = spend(ledger(c))[0]
    assert row["delta"] == -999 and row["accepted"] == 1 and row["operation"] == "set"
    assert row["reason"] == "set" and "unfulfilled" not in row


def test_scaled_unbounded_grant_keeps_atom_units_without_clipping():
    c = scaled(initial=999)
    c["pools"][0]["capacity"] = "unbounded"
    c["operations"][0].update(operation="grant", amount=8)
    c["operations"][0].pop("spend_policy")
    row = spend(ledger(c))[0]
    assert (row["before"], row["accepted"], row["after"], row["wasted"]) == (999, 8, 1007, 0)
    assert row["capacity_mode"] == "unbounded"
    assert row["unit_scale"] == 10 and row["quantity_unit"] == "atoms"


def test_cooldown_overflow_preserves_completed_prefix_and_all_cursors():
    c = scaled(initial=20)
    c["operations"][0]["cooldown"] = {"seconds": 1, "anchor": "success"}
    l = ledger(c)
    spend(l)
    before, trace = l.snapshot(), l.receipts()
    with pytest.raises(SharedPoolError) as exc: spend(l, t=10**15, run="0:later")
    assert exc.value.issue["code"] == "pool_time_limit"
    assert l.snapshot() == before and l.receipts() == trace
    # Failed submission did not claim its actual identity, clock, order or CD.
    assert spend(l, t=10**15-1_000_000, run="0:later")[0]["accepted"] == 8


def test_multikey_receipt_budget_failure_preserves_all_state(monkeypatch):
    from genshin_engine import shared_pools
    c = scaled()
    c["pools"][0].update(scope="per_character", initial={"a": 3, "b": 16})
    l = ledger(c)
    original = copy.deepcopy(l.snapshot())
    monkeypatch.setitem(shared_pools.LIMITS, "receipts", 1)
    with pytest.raises(SharedPoolError) as exc: spend(l, keys=["a", "b"])
    assert exc.value.issue["code"] == "pool_receipt_limit"
    assert l.snapshot() == original and l.receipts() == []
    monkeypatch.setitem(shared_pools.LIMITS, "receipts", 40_000)
    assert [r["accepted"] for r in spend(l, keys=["a", "b"])] == [3, 8]


@pytest.mark.parametrize("mutation", ["old_version", "missing_scale", "float", "bool", "negative", "zero_spend", "mixed_policy", "grant_policy"])
def test_scaled_schema_is_closed(mutation):
    c = scaled()
    if mutation == "old_version": c["schema_version"] = 1
    if mutation == "missing_scale": c["pools"][0].pop("unit_scale")
    if mutation == "float": c["operations"][0]["amount"] = .8
    if mutation == "bool": c["operations"][0]["amount"] = True
    if mutation == "negative": c["operations"][0]["amount"] = -8
    if mutation == "zero_spend": c["operations"][0]["amount"] = 0
    if mutation == "mixed_policy": c["operations"][0].update(amount="all_available", spend_policy="all_or_nothing")
    if mutation == "grant_policy": c["operations"][0]["operation"] = "grant"
    assert validate_shared_pools(c, character_ids=["a", "b"])


@pytest.mark.parametrize("scale", [0, True, .5, 1000001])
def test_unit_scale_is_a_positive_bounded_integer(scale):
    c = scaled()
    c["pools"][0]["unit_scale"] = scale
    issues = validate_shared_pools(c, character_ids=["a", "b"])
    assert any(r["path"] == "shared_pools.pools[0].unit_scale" for r in issues)
