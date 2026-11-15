"""Reference state rules cross the normal native plan, GUI and source boundaries."""
from __future__ import annotations

import copy
import csv
import io
import json

import pytest
from test_elemental_execution import profile
from test_reaction_sources_app import confirmed, native_swirl, source_api  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.gui.api import GuiApi
from genshin_engine.elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT

pytestmark = pytest.mark.usefixtures("source_api")


def native_freeze():
    plan = native_swirl()
    model = plan["mechanics_model"]["elemental_model"]
    evidence = {"status": "assumed", "source": "有界工程参考情景，非游戏实测"}
    model.update(ruleset=FREEZE_RULESET, contact_profiles=[], reference_acceptance={
        "contract_id": REFERENCE_CONTRACT["contract_id"],
        "contract_fingerprint": REFERENCE_CONTRACT["contract_fingerprint"],
        "acknowledged": True, "source": "采用公开参考模型范围验证App接线"})
    model["target"].pop("initial_aura")
    model["target"].update(initial_state={"ordinary_aura": None, "frozen": None,
        "freeze_history_seconds": 0, **evidence}, freeze={"freezable": True, "resistance": 0, **evidence})
    model["profiles"] = [profile("cryo"), profile("hydro"), profile("anemo", gu=4)]
    for element, hit, descriptor in zip(("cryo", "hydro", "hydro", "anemo"),
            plan["manual_template"]["hits"], plan["mechanics_model"]["hit_events"], strict=True):
        hit["element"] = element
        descriptor["application"]["profile_id"] = element
        descriptor["contact"] = {"kind": "none", **evidence}
    return plan


def test_native_fresh_sources_receipt_reload_and_gui_retain_reference_evidence(tmp_path):
    db = tmp_path / "genshin.db"
    plan = confirmed(native_freeze(), db)
    before = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, db)
    assert plan == before
    result = calculated["result"]
    assert result["elemental"]["evidence_tier"] == "experimental_reference"
    assert len([h for h in result["hits"] if h["damage_system"] == "transformative"]) == 2
    assert result["summary"]["total_damage"] == pytest.approx(1742.598)
    assert calculated["result_receipt"]["elemental_reference"]["reference_contract"] == REFERENCE_CONTRACT
    assert "实验参考" in result["rotation_check"]["damage_interpretation"]["message"]
    assert calculated["plan"]["mechanics_model"]["elemental_model"]["swirl_damage"]["icd"] == {"kind": "unknown"}
    path = tmp_path / "native.json"
    path.write_text(json.dumps(calculated["plan"]), "utf8")
    reloaded = read_plan(path)["plan"]
    reloaded["resolved_template"]["hits"][0]["multiplier"] = 999
    response = GuiApi(tmp_path).run_plan_calc(reloaded)
    assert response["code"] == 0, response
    assert response["data"]["result"] == result
    csv_rows = list(csv.DictReader(io.StringIO(result_bytes(result, calculated["result_receipt"], "csv").decode("utf-8-sig"))))
    assert all(json.loads(row["elemental_reference_json"])["evidence_tier"] == "experimental_reference" for row in csv_rows)


def test_reference_version_and_source_confirmations_are_independent(tmp_path):
    db = tmp_path / "genshin.db"
    previous = confirmed(native_swirl(), db)["reaction_damage_sources"]
    plan = native_freeze()
    plan["reaction_damage_sources"] = previous
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert "source_changed" in json.dumps(report["missing_inputs"])
    confirmed(plan, db)
    plan["mechanics_model"]["elemental_model"]["reference_acceptance"]["contract_fingerprint"] = "old-reference"
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)
    assert GuiApi(tmp_path).run_plan_calc(plan)["code"] != 0


def test_reference_nondamage_contacts_need_no_dummy_damage_input(tmp_path):
    plan = native_freeze()
    mechanics = plan["mechanics_model"]
    mechanics["application_events"] = [{"id": "apply-" + hit["id"], "action_id": desc["action_id"],
        "t": hit["t"], "order": index, "application": desc["application"], "contact": desc["contact"]}
        for index, (hit, desc) in enumerate(zip(plan["manual_template"]["hits"], mechanics["hit_events"], strict=True))]
    mechanics["hit_events"] = []
    plan["manual_template"]["hits"] = []
    confirmed(plan, tmp_path / "genshin.db")
    calculated = run_plan_calc(plan, tmp_path / "genshin.db")
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(1562.598)
    assert len(calculated["result"]["hits"]) == 2
    assert not plan["manual_template"]["hits"]
