"""Conservative disk-space planning for an atomic App/database pair update."""

from __future__ import annotations

import ctypes
import os
import shutil
from pathlib import Path
from typing import Any

from .windows_known_folders import WindowsPathAuthorityError, reject_reparse_chain

MIN_RESERVE_BYTES = 512 * 1024 * 1024
RESERVE_DIVISOR = 20  # five percent, with MIN_RESERVE_BYTES as the floor


class ReleasePairStorageError(RuntimeError):
    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        self.reason = reason
        self.details = details or {}
        super().__init__(reason)


def _existing_ancestor(path: Path) -> Path:
    current = Path(path).absolute()
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise ReleasePairStorageError("release_pair_storage_path_unavailable")
        current = parent
    return current.resolve(strict=True)


def _cluster_size(path: Path) -> int:
    if os.name == "nt":
        sectors = ctypes.c_uint32()
        bytes_per_sector = ctypes.c_uint32()
        free_clusters = ctypes.c_uint32()
        total_clusters = ctypes.c_uint32()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDiskFreeSpaceW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        kernel.GetDiskFreeSpaceW.restype = ctypes.c_bool
        if not kernel.GetDiskFreeSpaceW(
            str(path),
            ctypes.byref(sectors),
            ctypes.byref(bytes_per_sector),
            ctypes.byref(free_clusters),
            ctypes.byref(total_clusters),
        ):
            raise ReleasePairStorageError("release_pair_storage_path_unavailable")
        value = sectors.value * bytes_per_sector.value
    else:
        try:
            value = os.statvfs(path).f_frsize
        except OSError as error:
            raise ReleasePairStorageError(
                "release_pair_storage_path_unavailable"
            ) from error
    if not 512 <= value <= 4 * 1024 * 1024 or value & (value - 1):
        raise ReleasePairStorageError("release_pair_storage_cluster_invalid")
    return value


def _volume(path: Path) -> dict[str, Any]:
    existing = _existing_ancestor(path)
    try:
        stat = existing.stat()
        free = shutil.disk_usage(existing).free
    except OSError as error:
        raise ReleasePairStorageError("release_pair_storage_path_unavailable") from error
    return {
        "key": f"{stat.st_dev}:{existing.anchor.casefold()}",
        "path": existing,
        "free_bytes": free,
        "cluster_bytes": _cluster_size(existing),
    }


def _allocated(size: int, files: int, cluster: int) -> int:
    if type(size) is not int or size < 0 or type(files) is not int or files < 1:
        raise ReleasePairStorageError("release_pair_storage_input_invalid")
    # With only total logical bytes and file count available, this is the
    # conservative upper bound for per-file cluster rounding.
    return size + files * (cluster - 1)


def directory_shape(root: Path) -> dict[str, int]:
    """Return exact regular-file count/logical bytes and reject links."""

    try:
        supplied = reject_reparse_chain(Path(root), reason="release_pair_storage_tree_invalid")
    except WindowsPathAuthorityError as error:
        raise ReleasePairStorageError(error.reason) from error
    files = 0
    size = 0
    for directory, names, filenames in os.walk(supplied, followlinks=False):
        current = Path(directory)
        for name in (*names, *filenames):
            path = current / name
            try:
                is_link = path.is_symlink()
                junction = getattr(path, "is_junction", None)
                is_junction = callable(junction) and junction()
                reparse = bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
            except OSError as error:
                raise ReleasePairStorageError(
                    "release_pair_storage_tree_invalid"
                ) from error
            if is_link or is_junction or reparse:
                raise ReleasePairStorageError("release_pair_storage_tree_invalid")
        for name in filenames:
            path = current / name
            if not path.is_file():
                raise ReleasePairStorageError("release_pair_storage_tree_invalid")
            files += 1
            size += path.stat().st_size
    if files < 1:
        raise ReleasePairStorageError("release_pair_storage_tree_invalid")
    return {"bytes": size, "files": files}


def _add(bucket: dict[str, int], volume: dict[str, Any], amount: int) -> None:
    bucket[volume["key"]] = bucket.get(volume["key"], 0) + amount


def _finish(
    volumes: list[dict[str, Any]],
    phases: dict[str, dict[str, int]],
) -> dict[str, Any]:
    facts: dict[str, dict[str, Any]] = {}
    for volume in volumes:
        current = facts.setdefault(
            volume["key"],
            {
                "paths": [],
                "free_bytes": volume["free_bytes"],
                "cluster_bytes": volume["cluster_bytes"],
            },
        )
        current["paths"].append(str(volume["path"]))
        current["free_bytes"] = min(current["free_bytes"], volume["free_bytes"])
        if current["cluster_bytes"] != volume["cluster_bytes"]:
            raise ReleasePairStorageError("release_pair_storage_volume_inconsistent")
    enough = True
    for key, fact in facts.items():
        phase_bytes = {name: values.get(key, 0) for name, values in phases.items()}
        peak = max(phase_bytes.values(), default=0)
        reserve = max(MIN_RESERVE_BYTES, peak // RESERVE_DIVISOR)
        required = peak + reserve
        fact.update(
            phase_bytes=phase_bytes,
            peak_additional_bytes=peak,
            reserve_bytes=reserve,
            required_free_bytes=required,
            sufficient=fact["free_bytes"] >= required,
        )
        enough = enough and fact["sufficient"]
    result = {"sufficient": enough, "volumes": facts}
    if not enough:
        raise ReleasePairStorageError(
            "release_pair_update_disk_space_insufficient", result
        )
    return result


def preflight_before_download(
    *,
    workspace: Path,
    active_app_root: Path,
    data_dir: Path,
    app_archive_bytes: int,
    app_unpacked_bytes: int,
    app_unpacked_files: int,
    target_database_bytes: int,
    current_database_bytes: int,
    reuse_installed_database: bool = False,
    reuse_installed_app: bool = False,
    database_archive_bytes: int = 0,
    download_cache: Path | None = None,
) -> dict[str, Any]:
    """Budget transport plus the later maximum atomic-update working set."""

    if (type(reuse_installed_database) is not bool or type(reuse_installed_app) is not bool
            or type(database_archive_bytes) is not int or database_archive_bytes < 0):
        raise ReleasePairStorageError("release_pair_storage_input_invalid")
    staged_database_bytes = 0 if reuse_installed_database else target_database_bytes
    rollback_database_bytes = 0 if reuse_installed_database else current_database_bytes
    work = _volume(workspace)
    active = _volume(active_app_root.parent)
    data = _volume(data_dir)
    transport: dict[str, int] = {}
    update: dict[str, int] = {}
    _add(
        transport,
        work,
        (0 if reuse_installed_app else _allocated(app_archive_bytes, 1, work["cluster_bytes"])
         + _allocated(app_unpacked_bytes, app_unpacked_files, work["cluster_bytes"]))
        + (
            0
            if reuse_installed_database
            else _allocated(staged_database_bytes, 1, work["cluster_bytes"])
            + (0 if not database_archive_bytes else _allocated(database_archive_bytes, 1, work["cluster_bytes"]))
        ),
    )
    if download_cache is not None:
        cache_volume = _volume(download_cache)
        cache_bytes = ((0 if reuse_installed_app else app_archive_bytes)
                       + (0 if reuse_installed_database else database_archive_bytes or target_database_bytes))
        _add(transport, cache_volume, _allocated(cache_bytes, 2, cache_volume["cluster_bytes"]))
        _add(update, cache_volume, _allocated(cache_bytes, 2, cache_volume["cluster_bytes"]))
        volumes = [work, active, data, cache_volume]
    else:
        volumes = [work, active, data]
    # Staging remains in the workspace while a verified replacement is built
    # beside the App and the DB subtransaction holds its own staging/rollback.
    _add(
        update,
        work,
        (0 if reuse_installed_app else _allocated(app_unpacked_bytes, app_unpacked_files, work["cluster_bytes"]))
        + (
            0
            if reuse_installed_database
            else _allocated(staged_database_bytes, 1, work["cluster_bytes"])
        ),
    )
    _add(
        update,
        active,
        (0 if reuse_installed_app else _allocated(app_unpacked_bytes, app_unpacked_files, active["cluster_bytes"]))
        + (
            0
            if reuse_installed_database
            else _allocated(rollback_database_bytes, 1, active["cluster_bytes"])
        ),
    )
    _add(
        update,
        data,
        (
            0
            if reuse_installed_database
            else _allocated(staged_database_bytes, 1, data["cluster_bytes"])
            + _allocated(rollback_database_bytes, 1, data["cluster_bytes"])
        ),
    )
    return _finish(volumes, {"transport": transport, "update": update})


def preflight_after_stage(
    *,
    active_app_root: Path,
    data_dir: Path,
    app_unpacked_bytes: int,
    app_unpacked_files: int,
    target_database_bytes: int,
    current_database_bytes: int,
    reuse_installed_database: bool = False,
    reuse_installed_app: bool = False,
) -> dict[str, Any]:
    """Recheck remaining duplication after signed artifacts already exist."""

    if type(reuse_installed_database) is not bool or type(reuse_installed_app) is not bool:
        raise ReleasePairStorageError("release_pair_storage_input_invalid")
    target_bytes = 0 if reuse_installed_database else target_database_bytes
    rollback_bytes = 0 if reuse_installed_database else current_database_bytes
    active = _volume(active_app_root.parent)
    data = _volume(data_dir)
    update: dict[str, int] = {}
    _add(
        update,
        active,
        (0 if reuse_installed_app else _allocated(app_unpacked_bytes, app_unpacked_files, active["cluster_bytes"]))
        + (
            0
            if reuse_installed_database
            else _allocated(rollback_bytes, 1, active["cluster_bytes"])
        ),
    )
    _add(
        update,
        data,
        (
            0
            if reuse_installed_database
            else _allocated(target_bytes, 1, data["cluster_bytes"])
            + _allocated(rollback_bytes, 1, data["cluster_bytes"])
        ),
    )
    return _finish([active, data], {"update": update})


__all__ = [
    "ReleasePairStorageError",
    "directory_shape",
    "preflight_after_stage",
    "preflight_before_download",
]
