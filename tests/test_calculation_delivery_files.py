"""File-only delivery tests; byte fixtures intentionally do not execute an engine."""
import copy
import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from genshin_app import calculation_delivery_files as files
from genshin_app.equipment.schema import PlanError


def _hardlink_or_skip(link, target):
    try:
        link.hardlink_to(target)
    except OSError as exc:
        pytest.skip(f"当前测试卷或ACL不允许创建硬链接：{exc}")


def built(source_sha, *, diagnostic=False):
    return {"artifacts": {"native_plan": b'{"native":"fixture"}', "calculated": b'{"calculated":"fixture"}',
                          "result_workbook": b"PK-result-fixture", "formula_workbook": b"PK-formula-fixture"},
            "metadata": {"package_schema": files.PACKAGE_SCHEMA, "producer": {"app_version": "test", "composer_version": "test"},
                         "input_file_sha": source_sha, "native_input_fingerprint": "a" * 64,
                         "input_fingerprint": "a" * 64, "output_fingerprint": "b" * 64,
                         "source_fingerprint": "c" * 64, "result_fingerprint": "d" * 64,
                         "calculated_fingerprint": "e" * 64, "plan_id": "独立文件夹具", "revision": 0,
                         "data_versions": [{"version_line": "release", "snapshot_id": "source-A"}],
                         "calculation_status": "diagnostic" if diagnostic else "complete",
                         "rotation_status": "failed" if diagnostic else "unproven", "mechanics_status": "passed",
                         "complete_input_window": not diagnostic,
                         "damage_interpretation": {"kind": "executed_prefix" if diagnostic else "planned_damage"},
                         "formula_support": {"status": "partial", "limitation_count": 1}},
            "summary": {"total_damage": 45, "dps": None if diagnostic else 4.5, "reported_hits": 1, "issue_counts": {}},
            "timings": {"fresh_calc": .2, "validate_build": .1}, "diagnostic": diagnostic}


@pytest.fixture
def delivery(tmp_path):
    source = tmp_path / "input.json"
    source.write_bytes(b"source-input-bytes")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "delivery"
    return source, sha, target


def publish(delivery, *, diagnostic=False, overwrite=False):
    source, sha, target = delivery
    state = files.prepare_delivery_directory(target, overwrite=overwrite, protected_paths=[source])
    return files.publish_calculation_delivery(state, built(sha, diagnostic=diagnostic), input_path=source, input_file_sha=sha)


@pytest.mark.parametrize("diagnostic", [False, True])
def test_complete_generation_matches_all_hashes_and_keeps_calculation_state(delivery, diagnostic):
    source, sha, target = delivery
    state = files.prepare_delivery_directory(target, protected_paths=[source])
    assert not target.exists()
    with pytest.raises(FrozenInstanceError):
        state.generation_name = "other"
    value = built(sha, diagnostic=diagnostic)
    before = copy.deepcopy(value)
    published = files.publish_calculation_delivery(state, value, input_path=source, input_file_sha=sha)
    data = published["data"]
    assert value == before and published["diagnostic"] is diagnostic
    assert data["index_committed"] is True and data["publication_status"] == "complete"
    assert data["metadata"]["calculation_status"] == ("diagnostic" if diagnostic else "complete")
    assert "data_versions" not in data["metadata"]
    raw_index = (target / "index.json").read_bytes()
    assert hashlib.sha256(raw_index).hexdigest() == data["index_sha256"]
    index = json.loads(raw_index)
    manifest_raw = (target / index["manifest"]["path"]).read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest() == index["manifest"]["sha256"]
    assert len(manifest_raw) == index["manifest"]["bytes"]
    manifest = json.loads(manifest_raw)
    assert manifest["input_file_sha"] == sha and manifest["data_versions"][0]["snapshot_id"] == "source-A"
    total = len(raw_index) + len(manifest_raw)
    for entry, local in zip(index["artifacts"], manifest["artifacts"], strict=True):
        path = target / entry["path"]
        raw = path.read_bytes()
        assert len(raw) == entry["bytes"] == local["bytes"]
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"] == local["sha256"]
        assert path.parent.name == state.generation_name
        total += len(raw)
    assert total == manifest["total_bytes"] == data["total_bytes"]


def test_selected_app_products_publish_and_can_replace_existing_complete_index(delivery):
    source, sha, target = delivery
    publish(delivery)
    value = built(sha)
    value["artifacts"] = {key: content for key, content in value["artifacts"].items()
                          if key in {"native_plan", "calculated"}}
    state = files.prepare_delivery_directory(target, overwrite=True, protected_paths=[source])
    result = files.publish_calculation_delivery(state, value, input_path=source, input_file_sha=sha)
    assert [row["role"] for row in result["data"]["artifacts"]] == ["native_plan", "calculated"]
    assert not (state.generation_path / "formula.xlsx").exists()
    assert not (state.generation_path / "result.xlsx").exists()
    # Recognize this real subset index on the next ordinary preflight.
    files.prepare_delivery_directory(target, overwrite=True, protected_paths=[source])


def test_overwrite_replaces_only_index_and_keeps_old_and_user_files(delivery):
    _, _, target = delivery
    first = publish(delivery)
    old_index = (target / "index.json").read_bytes()
    old_file = target / json.loads(old_index)["artifacts"][0]["path"]
    old_file.write_text("user-edited old generation", encoding="utf-8")
    (target / "notes.txt").write_text("用户文件", encoding="utf-8")
    second = publish(delivery, overwrite=True)
    assert first["data"]["generation_path"] != second["data"]["generation_path"]
    assert (target / "index.json.previous").read_bytes() == old_index
    assert old_file.read_text("utf-8") == "user-edited old generation"
    assert (target / "notes.txt").read_text("utf-8") == "用户文件"


def test_existing_index_rejected_before_expensive_work_even_when_invalid(delivery):
    _, _, target = delivery
    target.mkdir()
    (target / "index.json").write_text("user JSON", encoding="utf-8")
    with pytest.raises(PlanError, match="已存在"):
        files.prepare_delivery_directory(target)
    with pytest.raises(PlanError, match="不是受支持"):
        files.prepare_delivery_directory(target, overwrite=True)
    assert [p.name for p in target.iterdir()] == ["index.json"]


@pytest.mark.parametrize("changed", ["schema", "generation", "manifest", "role", "duplicate", "bool_bytes", "extra"])
def test_foreign_or_corrupt_index_cannot_be_overwritten(delivery, changed):
    _, _, target = delivery
    publish(delivery)
    index = json.loads((target / "index.json").read_bytes())
    if changed == "schema":
        index["delivery_schema"] = "different.v1"
    elif changed == "generation":
        index["generation"] = "../escape"
    elif changed == "manifest":
        index["manifest"]["path"] = "foreign.json"
    elif changed == "role":
        index["artifacts"][0]["role"] = "other"
    elif changed == "duplicate":
        index["artifacts"][1] = index["artifacts"][0]
    elif changed == "bool_bytes":
        index["artifacts"][0]["bytes"] = True
    else:
        index["user_notes"] = "not our exact schema"
    (target / "index.json").write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(PlanError, match="不是受支持"):
        files.prepare_delivery_directory(target, overwrite=True)


def test_foreign_backup_file_is_not_silently_overwritten(delivery):
    _, _, target = delivery
    publish(delivery)
    backup = target / "index.json.previous"
    backup.write_text("用户自己的备份", encoding="utf-8")
    with pytest.raises(PlanError, match="不是受支持"):
        files.prepare_delivery_directory(target, overwrite=True)
    assert backup.read_text("utf-8") == "用户自己的备份"


@pytest.mark.parametrize("point", ["before_files", "before_index"])
def test_input_sha_change_rejects_publication_at_both_boundaries(delivery, monkeypatch, point):
    source, sha, target = delivery
    state = files.prepare_delivery_directory(target, protected_paths=[source])
    if point == "before_files":
        source.write_bytes(b"new external input")
    else:
        original = files.publish_files

        def change_after_files(outputs, **kwargs):
            result = original(outputs, **kwargs)
            source.write_bytes(b"new external input")
            return result

        monkeypatch.setattr(files, "publish_files", change_after_files)
    with pytest.raises(PlanError) as caught:
        files.publish_calculation_delivery(state, built(sha), input_path=source, input_file_sha=sha)
    assert caught.value.details["index_committed"] is False
    assert not (target / "index.json").exists()
    assert len(caught.value.details["published_paths"]) == (0 if point == "before_files" else 5)


def test_index_cas_captured_before_calculation_does_not_replace_competitor(delivery):
    source, sha, target = delivery
    publish(delivery)
    state = files.prepare_delivery_directory(target, overwrite=True, protected_paths=[source])
    (target / "index.json").write_bytes(b"external writer during calculation")
    with pytest.raises(PlanError) as caught:
        files.publish_calculation_delivery(state, built(sha), input_path=source, input_file_sha=sha)
    assert caught.value.details["index_committed"] is None
    assert caught.value.details["observed_index_state"] == "externally_changed"
    assert caught.value.details["published_paths"] == []
    assert not state.generation_path.exists()


@pytest.mark.parametrize("failure", ["third_file", "index", "file_changed", "post_commit", "unreadable_after_commit"])
def test_publication_failures_preserve_old_files_and_report_actual_commit(delivery, monkeypatch, failure):
    source, sha, target = delivery
    publish(delivery)
    old = (target / "index.json").read_bytes()
    old_files = {p: p.read_bytes() for p in target.glob("generation-*/*") if p.is_file()}
    state = files.prepare_delivery_directory(target, overwrite=True, protected_paths=[source])
    original, digest = files.publish_files, files._digest_file
    committed = False

    def fail(outputs, **kwargs):
        nonlocal committed
        is_index = outputs[0][0].name == "index.json"
        if not is_index and failure == "third_file":
            report = original(outputs[:2], **kwargs)
            raise PlanError("third file failed", "file_conflict", report)
        if is_index and failure == "index":
            raise OSError("index unavailable")
        report = original(outputs, **kwargs)
        if not is_index and failure == "file_changed":
            outputs[0][0].write_bytes(b"changed")
        if is_index:
            committed = True
            if failure in ("post_commit", "unreadable_after_commit"):
                raise OSError("error after index replace")
        return report

    def maybe_unreadable(path, maximum):
        if committed and failure == "unreadable_after_commit" and path.name == "index.json":
            raise PermissionError("temporarily unreadable index")
        return digest(path, maximum)

    monkeypatch.setattr(files, "publish_files", fail)
    monkeypatch.setattr(files, "_digest_file", maybe_unreadable)
    with pytest.raises(PlanError) as caught:
        files.publish_calculation_delivery(state, built(sha), input_path=source, input_file_sha=sha)
    expected = True if failure == "post_commit" else None if failure == "unreadable_after_commit" else False
    assert caught.value.details["index_committed"] is expected
    if expected is False:
        assert (target / "index.json").read_bytes() == old
    for path, raw in old_files.items():
        assert path.read_bytes() == raw


@pytest.mark.parametrize("reserved", ["index.json", "index.json.previous", ".index.json.plan.lock"])
def test_hardlinked_input_or_output_side_files_never_change_source(delivery, reserved):
    source, _, target = delivery
    before = source.read_bytes()
    target.mkdir()
    _hardlink_or_skip(target / reserved, source)
    with pytest.raises(PlanError):
        files.prepare_delivery_directory(target, overwrite=True, protected_paths=[source])
    assert source.read_bytes() == before


def test_database_write_lock_and_future_sidecars_are_reserved(delivery):
    source, _, target = delivery
    database = target.parent / "genshin.db"
    lock = target.parent / ".genshin.db.write.lock"
    lock.write_bytes(b"held lock contents")
    target.mkdir()
    _hardlink_or_skip(target / ".index.json.plan.lock", lock)
    with pytest.raises(PlanError, match="覆盖输入"):
        files.prepare_delivery_directory(target, protected_paths=[source, database])
    assert lock.read_bytes() == b"held lock contents"


@pytest.mark.parametrize("role,limit", [("native_plan", "native_plan_bytes"), ("calculated", "calculated_bytes"),
                                        ("result_workbook", "workbook_bytes"), ("formula_workbook", "workbook_bytes")])
def test_artifact_exact_byte_limit_and_overflow_without_files(delivery, monkeypatch, role, limit):
    source, sha, target = delivery
    value = built(sha)
    monkeypatch.setitem(files.LIMITS, limit, 100)
    value["artifacts"][role] = b"x" * 101
    state = files.prepare_delivery_directory(target, protected_paths=[source])
    with pytest.raises(PlanError) as caught:
        files.publish_calculation_delivery(state, value, input_path=source, input_file_sha=sha)
    assert caught.value.reason == "response_budget_exceeded" and not target.exists()
    value["artifacts"][role] = b"x" * 100
    assert files.publish_calculation_delivery(state, value, input_path=source, input_file_sha=sha)["data"]["index_committed"]


@pytest.mark.parametrize("limit,phase", [("manifest_bytes", "manifest"), ("index_bytes", "index"),
                                      ("summary_bytes", "summary"), ("total_bytes", "total")])
def test_every_document_and_whole_envelope_budget_is_prepublication(delivery, monkeypatch, limit, phase):
    source, sha, target = delivery
    state = files.prepare_delivery_directory(target, protected_paths=[source])
    monkeypatch.setitem(files.LIMITS, limit, 100)
    with pytest.raises(PlanError) as caught:
        files.publish_calculation_delivery(state, built(sha), input_path=source, input_file_sha=sha)
    assert caught.value.reason == "response_budget_exceeded"
    assert caught.value.details["phase"] == phase
    assert caught.value.details["index_committed"] is False and not target.exists()


def test_generation_collision_never_repairs_or_removes_existing_files(delivery):
    source, sha, _ = delivery
    state = files.prepare_delivery_directory(delivery[2], protected_paths=[source])
    state.generation_path.mkdir(parents=True)
    notes = state.generation_path / "user-notes.txt"
    notes.write_text("remain", encoding="utf-8")
    with pytest.raises(PlanError):
        files.publish_calculation_delivery(state, built(sha), input_path=source, input_file_sha=sha)
    assert notes.read_text() == "remain" and not state.index_path.exists()


def test_final_protection_rejects_a_new_input_hardlink_before_index_commit(delivery, monkeypatch):
    source, sha, target = delivery
    publish(delivery)
    state = files.prepare_delivery_directory(target, overwrite=True, protected_paths=[source])
    original = files.publish_files

    def change_after_files(outputs, **kwargs):
        report = original(outputs, **kwargs)
        _hardlink_or_skip(target / "index.json.previous", source)
        return report

    monkeypatch.setattr(files, "publish_files", change_after_files)
    with pytest.raises(PlanError) as caught:
        files.publish_calculation_delivery(state, built(sha), input_path=source, input_file_sha=sha)
    assert caught.value.details["index_committed"] is False
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha
