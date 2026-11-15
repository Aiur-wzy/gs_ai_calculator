"""Operation evidence must improve recovery without becoming authority."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from genshin_app.agent_chat import candidate as candidate_module
from genshin_app.agent_chat import operation_attempts as attempts
from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.feedback_context import feedback_context
from genshin_app.agent_chat.native_prompt_v2 import build_native_prompt_v2
from genshin_app.agent_chat.tools import _current_diagnostic_error
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from tests.test_agent_calculation_preflight import pending_plan
from tests.test_calculation_delivery import delivery_plan
from tests.test_calculation_diagnostics import failed


def session(tmp_path, plan=None, cls=ArtifactCandidateSession):
    return cls(str(uuid.uuid4()), tmp_path / "candidate", tmp_path / "absent.db", "native",
               plan if plan is not None else pending_plan())


def calculate(current):
    return current.call_tool("gs_calculate", {"expected_revision": current.revision})


@pytest.mark.parametrize("where", ["start_attempt", "finish_attempt"])
@pytest.mark.parametrize("ready", [False, True])
def test_diagnostic_program_fault_never_changes_domain_result(tmp_path, monkeypatch, where, ready):
    current = session(tmp_path, delivery_plan() if ready else pending_plan())

    def broken(*args, **kwargs):
        raise RuntimeError("diagnostic-only injected fault")

    monkeypatch.setattr(attempts, where, broken)
    response = calculate(current)
    assert response["code"] == (0 if ready else 1)
    if ready:
        assert current.summary()["calculation_acceptance"]["status"] == "accepted"
    else:
        assert response["reason"] == "missing_input" and current.calculated is None
    assert current.summary()["latest_attempt"]["diagnostic_storage_warning"]["cause"] == "RuntimeError"


def test_native_default_assistance_is_compact_but_full_resource_remains(tmp_path):
    current = session(tmp_path, delivery_plan())
    assert calculate(current)["code"] == 0
    current.report["default_assistance"] = {"energy": {"burst_checks": [{"id": str(i)} for i in range(20)]}}
    short = current.summary()["default_assistance"]
    assert len(short["energy"]["burst_checks"]) == 8
    assert short["presentation"]["omissions"][0]["total"] == 20
    full = current.call_tool("gs_read_context", {"resource": "default_assistance", "pointer": "/energy/burst_checks"})
    assert full["code"] == 0 and full["data"]["total"] == 20


def test_preflight_failure_is_durable_without_changing_business_checkpoint(tmp_path):
    current = session(tmp_path, pending_plan(20))
    checkpoint = (current.root / "checkpoint.json").read_bytes()
    response = calculate(current)
    attempt = response["latest_attempt"]
    assert (attempt["phase"], attempt["engine_execution"], attempt["user_action"]) == ("input_preflight", "not_run", None)
    assert attempt["failure_summary"]["reported_issue_count"] == 20
    assert (current.root / "checkpoint.json").read_bytes() == checkpoint
    restored = CandidateSession.load(current.session_id, current.root, current.db_path)
    latest = restored.summary()["latest_attempt"]
    assert latest["attempt_id"] == attempt["attempt_id"] and latest["matches_current_input"]
    detail = restored.call_tool("gs_read_context", {"resource": "calculation_attempt", "pointer": "/evidence/details/missing_inputs", "limit": 200})
    assert detail["code"] == 0 and detail["data"]["total"] == 20
    assert restored.stage_context()["current_problem"]["user_action"] is None
    assert restored.calculated is None


def test_accepted_result_and_later_preflight_failure_are_independent(tmp_path, monkeypatch):
    current = session(tmp_path, delivery_plan())
    ok = calculate(current)
    assert ok["code"] == 0, ok
    receipt = current.summary()["receipt_fingerprint"]
    rejected = candidate_module.compose_equipment_plan(pending_plan(), current.db_path)
    monkeypatch.setattr(candidate_module, "compose_equipment_plan", lambda *a, **k: copy.deepcopy(rejected))
    bad = calculate(current)
    summary = current.summary()
    assert bad["reason"] == "missing_input"
    assert summary["latest_attempt"]["state"] == "failed"
    assert summary["saved_calculation"]["available"] is True
    assert summary["receipt_fingerprint"] == receipt
    assert summary["calculation_acceptance"]["status"] == "accepted"
    assert summary["execution_feedback"]["execution"] == "complete"
    assert current.stage_context()["current_problem"]["resource"] == "calculation_attempt"


def test_changed_input_or_policy_never_relabels_old_failure_as_current(tmp_path):
    current = session(tmp_path)
    calculate(current)
    original = copy.deepcopy(current.candidate)
    current.candidate["manual_template"]["characters"][0]["stats"]["atk"] += 1
    assert attempts.attempt_projection(current)["matches_current_input"] is False
    assert "current_problem" not in current.stage_context()
    current.candidate = original
    current.stage_state["energy_intent"] = "multi_round_legal"
    assert attempts.attempt_projection(current)["matches_current_input"] is False
    with pytest.raises(PlanError, match="策略已变化"):
        _current_diagnostic_error(current)


def test_database_change_invalidates_diagnostic_currency(tmp_path, monkeypatch):
    current = session(tmp_path)
    calculate(current)
    monkeypatch.setattr(current, "_db_snapshot", lambda: {"state": "different"})
    assert attempts.attempt_projection(current)["matches_current_input"] is False


@pytest.mark.parametrize('change', ['input', 'policy', 'database'])
@pytest.mark.parametrize('view', ['assistance', 'energy_trials'])
def test_auxiliary_views_use_current_input_without_stale_execution(tmp_path, monkeypatch, change, view):
    current = session(tmp_path, delivery_plan())
    def reject(*args, **kwargs):
        value = failed()
        raise PlanError('executed failure', value['reason'], value['details'])
    monkeypatch.setattr(candidate_module, 'run_plan_calc', reject)
    assert calculate(current)['reason'] == 'mechanics_pending'
    if change == 'input':
        current.candidate['manual_template']['characters'][0]['stats']['atk'] += 1
    elif change == 'policy':
        current.stage_state['energy_intent'] = 'multi_round_legal'
    else:
        monkeypatch.setattr(current, '_db_snapshot', lambda: {'state': 'different'})
    assert current.call_tool('gs_diagnose_result', {'expected_revision': 0})['reason'] == 'agent_diagnostic_stale'
    seen = []
    def inspect(value, request, **options):
        seen.append(copy.deepcopy(value))
        return {'authority': 'advisory_only'}
    monkeypatch.setattr('genshin_app.calculation_assistance.diagnose_assistance', inspect)
    request = {'view': view, **({'trials': [{'id': 'unchanged', 'changes': []}]} if view == 'energy_trials' else {})}
    response = current.call_tool('gs_diagnose_result', {'expected_revision': 0, 'request': request})
    assert response['code'] == 0, response
    assert seen == [{'plan': current.candidate}]
    assert response['data']['evidence_selection']['source'] == 'current_input_only'
    assert response['data']['evidence_selection']['execution_evidence'] == 'historical_input_or_policy'
    assert current.calculated is None and current._diagnostic_cache is not None


def test_new_generic_failure_never_consumes_old_execution_in_assistance(tmp_path, monkeypatch):
    current = session(tmp_path, delivery_plan())
    def reject(*args, **kwargs):
        value = failed()
        raise PlanError('old execution failure', value['reason'], value['details'])
    monkeypatch.setattr(candidate_module, 'run_plan_calc', reject)
    calculate(current)
    def other(*args, **kwargs):
        raise PlanError('new non-execution error', 'different_failure', copy.deepcopy(failed()['details']))
    monkeypatch.setattr(candidate_module, 'run_plan_calc', other)
    assert calculate(current)['reason'] == 'different_failure'
    response = current.call_tool('gs_diagnose_result', {'expected_revision': 0, 'request': {'view': 'assistance'}})
    assert response['code'] == 0, response
    assert response['data']['evidence_selection']['execution_evidence'] == 'operation_failure_only'
    assert current.calculated is None


def test_successful_compose_does_not_relabel_previous_failure_with_new_attempt_id(tmp_path, monkeypatch):
    current = session(tmp_path, delivery_plan())
    def reject(*args, **kwargs):
        value = failed()
        raise PlanError('old failure', value['reason'], value['details'])
    monkeypatch.setattr(candidate_module, 'run_plan_calc', reject)
    reference = calculate(current)['latest_attempt']['details_ref']
    assert current.call_tool('gs_compose', {'expected_revision': 0})['code'] == 0
    assert current.call_tool('gs_diagnose_result', {'expected_revision': 0})['reason'] == 'agent_diagnostic_missing'
    saved = current.call_tool('gs_read_context', {'resource': reference['resource'], 'pointer': '/evidence/reason'})
    assert saved['code'] == 0 and saved['data']['text'] == 'mechanics_pending'


def test_repeated_problem_reports_unchanged_input_without_retry_gate(tmp_path):
    current = session(tmp_path)
    first = calculate(current)
    second = calculate(current)
    repeated = second["latest_attempt"]["repetition"]
    assert repeated["previous_attempt_id"] == first["latest_attempt"]["attempt_id"]
    assert repeated["same_input"] and repeated["same_request"] and repeated["same_primary_problem"]
    assert repeated["changed_input_sections"] == []
    assert repeated["retry_limit"] is None
    assert calculate(current)["reason"] == "missing_input"


def test_failure_reference_survives_compose_retry_and_cold_restore(tmp_path):
    current = session(tmp_path)
    failed_attempt = calculate(current)["latest_attempt"]
    reference = failed_attempt["details_ref"]
    assert current.call_tool("gs_compose", {"expected_revision": 0})["code"] == 0
    restored = CandidateSession.load(current.session_id, current.root, current.db_path)
    assert restored.summary()["latest_attempt"]["state"] == "succeeded"
    history = restored.call_tool("gs_read_context", {"resource": reference["resource"], "pointer": "/state"})
    assert history["code"] == 0 and history["data"]["text"] == "failed"
    evidence = restored.call_tool("gs_read_context", {"resource": reference["resource"], "pointer": reference["pointer"]})
    assert evidence["code"] == 0
    other = session(tmp_path / "another")
    assert other.call_tool("gs_read_context", {"resource": reference["resource"]})["reason"] == "agent_attempt_reference_missing"


def test_tampered_diagnostic_is_reported_but_does_not_invalidate_candidate(tmp_path):
    current = session(tmp_path)
    calculate(current)
    index = json.loads((current.root / attempts.INDEX).read_text(encoding="utf-8"))
    path = current.root / index["file"]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["state"] = "succeeded"
    path.write_text(json.dumps(value), encoding="utf-8")
    # The small authenticated header avoids rereading large diagnostics on
    # every model step; full evidence is checked against its digest on access.
    assert current.summary()["latest_attempt"]["state"] == "failed"
    assert current.summary()["saveable"] is True
    assert current.summary()["calculation_acceptance"]["status"] != "accepted"
    response = current.call_tool("gs_read_context", {"resource": "calculation_attempt"})
    assert response["reason"] == "agent_attempt_reference_invalid"


def test_tampered_small_header_is_never_projected(tmp_path):
    current = session(tmp_path)
    calculate(current)
    path = current.root / attempts.INDEX
    index = json.loads(path.read_text(encoding="utf-8"))
    index["summary"]["state"] = "succeeded"
    path.write_text(json.dumps(index), encoding="utf-8")
    assert current.summary()["latest_attempt"]["state"] == "diagnostic_unavailable"


@pytest.mark.parametrize("valid", [True, False])
def test_diagnostic_storage_failure_preserves_real_operation_outcome(tmp_path, monkeypatch, valid):
    current = session(tmp_path, delivery_plan() if valid else pending_plan())
    def reject(*a, **k):
        raise OSError("diagnostic disk unavailable")
    monkeypatch.setattr(attempts, "write_owned", reject)
    result = calculate(current)
    assert result["code"] == (0 if valid else 1)
    assert current._attempt_storage_failure["reason"] == "operation_diagnostic_not_saved"
    assert result.get("data", current.summary())["latest_attempt"]["state"] == "diagnostic_unavailable"
    assert (current.calculated is not None) is valid


def test_mechanics_failure_can_be_diagnosed_after_cold_restore(tmp_path, monkeypatch):
    current = session(tmp_path, delivery_plan(), cls=CandidateSession)
    def reject(*a, **k):
        value = failed()
        raise PlanError("mechanics diagnostic", value["reason"], value["details"])
    monkeypatch.setattr(candidate_module, "run_plan_calc", reject)
    assert calculate(current)["reason"] == "mechanics_pending"
    restored = CandidateSession.load(current.session_id, current.root, current.db_path)
    assert getattr(restored, "_diagnostic_cache", None) is None
    assert fingerprint(_current_diagnostic_error(restored)) == fingerprint(_current_diagnostic_error(current))


def test_context_remains_small_integer_safe_and_full_facts_remain_readable(tmp_path):
    current = session(tmp_path)
    calculate(current)
    summary = current.summary()
    summary["latest_attempt"]["failure_summary"]["primary_reported_issue"]["actual"] = 28.8
    summary["default_assistance"] = {"energy": {"planned_burst_count": 4, "burst_rows": [{"before": 28.8}]},
                                     "panels": [{"atk": 1876.25}]}
    context = feedback_context(summary)
    assert context["assistance"]["energy"] == {"planned_burst_count": 4}
    assert context["latest_attempt"]["problem"]["actual"] == "28.8"
    assert summary["latest_attempt"]["failure_summary"]["primary_reported_issue"]["actual"] == 28.8
    build_native_prompt_v2(workflow="artifact", runtime_context={"workflow": "artifact", "candidate": context})


def test_exact_mismatch_survives_preflight_compaction(tmp_path, monkeypatch):
    current = session(tmp_path)
    mismatch = {"code": "source_changed", "path": "resource.a", "message": "绑定不一致",
        "details": {"differences": [{"field": "expected_scope_fingerprint", "actual": "a" * 64,
            "expected": "b" * 64, "input_ref": "/action_resource_sources/0/expected_scope_fingerprint",
            "expected_ref": "/action_resource_compilations/0/required_confirmation/expected_scope_fingerprint"}]}}
    def reject(*a, **k):
        raise PlanError("待补输入", "missing_input", {"state": "pending", "missing_inputs": [mismatch]})
    monkeypatch.setattr(current, "calculate", reject)
    result = calculate(current)
    assert result["failure_summary"]["primary_reported_issue"]["differences"] == mismatch["details"]["differences"]
    assert result["latest_attempt"]["failure_summary"] == result["failure_summary"]
