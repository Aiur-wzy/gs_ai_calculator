"""EC07a real read-only action-resource projection delivery, not CD binding.

Six explicit resource queries share a snapshot. Raw bytes/pointers, Chinese
descriptions and hold-CD counterexamples are independently read for verification.
Only a new child directory of outputs/.../ec07a may be written.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.request
import zlib
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EC07A = ROOT / "outputs/engine-continuation-20261002/ec07a"
sys.path.insert(0, str(ROOT / "src"))

from genshin_app.version import APP_VERSION
from genshin_db.equipment_queries import borrow_read_connection, equipment_read_session
from genshin_db.runtime_action_resource import get_runtime_action_resource_profile
from genshin_db.runtime_queries import _Nodes, query_runtime, validate_runtime
from genshin_db.service import GenshinHTTPServer

CASES = [
    {"id": "ayaka-q", "character_id": 10000002, "slot": "burst", "cooldown": 20, "charges": 1, "energy": 80, "element": "cryo"},
    {"id": "shenhe-q", "character_id": 10000063, "slot": "burst", "cooldown": 20, "charges": 1, "energy": 80, "element": "cryo"},
    {"id": "kazuha-q", "character_id": 10000047, "slot": "burst", "cooldown": 15, "charges": 1, "energy": 60, "element": "anemo"},
    {"id": "kokomi-q", "character_id": 10000054, "slot": "burst", "cooldown": 18, "charges": 1, "energy": 70, "element": "hydro"},
    {"id": "shenhe-e", "character_id": 10000063, "slot": "skill", "cooldown": 10, "charges": 1, "energy": None, "element": None},
    {"id": "kazuha-e", "character_id": 10000047, "slot": "skill", "cooldown": 6, "charges": 1, "energy": None, "element": None},
]
SCOPE = "仅查询基础技能资源和中文原文，不自动填入CD/次数/耗能，不解释长按、命座、退款、派发或动作适用性。"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")


def node_sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf8")


def at(root, pointer):
    value = root
    if pointer:
        for token in pointer[1:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
    return value


class RawEvidence:
    """Independent JSON decoding on the session-owned read-only connection."""
    def __init__(self, conn, snapshot, check):
        self.conn, self.snapshot, self.check, self.cache = conn, snapshot, check, {}

    def source(self, path):
        if path not in self.cache:
            row = self.conn.execute("SELECT source_sha256,snapshot_id,payload_blob FROM runtime_source "
                "WHERE version_line=? AND source_path=?", (self.snapshot["version_line"], path)).fetchone()
            if row is None:
                raise AssertionError("missing raw source: " + path)
            raw = zlib.decompress(row["payload_blob"])
            self.check(path + " raw file SHA and snapshot", hashlib.sha256(raw).hexdigest() == row["source_sha256"]
                       and row["snapshot_id"] == self.snapshot["snapshot_id"])
            self.cache[path] = (json.loads(raw), row["source_sha256"])
        return self.cache[path]

    def origin(self, origin, label, *, pointer=None):
        payload, source_sha = self.source(origin["source_path"])
        value = at(payload, pointer if pointer is not None else origin["json_pointer"])
        self.check(label + " raw pointer/SHA", source_sha == origin["source_sha256"]
                   and node_sha(value) == origin["node_sha256"])
        return value


def verify_profile(data, case, raw, check):
    label = case["id"]
    check(label + " scope stays non-executing", data["scope"] == "base_skill_record"
          and data["variant_applicability"]["status"] == "not_evaluated"
          and data["execution_support"] == data["gameplay_interpretation"] == "not_evaluated"
          and data["action_dispatch"]["status"] == "pending")
    check(label + " exact selected identity", data["selection"]["character_id"] == case["character_id"]
          and data["selection"]["slot"] == case["slot"])
    identities = {}
    for kind, record in data["identity_sources"].items():
        identities[kind] = raw.origin(record, label + " " + kind)
        stored = raw.conn.execute("SELECT node_sha256,source_path,json_pointer,snapshot_id FROM runtime_definition "
            "WHERE version_line=? AND definition_id=?", (data["snapshot"]["version_line"], record["definition"]["definition_id"])).fetchone()
        check(label + " " + kind + " exact definition", stored is not None and stored["node_sha256"] == record["node_sha256"]
              and stored["source_path"] == record["source_path"] and stored["json_pointer"] == record["json_pointer"]
              and stored["snapshot_id"] == data["snapshot"]["snapshot_id"])
    skill = identities["skill"]
    for key, expected, source_field in (("base_cooldown_seconds", case["cooldown"], "cdTime"),
        ("base_charge_count", case["charges"], "maxChargeNum"), ("base_energy_cost", case["energy"], "costElemVal"),
        ("cost_element", case["element"], "costElemType")):
        field = data["projection"][key]
        origin = field["origin"]
        present = source_field in skill
        check(label + " " + key + " expected value/status", field["value"] == expected
              and field["status"] == ("ready" if present else "pending"))
        check(label + " " + key + " source field and raw preservation", field["source_field"] == source_field
              and origin["present"] is present and field["raw_value"] == skill.get(source_field))
        check(label + " " + key + " parent origin", origin["node_sha256"] == data["identity_sources"]["skill"]["node_sha256"]
              and origin["json_pointer"] == data["identity_sources"]["skill"]["json_pointer"])
        if present:
            payload, source_sha = raw.source(origin["source_path"])
            value = at(payload, origin["raw_pointer"])
            check(label + " " + key + " field pointer/SHA", value == field["raw_value"]
                  and node_sha(value) == origin["field_node_sha256"] and source_sha == origin["source_sha256"])
        else:
            check(label + " " + key + " absence is not zero", field["value"] is None
                  and origin["raw_pointer"] is None and origin["field_node_sha256"] is None)
    check(label + " top status does not hide missing fields", data["status"] == ("ready" if case["slot"] == "burst" else "partial"))
    descriptions = data["descriptions"]
    for key in ("name", "description"):
        item = descriptions[key]
        text = raw.origin(item["text_origin"], label + " Chinese " + key)
        check(label + " Chinese original " + key, item["status"] == "ready" and item["text"] == text
              and any("\u4e00" <= c <= "\u9fff" for c in text))
        hash_origin = item["hash_origin"]
        skill_source, _ = raw.source(hash_origin["source_path"])
        check(label + " Chinese hash relation " + key, at(skill_source, hash_origin["raw_pointer"]) == item["text_map_hash"]
              and item["text_origin"]["json_pointer"] == "/" + str(item["text_map_hash"]))
    return skill


def hold_evidence(data, skill, index, expected, raw, check):
    path = "ExcelBinOutput/ProudSkillExcelConfigData.json"
    rows, source_sha = raw.source(path)
    matching = [(i, row) for i, row in enumerate(rows)
                if row.get("proudSkillGroupId") == skill["proudSkillGroupId"] and row.get("level") == 10]
    check(str(skill["id"]) + " hold parameter identity unique", len(matching) == 1)
    position, row = matching[0]
    texts, text_sha = raw.source("TextMap/TextMap_MediumCHS.json")
    descriptions = [{"hash": h, "text": texts.get(str(h)), "pointer": "/"+str(h)}
                    for h in row["paramDescList"] if "{param"+str(index+1)+":" in texts.get(str(h), "")]
    value = row["paramList"][index]
    check(str(skill["id"]) + " hold differs from base with Chinese evidence", math.isclose(value, expected, abs_tol=1e-9)
          and value != data["projection"]["base_cooldown_seconds"]["value"]
          and len(descriptions) == 1 and "长按冷却时间" in descriptions[0]["text"])
    return {"kind": "independent_counterexample_not_resource_api_inference", "selection": data["selection"],
        "base_cooldown_seconds": data["projection"]["base_cooldown_seconds"]["value"], "hold_cooldown_seconds": value,
        "source_path": path, "source_sha256": source_sha, "node_pointer": f"/{position}", "node_sha256": node_sha(row),
        "parameter_pointer": f"/{position}/paramList/{index}", "parameter_node_sha256": node_sha(value),
        "proud_skill_id": row["proudSkillId"], "explicit_comparison_level": 10,
        "description_evidence": descriptions, "text_source_sha256": text_sha,
        "note": "长按数值仅作为独立反例证据；新资源API不读ProudSkill、不接收talent_level，未自动决定press/hold。"}


def read_response(proc):
    return json.loads(proc.stdout if proc.returncode == 0 else proc.stderr)


def verify_http(output, db, request, singles, batch, check):
    # Server startup creates annotation tables/token, so isolate those writes in
    # a private empty database. Only actual query routes read the selected DB.
    private = output / "http-startup"
    private.mkdir()
    (private / "genshin.db").touch()
    server = GenshinHTTPServer(private, port=0)
    server.db_path = db
    thread = threading.Thread(target=server.serve_forever, name="ec07a-http", daemon=True)
    thread.start()
    receipts = []
    try:
        single_request = json.loads((output / "ayaka-q.query.json").read_text("utf8"))
        for label, body, expected in (("single", single_request, singles["ayaka-q"]), ("batch", request, batch)):
            started = time.perf_counter()
            req = urllib.request.Request(server.base_url + "/v1/runtime/query", canonical(body),
                                         {"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=120) as response:
                wire = response.read()
                status = response.status
            (output / ("http-" + label + ".json")).write_bytes(wire)
            value = json.loads(wire)
            check("HTTP " + label + " real POST equals API", status == 200 and value["code"] == 0
                  and value["data"] == expected)
            check("HTTP " + label + " bounded wire bytes", len(wire) <= 8 * 1024 * 1024)
            receipts.append({"kind": label, "method": "POST", "path": "/v1/runtime/query", "status": status,
                             "bytes": len(wire), "seconds": time.perf_counter()-started})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        write(output / "http-receipt.json", {"requests": receipts, "own_thread_stopped": not thread.is_alive(),
            "scope": "源码真实HTTP路由；私有空库隔离初始化写入后，仅查询server.db_path指向指定只读库。未验证生产服务初始化或EXE HTTP。"})
    check("only own HTTP server stopped", not thread.is_alive())


def verify(args, check):
    output, db = args.output_dir, args.data_dir / "genshin.db"
    request = {"action": "batch", "version_line": "release", "items": [{"id": c["id"], "request": {
        "action": "action_resource_profile", "character_id": c["character_id"], "slot": c["slot"]}} for c in CASES]}
    originals = copy.deepcopy(request)
    source_paths, original_source = [], _Nodes.source
    def observed_source(self, path):
        source_paths.append(path)
        return original_source(self, path)
    print("EC07a: six resource queries and independent raw evidence in one read session", flush=True)
    with patch("genshin_db.runtime_queries.validate_runtime", wraps=validate_runtime) as validated, equipment_read_session(db):
        with patch.object(_Nodes, "source", observed_source):
            batch = query_runtime(db, request)
            check("six-query batch complete", batch["status"] == "complete" and batch["counts"]["ok"] == 6)
            singles = {}
            for case, item in zip(CASES, batch["items"], strict=True):
                selected = {"character_id": case["character_id"], "slot": case["slot"], "version_line": "release",
                            "snapshot_id": batch["snapshot"]["snapshot_id"]}
                single = get_runtime_action_resource_profile(db, **selected)
                singles[case["id"]] = single
                check(case["id"] + " direct API equals batch data", single == item["data"])
        check("only one validation in shared session", validated.call_count == 1)
        check("resource API never reads talent or parameter sources", not any("ProudSkill" in p or "/Talent/" in p for p in source_paths)
              and all("talent_level" not in item["request"] for item in request["items"]))
        with closing(borrow_read_connection(db)) as conn:
            raw = RawEvidence(conn, batch["snapshot"], check)
            skills = {case["id"]: verify_profile(singles[case["id"]], case, raw, check) for case in CASES}
            hold = [hold_evidence(singles[key], skills[key], index, value, raw, check)
                    for key, index, value in (("shenhe-e", 8, 15), ("kazuha-e", 3, 9))]
        negative_requests = {"unknown-slot": {"action": "action_resource_profile", "character_id": 10000002, "slot": "hold"},
            "foreign-skill": {"action": "action_resource_profile", "character_id": 10000047, "skill_id": 10019},
            "talent-level-not-an-input": {"action": "action_resource_profile", "character_id": 10000002, "slot": "burst", "talent_level": 10}}
        negatives = {}
        for label, negative in negative_requests.items():
            try:
                query_runtime(db, negative)
            except ValueError as exc:
                negatives[label] = {"type": type(exc).__name__, "message": str(exc)}
                check(label + " rejected as input error", True)
            else:
                raise AssertionError(label + " unexpectedly accepted")
    check("input request unchanged", request == originals)
    request["snapshot_id"] = batch["snapshot"]["snapshot_id"]
    write(output / "request.json", request)
    write(output / "api-batch.json", batch)
    write(output / "hold-counterexamples.json", hold)
    write(output / "api-rejected.json", negatives)
    write(output / "source-access.json", {"paths": sorted(set(source_paths)), "validation_count": validated.call_count,
          "note": "只记录资源API本身；独立反例核验随后另读ProudSkill/原文，不计为接口依赖。"})
    for key, value in singles.items():
        write(output / (key + ".query.json"), {"action": "action_resource_profile", "character_id": value["selection"]["character_id"],
              "slot": value["selection"]["slot"], "version_line": "release", "snapshot_id": batch["snapshot"]["snapshot_id"]})
        write(output / (key + ".api.json"), value)
    for label, negative in negative_requests.items():
        write(output / (label + ".query.json"), negative)
    print("EC07a: own background HTTP single and batch routes", flush=True)
    verify_http(output, db, request, singles, batch, check)
    command_records = []
    commands = [("source-cli", [sys.executable, "-m", "genshin_app"])]
    if args.cli_exe:
        commands.append(("package-cli", [str(args.cli_exe)]))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1"}
    for label, command in commands:
        print("EC07a: " + label + " small fact files, bounded summaries and input rejection", flush=True)
        def execute(name, arguments, command=command, label=label):
            argv = [*command, "--data-dir", str(args.data_dir), *map(str, arguments)]
            started = time.perf_counter()
            proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, encoding="utf8", timeout=180,
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            (output / f"{label}-{name}.stdout.json").write_text(proc.stdout, "utf8")
            (output / f"{label}-{name}.stderr.json").write_text(proc.stderr, "utf8")
            command_records.append({"argv": argv, "returncode": proc.returncode, "seconds": time.perf_counter()-started})
            return proc
        facts = output / (label + "-facts")
        proc = execute("batch", ["db", "runtime", "--input", output / "request.json", "--output-dir", facts, "--summary"])
        check(label + " batch query success and bounded stdout", proc.returncode == 0 and len(proc.stdout.encode("utf8")) < 32768)
        summary = read_response(proc)["data"]
        check(label + " batch complete with pending semantics preserved", summary["status"] == "complete"
              and all(i["result"]["execution_support"] == "not_evaluated" and i["result"]["action_dispatch"]["status"] == "pending"
                      and i["result"]["variant_applicability"]["status"] == "not_evaluated" for i in summary["items"]))
        check(label + " no descriptions/raw data in stdout", all(token not in proc.stdout for token in ('"identity_sources"', '"raw_value"', '"text_origin"', '"text":')))
        manifest = json.loads((facts / "index.json").read_text("utf8"))
        check(label + " manifest binds same batch", manifest["content_fingerprint"] == batch["content_fingerprint"] and len(manifest["items"]) == 6)
        for entry in manifest["items"]:
            path = (facts / entry["path"]).resolve()
            check(label + " bounded generation path " + entry["id"], path.is_relative_to(facts.resolve()) and path != (facts / "index.json").resolve())
            item = json.loads(path.read_text("utf8"))
            check(label + " file bytes/SHA " + entry["id"], digest(path) == entry["file_sha"] and path.stat().st_size == entry["bytes"])
            check(label + " full data equals direct API " + entry["id"], item["status"] == "ok" and item["data"] == singles[entry["id"]]
                  and item["data"]["snapshot"] == manifest["snapshot"])
        for key in ("ayaka-q", "shenhe-e"):
            target = output / f"{label}-{key}.json"
            proc = execute(key, ["db", "runtime", "--input", output / (key + ".query.json"), "--output", target, "--summary"])
            check(label + " single full result " + key, proc.returncode == 0 and json.loads(target.read_text("utf8")) == singles[key])
            projected = read_response(proc)["data"]["projection_summary"]
            check(label + " summary resource status " + key, projected["base_energy_cost"]["status"] == singles[key]["projection"]["base_energy_cost"]["status"]
                  and projected["base_cooldown_seconds"]["value"] == singles[key]["projection"]["base_cooldown_seconds"]["value"])
        for negative in negative_requests:
            forbidden = output / f"{label}-{negative}-must-not-exist.json"
            proc = execute(negative, ["db", "runtime", "--input", output / (negative + ".query.json"), "--output", forbidden, "--summary"])
            check(label + " rejected without output " + negative, proc.returncode != 0 and not forbidden.exists())
    write(output / "commands.json", command_records)
    return {"source_version": APP_VERSION, "source_snapshot": batch["snapshot"], "scope": SCOPE,
        "source_count": len(CASES), "batch_fingerprint": batch["content_fingerprint"],
        "actual_resource_values": [{"id": key, "status": data["status"], "projection": {k: v["value"] for k, v in data["projection"].items()}}
                                   for key, data in singles.items()],
        "verified_interfaces": ["source direct API", "same-snapshot source batch", "source background HTTP single/batch",
                                "source CLI summary", "generation/manifest fact files"]
             + (["candidate EXE CLI"] if args.cli_exe else []),
        "not_verified": ["automatic resource input or applicability", "constellation effects", "shared cooldown semantics",
                         "GUI interaction", "game-client values", "CD/energy execution", "character action dispatch"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=EC07A / "source-delivery")
    parser.add_argument("--cli-exe", type=Path)
    args = parser.parse_args()
    args.data_dir, args.output_dir = args.data_dir.resolve(), args.output_dir.resolve()
    if not args.output_dir.is_relative_to(EC07A.resolve()) or args.output_dir == EC07A.resolve():
        parser.error("--output-dir必须是EC07a全新独立子目录")
    if args.output_dir.exists():
        parser.error("输出目录已存在，请另选全新目录；禁止覆盖历史")
    db = args.data_dir / "genshin.db"
    if not db.is_file():
        parser.error("--data-dir中缺少genshin.db")
    if args.cli_exe:
        args.cli_exe = args.cli_exe.resolve()
        if not args.cli_exe.is_file():
            parser.error("--cli-exe不存在")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    checks, before, started = [], digest(db), time.perf_counter()
    report = {"status": "running", "checks": checks, "database_sha256_before": before, "data_dir": str(args.data_dir),
              "cli_exe": str(args.cli_exe) if args.cli_exe else None}
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)
    try:
        report.update(verify(args, check))
        check("selected database bytes unchanged", digest(db) == before)
        report.update(status="passed", passed=len(checks))
    except Exception as exc:
        report.update(status="failed", passed=sum(c["passed"] for c in checks), error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        report.update(database_sha256_after=digest(db), seconds=time.perf_counter()-started)
        write(args.output_dir / "verification.json", report)
    print(json.dumps({"status": "passed", "checks": len(checks), "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
