#!/usr/bin/env python3
"""Build a fail-closed archive/delete plan for the repository ``.tmp`` tree.

The command is intentionally read-only with respect to ``.tmp``.  It inventories
regular files without following reparse points, hashes their exact bytes, and
classifies every readable file as either:

* archive one content-unique representative and delete all temporary copies; or
* delete because an exact formal/Git copy exists or the file is disposable
  cache, lock, or shallow-clone metadata.

Unreadable paths, reparse points, and other special filesystem entries are
recorded as blockers.  A generated draft containing blockers is accepted for
review by ``validate_plan(..., allow_blocked_draft=True)`` but is rejected by
``archive_repository_history.py`` until access is normalized, blockers are
removed, and this generator is rerun.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.maintenance import archive_repository_history as archive_history

CHUNK_SIZE = 4 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
PLAN_PATH = Path("benchmarks/archive-manifests/2026-10-21-tmp-cleanup-plan.json")
INVENTORY_PATH = Path("benchmarks/archive-manifests/2026-10-21-tmp-cleanup-inventory.json")
RECOVERY_PATH = Path("docs/pm/audits/2026-10-21-tmp-cleanup-recovery.md")
ARCHIVE_ROOT = "archives/2026-10-21-tmp-cleanup"
RESULT_MANIFEST = "benchmarks/archive-manifests/2026-10-21-tmp-cleanup-result.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        stream.write(payload)
        temp_path = Path(stream.name)
    os.replace(temp_path, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def workspace_relative(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace).as_posix()


def error_record(workspace: Path, path: Path, kind: str, exc: OSError | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {"path": workspace_relative(workspace, path), "kind": kind}
    if exc is not None:
        record["error_type"] = type(exc).__name__
        record["errno"] = exc.errno
        if getattr(exc, "winerror", None) is not None:
            record["winerror"] = exc.winerror
    return record


def scan_tmp(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Inventory regular files without following links/junctions.

    Every inability to enumerate, stat, or hash an entry becomes a blocker rather
    than silently shrinking the inventory.
    """

    root = workspace / ".tmp"
    if not root.is_dir():
        raise FileNotFoundError(root)
    pending = [root]
    files: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except OSError as exc:
            blockers.append(error_record(workspace, directory, "directory_unreadable", exc))
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                status = entry.stat(follow_symlinks=False)
            except OSError as exc:
                blockers.append(error_record(workspace, path, "entry_unreadable", exc))
                continue
            attributes = getattr(status, "st_file_attributes", 0)
            if attributes & FILE_ATTRIBUTE_REPARSE_POINT or stat.S_ISLNK(status.st_mode):
                record = error_record(workspace, path, "reparse_point")
                try:
                    record["target"] = os.readlink(path)
                except OSError:
                    record["target"] = None
                blockers.append(record)
                continue
            if stat.S_ISDIR(status.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(status.st_mode):
                blockers.append(error_record(workspace, path, "special_filesystem_entry"))
                continue
            try:
                digest = sha256_file(path)
            except OSError as exc:
                blockers.append(error_record(workspace, path, "file_unreadable", exc))
                continue
            files.append(
                {
                    "path": workspace_relative(workspace, path),
                    "bytes": status.st_size,
                    "sha256": digest,
                }
            )
    files.sort(key=lambda item: item["path"])
    blockers.sort(key=lambda item: (item["path"], item["kind"]))
    return files, blockers


def run_git(workspace: Path, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd or workspace,
        capture_output=True,
        check=check,
    )


def tracked_blob_equivalents(
    workspace: Path, candidate_sizes: set[int]
) -> dict[str, list[dict[str, Any]]]:
    """Hash only Git index blobs whose byte size can match a temporary file."""

    raw = run_git(workspace, "ls-files", "--stage", "-z").stdout
    paths_by_oid: dict[str, list[str]] = defaultdict(list)
    for item in raw.split(b"\0"):
        if not item:
            continue
        metadata, encoded_path = item.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        if stage == "0" and mode != "160000":
            paths_by_oid[oid].append(encoded_path.decode("utf-8", errors="surrogateescape"))

    oids = sorted(paths_by_oid)
    checked = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        cwd=workspace,
        input=("\n".join(oids) + "\n").encode("ascii"),
        capture_output=True,
        check=True,
    )
    candidate_oids: list[str] = []
    for line in checked.stdout.decode("ascii").splitlines():
        oid, object_type, object_size = line.split()
        if object_type == "blob" and int(object_size) in candidate_sizes:
            candidate_oids.append(oid)

    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not candidate_oids:
        return by_sha
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=workspace,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(("\n".join(candidate_oids) + "\n").encode("ascii"))
    process.stdin.close()
    for requested_oid in candidate_oids:
        header = process.stdout.readline().decode("ascii").rstrip("\n")
        oid, object_type, object_size = header.split()
        if oid != requested_oid or object_type != "blob":
            raise RuntimeError(f"unexpected git cat-file response: {header}")
        remaining = int(object_size)
        digest = hashlib.sha256()
        while remaining:
            chunk = process.stdout.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise RuntimeError(f"truncated git blob: {oid}")
            digest.update(chunk)
            remaining -= len(chunk)
        if process.stdout.read(1) != b"\n":
            raise RuntimeError(f"missing git batch delimiter after blob: {oid}")
        for tracked_path in sorted(paths_by_oid[oid]):
            by_sha[digest.hexdigest()].append({"path": tracked_path, "git_blob": oid})
    stderr = process.stderr.read() if process.stderr is not None else b""
    if process.wait() != 0:
        raise RuntimeError(f"git cat-file failed: {stderr.decode(errors='replace')}")
    return by_sha


def formal_database_equivalents(
    workspace: Path, candidate_sizes: set[int]
) -> dict[str, list[dict[str, Any]]]:
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    root = workspace / "data" / "backups"
    for path in sorted(root.rglob("*.db"), key=lambda item: item.as_posix()):
        size = path.stat().st_size
        if size not in candidate_sizes:
            continue
        by_sha[sha256_file(path)].append(
            {"path": workspace_relative(workspace, path), "bytes": size}
        )
    return by_sha


def shallow_clone_metadata(workspace: Path) -> dict[str, Any]:
    clone = workspace / ".tmp" / "upstream-ref-audit"
    if not (clone / ".git").is_dir():
        return {"present": False}
    remotes_raw = run_git(workspace, "remote", cwd=clone).stdout.decode("utf-8", errors="replace")
    remotes: list[dict[str, Any]] = []
    for name in sorted(filter(None, remotes_raw.splitlines())):
        configured = run_git(workspace, "remote", "get-url", name, cwd=clone, check=False)
        configured_url = configured.stdout.decode("utf-8", errors="replace").strip() or None
        inferred_url = name if "://" in name else None
        remotes.append(
            {
                "name": name,
                "configured_url": configured_url,
                "source_url": configured_url or inferred_url,
            }
        )
    refs_raw = run_git(workspace, "show-ref", cwd=clone, check=False).stdout.decode(
        "ascii", errors="replace"
    )
    refs = [
        {"commit": line.split()[0], "ref": line.split()[1]}
        for line in refs_raw.splitlines()
        if len(line.split()) == 2
    ]
    return {
        "present": True,
        "path": ".tmp/upstream-ref-audit",
        "partial_clone": (clone / ".git" / "objects" / "info" / "alternates").exists()
        or b"partialclonefilter" in (clone / ".git" / "config").read_bytes(),
        "remotes": remotes,
        "refs": sorted(refs, key=lambda item: item["ref"]),
    }


def is_lock(record: dict[str, Any]) -> bool:
    return Path(record["path"]).name.casefold().endswith(".lock")


def is_obvious_test_cache(record: dict[str, Any]) -> bool:
    parts = [part.casefold() for part in Path(record["path"]).parts]
    if any(part in {"__pycache__", ".pytest_cache"} for part in parts):
        return True
    if any(part.startswith("pytest-of-") for part in parts):
        return True
    # Non-database payloads below explicitly named pytest work roots are test
    # scratch.  Databases are classified before this predicate and therefore
    # still receive the conservative database-preservation treatment.
    tmp_root = parts[1] if len(parts) > 1 else ""
    return (
        tmp_root == "pytest"
        or tmp_root.startswith("pytest-")
        or tmp_root.endswith(("-pytest", "-tests"))
        or "-pytest-" in tmp_root
        or "-tests-" in tmp_root
    )


def summarize_records(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    selected = list(records)
    return {"files": len(selected), "bytes": sum(item["bytes"] for item in selected)}


def classify(
    records: list[dict[str, Any]],
    git_by_sha: dict[str, list[dict[str, Any]]],
    formal_db_by_sha: dict[str, list[dict[str, Any]]],
    clone_metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return archive-tool groups and an evidence-rich per-file ledger."""

    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_sha[record["sha256"]].append(record)
    for values in by_sha.values():
        values.sort(key=lambda item: item["path"])

    groups: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    classified: set[str] = set()

    def add_delete_group(
        group_id: str,
        kind: str,
        reason: str,
        selected: list[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
        evidence_for: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        if not selected:
            return
        paths = sorted(item["path"] for item in selected)
        groups.append(
            {
                "id": group_id,
                "kind": kind,
                "label": group_id,
                "reason": reason,
                "disposition": "delete_unreferenced",
                "archive_sources": [],
                "delete_sources": paths,
                "metadata": metadata or {},
            }
        )
        for item in selected:
            if item["path"] in classified:
                raise RuntimeError(f"duplicate classification: {item['path']}")
            classified.add(item["path"])
            ledger_item = {
                **item,
                "disposition": "delete_without_archive",
                "reason": group_id,
            }
            if evidence_for is not None:
                ledger_item["equivalence_evidence"] = evidence_for(item)
            ledger.append(ledger_item)

    clone_records = [item for item in records if item["path"].startswith(".tmp/upstream-ref-audit/")]
    add_delete_group(
        "tmp-upstream-shallow-clone",
        "reconstructible_git_metadata",
        "Record remote and live/main commit identities, then remove the reconstructible partial clone.",
        clone_records,
        metadata=clone_metadata,
    )

    database_records = [
        item
        for item in records
        if item["path"] not in classified and Path(item["path"]).suffix.casefold() == ".db"
    ]
    formal_duplicates = [item for item in database_records if item["sha256"] in formal_db_by_sha]
    add_delete_group(
        "tmp-formal-database-duplicates",
        "database",
        "Exact bytes already exist in data/backups; keep the formal backup and delete temporary copies.",
        formal_duplicates,
        evidence_for=lambda item: formal_db_by_sha[item["sha256"]],
    )

    remaining_db_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in database_records:
        if item["path"] not in classified:
            remaining_db_by_sha[item["sha256"]].append(item)
    for digest in sorted(remaining_db_by_sha):
        copies = sorted(remaining_db_by_sha[digest], key=lambda item: item["path"])
        representative = copies[0]
        groups.append(
            {
                "id": f"tmp-database-{digest[:12]}",
                "kind": "database",
                "label": f"temporary database {digest[:12]}",
                "reason": "No exact formal backup exists; archive one byte-identical representative and delete all temporary copies.",
                "archive": f"databases/{digest[:12]}.zip",
                "archive_sources": [representative["path"]],
                "delete_sources": [item["path"] for item in copies],
                "require_same_content": True,
                "metadata": {
                    "sha256": digest,
                    "bytes_per_copy": representative["bytes"],
                    "copy_count": len(copies),
                    "representative": representative["path"],
                },
            }
        )
        for item in copies:
            classified.add(item["path"])
            ledger.append(
                {
                    **item,
                    "disposition": "archive_representative_then_delete",
                    "reason": "unique_database_content",
                    "archive_representative": representative["path"],
                }
            )

    locks = [item for item in records if item["path"] not in classified and is_lock(item)]
    add_delete_group(
        "tmp-lock-files",
        "lock",
        "Stale one-byte runtime/test locks have no recovery value.",
        locks,
    )
    caches = [
        item for item in records if item["path"] not in classified and is_obvious_test_cache(item)
    ]
    add_delete_group(
        "tmp-test-caches",
        "test_cache",
        "Disposable pytest and Python cache payloads are reproducible and are not historical evidence.",
        caches,
    )
    git_duplicates = [
        item for item in records if item["path"] not in classified and item["sha256"] in git_by_sha
    ]
    add_delete_group(
        "tmp-git-exact-duplicates",
        "tracked_duplicate",
        "Exact bytes already exist as Git index blobs; the ledger records the tracked recovery source.",
        git_duplicates,
        evidence_for=lambda item: git_by_sha[item["sha256"]],
    )

    historical = [item for item in records if item["path"] not in classified]
    historical_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in historical:
        historical_by_sha[item["sha256"]].append(item)
    upstream_representatives: list[dict[str, Any]] = []
    upstream_delete: list[dict[str, Any]] = []
    other_representatives: list[dict[str, Any]] = []
    other_delete: list[dict[str, Any]] = []
    representatives: dict[str, str] = {}
    for digest in sorted(historical_by_sha):
        copies = sorted(historical_by_sha[digest], key=lambda item: item["path"])
        upstream = [item for item in copies if item["path"].startswith(".tmp/upstream-samples/")]
        representative = upstream[0] if upstream else copies[0]
        representatives[digest] = representative["path"]
        if upstream:
            upstream_representatives.append(representative)
            upstream_delete.extend(copies)
        else:
            other_representatives.append(representative)
            other_delete.extend(copies)

    def add_archive_group(
        group_id: str,
        label: str,
        archive_name: str,
        representatives_list: list[dict[str, Any]],
        delete_list: list[dict[str, Any]],
    ) -> None:
        if not delete_list:
            return
        groups.append(
            {
                "id": group_id,
                "kind": "historical_process_material",
                "label": label,
                "reason": "Preserve one exact copy of every content-unique historical item before removing the temporary workspace.",
                "archive": archive_name,
                "archive_sources": sorted(item["path"] for item in representatives_list),
                "delete_sources": sorted(item["path"] for item in delete_list),
                "metadata": {
                    "unique_contents": len(representatives_list),
                    "source_files": len(delete_list),
                    "representative_bytes": sum(item["bytes"] for item in representatives_list),
                    "source_bytes": sum(item["bytes"] for item in delete_list),
                },
            }
        )
        for item in delete_list:
            if item["path"] in classified:
                raise RuntimeError(f"duplicate classification: {item['path']}")
            classified.add(item["path"])
            ledger.append(
                {
                    **item,
                    "disposition": "archive_representative_then_delete",
                    "reason": group_id,
                    "archive_representative": representatives[item["sha256"]],
                }
            )

    add_archive_group(
        "tmp-upstream-source-samples",
        "upstream source samples",
        "sources/upstream-samples.zip",
        upstream_representatives,
        upstream_delete,
    )
    add_archive_group(
        "tmp-unique-process-material",
        "unique scripts, reports, screenshots, packages, and process evidence",
        "process/unique-process-material.zip",
        other_representatives,
        other_delete,
    )

    all_paths = {item["path"] for item in records}
    if classified != all_paths:
        missing = sorted(all_paths - classified)
        extra = sorted(classified - all_paths)
        raise RuntimeError(f"classification closure failed; missing={missing}, extra={extra}")
    ledger.sort(key=lambda item: item["path"])
    return groups, ledger


def recovery_markdown(
    plan: dict[str, Any], inventory: dict[str, Any]
) -> str:
    summary = inventory["summary"]
    clone = inventory["shallow_clone"]
    lines = [
        "# `.tmp` 清理与恢复说明",
        "",
        "> 这是计划草案的恢复契约。本轮没有创建归档、删除源文件或修改 ACL。",
        "",
        "## 当前状态",
        "",
        f"- 可读文件：{summary['readable']['files']} 个，{summary['readable']['bytes']} 字节。",
        f"- 待归档代表文件：{summary['archive_representatives']['files']} 个，{summary['archive_representatives']['bytes']} 字节。",
        f"- 归档/删除计划覆盖：{summary['planned_delete']['files']} 个，{summary['planned_delete']['bytes']} 字节。",
        f"- ACL/读取未决路径：{summary['blocked_paths']} 个。",
        f"- 延后清理的 reparse point：{summary['deferred_reparse_paths']} 个。",
        f"- 执行状态：`{inventory['execution_status']}`。",
        "",
        "只要 `blocked_inventory_paths` 非空，归档工具会拒绝 prepare。先归一化这些路径的访问权限并检查内容，再重新运行计划生成器；不要手改 JSON 绕过门禁。junction 由 `deferred_reparse_paths` 精确登记，安全 walker 不会进入其外部目标。",
        "",
        "## 安全执行顺序",
        "",
        "1. 对清单中的拒绝访问目录归一化 ACL；不要遍历、修改或删除 reparse point 的外部目标。",
        "2. 重新运行 `python scripts/maintenance/build_tmp_cleanup_plan.py`，确认 ACL/读取未决路径为 0，并审查重新生成的计划、逐文件清单和 `deferred_reparse_paths`。",
        f"3. 准备归档：`python scripts/maintenance/archive_repository_history.py --plan {PLAN_PATH.as_posix()}`。",
        f"4. 只读复核：`python scripts/maintenance/archive_repository_history.py --plan {PLAN_PATH.as_posix()} --verify`。",
        "5. 通过复核后再运行带 `--delete` 的同一命令。该命令只删除计划中的普通文件。",
        "6. 归档工具会从最深层移除普通空目录，但保留 reparse point。核对其路径和已记录目标后，仅删除 `post_delete_remove_reparse_paths` 中的链接目录项，不得删除或遍历目标；最后再移除空的 `.tmp`。",
        "",
        "## 恢复规则",
        "",
        f"归档结果清单将写入 `{plan['manifest_path']}`。恢复归档内容时，从仓库根目录解压每个 ZIP；成员名保留原始 `.tmp/...` 路径。先核对结果清单中的 ZIP SHA-256，再解压。",
        "",
        "- `archive_representative_then_delete`：从相应 ZIP 恢复代表文件；同哈希的其他临时副本可按逐文件清单的 `archive_representative` 复制重建。",
        "- `tmp-formal-database-duplicates`：逐文件清单列出 `data/backups` 中的精确副本，可复制回原路径。",
        "- `tmp-git-exact-duplicates`：逐文件清单列出 Git blob 与跟踪路径，可从该 blob 或路径复制重建。",
        "- 测试缓存和锁：有意不归档，按原测试命令重新生成。",
        "",
        "## 上游浅克隆来源",
        "",
    ]
    if clone.get("present"):
        for remote in clone.get("remotes", []):
            lines.append(
                f"- remote `{remote['name']}`：`{remote.get('source_url') or '未配置 URL'}`"
            )
        for ref in clone.get("refs", []):
            lines.append(f"- `{ref['ref']}`：`{ref['commit']}`")
        lines.extend(
            [
                "",
                "该目录仅保存可重建的 partial-clone Git 元数据。重建时从记录的 source URL 新建 `--filter=blob:none --no-checkout` 克隆，再核对 live/main 的提交；不要把旧 `.git` 对象包当成项目资料归档。",
            ]
        )
    else:
        lines.append("- 当前没有检测到 `.tmp/upstream-ref-audit/.git`。")
    lines.extend(["", "## 完整性边界", ""])
    lines.append(
        "逐文件清单只证明当前账户能够读取的普通文件。ACL 重扫完成前，它不能证明 `.tmp` 全树闭包，因此当前草案不可用于删除。"
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (default: inferred from this script)",
    )
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    tmp_root = (workspace / ".tmp").resolve(strict=False)
    if not tmp_root.is_relative_to(workspace) or tmp_root == workspace:
        raise RuntimeError("unsafe .tmp root")

    records, blockers = scan_tmp(workspace)
    candidate_sizes = {item["bytes"] for item in records}
    database_sizes = {
        item["bytes"] for item in records if Path(item["path"]).suffix.casefold() == ".db"
    }
    git_by_sha = tracked_blob_equivalents(workspace, candidate_sizes)
    formal_db_by_sha = formal_database_equivalents(workspace, database_sizes)
    clone_metadata = shallow_clone_metadata(workspace)
    groups, ledger = classify(records, git_by_sha, formal_db_by_sha, clone_metadata)

    deferred_reparse_paths = sorted(
        {item["path"] for item in blockers if item["kind"] == "reparse_point"}
    )
    blocked_paths = sorted(
        {item["path"] for item in blockers if item["kind"] != "reparse_point"}
    )
    all_delete_paths = [path for group in groups for path in group["delete_sources"]]
    if len(all_delete_paths) != len(set(all_delete_paths)):
        raise RuntimeError("delete source occurs in multiple plan groups")
    if set(all_delete_paths) != {item["path"] for item in records}:
        raise RuntimeError("plan groups do not exactly cover the readable inventory")
    archive_sources = [path for group in groups for path in group.get("archive_sources", [])]
    record_by_path = {item["path"]: item for item in records}
    generated_at = datetime.now(UTC).isoformat()
    if blocked_paths:
        execution_status = "blocked_pending_acl_reinventory"
    elif deferred_reparse_paths:
        execution_status = "review_ready_with_deferred_reparse_cleanup"
    else:
        execution_status = "review_ready"
    plan = {
        "schema_version": 1,
        "archive_id": "2026-10-21-tmp-cleanup",
        "archive_root": ARCHIVE_ROOT,
        "manifest_path": RESULT_MANIFEST,
        "allowed_source_roots": [".tmp"],
        "inventory_roots": [".tmp"],
        "retained_sources": [],
        "blocked_inventory_paths": blocked_paths,
        "deferred_reparse_paths": deferred_reparse_paths,
        "post_delete_remove_reparse_paths": deferred_reparse_paths,
        "draft_status": execution_status,
        "generated_at": generated_at,
        "inventory_manifest": INVENTORY_PATH.as_posix(),
        "recovery_guide": RECOVERY_PATH.as_posix(),
        "groups": groups,
    }
    archive_history.validate_plan(plan, allow_blocked_draft=True)

    disposition_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ledger:
        disposition_groups[item["disposition"]].append(item)
    inventory = {
        "schema_version": 1,
        "inventory_id": "2026-10-21-tmp-cleanup",
        "generated_at": generated_at,
        "execution_status": execution_status,
        "policy": {
            "inventory_root": ".tmp",
            "follow_reparse_points": False,
            "archive_unique_database_hashes_without_formal_exact_copy": True,
            "archive_unique_noncache_process_material": True,
            "direct_delete_only_with_equivalent_or_reproducible_classification": True,
            "blocked_paths_must_be_rescanned_before_prepare": True,
        },
        "summary": {
            "readable": summarize_records(records),
            "archive_representatives": summarize_records(record_by_path[path] for path in archive_sources),
            "planned_delete": summarize_records(record_by_path[path] for path in all_delete_paths),
            "blocked_paths": len(blocked_paths),
            "deferred_reparse_paths": len(deferred_reparse_paths),
            "database_files": summarize_records(
                item for item in records if Path(item["path"]).suffix.casefold() == ".db"
            ),
            "database_unique_hashes": len(
                {
                    item["sha256"]
                    for item in records
                    if Path(item["path"]).suffix.casefold() == ".db"
                }
            ),
            "database_archived_unique_hashes": len(
                [group for group in groups if group["id"].startswith("tmp-database-")]
            ),
            "by_disposition": {
                name: summarize_records(items)
                for name, items in sorted(disposition_groups.items())
            },
        },
        "shallow_clone": clone_metadata,
        "blocked_inventory": [
            item for item in blockers if item["kind"] != "reparse_point"
        ],
        "deferred_reparse_inventory": [
            item for item in blockers if item["kind"] == "reparse_point"
        ],
        "files": ledger,
    }
    atomic_write_json(workspace / PLAN_PATH, plan)
    atomic_write_json(workspace / INVENTORY_PATH, inventory)
    atomic_write(workspace / RECOVERY_PATH, recovery_markdown(plan, inventory))
    print(
        json.dumps(
            {
                "status": execution_status,
                "readable_files": len(records),
                "readable_bytes": sum(item["bytes"] for item in records),
                "blocked_paths": len(blocked_paths),
                "deferred_reparse_paths": len(deferred_reparse_paths),
                "archive_representatives": len(archive_sources),
                "database_archived_unique_hashes": inventory["summary"][
                    "database_archived_unique_hashes"
                ],
                "plan": PLAN_PATH.as_posix(),
                "inventory": INVENTORY_PATH.as_posix(),
                "recovery": RECOVERY_PATH.as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
