"""Detached counter candidates; these tests do not claim joint ledger commit."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_counters import CounterProgram, ResourceCounterError
from genshin_engine.shared_pools import SharedPoolLedger
from genshin_engine.state_runtime import StateRuntime
from tests.test_shared_pool_v2 import scaled, spend
from tests.test_state_runtime import config, event


def definitions(window="created_until_end", reset="create", scope="pool_total", delay=0):
    pools, state = scaled(initial=100), config(delay=delay, first=1_000_000)
    counter = {"id": "count", "name_zh": "实际消费累计", "owner": "a", "source_id": "source:counter",
        "pool_id": "pool", "operation_ids": ["refill"], "scope": scope, "lifecycle_id": "field",
        "accumulate_window": window, "reset_on": reset, "status": "assumed", "source": "合成测试"}
    return {"schema_version": 1, "counters": [counter]}, pools, state


def setup(**kwargs):
    conf, pools, lives = definitions(**kwargs)
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    return program, program.initial_state(), clock, ledger


def apply_controls(program, state, clock, out):
    trace = []
    for step in clock.consume_ordered_steps(out["ordered_steps"]):
        plan = program.plan_control(state, step)
        state = plan["state"]
        trace.extend(plan["counter_receipts"])
    return state, trace


def wrap(state, receipts):
    return [{"ledger_sequence": state["last_ledger_sequence"]+i+1,
             "control_sequence": state["last_control_sequence"], "receipt": row} for i, row in enumerate(receipts)]


def test_known_unstarted_lifecycle_is_outside_not_unknown():
    program, state, _, ledger = setup()
    raw = spend(ledger)
    result = program.plan_spend(state, wrap(state, raw))
    assert raw[0]["accepted"] == 8
    assert result["state"]["values"]["count"]["total"] == 0
    assert result["counter_receipts"][0]["reason"] == "outside_window"


def test_counter_accepts_explicit_unbounded_pool_receipt():
    conf, pools, lives = definitions()
    pools["pools"][0]["capacity"] = "unbounded"
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    raw = spend(ledger)
    assert raw[0]["capacity_mode"] == "unbounded"
    result = program.plan_spend(program.initial_state(), wrap(program.initial_state(), raw))
    assert result["counter_receipts"][0]["resource_accepted"] == 8


def test_actual_partial_debit_and_empty_debit_use_only_accepted_atoms():
    conf, pools, lives = definitions()
    pools["pools"][0]["initial"] = {"team": 3}
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    state, _ = apply_controls(program, program.initial_state(), clock, clock.start("field", event(), eligible=True))
    first = program.plan_spend(state, wrap(state, spend(ledger)))
    state = first["state"]
    second = program.plan_spend(state, wrap(state, spend(ledger, t=100_000, run="0:later")))
    assert first["counter_receipts"][0]["accepted"] == 3
    assert second["counter_receipts"][0]["accepted"] == 0
    assert second["state"]["values"]["count"]["total"] == 3


@pytest.mark.parametrize("scope,expected", [("pool_total", {"total": 11}), ("pool_key", {"a": 3, "b": 8})])
def test_key_totals_do_not_multiply_by_recipient_count(scope, expected):
    conf, pools, lives = definitions(scope=scope)
    pools["pools"][0].update(scope="per_character", initial={"a": 3, "b": 16})
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    state, _ = apply_controls(program, program.initial_state(), clock, clock.start("field", event(), eligible=True))
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    result = program.plan_spend(state, wrap(state, spend(ledger, keys=["b", "a"])))
    assert result["state"]["values"]["count"] == expected


@pytest.mark.parametrize("window,expected", [("created_until_end", 8), ("established_until_end", 0)])
def test_create_and_establishment_windows_are_distinct(window, expected):
    program, state, clock, ledger = setup(window=window, delay=500_000)
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    result = program.plan_spend(state, wrap(state, spend(ledger)))
    assert result["state"]["values"]["count"]["total"] == expected


@pytest.mark.parametrize("reset,expected", [("create", 0), ("end", 0), ("never", 8)])
def test_replacement_reset_policies_keep_history_scope_explicit(reset, expected):
    program, state, clock, ledger = setup(reset=reset)
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    counted = program.plan_spend(state, wrap(state, spend(ledger)))
    captured = copy.deepcopy(counted["state"]["values"])
    state, _ = apply_controls(program, counted["state"], clock, clock.start("field", event(500_000, "0:again"), eligible=True))
    assert state["values"]["count"]["total"] == expected and captured["count"]["total"] == 8
    assert counted["counter_receipts"][0]["history_scope"] == ("lifetime_total" if reset == "never" else "since_last_reset")


def test_counter_planning_is_detached_and_failed_second_counter_is_atomic():
    conf, pools, lives = definitions()
    other = copy.deepcopy(conf["counters"][0])
    other.update(id="other", source_id="source:other")
    conf["counters"].append(other)
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    state, _ = apply_controls(program, program.initial_state(), clock, clock.start("field", event(), eligible=True))
    state["values"]["other"]["total"] = 10**15
    raw = spend(SharedPoolLedger(pools, character_ids=["a", "b"]))
    original = copy.deepcopy((state, raw))
    with pytest.raises(ResourceCounterError) as exc: program.plan_spend(state, wrap(state, raw))
    assert exc.value.issue["code"] == "counter_limit" and (state, raw) == original
    state["values"]["other"]["total"] = 0
    result = program.plan_spend(state, wrap(state, raw))
    assert result["extra_event_count"] == 2
    result["state"]["values"]["count"]["total"] = 999
    assert state["values"]["count"]["total"] == 0


@pytest.mark.parametrize("mutation", ["duplicate_id", "duplicate_source", "duplicate_operation", "unknown_operation", "wrong_owner", "unknown_life"])
def test_definitions_reject_ambiguous_or_unowned_links(mutation):
    args = definitions()
    counter = args[0]["counters"][0]
    if mutation == "duplicate_id": args[0]["counters"].append(copy.deepcopy(counter))
    if mutation == "duplicate_source": args[0]["counters"].append({**counter, "id": "alias"})
    if mutation == "duplicate_operation": counter["operation_ids"].append("refill")
    if mutation == "unknown_operation": counter["operation_ids"] = ["missing"]
    if mutation == "wrong_owner": counter["owner"] = "b"
    if mutation == "unknown_life": counter["lifecycle_id"] = "missing"
    with pytest.raises(ResourceCounterError): CounterProgram(*args, character_ids=["a", "b"])


def test_control_gap_old_generation_and_receipt_replay_are_rejected():
    program, state, clock, ledger = setup()
    steps = clock.consume_ordered_steps(clock.start("field", event(), eligible=True)["ordered_steps"])
    with pytest.raises(ResourceCounterError): program.plan_control(state, steps[1])
    for step in steps: state = program.plan_control(state, step)["state"]
    stale = copy.deepcopy(steps[-1]); stale.update(sequence=state["last_control_sequence"]+1, generation_uid="old")
    with pytest.raises(ResourceCounterError): program.plan_control(state, stale)
    raw = wrap(state, spend(ledger))
    state = program.plan_spend(state, raw)["state"]
    with pytest.raises(ResourceCounterError): program.plan_spend(state, raw)


def test_missing_due_control_is_pending_instead_of_outside_window():
    program, state, clock, ledger = setup()
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    with pytest.raises(ResourceCounterError) as exc:
        program.plan_spend(state, wrap(state, spend(ledger, t=2_000_000)))
    assert exc.value.issue["code"] == "counter_missing_control" and exc.value.issue["severity"] == "pending"


def closing_steps(program, state, clock, t=100_000):
    steps = clock.consume_ordered_steps(clock.end("field", event(t, "0:end"), eligible=True)["ordered_steps"])
    closing = steps[0]
    assert closing["kind"] == "lifecycle_closing"
    return program.plan_control(state, closing)["state"], steps[1:]


def test_closing_before_establishment_cannot_invent_established_qualification():
    program, state, clock, ledger = setup(window="established_until_end", delay=500_000)
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    state, _ = closing_steps(program, state, clock)
    result = program.plan_spend(state, wrap(state, spend(ledger, t=100_000)))
    assert result["state"]["values"]["count"]["total"] == 0


def test_established_control_cannot_retroactively_count_earlier_real_receipt():
    program, state, clock, ledger = setup(window="established_until_end", delay=500_000)
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    raw = spend(ledger, t=100_000)
    state, _ = apply_controls(program, state, clock, clock.handle(clock.pending()[0]))
    with pytest.raises(ResourceCounterError): program.plan_spend(state, wrap(state, raw))


def test_closing_rejects_future_receipts_until_end_control_is_delivered():
    program, state, clock, ledger = setup()
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    state, _ = closing_steps(program, state, clock)
    with pytest.raises(ResourceCounterError):
        program.plan_spend(state, wrap(state, spend(ledger, t=100_001)))


def test_closing_is_not_a_blanket_permit_for_unrelated_same_time_spend():
    program, state, clock, ledger = setup()
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    state, _ = closing_steps(program, state, clock)
    result = program.plan_spend(state, wrap(state, spend(ledger, t=100_000)))
    assert result["state"]["values"]["count"]["total"] == 0


@pytest.mark.parametrize("field,value", [("created_us", []), ("generation_uid", []), ("revision", False)])
def test_state_rejects_malformed_generation_and_time_as_structured_error(field, value):
    program, state, clock, _ = setup()
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    state["lifecycles"]["field"][field] = value
    with pytest.raises(ResourceCounterError) as exc: program._state(state)
    assert exc.value.issue["code"] == "counter_state"


@pytest.mark.parametrize("window,delay,expected", [
    ("created_until_end", 500_000, 8), ("established_until_end", 500_000, 0),
    ("established_until_end", 0, 8),
])
def test_authorized_end_dispatch_uses_observed_window_before_end_reset(window, delay, expected):
    conf, pools, lives = definitions(window=window, delay=delay, reset="end")
    lives["lifecycles"][0]["hooks"]["end"] = [{"kind": "resource_operation", "ref_id": "refill", "order": 1}]
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    state, _ = apply_controls(program, program.initial_state(), clock, clock.start("field", event(), eligible=True))
    steps = clock.consume_ordered_steps(clock.end("field", event(100_000, "0:end"), eligible=True)["ordered_steps"])
    old_capture, accumulated = copy.deepcopy(state["values"]), None
    for step in steps:
        state = program.plan_control(state, step)["state"]
        if step["kind"] == "dispatch":
            raw = spend(ledger, t=100_000, run=step["dispatch"]["run_id"])
            result = program.plan_spend(state, wrap(state, raw))
            state, accumulated = result["state"], result["counter_receipts"][0]
    assert accumulated["accepted"] == expected
    assert old_capture["count"]["total"] == 0
    assert state["values"]["count"]["total"] == 0  # reset follows actual old-generation end spend


def test_replace_weaves_old_end_new_create_without_batch_end_qualification():
    conf, pools, lives = definitions()
    hook = [{"kind": "resource_operation", "ref_id": "refill", "order": 1}]
    lives["lifecycles"][0]["hooks"].update(create=copy.deepcopy(hook), end=copy.deepcopy(hook))
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    state, clock = program.initial_state(), StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    ledger, values, generations = SharedPoolLedger(pools, character_ids=["a", "b"]), [], []
    for t, run in [(0, "0:first"), (100_000, "0:second")]:
        out = clock.start("field", event(t, run), eligible=True)
        for step in clock.consume_ordered_steps(out["ordered_steps"]):
            state = program.plan_control(state, step)["state"]
            if step["kind"] == "dispatch":
                raw = spend(ledger, t=t, run=step["dispatch"]["run_id"])
                plan = program.plan_spend(state, wrap(state, raw))
                state = plan["state"]
                values.append(state["values"]["count"]["total"])
                generations.append(plan["counter_receipts"][0]["generation_uid"])
    assert values == [8, 16, 8]
    assert generations[0] == generations[1] != generations[2]


def test_same_time_receipt_cannot_be_reassigned_to_later_control_cursor():
    program, state, clock, ledger = setup()
    raw = wrap(state, spend(ledger))
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    with pytest.raises(ResourceCounterError) as exc: program.plan_spend(state, raw)
    assert exc.value.issue["code"] == "counter_receipt_order"


def test_control_cannot_retreat_behind_already_received_ledger_time():
    program, state, clock, ledger = setup()
    state = program.plan_spend(state, wrap(state, spend(ledger, t=100_000)))["state"]
    first = clock.consume_ordered_steps(clock.start("field", event(), eligible=True)["ordered_steps"])[0]
    with pytest.raises(ResourceCounterError) as exc: program.plan_control(state, first)
    assert exc.value.issue["code"] == "counter_control_order"


@pytest.mark.parametrize("kind,amount", [("set", 1), ("grant", 5)])
def test_nonspend_changes_advance_ledger_cursor_without_accumulation(kind, amount):
    conf, pools, lives = definitions()
    other = copy.deepcopy(pools["operations"][0])
    other.update(id="other", source_id="source:other", operation=kind, amount=amount, order=2)
    other.pop("spend_policy")
    pools["operations"].append(other)
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    state, clock = program.initial_state(), StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    raw = ledger.apply_operation("other", event_id="tick", run_id="0:other", t_us=0, phase="post",
                                 event_kind="elemental_reaction", keys=["team"], eligible=True)
    plan = program.plan_spend(state, wrap(state, raw))
    assert not plan["counter_receipts"] and plan["state"]["last_ledger_sequence"] == 1
    assert plan["state"]["values"]["count"]["total"] == 0


def test_counter_declaration_permutation_has_identical_semantic_receipt_order():
    conf, pools, lives = definitions()
    conf["counters"].append({**copy.deepcopy(conf["counters"][0]), "id": "aaa", "source_id": "source:other"})
    traces = []
    for counters in [conf["counters"], list(reversed(conf["counters"]))]:
        program = CounterProgram({**conf, "counters": counters}, pools, lives, character_ids=["a", "b"])
        clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
        state, _ = apply_controls(program, program.initial_state(), clock, clock.start("field", event(), eligible=True))
        raw = spend(SharedPoolLedger(pools, character_ids=["a", "b"]))
        traces.append(program.plan_spend(state, wrap(state, raw))["counter_receipts"])
    assert traces[0] == traces[1]


@pytest.mark.parametrize("mutation", ["none", "values_list", "life_list", "time_bool", "ended_without_closing"])
def test_bad_internal_candidate_shapes_are_structured_rejections(mutation):
    program, state, _, _ = setup()
    if mutation == "none": state = None
    if mutation == "values_list": state["values"] = ["count"]
    if mutation == "life_list": state["lifecycles"]["field"] = []
    if mutation == "time_bool": state["last_time_us"] = True
    if mutation == "ended_without_closing": state["lifecycles"]["field"]["phase"] = "ended"
    with pytest.raises(ResourceCounterError) as exc: program._state(state)
    assert exc.value.issue["code"] == "counter_state"
