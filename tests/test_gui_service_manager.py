"""G1 服务自动化测试：复用 / 属主清理 / 崩溃重启 / 多窗口共享服务恢复。"""

import http.server
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock

import pytest

from genshin_app.gui.service_manager import SPAWN_TIMEOUT, ServiceManager, _serve_cmd


def test_serve_cmd_dev_and_frozen(monkeypatch, tmp_path):
    import sys

    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = _serve_cmd(tmp_path)
    assert cmd[1:3] == ["-m", "genshin_app"] and cmd[-2:] == ["db", "serve"]

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = _serve_cmd(tmp_path)
    assert cmd == [sys.executable, "--data-dir", str(tmp_path), "db", "serve"]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"code":0,"data":{}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def stub_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield {"base_url": f"http://127.0.0.1:{srv.server_address[1]}",
           "port": srv.server_address[1]}
    srv.shutdown()
    srv.server_close()


class FakePopen:
    """模拟 db serve 子进程：构造时写 service.json 指向 stub 服务，terminate 时清理。"""

    instances: ClassVar[list] = []
    stub: ClassVar[dict] = {}
    data_dir: ClassVar = None

    def __init__(self, cmd, stdout=None, stderr=None, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = 10000 + len(FakePopen.instances)
        self._returncode = None
        FakePopen.instances.append(self)
        (FakePopen.data_dir / "service.json").write_text(json.dumps({
            "base_url": FakePopen.stub["base_url"],
            "port": FakePopen.stub["port"],
            "pid": self.pid,
        }), encoding="utf-8")

    def poll(self):
        return self._returncode

    def terminate(self):
        self._returncode = 0
        path = FakePopen.data_dir / "service.json"
        if path.exists() and json.loads(path.read_text())["pid"] == self.pid:
            path.unlink(missing_ok=True)

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self._returncode


def use_fake_popen(monkeypatch, tmp_path, stub_server):
    FakePopen.instances = []
    FakePopen.stub = stub_server
    FakePopen.data_dir = tmp_path
    monkeypatch.setattr("genshin_app.gui.service_manager.Popen", FakePopen)


def test_spawn_budget_allows_independent_database_revalidation(tmp_path, monkeypatch):
    """A secure child may spend tens of seconds in its own startup gate."""

    assert SPAWN_TIMEOUT == 300.0
    manager = ServiceManager(tmp_path)
    process = Mock(pid=2222)
    process.poll.return_value = None
    winner = {"base_url": "http://127.0.0.1:55555", "pid": 2222}
    monkeypatch.setattr("genshin_app.gui.service_manager.Popen", Mock(return_value=process))
    monkeypatch.setattr(
        "genshin_app.gui.service_manager.time.monotonic",
        Mock(side_effect=[0.0, 240.0]),
    )
    monkeypatch.setattr(manager, "_available_service", Mock(return_value=winner))

    assert manager._spawn() == {**winner, "owned": True}


def _wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "服务状态未在期限内完成恢复"


def test_reuse_existing_service(tmp_path, stub_server, monkeypatch):
    (tmp_path / "service.json").write_text(json.dumps({
        "base_url": stub_server["base_url"], "port": stub_server["port"], "pid": 999,
    }), encoding="utf-8")
    m = ServiceManager(tmp_path, check_interval=0.05)
    spawn = Mock(side_effect=AssertionError("健康的外部服务不应触发启动"))
    monkeypatch.setattr(m, "_spawn", spawn)
    info = m.ensure_running()
    assert info["owned"] is False
    assert info["base_url"] == stub_server["base_url"]
    assert m._watchdog.is_alive()
    m.stop()
    spawn.assert_not_called()
    assert json.loads((tmp_path / "service.json").read_text())["pid"] == 999
    # 外部服务不受影响（属主语义：不杀别人起的实例）
    with urllib.request.urlopen(
        stub_server["base_url"] + "/v1/meta/version", timeout=1
    ) as resp:
        assert resp.status == 200


def test_spawn_owned_and_stop(tmp_path, stub_server, monkeypatch):
    use_fake_popen(monkeypatch, tmp_path, stub_server)
    m = ServiceManager(tmp_path, check_interval=0.05)
    info = m.ensure_running()
    assert info["owned"] is True
    assert len(FakePopen.instances) == 1
    m.stop()
    assert FakePopen.instances[0].poll() == 0  # 自有实例被终止
    assert not (tmp_path / "service.json").exists()  # 发现文件已清


def test_watchdog_restarts_crash(tmp_path, stub_server, monkeypatch):
    use_fake_popen(monkeypatch, tmp_path, stub_server)
    m = ServiceManager(tmp_path, check_interval=0.05, restart_delay=0.01)
    m.ensure_running()
    FakePopen.instances[0]._returncode = 1  # 模拟崩溃
    deadline = time.monotonic() + 3
    while len(FakePopen.instances) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    m.stop()
    assert len(FakePopen.instances) == 2  # 看门已重启新实例


def _write_info(data_dir, pid=999):
    info = {"base_url": "http://127.0.0.1:55555", "pid": pid}
    (data_dir / "service.json").write_text(json.dumps(info), encoding="utf-8")
    return info


@pytest.mark.parametrize("loser_exit_code", [None, 1])
def test_competing_spawn_reuses_winner_without_ownership(tmp_path, monkeypatch, loser_exit_code):
    winner = _write_info(tmp_path)
    proc = Mock(pid=2222)
    proc.poll.return_value = loser_exit_code
    proc.terminate.side_effect = lambda: setattr(proc.poll, "return_value", 0)
    monkeypatch.setattr("genshin_app.gui.service_manager.Popen", Mock(return_value=proc))
    monkeypatch.setattr("genshin_app.gui.service_manager.check_existing_service",
                        Mock(side_effect=[None, winner["base_url"]]))
    manager = ServiceManager(tmp_path)
    manager._start_watchdog = Mock()

    assert manager.ensure_running() == {**winner, "owned": False}
    assert not manager._owned
    manager._start_watchdog.assert_called_once()
    manager.stop()
    assert json.loads((tmp_path / "service.json").read_text()) == winner
    assert proc.terminate.call_count == (1 if loser_exit_code is None else 0)


def test_loser_waits_for_competing_service_to_finish_startup(tmp_path, monkeypatch):
    winner = _write_info(tmp_path)
    proc = Mock(pid=2222)
    proc.poll.return_value = 1
    monkeypatch.setattr("genshin_app.gui.service_manager.Popen", Mock(return_value=proc))
    monkeypatch.setattr("genshin_app.gui.service_manager.check_existing_service",
                        Mock(side_effect=[None, None, winner["base_url"]]))
    monkeypatch.setattr("genshin_app.gui.service_manager.lock_is_held", lambda _: True)
    manager = ServiceManager(tmp_path)
    assert manager.ensure_running() == {**winner, "owned": False}
    manager.stop()
    assert (tmp_path / "service.json").exists()


def test_stop_preserves_replacement_service_info(tmp_path):
    winner = _write_info(tmp_path)
    manager = ServiceManager(tmp_path)
    manager._owned = True
    manager._proc = Mock(pid=2222)
    manager._proc.poll.return_value = 1  # 自有服务已崩溃，其他实例已启动。
    manager.stop()
    assert json.loads((tmp_path / "service.json").read_text()) == winner


def test_stop_cleans_matching_discovery_after_forced_exit(tmp_path):
    _write_info(tmp_path, 2222)
    manager = ServiceManager(tmp_path)
    manager._owned = True
    proc = manager._proc = Mock(pid=2222)
    proc.poll.return_value = None
    from subprocess import TimeoutExpired
    proc.wait.side_effect = [TimeoutExpired("db serve", 5), 0]
    manager.stop()
    proc.kill.assert_called_once()
    assert proc.wait.call_count == 2
    assert not (tmp_path / "service.json").exists()


def test_watchdog_relinquishes_ownership_when_restart_loses(tmp_path, monkeypatch):
    winner = _write_info(tmp_path)
    manager = ServiceManager(tmp_path)
    manager._owned = True
    manager._proc = Mock(pid=2222)
    manager._proc.poll.return_value = 1
    # 重启时输给另一个窗口后，下一轮仍探测共享服务，而不是结束看门线程。
    manager._stop.wait = Mock(side_effect=[False, False, False, True])
    monkeypatch.setattr(manager, "_available_service", Mock(side_effect=[None, winner]))
    monkeypatch.setattr(manager, "_start_watchdog", Mock())
    monkeypatch.setattr(manager, "_spawn", Mock(return_value={**winner, "owned": False}))
    manager._watch_loop()
    assert not manager._owned
    assert manager._available_service.call_count == 2
    manager._spawn.assert_called_once()
    manager._start_watchdog.assert_called_once()
    manager.stop()
    assert (tmp_path / "service.json").exists()


def test_stop_requested_during_spawn_cleans_only_own_child(tmp_path, monkeypatch):
    manager = ServiceManager(tmp_path)
    proc = Mock(pid=2222)
    proc.poll.return_value = None
    proc.terminate.side_effect = lambda: setattr(proc.poll, "return_value", 0)
    monkeypatch.setattr("genshin_app.gui.service_manager.Popen", Mock(return_value=proc))
    calls = 0

    def probe(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            _write_info(tmp_path, proc.pid)
            manager._stop.set()

    monkeypatch.setattr("genshin_app.gui.service_manager.check_existing_service", probe)
    with pytest.raises(RuntimeError, match="启动已取消"):
        manager.ensure_running()
    manager.stop()
    proc.terminate.assert_called_once()
    assert not (tmp_path / "service.json").exists()


def test_ensure_running_twice_retains_actual_ownership(tmp_path, stub_server, monkeypatch):
    use_fake_popen(monkeypatch, tmp_path, stub_server)
    manager = ServiceManager(tmp_path)
    first = manager.ensure_running()
    assert manager.ensure_running() == first
    assert first["owned"] is True
    assert len(FakePopen.instances) == 1
    manager.stop()


def test_remaining_window_recovers_after_owner_closes(tmp_path, stub_server, monkeypatch):
    use_fake_popen(monkeypatch, tmp_path, stub_server)
    owner = ServiceManager(tmp_path, check_interval=0.02, restart_delay=0.01)
    follower = ServiceManager(tmp_path, check_interval=0.02, restart_delay=0.01)
    try:
        first = owner.ensure_running()
        assert first["owned"] is True
        assert follower.ensure_running() == {**first, "owned": False}
        assert follower._watchdog.is_alive()

        owner.stop()
        _wait_until(lambda: len(FakePopen.instances) == 2 and follower._owned)
        assert FakePopen.instances[0].poll() == 0
        assert FakePopen.instances[1].poll() is None
        assert follower.ensure_running()["pid"] == FakePopen.instances[1].pid
    finally:
        owner.stop()
        follower.stop()
    assert FakePopen.instances[1].poll() == 0
    assert not (tmp_path / "service.json").exists()
    assert not follower._watchdog.is_alive()


def test_recovery_rechecks_and_reuses_new_winner(tmp_path, monkeypatch):
    winner = _write_info(tmp_path)
    manager = ServiceManager(tmp_path)
    manager._stop.wait = Mock(side_effect=[False, False, True])
    monkeypatch.setattr(manager, "_available_service", Mock(side_effect=[None, winner]))
    monkeypatch.setattr(manager, "_spawn", Mock())
    monkeypatch.setattr(manager, "_start_watchdog", Mock())

    manager._watch_loop()
    assert not manager._owned
    manager._spawn.assert_not_called()
    manager._start_watchdog.assert_called_once()
    manager.stop()
    assert json.loads((tmp_path / "service.json").read_text()) == winner


def test_recovery_retries_after_spawn_failure(tmp_path, monkeypatch):
    manager = ServiceManager(tmp_path)
    manager._stop.wait = Mock(side_effect=[False, False, False, False, True])
    monkeypatch.setattr(manager, "_available_service", Mock(return_value=None))
    monkeypatch.setattr(manager, "_start_watchdog", Mock())
    recovered = {"base_url": "http://127.0.0.1:55555", "pid": 3333, "owned": True}
    spawn = Mock(side_effect=[OSError("temporary startup failure"), recovered])
    monkeypatch.setattr(manager, "_spawn", spawn)

    manager._watch_loop()
    assert spawn.call_count == 2
    assert manager._owned
    manager.stop()


def test_stop_during_recovery_delay_does_not_spawn(tmp_path, monkeypatch):
    manager = ServiceManager(tmp_path)
    monkeypatch.setattr(manager, "_available_service", Mock(return_value=None))
    monkeypatch.setattr(manager, "_spawn", Mock())

    def wait(delay):
        if delay == manager.restart_delay:
            manager._stop.set()
            return True
        return False

    manager._stop.wait = Mock(side_effect=wait)
    manager._watch_loop()
    manager._spawn.assert_not_called()
    manager.stop()
    with pytest.raises(RuntimeError, match="已停止"):
        manager.ensure_running()
    manager._spawn.assert_not_called()


def test_watchdog_does_not_replace_living_owned_process(tmp_path, monkeypatch):
    manager = ServiceManager(tmp_path)
    manager._owned = True
    manager._proc = Mock(pid=2222)
    manager._proc.poll.return_value = None
    manager._stop.wait = Mock(side_effect=[False, True])
    monkeypatch.setattr(manager, "_available_service", Mock(return_value=None))
    monkeypatch.setattr(manager, "_spawn", Mock())

    manager._watch_loop()
    manager._available_service.assert_not_called()
    manager._spawn.assert_not_called()
    manager.stop()
    manager._spawn.assert_not_called()


def test_dev_child_can_import_source_from_unrelated_working_directory(
    tmp_path, stub_server, monkeypatch
):
    import genshin_app.gui.service_manager as module

    use_fake_popen(monkeypatch, tmp_path, stub_server)
    monkeypatch.delattr(sys, "frozen", raising=False)
    previous_path = str(tmp_path / "existing-pythonpath")
    monkeypatch.setenv("PYTHONPATH", previous_path)
    manager = ServiceManager(tmp_path)
    try:
        manager.ensure_running()
        env = FakePopen.instances[0].kwargs["env"]
        source_root = str(Path(module.__file__).resolve().parents[2])
        assert env["PYTHONPATH"].split(os.pathsep) == [source_root, previous_path]
        assert os.environ["PYTHONPATH"] == previous_path
        # 真正启动一个后台 Python 子进程核对导入来源，不运行 GUI 或数据库服务。
        result = subprocess.run(
            [sys.executable, "-c",
             "import genshin_app.gui.service_manager as m; print(m.__file__)"],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
            timeout=10,
        )
        assert Path(result.stdout.strip()).resolve() == Path(module.__file__).resolve()
    finally:
        manager.stop()


def test_frozen_child_does_not_inject_source_pythonpath(tmp_path, stub_server, monkeypatch):
    use_fake_popen(monkeypatch, tmp_path, stub_server)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    manager = ServiceManager(tmp_path)
    try:
        manager.ensure_running()
        child = FakePopen.instances[0]
        assert child.cmd == [sys.executable, "--data-dir", str(tmp_path), "db", "serve"]
        assert "env" not in child.kwargs
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            assert child.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    finally:
        manager.stop()


def test_frozen_gui_uses_sibling_cli_hidden_from_desktop(tmp_path, stub_server, monkeypatch):
    use_fake_popen(monkeypatch, tmp_path, stub_server)
    binary_dir = tmp_path / "中文 程序目录"
    binary_dir.mkdir()
    console = binary_dir / "GenshinCalc.exe"
    console.write_bytes(b"test placeholder; never executed")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(binary_dir / "GenshinCalcGUI.exe"))
    manager = ServiceManager(tmp_path)
    try:
        manager.ensure_running()
        child = FakePopen.instances[0]
        assert child.cmd == [str(console), "--data-dir", str(tmp_path), "db", "serve"]
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            assert child.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    finally:
        manager.stop()
