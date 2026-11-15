"""Independent EC05b adversarial budgets, source errors and real process locking."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_runtime_action_facts import action_db, fake_repo, stub_fetcher  # noqa: F401

from genshin_db import runtime_action_parameter, runtime_queries
from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint
from tests import fixture_candidate_builder as fixture_builder

# ruff: noqa: F811 - pytest resolves imported fixture arguments.


def request(*indices, **extra):
    return {"action": "batch", "items": [{"id": f"item-{position}", "request": {
        "action": "action_parameter", "character_id": 90000001, "slot": "normal", "param_index": index}}
        for position, index in enumerate(indices)], **extra}


def one(db):
    return runtime_queries.query_runtime(db, request(0)["items"][0]["request"])


def test_review_budget_preserves_semantic_pending_and_accounts_for_all_tail_rows(action_db, monkeypatch):
    small = one(action_db)
    small.update(status="pending", action_dispatch={"status": "pending", "reasons": ["unverified"]})
    calls = []

    def controlled(_db, *, param_index, **_):
        calls.append(param_index)
        result = copy.deepcopy(small)
        if param_index == 1:
            result["untrimmed_source"] = "中文" * 20000
        return result

    monkeypatch.setattr(runtime_action_parameter, "get_runtime_action_parameter", controlled)
    source = request(0, 1, 2, max_bytes=65536)
    before = copy.deepcopy(source)
    result = runtime_queries.query_runtime(action_db, source)
    assert source == before
    assert result["status"] == "partial"
    assert [row["id"] for row in result["items"]] == ["item-0", "item-1", "item-2"]
    assert [row["status"] for row in result["items"]] == ["ok", "error", "skipped"]
    assert result["items"][0]["data"] == small, "do not trim or upgrade a successful pending source"
    assert calls == [0, 1], "skipped means no query was executed"
    assert len(dumps(result).encode("utf8")) <= 65536
    assert result["counts"] == {"requested": 3, "ok": 1, "error": 1, "skipped": 1}


def test_review_giant_error_details_have_explicit_omission_and_do_not_hide_later_success(action_db, monkeypatch):
    complete = one(action_db)
    details = {"raw_diagnostic": "界" * 40000}

    def controlled(_db, *, param_index, **_):
        if param_index == 0:
            raise RuntimeConfigError("No such configured node", "not_found", 404, details)
        return copy.deepcopy(complete)

    monkeypatch.setattr(runtime_action_parameter, "get_runtime_action_parameter", controlled)
    result = runtime_queries.query_runtime(action_db, request(0, 1, max_bytes=65536))
    assert [row["status"] for row in result["items"]] == ["error", "ok"]
    error = result["items"][0]["error"]
    assert error["code"] == 404
    assert error["details_omitted"] is True
    assert error["details_fingerprint"] == fingerprint(details)
    assert result["items"][1]["data"] == complete
    assert len(dumps(result).encode("utf8")) <= 65536


@pytest.mark.parametrize("reason,code", [("mixed_snapshot", 500), ("dataset_invalid", 500), ("snapshot_changed", 409)])
def test_review_source_integrity_errors_abort_batch_instead_of_partial(action_db, monkeypatch, reason, code):
    calls = []

    def controlled(_db, *, param_index, **_):
        calls.append(param_index)
        raise RuntimeConfigError("Untrusted source identity", reason, code)

    monkeypatch.setattr(runtime_action_parameter, "get_runtime_action_parameter", controlled)
    with pytest.raises(RuntimeConfigError) as caught:
        runtime_queries.query_runtime(action_db, request(0, 1))
    assert caught.value.reason == reason
    assert calls == [0]


def test_review_all_items_hold_one_snapshot_until_independent_publisher_can_replace(
        action_db, fake_repo, stub_fetcher, monkeypatch):
    old = one(action_db)
    proud_path = fake_repo / "ExcelBinOutput/ProudSkillExcelConfigData.json"
    proud = json.loads(proud_path.read_text("utf8"))
    next(row for row in proud if row["level"] == 10)["paramList"][0] = 1
    proud_path.write_text(json.dumps(proud), "utf8")
    stub_fetcher["commit"] = "b" * 40
    replacement_dir = action_db.parent / "batch-replacement"
    fixture_builder.build_database(replacement_dir, force=True)
    replacement = replacement_dir / "genshin.db"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    code = ("import os,sys\nfrom pathlib import Path\nfrom genshin_db.locking import database_lock,LockBusyError\n"
            "try:\n with database_lock(Path(sys.argv[1]),timeout=0):\n"
            "  os.replace(sys.argv[2],sys.argv[1]); print('published')\n"
            "except LockBusyError: print('busy')")

    def publisher():
        child = subprocess.run([sys.executable, "-c", code, str(action_db), str(replacement)],
            capture_output=True, text=True, encoding="utf8", env=env, timeout=5, check=False)
        assert child.returncode == 0, child.stderr
        return child.stdout.strip()

    original = runtime_action_parameter.get_runtime_action_parameter
    probes, validations = [], []
    original_validate = runtime_queries.validate_runtime

    def counted_validation(*args, **kwargs):
        validations.append(True)
        return original_validate(*args, **kwargs)

    def controlled(*args, **kwargs):
        probes.append(publisher())
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_queries, "validate_runtime", counted_validation)
    monkeypatch.setattr(runtime_action_parameter, "get_runtime_action_parameter", controlled)
    result = runtime_queries.query_runtime(action_db, request(0, 0, snapshot_id=old["snapshot"]["snapshot_id"]))
    assert probes == ["busy", "busy"]
    assert len(validations) == 1
    assert [row["data"] for row in result["items"]] == [old, old]
    assert publisher() == "published", "no live connection may escape the read phase"
    monkeypatch.setattr(runtime_action_parameter, "get_runtime_action_parameter", original)
    with pytest.raises(RuntimeConfigError) as caught:
        runtime_queries.query_runtime(action_db, request(0, snapshot_id=old["snapshot"]["snapshot_id"]))
    assert caught.value.reason == "snapshot_changed"
    current = runtime_queries.query_runtime(action_db, request(0))
    assert current["items"][0]["data"]["scalar_projection"]["value"] == 1
    assert current["snapshot"]["snapshot_id"] != old["snapshot"]["snapshot_id"]
