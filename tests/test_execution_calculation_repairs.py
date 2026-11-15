"""Character-neutral regressions for command/error identity and energy credits."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app import cli
from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance
from genshin_app.equipment.schema import PlanError
from genshin_engine import mechanics
from genshin_engine.energy_credit import plan_energy_gain
from genshin_engine.rotation_port import analyze_rotation, expand_rotation, run_rotation
from genshin_engine.snapshot_capture import SnapshotCapturePending
from tests.test_agent_candidate import native_session as _native_session
from tests.test_mechanics_execution import EVIDENCE
from tests.test_mechanics_execution import add as add_mechanics_rule
from tests.test_mechanics_execution import fixture as mechanics_fixture
from tests.test_mechanics_execution import rule as mechanics_rule
from tests.test_rotation_energy import _model, _template
from tests.test_snapshot_captures import er_fixture

native_session = _native_session


@pytest.mark.parametrize("command", ["validate", "compose", "calc", "export"])
def test_shared_cli_handler_preserves_selected_command(monkeypatch, tmp_path, capsys, command):
    def failure(_args):
        raise KeyError("private-key")
    monkeypatch.setattr(cli, "COMMANDS", [{"path": ("equipment", name), "description": "fixture",
        "args": [], "handler": failure} for name in ("validate", "compose", "calc", "export")])
    assert cli.main(["--data-dir", str(tmp_path), "equipment", command]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["system_fault"]["operation"] == "cli.equipment."+command
    assert "private-key" not in json.dumps(payload)


def test_cli_and_gui_preserve_same_capacity_classification(monkeypatch, tmp_path, capsys):
    from genshin_app import equipment
    from genshin_app.gui.ports.equipment import EquipmentGuiPort
    def failure(*args, **kwargs):
        raise PlanError("capacity", "mechanics_pending", {"issues": [{"code": "attribute_declaration_limit"}]})
    monkeypatch.setattr(cli, "COMMANDS", [{"path": ("probe",), "description": "fixture", "args": [], "handler": failure}])
    assert cli.main(["--data-dir", str(tmp_path), "probe"]) == 1
    cli_error = json.loads(capsys.readouterr().err)
    monkeypatch.setattr(equipment, "compose_equipment_plan", failure)
    gui_error = EquipmentGuiPort(tmp_path, tmp_path/'absent.db', manager_provider=lambda: None)._equipment_call("compose_equipment_plan")
    for error in (cli_error, gui_error):
        assert error["category"] == "engine_capability_missing"
        assert error["recovery"]["action"] == "report_capacity_boundary"
    assert cli_error["operation"] == "cli.probe" and gui_error["operation"] == "gui.compose_equipment_plan"


@pytest.mark.parametrize("code", ["attribute_declaration_limit", "attribute_work_limit", "attribute_receipt_limit", "attribute_output_limit", "attribute_dependency_depth"])
def test_nested_capacity_is_not_request_error_or_invented_adjustment(code):
    details = {"status": "pending", "issues": [{"code": code, "path": "attribute_ledger"}]}
    classification = classify_domain_failure("mechanics_pending", operation="gs_calculate", details=details)
    guidance = tool_failure_guidance("mechanics_pending", operation="gs_calculate", details=details)
    assert classification["category"] == "engine_capability_missing"
    assert guidance["recovery"] == {"action": "report_capacity_boundary", "retry_same_request": False}


def test_capacity_guidance_only_uses_published_adjustment():
    adjustment = {"path": "/calculation_options/attribute_limits/frames", "maximum": 1000}
    details = {"issues": [{"code": "attribute_declaration_limit", "details": {"adjustment": adjustment}}]}
    assert tool_failure_guidance("mechanics_pending", details=details)["recovery"] == {
        "action": "adjust_execution_budget", "retry_same_request": False, "adjustment": adjustment}
    direct_issue = {"issues": [{"code": "attribute_declaration_limit", "adjustment": adjustment}]}
    assert tool_failure_guidance("mechanics_pending", details=direct_issue)["recovery"]["adjustment"] == adjustment
    assert classify_domain_failure("invalid_execution_limits", details={"code": "schema"})["category"] == "request_error"
    assert classify_domain_failure("attribute_dependency_cycle")["category"] == "request_error"


def test_capacity_classification_scans_complete_typed_list_without_free_text_guess():
    issues = [{"code": "unrelated", "message": "attribute_work_limit"} for _ in range(20000)]
    assert classify_domain_failure("mechanics_pending", details={"issues": issues})["category"] == "request_error"
    issues[256] = {"code": "attribute_work_limit"}
    assert classify_domain_failure("mechanics_pending", details={"issues": issues})["category"] == "engine_capability_missing"
    assert tool_failure_guidance("mechanics_pending", details={"issues": issues})["recovery"]["action"] == "report_capacity_boundary"
    assert classify_domain_failure("invalid_schema", details={"issues": issues})["category"] == "request_error"
    assert tool_failure_guidance("invalid_schema", details={"issues": issues})["recovery"]["action"] == "correct_arguments"
    issues.append({"code": "invalid_schema"})
    assert classify_domain_failure("mechanics_pending", details={"issues": issues})["category"] == "request_error"
    cyclic = []
    cyclic.append(cyclic)
    assert classify_domain_failure("mechanics_pending", details=cyclic)["category"] == "request_error"
    assert classify_domain_failure("mechanics_pending", details={"issues": [{"code": "source_missing"}]})["category"] != "engine_capability_missing"


def test_large_tool_error_classifies_original_issue_before_compaction(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    details = {"status": "pending", "issues": [{"code": "attribute_declaration_limit", "path": "attribute_ledger"}],
               "mechanics_trace": [{"diagnostic": "x"*20000}]}
    def fail(*args, **kwargs):
        raise PlanError("capacity fixture", "mechanics_pending", details)
    monkeypatch.setattr(candidate, "run_plan_calc", fail)
    result = native_session.call_tool("gs_calculate", {"expected_revision": 0})
    assert result["reason"] == "mechanics_pending"
    assert result["category"] == "engine_capability_missing"
    assert result["recovery"]["action"] == "report_capacity_boundary"
    assert result["details"]["resource"] == "diagnostic_error"
    assert result["details"]["issue_codes"] == ["attribute_declaration_limit"]
    assert result["details"]["issue_count"] == 1
    assert result["details"]["attempt_id"] == native_session._diagnostic_cache["attempt_id"]
    assert native_session.call_tool("gs_read_context", {"resource": "diagnostic_error", "pointer": "/details/issues"})["code"] == 0
    assert not native_session.summary()["ready"]


@pytest.mark.parametrize("credit_at", [9.2, 10.2, 20.2])
def test_legacy_event_sample_keeps_true_origin_across_boundaries(credit_at):
    template, rotation, model = er_fixture(credit_at)
    model.pop("snapshot_captures")
    model["rules"][0]["effects"][0]["value"]["event_id"] = "cast"
    before = copy.deepcopy((template, rotation, model))
    output = mechanics.execute_mechanics(template, rotation, model)
    assert output["status"] == "unproven", output["issues"]
    credit = next(row for row in output["rotation_check"]["event_log"] if row["id"] == "pickup")
    assert credit["source_cycle"] == 0
    assert credit["energy"][0]["gain"] == pytest.approx(3.3)
    sample = next(row for row in output["sampling_trace"] if row["event_id"] == "pickup")
    assert sample["cycle"] == 0 and sample["sample_t"] == 0
    assert (template, rotation, model) == before
    if credit_at > 10:
        pending = output["rotation_check"]["cycles"][0]["boundary_state"]["mechanics"]["pending_samples"]
        assert any(row["event_id"] == "pickup" and row["reference_id"] == "cast" and row["relative_cycle"] == -1 for row in pending)


class CreditObserver:
    def __init__(self, *, fail=None):
        self.captures = {}
        self.calls = []
        self.failed = fail

    def energy_recharge(self, cid, time, frontline, event):
        self.calls.append((cid, time, frontline, event["origin"], event["id"]))
        if cid == self.failed:
            raise SnapshotCapturePending("snapshot_event_unexecuted", "fixture missing sample", "credit")
        return .1 if event["origin"] == 0 else .9

    def on_event(self, *args):
        return []

    def boundary(self, *args):
        return {}


def multiple_receivers(order=("a", "b", "c")):
    model = _model()
    base = model["characters"][0]
    model["characters"] = [{**copy.deepcopy(base), "id": cid, "initial_energy": 0, "energy_capacity": 100} for cid in order]
    model["abilities"] = [model["abilities"][0]]
    model["actions"] = [model["actions"][0]]
    model["actions"][0]["repeat"] = {"mode": "once"}
    model["actions"][0]["occupation_end"] = .1
    model["events"] = model["events"][:1]+model["events"][2:]
    for row in model["events"]:
        row["repeat"] = {"mode": "once"}
    model["events"][1]["credit_at"] = 11.2
    template = _template(model)
    template["characters"].sort(key=lambda row: row["id"])
    context, data = analyze_rotation(model, template)
    assert not context.issues
    expanded, windows, finite_until = expand_rotation(data, model, context)
    return model, data, expanded, windows, finite_until, context


def test_all_receivers_prepared_before_failed_sampling_consumes_any_credit():
    observer = CreditObserver(fail="c")
    with pytest.raises(SnapshotCapturePending) as caught:
        run_rotation(*multiple_receivers(), observer=observer)
    prefix = caught.value.rotation_check_prefix
    credit = prefix["event_log"][-1]
    assert credit["id"] == "credit" and credit["status"] == "pending" and "energy" not in credit
    assert {row["id"]: row["final_energy"] for row in prefix["characters"]} == {"a": 0, "b": 0, "c": 0}
    assert [row[0] for row in observer.calls] == ["a", "b", "c"]
    assert all(row[3:] == (0, "credit") for row in observer.calls)


def test_mechanics_failed_sampling_is_explicit_uncommitted_attempt(monkeypatch):
    template, rotation, model = er_fixture(.2)
    for cid in ("b", "c"):
        template["characters"].append({**copy.deepcopy(template["characters"][0]), "id": cid})
        rotation["characters"].append({**copy.deepcopy(rotation["characters"][0]), "id": cid})
    original = mechanics._Mechanics.value
    def sample(self, effect, rule, instance, cid, event, snapshot=None):
        if event["id"] == "pickup" and cid == "c":
            raise SnapshotCapturePending("snapshot_event_unexecuted", "synthetic third receiver failure", "pickup")
        return original(self, effect, rule, instance, cid, event, snapshot)
    monkeypatch.setattr(mechanics._Mechanics, "value", sample)
    result = mechanics.execute_mechanics(template, rotation, model)
    assert result["status"] == "pending" and result["result"] is None
    assert all(row["final_energy"] == 0 for row in result["rotation_check"]["characters"])
    attempt = next(row for row in result["mechanics_trace"] if row["type"] == "energy_credit_sampling")
    assert attempt["status"] == "not_committed" and attempt["sample_count"] == 2
    credit = result["rotation_check"]["event_log"][-1]
    assert credit["id"] == "pickup" and "energy" not in credit


def test_delayed_credit_uses_source_cycle_when_next_cycle_snapshot_already_exists():
    template, rotation, model = mechanics_fixture((.3,))
    rotation["characters"][0].update(initial_energy=0, energy_capacity=100)
    rotation["abilities"][0]["rules"] = [{"id": "particles", "kind": "energy_spawn"}]
    rotation["actions"][0]["rule_outcomes"] = [{"rule_id": "particles", "status": "triggered", "event_ids": ["spawn"]}]
    rotation["events"] = [
        {"id": "spawn", "type": "energy_spawn", "t": .2, "order": 1, "action_id": "cast", "count": 1,
         "element": "cryo", "particle_type": "particle", "source_kind": "skill", "repeat": {"mode": "each_cycle"}, **EVIDENCE},
        {"id": "pickup", "type": "energy_credit", "credit_at": 10.2, "order": 2, "spawn_id": "spawn", "count": 1,
         "repeat": {"mode": "each_cycle"}, **EVIDENCE}]
    add_mechanics_rule(model, mechanics_rule(benefit="benefit.energy_recharge.all", duration=30,
        value={"kind": "stat", "subject": "owner", "stat": "atk", "factor": .001, "sample": "event", "event_id": "cast"}))
    template["buffs"] = [{"id": "late-atk", "start": 9, "end": 30, "scope": "team", "effects": [
        {"id": "attack", "benefit_type_id": "benefit.stat_add.atk", "zone": "stat_add", "stat": "atk",
         "value": 100, "applies_to": {"mode": "all"}}]}]
    result = mechanics.execute_mechanics(template, rotation, model)
    assert result["issues"] == []
    credits = [row for row in result["rotation_check"]["event_log"] if row["id"] == "pickup"]
    assert [row["energy"][0]["gain"] for row in credits] == pytest.approx([3.3, 3.6])
    samples = [row for row in result["sampling_trace"] if row["event_id"] == "pickup"]
    assert [(row["cycle"], row["sample_t"], row["source_value"]) for row in samples] == [(0, 0.0, 100), (1, 10.0, 200)]


def test_recipient_order_does_not_change_values_or_source_sampling():
    outputs = [run_rotation(*multiple_receivers(order), observer=CreditObserver()) for order in (("a", "b", "c"), ("c", "a", "b"))]
    credits = [next(row for row in value["event_log"] if row["id"] == "credit") for value in outputs]
    values = [{row["character"]: row["gain"] for row in credit["energy"]} for credit in credits]
    assert values[0] == values[1]
    assert values[0] == pytest.approx({"a": 13.2, "b": 9.24, "c": 9.24})


def test_finite_er_gain_overflow_is_rejected_before_any_balance_commit():
    class OverflowObserver(CreditObserver):
        def energy_recharge(self, cid, time, frontline, event):
            return 1e308 if cid == "c" else 0
    with pytest.raises(SnapshotCapturePending) as caught:
        run_rotation(*multiple_receivers(), observer=OverflowObserver())
    result = caught.value.rotation_check_prefix
    assert result["status"] == "pending"
    assert result["issues"][-1]["code"] == "energy_credit_value_invalid"
    assert result["event_log"][-1]["id"] == "credit" and "energy" not in result["event_log"][-1]
    assert all(row["final_energy"] == 0 for row in result["characters"])
    json.dumps(result, allow_nan=False)


def test_pure_gain_plan_does_not_turn_zero_into_missing_or_overflow_into_energy():
    assert plan_energy_gain("a", 5, 10, 0, frontline="b")["gain"] == 0
    row = plan_energy_gain("a", 5, 10, 20, frontline="b", energy_recharge=1)
    assert (row["gain"], row["after"], row["overflow"], row["on_field"]) == (5, 10, 15, False)
    with pytest.raises(SnapshotCapturePending):
        plan_energy_gain("a", 5, 10, float("inf"), frontline="a")
    with pytest.raises(SnapshotCapturePending):
        plan_energy_gain("a", 5, 10, 10**1000, frontline="a")
