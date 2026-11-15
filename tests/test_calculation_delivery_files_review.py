"""Independent EC15 commit discovery and failure injection; no formal DB.

The file layer intentionally receives tiny opaque artifact bytes: it proves
publication, not the upstream builder's game calculations or XLSX validity.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_app import calculation_delivery_files as delivery_files
from genshin_app.equipment import files
from genshin_app.equipment.schema import PlanError


def sha(content):
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def review_publication(tmp_path):
    source = tmp_path / "input.json"
    source.write_bytes(b'{"captured":"input"}')
    out = tmp_path / "delivery"
    built = {
        "artifacts": {"native_plan": b"native bytes", "calculated": b"calculated bytes",
                      "result_workbook": b"result bytes", "formula_workbook": b"formula bytes"},
        "metadata": {"package_schema": "genshin.calculated-package.v1", "input_file_sha": sha(source.read_bytes()),
                     "calculation_status": "complete", **{key: sha(key.encode()) for key in (
                         "native_input_fingerprint", "input_fingerprint", "output_fingerprint",
                         "source_fingerprint", "result_fingerprint", "calculated_fingerprint")}},
        "summary": {"reported_hits": 1}, "timings": {"fresh_calc": 0.1}, "diagnostic": False,
    }
    built["metadata"]["native_input_fingerprint"] = built["metadata"]["input_fingerprint"]
    return source, out, built


def prepare(source, out, *, overwrite=False, protected=()):
    return delivery_files.prepare_delivery_directory(out, overwrite=overwrite, protected_paths=[source, *protected])


def publish(state, source, built):
    return delivery_files.publish_calculation_delivery(
        state, built, input_path=source, input_file_sha=built["metadata"]["input_file_sha"],
    )


def verify_generation(out):
    """Independent consumer: follow only index and check all committed contents."""
    index_bytes = (out / "index.json").read_bytes()
    index = json.loads(index_bytes)
    assert index["delivery_schema"] == "genshin.calculation-delivery.v1"
    manifest_path = out / index["manifest"]["path"]
    manifest_bytes = manifest_path.read_bytes()
    assert len(manifest_bytes) == index["manifest"]["bytes"]
    assert sha(manifest_bytes) == index["manifest"]["sha256"]
    manifest = json.loads(manifest_bytes)
    roles = {"native_plan", "calculated", "result_workbook", "formula_workbook"}
    assert {entry["role"] for entry in index["artifacts"]} == roles
    assert len(index["artifacts"]) == len(manifest["artifacts"]) == 4
    total = len(index_bytes) + len(manifest_bytes)
    for row in index["artifacts"]:
        path = out / row["path"]
        assert path.parent == manifest_path.parent
        content = path.read_bytes()
        assert len(content) == row["bytes"] and sha(content) == row["sha256"]
        mirror = next(item for item in manifest["artifacts"] if item["role"] == row["role"])
        assert mirror == {**row, "path": path.name}
        total += len(content)
    assert manifest["total_bytes"] == total
    return index_bytes, manifest


def test_review_two_prepared_writers_cannot_claim_each_others_generation(review_publication):
    source, out, built = review_publication
    first, second = prepare(source, out), prepare(source, out)
    winner = publish(first, source, built)
    before, _ = verify_generation(out)
    with pytest.raises(PlanError) as caught:
        publish(second, source, built)
    assert caught.value.details["index_committed"] is not True
    assert (out / "index.json").read_bytes() == before
    assert winner["data"]["index_sha256"] == sha(before)
    assert not second.generation_path.exists()


def test_review_foreign_index_is_not_overwrite_authorization(review_publication):
    source, out, _ = review_publication
    out.mkdir()
    foreign = b'{"owner":"another application"}'
    (out / "index.json").write_bytes(foreign)
    with pytest.raises(PlanError):
        prepare(source, out, overwrite=True)
    assert (out / "index.json").read_bytes() == foreign
    assert list(out.iterdir()) == [out / "index.json"]


def test_review_third_replace_failure_keeps_previous_complete_index(review_publication, monkeypatch):
    source, out, built = review_publication
    publish(prepare(source, out), source, built)
    old_index, _ = verify_generation(out)
    state = prepare(source, out, overwrite=True)
    original = files.os.replace

    def fail_third(src, dst):
        if Path(dst) == state.generation_path / "result.xlsx":
            raise OSError("independent third-artifact failure")
        return original(src, dst)

    monkeypatch.setattr(files.os, "replace", fail_third)
    with pytest.raises(PlanError) as caught:
        publish(state, source, built)
    assert caught.value.details["index_committed"] is False
    assert set(caught.value.details["published_paths"]) == {
        str(state.generation_path / "native.plan.json"), str(state.generation_path / "calculated.json"),
    }
    assert verify_generation(out)[0] == old_index
    assert not (out / "index.json.previous").exists()


@pytest.mark.parametrize("change", ["input", "artifact", "manifest"])
def test_review_change_after_generation_write_never_commits_index(review_publication, monkeypatch, change):
    source, out, built = review_publication
    publish(prepare(source, out), source, built)
    old_index, _ = verify_generation(out)
    state = prepare(source, out, overwrite=True)
    original = delivery_files.publish_files

    def mutate_after_artifacts(outputs, **kwargs):
        report = original(outputs, **kwargs)
        if len(outputs) == 5:
            target = source if change == "input" else state.generation_path / (
                "manifest.json" if change == "manifest" else "formula.xlsx")
            target.write_bytes(b"external change")
        return report

    monkeypatch.setattr(delivery_files, "publish_files", mutate_after_artifacts)
    with pytest.raises(PlanError) as caught:
        publish(state, source, built)
    assert caught.value.details["index_committed"] is False
    assert verify_generation(out)[0] == old_index


@pytest.mark.parametrize("after", ["raise_only", "external_rewrite"])
def test_review_exception_after_index_replace_reports_observed_commit(review_publication, monkeypatch, after):
    source, out, built = review_publication
    state = prepare(source, out)
    original = delivery_files.publish_files
    external = b'{"outside":"modified after successful replacement"}'

    def fail_after_replace(outputs, **kwargs):
        result = original(outputs, **kwargs)
        if len(outputs) == 1 and Path(outputs[0][0]) == state.index_path:
            if after == "external_rewrite":
                state.index_path.write_bytes(external)
            raise OSError("receipt read failed after replacement")
        return result

    monkeypatch.setattr(delivery_files, "publish_files", fail_after_replace)
    with pytest.raises(PlanError) as caught:
        publish(state, source, built)
    if after == "raise_only":
        assert caught.value.details["index_committed"] is True
        assert caught.value.details["observed_index_state"] == "new_generation"
        verify_generation(out)
    else:
        assert caught.value.details["index_committed"] is None
        assert caught.value.details["observed_index_state"] == "externally_changed"
        assert state.index_path.read_bytes() == external


def test_review_summary_limit_precedes_any_generation_publication(review_publication, monkeypatch):
    source, out, built = review_publication
    state = prepare(source, out)
    monkeypatch.setitem(delivery_files.LIMITS, "summary_bytes", 128)
    with pytest.raises(PlanError) as caught:
        publish(state, source, built)
    assert caught.value.details["phase"] == "summary"
    assert caught.value.details["index_committed"] is False
    assert not out.exists()


@pytest.mark.parametrize("extra", [0, 1])
def test_review_artifact_limit_exact_boundary_is_byte_not_character(review_publication, monkeypatch, extra):
    source, out, built = review_publication
    content = "一份独立字节边界".encode()
    built["artifacts"]["formula_workbook"] = content + b"x" * extra
    built["artifacts"]["result_workbook"] = b"small"
    monkeypatch.setitem(delivery_files.LIMITS, "workbook_bytes", len(content))
    state = prepare(source, out)
    if extra:
        with pytest.raises(PlanError):
            publish(state, source, built)
        assert not out.exists()
    else:
        result = publish(state, source, built)
        assert result["data"]["index_committed"] is True
        verify_generation(out)


@pytest.mark.parametrize("name", ["index.json.previous", ".index.json.plan.lock"])
def test_review_new_hardlink_to_input_is_rechecked_before_writes(review_publication, name):
    source, out, built = review_publication
    state = prepare(source, out)
    out.mkdir()
    original_input = source.read_bytes()
    try:
        os.link(source, out / name)
    except OSError as exc:
        pytest.skip(f"当前测试卷或ACL不允许创建硬链接：{exc}")
    with pytest.raises(PlanError):
        publish(state, source, built)
    assert source.read_bytes() == original_input
    assert not state.generation_path.exists()
    assert not state.index_path.exists()


def test_review_real_other_process_lock_keeps_old_index_and_retry_uses_new_generation(review_publication, monkeypatch):
    source, out, built = review_publication
    publish(prepare(source, out), source, built)
    old_index, _ = verify_generation(out)
    state = prepare(source, out, overwrite=True)
    lock = out / ".index.json.plan.lock"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    child_code = (
        "import sys\nfrom pathlib import Path\nfrom genshin_db.locking import file_lock\n"
        "with file_lock(Path(sys.argv[1])):\n"
        " print('locked', flush=True)\n sys.stdin.readline()\n"
    )
    child = subprocess.Popen([sys.executable, "-c", child_code, str(lock)], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    original_lock = files.file_lock
    try:
        assert child.stdout.readline().strip() == "locked"
        monkeypatch.setattr(files, "file_lock", lambda path: original_lock(path, timeout=.05))
        with pytest.raises(PlanError) as caught:
            publish(state, source, built)
        assert caught.value.details["index_committed"] is False
        assert len(caught.value.details["published_paths"]) == 5
        assert verify_generation(out)[0] == old_index
    finally:
        child.communicate("release\n", timeout=5)
    assert child.returncode == 0
    new_state = prepare(source, out, overwrite=True)
    assert new_state.generation_name != state.generation_name
    publish(new_state, source, built)
    assert verify_generation(out)[0] != old_index
    assert state.generation_path.exists()  # Preserve the uncommitted generation.


def test_review_total_budget_counts_index_and_manifest_before_creation(review_publication, monkeypatch):
    source, out, built = review_publication
    artifact_bytes = sum(map(len, built["artifacts"].values()))
    assert artifact_bytes > 0
    monkeypatch.setitem(delivery_files.LIMITS, "total_bytes", artifact_bytes)
    with pytest.raises(PlanError) as caught:
        publish(prepare(source, out), source, built)
    assert caught.value.details["phase"] == "total"
    assert caught.value.details["actual_bytes"] > artifact_bytes
    assert not out.exists()


def test_review_short_receipt_utf8_limit_exact_and_one_byte_short(review_publication, monkeypatch):
    source, out, built = review_publication
    state = prepare(source, out)
    _, _, data = delivery_files._build_documents(state, built, built["metadata"]["input_file_sha"])
    envelopes = ({"code": 0, "message": "ok", "data": data},
                 {"code": 1, "message": delivery_files.DIAGNOSTIC_MESSAGE,
                  "reason": "calculation_diagnostic", "details": data})
    # Actual two CLI envelopes, both pretty settings, with Windows line endings.
    maximum = max(len((json.dumps(envelope, ensure_ascii=False, indent=indent).replace("\n", "\r\n")
                       + "\r\n").encode("utf8")) for envelope in envelopes for indent in (None, 2))
    monkeypatch.setitem(delivery_files.LIMITS, "summary_bytes", maximum)
    actual = publish(state, source, built)
    assert actual["data"]["index_committed"] is True
    verify_generation(out)
    other = out.with_name("overflow")  # Same path length, so only the budget differs.
    assert len(str(other)) == len(str(out))
    monkeypatch.setitem(delivery_files.LIMITS, "summary_bytes", maximum - 1)
    with pytest.raises(PlanError) as caught:
        publish(prepare(source, other), source, built)
    assert caught.value.details["phase"] == "summary"
    assert caught.value.details["actual_bytes"] == maximum
    assert not other.exists()
