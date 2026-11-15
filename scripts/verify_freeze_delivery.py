"""Deliver EC06's independent 20-input ledger through real read-only sources.

No test constructors, foreground UI, database writes or old delivery scripts are
used. The engineering inputs are not a real character rotation or game truth.
Every run requires a new child directory under the EC06 output directory.
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
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EC06 = ROOT / "outputs/engine-continuation-20261002/ec06"
ORACLE = ROOT / "tests/fixtures/freeze_review_expected.json"
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_app.version import APP_VERSION
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_reaction_damage import get_runtime_reaction_damage_profile
from genshin_engine.elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
QUERY = "genshin_db.runtime_reaction_damage.get_runtime_reaction_damage_profile"
SCOPE = "独立20输入、14秒归一工程情景；90级ATK100/EM0、无暴击、敌90级10%抗；非真实角色/队伍轴，冻结模型是实验参考而非游戏真值。"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def near(actual, expected):
    return math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9)


def same_numbers(actual, expected):
    return len(actual) == len(expected) and all(near(a, e) for a, e in zip(actual, expected, strict=True))


def native_unknown(plan):
    return plan["mechanics_model"]["elemental_model"]["swirl_damage"]["icd"] == {"kind": "unknown"}


def make_plan(oracle):
    """Read only input columns, never feed expected state into the engine."""
    evidence = {"status": "assumed", "source": SCOPE}
    inputs = [{key: row[key] for key in ("id", "t", "element", "gu")} for row in oracle["rows"]]
    profile_keys = list(dict.fromkeys((row["element"], row["gu"]) for row in inputs))
    template = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
        "meta": {"name": "冻结参考·20输入工程验收", "assumptions": [SCOPE,
            "同秒输入是有序独立工程命中；一个抽象动作承载这组输入，不能视为真实角色可以同时释放20次技能。",
            "附着GU/时点/安全接触均为明确工程声明；真实数据库仅编译普通冰水扩散伤害序列。"]},
        "loop_duration": oracle["input_window_seconds"], "enemy": {"level": 90, "resistance": {"default": .1}},
        "characters": [{"id": "engineering", "name": "归一工程输入角色", "level": 90,
            "stats": {"atk": 100, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}}],
        "buffs": [], "switches": [], "hits": [{"id": row["id"], "t": row["t"], "character": "engineering",
            "skill": "有序工程输入", "hit_name": f"工程输入 {i+1}", "damage_system": "classic", "element": row["element"],
            "multiplier": 1, "scaling_stat": "atk", "damage_type_ids": ["damage.elemental_skill"]}
            for i, row in enumerate(inputs)]}
    rotation = {"schema_version": 1, "period": oracle["input_window_seconds"], "max_cycles": 3,
        "initial_frontline": "engineering", "characters": [{"id": "engineering", "element": "anemo",
            "energy_recharge": 1, "energy_capacity": 60, "initial_energy": 60, **evidence}],
        "abilities": [{"id": "engineering.action", "character": "engineering", "action_type_id": "action.elemental_skill",
            "cooldown": None, "energy": None, **evidence}],
        "actions": [{"id": "engineering.cast", "ability_id": "engineering.action", "start": 0, "occupation_end": .01,
            "order": 0, "repeat": {"mode": "once"}, **evidence}], "events": [], "initial_cooldowns": []}
    mechanics = {"schema_version": 1, "rules": [], "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []},
        "hit_events": [{"hit_id": row["id"], "action_id": "engineering.cast", "order": 10+i,
            "application": {"profile_id": f"{row['element']}{row['gu']}", "target_id": "enemy", "landed": True},
            "contact": {"kind": "profile", "profile_id": "engineering.safe"}} for i, row in enumerate(inputs)],
        "elemental_model": {"schema_version": 1, "ruleset": FREEZE_RULESET,
            "reference_acceptance": {"contract_id": REFERENCE_CONTRACT["contract_id"],
                "contract_fingerprint": REFERENCE_CONTRACT["contract_fingerprint"], "acknowledged": True,
                "source": "仅接受当前明确实验参考契约，用独立工程ledger检查实现；不声明该模型已被游戏实测证明。"},
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", **evidence,
                "initial_state": {"ordinary_aura": None, "frozen": None, "freeze_history_seconds": 0, **evidence},
                "freeze": {"freezable": True, "resistance": 0, **evidence}},
            "profiles": [{"id": f"{element}{gu}", "element": element, "application": "apply", "gauge_units": gu,
                "icd": {"kind": "none"}, **evidence} for element, gu in profile_keys],
            "contact_profiles": [{"id": "engineering.safe", "freeze_interaction": "none", **evidence}],
            "swirl_damage": {"icd": {"kind": "unknown"}, **evidence}}}
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics, reaction_damage_sources=[{
        "reaction_type": "swirl", "version_line": "release", "source": SCOPE,
        "expected_snapshot_id": None, "expected_profile_fingerprint": None}])
    validate_plan(plan)
    return plan


def check_state(state, expected, check, label):
    aura, ordinary = state["ordinary_aura"], expected["ordinary"]
    check(label + " ordinary identity", (aura is None) == (ordinary is None)
          and (ordinary is None or aura["element"] == ordinary[0]))
    if ordinary:
        check(label + " ordinary remaining GU", near(aura["remaining_gu"], ordinary[1]))
    frozen = expected.get("F", float(Fraction(expected.get("F_fraction", "0"))))
    check(label + " frozen GU", near((state["frozen"] or {}).get("remaining_gu", 0), frozen))
    check(label + " history x", near(state["freeze_history_seconds"], expected["x"]))
    check(label + " frozen decay", near(state["freeze_decay_gu_per_second"], .4 + .1 * expected["x"]))


def verify_result(result, oracle, check, label):
    elemental = result["elemental"]
    check(label + " experimental evidence", elemental["ruleset"] == FREEZE_RULESET
          and elemental["evidence_tier"] == "experimental_reference"
          and elemental["reference_acceptance"]["acknowledged"] is True)
    applications = [r for r in elemental["trace"] if r["type"] == "elemental_application"]
    check(label + " exactly 20 applications", len(applications) == len(oracle["rows"]) == 20)
    for actual, expected in zip(applications, oracle["rows"], strict=True):
        check(label + " ordered identity " + expected["id"], actual["stable_event_id"] == expected["id"] and actual["t"] == expected["t"])
        check_state(actual["after"], expected, check, label + " " + expected["id"])
        reactions = [r["type"] + (":" + r["reaction_element"] if r["type"] == "swirl" else "") for r in actual["reactions"]]
        check(label + " reactions " + expected["id"], reactions == expected["reactions"])
    check_state(elemental["input_window_state"], oracle["window_end"], check, label + " window end")
    reactions = [r for row in applications for r in row["reactions"]]
    attempts = [r for r in elemental["trace"] if r["type"] == "reaction_damage_attempt"]
    check(label + " independent event counts", {"input_applications": len(applications),
        "freeze_reactions": sum(r["type"] == "freeze" for r in reactions),
        "public_swirl_reactions": sum(r["type"] == "swirl" for r in reactions),
        "swirl_consumption_layers": sum(len(r["layer_consumptions"]) for r in applications),
        "swirl_damage_attempts": len(attempts), "swirl_damage_allowed": sum(r["allowed"] is True for r in attempts)} == oracle["expected_counts"])
    expected_damage = []
    for row in oracle["rows"]:
        expected_damage.extend([45.] + [781.299 for reaction in row["reactions"] if reaction.startswith("swirl:")])
    check(label + " all 28 hit damages independent arithmetic", same_numbers([h["expected"] for h in result["hits"]], expected_damage))
    check(label + " total damage 7150.392", near(result["summary"]["total_damage"], 7150.392)
          and near(result["summary"]["total_damage"], oracle["numerical_projection"]["total_damage"]))
    check(label + " natural expiry and history recovery present", {r["reason"] for r in elemental["trace"]
        if r["type"] == "elemental_state_transition"} >= {"frozen_expired", "freeze_history_recovered"})
    return expected_damage


def sheets(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [s.attrib["name"] for s in workbook.findall("s:sheets/s:sheet", NS)]
        return {name: ET.fromstring(archive.read(f"xl/worksheets/sheet{i+1}.xml")) for i, name in enumerate(names)}


def workbook_check(content, expected, check, label, *, formula):
    workbook = sheets(content)
    trace = workbook["元素状态流水"]
    check(label + " protected state sheet", trace.find("s:sheetProtection", NS) is not None)
    check(label + " state sheet has no formulas", trace.find(".//s:f", NS) is None)
    texts = [x.text or "" for x in trace.findall(".//s:t", NS)]
    check(label + " scope and source confirmation visible", "experimental_reference" in texts
          and "数据库来源确认" in texts and any("不是游戏真值" in text for text in texts))
    check(label + " expiry and consumption visible", "冻结层自然到期" in texts and "层消费（不另计伤害）" in texts)
    if formula:
        cells = {c.attrib["r"]: c for c in workbook["逐事件"].findall(".//s:c", NS)}
        check(label + " damage cached values", same_numbers([float(cells[f"V{i}"].find("s:v", NS).text)
            for i in range(5, 5+len(expected))], expected))
        check(label + " damage cells have formulas", all(cells[f"V{i}"].find("s:f", NS) is not None for i in range(5, 5+len(expected))))
        params = {c.attrib["r"]: c for c in workbook["参数"].findall(".//s:c", NS)}
        paths = {c.find("s:is/s:t", NS).text: ref[1:] for ref, c in params.items()
                 if ref.startswith("A") and c.find("s:is/s:t", NS) is not None}
        for path in ("mechanics_model.elemental_model.target.initial_state.freeze_history_seconds",
                     "mechanics_model.elemental_model.reference_acceptance.contract_fingerprint",
                     "mechanics_model.hit_events[1].contact.profile_id"):
            row = paths[path]
            check(label + " fixed structural guard " + path, params["C"+row].get("s") == "5"
                  and params["I"+row].find("s:f", NS).text == f"IF(C{row}<>D{row},1,0)")
    else:
        rows = workbook["逐事件明细"].findall("s:sheetData/s:row", NS)[1:]
        actual = [float(next(c for c in row.findall("s:c", NS) if c.attrib["r"].startswith("G")).find("s:v", NS).text) for row in rows]
        check(label + " snapshot damage values", same_numbers(actual, expected))
    check(label + " no formula errors", all(c.get("t") != "e" for sheet in workbook.values() for c in sheet.findall(".//s:c", NS)))


def reject(plan, db, check, label):
    try:
        run_plan_calc(plan, db)
    except PlanError as exc:
        check(label + " no result publication", exc.details.get("result") is None)
        return {"reason": exc.reason, "message": str(exc), "details": exc.details}
    raise AssertionError(label + " unexpectedly calculated")


def verify(args, check):
    output, db = args.output_dir, args.data_dir / "genshin.db"
    oracle = json.loads(ORACLE.read_text("utf8"))
    check("independent ledger shape", oracle["kind"] == "independent_hand_derived_expected_values_not_product_execution"
          and len(oracle["rows"]) == 20 and oracle["input_window_seconds"] == 14)
    write(output / "independent-oracle.json", oracle)
    plan = make_plan(oracle)
    write(output / "freeze-unconfirmed.plan.json", plan)
    print("EC06: querying and confirming real reaction sources", flush=True)
    preview = compose_equipment_plan(plan, db)
    write(output / "unconfirmed-compose.json", preview)
    check("source unconfirmed remains pending", preview["state"] == "pending" and native_unknown(preview["plan"]))
    with equipment_read_session(db):
        profiles = [get_runtime_reaction_damage_profile(db, element=element) for element in ("hydro", "cryo")]
    write(output / "reaction-damage-source-queries.json", profiles)
    for element, profile in zip(("hydro", "cryo"), profiles, strict=True):
        check(element + " bounded sequence and pending ability", profile["execution"]["sequence_status"] == "ready"
              and profile["execution"]["ability_execution"] == "pending"
              and profile["projection"]["damage_sequence"] == [1, 1] + [0]*8
              and profile["projection"]["reset_cycle_seconds_rounded"] == .5)
    check("shared source snapshot", len({p["snapshot"]["snapshot_id"] for p in profiles}) == 1)
    plan["reaction_damage_sources"][0].update(preview["reaction_damage_compilations"][0]["required_confirmation"])
    original = copy.deepcopy(plan)
    with patch(QUERY, wraps=get_runtime_reaction_damage_profile) as fresh:
        calculated = run_plan_calc(plan, db)
        check("calculation freshly reads both source profiles", fresh.call_count == 2)
    check("native input and actions unchanged", plan == original and calculated["plan"]["rotation_model"] == original["rotation_model"]
          and native_unknown(calculated["plan"]))
    check("source snapshot confirmed", calculated["result_receipt"]["reaction_damage_sources"][0]["snapshot"]["snapshot_id"] == profiles[0]["snapshot"]["snapshot_id"])
    expected = verify_result(calculated["result"], oracle, check, "source")
    write(output / "freeze-calculated.json", calculated)
    plan_path = output / "freeze-native.plan.json"
    write(plan_path, calculated["plan"])
    check("once input remains 20 explicit source hits", len(calculated["plan"]["manual_template"]["hits"]) == 20
          and calculated["plan"]["rotation_model"]["actions"][0]["repeat"] == {"mode": "once"})
    print("EC06: GUI bridge round trip and protected workbooks", flush=True)
    gui, saved_path = GuiApi(args.data_dir, plans_dir=output / "gui-plans"), output / "freeze-gui-roundtrip.plan.json"
    saved = gui.save_plan(str(saved_path), calculated["plan"])
    check("GUI bridge save succeeds", saved["code"] == 0)
    loaded = gui.load_plan(str(saved_path))
    check("GUI bridge reload ready", loaded["code"] == 0 and loaded["data"]["report"]["state"] == "ready")
    restored = read_plan(saved_path)["plan"]
    check("saved native retains unknown source placeholder", native_unknown(restored))
    gui_calc = gui.run_plan_calc(restored)
    write(output / "freeze-gui-calculated.json", gui_calc)
    check("GUI bridge numerical and trace equality", gui_calc["code"] == 0 and gui_calc["data"]["result"] == calculated["result"])
    for filename, content, formula in (("freeze-snapshot.xlsx", result_bytes(calculated["result"], calculated["result_receipt"], "xlsx", equipment_summary=calculated["equipment_summary"]), False),
                                       ("freeze-formula.xlsx", build_formula_workbook(calculated), True)):
        (output / filename).write_bytes(content)
        workbook_check(content, expected, check, filename, formula=formula)
    support = formula_workbook_support(calculated)
    check("formula support marks reference/contact regeneration", support["editable"]["elemental_initial_state_contact_reference"] == "regenerate_required")
    rejected = {"unknown-contact": copy.deepcopy(plan), "stale-reference": copy.deepcopy(plan)}
    del rejected["unknown-contact"]["mechanics_model"]["hit_events"][1]["contact"]
    rejected["stale-reference"]["mechanics_model"]["elemental_model"]["reference_acceptance"]["contract_fingerprint"] = "0"*64
    for label, bad in rejected.items():
        write(output / (label + ".plan.json"), bad)
        write(output / (label + "-rejected.json"), reject(bad, db, check, label))
        response = gui.run_plan_calc(bad)
        write(output / (label + "-gui-rejected.json"), response)
        check(label + " GUI has no authoritative result", response["code"] != 0 and (response.get("details") or {}).get("result") is None)
    commands = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        commands.append(("package-cli", [str(args.cli_exe)]))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"}
    command_records = []
    for label, command in commands:
        print("EC06: " + label + " calculation, exports and negative cases", flush=True)
        def execute(suffix, arguments, command=command, label=label):
            argv = [*command, "--data-dir", str(args.data_dir), *arguments]
            started = time.perf_counter()
            proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, encoding="utf8", timeout=180,
                                  check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            (output / f"{label}-{suffix}.stdout.json").write_text(proc.stdout, "utf8")
            (output / f"{label}-{suffix}.stderr.json").write_text(proc.stderr, "utf8")
            command_records.append({"argv": argv, "returncode": proc.returncode, "seconds": time.perf_counter()-started})
            return proc
        result_path = output / (label + "-result.json")
        proc = execute("calc", ["equipment", "calc", "--plan", str(plan_path), "--output", str(result_path), "--summary"])
        check(label + " calc successful with short stdout", proc.returncode == 0 and len(proc.stdout.encode("utf8")) < 12000)
        delivered = json.loads(result_path.read_text("utf8"))
        check(label + " full trace/numerical/source equality", delivered["result"] == calculated["result"]
              and delivered["result_receipt"]["reaction_damage_sources"] == calculated["result_receipt"]["reaction_damage_sources"]
              and delivered["result_receipt"]["elemental_reference"] == calculated["result_receipt"]["elemental_reference"])
        for kind in ("snapshot", "formula"):
            workbook = output / f"{label}-{kind}.xlsx"
            arguments = ["equipment", "workbook" if kind == "formula" else "export", "--plan", str(plan_path), "--output", str(workbook)]
            if kind == "snapshot":
                arguments.extend(["--format", "xlsx"])
            proc = execute(kind, arguments)
            check(label + " " + kind + " export successful", proc.returncode == 0 and workbook.is_file())
            workbook_check(workbook.read_bytes(), expected, check, label + " " + kind, formula=kind == "formula")
        for negative in rejected:
            forbidden = output / f"{label}-{negative}-must-not-exist.json"
            check(label + " rejection target initially absent " + negative, not forbidden.exists())
            proc = execute(negative, ["equipment", "calc", "--plan", str(output / (negative + ".plan.json")),
                "--output", str(forbidden), "--summary"])
            check(label + " no rejected result file " + negative, proc.returncode != 0 and not forbidden.exists())
    write(output / "commands.json", command_records)
    return {"native_plan": str(plan_path), "expected_total_damage": 7150.392, "expected_hit_damage": expected,
        "oracle_path": str(ORACLE), "oracle_sha256": digest(ORACLE), "source_snapshot": profiles[0]["snapshot"],
        "source_version": APP_VERSION, "formula_support": support, "scope": SCOPE,
        "verified_interfaces": ["source API", "headless source GUI bridge", "source CLI", "OOXML snapshot", "OOXML formula/cached baseline"]
            + (["candidate EXE CLI"] if args.cli_exe else []),
        "not_verified": ["foreground GUI clicking", "Excel desktop recalculation", "game-client measurements",
                         "full character action dispatch", "nonzero freeze resistance/hitlag/shatter/unsupported elements"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=EC06 / "source-delivery")
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if not args.output_dir.is_relative_to(EC06.resolve()) or args.output_dir == EC06.resolve():
        parser.error("--output-dir必须是EC06内的全新独立子目录")
    if args.output_dir.exists():
        parser.error("输出目录已存在；请另选全新目录，禁止覆盖历史验收")
    db = args.data_dir / "genshin.db"
    if not db.is_file():
        parser.error("--data-dir中缺少genshin.db")
    if args.cli_exe:
        args.cli_exe = args.cli_exe.resolve()
        if not args.cli_exe.is_file():
            parser.error("--cli-exe不存在")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    checks, before, started = [], digest(db), time.perf_counter()
    summary = {"status": "running", "data_dir": str(args.data_dir), "database_sha256_before": before,
               "cli_exe": str(args.cli_exe) if args.cli_exe else None, "checks": checks}
    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)
    try:
        summary.update(verify(args, check))
        check("database bytes unchanged", digest(db) == before)
        summary.update(status="passed", passed=len(checks))
    except Exception as exc:
        summary.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)}, passed=sum(c["passed"] for c in checks))
        raise
    finally:
        summary.update(database_sha256_after=digest(db), seconds=time.perf_counter()-started)
        write(args.output_dir / "verification.json", summary)
    print(json.dumps({"status": "passed", "checks": len(checks), "total_damage": 7150.392,
                      "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
