"""RF08 portable-product engineering preflight; never starts a model or game search.

Only a fresh child of outputs/engineering-mechanics-01 is writable. The private
database is copied without credentials, and all product calls use facility-v2's
public CLI allowlist. No source implementation is imported to compute answers.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import platform
import shutil
import sqlite3
import sys
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "engineering-mechanics-01"
FACILITY = ROOT / "benchmarks" / "followup" / "facility-v2" / "controlled_tools.py"
EXAMPLE = ROOT / "examples" / "declared-mechanics-plan.json"
SOURCE_DB = ROOT / "data" / "genshin.db"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RECEIPT_KEYS = ("plan_id", "revision", "input_fingerprint", "source_fingerprint",
                "output_fingerprint", "result_fingerprint")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, description):
    if not condition:
        raise AssertionError(description)


def load_facility():
    spec = importlib.util.spec_from_file_location("rf08_facility_v2", FACILITY)
    module = importlib.util.module_from_spec(spec)
    # A host import must not write __pycache__ into the frozen facility directory.
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def inventory(root, no_links):
    """Record every regular file and reject links before descending."""
    result = {}

    def visit(directory):
        no_links(directory)
        for path in sorted(directory.iterdir()):
            no_links(path)
            if path.is_dir():
                visit(path)
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = {
                    "bytes": path.stat().st_size, "sha256": sha(path)}
    visit(root)
    return result


def inspect_workbook(path, receipt):
    """Inspect actual generated OOXML; cached values are not an Excel recalculation."""
    with zipfile.ZipFile(path) as archive:
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        sheets = {}
        for sheet in book.findall("s:sheets/s:sheet", NS):
            relation = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = targets[relation]
            xml_path = target.lstrip("/") if target.startswith("/") else "xl/" + target
            tree = ET.fromstring(archive.read(xml_path))
            sheets[sheet.attrib["name"]] = {cell.attrib["r"]: {
                "formula": cell.findtext("s:f", namespaces=NS),
                "value": cell.findtext("s:v", namespaces=NS),
                "text": "".join(cell.itertext()) if cell.attrib.get("t") == "inlineStr" else None,
            } for cell in tree.findall(".//s:c", NS)}
    summary, hits = sheets["汇总"], sheets["逐事件"]
    require(summary["B6"]["formula"] and "逐事件" in summary["B6"]["formula"],
            "Workbook total must reference actual event formulas")
    require(math.isclose(float(summary["B6"]["value"]), 225), "Workbook total cache is not 225")
    require(math.isclose(float(summary["B7"]["value"]), 22.5), "Workbook DPS cache is not 22.5")
    evidence = {}
    for row, expected in zip((5, 6, 7), (90, 90, 45), strict=True):
        cell = hits[f"V{row}"]
        require(bool(cell["formula"]), f"V{row} is a static number, not a formula")
        require(math.isclose(float(cell["value"]), expected), f"V{row} has the wrong damage")
        evidence[f"逐事件!V{row}"] = cell
    source = sheets["来源与范围"]
    fields = {value["text"]: source.get("B" + coordinate[1:], {}).get("text")
              for coordinate, value in source.items() if coordinate.startswith("A")}
    for key in RECEIPT_KEYS:
        require(fields.get(key) == str(receipt[key]), f"Workbook receipt differs at {key}")
    formulas = [{"sheet": name, "cell": coordinate, "formula": cell["formula"]}
                for name, cells in sheets.items() for coordinate, cell in cells.items() if cell["formula"]]
    require(any("参数" in row["formula"] for row in formulas), "Workbook has no parameter dependencies")
    return {"sha256": sha(path), "formula_count": len(formulas),
            "formula_cells": formulas, "event_cells": evidence,
            "summary_cells": {key: summary[key] for key in ("B6", "B7")},
            "embedded_receipt": {key: fields[key] for key in RECEIPT_KEYS},
            "native_excel_recalculated": False,
            "note": "真实便携版生成的活公式及缓存值已核对；本脚本不启动 Excel 重算。"}


class Preflight:
    def __init__(self, exe, destination, facility):
        self.exe, self.root, self.facility = exe, destination, facility
        self.host = destination / "host"
        self.data = destination / "data"
        self.public = destination / "public"
        self.output = destination / "artifacts"
        self.steps = []
        self.checks = []
        self.started = time.monotonic()
        self.summary = {"schema_version": 1, "engineering_only": True,
                        "model_run_started": False, "external_game_searches": [],
                        "benchmark_result": False, "status": "running",
                        "started_at": datetime.now(UTC).isoformat(), "steps": self.steps,
                        "checks": self.checks, "run_root": str(destination)}
        self.before = None
        self.manifest = {"host": {"python": sys.version, "platform": platform.platform(),
                                   "executable": sys.executable, "script_sha256": sha(__file__),
                                   "controlled_tools_sha256": sha(FACILITY)},
                         "scope": "虚构单角色接口工程验证；无模型、无网络检索、无队伍答案"}

    def save(self, name, value):
        (self.host / name).write_text(encoded(value), encoding="utf-8")

    def check(self, name, condition, detail=None):
        self.checks.append({"name": name, "passed": bool(condition), "detail": detail})
        require(condition, name)

    def read(self, logical):
        pieces, offset = [], 0
        while True:
            result = self.tools.read_text(logical, offset=offset, limit=128000)
            require(result.get("ok"), f"Controlled read failed: {logical}: {result}")
            pieces.append(result["text"])
            if not result["truncated"]:
                break
            require(result["next_offset"] > offset, "Controlled read cursor did not advance")
            offset = result["next_offset"]
        return "".join(pieces)

    def read_json(self, logical):
        return json.loads(self.read(logical))

    def write(self, logical, value, *, raw=False, overwrite=False):
        result = self.tools.write_text(logical, value if raw else encoded(value), overwrite=overwrite)
        require(result.get("ok"), f"Controlled write failed: {logical}: {result}")
        return result

    def cli(self, label, argv):
        step = {"name": label, "argv": argv, "status": "running"}
        self.steps.append(step)
        started = time.monotonic()
        cache = f"cache/responses/{len(self.steps):02d}-{label}.json"
        response = self.tools.app_cli(argv, cache_path=cache)
        step.update(wall_seconds=time.monotonic() - started, response_cache=cache)
        if response.get("response_in_cache"):
            response = self.read_json(cache)
        step.update(status="passed" if response.get("ok") else "failed",
                    exit_code=response.get("exit_code"))
        self.save("preflight-result.json", self.summary)
        require(response.get("ok"), f"{label} failed: {response}")
        envelope = response.get("envelope")
        require(isinstance(envelope, dict) and envelope.get("code") == 0,
                f"{label} did not return a successful public CLI envelope")
        return envelope["data"]

    def prepare(self):
        for directory in (self.host, self.data, self.public, self.output):
            directory.mkdir()
        no_links = self.facility._no_links
        skill = self.exe.parent / "skill"
        require((skill / "SKILL.md").is_file(), "The portable package has no public Skill")
        for path in (self.exe, SOURCE_DB, EXAMPLE):
            no_links(path)
            require(path.is_file(), f"Missing frozen input: {path}")
        wal = SOURCE_DB.with_name(SOURCE_DB.name + "-wal")
        require(not wal.exists() or wal.stat().st_size == 0,
                "Source DB has a live WAL; obtain a quiescent snapshot before this copy-only preflight")
        self.before = {"package": inventory(self.exe.parent, no_links),
                       "skill": inventory(skill, no_links), "database": sha(SOURCE_DB),
                       "example": sha(EXAMPLE)}
        self.manifest.update(source_paths={"exe": str(self.exe), "database": str(SOURCE_DB),
                                          "skill": str(skill), "example": str(EXAMPLE)},
                             before=self.before)
        shutil.copy2(SOURCE_DB, self.data / "genshin.db")
        shutil.copytree(skill, self.public / "skill")
        shutil.copy2(EXAMPLE, self.public / "case.plan.json")
        self.manifest["copied_database_sha256"] = sha(self.data / "genshin.db")
        self.check("database_copy_matches_source", self.manifest["copied_database_sha256"] == self.before["database"])
        self.save("manifest.json", self.manifest)
        public_files = ["case.plan.json", *("skill/" + name for name in self.before["skill"])]
        self.tools = self.facility.ControlledTools(
            self.exe, self.data, self.public, self.output, "http://127.0.0.1:1",
            public_files=public_files, audit_path=self.host / "controlled-tools.jsonl")
        self.read("public/skill/SKILL.md")
        self.read("public/skill/references/app-mechanics/execution/rules.md")
        self.write("outputs/input.plan.json", self.read("public/case.plan.json"), raw=True)
        self.input_sha = sha(self.output / "input.plan.json")
        self.check("public_case_copy_preserves_bytes", self.input_sha == self.before["example"])

    def run(self):
        capabilities = self.cli("capabilities", ["capabilities"])
        contract = capabilities["plan_contract"]
        self.check("native_plan3_advertised", 3 in contract["supported_plan_versions"])
        commands = {command["name"] for command in capabilities["commands"]}
        self.check("required_commands_advertised", {
            "db context", "equipment compose", "equipment calc", "equipment workbook",
            "engine check-rotation", "workflow init", "workflow export"} <= commands)
        self.write("outputs/plan-schema.json", contract["schema"])
        self.write("outputs/mechanics-schema.json", contract["schema"]["properties"]["mechanics_model"])
        self.check("plan3_models_in_schema", {"rotation_model", "mechanics_model"} <= contract["schema"]["properties"].keys())
        validation = self.cli("schema-validation", ["equipment", "validate", "--plan", "outputs/input.plan.json"])
        self.check("native_example_validates", validation.get("valid") and validation.get("plan_version") == 3)
        self.manifest["reported_app_version"] = capabilities["version"]

        # Host selects one identity only. No rotation, talent, damage or reference-answer query.
        with sqlite3.connect((self.data / "genshin.db").as_uri() + "?mode=ro", uri=True) as conn:
            ident, line = conn.execute("SELECT id, version_line FROM character ORDER BY id, version_line LIMIT 1").fetchone()
        request = {"version_line": line, "characters": [{"id": ident, "sections": ["character"]}]}
        self.write("outputs/context-request.json", request)
        contexts = []
        for label in ("first", "second"):
            self.cli("context-" + label, ["db", "context", "--input", "outputs/context-request.json",
                     "--cache-dir", "cache/context", "--output", f"outputs/context-{label}.json"])
            contexts.append(self.read_json(f"outputs/context-{label}.json"))
        first, second = contexts
        self.check("context_miss_then_hit", first["cache"]["hit"] is False and second["cache"]["hit"] is True)
        self.check("context_snapshot_and_key_identical", first["cache"]["key"] == second["cache"]["key"]
                   and first["snapshot"] == second["snapshot"] and first["items"] == second["items"])
        self.check("identity_only_context_found", len(first["items"]) == 1
                   and first["items"][0]["status"] == "ok"
                   and first["items"][0]["data"]["selection"]["requested"]["sections"] == ["character"]
                   and "skills" not in first["items"][0]["data"])
        self.summary["context_probe"] = {"request": request, "cache_key": first["cache"]["key"],
                                          "snapshot_fingerprint": first["snapshot"]["fingerprint"]}

        composed = self.cli("compose-save", ["equipment", "compose", "--plan", "outputs/input.plan.json",
                            "--output", "outputs/composed.plan.json", "--export-resolved", "outputs/resolved.json"])
        self.check("compose_ready", composed["state"] == "ready")
        saved = self.read_json("outputs/composed.plan.json")
        resolved = self.read_json("outputs/resolved.json")
        self.saved_sha = sha(self.output / "composed.plan.json")
        self.check("saved_resolved_projection_identical", saved["resolved_template"] == resolved)
        original = self.cli("calc-source", ["equipment", "calc", "--plan", "outputs/input.plan.json",
                            "--output", "outputs/source-result.json"])
        reopened = self.cli("calc-reopened", ["equipment", "calc", "--plan", "outputs/composed.plan.json",
                            "--output", "outputs/reopened-result.json"])
        result, receipt = reopened["result"], reopened["result_receipt"]
        self.check("native_same_source_receipts", all(original["result_receipt"][key] == receipt[key] for key in RECEIPT_KEYS))
        self.check("saved_same_input_source_output", all(composed[key] == receipt[key] for key in
                   ("input_fingerprint", "source_fingerprint", "output_fingerprint")))
        self.check("receipt_matches_actual_numeric_template", fingerprint(resolved) == receipt["output_fingerprint"])
        self.check("receipt_matches_actual_result", fingerprint(result) == receipt["result_fingerprint"])
        self.check("three_hits_90_90_45", len(result["hits"]) == 3 and all(
            math.isclose(hit["expected"], value) for hit, value in zip(result["hits"], (90, 90, 45), strict=True)))
        self.check("total225_dps22_5", math.isclose(result["summary"]["total_damage"], 225)
                   and math.isclose(result["summary"]["dps"], 22.5))
        self.check("mechanics_first_cycle_complete", result["mechanics"]["complete_first_cycle"])
        self.summary.update(native_receipt=receipt, total_damage=result["summary"]["total_damage"],
                            hit_damage=[hit["expected"] for hit in result["hits"]])

        self.cli("rotation-check", ["engine", "check-rotation", "outputs/composed.plan.json",
                 "--format", "json", "--output", "outputs/rotation-check.json"])
        rotation = self.read_json("outputs/rotation-check.json")
        self.check("rotation_check_passed", rotation["status"] == result["rotation_check"]["status"] == "passed")
        self.check("rotation_check_same_execution", rotation == result["rotation_check"])
        workbook = self.cli("formula-workbook", ["equipment", "workbook", "--plan", "outputs/composed.plan.json",
                            "--output", "outputs/engineering.xlsx"])
        self.check("workbook_same_source_receipt", all(workbook["result_receipt"][key] == receipt[key] for key in RECEIPT_KEYS))
        self.check("workbook_formula_support", workbook["support"]["status"] == "supported")
        workbook_evidence = inspect_workbook(self.tools.resolve_output_path("outputs/engineering.xlsx", must_exist=True), receipt)
        self.save("workbook-inspection.json", workbook_evidence)
        self.summary["workbook"] = {"path": "artifacts/engineering.xlsx", "formula_count": workbook_evidence["formula_count"],
                                     "sha256": workbook_evidence["sha256"], "native_excel_recalculated": False}

        self.cli("workflow-init", ["workflow", "init", "--plan", "outputs/composed.plan.json",
                 "--audit", "outputs/workflow-audit.json"])
        audit = self.read_json("outputs/workflow-audit.json")
        audit["records"] = [{"id": "engineering-scope", "type": "decision",
                             "description": "虚构三个命中的接口工程验证，不是游戏队伍或模型 benchmark。"}]
        audit["scope"] = {"included": ["同源方案、缓存、机制数值、真实活公式交付"],
                          "excluded": ["真实队伍轴、游戏机制真实性、模型能力评价、网络检索"], "pending": []}
        self.write("outputs/workflow-audit.json", audit, overwrite=True)
        self.cli("workflow-bind", ["workflow", "bind", "--plan", "outputs/composed.plan.json",
                 "--audit", "outputs/workflow-audit.json"])
        self.cli("workflow-export", ["workflow", "export", "--plan", "outputs/composed.plan.json",
                 "--audit", "outputs/workflow-audit.json", "--output-dir", "outputs/delivery"])
        issues = self.read_json("outputs/delivery/issues.json")
        self.check("workflow_current_input", issues["input_fingerprint"] == receipt["input_fingerprint"]
                   and issues["review_status"] == "declared")
        self.check("workflow_four_files", all((self.output / "delivery" / name).is_file()
                   for name in ("issues.json", "lookups.jsonl", "web_findings.json", "rotation.md")))
        self.check("no_web_lookups_performed", self.read("outputs/delivery/lookups.jsonl").strip() == "")

    def finalize(self):
        if self.before is not None:
            after = {"package": inventory(self.exe.parent, self.facility._no_links),
                     "skill": inventory(self.exe.parent / "skill", self.facility._no_links),
                     "database": sha(SOURCE_DB), "example": sha(EXAMPLE)}
            self.manifest["after"] = after
            self.check("frozen_package_skill_source_database_example_unchanged", self.before == after)
            self.check("copied_database_unchanged", sha(self.data / "genshin.db") == self.before["database"])
            self.check("public_skill_unchanged", inventory(self.public / "skill", self.facility._no_links) == self.before["skill"])
            if hasattr(self, "input_sha"):
                self.check("input_plan_unchanged", sha(self.output / "input.plan.json") == self.input_sha)
            if hasattr(self, "saved_sha"):
                self.check("saved_plan_unchanged_by_readers", sha(self.output / "composed.plan.json") == self.saved_sha)
            self.manifest["artifacts"] = inventory(self.output, self.facility._no_links)
        self.summary.update(finished_at=datetime.now(UTC).isoformat(), wall_seconds=time.monotonic() - self.started)
        self.save("manifest.json", self.manifest)
        self.save("preflight-result.json", self.summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True, help="Rebuilt portable GenshinCalc.exe")
    parser.add_argument("--output-dir", type=Path, help="Fresh child of outputs/engineering-mechanics-01")
    args = parser.parse_args()
    facility = load_facility()
    requested = args.output_dir or BASE / ("facility-preflight-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    requested = requested.absolute()
    facility._no_links(requested)
    destination = requested.resolve()
    require(destination.is_relative_to(BASE.resolve()) and destination != BASE.resolve(),
            "All run files must be in a fresh child of outputs/engineering-mechanics-01")
    require(not destination.exists(), "Output path already exists; supply a new path. Nothing was overwritten.")
    exe = args.exe.absolute()
    facility._no_links(exe)
    require(exe.is_file(), "The requested portable EXE does not exist")
    destination.mkdir(parents=True)
    run = Preflight(exe, destination, facility)
    try:
        run.prepare()
        run.run()
        run.summary["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - always preserve a failed engineering run's evidence
        run.summary.update(status="failed", error=str(exc), traceback=traceback.format_exc())
    finally:
        try:
            run.finalize()
        except Exception as exc:  # noqa: BLE001 - even finalization failure must produce a failed receipt
            run.summary.update(status="failed", finalization_error=str(exc), finalization_traceback=traceback.format_exc())
            if run.host.is_dir():
                run.save("manifest.json", run.manifest)
                run.save("preflight-result.json", run.summary)
    print(encoded({"status": run.summary["status"], "result": str(run.host / "preflight-result.json"),
                   "manifest": str(run.host / "manifest.json"), "engineering_only": True}))
    return 0 if run.summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
