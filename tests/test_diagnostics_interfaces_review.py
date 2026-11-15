"""Independent durable-error identity and evidence retention checks."""
import copy

from tests.test_agent_candidate import native_session as _native_session
from tests.test_calculation_diagnostics import failed
from tests.test_diagnostics_interfaces import fail_engine

native_session = _native_session


def test_repeated_identical_failed_calculation_invalidates_previous_attempt_cursor(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    monkeypatch.setattr(candidate, "run_plan_calc", fail_engine)
    s = native_session
    assert s.call_tool("gs_calculate", {"expected_revision": 0})["code"] == 1
    page = s.call_tool("gs_diagnose_result", {"expected_revision": 0, "request": {"view": "issues", "limit": 2}})["data"]
    assert s.call_tool("gs_calculate", {"expected_revision": 0})["code"] == 1
    again = s.call_tool("gs_diagnose_result", {"expected_revision": 0, "request": {"view": "issues", "cursor": page["next_cursor"]}})
    assert again["code"] == 1 and again["reason"] == "stale_diagnostic_cursor"


def test_raw_error_remains_readable_when_aggregation_input_budget_is_exceeded(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    from genshin_engine import diagnostics
    monkeypatch.setattr(candidate, "run_plan_calc", fail_engine)
    monkeypatch.setattr(diagnostics, "MAX_INPUT_BYTES", 100)
    s = native_session
    assert s.call_tool("gs_calculate", {"expected_revision": 0})["code"] == 1
    raw = s.call_tool("gs_read_context", {"resource": "diagnostic_error", "pointer": "/details/issues", "limit": 1})
    assert raw["code"] == 0 and raw["data"]["total"] == 7
    assert s.call_tool("gs_diagnose_result", {"expected_revision": 0})["reason"] == "diagnostic_input_limit"
    assert s.calculated is None


def test_raw_error_read_response_cannot_mutate_cached_original(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    monkeypatch.setattr(candidate, "run_plan_calc", fail_engine)
    s = native_session
    s.call_tool("gs_calculate", {"expected_revision": 0})
    raw = s.call_tool("gs_read_context", {"resource": "diagnostic_error", "pointer": "/details/issues", "limit": 1})
    raw["data"]["items"][0]["value"]["code"] = "tampered"
    again = s.call_tool("gs_read_context", {"resource": "diagnostic_error", "pointer": "/details/issues", "limit": 1})
    assert again["data"]["items"][0]["value"]["code"] == "unknown_condition"


def test_candidate_fingerprint_change_without_revision_also_invalidates_cache(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    monkeypatch.setattr(candidate, "run_plan_calc", fail_engine)
    s = native_session
    s.call_tool("gs_calculate", {"expected_revision": 0})
    s.candidate["manual_template"]["characters"][0]["stats"]["atk"] += 1
    page = s.call_tool("gs_diagnose_result", {"expected_revision": 0})
    assert page["code"] == 1 and page["reason"] == "agent_diagnostic_stale"
    assert s._diagnostic_cache is not None  # Retained history is not current input evidence.
    assert s.call_tool('gs_read_context', {'resource': 'diagnostic_error'})['reason'] == 'agent_diagnostic_stale'


def test_new_nonmechanical_error_does_not_reuse_previous_mechanical_cache(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    from genshin_app.equipment.schema import PlanError
    monkeypatch.setattr(candidate, "run_plan_calc", fail_engine)
    s = native_session
    s.call_tool("gs_calculate", {"expected_revision": 0})
    def other_error(*args, **kwargs):
        raise PlanError("new failure", "different_failure", copy.deepcopy(failed()["details"]))
    monkeypatch.setattr(candidate, "run_plan_calc", other_error)
    assert s.call_tool("gs_calculate", {"expected_revision": 0})["reason"] == "different_failure"
    diagnosis = s.call_tool("gs_diagnose_result", {"expected_revision": 0})
    assert diagnosis["reason"] == "agent_diagnostic_view_unavailable"
    assert diagnosis["details"]["original_reason"] == "different_failure"
    assert diagnosis["recovery"]["arguments"] == {"resource": "diagnostic_error", "pointer": ""}
    raw = s.call_tool('gs_read_context', {'resource': 'diagnostic_error', 'pointer': '/reason'})
    assert raw['code'] == 0 and raw['data']['text'] == 'different_failure'
    assert s.calculated is None
