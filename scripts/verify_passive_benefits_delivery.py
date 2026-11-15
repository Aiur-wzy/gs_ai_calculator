"""EC12 real database -> accepted actions -> inherent bonus -> App and Excel."""
from __future__ import annotations

import argparse
import copy
import io
import json
import math
import os
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verify_action_events_delivery import NS, sha, write

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import input_view, read_plan
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_db import queries
from genshin_engine.runtime_passive_benefit_compiler import (
    passive_benefit_compiler_caps,
)

OUTPUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec12"
BASELINE = ROOT / "tests/fixtures/engine-continuation/ec10/delivery/kazuha-tap-native.plan.json"
SCOPE = ("固有天赋工程核验：实际C0/10级万叶点按E及固有Proud472201原文；显式90级突破6阶，给定ATK1000、EM900、零暴击，"
         "固定精通明确等于本来源非额外精通且没有额外转换，各接收者未预含天赋增伤。三次E动作0/12/24秒及命中偏移0.5秒为"
         "工程声明；独立接收者五次冰伤仅验证覆盖边界。初始2GU冰底/每秒衰减0.01、唯一原目标和全队范围、原子事件顺序"
         "与开始前无天赋实例均为明确工程输入，不是游戏帧测量或神鹤万心DPS实验。")
FACTOR, DURATION, EM = .00039999998989515007, 8, 900


def replace_scope(value, old):
    if isinstance(value, dict):
        return {k: replace_scope(v, old) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_scope(v, old) for v in value]
    return value.replace(old, SCOPE) if isinstance(value, str) else value


def make_plan(db):
    p = input_view(read_plan(BASELINE)["plan"])
    p.update(resolution={"state": "pending"}, resolved_template=None)
    p = replace_scope(p, p["manual_template"]["meta"]["assumptions"][0])
    p.update(plan_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "genshin:ec12:inherent-benefit-engineering")), revision=0)
    template, rotation, model = p["manual_template"], p["rotation_model"], p["mechanics_model"]
    template["meta"]["name"] = "万叶固有天赋自动增伤·工程核验"
    template["characters"][0]["stats"]["em"] = EM
    receiver = copy.deepcopy(template["characters"][0])
    receiver.update(id="receiver", name="冰伤接收者（工程输入）")
    receiver["stats"]["em"] = 0
    template["characters"].append(receiver)
    rotation["characters"].append({"id": "receiver", "element": "cryo", "energy_recharge": 1, "energy_capacity": 80,
        "initial_energy": 80, "status": "assumed", "source": SCOPE})
    rotation["abilities"].append({"id": "receiver.attack", "character": "receiver", "action_type_id": "action.normal_attack",
        "cooldown": None, "energy": None, "status": "assumed", "source": SCOPE})
    for index, t in enumerate((1, 8.5, 13, 20.5, 24.9)):
        hid, aid = f"receiver.hit.{index}", f"receiver.action.{index}"
        template["hits"].append({"id": hid, "t": t, "character": "receiver", "skill": "普通攻击", "hit_name": "冰伤覆盖验证",
            "damage_type_ids": ["damage.normal_attack"], "damage_system": "classic", "scaling_stat": "atk", "multiplier": 1, "element": "cryo"})
        rotation["actions"].append({"id": aid, "ability_id": "receiver.attack", "start": t, "occupation_end": round(t + .05, 6),
            "order": 10, "repeat": {"mode": "once"}, "status": "assumed", "source": SCOPE})
        model["hit_events"].append({"hit_id": hid, "action_id": aid, "order": 11,
            "application": {"profile_id": "receiver.no-application", "target_id": "enemy", "landed": True},
            "contact": {"kind": "none", "status": "assumed", "source": SCOPE}})
    model["elemental_model"]["profiles"].append({"id": "receiver.no-application", "element": "cryo", "application": "none",
        "status": "assumed", "source": "独立工程输出不施加元素，避免改变此覆盖边界试验的受控初始底；不声称角色攻击附着规则。"})
    for owner, target in (("actor", "receiver"), ("receiver", "actor")):
        rotation["abilities"].append({"id": "switch." + target, "character": owner, "action_type_id": "action.switch",
            "cooldown": None, "energy": None, "status": "assumed", "source": SCOPE})
    for index, (t, target) in enumerate(((.8, "receiver"), (9, "actor"), (12.8, "receiver"), (21, "actor"), (24.8, "receiver"))):
        rotation["actions"].append({"id": f"switch.{index}", "ability_id": "switch." + target, "start": t, "occupation_end": round(t + .05, 6),
            "switch_to": target, "order": 2, "repeat": {"mode": "once"}, "status": "assumed", "source": SCOPE})
    model["elemental_model"]["target"]["initial_state"]["ordinary_aura"] = {
        "element": "cryo", "remaining_gu": 2, "decay_gu_per_second": .01, "source": SCOPE}
    for profile in p["action_event_profiles"]:
        profile["accepted_profile_fingerprint"] = None
    line = queries.get_meta(db)["datasets"]["equipment"]["lines"]["release"]
    version = {"version_line": "release", "upstream_commit": line["upstream_commit"], "equipment_schema_version": line["schema_version"], "built_at": line["built_at"]}
    panel = {"mode": "panel", "panel_policy": "static_included", "weapon": None, "artifact_sets": [],
        "source_refs": [{"kind": "engineering", "note": SCOPE}], "data_version": version}
    p["equipment_inputs"] = {"actor": {**copy.deepcopy(panel), "character_ref": {"db_character_id": 10000047, "version_line": "release"},
        "progression": {"ascension": 6, "status": "assumed", "source": SCOPE}}, "receiver": panel}
    scenario = passive_benefit_compiler_caps()["default_scenario"]
    scenario.update(target_id="enemy", origin_target_range="within_source_range", recipient_range="all_party_within_source_range",
        initial_source_effects="none", recipient_bonus_policy="not_included", stat_basis="fixed_panel_nonextra_em_equals_total_em",
        event_order_policy="declared_atomic_hit_order", status="assumed", source=SCOPE)
    p["passive_sources"] = [{"id": "inherent.kazuha", "actor": "actor", "proud_skill_id": 472201,
        "constellation": 0, "scenario": scenario, "accepted_fingerprint": None}]
    return p


def confirm(p, db, output, check):
    first = compose_equipment_plan(p, db)
    write(output / "before-confirmation.json", first)
    check("unaccepted parents and source cannot execute", first["state"] == "pending" and not first["passive_compilations"][0]["rules"])
    for record in first.get("reaction_damage_compilations", []):
        next(r for r in p["reaction_damage_sources"] if r["reaction_type"] == record["reaction_type"]).update(record["required_confirmation"])
    second = compose_equipment_plan(p, db)
    for record in second["action_event_compilations"]:
        next(r for r in p["action_event_profiles"] if r["id"] == record["profile_id"]).update(record["required_confirmation"])
    parent = p["action_event_profiles"][0]["accepted_profile_fingerprint"]
    third = compose_equipment_plan(p, db)
    write(output / "before-passive-acceptance.json", third)
    p["passive_sources"][0].update(third["passive_compilations"][0]["required_confirmation"] or {})
    final = compose_equipment_plan(p, db)
    write(output / "after-confirmation.json", final)
    check("actual source accepted without parent confirmation cycle", final["state"] == "ready"
        and final["action_event_compilations"][0]["profile_fingerprint"] == parent)
    return p


def workbook(content, check, label, formula, hits):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        sheets = {n.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, n in enumerate(names, 1)}
    source = sheets["固有天赋来源"]
    text = "".join(source.itertext())
    check(label + " complete readonly inherent source", source.find("s:sheetProtection", NS) is not None and not source.findall(".//s:f", NS)
        and all(s in text for s in ("ordinary_swirl_passive_projection_certificate", "激活或续期", "非额外精通", "472201")))
    check(label + " parent provenance preserved", "动作派发来源" in sheets and "攻击元信息来源" in sheets)
    table = sheets["逐事件" if formula else "逐事件明细"]
    start = 5 if formula else 2
    def cell(row, column):
        return table.find(f"s:sheetData/s:row[@r='{row}']/s:c[@r='{column}{row}']", NS)
    def number(row, column):
        return float(cell(row, column).find("s:v", NS).text)
    actual_rows = [r for r in table.findall("s:sheetData/s:row", NS) if int(r.get("r")) >= start]
    check(label + " complete event count", len(actual_rows) == len(hits))
    check(label + " per event cached numbers match engine", all(math.isclose(number(start+i, "V" if formula else "G"), h["expected"], abs_tol=1e-8)
        and math.isclose(number(start+i, "D" if formula else "A"), h["t"], abs_tol=1e-8) for i, h in enumerate(hits)))
    check(label + " no Excel error values", not any(s.findall(".//s:c[@t='e']", NS) for s in sheets.values()))
    if formula:
        check(label + " formulas identities and baseline match", all(cell(start+i, "V").find("s:f", NS) is not None
            and "".join(cell(start+i, "A").itertext()) == h["id"] and "".join(cell(start+i, "AB").itertext()) == h["character"]
            and math.isclose(number(start+i, "X"), h["expected"], abs_tol=1e-8) and abs(number(start+i, "Y")) < 1e-8 for i, h in enumerate(hits)))
        values = [r for r in sheets["参数"].findall("s:sheetData/s:row", NS) if ".value.factor" in "".join(r.itertext())
            and "mechanics_model.rules[" in "".join(r.itertext())]
        check(label + " source factors fixed", len(values) == 2 and all(r.findall("s:c", NS)[2].get("s") == "5" for r in values))


def verify(args, check):
    db, out = args.data_dir / "genshin.db", args.output_dir
    print("EC12 actual E source -> inherent source -> passive execution", flush=True)
    p = confirm(make_plan(db), db, out, check)
    before = copy.deepcopy(p)
    data = run_plan_calc(p, db)
    write(out / "calculated.json", data)
    native = out / "kazuha-inherent-bonus-native.plan.json"
    write(native, data["plan"])
    result, record = data["result"], data["result"]["passive_compilations"][0]
    check("native unchanged and no generated rules", p == before and not p["mechanics_model"]["rules"] and not p["manual_template"]["buffs"])
    check("thirteen explicit actions, eleven damage events", len(p["rotation_model"]["actions"]) == 13 and len(result["hits"]) == 11)
    receiver = [h for h in result["hits"] if h["character"] == "receiver"]
    expected = [450 * (1 + EM * FACTOR), 450, 450 * (1 + EM * FACTOR), 450, 450 * (1 + EM * FACTOR)]
    check("independent five receiver calculations with exact expiry", len(receiver) == 5 and all(math.isclose(h["expected"], v, abs_tol=1e-8) for h, v in zip(receiver, expected, strict=True)))
    check("independent full reference arithmetic", math.isclose(sum(h["expected"] for h in result["hits"]), 21384.15807426112, abs_tol=1e-8))
    cryo = next(r["id"] for r in record["rules"] if r["trigger"]["reaction_elements"] == ["cryo"])
    activations = [r for r in result["mechanics_trace"] if r.get("type") == "activation" and r.get("rule_id") == cryo and r.get("cycle") == 0]
    check("three automatic eight second intervals", [(r["t"], r["expires_at"]) for r in activations] == [(.5, [8.5]), (12.5, [20.5]), (24.5, [32.5])])
    check("source closure unlock and actual parents retained", record["source"]["source_status"] == "ready"
        and record["unlock"]["status"] == "eligible_under_declared_progression" and len(record["scope"]["provider_parent_events"]) == 3
        and record["whole_source_status"] == "partial" and record["coverage_certificate"]["gameplay_verified"] is False)
    manual = copy.deepcopy(p)
    del manual["passive_sources"]
    manual["mechanics_model"]["manual_buff_repeat"] = "once"
    for index, start in enumerate((.5, 12.5, 24.5)):
        manual["manual_template"]["buffs"].append({"id": f"explicit.bonus.{index}", "start": start, "end": start + 8,
            "target": "receiver", "scope": "team", "effects": [{"id": "bonus", "zone": "dmg_bonus", "stat": "cryo",
                "benefit_type_id": "benefit.dmg_bonus.cryo", "value": EM * FACTOR, "applies_to": {"mode": "all"}}],
            "extensions": {"owner": "actor", "source_id": "explicit.inherent.reference"}})
        manual["manual_effect_dependencies"].append({"effect_ref": {"buff_id": f"explicit.bonus.{index}", "effect_id": "bonus"},
            "mode": "source_only", "context_fingerprint": None,
            "note": "独立工程参考：数值固定为900乘原始系数，不随换装联动；仅在本文同一声明面板下比较。"})
    explicit = run_plan_calc(manual, db)
    write(out / "explicit-reference.plan.json", manual)
    write(out / "explicit-reference-calculated.json", explicit)
    check("explicit reference agrees for all damage and elemental state", [h["expected"] for h in result["hits"]] == [h["expected"] for h in explicit["result"]["hits"]]
        and result["elemental"]["trace"] == explicit["result"]["elemental"]["trace"])
    missed = copy.deepcopy(p)
    missed["action_event_overrides"] = [{"action_id": "cast.2", "landed": False, "status": "assumed", "source": SCOPE}]
    missed_result = run_plan_calc(missed, db)["result"]
    check("missed E removes its reaction and bonus without manual editing", len(missed_result["hits"]) == 9
        and next(h for h in missed_result["hits"] if h["id"] == "receiver.hit.2")["expected"] == 450)
    gui = GuiApi(args.data_dir, plans_dir=out / "gui-plans")
    saved = out / "gui-saved.plan.json"
    check("GUI save source", gui.save_plan(str(saved), data["plan"])["code"] == 0)
    loaded = gui.load_plan(str(saved))
    check("GUI load fresh source ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
    check("GUI recalculate identical", gui.run_plan_calc(read_plan(saved)["plan"])["data"]["result"] == result)
    for kind, content in (("snapshot", result_bytes(result, data["result_receipt"], "xlsx")), ("formula", build_formula_workbook(data))):
        (out / (kind + ".xlsx")).write_bytes(content)
        workbook(content, check, kind, kind == "formula", result["hits"])
    check("passive changes require App recalculation", formula_workbook_support(data)["editable"]["passive_sources_progression_and_stat_basis"] == "regenerate_required")
    prefixes = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        prefixes.append(("package-cli", [str(args.cli_exe)]))
    commands, env = [], {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"}
    for label, prefix in prefixes:
        print("EC12 hidden " + label, flush=True)
        for op, mid, ext in (("calc", ["calc", "--summary"], "json"), ("export", ["export", "--format", "xlsx"], "xlsx"), ("workbook", ["workbook"], "xlsx")):
            file = out / f"{label}-{op}.{ext}"
            argv = [*prefix, "--data-dir", str(args.data_dir), "equipment", *mid, "--plan", str(native), "--output", str(file)]
            start = time.perf_counter()
            run = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, encoding="utf8", timeout=180, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            write(out / (file.name + ".command.json"), {"argv": argv, "exit": run.returncode, "stdout": run.stdout, "stderr": run.stderr})
            commands.append({"label": label + "/" + op, "exit": run.returncode, "seconds": time.perf_counter() - start})
            check(label + " " + op + " success", run.returncode == 0 and file.is_file())
            if op == "calc":
                check(label + " identical computed events", json.loads(file.read_text("utf8"))["result"] == result)
                check(label + " summary bounded", len(run.stdout.encode()) < 5500)
            else:
                workbook(file.read_bytes(), check, label + " " + op, op == "workbook", result["hits"])
    return {"native": str(native), "commands": commands, "scope": SCOPE, "baseline_file_sha256": sha(BASELINE), "expected_receiver_damage": expected,
        "window_seconds": 25, "action_repeat": "all_once", "damage_scope": "explicit_input_window_only_not_three_repeated_rounds",
        "input_reduction": {"hypothetical_equivalent_rule_count": 2, "hypothetical_equivalent_initial_slots": 2,
            "explicit_reference_buff_windows": 3, "native_generated_rules": 0, "native_generated_buff_windows": 0,
            "source_selections": 1, "explicit_actions": 13, "scene_and_progression_declarations_retained": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if OUTPUT_ROOT.resolve() not in args.output_dir.parents or args.output_dir.exists():
        parser.error("请使用EC12目录下新的子目录，保留原始验证记录")
    if args.cli_exe:
        args.cli_exe = args.cli_exe.resolve()
    args.output_dir.mkdir(parents=True)
    checks, before = [], sha(args.data_dir / "genshin.db")
    def check(name, ok):
        checks.append({"name": name, "passed": bool(ok)})
        assert ok, name
    try:
        details = verify(args, check)
        after = sha(args.data_dir / "genshin.db")
        check("formal database bytes unchanged", before == after)
        write(args.output_dir / "verification.json", {"status": "passed", "source_version": APP_VERSION, "passed": len(checks),
            "checks": checks, "cli_exe": str(args.cli_exe) if args.cli_exe else None, "database_sha256_before": before, "database_sha256_after": after, **details})
        print(json.dumps({"passed": len(checks), "output": str(args.output_dir)}), flush=True)
    except Exception as exc:
        write(args.output_dir / "failure.json", {"error": type(exc).__name__, "message": str(exc), "details": getattr(exc, "details", None), "checks": checks,
            "database_sha256_before": before, "database_sha256_after": sha(args.data_dir / "genshin.db")})
        raise


if __name__ == "__main__":
    main()
