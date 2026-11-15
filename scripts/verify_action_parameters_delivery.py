"""Real Ayaka/Kazuha E parameter delivery through App, GUI bridge and CLI.

This normalized engineering scenario fixes ATK=1000, level 90 versus level 90,
zero crit, 10% resistance, no buffs or reactions. It is not a team DPS claim.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
from genshin_db.runtime_queries import query_runtime

ACTORS = (("ayaka", "神里绫华", 10000002, "cryo", 4.305600166320801),
          ("kazuha", "枫原万叶", 10000047, "anemo", 3.4560000896453857))
EXPECTED = [450 * actor[4] for actor in ACTORS]
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def same_numbers(actual, expected=EXPECTED):
    return len(actual) == len(expected) and all(math.isclose(a, e, rel_tol=0, abs_tol=1e-9)
                                               for a, e in zip(actual, expected, strict=True))


def make_plan():
    evidence = {"status": "assumed", "source": "归一工程情景：显式给定动作和命中时点、一次释放；不代表真实动画时长、完整技能机制或队伍DPS"}
    template = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
                "enemy": {"level": 90, "resistance": {"default": .1}},
                "meta": {"name": "数据库技能参数·双角色工程核对", "assumptions": [evidence["source"],
                    "90级攻击1000、0暴击、敌人90级10%抗性，无增益和反应；每次期望伤害=450×所选数据库倍率"]},
                "characters": [{"id": cid, "name": name, "level": 90,
                    "stats": {"atk": 1000, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}}
                    for cid, name, _, _, _ in ACTORS], "buffs": [], "switches": [], "loop_duration": 5,
                "hits": [{"id": cid + ".hit", "character": cid, "skill": "元素战技", "hit_name": name + "·战技参数工程命中",
                    "element": element, "t": .2 if cid == "ayaka" else 1.3, "scaling_stat": "atk", "damage_system": "classic",
                    "damage_type_ids": ["damage.elemental_skill"]} for cid, name, _, element, _ in ACTORS]}
    rotation = {"schema_version": 1, "period": 5, "max_cycles": 3, "initial_frontline": "ayaka",
                "characters": [{"id": cid, "element": element, "energy_recharge": 1, "energy_capacity": 60,
                    "initial_energy": 60, **evidence} for cid, _, _, element, _ in ACTORS],
                "abilities": [{"id": cid + ".e", "character": cid, "action_type_id": "action.elemental_skill",
                    "cooldown": None, "energy": None, **evidence} for cid, *_ in ACTORS],
                "actions": [], "events": [], "initial_cooldowns": []}
    rotation["abilities"].append({"id": "switch", "character": "ayaka", "action_type_id": "action.switch",
                                   "cooldown": None, "energy": None, **evidence})
    for aid, ability, time, end in (("ayaka.cast", "ayaka.e", 0, .5), ("switch", "switch", 1, 1), ("kazuha.cast", "kazuha.e", 1.1, 1.6)):
        row = {"id": aid, "ability_id": ability, "start": time, "occupation_end": end, "order": 0,
               "repeat": {"mode": "once"}, **evidence}
        if aid == "switch":
            row["switch_to"] = "kazuha"
        rotation["actions"].append(row)
    mechanics = {"schema_version": 1, "rules": [], "hit_events": [
        {"hit_id": cid + ".hit", "action_id": cid + ".cast", "order": 10} for cid, *_ in ACTORS],
        "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []}}
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics, hit_parameter_sources=[
        {"hit_id": cid + ".hit", "version_line": "release", "selection": {"character_id": dbid, "slot": "skill", "constellation": 0, "talent_level": 10},
         "param_index": 0, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", "status": "assumed",
         "source": f"工程用途待核对：{name}元素战技param1，选择为单攻击力伤害倍率；来源标量不自动证明完整攻击公式",
         "expected_snapshot_id": None, "expected_node_sha256": None} for cid, name, dbid, _, _ in ACTORS])
    return plan


def verify_workbook(content, check, label):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        events = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        cells = {c.attrib["r"]: c for c in events.findall(".//s:c", NS)}
        check(label + " formula damage matches independent expectation", same_numbers([
            float(cells[f"V{i}"].find("s:v", NS).text) for i in (5, 6)]))
        check(label + " damage cells contain formulas", all(cells[f"V{i}"].find("s:f", NS) is not None for i in (5, 6)))
        inputs = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        selected = []
        for row in inputs.findall(".//s:row", NS):
            by_col = {re.sub(r"\d+", "", c.attrib["r"]): c for c in row}
            key = "".join(by_col["A"].itertext()) if "A" in by_col else ""
            if key.startswith("hits[") and key.endswith(".multiplier"):
                selected.append((row.attrib["r"], by_col))
        check(label + " database multipliers are fixed gray cells", len(selected) == 2 and all(c["C"].attrib.get("s") == "5" for _, c in selected))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        style = styles.find("s:cellXfs", NS)[5]
        fill = styles.find("s:fills", NS)[int(style.attrib["fillId"])]
        color = fill.find("s:patternFill/s:fgColor", NS).attrib["rgb"][-6:]
        check(label + " fixed style uses the existing gray palette", color == "F0F2F5")
        check(label + " multiplier changes invalidate fixed structure", all(
            c["I"].find("s:f", NS).text == f"IF(C{r}<>D{r},1,0)" for r, c in selected))
        check(label + " no cached formula errors", not any(
            c.attrib.get("t") == "e" for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            for c in ET.fromstring(archive.read(name)).findall(".//s:c", NS)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/engine-continuation-20261002/ec03/delivery")
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    data, output = args.data_dir.resolve(), args.output_dir.resolve()
    allowed = (ROOT / "outputs").resolve()
    if not output.is_relative_to(allowed) or output == allowed:
        parser.error("--output-dir必须是项目outputs下的独立子目录")
    frozen = ROOT / "outputs/engine-continuation-20261002"
    if output.is_relative_to(frozen) and not output.is_relative_to(frozen / "ec03"):
        parser.error("不能写入EC01/EC02冻结材料；本批应使用ec03子目录")
    output.mkdir(parents=True, exist_ok=True)
    db = data / "genshin.db"
    before_db, checks = digest(db), []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)

    plan = make_plan()
    unconfirmed = copy.deepcopy(plan)
    write(output / "倍率参数-未确认.json", unconfirmed)
    preview = compose_equipment_plan(plan, db)
    check("unconfirmed sources remain pending", preview["state"] == "pending")
    write(output / "unconfirmed-report.json", preview)
    descriptions = []
    with equipment_read_session(db):
        for binding, (cid, name, _, _, expected) in zip(plan["hit_parameter_sources"], ACTORS, strict=True):
            request = {"action": "action_parameter", "param_index": 0, "version_line": "release", **binding["selection"]}
            facts = query_runtime(db, request)
            write(output / ("query-" + cid + ".json"), {"request": request, "response": facts})
            check(cid + " selected scalar is exact level10 input", facts["scalar_projection"]["status"] == "ready" and facts["scalar_projection"]["value"] == expected)
            matched = facts["parameter_descriptions"]["matched"]
            check(cid + " Chinese source describes selected parameter", bool(matched) and all(0 in row["param_indices"] and re.search(r"[\u4e00-\u9fff]", row["text"]) for row in matched))
            expected_label = "技能伤害" if cid == "ayaka" else "点按技能伤害"
            check(cid + " selected Chinese damage label reviewed", any(row["text"].split("|", 1)[0] == expected_label for row in matched))
            quote = "；".join(row["text"] for row in matched)
            binding["source"] = f"已核对同快照中文参数原文：{quote}。本工程将{name}战技param1用作单攻击力伤害倍率；用途是显式假设，不证明完整技能逻辑。"
            binding.update(expected_snapshot_id=facts["snapshot"]["snapshot_id"], expected_node_sha256=facts["source"]["node_sha256"])
            descriptions.append({"character": name, "raw_descriptions": matched, "declared_purpose": binding["source"]})
    write(output / "中文原文与用途核对.json", descriptions)
    original = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, db)
    check("confirmed scalar sources compose ready", report["state"] == "ready")
    check("native inputs are immutable", plan == original and all("multiplier" not in h for h in report["plan"]["manual_template"]["hits"]))
    check("source ready does not imply action dispatch", all(row["action_dispatch"]["status"] == "pending" and row["operation_execution"] == "not_evaluated" for row in report["hit_parameter_compilations"]))
    poisoned = copy.deepcopy(report["plan"])
    poisoned["resolved_template"]["hits"][0]["multiplier"] = 999999
    poisoned["resolution"]["hit_parameter_compilations"][0]["multiplier"] = 999999
    fresh = run_plan_calc(poisoned, db)
    calculated = run_plan_calc(plan, db)
    check("poisoned cached resolved data is ignored", fresh["result"] == calculated["result"])
    check("damage equals independent 450 times each multiplier", same_numbers([h["expected"] for h in calculated["result"]["hits"]]))
    check("input actions retain exact order timing and once mode", calculated["plan"]["rotation_model"] == original["rotation_model"])
    check("native hits never receive compiled multiplier", all("multiplier" not in h for h in calculated["plan"]["manual_template"]["hits"]))
    check("no inferred reaction layer", "elemental_model" not in calculated["plan"]["mechanics_model"])
    check("once-only engineering does not prove recurrence", calculated["result"]["rotation_check"]["energy_check"]["proven_cycle"] is None)
    check("both selected source receipts are delivered", len(calculated["result_receipt"]["hit_parameter_sources"]) == 2)
    manual = copy.deepcopy(original)
    manual.pop("hit_parameter_sources")
    for hit, actor in zip(manual["manual_template"]["hits"], ACTORS, strict=True):
        hit["multiplier"] = actor[4]
    manual_result = run_plan_calc(manual, db)
    check("previous manual-scalar path has identical hit arithmetic", manual_result["result"]["hits"] == calculated["result"]["hits"])
    check("manual baseline has identical frozen actions", manual["rotation_model"] == original["rotation_model"])
    manual_path = output / "倍率参数-旧手填基线.json"
    write(manual_path, manual)
    write(output / "倍率参数-旧手填基线结果.json", manual_result)
    plan_path = output / "倍率参数-原生方案.json"
    write(plan_path, calculated["plan"])
    write(output / "倍率参数-计算结果.json", calculated)
    rejects = {"未确认": unconfirmed, "错误节点确认": copy.deepcopy(plan), "用途待核对": copy.deepcopy(plan), "不支持用途": copy.deepcopy(plan)}
    rejects["错误节点确认"]["hit_parameter_sources"][0]["expected_node_sha256"] = "0" * 64
    rejects["用途待核对"]["hit_parameter_sources"][0]["status"] = "pending"
    rejects["不支持用途"]["hit_parameter_sources"][0]["purpose"] = "healing_ratio"
    for label, rejected in rejects.items():
        write(output / ("倍率参数-" + label + ".json"), rejected)
        try:
            rejected_report = compose_equipment_plan(rejected, db)
        except PlanError as exc:
            check(label + " invalid purpose rejected by schema", label == "不支持用途")
            rejected_report = {"state": "rejected", "reason": exc.reason, "message": str(exc)}
        else:
            check(label + " pending", rejected_report["state"] == "pending")
        write(output / (label + "-rejected-report.json"), rejected_report)
        try:
            run_plan_calc(rejected, db)
        except PlanError:
            check(label + " cannot calculate", True)
        else:
            check(label + " cannot calculate", False)
    gui = GuiApi(data, plans_dir=output / "gui-plans")
    saved_path = output / "倍率参数-GUI保存重开.json"
    saved = gui.save_plan(str(saved_path), calculated["plan"], digest(saved_path) if saved_path.exists() else None)
    check("GUI bridge saves native plan", saved["code"] == 0)
    loaded = gui.load_plan(str(saved_path))
    check("GUI bridge reload composes ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
    restored = read_plan(saved_path)["plan"]
    replay = gui.run_plan_calc(restored)
    check("GUI bridge reload calculates same result", replay["code"] == 0 and replay["data"]["result"] == calculated["result"])
    check("GUI saved native retains omitted multipliers", all("multiplier" not in h for h in restored["manual_template"]["hits"]))
    book = build_formula_workbook(calculated)
    (output / "倍率参数-公式核对.xlsx").write_bytes(book)
    verify_workbook(book, check, "source workbook")
    support = formula_workbook_support(calculated)
    check("workbook explicitly limits database multiplier edits", support["editable"]["hit_multiplier"] == "manual_only" and support["editable"]["database_hit_parameters"] == "regenerate_required")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    commands = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        commands.append(("package-cli", [str(args.cli_exe.resolve())]))
    for label, command in commands:
        def execute(arguments, command=command):
            return subprocess.run([*command, "--data-dir", str(data), *arguments], cwd=ROOT, env=env,
                                  capture_output=True, encoding="utf8", timeout=120, check=False)
        result_path = output / (label + "-result.json")
        proc = execute(["equipment", "calc", "--plan", str(plan_path), "--output", str(result_path), "--summary", "--overwrite"])
        (output / (label + ".stdout.json")).write_text(proc.stdout, encoding="utf8")
        (output / (label + ".stderr.json")).write_text(proc.stderr, encoding="utf8")
        check(label + " calculation succeeds with bounded stdout", proc.returncode == 0 and len(proc.stdout.encode("utf8")) < 8000)
        result = json.loads(result_path.read_text("utf8"))
        check(label + " result and source receipts equal in-process", result["result"] == calculated["result"] and result["result_receipt"]["hit_parameter_sources"] == calculated["result_receipt"]["hit_parameter_sources"])
        baseline_path = output / (label + "-旧手填基线结果.json")
        proc = execute(["equipment", "calc", "--plan", str(manual_path), "--output", str(baseline_path), "--summary", "--overwrite"])
        (output / (label + "-baseline.stderr.json")).write_text(proc.stderr, encoding="utf8")
        check(label + " manual baseline calculation succeeds", proc.returncode == 0)
        baseline_result = json.loads(baseline_path.read_text("utf8"))
        check(label + " manual and source-bound hit arithmetic agree", baseline_result["result"]["hits"] == result["result"]["hits"])
        for name in rejects:
            rejected_path = output / (label + "-" + name + "-不得生成结果.json")
            check(label + " rejection output starts absent: " + name, not rejected_path.exists())
            proc = execute(["equipment", "calc", "--plan", str(output / ("倍率参数-" + name + ".json")), "--output", str(rejected_path), "--summary"])
            (output / (label + "-" + name + ".stderr.json")).write_text(proc.stderr, encoding="utf8")
            check(label + " rejection creates no result: " + name, proc.returncode != 0 and not rejected_path.exists())
        book_path = output / (label + "-公式核对.xlsx")
        proc = execute(["equipment", "workbook", "--plan", str(plan_path), "--output", str(book_path), "--overwrite"])
        (output / (label + "-workbook.stdout.json")).write_text(proc.stdout, encoding="utf8")
        (output / (label + "-workbook.stderr.json")).write_text(proc.stderr, encoding="utf8")
        check(label + " workbook succeeds", proc.returncode == 0 and book_path.is_file())
        verify_workbook(book_path.read_bytes(), check, label + " workbook")
    check("selected database bytes unchanged", before_db == digest(db))
    summary = {"status": "passed", "passed": len(checks), "checks": checks, "data_dir": str(data), "database_sha256": before_db,
               "expected_multipliers": [a[4] for a in ACTORS], "expected_damage": EXPECTED, "workbook_support": support,
               "native_plan": str(plan_path), "formula_workbook": str(output / "倍率参数-公式核对.xlsx"),
               "cli_exe": str(args.cli_exe.resolve()) if args.cli_exe else None,
               "scope": "真实C0十级E参数的归一工程算术；Agent显式选定单ATK倍率用途、命中和切人时点；非真实队伍DPS或完整动作自动派发",
               "not_verified": ["前台GUI交互", "Excel桌面重算", "游戏客户端", "完整角色技能机制"]}
    write(output / "verification.json", summary)
    print(json.dumps({"status": "passed", "passed": len(checks), "expected_damage": EXPECTED, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
