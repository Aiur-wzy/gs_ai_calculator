"""EC15 real-source delivery checks. Existing frozen engineering cases, no GUI."""
from __future__ import annotations

import argparse
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
OUT_ROOT = ROOT / "outputs/engine-continuation-20261002/ec15"
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.equipment.files import input_view, read_plan
from genshin_app.version import APP_VERSION

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf8")


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(path.read_text("utf-8-sig"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf8")


def sheets(path):
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        return {row.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
                for i, row in enumerate(names, 1)}


def inspect_delivery(directory, reference, check, label):
    index = load(directory / "index.json")
    generation = directory / index["generation"]
    manifest_path = directory / index["manifest"]["path"]
    check(label + " manifest identity", manifest_path.parent == generation
          and manifest_path.stat().st_size == index["manifest"]["bytes"]
          and sha(manifest_path) == index["manifest"]["sha256"])
    manifest = load(manifest_path)
    paths = {}
    check(label + " four roles", {row["role"] for row in index["artifacts"]} == {
        "native_plan", "calculated", "result_workbook", "formula_workbook"} and len(index["artifacts"]) == 4)
    for row, inner in zip(index["artifacts"], manifest["artifacts"], strict=True):
        path = directory / row["path"]
        check(label + "/" + row["role"] + " actual identity", path.parent == generation
              and inner == {**row, "path": path.name}
              and path.stat().st_size == row["bytes"] and sha(path) == row["sha256"])
        paths[row["role"]] = path
    native = read_plan(paths["native_plan"])["plan"]
    package = load(paths["calculated"])
    calc = package["calculated"]
    receipt, result = calc["result_receipt"], calc["result"]
    check(label + " valid native cache reset and same semantic input", native["resolution"] == {"state": "pending"}
          and native["resolved_template"] is None and fingerprint(input_view(native)) == receipt["input_fingerprint"]
          and input_view(native) == input_view(reference["plan"]))
    check(label + " complete versioned payload", package["package_schema"] == "genshin.calculated-package.v1"
          and package["kind"] == "historical_calculation_snapshot" and {"plan", "report", "result", "result_receipt",
          "equipment_summary", "presentation_issues", "display_context"} <= calc.keys()
          and package["integrity"] == {"calculated_fingerprint": fingerprint(calc),
              "report_fingerprint": fingerprint(calc["report"]), "display_context_fingerprint": fingerprint(calc["display_context"]),
              "equipment_summary_fingerprint": fingerprint(calc["equipment_summary"])})
    check(label + " previous engineering numbers and per-hit values", result["summary"] == reference["result"]["summary"]
          and result["hits"] == reference["result"]["hits"])
    check(label + " automatic history retained", result["rotation_check"].get("automatic_energy_delivery")
          == reference["result"]["rotation_check"].get("automatic_energy_delivery"))
    check(label + " source and window provenance", receipt["source_fingerprint"] == reference["result_receipt"]["source_fingerprint"]
          and calc["report"]["plan"] == calc["plan"]
          and manifest["input_fingerprint"] == receipt["input_fingerprint"]
          and manifest["result_fingerprint"] == fingerprint(result))
    formula, static = sheets(paths["formula_workbook"]), sheets(paths["result_workbook"])
    cells = {cell.get("r"): cell for cell in formula["逐事件"].findall(".//s:c", NS)}
    check(label + " actual formulas and exact cached hit values", all(cells[f"V{i}"].find("s:f", NS) is not None
          and math.isclose(float(cells[f"V{i}"].find("s:v", NS).text), hit["expected"], rel_tol=1e-10)
          for i, hit in enumerate(result["hits"], 5)))
    check(label + " no spreadsheet error caches", all(cell.get("t") != "e" for group in (formula, static)
          for sheet in group.values() for cell in sheet.findall(".//s:c", NS)))
    source_sheet = "动作资源来源" if result.get("action_resource_compilations") else "周期攻击来源"
    check(label + " readonly complete source sheet", all(source_sheet in group
          and group[source_sheet].find("s:sheetProtection", NS) is not None for group in (formula, static)))
    actual_total = sum(p.stat().st_size for p in paths.values()) + manifest_path.stat().st_size + (directory / "index.json").stat().st_size
    check(label + " total byte accounting", actual_total == manifest["total_bytes"])
    return {"index": index, "manifest": manifest, "native_path": str(paths["native_plan"]), "bytes": actual_total}


def verify(args, check):
    old = getattr(args, "reference_root", None) or ROOT / "tests/fixtures/engine-continuation"
    positive = load(old / "ec14/delivery/calculated.json")
    negative = load(old / "ec14/delivery/missing-refill-calculated.json")
    periodic = load(old / "ec13b/delivery/calculated.json")
    negative_path = args.output_dir / "missing-refill.native.json"
    write(negative_path, negative["plan"])
    cases = [
        ("energy", old / "ec14/delivery/automatic-energy-native.plan.json", positive, 0),
        ("diagnostic", negative_path, negative, 1),
        ("periodic", old / "ec13b/delivery/periodic-attack-native.plan.json", periodic, 0),
    ]
    observations = []
    for runtime, base in [("source", [sys.executable, "-X", "utf8", "-m", "genshin_app"])] + (
            [("package", [str(args.cli_exe)])] if args.cli_exe else []):
        for case, native, reference, expected_exit in cases:
            directory = args.output_dir / f"{runtime}-{case}"
            original_sha = sha(native)
            command = [*base, "--data-dir", str(args.data_dir), *( ["--pretty"] if runtime == "source" else []),
                       "equipment", "deliver", "--plan", str(native), "--output-dir", str(directory)]
            started = time.perf_counter()
            process = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf8", timeout=180, check=False)
            elapsed = time.perf_counter() - started
            write(args.output_dir / f"{runtime}-{case}-process.json", {"returncode": process.returncode,
                "wall_seconds": elapsed, "stdout": process.stdout, "stderr": process.stderr})
            label = runtime + "/" + case
            check(label + " process status", process.returncode == expected_exit)
            response = json.loads(process.stderr if expected_exit else process.stdout)
            data = response["details"] if expected_exit else response["data"]
            check(label + " bounded explicit publication", data["index_committed"] is True
                  and data["publication_status"] == "complete"
                  and max(len(process.stdout.encode("utf8")), len(process.stderr.encode("utf8"))) <= 65536
                  and "mechanics_trace" not in data and "calculated" not in data)
            inspected = inspect_delivery(directory, reference, check, label)
            check(label + " diagnostic distinct from file completeness", inspected["manifest"]["calculation_status"]
                  == ("diagnostic" if expected_exit else "complete")
                  and (not expected_exit or data["summary"]["dps"] is None))
            check(label + " original input unchanged", sha(native) == original_sha)
            observations.append({"runtime": runtime, "case": case, "wall_seconds": elapsed,
                "bytes": inspected["bytes"], "native_path": inspected["native_path"], "expected_exit": expected_exit})
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--cli-exe", type=Path)
    parser.add_argument("--reference-root", type=Path,
                        help="已通过原工程验收的新快照样本根目录；包含ec14/delivery和ec13b/delivery")
    args = parser.parse_args()
    args.output_dir, args.data_dir = args.output_dir.resolve(), args.data_dir.resolve()
    if args.cli_exe: args.cli_exe = args.cli_exe.resolve()
    if args.reference_root:
        args.reference_root = args.reference_root.resolve()
        if not args.reference_root.is_relative_to(ROOT / "outputs"):
            parser.error("Reference engineering evidence must stay within workspace outputs")
    if not args.output_dir.is_relative_to(OUT_ROOT) or args.output_dir.exists():
        parser.error("Use a fresh evidence directory under EC15")
    args.output_dir.mkdir(parents=True)
    database = args.data_dir / "genshin.db"
    before, checks = sha(database), []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        assert passed, name

    try:
        observations = verify(args, check)
    except Exception as exc:
        write(args.output_dir / "failure.json", {"error": type(exc).__name__, "message": str(exc), "checks": checks,
              "database_sha256_before": before, "database_sha256_after": sha(database)})
        raise
    check("formal database unchanged", sha(database) == before)
    write(args.output_dir / "verification.json", {"status": "passed", "source_version": APP_VERSION,
        "passed": len(checks), "checks": checks, "observations": observations,
        "database_sha256": before, "exe_sha256": sha(args.cli_exe) if args.cli_exe else None,
        "scope": "Existing engineering fixtures only. Explicit refills, calibrated contacts, no real team DPS claim.",
        "memory_measurement": "not_captured; output budgets do not guarantee process peak memory"})
    print(json.dumps({"status": "passed", "passed": len(checks), "output": str(args.output_dir)}), flush=True)


if __name__ == "__main__":
    main()
