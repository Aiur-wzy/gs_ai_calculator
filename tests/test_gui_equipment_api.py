"""Native GUI bridge: shared authority, durable pending plans, and discovery errors."""
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from genshin_app.gui.api import GuiApi


def manual_plan(api):
    template = api.new_template()["data"]["template"]
    plan = api.new_plan(template)["data"]
    for hit in plan["manual_template"]["hits"]:
        hit["damage_type_ids"] = ["damage.normal_attack"]
    return plan


def test_gui_plan_save_reopen_and_compare_and_swap(tmp_path):
    api = GuiApi(tmp_path)
    plan = manual_plan(api)
    path = str(tmp_path / "native.plan.json")
    saved = api.save_plan(path, plan)
    assert saved["code"] == 0
    assert api.inspect_plan_file(path)["data"]["kind"] == "genshin-plan"
    reopened = api.load_plan(path)
    assert reopened["code"] == 0
    assert reopened["data"]["plan"]["plan_id"] == plan["plan_id"]
    assert api.save_plan(path, plan)["reason"] == "file_conflict"
    assert api.save_plan(path, plan, saved["data"]["file_sha"])["code"] == 0
    assert api.list_plan_details()["code"] == 0


def test_gui_calc_and_export_revalidate_native_plan(tmp_path):
    api = GuiApi(tmp_path)
    plan = manual_plan(api)
    result = api.run_plan_calc(plan)
    assert result["code"] == 0
    assert result["data"]["result_receipt"]["plan_id"] == plan["plan_id"]
    export = api.export_plan_result(plan, "json", "native")
    assert export["code"] == 0
    payload = json.loads(Path(export["data"]["path"]).read_text(encoding="utf-8"))
    assert payload["result_receipt"]["input_fingerprint"]
    plan["manual_template"]["hits"][0]["damage_type_ids"] = None
    plan["resolution"] = {"state": "ready"}
    for response in (api.run_plan_calc(plan), api.export_plan_result(plan, "json"),
                     api.export_resolved_plan(plan, str(tmp_path / "invalid.resolved.json"))):
        assert response["code"] == 1
        assert response["reason"] == "missing_input"
    assert not (tmp_path / "invalid.resolved.json").exists()


def test_gui_calc_and_all_native_exports_forward_current_candidate_session(
    tmp_path,
    monkeypatch,
):
    from genshin_app import equipment, formula_workbook
    from genshin_app.equipment import service

    candidate_session = object()
    boundary_calls = []

    class Manager:
        @contextmanager
        def candidate_session_boundary(self, session_id, plan):
            boundary_calls.append((session_id, plan["plan_id"]))
            yield candidate_session

    api = GuiApi(tmp_path, agent_manager_factory=lambda _root: Manager())
    plan = manual_plan(api)
    calc_calls = []
    calc_results = []
    completion_calls = []
    export_calls = []
    workbook_calls = []

    def fake_calc(plan_value, db_path, *, candidate_session=None, **kwargs):
        calc_calls.append((plan_value["plan_id"], db_path, candidate_session, kwargs))
        calculated = {
            "plan": plan_value,
            "result": {},
            "result_receipt": {"plan_id": plan_value["plan_id"]},
            "report": {},
            "equipment_summary": {},
            "presentation_issues": [],
        }
        calc_results.append(calculated)
        return calculated

    def completion_port(calculated):
        # Scope is argument forwarding only. Spy the completion branch instead
        # of fabricating a numerical result or reusable calculation proof.
        assert any(calculated is value for value in calc_results)
        completion_calls.append(("calc", calculated))
        return {"task_admission": "accepted"}

    def require_completion_port(calculated):
        assert any(calculated is value for value in calc_results)
        completion_calls.append(("require", calculated))

    def fake_export(plan_value, db_path, fmt, path, *, candidate_session=None):
        export_calls.append((fmt, plan_value["plan_id"], db_path, candidate_session))
        return {"path": str(path), "result_receipt": {"plan_id": plan_value["plan_id"]}}

    def fake_workbook(calculated, *, candidate_session=None, **_kwargs):
        workbook_calls.append((calculated["plan"]["plan_id"], candidate_session))
        return b"workbook"

    monkeypatch.setattr(equipment, "run_plan_calc", fake_calc)
    monkeypatch.setattr(api._equipment_port.viewed_reports, "retain", lambda *_args, **_kwargs: "trusted-report-handle")
    monkeypatch.setattr(equipment, "export_plan_result", fake_export)
    monkeypatch.setattr(service, "run_plan_calc", fake_calc)
    monkeypatch.setattr(formula_workbook, "build_formula_workbook", fake_workbook)
    monkeypatch.setattr(formula_workbook, "formula_workbook_support", lambda _value: {})
    monkeypatch.setattr("genshin_app.calculation_completion.calculation_completion", completion_port)
    monkeypatch.setattr(
        "genshin_app.calculation_completion.require_calculation_complete",
        require_completion_port,
    )
    monkeypatch.setattr(
        "genshin_app.equipment.files.publish_files",
        lambda outputs: {"published_paths": [str(outputs[0][0])]},
    )

    session_id = "native-" + "1" * 32
    assert api.run_plan_calc(plan, session_id)["code"] == 0
    for fmt in ("json", "csv", "xlsx"):
        assert api.export_plan_result(plan, fmt, "session-aware", session_id)["code"] == 0
    assert api.export_formula_workbook(plan, "session-aware", session_id)["code"] == 0

    assert len(boundary_calls) == 5
    assert all(row == (session_id, plan["plan_id"]) for row in boundary_calls)
    assert all(row[2] is candidate_session for row in calc_calls)
    assert all(row[3] is candidate_session for row in export_calls)
    assert workbook_calls == [(plan["plan_id"], candidate_session)]
    assert len(calc_results) == 2
    assert completion_calls == [("calc", calc_results[0]), ("require", calc_results[1])]


def test_viewed_report_exports_exact_calculation_without_engine_replay(tmp_path, monkeypatch):
    api = GuiApi(tmp_path)
    plan = manual_plan(api)
    result = api.run_plan_calc(plan)
    assert result["code"] == 0, result
    handle = result["data"]["report_handle"]

    def no_replay(*_args, **_kwargs):
        pytest.fail("Export of a report already verified by the Host ran the engine again")

    monkeypatch.setattr("genshin_engine.mechanics.execute_mechanics", no_replay)
    monkeypatch.setattr("genshin_app.equipment.run_plan_calc", no_replay)
    for fmt in ("json", "csv", "xlsx"):
        target = tmp_path / f"已计算.{fmt}"
        response = api.export_viewed_report(handle, fmt, str(target))
        assert response["code"] == 0, response
        assert target.stat().st_size > 0
        assert response["data"]["result_fingerprint"] == result["data"]["result_receipt"]["result_fingerprint"]
    assert api.export_viewed_report("expired", "json", str(tmp_path / "refused.json"))["reason"] == "report_delivery_unavailable"
    assert not (tmp_path / "refused.json").exists()
    changed = tmp_path / "changed.json"
    changed.write_bytes(b"recent write")
    assert api.export_viewed_report(handle, "json", str(changed), "a" * 64)["reason"] == "file_conflict"
    assert changed.read_bytes() == b"recent write"


def test_saved_calculation_opens_as_a_read_only_summary_and_paged_result(tmp_path, monkeypatch):
    from genshin_app.equipment.calculation_wire import PACKAGE_KIND
    from genshin_app.equipment.files import file_sha
    from genshin_app.equipment.schema import PlanError

    api = GuiApi(tmp_path)
    result = api.run_plan_calc(manual_plan(api))
    assert result["code"] == 0, result
    calculated = result["data"]
    source = tmp_path / "stored.calculated.json"
    written = api.export_viewed_report(calculated["report_handle"], "json", str(source))
    assert written["code"] == 0, written
    assert api.inspect_plan_file(str(source))["data"]["kind"] == PACKAGE_KIND
    def no_duplicate_export_verification(*_args, **_kwargs):
        pytest.fail("A fully verified package must not repeat the standalone export validator")

    monkeypatch.setattr("genshin_app.gui.ports.report_delivery._validate_result_export", no_duplicate_export_verification)
    opened = api.open_saved_calculation(str(source), file_sha(source))
    assert opened["code"] == 0, opened
    report = opened["data"]
    assert report["read_only"] and report["report_projection"] == "summary"
    assert report["result_receipt"]["result_fingerprint"] == calculated["result_receipt"]["result_fingerprint"]
    assert "hits" not in report["result"] and "resolved_template" not in report["plan"]
    handle = report["report_handle"]
    assert api.read_viewed_report(handle, "hits")["data"] == {
        "items": calculated["result"]["hits"], "total": 1, "next_offset": None,
    }
    assert api.read_viewed_report(handle, "hits", 2)["reason"] == "report_page_invalid"
    assert api.read_viewed_report(handle, "other")["reason"] == "report_section_invalid"
    exported = api.export_viewed_report(handle, "xlsx", str(tmp_path / "stored.xlsx"))
    assert exported["code"] == 0, exported
    assert exported["data"]["result_fingerprint"] == report["result_receipt"]["result_fingerprint"]
    incompatible = tmp_path / "incompatible.calculated.json"
    incompatible.write_text(json.dumps({"kind": PACKAGE_KIND, "package_schema": "old"}), encoding="utf-8")
    failure = api.open_saved_calculation(str(incompatible), file_sha(incompatible))
    assert failure["reason"] == "saved_calculation_version_incompatible"
    assert failure["details"]["migration_prompt"]

    def reject_override(*_args, **_kwargs):
        raise PlanError("计算覆盖回执未核验", "invalid_override_receipt")

    monkeypatch.setattr("genshin_app.equipment.calculation_overrides.validate_execution_result_receipt", reject_override)
    assert api.open_saved_calculation(str(source), file_sha(source))["reason"] == "invalid_override_receipt"


@pytest.mark.parametrize("operation", ["calc", "formula", "json", "csv", "xlsx"])
def test_gui_incomplete_calculation_cannot_reach_any_serializer(
        tmp_path, monkeypatch, operation):
    from genshin_app import equipment, formula_workbook
    from genshin_app.equipment import service

    api = GuiApi(tmp_path)
    plan = manual_plan(api)
    incomplete = {
        "plan": plan, "result": {}, "result_receipt": {"plan_id": plan["plan_id"]},
        "report": {}, "equipment_summary": {}, "presentation_issues": [],
    }
    monkeypatch.setattr(equipment, "run_plan_calc", lambda *_a, **_k: incomplete)
    monkeypatch.setattr(service, "run_plan_calc", lambda *_a, **_k: incomplete)
    monkeypatch.setattr(service, "result_bytes", lambda *_a, **_k:
                        pytest.fail("incomplete result reached serializer"))
    monkeypatch.setattr(formula_workbook, "build_formula_workbook", lambda *_a, **_k:
                        pytest.fail("incomplete result reached formula serializer"))
    monkeypatch.setattr("genshin_app.equipment.files.publish_files", lambda *_a, **_k:
                        pytest.fail("incomplete result reached publication"))
    if operation == "calc":
        response = api.run_plan_calc(plan)
        assert response["details"]["diagnostic"] is incomplete
    elif operation == "formula":
        response = api.export_formula_workbook(plan, "incomplete")
    else:
        response = api.export_plan_result(plan, operation, "incomplete")
    assert response["code"] == 1
    assert response["reason"] == "calculation_incomplete"
    assert "numeric_result_unavailable" in response["details"]["completion"]["reasons"]
    assert not list((tmp_path / "exports").glob("*"))


@pytest.mark.parametrize("method,args", [
    ("load_plan", (None,)), ("save_plan", (None, {})),
    ("export_resolved_plan", ({}, None)), ("compose_equipment_plan", ({},)),
])
def test_invalid_native_bridge_inputs_return_error_envelope(tmp_path, method, args):
    assert getattr(GuiApi(tmp_path), method)(*args)["code"] == 1


@pytest.mark.parametrize("state", ["missing", "invalid", "incompatible"])
def test_equipment_state_does_not_masquerade_as_a_project_update(
    tmp_path,
    monkeypatch,
    state,
):
    (tmp_path / "genshin.db").touch()
    monkeypatch.setattr(
        "genshin_app.gui.ports.database.queries.get_meta",
        lambda _: {
            "upstream_commit": "a" * 40,
            "datasets": {"equipment": {"lines": {"release": {"state": state}}}},
        },
    )
    response = GuiApi(tmp_path).check_database_update()
    assert response["code"] == 0
    assert response["data"]["available"] is False
    assert response["data"]["disabled"] is True
    assert "equipment_state" not in response["data"]


def test_equipment_query_preserves_structured_failure_and_arguments(tmp_path, monkeypatch):
    from genshin_db.equipment import EquipmentError
    calls = []
    def query(path, weapon_id, **kwargs):
        calls.append((path, weapon_id, kwargs))
        raise EquipmentError("missing", "dataset_unavailable", 409)
    monkeypatch.setattr("genshin_app.gui.ports.database.queries.get_weapon_detail", query)
    api = GuiApi(tmp_path)
    assert api.weapon_detail(11509, "beta")["reason"] == "database_not_installed"
    assert calls == []
    database = tmp_path / "genshin.db"
    database.touch()
    response = api.weapon_detail(11509, "beta")
    assert calls == [(database, 11509, {"version_line": "beta"})]
    assert response["code"] == 1
    assert response["reason"] == "dataset_unavailable"


def test_editor_initialization_is_tracked_for_conservative_close(tmp_path):
    api = GuiApi(tmp_path)
    assert not api._editor_initialized
    assert api.editor_initialized()["code"] == 0
    assert api._editor_initialized
