"""Exact counter views; runtime authority is supplied by the mechanics bridge."""
from __future__ import annotations

from genshin_engine.rotation_schema import arr, obj
from genshin_engine.shared_pool_schema import EVIDENCE, ID

VIEW_LIMITS = {"views": 64, "definition_bytes": 256*1024, "value": 10**15}
POSITIVE = {"type": "integer", "minimum": 1, "maximum": VIEW_LIMITS["value"]}
VIEW_KEY = {"oneOf": [
    obj({"kind": {"const": "total"}}, ["kind"]),
    obj({"kind": {"const": "owner"}}, ["kind"]),
    obj({"kind": {"const": "recipient"}}, ["kind"]),
    obj({"kind": {"const": "character"}, "character_id": ID}, ["kind", "character_id"]),
]}
TRANSFORM = {"oneOf": [
    obj({"kind": {"const": "scaled"}, "max_atoms": POSITIVE}, ["kind"]),
    obj({"kind": {"const": "steps"}, "step_atoms": POSITIVE, "max_steps": POSITIVE}, ["kind", "step_atoms"]),
]}
COUNTER_VIEW = obj({"id": ID, "name_zh": ID, "owner": ID, "source_id": ID, "counter_id": ID,
                    "key": VIEW_KEY, "transform": TRANSFORM, **EVIDENCE},
                   ["id", "name_zh", "owner", "source_id", "counter_id", "key", "transform", "status", "source"])
RESOURCE_COUNTER_VIEW_SCHEMA = obj({"schema_version": {"type": "integer", "enum": [1, 2]},
    "views": {**arr(COUNTER_VIEW), "maxItems": VIEW_LIMITS["views"]}}, ["schema_version", "views"])

# Clone reused scalar contracts before adding presentation metadata.
for key, label in {"id": "读数标识", "name_zh": "中文名称", "owner": "提供角色",
                   "source_id": "来源部件", "counter_id": "累计账标识", "key": "读取范围",
                   "transform": "换算与封顶", "status": "核验状态", "source": "依据说明"}.items():
    COUNTER_VIEW["properties"][key] = {**COUNTER_VIEW["properties"][key], "x-label": label}
for schema, label in zip(VIEW_KEY["oneOf"], ("累计总额", "提供者资源键", "当前受益者资源键", "指定角色资源键"), strict=True):
    schema["x-label"] = label
    schema["properties"]["kind"]["x-label"] = "读取范围"
    schema["properties"]["kind"]["x-enum-labels"] = {schema["properties"]["kind"]["const"]: label}
    if "character_id" in schema["properties"]:
        schema["properties"]["character_id"] = {**schema["properties"]["character_id"], "x-label": "指定角色"}
for schema, label in zip(TRANSFORM["oneOf"], ("按资源单位换算", "整数档位"), strict=True):
    schema["x-label"] = label
    schema["properties"]["kind"].update({"x-label": "读数算法", "x-enum-labels": {schema["properties"]["kind"]["const"]: label}})
    for key in schema["properties"].keys()-{"kind"}:
        schema["properties"][key] = {**schema["properties"][key], "x-label": {"max_atoms": "原子数封顶", "step_atoms": "每档原子数", "max_steps": "最高档位"}[key]}
COUNTER_VIEW["x-label"] = "累计资源读数"
RESOURCE_COUNTER_VIEW_SCHEMA["x-label"] = "资源累计读数"
RESOURCE_COUNTER_VIEW_SCHEMA["properties"]["schema_version"]["x-label"] = "读数格式版本"
RESOURCE_COUNTER_VIEW_SCHEMA["properties"]["views"]["x-label"] = "读数定义"
