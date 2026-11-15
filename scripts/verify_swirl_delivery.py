"""Verify real database ordinary-swirl sources across native plans, CLI, GUI bridge and XLSX.

This is a normalized engineering scenario, not a character kit or team DPS claim.
The selected database is read only. All artifacts must stay in the EC04 directory.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EC04 = ROOT / "outputs/engine-continuation-20261002/ec04"
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_reaction_damage import get_runtime_reaction_damage_profile

EXPECTED_SWIRL = 1446.85 * .6 * .9  # Level90, EM0, ordinary swirl, resistance10%.
EXPECTED_DAMAGE = [45., EXPECTED_SWIRL, 45., EXPECTED_SWIRL, 45.]
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
QUERY_NAME = "genshin_db.runtime_reaction_damage.get_runtime_reaction_damage_profile"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def same_numbers(actual, expected):
    return len(actual) == len(expected) and all(math.isclose(a, e, rel_tol=0, abs_tol=1e-9)
                                               for a, e in zip(actual, expected, strict=True))


def make_plan():
    evidence = {"status": "assumed", "source": "归一工程情景：明确普通单目标、固定EM0、无Alter/Star；动作和附着时点为输入，非真实角色完整机制或队伍DPS"}
    template = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
                "enemy": {"level": 90, "resistance": {"default": .1}},
                "meta": {"name": "普通扩散·真实来源工程验收", "assumptions": [evidence["source"],
                    "90级EM0，敌人10%抗性；独立扩散期望1446.85×0.6×0.9=781.299；非真实队伍伤害基线"]},
                "characters": [{"id": "wind", "name": "工程风提供者", "level": 90,
                    "stats": {"atk": 100, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}}],
                "buffs": [], "switches": [], "loop_duration": 2,
                "hits": [{"id": f"wind.hit{i}", "character": "wind", "skill": "工程风攻击", "hit_name": f"工程风命中{i+1}",
                    "element": "anemo", "t": time, "multiplier": 1, "scaling_stat": "atk", "damage_system": "classic",
                    "damage_type_ids": ["damage.elemental_skill"]} for i, time in enumerate((.2, .3, .4))]}
    rotation = {"schema_version": 1, "period": 2, "max_cycles": 3, "initial_frontline": "wind",
                "characters": [{"id": "wind", "element": "anemo", "energy_recharge": 1, "energy_capacity": 60,
                    "initial_energy": 60, **evidence}],
                "abilities": [{"id": "wind.e", "character": "wind", "action_type_id": "action.elemental_skill",
                    "cooldown": None, "energy": None, **evidence}],
                "actions": [{"id": "wind.cast", "ability_id": "wind.e", "start": 0, "occupation_end": .1, "order": 0,
                    "repeat": {"mode": "once"}, **evidence}], "events": [], "initial_cooldowns": []}
    mechanics = {"schema_version": 1, "rules": [], "hit_events": [
        {"hit_id": hit["id"], "action_id": "wind.cast", "order": 10+i,
         "application": {"profile_id": "wind", "target_id": "enemy", "landed": True}}
        for i, hit in enumerate(template["hits"])],
        "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []},
        "elemental_model": {"schema_version": 1, "ruleset": "gauge-swirl-v2",
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", **evidence,
                "initial_aura": {"element": "cryo", "remaining_gu": 2.4, "decay_gu_per_second": .001, "source": "工程明确冰底"}},
            "profiles": [{"id": "wind", "element": "anemo", "application": "apply", "gauge_units": 1,
                          "icd": {"kind": "none"}, **evidence}],
            "swirl_damage": {"icd": {"kind": "unknown"}, **evidence}}}
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics, reaction_damage_sources=[
        {"reaction_type": "swirl", "version_line": "release", "source": evidence["source"],
         "expected_snapshot_id": None, "expected_profile_fingerprint": None}])
    return plan


def native_unknown(plan):
    return plan["mechanics_model"]["elemental_model"]["swirl_damage"]["icd"] == {"kind": "unknown"}


def verify_workbook(content, expected, check, label):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        cells = {c.attrib["r"]: c for c in sheet.findall(".//s:c", NS)}
        check(label + " formula cached values match independent damage", same_numbers([
            float(cells[f"V{i}"].find("s:v", NS).text) for i in range(5, 5+len(expected))], expected))
        check(label + " damage cells retain formulas", all(cells[f"V{i}"].find("s:f", NS) is not None for i in range(5, 5+len(expected))))
        check(label + " no cached formula errors", not any(c.attrib.get("t") == "e"
            for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            for c in ET.fromstring(archive.read(name)).findall(".//s:c", NS)))


def rejected_calculation(plan, db, check, label):
    try:
        run_plan_calc(plan, db)
    except PlanError as exc:
        check(label + " calculation refuses publication", True)
        return {"reason": exc.reason, "message": str(exc), "details": exc.details}
    check(label + " calculation refuses publication", False)


def verify(args, check):
    data, output, db = args.data_dir, args.output_dir, args.data_dir / "genshin.db"
    plan = make_plan()
    unconfirmed = copy.deepcopy(plan)
    write(output / "普通扩散-未确认.json", plan)
    preview = compose_equipment_plan(plan, db)
    write(output / "未确认-资料回执.json", preview)
    check("unconfirmed source remains pending", preview["state"] == "pending")
    check("preview never fills native ICD", native_unknown(plan) and native_unknown(preview["plan"]))
    record = preview["reaction_damage_compilations"][0]
    with equipment_read_session(db):
        profiles = [get_runtime_reaction_damage_profile(db, reaction_type="swirl", element=element, version_line="release")
                    for element in ("hydro", "cryo")]
    write(output / "数据库-普通冰水扩散资料.json", profiles)
    for element, profile in zip(("hydro", "cryo"), profiles, strict=True):
        check(element + " damage sequence ready", profile["execution"]["sequence_status"] == "ready")
        check(element + " general ability remains unimplemented", profile["execution"]["ability_execution"] == "pending")
        check(element + " exact ordinary damage sequence", profile["projection"]["damage_sequence"] == [1, 1]+[0]*8
              and profile["projection"]["reset_cycle_seconds_rounded"] == .5)
    check("both real sources share one snapshot", len({p["snapshot"]["snapshot_id"] for p in profiles}) == 1)
    plan["reaction_damage_sources"][0].update(record["required_confirmation"])
    original = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, db)
    write(output / "已确认-资料回执.json", report)
    check("reviewed ordinary source composes ready", report["state"] == "ready")
    check("native source plan remains immutable and unknown", plan == original and native_unknown(report["plan"]))
    with patch(QUERY_NAME, wraps=get_runtime_reaction_damage_profile) as fresh_queries:
        calculated = run_plan_calc(plan, db)
        poisoned = copy.deepcopy(calculated["plan"])
        poisoned["resolved_template"]["hits"][0]["multiplier"] = 999999
        poisoned["resolution"]["reaction_damage_compilations"][0]["compiled_icd"]["sequence"] = [0]
        replay = run_plan_calc(poisoned, db)
        check("each calculation freshly queries both database sources", fresh_queries.call_count == 4)
    check("cached projection cannot override fresh sources", replay["result"] == calculated["result"])
    check("independent arithmetic matches all five damage rows", same_numbers([h["expected"] for h in calculated["result"]["hits"]], EXPECTED_DAMAGE))
    attempts = [row for row in calculated["result"]["elemental"]["trace"] if row["type"] == "reaction_damage_attempt"]
    check("two damage attempts allowed and third suppressed", [row["allowed"] for row in attempts] == [True, True, False])
    check("all three reactions still consume aura", len([row for row in calculated["result"]["elemental"]["trace"]
          if row["type"] == "elemental_application" and row["reaction"]]) == 3)
    check("suppression shares fixed actor target reaction element identity", len({tuple(row["shared_key"]) for row in attempts}) == 1)
    check("once actions were not copied into later input damage", calculated["plan"]["rotation_model"] == original["rotation_model"]
          and calculated["result"]["rotation_check"]["energy_check"]["proven_cycle"] is None)
    check("native unknown survives calculation", native_unknown(calculated["plan"]))
    comp = calculated["result"]["elemental"]["reaction_damage_source_compilations"][0]
    check("result receipt binds source compilation", calculated["result_receipt"]["reaction_damage_sources"][0]["compilation_fingerprint"] == comp["compilation_fingerprint"])
    plan_path = output / "普通扩散-原生方案.json"
    write(plan_path, calculated["plan"])
    write(output / "普通扩散-完整计算结果.json", calculated)
    changed_confirmation = copy.deepcopy(plan)
    changed_confirmation["reaction_damage_sources"][0]["expected_profile_fingerprint"] = "0"*64
    write(output / "普通扩散-过期来源确认.json", changed_confirmation)
    for label, rejected in (("未确认", unconfirmed), ("过期来源确认", changed_confirmation)):
        pending = compose_equipment_plan(rejected, db)
        check(label + " fresh real source refuses stale confirmation", pending["state"] == "pending")
        write(output / (label + "-拒绝计算.json"), rejected_calculation(rejected, db, check, label))
    # Controlled negative: alter only a copied fresh query response. Never edit the database.
    def changed_source(*a, **kw):
        result = get_runtime_reaction_damage_profile(*a, **kw)
        if kw["element"] == "cryo":
            result["content_fingerprint"] = "controlled-review-source-change"
        return result
    with patch(QUERY_NAME, side_effect=changed_source):
        changed_report = compose_equipment_plan(calculated["plan"], db)
        check("fresh changed source invalidates saved cached report", changed_report["state"] == "pending"
              and "source_changed" in json.dumps(changed_report))
        write(output / "受控新来源变化-拒绝回执.json", {"test_method": "真实只读查询后，仅修改返回副本content_fingerprint；未改库", "report": changed_report,
              "calculation": rejected_calculation(calculated["plan"], db, check, "controlled fresh source change")})
    nohit = copy.deepcopy(plan)
    nohit["manual_template"]["hits"] = []
    nohit["mechanics_model"]["hit_events"] = []
    nohit["mechanics_model"]["application_events"] = [{"id": "wind.contact", "action_id": "wind.cast", "t": .2, "order": 10,
        "application": {"profile_id": "wind", "target_id": "enemy", "landed": True}}]
    nohit_result = run_plan_calc(nohit, db)
    check("nondamage contact generates exactly one independent swirl", same_numbers([h["expected"] for h in nohit_result["result"]["hits"]], [EXPECTED_SWIRL]))
    check("nondamage native never gains dummy base hits", not nohit_result["plan"]["manual_template"]["hits"] and native_unknown(nohit_result["plan"]))
    nohit_path = output / "无伤害附着-原生方案.json"
    write(nohit_path, nohit_result["plan"])
    write(output / "无伤害附着-完整计算结果.json", nohit_result)
    empty = copy.deepcopy(nohit)
    empty["mechanics_model"]["elemental_model"]["target"]["initial_aura"] = None
    write(output / "无底风附着-不得发布数字.json", empty)
    empty_rejection = rejected_calculation(empty, db, check, "empty aura no generated damage")
    check("empty input does not publish a zero damage answer", "no_reported_damage" in json.dumps(empty_rejection))
    write(output / "无底风附着-拒绝回执.json", empty_rejection)
    gui = GuiApi(data, plans_dir=output / "gui-plans")
    for label, value in (("普通扩散", calculated), ("无伤害附着", nohit_result)):
        saved_path = output / (label + "-GUI保存重开.json")
        saved = gui.save_plan(str(saved_path), value["plan"], digest(saved_path) if saved_path.exists() else None)
        check(label + " GUI bridge saves native", saved["code"] == 0)
        loaded = gui.load_plan(str(saved_path))
        check(label + " GUI bridge reload composes ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
        restored = read_plan(saved_path)["plan"]
        check(label + " file stores unknown native ICD", native_unknown(restored))
        gui_result = gui.run_plan_calc(restored)
        check(label + " GUI bridge calculates same result", gui_result["code"] == 0 and gui_result["data"]["result"] == value["result"])
        write(output / (label + "-GUI计算回执.json"), gui_result)
        workbook = build_formula_workbook(value)
        (output / (label + "-公式核对.xlsx")).write_bytes(workbook)
        verify_workbook(workbook, EXPECTED_DAMAGE if label == "普通扩散" else [EXPECTED_SWIRL], check, label)
    support = formula_workbook_support(calculated)
    check("Excel explicitly requires regeneration for source structure changes", support["editable"]["database_reaction_damage"] == "regenerate_required")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")+os.pathsep+os.environ.get("PYTHONPATH", "")}
    commands = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        commands.append(("package-cli", [str(args.cli_exe)]))
    for label, command in commands:
        def execute(arguments, command=command):
            return subprocess.run([*command, "--data-dir", str(data), *arguments], cwd=ROOT, env=env,
                capture_output=True, encoding="utf8", timeout=180, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for variant, path, expected in (("普通扩散", plan_path, calculated), ("无伤害附着", nohit_path, nohit_result)):
            result_path = output / (label + "-" + variant + "-结果.json")
            proc = execute(["equipment", "calc", "--plan", str(path), "--output", str(result_path), "--summary", "--overwrite"])
            (output / (label + "-" + variant + ".stdout.json")).write_text(proc.stdout, encoding="utf8")
            (output / (label + "-" + variant + ".stderr.json")).write_text(proc.stderr, encoding="utf8")
            check(label + " " + variant + " calc succeeds with bounded stdout", proc.returncode == 0 and len(proc.stdout.encode("utf8")) < 8000)
            result = json.loads(result_path.read_text("utf8"))
            check(label + " " + variant + " source receipts and damage equal in-process", result["result"] == expected["result"]
                  and result["result_receipt"]["reaction_damage_sources"] == expected["result_receipt"]["reaction_damage_sources"])
        for variant, filename in (("未确认", "普通扩散-未确认.json"), ("过期来源", "普通扩散-过期来源确认.json"), ("无底无伤害", "无底风附着-不得发布数字.json")):
            rejected_path = output / (label + "-" + variant + "-不得生成结果.json")
            check(label + " rejection output starts absent: " + variant, not rejected_path.exists())
            proc = execute(["equipment", "calc", "--plan", str(output / filename), "--output", str(rejected_path), "--summary"])
            (output / (label + "-" + variant + ".stderr.json")).write_text(proc.stderr, encoding="utf8")
            check(label + " refuses and creates no result: " + variant, proc.returncode != 0 and not rejected_path.exists())
        workbook_path = output / (label + "-公式核对.xlsx")
        proc = execute(["equipment", "workbook", "--plan", str(plan_path), "--output", str(workbook_path), "--overwrite"])
        (output / (label + "-workbook.stdout.json")).write_text(proc.stdout, encoding="utf8")
        (output / (label + "-workbook.stderr.json")).write_text(proc.stderr, encoding="utf8")
        check(label + " workbook export succeeds", proc.returncode == 0 and workbook_path.is_file())
        verify_workbook(workbook_path.read_bytes(), EXPECTED_DAMAGE, check, label)
    return {"native_plan": str(plan_path), "nondamage_native_plan": str(nohit_path), "formula_workbook": str(output / "普通扩散-公式核对.xlsx"),
            "expected_swirl_damage": EXPECTED_SWIRL, "expected_damage": EXPECTED_DAMAGE, "damage_sequence": [True, True, False],
            "workbook_support": support, "source_snapshots": [p["snapshot"] for p in profiles],
            "scope": "真实DB普通冰水原目标扩散伤害参数的归一工程例；明确输入GU/动作/时点/固定EM，非真实角色或队伍DPS",
            "not_verified": ["前台GUI鼠标交互", "Excel桌面重算", "游戏客户端", "完整角色技能自动派发", "Alter/Star/传播/多目标"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=EC04 / "delivery-source")
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if not args.output_dir.is_relative_to(EC04.resolve()) or args.output_dir == EC04.resolve():
        parser.error("--output-dir必须是outputs/engine-continuation-20261002/ec04下的独立子目录；不可覆盖冻结批次")
    if not (args.data_dir / "genshin.db").is_file():
        parser.error("--data-dir中缺少genshin.db")
    if args.cli_exe:
        args.cli_exe = args.cli_exe.resolve()
        if not args.cli_exe.is_file():
            parser.error("--cli-exe不存在")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    db, checks = args.data_dir / "genshin.db", []
    before = digest(db)
    summary = {"status": "running", "data_dir": str(args.data_dir), "database_sha256_before": before,
               "cli_exe": str(args.cli_exe) if args.cli_exe else None, "checks": checks}
    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)
    try:
        summary.update(verify(args, check))
        check("selected database bytes unchanged", digest(db) == before)
        summary.update(status="passed", passed=len(checks))
    except Exception as exc:
        summary.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)}, passed=sum(c["passed"] for c in checks))
        raise
    finally:
        summary["database_sha256_after"] = digest(db)
        write(args.output_dir / "verification.json", summary)
    print(json.dumps({"status": "passed", "passed": len(checks), "expected_swirl_damage": EXPECTED_SWIRL,
                      "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
