"""Real Gadget identities through source/CLI/HTTP; no gameplay dispatch claim."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zlib
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from genshin_db.service import GenshinHTTPServer

CASES = (
    ("ayaka-main", 41002001, "ConfigAbility_Avatar_Ayaka.json", "/14/Default/ECFFDLHMCLK/0/DFLNJNIGJLL", "FrozenWindmill"),
    ("ayaka-sub-one", 41002005, "ConfigAbility_Avatar_Ayaka.json", "/14/Default/ECFFDLHMCLK/1/DFLNJNIGJLL", "FrozenWindmill_Sub"),
    ("ayaka-sub-two", 41002005, "ConfigAbility_Avatar_Ayaka.json", "/14/Default/ECFFDLHMCLK/2/DFLNJNIGJLL", "FrozenWindmill_Sub"),
    ("kokomi-jelly", 41054002, "ConfigAbility_Avatar_Kokomi.json", "/13/Default/ECFFDLHMCLK/1/BMEPMJIOPDC", "Kokomi_ElementalArt_Gadget"),
    ("shenhe-shadow", 41063003, "ConfigAbility_Avatar_Shenhe.json", "/10/Default/GPPBBDEICKP/Shenhe_ElementalBurst_ShadowHandler/HHDOFNHCIAE/0/BMEPMJIOPDC", "Shenhe_Shadow"),
)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fp(value):
    return hashlib.sha256(canonical(value).encode("utf8")).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


def pointer(value, path):
    for token in path.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def main(args):
    db, out = args.data_dir / "genshin.db", args.output_dir
    if out.exists():
        raise SystemExit("Choose a fresh delivery directory; existing evidence is preserved")
    out.mkdir(parents=True)
    before, checks = sha(db), []

    def check(name, ok):
        checks.append({"name": name, "passed": bool(ok)})
        if not ok:
            raise AssertionError(name)

    def run_cli(name, prefix, request, expected_ok=True, file_mode="--output"):
        req, dest = out / (name + "-request.json"), out / (name + ("-files" if file_mode == "--output-dir" else ".json"))
        write(req, request)
        command = [*prefix, "--data-dir", str(args.data_dir), "db", "runtime", "--input", str(req), file_mode, str(dest), "--summary"]
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf8", timeout=180, check=False)
        (out / (name + ".stdout.json")).write_text(proc.stdout, encoding="utf8")
        (out / (name + ".stderr.txt")).write_text(proc.stderr, encoding="utf8")
        check(name + " CLI status", (proc.returncode == 0) == expected_ok)
        check(name + " bounded console", len(proc.stdout.encode("utf8")) < 12000 and len(proc.stderr.encode("utf8")) < 12000)
        return dest, proc

    try:
        caps = runtime_capabilities()
        check("new self describing operation", "gadget_source_profile" in caps["actions"] and caps["gadget_source_profile"]["minimum_indexer_version"] == "5")
        write(out / "runtime-capabilities.json", caps)
        requests = []
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            sources = {}

            def raw_source(path):
                if path not in sources:
                    row = conn.execute("SELECT payload_blob,source_sha256 FROM runtime_source WHERE version_line='release' AND source_path=?", (path,)).fetchone()
                    raw = zlib.decompress(row[0])
                    assert hashlib.sha256(raw).hexdigest() == row[1]
                    sources[path] = (json.loads(raw), row[1])
                return sources[path]

            for name, ident, filename, ptr, config_name in CASES:
                rows = conn.execute("SELECT reference_id,from_definition_id,source_path FROM runtime_reference WHERE version_line='release' AND json_pointer=? AND source_path LIKE ?", (ptr, "%/" + filename)).fetchall()
                assert len(rows) == 1, (name, rows)
                requests.append({"id": name, "request": {"action": "gadget_source_profile", "gadget_id": ident, "origin_reference_id": rows[0][0]}})
        batch_request = {"action": "batch", "version_line": "release", "items": requests}
        result = query_runtime(db, batch_request)
        write(out / "source-origin-batch.json", result)
        check("five real creation leaves resolve", result["status"] == "complete" and result["counts"]["ok"] == 5)
        by_name = {item["id"]: item["data"] for item in result["items"]}
        # Independent raw source reads; do not call the extractor to create expected values.
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            for name, ident, filename, ptr, config_name in CASES:
                data = by_name[name]
                check(name + " identity ready only", data["status"] == "ready" and data["graph_scope"] == "gadget_identity_v1" and data["execution"] == {"status": "not_evaluated", "creation": "not_evaluated", "controller": "not_evaluated"})
                check(name + " correct exact row and config", data["record"]["record"]["id"] == ident and data["record"]["record"]["jsonName"] == config_name and data["config"]["selected"]["definition"]["exact_key"] == config_name)
                origin = data["origin"]
                check(name + " selected raw creation leaf", origin["status"] == "verified_relationship" and origin["operation"]["id_pointer"] == ptr and origin["operation"]["raw_id"] == ident and origin["legacy_reference"]["relation_kind"] == "external_asset" and origin["object_reference"]["relation_kind"] == "gadget_id_reference")
                nodes = [data["record"], data["config"]["selected"], origin["parent"], *[m["selected"] for m in data["mounted_abilities"]]]
                if origin["owner_ability"]:
                    nodes.append(origin["owner_ability"])
                for node in nodes:
                    raw, source_sha = raw_source(node["source_path"])
                    expected = pointer(raw, node["json_pointer"])
                    payload = node["record"] if "record" in node else node["payload"]
                    assert payload == expected and node["source_sha256"] == source_sha and node["node_sha256"] == fp(expected)
                check(name + " all full nodes match immutable DB bytes", True)
                check(name + " full bounded receipt", len(canonical(data).encode("utf8")) <= 262144 and data["source_fingerprint"] != data["content_fingerprint"])
                check(name + " declared mounts all exact", data["mount_list_field_status"] == "present" and data["mount_list_status"] == "ready" and all(m["status"] == "ready" for m in data["mounted_abilities"]))
        check("same parent same ID distinct leaves have different identity", by_name["ayaka-sub-one"]["source_fingerprint"] != by_name["ayaka-sub-two"]["source_fingerprint"])
        check("modifier includes complete owner Ability", by_name["shenhe-shadow"]["origin"]["owner_ability"] is not None)
        shared = query_runtime(db, {"action": "batch", "items": [{"id": "id" + str(i), "request": {"action": "gadget_source_profile", "gadget_id": i}} for i in (40000115, 40000116, 40000117)]})
        write(out / "same-name-distinct-ids.json", shared)
        check("three shared config IDs independently retained", shared["counts"]["ok"] == 3 and len({r["data"]["source_fingerprint"] for r in shared["items"]}) == 3)
        check("query without origin does not invent actor", all(r["data"]["origin"]["status"] == "not_requested" for r in shared["items"]))
        # Read-only real HTTP path on an ephemeral localhost port. No desktop window.
        # Initial token/schema creation belongs to a disposable server directory;
        # only the read-only query handler is pointed at the audited database.
        http_state = out / "http-state"
        http_state.mkdir()
        with closing(sqlite3.connect(http_state / "genshin.db")) as temporary_db, temporary_db:
            temporary_db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
        server = GenshinHTTPServer(http_state, port=0)
        server.db_path = db
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        worker.start()
        try:
            req = urllib.request.Request(server.base_url + "/v1/runtime/query", data=canonical(requests[0]["request"]).encode("utf8"), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=120) as response:
                payload = json.load(response)
                check("real HTTP supports same source identity", response.status == 200 and payload["data"]["source_fingerprint"] == by_name["ayaka-main"]["source_fingerprint"])
            write(out / "http-gadget.json", payload)
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
        prefixes = [("source", [sys.executable, "-X", "utf8", "-m", "genshin_app"])]
        if args.cli_exe:
            prefixes.append(("package", [str(args.cli_exe)]))
        for label, prefix in prefixes:
            dest, _ = run_cli(label + "-one", prefix, requests[0]["request"])
            file_data = json.loads(dest.read_text("utf8"))
            data = file_data.get("data", file_data)
            check(label + " complete file identity", data["source_fingerprint"] == by_name["ayaka-main"]["source_fingerprint"])
            dest, _ = run_cli(label + "-batch", prefix, batch_request, file_mode="--output-dir")
            index = json.loads((dest / "index.json").read_text("utf8"))
            check(label + " batch publishes index", index["status"] == "complete")
            for item in index["items"]:
                file_path = (dest / item["path"]).resolve()
                assert file_path.is_relative_to(dest) and sha(file_path) == item["file_sha"]
                complete_item = json.loads(file_path.read_text("utf8"))
                assert complete_item["data"]["source_fingerprint"] == by_name[item["id"]]["source_fingerprint"]
            check(label + " every small file SHA and source verified", True)
            write(out / (label + "-index-copy.json"), index)
        if args.old_data_dir:
            upgrade = {"action": "batch", "items": [{"id": "gadget", "request": {"action": "gadget_source_profile", "gadget_id": 41002001}}, {"id": "old-query", "request": {"action": "action_resource_profile", "character_id": 10000002, "slot": "burst"}}]}
            old = query_runtime(args.old_data_dir / "genshin.db", upgrade)
            write(out / "old-index-batch.json", old)
            check("old index explicit per-item upgrade and continue", old["status"] == "partial" and old["items"][0]["error"]["reason"] == "gadget_index_upgrade_required" and old["items"][1]["status"] == "ok")
        if args.legacy_cli_exe:
            request_path = out / "legacy-program-request.json"
            write(request_path, {"action": "action_resource_profile", "character_id": 10000002, "slot": "burst"})
            old_program = subprocess.run([str(args.legacy_cli_exe), "--data-dir", str(args.data_dir), "db", "runtime", "--input", str(request_path)], cwd=ROOT, capture_output=True, encoding="utf8", timeout=120, check=False)
            (out / "legacy-program.stdout.txt").write_text(old_program.stdout, "utf8")
            (out / "legacy-program.stderr.json").write_text(old_program.stderr, "utf8")
            error = json.loads(old_program.stderr)
            check("previous EXE rejects index5 with controlled reason", old_program.returncode == 1 and error.get("reason") == "unsupported_schema" and not old_program.stdout)
        check("database bytes unchanged", sha(db) == before)
        receipt = {"status": "passed", "scope": "real identity/CLI/HTTP only; no gameplay dispatch", "passed": len(checks), "checks": checks,
                   "database_sha256": before, "cli_exe": str(args.cli_exe) if args.cli_exe else None,
                   "exe_sha256": sha(args.cli_exe) if args.cli_exe else None, "snapshot": result["snapshot"]}
        write(out / "verification.json", receipt)
        print(canonical({"status": "passed", "checks": len(checks), "output": str(out)}), flush=True)
    except Exception as exc:
        write(out / "verification-failed.json", {"checks": checks, "error": repr(exc), "database_sha256_before": before, "database_sha256_after": sha(db)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cli-exe", type=Path)
    parser.add_argument("--old-data-dir", type=Path)
    parser.add_argument("--legacy-cli-exe", type=Path)
    args = parser.parse_args()
    for name in ("data_dir", "output_dir", "cli_exe", "old_data_dir", "legacy_cli_exe"):
        value = getattr(args, name)
        if value:
            setattr(args, name, value.resolve())
    if not args.output_dir.is_relative_to(ROOT / "outputs"):
        parser.error("Evidence must remain inside workspace outputs")
    main(args)
