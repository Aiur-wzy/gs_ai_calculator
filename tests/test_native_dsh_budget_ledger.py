from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from genshin_app.agent_chat.native_dsh_budget_ledger import NativeDshBudgetLedger
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_turn_budget import DEFAULT_BUDGET
from genshin_db.locking import lock_is_held


def request(**changes):
    return {"session_id": "one", "lease_id": str(uuid.uuid4()), "purpose": "main", "turn": 1,
        "step": 1, "provider": "deepseek-official", "model": "deepseek-flash",
        "reasoning_effort": "high", "max_tokens": None, **changes}


def ledger(tmp_path, **limits):
    return NativeDshBudgetLedger(tmp_path,
        policy_loader=lambda: {"effective": {**DEFAULT_BUDGET, **limits}, "generation": 0})


def settle(store, req, **changes):
    return store.settle({**{key: req[key] for key in ("session_id", "lease_id", "purpose", "turn", "step")},
                        "finish": "stop", "usage": None, **changes})


def test_physical_retry_and_compaction_are_charged_without_old_retry_zero_ban(tmp_path):
    store = ledger(tmp_path, provider_steps=3)
    first = request()
    second = request()
    compact = request(purpose="compaction", step=None)
    assert store.acquire(first)["allowed"]
    settle(store, first, finish="error")
    assert store.acquire(second)["allowed"]
    assert store.acquire(compact)["allowed"]
    assert not store.acquire(request())["allowed"]
    assert store.acquire(request(session_id="two"))["allowed"]
    assert store.acquire(request(turn=2))["allowed"]


def test_exact_claim_replay_never_dispatches_a_second_paid_request(tmp_path):
    store = ledger(tmp_path)
    req = request()
    assert store.acquire(req)["allowed"]
    replay = store.acquire(req)
    assert replay["allowed"] is False and replay["reason"]["code"] == "dsh_budget_lease_already_claimed"
    with pytest.raises(NativeDshMuxError, match="dsh_budget_lease_conflict"):
        store.acquire({**req, "session_id": "other"})
    assert len(store._read()["leases"]) == 1


def test_usage_cache_tokens_and_settlement_identity_are_not_duplicated(tmp_path):
    store = ledger(tmp_path, cumulative_input_hard_tokens=100)
    req = request()
    store.acquire(req)
    usage = {"inputTokens": 20, "cacheReadTokens": 70, "cacheWriteTokens": 10, "outputTokens": 4}
    assert settle(store, req, usage=usage)["status"] == "settled"
    assert settle(store, req, usage=usage)["status"] == "already_settled"
    assert not store.acquire(request())["allowed"]
    with pytest.raises(NativeDshMuxError, match="settlement_conflict"):
        settle(store, req, usage={**usage, "outputTokens": 10})
    with pytest.raises(NativeDshMuxError, match="settlement_identity_mismatch"):
        settle(store, {**req, "turn": 2}, usage=usage)


def test_healthy_inflight_request_is_not_cut_at_wall_budget(tmp_path):
    now = [0]
    store = NativeDshBudgetLedger(tmp_path, clock=lambda: now[0],
        policy_loader=lambda: {"effective": {**DEFAULT_BUDGET, "wall_clock_seconds": 100}})
    req = request()
    assert store.acquire(req)["allowed"]
    now[0] = 10000
    assert store._read()["leases"][req["lease_id"]]["settled_at"] is None
    assert settle(store, req)["status"] == "settled"
    denied = store.acquire(request())
    assert not denied["allowed"] and denied["reason"]["code"] == "dsh_budget_wall_clock_seconds"


def test_missing_main_fact_and_invalid_usage_do_not_publish_fabricated_settlements(tmp_path):
    store = ledger(tmp_path)
    with pytest.raises(NativeDshMuxError, match="main_fact_missing"):
        store.acquire(request(turn=None, step=None))
    req = request()
    store.acquire(req)
    before = copy.deepcopy(store._read())
    with pytest.raises(NativeDshMuxError, match="counter_invalid"):
        settle(store, req, usage={"inputTokens": -1})
    assert store._read() == before


def test_settings_changes_apply_to_next_dsh_turn_not_current_request(tmp_path):
    policy = {"generation": 1, "effective": {**DEFAULT_BUDGET, "provider_steps": 1}}
    store = NativeDshBudgetLedger(tmp_path, policy_loader=lambda: copy.deepcopy(policy))
    assert store.acquire(request())["allowed"]
    policy["generation"] = 2
    policy["effective"]["provider_steps"] = 8
    assert not store.acquire(request())["allowed"]
    new = store.acquire(request(turn=2))
    assert new["allowed"] and new["budget_snapshot"]["policy_generation"] == 2


def test_total_resource_admission_counts_app_and_web_once_without_owning_outcomes(tmp_path):
    store = ledger(tmp_path, total_tool_calls=2, read_tool_calls=1)
    one = {"session_id": "one", "turn": 1, "claim_id": "a" * 64, "category": "read"}
    assert store.admit_tool(**one)["status"] == "reserved"
    assert store.admit_tool(**one)["status"] == "already_reserved"
    with pytest.raises(NativeDshMuxError, match="read_tool_calls"):
        store.admit_tool(**{**one, "claim_id": "b" * 64})
    assert store.admit_tool(**{**one, "claim_id": "c" * 64, "category": "web"})["status"] == "reserved"
    with pytest.raises(NativeDshMuxError, match="total_tool_calls"):
        store.admit_tool(**{**one, "claim_id": "d" * 64, "category": "mutation"})
    assert store.admit_tool(**{**one, "turn": 2, "claim_id": "e" * 64})["status"] == "reserved"
    assert not store._read()["leases"]  # no fabricated provider request or success fact


def test_generic_dsh_tools_share_total_budget_without_consuming_database_read_quota(tmp_path):
    store = ledger(tmp_path, total_tool_calls=2, read_tool_calls=1)
    generic = {"session_id": "one", "turn": 1, "claim_id": "a"*64, "category": "generic"}
    assert store.admit_tool(**generic)["status"] == "reserved"
    assert store.admit_tool(**generic)["status"] == "already_reserved"
    assert store.admit_tool(**{**generic, "claim_id": "b"*64, "category": "read"})["status"] == "reserved"
    with pytest.raises(NativeDshMuxError, match="total_tool_calls"):
        store.admit_tool(**{**generic, "claim_id": "c"*64})
    summary = store.summary("one")
    assert summary["tool_capacity_reservations"]["generic"] == 1
    assert summary["tool_capacity_reservations"]["read"] == 1
    assert summary["provider_requests"] == 0


def test_summary_reader_holds_the_same_cross_thread_lock_as_atomic_writers(tmp_path, monkeypatch):
    from genshin_app.agent_chat import native_dsh_budget_ledger as module

    store = ledger(tmp_path)
    store.acquire(request())
    original = module.read_owned
    checked = []
    def observed_read(*args, **kwargs):
        # A different thread must see the actual OS/write lock as held while
        # read_owned opens the target, not merely while the summary is built.
        with ThreadPoolExecutor(max_workers=1) as other:
            checked.append(other.submit(lock_is_held,
                store.root / ".native-dsh-paid-requests.lock").result())
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "read_owned", observed_read)
    assert store.summary("one")["provider_requests"] == 1
    assert checked == [True]


def test_high_frequency_summaries_do_not_block_budget_claims_or_settlements(tmp_path):
    store = ledger(tmp_path)
    store.acquire(request())
    barrier = threading.Barrier(3)
    def poll():
        barrier.wait(timeout=5)
        for _ in range(30):
            store.summary("one")
    def claim_and_settle():
        barrier.wait(timeout=5)
        for _ in range(15):
            req = request()
            assert store.acquire(req)["allowed"]
            assert settle(store, req)["status"] == "settled"
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = [pool.submit(poll), pool.submit(poll), pool.submit(claim_and_settle)]
        for result in pending:
            result.result(timeout=15)
    final = store.summary("one")
    assert final["provider_requests"] == 16 and final["provider_settled"] == 15


def test_unused_account_summary_creates_no_directory_or_lock(tmp_path):
    store = ledger(tmp_path / "untouched")
    assert store.summary("one")["provider_requests"] == 0
    assert not store.data_dir.exists()
