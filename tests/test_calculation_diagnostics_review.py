"""Independent saved-execution adapter checks; no execution promotion."""
import copy

import pytest
from test_calculation_diagnostics import failed
from test_calculation_review import _saved

from genshin_app.calculation_diagnostics import inspect_calculation_diagnostics
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError


def test_receipted_saved_result_retains_exact_raw_pointers_and_status():
    data = _saved()
    data["result"].update(mechanics_status="unproven", mechanics_trace=[],
        mechanics_issues=[{"code": "unknown_condition", "path": "raw", "event_id": "h0"}])
    data["result_receipt"]["result_fingerprint"] = fingerprint(data["result"])
    before = copy.deepcopy(data)
    page = inspect_calculation_diagnostics(data, {"view": "issues"})
    assert page["validation_scope"] == "saved_result_fingerprint"
    assert page["execution_status"] == "unproven"
    assert page["items"][0]["json_pointer"] == "/result/mechanics_issues/0"
    assert data == before and "summary" not in page


def test_pending_error_with_reserved_calculation_container_cannot_bypass_validation():
    data = failed()
    data["result_receipt"] = {"result_fingerprint": "fake"}
    with pytest.raises(PlanError):
        inspect_calculation_diagnostics(data)


def test_pending_error_with_full_package_marker_cannot_bypass_validation():
    data = failed()
    data["calculated"] = {"result": {}, "result_receipt": {"result_fingerprint": "fake"}}
    with pytest.raises(PlanError):
        inspect_calculation_diagnostics(data)


@pytest.mark.parametrize("side", ["input", "query"])
def test_nonfinite_values_are_structured_adapter_errors(side):
    data, request = failed(), {}
    if side == "input":
        data["details"]["mechanics_trace"] = [{"t": float("nan")}]
    else:
        request["limit"] = float("nan")
    with pytest.raises(PlanError):
        inspect_calculation_diagnostics(data, request)


def test_filtered_pagination_preserves_original_indices_and_requires_same_selection():
    data = failed()
    for index, row in enumerate(data["details"]["issues"]):
        row["rule_id"] = "one" if index % 2 else "two"
    request = {"view": "issues", "limit": 1, "filters": {"rule_id": "one"}}
    indices = []
    page = inspect_calculation_diagnostics(data, request)
    with pytest.raises(PlanError):
        inspect_calculation_diagnostics(data, {**request, "filters": {"rule_id": "two"}, "cursor": page["next_cursor"]})
    while True:
        indices.extend(row["index"] for row in page["items"])
        if page["next_cursor"] is None:
            break
        page = inspect_calculation_diagnostics(data, {**request, "cursor": page["next_cursor"]})
    assert indices == [1, 3, 5]


def test_trace_rule_id_is_not_inferred_into_issue_filters():
    data = failed()
    data["details"]["mechanics_trace"] = [{"event_id": "h0", "rule_id": "invented-link"}]
    page = inspect_calculation_diagnostics(data, {"filters": {"rule_id": "invented-link"}})
    assert page["matched_issue_count"] == 0 and page["items"] == []
    page = inspect_calculation_diagnostics(data)
    assert page["items"][0]["unknown_phase_count"] == 7


def test_cursor_binds_result_none_and_all_other_saved_fields():
    data = failed()
    page = inspect_calculation_diagnostics(data, {"view": "issues", "limit": 1})
    data["details"]["result"] = {"untrusted": "changed"}
    with pytest.raises(PlanError) as error:
        inspect_calculation_diagnostics(data, {"view": "issues", "cursor": page["next_cursor"]})
    assert error.value.reason == "stale_diagnostic_cursor"
