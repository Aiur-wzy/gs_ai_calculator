"""Request-local CLI disconnect cancellation, using the engine's existing scope.

This is transport lifetime only. It owns no Agent/Session/Job state or retries.
An edit already committed remains committed; the client must inspect its CAS.
"""
from __future__ import annotations

import select
import socket
import threading
from contextlib import contextmanager
from contextvars import ContextVar

from genshin_engine.execution_control import execution_control_scope

_CURRENT = ContextVar("genshin_cli_request_cancellation", default=None)


class CliRequestCancellation:
    def __init__(self):
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks = []
        self.callback_errors = []

    def register(self, callback):
        with self._lock:
            cancelled = self.event.is_set()
            if not cancelled:
                self._callbacks.append(callback)
        if cancelled:
            callback()

    def cancel(self):
        with self._lock:
            if self.event.is_set():
                return
            self.event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception as error:  # noqa: BLE001 - cancellation still reaches every request
                self.callback_errors.append(type(error).__name__)

    def watch_disconnect(self, connection, finished):
        while not finished.wait(0.1):
            try:
                readable, _, _ = select.select([connection], [], [], 0)
                if readable and connection.recv(1, socket.MSG_PEEK) == b"":
                    self.cancel()
                    return
            except (OSError, ValueError):
                self.cancel()
                return


def current_cli_cancellation():
    return _CURRENT.get()


@contextmanager
def cli_request_scope(control):
    token = _CURRENT.set(control)
    try:
        with execution_control_scope(control.event.is_set):
            yield
    finally:
        _CURRENT.reset(token)
