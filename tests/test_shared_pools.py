"""Integer-ledger tests; upper-layer event and source qualification is not proved."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.shared_pool_schema import LIMITS, shared_pool_capabilities
from genshin_engine.shared_pools import (
    SharedPoolError,
    SharedPoolLedger,
    validate_shared_pools,
)


def config():
    evidence = {"status": "assumed", "source": "合成工程资料"}
    return {"schema_version": 1,
            "pools": [{"id": "pool", "name_zh": "共享池", "owner": "a", "source_id": "binding:pool",
                       "scope": "team", "capacity": 20, "initial": {"team": 10}, **evidence}],
            "operations": [{"id": "refill", "pool_id": "pool", "source_id": "binding:refill",
                            "trigger": {"event": "elemental_reaction", "phase": "post"}, "order": 0,
                            "operation": "grant", "amount": 3, "targets": {"mode": "all_pool_keys"},
                            "cooldown": {"seconds": 0, "anchor": "success"}, **evidence}],
            "consumers": [{"id": "consumer", "pool_id": "pool", "source_id": "binding:benefit",
                           "filter": {"event": "hit", "phase": "pre"}, "actor_relation": "other_frontline",
                           "damage_system": "classic", "amount": 1, "eligibility_rule_id": "benefit", **evidence}]}


def ledger(c=None):
    return SharedPoolLedger(c or config(), character_ids=["a", "b"], rule_ids=["benefit"],
                            source_parts=["binding:pool", "binding:refill", "binding:benefit"])


def consume(l, event="h1", run="0:h1", t=1, **kw):
    return l.try_consume("consumer", event_id=event, run_id=run, t_us=t, actor="b", eligible=True, **kw)


def grant(l, event="reaction", run="0:reaction", t=2, **kw):
    return l.apply_operation("refill", event_id=event, run_id=run, t_us=t, phase="post",
                             event_kind="elemental_reaction", keys=["team"], eligible=True, **kw)


def test_grant_amount_is_not_capacity_and_clamps():
    c = config()
    c["pools"][0]["initial"]["team"] = 2
    l = ledger(c)
    result = grant(l)
    assert (result[0]["before"], result[0]["requested"], result[0]["accepted"], result[0]["after"]) == (2, 3, 3, 5)
    c["pools"][0]["initial"]["team"] = 19
    result = grant(ledger(c))[0]
    assert (result["accepted"], result["after"], result["wasted"]) == (1, 20, 2)
    assert "capacity_mode" not in result  # Legacy finite-pool receipt shape remains valid.


def test_unbounded_pool_grants_and_sets_without_gameplay_clipping():
    c = config()
    c["pools"][0].update(capacity="unbounded", initial={"team": 19})
    first_ledger = ledger(c)
    first = grant(first_ledger)[0]
    second = grant(first_ledger, event="reaction-2", run="0:reaction-2", t=3)[0]
    assert [(row["before"], row["accepted"], row["after"], row["wasted"])
            for row in (first, second)] == [(19, 3, 22, 0), (22, 3, 25, 0)]
    assert all(row["capacity_mode"] == "unbounded" for row in (first, second))
    assert first_ledger.snapshot()["capacity_modes"] == {"pool": "unbounded"}

    c["operations"][0].update(operation="set", amount=50)
    set_row = grant(ledger(c))[0]
    assert (set_row["accepted"], set_row["after"], set_row["wasted"]) == (50, 50, 0)


def test_unbounded_pool_safety_limit_fails_atomically_instead_of_clipping():
    c = config()
    c["pools"][0].update(capacity="unbounded", initial={"team": LIMITS["count"]})
    l = ledger(c)
    before = l.snapshot()
    with pytest.raises(SharedPoolError) as exc:
        grant(l)
    assert exc.value.issue["code"] == "pool_balance_limit"
    assert exc.value.issue["severity"] == "pending"
    assert l.snapshot() == before and l.receipts() == []


def test_actual_event_occurrence_id_not_axis_cycle_is_the_dedup_key():
    l = ledger()
    assert consume(l)["after"] == 9
    # Different hits in the same axis cycle must both consume.
    assert consume(l, event="h2", run="0:h2", t=1)["after"] == 8
    # A same-named hit in a later check cycle is another real occurrence.
    assert consume(l, event="h1", run="1:h1", t=100)["after"] == 7
    assert len(l.receipts()) == 3


def test_repeat_read_is_idempotent_but_changed_identity_rejected():
    l = ledger()
    first = consume(l)
    assert consume(l) == first
    first["after"] = 999
    assert l.receipts()[0]["after"] == 9
    with pytest.raises(SharedPoolError, match="身份"):
        consume(l, event="h2", run="0:h1")
    assert l.snapshot()["balances"]["pool"]["team"] == 9


def test_ineligible_empty_and_whole_cost():
    c = config()
    c["pools"][0]["initial"]["team"] = 1
    c["consumers"][0]["amount"] = 2
    l = ledger(c)
    assert consume(l)["reason"] == "insufficient"
    assert l.try_consume("consumer", event_id="off", run_id="0:off", t_us=2,
                         actor="b", eligible=False)["reason"] == "ineligible"
    assert l.snapshot()["balances"]["pool"]["team"] == 1


def test_post_refill_does_not_change_already_created_consumption_receipt():
    c = config()
    c["pools"][0]["initial"]["team"] = 0
    l = ledger(c)
    first = consume(l, t=2)
    grant(l, t=2)
    later = consume(l, event="h2", run="0:h2", t=2)
    assert (first["accepted"], later["accepted"], later["after"]) == (0, 1, 2)


def test_multiple_sources_write_one_pool_in_explicit_order():
    c = config()
    second = copy.deepcopy(c["operations"][0])
    second.update(id="set", order=1, operation="set", amount=4)
    c["operations"].append(second)
    l = ledger(c)
    grant(l)
    l.apply_operation("set", event_id="reaction", run_id="0:reaction", t_us=2, phase="post",
                      event_kind="elemental_reaction", keys=["team"], eligible=True)
    assert l.snapshot()["balances"]["pool"]["team"] == 4
    l = ledger(c)
    l.apply_operation("set", event_id="reaction", run_id="0:reaction", t_us=2, phase="post",
                      event_kind="elemental_reaction", keys=["team"], eligible=True)
    with pytest.raises(SharedPoolError) as err:
        grant(l)
    assert err.value.issue["code"] == "pool_operation_order"
    assert l.snapshot()["balances"]["pool"]["team"] == 4


def test_same_pool_phase_order_rejected_conservatively():
    c = config()
    c["operations"].append({**copy.deepcopy(c["operations"][0]), "id": "other"})
    issues = validate_shared_pools(c, character_ids=["a", "b"])
    assert any(x["code"] == "pool_operation_order" and x["path"] == "shared_pools.operations[1].order" for x in issues)


@pytest.mark.parametrize("anchor, expected", [("attempt", "cooldown"), ("success", "granted")])
def test_cooldown_attempt_and_success_qualified_operation(anchor, expected):
    c = config()
    c["operations"][0]["cooldown"] = {"seconds": .1, "anchor": anchor}
    l = ledger(c)
    l.apply_operation("refill", event_id="r0", run_id="0:r0", t_us=0, phase="post",
                      event_kind="elemental_reaction", keys=["team"], eligible=False)
    assert grant(l, t=99999)[0]["reason"] == expected
    assert grant(l, event="r2", run="0:r2", t=200000)[0]["reason"] == "granted"


def test_per_character_keys_are_independent_and_targets_exact():
    c = config()
    c["pools"][0].update(scope="per_character", initial={"a": 2, "b": 3})
    l = ledger(c)
    assert consume(l)["after"] == 2
    assert l.snapshot()["balances"]["pool"] == {"a": 2, "b": 2}
    with pytest.raises(SharedPoolError):
        grant(l)
    result = l.apply_operation("refill", event_id="r", run_id="0:r", t_us=3, phase="post",
                               event_kind="elemental_reaction", keys=["b", "a"], eligible=True)
    assert [x["key"] for x in result] == ["a", "b"]
    assert l.snapshot()["balances"]["pool"] == {"a": 5, "b": 5}


def test_no_mutable_alias_and_no_duplicate_operation():
    c = config()
    saved = copy.deepcopy(c)
    l = ledger(c)
    grant(l)
    snap = l.snapshot()
    snap["balances"]["pool"]["team"] = 999
    with pytest.raises(SharedPoolError) as err:
        grant(l)
    assert err.value.issue["code"] == "pool_duplicate_operation"
    assert l.snapshot()["balances"]["pool"]["team"] == 13
    assert c == saved


@pytest.mark.parametrize("path, value", [("capacity", True), ("capacity", -1), ("capacity", 0),
                                          ("capacity", 1.5), ("capacity", 1000001),
                                          ("capacity", "unknown")])
def test_invalid_pool_integer(path, value):
    c = config()
    c["pools"][0][path] = value
    with pytest.raises(SharedPoolError):
        ledger(c)


def test_pending_source_unknown_fields_and_future_fields_fail_closed():
    c = config()
    c["pools"][0]["status"] = "pending"
    assert any(x["severity"] == "pending" for x in validate_shared_pools(c, character_ids=["a", "b"]))
    with pytest.raises(SharedPoolError):
        ledger(c)
    c = config()
    c["pools"][0]["scale"] = 10
    assert any(x["path"].endswith(".scale") for x in validate_shared_pools(c, character_ids=["a", "b"]))


def test_reference_validation_and_scope_are_explicit():
    c = config()
    assert validate_shared_pools(c, character_ids=["a", "b"], rule_ids=["benefit"],
                                 source_parts=["binding:pool", "binding:refill", "binding:benefit"]) == []
    assert validate_shared_pools(c, character_ids=["b"], rule_ids=[], source_parts=[])
    caps = shared_pool_capabilities()
    assert caps["upper_layer_qualification"] == "not_evaluated"
    assert caps["unit"] == "integer_count"


def test_time_precision_context_mismatch_and_changed_duplicate_qualification():
    l = ledger()
    consume(l)
    with pytest.raises(SharedPoolError):
        l.try_consume("consumer", event_id="h1", run_id="0:h1", t_us=1, actor="b", eligible=False)
    with pytest.raises(SharedPoolError):
        consume(l, event="x", run="0:x", t=0)
    with pytest.raises(SharedPoolError):
        consume(l, event="x", run="0:x", t=True)
    with pytest.raises(SharedPoolError):
        l.apply_operation("refill", event_id="r", run_id="0:r", t_us=2, phase="pre",
                          event_kind="elemental_reaction", keys=["team"], eligible=True)


def test_post_operation_cannot_run_before_new_pre_consumption_on_same_occurrence():
    c = config()
    c["operations"][0]["trigger"]["event"] = "hit"
    l = ledger(c)
    l.apply_operation("refill", event_id="h1", run_id="0:h1", t_us=1, phase="post",
                      event_kind="hit", keys=["team"], eligible=True)
    with pytest.raises(SharedPoolError) as err:
        consume(l)
    assert err.value.issue["code"] == "pool_event_phase_order"
    assert l.snapshot()["balances"]["pool"]["team"] == 13


def test_pre_operation_after_consumption_rejected_and_duplicate_read_still_allowed():
    c = config()
    c["operations"][0]["trigger"].update(event="hit", phase="pre")
    l = ledger(c)
    original = consume(l)
    with pytest.raises(SharedPoolError):
        l.apply_operation("refill", event_id="h1", run_id="0:h1", t_us=1, phase="pre",
                          event_kind="hit", keys=["team"], eligible=True)
    consume(l, event="h2", run="0:h2", t=2)
    assert consume(l) == original


def test_set_is_absolute_and_documents_clamp_and_delta():
    c = config()
    c["operations"][0].update(operation="set", amount=3)
    result = grant(ledger(c))[0]
    assert (result["before"], result["accepted"], result["after"], result["delta"]) == (10, 3, 3, -7)
    c["operations"][0]["amount"] = 100
    result = grant(ledger(c))[0]
    assert (result["accepted"], result["after"], result["wasted"]) == (20, 20, 80)


def test_at_capacity_qualified_grant_starts_success_cooldown():
    c = config()
    c["pools"][0]["initial"]["team"] = 20
    c["operations"][0]["cooldown"]["seconds"] = 1
    l = ledger(c)
    first = grant(l)[0]
    assert first["succeeded"] and first["accepted"] == 0 and first["wasted"] == 3
    assert grant(l, event="r2", run="0:r2", t=3)[0]["reason"] == "cooldown"


def test_receipt_limit_failure_does_not_mutate_accounting(monkeypatch):
    import genshin_engine.shared_pools as module
    l = ledger()
    before = l.snapshot()
    monkeypatch.setitem(module.LIMITS, "receipts", 0)
    with pytest.raises(SharedPoolError) as err:
        consume(l)
    assert err.value.issue["code"] == "pool_receipt_limit"
    assert l.snapshot() == before and l.receipts() == []


def test_receipt_byte_limit_and_cooldown_time_overflow_are_atomic(monkeypatch):
    import genshin_engine.shared_pools as module
    c = config()
    c["operations"][0]["cooldown"]["seconds"] = 1
    l = ledger(c)
    before = l.snapshot()
    with pytest.raises(SharedPoolError):
        grant(l, t=10**15)
    assert l.snapshot() == before
    monkeypatch.setitem(module.LIMITS, "receipt_bytes", 3)
    with pytest.raises(SharedPoolError):
        grant(l)
    assert l.snapshot() == before


@pytest.mark.parametrize("field, value", [("capacity", 10**1000), ("capacity", float("inf")), ("capacity", float("nan"))])
def test_extreme_untrusted_numbers_return_issues(field, value):
    c = config()
    c["pools"][0][field] = value
    assert validate_shared_pools(c, character_ids=["a", "b"])


@pytest.mark.parametrize("bad", [[], {}, None, 1, True])
def test_invalid_ids_raise_domain_errors(bad):
    l = ledger()
    with pytest.raises(SharedPoolError):
        l.try_consume(bad, event_id="h", run_id="0:h", t_us=0, actor="b", eligible=True)
    with pytest.raises(SharedPoolError):
        consume(l, run=bad)
    with pytest.raises(SharedPoolError):
        l.apply_operation(bad, event_id="r", run_id="0:r", t_us=0, phase="post",
                          event_kind="elemental_reaction", keys=["team"], eligible=True)


def test_duplicate_source_pool_and_reference_scope_rejected():
    c = config()
    c["pools"].append({**copy.deepcopy(c["pools"][0]), "id": "alias"})
    assert any(x["code"] == "pool_duplicate_source" for x in validate_shared_pools(c, character_ids=["a", "b"]))
    c = config()
    c["consumers"][0]["filter"]["characters"] = ["missing"]
    assert any(x["code"] == "pool_character_reference" for x in validate_shared_pools(c, character_ids=["a", "b"]))


def test_boundary_canonical_and_capabilities_detached():
    l = ledger()
    grant(l)
    first = l.boundary()
    first["cooldown_until_us"]["refill"] = 999
    assert l.boundary()["cooldown_until_us"]["refill"] == 2
    caps = shared_pool_capabilities()
    assert caps["capacity"].startswith("正整数")
    caps["schema"]["properties"].clear()
    assert shared_pool_capabilities()["schema"]["properties"]
