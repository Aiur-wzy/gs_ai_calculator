"""Failure boundaries of the index-last runtime fact publisher."""
import hashlib
import json

import pytest

from genshin_app import runtime_batch_files as files
from genshin_app.equipment.schema import PlanError


def batch():
    return {"batch_version": 1, "status": "partial", "snapshot": {"snapshot_id": "A"},
            "counts": {"requested": 3, "ok": 1, "error": 1, "skipped": 1},
            "limits": {"max_items": 8}, "content_fingerprint": "frozen-payload",
            "items": [
                {"id": "CON", "request": {"action": "action_facts"}, "status": "ok",
                 "data": {"status": "pending", "原文": "保留全部中文资料"}},
                {"id": "two", "request": {"action": "assemble"}, "status": "error",
                 "error": {"reason": "not_found"}},
                {"id": "three", "request": {"action": "action_parameter"},
                 "status": "skipped", "reason": "batch_budget_exhausted"}]}


def test_partial_generations_keep_each_status_and_original_files(tmp_path):
    target = tmp_path / "facts"
    prepared = files.prepare_batch_directory(target)
    assert not target.exists()
    first = files.publish_batch_directory(prepared, batch())
    index_bytes = (target / "index.json").read_bytes()
    index = json.loads(index_bytes)
    assert index["status"] == "partial"
    for expected, entry in zip(batch()["items"], index["items"], strict=True):
        path = target / entry["path"]
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["file_sha"]
        assert len(raw) == entry["bytes"]
        actual = json.loads(raw)
        assert {k: actual[k] for k in expected} == expected
        assert "CON" not in path.name
    old_file = target / index["items"][0]["path"]
    old_file.write_text("用户笔记", encoding="utf-8")
    second = files.publish_batch_directory(
        files.prepare_batch_directory(target, overwrite=True), batch())
    assert first["generation_path"] != second["generation_path"]
    assert old_file.read_text(encoding="utf-8") == "用户笔记"
    assert (target / "index.json.previous").read_bytes() == index_bytes


def test_default_rejects_existing_index_before_query(tmp_path):
    index = tmp_path / "index.json"
    index.write_text("user index", encoding="utf-8")
    with pytest.raises(PlanError, match="已存在"):
        files.prepare_batch_directory(tmp_path)
    assert list(tmp_path.iterdir()) == [index]


def test_cas_captured_before_query_rejects_later_index(tmp_path):
    index = tmp_path / "index.json"
    index.write_text("old", encoding="utf-8")
    state = files.prepare_batch_directory(tmp_path, overwrite=True)
    index.write_text("outside edit during DB query", encoding="utf-8")
    with pytest.raises(PlanError) as caught:
        files.publish_batch_directory(state, batch())
    assert caught.value.details["index_committed"] is None
    assert caught.value.details["observed_index_state"] == "externally_changed"
    assert caught.value.details["published_paths"] == []
    assert not list(tmp_path.glob("generation-*"))
    assert index.read_text() == "outside edit during DB query"


@pytest.mark.parametrize("failure", ["item", "index", "mutation"])
def test_failed_publication_keeps_old_index_and_reports_orphans(tmp_path, monkeypatch, failure):
    first = files.publish_batch_directory(files.prepare_batch_directory(tmp_path), batch())
    old_index = (tmp_path / "index.json").read_bytes()
    state = files.prepare_batch_directory(tmp_path, overwrite=True)
    real_publish = files.publish_files
    calls = 0

    def faulty(outputs, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            if failure == "item":
                report = real_publish(outputs[:1], **kwargs)
                raise PlanError("磁盘写入失败", "file_conflict", report)
            report = real_publish(outputs, **kwargs)
            if failure == "mutation":
                outputs[0][0].write_text("outside item edit", encoding="utf-8")
            return report
        raise OSError("index replace failed")

    monkeypatch.setattr(files, "publish_files", faulty)
    with pytest.raises(PlanError) as caught:
        files.publish_batch_directory(state, batch())
    assert (tmp_path / "index.json").read_bytes() == old_index
    assert caught.value.details["index_committed"] is False
    assert len(caught.value.details["published_paths"]) == (1 if failure == "item" else 3)
    assert all((tmp_path / item["path"]).exists() for item in first["index"]["items"])
    if failure == "mutation":
        assert calls == 1  # Corrupt new files never reach the manifest publication.


def test_modified_index_after_items_keeps_outside_edit(tmp_path, monkeypatch):
    files.publish_batch_directory(files.prepare_batch_directory(tmp_path), batch())
    state = files.prepare_batch_directory(tmp_path, overwrite=True)
    real_publish = files.publish_files

    def edit_after_items(outputs, **kwargs):
        result = real_publish(outputs, **kwargs)
        (tmp_path / "index.json").write_text("edited after items", encoding="utf-8")
        return result

    monkeypatch.setattr(files, "publish_files", edit_after_items)
    with pytest.raises(PlanError) as caught:
        files.publish_batch_directory(state, batch())
    assert len(caught.value.details["published_paths"]) == 3
    assert (tmp_path / "index.json").read_text() == "edited after items"


@pytest.mark.parametrize("reserved", ["index.json", "index.json.previous", ".index.json.plan.lock"])
def test_protected_inputs_and_hardlink_aliases_are_never_touched(tmp_path, reserved):
    source = tmp_path / "source.json"
    source.write_bytes(b"source input")
    target = tmp_path / "facts"
    target.mkdir()
    try:
        (target / reserved).hardlink_to(source)
    except OSError as exc:
        pytest.skip(f"当前测试卷或ACL不允许创建硬链接：{exc}")
    with pytest.raises(PlanError, match="覆盖输入"):
        files.prepare_batch_directory(target, overwrite=True, protected_paths=[source])
    assert source.read_bytes() == b"source input"


def test_total_utf8_delivery_budget_checked_before_creating_directory(tmp_path, monkeypatch):
    target = tmp_path / "facts"
    monkeypatch.setattr(files, "MAX_DELIVERY_BYTES", 1500)
    data = batch()
    data["items"][0]["data"]["原文"] = "中文" * 500
    with pytest.raises(PlanError) as caught:
        files.publish_batch_directory(files.prepare_batch_directory(target), data)
    assert caught.value.reason == "response_budget_exceeded"
    assert caught.value.details["published_paths"] == []
    assert not target.exists()


def test_index_committed_before_receipt_error_is_reported_honestly(tmp_path, monkeypatch):
    state = files.prepare_batch_directory(tmp_path)
    real_publish = files.publish_files

    def fail_after_index(outputs, **kwargs):
        result = real_publish(outputs, **kwargs)
        if outputs[0][0].name == "index.json":
            raise OSError("receipt read failed after replace")
        return result

    monkeypatch.setattr(files, "publish_files", fail_after_index)
    with pytest.raises(PlanError) as caught:
        files.publish_batch_directory(state, batch())
    assert caught.value.details["index_committed"] is True
    assert caught.value.details["observed_index_state"] == "new_generation"
    index = json.loads((tmp_path / "index.json").read_bytes())
    for entry in index["items"]:
        assert files.file_sha(tmp_path / entry["path"]) == entry["file_sha"]
