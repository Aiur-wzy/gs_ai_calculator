"""EC08 real DB -> native -> GUI bridge/CLI -> protected workbooks, without foreground UI."""
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
OUTPUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec08"
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_db import queries
from genshin_engine.elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT
from genshin_engine.runtime_target_state_compiler import target_state_compiler_caps

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SCOPE = "四命中冰套条件工程序列；给定ATK100/CR0/CD1/冰伤15%，敌90级10%抗。实际冰套资料，动作/命中/GU为受控输入，不代表真实角色技能或队伍DPS。"
EXPECTED = (51.75, 54.00000013411045, 63.0000002682209, 45)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf8")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_plan(db):
    ev = {"status": "assumed", "source": SCOPE}
    line = queries.get_meta(db)["datasets"]["equipment"]["lines"]["release"]
    version = {"version_line": "release", "upstream_commit": line["upstream_commit"],
               "equipment_schema_version": line["schema_version"], "built_at": line["built_at"]}
    entries = [("first-cryo", 0, "cryo"), ("make-freeze", .1, "hydro"),
               ("during-freeze", .2, "physical"), ("after-thaw", 10, "physical")]
    template = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
        "meta": {"name": "冰套逐击条件·工程核对", "assumptions": [SCOPE, "常驻两件15%冰伤已含，四件条件暴率未含。实验冻结参考不代表游戏真值。"]},
        "loop_duration": 11, "enemy": {"level": 90, "resistance": {"default": .1}},
        "characters": [{"id": "actor", "name": "工程样本角色", "level": 90,
            "stats": {"atk": 100, "em": 0, "crit_rate": 0, "crit_dmg": 1, "dmg_bonus": {"cryo": .15}, "energy_recharge": 1}}],
        "hits": [{"id": hid, "t": t, "element": element, "character": "actor", "skill": "工程序列",
            "hit_name": title, "damage_system": "classic", "damage_type_ids": ["damage.other"], "multiplier": 1}
            for (hid, t, element), title in zip(entries, ("首次挂冰", "本击造成冻结", "冻结期间命中", "解冻后命中"), strict=True)],
        "buffs": [], "switches": []}
    rotation = {"schema_version": 1, "period": 11, "max_cycles": 3, "initial_frontline": "actor",
        "characters": [{"id": "actor", "element": "cryo", "energy_recharge": 1, "energy_capacity": 80, "initial_energy": 80, **ev}],
        "abilities": [{"id": "engineering", "character": "actor", "action_type_id": "action.elemental_skill", "cooldown": None, "energy": None, **ev}],
        "actions": [{"id": "cast", "ability_id": "engineering", "start": 0, "occupation_end": .01, "order": 0,
            "repeat": {"mode": "once"}, **ev}], "events": [], "initial_cooldowns": []}
    elemental = {"schema_version": 1, "ruleset": FREEZE_RULESET,
        "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", **ev,
            "initial_state": {"ordinary_aura": None, "frozen": None, "freeze_history_seconds": 0, **ev},
            "freeze": {"freezable": True, "resistance": 0, **ev}},
        "reference_acceptance": {"contract_id": REFERENCE_CONTRACT["contract_id"], "contract_fingerprint": REFERENCE_CONTRACT["contract_fingerprint"],
            "acknowledged": True, "source": "明确采用本次实验参考用于工程验证；不宣称实测游戏真值。"},
        "profiles": [{"id": element, "element": element, "application": "apply", "gauge_units": 1, "icd": {"kind": "none"}, **ev}
                     for element in ("cryo", "hydro")] + [{"id": "physical", "element": "physical", "application": "none", **ev}],
        "contact_profiles": [{"id": "safe", "freeze_interaction": "none", **ev}], "swirl_damage": {"icd": {"kind": "unknown"}, **ev}}
    model = {"schema_version": 1, "rules": [], "initial_state": {"rules": []}, "hit_patterns": [], "condition_events": [],
        "hit_events": [{"hit_id": hid, "action_id": "cast", "order": 10 + i,
            "application": {"profile_id": element, "target_id": "enemy", "landed": True},
            "contact": {"kind": "profile", "profile_id": "safe"}} for i, (hid, _, element) in enumerate(entries)], "elemental_model": elemental}
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model,
        equipment_inputs={"actor": {"mode": "panel", "panel_policy": "static_included", "character_ref": None,
            "weapon": None, "artifact_sets": [{"set_id": 14001, "count": 4}], "source_refs": [{"kind": "engineering", "note": SCOPE}], "data_version": version}},
        reaction_damage_sources=[{"reaction_type": "swirl", "version_line": "release", "source": SCOPE,
                                  "expected_snapshot_id": None, "expected_profile_fingerprint": None}])
    first = compose_equipment_plan(plan, db)
    for req in first["effect_requirements"]:
        binding = copy.deepcopy(req["proposed_binding"])
        if req["owner"]["required_count"] == 2:
            binding.update(source_fingerprint=req["source_fingerprint"], coverage={"state": "complete", "reviewed_by": "engineering",
                "source_fingerprint": req["source_fingerprint"], "note": "两件常驻15%冰伤明确已包含于给定面板"},
                parts=[{"part_id": "included", "label": "两件冰伤已含", "state": "excluded", "rule": None, "delivery": None,
                    "conditions": [], "reason": "给定dmg_bonus.cryo=0.15已含两件套，不重复加入",
                    "dependencies": {"mode": "source_only", "context_fingerprint": None, "note": "本工程给定面板"}}])
        else:
            scenario = target_state_compiler_caps()["default_scenario"]
            scenario.update(status="assumed", source="普通可冻单目标、固定冰套四件；给定面板不含两段条件暴率；只执行经典命中投影。")
            binding.update(parts=[], upstream_recipe={"schema_version": 1, "scenario": scenario})
        plan["effect_bindings"].append(binding)
    return plan


def workbook(content, check, label, formula):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = {s.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
                  for i, s in enumerate(book.findall("s:sheets/s:sheet", NS), 1)}
    conditions = sheets["目标条件收益"]
    check(label + " protected conditions snapshot", conditions.find("s:sheetProtection", NS) is not None and not conditions.findall(".//s:f", NS))
    check(label + " evidence boundary visible", "实验参考" in "".join(conditions.itertext()) and "局部投影" in "".join(conditions.itertext()))
    check(label + " no cached errors", not any(c.get("t") == "e" for s in sheets.values() for c in s.findall(".//s:c", NS)))
    if formula:
        cells = {c.get("r"): c for c in sheets["逐事件"].findall(".//s:c", NS)}
        check(label + " actual four formulas and independent expected", all(cells[f"V{i}"].find("s:f", NS) is not None
            and math.isclose(float(cells[f"V{i}"].find("s:v", NS).text), expected, abs_tol=1e-9)
            for i, expected in enumerate(EXPECTED, 5)))
        fixed = [row for row in sheets["参数"].findall("s:sheetData/s:row", NS) if "fixed_effect_sources[" in "".join(row.itertext())]
        check(label + " fixed source leaves guarded", bool(fixed) and all(row.findall("s:c", NS)[2].get("s") == "5"
              and row.findall("s:c", NS)[8].find("s:f", NS) is not None for row in fixed))


def verify(args, check):
    db, output = args.data_dir / "genshin.db", args.output_dir
    print("EC08: real source preview and confirmation", flush=True)
    plan = make_plan(db)
    preview = compose_equipment_plan(plan, db)
    write(output / "unconfirmed.plan.json", plan)
    write(output / "unconfirmed-report.json", preview)
    check("unconfirmed source pending", preview["state"] == "pending")
    for binding in plan["effect_bindings"]:
        if "upstream_recipe" in binding:
            record = next(r for r in preview["upstream_compilations"] if r["binding_id"] == binding["binding_id"])
            binding["upstream_recipe"]["scenario"].update(record["required_confirmation"])
    plan["reaction_damage_sources"][0].update(preview["reaction_damage_compilations"][0]["required_confirmation"])
    before = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, db)
    write(output / "calculated.json", calculated)
    path = output / "target-conditions-native.plan.json"
    write(path, calculated["plan"])
    result, record = calculated["result"], calculated["report"]["upstream_compilations"][0]
    check("native never materializes generated sources", plan == before and "fixed_effect_sources" not in calculated["plan"]["mechanics_model"]
          and next(b for b in calculated["plan"]["effect_bindings"] if "upstream_recipe" in b)["parts"] == [])
    check("actual DB four expected damages", len(result["hits"]) == 4 and all(math.isclose(h["expected"], x, abs_tol=1e-9)
          for h, x in zip(result["hits"], EXPECTED, strict=True)))
    check("before own application and after natural thaw", [r["automatic_value"] for r in result["target_condition_trace"] if r["cycle"] == 0]
          == [False, False, True, False, True, True, False, False])
    check("partial source retains all four branches", record["whole_source_status"] == "partial"
          and record["coverage_certificate"]["source_complete"] is False and len(record["branches"]) == 4)
    check("no fake lifetime or initial rules", not calculated["report"]["mechanics_model"]["rules"]
          and not calculated["report"]["mechanics_model"]["initial_state"]["rules"])
    poisoned = copy.deepcopy(calculated["plan"])
    poisoned["resolved_template"]["hits"][0]["multiplier"] = 999
    poisoned["resolution"]["upstream_compilations"][0]["fixed_effect_sources"][0]["effects"][0]["value"] = 1
    check("serialized compiled cache ignored", run_plan_calc(poisoned, db)["result"] == result)
    print("EC08: headless GUI save/load and Excel", flush=True)
    gui = GuiApi(args.data_dir, plans_dir=output / "gui-plans")
    saved = output / "gui-saved.plan.json"
    check("GUI save", gui.save_plan(str(saved), calculated["plan"])["code"] == 0)
    loaded = gui.load_plan(str(saved))
    check("GUI reload ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
    check("GUI recalc same", gui.run_plan_calc(read_plan(saved)["plan"])["data"]["result"] == result)
    for kind, content in (("snapshot", result_bytes(result, calculated["result_receipt"], "xlsx")), ("formula", build_formula_workbook(calculated))):
        (output / (kind + ".xlsx")).write_bytes(content)
        workbook(content, check, kind, kind == "formula")
    check("fixed conditions require regeneration", formula_workbook_support(calculated)["editable"]["target_condition_sources"] == "regenerate_required")
    negatives = {"unconfirmed": json.loads((output / "unconfirmed.plan.json").read_text("utf8")), "lunar": copy.deepcopy(plan)}
    negatives["lunar"]["manual_template"]["hits"][0]["damage_system"] = "lunar"
    for label, negative in negatives.items():
        write(output / (label + ".plan.json"), negative)
    commands = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        commands.append(("package-cli", [str(args.cli_exe)]))
    records = []
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"}
    for label, prefix in commands:
        print("EC08: " + label, flush=True)
        def execute(kind, options, prefix=prefix, label=label):
            argv = [*prefix, "--data-dir", str(args.data_dir), *map(str, options)]
            start = time.perf_counter()
            proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, encoding="utf8", timeout=180,
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            (output / f"{label}-{kind}.stdout.json").write_text(proc.stdout, "utf8")
            (output / f"{label}-{kind}.stderr.txt").write_text(proc.stderr, "utf8")
            records.append({"argv": argv, "returncode": proc.returncode, "seconds": time.perf_counter() - start})
            return proc
        target = output / (label + "-result.json")
        proc = execute("calc", ["equipment", "calc", "--plan", path, "--output", target, "--summary"])
        check(label + " bounded calc", proc.returncode == 0 and len(proc.stdout.encode("utf8")) < 16000)
        check(label + " exact result equality", json.loads(target.read_text("utf8"))["result"] == result)
        for kind in ("snapshot", "formula"):
            target = output / f"{label}-{kind}.xlsx"
            options = ["equipment", "workbook" if kind == "formula" else "export", "--plan", path, "--output", target]
            if kind == "snapshot":
                options += ["--format", "xlsx"]
            proc = execute(kind, options)
            check(label + " " + kind + " export", proc.returncode == 0 and target.exists())
            workbook(target.read_bytes(), check, label + " " + kind, kind == "formula")
        for negative in negatives:
            target = output / f"{label}-{negative}-must-not-exist.json"
            proc = execute(negative, ["equipment", "calc", "--plan", output / (negative + ".plan.json"), "--output", target, "--summary"])
            check(label + " " + negative + " rejected", proc.returncode != 0 and not target.exists())
    write(output / "commands.json", records)
    return {"source_version": APP_VERSION, "scope": SCOPE, "expected_damage": sum(EXPECTED), "native_plan": str(path),
            "not_verified": ["actual game truth", "complete source/Moon/Star", "real character dispatch or team DPS", "foreground UI", "Excel desktop recalculation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if not args.output_dir.is_relative_to(OUTPUT_ROOT.resolve()) or args.output_dir == OUTPUT_ROOT.resolve() or args.output_dir.exists():
        parser.error("输出必须为EC08内全新子目录，禁止覆盖")
    db = args.data_dir / "genshin.db"
    if not db.is_file():
        parser.error("缺少genshin.db")
    if args.cli_exe:
        args.cli_exe = args.cli_exe.resolve()
        if not args.cli_exe.is_file():
            parser.error("EXE不存在")
    args.output_dir.mkdir(parents=True)
    checks, before, start = [], digest(db), time.perf_counter()
    report = {"status": "running", "checks": checks, "database_sha256_before": before,
              "cli_exe": str(args.cli_exe) if args.cli_exe else None}
    def check(label, condition):
        checks.append({"name": label, "passed": bool(condition)})
        if not condition:
            raise AssertionError(label)
    try:
        report.update(verify(args, check))
        check("formal database unchanged", digest(db) == before)
        report.update(status="passed", passed=len(checks))
    except Exception as exc:
        report.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        report.update(seconds=time.perf_counter() - start, database_sha256_after=digest(db))
        write(args.output_dir / "verification.json", report)
    print(json.dumps({"status": "passed", "checks": len(checks), "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
