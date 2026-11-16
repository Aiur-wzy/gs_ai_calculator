"""Startup handoff stays window-local and uses existing, read-only file loaders."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from genshin_app.gui import launcher
from genshin_app.gui.api import GuiApi


def test_startup_path_is_absolute_claimed_once_and_local_to_each_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = Path("中文 方案's.json")
    first = GuiApi(tmp_path, startup_path=target)
    second = GuiApi(tmp_path, startup_path=target)
    # Resolution happens at construction, not after a later working-dir change.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    expected = str(tmp_path / target)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: first.get_startup_file(), range(16)))
    assert all(result["code"] == 0 for result in claims)
    assert [result["data"]["path"] for result in claims].count(expected) == 1
    assert [result["data"]["path"] for result in claims].count(None) == 15
    assert second.get_startup_file() == {"code": 0, "data": {"path": expected}}
    assert GuiApi(tmp_path).get_startup_file() == {"code": 0, "data": {"path": None}}
    assert not (tmp_path / target).exists()


@pytest.mark.parametrize("kind", ["legacy", "native"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_claimed_startup_file_loads_via_existing_bridge_without_write_or_calc(tmp_path, monkeypatch, kind, encoding):
    target = tmp_path / "角色 方案.json"
    api = GuiApi(tmp_path, startup_path=target)
    template = api.new_template()["data"]["template"]
    payload = api.new_plan(template)["data"] if kind == "native" else template
    original = json.dumps(payload, ensure_ascii=False).encode(encoding)
    target.write_bytes(original)

    def unexpected_calc(*_args, **_kwargs):
        pytest.fail("Opening a file must not calculate damage")

    monkeypatch.setattr("genshin_app.equipment.service.run_plan_calc", unexpected_calc)
    monkeypatch.setattr("genshin_app.gui.ports.document.calculate", unexpected_calc)
    selected = api.get_startup_file()["data"]["path"]
    inspected = api.inspect_plan_file(selected)
    assert inspected["code"] == 0
    if inspected["data"]["kind"]:
        loaded = api.load_plan(selected)
        assert loaded["data"]["plan"]["plan_id"] == payload["plan_id"]
    else:
        loaded = api.load_template(selected)
        assert loaded["data"]["template"] == payload
    assert loaded["code"] == 0
    assert loaded["data"]["path"] == str(target)
    assert api.get_startup_file()["data"]["path"] is None
    assert target.read_bytes() == original
    # Native composition may take the shared database lock; opening must not
    # create a substitute plan or database, or rewrite the source file.
    assert set(tmp_path.iterdir()) <= {target, tmp_path / ".genshin.db.write.lock"}
    assert not (tmp_path / "genshin.db").exists()


@pytest.mark.parametrize("payload", [None, b"not JSON", b"{}"])
def test_missing_or_invalid_startup_is_delivered_to_gui_loader_not_replaced(tmp_path, payload):
    target = tmp_path / "错误文件.json"
    if payload is not None:
        target.write_bytes(payload)
    api = GuiApi(tmp_path, startup_path=target)
    selected = api.get_startup_file()["data"]["path"]
    inspected = api.inspect_plan_file(selected)
    failure = inspected if inspected["code"] else api.load_template(selected)
    assert failure["code"] == 1
    assert failure["message"]
    assert api.get_startup_file()["data"]["path"] is None
    assert target.exists() == (payload is not None)
    if payload is not None:
        assert target.read_bytes() == payload


@pytest.mark.parametrize("open_name", [None, "中文 空格's.json"])
def test_launcher_passes_path_as_bridge_data_without_opening_real_window(tmp_path, monkeypatch, open_name):
    created, managers, guards, startup_scripts = [], [], [], []

    class Manager:
        def __init__(self, directory):
            self.directory = directory
            self.stopped = False
            managers.append(self)

        def ensure_running(self):
            return {"base_url": "http://127.0.0.1:1", "owned": False}

        def stop(self):
            self.stopped = True

    def create_window(title, **kwargs):
        window = SimpleNamespace(
            evaluate_js=lambda script: startup_scripts.append(script),
            destroy=lambda: None,
        )
        created.append({"title": title, "window": window, **kwargs})
        return window

    def start(callback=None, *, storage_path, private_mode):
        assert Path(storage_path) == tmp_path / "webview-profile"
        assert private_mode is False
        if callback is not None:
            callback()
        entry = created[-1]
        assert entry["js_api"]._window is entry["window"]
        assert "js" not in entry and "html" not in entry
        assert entry["js_api"].get_startup_file()["data"]["path"] == (
            str(tmp_path / open_name) if open_name else None)

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create_window, start=start))
    monkeypatch.setattr(launcher, "ensure_data_dir", lambda path: path)
    monkeypatch.setattr(
        launcher,
        "prepare_database_startup",
        lambda _path, progress=None: {
            "state": "current_dev_ready",
            "can_start": True,
        },
    )
    monkeypatch.setattr(launcher, "ServiceManager", Manager)
    monkeypatch.setattr(launcher, "install_close_guard", lambda window, api: guards.append((window, api)))
    for _ in range(2):
        assert launcher.run_gui(tmp_path, tmp_path / open_name if open_name else None) == 0
    main = [entry for entry in created if "url" in entry]
    assert all(type(entry["js_api"]).__name__ == "DatabaseStartupRecovery"
               for entry in created if "html" in entry)
    assert len(guards) == len(main) == 2
    assert len(created) == 4  # one visible admission page + one App window per run
    assert main[0]["js_api"] is not main[1]["js_api"]
    assert main[0]["window"] is not main[1]["window"]
    assert main[0]["title"] == (f"原神拉表 · {open_name}" if open_name else "原神拉表")
    assert all(
        entry["js_api"]._app_identity["release_plan_state"] == "current_dev_ready"
        for entry in main
    )
    assert all(manager.stopped for manager in managers)
    assert any("服务子进程会独立复核数据库" in script for script in startup_scripts)
    assert not list(tmp_path.iterdir())


def test_verified_app_only_launches_main_without_database_service(tmp_path, monkeypatch):
    from genshin_app import product_database_setup

    setup = Mock()
    windows = []

    def create_window(_title, **kwargs):
        windows.append(kwargs)
        return SimpleNamespace(evaluate_js=lambda _text: None, destroy=lambda: None)

    def start(callback, *, storage_path, private_mode):
        assert Path(storage_path) == tmp_path / "webview-profile"
        assert private_mode is False
        callback()

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=create_window, start=start))
    monkeypatch.setattr(product_database_setup, "build_database_setup", lambda _path: setup)
    monkeypatch.setattr(launcher, "prepare_database_startup", Mock(side_effect=AssertionError("uninstalled database")))
    monkeypatch.setattr(launcher, "ServiceManager", Mock(side_effect=AssertionError("uninstalled database")))
    monkeypatch.setattr(launcher, "install_close_guard", lambda *_args: None)
    assert launcher.run_gui(tmp_path) == 0
    api = windows[-1]["js_api"]
    assert api.ping()["data"]["app_identity"]["release_plan_state"] == "database_not_installed"
    assert api.db_overview()["reason"] == "database_not_installed"
    assert api.whiteboard_new()["code"] == 0
    setup.validate_app_only.assert_called_once()
    assert not (tmp_path / "genshin.db").exists()


@pytest.mark.parametrize("failure_stage", ["create_window", "close_guard", "start"])
def test_launcher_cleans_service_when_window_setup_or_start_fails(tmp_path, monkeypatch, failure_stage):
    manager = Mock()
    manager.ensure_running.return_value = {"base_url": "http://127.0.0.1:1", "owned": True}
    window = SimpleNamespace(evaluate_js=lambda _script: None, destroy=lambda: None)
    create_window = Mock(return_value=window)
    close_guard = Mock()
    if failure_stage == "create_window":
        create_window.side_effect = [window, RuntimeError("injected window startup failure")]
    elif failure_stage == "close_guard":
        close_guard.side_effect = RuntimeError("injected window startup failure")

    def webview_start(callback=None, *, storage_path, private_mode):
        assert Path(storage_path) == tmp_path / "webview-profile"
        assert private_mode is False
        if failure_stage == "start":
            raise RuntimeError("injected window startup failure")
        if callback is not None:
            callback()

    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(create_window=create_window, start=webview_start),
    )
    monkeypatch.setattr(launcher, "ensure_data_dir", lambda path: path)
    monkeypatch.setattr(
        launcher,
        "prepare_database_startup",
        lambda _path, progress=None: {"state": "current_dev_ready", "can_start": True},
    )
    monkeypatch.setattr(launcher, "ServiceManager", Mock(return_value=manager))
    monkeypatch.setattr(launcher, "install_close_guard", close_guard)

    with pytest.raises(RuntimeError, match="injected window startup failure"):
        launcher.run_gui(tmp_path, tmp_path / "方案.json")
    if failure_stage == "start":
        manager.ensure_running.assert_not_called()
        manager.stop.assert_not_called()
    else:
        manager.ensure_running.assert_called_once()
        manager.stop.assert_called_once()
    if failure_stage == "start":
        assert not list(tmp_path.iterdir())
    else:
        diagnostic = json.loads((tmp_path / "logs/database-startup-last.json").read_text(encoding="utf-8"))
        assert diagnostic["status"] == "failed"
        assert diagnostic["reason"] == "database_startup_failed"


def test_database_gate_failure_stays_on_startup_page_and_never_constructs_runtime(
    tmp_path,
    monkeypatch,
):
    scripts = []
    splash = SimpleNamespace(
        evaluate_js=lambda script: scripts.append(script),
        destroy=lambda: pytest.fail("failed admission must keep the error page visible"),
    )

    def start(callback=None, *, storage_path, private_mode):
        assert Path(storage_path) == tmp_path / "webview-profile"
        assert private_mode is False
        assert callback is not None
        callback()

    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(create_window=Mock(return_value=splash), start=start),
    )
    monkeypatch.setattr(launcher, "ensure_data_dir", lambda path: path)
    monkeypatch.setattr(
        launcher,
        "prepare_database_startup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            launcher.DatabaseStartupError(
                "database_release_identity_unmanifested",
                "数据库没有受审发行身份",
            )
        ),
    )
    monkeypatch.setattr(
        launcher,
        "ServiceManager",
        lambda *_args: pytest.fail("service must not exist before database admission"),
    )
    monkeypatch.setattr(
        launcher,
        "GuiApi",
        lambda *_args, **_kwargs: pytest.fail(
            "GUI engine/Agent bridge must not exist before database admission"
        ),
    )

    with pytest.raises(
        launcher.DatabaseStartupError,
        match="数据库没有受审发行身份",
    ) as failure:
        launcher.run_gui(tmp_path)
    assert failure.value.reason == "database_release_identity_unmanifested"
    assert any("databaseStartupFailure" in script for script in scripts)
