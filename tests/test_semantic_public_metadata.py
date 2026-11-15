"""Public metadata/CLI checks using only small isolated, persisted databases."""

import json
import sqlite3
from contextlib import closing

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")

from genshin_app.cli import main
from genshin_db import pipeline
from genshin_db import semantic_store as store
from genshin_db.queries import get_meta
from tests import fixture_candidate_builder as fixture_builder
from tests.mechanism_source_fixtures import mechanism_db, mechanism_repo  # noqa: F401
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_semantic_store import descriptor_bundle

# ruff: noqa: F811


def invoke(folder, capsys, *args):
    code = main(["--data-dir", str(folder), *args])
    streams = capsys.readouterr()
    assert code == 0, streams.err
    assert not streams.err
    return json.loads(streams.out)["data"]


def runtime_summary(db, tmp_path, capsys, request):
    live = tmp_path / "genshin.db"
    db.rename(live)
    query = tmp_path / "request.json"
    output = tmp_path / "complete.json"
    query.write_text(json.dumps(request), encoding="utf-8")
    before = live.read_bytes()
    summary = invoke(tmp_path, capsys, "db", "runtime", "--input", str(query),
                     "--output", str(output), "--summary")
    assert live.read_bytes() == before
    return summary, json.loads(output.read_text(encoding="utf-8"))


def test_legacy_metadata_and_status_never_install_missing_component(tmp_path, stub_fetcher, capsys):
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        for table in (*store.TABLES.values(), store.HEAD_TABLE):
            conn.execute(f'DROP TABLE "{table}"')
        conn.execute("DELETE FROM data_component WHERE component=?", (store.COMPONENT,))
    before = db.read_bytes()
    datasets = [get_meta(db)["datasets"], pipeline._datasets(db),
                invoke(tmp_path, capsys, "db", "status")["datasets"]]
    for dataset in datasets:
        component = dataset[store.COMPONENT]
        assert component["schema_version"] == 1
        assert all(row["state"] == "missing" and row["execution_support"] == "not_evaluated"
                   for row in component["lines"].values())
    assert db.read_bytes() == before
    update = fixture_builder.build_database(tmp_path)
    assert update.action == "unchanged" and not update.updated
    assert update.datasets[store.COMPONENT]["lines"]["release"]["state"] == "missing"
    with closing(sqlite3.connect(db)) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (store.HEAD_TABLE,)).fetchone()


def test_public_metadata_counts_real_records_without_claiming_execution(tmp_path, stub_fetcher, capsys):
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    store.import_bundle(db, descriptor_bundle(), dry_run=False)
    before = db.read_bytes()
    expected = {"facts": 1, "descriptors": 1, "heads": 2}
    for dataset in (get_meta(db)["datasets"], pipeline._datasets(db),
                    invoke(tmp_path, capsys, "db", "status")["datasets"]):
        states = dataset[store.COMPONENT]["lines"]
        assert states["release"]["state"] == "ready" and states["release"]["counts"] == expected
        assert states["release"]["execution_support"] == "not_evaluated"
        assert states["beta"]["state"] == "missing"
    assert db.read_bytes() == before


@pytest.mark.parametrize("revoked", [False, True])
def test_runtime_summary_distinguishes_ready_templates_from_axis_execution(mechanism_db, tmp_path, capsys, revoked):
    ident = "skill.current-atk-limited-addition"
    if revoked:
        with closing(sqlite3.connect(mechanism_db)) as conn:
            record = store.read_record(conn, "release", "descriptor", ident)
        store.review_record(mechanism_db, version_line="release", record_kind="descriptor", record_id=ident,
                            revision=1, status="revoked", reviewer="public-test", reviewed_at="2026-10-03",
                            expected_review_fingerprint=record["review_fingerprint"])
    summary, full = runtime_summary(mechanism_db, tmp_path, capsys,
                                    {"action": "mechanism_profile", "mechanism_id": ident})
    assert summary["status"] == ("pending" if revoked else "ready")
    assert summary["source_status"] == ("pending" if revoked else "ready")
    assert summary["review_status"] == ("pending" if revoked else "approved")
    assert summary["materialization_status"] == summary["status"]
    assert summary["execution_support"] == "not_evaluated"
    assert summary["templates"]["rule_count"] == (0 if revoked else 1)
    assert summary["templates"]["parameter_count"] == len(full["resolved_parameters"])
    if revoked:
        assert full["compiler_result"] is None and full["source_proof"] is None and full["descriptor"] is None
        for name in ("semantic_fingerprint", "authorization_fingerprint", "record_payload_fingerprint"):
            assert name not in summary and name not in full
        assert "execution_status" not in summary["templates"]
        assert "verification_scope" not in summary["templates"]
        assert summary["selection"] == {"mechanism_id": ident, "revision": None}
        assert full["issues"][0]["record_id"] == ident
        assert full["issues"][0]["current_revision"] == 1
        assert full["issues"][0]["review_status"] == "revoked"
    else:
        assert summary["templates"]["execution_status"] == "not_evaluated"
        assert summary["templates"]["verification_scope"] == "materialized_consistency_only"
        for name in ("semantic_fingerprint", "authorization_fingerprint", "record_payload_fingerprint"):
            assert summary[name] == full[name]
            assert len(summary[name]) == 64
        assert summary["selection"] == {"mechanism_id": ident, "revision": 1}
        assert full["source_proof"]["nodes"] and full["descriptor"]
    assert not {"descriptor", "facts", "source_proof", "compiler_result", "rule_templates"}.intersection(summary)


def test_catalog_summary_keeps_review_identity_without_raw_nodes(mechanism_db, tmp_path, capsys):
    summary, full = runtime_summary(mechanism_db, tmp_path, capsys, {"action": "mechanism_catalog"})
    assert summary["execution_support"] == "not_evaluated" and summary["total"] == 2
    assert summary["component"] == store.COMPONENT
    assert summary["items"] == full["items"]
    assert all(row["review"]["status"] == "approved" and len(row["review_fingerprint"]) == 64
               for row in summary["items"])
    assert "source_proof" not in summary and "rule_templates" not in summary
