"""Independent public CLI races/failures: existing files are user-owned inputs."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")
from test_runtime_action_facts import action_db, fake_repo, stub_fetcher  # noqa: F401
from test_runtime_batch_review import request

from genshin_app import runtime_batch_files
from genshin_app.cli import main
from genshin_app.equipment import files as plan_files
from genshin_app.equipment.schema import PlanError
from genshin_db import runtime_action_parameter
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime

# ruff: noqa: F811 - pytest resolves imported fixture arguments.


def invoke(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    body = json.loads(captured.err if code else captured.out)
    return code, body


def paths_and_args(db):
    input_path, output_dir = db.parent / "batch-request.json", db.parent / "facts"
    input_path.write_text(json.dumps(request(0)), "utf8")
    return input_path, output_dir, ["--data-dir", str(db.parent), "db", "runtime", "--input", str(input_path)]


def test_review_cli_captures_index_before_query_and_preserves_external_edit(action_db, monkeypatch, capsys):
    _, output, args = paths_and_args(action_db)
    args += ["--output-dir", str(output), "--summary"]
    assert invoke(args, capsys)[0] == 0
    previous = {p: p.read_bytes() for p in output.rglob("item-*.json")}
    index = output / "index.json"
    user_edit = b'{"user":"edit during database query"}'
    original = runtime_action_parameter.get_runtime_action_parameter
    calls = []

    def intervene(*pos, **kw):
        calls.append(True)
        index.write_bytes(user_edit)
        return original(*pos, **kw)

    monkeypatch.setattr(runtime_action_parameter, "get_runtime_action_parameter", intervene)
    code, error = invoke([*args, "--overwrite"], capsys)
    assert calls == [True]
    assert code == 1 and error["reason"] == "file_conflict"
    assert index.read_bytes() == user_edit
    assert {p: p.read_bytes() for p in previous} == previous
    assert list(output.rglob("item-*.json")) == list(previous), "CAS conflict must precede creating a new generation"


def test_review_cli_actual_last_replace_failure_never_commits_new_index(action_db, monkeypatch, capsys):
    _, output, args = paths_and_args(action_db)
    args += ["--output-dir", str(output), "--summary"]
    assert invoke(args, capsys)[0] == 0
    index = output / "index.json"
    baseline = index.read_bytes()
    old_files = {p: p.read_bytes() for p in output.rglob("item-*.json")}
    original = plan_files.os.replace

    def fail_index(source, target):
        if Path(target) == index:
            raise OSError("independent final index replace failure")
        return original(source, target)

    monkeypatch.setattr(plan_files.os, "replace", fail_index)
    code, error = invoke([*args, "--overwrite"], capsys)
    assert code == 1 and error["reason"] == "file_conflict"
    assert index.read_bytes() == baseline
    assert {p: p.read_bytes() for p in old_files} == old_files
    assert error["details"]["index_committed"] is False
    published = [Path(p) for p in error["details"]["published_paths"]]
    assert published and index not in published
    assert all(p.is_file() and p not in old_files for p in published)


@pytest.mark.parametrize("source_kind", ["database", "request"])
def test_review_batch_single_output_protects_hardlinks_without_summary(action_db, monkeypatch, capsys, source_kind):
    input_path, _, args = paths_and_args(action_db)
    protected = action_db if source_kind == "database" else input_path
    before = hashlib.sha256(protected.read_bytes()).hexdigest()
    alias = action_db.parent / "user-output.json"
    try:
        os.link(protected, alias)
    except OSError as exc:
        pytest.skip(f"当前测试卷或ACL不允许创建硬链接：{exc}")
    called = []

    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError("protected output must fail before reading runtime data")

    monkeypatch.setattr(runtime_action_parameter, "get_runtime_action_parameter", forbidden)
    code, error = invoke([*args, "--output", str(alias), "--overwrite"], capsys)
    assert code == 1 and error["reason"] == "file_conflict"
    assert called == []
    assert hashlib.sha256(protected.read_bytes()).hexdigest() == before
    assert alias.samefile(protected)


@pytest.mark.parametrize("fatal", [False, True])
def test_review_cli_partial_publishes_all_receipts_but_fatal_never_publishes_index(action_db, monkeypatch, capsys, fatal):
    input_path, output, args = paths_and_args(action_db)
    query = request(0, 0)
    query["items"][1]["request"]["slot"] = "unknown-slot"
    input_path.write_text(json.dumps(query), "utf8")
    if fatal:
        def corrupt(*args, **kwargs):
            raise RuntimeConfigError("independent source corruption", "dataset_invalid", 500)
        monkeypatch.setattr(runtime_action_parameter, "get_runtime_action_parameter", corrupt)
    code, error = invoke([*args, "--output-dir", str(output), "--summary"], capsys)
    assert code == 1
    if fatal:
        assert error["reason"] == "dataset_invalid"
        assert not output.exists()
    else:
        assert error["reason"] == "batch_partial"
        index = json.loads((output / "index.json").read_text("utf8"))
        assert [row["id"] for row in index["items"]] == ["item-0", "item-1"]
        assert [row["status"] for row in index["items"]] == ["ok", "error"]
        for row in index["items"]:
            body = (output / row["path"]).read_bytes()
            assert hashlib.sha256(body).hexdigest() == row["file_sha"]
            assert json.loads(body)["status"] == row["status"]


def test_review_post_commit_exception_reports_the_observed_committed_index(action_db, monkeypatch):
    data = query_runtime(action_db, request(0))
    output = action_db.parent / "post-commit-facts"
    state = runtime_batch_files.prepare_batch_directory(output)
    original = runtime_batch_files.publish_files

    def committed_then_failed(paths, **kwargs):
        result = original(paths, **kwargs)
        if any(Path(p).name == "index.json" for p, _ in paths):
            raise OSError("response/cleanup failed after actual successful replacement")
        return result

    monkeypatch.setattr(runtime_batch_files, "publish_files", committed_then_failed)
    with pytest.raises(PlanError) as caught:
        runtime_batch_files.publish_batch_directory(state, data)
    index = json.loads((output / "index.json").read_text("utf8"))
    assert index["content_fingerprint"] == data["content_fingerprint"]
    assert caught.value.details["index_committed"] is True, "an error is not proof that an atomic replacement did not happen"
    assert "observed_index_state" in caught.value.details
    for row in index["items"]:
        assert hashlib.sha256((output / row["path"]).read_bytes()).hexdigest() == row["file_sha"]


def test_review_real_process_index_lock_failure_keeps_old_index_and_recovers(action_db, monkeypatch):
    data = query_runtime(action_db, request(0))
    output = action_db.parent / "locked-index-facts"
    runtime_batch_files.publish_batch_directory(runtime_batch_files.prepare_batch_directory(output), data)
    index, marker = output / "index.json", action_db.parent / "index-lock-held"
    old = index.read_bytes()
    original_lock = plan_files.file_lock
    monkeypatch.setattr(plan_files, "file_lock", lambda path: original_lock(path, timeout=0))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    code = ("import sys\nfrom pathlib import Path\nfrom genshin_db.locking import file_lock\n"
            "with file_lock(Path(sys.argv[1])):\n Path(sys.argv[2]).write_text('held');sys.stdin.read()")
    child = subprocess.Popen([sys.executable, "-c", code, str(output / ".index.json.plan.lock"), str(marker)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        until = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < until:
            assert child.poll() is None
            time.sleep(.01)
        assert marker.exists(), "independent lock owner failed to start"
        state = runtime_batch_files.prepare_batch_directory(output, overwrite=True)
        with pytest.raises(PlanError) as caught:
            runtime_batch_files.publish_batch_directory(state, data)
        assert index.read_bytes() == old
        assert caught.value.details["index_committed"] is False
        assert caught.value.details["published_paths"], "new orphan item files must be acknowledged"
    finally:
        try:
            child.communicate(input=b"", timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=3)
    recovered = runtime_batch_files.publish_batch_directory(
        runtime_batch_files.prepare_batch_directory(output, overwrite=True), data)
    assert recovered["path"] == str(index)
    assert index.read_bytes() != old
