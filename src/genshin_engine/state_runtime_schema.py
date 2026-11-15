"""Finite lifecycle/timer declarations; no implicit combat or database authority."""
from __future__ import annotations

import copy

from genshin_engine.rotation_schema import arr, obj
from genshin_engine.shared_pool_schema import EVIDENCE, ID, SELECTOR

LIMITS = {"lifecycles": 64, "timers": 128, "hooks": 32, "characters": 16,
          "events": 40000, "time_us": 10**15, "declaration_bytes": 256*1024,
          "trace_bytes": 32*1024*1024, "capture_bytes": 1024*1024}
TIME = {"type": "integer", "minimum": 0, "maximum": LIMITS["time_us"]}
ORDER = {"type": "integer", "minimum": -1000000, "maximum": 1000000}
PAYLOAD = obj({"kind": {"enum": ["dispatch_rule", "resource_operation"]}, "ref_id": ID}, ["kind", "ref_id"])
HOOK = obj({**PAYLOAD["properties"], "order": ORDER}, ["kind", "ref_id", "order"])
HOOKS = obj({key: {**arr(HOOK), "maxItems": LIMITS["hooks"]} for key in ("create", "established", "end")},
            ["create", "established", "end"])
LIFECYCLE = obj({"id": ID, "name_zh": ID, "owner": ID, "source_id": ID,
                 "start": SELECTOR, "end_trigger": SELECTOR, "start_order": ORDER,
                 "establishment_order": ORDER, "end_order": ORDER,
                 "establishment_delay_us": TIME, "duration_us": {**TIME, "minimum": 1},
                 "restart": {"enum": ["replace", "keep", "refresh_duration"]},
                 "pending_cancel": {"enum": ["never_after_success", "source_action_cancel_before_establishment"]},
                 "hooks": HOOKS, **EVIDENCE},
                ["id", "name_zh", "owner", "source_id", "start", "start_order", "establishment_order", "end_order",
                 "establishment_delay_us", "duration_us", "restart", "pending_cancel", "hooks", "status", "source"])
TIMER = obj({"id": ID, "source_id": ID, "lifecycle_id": ID,
             "anchor": {"enum": ["create", "established"]}, "first_offset_us": TIME,
             "interval_us": {**TIME, "minimum": 1}, "max_ticks": {"type": "integer", "minimum": 1, "maximum": LIMITS["events"]},
             "order": ORDER, "end_tick": {"enum": ["exclude", "before_expiry"]},
             "terminal_order": ORDER, "payload": PAYLOAD, **EVIDENCE},
            ["id", "source_id", "lifecycle_id", "anchor", "first_offset_us", "interval_us", "max_ticks", "order", "end_tick", "payload", "status", "source"])
STATE_RUNTIME_SCHEMA = obj({"schema_version": {"type": "integer", "const": 1},
                           "lifecycles": {**arr(LIFECYCLE), "maxItems": LIMITS["lifecycles"]},
                           "timers": {**arr(TIMER), "maxItems": LIMITS["timers"]}},
                          ["schema_version", "lifecycles", "timers"])

THRESHOLD_ENDPOINT = obj({"kind": {"const": "resource_threshold"}, "binding_id": ID}, ["kind", "binding_id"])
THRESHOLD_ENDPOINT["x-label"] = "真实资源阈值端点"
THRESHOLD_ENDPOINT["properties"]["kind"] = {"const": "resource_threshold", "x-label": "触发类型",
    "x-enum-labels": {"resource_threshold": "真实资源越界"}}
THRESHOLD_ENDPOINT["properties"]["binding_id"] = {**ID, "x-label": "阈值联动标识"}
LIFECYCLE_V2 = copy.deepcopy(LIFECYCLE)
for _field in ("start", "end_trigger"):
    LIFECYCLE_V2["properties"][_field] = {"x-label": "建立条件" if _field == "start" else "结束条件",
        "oneOf": [{**copy.deepcopy(SELECTOR), "x-label": "原生事件条件"}, THRESHOLD_ENDPOINT]}
STATE_RUNTIME_V2_SCHEMA = copy.deepcopy(STATE_RUNTIME_SCHEMA)
STATE_RUNTIME_V2_SCHEMA["x-label"] = "有限生命周期与阈值联动"
STATE_RUNTIME_V2_SCHEMA["properties"]["schema_version"]["const"] = 2
STATE_RUNTIME_V2_SCHEMA["properties"]["schema_version"]["x-label"] = "格式版本"
STATE_RUNTIME_V2_SCHEMA["properties"]["lifecycles"]["x-label"] = "生命周期"
STATE_RUNTIME_V2_SCHEMA["properties"]["timers"]["x-label"] = "周期任务"
STATE_RUNTIME_V2_SCHEMA["properties"]["lifecycles"]["items"] = LIFECYCLE_V2


def state_runtime_capabilities():
    return {"contract": "finite-state-clock-v1-v2", "supported_versions": [1, 2],
            "schema": {"oneOf": [copy.deepcopy(STATE_RUNTIME_SCHEMA), copy.deepcopy(STATE_RUNTIME_V2_SCHEMA)]}, "limits": dict(LIMITS),
            "integration": "pure_service_only", "upper_layer_qualification": "not_evaluated",
            "clock": "integer_microseconds_declared_reference", "terminal": "terminal_before_ordinary_with_one_time_preclose_permit",
            "scope": "只调度有限事件并交付指令，不执行FILTER、资源消费、Buff投影、场景空间或数据库审核。",
            "refresh_duration": "已建立状态再次触发时仅延长到期，保留同一代、累计值和计时节奏；真实到期才运行关闭和结束钩子。",
            "run_id": "一次实际源事件出现的唯一ID，不能只用轴轮次。",
            "positioned_clock": "显式v2要求受控位置序列和有界单调索引；纯服务不把调用者位置事实当真实队列或来源许可。",
            "order": "start_order、普通建立/timer order、end_order、事务内terminal_order各自显式；不按来源名称裁决。",
            "boundary": "相对时间和未来可观察状态；调用者须提供未来仍会使用的捕获值及代关联。"}
