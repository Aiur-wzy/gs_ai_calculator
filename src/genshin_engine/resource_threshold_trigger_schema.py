"""Authenticated operation-tail constant provider or finite lifecycle triggers."""
from __future__ import annotations

import copy

from genshin_engine.rotation_schema import arr, obj
from genshin_engine.shared_pool_schema import EVIDENCE, ID

THRESHOLD_TRIGGER = obj({"kind": {"const": "resource_threshold"}, "binding_id": ID}, ["kind", "binding_id"])
THRESHOLD_BINDING = obj({"id": ID, "name_zh": ID, "owner": ID, "source_id": ID, "observer_id": ID,
    "order": {"type": "integer", "minimum": 0, "maximum": 1000000},
    "qualification": obj({"condition": ID, "owner_presence": {"enum": ["any", "frontline"]}}, []),
    "payload": obj({"kind": {"const": "activate_rule"}, "ref_id": ID}, ["kind", "ref_id"]), **EVIDENCE},
    ["id", "name_zh", "owner", "source_id", "observer_id", "order", "payload", "status", "source"])
RESOURCE_THRESHOLD_TRIGGER_SCHEMA = obj({"schema_version": {"type": "integer", "const": 1},
    "bindings": {**arr(THRESHOLD_BINDING), "minItems": 1, "maxItems": 64}}, ["schema_version", "bindings"])
RESOURCE_THRESHOLD_TRIGGER_SCHEMA["x-label"] = "资源阈值触发收益"
RESOURCE_THRESHOLD_TRIGGER_SCHEMA["properties"]["schema_version"]["x-label"] = "格式版本"
RESOURCE_THRESHOLD_TRIGGER_SCHEMA["properties"]["bindings"]["x-label"] = "阈值联动条目"
THRESHOLD_TRIGGER["x-label"] = "通过真实资源越界触发"
THRESHOLD_TRIGGER["properties"]["kind"]["x-label"] = "触发类型"
THRESHOLD_TRIGGER["properties"]["kind"]["x-enum-labels"] = {"resource_threshold": "真实资源阈值"}
THRESHOLD_TRIGGER["properties"]["binding_id"] = {**THRESHOLD_TRIGGER["properties"]["binding_id"], "x-label": "阈值联动标识"}
for _field, _label in {"id": "联动标识", "name_zh": "中文名称", "owner": "提供者", "source_id": "来源部件",
    "observer_id": "阈值观察", "order": "同一事务的触发顺序", "qualification": "当前资格", "payload": "目标收益",
    "status": "证据状态", "source": "证据说明"}.items():
    THRESHOLD_BINDING["properties"][_field] = {**THRESHOLD_BINDING["properties"][_field], "x-label": _label}
for _group, _fields in {"qualification": {"condition": "外部条件", "owner_presence": "提供者位置"},
                        "payload": {"kind": "执行任务", "ref_id": "目标规则"}}.items():
    for _field, _label in _fields.items():
        _properties = THRESHOLD_BINDING["properties"][_group]["properties"]
        _properties[_field] = {**_properties[_field], "x-label": _label}
THRESHOLD_BINDING["properties"]["qualification"]["properties"]["owner_presence"]["x-enum-labels"] = {"any": "前后台均可", "frontline": "仅前台"}
THRESHOLD_BINDING["properties"]["payload"]["properties"]["kind"]["x-enum-labels"] = {"activate_rule": "激活或刷新收益规则"}

# Explicit opt-in for fixed counter keys and uniquely owned ordinary timers.
RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA = copy.deepcopy(RESOURCE_THRESHOLD_TRIGGER_SCHEMA)
RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA["properties"]["schema_version"]["const"] = 2

RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA = copy.deepcopy(RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA)
RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA["properties"]["schema_version"]["const"] = 3
RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA["properties"]["bindings"]["items"]["properties"]["payload"]["properties"]["kind"] = {
    "enum": ["activate_rule", "start_lifecycle", "end_lifecycle"], "x-label": "执行任务",
    "x-enum-labels": {"activate_rule": "激活或刷新收益规则", "start_lifecycle": "建立有限生命周期", "end_lifecycle": "结束有限生命周期"}}
RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA["properties"]["bindings"]["items"]["properties"]["payload"]["properties"]["ref_id"]["x-label"] = "目标规则或生命周期"

# Explicit reaction-tail extension; old versions retain their origin scope.
RESOURCE_THRESHOLD_TRIGGER_V4_SCHEMA = copy.deepcopy(RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA)
RESOURCE_THRESHOLD_TRIGGER_V4_SCHEMA['properties']['schema_version']['const'] = 4
