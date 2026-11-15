"""Isolate optional observation failures, without swallowing cancellation."""
from __future__ import annotations

import copy

from genshin_engine.execution_control import ExecutionCancelled


def observe_advisory(name, callback, *, fallback):
    try:
        return callback()
    except ExecutionCancelled:
        raise
    except Exception as error:  # noqa: BLE001 - optional evidence cannot revoke calculation
        result = copy.deepcopy(fallback)
        result.update(state="observation_unavailable", authority="advisory_only",
                      acceptance_effect="none", candidate_effect="none",
                      observation_error={"component": name, "type": type(error).__name__,
                          "message": "辅助检查未完成；不能据此判断没有遗漏，也不改变主计算资格。"})
        return result
