"""EC10 real-source metadata reduction, one parent acceptance and portable delivery."""
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
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from verify_action_events_delivery import (
    NS,
    ROOT,
    check_workbook,
    confirm,
    make_plan,
    sha,
    write,
)
from verify_action_events_delivery import (
    SCOPE as OLD_SCOPE,
)

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_queries import query_runtime

OUTPUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec10"
SCOPE = ("攻击元信息工程验证：实际C0/10级E来源；ATK1000、零暴击、同90级、10%抗性。"
         "三次明确动作0/12/24秒，0.5秒偏移、0.8秒占用、变体、命中及接触均为工程声明。"
         "数据库投影参数/元素/原始GU/衰减配置；单ATK用途与共享ICD身份显式声明。"
         "非游戏帧数测量，不自动产球，不证明真实角色资源循环或整个技能支持。")
SAMPLES = (
    ("ayaka", "神里绫华", 10000002, "cryo", "tap", "Avatar_Ayaka_Icespine_Strike_New", 0, 2, 4.305600166320801),
    ("kazuha-tap", "枫原万叶", 10000047, "anemo", "tap", "Avatar_Kazuha_ElementalArt_EmptyJump", 0, 1, 3.4560000896453857),
    ("kazuha-hold", "枫原万叶", 10000047, "anemo", "hold", "Avatar_Kazuha_ElementalArt_FlyAway", 2, 2, 4.694399833679199),
)


def evidence_text(value):
    if isinstance(value, dict):
        return {key: evidence_text(child) for key, child in value.items()}
    if isinstance(value, list):
        return [evidence_text(child) for child in value]
    return SCOPE if value == OLD_SCOPE else value


def request(cid, candidate_id=None):
    result = {"action": "attack_metadata_profile", "version_line": "release",
              "character_id": cid, "slot": "skill", "constellation": 0}
    if candidate_id is not None:
        result["candidate_id"] = candidate_id
    return result


def metadata_workbook(content, check, label, expected, formula):
    check_workbook(content, check, label, expected, formula=formula)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        sheets = {item.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, item in enumerate(names, 1)}
    page = sheets["攻击元信息来源"]
    check(label + " metadata protected complete literal", page.find("s:sheetProtection", NS) is not None
          and not page.findall(".//s:f", NS) and "source_fingerprint" in "".join(page.itertext())
          and "父链" in "".join(page.itertext()))
    if formula:
        rows = sheets["参数"].findall("s:sheetData/s:row", NS)
        source_rows = [row for row in rows if "attack_metadata_sources[" in "".join(row.itertext())]
        numeric = [row for row in rows if "].multiplier" in "".join(row.itertext())]
        check(label + " source and generated multipliers fixed", len(numeric) == 3 and bool(source_rows)
              and all(row.findall("s:c", NS)[2].get("s") == "5" for row in source_rows + numeric))


def verify(args, check):
    db, directory = args.data_dir / "genshin.db", args.output_dir
    discovered = {}
    with equipment_read_session(db):
        for cid in (10000002, 10000047, 10000063, 10000054):
            discovered[cid] = query_runtime(db, request(cid))
            write(directory / f"discovery-{cid}.json", discovered[cid])
    check("discovery never auto selects a candidate", all(q["selected_candidate"] is None for q in discovered.values()))
    negative = [c for cid in (10000063, 10000054) for c in discovered[cid]["candidates"]]
    check("complex skill sources remain pending", bool(negative) and all(c["status"] == "pending" for c in negative))
    anti = [c for c in discovered[10000047]["candidates"]
            if any(i["code"] == "attack_element_unsupported" for i in c["issues"])]
    check("non damage detection remains pending", bool(anti) and all(c["status"] == "pending" for c in anti))
    deliveries = []
    for label, name, cid, element, variant, ability, index, gu, multiplier in SAMPLES:
        print("EC10 real source " + label, flush=True)
        candidates = [c for c in discovered[cid]["candidates"] if c["ability_name"] == ability and c["status"] == "ready"]
        check(label + " unique independently chosen real source", len(candidates) == 1)
        candidate = candidates[0]
        selected = query_runtime(db, request(cid, candidate["candidate_id"]))
        write(directory / (label + "-selected-source.json"), selected)
        projection = selected["selected_candidate"]["projection"]
        check(label + " independent metadata facts", selected["status"] == "ready"
              and projection["parameter"]["param_index"] == index and projection["parameter"]["value"] == multiplier
              and projection["element"]["value"] == element and projection["gauge"]["value"] == gu
              and projection["gauge"]["raw_durability"] == gu * 25 and projection["gauge"]["tax_applied"] is False)
        explicit = evidence_text(make_plan(name, cid, element))
        explicit["action_event_profiles"][0]["variant"] = explicit["rotation_model"]["abilities"][0]["variant"] = variant
        explicit["hit_parameter_sources"][0]["param_index"] = index
        explicit["mechanics_model"]["elemental_model"]["profiles"][0]["gauge_units"] = gu
        plan = copy.deepcopy(explicit)
        plan.pop("hit_parameter_sources")
        plan.pop("elemental_sources")
        plan["manual_template"]["hits"][0].pop("element")
        plan["mechanics_model"]["elemental_model"]["profiles"] = []
        plan["attack_metadata_sources"] = [{"action_event_profile_id": "skill-contact", "version_line": "release",
            "selection": {"character_id": cid, "slot": "skill", "constellation": 0}, "candidate_id": candidate["candidate_id"],
            "shared_group": "declared-skill-shared", "purpose": "single_stat_damage_multiplier", "status": "assumed", "source": SCOPE}]
        preview = compose_equipment_plan(plan, db)
        write(directory / (label + "-source-preview.json"), preview)
        check(label + " metadata ready cannot bypass parent acceptance", preview["state"] == "pending"
              and preview["attack_metadata_compilations"][0]["status"] == "ready"
              and not preview["action_event_compilations"][0]["generated"])
        # The existing global reaction source is independent of this attack bundle.
        plan["reaction_damage_sources"][0].update(preview["reaction_damage_compilations"][0]["required_confirmation"])
        preview = compose_equipment_plan(plan, db)
        write(directory / (label + "-profile-preview.json"), preview)
        check(label + " one attack parent confirmation available", preview["state"] == "pending"
              and bool(preview["action_event_compilations"][0].get("required_confirmation")))
        plan["action_event_profiles"][0].update(preview["action_event_compilations"][0]["required_confirmation"])
        original = copy.deepcopy(plan)
        data = run_plan_calc(plan, db)
        result = data["result"]
        path = directory / (label + "-native.plan.json")
        write(path, data["plan"])
        write(directory / (label + "-calculated.json"), data)
        expected = 450 * multiplier
        check(label + " independent three hit damage", len(result["hits"]) == 3
              and all(math.isclose(h["expected"], expected, abs_tol=1e-9) for h in result["hits"]))
        check(label + " reduced native intact", plan == original and len(data["plan"]["attack_metadata_sources"]) == 1
              and not ({"element", "multiplier"} & data["plan"]["manual_template"]["hits"][0].keys())
              and not data["plan"].get("hit_parameter_sources") and not data["plan"].get("elemental_sources")
              and not data["plan"]["mechanics_model"]["elemental_model"]["profiles"]
              and not data["plan"]["mechanics_model"]["hit_patterns"])
        explicit = confirm(explicit, db, directory, check, label + "-explicit")
        baseline = run_plan_calc(explicit, db)
        write(directory / (label + "-explicit-calculated.json"), baseline)
        check(label + " equals explicit same-facts damage and elemental state", result["hits"] == baseline["result"]["hits"]
              and result["elemental"]["trace"] == baseline["result"]["elemental"]["trace"])
        if element == "cryo":
            applications = [r for r in result["elemental"]["trace"] if r["type"] == "elemental_application"]
            check("2 GU receives aura tax only once", math.isclose(applications[0]["after"]["ordinary_aura"]["remaining_gu"], 1.6))
        if label == "ayaka":
            gui = GuiApi(args.data_dir, plans_dir=directory / "gui-plans")
            saved = directory / "gui-saved.plan.json"
            check("GUI metadata save", gui.save_plan(str(saved), data["plan"])["code"] == 0)
            loaded = gui.load_plan(str(saved))
            check("GUI metadata load ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
            check("GUI metadata recalculate same", gui.run_plan_calc(read_plan(saved)["plan"])["data"]["result"] == result)
            moved = copy.deepcopy(plan)
            moved["rotation_model"]["actions"][1].update(start=13, occupation_end=13.8)
            moved_result = run_plan_calc(moved, db)["result"]
            check("metadata reuses acceptance when only one action moves", [h["t"] for h in moved_result["hits"]] == [.5, 13.5, 24.5]
                  and [h["id"] for h in moved_result["hits"]] == [h["id"] for h in result["hits"]])
        for kind, content in (("snapshot", result_bytes(result, data["result_receipt"], "xlsx")), ("formula", build_formula_workbook(data))):
            (directory / f"{label}-{kind}.xlsx").write_bytes(content)
            metadata_workbook(content, check, label + " " + kind, expected, kind == "formula")
        check(label + " metadata not editable in workbook", formula_workbook_support(data)["editable"]["attack_metadata_sources"] == "regenerate_required")
        deliveries.append({"label": label, "plan": str(path), "request": request(cid, candidate["candidate_id"]), "expected_single_hit": expected})
    commands = []
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"}
    prefixes = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        prefixes.append(("package-cli", [str(args.cli_exe)]))
    for prefix_label, prefix in prefixes:
        print("EC10 hidden CLI " + prefix_label, flush=True)
        for entry in deliveries:
            label = prefix_label + "-" + entry["label"]
            req_path = directory / (label + "-request.json")
            write(req_path, entry["request"])
            operations = [("query", ["db", "runtime", "--input", str(req_path), "--summary"], "json"),
                          ("calc", ["equipment", "calc", "--plan", entry["plan"], "--summary"], "json")]
            if entry["label"] == "ayaka":
                operations += [("export", ["equipment", "export", "--plan", entry["plan"], "--format", "xlsx"], "xlsx"),
                               ("workbook", ["equipment", "workbook", "--plan", entry["plan"]], "xlsx")]
            for operation, middle, extension in operations:
                target = directory / f"{label}-{operation}.{extension}"
                argv = [*prefix, "--data-dir", str(args.data_dir), *middle, "--output", str(target)]
                start = time.perf_counter()
                proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, encoding="utf8", timeout=180,
                                      check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                write(directory / (target.name + ".command.json"), {"argv": argv, "exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
                commands.append({"name": label + " " + operation, "exit": proc.returncode, "seconds": time.perf_counter()-start})
                check(label + " " + operation + " exit", proc.returncode == 0 and target.is_file())
                if operation == "query":
                    payload = json.loads(target.read_text("utf8"))
                    check(label + " query same selected evidence", payload["status"] == "ready" and payload["selected_candidate"]["candidate_id"] == entry["request"]["candidate_id"] and len(proc.stdout.encode()) < 4500)
                elif operation == "calc":
                    payload = json.loads(target.read_text("utf8"))
                    summary = json.loads(proc.stdout)["data"]
                    check(label + " compact metadata summary and correct damage", len(proc.stdout.encode()) < 4500
                          and summary["attack_metadata"]["source_count"] == 1
                          and math.isclose(payload["result"]["summary"]["total_damage"], entry["expected_single_hit"] * 3, abs_tol=1e-9))
                else:
                    metadata_workbook(target.read_bytes(), check, label + " " + operation, entry["expected_single_hit"], operation == "workbook")
    return {"deliveries": deliveries, "commands": commands,
            "input_reduction": {"manual_fact_locations_removed": 5, "fact_kinds_removed": 4, "source_rows_before": 2, "source_rows_after": 1,
                                "candidate_selections_added": 1, "native_application_profiles_before": 1, "native_application_profiles_after": 0,
                                "per_action_rows_already_automatic_before_this_release": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if OUTPUT_ROOT.resolve() not in args.output_dir.parents or args.output_dir.exists():
        parser.error("输出须为EC10下的新子目录，保留旧证据。")
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
            "cli_exe": str(args.cli_exe) if args.cli_exe else None, "passed": len(checks), "checks": checks,
            "database_sha256_before": before, "database_sha256_after": after, "scope": SCOPE, **details})
        print(json.dumps({"passed": len(checks), "output": str(args.output_dir)}), flush=True)
    except Exception as exc:
        write(args.output_dir / "failure.json", {"error": type(exc).__name__, "message": str(exc), "checks": checks})
        raise


if __name__ == "__main__":
    main()
