"""One execution claim has identical qualification outside GUI-only sessions."""
import json
import uuid

import pytest

from genshin_app.calculation_qualification import (
    qualification_for_claim,
    qualify_execution,
)
from genshin_app.cli import main
from genshin_app.equipment import run_plan_calc
from tests.test_calculation_delivery import delivery_plan
from tests.test_energy_trials import plan


@pytest.fixture(autouse=True)
def admitted(monkeypatch):
    monkeypatch.setattr("genshin_app.cli._require_runtime_database", lambda *_: {"can_start": True})


def test_plain_formula_remains_accepted_without_an_action_closure_claim(tmp_path):
    calculated = run_plan_calc(delivery_plan(), tmp_path / "absent.db")
    result = qualification_for_claim(calculated)
    assert result["claim"] == "formula_only" and result["can_accept"]
    assert result["event_inventory"] == "not_claimed"


def test_plain_formula_native_candidate_and_cli_have_same_admission(tmp_path):
    from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
    current = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db",
                                      "native", delivery_plan())
    response = current.call_tool("gs_calculate", {"expected_revision": 0})
    assert response["code"] == 0, response
    gui = current.calculation_acceptance()
    cli = qualification_for_claim(current.calculated)
    assert gui == cli and gui["can_accept"] and gui["claim"] == "formula_only"


def test_explicit_action_qualification_reuses_same_core_without_extra_inventory(tmp_path):
    source = plan()
    source["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 2
    calculated = run_plan_calc(source, tmp_path / "absent.db")
    result = qualification_for_claim(calculated, energy_intent="multi_round_legal")
    expected = qualify_execution(calculated, energy_intent="multi_round_legal")
    assert {key: value for key, value in result.items() if key != "claim"} == expected


def test_explicit_sustainable_claim_cannot_use_plain_formula_as_energy_proof(tmp_path, capsys):
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(delivery_plan()), encoding="utf-8")
    output = tmp_path / "diagnostic.json"
    assert main(["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(source),
        "--output", str(output), "--energy-intent", "sustainable_loop"]) == 1
    response = json.loads(capsys.readouterr().err)
    assert response["reason"] == "calculation_qualification_rejected"
    assert response["details"]["qualification"]["can_accept"] is False
    assert json.loads(output.read_text("utf-8"))["kind"] == "genshin-calculation-diagnostic"


def test_cli_only_app_roles_never_call_workbook_builders(tmp_path, monkeypatch, capsys):
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(delivery_plan()), encoding="utf-8")
    def forbidden(*_args, **_kwargs):
        pytest.fail("Excel was requested by an App-only CLI publication")
    monkeypatch.setattr("genshin_app.formula_workbook.build_formula_workbook", forbidden)
    monkeypatch.setattr("genshin_app.formula_workbook.formula_workbook_support", forbidden)
    target = tmp_path / "delivery"
    assert main(["--data-dir", str(tmp_path), "equipment", "deliver", "--plan", str(source),
        "--output-dir", str(target), "--roles", "native_plan,calculated"]) == 0
    value = json.loads(capsys.readouterr().out)["data"]
    assert {row["role"] for row in value["artifacts"]} == {"native_plan", "calculated"}
    assert not list(target.rglob("*.xlsx"))
