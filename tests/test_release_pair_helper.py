from __future__ import annotations

from pathlib import Path

import pytest

from genshin_app import release_pair_helper as helper
from genshin_app.release_pair_storage import ReleasePairStorageError


def _fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    staged_app = workspace / "staging" / "app"
    staged_app.mkdir(parents=True)
    (staged_app / "PACKAGE-FILES.sha256").write_text("manifest", encoding="utf-8")
    (staged_app / "app.bin").write_bytes(b"new-app")
    staged_db = workspace / "staging" / "genshin.db"
    staged_db.write_bytes(b"new-db")
    active_app = tmp_path / "active-app"
    active_app.mkdir()
    (active_app / "app.bin").write_bytes(b"old-app")
    data = tmp_path / "data"
    data.mkdir()
    (data / "genshin.db").write_bytes(b"old-db")
    pair = {
        "release_pair_id": "pair-next",
        "pair_policy": "current_only",
        "app": {
            "app_release_id": "app-next",
            "app_version": "0.46.0",
            "required_database_release_id": "db-next",
            "logic_profile": "logic-v1",
            "data_contract_version": 1,
            "artifact_manifest": {"sha256": "a" * 64},
        },
    }
    manifest = {"database_release_id": "db-next"}
    return locals()


def _patch_flow(monkeypatch, item, *, fail_install=False):
    calls = []

    monkeypatch.setattr(
        helper.pair_update,
        "_verify_app",
        lambda *_args, **_kwargs: {"manifest_sha256": "a" * 64},
    )

    def inspect(*_args, **_kwargs):
        calls.append("inspect")
        return {
            "operation_id": "op",
            "state": "handoff_ready",
            "production": False,
            "staged_app_root": item["staged_app"],
            "staged_database": item["staged_db"],
            "pair_manifest": item["pair"],
            "database_manifest": item["manifest"],
            "rollback_required": True,
        }

    monkeypatch.setattr(
        helper.pair_update,
        "inspect_pair_update_handoff",
        inspect,
    )
    monkeypatch.setattr(
        helper.pair_update,
        "begin_pair_update_activation",
        lambda *_args, **_kwargs: calls.append("begin") or {"status": "activation_started"},
    )

    def install(data, package, _manifest, **_kwargs):
        calls.append("install")
        if fail_install:
            raise RuntimeError("install failed")
        (Path(data) / "genshin.db").write_bytes(Path(package).read_bytes())
        return {"status": "installed"}

    monkeypatch.setattr(helper, "install_database_release", install)

    def postcheck(_workspace, active, data, **_kwargs):
        calls.append("postcheck")
        assert (Path(active) / "app.bin").read_bytes() == b"new-app"
        assert (Path(data) / "genshin.db").read_bytes() == b"new-db"
        return {"status": "operational", "release_pair_id": "pair-next"}

    monkeypatch.setattr(helper.pair_update, "postcheck_pair_update", postcheck)
    monkeypatch.setattr(
        helper.pair_update,
        "complete_pair_update_cleanup",
        lambda *_args, **_kwargs: calls.append("cleanup") or {"status": "cleanup_complete"},
    )
    monkeypatch.setattr(
        helper.pair_update,
        "confirm_pair_update_rollback",
        lambda *_args, **_kwargs: calls.append("rollback") or {"status": "rolled_back"},
    )
    return calls


def test_external_helper_swaps_both_artifacts_then_postchecks(tmp_path, monkeypatch):
    item = _fixture(tmp_path)
    calls = _patch_flow(monkeypatch, item)
    result = helper.execute_external_pair_update(
        item["workspace"],
        item["active_app"],
        item["data"],
        operation_id="op",
    )
    assert result["status"] == "complete"
    assert result["pair_operational"] is True
    assert (item["active_app"] / "app.bin").read_bytes() == b"new-app"
    assert (item["data"] / "genshin.db").read_bytes() == b"new-db"
    assert calls == ["inspect", "begin", "install", "postcheck", "cleanup"]


def test_external_helper_reuses_semantically_equal_database(tmp_path, monkeypatch):
    item = _fixture(tmp_path)
    calls = _patch_flow(monkeypatch, item)
    monkeypatch.setattr(
        helper,
        "verified_reusable_database",
        lambda *_args: {"sha256": "b" * 64},
    )
    monkeypatch.setattr(
        helper,
        "rebind_database_sidecars",
        lambda *_args: calls.append("rebind"),
    )
    monkeypatch.setattr(
        helper,
        "install_database_release",
        lambda *_args, **_kwargs: pytest.fail("same DB release must not be swapped"),
    )

    def postcheck(_workspace, active, data, **_kwargs):
        calls.append("postcheck")
        assert (Path(active) / "app.bin").read_bytes() == b"new-app"
        assert (Path(data) / "genshin.db").read_bytes() == b"old-db"
        return {"status": "operational", "release_pair_id": "pair-next"}

    monkeypatch.setattr(helper.pair_update, "postcheck_pair_update", postcheck)
    result = helper.execute_external_pair_update(
        item["workspace"],
        item["active_app"],
        item["data"],
        operation_id="op",
    )

    assert result["status"] == "complete"
    assert calls == ["inspect", "begin", "rebind", "postcheck", "cleanup"]


def test_explicit_app_only_mode_uses_no_staged_or_rollback_database(
    tmp_path, monkeypatch
):
    item = _fixture(tmp_path)
    calls = _patch_flow(monkeypatch, item)
    item["staged_db"].unlink()

    def inspect(*_args, **_kwargs):
        calls.append("inspect")
        return {
            "operation_id": "op",
            "state": "handoff_ready",
            "production": False,
            "staged_app_root": item["staged_app"],
            "staged_database": None,
            "database_mode": "reuse_installed",
            "pair_manifest": item["pair"],
            "database_manifest": item["manifest"],
            "rollback_required": True,
        }

    monkeypatch.setattr(helper.pair_update, "inspect_pair_update_handoff", inspect)
    monkeypatch.setattr(
        helper,
        "verified_reusable_database",
        lambda *_args: {"sha256": "b" * 64},
    )
    monkeypatch.setattr(
        helper,
        "rebind_database_sidecars",
        lambda *_args: calls.append("rebind"),
    )
    monkeypatch.setattr(
        helper,
        "install_database_release",
        lambda *_args, **_kwargs: pytest.fail("App-only mode must not install DB"),
    )
    original_prepare = helper._prepare_protected_rollback

    def prepare(*args, **kwargs):
        assert kwargs["include_database"] is False
        path = original_prepare(*args, **kwargs)
        assert not (path / "genshin.db").exists()
        return path

    monkeypatch.setattr(helper, "_prepare_protected_rollback", prepare)

    def postcheck(_workspace, active, data, **_kwargs):
        calls.append("postcheck")
        assert (Path(active) / "app.bin").read_bytes() == b"new-app"
        assert (Path(data) / "genshin.db").read_bytes() == b"old-db"
        return {"status": "operational", "release_pair_id": "pair-next"}

    monkeypatch.setattr(helper.pair_update, "postcheck_pair_update", postcheck)
    result = helper.execute_external_pair_update(
        item["workspace"], item["active_app"], item["data"], operation_id="op"
    )
    assert result["status"] == "complete"
    assert calls == ["inspect", "begin", "rebind", "postcheck", "cleanup"]


def test_external_helper_restores_exact_old_pair_on_failure(tmp_path, monkeypatch):
    item = _fixture(tmp_path)
    calls = _patch_flow(monkeypatch, item, fail_install=True)
    monkeypatch.setattr(
        helper,
        "_launch_replacement",
        lambda *_args, **_kwargs: pytest.fail("failed update must not restart App"),
    )
    with pytest.raises(RuntimeError, match="install failed"):
        helper.execute_external_pair_update(
            item["workspace"],
            item["active_app"],
            item["data"],
            operation_id="op",
            restart_relative="GenshinCalc.exe",
        )
    assert (item["active_app"] / "app.bin").read_bytes() == b"old-app"
    assert (item["data"] / "genshin.db").read_bytes() == b"old-db"
    assert calls == ["inspect", "begin", "install", "rollback", "cleanup"]


def test_failure_ignores_tampered_user_workspace_rollback(tmp_path, monkeypatch):
    item = _fixture(tmp_path)
    calls = _patch_flow(monkeypatch, item, fail_install=True)

    def begin(*_args, **_kwargs):
        calls.append("begin")
        # This tree is deliberately user writable.  It remains a journal
        # artifact at most, but must never be restored to Program Files.
        fake = item["workspace"] / "rollback" / "app"
        fake.mkdir(parents=True)
        (fake / "app.bin").write_bytes(b"attacker-app")
        return {"status": "activation_started"}

    monkeypatch.setattr(helper.pair_update, "begin_pair_update_activation", begin)
    with pytest.raises(RuntimeError, match="install failed"):
        helper.execute_external_pair_update(
            item["workspace"],
            item["active_app"],
            item["data"],
            operation_id="op",
        )
    assert (item["active_app"] / "app.bin").read_bytes() == b"old-app"
    assert calls == ["inspect", "begin", "install", "rollback", "cleanup"]


def test_protected_replacement_is_verified_before_active_swap(tmp_path, monkeypatch):
    item = _fixture(tmp_path)
    calls = _patch_flow(monkeypatch, item)

    def reject_replacement(root, _pair):
        assert root.parent == item["active_app"].parent
        assert root.name.startswith(".active-app.next-")
        raise helper.pair_update.ReleasePairUpdateError("tampered-protected-copy")

    monkeypatch.setattr(helper.pair_update, "_verify_app", reject_replacement)
    with pytest.raises(
        helper.pair_update.ReleasePairUpdateError,
        match="tampered-protected-copy",
    ):
        helper.execute_external_pair_update(
            item["workspace"],
            item["active_app"],
            item["data"],
            operation_id="op",
        )
    assert (item["active_app"] / "app.bin").read_bytes() == b"old-app"
    assert not (item["active_app"].parent / ".active-app.next-op").exists()
    assert calls == ["inspect", "begin", "rollback", "cleanup"]


def test_helper_rechecks_space_before_creating_protected_rollback(
    tmp_path, monkeypatch
):
    item = _fixture(tmp_path)
    calls = _patch_flow(monkeypatch, item)
    monkeypatch.setattr(
        helper,
        "preflight_after_stage",
        lambda **_kwargs: (_ for _ in ()).throw(
            ReleasePairStorageError("release_pair_update_disk_space_insufficient")
        ),
    )
    monkeypatch.setattr(
        helper,
        "_prepare_protected_rollback",
        lambda *_args, **_kwargs: pytest.fail("space gate must run first"),
    )
    with pytest.raises(helper.ReleasePairHelperError) as failure:
        helper.execute_external_pair_update(
            item["workspace"],
            item["active_app"],
            item["data"],
            operation_id="op",
        )
    assert failure.value.reason == "release_pair_update_disk_space_insufficient"
    assert (item["active_app"] / "app.bin").read_bytes() == b"old-app"
    assert calls == ["inspect"]


def test_prepare_is_a_thin_non_mutating_handoff_boundary(tmp_path, monkeypatch):
    observed = {}

    def prepare(*args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return {"status": "handoff_ready"}

    monkeypatch.setattr(helper.pair_update, "prepare_release_pair_handoff", prepare)
    result = helper.prepare_external_pair_update(
        tmp_path / "workspace",
        tmp_path / "app",
        tmp_path / "database",
        {"database": True},
        {"pair": True},
        user_confirmed=True,
        production=False,
        trust_assertion={"review": True},
    )
    assert result == {"status": "handoff_ready"}
    assert observed["kwargs"]["user_confirmed"] is True


def test_helper_waits_for_gui_exit_and_restarts_only_after_postcheck(
    tmp_path, monkeypatch
):
    item = _fixture(tmp_path)
    calls = _patch_flow(monkeypatch, item)
    owns_writer = False
    original_writer = helper.profile_writer

    from contextlib import contextmanager

    @contextmanager
    def tracked_writer(data):
        nonlocal owns_writer
        with original_writer(data):
            owns_writer = True
            try:
                yield
            finally:
                owns_writer = False

    def wait(pid, timeout):
        assert not owns_writer
        calls.append(("wait", pid, timeout))

    def launch(active, relative):
        assert not owns_writer
        calls.append(("restart", relative))
        assert (Path(active) / "app.bin").read_bytes() == b"new-app"
        return 77

    monkeypatch.setattr(helper, "profile_writer", tracked_writer)
    monkeypatch.setattr(helper, "_wait_for_parent_exit", wait)
    monkeypatch.setattr(helper, "_launch_replacement", launch)
    result = helper.execute_external_pair_update(
        item["workspace"],
        item["active_app"],
        item["data"],
        operation_id="op",
        parent_pid=456,
        parent_wait_timeout=12,
        restart_relative="GenshinCalc.exe",
    )
    assert calls == [
        ("wait", 456, 12),
        "inspect",
        "begin",
        "install",
        "postcheck",
        "cleanup",
        ("restart", "GenshinCalc.exe"),
    ]
    assert result["restart"] == {"requested": True, "started": True, "pid": 77}


@pytest.mark.parametrize(
    "relative",
    ["../outside.exe", "/absolute.exe", "C:/absolute.exe", "bin\\app.exe", ""],
)
def test_restart_entry_rejects_absolute_traversal_and_shell_shaped_paths(
    tmp_path, relative
):
    active = tmp_path / "app"
    active.mkdir()
    with pytest.raises(helper.ReleasePairHelperError) as failure:
        helper._restart_target(active, relative)
    assert failure.value.reason == "release_pair_helper_restart_entry_invalid"


def test_helper_rejects_overlapping_active_workspace_or_data_paths(tmp_path, monkeypatch):
    active = tmp_path / "app"
    workspace = active / "updater"
    workspace.mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(
        helper,
        "_wait_for_parent_exit",
        lambda *_args, **_kwargs: pytest.fail("topology must fail before waiting"),
    )
    with pytest.raises(helper.ReleasePairHelperError) as failure:
        helper.execute_external_pair_update(
            workspace,
            active,
            data,
            operation_id="op",
        )
    assert failure.value.reason == "release_pair_helper_path_topology_invalid"


def test_cli_request_requires_lifecycle_and_restart_fields(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(
        '{"workspace":"w","active_app_root":"a","data_dir":"d",'
        '"operation_id":"op","cleanup":true}',
        encoding="utf-8",
    )
    with pytest.raises(helper.ReleasePairHelperError) as failure:
        helper._request(request)
    assert failure.value.reason == "release_pair_helper_request_invalid"


def test_cli_request_is_size_bounded_before_json_parse(tmp_path):
    request = tmp_path / "request.json"
    request.write_bytes(b"{" + b" " * helper.MAX_HELPER_REQUEST_BYTES + b"}")
    with pytest.raises(helper.ReleasePairHelperError) as failure:
        helper._request(request)
    assert failure.value.reason == "release_pair_helper_request_invalid"


def test_frozen_helper_rejects_user_request_path_redirection(tmp_path, monkeypatch):
    active = tmp_path / "installed-app"
    data = tmp_path / "profile" / "data"
    workspace = tmp_path / "profile" / "update-workspace"
    for path in (active, data, workspace):
        path.mkdir(parents=True)
    operation_id = "op"
    request_path = workspace / f"helper-request-{operation_id}.json"
    request_path.write_text("{}", encoding="utf-8")
    request = {
        "workspace": str(workspace),
        "active_app_root": str(active),
        "data_dir": str(data),
        "operation_id": operation_id,
    }
    monkeypatch.setattr(helper.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        helper,
        "_installed_product_roots",
        lambda: (active.resolve(), data.resolve(), workspace.resolve()),
    )
    helper._validate_frozen_product_request(request_path, request)

    redirected = {**request, "active_app_root": str(tmp_path / "other")}
    (tmp_path / "other").mkdir()
    with pytest.raises(helper.ReleasePairHelperError) as failure:
        helper._validate_frozen_product_request(request_path, redirected)
    assert failure.value.reason == "release_pair_helper_request_path_mismatch"


def test_frozen_helper_rebinds_handoff_to_protected_current_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(helper.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        helper.pair_update,
        "inspect_active_release_pair",
        lambda *_args, **_kwargs: {
            "identity_fingerprint": "f" * 64,
            "pair_manifest": {"pair_sequence": 4},
        },
    )
    valid = {
        "production": True,
        "rollback_required": True,
        "expected_current_fingerprint": "f" * 64,
        "pair_manifest": {"pair_sequence": 5},
    }
    helper._verify_frozen_active_pair(valid, tmp_path / "app", tmp_path / "data")

    no_rollback = {**valid, "rollback_required": False}
    with pytest.raises(helper.ReleasePairHelperError) as missing:
        helper._verify_frozen_active_pair(
            no_rollback, tmp_path / "app", tmp_path / "data"
        )
    assert missing.value.reason == "release_pair_helper_current_identity_mismatch"

    downgrade = {**valid, "pair_manifest": {"pair_sequence": 3}}
    with pytest.raises(helper.ReleasePairHelperError) as lower:
        helper._verify_frozen_active_pair(
            downgrade, tmp_path / "app", tmp_path / "data"
        )
    assert lower.value.reason == "release_pair_update_rollback_forbidden"
