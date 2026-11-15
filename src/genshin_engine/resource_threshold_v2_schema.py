"""Explicit v2 fixed counter-key observation; timer delivery uses its own contract."""
from __future__ import annotations

import copy

from genshin_engine.resource_counter_schema import COUNTER_LIMITS
from genshin_engine.resource_threshold_schema import OBSERVER, THRESHOLD_LIMITS
from genshin_engine.rotation_schema import arr, obj
from genshin_engine.shared_pool_schema import ID

COUNTER_OBSERVER = copy.deepcopy(OBSERVER)
COUNTER_OBSERVER["properties"]["input"] = {"oneOf": [copy.deepcopy(OBSERVER["properties"]["input"]),
    obj({"kind": {"const": "counter_value_key"}, "counter_id": ID, "key": ID}, ["kind", "counter_id", "key"])]}
COUNTER_OBSERVER["properties"]["threshold_atoms"] = {"type": "integer", "minimum": 0,
    "maximum": COUNTER_LIMITS["value"], "x-label": "阈值原子数"}
RESOURCE_THRESHOLD_V2_SCHEMA = obj({"schema_version": {"type": "integer", "const": 2},
    "observers": {**arr(COUNTER_OBSERVER), "minItems": 1, "maxItems": THRESHOLD_LIMITS["observers"]}}, ["schema_version", "observers"])
RESOURCE_THRESHOLD_V2_SCHEMA["x-label"] = "资源余额或累计阈值观察"
RESOURCE_THRESHOLD_V2_SCHEMA["properties"]["schema_version"]["x-label"] = "格式版本"
RESOURCE_THRESHOLD_V2_SCHEMA["properties"]["observers"]["x-label"] = "阈值观察条目"
COUNTER_OBSERVER["properties"]["input"]["x-label"] = "观察的资源数值"
_counter_input = COUNTER_OBSERVER["properties"]["input"]["oneOf"][1]
_counter_input["x-label"] = "累计资源固定键"
for _field, _label in {"kind": "读取方式", "counter_id": "累计账标识", "key": "固定累计键"}.items():
    _counter_input["properties"][_field] = {**_counter_input["properties"][_field], "x-label": _label}
_counter_input["properties"]["kind"]["x-enum-labels"] = {"counter_value_key": "实际累计的固定键"}
