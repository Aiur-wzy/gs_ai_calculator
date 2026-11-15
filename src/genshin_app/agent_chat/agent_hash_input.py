"""Pure technical hashes for Agent-provided JSON, without game-fact authority."""
from __future__ import annotations

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError, _json_guard
from genshin_engine.special_reaction_profiles import (
    direct_lunar_damage_profile_content_hash,
    direct_stellar_damage_profile_content_hash,
)

HASH_KINDS = (
    "canonical_json", "direct_stellar_damage_profile", "direct_lunar_damage_profile",
)
TOOL_DESCRIPTION = (
    "对同一份严格JSON输入计算SHA-256校验码。canonical_json使用App规范JSON指纹；"
    "两类direct_*_damage_profile使用引擎现有能力直伤profile算法，只排除其自身profile_content_hash字段。"
    "把原样内容与返回校验码成对提交；内容改变必须重新计算。"
    "本工具不核实或批准游戏事实，不改变status/confirmed、候选、资料或数据库，也不读写文件。"
)
TOOL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": list(HASH_KINDS)},
        "value": {
            "anyOf": [{"type": root} for root in ("object", "array", "string", "number", "boolean", "null")],
            "description": "待计算的严格有限JSON值；两类能力profile必须是对象。",
        },
    },
    "required": ["kind", "value"],
}


def hash_input(kind, value, *, maximum_bytes):
    """Reuse domain hash functions; the caller supplies its existing size limit.

    A digest is only content identity. Even a malformed or falsely confirmed
    profile can be hashed; the original consumer still validates its structure,
    binding, reviewed registration and facts. No authority is created here.
    """
    if type(kind) is not str or kind not in HASH_KINDS:
        raise PlanError("未知校验码类型，请使用工具合同列出的kind。", "agent_tool_input", {"path": "/kind"})
    request = {"kind": kind, "value": value}
    _json_guard(request, "request")
    if len(json_bytes(request)) > maximum_bytes:
        raise PlanError("校验码输入超过当前工具输入容量。", "agent_size_limit")
    excluded = []
    if kind == "canonical_json":
        content_hash = fingerprint(value)
    else:
        if not isinstance(value, dict):
            raise PlanError("能力直伤profile校验码需要JSON对象。", "agent_tool_input", {"path": "/value"})
        compute = (direct_stellar_damage_profile_content_hash
                   if kind == "direct_stellar_damage_profile" else direct_lunar_damage_profile_content_hash)
        content_hash = compute(value)
        excluded = ["profile_content_hash"]
    return {"kind": kind, "algorithm": "sha256", "content_hash": content_hash,
            "excluded_fields": excluded, "game_facts_verified": False, "authority": "none"}
