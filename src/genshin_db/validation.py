"""共享查询/HTTP 输入校验；避免 SQLite 隐式转换和未处理的类型异常。"""

from __future__ import annotations

VERSION_LINES = ("release", "beta")
TARGET_TYPES = ("character", "skill", "talent", "skill_param", "inherent_skill",
                "weapon", "artifact_set", "artifact", "equip_affix")
ALL_STATUS = ("draft", "pending", "approved", "rejected", "orphan")
MAX_SQLITE_INT = 2**63 - 1


class QueryValidationError(ValueError):
    """Request producer's explicit validation error, retaining ValueError ABI."""

    reason = "invalid_query_request"


def positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise QueryValidationError(f"{name} 必须为正整数")
    if isinstance(value, str) and (not value.isascii() or not value.isdecimal()):
        raise QueryValidationError(f"{name} 必须为正整数")
    result = int(value)
    if not 1 <= result <= MAX_SQLITE_INT:
        raise QueryValidationError(f"{name} 超出正整数范围")
    return result


def text_field(value, name: str, required: bool = False) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise QueryValidationError(f"{name} 必须为{'非空' if required else ''}文本")
    return value


def enum_value(value, name: str, choices: tuple, nullable: bool = False):
    if nullable and value is None:
        return value
    if not isinstance(value, str) or value not in choices:
        raise QueryValidationError(f"非法 {name}（可选 {', '.join(choices)}）")
    return value


def version_line(value: str | None, nullable: bool = False) -> str | None:
    return enum_value(value, "version_line", VERSION_LINES, nullable)
