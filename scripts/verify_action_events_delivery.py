"""EC09 real scalar sources, reusable calibration, hidden CLI/GUI bridge and Excel verification."""
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
OUTPUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec09"
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_engine.elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SCOPE = ("动作档案工程验证：实际C0/10级战技数据库参数；ATK1000、零暴击、90级同等级、10%抗性。"
         "三次动作0/12/24秒、0.5秒出伤偏移、0.8秒占用及安全接触均为工程声明，并非实测技能时序。"
         "给定GU和共享组仅验证接线，不宣称游戏攻击映射已证实；不自动产球，也不验证真实角色能量/CD循环。")
SAMPLES = (("ayaka", "神里绫华", 10000002, "cryo", 4.305600166320801),
           ("kazuha", "枫原万叶", 10000047, "anemo", 3.4560000896453857))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_plan(name, cid, element):
    evidence = {"status": "assumed", "source": SCOPE}
    template = {"version": 2, "form": "resolved", "id_registry_version": 1,
        "type_definitions": {"damage_types": []}, "meta": {"name": name + "·校准动作装配工程验证", "assumptions": [SCOPE]},
        "loop_duration": 25, "enemy": {"level": 90, "resistance": {"default": .1}},
        "characters": [{"id": "actor", "name": name, "level": 90,
            "stats": {"atk": 1000, "crit_rate": 0, "crit_dmg": 1, "em": 0, "energy_recharge": 1, "dmg_bonus": {}}}],
        "hits": [{"id": "skill-prototype", "t": 0, "character": "actor", "skill": "元素战技",
                  "hit_name": name + "战技工程命中", "damage_type_ids": ["damage.elemental_skill"],
                  "damage_system": "classic", "element": element, "scaling_stat": "atk"}], "buffs": [], "switches": []}
    rotation = {"schema_version": 1, "period": 25, "max_cycles": 3, "initial_frontline": "actor",
        "characters": [{"id": "actor", "element": element, "energy_recharge": 1, "energy_capacity": 80,
                        "initial_energy": 80, **evidence}],
        "abilities": [{"id": "skill", "character": "actor", "action_type_id": "action.elemental_skill",
                       "variant": "tap", "cooldown": None, "energy": None, **evidence}],
        "actions": [{"id": f"cast.{i + 1}", "ability_id": "skill", "start": t, "occupation_end": t + .8,
                     "order": 0, "repeat": {"mode": "once"}, **evidence} for i, t in enumerate((0, 12, 24))],
        "events": [], "initial_cooldowns": []}
    elemental = {"schema_version": 1, "ruleset": FREEZE_RULESET,
        "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", **evidence,
            "initial_state": {"ordinary_aura": None, "frozen": None, "freeze_history_seconds": 0, **evidence},
            "freeze": {"freezable": True, "resistance": 0, **evidence}},
        "reference_acceptance": {"contract_id": REFERENCE_CONTRACT["contract_id"],
            "contract_fingerprint": REFERENCE_CONTRACT["contract_fingerprint"], "acknowledged": True,
            "source": "接受明确参考假设用于工程装配测试，不声称游戏客户端复验。"},
        "profiles": [{"id": "skill-application", "element": element, "application": "apply", "gauge_units": 1,
                      "icd": {"kind": "unknown"}, **evidence}],
        "contact_profiles": [{"id": "safe-contact", "freeze_interaction": "none", **evidence}],
        "swirl_damage": {"icd": {"kind": "unknown"}, **evidence}}
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation,
        mechanics_model={"schema_version": 1, "rules": [], "initial_state": {"rules": []},
                         "hit_events": [], "hit_patterns": [], "condition_events": [], "elemental_model": elemental},
        hit_parameter_sources=[{"hit_id": "skill-prototype", "version_line": "release",
            "selection": {"character_id": cid, "slot": "skill", "constellation": 0}, "param_index": 0,
            "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", **evidence,
            "expected_snapshot_id": None, "expected_node_sha256": None}],
        elemental_sources=[{"profile_id": "skill-application", "version_line": "release", "attenuation_group": "default",
            "shared_group": "declared-skill-shared", "source": SCOPE, "expected_snapshot_id": None, "expected_node_sha256": None}],
        reaction_damage_sources=[{"reaction_type": "swirl", "version_line": "release", "source": SCOPE,
                                  "expected_snapshot_id": None, "expected_profile_fingerprint": None}],
        action_event_profiles=[{"id": "skill-contact", "kind": "calibrated_single_contact_v1", "prototype_hit_id": "skill-prototype",
            "variant": "tap", "calibration": {"offset_seconds": .5, "precision_seconds": .01, **evidence},
            "application_profile_id": "skill-application", "contact_profile_id": "safe-contact",
            "default_landed": True, "self_sampling": "current", "accepted_profile_fingerprint": None}],
        action_event_bindings=[{"ability_id": "skill", "profile_id": "skill-contact"}], action_event_overrides=[])
    return plan


def confirm(plan, db, directory, check, label):
    preview = compose_equipment_plan(plan, db)
    write(directory / (label + "-source-preview.json"), preview)
    check(label + " initial unknown source pending", preview["state"] == "pending")
    for field, receipt in (("hit_parameter_sources", "hit_parameter_compilations"),
                           ("elemental_sources", "elemental_compilations"),
                           ("reaction_damage_sources", "reaction_damage_compilations")):
        for declaration, record in zip(plan[field], preview[receipt], strict=True):
            declaration.update(record["required_confirmation"])
    preview = compose_equipment_plan(plan, db)
    write(directory / (label + "-calibration-preview.json"), preview)
    check(label + " facts do not auto accept calibration", preview["state"] == "pending")
    for profile, record in zip(plan["action_event_profiles"], preview["action_event_compilations"], strict=True):
        profile.update(record["required_confirmation"])
    return plan


def check_workbook(content, check, label, expected, *, formula):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        sheets = {item.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, item in enumerate(names, 1)}
    page = sheets["动作派发来源"]
    check(label + " dispatch sheet protected/literal", page.find("s:sheetProtection", NS) is not None and not page.findall(".//s:f", NS))
    check(label + " calibrated partial boundary visible", "校准" in "".join(page.itertext()) and "partial" in "".join(page.itertext()))
    check(label + " no cached errors", not any(c.get("t") == "e" for sheet in sheets.values() for c in sheet.findall(".//s:c", NS)))
    if formula:
        cells = {cell.get("r"): cell for cell in sheets["逐事件"].findall(".//s:c", NS)}
        check(label + " formula independent three hit expected", all(cells[f"V{row}"].find("s:f", NS) is not None
              and math.isclose(float(cells[f"V{row}"].find("s:v", NS).text), expected, abs_tol=1e-9) for row in (5, 6, 7)))
        rows = [r for r in sheets["参数"].findall("s:sheetData/s:row", NS) if "action_event_profiles[" in "".join(r.itertext())]
        check(label + " calibration fixed leaves guarded", bool(rows) and all(r.findall("s:c", NS)[2].get("s") == "5"
              and r.findall("s:c", NS)[8].find("s:f", NS) is not None for r in rows))


def verify(args, check):
    db, output = args.data_dir / "genshin.db", args.output_dir
    deliveries = []
    for label, name, cid, element, multiplier in SAMPLES:
        print("EC09: real source " + label, flush=True)
        plan = confirm(make_plan(name, cid, element), db, output, check, label)
        original = copy.deepcopy(plan)
        data = run_plan_calc(plan, db)
        write(output / (label + "-calculated.json"), data)
        path = output / (label + "-native.plan.json")
        write(path, data["plan"])
        deliveries.append({"label": label, "plan": str(path), "expected_single_hit": 450 * multiplier})
        result, records = data["result"], data["result"]["action_event_compilations"]
        generated = records[0]["generated"]
        check(label + " independent scalar/damage", len(result["hits"]) == 3 and all(
              math.isclose(h["expected"], 450 * multiplier, abs_tol=1e-9) for h in result["hits"]))
        check(label + " only explicit actions and correct times", len(generated) == 3 and [h["t"] for h in result["hits"]] == [.5, 12.5, 24.5])
        check(label + " prototype not emitted", "skill-prototype" not in {h["id"] for h in result["hits"]})
        check(label + " native remains one source zero patterns", plan == original and len(data["plan"]["manual_template"]["hits"]) == 1
              and "multiplier" not in data["plan"]["manual_template"]["hits"][0] and not data["plan"]["mechanics_model"]["hit_patterns"])
        check(label + " execution receipt model", data["result_receipt"]["action_events"]["compiled_mechanics_model_fingerprint"] == fingerprint(data["report"]["mechanics_model"]))
        check(label + " explicit partial coverage", records[0]["dispatch_mode"] == "calibrated" and records[0]["skill_coverage"] == "partial"
              and records[0]["resource_coverage"] == "not_generated")
        moved = copy.deepcopy(plan)
        moved["rotation_model"]["actions"][1].update(start=13, occupation_end=13.8)
        moved_data = run_plan_calc(moved, db)
        check(label + " move only one child and keep identities", [h["t"] for h in moved_data["result"]["hits"]] == [.5, 13.5, 24.5]
              and [h["id"] for h in moved_data["result"]["hits"]] == [h["id"] for h in result["hits"]]
              and moved_data["result"]["action_event_compilations"][0]["profile_fingerprint"] == records[0]["profile_fingerprint"])
        missed = copy.deepcopy(plan)
        missed["action_event_overrides"] = [{"action_id": "cast.2", "landed": False, "status": "assumed", "source": "工程反例：第二次落空。"}]
        missed_data = run_plan_calc(missed, db)
        check(label + " one override suppresses only one hit", len(missed_data["result"]["hits"]) == 2
              and math.isclose(missed_data["result"]["summary"]["total_damage"], 900 * multiplier, abs_tol=1e-9))
        poisoned = copy.deepcopy(data["plan"])
        poisoned["resolved_template"]["hits"][0]["multiplier"] = 999
        poisoned["resolution"]["action_event_compilations"][0]["generated"][0]["t"] = 999
        check(label + " cached generated values not trusted", run_plan_calc(poisoned, db)["result"] == result)
        deleted = copy.deepcopy(plan)
        deleted["rotation_model"]["actions"].pop(1)
        check(label + " deleted action removes its child", len(run_plan_calc(deleted, db)["result"]["hits"]) == 2)
        if label == "ayaka":
            gui = GuiApi(args.data_dir, plans_dir=output / "gui-plans")
            saved = output / "gui-saved.plan.json"
            check("GUI native save", gui.save_plan(str(saved), data["plan"])["code"] == 0)
            loaded = gui.load_plan(str(saved))
            check("GUI native load ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
            check("GUI calculate same", gui.run_plan_calc(read_plan(saved)["plan"])["data"]["result"] == result)
        for kind, content in (("snapshot", result_bytes(result, data["result_receipt"], "xlsx")), ("formula", build_formula_workbook(data))):
            (output / f"{label}-{kind}.xlsx").write_bytes(content)
            check_workbook(content, check, label + " " + kind, 450 * multiplier, formula=kind == "formula")
        check(label + " editable boundaries", formula_workbook_support(data)["editable"]["action_event_profiles_bindings_overrides"] == "regenerate_required")

    print("EC09: hidden CLI delivery", flush=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"}
    prefixes = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        prefixes.append(("package-cli", [str(args.cli_exe)]))
    commands = []
    for prefix_label, prefix in prefixes:
        for entry in deliveries:
            label, path, expected = entry["label"], Path(entry["plan"]), entry["expected_single_hit"]
            for operation in ("calc", "export", "workbook"):
                filename = f"{prefix_label}-{label}-{operation}." + ("json" if operation == "calc" else "xlsx")
                destination = output / filename
                argv = [*prefix, "--data-dir", str(args.data_dir), "equipment", operation, "--plan", str(path), "--output", str(destination)]
                if operation == "calc":
                    argv += ["--summary"]
                elif operation == "export":
                    argv += ["--format", "xlsx"]
                started = time.perf_counter()
                proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, encoding="utf8", timeout=180,
                                      check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                write(output / (filename + ".command.json"), {"argv": argv, "exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
                commands.append({"label": prefix_label + " " + label + " " + operation, "exit": proc.returncode,
                                 "seconds": time.perf_counter() - started})
                check(commands[-1]["label"] + " exit", proc.returncode == 0 and destination.is_file())
                if operation == "calc":
                    summary = json.loads(proc.stdout)["data"]
                    payload = json.loads(destination.read_text("utf8"))
                    check(commands[-1]["label"] + " bounded summary and actual result", len(proc.stdout.encode("utf8")) < 4500
                          and summary["action_events"]["generated_hit_count"] == 3
                          and math.isclose(payload["result"]["summary"]["total_damage"], expected * 3, abs_tol=1e-9))
                else:
                    check_workbook(destination.read_bytes(), check, commands[-1]["label"], expected, formula=operation == "workbook")
    return {"deliveries": deliveries, "commands": commands}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if OUTPUT_ROOT.resolve() not in args.output_dir.parents or args.output_dir.exists():
        parser.error("请使用EC09目录下尚不存在的子目录，保留前次证据。")
    if args.cli_exe:
        args.cli_exe = args.cli_exe.resolve()
    args.output_dir.mkdir(parents=True)
    checks = []
    before = sha(args.data_dir / "genshin.db")
    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        assert passed, name
    try:
        details = verify(args, check)
        after = sha(args.data_dir / "genshin.db")
        check("formal database bytes unchanged", before == after)
        report = {"status": "passed", "source_version": APP_VERSION, "passed": len(checks), "checks": checks,
            "cli_exe": str(args.cli_exe) if args.cli_exe else None, "scope": SCOPE,
            "database_sha256_before": before, "database_sha256_after": after, **details}
        write(args.output_dir / "verification.json", report)
        print(json.dumps({"status": "passed", "checks": len(checks), "output": str(args.output_dir)}), flush=True)
    except Exception as exc:
        write(args.output_dir / "failure.json", {"error": type(exc).__name__, "message": str(exc), "checks": checks})
        raise


if __name__ == "__main__":
    main()
