"""EC11 real action source -> attachment -> reaction -> equipment -> App/Excel."""
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
from genshin_engine.runtime_reaction_benefit_compiler import (
    reaction_benefit_compiler_caps,
)

OUTPUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec11"
BASELINE = ROOT / "tests/fixtures/engine-continuation/ec10/delivery/kazuha-tap-native.plan.json"
SCOPE = ("普通冰扩散与风套联动工程核验：沿实际C0/10级万叶点按E攻击来源；给定ATK1000、EM0、零暴击、"
         "面板含15%风伤，90级对90级10%抗。三次动作0/12/24秒、命中偏移0.5及初始2GU冰底/每秒衰减0.01"
         "是受控工程输入，不是游戏时序测量或神鹤万心DPS实验。原目标范围与原子顺序明确，开始前无该源减抗；"
         "不证明随机产球、角色天赋或完整技能支持。")
BONUS, SHRED, DURATION = .6000000238418579, .4000000059604645, 10
EXPECTED_DIRECT = 450 * 3.4560000896453857 * 1.15
EXPECTED_SWIRL = 1446.85 * .6 * (1 + BONUS) * (1 - (.1 - SHRED) / 2)


def replace_text(value, old):
    if isinstance(value, dict):
        return {key: replace_text(child, old) for key, child in value.items()}
    if isinstance(value, list):
        return [replace_text(child, old) for child in value]
    return value.replace(old, SCOPE) if isinstance(value, str) else value


def make_plan(db):
    plan = input_view(read_plan(BASELINE)["plan"])
    plan.update(resolution={"state": "pending"}, resolved_template=None)
    plan = replace_text(plan, plan["manual_template"]["meta"]["assumptions"][0])
    plan["plan_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "genshin:ec11:real-reaction-benefits-engineering"))
    plan["revision"] = 0
    plan["manual_template"]["meta"]["name"] = "万叶战技与风套联动·工程核验"
    plan["manual_template"]["characters"][0]["stats"]["dmg_bonus"] = {"anemo": .15}
    plan["mechanics_model"]["elemental_model"]["target"]["initial_state"]["ordinary_aura"] = {
        "element": "cryo", "remaining_gu": 2, "decay_gu_per_second": .01, "source": SCOPE}
    for profile in plan["action_event_profiles"]:
        profile["accepted_profile_fingerprint"] = None
    line = queries.get_meta(db)["datasets"]["equipment"]["lines"]["release"]
    version = {"version_line": "release", "upstream_commit": line["upstream_commit"],
               "equipment_schema_version": line["schema_version"], "built_at": line["built_at"]}
    plan["equipment_inputs"] = {"actor": {"mode": "panel", "panel_policy": "static_included",
        "character_ref": {"db_character_id": 10000047, "version_line": "release"}, "weapon": None,
        "artifact_sets": [{"set_id": 15002, "count": 4}], "source_refs": [{"kind": "engineering", "note": SCOPE}], "data_version": version}}
    for requirement in compose_equipment_plan(plan, db)["effect_requirements"]:
        binding = copy.deepcopy(requirement["proposed_binding"])
        if requirement["owner"]["required_count"] == 2:
            binding.update(source_fingerprint=requirement["source_fingerprint"],
                coverage={"state": "complete", "reviewed_by": "engineering", "source_fingerprint": requirement["source_fingerprint"],
                          "note": "两件常驻15%风伤已包含于给定面板"},
                parts=[{"part_id": "included", "label": "常驻风伤已含", "state": "excluded", "rule": None, "delivery": None,
                        "conditions": [], "reason": "给定面板已含15%风伤，不重复加入",
                        "dependencies": {"mode": "source_only", "context_fingerprint": None, "note": SCOPE}}])
        else:
            scenario = reaction_benefit_compiler_caps()["default_scenario"]
            scenario.update(target_id="enemy", origin_target_range="within_source_range", initial_source_effects="none",
                            event_order_policy="declared_atomic_hit_order", status="assumed", source=SCOPE)
            binding.update(parts=[], upstream_recipe={"schema_version": 1, "scenario": scenario})
        plan["effect_bindings"].append(binding)
    return plan


def confirm(plan, db, output, check):
    first = compose_equipment_plan(plan, db)
    write(output / "before-confirmation.json", first)
    check("unaccepted recipe cannot execute", first["state"] == "pending")
    records = first.get("upstream_compilations", [])
    check("one exact partial equipment projection", len(records) == 1 and records[0]["whole_source_status"] == "partial"
          and len(records[0]["branches"]) >= 8)
    for record in records:
        binding = next(row for row in plan["effect_bindings"] if row["binding_id"] == record["binding_id"])
        binding["upstream_recipe"]["scenario"].update(record["required_confirmation"])
    for record in first.get("reaction_damage_compilations", []):
        next(row for row in plan["reaction_damage_sources"] if row["reaction_type"] == record["reaction_type"]).update(record["required_confirmation"])
    second = compose_equipment_plan(plan, db)
    for record in second.get("action_event_compilations", []):
        next(row for row in plan["action_event_profiles"] if row["id"] == record["profile_id"]).update(record["required_confirmation"])
    return plan


def manual_equivalent(plan, report):
    """Independent explicit D20 construction using fixed reference facts."""
    plan = copy.deepcopy(plan)
    binding = next(row for row in plan["effect_bindings"] if "upstream_recipe" in row)
    del binding["upstream_recipe"]
    requirement = next(row for row in report["effect_requirements"] if row["binding_id"] == binding["binding_id"])
    binding.update(source_fingerprint=requirement["source_fingerprint"], coverage={"state": "complete", "reviewed_by": "engineering",
        "source_fingerprint": requirement["source_fingerprint"], "note": "明确仅在本文普通冰水工程场景中手填相同来源事实；不作为游戏完整覆盖证明"}, parts=[])
    def part(pid, rule, delivery):
        return {"part_id": pid, "label": "独立显式参考", "state": "resolved", "rule": rule, "delivery": delivery,
                "conditions": [], "dependencies": {"mode": "source_only", "context_fingerprint": None, "note": SCOPE}, "reason": None}
    binding["parts"].append(part("bonus", {"kind": "constant", "value": BONUS}, {"kind": "buff", "start": 0, "end": 25,
        "scope": "team", "target": "actor", "effect": {"id": "bonus", "benefit_type_id": "benefit.reaction_bonus.swirl",
        "zone": "reaction_bonus", "stat": "swirl", "applies_to": {"mode": "all"}}}))
    for element in ("hydro", "cryo"):
        rid = "manual-reference-" + element
        rule = {"id": rid, "name_zh": "显式参考减抗", "owner": "actor", "source_id": binding["binding_id"] + ":" + element,
            "trigger": {"event": "elemental_reaction", "phase": "post", "characters": ["actor"], "reaction_types": ["swirl"],
                        "reaction_elements": [element], "owner_presence": "frontline"},
            "beneficiaries": {"mode": "team", "scope": "locked"},
            "timing": {"delay": 0, "duration": DURATION, "cooldown": 0, "cooldown_anchor": "success"},
            "stacking": {"max": 1, "add": 1, "policy": "refresh"},
            "effects": [{"id": "value", "benefit_type_id": "benefit.res_shred." + element, "value": SHRED, "domain": "enemy"}],
            "status": "assumed", "source": SCOPE}
        plan["mechanics_model"]["rules"].append(rule)
        plan["mechanics_model"]["initial_state"]["rules"].append({"rule_id": rid, "cooldown_remaining": 0, "instances": []})
        binding["parts"].append(part(element, {"kind": "mechanics", "rule_id": rid}, {"kind": "mechanics"}))
    return plan


def workbook(content, check, label, formula):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        sheets = {node.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, node in enumerate(names, 1)}
    source = sheets["反应收益来源"]
    check(label + " complete source readonly", source.find("s:sheetProtection", NS) is not None and not source.findall(".//s:f", NS)
          and "ordinary_swirl_benefit_projection_certificate" in "".join(source.itertext()) and "激活或续期" in "".join(source.itertext()))
    check(label + " action and attack provenance retained", "动作派发来源" in sheets and "攻击元信息来源" in sheets)
    if formula:
        values = [row for row in sheets["参数"].findall("s:sheetData/s:row", NS) if ".value" in "".join(row.itertext())
                  and any(prefix in "".join(row.itertext()) for prefix in ("mechanics_input.buffs[", "mechanics_model.rules["))]
        check(label + " three automatic benefit values fixed", len(values) == 3 and all(row.findall("s:c", NS)[2].get("s") == "5" for row in values))


def verify(args, check):
    db, output = args.data_dir / "genshin.db", args.output_dir
    print("EC11 real action -> reaction -> benefits", flush=True)
    plan = confirm(make_plan(db), db, output, check)
    before = copy.deepcopy(plan)
    data = run_plan_calc(plan, db)
    write(output / "calculated.json", data)
    native = output / "kazuha-swirl-benefits-native.plan.json"
    write(native, data["plan"])
    result = data["result"]
    check("original input unchanged", plan == before)
    check("three actual actions produce six damage events", len(result["hits"]) == 6
          and [hit["t"] for hit in result["hits"]] == [.5, .5, 12.5, 12.5, 24.5, 24.5])
    check("independent direct and swirl arithmetic", all(math.isclose(hit["expected"], expected, abs_tol=1e-8)
          for hit, expected in zip(result["hits"], [EXPECTED_DIRECT, EXPECTED_SWIRL] * 3, strict=True)))
    check("native keeps no generated benefits or patterns", not plan["mechanics_model"]["rules"] and not plan["mechanics_model"]["hit_patterns"]
          and not plan["manual_template"]["buffs"] and all(not row["parts"] for row in plan["effect_bindings"] if "upstream_recipe" in row))
    record = result["reaction_benefit_compilations"][0]
    cryo = next(row["id"] for row in record["rules"] if row["trigger"]["reaction_elements"] == ["cryo"])
    activations = [row for row in result["mechanics_trace"] if row.get("type") == "activation" and row.get("rule_id") == cryo and row.get("cycle") == 0]
    check("source duration ten seconds each time", [(row["t"], row["expires_at"]) for row in activations] == [(.5, [10.5]), (12.5, [22.5]), (24.5, [34.5])])
    manual = manual_equivalent(plan, data["report"])
    manual_data = run_plan_calc(manual, db)
    write(output / "explicit-reference.plan.json", manual)
    write(output / "explicit-reference-calculated.json", manual_data)
    check("explicit reference damage and elemental trace identical", [row["expected"] for row in result["hits"]] == [row["expected"] for row in manual_data["result"]["hits"]]
          and result["elemental"]["trace"] == manual_data["result"]["elemental"]["trace"])
    moved = copy.deepcopy(plan)
    moved["rotation_model"]["actions"][1].update(start=13, occupation_end=13.8)
    moved_result = run_plan_calc(moved, db)["result"]
    check("moving action automatically moves reaction and benefit", [row["t"] for row in moved_result["hits"]] == [.5, .5, 13.5, 13.5, 24.5, 24.5]
          and [row["expires_at"] for row in moved_result["mechanics_trace"] if row.get("type") == "activation" and row.get("rule_id") == cryo and row.get("cycle") == 0] == [[10.5], [23.5], [34.5]])
    missed = copy.deepcopy(plan)
    missed["action_event_overrides"] = [{"action_id": "cast.2", "landed": False, "status": "assumed", "source": "工程反例第二次落空"}]
    missed_result = run_plan_calc(missed, db)["result"]
    check("missed action creates neither reaction damage nor benefit", len(missed_result["hits"]) == 4
          and len([row for row in missed_result["mechanics_trace"] if row.get("type") == "activation" and row.get("rule_id") == cryo and row.get("cycle") == 0]) == 2)
    gui = GuiApi(args.data_dir, plans_dir=output / "gui-plans")
    saved = output / "gui-saved.plan.json"
    check("GUI save", gui.save_plan(str(saved), data["plan"])["code"] == 0)
    loaded = gui.load_plan(str(saved))
    check("GUI load source ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
    check("GUI recalculate same", gui.run_plan_calc(read_plan(saved)["plan"])["data"]["result"] == result)
    for kind, content in (("snapshot", result_bytes(result, data["result_receipt"], "xlsx")), ("formula", build_formula_workbook(data))):
        (output / (kind + ".xlsx")).write_bytes(content)
        workbook(content, check, kind, kind == "formula")
    check("source edits require recalculation", formula_workbook_support(data)["editable"]["reaction_benefit_sources"] == "regenerate_required")
    prefixes = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        prefixes.append(("package-cli", [str(args.cli_exe)]))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"}
    commands = []
    for label, prefix in prefixes:
        print("EC11 hidden " + label, flush=True)
        for operation, middle, extension in (("calc", ["calc", "--summary"], "json"),
                ("export", ["export", "--format", "xlsx"], "xlsx"), ("workbook", ["workbook"], "xlsx")):
            path = output / f"{label}-{operation}.{extension}"
            argv = [*prefix, "--data-dir", str(args.data_dir), "equipment", *middle, "--plan", str(native), "--output", str(path)]
            start = time.perf_counter()
            proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, encoding="utf8", check=False, timeout=180,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            write(output / (path.name + ".command.json"), {"argv": argv, "exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
            commands.append({"label": label + "/" + operation, "exit": proc.returncode, "seconds": time.perf_counter() - start})
            check(label + " " + operation + " success", proc.returncode == 0 and path.is_file())
            if operation == "calc":
                calculated = json.loads(path.read_text("utf8"))
                check(label + " same actual events", calculated["result"] == result)
                check(label + " compact honest summary", len(proc.stdout.encode()) < 5000 and json.loads(proc.stdout)["data"]["reaction_benefits"]["rule_count"] == 2)
            else:
                workbook(path.read_bytes(), check, label + " " + operation, operation == "workbook")
    return {"native": str(native), "commands": commands, "scope": SCOPE, "baseline_file_sha256": sha(BASELINE),
            "expected_direct": EXPECTED_DIRECT, "expected_swirl": EXPECTED_SWIRL,
            "input_reduction": {"manual_benefit_parts_before": 3, "manual_benefit_parts_after": 0,
                "manual_rules_before": 2, "manual_rules_after": 0, "manual_initial_rule_slots_before": 2, "manual_initial_rule_slots_after": 0,
                "source_recipes_after": 1, "explicit_actions_unchanged": 3, "scene_assumptions_retained": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if OUTPUT_ROOT.resolve() not in args.output_dir.parents or args.output_dir.exists():
        parser.error("请使用EC11目录下不存在的新子目录，保留先前证据。")
    if args.cli_exe:
        args.cli_exe = args.cli_exe.resolve()
    args.output_dir.mkdir(parents=True)
    checks, before = [], sha(args.data_dir / "genshin.db")
    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        assert passed, name
    try:
        details = verify(args, check)
        after = sha(args.data_dir / "genshin.db")
        check("formal database bytes unchanged", before == after)
        write(args.output_dir / "verification.json", {"status": "passed", "source_version": APP_VERSION,
            "passed": len(checks), "checks": checks, "cli_exe": str(args.cli_exe) if args.cli_exe else None,
            "database_sha256_before": before, "database_sha256_after": after, **details})
        print(json.dumps({"passed": len(checks), "output": str(args.output_dir)}), flush=True)
    except Exception as exc:
        write(args.output_dir / "failure.json", {"error": type(exc).__name__, "message": str(exc),
            "details": getattr(exc, "details", None), "checks": checks})
        raise


if __name__ == "__main__":
    main()
