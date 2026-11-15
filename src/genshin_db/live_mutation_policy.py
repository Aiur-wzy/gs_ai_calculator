"""Policy shared by deprecated public live-database mutation entrypoints.

The low-level stores remain usable by reviewed release builders against an
isolated staging candidate.  Public App, CLI and HTTP entrypoints must not use
them to mutate the accepted database that is currently serving queries.
"""

from __future__ import annotations

from pathlib import Path

POLICY_VERSION = "immutable-accepted-database-v1"
REASON = "accepted_database_is_immutable"
ACCEPTED_RELEASE_MARKERS = (
    "database-live-manifest.json",
    "database-live-receipt.json",
    "release-pair-live-manifest.json",
)


class AcceptedDatabaseImmutableError(PermissionError):
    """低层写入器被误指向已接受的活动数据库目录。"""


def rejection_message(operation: str) -> str:
    """Return a stable, human-readable rejection for one legacy operation."""

    return (
        f"旧数据库写入入口已停用（{operation}）：当前活动数据库是已发布版本，"
        "不能原地修改。请先生成带来源和内容清单的变更提案，取得用户同意后，"
        "在隔离候选中构建并验证完整的新数据库版本，再通过正式迁移切换。"
        "本次请求没有修改数据库。"
    )


def capability(operation: str) -> dict:
    """Describe a retained compatibility name without advertising write access."""

    return {
        "operation": operation,
        "lifecycle": "deprecated",
        "available": False,
        "reason": REASON,
        "policy": POLICY_VERSION,
        "replacement": "reviewed_change_proposal_to_complete_database_release",
        "active_database_modified": False,
    }


def assert_candidate_data_dir(data_dir: str | Path, operation: str) -> Path:
    """候选构建器不得对带活动发行凭据的目录执行。"""

    directory = Path(data_dir).resolve()
    markers = [name for name in ACCEPTED_RELEASE_MARKERS if (directory / name).exists()]
    if markers:
        error = AcceptedDatabaseImmutableError(rejection_message(operation))
        error.details = {
            **capability(operation),
            "accepted_release_markers": markers,
        }
        raise error
    return directory


def assert_candidate_database(db_path: str | Path, operation: str) -> Path:
    """拒绝将低层写函数直接指向已接受的活动库。"""

    path = Path(db_path).resolve()
    assert_candidate_data_dir(path.parent, operation)
    return path
