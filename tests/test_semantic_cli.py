"""Real CLI dispatch and temporary B-built databases, never the project database."""

import json
import sqlite3
from contextlib import closing

import pytest

from genshin_app.cli import main
from genshin_db import semantic_store as store
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_semantic_store import bundle, fact, head

# ruff: noqa: F811


@pytest.fixture
def cli_database(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    return tmp_path


def cli(folder, capsys, *args):
    code = main(["--data-dir", str(folder), *args])
    streams = capsys.readouterr()
    return code, json.loads(streams.err if code else streams.out)


def request(folder, name, data):
    path = folder / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_capabilities_describe_all_semantic_operations_and_defaults(tmp_path, capsys):
    code, response = cli(tmp_path, capsys, "capabilities")
    assert code == 0, response
    commands = {command["name"]: command for command in response["data"]["commands"]}
    assert {"db semantics import", "db semantics record", "db semantics review"} <= commands.keys()
    args = commands["db semantics import"]["args"]
    assert next(arg for arg in args if arg["name"] == "--apply")["default"] is False
    assert commands["db semantics import"]["lifecycle"] == "deprecated"
    assert commands["db semantics import"]["available"] is False
    assert commands["db semantics review"]["lifecycle"] == "deprecated"
    assert commands["db semantics record"].get("available", True) is True


def test_cli_import_is_deprecated_for_dry_run_and_apply(cli_database, capsys):
    folder = cli_database
    path = request(folder, "bundle.json", bundle())
    db = folder / "genshin.db"; before = db.read_bytes()
    for extra in ((), ("--apply",)):
        code, response = cli(
            folder, capsys, "db", "semantics", "import", "--input", path, *extra
        )
        assert code == 1
        assert response["reason"] == "accepted_database_is_immutable"
        assert response["details"]["active_database_modified"] is False
        assert db.read_bytes() == before


def test_cli_record_remains_read_only_for_candidate_fixture(cli_database, capsys):
    folder = cli_database
    db = folder / "genshin.db"
    store.import_bundle(db, bundle(), dry_run=False)
    query = request(folder, "record.json", {"record_kind": "fact", "record_id": "one"})
    code, response = cli(folder, capsys, "db", "semantics", "record", "--input", query)
    record = response["data"]
    assert code == 0 and record["revision"] == 1 and record["review"]["status"] == "approved"
    assert len(record["review_fingerprint"]) == 64 and len(record["payload_fingerprint"]) == 64


def test_cli_review_is_deprecated_and_does_not_change_existing_record(cli_database, capsys):
    folder = cli_database; db = folder / "genshin.db"
    store.import_bundle(db, bundle(), dry_run=False)
    query = request(folder, "record.json", {"record_kind": "fact", "record_id": "one", "revision": 1})
    code, record = cli(folder, capsys, "db", "semantics", "record", "--input", query)
    assert code == 0
    update = {"record_kind": "fact", "record_id": "one", "revision": 1, "status": "revoked",
              "reviewer": "cli-reviewer", "reviewed_at": "2026-10-03",
              "expected_review_fingerprint": record["data"]["review_fingerprint"]}
    path = request(folder, "review.json", update)
    before = db.read_bytes()
    code, result = cli(folder, capsys, "db", "semantics", "review", "--input", path)
    assert code == 1
    assert result["reason"] == "accepted_database_is_immutable"
    assert db.read_bytes() == before
    with closing(sqlite3.connect(db)) as conn:
        assert store.read_record(conn, "release", "fact", "one")["review"]["status"] == "approved"


def test_cli_import_deprecation_precedes_bundle_conflict_processing(cli_database, capsys):
    folder = cli_database; db = folder / "genshin.db"; before = db.read_bytes()
    modified = fact(); modified["payload"]["claim"]["requirement"] = "fixed_party"
    path = request(folder, "conflict.json", bundle([modified, fact("two")], [head("two")]))
    code, response = cli(folder, capsys, "db", "semantics", "import", "--input", path, "--apply")
    assert code == 1 and response["reason"] == "accepted_database_is_immutable"
    assert db.read_bytes() == before


def test_cli_semantic_input_and_revision_errors_do_not_write(cli_database, capsys):
    folder = cli_database; db = folder / "genshin.db"; before = db.read_bytes()
    code, response = cli(folder, capsys, "db", "semantics", "import")
    assert code == 1 and response["reason"] == "accepted_database_is_immutable"
    path = request(folder, "invalid.json", {"record_kind": "fact", "record_id": "one", "revision": True})
    code, response = cli(folder, capsys, "db", "semantics", "record", "--input", path)
    assert code == 1 and response["reason"] == "semantic_record_request_invalid"
    assert "revision" in response["message"]
    assert db.read_bytes() == before
