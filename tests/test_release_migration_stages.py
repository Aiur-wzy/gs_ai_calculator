"""Independent frozen-coordinator comparisons of every durable fault boundary."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path
from uuid import UUID

import pytest

from genshin_db import release_migration as migration
from tests.reference_migration_coordinator_v1 import make_reference_install_locked
from tests.test_database_release_migration import _make_pair, coordinator  # noqa: F401

# ruff: noqa: F811 - imported pytest fixture

FAULTS = (
    None,
    "before_stage_copy",
    "after_candidate_validation",
    "after_rollback_snapshot",
    "before_replace",
    "after_replace",
    "after_active_postcheck",
    "after_manifest_write",
    "after_receipt_write",
    "after_commit",
    "before_cleanup",
)


def _snapshot(data):
    result = {}
    for path in sorted(data.rglob("*")):
        if not path.is_file() or path.name.endswith(".lock"):
            continue
        result[path.relative_to(data).as_posix()] = (
            json.loads(path.read_text("utf8"))
            if path.suffix == ".json"
            else hashlib.sha256(path.read_bytes()).hexdigest()
        )
    return result


@pytest.mark.parametrize("production", [True, False])
@pytest.mark.parametrize("fault", FAULTS)
def test_pass_coordinator_matches_frozen_state_and_fault_order(
    tmp_path,
    coordinator,
    monkeypatch,
    production,
    fault,
):
    monkeypatch.setattr(migration, "_now", lambda: "2026-10-31T00:00:00+00:00")
    monkeypatch.setattr(migration, "uuid4", lambda: UUID(int=1))
    actual = migration._install_locked
    reference = make_reference_install_locked(migration)
    _seed_dir, seed_live, seed_package, manifest = _make_pair(tmp_path / "seed")
    observed = []
    for name, implementation in (("actual", actual), ("reference", reference)):
        directory = tmp_path / name / "data"
        directory.mkdir(parents=True)
        package = tmp_path / name / "package.db"
        shutil.copyfile(seed_live, directory / "genshin.db")
        shutil.copyfile(seed_package, package)
        trace = []

        def fault_hook(point, *, _trace=trace):
            _trace.append(point)
            if point == fault:
                raise RuntimeError("controlled-" + point)

        with monkeypatch.context() as patch:
            patch.setattr(migration, "_install_locked", implementation)
            try:
                outcome = coordinator(
                    directory,
                    package,
                    manifest,
                    production=production,
                    fault_hook=fault_hook,
                )
            except RuntimeError as error:
                outcome = (type(error).__module__, type(error).__name__, str(error))
            before_retry = _snapshot(directory)
            retry = coordinator(directory, package, manifest, production=production)
            observed.append((outcome, trace, before_retry, retry, _snapshot(directory)))
    assert observed[0] == observed[1]


def test_stage_leaves_cannot_own_locks_journal_or_active_swap():
    source = Path(migration.__file__).with_name("release_migration_stages.py")
    tree = ast.parse(source.read_text("utf8"))
    forbidden = {
        "database_lock",
        "file_lock",
        "journal",
        "_journal",
        "replace",
        "restore_rollback",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else (node.func.attr if isinstance(node.func, ast.Attribute) else None)
        )
        assert called not in forbidden
