"""上游数据拉取器：负责上游开源仓库的浅克隆与更新检测。"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from genshin_db.upstream_version import HISTORY_DEPTH as VERSION_HISTORY_DEPTH
from genshin_db.upstream_version import inspect as inspect_version_evidence

UPSTREAM_URL = "https://gitlab.com/GuraFoundation/YuanShenResources"
BRANCH = "main"
HISTORY_DEPTH = VERSION_HISTORY_DEPTH


class FetcherError(Exception):
    """git 或网络失败时抛出。"""


@dataclass
class FetchResult:
    repo_dir: Path
    commit: str
    changed: bool
    snapshot_updated_at: str | None = None
    version_evidence: dict | None = None


def _result(repo_dir: Path, commit: str, changed: bool) -> FetchResult:
    evidence = inspect_version_evidence(repo_dir, commit)
    return FetchResult(
        repo_dir=repo_dir,
        commit=commit,
        changed=changed,
        snapshot_updated_at=evidence.get("snapshot_updated_at"),
        version_evidence=evidence,
    )


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except FileNotFoundError as e:
        raise FetcherError("未找到 git 命令，请先安装 git") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise FetcherError(f"git {' '.join(args)} 执行失败: {detail}") from e
    return proc.stdout.strip()


def _canonical_remote(value: str) -> str:
    return value.strip().rstrip("/").removesuffix(".git")


def _validate_managed_cache(repo_dir: Path) -> None:
    root = Path(_run_git(["rev-parse", "--show-toplevel"], cwd=repo_dir)).resolve()
    if root != repo_dir.resolve():
        raise FetcherError("上游缓存路径必须是Git工作树根目录")
    origin = _run_git(["remote", "get-url", "origin"], cwd=repo_dir)
    if _canonical_remote(origin) != _canonical_remote(UPSTREAM_URL):
        raise FetcherError("上游缓存origin与固定权威仓库不符")
    if _run_git(
        ["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo_dir,
    ):
        raise FetcherError("上游缓存有未提交或未跟踪文件，拒绝覆盖或混入构建")


def ensure_upstream(cache_dir: Path) -> FetchResult:
    repo_dir = cache_dir / "repo"
    if not (repo_dir / ".git").exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        # The repository is several GiB even at one revision.  Version
        # resolution needs commit/tree history, not every historical blob, so
        # keep the cache shallow and partial instead of multiplying disk use by
        # HISTORY_DEPTH.
        _run_git([
            "clone",
            "--filter=blob:none",
            "--depth",
            str(HISTORY_DEPTH),
            "--branch",
            BRANCH,
            UPSTREAM_URL,
            str(repo_dir),
        ])
        commit = _run_git(["rev-parse", "HEAD"], cwd=repo_dir)
        return _result(repo_dir, commit, True)
    _validate_managed_cache(repo_dir)
    local = _run_git(["rev-parse", "HEAD"], cwd=repo_dir)
    _run_git(
        [
            "fetch",
            "--filter=blob:none",
            "--depth",
            str(HISTORY_DEPTH),
            "origin",
            BRANCH,
        ],
        cwd=repo_dir,
    )
    remote = _run_git(["rev-parse", "FETCH_HEAD"], cwd=repo_dir)
    if remote == local:
        return _result(repo_dir, local, False)
    _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=repo_dir)
    return _result(repo_dir, remote, True)


def inspect_pinned_upstream(repo_dir: Path, expected_commit: str) -> FetchResult:
    """Inspect an existing checkout without fetch, reset, or network access."""
    if not isinstance(expected_commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", expected_commit):
        raise ValueError("expected_commit必须为精确40位Git提交SHA")
    repo_dir = Path(repo_dir).resolve()
    if not repo_dir.is_dir():
        raise FetcherError("离线上游目录不存在")
    root = Path(_run_git(["rev-parse", "--show-toplevel"], cwd=repo_dir)).resolve()
    if root != repo_dir:
        raise FetcherError("离线上游路径必须是Git工作树根目录")
    commit = _run_git(["rev-parse", "HEAD"], cwd=repo_dir)
    if commit != expected_commit.lower():
        raise FetcherError("离线上游HEAD与expected_commit不符")
    if _run_git(["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"], cwd=repo_dir):
        raise FetcherError("离线上游工作树有未提交或未跟踪文件")
    return _result(repo_dir, commit, False)


def verify_pinned_sources(repo_dir: Path, expected_commit: str, manifest: dict) -> dict:
    """Every selected source must match a regular Git blob at the pinned commit.

    Git status alone omits ignored and skip-worktree files. Exact blob hashing
    also rejects those gaps. The only permitted checkout-byte equivalence is
    CRLF to LF with an exact matching Git blob; no arbitrary filter is executed
    to transform or accept the selected source bytes.
    """
    inspected = inspect_pinned_upstream(repo_dir, expected_commit)
    autocrlf = _run_git(["config", "--default", "false", "--get", "core.autocrlf"], cwd=repo_dir).lower()
    if autocrlf not in ("true", "false", "input"):
        raise FetcherError("离线上游core.autocrlf值无效")
    entries = {}
    for entry in _run_git(["ls-tree", "-rz", "--full-tree", inspected.commit], cwd=repo_dir).split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", 1)
        mode, kind, blob = metadata.split()
        entries[path] = (mode, kind, blob)
    converted = []
    for relative, spec in manifest["files"].items():
        expected = entries.get(relative)
        if expected is None or expected[0] not in ("100644", "100755") or expected[1] != "blob":
            raise FetcherError(f"必需来源不是固定commit中的普通文件: {relative}")
        path = Path(repo_dir) / relative
        if not path.resolve().is_relative_to(Path(repo_dir).resolve()):
            raise FetcherError(f"来源路径越界: {relative}")
        payload = path.read_bytes()
        if len(payload) != spec["size_bytes"] or hashlib.sha256(payload).hexdigest() != spec["sha256"]:
            raise FetcherError(f"来源在核验期间改变: {relative}")
        header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
        if hashlib.sha1(header + payload, usedforsecurity=False).hexdigest() != expected[2]:
            canonical = payload.replace(b"\r\n", b"\n")
            header = b"blob " + str(len(canonical)).encode("ascii") + b"\0"
            if canonical == payload or hashlib.sha1(header + canonical, usedforsecurity=False).hexdigest() != expected[2]:
                raise FetcherError(f"来源字节与固定commit不符（仅允许CRLF→LF等价）: {relative}")
            converted.append(relative)
    return {"mode": "pinned-git-blob-v1", "commit": inspected.commit,
            "manifest_fingerprint": manifest["fingerprint"], "checked_files": len(manifest["files"]),
            "core_autocrlf": autocrlf, "line_ending_equivalence": "exact_or_crlf_to_lf_with_matching_git_blob",
            "crlf_equivalent_files": len(converted), "crlf_examples": converted[:10],
            "stored_source_bytes": "unchanged_checkout_bytes"}


def repo_head_message(repo_dir: Path) -> str:
    """读取 HEAD commit message（T9：上游在 message 中携带游戏版本，如 OSCBWin7.0.54_...）。"""
    return _run_git(["log", "-1", "--format=%s"], cwd=repo_dir)


def inspect_upstream_version(repo_dir: Path, expected_commit: str | None = None) -> dict:
    """Read structured version evidence without fetching or mutating the checkout."""
    return inspect_version_evidence(repo_dir, expected_commit)


if __name__ == "__main__":
    result = ensure_upstream(Path("data/upstream"))
    print(f"commit: {result.commit}")
    print(f"changed: {result.changed}")
