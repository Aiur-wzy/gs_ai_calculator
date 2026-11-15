"""Optional context-local cancellation for bounded, synchronous calculations.

No timeout or thread is created here. The caller owns its cancellation signal
and must enter a scope in the actual execution thread, not a UI thread.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_CANCEL_CHECK = ContextVar('genshin_execution_cancel_check', default=None)


class ExecutionCancelled(ValueError):
    def __init__(self):
        self.issue = {'code': 'calculation_cancelled', 'message': '计算已取消；请重新发起完整计算',
                      'path': 'execution', 'severity': 'cancelled'}
        self.reason = 'calculation_cancelled'
        self.details = self.issue
        super().__init__(self.issue['message'])


def check_execution_cancelled():
    check = _CANCEL_CHECK.get()
    if check is not None and check():
        raise ExecutionCancelled()


@contextmanager
def execution_control_scope(cancel_check):
    if cancel_check is not None and not callable(cancel_check):
        raise TypeError('cancel_check must be callable or None')
    token = _CANCEL_CHECK.set(cancel_check)
    try:
        check_execution_cancelled()
        yield
    finally:
        _CANCEL_CHECK.reset(token)
