"""Independent EC14 acceptance; deterministic inputs, no production database."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_app.equipment import (
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
    validate_plan,
)
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.rotation import check_rotation
from tests.test_action_resources_app import resource_plan
from tests.test_mechanics_execution import EVIDENCE, add, fixture, rule
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811
CONTRACT = "automatic-energy-delivery-v1"


def expected_receipt_id(action_id):
    canonical = json.dumps([CONTRACT, action_id], sort_keys=True,
                           ensure_ascii=False, separators=(",", ":"))
    return "@energy_spend:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def energy_case(*, initial=80, capacity=100, cost=80, refund=30,
                refund_order=21, automatic=True, repeat="once", max_cycles=3):
    template, rotation, mechanics = fixture((.2,))
    template["hits"][0]["damage_type_ids"] = ["damage.elemental_burst"]
    rotation["max_cycles"] = max_cycles
    rotation["characters"][0].update(initial_energy=initial, energy_capacity=capacity)
    rotation["abilities"][0].update(id="q", action_type_id="action.elemental_burst",
        cooldown={"group": "q", "duration": 5, "charges": 1,
                  "start": "action_start", "recovery": "sequential"},
        energy={"required": cost, "amount": cost})
    if automatic:
        rotation["abilities"][0]["energy"]["delivery"] = "action_start"
    rotation["actions"][0].update(ability_id="q", order=20, repeat={"mode": repeat})
    rotation["initial_cooldowns"] = [{"character": "a", "group": "q", "ready_in": []}]
    rotation["action_hit_links"] = [{"action_id": "cast", "hit_id": "h0"}]
    mechanics["hit_events"][0]["order"] = 100
    rotation["events"] = []
    if refund is not None:
        rotation["events"].append({"id": "refund", "type": "energy_direct", "t": 0,
            "amounts": {"a": refund}, "order": refund_order, "repeat": {"mode": repeat}, **EVIDENCE})
    if not automatic:
        rotation["events"].append({"id": "spend", "type": "energy_spend", "t": 0,
            "character": "a", "action_id": "cast", "required": cost, "amount": cost,
            "order": 22, "repeat": {"mode": repeat}, **EVIDENCE})
    return template, rotation, mechanics


def run_case(mode, template, rotation, mechanics):
    before = copy.deepcopy((template, rotation, mechanics))
    if mode == "plain":
        output = result = check_rotation(template, rotation)
    else:
        output = execute_mechanics(template, rotation, mechanics)
        result = output["rotation_check"]
    assert (template, rotation, mechanics) == before
    return output, result


def audits(result):
    return [row for row in result["event_log"] if row.get("delivery") == "automatic_action_start"]


def auto_actions(result):
    return [row for row in result["event_log"] if row["type"] == "action" and "energy_delivery" in row]


def assert_summary(result, *, committed, not_committed=0, not_verified=0, horizon=None, unreported=0):
    summary = result["automatic_energy_delivery"]
    reported = committed + not_committed + not_verified
    assert summary["contract"] == CONTRACT
    assert summary["count_scope"] == "reported_action_rows"
    assert summary["planned_actions"] == reported == len(auto_actions(result))
    assert summary["committed_actions"] == committed
    assert summary["not_committed_actions"] == not_committed
    assert summary["not_verified_actions"] == not_verified
    assert summary["receipt_count"] == committed == len(audits(result))
    assert summary["unreported_actions"] == unreported
    assert summary["max_cycles_planned_actions"] == (horizon if horizon is not None else reported)


@pytest.mark.parametrize("mode", ["plain", "mechanics"])
@pytest.mark.parametrize("initial,refund_order,final,before,after,committed", [
    (80, 21, 30, 80, 0, True), (80, 19, 20, 100, 20, True),
    (50, 21, 50, None, None, False), (50, 19, 0, 80, 0, True),
])
def test_review_automatic_admission_refund_order_has_independent_energy_conservation(
        mode, initial, refund_order, final, before, after, committed):
    output, result = run_case(mode, *energy_case(initial=initial, refund_order=refund_order))
    assert result["characters"][0]["final_energy"] == final
    assert result["status"] == ("unproven" if committed else "failed")
    assert_summary(result, committed=int(committed), not_committed=int(not committed))
    if committed:
        row = audits(result)[0]
        assert (row["before"], row["required"], row["amount"], row["after"]) == (before, 80, 80, after)
        assert row["id"] == expected_receipt_id("cast")
        assert row["run_id"] == "0:" + row["id"]
        assert row["parent_run_id"] == "0:cast"
        assert row["phase"] == "action_admission" and row["contract"] == CONTRACT
        assert "order" not in row and "source" not in row
        index = result["event_log"].index(row)
        assert result["event_log"][index - 1]["id"] == "cast"
        assert sum(c["energy_spend_count"] for c in result["cycles"]) == 1
        assert sum(c["burst_energy_spend_count"] for c in result["cycles"]) == 1
    else:
        assert auto_actions(result)[0]["energy_delivery"]["status"] == "not_committed"
        assert result["event_log"][-1]["status"] == "not_verified"
        if mode == "mechanics":
            assert not output.get("result", {}).get("hits")


@pytest.mark.parametrize("mode,initial,final,status", [
    ("plain", 10, 0, "unproven"), ("mechanics", 10, 10, "unproven"),
    ("plain", 0, 0, "unproven"), ("mechanics", 0, 0, "failed"),
])
def test_review_old_manual_mode_retains_its_existing_plain_mechanics_difference(mode, initial, final, status):
    _, result = run_case(mode, *energy_case(initial=initial, capacity=10, cost=10, refund=10, automatic=False))
    assert result["status"] == status
    assert result["characters"][0]["final_energy"] == final
    assert "automatic_energy_delivery" not in result
    assert audits(result) == [] and auto_actions(result) == []


def test_review_periodic_proof_early_stop_does_not_invent_future_audits():
    template, rotation, mechanics = energy_case(initial=60, capacity=60, cost=60, refund=60,
                                                repeat="each_cycle", max_cycles=20)
    rotation["events"][0]["t"] = 1
    _, result = run_case("plain", template, rotation, mechanics)
    assert result["status"] == "passed"
    assert result["energy_check"]["proven_cycle"] == 2
    assert_summary(result, committed=3, horizon=20, unreported=17)
    assert [row["source_cycle"] for row in audits(result)] == [0, 1, 2]
    assert [row["run_id"] for row in audits(result)] == [f"{n}:" + expected_receipt_id("cast") for n in range(3)]
    assert all(c["boundary_state"]["pending_events"] == [] for c in result["cycles"])


def test_review_failed_second_cycle_accounts_reported_not_verified_tail():
    args = energy_case(initial=60, capacity=60, cost=60, refund=None, repeat="each_cycle", max_cycles=20)
    _, result = run_case("plain", *args)
    assert result["status"] == "failed"
    assert result["energy_check"]["failed_cycle"] == 1
    assert_summary(result, committed=1, not_committed=1, not_verified=18, horizon=20)
    assert [r["energy_delivery"]["status"] for r in auto_actions(result)] == [
        "committed", "not_committed", *(["not_verified"] * 18)]


@pytest.mark.parametrize("order", [-10**12, 10**12])
def test_review_audit_never_claims_next_order_even_for_extreme_integer_orders(order):
    template, rotation, mechanics = energy_case()
    rotation["actions"][0]["order"] = order
    rotation["events"][0]["order"] = order + 1
    rotation["actions"][0]["occupation_end"] = 0
    _, result = run_case("plain", template, rotation, mechanics)
    assert result["status"] == "unproven"
    assert result["characters"][0]["final_energy"] == 30
    assert "order" not in audits(result)[0]
    assert [(r["id"], r["order"]) for r in result["event_log"] if "order" in r] == [
        ("cast", order), ("refund", order + 1)]


@pytest.mark.parametrize("change", [
    lambda r: r["abilities"][0]["energy"].update(amount=0, required=0),
    lambda r: r["abilities"][0]["energy"].update(amount=79),
    lambda r: r["abilities"][0].update(action_type_id="action.elemental_skill"),
    lambda r: r["abilities"][0]["cooldown"].update(charges=2),
    lambda r: r["abilities"][0]["energy"].update(delivery=None),
    lambda r: r["abilities"][0]["energy"].update(delivery="existing_events"),
])
def test_review_pure_engine_explicit_marker_has_strict_visible_scope(change):
    template, rotation, mechanics = energy_case()
    change(rotation)
    _, result = run_case("plain", template, rotation, mechanics)
    assert result["status"] in ("invalid", "pending")
    assert not audits(result)


@pytest.mark.parametrize("mode", ["plain", "mechanics"])
@pytest.mark.parametrize("remove_middle", [False, True])
def test_review_explicit_three_actions_have_three_audits_and_deleting_one_never_clones_it(mode, remove_middle):
    template, rotation, mechanics = energy_case(initial=60, capacity=60, cost=60, refund=None)
    rotation["period"] = template["loop_duration"] = 50
    action, hit = rotation["actions"][0], template["hits"][0]
    rotation["actions"], template["hits"] = [], []
    rotation["action_hit_links"], mechanics["hit_events"] = [], []
    selected = (1, 3) if remove_middle else (1, 2, 3)
    for number in selected:
        start, aid, hid = (number - 1) * 20, f"q{number}", f"hit{number}"
        rotation["actions"].append({**copy.deepcopy(action), "id": aid, "start": start, "occupation_end": start + .1})
        template["hits"].append({**copy.deepcopy(hit), "id": hid, "t": start + .2})
        rotation["action_hit_links"].append({"action_id": aid, "hit_id": hid})
        mechanics["hit_events"].append({"action_id": aid, "hit_id": hid, "order": 100})
    rotation["events"] = [{"id": f"refund{number}", "type": "energy_direct", "t": time,
        "amounts": {"a": 60}, "order": 0, "repeat": {"mode": "once"}, **EVIDENCE}
        for number, time in enumerate((10, 30))]
    output, result = run_case(mode, template, rotation, mechanics)
    assert result["status"] == "unproven"
    assert result["characters"][0]["final_energy"] == 0
    assert_summary(result, committed=len(selected))
    assert [(row["id"], row["before"], row["after"], row["source_cycle"]) for row in audits(result)] == [
        (expected_receipt_id(f"q{number}"), 60, 0, 0) for number in selected]
    if mode == "mechanics":
        assert output["result"]["summary"]["total_damage"] == 45 * len(selected)


@pytest.mark.parametrize("mode", ["plain", "mechanics"])
def test_review_count_budget_rejects_before_energy_cd_and_observer_commit(mode, monkeypatch):
    from genshin_engine import automatic_energy
    from genshin_engine import mechanics as mechanics_module
    template, rotation, mechanics = energy_case(refund=None)
    # A wrongly committed CD would still be visible at the first 10s boundary.
    rotation["abilities"][0]["cooldown"]["duration"] = 15
    monkeypatch.setattr(automatic_energy, "MAX_AUTOMATIC_ENERGY_RECEIPTS", 0)
    monkeypatch.setattr(mechanics_module._Mechanics, "on_event",
                        lambda *a, **k: pytest.fail("budget rejected action reached observer"))
    output, result = run_case(mode, template, rotation, mechanics)
    assert result["status"] == "pending"
    assert result["characters"][0]["final_energy"] == 80
    failed_cycle = result["cycles"][0]
    assert failed_cycle["incomplete"] and failed_cycle["executed_action_count"] == 0
    assert failed_cycle["energy_spend_count"] == 0
    assert failed_cycle["start_energy"] == failed_cycle["end_energy"] == {"a": 80}
    if mode == "plain":
        assert failed_cycle["boundary_state"]["cooldown_ready_in"] == {'["a","q"]': []}
    else:
        # The mechanism executor stopped at the rejected action. It must not
        # fabricate a future full-period snapshot merely to inspect its CD.
        assert failed_cycle["boundary_state"] is None
    assert "next_ready_at" not in auto_actions(result)[0]
    assert "energy_admission" not in auto_actions(result)[0]
    assert result["energy_check"]["proven_cycle"] is None
    assert_summary(result, committed=0, not_committed=1)
    assert auto_actions(result)[0]["energy_delivery"]["reason"] == "automatic_energy_receipt_limit"
    if mode == "mechanics":
        assert output["result"] is None and output["resolved_template"] is None


@pytest.mark.parametrize("budget_delta", [0, -1])
def test_review_byte_limit_is_exact_canonical_utf8_array_size_before_first_commit(monkeypatch, budget_delta):
    from genshin_engine import automatic_energy
    template, rotation, mechanics = energy_case(refund=None)
    identity = expected_receipt_id("cast")
    expected_row = {"id": identity, "run_id": "0:" + identity, "type": "energy_spend",
        "delivery": "automatic_action_start", "phase": "action_admission", "contract": CONTRACT,
        "action_id": "cast", "ability_id": "q", "character": "a", "parent_run_id": "0:cast",
        "cycle": 0, "source_cycle": 0, "t": 0.0, "status": "passed", "before": 80,
        "required": 80, "amount": 80, "deficit": 0, "after": 0, "committed_at_action_start": True}
    byte_count = len(json.dumps([expected_row], ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf8"))
    monkeypatch.setattr(automatic_energy, "MAX_AUTOMATIC_ENERGY_RECEIPT_BYTES", byte_count + budget_delta)
    _, result = run_case("plain", template, rotation, mechanics)
    if budget_delta == 0:
        assert result["status"] == "unproven"
        assert audits(result) == [expected_row]
        assert result["automatic_energy_delivery"]["receipt_bytes"] == byte_count
    else:
        assert result["status"] == "pending"
        assert result["characters"][0]["final_energy"] == 80
        assert result["automatic_energy_delivery"]["receipt_bytes"] == 2
        assert audits(result) == []


def test_review_second_budget_failure_preserves_first_commit_and_marks_remaining_actions(monkeypatch):
    from genshin_engine import automatic_energy
    template, rotation, mechanics = energy_case(initial=60, capacity=60, cost=60, refund=60,
                                                repeat="each_cycle", max_cycles=20)
    rotation["events"][0]["t"] = 1
    monkeypatch.setattr(automatic_energy, "MAX_AUTOMATIC_ENERGY_RECEIPTS", 1)
    output, result = run_case("mechanics", template, rotation, mechanics)
    assert result["status"] == output["status"] == "pending"
    assert result["characters"][0]["final_energy"] == 60
    assert_summary(result, committed=1, not_committed=1, not_verified=18, horizon=20)
    completed, failed = result["cycles"]
    assert completed["status"] == "passed"
    assert completed["boundary_state"]["cooldown_ready_in"] == {'["a","q"]': []}
    assert failed["status"] == "failed" and failed["incomplete"]
    assert failed["boundary_state"] is None
    assert failed["start_energy"] == failed["end_energy"] == {"a": 60}
    assert failed["executed_action_count"] == failed["energy_spend_count"] == 0
    first, rejected = auto_actions(result)[:2]
    assert first["energy_delivery"]["status"] == "committed" and first["next_ready_at"] == 5
    assert rejected["energy_delivery"]["status"] == "not_committed"
    assert rejected["charges_before"] == 1
    assert "next_ready_at" not in rejected and "energy_admission" not in rejected
    assert output["result"] is None and output["resolved_template"] is None


def test_review_controlled_observer_pending_keeps_committed_historical_prefix(monkeypatch):
    from genshin_engine import mechanics as mechanics_module
    from genshin_engine.elemental import ElementalPending

    def stop(self, event, row, frontline, energy):
        assert event["kind"] == "action" and energy["a"] == 0
        raise ElementalPending("independent_pending", "独立提交后中止反例", event["id"])

    monkeypatch.setattr(mechanics_module._Mechanics, "on_event", stop)
    output, result = run_case("mechanics", *energy_case(refund=None))
    assert output["status"] == result["status"] == "pending"
    assert output["result"] is None and output["resolved_template"] is None
    assert_summary(result, committed=1)
    assert auto_actions(result)[0]["status"] == "pending"
    assert auto_actions(result)[0]["energy_delivery"]["status"] == "committed"
    assert [(row["before"], row["after"]) for row in audits(result)] == [(80, 0)]
    assert result["characters"][0]["final_energy"] == 0


@pytest.mark.parametrize("place", ["action", "hit", "event", "selector", "self_snapshot", "replacement"])
def test_review_reserved_audit_identity_cannot_be_a_gameplay_event_or_reference(place):
    template, rotation, mechanics = energy_case()
    reserved = expected_receipt_id("cast")
    if place == "action":
        rotation["actions"][0]["id"] = reserved
        rotation["action_hit_links"][0]["action_id"] = reserved
        mechanics["hit_events"][0]["action_id"] = reserved
    elif place == "hit":
        template["hits"][0]["id"] = reserved
        rotation["action_hit_links"][0]["hit_id"] = reserved
        mechanics["hit_events"][0]["hit_id"] = reserved
    elif place == "event":
        rotation["events"][0]["id"] = reserved
    elif place == "selector":
        add(mechanics, rule(trigger={"event": "hit", "phase": "post", "event_ids": [reserved]}))
    elif place == "self_snapshot":
        mechanics["hit_events"][0]["self_snapshot_event"] = reserved
    else:
        add(mechanics, rule(resources=[{"id": "refund", "kind": "energy_direct", "delay": 0,
            "amounts": {"a": 10}, "source_part": "refund"}]))
        mechanics["resource_ownership"] = [{"rule_id": "boost", "action_id": "cast", "source_part": "refund",
                                           "replaces_event_ids": [reserved]}]
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] == "invalid"
    assert any(issue["code"] == "automatic_energy_namespace" for issue in output["issues"])


def test_review_namespace_is_not_a_free_text_ban_or_new_legacy_restriction():
    args = energy_case()
    args[1]["actions"][0]["source"] = "@energy_spend: 在这里仅是用户说明，不是事件引用"
    _, auto = run_case("plain", *args)
    assert auto["status"] == "unproven"
    template, rotation, mechanics = energy_case(automatic=False)
    rotation["events"][0]["id"] = "@energy_spend:legacy-user-id"
    _, legacy = run_case("plain", template, rotation, mechanics)
    assert legacy["status"] == "unproven"


def test_review_legitimate_same_action_resource_refund_is_independent_of_automatic_spend():
    template, rotation, mechanics = energy_case(initial=60, capacity=60, cost=60, refund=None)
    add(mechanics, rule(resources=[{"id": "refund", "kind": "energy_direct", "delay": 0,
        "amounts": {"a": 30}, "source_part": "refund"}]))
    mechanics["resource_ownership"] = [{"rule_id": "boost", "action_id": "cast", "source_part": "refund",
                                       "replaces_event_ids": []}]
    output, result = run_case("mechanics", template, rotation, mechanics)
    assert output["status"] == "unproven"
    assert result["characters"][0]["final_energy"] == 30
    assert audits(result)[0]["after"] == 0
    assert_summary(result, committed=1)


def automatic_native():
    plan = resource_plan()
    plan["action_resource_sources"][0]["energy_delivery"] = "action_start"
    plan["rotation_model"]["events"] = []
    return plan


def accept_resource_sources(plan, db):
    report = compose_equipment_plan(plan, db)
    records = {r["ability_id"]: r for r in report["action_resource_compilations"]}
    for binding in plan["action_resource_sources"]:
        binding.update(records[binding["ability_id"]]["required_confirmation"])
    return plan


def test_review_app_single_source_acceptance_only_materializes_private_ability(resource_db):
    plan = automatic_native()
    preview = compose_equipment_plan(plan, resource_db)
    assert preview["state"] == "pending"
    accept_resource_sources(plan, resource_db)
    original = copy.deepcopy(plan)
    out = run_plan_calc(plan, resource_db)
    assert plan == original
    # Returned plans legitimately add resolution caches; native input must stay exact.
    assert input_view(out["plan"]) == input_view(original)
    assert plan["rotation_model"]["abilities"][0]["energy"] == {}
    compiled = out["report"]["compiled_rotation_model"]
    assert compiled["events"] == []
    assert compiled["abilities"][0]["energy"] == {"required": 60, "amount": 60, "delivery": "action_start"}
    record = out["report"]["action_resource_compilations"][0]
    assert record["action_spend_links"] == []
    assert record["energy_delivery"]["links"] == [{"action_id": "cast", "ability_id": "q",
        "receipt_id": expected_receipt_id("cast")}]
    assert all("events" not in path for path in record["target_paths"])
    assert_summary(out["result"]["rotation_check"], committed=1)


def test_review_explicit_old_default_preserves_scope_but_changes_native_input_hash(resource_db):
    old = accept_resource_sources(resource_plan(), resource_db)
    old_report = compose_equipment_plan(old, resource_db)
    explicit = copy.deepcopy(old)
    explicit["action_resource_sources"][0]["energy_delivery"] = "existing_events"
    explicit_report = compose_equipment_plan(explicit, resource_db)
    assert explicit_report["state"] == "ready"
    before = old_report["action_resource_compilations"][0]
    after = explicit_report["action_resource_compilations"][0]
    assert before["scope_fingerprint"] == after["scope_fingerprint"]
    assert "energy_delivery" not in before and "energy_delivery" not in after
    assert fingerprint(input_view(old)) != fingerprint(input_view(explicit))
    assert old_report["compiled_rotation_model"] == explicit_report["compiled_rotation_model"]


@pytest.mark.parametrize("values", [{}, {"required": 60, "amount": 60}, {"required": None, "amount": None}])
def test_review_auto_native_cannot_keep_even_one_manual_spend(resource_db, values):
    plan = automatic_native()
    spend = resource_plan()["rotation_model"]["events"][0]
    spend.update(values)
    plan["rotation_model"]["events"].append(spend)
    with pytest.raises(PlanError) as error:
        compose_equipment_plan(plan, resource_db)
    assert error.value.reason == "duplicate_delivery"


@pytest.mark.parametrize("with_binding", [True, False])
def test_review_native_private_delivery_marker_cannot_claim_trust(with_binding):
    plan = automatic_native()
    if not with_binding:
        plan.pop("action_resource_sources")
    plan["rotation_model"]["abilities"][0]["energy"] = {"required": 60, "amount": 60, "delivery": "action_start"}
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_review_ownership_cannot_launder_duplicate_before_a_source_query(resource_db, monkeypatch):
    from genshin_db import runtime_action_resource
    plan = automatic_native()
    plan["rotation_model"]["events"] = resource_plan()["rotation_model"]["events"]
    plan["mechanics_model"]["resource_ownership"] = [{"rule_id": "refund", "action_id": "cast",
        "source_part": "refund", "replaces_event_ids": ["spend"]}]
    monkeypatch.setattr(runtime_action_resource, "get_runtime_action_resource_profile",
                        lambda *a, **kw: pytest.fail("duplicate reached resource query"))
    with pytest.raises(PlanError) as error:
        compose_equipment_plan(plan, resource_db)
    assert error.value.reason == "duplicate_delivery"


def test_review_known_aliases_can_mix_delivery_but_keep_one_physical_cooldown(resource_db):
    plan = automatic_native()
    rotation = plan["rotation_model"]
    alias = copy.deepcopy(rotation["abilities"][0])
    alias["id"] = "q.manual"
    rotation["abilities"].append(alias)
    action = copy.deepcopy(rotation["actions"][0])
    action.update(id="cast.manual", ability_id="q.manual", start=20, occupation_end=20.1)
    rotation["actions"].append(action)
    rotation["period"] = plan["manual_template"]["loop_duration"] = 30
    spend = copy.deepcopy(resource_plan()["rotation_model"]["events"][0])
    spend.update(action_id="cast.manual", t=20)
    rotation["events"] = [spend, {"id": "refund", "type": "energy_direct", "t": 10,
        "order": 0, "amounts": {"a": 60}, "repeat": {"mode": "once"}, **EVIDENCE}]
    binding = copy.deepcopy(plan["action_resource_sources"][0])
    binding.update(ability_id="q.manual", energy_delivery="existing_events")
    plan["action_resource_sources"].append(binding)
    accept_resource_sources(plan, resource_db)
    result = run_plan_calc(plan, resource_db)["result"]["rotation_check"]
    assert result["status"] == "unproven"
    assert_summary(result, committed=1)
    assert sum(c["energy_spend_count"] for c in result["cycles"]) == 2
    assert len([r for r in result["event_log"] if r["type"] == "energy_spend"]) == 2
    # Moving alias into the original cooldown is a real failure, not a new group.
    action["start"], action["occupation_end"], spend["t"] = 12, 12.1, 12
    changed = run_plan_calc(plan, resource_db)["result"]["rotation_check"]
    assert changed["status"] == "failed"
    assert any(i["code"] == "cooldown_unavailable" for i in changed["issues"])


def test_review_auto_source_scope_covers_action_set_and_mode_not_unrelated_time_or_panel(resource_db):
    plan = accept_resource_sources(automatic_native(), resource_db)
    before = compose_equipment_plan(plan, resource_db)["action_resource_compilations"][0]
    timed = copy.deepcopy(plan)
    timed["rotation_model"]["actions"][0].update(start=.01, occupation_end=.11)
    timed["manual_template"]["characters"][0]["stats"]["atk"] = 200
    after = compose_equipment_plan(timed, resource_db)
    assert after["state"] == "ready"
    assert after["action_resource_compilations"][0]["scope_fingerprint"] == before["scope_fingerprint"]
    assert fingerprint(input_view(timed)) != fingerprint(input_view(plan))
    extra = copy.deepcopy(timed["rotation_model"]["actions"][0])
    extra.update(id="second", start=5, occupation_end=5.1)
    timed["rotation_model"]["actions"].append(extra)
    changed = compose_equipment_plan(timed, resource_db)
    assert changed["state"] == "pending"
    assert changed["action_resource_compilations"][0]["scope_fingerprint"] != before["scope_fingerprint"]


@pytest.mark.parametrize("mode", [None, "automatic", True])
def test_review_binding_mode_cannot_default_unknown_or_null_to_existing(mode):
    plan = automatic_native()
    plan["action_resource_sources"][0]["energy_delivery"] = mode
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("mode", ["plain", "mechanics"])
@pytest.mark.parametrize("failure", ["cooldown", "frontline", "occupation"])
def test_review_failed_admission_never_debits_or_creates_audit(mode, failure):
    template, rotation, mechanics = energy_case(refund=None)
    if failure == "cooldown":
        rotation["initial_cooldowns"][0]["ready_in"] = [15]
        expected = "cooldown_unavailable"
    elif failure == "frontline":
        rotation["characters"].append({**copy.deepcopy(rotation["characters"][0]), "id": "b"})
        template["characters"].append({**copy.deepcopy(template["characters"][0]), "id": "b"})
        rotation["initial_frontline"] = "b"
        template["switches"] = [{"t": 0, "character": "b"}]
        expected = "not_frontline"
    else:
        rotation["abilities"].append({"id": "wait", "character": "a", "action_type_id": "action.wait",
                                      "cooldown": None, "energy": None, **EVIDENCE})
        rotation["actions"][0].update(start=.2, occupation_end=.3)
        template["hits"][0]["t"] = .4
        rotation["actions"].insert(0, {"id": "busy", "ability_id": "wait", "start": 0,
            "occupation_end": 1, "order": 0, "repeat": {"mode": "once"}, **EVIDENCE})
        expected = "action_overlap"
    _, result = run_case(mode, template, rotation, mechanics)
    assert result["status"] == "failed"
    assert any(issue["code"] == expected for issue in result["issues"])
    assert next(c for c in result["characters"] if c["id"] == "a")["final_energy"] == 80
    assert_summary(result, committed=0, not_committed=1)
    assert sum(c["energy_spend_count"] for c in result["cycles"]) == 0


def test_review_plan_v2_numerically_complete_unbound_input_still_cannot_inject_private_marker():
    template, rotation, _ = energy_case(automatic=False)
    plan = new_plan(template)
    plan.update(plan_version=2, rotation_model=rotation)
    validate_plan(plan)  # Baseline is legal; the marker is the sole rejection trigger.
    rotation["abilities"][0]["energy"]["delivery"] = "action_start"
    with pytest.raises(PlanError):
        validate_plan(plan)
    plan["plan_version"] = 1
    with pytest.raises(PlanError):
        validate_plan(plan)
