"""The hidden GUI entry reports GUI failures without popping up background errors."""

import ctypes
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def entry(monkeypatch):
    # Import without running __main__ or modifying the test runner's import path.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    spec = importlib.util.spec_from_file_location(
        "genshin_test_entry", Path(__file__).resolve().parents[1] / "app_entry.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("argv,expected", [
    (["gui"], True),
    (["--pretty", "--data-dir", "中文 空格", "gui", "--open", "轴.json"], True),
    (["--data-dir=中文 空格", "gui"], True),
    (["--data-dir", "gui", "db", "serve"], False),
    (["--data-dir=gui", "db", "serve"], False),
    (["shortcut", "create", "--input", "gui"], False),
])
def test_gui_command_detection_skips_global_option_values(entry, argv, expected):
    assert entry._gui_invocation(argv) is expected


@pytest.mark.parametrize("argv,code,visible", [
    ([], 1, True),
    (["gui", "--open", "失效 文件.json"], 1, True),
    (["--data-dir", "中文 数据", "gui"], 1, True),
    (["gui"], 0, False),
    (["db", "serve"], 1, False),
    (["--data-dir", "gui", "db", "serve"], 1, False),
    (["capabilities"], 1, False),
])
def test_hidden_entry_only_shows_interactive_gui_failure(entry, monkeypatch, argv, code, visible):
    message_box = Mock()
    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", message_box)
    else:
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(MessageBoxW=message_box)), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["GenshinCalcGUI.exe", *argv])
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    def failed_main(received):
        assert received == (argv or ["gui"])
        print("启动失败：测试诊断", file=sys.stderr)
        return code

    monkeypatch.setattr(entry, "main", failed_main)
    assert entry.entry_main() == code
    assert message_box.call_count == int(visible)
    if visible:
        assert "测试诊断" in message_box.call_args.args[1]


def test_console_entry_keeps_error_on_console(entry, monkeypatch, capsys):
    message_box = Mock()
    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", message_box)
    else:
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(MessageBoxW=message_box)), raising=False)
    monkeypatch.setattr(sys, "argv", ["GenshinCalc.exe", "gui"])

    def failed_main(_):
        print("console failure", file=sys.stderr)
        return 1

    monkeypatch.setattr(entry, "main", failed_main)
    assert entry.entry_main() == 1
    assert "console failure" in capsys.readouterr().err
    message_box.assert_not_called()


def test_missing_portable_identity_explains_full_extraction(entry, monkeypatch, capsys):
    from genshin_app.product_install_contract import FirstInstallError
    monkeypatch.setattr(sys, "argv", ["GenshinCalc.exe", "db", "catalog"])
    def missing(_):
        raise FirstInstallError("portable_layout_missing")
    monkeypatch.setattr(entry, "main", missing)
    assert entry.entry_main() == 2
    message = capsys.readouterr().err
    assert "完整便携包" in message and "app 和 data" in message
    assert "正在维护" not in message
