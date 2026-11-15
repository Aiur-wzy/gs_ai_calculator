"""Closing must release the UI thread before asking WebView about dirty input."""
import threading
from types import SimpleNamespace

import pytest

from genshin_app.gui.api import GuiApi
from genshin_app.gui.launcher import install_close_guard


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


@pytest.mark.parametrize("initialized", [True, False])
def test_failed_editor_query_preserves_initialized_input(tmp_path, initialized):
    window, api = setup(tmp_path, RuntimeError("WebView unavailable"))
    api._editor_initialized = initialized
    window.release.set()
    assert window.events.closing.handler() is False
    assert window.done.wait(3)
    assert window.closed is not initialized
    assert bool(window.warning) is initialized
