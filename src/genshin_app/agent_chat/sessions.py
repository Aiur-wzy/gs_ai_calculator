"""Bounded session-owned storage; callers never supply model-selected paths."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES
from genshin_app.equipment.files import json_bytes, publish_files
from genshin_app.equipment.schema import PlanError

MAX_CHECKPOINT = 1024 * 1024
MAX_NATIVE = 8 * 1024 * 1024
MAX_CALCULATED = CALCULATED_ARTIFACT_BYTES


def safe_path(root, name):
    root = Path(root).absolute()
    if (not isinstance(name, str) or not name or Path(name).name != name or name in (".", "..")
            or any(char in name for char in '\\/:\x00') or name.endswith((".", " "))):
        raise PlanError("会话文件名不合法", "agent_path_rejected")
    target = root / name
    for item in [*root.parents, root, target]:
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if item.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise PlanError("会话目录不允许链接或重解析点", "agent_path_rejected")
        if item.is_file() and info.st_nlink > 1:
            raise PlanError("会话文件不允许硬链接", "agent_path_rejected")
    if os.path.commonpath((str(root.resolve()), str(target.resolve()))) != str(root.resolve()):
        raise PlanError("会话文件不能越出目录", "agent_path_rejected")
    return target


def read_owned(root, name, maximum):
    path = safe_path(root, name)
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise PlanError("会话文件超过读取限额", "agent_size_limit")
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError("会话文件不是有效JSON", "agent_checkpoint_invalid") from exc


def write_owned(root, name, value, maximum):
    raw = json_bytes(value)
    if len(raw) > maximum:
        raise PlanError("会话文件超过保存限额", "agent_size_limit")
    target = safe_path(root, name)
    safe_path(root, name + ".previous")
    safe_path(root, "." + name + ".plan.lock")
    publish_files([(target, raw)], overwrite=True)
    return target
