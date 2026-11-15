from __future__ import annotations

from pathlib import Path

import pytest

from genshin_app import release_pair_storage as storage


def _allocated(size: int, files: int, cluster: int = 4096) -> int:
    return size + files * (cluster - 1)


def test_before_download_aggregates_exact_same_volume_peak(tmp_path, monkeypatch):
    free = 100 * 1024 * 1024 * 1024

    def volume(path):
        return {
            "key": "same-volume",
            "path": Path(path),
            "free_bytes": free,
            "cluster_bytes": 4096,
        }

    monkeypatch.setattr(storage, "_volume", volume)
    result = storage.preflight_before_download(
        workspace=tmp_path / "workspace",
        active_app_root=tmp_path / "app",
        data_dir=tmp_path / "data",
        app_archive_bytes=100,
        app_unpacked_bytes=1_000,
        app_unpacked_files=10,
        target_database_bytes=500,
        current_database_bytes=400,
    )
    fact = result["volumes"]["same-volume"]
    assert fact["phase_bytes"]["transport"] == (
        _allocated(100, 1) + _allocated(1_000, 10) + _allocated(500, 1)
    )
    assert fact["phase_bytes"]["update"] == (
        2 * _allocated(1_000, 10)
        + 2 * _allocated(500, 1)
        + 2 * _allocated(400, 1)
    )
    assert fact["required_free_bytes"] == (
        fact["phase_bytes"]["update"] + storage.MIN_RESERVE_BYTES
    )


def test_after_stage_fails_closed_with_per_volume_diagnostics(tmp_path, monkeypatch):
    def volume(path):
        return {
            "key": str(path),
            "path": Path(path),
            "free_bytes": 1,
            "cluster_bytes": 4096,
        }

    monkeypatch.setattr(storage, "_volume", volume)
    with pytest.raises(storage.ReleasePairStorageError) as failure:
        storage.preflight_after_stage(
            active_app_root=tmp_path / "app",
            data_dir=tmp_path / "data",
            app_unpacked_bytes=100,
            app_unpacked_files=2,
            target_database_bytes=100,
            current_database_bytes=100,
        )
    assert failure.value.reason == "release_pair_update_disk_space_insufficient"
    assert failure.value.details["sufficient"] is False


def test_reused_database_is_not_budgeted_as_stage_or_rollback(tmp_path, monkeypatch):
    def volume(path):
        return {
            "key": "same-volume",
            "path": Path(path),
            "free_bytes": 100 * 1024 * 1024 * 1024,
            "cluster_bytes": 4096,
        }

    monkeypatch.setattr(storage, "_volume", volume)
    result = storage.preflight_before_download(
        workspace=tmp_path / "workspace",
        active_app_root=tmp_path / "app",
        data_dir=tmp_path / "data",
        app_archive_bytes=100,
        app_unpacked_bytes=1_000,
        app_unpacked_files=10,
        target_database_bytes=10_000_000,
        current_database_bytes=9_000_000,
        reuse_installed_database=True,
    )
    fact = result["volumes"]["same-volume"]
    assert fact["phase_bytes"]["transport"] == (
        _allocated(100, 1) + _allocated(1_000, 10)
    )
    assert fact["phase_bytes"]["update"] == 2 * _allocated(1_000, 10)


def test_directory_shape_counts_all_files_and_rejects_links(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "one").write_bytes(b"123")
    (root / "two").write_bytes(b"45678")
    assert storage.directory_shape(root) == {"bytes": 8, "files": 2}

    alias = root / "alias"
    alias_root = tmp_path / "alias-root"
    try:
        alias.symlink_to(root / "one")
        alias_root.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(storage.ReleasePairStorageError, match="release_pair_storage_tree_invalid"):
        storage.directory_shape(alias_root)
    with pytest.raises(storage.ReleasePairStorageError) as failure:
        storage.directory_shape(root)
    assert failure.value.reason == "release_pair_storage_tree_invalid"
