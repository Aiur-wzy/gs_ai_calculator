"""Small two-source DB fixtures; no formal DB or network, no runtime adapters."""
import copy
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import pytest

from genshin_db.runtime_queries import get_runtime_status
from genshin_db.semantic_store import import_bundle
from genshin_runtime.limited_hit_benefit_identity import recipe
from genshin_runtime.mechanism_semantics import fingerprint
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build, write

# ruff: noqa: F811
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mechanism_repo(fake_repo):
    raw = recipe()
    tables = defaultdict(list)
    for entry in raw["fixture_records"]:
        tables[entry["source_path"]].append(copy.deepcopy(entry["raw"]))
    write(fake_repo, raw["ability"]["source_path"], [{"Default": raw["ability"]["new_raw"]}])
    write(fake_repo, raw["talent"]["source_path"], {raw["talent"]["name"]: raw["talent"]["raw"]})
    passive = json.loads((ROOT / "tests/fixtures/runtime_passive_kazuha.json").read_text("utf8"))
    bundle, records = passive["source_bundle"], passive["entry_records"]
    for entry in (records["avatar"], records["depot"], bundle["parameter_record"], passive["unlock"]["selected_promote_record"]):
        tables[entry["source_path"]].append(copy.deepcopy(entry["record"]))
    for path, rows in tables.items():
        write(fake_repo, path, rows)
    ability, talent = bundle["abilities"][0], bundle["talent"]
    write(fake_repo, ability["source_path"], [{"Default": ability["payload"]}])
    write(fake_repo, talent["source_path"], {talent["json_pointer"][1:]: talent["operations"]})
    return fake_repo


def fixture_catalog(db, *, approve=True):
    """Relocate the same complete raw nodes to a tiny fixture snapshot for tests."""
    seed = json.loads((ROOT / "src/genshin_db/resources/mechanisms/reviewed-mechanisms-v1.json").read_text("utf8"))
    snapshot = get_runtime_status(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        evidence_by_subject = {}
        for record in seed["descriptors"]:
            binding = record["payload"]["source_binding"]
            # The packaged catalog is a frozen historical review.  This helper
            # relocates its unchanged semantics into the tiny *current* test
            # snapshot, so every part of the source boundary must move
            # together; otherwise the descriptor correctly fails closed as a
            # mixed old-commit/current-evidence record.
            binding["upstream_commit"] = snapshot["upstream_commit"]
            binding["normalization_fingerprint"] = fingerprint(
                snapshot["normalization"]
            )
            for node in binding["nodes"]:
                rows = conn.execute("SELECT * FROM runtime_definition WHERE version_line='release' "
                    "AND source_path=? AND definition_kind=? AND node_sha256=?",
                    (node["source_path"], node["definition_kind"], node["node_sha256"])).fetchall()
                assert len(rows) == 1, node
                node["json_pointer"] = rows[0]["json_pointer"]
                node["source_sha256"] = conn.execute("SELECT source_sha256 FROM runtime_source WHERE version_line='release' AND source_path=?",
                                                     (node["source_path"],)).fetchone()[0]
                e = next(e for e in binding["evidence"] if e["id"] == node["id"])
                e.update(locator=node["source_path"] + "#" + node["json_pointer"], sha256=node["node_sha256"])
            evidence_by_subject[record["subject"]["target_id"]] = binding["evidence"]
        for record in [*seed["descriptors"], *seed["facts"]]:
            record["evidence"] = copy.deepcopy(evidence_by_subject[record["subject"]["target_id"]])
            record["applicability"] = {"version_line": "release", "upstream_commit": snapshot["upstream_commit"],
                                       "normalization_fingerprint": fingerprint(snapshot["normalization"])}
            if approve:
                record["review"] = {"status": "approved", "reviewer": "isolated-fixture-only",
                                    "reviewed_at": "2026-10-03T00:00:00+00:00"}
    return seed


def fixture_qualified_catalog(db, *, approve=True):
    """Return the explicit v2 migration, relocated to the same isolated sources.

    Approval is fixture-only and never alters the pending packaged seed. Callers
    must have installed v1 first; descriptor head CAS remains revision 1 -> 2.
    """
    seed = json.loads((ROOT / "src/genshin_db/resources/mechanisms/reviewed-mechanisms-v2.json").read_text("utf8"))
    prior = {r["id"]: r for r in fixture_catalog(db, approve=approve)["descriptors"]}
    by_subject = {}
    for record in seed["descriptors"]:
        source = prior[record["id"]]
        record["payload"]["source_binding"] = copy.deepcopy(source["payload"]["source_binding"])
        for key in ("evidence", "applicability", "review"):
            record[key] = copy.deepcopy(source[key])
        by_subject[record["subject"]["target_id"]] = record
    for record in seed["facts"]:
        for key in ("evidence", "applicability", "review"):
            record[key] = copy.deepcopy(by_subject[record["subject"]["target_id"]][key])
    return seed


@pytest.fixture
def mechanism_db(tmp_path, mechanism_repo):
    db = tmp_path / "mechanisms.db"
    build(db, mechanism_repo, upstream_commit=recipe()["commit"])
    assert import_bundle(db, fixture_catalog(db), dry_run=False)["applied"]
    return db
