"""Whole existing receipts/fault matrices remain exact with observers enabled."""
from __future__ import annotations

import copy
import json

import pytest
from test_mechanics_execution import fixture as mechanics_inputs

from genshin_app.equipment import composer
from genshin_app.equipment.files import fingerprint
from genshin_app.whiteboard import (
    load_whiteboard,
    prepare_whiteboard_edit,
    save_whiteboard,
    validate_whiteboard_revision_transition,
)
from genshin_engine import mechanics
from genshin_runtime.diagnostics import capture_runtime_diagnostics
from tests.compose_report_oracle import assert_pre_extraction_envelope
from tests.test_candidate_build_stages import STAGES, _build
from tests.test_database_release_migration import (
    coordinator as coordinator,  # noqa: PLC0414
)
from tests.test_equipment_determinism_regression import _plan
from tests.test_equipment_plan import database as database  # noqa: PLC0414
from tests.test_release_migration_stages import (
    test_pass_coordinator_matches_frozen_state_and_fault_order as frozen_install_comparison,
)
from tests.test_whiteboard import board_fixture


def broken_observer(_record):
    raise RuntimeError("private observer failure")


@pytest.mark.parametrize("sink", [None, broken_observer])
def test_composer_complete_golden_and_inputs_are_identical_with_diagnostics(database, sink, monkeypatch):
    plan = _plan()
    before = copy.deepcopy(plan)
    expected = composer.compose_equipment_plan(plan, database[0])
    with capture_runtime_diagnostics(sink=sink) as capture:
        actual = composer.compose_equipment_plan(plan, database[0])
    assert actual == expected
    assert_pre_extraction_envelope(actual, plan, database[0],
        "43939df0e19fee53a51af03fb428c44bd778c306d067d979d5c6c31b3c912e9a", monkeypatch)
    assert plan == before
    records = [row for row in capture.records if row.domain == "composer"]
    assert len(records) == 12 and all(row.status == "completed" for row in records)
    assert dict(records[-1].output_fingerprints)["final_report"] == fingerprint(actual)
    assert "diagnostics" not in actual and "pass_trace" not in actual
    assert sink is None or capture.observer_failures >= 12


@pytest.mark.parametrize("entrypoint", [composer.compose_equipment_plan, composer.apply_data_update])
@pytest.mark.parametrize("observer_failed", [False, True])
def test_final_report_fingerprint_includes_safe_assistance_once(
    database, monkeypatch, entrypoint, observer_failed,
):
    from genshin_app import default_assistance

    plan = _plan()
    before = copy.deepcopy(plan)
    expected = entrypoint(plan, database[0])
    read_assistance = default_assistance.read_default_assistance
    calls = []

    def observed(*args, **kwargs):
        calls.append(True)
        if observer_failed:
            raise RuntimeError("synthetic optional observation failure")
        return read_assistance(*args, **kwargs)

    monkeypatch.setattr(default_assistance, "read_default_assistance", observed)
    with capture_runtime_diagnostics(sink=broken_observer) as capture:
        actual = entrypoint(plan, database[0])
    assert calls == [True]
    assert plan == before
    records = [row for row in capture.records if row.domain == "composer"]
    assert len(records) == 12 and all(row.status == "completed" for row in records)
    assert records[-1].stage_id == "report-projector-v1"
    assert dict(records[-1].output_fingerprints)["final_report"] == fingerprint(actual)
    assert actual["panel_assistance"] == actual["default_assistance"]["panels"]
    if observer_failed:
        assert actual["default_assistance"]["validation_scope"]["state"] == "observation_unavailable"
        # Optional feedback cannot change the compilation, its admission or input.
        advisory_keys = {"default_assistance", "panel_assistance"}
        assert {k: v for k, v in actual.items() if k not in advisory_keys} == {
            k: v for k, v in expected.items() if k not in advisory_keys
        }
    else:
        assert actual == expected


@pytest.mark.parametrize("invalid", [False, True])
def test_mechanics_full_result_and_one_pass_trace_owner_are_unchanged(invalid):
    values = mechanics_inputs()
    if invalid:
        values[2]["schema_version"] = 999
    original = copy.deepcopy(values)
    expected = mechanics.execute_mechanics(*values)
    with capture_runtime_diagnostics(sink=broken_observer) as capture:
        actual = mechanics.execute_mechanics(*values)
    assert actual == expected and values == original
    records = [row for row in capture.records if row.domain == "mechanics"]
    assert [row.stage_id for row in records][:1] == ["basic-admission-v1"]
    assert len(records) == (1 if invalid else 10)
    assert "pass_trace" not in actual


def test_database_candidate_records_source_and_components_without_exposing_paths(tmp_path):
    with capture_runtime_diagnostics(operation_id="candidate-operation") as capture:
        report = _build(tmp_path)
    records = [row for row in capture.records if row.domain == "database-build"]
    assert [row.stage_id for row in records if row.stage_id != "build-component-v1"] == [
        name.replace("_", "-") + "-v1" for name in STAGES]
    assert {row.component for row in records if row.stage_id == "build-component-v1"} >= {
        "raw", "curated", "equipment", "runtime_config"}
    assert all(row.source_commit == "a" * 40 and row.status == "completed" for row in records)
    assert report.output_db == str(tmp_path / "candidate.db")
    assert str(tmp_path) not in json.dumps([row.as_dict() for row in records])
    assert "diagnostics" not in report.receipt


@pytest.mark.parametrize("fault", [None, "after_replace", "after_receipt_write"])
def test_installation_fault_and_retry_match_independent_frozen_coordinator_with_observer(
    tmp_path, coordinator, monkeypatch, fault,
):
    with capture_runtime_diagnostics(sink=broken_observer) as capture:
        frozen_install_comparison(tmp_path, coordinator, monkeypatch, True, fault)
    assert any(row.domain == "database-install" for row in capture.records)
    assert capture.observer_failures >= len(capture.records)


def test_whiteboard_previous_next_cas_and_serialized_file_are_unaffected(tmp_path):
    board = board_fixture()
    path = tmp_path / "whiteboard.json"
    saved = save_whiteboard(path, board)
    candidate = copy.deepcopy(board)
    candidate["title"] = "new title"
    with capture_runtime_diagnostics() as capture:
        prepared = prepare_whiteboard_edit(board, candidate)
        transition = validate_whiteboard_revision_transition(board, prepared)
        applied = save_whiteboard(path, prepared, saved["file_sha"])
        loaded = load_whiteboard(path)
    assert json.loads(path.read_text(encoding="utf-8")) == prepared
    assert applied["revision_transition"] == transition
    assert loaded["board"] == prepared
    assert {row.domain for row in capture.records} == {"whiteboard"}
    assert all(row.operation_id == capture.operation_id for row in capture.records)
    assert all(row.board_id == board["board_id"] for row in capture.records)
    assert all(row.revision == prepared["revision"] and row.intent_fingerprint for row in capture.records)
