"""App source integration and bounded offline zero-damage evidence."""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")
from test_limited_hit_benefit_sources import benefit_plan, confirm_benefit
from test_mechanics_execution import EVIDENCE
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_limited_hit_benefit import benefit_db, benefit_repo  # noqa: F401

from genshin_app.equipment import damage_reporting_receipt as proof
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes

# ruff: noqa: F811


@pytest.fixture
def missed_calculation(benefit_db):
    plan = benefit_plan(benefit_db)
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": False, "order": 20, **EVIDENCE}]
    return run_plan_calc(confirm_benefit(plan, benefit_db), benefit_db, capture_display=True)


def validate(calculated):
    proof.validate_empty_damage_receipt(calculated["plan"], calculated["result"],
                                       calculated["report"], calculated["result_receipt"])


def test_real_app_coverage_and_offline_validation(missed_calculation, monkeypatch):
    value = missed_calculation
    before = copy.deepcopy(value)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("offline proof queried DB"))
    validate(value)
    assert value == before
    coverage = value["result"]["damage_reporting"]
    assert coverage["scope"]["end_tick"] == 10_000_000
    actual_id = value["report"]["action_event_compilations"][0]["generated"][0]["hit_id"]
    assert coverage["contacts"] == [{"event_id": actual_id, "run_id": "0:" + actual_id,
        "action_id": "cast", "tick": 200_000, "order": 20, "report": True, "landed": False, "outcome": "missed"}]
    assert value["result"]["rotation_check"]["status"] == "unproven"


@pytest.mark.parametrize("strip_mechanics", [False, True])
def test_stripped_coverage_cannot_be_exported_as_legacy_zero(missed_calculation, strip_mechanics):
    value = missed_calculation
    result, receipt = value["result"], value["result_receipt"]
    result.pop("damage_reporting")
    receipt.pop("damage_reporting")
    if strip_mechanics:
        result.pop("mechanics")
        result.pop("mechanics_status")
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(PlanError, match="零伤害窗口"):
        result_bytes(result, receipt, "json")


@pytest.mark.parametrize("key", ["dps", "total_damage"])
def test_boolean_is_not_a_zero_number(missed_calculation, key):
    value = missed_calculation
    value["result"]["summary"][key] = False
    value["result_receipt"]["result_fingerprint"] = fingerprint(value["result"])
    with pytest.raises(ValueError, match="零伤害窗口"):
        validate(value)


def test_altered_model_rehashed_proof_still_requires_actual_missed_candidates(missed_calculation):
    value = missed_calculation
    value["report"]["mechanics_model"]["hit_patterns"][0]["application"]["landed"] = True
    value["result_receipt"]["damage_reporting"] = proof.make_empty_damage_receipt(
        value["plan"], value["result"], value["report"])
    with pytest.raises(ValueError, match="零伤害窗口"):
        validate(value)


def test_zero_proof_budget_is_enforced_offline(missed_calculation, monkeypatch):
    monkeypatch.setattr(proof, "MAX_EVIDENCE_BYTES", 32)
    with pytest.raises(ValueError, match="证明超过"):
        validate(missed_calculation)


def test_source_activation_remains_without_consuming_quotas(missed_calculation):
    result = missed_calculation["result"]
    assert result["limited_hit_benefit_summary"]["activations"] == 1
    assert result["limited_hit_benefit_summary"]["consumed_hits"] == 0
    assert result["limited_hit_benefit_summary"]["sampled_hits"] == 0
    assert result["hits"] == []
    assert result_bytes(result, missed_calculation["result_receipt"], "json")


@pytest.mark.parametrize("save_native", [False, True])
def test_cli_compose_cannot_export_unusable_empty_resolved_template(missed_calculation, benefit_db, tmp_path, capsys, save_native):
    from genshin_app.cli import main
    from genshin_app.equipment.files import input_view

    shutil.copyfile(benefit_db, benefit_db.parent / "genshin.db")
    plan = copy.deepcopy(input_view(missed_calculation["plan"]))
    plan.update(resolution={"state": "pending"}, resolved_template=None)
    source = tmp_path / "input.plan.json"
    source.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf8")
    resolved, saved = tmp_path / "empty-template.json", tmp_path / "saved-native.json"
    arguments = ["--data-dir", str(benefit_db.parent), "equipment", "compose", "--plan", str(source),
                 "--export-resolved", str(resolved)]
    if save_native:
        arguments.extend(["--output", str(saved)])
    status = main(arguments)
    captured = capsys.readouterr()
    assert status == 1
    assert "独立伤害模板" in json.loads(captured.err)["message"]
    assert not resolved.exists() and not saved.exists()
