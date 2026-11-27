"""Qualify frozen initialization and reads through the portable default path.

Only a fresh task-owned portable directory with empty data is accepted. No GUI,
API key, network, registry or Stable profile is used. Created data is retained
for inspection and subsequent GUI acceptance; this script never deletes it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.installation_location import checked_path
from genshin_app.package_integrity import verify_package_artifact
from genshin_app.portable_layout import PORTABLE_MARKER, PORTABLE_MARKER_BYTES
from genshin_db.release_profile import (
    load_database_release_manifest,
    load_release_pair_manifest,
)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def qualify(app: Path, report: Path, *, calculation_plan: Path | None = None) -> dict:
    app = checked_path(app.absolute())
    if app.name != "app" or (app.parent / "installation.json").exists():
        raise ValueError("Use an unregistered complete app/data portable layout")
    data = checked_path(app.parent / "data")
    if data.exists() and (not data.is_dir() or any(data.iterdir())):
        raise ValueError("Portable qualification requires fresh empty data; never overwrite a profile")
    report = checked_path(report.absolute())
    if report.is_relative_to(app.parent) or report.exists():
        raise ValueError("Use a new report outside the portable root")
    if calculation_plan is not None:
        calculation_plan = checked_path(calculation_plan.absolute())
        if not calculation_plan.is_file() or calculation_plan.is_relative_to(data):
            raise ValueError("Use an existing native plan outside the new portable data directory")
    if (app / PORTABLE_MARKER).read_bytes() != PORTABLE_MARKER_BYTES:
        raise ValueError("Portable marker missing or invalid")
    inventory = verify_package_artifact(app)
    pair = load_release_pair_manifest(app / "_internal/bundled/release-pair-manifest.json")
    database_manifest = load_database_release_manifest(app / "_internal/bundled/database-release-manifest.json")
    if inventory["manifest_sha256"] != pair["app"]["artifact_manifest"]["sha256"]:
        raise ValueError("Portable pair does not bind the actual inventory")
    started = time.monotonic()
    result = {"contract": "genshin-portable-default-startup-qualification-v1", "status": "failed",
              "app": str(app), "data": str(data), "release_pair_id": pair["release_pair_id"],
              "app_manifest_sha256": inventory["manifest_sha256"], "explicit_data_dir": False}
    report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="genshin-portable-default-") as cwd:
        request = Path(cwd) / "catalog.json"
        request.write_text(json.dumps({"kind": "character", "page_size": 3, "query": "凯亚"}), encoding="utf-8")
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        executable = str(app / "GenshinCalc.exe")
        timings = {}

        def invoke(name, arguments):
            command_started = time.monotonic()
            run = subprocess.run([executable, *arguments], cwd=cwd, env=environment,
                capture_output=True, check=False, timeout=900,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            timings[name] = time.monotonic() - command_started
            report.with_suffix(f".{name}.stdout.log").write_bytes(run.stdout)
            report.with_suffix(f".{name}.stderr.log").write_bytes(run.stderr)
            result["returncode"] = run.returncode
            if run.returncode:
                raise ValueError(f"Frozen default-path CLI failed at {name}; see stdout/stderr")
            payload = json.loads(run.stdout.decode("utf-8-sig"))
            if not isinstance(payload, dict) or payload.get("code") != 0:
                raise ValueError(f"CLI response is not a successful envelope: {name}")
            return payload.get("data")

        try:
            # Catalog is deliberately read-only and refuses to create a missing
            # database. Use the normal whiteboard creation/admission path first.
            invoke("initialization", ["whiteboard", "new", "--output", str(Path(cwd) / "blank.json")])
            database = checked_path(data / "genshin.db")
            database_sha = sha(database)
            if database_sha != database_manifest["database"]["sha256"]:
                raise ValueError("Default path did not initialize this package's database")
            live_pair = load_release_pair_manifest(data / "release-pair-live-manifest.json")
            if live_pair != pair:
                raise ValueError("Default path did not admit the bundled release pair")
            for name in ("catalog_first", "catalog_warm"):
                catalog = invoke(name, ["db", "catalog", "--input", str(request)])
                if not isinstance(catalog, dict) or not any(
                    row.get("id") == 10000015 and row.get("name") == "凯亚"
                    and row.get("publication", {}).get("entity_status") == "released"
                    for row in catalog.get("items", []) if isinstance(row, dict)
                ):
                    raise ValueError("Catalog did not return the accepted released Kaeya entity")
            if calculation_plan is not None:
                calculated = Path(cwd) / "special.calculated.json"
                calc = invoke("special_calculation", ["equipment", "calc", "--plan", str(calculation_plan),
                    "--output", str(calculated), "--summary"])
                observed = calc.get("validation_scope", {}).get("special_reactions", {})
                if (calc.get("task_admission") != "accepted" or not calculated.is_file()
                        or observed.get("execution_observed") is not True or not observed.get("trace_event_count")):
                    raise ValueError("Portable calculation did not execute and save an accepted special reaction")
                reopened = invoke("special_review", ["equipment", "review", "--calculated", str(calculated)])
                if (reopened.get("validation") != "saved_result_fingerprint"
                        or reopened.get("calculation_totals", {}).get("total_damage") != calc["summary"]["total_damage"]):
                    raise ValueError("Portable saved result did not reopen with the same verified damage")
                result["special_calculation"] = {"task_admission": calc["task_admission"],
                    "trace_event_count": observed["trace_event_count"], "total_damage": calc["summary"]["total_damage"],
                    "cold_review": "verified"}
            if sha(database) != database_sha or load_release_pair_manifest(data / "release-pair-live-manifest.json") != live_pair:
                raise ValueError("Read-only warm startup changed admitted database identity")
            result.update(status="verified", database_sha256=database_sha)
        except Exception as error:
            result["error"] = str(error)
            raise
        finally:
            result["elapsed_seconds"] = time.monotonic() - started
            result["command_seconds"] = timings
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--calculation-plan", type=Path,
                        help="Native star or lunar reaction plan for the bundled database snapshot")
    args = parser.parse_args()
    print(json.dumps(qualify(args.app_root, args.report, calculation_plan=args.calculation_plan), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
