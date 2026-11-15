"""Native source binding and GUI/XLSX delivery of automatic swirl damage."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
from test_elemental_execution import profile, setup
from test_mechanics_execution import EVIDENCE, add, rule

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi


def native_swirl():
    t, r, m = setup(("cryo", "anemo", "anemo", "anemo"))
    t["characters"][0]["stats"]["energy_recharge"] = 1
    r["actions"][0]["repeat"] = {"mode": "once"}
    model = m["elemental_model"]
    model.update(ruleset="gauge-swirl-v2", swirl_damage={"icd": {"kind": "unknown"}, **EVIDENCE})
    model["profiles"] = [profile("cryo", gu=4), profile("anemo")]
    plan = enable_mechanics(new_plan(t))
    plan.update(rotation_model=r, mechanics_model=m)
    plan["reaction_damage_sources"] = [{"reaction_type": "swirl", "version_line": "release",
        "source": "工程情景，明确普通水冰原目标扩散；不代表整段原始能力可执行",
        "expected_snapshot_id": None, "expected_profile_fingerprint": None}]
    return plan


@pytest.fixture
def source_api(monkeypatch):
    profiles = {element: {"status": "ready", "selection": {"element": element},
        "snapshot": {"snapshot_id": "snapshot"}, "content_fingerprint": element + "-fingerprint",
        "execution": {"sequence_status": "ready", "ability_execution": "pending"},
        "projection": {"reset_cycle_seconds_rounded": .5, "damage_sequence": [1, 1] + [0]*8},
        "sources": {}} for element in ("hydro", "cryo")}
    calls = []
    def query(_db, **kw):
        calls.append(kw)
        return copy.deepcopy(profiles[kw["element"]])
    monkeypatch.setattr("genshin_db.runtime_reaction_damage.get_runtime_reaction_damage_profile", query)
    return profiles, calls


def confirmed(plan, db):
    report = compose_equipment_plan(plan, db)
    record = report["reaction_damage_compilations"][0]
    plan["reaction_damage_sources"][0].update(record["required_confirmation"])
    return plan


def test_pending_source_requires_confirmation_and_input_stays_unknown(source_api, tmp_path):
    plan = native_swirl()
    original = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, tmp_path / "genshin.db")
    assert report["state"] == "pending"
    assert report["reaction_damage_compilations"][0]["profiles"][0]["execution"]["ability_execution"] == "pending"
    with pytest.raises(PlanError):
        run_plan_calc(plan, tmp_path / "genshin.db")
    assert plan == original


def test_fresh_sources_drive_two_children_gui_reload_and_excel(source_api, tmp_path):
    plan = confirmed(native_swirl(), tmp_path / "genshin.db")
    original = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, tmp_path / "genshin.db")
    assert plan == original
    assert calculated["plan"]["mechanics_model"]["elemental_model"]["swirl_damage"]["icd"] == {"kind": "unknown"}
    hits = calculated["result"]["hits"]
    assert len(hits) == 6
    assert sorted(h["expected"] for h in hits) == pytest.approx([45]*4 + [781.299]*2)
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(1742.598)
    comp = calculated["result"]["elemental"]["reaction_damage_source_compilations"][0]
    assert comp["status"] == "ready"
    assert calculated["result_receipt"]["reaction_damage_sources"][0]["compilation_fingerprint"] == comp["compilation_fingerprint"]
    assert formula_workbook_support(calculated)["editable"]["database_reaction_damage"] == "regenerate_required"
    with zipfile.ZipFile(io.BytesIO(build_formula_workbook(calculated))) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        cells = {c.attrib["r"]: c for c in root.findall(".//s:c", ns)}
        assert sorted(float(cells[f"V{i}"].find("s:v", ns).text) for i in range(5, 11)) == pytest.approx([45]*4 + [781.299]*2)
        assert all(cells[f"V{i}"].find("s:f", ns) is not None for i in range(5, 11))
    path = tmp_path / "native.json"
    path.write_text(json.dumps(calculated["plan"]), "utf8")
    loaded = read_plan(path)["plan"]
    loaded["resolved_template"]["hits"][0]["multiplier"] = 999
    gui = GuiApi(tmp_path).run_plan_calc(loaded)
    assert gui["code"] == 0, gui
    assert gui["data"]["result"] == calculated["result"]
    assert len(source_api[1]) == 6  # two sources freshly read for each compose


@pytest.mark.parametrize("mutation,code", [
    (lambda p: p["cryo"].update(content_fingerprint="changed"), "source_changed"),
    (lambda p: p["cryo"]["execution"].update(sequence_status="pending"), "unsupported_reaction_source"),
    (lambda p: p["cryo"]["projection"].update(damage_sequence=[1, 0]), "reaction_damage_group_conflict"),
    (lambda p: p["cryo"]["snapshot"].update(snapshot_id="other"), "reaction_source_snapshot_conflict"),
])
def test_changed_or_unusable_sources_never_publish_result(source_api, tmp_path, mutation, code):
    plan = confirmed(native_swirl(), tmp_path / "genshin.db")
    mutation(source_api[0])
    report = compose_equipment_plan(plan, tmp_path / "genshin.db")
    assert report["state"] == "pending"
    assert code in json.dumps(report)
    with pytest.raises(PlanError):
        run_plan_calc(plan, tmp_path / "genshin.db")


def test_duplicate_manual_icd_and_binding_rejected(source_api, tmp_path):
    plan = native_swirl()
    plan["mechanics_model"]["elemental_model"]["swirl_damage"]["icd"] = {"kind": "sequence", "reset_seconds": .5, "sequence": [1]}
    with pytest.raises(PlanError, match="手填ICD"):
        compose_equipment_plan(plan, tmp_path / "genshin.db")


def test_only_nondamage_wind_can_generate_output_without_dummy_hit(source_api, tmp_path):
    plan = native_swirl()
    plan["manual_template"]["hits"] = []
    mechanics = plan["mechanics_model"]
    mechanics["hit_events"] = []
    mechanics["elemental_model"]["target"]["initial_aura"] = {
        "element": "cryo", "remaining_gu": .8, "decay_gu_per_second": .01, "source": "工程初态"}
    mechanics["application_events"] = [{"id": "wind_contact", "action_id": "cast", "t": .2, "order": 10,
        "application": {"profile_id": "anemo", "target_id": "enemy", "landed": True}}]
    confirmed(plan, tmp_path / "genshin.db")
    calculated = run_plan_calc(plan, tmp_path / "genshin.db")
    assert len(calculated["result"]["hits"]) == 1
    assert calculated["result"]["hits"][0]["expected"] == pytest.approx(781.299)
    assert not calculated["plan"]["manual_template"]["hits"]
    assert GuiApi(tmp_path).run_plan_calc(plan)["code"] == 0
    assert build_formula_workbook(calculated).startswith(b"PK")
    mechanics["elemental_model"]["target"]["initial_aura"] = None
    with pytest.raises(PlanError) as exc:
        run_plan_calc(plan, tmp_path / "genshin.db")
    assert "no_reported_damage" in json.dumps(exc.value.details)


def test_excel_links_frozen_provider_bonus_and_current_enemy_shred(source_api, tmp_path):
    from test_plan_mechanics import native as binding_fixture
    from test_plan_mechanics import review

    plan = native_swirl()
    mechanics = plan["mechanics_model"]
    add(mechanics,
        rule("bonus", "benefit.reaction_bonus.swirl", .6, domain="provider",
             trigger={"event": "elemental_reaction", "phase": "post"}),
        rule("shred", "benefit.res_shred.cryo", .4, domain="enemy",
             trigger={"event": "elemental_reaction", "phase": "post", "reaction_elements": ["cryo"]}))
    binding = binding_fixture()["effect_bindings"][0]
    part = copy.deepcopy(binding["parts"][0])
    binding["parts"] = []
    for item in mechanics["rules"]:
        item["source_id"] = "binding:" + item["id"]
        row = copy.deepcopy(part)
        row.update(part_id=item["id"], rule={"kind": "mechanics", "rule_id": item["id"]})
        binding["parts"].append(row)
    plan["effect_bindings"] = [binding]
    review(plan, tmp_path / "genshin.db")
    confirmed(plan, tmp_path / "genshin.db")
    calculated = run_plan_calc(plan, tmp_path / "genshin.db")
    assert formula_workbook_support(calculated)["status"] == "supported"
    damage = [h["expected"] for h in calculated["result"]["hits"]]
    assert sorted(damage) == pytest.approx([45]*4 + [998.3265, 1597.3224])
    with zipfile.ZipFile(io.BytesIO(build_formula_workbook(calculated))) as archive:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        cells = {c.attrib["r"]: c for c in ET.fromstring(archive.read("xl/worksheets/sheet3.xml")).findall(".//s:c", ns)}
        assert [float(cells[f"V{i}"].find("s:v", ns).text) for i in range(5, 11)] == pytest.approx(damage)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(plan_version=2),
    lambda p: p["reaction_damage_sources"].append(copy.deepcopy(p["reaction_damage_sources"][0])),
    lambda p: p["reaction_damage_sources"][0].update(source="  "),
])
def test_source_schema_requires_v3_single_binding_and_basis(mutation):
    plan = native_swirl()
    mutation(plan)
    with pytest.raises(PlanError):
        validate_plan(plan)
