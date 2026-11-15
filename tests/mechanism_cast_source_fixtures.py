"""EC22 isolated real-node migration; approval helpers are tests-only."""
import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from genshin_db.runtime_queries import get_runtime_status
from genshin_db.semantic_store import import_bundle
from genshin_runtime.limited_hit_benefit_identity import recipe
from genshin_runtime.mechanism_semantics import fingerprint
from tests.mechanism_source_fixtures import (  # noqa: F401
    ROOT,
    fake_repo,
    fixture_catalog,
    fixture_qualified_catalog,
    mechanism_repo,
)
from tests.test_runtime_object_index import build, write

# ruff: noqa: F811
IDS = ("skill.current-atk-limited-addition", "artifact.burst-team-atk-percent")


def cast_seed():
    return json.loads((ROOT / "src/genshin_db/resources/mechanisms/reviewed-mechanisms-v3.json").read_text("utf8"))


@pytest.fixture
def cast_repo(mechanism_repo):
    nodes = json.loads((Path(__file__).parent / "fixtures/mechanism_cast_nodes.json").read_text("utf8"))["nodes"]
    for n in nodes:
        if not n["id"].startswith("noblesse."):
            continue
        path = n["source_path"]
        if n["definition_kind"] == "ability":
            data = [{"Default": n["raw"]}]
        elif n["definition_kind"] == "talent_config":
            data = {n["json_pointer"][1:]: n["raw"]}
        else:
            file = mechanism_repo / path
            data = json.loads(file.read_text("utf8")) if file.exists() else []
            data.append(n["raw"])
        write(mechanism_repo, path, data)
    return mechanism_repo


def fixture_cast_catalog(db, *, approve=False):
    """Relocate a seed to these exact small sources; never modify packaged seed."""
    seed = cast_seed()
    snapshot = get_runtime_status(db)
    by_subject = {}
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        for r in seed["descriptors"]:
            b = r["payload"]["source_binding"]
            for n in b["nodes"]:
                rows = conn.execute("SELECT * FROM runtime_definition WHERE version_line='release' AND source_path=? AND definition_kind=? AND node_sha256=?",
                                    (n["source_path"], n["definition_kind"], n["node_sha256"])).fetchall()
                assert len(rows) == 1, n
                n["json_pointer"] = rows[0]["json_pointer"]
                n["source_sha256"] = conn.execute("SELECT source_sha256 FROM runtime_source WHERE version_line='release' AND source_path=?", (n["source_path"],)).fetchone()[0]
                e = next(e for e in b["evidence"] if e["id"] == n["id"])
                e.update(locator=n["source_path"] + "#" + n["json_pointer"], sha256=n["node_sha256"])
            b["upstream_commit"] = snapshot["upstream_commit"]
            b["normalization_fingerprint"] = fingerprint(snapshot["normalization"])
            by_subject[r["subject"]["target_id"]] = b
    for r in [*seed["facts"], *seed["descriptors"]]:
        b = by_subject[r["subject"]["target_id"]]
        r["evidence"] = copy.deepcopy(b["evidence"])
        r["applicability"] = {"version_line": "release", "upstream_commit": b["upstream_commit"], "normalization_fingerprint": b["normalization_fingerprint"]}
        if approve:
            r["review"] = {"status": "approved", "reviewer": "isolated-cast-fixture-only", "reviewed_at": "2026-10-03T00:00:00+00:00"}
    return seed


@pytest.fixture
def cast_db(tmp_path, cast_repo):
    db = tmp_path / "cast-sources.db"
    build(db, cast_repo, upstream_commit=recipe()["commit"])
    assert import_bundle(db, fixture_catalog(db), dry_run=False)["applied"]
    assert import_bundle(db, fixture_qualified_catalog(db), dry_run=False)["applied"]
    return db


@pytest.fixture
def full_cast_db(tmp_path, cast_repo):
    """Actual raw/curated/equipment/runtime stack for public App integration."""
    from genshin_db.annotation_store import create_annotation_tables
    from genshin_db.equipment import build_equipment_layer
    from genshin_db.extractor import build_curated_layer
    from genshin_db.raw_store import build_raw_layer

    closure = json.loads((ROOT / "tests/fixtures/mechanism_cast_equipment_closure.json").read_text("utf8"))
    for path, source in closure["sources"].items():
        old = json.loads((cast_repo / path).read_text("utf8"))
        records = [row["record"] for row in source["records"]]
        def key(r, level_table="LevelExcel" in path):
            return (r.get("rank"), r["level"]) if level_table else r["id"]
        # Keep unrelated fixture records; collisions take the exact source row.
        merged = {key(r): r for r in old}
        merged.update({key(r): r for r in records})
        write(cast_repo, path, list(merged.values()))
    bundles = json.loads((ROOT / "tests/fixtures/runtime-source-bundles.json").read_text("utf8"))
    lower = bundles["sources"]["2150070"]["affix"]["record"]
    assert lower["affixId"] == 2150070 and lower["id"] == 215007
    path = "ExcelBinOutput/EquipAffixExcelConfigData.json"
    rows = json.loads((cast_repo / path).read_text("utf8"))
    assert not any(r.get("affixId") == lower["affixId"] for r in rows)
    write(cast_repo, path, [*rows, lower])
    db = tmp_path / "full-cast.db"
    stats = build_raw_layer(cast_repo, db)
    commit = recipe()["commit"]
    build_curated_layer(db, "fixture", "release", upstream_commit=commit)
    build_equipment_layer(db, "release", upstream_commit=commit, manifest=stats.manifest)
    create_annotation_tables(db)
    build(db, cast_repo, upstream_commit=commit)
    assert import_bundle(db, fixture_catalog(db), dry_run=False)["applied"]
    assert import_bundle(db, fixture_qualified_catalog(db), dry_run=False)["applied"]
    assert import_bundle(db, fixture_cast_catalog(db, approve=True), dry_run=False)["applied"]
    return db
