"""Equipment provenance stays distinct from cached panels and later edits."""
import copy
import io
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import SLOTS
from genshin_app.equipment.service import panel_example
from genshin_app.equipment.summary import build_equipment_summary, equipment_rows
from genshin_db import queries
from tests.test_equipment_plan import database as _database
from tests.test_equipment_plan import plan_fixture

database = _database


def test_summary_rebuilds_from_fresh_sources_and_never_uses_cached_resolution(database):
    db, _ = database
    plan = plan_fixture()
    before = fingerprint(input_view(plan))
    report = compose_equipment_plan(plan, db)
    summary = report["equipment_summary"]
    char = summary["characters"][0]
    assert char["weapon"]["weapon_id"] == 1
    assert char["panel_stages"]["base"]["atk"] == 800
    assert sum(c["value"] for c in char["base_sources"] if c["stat"] == "atk") == 800
    assert char["panel_stages"]["final"]["atk"] == 1800
    assert not any(c["stat"] == "base_atk" for c in char["contributions"])
    assert fingerprint(input_view(plan)) == before
    # The cache in a file is never enough to call a summary current.
    plan["resolution"] = {"contribution_report": {"characters": {"char1": {"final": {"atk": 999999}}}}}
    unverified = build_equipment_summary(plan)["characters"][0]
    assert unverified["panel_stages"] == {}
    assert unverified["source_status"] == "unverified"
    plan["equipment_inputs"]["char1"]["weapon"]["weapon_id"] = 2
    assert char["weapon"]["weapon_id"] == 1
    with pytest.raises(ValueError, match="不属于当前输入"):
        build_equipment_summary(plan, report)


def test_mixed_manual_missing_and_explicit_empty_have_distinct_states():
    plan = plan_fixture()
    plan["manual_template"]["characters"].append({"id": "manual", "name": "手工角色", "level": 90, "stats": {"atk": 2020}})
    plan["equipment_inputs"]["char1"]["artifacts"][SLOTS[0]] = None
    gear, manual = build_equipment_summary(plan)["characters"]
    assert gear["artifact_slots"][0]["state"] == "not_specified"
    assert gear["artifact_slots"][1]["state"] == "empty"
    assert manual["input_mode"] == "manual_resolved"
    assert manual["weapon"]["state"] == "not_provided"
    assert manual["panel_stages"]["final"]["atk"] == 2020
    assert all(s["state"] == "not_provided" for s in manual["artifact_slots"])


def test_artifact_summary_uses_real_set_identity_and_grown_main_stat(database, monkeypatch):
    db, current = database
    plan = plan_fixture()
    monkeypatch.setattr(queries, "get_artifact_detail", lambda _, aid, line="release": {
        "artifact": {"id": aid, "name": "同名单件", "equip_type": SLOTS[aid - 1], "set_id": 42 if aid < 3 else 43,
                     "rarity": 5}, "data_version": current})
    monkeypatch.setattr(queries, "get_artifact_main_stats", lambda _, aid, level, mid, line="release": {
        "stat": {"prop_type": "FIGHT_PROP_HP", "prop_name": "生命值", "value": 4780, "unit": "flat", "source_refs": []}})
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda _, sid, line="release": {
        "artifact_set": {"id": sid, "name": "同名套装"},
        "effects": [{"required_count": c, "effect": {"affix_id": c, "desc_template": "效果原文"}} for c in (2, 4)]})
    for i, slot in enumerate(SLOTS[:3], 1):
        plan["equipment_inputs"]["char1"]["artifacts"][slot] = {"state": "equipped", "artifact_id": i,
            "level": 20, "main_prop_id": 1, "substats": [], "selected_by": "user"}
    char = compose_equipment_plan(plan, db)["equipment_summary"]["characters"][0]
    assert [(s["set_id"], s["count"]) for s in char["artifact_sets"]] == [(42, 2), (43, 1)]
    assert char["artifact_slots"][0]["main_stat"]["value"] == 4780
    assert char["artifact_sets"][0]["effects"][0]["state"] == "pending"
    assert char["artifact_sets"][0]["effects"][1]["state"] == "not_applicable"
    assert char["pending_effects"] == 1


def test_readable_rows_include_element_bonus_and_exact_equipment_sources(database):
    db, _ = database
    plan = plan_fixture()
    plan["equipment_inputs"]["char1"]["basis"]["dmg_bonus"] = {"cryo": .466}
    report = compose_equipment_plan(plan, db)
    summary = build_equipment_summary(report["plan"], report, kind="calculation_snapshot")
    rows = equipment_rows(summary)
    assert any(r[2] == "冰元素伤害加成" and r[3] == .466 for r in rows)
    assert any(r[1] == "基础来源" and r[3] == 500 for r in rows)
    assert summary["context"]["input_fingerprint"] == report["input_fingerprint"]
    assert summary["context"]["kind"] == "calculation_snapshot"


def test_excel_and_json_delivery_keep_equipment_snapshot(database):
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.service import result_bytes
    db, _ = database
    report = compose_equipment_plan(plan_fixture(), db)
    calculated = run_plan_calc(report["plan"], db, capture_display=True)
    result = calculated["result"]
    receipt = calculated["result_receipt"]
    summary = calculated["equipment_summary"]
    old = copy.deepcopy(summary)
    assert receipt["input_fingerprint"] == report["input_fingerprint"]
    data = result_bytes(result, receipt, "xlsx", equipment_summary=summary)
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        assert "角色与装备" in book.read("xl/workbook.xml").decode()
        sheet = book.read("xl/worksheets/sheet5.xml").decode()
        assert "武器白值已计入计算基础攻击力" in sheet
        assert report["input_fingerprint"] in sheet
    assert b'equipment_summary' in result_bytes(result, receipt, "json", equipment_summary=summary)
    assert summary == old


def test_unverified_panel_summary_preserves_given_stats_and_has_no_missing_slots():
    plan = panel_example()
    before = copy.deepcopy(plan)
    plan["resolution"] = {"contribution_report": {"characters": {"char1": {"static": {"atk": 999999}}}}}
    char = build_equipment_summary(plan)["characters"][0]
    assert char["input_mode"] == "panel"
    assert char["level"] == before["manual_template"]["characters"][0]["level"]
    assert char["panel_stages"]["static"] == before["manual_template"]["characters"][0]["stats"]
    assert char["artifact_slots"] == []
    assert "specified_slots" not in char
    assert char["source_status"] == "unverified"
    assert char["source_refs"] == before["equipment_inputs"]["char1"]["source_refs"]
    assert char["artifact_sets"][0]["count"] == 4


@pytest.mark.parametrize("selected", [[{"set_id": 42, "count": 4}],
                                      [{"set_id": 42, "count": 2}, {"set_id": 43, "count": 2}]])
def test_panel_composition_summary_and_export_bind_exact_sets_to_given_panel(database, monkeypatch, selected):
    db, current = database
    plan = panel_example()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = copy.deepcopy(selected)
    plan["equipment_inputs"]["char1"]["data_version"] = current
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda _, sid, version_line="release": {
        "artifact_set": {"id": sid, "name": f"测试套装{sid}", "source_refs": [{"set_id": sid}]},
        "effects": [{"required_count": n, "effect": {"affix_id": sid * 10 + n,
            "desc_template": "核对当前常驻与条件效果"}} for n in (2, 4)], "data_version": current})
    report = compose_equipment_plan(plan, db)
    summary = report["equipment_summary"]
    char = summary["characters"][0]
    assert char["source_status"] == "current"
    assert char["artifact_slots"] == []
    assert [(s["set_id"], s["count"]) for s in char["artifact_sets"]] == [(s["set_id"], s["count"]) for s in selected]
    assert char["artifact_sets"][0]["source_refs"] == [{"set_id": 42}]
    assert char["panel_stages"]["static"] == plan["manual_template"]["characters"][0]["stats"]
    rows = equipment_rows(summary)
    assert any(r[1] == "给定初始面板" and r[2] == "攻击力" and r[3] == 1800 for r in rows)
    assert any(r[1] == "换装口径" and "不自动重算词条" in r[3] for r in rows)
    assert not any(r[1] in {"圣遗物", "主词条", "累计副词条"} for r in rows)
    plan["manual_template"]["characters"][0]["stats"]["atk"] = 9999
    plan["equipment_inputs"]["char1"]["artifact_sets"] = []
    assert char["panel_stages"]["static"]["atk"] == 1800
    assert char["artifact_sets"][0]["set_id"] == 42


def test_panel_summary_keeps_given_stats_but_marks_missing_database_unverified(database):
    db, _ = database
    plan = panel_example()
    report = compose_equipment_plan(plan, db.with_name("missing.db"))
    char = report["equipment_summary"]["characters"][0]
    assert report["state"] == "pending"
    assert char["source_status"] == "unverified"
    assert any(m["path"] == "equipment_inputs" and m["code"] == "equipment_unavailable"
               for m in char["missing_inputs"])
    assert char["panel_stages"]["static"] == plan["manual_template"]["characters"][0]["stats"]


@pytest.mark.parametrize("selection", ["weapon", "artifact_set"])
def test_panel_summary_does_not_claim_current_when_selected_source_query_fails(database, monkeypatch, selection):
    db, current = database
    plan = panel_example()
    gear = plan["equipment_inputs"]["char1"]
    gear["data_version"] = current
    if selection == "weapon":
        gear["weapon"] = {"weapon_id": 1, "refinements": [], "selected_by": "user"}

    def unavailable(*args, **kwargs):
        raise ValueError("所选资料读取失败")

    monkeypatch.setattr(queries, f"get_{selection}_detail", unavailable)
    report = compose_equipment_plan(plan, db)
    char = report["equipment_summary"]["characters"][0]
    assert report["state"] == "pending"
    assert char["source_status"] == "unverified"
    assert any(m["path"] == "equipment_inputs.char1" for m in char["missing_inputs"])
    assert char["panel_stages"]["static"] == plan["manual_template"]["characters"][0]["stats"]
