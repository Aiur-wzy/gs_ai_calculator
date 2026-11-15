"""Compatibility import for older Agent-chat consumers."""

from genshin_app.task_capability import (
    CONTRACT,
    blocking_task_issues,
    classify_task_issues,
    normalize_task_issue,
)

__all__ = [
    "CONTRACT", "blocking_task_issues", "classify_task_issues",
    "normalize_task_issue",
]
