"""EC16b real source dispatch and one-calculation App/Excel delivery.

Engineering inputs with explicit calibrated timing; not a team DPS benchmark.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from pathlib import Path

from verify_action_events_delivery import NS, ROOT, check_workbook, make_plan, sha
from verify_action_events_delivery import SCOPE as OLD_SCOPE
from verify_calculation_delivery import sheets as workbook_sheets
from verify_energy_generation_delivery import CASES, PREFIX

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.energy_generation_schema import DEFAULT_SOURCE
from genshin_app.equipment.files import fingerprint, input_view, read_plan
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_db.runtime_queries import query_runtime

SCOPE = ("确定产球工程验证，非队伍DPS或游戏帧数实测：C0/10级、单目标、零卡肉参考，"
         "ATK1000、零暴击、同90级与10%抗性；0/20/40秒三个明确动作，1秒命中，1.5秒占用。"
         "产出延迟0.1秒、旅行0.5秒均为显式工程校准；不覆盖完整角色技能。"
         "冷却采用固定参考基础6/9/15秒，从占用结束计作保守工程限制，非精确游戏冷却起点。")
SAMPLES = (
    ("kazuha-tap", "枫原万叶", 10000047, "anemo", "tap", 0, 3, 3.4560000896453857, 6,
     "Avatar_Kazuha_ElementalArt_EmptyJump"),
    ("shenhe-hold", "申鹤", 10000063, "cryo", "hold", 1, 4, 3.398400068283081, 15, None),
    ("kazuha-hold", "枫原万叶", 10000047, "anemo", "hold", 2, 4, 4.694399833679199, 9,
     "Avatar_Kazuha_ElementalArt_FlyAway"),
)


def write(path, value):
    with path.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def evidence(value):
    if isinstance(value, dict):
        return {key: evidence(child) for key, child in value.items()}
    if isinstance(value, list):
        return [evidence(child) for child in value]
    return SCOPE if value == OLD_SCOPE else value


def origin(db, label):
    _, character, pointer, *_ = next(row for row in CASES if row[0] == label)
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        rows = [r for r in conn.execute(
            "SELECT reference_id,json_pointer FROM runtime_reference WHERE version_line='release' AND source_path=? AND field_name='configID'",
            (PREFIX + character + ".json",)) if r[1].rsplit("/", 1)[0] == pointer]
    assert len(rows) == 1
    return rows[0][0]


def native(db, sample, directory, check):
    key, name, cid, element, variant, param, _count, _multiplier, cooldown, ability = sample
    plan = evidence(make_plan(name, cid, element))
    plan["manual_template"]["meta"]["name"] = name + ("点按" if variant == "tap" else "长按") + "·自动产球工程"
    plan["manual_template"]["loop_duration"] = plan["rotation_model"]["period"] = 45
    plan["rotation_model"]["characters"][0]["initial_energy"] = 0
    plan["rotation_model"]["abilities"][0].update(variant=variant,
        cooldown={"group": "source-skill", "duration": cooldown, "start": "occupation_end", "charges": 1, "recovery": "sequential"})
    plan["rotation_model"]["initial_cooldowns"] = [{"character": "actor", "group": "source-skill", "ready_in": [0]}]
    for action, start in zip(plan["rotation_model"]["actions"], (0, 20, 40), strict=True):
        action.update(start=start, occupation_end=start + 1.5)
    plan["action_event_profiles"][0]["variant"] = variant
    plan["action_event_profiles"][0]["calibration"]["offset_seconds"] = 1
    plan["hit_parameter_sources"][0]["param_index"] = param
    if ability:
        query = {"action": "attack_metadata_profile", "character_id": cid, "slot": "skill", "constellation": 0}
        discovery = query_runtime(db, query)
        write(directory / (key + "-attack-discovery.json"), discovery)
        candidates = [c for c in discovery["candidates"] if c["ability_name"] == ability and c["status"] == "ready"]
        check(key + " exact attack selected independently", len(candidates) == 1)
        plan.pop("hit_parameter_sources")
        plan.pop("elemental_sources")
        plan["manual_template"]["hits"][0].pop("element")
        plan["mechanics_model"]["elemental_model"]["profiles"] = []
        plan["attack_metadata_sources"] = [{"action_event_profile_id": "skill-contact", "version_line": "release",
            "selection": {"character_id": cid, "slot": "skill", "constellation": 0}, "candidate_id": candidates[0]["candidate_id"],
            "shared_group": "declared-skill-shared", "purpose": "single_stat_damage_multiplier", "status": "assumed", "source": SCOPE}]
    else:
        # This remains the independent scalar path; the complex EC10 ratio is not promoted.
        plan["mechanics_model"]["elemental_model"]["profiles"][0]["gauge_units"] = 1
    preview = compose_equipment_plan(plan, db)
    write(directory / (key + "-facts-preview.json"), preview)
    for field, report in (("hit_parameter_sources", "hit_parameter_compilations"),
                          ("elemental_sources", "elemental_compilations"),
                          ("reaction_damage_sources", "reaction_damage_compilations")):
        for binding, record in zip(plan.get(field, []), preview.get(report, []) if field in plan else [], strict=True):
            binding.update(record["required_confirmation"])
    preview = compose_equipment_plan(plan, db)
    write(directory / (key + "-parent-preview.json"), preview)
    plan["action_event_profiles"][0].update(preview["action_event_compilations"][0]["required_confirmation"])
    declaration = copy.deepcopy(DEFAULT_SOURCE)
    declaration.update(id="skill-particles", action_event_profile_id="skill-contact", origin_reference_id=origin(db, key),
        scenario={"target_kind": "ordinary_non_animal_monster", "drop_allowed": True, "initial_controller": "inactive",
                  "pickup": "all_at_once", "hitlag_policy": "zero_hitlag_reference", "no_external_controller_changes": True,
                  "all_party_alive": True, "status": "assumed", "source": SCOPE},
        calibration={"spawn_delay_seconds": .1, "travel_seconds": .5, "precision_seconds": .01, "status": "assumed", "source": SCOPE})
    plan["energy_generation_sources"] = [declaration]
    preview = compose_equipment_plan(plan, db)
    write(directory / (key + "-energy-preview.json"), preview)
    record = preview["energy_generation_compilations"][0]
    check(key + " source requires exactly one acceptance", record["status"] == "pending"
          and {i["code"] for i in record["issues"]} == {"energy_generation_confirmation_required"}
          and not record["rules"] and record["required_confirmation"]["accepted_fingerprint"])
    declaration.update(record["required_confirmation"])
    return plan


def check_result(data, sample, check, label):
    _, _, _, _, _, _, count, multiplier, *_ = sample
    result, records = data["result"], data["result"]["energy_generation_compilations"]
    row, summary = records[0], result["energy_generation_summary"]
    check(label + " three legal explicit contacts", len(result["hits"]) == 3
          and [h["t"] for h in result["hits"]] == [1, 21, 41]
          and all(math.isclose(h["expected"], 450 * multiplier, abs_tol=1e-8) for h in result["hits"]))
    check(label + " independent source quantity", row["resource_projection"]["count"] == count
          and len(row["rules"]) == 1 and len(row["resource_ownership"]) == 3)
    check(label + " actual spawn and arrival", summary["planned_parent_hits"] == summary["reported_parent_hits"] == 3
          and summary["spawned_batches"] == summary["credited_batches"] == 3
          and summary["spawned_particles"] == summary["credited_particles"] == 3 * count
          and summary["in_flight_particles"] == 0)
    events = result["rotation_check"]["event_log"]
    spawns = [e for e in events if e.get("type") == "energy_spawn" and e.get("status") == "passed"]
    credits = [e for e in events if e.get("type") == "energy_credit" and e.get("status") == "passed"]
    check(label + " calibrated relative phases", [e["t"] for e in spawns] == [1.1, 21.1, 41.1]
          and [e["t"] for e in credits] == [1.6, 21.6, 41.6])
    gain = 3 * count
    check(label + " actual energy credited independently", all(len(row["energy"]) == 1
          and row["energy"][0]["character"] == "actor"
          and row["energy"][0]["on_field"] is True
          and all(math.isclose(row["energy"][0][field], expected, abs_tol=1e-9)
                  for field, expected in {"before": i * gain, "raw": gain, "gain": gain,
                                          "overflow": 0, "after": (i + 1) * gain}.items())
          for i, row in enumerate(credits))
          and result["rotation_check"]["characters"][0]["final_energy"] == 3 * gain)
    check(label + " partial reference and no fabricated native queue", row["whole_source_status"] == "partial"
          and row["dispatch_mode"] == "calibrated_reference" and not data["plan"]["rotation_model"]["events"]
          and not data["plan"]["mechanics_model"]["rules"])
    check(label + " receipt binds actual summary", data["result_receipt"]["energy_generation"]["summary_fingerprint"] == fingerprint(summary))


def verify(args, check):
    db, out = args.data_dir / "genshin.db", args.output_dir
    entries = []
    for sample in SAMPLES:
        key = sample[0]
        print("Real particle source " + key, flush=True)
        plan = native(db, sample, out, check)
        frozen = copy.deepcopy(plan)
        data = run_plan_calc(plan, db)
        write(out / (key + "-calculated.json"), data)
        write(out / (key + ".plan.json"), data["plan"])
        check_result(data, sample, check, key)
        check(key + " native declarations not mutated", plan == frozen)
        moved = copy.deepcopy(plan)
        moved["rotation_model"]["actions"][1].update(start=22, occupation_end=23.5)
        moved_result = run_plan_calc(moved, db)["result"]
        check(key + " movement preserves source acceptance", moved_result["energy_generation_compilations"][0]["accepted_fingerprint"]
              == data["result"]["energy_generation_compilations"][0]["accepted_fingerprint"]
              and [h["t"] for h in moved_result["hits"]] == [1, 23, 41])
        missed = copy.deepcopy(plan)
        missed["action_event_overrides"] = [{"action_id": "cast.2", "landed": False, "status": "assumed", "source": "独立工程落空反例"}]
        missed_result = run_plan_calc(missed, db)["result"]
        check(key + " miss suppresses only that batch", missed_result["energy_generation_summary"]["spawned_particles"] == 2 * sample[6]
              and missed_result["energy_generation_summary"]["missed_parent_hits"] == 1)
        deleted = copy.deepcopy(plan)
        deleted["rotation_model"]["actions"].pop(1)
        check(key + " deleting action removes its particles", run_plan_calc(deleted, db)["result"]["energy_generation_summary"]["spawned_batches"] == 2)
        if key == "kazuha-tap":
            gui = GuiApi(args.data_dir, plans_dir=out / "gui-plans")
            saved = out / "gui-saved.plan.json"
            check("GUI bridge saves native source", gui.save_plan(str(saved), data["plan"])["code"] == 0)
            loaded = gui.load_plan(str(saved))
            check("GUI bridge fresh load ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
            check("GUI bridge calculates same result", gui.run_plan_calc(data["plan"])["data"]["result"] == data["result"])
        entries.append((sample, out / (key + ".plan.json"), data))
    for runtime, prefix in [("source", [sys.executable, "-X", "utf8", "-m", "genshin_app"])] + (
            [("package", [str(args.cli_exe)])] if args.cli_exe else []):
        for sample, plan_path, reference in entries:
            key = runtime + "-" + sample[0]
            destination = out / key
            proc = subprocess.run([*prefix, "--data-dir", str(args.data_dir), "equipment", "deliver", "--plan", str(plan_path),
                                   "--output-dir", str(destination)], capture_output=True, encoding="utf8", cwd=ROOT, timeout=240,
                                   check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            write(out / (key + "-process.json"), {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
            check(key + " one-command complete delivery", proc.returncode == 0)
            reply = json.loads(proc.stdout)["data"]
            check(key + " compact committed result", reply["index_committed"] and reply["publication_status"] == "complete"
                  and len(proc.stdout.encode("utf8")) < 65536)
            index = json.loads((destination / "index.json").read_text("utf8"))
            generation = (destination / index["generation"]).resolve()
            manifest_path = (destination / index["manifest"]["path"]).resolve()
            check(key + " manifest identity", generation.is_relative_to(destination.resolve())
                  and manifest_path.parent == generation and sha(manifest_path) == index["manifest"]["sha256"]
                  and manifest_path.stat().st_size == index["manifest"]["bytes"])
            manifest = json.loads(manifest_path.read_text("utf8"))
            check(key + " exactly four artifact roles", len(index["artifacts"]) == 4
                  and {item["role"] for item in index["artifacts"]} == {
                      "native_plan", "calculated", "result_workbook", "formula_workbook"})
            paths = {}
            for item, inner in zip(index["artifacts"], manifest["artifacts"], strict=True):
                file = (destination / item["path"]).resolve()
                check(key + " artifact identity " + item["role"], file.parent == generation and file.is_file()
                      and sha(file) == item["sha256"] and file.stat().st_size == item["bytes"]
                      and inner == {**item, "path": file.name})
                paths[item["role"]] = file
            native_plan = read_plan(paths["native_plan"])["plan"]
            check(key + " delivered native identity", fingerprint(input_view(native_plan)) == fingerprint(input_view(reference["plan"])))
            package = json.loads(paths["calculated"].read_text("utf8"))
            calculated = package["calculated"]
            check(key + " complete calculated snapshot integrity", package["package_schema"] == "genshin.calculated-package.v1"
                  and package["kind"] == "historical_calculation_snapshot"
                  and package["integrity"] == {"calculated_fingerprint": fingerprint(calculated),
                      "report_fingerprint": fingerprint(calculated["report"]),
                      "display_context_fingerprint": fingerprint(calculated["display_context"]),
                      "equipment_summary_fingerprint": fingerprint(calculated["equipment_summary"])})
            check(key + " actual result and energy receipts match", calculated["result"] == reference["result"]
                  and calculated["report"]["plan"] == calculated["plan"]
                  and all(calculated["result_receipt"][field] == reference["result_receipt"][field]
                          for field in ("input_fingerprint", "result_fingerprint", "source_fingerprint", "energy_generation", "action_events"))
                  and manifest["input_fingerprint"] == calculated["result_receipt"]["input_fingerprint"]
                  and manifest["result_fingerprint"] == fingerprint(calculated["result"]))
            check_result(calculated, sample, check, key + " saved payload")
            # Role names come from the actual index; inspect every delivered workbook.
            workbooks = [file for file in paths.values() if file.suffix == ".xlsx"]
            check(key + " two workbook forms", len(workbooks) == 2)
            for file in workbooks:
                formula = file == paths["formula_workbook"]
                check_workbook(file.read_bytes(), check, key + " " + file.name, 450 * sample[7], formula=formula)
                with zipfile.ZipFile(file) as archive:
                    sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
                    page_index = next(i for i, sheet in enumerate(sheets, 1) if sheet.get("name") == "产球来源")
                    page = ET.fromstring(archive.read(f"xl/worksheets/sheet{page_index}.xml"))
                    check(key + " protected energy source " + file.name, page.find("s:sheetProtection", NS) is not None
                          and not page.findall(".//s:f", NS) and "zero_hitlag_reference" in "".join(page.itertext()))
                if not formula:
                    cells = {cell.get("r"): cell for cell in workbook_sheets(file)["逐事件明细"].findall(".//s:c", NS)}
                    check(key + " static independent three hit values", all(
                        math.isclose(float(cells[f"G{row}"].find("s:v", NS).text), 450 * sample[7], abs_tol=1e-9)
                        for row in (2, 3, 4)))
            total = sum(file.stat().st_size for file in paths.values()) + manifest_path.stat().st_size + (destination / "index.json").stat().st_size
            check(key + " complete byte accounting", total == manifest["total_bytes"])
    return [{"case": sample[0], "native_path": str(path), "summary": data["result"]["energy_generation_summary"]}
            for sample, path, data in entries]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    assert args.output_dir.is_relative_to(ROOT / "outputs") and not args.output_dir.exists()
    args.output_dir.mkdir(parents=True)
    db = args.data_dir / "genshin.db"
    before, checks, start = sha(db), [], time.perf_counter()

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        assert value, name

    try:
        observations = verify(args, check)
        check("formal database unchanged", sha(db) == before)
        receipt = {"status": "passed", "passed": len(checks), "checks": checks, "version": APP_VERSION,
                   "database_sha256": before, "exe_sha256": sha(args.cli_exe) if args.cli_exe else None,
                   "scope": SCOPE, "observations": observations, "wall_seconds": time.perf_counter() - start}
        write(args.output_dir / "verification.json", receipt)
        print(json.dumps({"status": "passed", "checks": len(checks), "output": str(args.output_dir)}))
    except BaseException as exc:
        write(args.output_dir / "failure.json", {"error": str(exc), "checks": checks, "database_unchanged": sha(db) == before})
        raise


if __name__ == "__main__":
    main()
