"""Verify EC05b batch facts with the actual CLI and a private background service."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "outputs/engine-continuation-20261002"
FIXTURES = ROOT / "tests/fixtures/engine-continuation"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf8")


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf8")


def legacy_baseline(request, db):
    """Freeze real 0.8.1 single-query files once; compare JSON independent of key order."""
    folder = FIXTURES / "ec05b/legacy-single-0.8.1"
    manifest = folder / "verification.json"
    if manifest.exists():
        report = json.loads(manifest.read_text("utf8"))
        if report["request_sha256"] != hashlib.sha256(canonical(request)).hexdigest():
            raise AssertionError("legacy baseline request changed")
        for item in report["items"]:
            if digest(folder / item["file"]) != item["file_sha256"]:
                raise AssertionError("frozen legacy file changed")
        return report
    if folder.exists():
        raise AssertionError("partial legacy baseline retained; use a reviewed fresh baseline directory")
    folder.mkdir()
    exe = ROOT / "dist/0.8.1/GenshinCalc/GenshinCalc.exe"
    records = []
    for number, item in enumerate(request["items"]):
        source = folder / f"request-{number:02d}.json"
        target = folder / f"result-{number:02d}.json"
        write(source, {**item["request"], "version_line": request["version_line"],
                       "snapshot_id": request["snapshot_id"]})
        process = subprocess.run([str(exe), "--data-dir", str(db.parent), "db", "runtime",
            "--input", str(source), "--output", str(target), "--summary"],
            capture_output=True, encoding="utf8", cwd=ROOT, timeout=120, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (folder / f"receipt-{number:02d}.stdout.json").write_text(process.stdout, "utf8")
        (folder / f"receipt-{number:02d}.stderr.json").write_text(process.stderr, "utf8")
        if process.returncode != 0:
            raise AssertionError(f"legacy query failed: {item['id']}")
        value = json.loads(target.read_text("utf8"))
        records.append({"id": item["id"], "file": target.name,
                        "file_sha256": digest(target),
                        "data_sha256": hashlib.sha256(canonical(value)).hexdigest()})
    report = {"version": "0.8.1", "exe_sha256": digest(exe), "items": records,
              "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
              "hash_convention": "sorted-key compact UTF8 JSON of the complete single-query payload"}
    write(manifest, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-exe", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    allowed = (EVIDENCE / "ec05b").resolve()
    if not output.is_relative_to(allowed) or output == allowed:
        parser.error("输出必须位于EC05b独立子目录")
    if output.exists():
        parser.error("验收使用新目录，保留旧证据")
    output.mkdir(parents=True)
    command = [str(args.cli_exe.resolve())] if args.cli_exe else [sys.executable, "-m", "genshin_app"]
    db = ROOT / "data/genshin.db"
    frozen_request = FIXTURES / "ec05b-research/proposed-batch-request.json"
    request = json.loads(frozen_request.read_text("utf8"))
    before_db, before_request, checks = digest(db), digest(frozen_request), []
    baseline = legacy_baseline(request, db)

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)

    def cli(label, *arguments, expected=0, data_dir=db.parent):
        result = subprocess.run([*command, "--data-dir", str(data_dir), *map(str, arguments)],
            capture_output=True, encoding="utf8", cwd=ROOT, timeout=120, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (output / (label + ".stdout.json")).write_text(result.stdout, "utf8")
        (output / (label + ".stderr.json")).write_text(result.stderr, "utf8")
        check(label + " exit", result.returncode == expected)
        response = json.loads(result.stdout if expected == 0 else result.stderr)
        return response.get("data", response)

    caps = cli("capabilities", "capabilities")
    check("candidate version", caps["version"] == "0.8.2")
    facts = output / "facts"
    started = time.monotonic()
    summary = cli("complete", "db", "runtime", "--input", frozen_request,
                  "--output-dir", facts, "--summary")
    seconds = time.monotonic() - started
    check("complete status and count", summary["status"] == "complete" and summary["counts"]["ok"] == 8)
    check("summary does not echo raw evidence", "raw_nodes" not in (output / "complete.stdout.json").read_text("utf8"))
    index = json.loads((facts / "index.json").read_text("utf8"))
    originals = {entry["id"]: entry for entry in baseline["items"]}
    for entry in index["items"]:
        path = facts / entry["path"]
        item = json.loads(path.read_text("utf8"))
        check(entry["id"] + " manifest SHA/bytes", digest(path) == entry["file_sha"] and path.stat().st_size == entry["bytes"])
        check(entry["id"] + " unchanged single-query data",
              hashlib.sha256(canonical(item["data"])).hexdigest() == originals[entry["id"]]["data_sha256"])
        check(entry["id"] + " same snapshot", item["data"]["snapshot"] == index["snapshot"])
        if entry["id"] in ("ayaka-e", "kazuha-e"):
            check(entry["id"] + " dispatch still pending", item["data"]["action_dispatch"]["status"] == "pending")
    old_index = digest(facts / "index.json")
    # Existing output rejection must not even need an available database.
    denied = cli("existing", "db", "runtime", "--input", frozen_request,
                 "--output-dir", facts, "--summary", expected=1, data_dir=output / "missing-db")
    check("existing manifest rejected before DB read", denied["reason"] == "file_conflict")
    check("existing generation unchanged", digest(facts / "index.json") == old_index)

    partial_request = copy.deepcopy(request)
    partial_request["items"] = [request["items"][4],
        {"id": "explicit-missing", "request": {"action": "attenuation_profile", "group": "EC05b-no-such-group"}},
        request["items"][5]]
    partial_path = output / "partial-request.json"
    write(partial_path, partial_request)
    partial = cli("partial", "db", "runtime", "--input", partial_path,
                  "--output-dir", output / "partial-facts", "--summary", expected=1)
    check("partial has explicit reason", partial["reason"] == "batch_partial")
    partial_index = json.loads((output / "partial-facts/index.json").read_text("utf8"))
    check("partial keeps all positions", [x["status"] for x in partial_index["items"]] == ["ok", "error", "ok"])
    check("partial has complete error file", all((output / "partial-facts" / x["path"]).exists() for x in partial_index["items"]))
    stale = copy.deepcopy(request)
    stale["snapshot_id"] = "EC05b-wrong-snapshot"
    stale_path = output / "stale-request.json"
    write(stale_path, stale)
    rejected = cli("stale", "db", "runtime", "--input", stale_path,
                   "--output-dir", output / "stale-facts", "--summary", expected=1)
    check("stale snapshot rejected", rejected["reason"] == "snapshot_changed")
    check("fatal publishes no manifest", not (output / "stale-facts/index.json").exists())

    # Copy only this task's source DB to a private location. Never use/stop the user's service.
    private = output / ("http-fixture-" + uuid.uuid4().hex[:8])
    private.mkdir()
    shutil.copyfile(db, private / "genshin.db")
    server = subprocess.Popen([*command, "--data-dir", str(private), "db", "serve", "--port", "0"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        discovery = private / "service.json"
        deadline = time.monotonic() + 30
        while not discovery.exists() and time.monotonic() < deadline and server.poll() is None:
            time.sleep(.05)
        check("private service started", discovery.exists() and server.poll() is None)
        base = json.loads(discovery.read_text("utf8"))["base_url"]
        for label, body in (("http-complete", request), ("http-partial", partial_request)):
            req = urllib.request.Request(base + "/v1/runtime/query", canonical(body),
                                         {"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as response:
                raw = response.read()
                check(label + " transport status", response.status == 200)
            (output / (label + ".json")).write_bytes(raw)
            value = json.loads(raw)["data"]
            check(label + " wire bounded", len(raw) <= 8 * 1024 * 1024)
            expected_index = index if label == "http-complete" else partial_index
            check(label + " CLI fingerprint", value["content_fingerprint"] == expected_index["content_fingerprint"])
    finally:
        if server.poll() is None:
            server.terminate()
        _, error = server.communicate(timeout=10)
        (output / "private-service.stderr.txt").write_bytes(error or b"")
    check("private database unchanged", digest(private / "genshin.db") == before_db)
    check("production database unchanged", digest(db) == before_db)
    check("frozen request unchanged", digest(frozen_request) == before_request)
    report = {"status": "passed", "passed": len(checks), "checks": checks,
        "version": caps["version"], "database_sha256": before_db,
        "exe_sha256": digest(args.cli_exe.resolve()) if args.cli_exe else None,
        "complete_cli_seconds": seconds, "request_sha256": before_request,
        "scope": "8项实际资料与旧单项语义对等；同快照小文件、partial/fatal及独立后台HTTP，非角色动作自动化或队伍实验。",
        "not_verified": ["前台GUI", "安装器", "游戏客户端", "稳定吞吐量"]}
    write(output / "verification.json", report)
    print(json.dumps({"passed": len(checks), "output": str(output), "seconds": seconds}, ensure_ascii=False))


if __name__ == "__main__":
    main()
