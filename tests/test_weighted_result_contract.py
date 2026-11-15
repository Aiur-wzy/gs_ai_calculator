"""Engine-generated weights stay distinct from author input and failed writes."""
from __future__ import annotations

import copy
import json
import math
import uuid

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.calculation_attempt_failure import (
    CalculationOutputValidationFailure,
    permits_output_validation_failure,
)
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.app_fault_store import fault_scope, read_system_diagnostic
from genshin_app.equipment.files import fingerprint, input_view, read_plan, save_plan
from genshin_app.equipment.schema import (
    RULE,
    PlanError,
    _check,
    new_plan,
    validate_executed_plan,
    validate_plan,
)
from genshin_app.formula_weighting import weighting_projection
from genshin_engine.mechanics import execute_mechanics
from tests.test_calculation_delivery import delivery_plan
from tests.test_elemental_multiaura_v4 import aura
from tests.test_mechanics_execution import second_character
from tests.test_native_app_tool_host import call, host
from tests.test_special_reaction_mechanics_projection import (
    _application_fixture,
    _enable,
)


def weighted_execution():
    template, rotation, model = _application_fixture("anemo", initial=[aura("cryo", .7, "b")])
    second_character(template, rotation, "b")
    template["characters"][0]["stats"].update(em=0, crit_rate=0)
    template["characters"][1]["stats"].update(em=800, crit_rate=1, crit_dmg=1)
    _enable(model, "stellar_swirl")
    value = execute_mechanics(template, rotation, model)
    assert value["result"] is not None
    plan = new_plan()
    plan.update(plan_version=3, manual_template=template, rotation_model=rotation,
                mechanics_model=model, resolved_template=value["resolved_template"])
    return plan, value


def weighted_hit(plan):
    return next(row for row in plan["resolved_template"]["hits"] if "result_weight" in row)


def test_actual_weighted_output_passes_strict_app_contract_and_source_proof():
    plan, value = weighted_execution()
    validate_plan(plan)
    assert weighting_projection(plan["resolved_template"], value["result"])["has_derived_weights"]


@pytest.mark.parametrize("weight", [-.1, 0, 1.1, float("nan"), float("inf"), True, ".3"])
def test_invalid_generated_weight_is_rejected_at_real_field(weight):
    plan, _value = weighted_execution()
    weighted_hit(plan)["result_weight"] = weight
    with pytest.raises(PlanError) as caught:
        validate_plan(plan)
    if isinstance(weight, float) and not math.isfinite(weight):
        assert "resolved_template" in str(caught.value.details) or "有限" in str(caught.value)
    else:
        assert "result_weight" in str(caught.value.details)
        assert "需要 null" not in str(caught.value.details)


@pytest.mark.parametrize("branch", ["manual", "unknown_output"])
def test_runtime_field_does_not_open_author_or_unknown_field_inputs(branch):
    plan, _value = weighted_execution()
    if branch == "manual":
        plan["manual_template"]["hits"][0]["result_weight"] = .3
    else:
        weighted_hit(plan)["invented_outcome_multiplier"] = 9
    with pytest.raises(PlanError) as caught:
        validate_plan(plan)
    assert ("manual_template" if branch == "manual" else "invented_outcome_multiplier") in str(caught.value.details)


def test_explicit_hit_rule_is_still_author_input_without_runtime_weight():
    plan, _ = weighted_execution()
    hit = copy.deepcopy(plan["manual_template"]["hits"][0])
    hit["result_weight"] = .3
    errors = []
    _check({"kind": "explicit_hit", "hit": hit}, RULE, "rule", errors)
    assert any(row["path"] == "rule.hit.result_weight" for row in errors)


def test_weighted_output_exports_only_editable_source_and_recomputes(tmp_path):
    plan, first = weighted_execution()
    before = copy.deepcopy(plan)
    path = tmp_path / "weighted.plan.json"
    saved = save_plan(path, plan)
    loaded = read_plan(path)["plan"]
    assert loaded == saved["plan"] and loaded["resolved_template"] is None
    assert loaded["resolution"] == {"state": "pending"}
    assert input_view(loaded) == input_view(plan) and plan == before
    assert all("result_weight" not in hit for hit in loaded["manual_template"]["hits"])
    second = execute_mechanics(loaded["manual_template"], loaded["rotation_model"], loaded["mechanics_model"])
    assert second["resolved_template"] == first["resolved_template"]
    assert second["result"] == first["result"]


@pytest.mark.parametrize("value", [None, [], "not-a-plan"])
def test_native_file_writer_rejects_nonobjects_before_publishing(tmp_path, value):
    path = tmp_path / "invalid.plan.json"
    with pytest.raises(PlanError):
        save_plan(path, value)
    assert not path.exists()


@pytest.mark.parametrize("target", ["weight", "source", "group"])
def test_rehashing_or_copying_weight_metadata_cannot_replace_execution_provenance(target):
    plan, value = weighted_execution()
    if target == "weight":
        weighted_hit(plan)["result_weight"] = .123
    elif target == "source":
        weighted_hit(plan)["extensions"]["special_reaction"]["request_id"] = "different-request"
    else:
        value["result"]["special_reactions"]["damage_weighting"].clear()
    with pytest.raises(ValueError):
        weighting_projection(plan["resolved_template"], value["result"])


@pytest.mark.parametrize("value,schema,expected", [
    ({"x": 0}, {"anyOf": [{"type": "null"}, {"type": "object", "properties": {"x": {"type": "number", "minimum": 1}}}]}, "value.x"),
    ([0, 0], {"anyOf": [{"type": "null"}, {"type": "array", "items": {"type": "string"}}]}, "value[0]"),
])
def test_anyof_uses_actual_type_not_shorter_unrelated_branch(value, schema, expected):
    errors = []
    _check(value, schema, "value", errors)
    assert errors[0]["path"] == expected


def expanded_plan():
    plan = delivery_plan()
    plan["resolved_template"] = copy.deepcopy(plan["manual_template"])
    plan["resolved_template"]["extensions"] = {"execution_audit": "x" * (9 * 1024 * 1024)}
    return plan


def test_internal_execution_cache_uses_calculated_budget_without_raising_author_budget(tmp_path):
    plan = expanded_plan()
    validate_executed_plan(plan)
    with pytest.raises(PlanError, match="8 MiB"):
        validate_plan(plan)
    saved = save_plan(tmp_path / "source.plan.json", plan)["plan"]
    assert saved["manual_template"] == plan["manual_template"] and saved["resolved_template"] is None
    validate_plan(saved)


def test_expanded_plan_does_not_increase_author_or_total_calculation_limits(monkeypatch):
    from genshin_app.calculation_delivery_files import LIMITS

    plan = delivery_plan()
    plan["manual_template"]["meta"]["large_author_note"] = "x" * (9 * 1024 * 1024)
    with pytest.raises(PlanError, match="8 MiB"):
        validate_executed_plan(plan)
    plan = delivery_plan()
    plan["resolved_template"] = copy.deepcopy(plan["manual_template"])
    monkeypatch.setitem(LIMITS, "calculated_bytes", 10 * 1024 * 1024)
    plan["resolved_template"]["extensions"] = {"audit": "x" * (10 * 1024 * 1024)}
    with pytest.raises(PlanError, match="10 MiB"):
        validate_executed_plan(plan)


def test_whole_calculated_still_has_independent_artifact_limit(monkeypatch):
    from genshin_app.calculation_delivery_files import LIMITS
    from genshin_app.equipment.delivery import validate_delivery_calculated

    monkeypatch.setitem(LIMITS, "calculated_bytes", 1024 * 1024)
    value = {key: {} for key in ("plan", "result", "result_receipt", "report", "equipment_summary", "display_context")}
    value["presentation_issues"] = []
    value["result"]["audit"] = "x" * (1024 * 1024)
    with pytest.raises(PlanError) as caught:
        validate_delivery_calculated(value)
    assert caught.value.reason == "delivery_size_limit"
    assert caught.value.details["role"] == "calculated"


@pytest.mark.parametrize("mutation", ["unknown_field", "nonfinite", "deep"])
def test_large_execution_cache_keeps_strict_fields_finiteness_and_depth(mutation):
    plan = expanded_plan()
    target = plan["resolved_template"]
    if mutation == "unknown_field":
        target["invented_runtime_output"] = 1
    elif mutation == "nonfinite":
        target["extensions"]["invalid"] = float("nan")
    else:
        deep = target["extensions"]
        for _ in range(101):
            deep["next"] = {}
            deep = deep["next"]
    with pytest.raises(PlanError) as caught:
        validate_executed_plan(plan)
    assert "8 MiB" not in str(caught.value)


def failing_session(tmp_path, monkeypatch, *, mutate_author=False):
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())
    attempts = []

    def invalid_output(_value):
        attempts.append(1)
        if mutate_author:
            session.candidate["manual_template"]["characters"][0]["stats"]["atk"] += 1
        raise PlanError("生成产物字段不合法", "invalid_plan", [{"path": "plan.resolved_template.hits[0].test", "message": "未知输出字段"}])

    monkeypatch.setattr(session, "_validate_calculated", invalid_output)
    return session, attempts


def test_generated_validation_fault_is_exact_durable_diagnostic_and_replay(tmp_path, monkeypatch):
    session, attempts = failing_session(tmp_path, monkeypatch)
    before = copy.deepcopy(session.candidate)
    current = host(tmp_path / "host", session, tools.call_tool)
    raw = json.dumps({"expected_revision": session.revision})
    result = call(current, tool="gs_calculate", raw=raw)
    assert result["terminal_state"] == "settled_failure", result
    response = result["result"]
    assert response["failure_kind"] == "system" and response["reason"] == "app_internal_error"
    assert response["details"]["phase"] == "generated_result_validation"
    assert response["details"]["diagnostic"]["diagnostic"]["original_error"]["reason"] == "invalid_plan"
    assert session.candidate == before and session.calculated is None and not session.summary()["ready"]
    diagnostic = read_system_diagnostic(tmp_path, response["system_fault"]["diagnostic_id"])
    assert diagnostic["diagnostic"]["frames"]
    assert permits_output_validation_failure(response, diagnostic_cache=session._diagnostic_cache,
        require_cached_verification=True, candidate_state={"revision": session.revision, "fingerprint": fingerprint(before)})
    replay = call(current, tool="gs_calculate", raw=raw)
    assert replay["status"] == "replay" and replay["result"] == response and len(attempts) == 1
    read = session.call_tool("gs_read_context", {"resource": "diagnostic_error"})
    assert read["code"] == 0
    diagnosed = session.call_tool("gs_diagnose_result", {"expected_revision": session.revision, "request": {"view": "issues"}})
    assert diagnosed["code"] == 0, diagnosed
    assert diagnosed["data"]["validation_scope"] == "unverified_saved_output_validation_failure"
    assert session._diagnostic_cache["value"]["sealed"] is False
    cold = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert cold.candidate == before and cold.calculated is None
    assert read_system_diagnostic(tmp_path, response["system_fault"]["diagnostic_id"]) == diagnostic


def test_failed_output_validation_durably_invalidates_previous_ready_result(tmp_path, monkeypatch):
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())
    session.calculate(0)
    assert session.summary()["ready"]
    assert CandidateSession.load(session.session_id, session.root, session.db_path).summary()["ready"]

    def fail(_value):
        raise PlanError("App产物验证故障", "invalid_plan", [{"path": "output.test", "message": "错误"}])

    monkeypatch.setattr(session, "_validate_calculated", fail)
    result = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert result["reason"] == "app_internal_error" and not session.summary()["ready"]
    # No caller save: the failure producer itself has closed the old ready state.
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert restored.calculated is None and not restored.summary()["ready"]


def test_failure_diagnostic_honors_existing_writable_app_scope(tmp_path, monkeypatch):
    session, _ = failing_session(tmp_path / "readonly-db-side", monkeypatch)
    profile = tmp_path / "app-profile"
    with fault_scope(profile), pytest.raises(CalculationOutputValidationFailure) as caught:
        session.calculate(0)
    identifier = caught.value.response["system_fault"]["diagnostic_id"]
    assert read_system_diagnostic(profile, identifier)["operation"] == "gs_calculate.validate_generated_result"
    assert not (session.db_path.parent / "logs").exists()


def test_wire_failure_evidence_rejects_malformed_nested_diagnostic(tmp_path, monkeypatch):
    from genshin_app.agent_chat.native_app_tool_bridge import (
        NativeAppToolBridgeError,
        build_native_app_tool_settlement,
        validate_native_app_tool_settlement,
    )

    session, _ = failing_session(tmp_path, monkeypatch)
    current = host(tmp_path / "host", session, tools.call_tool)
    result = call(current, tool="gs_calculate", raw='{"expected_revision":0}')
    payload = build_native_app_tool_settlement(result, tool_name="gs_calculate", current_snapshot=current.snapshot)
    body = json.loads(payload["result_json"])
    body["details"]["diagnostic"]["diagnostic"] = "malformed"
    payload["result_json"] = json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    payload["result_hash"] = fingerprint(body)
    with pytest.raises(NativeAppToolBridgeError, match="calculation_failure_evidence"):
        validate_native_app_tool_settlement(payload, current_snapshot=current.snapshot)


@pytest.mark.parametrize("mutation", ["cache", "attempt", "promoted", "candidate", "wrong_operation", "schema_bool"])
def test_unaccepted_diagnostic_is_not_a_generic_failure_commit_permission(tmp_path, monkeypatch, mutation):
    session, _ = failing_session(tmp_path, monkeypatch)
    response = session.call_tool("gs_calculate", {"expected_revision": 0})
    cache = copy.deepcopy(session._diagnostic_cache)
    state = {"revision": 0, "fingerprint": fingerprint(session.candidate)}
    if mutation == "cache":
        cache = None
    elif mutation == "attempt":
        cache["attempt_id"] = "f" * 32
    elif mutation == "promoted":
        response["details"]["diagnostic"]["sealed"] = True
    elif mutation == "wrong_operation":
        response["system_fault"]["operation"] = "unrelated_operation"
    elif mutation == "schema_bool":
        response["details"]["diagnostic"]["schema_version"] = True
    else:
        state["fingerprint"] = "f" * 64
    assert not permits_output_validation_failure(response, diagnostic_cache=cache,
        require_cached_verification=True, candidate_state=state)


def test_validator_changing_author_input_still_has_unknown_outcome(tmp_path, monkeypatch):
    session, _ = failing_session(tmp_path, monkeypatch, mutate_author=True)
    current = host(tmp_path / "host", session, tools.call_tool)
    response = call(current, tool="gs_calculate", raw='{"expected_revision":0}')
    assert response["terminal_state"] == "unknown_after_interruption"
    assert response["terminal_reason"] == "effect_mask_violation"


@pytest.mark.parametrize("validation_failure", [False, True])
def test_uncertain_publication_failure_is_not_mislabeled_validation_diagnostic(tmp_path, monkeypatch, validation_failure):
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())
    if validation_failure:
        monkeypatch.setattr(session, "_validate_calculated", lambda _: (_ for _ in ()).throw(PlanError("输出合同故障")))
    monkeypatch.setattr(session, "save", lambda: (_ for _ in ()).throw(OSError("publication outcome unknown")))
    current = host(tmp_path / "host", session, tools.call_tool)
    response = call(current, tool="gs_calculate", raw='{"expected_revision":0}')
    assert response["terminal_state"] == "unknown_after_interruption"
    assert response["terminal_reason"] == "failed_tool_changed_state"

