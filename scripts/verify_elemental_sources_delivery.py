"""Real-source ICD projection integration probe, without inferred skill/GU mapping.

Writes only outputs/engine-continuation-20261002/ec02/delivery; reads the project
database through normal App paths. Optional --cli-exe also checks a built CLI.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.equipment import (
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_queries import query_runtime


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_plan():
    evidence = {"status": "assumed", "source": "独立工程情景：给定GU=1与命中时点；不代表真实角色或技能附着映射"}
    template = {"version": 2, "form": "resolved", "id_registry_version": 1,
                "type_definitions": {"damage_types": []}, "enemy": {"level": 90, "resistance": {"default": .1}},
                "meta": {"name": "数据库ICD序列·工程对照", "assumptions": [evidence["source"], "倍率归一为1、攻击1000、无暴击/增伤，仅核对序列和工程伤害，不是真实DPS"]},
                "characters": [{"id": "probe", "name": "工程测试角色", "level": 90,
                                "stats": {"atk": 1000, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}}],
                "hits": [], "buffs": [], "switches": [], "loop_duration": 4}
    rotation = {"schema_version": 1, "period": 4, "max_cycles": 3, "initial_frontline": "probe",
                "characters": [{"id": "probe", "element": "hydro", "energy_recharge": 1,
                                "energy_capacity": 60, "initial_energy": 60, **evidence}],
                "abilities": [{"id": "probe.skill", "character": "probe", "action_type_id": "action.elemental_skill",
                               "cooldown": None, "energy": None, **evidence}],
                "actions": [], "events": [], "initial_cooldowns": []}
    mechanics = {"schema_version": 1, "rules": [], "hit_events": [], "hit_patterns": [], "condition_events": [],
                 "initial_state": {"rules": []}, "elemental_model": {"schema_version": 1, "ruleset": "gauge-basic-v1",
                   "target": {"id": "target", "kind": "ordinary", "knowledge": "known", "initial_aura": None,
                              "status": "assumed", "source": "工程情景：普通单目标、起始无元素底"}, "profiles": []}}
    sources = []
    for tag, group, start in (("a", "default", 0), ("b", "Diluc", 2)):
        pid, aid = f"profile.{tag}", f"cast.{tag}"
        rotation["actions"].append({"id": aid, "ability_id": "probe.skill", "start": start,
                                    "occupation_end": start + 1, "order": 0, "repeat": {"mode": "once"}, **evidence})
        mechanics["elemental_model"]["profiles"].append({"id": pid, "element": "hydro", "application": "apply",
                "gauge_units": 1, "icd": {"kind": "unknown"}, **evidence})
        sources.append({"profile_id": pid, "version_line": "release", "attenuation_group": group,
                        "shared_group": f"engineering-independent-{tag}", "source": evidence["source"],
                        "expected_snapshot_id": None, "expected_node_sha256": None})
        for i in range(1, 8):
            hid = f"hit.{tag}.{i}"
            template["hits"].append({"id": hid, "character": "probe", "skill": "工程序列命中", "hit_name": f"规则{tag.upper()}·第{i}次命中",
                                     "element": "hydro", "t": round(start + i / 10, 6), "multiplier": 1,
                                     "damage_system": "classic", "damage_type_ids": ["damage.elemental_skill"]})
            mechanics["hit_events"].append({"hit_id": hid, "action_id": aid, "order": 10,
                 "application": {"profile_id": pid, "target_id": "target", "landed": True}})
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics, elemental_sources=sources)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-exe", type=Path, help="Optional built GenshinCalc.exe; no GUI window is opened")
    args = parser.parse_args()
    output = ROOT / "outputs/engine-continuation-20261002/ec02/delivery"
    output.mkdir(parents=True, exist_ok=True)
    db = ROOT / "data/genshin.db"
    before_db = digest(db)
    checks = []

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        if not value:
            raise AssertionError(name)

    plan = make_plan()
    write(output / "ICD序列-未确认来源.json", plan)
    unconfirmed = compose_equipment_plan(plan, db)
    check("unconfirmed source is pending", unconfirmed["state"] == "pending" and
          any(x["code"] == "source_changed" for x in unconfirmed["missing_inputs"]))
    write(output / "unconfirmed-report.json", unconfirmed)
    with equipment_read_session(db):
        for binding in plan["elemental_sources"]:
            facts = query_runtime(db, {"action": "attenuation_profile", "group": binding["attenuation_group"]})
            check(binding["attenuation_group"] + " finite profile supported", facts["execution"]["status"] == "ready")
            binding.update(expected_snapshot_id=facts["snapshot"]["snapshot_id"], expected_node_sha256=facts["source"]["node_sha256"])
            write(output / ("source-" + binding["attenuation_group"] + ".json"), facts)

    before_input = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, db)
    check("confirmed source composes", report["state"] == "ready")
    check("native placeholder unchanged", plan == before_input and all(p["icd"] == {"kind": "unknown"} for p in report["plan"]["mechanics_model"]["elemental_model"]["profiles"]))
    check("working projection owns compiled sequence", all(p["icd"]["kind"] == "sequence" for p in report["mechanics_model"]["elemental_model"]["profiles"]))
    check("shared identity remains explicit", [x["compiled_icd"]["group"] for x in report["elemental_compilations"]] == ["engineering-independent-a", "engineering-independent-b"])
    poisoned = copy.deepcopy(report["plan"])
    poisoned["resolution"]["elemental_compilations"][0]["compiled_icd"]["sequence"] = [0]
    poisoned["resolved_template"]["hits"][0]["multiplier"] = 999
    fresh = compose_equipment_plan(poisoned, db)
    check("cached compilation is recomputed", fresh["elemental_compilations"][0]["compiled_icd"]["sequence"] == [1, 0, 0] * 8)
    check("cached damage template ignored", fresh["plan"]["resolved_template"]["hits"][0]["multiplier"] == 1)

    stale = copy.deepcopy(plan)
    stale["elemental_sources"][0]["expected_node_sha256"] = "0" * 64
    write(output / "ICD序列-错误来源确认.json", stale)
    changed = compose_equipment_plan(stale, db)
    check("changed expected node rejects execution", changed["state"] == "pending" and any(x["code"] == "source_changed" for x in changed["missing_inputs"]))
    write(output / "changed-source-report.json", changed)
    try:
        run_plan_calc(stale, db)
    except PlanError as error:
        check("stale source cannot calculate", error.reason == "missing_input")
    else:
        check("stale source cannot calculate", False)

    calculated = run_plan_calc(plan, db)
    actual = [row["expected"] for row in calculated["result"]["hits"]]
    check("independent damage 14 x 450", actual == [450.0] * 14)
    trace = calculated["result"]["elemental"]["trace"]
    expected_sequences = {"profile.a": [True, False, False, True, False, False, True],
                          "profile.b": [True, False, False, False, False, True, False]}
    for pid, expected in expected_sequences.items():
        check(pid + " actual application sequence", [row["allowed"] for row in trace if row["profile_id"] == pid] == expected)
    check("source receipts include confirmed hashes", len(calculated["result_receipt"]["elemental_sources"]) == 2)
    check("once actions do not prove recurrence", calculated["result"]["rotation_check"]["energy_check"]["proven_cycle"] is None)
    plan_path = output / "ICD序列-原生方案.json"
    write(plan_path, calculated["plan"])
    write(output / "ICD序列-计算结果.json", calculated)
    reloaded = read_plan(plan_path)["plan"]
    check("saved native retains unknown placeholders", all(p["icd"] == {"kind": "unknown"} for p in reloaded["mechanics_model"]["elemental_model"]["profiles"]))
    gui = GuiApi(ROOT / "data", plans_dir=output / "gui-plans")
    gui_load = gui.load_plan(str(plan_path))
    check("GUI bridge native reload ready", gui_load["code"] == 0 and gui_load["data"]["report"]["state"] == "ready")
    gui_calc = gui.run_plan_calc(reloaded)
    check("GUI bridge shares same result", gui_calc["code"] == 0 and gui_calc["data"]["result"] == calculated["result"])
    check("GUI bridge rejects stale source", gui.run_plan_calc(stale)["code"] == 1)

    workbook = build_formula_workbook(calculated)
    workbook_path = output / "ICD序列-公式核对.xlsx"
    workbook_path.write_bytes(workbook)
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        cells = {cell.attrib["r"]: cell for cell in ET.fromstring(archive.read("xl/worksheets/sheet3.xml")).findall(".//s:c", ns)}
        check("workbook cached independent values", [float(cells[f"V{i}"].find("s:v", ns).text) for i in range(5, 19)] == [450.0] * 14)
        check("workbook contains damage formulas", all(cells[f"V{i}"].find("s:f", ns) is not None for i in range(5, 19)))
    support = formula_workbook_support(calculated)
    check("workbook source changes require rerun", support["editable"]["elemental_profiles_initial_aura_icd_reactions"] == "regenerate_required")

    if args.cli_exe:
        command = [str(args.cli_exe.resolve()), "--data-dir", str(ROOT / "data"), "equipment", "calc", "--plan", str(plan_path),
                   "--output", str(output / "package-result.json"), "--summary", "--overwrite"]
        process = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf8", timeout=120, check=False)
        (output / "package.stdout.json").write_text(process.stdout, encoding="utf8")
        (output / "package.stderr.json").write_text(process.stderr, encoding="utf8")
        check("optional packaged CLI success", process.returncode == 0)
        check("optional packaged CLI bounded stdout", len(process.stdout.encode("utf8")) < 4000)
        package = json.loads((output / "package-result.json").read_text("utf8"))
        check("optional packaged CLI independent damage", [h["expected"] for h in package["result"]["hits"]] == [450.0] * 14)
        check("packaged source compilation equals source runtime", package["result"]["elemental"]["source_compilations"] == calculated["result"]["elemental"]["source_compilations"])
        for name in ("ICD序列-未确认来源", "ICD序列-错误来源确认"):
            rejected_path = output / (name+"-不得生成结果.json")
            proc = subprocess.run([str(args.cli_exe.resolve()), "--data-dir", str(ROOT / "data"), "equipment", "calc",
                "--plan", str(output / (name+".json")), "--output", str(rejected_path), "--summary"],
                cwd=ROOT, capture_output=True, encoding="utf8", timeout=120, check=False)
            (output / (name+"-package.stderr.json")).write_text(proc.stderr, encoding="utf8")
            check(name+" packaged refusal", proc.returncode != 0 and not rejected_path.exists())
        packed_book = output / "package-formulas.xlsx"
        proc = subprocess.run([str(args.cli_exe.resolve()), "--data-dir", str(ROOT / "data"), "equipment", "workbook",
            "--plan", str(plan_path), "--output", str(packed_book), "--overwrite"], cwd=ROOT,
            capture_output=True, encoding="utf8", timeout=120, check=False)
        check("packaged source-bound workbook succeeds", proc.returncode == 0 and packed_book.is_file())
        with zipfile.ZipFile(packed_book) as archive:
            cells = {cell.attrib["r"]: cell for cell in ET.fromstring(archive.read("xl/worksheets/sheet3.xml")).findall(".//s:c", ns)}
            check("packaged source-bound formulas and values", all(float(cells[f"V{i}"].find("s:v", ns).text) == 450
                 and cells[f"V{i}"].find("s:f", ns) is not None for i in range(5, 19)))
    check("formal database unchanged", digest(db) == before_db)
    summary = {"status": "passed", "checks": checks, "passed": len(checks), "database_sha256": before_db,
               "expected_application_sequences": expected_sequences, "expected_damage": [450.0] * 14,
               "workbook_support": support, "xlsx_sha256": digest(workbook_path),
               "scope": "真实default/Diluc规则模板投影与显式合成档案；不证明技能GU、共享组或时点映射，不是真实队伍DPS",
               "not_verified": ["前台GUI交互", "Excel桌面重算", "游戏客户端", "任意攻击自动映射"],
               "cli_exe": str(args.cli_exe.resolve()) if args.cli_exe else None}
    write(output / "verification.json", summary)
    print(json.dumps({"status": "passed", "passed": len(checks), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
