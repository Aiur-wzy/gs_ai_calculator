import gzip
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest


def _load_exporter():
    script = Path(__file__).resolve().parents[1] / "scripts/export_public_source.py"
    spec = importlib.util.spec_from_file_location("public_source_export", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_export_covers_pinned_workspace_patches():
    exporter = _load_exporter()
    root = Path(__file__).resolve().parents[1]
    rules_path = root / exporter.ALLOWLIST
    if rules_path.is_file():
        rules = json.loads(rules_path.read_text("utf-8"))
        trees = rules["main_repository"]["include_trees_after_review"]
    else:
        rules = None
        snapshot = json.loads((root / exporter.MANIFEST).read_text("utf-8"))
        snapshot_files = {row["path"]: row for row in snapshot["files"]}
    for project in ("frontend", "agent-runtime"):
        workspace = (root / project / "pnpm-workspace.yaml").read_text("utf-8")
        patches = re.findall(r'^\s*"[^"\n]+":\s*(patches/[^\n]+\.patch)\s*$', workspace, re.MULTILINE)
        assert patches, f"{project}: expected the reviewed DSH source contract patch"
        for patch in patches:
            name = f"{project}/{patch}"
            assert (root / name).is_file(), name
            if rules is not None:
                assert any(name.startswith(tree.rstrip("/") + "/") for tree in trees), name
                assert not exporter._excluded(name, rules), name
            else:
                assert name in snapshot_files
                assert hashlib.sha256((root / name).read_bytes()).hexdigest() == snapshot_files[name]["sha256"], name


def test_source_export_rejects_stale_inputs_and_unexpected_outputs(tmp_path):
    exporter = _load_exporter()
    source = tmp_path / "workspace"
    (source / "code").mkdir(parents=True)
    (source / "docs").mkdir()
    (source / "code/example.py").write_text("answer = 42\n", encoding="utf-8")
    (source / "code/private.db").write_bytes(b"SQLite format 3\0")
    (source / "docs/public-readme.md").write_text("# Public project\n", encoding="utf-8")
    rules = {"main_repository": {"include_trees_after_review": ["code/"],
                                 "source_overlays": {"README.md": "docs/public-readme.md"}},
             "global_exclude_globs": ["**/*.db"]}
    snapshot = exporter.plan(source, rules)
    assert [row["path"] for row in snapshot["files"]] == ["README.md", "code/example.py"]
    assert snapshot["files"][0]["source_path"] == "docs/public-readme.md"
    assert snapshot["excluded"] == [{"path": "code/private.db", "reason": "allowlist_exclusion"}]

    target = tmp_path / "export"
    (source / "code/example.py").write_text("answer = 43\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since snapshot"):
        exporter.export(source, target, snapshot)
    assert not target.exists()

    snapshot = exporter.plan(source, rules)
    exporter.export(source, target, snapshot)
    assert exporter.verify(target)["file_count"] == 2
    (target / "extra.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory mismatch"):
        exporter.verify(target)


def test_source_export_requires_pinned_synthetic_review_and_rejects_database(tmp_path):
    exporter = _load_exporter()
    source = tmp_path / "workspace"
    (source / "tests").mkdir(parents=True)
    fixture_path = "tests/fixture.txt"
    fixture = source / fixture_path
    data = ('C:/' + 'Users/example/secret.txt').encode()
    fixture.write_bytes(data)
    rules = {"main_repository": {"include_files_after_review": [fixture_path]},
             "global_exclude_globs": []}
    snapshot = exporter.plan(source, rules)
    target = tmp_path / "export"
    with pytest.raises(ValueError, match="review required"):
        exporter.export(source, target, snapshot)

    review = {fixture_path: {"sha256": hashlib.sha256(data).hexdigest(), "findings": ["machine_path"],
                             "classification": "synthetic_fixture", "reason": "Deliberate test fixture."}}
    exporter.export(source, target, snapshot, reviewed=review)
    assert exporter.verify(target)["status"] == "verified"

    sqlite_data = b"SQLite format 3\0"
    fixture.write_bytes(sqlite_data)
    review[fixture_path].update(sha256=hashlib.sha256(sqlite_data).hexdigest(), findings=["sqlite"])
    with pytest.raises(ValueError, match="sqlite"):
        exporter.export(source, tmp_path / "refused", exporter.plan(source, rules), reviewed=review)


def test_scan_detects_compressed_and_utf16_fixtures_but_not_dependency_names():
    exporter = _load_exporter()
    assert not exporter.scan_bytes(b'node_modules/@fixture/users/index.d.ts')
    synthetic = ('C:/' + 'Users/example/private-file').encode('utf-16')
    assert exporter.scan_bytes(gzip.compress(synthetic)) == ["machine_path"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("public/placeholder.txt", synthetic)
    assert exporter.scan_bytes(buffer.getvalue()) == ["machine_path"]


def test_git_ignored_tree_contents_are_excluded_unless_generated_input(tmp_path):
    exporter = _load_exporter()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*.local\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore"], check=True)
    directory = tmp_path / "tree"
    directory.mkdir()
    for name in ("private.local", "coherent.local", "source.txt"):
        (directory / name).write_text("fixture\n", encoding="utf-8")
    rules = {"main_repository": {"include_trees_after_review": ["tree/"],
                                 "generated_files": ["tree/coherent.local"]},
             "global_exclude_globs": []}
    snapshot = exporter.plan(tmp_path, rules)
    assert [(row["path"], row["origin"]) for row in snapshot["files"]] == [
        ("tree/coherent.local", "generated"), ("tree/source.txt", "untracked")]
    assert snapshot["excluded"] == [{"path": "tree/private.local", "reason": "git_ignored_file"}]


def test_isolated_snapshot_does_not_inherit_parent_checkout_rules(tmp_path):
    exporter = _load_exporter()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    source = tmp_path / "build/export"
    source.mkdir(parents=True)
    (source / "code.py").write_text("answer = 42\n", encoding="utf-8")
    rules = {"main_repository": {"include_files_after_review": ["code.py"]}, "global_exclude_globs": []}
    snapshot = exporter.plan(source, rules)
    assert [row["path"] for row in snapshot["files"]] == ["code.py"]
    assert snapshot["source_revision"] is None


def test_committed_snapshot_allows_root_git_but_rejects_source_drift(tmp_path):
    exporter = _load_exporter()
    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("answer = 42\n", encoding="utf-8")
    rules = {"main_repository": {"include_files_after_review": ["code.py"]},
             "global_exclude_globs": []}
    target = tmp_path / "public"
    exporter.export(source, target, exporter.plan(source, rules))
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    assert exporter.verify(target)["status"] == "verified"

    nested = target / "unexpected/.git"
    nested.mkdir(parents=True)
    (nested / "config").write_text("[core]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory mismatch"):
        exporter.verify(target)
    (nested / "config").unlink()
    nested.rmdir()
    nested.parent.rmdir()

    (target / "code.py").write_text("answer = 43\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since snapshot"):
        exporter.verify(target)
