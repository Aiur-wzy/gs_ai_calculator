"""Pure counter definitions; public mechanics support is wired separately."""
from __future__ import annotations

from genshin_engine.rotation_schema import arr, obj
from genshin_engine.shared_pool_schema import EVIDENCE, ID

COUNTER_LIMITS = {"counters": 64, "value": 10**15, "definition_bytes": 256*1024, "state_bytes": 4*1024*1024}
COUNTER = obj({"id": ID, "name_zh": ID, "owner": ID, "source_id": ID, "pool_id": ID,
               "operation_ids": {**arr(ID), "minItems": 1, "maxItems": 256},
               "scope": {"enum": ["pool_key", "pool_total"]}, "lifecycle_id": ID,
               "accumulate_window": {"enum": ["created_until_end", "established_until_end"]},
               "reset_on": {"enum": ["create", "end", "never"]}, **EVIDENCE},
              ["id", "name_zh", "owner", "source_id", "pool_id", "operation_ids", "scope", "lifecycle_id",
               "accumulate_window", "reset_on", "status", "source"])
RESOURCE_COUNTER_SCHEMA = obj({"schema_version": {"type": "integer", "const": 1},
                               "counters": {**arr(COUNTER), "maxItems": COUNTER_LIMITS["counters"]}},
                              ["schema_version", "counters"])
RESOURCE_COUNTER_SCHEMA["x-label"] = "资源消费累计"
RESOURCE_COUNTER_SCHEMA["properties"]["counters"]["x-label"] = "累计条目"
COUNTER["x-label"] = "累计规则"
for _field, _label in {"id": "累计标识", "name_zh": "中文名称", "owner": "提供者",
        "source_id": "来源部件标识", "pool_id": "资源池", "operation_ids": "计入累计的扣除操作",
        "scope": "累计范围", "lifecycle_id": "关联生命周期", "accumulate_window": "累计有效阶段",
        "reset_on": "清零时机", "status": "证据状态", "source": "证据说明"}.items():
    COUNTER["properties"][_field] = {**COUNTER["properties"][_field], "x-label": _label}
COUNTER["properties"]["scope"]["x-enum-labels"] = {"pool_key": "每个资源键分别累计", "pool_total": "整个资源池统一累计"}
COUNTER["properties"]["accumulate_window"]["x-enum-labels"] = {
    "created_until_end": "从创建至结束", "established_until_end": "从建立生效至结束"}
COUNTER["properties"]["reset_on"]["x-enum-labels"] = {
    "create": "新一代创建前清零", "end": "本代结束操作完成后清零", "never": "跨代保留累计"}
