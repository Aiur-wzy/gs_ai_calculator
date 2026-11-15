"""Plan, copy, and verify a fresh public-source snapshot without changing Git."""
from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

CONTRACT = "genshin-public-source-snapshot-v1"
MANIFEST = "SOURCE-SNAPSHOT.json"
ALLOWLIST = Path("docs/pm/audits/2026-11-14-publication-export-allowlist.json")
PRIVATE_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "provider_token": re.compile(rb"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,})"),
    "machine_path": re.compile(
        rb"(?i)(?<![\w./@-])(?:[a-z]:[/\\]+(?:Users|code|pnpm-store|checkout|machine)(?=[/\\\s\"']|$)"
        rb"|/(?:home|Users)/[^/\s\"']+)|file:///[a-z]:[/\\]+[^\s/\\\"']+"
    ),
}
_REVIEWABLE = {"machine_path", "provider_token"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
            or not path.parts or path.as_posix() != value or any(ord(char) < 32 for char in value)):
        raise ValueError(f"invalid relative path: {value!r}")
    return path


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])
    )


def _excluded(path: str, rules: dict) -> bool:
    return any(_matches(path, pattern) for pattern in rules["global_exclude_globs"])


def _plain(path: Path) -> bool:
    return not path.is_symlink() and not path.is_junction()


def _inside_file(source: Path, name: str) -> Path:
    path = source
    for part in relative_path(name).parts:
        path /= part
        if not _plain(path):
            raise ValueError(f"snapshot refuses linked path: {name}")
    return path


def _inventory(source: Path, rules: dict, repo: dict, extra_files: list[str]) -> tuple[list[str], list[dict]]:
    paths = set(repo.get("include_files_after_review", [])) | set(extra_files)
    excluded = []
    for relative in repo.get("include_trees_after_review", []):
        relative = relative.rstrip("/")
        tree = _inside_file(source, relative)
        if not tree.is_dir():
            excluded.append({"path": relative, "reason": "missing_candidate_tree"})
            continue
        for directory, subdirs, files in os.walk(tree):
            parent = Path(directory)
            for name in subdirs[:]:
                child = parent / name
                rel = child.relative_to(source).as_posix()
                if _excluded(rel + "/", rules) or not _plain(child):
                    subdirs.remove(name)
                    excluded.append({"path": rel, "reason": "excluded_or_linked_tree"})
            paths.update((parent / name).relative_to(source).as_posix() for name in files)
    return sorted(paths), excluded


def _scan_payload(content: bytes) -> set[str]:
    variants = [content]
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        variants.append(content.decode("utf-16", errors="replace").encode("utf-8"))
    kinds = {name for name, pattern in PRIVATE_PATTERNS.items() if any(pattern.search(part) for part in variants)}
    if content.startswith(b"SQLite format 3\x00"):
        kinds.add("sqlite")
    return kinds


def scan_bytes(content: bytes) -> list[str]:
    if content.startswith(b"\x1f\x8b"):
        return sorted(_scan_payload(gzip.decompress(content)))
    if content.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            findings = set()
            for member in archive.infolist():
                if member.filename.startswith(("/", "\\")) or ".." in PurePosixPath(member.filename.replace("\\", "/")).parts:
                    findings.add("archive_member_path")
                findings.update(_scan_payload(member.filename.encode()))
                if not member.is_dir():
                    findings.update(_scan_payload(archive.read(member)))
            return sorted(findings)
    return sorted(_scan_payload(content))


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _tracked(source: Path) -> set[str]:
    result = subprocess.run(["git", "-C", str(source), "ls-files", "-z"],
                            capture_output=True, check=False)
    return set(filter(None, result.stdout.decode("utf-8").split("\0"))) if result.returncode == 0 else set()


def _ignored(source: Path, names: set[str]) -> set[str]:
    if not names:
        return set()
    result = subprocess.run(["git", "-C", str(source), "check-ignore", "--stdin", "-z"],
                            input=("\0".join(sorted(names)) + "\0").encode("utf-8"),
                            capture_output=True, check=False)
    if result.returncode not in (0, 1):
        raise ValueError("Git ignored-file check failed")
    return set(filter(None, result.stdout.decode("utf-8").split("\0")))


def _revision(source: Path) -> str | None:
    result = subprocess.run(["git", "-C", str(source), "rev-parse", "--verify", "HEAD"],
                            capture_output=True, check=False)
    return result.stdout.decode("ascii").strip() if result.returncode == 0 else None


def _is_repository_root(source: Path) -> bool:
    git = source / ".git"
    # A checkout or Git worktree has its own marker. An isolated source copy
    # under build/ must not inherit the development repository's ignore rules.
    return _plain(git) and (git.is_dir() or git.is_file())


def plan(source: Path, rules: dict, extra_files: list[str] | None = None, *, repository: str = "main") -> dict:
    source = source.resolve(strict=True)
    repo = rules[f"{repository}_repository"]
    paths, excluded = _inventory(source, rules, repo, extra_files or [])
    git_checkout = _is_repository_root(source)
    tracked = _tracked(source) if git_checkout else set()
    files = []
    overlays = repo.get("source_overlays", {})
    generated = repo.get("generated_files", [])
    ignored = _ignored(source, ({*paths, *overlays.values()} - tracked)) if git_checkout else set()
    for name in sorted(set(paths) | overlays.keys()):
        relative_path(name)
        if name == MANIFEST:
            raise ValueError(f"reserved snapshot filename: {name}")
        source_name = overlays.get(name, name)
        if _excluded(name, rules) or _excluded(source_name, rules):
            excluded.append({"path": name, "reason": "allowlist_exclusion"})
            continue
        is_generated = any(_matches(name, pattern) for pattern in generated)
        if source_name in ignored and not is_generated:
            excluded.append({"path": name, "reason": "git_ignored_file"})
            continue
        path = _inside_file(source, source_name)
        if not path.is_file():
            excluded.append({"path": name, "reason": "missing_candidate_file"})
            continue
        content = path.read_bytes()
        files.append({"path": name, "source_path": source_name, "sha256": _sha(content), "bytes": len(content),
                      "origin": ("generated" if is_generated
                                 else "tracked" if source_name in tracked else "untracked"),
                      "selection": "curated_overlay" if name in overlays else "allowlist",
                      "review": scan_bytes(content)})
    return {"contract": CONTRACT, "repository": repository, "source_revision": _revision(source) if git_checkout else None,
            "rules_sha256": _sha(json.dumps(rules, sort_keys=True).encode()),
            "files": files, "excluded": excluded}


def _verify_bytes(path: Path, expected: dict) -> bytes:
    content = path.read_bytes()
    if len(content) != expected["bytes"] or _sha(content) != expected["sha256"]:
        raise ValueError(f"file changed since snapshot: {expected['path']}")
    return content


def _require_review(row: dict, content: bytes, reviews: dict) -> None:
    detected = scan_bytes(content)
    if detected != row["review"]:
        raise ValueError(f"snapshot review findings differ: {row['path']}")
    if not detected:
        return
    review = reviews.get(row["path"], {})
    if (not isinstance(review, dict) or not row["path"].startswith("tests/")
            or row["path"].lower().endswith((".gz", ".zip"))
            or not set(detected) <= _REVIEWABLE or review.get("sha256") != row["sha256"]
            or review.get("findings") != detected or review.get("classification") != "synthetic_fixture"
            or not isinstance(review.get("reason"), str) or not review["reason"].strip()):
        raise ValueError(f"review required: {row['path']} ({', '.join(detected)})")


def _check_inventory(snapshot: dict, reviews: dict) -> set[str]:
    if snapshot.get("contract") != CONTRACT or snapshot.get("repository") not in {"main", "database"}:
        raise ValueError("unrecognized snapshot manifest")
    rows = snapshot["files"]
    names = set()
    findings = set()
    for row in rows:
        name = relative_path(row["path"]).as_posix()
        relative_path(row["source_path"])
        if name == MANIFEST or name.casefold() in names:
            raise ValueError(f"duplicate or reserved snapshot filename: {name}")
        names.add(name.casefold())
        if not _SHA256.fullmatch(row["sha256"]) or type(row["bytes"]) is not int or row["bytes"] < 0:
            raise ValueError(f"invalid file identity: {name}")
        if row["review"]:
            findings.add(name)
    if reviews.keys() - findings:
        raise ValueError("synthetic reviews refer to files without current findings")
    return {row["path"] for row in rows}


def export(source: Path, target: Path, snapshot: dict, *, reviewed: dict | None = None) -> dict:
    source = source.resolve(strict=True)
    target = target.absolute()
    if target.resolve() == source:
        raise ValueError("export target must be separate from the source")
    if target.resolve().is_relative_to(source):
        relative = target.resolve().relative_to(source)
        if len(relative.parts) < 3 or relative.parts[:2] != ("build", "publication-export"):
            raise ValueError("in-repository exports belong under build/publication-export")
    if not all(_plain(parent) for parent in (target, *target.parents)):
        raise ValueError("export target has a linked ancestor")
    reviews = reviewed or {}
    _check_inventory(snapshot, reviews)
    for row in snapshot["files"]:
        content = _verify_bytes(_inside_file(source, row["source_path"]), row)
        _require_review(row, content, reviews)
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True, exist_ok=False)
    for row in snapshot["files"]:
        name = row["path"]
        destination = _inside_file(target, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = _verify_bytes(_inside_file(source, row["source_path"]), row)
        with destination.open("xb") as stream:
            stream.write(content)
    for row in snapshot["files"]:
        _verify_bytes(_inside_file(source, row["source_path"]), row)
        _verify_bytes(_inside_file(target, row["path"]), row)
    manifest = {**snapshot, "status": "copied", "reviewed_files": reviews}
    (target / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(target: Path) -> dict:
    if not all(_plain(parent) for parent in (target, *target.parents)):
        raise ValueError("export target has a linked ancestor")
    snapshot = json.loads((target / MANIFEST).read_text(encoding="utf-8"))
    if snapshot.get("status") != "copied":
        raise ValueError("unrecognized snapshot manifest")
    expected = _check_inventory(snapshot, snapshot["reviewed_files"]) | {MANIFEST}
    found = set()
    for directory, subdirs, names in os.walk(target):
        base = Path(directory)
        if any(not _plain(base / name) for name in [*subdirs, *names]):
            raise ValueError("linked path in export")
        if base == target:
            # A fresh public commit/clone adds root Git metadata, not source.
            # Do not ignore nested repositories or any other extra files.
            if ".git" in subdirs:
                subdirs.remove(".git")
            if ".git" in names:
                names.remove(".git")
        found.update((base / name).relative_to(target).as_posix() for name in names)
    if found != expected:
        raise ValueError("snapshot file inventory mismatch")
    for row in snapshot["files"]:
        content = _verify_bytes(_inside_file(target, row["path"]), row)
        _require_review(row, content, snapshot["reviewed_files"])
    return {"contract": CONTRACT, "status": "verified", "file_count": len(expected) - 1}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "export", "verify"], nargs="?", default="plan")
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--rules", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--repository", choices=["main", "database"], default="main")
    parser.add_argument("--plan", type=Path, help="Previously generated snapshot plan")
    parser.add_argument("--reviewed", type=Path, help="Reviewed synthetic fixtures with SHA-256, findings, classification and reason")
    parser.add_argument("--include-file", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.mode == "verify":
            result = verify(args.target)
        elif args.mode == "export":
            snapshot = json.loads(args.plan.read_text(encoding="utf-8"))
            reviewed = json.loads(args.reviewed.read_text(encoding="utf-8")) if args.reviewed else None
            rules = json.loads((args.rules or args.source / ALLOWLIST).read_text(encoding="utf-8"))
            current = plan(args.source, rules, args.include_file, repository=args.repository)
            if snapshot != current:
                raise ValueError("source or export rules changed. Generate and review a fresh plan.")
            result = export(args.source, args.target, snapshot, reviewed=reviewed)
        else:
            rules = json.loads((args.rules or args.source / ALLOWLIST).read_text(encoding="utf-8"))
            result = plan(args.source, rules, args.include_file, repository=args.repository)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
