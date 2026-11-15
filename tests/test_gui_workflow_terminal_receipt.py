"""Only the ordinary terminal report is scoped; private owners keep their stores."""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from genshin_app.agent_chat.dsh_gui_workflow_runner import run_gui_agent_workflow
from genshin_app.equipment.files import publish_files


def junction(path, destination):
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(destination)], capture_output=True, check=False)
        assert made.returncode == 0, (made.stdout, made.stderr)
        assert path.is_junction()
    else:
        path.symlink_to(destination, target_is_directory=True)
    assert path.resolve() == destination.resolve()


@pytest.mark.parametrize("redirect", [False, True])
def test_real_runner_terminal_writer_rechecks_managed_root_after_factory(tmp_path, redirect):
    data = tmp_path / "custom-data"
    managed = data / "native-dsh-workspaces"
    managed.mkdir(parents=True)
    target = tmp_path / "new-output"
    private = managed / "owner-checkpoint.json"

    def factory(_data):
        assert target.is_dir() and not list(target.iterdir())
        # The harness's private owner is intentionally outside the ordinary
        # report scope, even though its CLI has immutable prompt-file roles.
        publish_files([(private, b"private-owner-checkpoint")])
        if redirect:
            assert target.resolve().is_relative_to(tmp_path.resolve())
            os.rmdir(target)
            junction(target, managed)
        raise RuntimeError("controlled pre-API failure, no provider ever started")

    receipt = run_gui_agent_workflow(data_dir=data, prompt="offline report boundary", output_dir=target, api_factory=factory)
    assert receipt["status"] == "failed" and receipt["reason"] == "app_internal_error"
    assert private.read_bytes() == b"private-owner-checkpoint"
    if redirect:
        assert receipt["receipt_publication"]["status"] == "failed"
        assert receipt["receipt_publication"]["reason"] == "file_conflict"
        assert receipt["receipt_publication"]["details"]["protected_role"] == "managed"
        assert not (managed / "gui-workflow-receipt.json").exists()
        assert not (managed / ".gui-workflow-receipt.json.plan.lock").exists()
    else:
        assert receipt["receipt_publication"] == {"status": "published", "path": str(target / "gui-workflow-receipt.json")}
        assert (target / "gui-workflow-receipt.json").is_file()


def test_failed_final_report_preserves_truthful_partial_publication_details(tmp_path, monkeypatch):
    from genshin_app import output_policy
    from genshin_app.equipment.schema import PlanError

    target = tmp_path / "output"
    publication = {"published_paths": [str(target / "gui-workflow-receipt.json")],
                   "pending_paths": [], "unknown_paths": [], "cause": "lock-exit-observation"}

    ordinary = output_policy.publish_ordinary_files
    def failing_report(*args, **kwargs):
        published = ordinary(*args, **kwargs)
        assert published["published_paths"] == publication["published_paths"]
        raise PlanError("回执已写入但退出锁观察失败", "file_conflict", publication)

    monkeypatch.setattr(output_policy, "publish_ordinary_files", failing_report)
    def factory(_data):
        raise RuntimeError("controlled no-provider path")
    receipt = run_gui_agent_workflow(data_dir=tmp_path / "data", prompt="offline receipt settlement",
                                     output_dir=target, api_factory=factory)
    assert receipt["reason"] == "app_internal_error"
    assert receipt["receipt_publication"]["details"] == publication
    assert json.loads((target / "gui-workflow-receipt.json").read_text(encoding="utf-8"))["receipt_publication"]["status"] == "published"
