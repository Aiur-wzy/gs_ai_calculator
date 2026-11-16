from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app.gui import launcher


class _Api:
    def __init__(self):
        self._window = None
        self.shutdowns = 0

    def agent_shutdown(self):
        self.shutdowns += 1
        return {"code": 0, "data": {"closed": True}}


def test_external_database_skips_legacy_service_and_marks_window(tmp_path, monkeypatch):
    captured = {}
    api = _Api()

    def api_factory(data_dir, **kwargs):
        captured["api"] = (Path(data_dir), kwargs)
        return api

    class ForbiddenService:
        def __init__(self, _data_dir):
            raise AssertionError("external database must not start legacy db service")

    class Webview:
        @staticmethod
        def create_window(title, **kwargs):
            captured["window"] = (title, kwargs)
            return SimpleNamespace()

        @staticmethod
        def start(**options):
            captured["started"] = True
            captured["webview_options"] = options

    monkeypatch.setitem(__import__("sys").modules, "webview", Webview)
    monkeypatch.setattr(launcher, "GuiApi", api_factory)
    monkeypatch.setattr(launcher, "ServiceManager", ForbiddenService)
    monkeypatch.setattr(launcher, "install_close_guard", lambda *_args: None)
    monkeypatch.setattr(
        launcher,
        "prepare_database_startup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("external database mode must bypass paired startup admission")
        ),
    )
    monkeypatch.setattr(
        launcher,
        "ensure_data_dir",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("external database mode must not bootstrap a database")
        ),
    )

    profile = tmp_path / "profile"
    shared = tmp_path / "source" / "genshin.db"
    factory = object()
    shell_factory = object()
    save_dialog = object()
    file_dialog = object()
    result = launcher.run_gui(
        profile,
        database_path=shared,
        app_identity={
            "mode": "development_acceptance",
            "label": "本地开发验收版",
            "network_policy": "disabled",
        },
        agent_manager_factory=factory,
        dsh_shell_factory=shell_factory,
        dsh_result_save_dialog=save_dialog,
        dsh_file_dialog=file_dialog,
    )

    assert result == 0 and captured["started"] is True
    assert captured["webview_options"] == {"storage_path": str(profile / "webview-profile"), "private_mode": False}
    args, kwargs = captured["api"]
    assert args == profile
    assert kwargs["database_path"] == shared.resolve()
    assert kwargs["agent_manager_factory"] is factory
    assert kwargs["dsh_shell_factory"] is shell_factory
    assert kwargs["dsh_result_save_dialog"] is save_dialog
    assert kwargs["dsh_file_dialog"] is file_dialog
    assert kwargs["app_identity"]["service_mode"] == "skipped_external_database"
    assert kwargs["app_identity"]["release_plan_state"] == (
        "external_database_acceptance_bypass"
    )
    assert captured["window"][0] == "原神拉表 · 本地开发验收版"
    assert api.shutdowns == 1
    assert not (profile / "genshin.db").exists()
    assert all((profile / name).is_dir() for name in ("build", "exports", "plans"))


def test_external_database_requires_reviewed_acceptance_mode(tmp_path, monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "webview",
        SimpleNamespace(),
    )
    monkeypatch.setattr(launcher, "is_frozen", lambda: False)

    with pytest.raises(launcher.DatabaseStartupError) as failure:
        launcher.run_gui(
            tmp_path / "profile",
            database_path=tmp_path / "source" / "genshin.db",
        )
    assert failure.value.reason == "external_database_acceptance_capability_required"
    assert not (tmp_path / "profile").exists()


def test_formal_frozen_launcher_rejects_external_database_even_with_dev_identity(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setitem(
        __import__("sys").modules,
        "webview",
        SimpleNamespace(),
    )
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)

    with pytest.raises(launcher.DatabaseStartupError) as failure:
        launcher.run_gui(
            tmp_path / "profile",
            database_path=tmp_path / "source" / "genshin.db",
            app_identity={"mode": "development_acceptance"},
        )
    assert failure.value.reason == "external_database_forbidden_in_formal_app"
    assert not (tmp_path / "profile").exists()
