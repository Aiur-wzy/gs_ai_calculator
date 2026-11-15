"""中文显示/导出与身份、数值和本地数据库的兼容边界。"""

import copy
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from genshin_app.database_localization import localize_database
from genshin_app.export import export_result, hit_rows
from genshin_app.gui.api import GuiApi
from genshin_app.presentation import display_contract, display_names
from genshin_db.validation import ALL_STATUS, TARGET_TYPES
from genshin_engine import calculate, constants

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "freeze_team.json"


def test_all_engine_choices_have_chinese_labels():
    labels = display_contract()["labels"]
    for group, keys in (
        ("elements", constants.ELEMENTS), ("buff_zones", constants.BUFF_ZONES),
        ("damage_systems", constants.DAMAGE_SYSTEMS), ("buff_scopes", constants.BUFF_SCOPES),
        ("stats", constants.SCALING_STATS), ("annotation_target_types", TARGET_TYPES),
        ("states", ALL_STATUS),
        ("reaction_types", {"amplify", *constants.AGGRAVATE_FACTOR, *constants.TRANSFORMATIVE_FACTOR,
                            *constants.LUNAR_FACTOR, *constants.STELLAR_FACTOR, "stellar_superconduct"}),
    ):
        assert set(keys) <= labels[group].keys()
        assert all(any("\u3400" <= c <= "\u9fff" for c in labels[group][key]) for key in keys)
    appendix_categories = {
        "hit_pattern", "action_dispatch", "action_lifecycle", "periodic_attack",
        "elemental_application",
    }
    assert appendix_categories <= labels["annotation_categories"].keys()


def test_display_contract_is_copy_and_gui_keeps_machine_enums(tmp_path):
    api = GuiApi(tmp_path)
    first = api.get_enums()["data"]
    first["display"]["labels"]["elements"]["cryo"] = "changed"
    second = api.get_enums()["data"]
    assert second["display"]["labels"]["elements"]["cryo"] == "冰元素"
    assert second["elements"] == list(constants.ELEMENTS)


def test_display_names_resolve_unique_aliases_without_changing_references():
    template = {"characters": [
        {"id": "raiden_shogun"}, {"id": "AYAKA"}, {"id": "twin"},
        {"id": "custom", "name": "用户角色"},
    ], "buffs": [{"id": "vv1", "source": "翠绿之影"}, {"id": "unknown"}]}
    catalog = {"characters": [
        {"id": 1, "name": "雷电将军", "aliases": ["Raiden Shogun"]},
        {"id": 2, "name": "神里绫华", "aliases": ["Ayaka"]},
        {"id": 3, "name": "甲", "aliases": ["twin"]},
        {"id": 4, "name": "乙", "aliases": ["twin"]},
    ]}
    original = copy.deepcopy(template)
    names = display_names(template, catalog)
    assert template == original
    assert names["characters"] == {"raiden_shogun": "雷电将军", "AYAKA": "神里绫华",
                                   "twin": "角色3（中文名待补）", "custom": "用户角色"}
    assert names["buffs"] == {"vv1": "翠绿之影", "unknown": "增益2（中文名待补）"}


def test_native_reference_precedes_conflicting_legacy_alias():
    plan = {"manual_template": {"characters": [{"id": "same"}]},
            "equipment_inputs": {"same": {"character_ref": {"db_character_id": 2}}}}
    catalog = {"characters": [{"id": 1, "name": "甲", "aliases": ["same"]},
                               {"id": 2, "name": "乙", "aliases": []}]}
    assert display_names(plan, catalog)["characters"]["same"] == "乙"


def test_native_reference_does_not_borrow_name_from_other_line():
    plan = {"manual_template": {"characters": [{"id": "ayaka"}]},
            "equipment_inputs": {"ayaka": {"character_ref": {
                "db_character_id": 2, "version_line": "beta"}}}}
    catalog = {"version_line": "release", "characters": [
        {"id": 2, "name": "另一数据线的名字", "aliases": ["ayaka"]}]}
    assert "中文名待补" in display_names(plan, catalog)["characters"]["ayaka"]
    plan["equipment_inputs"]["ayaka"]["character_ref"] = {"db_character_id": 999,
                                                            "version_line": "release"}
    assert "中文名待补" in display_names(plan, catalog)["characters"]["ayaka"]


def test_missing_database_catalog_is_read_only(tmp_path):
    folder = tmp_path / "does-not-exist"
    api = GuiApi(folder)
    response = api.get_display_catalog()
    assert response["code"] == 0 and response["data"]["characters"] == []
    assert response["data"]["state"] == "missing"
    assert not folder.exists()
    assert api.get_display_catalog("unrecognized")["code"] == 1
    assert not folder.exists()


def make_db(path):
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
                         "CREATE TABLE character(id INTEGER,version_line TEXT,name TEXT,desc TEXT);"
                         "CREATE TABLE annotation(id INTEGER,content TEXT);")
        db.executemany("INSERT INTO character VALUES(?,?,?,?)",
                       [(10000002, "release", "神里绫华", "角色介绍"),
                        (10000002, "beta", "另一数据线角色", "测试介绍")])
        db.execute("INSERT INTO annotation VALUES(1,'人工备注')")


def test_explicit_localize_has_valid_backup_and_preserves_existing_data(tmp_path):
    path = tmp_path / "genshin.db"
    make_db(path)
    report = localize_database(path)
    backup = Path(report["backup_path"])
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == report["backup_sha256"]
    for candidate in (backup, path):
        with sqlite3.connect(candidate) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("SELECT COUNT(*) FROM character").fetchone()[0] == 2
            assert db.execute("SELECT content FROM annotation").fetchone()[0] == "人工备注"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM entity_alias WHERE version_line='beta'").fetchone()[0] == 0
    assert json.loads(Path(report["manifest_path"]).read_text("utf-8"))["state"] == "complete"


def test_invalid_localize_request_does_not_create_backup_or_database(tmp_path):
    with pytest.raises(ValueError):
        localize_database(tmp_path / "none.db", "wrong")
    assert list(tmp_path.iterdir()) == []


def test_native_manual_export_does_not_require_display_database(tmp_path):
    from genshin_app.equipment import new_plan, run_plan_calc
    from genshin_app.equipment.service import export_plan_result

    plan = new_plan()
    plan["manual_template"].update(
        characters=[{"id": "custom", "name": "角色甲", "level": 90, "stats": {"atk": 1000}}],
        hits=[{"id": "hit.one", "damage_type_ids": ["damage.other"], "t": 1,
               "character": "custom", "skill": "E", "hit_name": "技能伤害", "multiplier": 2,
               "damage_system": "classic", "element": "physical"}])
    db_path = tmp_path / "absent.db"
    expected = run_plan_calc(plan, db_path)["result"]["hits"][0]["expected"]
    result = export_plan_result(plan, db_path, "csv", tmp_path / "result.csv")
    with Path(result["path"]).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[1][1] == "角色甲" and float(rows[1][6]) == expected
    assert result["display_warnings"]
    assert not db_path.exists()


def test_plan_list_displays_title_without_changing_path_identity(tmp_path):
    folder = tmp_path / "plans"
    folder.mkdir()
    (folder / "english-file.json").write_text(json.dumps({"meta": {"name": "中文方案"}}), "utf-8")
    (folder / "bad-meta.json").write_text('{"meta":null}', "utf-8")
    items = GuiApi(tmp_path).list_plan_details()["data"]["items"]
    chosen = next(item for item in items if item["name"] == "english-file.json")
    assert chosen["display_name"] == "中文方案"
    assert next(item for item in items if item["name"] == "bad-meta.json")["display_name"] is None


def test_gui_export_has_chinese_names_but_preserves_damage_and_machine_json(tmp_path):
    template = json.loads(EXAMPLE.read_text("utf-8"))
    for index, char in enumerate(template["characters"], 1):
        char["name"] = f"测试角色{index}"
    result = calculate(template)
    baseline = copy.deepcopy(result)
    result["gui_input"] = {"template": template}
    api = GuiApi(tmp_path)
    response = api.export_result(result, "csv", "中文导出")
    assert response["code"] == 0
    with Path(response["data"]["path"]).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[1][1].startswith("测试角色")
    assert rows[1][2] == "元素爆发" and rows[1][4] == "传统伤害"
    assert [float(row[6]) for row in rows[1:]] == [hit["expected"] for hit in baseline["hits"]]
    assert {key: value for key, value in result.items() if key != "gui_input"} == baseline
    target = tmp_path / "machine.json"
    export_result(baseline, "json", target, display=display_names(template))
    assert json.loads(target.read_text("utf-8")) == baseline
    assert hit_rows(baseline)[1][1] == baseline["hits"][0]["character"]
