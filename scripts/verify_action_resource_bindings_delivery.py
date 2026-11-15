"""EC07b four C0 fixed-burst resource bindings: normalized engineering delivery.

Each character casts once and deals one artificial ATK100*1 hit (45 damage).
This verifies resource binding, not character dispatch, actual Q damage or team DPS.
Only a fresh EC07b output directory is accepted; the selected DB is read-only.
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
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec07b"
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_db.runtime_queries import query_runtime

ACTORS = (("ayaka", "神里绫华", 10000002, 10019, "cryo", 20, 80),
          ("shenhe", "申鹤", 10000063, 10635, "cryo", 20, 80),
          ("kazuha", "枫原万叶", 10000047, 10475, "anemo", 15, 60),
          ("kokomi", "珊瑚宫心海", 10000054, 10545, "hydro", 18, 70))
SCOPE = "四角色C0普通Q基础资源绑定工程验证；一次释放和切人时点均显式给定。每人一个人工倍率1命中，不代表真实Q技能、完整队伍或DPS。"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf8")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_plan():
    evidence = {"status": "assumed", "source": SCOPE}
    template = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
        "enemy": {"level": 90, "resistance": {"default": .1}}, "loop_duration": 8,
        "meta": {"name": "普通爆发基础资源·四角色工程核对", "assumptions": [SCOPE,
            "等级90、攻击100、0暴击，敌人90级10%抗；4个工程命中各45、总180。能量容量与初态为场景显式输入。"]},
        "characters": [{"id": cid, "name": name, "level": 90,
            "stats": {"atk": 100, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}}
            for cid, name, *_ in ACTORS], "buffs": [], "switches": [],
        "hits": [{"id": cid + ".hit", "character": cid, "skill": "工程命中", "hit_name": name + "·人工倍率1核对",
            "element": element, "t": 2*i + .2, "multiplier": 1, "damage_system": "classic",
            "damage_type_ids": ["damage.elemental_burst"]} for i, (cid, name, _, _, element, _, _) in enumerate(ACTORS)]}
    rotation = {"schema_version": 1, "period": 8, "max_cycles": 3, "initial_frontline": ACTORS[0][0],
        "characters": [{"id": cid, "element": element, "energy_recharge": 1, "energy_capacity": cost, "initial_energy": cost, **evidence}
                       for cid, _, _, _, element, _, cost in ACTORS],
        "abilities": [], "actions": [], "events": [], "initial_cooldowns": []}
    for i, (cid, _, _, _, _, _, _) in enumerate(ACTORS):
        aid = cid + ".q"
        rotation["abilities"].append({"id": aid, "character": cid, "action_type_id": "action.elemental_burst",
            "variant": "普通元素爆发", "cooldown": {"group": aid, "start": "action_start", "recovery": "sequential"},
            "energy": {}, **evidence})
        rotation["initial_cooldowns"].append({"character": cid, "group": aid, "ready_in": []})
        rotation["actions"].append({"id": cid + ".cast", "ability_id": aid, "start": 2*i, "occupation_end": 2*i + .5,
            "order": 0, "repeat": {"mode": "once"}, **evidence})
        rotation["events"].append({"id": cid + ".spend", "type": "energy_spend", "character": cid,
            "action_id": cid + ".cast", "t": 2*i, "order": 1, "repeat": {"mode": "once"}, **evidence})
        if i:
            switch, previous = "switch." + cid, ACTORS[i-1][0]
            rotation["abilities"].append({"id": switch, "character": previous, "action_type_id": "action.switch",
                "cooldown": None, "energy": None, **evidence})
            rotation["actions"].append({"id": switch, "ability_id": switch, "start": 2*i-.5, "occupation_end": 2*i-.5,
                "switch_to": cid, "order": 0, "repeat": {"mode": "once"}, **evidence})
    rotation["actions"].sort(key=lambda a: a["start"])
    mechanics = {"schema_version": 1, "rules": [], "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []},
        "hit_events": [{"hit_id": cid + ".hit", "action_id": cid + ".cast", "order": 10} for cid, *_ in ACTORS]}
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics, action_resource_sources=[
        {"ability_id": cid + ".q", "version_line": "release", "selection": {"character_id": dbid, "slot": "burst", "constellation": 0},
         "purpose": "ordinary_fixed_burst_base_resources", "variant": "普通元素爆发", **evidence,
         "expected_snapshot_id": None, "expected_resource_fingerprint": None, "expected_scope_fingerprint": None}
        for cid, _, dbid, *_ in ACTORS])
    return plan


def numbers_omitted(plan):
    model = plan["rotation_model"]
    ids = {binding["ability_id"] for binding in plan["action_resource_sources"]}
    return all(not ({"duration", "charges"} & ability["cooldown"].keys()) and ability["energy"] == {}
               for ability in model["abilities"] if ability["id"] in ids) and all(
                   not ({"required", "amount"} & event.keys()) for event in model["events"] if event["type"] == "energy_spend")


def verify_workbook(content, check, label, formula):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        sheets = {item.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, item in enumerate(names, 1)}
    source = sheets["动作资源来源"]
    check(label + " readonly resource provenance", source.find("s:sheetProtection", NS) is not None and not source.findall(".//s:f", NS))
    text = "".join(source.itertext())
    check(label + " explicit scope and four identities", "declared_not_verified" in text and "explicit_sources_only" in text
          and all(str(dbid) in text and str(skill) in text for _, _, dbid, skill, *_ in ACTORS))
    check(label + " no cached Excel errors", not any(c.get("t") == "e" for sheet in sheets.values() for c in sheet.findall(".//s:c", NS)))
    if formula:
        cells = {c.get("r"): c for c in sheets["逐事件"].findall(".//s:c", NS)}
        check(label + " four actual damage formulas cached45", all(cells[f"V{i}"].find("s:f", NS) is not None
            and math.isclose(float(cells[f"V{i}"].find("s:v", NS).text), 45, abs_tol=1e-9) for i in range(5, 9)))
        fixed = [row for row in sheets["参数"].findall("s:sheetData/s:row", NS)
                 if "action_resource_compilations[" in "".join(row.itertext())]
        check(label + " sixteen compiled resource values fixed", len(fixed) == 16 and all(
            row.findall("s:c", NS)[2].get("s") == "5" and "<>" in row.findall("s:c", NS)[8].find("s:f", NS).text for row in fixed))
    else:
        rows = sheets["逐事件明细"].findall("s:sheetData/s:row", NS)[1:]
        check(label + " four snapshot damages45", len(rows) == 4 and all(
            math.isclose(float(row.findall("s:c", NS)[6].find("s:v", NS).text), 45, abs_tol=1e-9) for row in rows))


def verify(args, check):
    output, db = args.output_dir, args.data_dir / "genshin.db"
    request = {"action": "batch", "version_line": "release", "items": [{"id": cid, "request": {
        "action": "action_resource_profile", "character_id": dbid, "skill_id": skill}} for cid, _, dbid, skill, *_ in ACTORS]}
    print("EC07b: read exact four Q sources, preview and confirm", flush=True)
    queries = query_runtime(db, request)
    write(output / "resource-queries.json", {"request": request, "response": queries})
    check("four explicit Q queries complete", queries["status"] == "complete" and queries["counts"]["ok"] == 4)
    plan = make_plan()
    for actor, item, binding in zip(ACTORS, queries["items"], plan["action_resource_sources"], strict=True):
        cid, name, dbid, skill, element, cd, cost = actor
        facts = item["data"]
        check(cid + " exact Q identity and resource values", facts["selection"]["character_id"] == dbid
              and facts["selection"]["skill_id"] == skill and "burst" in facts["selection"]["matching_slots"]
              and {k: v["value"] for k, v in facts["projection"].items()} == {
                  "base_cooldown_seconds": cd, "base_charge_count": 1, "base_energy_cost": cost, "cost_element": element})
        check(cid + " complete Chinese descriptions", facts["descriptions"]["status"] == "ready"
              and all(any("\u4e00" <= c <= "\u9fff" for c in facts["descriptions"][k]["text"]) for k in ("name", "description")))
        binding["source"] = f"已核对同快照{name}的{facts['descriptions']['name']['text']}完整中文原文和基础资源。工程场景声明为C0普通固定耗能Q；不宣称数据库自动证明特殊机制、能量退款或用途。"
    unconfirmed = copy.deepcopy(plan)
    write(output / "unconfirmed.plan.json", unconfirmed)
    preview = compose_equipment_plan(plan, db)
    write(output / "unconfirmed-report.json", preview)
    check("omitted numbers and unconfirmed source remain pending", numbers_omitted(plan) and preview["state"] == "pending")
    for binding, record, query in zip(plan["action_resource_sources"], preview["action_resource_compilations"], queries["items"], strict=True):
        check(binding["ability_id"] + " fresh exact ID and slot share resource fingerprint", record["resource_fingerprint"] == query["data"]["resource_fingerprint"])
        binding.update(record["required_confirmation"])
    before = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, db)
    write(output / "calculated.json", calculated)
    path = output / "four-q-native.plan.json"
    write(path, calculated["plan"])
    check("native original timing and omitted fields preserved", plan == before and calculated["plan"]["rotation_model"] == before["rotation_model"]
          and numbers_omitted(calculated["plan"]))
    check("four normalized damages independently180", len(calculated["result"]["hits"]) == 4
          and all(math.isclose(hit["expected"], 45, abs_tol=1e-9) for hit in calculated["result"]["hits"])
          and math.isclose(calculated["result"]["summary"]["total_damage"], 180, abs_tol=1e-9))
    compiled = calculated["report"]["compiled_rotation_model"]
    executed = calculated["result"]["rotation_check"]["resolved_rotation_model"]
    for cid, _, _, _, _, cd, cost in ACTORS:
        for label, model in (("compiled", compiled), ("executed", executed)):
            ability = next(a for a in model["abilities"] if a["id"] == cid + ".q")
            spend = next(e for e in model["events"] if e["id"] == cid + ".spend")
            check(cid + " " + label + " correct CD/charges/atomic spend", ability["cooldown"]["duration"] == cd
                  and ability["cooldown"]["charges"] == 1 and ability["energy"] == {"required": cost, "amount": cost}
                  and spend["required"] == spend["amount"] == cost)
    check("compiled receipt fingerprint is before ER resolution", calculated["result_receipt"]["action_resources"]["compiled_rotation_model_fingerprint"] == fingerprint(compiled))
    poisoned = copy.deepcopy(calculated["plan"])
    poisoned["resolved_template"]["hits"][0]["multiplier"] = 999
    poisoned["resolution"]["action_resource_compilations"][0]["compiled_values"]["energy"]["amount"] = 1
    check("cached resolved data never controls fresh calc", run_plan_calc(poisoned, db)["result"] == calculated["result"])
    print("EC07b: own GUI bridge save/reload and protected workbooks", flush=True)
    gui = GuiApi(args.data_dir, plans_dir=output / "gui-plans")
    saved_path = output / "gui-saved.plan.json"
    check("GUI bridge save succeeds", gui.save_plan(str(saved_path), calculated["plan"])["code"] == 0)
    loaded = gui.load_plan(str(saved_path))
    write(output / "gui-loaded.json", loaded)
    restored = read_plan(saved_path)["plan"]
    check("GUI reload remains ready and native omitted", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready" and numbers_omitted(restored))
    repeated = gui.run_plan_calc(restored)
    write(output / "gui-calculated.json", repeated)
    check("GUI bridge result equality", repeated["code"] == 0 and repeated["data"]["result"] == calculated["result"])
    for kind, content in (("snapshot", result_bytes(calculated["result"], calculated["result_receipt"], "xlsx")),
                          ("formula", build_formula_workbook(calculated))):
        (output / (kind + ".xlsx")).write_bytes(content)
        verify_workbook(content, check, kind, kind == "formula")
    check("formula contract requires regeneration", formula_workbook_support(calculated)["editable"]["database_action_resources"] == "regenerate_required")
    stale = copy.deepcopy(calculated)
    stale["plan"]["action_resource_sources"][0]["source"] += "修改用途依据"
    try:
        build_formula_workbook(stale)
    except FormulaWorkbookError as exc:
        write(output / "stale-workbook-rejected.json", {"type": type(exc).__name__, "message": str(exc)})
        check("same revision old receipt cannot export changed binding", True)
    else:
        raise AssertionError("stale workbook unexpectedly accepted")
    negatives = {"unconfirmed": unconfirmed, "scope-changed": copy.deepcopy(plan)}
    negatives["scope-changed"]["action_resource_sources"][0]["source"] += "修改用途依据"
    for label, invalid in negatives.items():
        write(output / (label + ".plan.json"), invalid)
        try:
            run_plan_calc(invalid, db)
        except PlanError as exc:
            write(output / (label + "-rejected.json"), {"reason": exc.reason, "message": str(exc), "details": exc.details})
            check(label + " no authoritative result", True)
        else:
            raise AssertionError(label + " unexpectedly calculated")
    commands = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        commands.append(("package-cli", [str(args.cli_exe)]))
    records = []
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"}
    for label, prefix in commands:
        print("EC07b: " + label + " calc, snapshot/formula export, rejected inputs", flush=True)
        def execute(kind, options, prefix=prefix, label=label):
            argv = [*prefix, "--data-dir", str(args.data_dir), *map(str, options)]
            start = time.perf_counter()
            result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, encoding="utf8", timeout=180,
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            (output / f"{label}-{kind}.stdout.json").write_text(result.stdout, "utf8")
            (output / f"{label}-{kind}.stderr.json").write_text(result.stderr, "utf8")
            records.append({"argv": argv, "returncode": result.returncode, "seconds": time.perf_counter()-start})
            return result
        result_path = output / (label + "-result.json")
        proc = execute("calc", ["equipment", "calc", "--plan", path, "--output", result_path, "--summary"])
        check(label + " bounded successful calc", proc.returncode == 0 and len(proc.stdout.encode("utf8")) < 16000)
        delivered = json.loads(result_path.read_text("utf8"))
        check(label + " exact result and resource receipt equality", delivered["result"] == calculated["result"]
              and delivered["result_receipt"]["action_resources"] == calculated["result_receipt"]["action_resources"])
        for kind in ("snapshot", "formula"):
            target = output / f"{label}-{kind}.xlsx"
            options = ["equipment", "workbook" if kind == "formula" else "export", "--plan", path, "--output", target]
            if kind == "snapshot":
                options += ["--format", "xlsx"]
            proc = execute(kind, options)
            check(label + " " + kind + " successful", proc.returncode == 0 and target.is_file())
            verify_workbook(target.read_bytes(), check, label + " " + kind, kind == "formula")
        for negative in negatives:
            target = output / f"{label}-{negative}-must-not-exist.json"
            proc = execute(negative, ["equipment", "calc", "--plan", output / (negative + ".plan.json"), "--output", target, "--summary"])
            check(label + " " + negative + " rejected with no output", proc.returncode != 0 and not target.exists())
    write(output / "commands.json", records)
    return {"source_version": APP_VERSION, "scope": SCOPE, "expected_damage": 180, "source_snapshot": queries["snapshot"],
        "native_plan": str(path), "verified": ["source API", "source headless GUI bridge save/load", "source CLI",
            "normal XLSX", "formula XLSX cached damage and fixed provenance"] + (["candidate EXE CLI"] if args.cli_exe else []),
        "not_verified": ["foreground GUI", "Excel desktop recalculation", "full character Q damage/dispatch", "team DPS",
                         "C>0", "variable costs", "resource-mode gameplay truth", "long-term energy recurrence"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT / "delivery-source")
    parser.add_argument("--cli-exe", "--exe", dest="cli_exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if not args.output_dir.is_relative_to(OUTPUT_ROOT.resolve()) or args.output_dir == OUTPUT_ROOT.resolve():
        parser.error("输出必须为EC07b内全新独立目录")
    if args.output_dir.exists():
        parser.error("禁止覆盖已有交付，请指定全新目录")
    db = args.data_dir / "genshin.db"
    if not db.is_file():
        parser.error("指定目录缺少genshin.db")
    if args.cli_exe:
        args.cli_exe = args.cli_exe.resolve()
        if not args.cli_exe.is_file():
            parser.error("EXE不存在")
    args.output_dir.mkdir(parents=True)
    checks, before, start = [], digest(db), time.perf_counter()
    report = {"status": "running", "checks": checks, "database_sha256_before": before, "cli_exe": str(args.cli_exe) if args.cli_exe else None}
    def check(label, condition):
        checks.append({"name": label, "passed": bool(condition)})
        if not condition:
            raise AssertionError(label)
    try:
        report.update(verify(args, check))
        check("formal database SHA unchanged", digest(db) == before)
        report.update(status="passed", passed=len(checks))
    except Exception as exc:
        report.update(status="failed", passed=sum(c["passed"] for c in checks), error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        report.update(seconds=time.perf_counter()-start, database_sha256_after=digest(db))
        write(args.output_dir / "verification.json", report)
    print(json.dumps({"status": "passed", "checks": len(checks), "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
