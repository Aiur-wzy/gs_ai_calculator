"""Build an isolated complete r5 candidate from the exact approved W08 proposal."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from propose_entity_publication_release import (
    BASE,
    BASE_SHA,
    TARGET,
    UPSTREAM_COMMIT,
    sha256_file,
)

from genshin_db.content_change_set import validate_approval, validate_change_set
from genshin_db.pipeline import build_candidate_database
from genshin_db.release_hygiene import validate_database_release_hygiene
from genshin_db.release_integrity import build_release_core
from genshin_db.release_profile import (
    create_database_release_manifest,
    write_database_release_manifest,
)


def verify_public_queries(database, operations):
    from genshin_app.public_appendix_query import read_appendix
    from genshin_db.catalog_query import query_catalog

    appendices = []
    for operation in operations:
        if operation["table"] != "annotation":
            continue
        row = operation["row"]
        request = {key: row[key] for key in ("target_type", "target_id", "version_line", "category", "status")}
        response = read_appendix(database, request)
        actual = next((item for item in response["items"] if item["id"] == row["id"]), None)
        if (actual is None or actual["content"] != row["content"] or actual["execution_authority"] != "none"
                or response["execution_authority"] != "none" or not response["owner_publication"]):
            raise ValueError("candidate_public_appendix_readback_failed")
        appendices.append({"id": actual["id"], "status": actual["status"], "target": response["target"],
                           "execution_authority": actual["execution_authority"]})
    for name, expected in (("米提亚", "released"), ("达妮卡", "unreleased"), ("凯特", "unknown")):
        response = query_catalog(database, {"kind": "character", "query": name})
        row = next((row for row in response["items"] if row["name"] == name), None)
        if row is None or row["publication"]["entity_status"] != expected:
            raise ValueError("candidate_public_catalog_readback_failed")
    return appendices


def build(base, upstream, proposal_path, approval_path, destination, *, workspace_root):
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    change_set = validate_change_set(proposal["change_set"])
    approval = validate_approval(json.loads(approval_path.read_text(encoding="utf-8")), change_set)
    if (change_set["base_database_release_id"] != BASE or change_set["target_database_release_id"] != TARGET
            or proposal["source_upstream_commit"] != UPSTREAM_COMMIT or sha256_file(base) != BASE_SHA):
        raise ValueError("W08 proposal or r4 baseline does not match the reviewed release identity")
    destination.mkdir(parents=True, exist_ok=False)
    database = destination / "genshin.db"
    base_manifest = base.with_name("database-release-manifest.json")
    report = build_candidate_database(base_db=base, output_db=database, repo_dir=upstream,
        target_commit=UPSTREAM_COMMIT, content_change_set=change_set, content_approval=approval,
        base_database_release_id=BASE, target_database_release_id=TARGET,
        base_release_manifest=base_manifest, base_live_receipt=base.with_name("database-live-receipt.json"))
    # Content changes preserve upstream rows; compaction clears SQLite's free pages.
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("VACUUM")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("candidate_foreign_key_check_failed")
    hygiene = validate_database_release_hygiene(database, workspace_root=workspace_root)
    original = build_release_core(base)
    candidate = build_release_core(database)
    before, after = original["tables"], candidate["tables"]
    if set(after) != set(before) | {"entity_publication"} or any(after[name] != row for name, row in before.items()):
        raise ValueError("candidate_upstream_release_core_changed")
    if sha256_file(base) != BASE_SHA:
        raise ValueError("r4_base_bytes_changed")
    appendices = verify_public_queries(database, change_set["operations"])
    manifest = create_database_release_manifest(database, database_release_id=TARGET, release_sequence=7,
        channel="dev", logic_profile="genshin-calculation-v1", data_contract_version=1)
    result = {"status": "verified_candidate_not_activated", "database_release_id": TARGET,
              "release_sequence": 7, "base_sha256": BASE_SHA, "candidate_sha256": sha256_file(database),
              "candidate_size_bytes": database.stat().st_size, "source_upstream_commit": UPSTREAM_COMMIT,
              "change_set_id": change_set["change_set_id"], "approval_digest": approval["approval_digest"],
              "entity_publication_counts": report.stats["entity_publication_counts"],
              "hygiene": hygiene, "upstream_core_tables_preserved": len(before),
              "appendices_verified": appendices}
    write_database_release_manifest(destination / "database-release-manifest.json", manifest)
    with (destination / "candidate-verification.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "upstream", "proposal", "approval", "destination"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.upstream, args.proposal, args.approval, args.destination,
                           workspace_root=Path(__file__).resolve().parents[2]), ensure_ascii=False))


if __name__ == "__main__":
    main()
