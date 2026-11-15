#!/usr/bin/env python3
"""Create verified local history archives and then remove their source files.

The plan and result manifest use workspace-relative paths.  Archives are expected
to live in an ignored local directory, while the result manifest is suitable for
Git.  Preparation never deletes sources.  Deletion revalidates the plan, every
archive, every archived member, and every source hash before removing anything.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHUNK_SIZE = 4 * 1024 * 1024


def sha256_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256_stream(stream)


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        stream.write(payload)
        temp_path = Path(stream.name)
    os.replace(temp_path, path)


def resolve_workspace_path(workspace: Path, relative: str, *, allowed_roots: Iterable[Path]) -> Path:
    candidate = (workspace / relative).resolve(strict=False)
    if Path(relative).is_absolute():
        raise ValueError(f"absolute source path is forbidden: {relative}")
    for root in allowed_roots:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    raise ValueError(f"source path escaped allowed roots: {relative}")


def relative_posix(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _is_link_or_reparse(path: Path, entry: os.DirEntry[str] | None = None) -> bool:
    """Return True for links/junctions without following their targets."""

    if entry is not None and entry.is_symlink():
        return True
    info = entry.stat(follow_symlinks=False) if entry is not None else path.lstat()
    if getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        return True
    if path.is_symlink():
        return True
    return bool(getattr(path, "is_junction", lambda: False)())


def scan_inventory_tree(
    root: Path,
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """Inventory a tree without crossing links, junctions, or reparse points.

    Returns regular files, ordinary subdirectories, deferred reparse paths, and
    inaccessible paths.  Inaccessible paths are reported rather than silently
    omitted so a full-coverage plan cannot archive or delete a partial tree.
    """

    files: list[Path] = []
    directories: list[Path] = []
    reparse_paths: list[Path] = []
    inaccessible_paths: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as stream:
                entries = sorted(stream, key=lambda item: item.name.casefold(), reverse=True)
        except PermissionError:
            inaccessible_paths.append(current)
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if _is_link_or_reparse(path, entry):
                    reparse_paths.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
                elif entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                    pending.append(path)
            except PermissionError:
                inaccessible_paths.append(path)
    return tuple(
        sorted(values, key=lambda path: path.as_posix())
        for values in (files, directories, reparse_paths, inaccessible_paths)
    )  # type: ignore[return-value]


def _validated_deferred_inventory_paths(
    workspace: Path,
    relative_paths: Iterable[str],
    inventory_roots: list[Path],
    *,
    label: str,
) -> set[str]:
    validated: set[str] = set()
    for relative in relative_paths:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"{label} path escaped workspace: {relative}")
        candidate = workspace / relative_path
        if not any(candidate.is_relative_to(root) for root in inventory_roots):
            raise ValueError(f"{label} path is outside inventory_roots: {relative}")
        validated.add(relative_path.as_posix())
    return validated


def source_record(workspace: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    line_count = 0
    last_byte = b""
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
            if path.suffix.lower() == ".jsonl":
                line_count += chunk.count(b"\n")
                last_byte = chunk[-1:]

    record = {
        "path": relative_posix(workspace, path),
        "bytes": stat.st_size,
        "sha256": digest.hexdigest(),
    }
    if path.suffix.lower() == ".jsonl":
        if stat.st_size and last_byte != b"\n":
            line_count += 1
        record["line_count"] = line_count
    return record


def validate_plan(
    plan: dict[str, Any], *, allow_blocked_draft: bool = False
) -> None:
    if plan.get("schema_version") != 1:
        raise ValueError("plan schema_version must be 1")
    required = ("archive_id", "archive_root", "manifest_path", "allowed_source_roots", "groups")
    for key in required:
        if not plan.get(key):
            raise ValueError(f"plan is missing {key}")
    ids = [group.get("id") for group in plan["groups"]]
    if any(not group_id for group_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("group ids must be non-empty and unique")

    has_inventory_roots = "inventory_roots" in plan
    has_retained_sources = "retained_sources" in plan
    if has_inventory_roots and not has_retained_sources:
        raise ValueError("retained_sources is required when inventory_roots is provided")
    if has_inventory_roots:
        inventory_roots = plan["inventory_roots"]
        if not isinstance(inventory_roots, list) or not inventory_roots:
            raise ValueError("inventory_roots must be a non-empty list")
        if len(inventory_roots) != len(set(inventory_roots)):
            raise ValueError("inventory_roots must be unique")
    if has_retained_sources:
        retained_sources = plan["retained_sources"]
        if not isinstance(retained_sources, list):
            raise ValueError("retained_sources must be a list")
        if len(retained_sources) != len(set(retained_sources)):
            raise ValueError("retained_sources must be unique")

    deferred_reparse_paths = plan.get("deferred_reparse_paths", [])
    if not isinstance(deferred_reparse_paths, list):
        raise TypeError("deferred_reparse_paths must be a list")
    if any(not isinstance(path, str) or not path for path in deferred_reparse_paths):
        raise ValueError("deferred_reparse_paths must contain non-empty strings")
    if len(deferred_reparse_paths) != len(set(deferred_reparse_paths)):
        raise ValueError("deferred_reparse_paths must be unique")
    if deferred_reparse_paths and not has_inventory_roots:
        raise ValueError("deferred_reparse_paths requires inventory_roots")

    blocked_inventory_paths = plan.get("blocked_inventory_paths", [])
    if not isinstance(blocked_inventory_paths, list):
        raise TypeError("blocked_inventory_paths must be a list")
    if any(not isinstance(path, str) or not path for path in blocked_inventory_paths):
        raise ValueError("blocked_inventory_paths must contain non-empty strings")
    if len(blocked_inventory_paths) != len(set(blocked_inventory_paths)):
        raise ValueError("blocked_inventory_paths must be unique")
    if blocked_inventory_paths and not allow_blocked_draft:
        raise ValueError(
            "plan has unresolved blocked inventory paths; normalize access/remove "
            "reparse points, regenerate the plan, and review it before archiving"
        )
    overlap = set(blocked_inventory_paths).intersection(deferred_reparse_paths)
    if overlap:
        raise ValueError(
            f"blocked_inventory_paths overlap deferred_reparse_paths: {sorted(overlap)}"
        )

    for group in plan["groups"]:
        if group.get("require_full_archive_coverage") is True:
            archive_sources = list(group.get("archive_sources", []))
            delete_sources = list(group.get("delete_sources", []))
            if archive_sources != delete_sources:
                raise ValueError(
                    "require_full_archive_coverage requires archive_sources "
                    f"to equal delete_sources: {group.get('id')}"
                )


def verify_zip(
    archive_path: Path,
    workspace: Path,
    expected_members: list[dict[str, Any]],
) -> None:
    if not archive_path.is_relative_to((workspace / "archives").resolve()):
        raise RuntimeError(f"archive escaped local archive root: {archive_path}")
    expected_by_name = {record["path"]: record for record in expected_members}
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if names != list(expected_by_name):
            raise RuntimeError(f"archive member order/content mismatch: {archive_path}")
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"CRC failure in {archive_path}: {bad_member}")
        for name, record in expected_by_name.items():
            info = archive.getinfo(name)
            if info.file_size != record["bytes"]:
                raise RuntimeError(f"size mismatch for {name} in {archive_path}")
            with archive.open(name, "r") as stream:
                actual_hash = sha256_stream(stream)
            if actual_hash != record["sha256"]:
                raise RuntimeError(f"hash mismatch for {name} in {archive_path}")


def validated_plan_paths(workspace: Path, plan: dict[str, Any]) -> tuple[list[Path], Path, Path]:
    workspace = workspace.resolve()
    archive_root = (workspace / plan["archive_root"]).resolve()
    required_archive_root = (workspace / "archives").resolve()
    if not archive_root.is_relative_to(required_archive_root):
        raise ValueError("archive_root must be below archives/")

    manifest_path = (workspace / plan["manifest_path"]).resolve()
    if not manifest_path.is_relative_to((workspace / "benchmarks").resolve()):
        raise ValueError("manifest_path must be below benchmarks/")

    allowed_roots = [(workspace / value).resolve() for value in plan["allowed_source_roots"]]
    for root in allowed_roots:
        if not root.is_relative_to(workspace):
            raise ValueError(f"allowed source root escaped workspace: {root}")
    return allowed_roots, archive_root, manifest_path


def validate_inventory_closure(
    workspace: Path,
    plan: dict[str, Any],
    allowed_roots: list[Path],
    *,
    allow_missing_delete_sources: bool,
) -> None:
    """Fail closed when an optional retained/delete inventory is incomplete.

    Preparation requires every current file below ``inventory_roots`` to be
    classified exactly once as retained or deleted.  Verification permits a
    planned delete source to be absent so an interrupted or completed deletion
    remains idempotent, but retained files must still exist and no unclassified
    file may have appeared.
    """

    if "inventory_roots" not in plan:
        return

    inventory_roots: list[Path] = []
    for relative in plan["inventory_roots"]:
        root = resolve_workspace_path(workspace, relative, allowed_roots=allowed_roots)
        if not root.is_dir():
            raise ValueError(f"inventory root is not a directory: {relative}")
        inventory_roots.append(root)

    delete_sources: list[str] = []
    for group in plan["groups"]:
        delete_sources.extend(group.get("delete_sources", []))
    if len(delete_sources) != len(set(delete_sources)):
        raise ValueError("inventory delete sources must be unique across groups")

    retained_sources = list(plan["retained_sources"])
    retained_set = set(retained_sources)
    delete_set = set(delete_sources)
    overlap = retained_set.intersection(delete_set)
    if overlap:
        raise ValueError(
            f"retained and delete sources overlap: {sorted(overlap)}"
        )

    expected_set = retained_set.union(delete_set)
    for relative in expected_set:
        source = resolve_workspace_path(workspace, relative, allowed_roots=allowed_roots)
        if not any(source.is_relative_to(root) for root in inventory_roots):
            raise ValueError(f"classified source is outside inventory_roots: {relative}")

    planned_reparse_set = _validated_deferred_inventory_paths(
        workspace,
        plan.get("deferred_reparse_paths", []),
        inventory_roots,
        label="deferred reparse",
    )
    planned_blocked_set = _validated_deferred_inventory_paths(
        workspace,
        plan.get("blocked_inventory_paths", []),
        inventory_roots,
        label="blocked inventory",
    )

    actual_set: set[str] = set()
    actual_reparse_set: set[str] = set()
    actual_blocked_set: set[str] = set()
    for root in inventory_roots:
        files, _directories, reparse_paths, inaccessible_paths = scan_inventory_tree(root)
        actual_set.update(relative_posix(workspace, path) for path in files)
        actual_reparse_set.update(relative_posix(workspace, path) for path in reparse_paths)
        actual_blocked_set.update(relative_posix(workspace, path) for path in inaccessible_paths)

    if actual_reparse_set != planned_reparse_set:
        raise RuntimeError(
            "inventory reparse point set mismatch; "
            f"unexpected={sorted(actual_reparse_set - planned_reparse_set)}, "
            f"missing={sorted(planned_reparse_set - actual_reparse_set)}"
        )
    if actual_blocked_set != planned_blocked_set:
        raise RuntimeError(
            "inventory inaccessible path set mismatch; "
            f"unexpected={sorted(actual_blocked_set - planned_blocked_set)}, "
            f"missing={sorted(planned_blocked_set - actual_blocked_set)}"
        )
    if actual_blocked_set:
        raise RuntimeError(
            f"inventory contains inaccessible path(s): {sorted(actual_blocked_set)}"
        )

    unexpected = actual_set.difference(expected_set)
    if unexpected:
        raise RuntimeError(f"inventory contains unclassified file(s): {sorted(unexpected)}")

    missing_retained = retained_set.difference(actual_set)
    if missing_retained:
        raise RuntimeError(f"retained inventory file(s) are missing: {sorted(missing_retained)}")

    if not allow_missing_delete_sources:
        missing_delete = delete_set.difference(actual_set)
        if missing_delete:
            raise RuntimeError(f"delete inventory file(s) are missing: {sorted(missing_delete)}")


def cleanup_empty_inventory_directories(workspace: Path, plan: dict[str, Any]) -> int:
    """Remove ordinary empty inventory directories without crossing links."""

    if "inventory_roots" not in plan:
        return 0
    removed = 0
    for relative in plan["inventory_roots"]:
        root = workspace / relative
        _files, directories, reparse_paths, inaccessible_paths = scan_inventory_tree(root)
        actual_reparse = {relative_posix(workspace, path) for path in reparse_paths}
        expected_reparse = set(plan.get("deferred_reparse_paths", []))
        if not actual_reparse.issubset(expected_reparse):
            raise RuntimeError(
                f"cleanup encountered unplanned reparse point(s): {sorted(actual_reparse - expected_reparse)}"
            )
        if inaccessible_paths:
            raise RuntimeError(
                "cleanup encountered inaccessible path(s): "
                f"{[relative_posix(workspace, path) for path in inaccessible_paths]}"
            )
        for directory in sorted(
            directories,
            key=lambda path: (len(path.parts), path.as_posix()),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError as exc:
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    raise
                continue
            removed += 1
    return removed


def prepare(workspace: Path, plan_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(plan)
    allowed_roots, archive_root, manifest_path = validated_plan_paths(workspace, plan)
    validate_inventory_closure(
        workspace,
        plan,
        allowed_roots,
        allow_missing_delete_sources=False,
    )
    archive_root.mkdir(parents=True, exist_ok=True)

    plan_hash = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    group_results: list[dict[str, Any]] = []
    all_delete_paths: set[str] = set()

    for group in plan["groups"]:
        group_id = group["id"]
        archive_sources = list(group.get("archive_sources", []))
        delete_sources = list(group.get("delete_sources", []))
        if len(delete_sources) != len(set(delete_sources)):
            raise ValueError(f"duplicate delete source in group {group_id}")
        duplicate_across_groups = all_delete_paths.intersection(delete_sources)
        if duplicate_across_groups:
            raise ValueError(f"sources occur in multiple groups: {sorted(duplicate_across_groups)}")
        all_delete_paths.update(delete_sources)
        if not set(archive_sources).issubset(delete_sources):
            raise ValueError(f"archive sources must also be delete sources: {group_id}")

        records_by_path: dict[str, dict[str, Any]] = {}
        print(f"[{group_id}] hashing {len(delete_sources)} source file(s)", flush=True)
        for relative in delete_sources:
            source = resolve_workspace_path(workspace, relative, allowed_roots=allowed_roots)
            if not source.is_file():
                raise FileNotFoundError(source)
            records_by_path[relative] = source_record(workspace, source)

        source_records = [records_by_path[value] for value in delete_sources]
        if group.get("require_same_content") and len({record["sha256"] for record in source_records}) > 1:
            raise RuntimeError(f"group does not contain identical files: {group_id}")

        archive_result: dict[str, Any] | None = None
        archive_relative = group.get("archive")
        if archive_relative:
            archive_path = (archive_root / archive_relative).resolve()
            if not archive_path.is_relative_to(archive_root):
                raise ValueError(f"archive escaped archive_root: {archive_relative}")
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archived_records = [records_by_path[value] for value in archive_sources]
            temp_path = archive_path.with_name(archive_path.name + ".tmp")
            temp_path.unlink(missing_ok=True)
            print(f"[{group_id}] writing {archive_path.relative_to(workspace)}", flush=True)
            with zipfile.ZipFile(
                temp_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                for record in archived_records:
                    archive.write(workspace / record["path"], arcname=record["path"])
            verify_zip(temp_path, workspace, archived_records)
            os.replace(temp_path, archive_path)
            archive_result = {
                "path": relative_posix(workspace, archive_path),
                "bytes": archive_path.stat().st_size,
                "sha256": sha256_file(archive_path),
                "members": archived_records,
            }
        elif group.get("disposition") != "delete_unreferenced":
            raise ValueError(f"unarchived group must be delete_unreferenced: {group_id}")

        group_results.append(
            {
                "id": group_id,
                "kind": group.get("kind", "unspecified"),
                "label": group.get("label"),
                "reason": group.get("reason"),
                "metadata": group.get("metadata"),
                "disposition": group.get("disposition", "archived"),
                "archive": archive_result,
                "sources": source_records,
            }
        )

    result = {
        "schema_version": 1,
        "archive_id": plan["archive_id"],
        "created_at": datetime.now(UTC).isoformat(),
        "plan_path": relative_posix(workspace, plan_path),
        "plan_sha256": plan_hash,
        "archive_root": relative_posix(workspace, archive_root),
        "groups": group_results,
    }
    atomic_write_json(manifest_path, result)
    print(f"wrote verified manifest: {manifest_path.relative_to(workspace)}", flush=True)
    return result


def verify_and_delete(
    workspace: Path,
    plan_path: Path,
    plan: dict[str, Any],
    *,
    delete_sources: bool = True,
) -> None:
    validate_plan(plan)
    allowed_roots, archive_root, manifest_path = validated_plan_paths(workspace, plan)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan_hash = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    if manifest.get("plan_sha256") != plan_hash:
        raise RuntimeError("plan changed after archive preparation")
    if manifest.get("plan_path") != relative_posix(workspace, plan_path):
        raise RuntimeError("manifest plan path mismatch")
    if manifest.get("archive_id") != plan["archive_id"]:
        raise RuntimeError("manifest archive id mismatch")
    expected_archive_root = relative_posix(workspace, (workspace / plan["archive_root"]).resolve())
    if manifest.get("archive_root") != expected_archive_root:
        raise RuntimeError("manifest archive root mismatch")

    plan_groups = plan["groups"]
    manifest_groups = manifest.get("groups", [])
    if len(manifest_groups) != len(plan_groups):
        raise RuntimeError("manifest group count mismatch")
    for planned, recorded in zip(plan_groups, manifest_groups, strict=True):
        if recorded.get("id") != planned["id"]:
            raise RuntimeError(f"manifest group id/order mismatch: {planned['id']}")
        expected_sources = list(planned.get("delete_sources", []))
        recorded_sources = [item.get("path") for item in recorded.get("sources", [])]
        if recorded_sources != expected_sources:
            raise RuntimeError(f"manifest source list mismatch: {planned['id']}")
        source_records_by_path = {
            item["path"]: item for item in recorded.get("sources", [])
        }
        if planned.get("require_same_content") and len(
            {item.get("sha256") for item in recorded.get("sources", [])}
        ) > 1:
            raise RuntimeError(f"manifest same-content assertion failed: {planned['id']}")

        expected_archive = planned.get("archive")
        recorded_archive = recorded.get("archive")
        if expected_archive:
            if not recorded_archive:
                raise RuntimeError(f"manifest archive missing: {planned['id']}")
            planned_archive_path = (archive_root / expected_archive).resolve()
            if not planned_archive_path.is_relative_to(archive_root):
                raise RuntimeError(f"planned archive escaped archive root: {planned['id']}")
            expected_archive_path = relative_posix(workspace, planned_archive_path)
            if recorded_archive.get("path") != expected_archive_path:
                raise RuntimeError(f"manifest archive path mismatch: {planned['id']}")
            expected_members = list(planned.get("archive_sources", []))
            recorded_members = [item.get("path") for item in recorded_archive.get("members", [])]
            if recorded_members != expected_members:
                raise RuntimeError(f"manifest archive member list mismatch: {planned['id']}")
            for member in recorded_archive.get("members", []):
                if member != source_records_by_path.get(member["path"]):
                    raise RuntimeError(
                        f"manifest source/archive record mismatch: {member['path']}"
                    )
        elif recorded_archive is not None:
            raise RuntimeError(f"unexpected manifest archive: {planned['id']}")

        expected_disposition = planned.get("disposition", "archived")
        if recorded.get("disposition") != expected_disposition:
            raise RuntimeError(f"manifest disposition mismatch: {planned['id']}")

    validate_inventory_closure(
        workspace,
        plan,
        allowed_roots,
        allow_missing_delete_sources=True,
    )

    records: list[dict[str, Any]] = []
    for group in manifest["groups"]:
        archive_record = group.get("archive")
        if archive_record:
            archive_path = (workspace / archive_record["path"]).resolve()
            if not archive_path.is_relative_to(archive_root):
                raise RuntimeError(f"archive escaped configured archive root: {archive_path}")
            if sha256_file(archive_path) != archive_record["sha256"]:
                raise RuntimeError(f"archive hash changed: {archive_path}")
            verify_zip(archive_path, workspace, archive_record["members"])
        elif group.get("disposition") != "delete_unreferenced":
            raise RuntimeError(f"unexpected unarchived group: {group['id']}")
        records.extend(group["sources"])

    print(f"revalidating {len(records)} source record(s)", flush=True)
    resolved_records: list[tuple[Path, dict[str, Any]]] = []
    already_deleted = 0
    for record in records:
        source = resolve_workspace_path(workspace, record["path"], allowed_roots=allowed_roots)
        if not source.is_file():
            if source.exists():
                raise RuntimeError(f"source is no longer a regular file: {source}")
            already_deleted += 1
            continue
        if source.stat().st_size != record["bytes"] or sha256_file(source) != record["sha256"]:
            raise RuntimeError(f"source changed after archive preparation: {source}")
        resolved_records.append((source, record))

    if not delete_sources:
        print(
            f"verified prepared archives and manifest; {len(resolved_records)} source file(s) "
            f"remain and {already_deleted} source file(s) are absent",
            flush=True,
        )
        return

    for source, _record in resolved_records:
        try:
            source.unlink()
        except PermissionError:
            os.chmod(source, source.stat().st_mode | stat.S_IWRITE)
            source.unlink()
    removed_directories = cleanup_empty_inventory_directories(workspace, plan)
    print(
        f"deleted {len(resolved_records)} verified source file(s); "
        f"{already_deleted} source file(s) were already absent from a prior verified deletion pass; "
        f"removed {removed_directories} ordinary empty directorie(s)",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify", action="store_true", help="verify prepared archives without deleting sources")
    action.add_argument("--delete", action="store_true", help="verify prepared archives and delete source files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = Path.cwd().resolve()
    plan_path = (workspace / args.plan).resolve()
    if not plan_path.is_relative_to(workspace):
        raise ValueError("plan path escaped workspace")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(plan)
    if args.delete or args.verify:
        verify_and_delete(workspace, plan_path, plan, delete_sources=args.delete)
    else:
        prepare(workspace, plan_path, plan)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - command-line diagnostic
        print(f"archive failed: {exc}", file=sys.stderr)
        raise
