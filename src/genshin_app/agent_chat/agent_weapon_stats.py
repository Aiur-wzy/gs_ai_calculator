"""Read-only Agent bridge to the existing deterministic weapon growth query.

The caller holds the candidate's database read guard and supplies its trusted
snapshot fingerprint and version line. No selection, panel or session resource
is mutated; refinement belongs to the separate passive-effect query.
"""
from __future__ import annotations

import copy
import re
from collections import OrderedDict
from pathlib import Path
from threading import RLock

from genshin_app.equipment.schema import PlanError
from genshin_db import equipment_queries
from genshin_db.equipment import MAX_PUBLIC_ID

TOOL_DESCRIPTION = (
    "查询当前固定数据库中武器指定等级和突破阶段的基础攻击与成长副属性，"
    "返回规范值、flat/ratio单位、原始计算项及来源。level和ascension必须显式填写；"
    "精炼不影响成长，精炼被动另查详情。比例0.3表示30%额外属性，不是最终面板。"
    "不更改装备或面板，不自动分配词条。"
)
TOOL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "weapon_id": {"type": "integer", "minimum": 1, "maximum": MAX_PUBLIC_ID},
        "level": {"type": "integer", "minimum": 1, "maximum": MAX_PUBLIC_ID},
        "ascension": {"type": "integer", "minimum": 0, "maximum": MAX_PUBLIC_ID},
    },
    "required": ["weapon_id", "level", "ascension"],
}
FORMULA_VERSION = "weapon-growth-v1"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_CACHE_LOCK = RLock()
MAX_CACHE_ENTRIES = 256


def query_weapon_stats(db_path, weapon_id, level, ascension, version_line, *, database_fingerprint):
    """Return a defensive copy; the trusted caller must guard the DB snapshot.

    EquipmentError, including invalid_equipment_state/available_states, passes
    through unchanged for the canonical App error adapter. It is not evidence
    of absent data merely because a request or source query failed.
    """
    for key, value, minimum in (("weapon_id", weapon_id, 1), ("level", level, 1),
                                ("ascension", ascension, 0)):
        if type(value) is not int or not minimum <= value <= MAX_PUBLIC_ID:
            raise PlanError(f"{key}必须是有效整数", "agent_tool_input", {"path": "/" + key})
    if type(database_fingerprint) is not str or _HASH.fullmatch(database_fingerprint) is None:
        raise PlanError("武器成长查询缺少受信数据库身份", "agent_database_identity_invalid")
    if version_line not in {"release", "beta"}:
        raise PlanError("武器成长查询版本线无效", "agent_tool_input", {"path": "/version_line"})
    key = (str(Path(db_path).resolve()), database_fingerprint, version_line,
           weapon_id, level, ascension, FORMULA_VERSION)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return copy.deepcopy(cached)
    result = equipment_queries.get_weapon_stats(db_path, weapon_id, level, ascension, version_line)
    # Cache only complete query responses. Missing/unknown attributes remain
    # explicit in the result; they are never replaced by guessed defaults.
    result = {**copy.deepcopy(result), "database_fingerprint": database_fingerprint}
    with _CACHE_LOCK:
        _CACHE[key] = copy.deepcopy(result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)
    return result
