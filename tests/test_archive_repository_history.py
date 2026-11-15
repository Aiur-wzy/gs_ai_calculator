from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from scripts.maintenance import archive_repository_history as archive_history


@pytest.fixture
def archive_workspace() -> Path:
    root = Path(".archive-test-work") / uuid.uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root.resolve()
    finally:
        shutil.rmtree(root, ignore_errors=False)


def _plan() -> dict:
    return {
        "schema_version": 1,
        "archive_id": "test-history",
        "archive_root": "archives/test-history",
        "manifest_path": "benchmarks/archive-manifests/test-history.json",
        "allowed_source_roots": ["benchmarks/source"],
        "groups": [
            {
                "id": "raw-log",
                "kind": "raw_jsonl",
                "archive": "raw/log.zip",
                "archive_sources": ["benchmarks/source/log.jsonl"],
                "delete_sources": ["benchmarks/source/log.jsonl"],
            }
        ],
    }


def _inventory_plan() -> dict:
    plan = _plan()
    plan["inventory_roots"] = ["benchmarks/source"]
    plan["retained_sources"] = ["benchmarks/source/keep.txt"]
    plan["groups"][0]["require_full_archive_coverage"] = True
    return plan


def _write_plan(workspace: Path, plan: dict) -> Path:
    path = workspace / "benchmarks/archive-manifests/test-history-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
        )
        if result.returncode:
            # cmd emits bytes in the active Windows code page, independently
            # of Python's UTF-8 mode. Preserve them exactly for diagnostics;
            # a successful mklink does not require decoding its prose.
            pytest.skip(f"cannot create test junction: {(result.stderr or result.stdout)!r}")
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd output contract")
def test_directory_link_command_output_stays_bytes(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        # Native output can be valid in the Windows code page and invalid
        # UTF-8. Do not silently replace or discard those bytes.
        return subprocess.CompletedProcess(argv, 0, b"\xb4\xb4\xbd\xa8", b"")

    monkeypatch.setattr(subprocess, "run", run)
    link, target = tmp_path / "link", tmp_path / "target"
    _make_directory_link(link, target)
    assert calls == [(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                      {"check": False, "capture_output": True})]


def test_prepare_delete_is_verified_and_idempotent(archive_workspace: Path) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'{"value": 1}\n{"value": 2}')
    plan = _plan()
    plan_path = _write_plan(archive_workspace, plan)

    manifest = archive_history.prepare(archive_workspace, plan_path, plan)

    record = manifest["groups"][0]["sources"][0]
    assert record["line_count"] == 2
    assert record == manifest["groups"][0]["archive"]["members"][0]
    archive_history.verify_and_delete(
        archive_workspace, plan_path, plan, delete_sources=False
    )
    assert source.exists()
    archive_history.verify_and_delete(archive_workspace, plan_path, plan)
    assert not source.exists()
    archive_history.verify_and_delete(archive_workspace, plan_path, plan)


def test_delete_rejects_manifest_source_not_in_plan(archive_workspace: Path) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    victim = archive_workspace / "benchmarks/source/keep.jsonl"
    victim.write_text('{"keep": true}\n', encoding="utf-8")
    plan = _plan()
    plan_path = _write_plan(archive_workspace, plan)
    archive_history.prepare(archive_workspace, plan_path, plan)

    manifest_path = archive_workspace / plan["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["groups"][0]["sources"][0]["path"] = "benchmarks/source/keep.jsonl"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest source list mismatch"):
        archive_history.verify_and_delete(archive_workspace, plan_path, plan)
    assert source.exists()
    assert victim.exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("archive_root", "../outside-archives", "archive_root must be below archives"),
        ("manifest_path", "../outside-manifest.json", "manifest_path must be below benchmarks"),
        ("allowed_source_roots", [".."], "allowed source root escaped workspace"),
    ],
)
def test_plan_paths_cannot_escape_workspace(
    archive_workspace: Path, field: str, value: object, message: str
) -> None:
    plan = _plan()
    plan[field] = value
    with pytest.raises(ValueError, match=message):
        archive_history.validated_plan_paths(archive_workspace, plan)


def test_group_archive_cannot_escape_configured_archive_root(archive_workspace: Path) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    plan = _plan()
    plan["groups"][0]["archive"] = "../escaped.zip"
    plan_path = _write_plan(archive_workspace, plan)

    with pytest.raises(ValueError, match="archive escaped archive_root"):
        archive_history.prepare(archive_workspace, plan_path, plan)
    assert source.exists()


def test_inventory_closure_supports_verified_idempotent_delete(
    archive_workspace: Path,
) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    retained = archive_workspace / "benchmarks/source/keep.txt"
    retained.write_text("keep\n", encoding="utf-8")
    plan = _inventory_plan()
    plan_path = _write_plan(archive_workspace, plan)

    archive_history.prepare(archive_workspace, plan_path, plan)
    archive_history.verify_and_delete(archive_workspace, plan_path, plan)
    assert not source.exists()
    assert retained.exists()
    archive_history.verify_and_delete(archive_workspace, plan_path, plan)


def test_inventory_closure_rejects_unclassified_file_before_delete(
    archive_workspace: Path,
) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    retained = archive_workspace / "benchmarks/source/keep.txt"
    retained.write_text("keep\n", encoding="utf-8")
    plan = _inventory_plan()
    plan_path = _write_plan(archive_workspace, plan)
    archive_history.prepare(archive_workspace, plan_path, plan)
    (archive_workspace / "benchmarks/source/unplanned.txt").write_text(
        "new\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="inventory contains unclassified file"):
        archive_history.verify_and_delete(archive_workspace, plan_path, plan)
    assert source.exists()
    assert retained.exists()


def test_inventory_closure_rejects_unclassified_file_during_prepare(
    archive_workspace: Path,
) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    retained = archive_workspace / "benchmarks/source/keep.txt"
    retained.write_text("keep\n", encoding="utf-8")
    (archive_workspace / "benchmarks/source/unplanned.txt").write_text(
        "new\n", encoding="utf-8"
    )
    plan = _inventory_plan()
    plan_path = _write_plan(archive_workspace, plan)

    with pytest.raises(RuntimeError, match="inventory contains unclassified file"):
        archive_history.prepare(archive_workspace, plan_path, plan)
    assert source.exists()
    assert retained.exists()
    assert not (archive_workspace / "archives").exists()


def test_inventory_closure_rejects_missing_retained_file_before_delete(
    archive_workspace: Path,
) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    retained = archive_workspace / "benchmarks/source/keep.txt"
    retained.write_text("keep\n", encoding="utf-8")
    plan = _inventory_plan()
    plan_path = _write_plan(archive_workspace, plan)
    archive_history.prepare(archive_workspace, plan_path, plan)
    retained.unlink()

    with pytest.raises(RuntimeError, match="retained inventory file.*missing"):
        archive_history.verify_and_delete(archive_workspace, plan_path, plan)
    assert source.exists()


def test_inventory_closure_rejects_retained_delete_overlap(
    archive_workspace: Path,
) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    plan = _inventory_plan()
    plan["retained_sources"] = ["benchmarks/source/log.jsonl"]
    plan_path = _write_plan(archive_workspace, plan)

    with pytest.raises(ValueError, match="retained and delete sources overlap"):
        archive_history.prepare(archive_workspace, plan_path, plan)


def test_full_archive_coverage_rejects_unarchived_delete_source(
    archive_workspace: Path,
) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    plan = _plan()
    plan["groups"][0]["archive_sources"] = []
    plan["groups"][0]["require_full_archive_coverage"] = True
    plan_path = _write_plan(archive_workspace, plan)

    with pytest.raises(ValueError, match="require_full_archive_coverage"):
        archive_history.prepare(archive_workspace, plan_path, plan)


def test_blocked_inventory_draft_cannot_prepare(archive_workspace: Path) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    plan = _plan()
    plan["blocked_inventory_paths"] = ["benchmarks/source/inaccessible"]
    plan_path = _write_plan(archive_workspace, plan)

    archive_history.validate_plan(plan, allow_blocked_draft=True)
    with pytest.raises(ValueError, match="unresolved blocked inventory paths"):
        archive_history.prepare(archive_workspace, plan_path, plan)
    assert source.exists()
    assert not (archive_workspace / "archives").exists()


def test_inventory_and_empty_cleanup_do_not_cross_directory_link(
    archive_workspace: Path,
) -> None:
    source = archive_workspace / "benchmarks/source/remove/nested/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    retained = archive_workspace / "benchmarks/source/keep.txt"
    retained.write_text("keep\n", encoding="utf-8")
    external = archive_workspace / "external-runtime"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("outside inventory\n", encoding="utf-8")
    link = archive_workspace / "benchmarks/source/node_modules"
    _make_directory_link(link, external)

    plan = _inventory_plan()
    plan["groups"][0]["archive_sources"] = [
        "benchmarks/source/remove/nested/log.jsonl"
    ]
    plan["groups"][0]["delete_sources"] = [
        "benchmarks/source/remove/nested/log.jsonl"
    ]
    plan["deferred_reparse_paths"] = ["benchmarks/source/node_modules"]
    plan_path = _write_plan(archive_workspace, plan)

    manifest = archive_history.prepare(archive_workspace, plan_path, plan)
    members = manifest["groups"][0]["archive"]["members"]
    assert [record["path"] for record in members] == [
        "benchmarks/source/remove/nested/log.jsonl"
    ]
    archive_history.verify_and_delete(archive_workspace, plan_path, plan)

    assert not source.exists()
    assert not source.parent.exists()
    assert link.exists()
    assert sentinel.read_text(encoding="utf-8") == "outside inventory\n"
    assert retained.exists()


def test_inventory_rejects_unplanned_directory_link(
    archive_workspace: Path,
) -> None:
    source = archive_workspace / "benchmarks/source/log.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n", encoding="utf-8")
    retained = archive_workspace / "benchmarks/source/keep.txt"
    retained.write_text("keep\n", encoding="utf-8")
    external = archive_workspace / "external-runtime"
    external.mkdir()
    (external / "sentinel.txt").write_text("outside\n", encoding="utf-8")
    _make_directory_link(
        archive_workspace / "benchmarks/source/node_modules", external
    )
    plan = _inventory_plan()
    plan_path = _write_plan(archive_workspace, plan)

    with pytest.raises(RuntimeError, match="inventory reparse point set mismatch"):
        archive_history.prepare(archive_workspace, plan_path, plan)
    assert source.exists()
    assert not (archive_workspace / "archives").exists()
