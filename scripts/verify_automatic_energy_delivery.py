"""EC14: four real fixed Q resource sources, twelve explicit engineering casts.

Positive costs come from the DB. Two declared full-team refills are engineering
inputs, not real particle production or a team DPS claim. No foreground UI.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import math
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec14"
sys.path.insert(0, str(ROOT / "src"))

from verify_action_resource_bindings_delivery import (
    ACTORS,
    NS,
    digest,
    make_plan,
    numbers_omitted,
    write,
)

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, input_view, read_plan
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_db.runtime_queries import query_runtime

SCOPE = "自动扣能工程：四角色真实C0普通Q基础成本，12次显式爆发与11次切人，完整56秒窗口；23/47秒各明确给予全队一次固定回能，仅作隔离场景假设，不代表真实产球或循环。每次爆发一个人工ATK100倍率1命中，各45、总540，不代表技能完整伤害或队伍DPS。"


def make_engineering_plan():
    plan = make_plan()
    model, template = plan["rotation_model"], plan["manual_template"]
    evidence = {"status": "assumed", "source": SCOPE}
    template["loop_duration"] = model["period"] = 56
    template["meta"] = {"name": "自动扣能·四角色十二次动作工程验证", "assumptions": [SCOPE]}
    base_actions, base_hits = copy.deepcopy(model["actions"]), copy.deepcopy(template["hits"])
    base_hit_events = copy.deepcopy(plan["mechanics_model"]["hit_events"])
    model["actions"], model["events"], template["hits"], plan["mechanics_model"]["hit_events"] = [], [], [], []
    model["abilities"].append({"id": "switch.ayaka", "character": "kokomi", "action_type_id": "action.switch",
        "cooldown": None, "energy": None, **evidence})
    for cycle, offset in enumerate((0, 24, 48)):
        suffix = f".r{cycle + 1}"
        if cycle:
            model["actions"].append({"id": "switch.ayaka" + suffix, "ability_id": "switch.ayaka",
                "start": offset - .5, "occupation_end": offset - .5, "order": 0,
                "switch_to": "ayaka", "repeat": {"mode": "once"}, **evidence})
            model["events"].append({"id": "explicit.refill" + suffix, "type": "energy_direct",
                "t": offset - 1, "order": 0, "amounts": {cid: cost for cid, *_, cost in ACTORS},
                "repeat": {"mode": "once"}, **evidence})
        for item in base_actions:
            model["actions"].append({**item, "id": item["id"] + suffix,
                                     "start": item["start"] + offset, "occupation_end": item["occupation_end"] + offset,
                                     "source": SCOPE})
        for hit, event in zip(base_hits, base_hit_events, strict=True):
            template["hits"].append({**hit, "id": hit["id"] + suffix, "t": hit["t"] + offset})
            plan["mechanics_model"]["hit_events"].append({**event, "hit_id": event["hit_id"] + suffix,
                                                         "action_id": event["action_id"] + suffix})
    for binding in plan["action_resource_sources"]:
        binding.update(energy_delivery="action_start", source=SCOPE)
    return plan


def confirmed(plan, db):
    report = compose_equipment_plan(plan, db)
    for binding, record in zip(plan["action_resource_sources"], report["action_resource_compilations"], strict=True):
        binding.update(record["required_confirmation"])
    return plan


def workbook(content, check, label, formula):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        sheets = {item.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, item in enumerate(names, 1)}
    source = sheets["动作资源来源"]
    text = "".join(source.itertext())
    check(label + " full readonly resource and automatic identity", source.find("s:sheetProtection", NS) is not None
          and not source.findall(".//s:f", NS) and "action_admission" in text
          and all(str(dbid) in text for _, _, dbid, *_ in ACTORS) and "declared_not_verified" in text)
    check(label + " no Excel error cache", not any(c.get("t") == "e" for s in sheets.values() for c in s.findall(".//s:c", NS)))
    if formula:
        cells = {c.get("r"): c for c in sheets["逐事件"].findall(".//s:c", NS)}
        check(label + " twelve formulas and independent45 caches", all(cells[f"V{i}"].find("s:f", NS) is not None
              and math.isclose(float(cells[f"V{i}"].find("s:v", NS).text), 45) for i in range(5, 17)))
        rows = {r.find("s:c/s:is/s:t", NS).text: r for r in sheets["参数"].findall("s:sheetData/s:row", NS)
                if r.find("s:c/s:is/s:t", NS) is not None}
        required = ["action_resource_sources[0].energy_delivery", "rotation_model.actions[0].start",
                    "rotation_model.characters[0].initial_energy", "rotation_model.events[0].t"]
        check(label + " native mode actions initial energy and refill fixed", all(k in rows
              and rows[k].findall("s:c", NS)[2].get("s") == "5"
              and "<>" in rows[k].findall("s:c", NS)[8].find("s:f", NS).text for k in required))
    else:
        rows = sheets["逐事件明细"].findall("s:sheetData/s:row", NS)[1:]
        check(label + " twelve independently45 damages", len(rows) == 12 and all(
            math.isclose(float(r.findall("s:c", NS)[6].find("s:v", NS).text), 45) for r in rows))


def verify(args, check):
    out, db = args.output_dir, args.data_dir / "genshin.db"
    request = {"action": "batch", "version_line": "release", "items": [{"id": cid, "request": {"action": "action_resource_profile",
               "character_id": dbid, "skill_id": skill}} for cid, _, dbid, skill, *_ in ACTORS]}
    queries = query_runtime(db, request)
    write(out / "resource-queries.json", queries)
    check("four exact source queries complete", queries["status"] == "complete" and queries["counts"]["ok"] == 4)
    for actor, item in zip(ACTORS, queries["items"], strict=True):
        cid, _, dbid, skill, element, cd, cost = actor
        fact = item["data"]
        check(cid + " exact source identity and positive fixed values", fact["selection"]["character_id"] == dbid
              and fact["selection"]["skill_id"] == skill and {k: v["value"] for k, v in fact["projection"].items()} == {
                  "base_cooldown_seconds": cd, "base_charge_count": 1, "base_energy_cost": cost, "cost_element": element})
    p = make_engineering_plan()
    initial = compose_equipment_plan(p, db)
    write(out / "initial-preview.json", initial)
    check("no hand spend and unconfirmed source remain pending", initial["state"] == "pending"
          and not any(e["type"] == "energy_spend" for e in p["rotation_model"]["events"]))
    for b, r in zip(p["action_resource_sources"], initial["action_resource_compilations"], strict=True):
        b.update(r["required_confirmation"])
    before = copy.deepcopy(p)
    calculated = run_plan_calc(p, db)
    write(out / "calculated.json", calculated)
    native = out / "automatic-energy-native.plan.json"
    write(native, calculated["plan"])
    result, rotation = calculated["result"], calculated["result"]["rotation_check"]
    audits = [r for r in rotation["event_log"] if r.get("delivery") == "automatic_action_start"]
    check("twelve explicit damage events total540", len(result["hits"]) == 12
          and math.isclose(result["summary"]["total_damage"], 540) and all(math.isclose(h["expected"], 45) for h in result["hits"]))
    check("twelve commits once without queued spend", len(audits) == 12
          and all("order" not in r and r["phase"] == "action_admission" for r in audits)
          and not any(e["type"] == "energy_spend" for e in rotation["resolved_rotation_model"]["events"]))
    for cid, _, _, _, _, _, cost in ACTORS:
        rows = [r for r in audits if r["character"] == cid]
        check(cid + " three independent cost commits", len(rows) == 3 and all(
            r["before"] == r["required"] == r["amount"] == cost and r["after"] == 0 for r in rows))
    check("native fields and full window preserved", p == before and numbers_omitted(calculated["plan"])
          and calculated["plan"]["rotation_model"] == p["rotation_model"] and p["rotation_model"]["period"] == 56)
    check("native round trip", fingerprint(input_view(read_plan(native)["plan"])) == fingerprint(input_view(calculated["plan"])))
    explicit = copy.deepcopy(p)
    for binding in explicit["action_resource_sources"]:
        binding.pop("energy_delivery")
    abilities = {a["id"]: a for a in explicit["rotation_model"]["abilities"]}
    for action in explicit["rotation_model"]["actions"]:
        ability = abilities[action["ability_id"]]
        if ability["action_type_id"] == "action.elemental_burst":
            explicit["rotation_model"]["events"].append({"id": action["id"] + ".manual-spend", "type": "energy_spend",
                "character": ability["character"], "action_id": action["id"], "t": action["start"], "order": 1,
                "repeat": {"mode": "once"}, "status": "assumed", "source": "显式参考，与新模式采用同一动作接纳口径"})
    reference = run_plan_calc(confirmed(explicit, db), db)
    write(out / "explicit-reference.json", reference)
    check("automatic result agrees with explicit v3 reference", reference["result"]["hits"] == result["hits"]
          and reference["result"]["summary"] == result["summary"]
          and reference["result"]["rotation_check"]["characters"] == rotation["characters"])
    failed = copy.deepcopy(p)
    failed["rotation_model"]["events"] = [e for e in failed["rotation_model"]["events"] if e["t"] != 23]
    failure = run_plan_calc(confirmed(failed, db), db)
    write(out / "missing-refill-calculated.json", failure)
    failed_check = failure["result"]["rotation_check"]
    check("missing declared refill fails second round without fabricated energy", failed_check["status"] == "failed"
          and failed_check["automatic_energy_delivery"]["committed_actions"] == 4
          and len(failure["result"]["hits"]) == 4 and math.isclose(failure["result"]["summary"]["total_damage"], 180))
    for kind, content in (("result", result_bytes(result, calculated["result_receipt"], "xlsx")),
                          ("formula", build_formula_workbook(calculated))):
        (out / (kind + ".xlsx")).write_bytes(content)
        workbook(content, check, kind, kind == "formula")
    check("formula energy model requires regeneration", formula_workbook_support(calculated)["editable"]["automatic_energy_delivery"] == "regenerate_required")
    bridge = GuiApi(args.data_dir, plans_dir=out / "gui-plans")
    gui = bridge.run_plan_calc(p)
    write(out / "gui-calculated.json", gui)
    check("hidden GUI backend automatic parity", gui["code"] == 0 and gui["data"]["result"] == result)
    commands = []
    for label, base in [("source-cli", [sys.executable, "-X", "utf8", "-m", "genshin_app"])] + ([("package-cli", [str(args.cli_exe)])] if args.cli_exe else []):
        for operation in ("calc", "export", "workbook"):
            target = out / f"{label}-{operation}.{'json' if operation == 'calc' else 'xlsx'}"
            argv = [*base, "--data-dir", str(args.data_dir), "equipment", operation, "--plan", str(native), "--output", str(target)]
            if operation == "calc": argv.append("--summary")
            if operation == "export": argv.extend(["--format", "xlsx"])
            started = time.perf_counter()
            proc = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf8", timeout=120, check=False)
            (out / f"{label}-{operation}.stdout.json").write_text(proc.stdout, "utf8")
            (out / f"{label}-{operation}.stderr.txt").write_text(proc.stderr, "utf8")
            commands.append({"label": label + "/" + operation, "exit": proc.returncode, "seconds": time.perf_counter() - started})
            check(label + " " + operation + " exits", proc.returncode == 0)
            if operation == "calc":
                full = json.loads(target.read_text("utf8"))
                summary = json.loads(proc.stdout)["data"]
                check(label + " calc independent arithmetic and summary", math.isclose(full["result"]["summary"]["total_damage"], 540)
                      and summary["automatic_energy_delivery"]["committed_actions"] == 12 and len(proc.stdout.encode("utf8")) < 12000)
            else:
                workbook(target.read_bytes(), check, label + "/" + operation, operation == "workbook")
    return {"scope": SCOPE, "native": str(native), "native_sha256": digest(native), "commands": commands,
            "expected_damage": 540, "explicit_q_actions": 12, "switch_actions": 11, "explicit_refills": 2,
            "input_reduction": {"manual_spend_objects_before": 12, "manual_spend_objects_after": 0,
                                "binding_mode_fields_added": 4, "same_explicit_actions": True,
                                "resource_numbers_already_automatic_before": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if args.cli_exe: args.cli_exe = args.cli_exe.resolve()
    if not args.output_dir.is_relative_to(OUTPUT_ROOT) or args.output_dir.exists():
        parser.error("Use a fresh directory under EC14; preserve old evidence")
    args.output_dir.mkdir(parents=True)
    db, checks = args.data_dir / "genshin.db", []
    before = digest(db)

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        assert value, name

    try:
        details = verify(args, check)
    except Exception as exc:
        write(args.output_dir / "failure.json", {"status": "failed", "error": type(exc).__name__, "message": str(exc),
            "details": getattr(exc, "details", None), "checks": checks,
            "database_sha256_before": before, "database_sha256_after": digest(db)})
        raise
    check("formal DB unchanged", digest(db) == before)
    write(args.output_dir / "verification.json", {"status": "passed", "passed": len(checks), "checks": checks,
        "source_version": APP_VERSION, "database_sha256": before,
        "exe_sha256": digest(args.cli_exe) if args.cli_exe else None, **details})
    print(json.dumps({"status": "passed", "passed": len(checks), "output": str(args.output_dir)}), flush=True)


if __name__ == "__main__":
    main()
