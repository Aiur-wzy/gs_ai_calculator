import json
import os
from types import SimpleNamespace

import pytest

from genshin_app import setup_frozen_qualification as qualification
from genshin_app.release_pair_helper import ReleasePairHelperError
from genshin_db.release_migration import DatabaseMigrationError


def test_packaged_install_reports_sanitized_migration_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(qualification.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(qualification, "sys", SimpleNamespace(frozen=True, executable="GenshinCalcSetup.exe"))
    isolated_roots = []
    monkeypatch.setattr(qualification, "_install_isolation", isolated_roots.append)
    files = [tmp_path / name for name in ("latest.json", "app.zip", "db.sqlite.gz")]
    for path in files:
        path.touch()
    failure = DatabaseMigrationError(
        "database_migration_component_invalid",
        "private path inside the raw exception that must not appear in a report",
        {"component": "runtime_config", "state": "invalid", "userdata": "internal"},
    )

    def fail(*_args):
        raise ReleasePairHelperError("release_pair_helper_first_install_recovery_required") from failure

    monkeypatch.setattr(qualification, "_verify_local_install", fail)
    sandbox = tmp_path / (qualification.SANDBOX_PREFIX + "report")
    report = sandbox / "result.json"
    opts = {"index": files[0], "app_archive": files[1], "database_archive": files[2], "sandbox_root": sandbox}
    assert qualification.verify_frozen_install(**opts, report=tmp_path / "outside.json") == 2
    assert not sandbox.exists()
    assert qualification.verify_frozen_install(**opts, report=report) == 1
    assert isolated_roots == [sandbox]

    text = report.read_text(encoding="utf-8")
    value = json.loads(text)
    assert value["status"] == "failed"
    assert [frame["reason"] for frame in value["cause_chain"]] == [
        "release_pair_helper_first_install_recovery_required", "database_migration_component_invalid",
    ]
    assert value["cause_chain"][1]["component"] == "runtime_config"
    assert value["cause_chain"][1]["state"] == "invalid"
    assert "private path" not in text and "userdata" not in text


def test_install_audit_keeps_signed_artifacts_readable_and_limits_writes(tmp_path, monkeypatch):
    hooks = []
    monkeypatch.setattr(qualification, "sys", SimpleNamespace(addaudithook=hooks.append))
    root = tmp_path / (qualification.SANDBOX_PREFIX + "audit")
    qualification._install_isolation(root)
    assert len(hooks) == 1
    audit = hooks[0]
    audit("open", (str(tmp_path / "signed.zip"), "r", os.O_RDONLY))
    audit("open", (str(root / "report.json"), "w", os.O_WRONLY | os.O_CREAT))
    audit("sqlite3.connect", (f"{(root / 'genshin.db').as_uri()}?mode=ro",))
    with pytest.raises(qualification.SetupQualificationError, match="external_write"):
        audit("open", (str(tmp_path / "signed.zip"), "w", os.O_WRONLY))
    with pytest.raises(qualification.SetupQualificationError, match="external_write"):
        audit("sqlite3.connect", (f"{(tmp_path / 'live.db').as_uri()}?mode=rw",))
    for event in ("socket.connect", "urllib.Request", "subprocess.Popen", "winreg.OpenKey"):
        with pytest.raises(qualification.SetupQualificationError, match="external_action"):
            audit(event, ())


def test_upgrade_admission_rechecks_installed_app_layout(tmp_path, monkeypatch):
    from genshin_app import database_bootstrap

    app = tmp_path / "app"
    internal = app / "_internal"
    internal.mkdir(parents=True)
    executable = app / "GenshinCalc.exe"
    executable.touch()
    target = {"pair_manifest": {"release_pair_id": "pair-test", "app": {"app_release_id": "app-test"}},
              "database_manifest": {"database_release_id": "db-test"}}

    def admit(data):
        assert data == tmp_path / "data"
        assert database_bootstrap.resource_dir() == internal
        assert database_bootstrap.sys.executable == str(executable)
        return {"can_start": True, "pair_policy": "exact", "release_pair_id": "pair-test",
                "app_release_id": "app-test", "database_release_id": "db-test"}

    monkeypatch.setattr(database_bootstrap, "prepare_database_startup", admit)
    result = qualification._verify_startup_admission(tmp_path, target)
    assert result["execution_layer"] == "app_startup_gate_in_frozen_setup"
    executable.unlink()
    with pytest.raises(qualification.SetupQualificationError, match="app_entry_missing"):
        qualification._verify_startup_admission(tmp_path, target)


def test_upgrade_entry_rejects_source_execution_and_overlapping_inputs(tmp_path, monkeypatch):
    paths = [tmp_path / filename for filename in ("previous.json", "previous.zip", "target.json", "target.zip", "db.gz")]
    for path in paths:
        path.touch()
    previous_index, previous_app, index, app, database = paths
    root = tmp_path / (qualification.SANDBOX_PREFIX + "upgrade")
    inputs = {"previous_index": previous_index, "previous_app_archive": previous_app,
              "index": index, "app_archive": app, "database_archive": database,
              "sandbox_root": root, "report": root / "result.json"}
    assert qualification.verify_frozen_upgrade(**inputs) == 2
    assert not root.exists()

    monkeypatch.setattr(qualification.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(qualification, "sys", SimpleNamespace(frozen=True, executable="GenshinCalcSetup.exe"))
    assert qualification.verify_frozen_upgrade(**{**inputs, "app_archive": previous_app}) == 2
    assert qualification.verify_frozen_upgrade(**{**inputs, "sandbox_root": tmp_path}) == 2
    assert not root.exists()


def test_lifecycle_entry_routes_only_named_isolated_mode(tmp_path, monkeypatch):
    import installer_entry

    calls = []
    monkeypatch.setattr(qualification, "verify_frozen_lifecycle", lambda **kw: calls.append(kw) or 0)
    names = ("index", "app-archive", "database-archive", "sandbox-root", "report")
    arguments = ["--verify-frozen-lifecycle"]
    for name in names:
        arguments.extend(["--" + name, str(tmp_path / name)])
    assert installer_entry.main(arguments) == 0
    assert calls[0]["index"] == tmp_path / "index"
    with pytest.raises(SystemExit):
        installer_entry.main(arguments + ["--previous-index", str(tmp_path / "previous")])


def test_maintenance_preview_cannot_select_unavailable_app_only_mode():
    import installer_entry

    migration = {"mode": "migrate_installation"}
    assert installer_entry._preview_choice({False: migration}, True) == (False, migration)
    assert installer_entry._preview_choice({}, True) == (False, None)
    assert installer_entry._preview_choice({False: "complete", True: "app"}, True) == (True, "app")
    assert installer_entry._preview_choice({False: "complete", True: "app"}, True, local_files=True) == (False, "complete")


def test_installer_explains_causes_beneath_wrappers_and_does_not_invent_success():
    import installer_entry

    message = installer_entry._failure_message(
        ["release_pair_helper_first_install_recovery_required", "product_install_link_unsupported"], "安装")
    assert "目录联接" in message and "已经完成" not in message
    message = installer_entry._failure_message(["product_migration_source_changed"], "迁移")
    assert "保留原目录、目标目录" in message and "原安装没有移动" not in message
    message = installer_entry._failure_message(["product_uninstall_release_proof_missing"], "卸载")
    assert "归属" in message and "不要手动删除" in message
    assert "未知格式" not in installer_entry._failure_message(["unmapped_error"], "迁移")


def test_completion_message_distinguishes_app_only_and_migration_cleanup():
    import installer_entry

    result = installer_entry._installation_completion({"install_directory": "X", "database_state": "database_not_installed",
                                                        "registration_state": "registration_pending"})
    assert "补装数据库" in result and "系统入口尚待修复" in result
    assert "程序和数据库安装完成" not in result
    result = installer_entry._installation_completion({"install_directory": "Y", "source_directory": "X", "cleanup_pending": True})
    assert "新位置已生效" in result and "原位置仍有受管文件待清理" in result

