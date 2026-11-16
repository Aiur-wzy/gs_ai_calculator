"""Closing must release the UI thread before asking WebView about dirty input."""
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app.gui.api import GuiApi
from genshin_app.gui.launcher import _EDITOR_CLOSE_SCRIPT, install_close_guard


class ClosingEvent:
    def __iadd__(self, handler):
        self.handler = handler
        return self


class Window:
    def __init__(self, answer=True):
        self.events = SimpleNamespace(closing=ClosingEvent())
        self.answer = answer
        self.entered = threading.Event()
        self.release = threading.Event()
        self.done = threading.Event()
        self.calls = []
        self.closed = False
        self.warning = None

    def evaluate_js(self, script):
        self.calls.append(threading.get_ident())
        self.entered.set()
        assert self.release.wait(3)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def destroy(self):
        self.closed = self.events.closing.handler()
        self.done.set()

    def create_confirmation_dialog(self, title, message):
        self.warning = (title, message)
        self.done.set()


def setup(tmp_path, answer=True):
    window, api = Window(answer), GuiApi(tmp_path)
    api._window = window
    install_close_guard(window, api)
    return window, api


def test_close_query_is_off_ui_thread_and_duplicate_requests_are_coalesced(tmp_path):
    window, api = setup(tmp_path)
    assert window.events.closing.handler() is False
    assert window.entered.wait(3)
    assert window.events.closing.handler() is False
    assert len(window.calls) == 1
    assert window.calls[0] != threading.get_ident()
    assert not window.closed
    window.release.set()
    assert window.done.wait(3)
    assert window.closed and api._closing_approved


def test_dirty_editor_decides_when_to_confirm_close(tmp_path):
    window, api = setup(tmp_path, False)
    assert window.events.closing.handler() is False
    assert window.entered.wait(3)
    window.release.set()
    assert not api._closing_approved
    # The JS leave dialog can cancel indefinitely; only its explicit approval closes.
    api.confirm_close()
    assert window.done.wait(3) and window.closed


def test_close_failure_message_is_visible_and_does_not_destroy_window(tmp_path):
    window, api = setup(tmp_path)
    api.confirm_close = lambda: {"code": 1, "message": "聊天草稿尚未读取完成，请重试。"}
    window.release.set()
    assert window.events.closing.handler() is False
    assert window.done.wait(3)
    assert not window.closed and not api._closing_approved
    assert window.warning == ("暂未退出应用", "聊天草稿尚未读取完成，请重试。")


@pytest.mark.parametrize("initialized", [True, False])
def test_failed_editor_query_preserves_initialized_input(tmp_path, initialized):
    window, api = setup(tmp_path, RuntimeError("WebView unavailable"))
    api._editor_initialized = initialized
    window.release.set()
    assert window.events.closing.handler() is False
    assert window.done.wait(3)
    assert window.closed is not initialized
    assert bool(window.warning) is initialized


@pytest.mark.parametrize(("setup_script", "expected"), [
    ("globalThis.window={WB:{},PL:{}};", "true"),
    ("globalThis.window={WB:{requestClose:()=>false}};", "false"),
    ("globalThis.window={WB:{},PL:{requestClose:()=>false}};", "false"),
    ("globalThis.window={WB:{requestClose:()=>true},PL:{requestClose:()=>false}};", "true"),
])
def test_close_checks_only_initialized_editor_functions(setup_script, expected):
    node = Path(__file__).resolve().parents[1] / "agent-runtime" / "node.exe"
    result = subprocess.run(
        [str(node), "-e", setup_script + "console.log(" + _EDITOR_CLOSE_SCRIPT + ")"],
        capture_output=True, text=True, check=True, timeout=15,
    )
    assert result.stdout.strip() == expected
