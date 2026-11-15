"""Independent EC05 cross-process publication while real source calculation is paused."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest
from test_hit_parameters_app import native_source
from test_runtime_action_facts import action_db, fake_repo, stub_fetcher  # noqa: F401

from genshin_app.equipment import service
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import database_lock
from genshin_engine import mechanics
from tests import fixture_candidate_builder as fixture_builder

# Imported fixtures intentionally share the existing small source builder, not an oracle.
# ruff: noqa: F811


def source_plan(db):
    # The real DB identity also resolves the manual actor's display name.
    return json.loads(json.dumps(native_source(db)).replace('"a"', '"90000001"'))


def replacement_db(action_db, fake_repo, stub_fetcher):
    proud_path = fake_repo / "ExcelBinOutput/ProudSkillExcelConfigData.json"
    proud = json.loads(proud_path.read_text("utf8"))
    next(row for row in proud if row["level"] == 10)["paramList"][0] = 1.0
    proud_path.write_text(json.dumps(proud), "utf8")
    text_path = fake_repo / "TextMap/TextMap_MediumCHS.json"
    texts = json.loads(text_path.read_text("utf8"))
    texts["100"] = "后来角色"
    text_path.write_text(json.dumps(texts, ensure_ascii=False), "utf8")
    stub_fetcher["commit"] = "b" * 40
    destination = action_db.parent / "prepared-b"
    fixture_builder.build_database(destination, force=True)
    return destination / "genshin.db"


@pytest.mark.parametrize("operation", ["run", "csv", "xlsx"])
def test_review_real_sources_survive_cross_process_replace_after_read_phase(
        action_db, fake_repo, stub_fetcher, monkeypatch, operation):
    plan = source_plan(action_db)
    snapshot_a = plan["hit_parameter_sources"][0]["expected_snapshot_id"]
    replacement = replacement_db(action_db, fake_repo, stub_fetcher)
    reached, resume = threading.Event(), threading.Event()
    observed, errors, lock_acquired = {}, [], []
    original_execute = mechanics.execute_mechanics
    original_connect = sqlite3.connect
    output_path = action_db.parent / f"independent-export.{operation}"

    def delayed_execute(*args, **kwargs):
        reached.set()
        assert resume.wait(8), "publisher did not complete"
        result = original_execute(*args, **kwargs)
        observed["engine"] = result
        return result

    def no_late_connection(*args, **kwargs):
        if threading.current_thread() is calculator and reached.is_set():
            raise AssertionError("calculation/export tried to reopen DB after materialization")
        return original_connect(*args, **kwargs)

    def calculate():
        try:
            observed["output"] = (service.run_plan_calc(plan, action_db, capture_display=True)
                if operation == "run" else service.export_plan_result(plan, action_db, operation, output_path))
        except BaseException as exc:  # noqa: BLE001 - return worker failures to the asserting thread
            errors.append(exc)

    calculator = threading.Thread(target=calculate, daemon=True)
    monkeypatch.setattr(mechanics, "execute_mechanics", delayed_execute)
    monkeypatch.setattr(sqlite3, "connect", no_late_connection)
    calculator.start()
    try:
        assert reached.wait(5), errors

        def thread_contender():
            try:
                with database_lock(action_db, timeout=0):
                    lock_acquired.append(True)
            except BaseException as exc:  # noqa: BLE001 - return contender failures to the asserting thread
                errors.append(exc)

        contender = threading.Thread(target=thread_contender)
        contender.start()
        contender.join(2)
        assert not contender.is_alive() and lock_acquired == [True], errors
        # An independent interpreter must acquire the OS lock and perform an actual
        # Windows file replacement; a mocked publish cannot detect leaked handles.
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        code = ("import os,sys\nfrom pathlib import Path\nfrom genshin_db.locking import database_lock\n"
                "with database_lock(Path(sys.argv[1]), timeout=.5):\n os.replace(sys.argv[2],sys.argv[1])\n"
                "print('published')")
        child = subprocess.run([sys.executable, "-c", code, str(action_db), str(replacement)],
            capture_output=True, text=True, encoding="utf8", env=env, timeout=5, check=False)
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "published"
        with contextlib.closing(original_connect(action_db)) as conn:
            assert conn.execute("SELECT name FROM character WHERE id=90000001").fetchone()[0] == "后来角色"
    finally:
        resume.set()
        calculator.join(8)
    assert not calculator.is_alive() and not errors, errors
    assert observed["engine"]["result"]["hits"][0]["expected"] == pytest.approx(22.5)
    output = observed["output"]
    receipt = output["result_receipt"]
    assert receipt["hit_parameter_sources"][0]["snapshot"]["snapshot_id"] == snapshot_a
    assert output["plan"]["resolved_template"]["hits"][0]["multiplier"] == .5
    if operation == "run":
        assert output["display_context"]["catalog"]["characters"][0]["display_name"] == "甲"
    elif operation == "csv":
        exported = output_path.read_text("utf-8-sig")
        assert "甲" in exported and "后来角色" not in exported
        assert "22.5" in exported
    else:
        with zipfile.ZipFile(output_path) as archive:
            exported = "\n".join(archive.read(path).decode("utf8") for path in archive.namelist() if path.endswith(".xml"))
        assert "甲" in exported and "后来角色" not in exported
        assert ">22.5<" in exported
    # Already confirmed A cannot silently become B; a new explicit selection does.
    with pytest.raises(PlanError):
        service.run_plan_calc(plan, action_db)
    monkeypatch.setattr(mechanics, "execute_mechanics", original_execute)
    current = service.run_plan_calc(source_plan(action_db), action_db, capture_display=True)
    assert current["result"]["hits"][0]["expected"] == pytest.approx(45)
    assert current["display_context"]["catalog"]["characters"][0]["display_name"] == "后来角色"
    assert current["result_receipt"]["hit_parameter_sources"][0]["snapshot"]["snapshot_id"] != snapshot_a
