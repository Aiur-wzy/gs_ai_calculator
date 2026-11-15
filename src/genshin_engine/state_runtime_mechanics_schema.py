"""Closed bridge selectors; routing strings carry no execution authority."""
from __future__ import annotations

import copy

from genshin_engine.rotation_schema import arr, obj
from genshin_engine.shared_pool_schema import ID
from genshin_engine.state_runtime_schema import (
    STATE_RUNTIME_SCHEMA,
    STATE_RUNTIME_V2_SCHEMA,
)

DISPATCH_SELECTOR = obj({
    "event": {"const": "condition"}, "phase": {"enum": ["pre", "post"]},
    "event_ids": {**arr(ID), "minItems": 1, "maxItems": 1},
    "characters": {**arr(ID), "minItems": 1, "maxItems": 16},
    "condition": ID, "owner_presence": {"enum": ["frontline", "any"]},
}, ["event", "phase", "event_ids"])
START_END_EVENTS = frozenset({"action_start", "action_end", "switch", "condition"})
RESERVED_PREFIXES = ("@state:", "@state_dispatch:", "@state_route:")


def state_runtime_mechanics_capabilities():
    from genshin_engine.state_runtime_mechanics import route_event_id
    return {"contract": "finite-state-mechanics-bridge-v1-v2", "supported_versions": [1, 2],
            "schema": {"oneOf": [copy.deepcopy(STATE_RUNTIME_SCHEMA), copy.deepcopy(STATE_RUNTIME_V2_SCHEMA)]},
            "dispatch_selector": copy.deepcopy(DISPATCH_SELECTOR),
            "dispatch_routing": {"contract": "state-runtime-route-id-v1", "authority": "none",
                "target_kinds": ["dispatch_rule", "resource_operation"],
                "event_id_prefix": "@state_route:",
                "hash_input": {"kind": "canonical_json", "value_shape": ["target_kind", "exact_target_ref_id"]},
                "construction": "用App engine hash-input或gs_hash_input计算[kind,ref_id]的canonical_json哈希，在content_hash前加event_id_prefix；trigger.event=condition、phase按实际pre/post、event_ids仅该单一身份。",
                "example": {"kind": "resource_operation", "ref_id": "operation.example",
                    "event_id": route_event_id("resource_operation", "operation.example")},
                "approval": "身份只定位路由；仍须唯一同源hook/timer、提供者归属及真实桥签发对象，不授权手工condition伪造派发。"},
            "engine_queue_integration": "mechanics_event_queue",
            "source_approval": "not_evaluated_by_engine",
            "start_end_events": sorted(START_END_EVENTS),
            "scope": "已声明有限timer路由至同源refresh规则或共享池set/grant/spend；显式state2与threshold3允许真实资源越界建立/结束有限生命周期。无连续游戏时钟或自动来源批准。",
            "lifecycle_thresholds": "仅完整已核typed端点及有限目标图；普通timer原批实际完成后再处理目标，每个目标的控制批须实际完成。无counter时也按真实工作计公共预算。",
            "result": "clock2分别保留issued与actual execution/applied_lifecycles；失败只保真实前缀，不把领取或签发作为生效证明。",
            "internal_transport": "保留condition路由兼容既有FILTER；必须同时持有本次桥签发的对象授权，字符串不是权限。"}
