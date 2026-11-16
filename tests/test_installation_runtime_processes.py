"""Real cross-process maintenance exclusion, with no GUI or production profile."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from genshin_app.installation_runtime import (
    assert_installation_idle,
    installation_mutation_lock,
)
from genshin_app.product_install_contract import (
    FIRST_INSTALL_CONTRACT,
    REGISTRATION_FILENAME,
    FirstInstallError,
)


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "自选 安装 (租约)"
    root.mkdir()
    (root / REGISTRATION_FILENAME).write_text(json.dumps({
        "contract": FIRST_INSTALL_CONTRACT,
        "scope": "current_user", "app_directory": "app", "data_directory": "data",
        "layout_version": 2, "installation_id": "a" * 32, "installation_state": "installed",
    }), encoding="utf-8")
    return root


@contextmanager
def worker(root, name, *, crash=False):
    folder = root.parent / name
    folder.mkdir()
    marker, release = folder / "entered.json", folder / "release"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.Popen([
        sys.executable, str(Path(__file__).with_name("installation_lease_worker.py")),
        str(root), str(marker), str(release), "crash" if crash else "wait",
    ], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 15
        while not marker.exists():
            if process.poll() is not None:
                out, err = process.communicate()
                pytest.fail(f"worker exited before checkpoint: {process.returncode}: {out!r} {err!r}")
            if time.monotonic() >= deadline:
                pytest.fail("worker failed to reach checkpoint within 15 seconds")
            time.sleep(0.02)
        # The writer closes its tiny JSON immediately; tolerate observing the
        # file between creation and completion on Windows.
        while True:
            try:
                observed = json.loads(marker.read_text(encoding="utf-8"))
                break
            except json.JSONDecodeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        yield process, observed
    finally:
        release.touch()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()  # Only this test's own child, never an App window.
            process.communicate(timeout=10)


def test_two_real_runtime_processes_block_maintenance_until_both_exit(installation):
    with worker(installation, "first") as (_, first):
        assert first == {"status": "entered"}
        with worker(installation, "second") as (_, second):
            assert second == {"status": "entered"}
            with (pytest.raises(FirstInstallError, match="installation_running"),
                  installation_mutation_lock(installation)):
                pytest.fail("maintenance entered with two live processes")
        with pytest.raises(FirstInstallError, match="installation_running"):
            assert_installation_idle(installation)
    with installation_mutation_lock(installation):
        assert_installation_idle(installation)


def test_real_new_process_is_blocked_during_maintenance_then_can_reopen(installation):
    with installation_mutation_lock(installation), worker(installation, "blocked") as (process, observed):
        assert observed == {"status": "rejected", "reason": "product_installation_maintenance_busy"}
        assert process.wait(timeout=10) == 2
    with worker(installation, "reopened") as (_, observed):
        assert observed == {"status": "entered"}


def test_abrupt_exit_releases_os_lock_and_stale_file_is_reclaimed(installation):
    with worker(installation, "crashed", crash=True) as (process, observed):
        assert observed == {"status": "entered"}
        assert process.wait(timeout=10) == 17
    directory = installation / "maintenance/runtime-leases"
    assert list(directory.glob("*.lock")), "crash must leave evidence for recovery"
    with installation_mutation_lock(installation):
        assert not list(directory.glob("*.lock"))
    with worker(installation, "after-crash") as (_, observed):
        assert observed == {"status": "entered"}


@pytest.mark.parametrize("state", ["publishing", "uninstalling", "uninstalled", "migration_source_retired"])
def test_new_process_rereads_final_registration_after_maintenance(installation, state):
    with installation_mutation_lock(installation):
        registration = installation / REGISTRATION_FILENAME
        row = json.loads(registration.read_text(encoding="utf-8"))
        row["installation_state"] = state
        registration.write_text(json.dumps(row), encoding="utf-8")
    with worker(installation, "inactive") as (process, observed):
        assert observed == {"status": "rejected", "reason": "product_installation_not_active"}
        assert process.wait(timeout=10) == 2
