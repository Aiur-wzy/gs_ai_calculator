"""Resolve an upstream data snapshot to reviewed, source-bound version evidence.

The upstream repository does not expose one consistently reliable machine-readable
version file.  A README value is therefore auxiliary only.  Primary evidence is
either the pinned commit title itself, a reviewed exact-snapshot profile, or the
nearest first-parent version commit whose configuration trees are byte-identical
to the selected snapshot.

``version_evidence_ready`` only qualifies this version-evidence record.  It is
not a database release verdict; source-layout, normalization and component
gates remain independent and may still reject the same snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

RESOLVER_VERSION = 1
HISTORY_DEPTH = 64
# ``CONFIGURATION_ROOTS`` are the roots whose byte identity may carry a version
# label across a TextMap-only commit. ``SOURCE_TREE_ROOTS`` separately records
# every top-level tree consumed by the database build.  Keeping the two sets
# distinct prevents a localization-only snapshot from losing its own identity
# while also preventing README text from becoming primary version evidence.
CONFIGURATION_ROOTS = ("BinOutput", "ExcelBinOutput")
SOURCE_TREE_ROOTS = (*CONFIGURATION_ROOTS, "TextMap")
SUPPORTED_TRACKS = frozenset({"OSCBWin", "OSRELWin", "CNCBWin", "CNRELWin"})
_VERSION = re.compile(
    r"(?P<raw>(?P<track>(?:OS|CN)(?:CB|REL)Win)(?P<version>\d+(?:\.\d+){2,3}))"
    r"(?=$|[_\s.-])"
)
_README_VERSION = re.compile(
    r"Current\s+version\s*:\s*"
    r"(?P<raw>(?P<track>(?:OS|CN)(?:CB|REL)Win)(?P<version>\d+(?:\.\d+){2,3}))",
    re.IGNORECASE,
)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_VERSION_ONLY = re.compile(r"\d+(?:\.\d+){2,3}\Z")
_RESOLVED_EVIDENCE_KEYS = frozenset(
    {
        "resolver_version",
        "status",
        "version_evidence_ready",
        "snapshot_commit",
        "snapshot_updated_at",
        "track",
        "version",
        "raw_version",
        "evidence_kind",
        "evidence_commit",
        "review_id",
        "configuration_trees",
        "source_trees",
        "readme_version",
        "issues",
        "evidence_fingerprint",
    }
)
_EVIDENCE_KINDS = frozenset(
    {
        "snapshot_commit_subject",
        "reviewed_source_tree_profile",
        "source_tree_equivalent_ancestor",
    }
)

# This profile is backed by the isolated 2026-10-02 source-tree audit.  The
# version-bearing parent is not present in the project's current depth-1 cache,
# so the exact snapshot and both configuration tree OIDs are pinned here.  A
# future snapshot never inherits this evidence by version number or prefix.
_REVIEWED_SNAPSHOTS: dict[str, dict[str, Any]] = {
    "342084292ba4c3c6ec2bc34c045de4de72ae8d53": {
        "review_id": "upstream-version-34208429-v1",
        "track": "OSCBWin",
        "version": "7.1.51",
        "version_commit": "34658855d518b2c504428a77d4676d3cb9af38c1",
        "trees": {
            "BinOutput": "f0656482590806f6e60ef83ea6b906fc51e3cb57",
            "ExcelBinOutput": "74c9c2344cc0a9827e910affbab71997d0fcee41",
        },
        "allowed_auxiliary_versions": ["OSCBWin6.7.54"],
    },
}


def _git(repo: Path, args: list[str]) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode:
        raise ValueError((process.stderr or process.stdout or "git failed").strip())
    return process.stdout.strip()


def _commit(repo: Path, revision: str) -> dict[str, str]:
    value = _git(repo, ["show", "-s", "--format=%H%x1f%cI%x1f%s", revision])
    parts = value.split("\x1f", 2)
    if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{40}", parts[0]):
        raise ValueError("invalid commit metadata")
    return {"commit": parts[0], "updated_at": parts[1], "subject": parts[2]}


def _tree(repo: Path, revision: str, root: str) -> str | None:
    try:
        value = _git(repo, ["rev-parse", f"{revision}:{root}"])
    except ValueError:
        return None
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def _trees(
    repo: Path,
    revision: str,
    roots: tuple[str, ...] = CONFIGURATION_ROOTS,
) -> dict[str, str]:
    return {
        root: value
        for root in roots
        if (value := _tree(repo, revision, root)) is not None
    }


def _readme(repo: Path) -> dict[str, str] | None:
    path = repo / "README.md"
    if not path.is_file():
        return None
    try:
        match = _README_VERSION.search(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        return None
    return match.groupdict() if match else None


def _history(repo: Path, revision: str) -> list[dict[str, str]]:
    value = _git(
        repo,
        [
            "log",
            "--first-parent",
            "-n",
            str(HISTORY_DEPTH),
            "--format=%H%x1f%cI%x1f%s%x1e",
            revision,
        ],
    )
    records: list[dict[str, str]] = []
    for raw in value.split("\x1e"):
        raw = raw.strip("\r\n")
        if not raw:
            continue
        fields = raw.split("\x1f", 2)
        if len(fields) == 3 and re.fullmatch(r"[0-9a-f]{40}", fields[0]):
            records.append({"commit": fields[0], "updated_at": fields[1], "subject": fields[2]})
    return records


def evidence_fingerprint(result: Mapping[str, Any]) -> str:
    """Return the canonical digest of an evidence object without its digest field."""

    payload = {key: value for key, value in result.items() if key != "evidence_fingerprint"}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _aware_timestamp(value: Any) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_resolved_evidence(
    value: Any,
    *,
    expected_commit: str | None = None,
    expected_version: str | None = None,
    expected_track: str | None = None,
    expected_snapshot_updated_at: str | None = None,
) -> dict[str, Any]:
    """Validate the complete resolver proof used by a database release.

    A self-consistent fingerprint is only a checksum, not proof that the
    enclosed fields have resolver semantics. Formal release callers use this
    validator to bind the version, source trees and evidence kind to the same
    snapshot before accepting the manifest.
    """

    if type(value) is not dict or set(value) != _RESOLVED_EVIDENCE_KEYS:
        raise ValueError("upstream_version_evidence_schema_invalid")
    evidence = copy.deepcopy(value)
    track = evidence.get("track")
    version = evidence.get("version")
    snapshot_commit = evidence.get("snapshot_commit")
    updated_at = evidence.get("snapshot_updated_at")
    kind = evidence.get("evidence_kind")
    evidence_commit = evidence.get("evidence_commit")
    review_id = evidence.get("review_id")
    issues = evidence.get("issues")
    configuration_trees = evidence.get("configuration_trees")
    source_trees = evidence.get("source_trees")

    if (
        evidence.get("resolver_version") != RESOLVER_VERSION
        or evidence.get("status") != "resolved"
        or evidence.get("version_evidence_ready") is not True
        or track not in SUPPORTED_TRACKS
        or type(version) is not str
        or _VERSION_ONLY.fullmatch(version) is None
        or evidence.get("raw_version") != f"{track}{version}"
        or type(snapshot_commit) is not str
        or _COMMIT.fullmatch(snapshot_commit) is None
        or not _aware_timestamp(updated_at)
        or kind not in _EVIDENCE_KINDS
        or type(evidence_commit) is not str
        or _COMMIT.fullmatch(evidence_commit) is None
        or type(issues) is not list
        or any(type(issue) is not str or not issue for issue in issues)
        or any(issue != "reviewed_stale_readme" for issue in issues)
        or type(configuration_trees) is not dict
        or set(configuration_trees) != set(CONFIGURATION_ROOTS)
        or type(source_trees) is not dict
        or set(source_trees) != set(SOURCE_TREE_ROOTS)
        or any(
            type(oid) is not str or _COMMIT.fullmatch(oid) is None
            for oid in (*configuration_trees.values(), *source_trees.values())
        )
        or any(
            configuration_trees[root] != source_trees[root]
            for root in CONFIGURATION_ROOTS
        )
    ):
        raise ValueError("upstream_version_evidence_semantics_invalid")
    if kind == "snapshot_commit_subject" and evidence_commit != snapshot_commit:
        raise ValueError("upstream_version_evidence_commit_invalid")
    if kind == "reviewed_source_tree_profile":
        if type(review_id) is not str or not review_id or len(review_id) > 128:
            raise ValueError("upstream_version_evidence_review_invalid")
    elif review_id is not None:
        raise ValueError("upstream_version_evidence_review_invalid")

    readme_version = evidence.get("readme_version")
    if readme_version is not None and (
        type(readme_version) is not str
        or _VERSION.fullmatch(readme_version) is None
    ):
        raise ValueError("upstream_version_evidence_readme_invalid")
    fingerprint = evidence.get("evidence_fingerprint")
    if (
        type(fingerprint) is not str
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        or fingerprint != evidence_fingerprint(evidence)
    ):
        raise ValueError("upstream_version_evidence_fingerprint_invalid")
    if (
        (expected_commit is not None and snapshot_commit != expected_commit)
        or (expected_version is not None and version != expected_version)
        or (expected_track is not None and track != expected_track)
        or (
            expected_snapshot_updated_at is not None
            and updated_at != expected_snapshot_updated_at
        )
    ):
        raise ValueError("upstream_version_evidence_binding_invalid")
    return evidence


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["evidence_fingerprint"] = evidence_fingerprint(result)
    return result


def unresolved_evidence(commit: str | None, reason: str) -> dict[str, Any]:
    return _finish(
        {
            "resolver_version": RESOLVER_VERSION,
            "status": "unresolved",
            "version_evidence_ready": False,
            "snapshot_commit": commit,
            "snapshot_updated_at": None,
            "track": None,
            "version": None,
            "raw_version": None,
            "evidence_kind": None,
            "evidence_commit": None,
            "configuration_trees": {},
            "source_trees": {},
            "readme_version": None,
            "issues": [reason],
        }
    )


def inspect(repo_dir: Path, expected_commit: str | None = None) -> dict[str, Any]:
    """Return deterministic evidence without changing or fetching the checkout."""

    repo = Path(repo_dir)
    try:
        head = _commit(repo, expected_commit or "HEAD")
    except (OSError, ValueError):
        return unresolved_evidence(expected_commit, "git_metadata_unavailable")
    if expected_commit is not None and head["commit"] != expected_commit.lower():
        return unresolved_evidence(expected_commit, "snapshot_commit_mismatch")

    source_trees = _trees(repo, head["commit"], SOURCE_TREE_ROOTS)
    trees = {root: source_trees[root] for root in CONFIGURATION_ROOTS if root in source_trees}
    readme = _readme(repo)
    readme_raw = readme["raw"] if readme else None
    issues = [
        f"source_tree_missing:{root}"
        for root in SOURCE_TREE_ROOTS
        if root not in source_trees
    ]
    chosen: dict[str, Any] | None = None

    direct = _VERSION.search(head["subject"])
    if direct:
        chosen = {
            **direct.groupdict(),
            "evidence_kind": "snapshot_commit_subject",
            "evidence_commit": head["commit"],
            "review_id": None,
        }

    profile = _REVIEWED_SNAPSHOTS.get(head["commit"])
    if chosen is None and profile is not None:
        expected_trees = profile["trees"]
        if all(trees.get(root) == oid for root, oid in expected_trees.items()):
            chosen = {
                "raw": profile["track"] + profile["version"],
                "track": profile["track"],
                "version": profile["version"],
                "evidence_kind": "reviewed_source_tree_profile",
                "evidence_commit": profile["version_commit"],
                "review_id": profile["review_id"],
            }
            if readme_raw and readme_raw != chosen["raw"]:
                if readme_raw not in profile.get("allowed_auxiliary_versions", []):
                    issues.append("readme_version_conflict")
                else:
                    issues.append("reviewed_stale_readme")
        else:
            issues.append("reviewed_profile_tree_mismatch")

    if chosen is None and trees:
        try:
            history = _history(repo, head["commit"])
        except ValueError:
            history = []
        for candidate in history[1:]:
            match = _VERSION.search(candidate["subject"])
            if not match:
                continue
            candidate_trees = _trees(repo, candidate["commit"])
            if candidate_trees and candidate_trees == trees:
                chosen = {
                    **match.groupdict(),
                    "evidence_kind": "source_tree_equivalent_ancestor",
                    "evidence_commit": candidate["commit"],
                    "review_id": None,
                }
                break

    if chosen is None:
        if readme_raw:
            issues.append("readme_is_auxiliary_only")
        issues.append("primary_version_evidence_missing")
        return _finish(
            {
                "resolver_version": RESOLVER_VERSION,
                "status": "unresolved",
                "version_evidence_ready": False,
                "snapshot_commit": head["commit"],
                "snapshot_updated_at": head["updated_at"],
                "track": None,
                "version": None,
                "raw_version": None,
                "evidence_kind": None,
                "evidence_commit": None,
                "configuration_trees": trees,
                "source_trees": source_trees,
                "readme_version": readme_raw,
                "issues": issues,
            }
        )

    if readme_raw and readme_raw != chosen["raw"] and "reviewed_stale_readme" not in issues:
        issues.append("readme_version_conflict")
    version_evidence_ready = not any(
        issue.endswith(("conflict", "mismatch"))
        or issue.startswith("source_tree_missing:")
        for issue in issues
    )
    result = _finish(
        {
            "resolver_version": RESOLVER_VERSION,
            "status": "resolved" if version_evidence_ready else "review_required",
            "version_evidence_ready": version_evidence_ready,
            "snapshot_commit": head["commit"],
            "snapshot_updated_at": head["updated_at"],
            "track": chosen["track"],
            "version": chosen["version"],
            "raw_version": chosen["raw"],
            "evidence_kind": chosen["evidence_kind"],
            "evidence_commit": chosen["evidence_commit"],
            "review_id": chosen.get("review_id"),
            "configuration_trees": trees,
            "source_trees": source_trees,
            "readme_version": readme_raw,
            "issues": issues,
        }
    )
    if version_evidence_ready:
        return validate_resolved_evidence(result)
    return result
