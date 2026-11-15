"""Fixed-key committed threshold audit; separate bindings authorize dispatch."""
from __future__ import annotations

from genshin_engine.rotation_schema import arr, obj
from genshin_engine.shared_pool_schema import EVIDENCE, ID

THRESHOLD_LIMITS = {"observers": 64, "definition_bytes": 256*1024, "state_bytes": 64*1024,
                    "trace_bytes": 8*1024*1024, "receipts": 40000, "sequence": 10**15,
                    "index_visits": 64*40000}
OBSERVER = obj({"id": ID, "name_zh": ID, "owner": ID, "source_id": ID,
    "input": obj({"kind": {"const": "pool_balance_key"}, "pool_id": ID, "key": ID}, ["kind", "pool_id", "key"]),
    "comparison": {"enum": ["enter_gte", "enter_lte"]},
    "threshold_atoms": {"type": "integer", "minimum": 0, "maximum": 1000000},
    "causes": obj({"operation_ids": {**arr(ID), "maxItems": 256},
                   "consumer_ids": {**arr(ID), "maxItems": 128}}, ["operation_ids", "consumer_ids"]),
    **EVIDENCE}, ["id", "name_zh", "owner", "source_id", "input", "comparison", "threshold_atoms", "causes", "status", "source"])
RESOURCE_THRESHOLD_SCHEMA = obj({"schema_version": {"type": "integer", "const": 1},
    "observers": {**arr(OBSERVER), "minItems": 1, "maxItems": THRESHOLD_LIMITS["observers"]}}, ["schema_version", "observers"])
RESOURCE_THRESHOLD_SCHEMA["x-label"] = "资源余额阈值观察"
RESOURCE_THRESHOLD_SCHEMA["properties"]["schema_version"]["x-label"] = "格式版本"
RESOURCE_THRESHOLD_SCHEMA["properties"]["observers"]["x-label"] = "阈值观察条目"
for _field, _label in {"id": "观察标识", "name_zh": "中文名称", "owner": "提供者", "source_id": "来源部件",
    "input": "固定资源键", "comparison": "进入区间的方向", "threshold_atoms": "阈值原子数", "causes": "允许观察的实际操作",
    "status": "证据状态", "source": "证据说明"}.items():
    OBSERVER["properties"][_field] = {**OBSERVER["properties"][_field], "x-label": _label}
OBSERVER["properties"]["comparison"]["x-enum-labels"] = {"enter_gte": "从低于进入大于等于", "enter_lte": "从高于进入小于等于"}
for _field, _label in {"kind": "读取方式", "pool_id": "资源池", "key": "固定资源键"}.items():
    OBSERVER["properties"]["input"]["properties"][_field] = {**OBSERVER["properties"]["input"]["properties"][_field], "x-label": _label}
OBSERVER["properties"]["input"]["properties"]["kind"]["x-enum-labels"] = {"pool_balance_key": "资源池固定键余额"}
for _field, _label in {"operation_ids": "资源操作", "consumer_ids": "命中消费者"}.items():
    OBSERVER["properties"]["causes"]["properties"][_field] = {**OBSERVER["properties"]["causes"]["properties"][_field], "x-label": _label}
