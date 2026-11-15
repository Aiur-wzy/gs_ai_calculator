"""Independent sole-ledger commit probes; not a queue or game-clock approval."""
from __future__ import annotations

import copy
import json

import pytest

import genshin_engine.shared_pools as pools_module
from genshin_engine.resource_counters import CounterProgram
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from genshin_engine.state_runtime import StateRuntime
from tests.test_resource_counter_atomic import Budget, controls, image, make
from tests.test_resource_counters import definitions
from tests.test_shared_pool_v2 import spend
from tests.test_shared_pools import consume
from tests.test_state_runtime import event


def test_shared_byte_limit_rejects_before_public_reservation_and_can_retry(monkeypatch):
    ledger, clock, budget = make()
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    _, config, _ = definitions()
    raw = spend(SharedPoolLedger(config, character_ids=["a", "b"]))
    encoded = sum(len(json.dumps(r, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":")).encode())+1 for r in raw)
    prior = image(ledger, budget)
    limit = pools_module.LIMITS["receipt_bytes"]
    # Room for the actual resource row, but no room for its counter update.
    monkeypatch.setitem(pools_module.LIMITS, "receipt_bytes",
                        ledger._receipt_bytes+ledger._counter_receipt_bytes+encoded)
    with pytest.raises(SharedPoolError) as exc:
        spend(ledger)
    assert exc.value.issue["code"] == "counter_limit"
    assert image(ledger, budget) == prior
    monkeypatch.setitem(pools_module.LIMITS, "receipt_bytes", limit)
    spend(ledger)
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 8


@pytest.mark.parametrize("anchor", ["success", "attempt"])
def test_failed_joint_commit_does_not_start_either_cooldown_policy(anchor):
    conf, pools, lives = definitions()
    pools["operations"][0]["cooldown"] = {"seconds": 1, "anchor": anchor}
    budget = Budget()
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"],
                              counter_program=CounterProgram(conf, pools, lives, character_ids=["a", "b"]),
                              reserve_events=budget.reserve)
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    budget.limit = budget.used+1
    prior = image(ledger, budget)
    with pytest.raises(SharedPoolError):
        spend(ledger)
    assert image(ledger, budget) == prior
    budget.limit += 3
    assert spend(ledger)[0]["accepted"] == 8
    assert spend(ledger, t=100_000, run="0:later")[0]["reason"] == "cooldown"
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 8


def test_old_consumer_reread_after_later_controls_is_read_only():
    ledger, clock, budget = make()
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    first = consume(ledger, t=0)
    controls(ledger, clock, clock.start("field", event(100_000, "0:second"), eligible=True))
    spend(ledger, t=100_000, run="0:new-spend")
    prior = image(ledger, budget)
    second = consume(ledger, t=0)
    assert second == first and image(ledger, budget) == prior
    second["accepted"] = 999
    assert consume(ledger, t=0) == first and image(ledger, budget) == prior


def test_no_serialization_or_deepcopy_occurs_after_successful_reservation(monkeypatch):
    ledger, clock, budget = make()
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    original_copy, original_encode = copy.deepcopy, pools_module._canonical
    reserved = False

    def guarded_copy(*args, **kwargs):
        assert not reserved, "copy after the commit reservation"
        return original_copy(*args, **kwargs)

    def guarded_encode(*args, **kwargs):
        assert not reserved, "encode after the commit reservation"
        return original_encode(*args, **kwargs)

    def reserve(count):
        nonlocal reserved
        budget.reserve(count)
        reserved = True

    ledger._reserve_counter_events = reserve
    monkeypatch.setattr(copy, "deepcopy", guarded_copy)
    monkeypatch.setattr(pools_module, "_canonical", guarded_encode)
    result = spend(ledger)
    reserved = False
    assert result[0]["accepted"] == 8
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 8
