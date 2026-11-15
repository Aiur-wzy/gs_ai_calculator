"""EC05: detach calculation and export from a single materialized DB snapshot."""
import copy
import csv
import io
import json
import sqlite3
import threading
import zipfile
from contextlib import closing

import pytest
from test_plan_mechanics import native, review

from genshin_app.cli import main
from genshin_app.equipment import service
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import new_plan
from genshin_db import equipment_queries
from genshin_db.display_names import get_display_catalog
from genshin_db.locking import database_lock


@pytest.fixture
def small_db(tmp_path):
    path = tmp_path / "genshin.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE character(id INTEGER,version_line TEXT,name TEXT);
            INSERT INTO character VALUES(10000002,'release','旧快照角色');
        """)
    return path


def manual():
    plan = new_plan()
    plan["manual_template"].update(
        characters=[{"id": "10000002", "level": 90, "stats": {"atk": 1000}}],
        hits=[{"id": "h", "damage_type_ids": ["damage.other"], "t": 1,
               "character": "10000002", "skill": "工程伤害", "damage_system": "classic",
               "element": "physical", "multiplier": 1}],
    )
    return plan


def test_calculation_closes_read_session_and_releases_writer_lock(small_db, monkeypatch):
    original = service.calculate

    def calculate(template):
        assert equipment_queries.borrow_read_connection(small_db) is None
        acquired, errors = [], []

        def contender():
            try:
                with database_lock(small_db, timeout=.2):
                    acquired.append(True)
            except Exception as exc:  # noqa: BLE001 - expose worker failure in parent
                errors.append(exc)

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and not errors and acquired == [True]
        return original(template)

    monkeypatch.setattr(service, "calculate", calculate)
    assert service.run_plan_calc(manual(), small_db)["result"]["summary"]["total_damage"] > 0


@pytest.mark.parametrize("version", [1, 3])
def test_post_compose_caller_mutation_cannot_change_receipt_or_execution(tmp_path, monkeypatch, version):
    db = tmp_path / "absent.db"
    plan = native() if version == 3 else manual()
    if version == 3:
        review(plan, db)
    expected = service.run_plan_calc(plan, db)
    original = service.compose_equipment_plan

    def compose(*args, **kwargs):
        report = original(*args, **kwargs)
        plan["revision"] += 1
        plan["plan_id"] = "changed-after-capture"
        plan["plan_version"] = 99
        if version == 3:
            plan["rotation_model"]["actions"][0]["repeat"] = {"mode": "once"}
        return report

    monkeypatch.setattr(service, "compose_equipment_plan", compose)
    actual = service.run_plan_calc(plan, db)
    assert actual["result"] == expected["result"]
    assert actual["plan"] == expected["plan"]
    for key in ("revision", "plan_id", "input_fingerprint", "result_fingerprint"):
        assert actual["result_receipt"][key] == expected["result_receipt"][key]
    assert not db.exists()


@pytest.mark.parametrize("version", [1, 3])
def test_engine_summary_and_serialization_have_no_late_db_connections(tmp_path, monkeypatch, version):
    db = tmp_path / "absent.db"
    plan = native() if version == 3 else manual()
    if version == 3:
        review(plan, db)
    original = service.compose_equipment_plan

    def forbidden(*args, **kwargs):
        pytest.fail("late SQLite connect after source materialization")

    def compose(*args, **kwargs):
        report = original(*args, **kwargs)
        monkeypatch.setattr(sqlite3, "connect", forbidden)
        monkeypatch.setattr(sqlite3.dbapi2, "connect", forbidden)
        return report

    monkeypatch.setattr(service, "compose_equipment_plan", compose)
    actual = service.run_plan_calc(plan, db)
    for fmt in ("json", "csv", "xlsx"):
        assert service.result_bytes(actual["result"], actual["result_receipt"], fmt,
                                    equipment_summary=actual["equipment_summary"])


def test_display_catalog_borrows_one_connection_without_closing_it(small_db, monkeypatch):
    expected = get_display_catalog(small_db)
    with equipment_queries.equipment_read_session(small_db):
        borrowed = equipment_queries.borrow_read_connection(small_db)

        def forbidden(*args, **kwargs):
            pytest.fail("display query opened another connection inside read session")

        monkeypatch.setattr(sqlite3, "connect", forbidden)
        assert get_display_catalog(small_db) == expected
        assert borrowed.execute("SELECT count(*) FROM character").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        borrowed.execute("SELECT 1")


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
@pytest.mark.parametrize("entry", ["service", "cli"])
def test_export_freezes_names_before_calculation_and_records_display_source(small_db, tmp_path, monkeypatch, capsys, fmt, entry):
    plan = manual()
    before = copy.deepcopy(plan)
    old_catalog = get_display_catalog(small_db, entity_types=["character"])
    original = service.calculate

    def calculate(template):
        assert equipment_queries.borrow_read_connection(small_db) is None
        with database_lock(small_db), closing(sqlite3.connect(small_db)) as conn, conn:
            conn.execute("UPDATE character SET name='新快照角色'")
        return original(template)

    monkeypatch.setattr(service, "calculate", calculate)
    path = tmp_path / f"frozen.{fmt}"
    if entry == "service":
        output = service.export_plan_result(plan, small_db, fmt, path)
    else:
        source = tmp_path / "plan.json"
        source.write_text(json.dumps(plan), encoding="utf-8")
        # This branch verifies the CLI export projection against the same
        # synthetic SQLite snapshot as the service branch. Release-pair
        # admission has dedicated tests and must not reinterpret this fixture
        # as an installed product database.
        monkeypatch.setattr(
            "genshin_app.cli._require_runtime_database", lambda _data_dir: {}
        )
        assert main(["--data-dir", str(tmp_path), "equipment", "export", "--plan", str(source),
                     "--format", fmt, "--output", str(path)]) == 0
        output = json.loads(capsys.readouterr().out)["data"]
    assert output["display_warnings"] == [] and plan == before
    receipt = output["result_receipt"]["display_source"]
    assert receipt == {"kind": "calculation_snapshot", "version_line": "release",
                       "catalog_fingerprint": fingerprint(old_catalog), "status": "ready"}
    if fmt == "csv":
        text = path.read_text(encoding="utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        assert "display_source_json" in rows[0]
    else:
        with zipfile.ZipFile(path) as archive:
            text = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist()
                             if name.endswith(".xml"))
    assert "旧快照角色" in text and "新快照角色" not in text
    assert get_display_catalog(small_db)["characters"][0]["name"] == "新快照角色"


def test_display_metadata_does_not_change_calculation_fingerprints(small_db):
    plan = manual()
    ordinary = service.run_plan_calc(plan, small_db)
    with_names = service.run_plan_calc(plan, small_db, capture_display=True)
    assert ordinary["result"] == with_names["result"]
    assert "display_context" not in ordinary and "display_source" not in ordinary["result_receipt"]
    for key in ("input_fingerprint", "output_fingerprint", "source_fingerprint", "result_fingerprint"):
        assert ordinary["result_receipt"][key] == with_names["result_receipt"][key]


def test_missing_database_export_uses_explicit_template_fallback_without_creation(tmp_path):
    db = tmp_path / "absent.db"
    exported = service.export_plan_result(manual(), db, "csv", tmp_path / "result.csv")
    assert exported["display_warnings"]
    assert exported["result_receipt"]["display_source"]["status"] == "template_fallback"
    assert not db.exists()
