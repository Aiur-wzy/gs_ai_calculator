"""App-domain compatibility evidence; never a DSH Session or chat gate.

Old messages may be restored by DSH without granting current App execution,
review, approval or delivery rights. A legacy binding cannot acquire today's
identity merely by being opened. This module does not migrate any document.
"""
from __future__ import annotations

import copy
import re

from genshin_app.paths import is_frozen

from .native_compatibility_identity import (
    IDENTITY_CONTRACT,
    SHELL_RELEASE_IDENTITY_CONTRACT,
    current_native_compatibility_identity,
)
from .native_dsh_mux_client import NativeDshMuxError

COMPONENTS = (
    "native_shell_release", "release_pair_identity_hash",
    "app_artifact_identity_hash", "skill_bundle_hash",
)
COMPATIBILITY_REASONS = frozenset({
    "native_app_binding_compatibility_missing",
    "native_app_binding_compatibility_changed",
})
MIGRATION_PROMPT = (
    "请根据当前 App、数据库和拉表说明重新整理我提供的旧排轴文件。"
    "保留我的队伍、配装意图和动作要求；重新核查资料、事件绑定和数值，"
    "完成计算验证后提交候选供我审核。不要直接沿用旧计算接纳、授权或审核凭据。"
)


def _valid(value):
    if (type(value) is not dict or set(value) != {"contract", *COMPONENTS}
            or value["contract"] != IDENTITY_CONTRACT):
        return False
    for key in COMPONENTS[1:]:
        if type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            return False
    shell = value["native_shell_release"]
    if (type(shell) is not dict or set(shell) != {
            "contract", "runtime_generation", "state", "manifest_sha256"}
            or shell["contract"] != SHELL_RELEASE_IDENTITY_CONTRACT
            or shell["runtime_generation"] != "dsh_client_shell"):
        return False
    if shell["state"] == "pending":
        return shell["manifest_sha256"] is None
    return (shell["state"] == "pinned" and type(shell["manifest_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", shell["manifest_sha256"]) is not None)


def current_workspace_identity(data_dir=None):
    value = (current_native_compatibility_identity(data_dir) if data_dir is not None and is_frozen()
             else current_native_compatibility_identity())
    if not _valid(value):
        raise NativeDshMuxError("native_app_binding_compatibility_identity_invalid")
    return copy.deepcopy(value)


def _incompatible(state, recorded, current):
    code = "native_app_binding_compatibility_" + ("missing" if state == "missing" else "changed")
    message = ("这份排轴候选缺少版本证明，暂不能继续计算或应用。聊天和历史仍可使用。"
        if state == "missing" else
        "这份排轴候选来自其他版本，暂不能继续计算或应用。聊天和历史仍可使用。")
    differences = [{"component": key,
        "recorded": None if recorded is None else copy.deepcopy(recorded[key]),
        "current": copy.deepcopy(current[key])}
        for key in COMPONENTS if recorded is None or recorded[key] != current[key]]
    raise NativeDshMuxError(code, failure={"code": code, "message": message, "details": {
        "contract": "genshin-native-domain-incompatibility-v1", "state": state,
        "differences": differences, "migration_prompt": MIGRATION_PROMPT}})


def assert_workspace_compatible(recorded, data_dir=None):
    current = current_workspace_identity(data_dir) if data_dir is not None else current_workspace_identity()
    if recorded is None:
        _incompatible("missing", None, current)
    if not _valid(recorded):
        raise NativeDshMuxError("native_app_binding_compatibility_identity_invalid")
    if recorded != current:
        _incompatible("changed", recorded, current)


def compatibility_issue(error):
    """Return only known domain-unavailable facts; never hide system faults."""
    if not isinstance(error, NativeDshMuxError) or error.code not in COMPATIBILITY_REASONS:
        return None
    return copy.deepcopy(error.failure)
