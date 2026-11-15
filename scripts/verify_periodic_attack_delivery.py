"""EC13b real Q sources, explicit multi-contact actions, GUI bridge and Excel."""
from __future__ import annotations

import argparse
import copy
import hashlib
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
sys.path.insert(0, str(ROOT / "src"))
from verify_action_events_delivery import SCOPE as OLD_SCOPE
from verify_action_events_delivery import make_plan as base_plan

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, input_view, read_plan
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_queries import query_runtime

OUTPUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec13b"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MULTIPLIERS = {"slice": 2.021399974822998, "blast": 3.032099962234497}
TOTAL = 1500.889481306076015
MISSED = 1409.926482439041105
SCOPE = ("周期动作工程验证：实际C0/10级神里绫华Q主创建来源、切割和独立启动攻击；给定ATK100、零暴击、同90级、10%抗性。"
         "动作0/10/20秒、占用0.5秒、建立0.1秒、结束1.2秒、首击0.2秒、切割4次及独立攻击1.1秒均为工程校准，非游戏实测次数或生命周期。"
         "数据库提供倍率/冰元素/1GU/附着规则/0.25秒周期；共享计数、安全接触、存活无中断无替换和当前取样显式声明。"
         "完整输入窗口22秒，不复制轮次；为隔离伤害派发测试明确使用虚构零耗能和无CD，逐动作仍提供零值扣能事件。"
         "不自动产球、不证明真实角色CD/能量循环、不表示完整技能或真实队伍DPS。")


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def scope(value):
    if isinstance(value, dict):
        return {key: scope(child) for key, child in value.items()}
    if isinstance(value, list):
        return [scope(child) for child in value]
    return SCOPE if value == OLD_SCOPE else value


def make_plan(origin, candidates):
    p = scope(base_plan("神里绫华", 10000002, "cryo"))
    evidence = {"status": "assumed", "source": SCOPE}
    template = p["manual_template"]
    template["meta"]["name"] = "周期动作与独立攻击·工程验证"
    template["loop_duration"] = 22
    template["characters"][0]["stats"]["atk"] = 100
    template["hits"] = [{"id": key, "t": 0, "character": "actor", "skill": "元素爆发",
        "hit_name": "校准周期切割" if key == "slice" else "校准独立攻击",
        "damage_type_ids": ["damage.elemental_burst"], "damage_system": "classic", "scaling_stat": "atk", "count": 1}
        for key in MULTIPLIERS]
    rotation = p["rotation_model"]
    rotation["period"] = 22
    rotation["abilities"][0].update(id="burst", action_type_id="action.elemental_burst", variant="default",
                                     energy={"required": 0, "amount": 0})
    rotation["actions"] = [{"id": f"cast.{i+1}", "ability_id": "burst", "start": t, "occupation_end": t+.5,
                            "order": 0, "repeat": {"mode": "once"}, **evidence} for i, t in enumerate((0, 10, 20))]
    rotation["events"] = [{"id": f"spend.{i+1}", "type": "energy_spend", "action_id": f"cast.{i+1}",
        "character": "actor", "t": t, "order": 1, "required": 0, "amount": 0,
        "repeat": {"mode": "once"}, **evidence} for i, t in enumerate((0, 10, 20))]
    p.pop("hit_parameter_sources")
    p.pop("elemental_sources")
    p["mechanics_model"]["elemental_model"]["profiles"] = []
    channels = [{"channel_id": key, "prototype_hit_id": key, "application_profile_id": key + ".application",
        "contact_profile_id": "safe-contact", "default_landed": True, "self_sampling": "current",
        "schedule": {**({"kind": "periodic", "first_offset_seconds": .2, "count": 4} if key == "slice"
                        else {"kind": "single", "offset_seconds": 1.1}), "precision_seconds": .01, **evidence}}
        for key in MULTIPLIERS]
    p["action_event_profiles"] = [{"id": "burst-contacts", "kind": "calibrated_multi_contact_v1", "variant": "default",
        "channels": channels, "lifecycle": {"establishment_offset_seconds": .1, "instance_end_offset_seconds": 1.2,
            "precision_seconds": .01, "owner_and_target_survive": True, "no_interrupt": True,
            "no_refresh_replacement_or_early_despawn": True, **evidence}, "accepted_profile_fingerprint": None}]
    p["action_event_bindings"] = [{"ability_id": "burst", "profile_id": "burst-contacts"}]
    p["periodic_attack_sources"] = [{"action_event_profile_id": "burst-contacts", "version_line": "release",
        "selection": {"character_id": 10000002, "slot": "burst", "constellation": 0}, "origin_reference_id": origin,
        "channels": [{"channel_id": key, "candidate_id": candidates[key], "shared_group": "declared-burst-shared"} for key in MULTIPLIERS],
        "purpose": "calibrated_periodic_attack", **evidence}]
    return p


def workbook(content, check, label, expected, *, formula):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        sheets = {name.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, name in enumerate(names, 1)}
    for name in ("周期攻击来源", "动作派发来源"):
        page = sheets[name]
        check(label + " protected source " + name, page.find("s:sheetProtection", NS) is not None and not page.findall(".//s:f", NS))
    page = sheets["周期攻击来源"]
    check(label + " complete evidence retained", "creation_bundle" in "".join(page.itertext()) and "source_fingerprint" in "".join(page.itertext()))
    check(label + " no Excel error cache", not any(c.get("t") == "e" for sheet in sheets.values() for c in sheet.findall(".//s:c", NS)))
    sheet = sheets["逐事件" if formula else "逐事件明细"]
    cells = {cell.get("r"): cell for cell in sheet.findall(".//s:c", NS)}
    first, column = (5, "V") if formula else (2, "G")
    check(label + " independent per contact damage", all(math.isclose(float(cells[f"{column}{first+i}"].find("s:v", NS).text), value, abs_tol=1e-8) for i, value in enumerate(expected)))
    if formula:
        check(label + " real damage formulas", all(cells[f"V{5+i}"].find("s:f", NS) is not None for i in range(len(expected))))
        fixed_rows = [r for r in sheets["参数"].findall("s:sheetData/s:row", NS) if any(k in "".join(r.itertext()) for k in ("periodic_attack_sources[", "action_event_profiles[", "].multiplier"))]
        check(label + " source and calibration fixed", bool(fixed_rows) and all(r.findall("s:c", NS)[2].get("s") == "5" and r.findall("s:c", NS)[8].find("s:f", NS) is not None for r in fixed_rows))


def verify(args, check):
    db, out = args.data_dir / "genshin.db", args.output_dir
    request = {"action": "periodic_attack_profile", "character_id": 10000002, "slot": "burst", "constellation": 0, "version_line": "release"}
    with equipment_read_session(db):
        discovery = query_runtime(db, request)
        write(out / "origin-discovery.json", discovery)
        origins = [c for c in discovery["creation_candidates"] if c["gadget_id"] == 41002001]
        check("one independently identified main creation leaf", len(origins) == 1 and discovery["status"] == "pending")
        origin = origins[0]["origin_reference_id"]
        channels = query_runtime(db, {**request, "origin_reference_id": origin})
        write(out / "channel-discovery.json", channels)
        chosen = {"slice" if c["channel_kind"] == "periodic_callback_attack" else "blast": c["candidate_id"] for c in channels["candidates"]}
        check("two separate supported channel kinds", len(channels["candidates"]) == 2 and set(chosen) == set(MULTIPLIERS))
        selected = query_runtime(db, {**request, "origin_reference_id": origin, "candidate_ids": list(chosen.values())})
        write(out / "selected-source.json", selected)
    check("local source ready is not native dispatch proof", selected["status"] == "ready" and selected["action_creation_association"] == "unproven" and selected["dispatch_semantics_status"] == "not_proven")
    for c in selected["selected_candidates"]:
        key = "slice" if c["channel_kind"] == "periodic_callback_attack" else "blast"
        p = c["projection"]
        check(key + " fixed source facts", p["parameter"]["value"] == MULTIPLIERS[key] and p["element"]["value"] == "cryo" and p["gauge"]["value"] == 1 and p["classification"]["value"] == "damage.elemental_burst")
        check(key + " timer meaning", p["timer"]["ticks"] == (250000 if key == "slice" else None))
    p = make_plan(origin, chosen)
    preview = compose_equipment_plan(p, db)
    write(out / "initial-preview.json", preview)
    check("fresh source cannot bypass profile acceptance", preview["state"] == "pending" and not preview["action_event_compilations"][0]["generated"])
    for declared, record in zip(p["reaction_damage_sources"], preview["reaction_damage_compilations"], strict=True):
        declared.update(record["required_confirmation"])
    preview = compose_equipment_plan(p, db)
    write(out / "calibration-preview.json", preview)
    required = preview["action_event_compilations"][0]["required_confirmation"]
    check("explicit composite acceptance available", isinstance(required, dict) and bool(required.get("accepted_profile_fingerprint")))
    p["action_event_profiles"][0].update(required)
    original = copy.deepcopy(p)
    calculated = run_plan_calc(p, db)
    write(out / "calculated.json", calculated)
    native = out / "periodic-attack-native.plan.json"
    write(native, calculated["plan"])
    result = calculated["result"]
    expected = [value for _ in range(3) for value in ([45*MULTIPLIERS["slice"]]*4+[45*MULTIPLIERS["blast"]])]
    check("independent fifteen hit arithmetic", len(result["hits"]) == 15 and math.isclose(result["summary"]["total_damage"], TOTAL, abs_tol=1e-8) and all(math.isclose(h["expected"], v, abs_tol=1e-8) for h, v in zip(result["hits"], expected, strict=True)))
    check("explicit full window only", [h["t"] for h in result["hits"]] == [t+offset for t in (0, 10, 20) for offset in (.2, .45, .7, .95, 1.1)] and p["rotation_model"]["period"] == 22)
    check("native remains source plus declarations", p == original and len(calculated["plan"]["manual_template"]["hits"]) == 2 and not calculated["plan"]["mechanics_model"]["hit_patterns"] and all("multiplier" not in h and "element" not in h for h in calculated["plan"]["manual_template"]["hits"]))
    check("native round trip", fingerprint(input_view(read_plan(native)["plan"])) == fingerprint(input_view(calculated["plan"])))
    missed = copy.deepcopy(p)
    missed["action_event_overrides"] = [{"kind": "calibrated_multi_contact_v1", "action_id": "cast.2", "contacts": [{"channel_id": "slice", "ordinal": 2, "landed": False, "status": "assumed", "source": "独立工程反例：第二动作第二次切割落空"}]}]
    missed_result = run_plan_calc(missed, db)
    write(out / "missed-contact-calculated.json", missed_result)
    check("one miss independent arithmetic", len(missed_result["result"]["hits"]) == 14 and math.isclose(missed_result["result"]["summary"]["total_damage"], MISSED, abs_tol=1e-8))
    check("miss keeps all contact identities", [g["hit_id"] for g in result["action_event_compilations"][0]["generated"]] == [g["hit_id"] for g in missed_result["result"]["action_event_compilations"][0]["generated"]])
    changed = copy.deepcopy(p)
    changed["action_event_profiles"][0]["channels"][0]["schedule"]["count"] = 3
    pending = compose_equipment_plan(changed, db)
    check("calibration change cannot reuse old acceptance", pending["state"] == "pending" and not pending["action_event_compilations"][0]["generated"])
    for kind, content in (("result", result_bytes(result, calculated["result_receipt"], "xlsx")), ("formula", build_formula_workbook(calculated))):
        (out / (kind + ".xlsx")).write_bytes(content)
        workbook(content, check, kind, expected, formula=kind == "formula")
    support = formula_workbook_support(calculated)
    check("workbook source edits require regeneration", support["editable"]["periodic_attack_sources"] == "regenerate_required")
    bridge = GuiApi(args.data_dir, plans_dir=out / "gui-plans")
    gui = bridge.run_plan_calc(p)
    write(out / "gui-bridge-calc.json", gui)
    check("hidden GUI backend parity", gui["code"] == 0 and math.isclose(gui["data"]["result"]["summary"]["total_damage"], TOTAL, abs_tol=1e-8))
    commands = []
    for label, base in [("source-cli", [sys.executable, "-X", "utf8", "-m", "genshin_app"])] + ([("package-cli", [str(args.cli_exe)])] if args.cli_exe else []):
        for operation in ("calc", "export", "workbook"):
            target = out / f"{label}-{operation}.{'json' if operation == 'calc' else 'xlsx'}"
            argv = [*base, "--data-dir", str(args.data_dir), "equipment", operation, "--plan", str(native), "--output", str(target)]
            if operation == "calc":
                argv.append("--summary")
            if operation == "export":
                argv.extend(["--format", "xlsx"])
            started = time.perf_counter()
            proc = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf8", timeout=120, check=False)
            (out / f"{label}-{operation}.stdout.json").write_text(proc.stdout, "utf8")
            (out / f"{label}-{operation}.stderr.txt").write_text(proc.stderr, "utf8")
            commands.append({"label": label+"/"+operation, "exit": proc.returncode, "seconds": time.perf_counter()-started})
            check(label + " " + operation + " exits", proc.returncode == 0)
            if operation == "calc":
                full = json.loads(target.read_text("utf8"))
                check(label + " calc arithmetic", math.isclose(full["result"]["summary"]["total_damage"], TOTAL, abs_tol=1e-8))
                check(label + " bounded summary", len(proc.stdout.encode("utf8")) < 12000)
            else:
                workbook(target.read_bytes(), check, label+"/"+operation, expected, formula=operation == "workbook")
    return {"native": str(native), "native_sha256": sha(native), "commands": commands, "scope": SCOPE,
        "expected_total": TOTAL, "expected_after_one_miss": MISSED, "window_seconds": 22, "explicit_actions": 3,
        "input_reduction": {"manual_pattern_containers_before": 6, "native_generated_patterns": 0,
            "manual_offset_numbers_before": 15, "schedule_scalars": 5, "precision_declarations": 3,
            "profiles": 1, "bindings": 1, "sources": 1, "prototypes": 2, "other_identity_and_semantics_declarations_retained": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if not args.output_dir.is_relative_to(OUTPUT_ROOT) or args.output_dir.exists():
        parser.error("Use a fresh output directory under EC13b")
    args.output_dir.mkdir(parents=True)
    db = args.data_dir / "genshin.db"
    before = sha(db)
    checks = []

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        assert value, name

    try:
        details = verify(args, check)
    except Exception as exc:
        write(args.output_dir / "failure.json", {"status": "failed", "message": str(exc),
            "reason": getattr(exc, "reason", type(exc).__name__), "details": getattr(exc, "details", None),
            "checks_completed": checks, "database_sha256_before": before, "database_sha256_after": sha(db)})
        raise
    check("formal database unchanged", sha(db) == before)
    report = {"status": "passed", "source_version": APP_VERSION, "passed": len(checks), "checks": checks,
        "database_sha256": before, "cli_exe": str(args.cli_exe) if args.cli_exe else None,
        "exe_sha256": sha(args.cli_exe) if args.cli_exe else None, **details}
    write(args.output_dir / "verification.json", report)
    print(json.dumps({"status": "passed", "passed": len(checks), "output": str(args.output_dir)}), flush=True)


if __name__ == "__main__":
    main()
