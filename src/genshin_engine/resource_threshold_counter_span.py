"""Check detached counter facts against one fully validated resource span.

No second plan_spend call or consumption occurs. The real ledger supplies its
own pre-state and prepared candidate; pure callers only get arithmetic checks,
never a capability. Reset/control transitions cannot be hidden in this span.
"""
from __future__ import annotations

import copy

from genshin_engine.resource_counter_schema import COUNTER_LIMITS
from genshin_engine.shared_pools import SharedPoolError, _canonical


def validate_counter_span(program, span, resources, start, end):
    from genshin_engine.resource_thresholds import ResourceThresholdError
    def fail(message):
        raise ResourceThresholdError("threshold_counter_span", message, "resource_threshold_observers")
    try:
        if not isinstance(span, dict) or set(span) != {"before", "after", "receipts"}:
            fail("累计附属事实必须包含完整前后状态及流水")
        before, after = program._state(span["before"]), program._state(span["after"])
        rows = span["receipts"]
        if (type(rows) is not list or len(rows) > 16*COUNTER_LIMITS["counters"]
                or len(_canonical(rows)) > 8*1024*1024
                or before["last_ledger_sequence"] != start-1 or after["last_ledger_sequence"] != end):
            fail("累计前后游标或流水范围不等于本笔资源span")
        expected, expected_rows, visits = copy.deepcopy(before), [], 0
        for offset, resource in enumerate(resources):
            if before["last_time_us"] is not None and resource["t_us"] < before["last_time_us"]:
                fail("资源不能倒退到已提交累计控制之前")
            if resource["type"] == "operation" and resource["operation"] == "spend":
                for ident, counter in program._counters.items():
                    visits += 1
                    if resource["operation_id"] not in counter["operation_ids"]:
                        continue
                    life = before["lifecycles"][counter["lifecycle_id"]]
                    window = program._spend_window(counter, life, resource, before["active_dispatch"])
                    key = "total" if counter["scope"] == "pool_total" else resource["key"]
                    first = expected["values"][ident][key]
                    accepted = resource["accepted"] if window else 0
                    final = first+accepted
                    if final > COUNTER_LIMITS["value"]:
                        fail("累计候选原子值超限")
                    expected["values"][ident][key] = final
                    expected_rows.append({"type": "counter_accumulation", "counter_id": ident, "source_id": counter["source_id"],
                        "resource_receipt_id": resource["id"], "ledger_sequence": start+offset, "pool_id": counter["pool_id"], "key": key,
                        "requested": resource["requested"], "resource_accepted": resource["accepted"], "accepted": accepted,
                        "before": first, "after": final, "unit_scale": resource["unit_scale"], "quantity_unit": "atoms",
                        "generation_uid": life["generation_uid"], "reason": "accumulated" if window else "outside_window",
                        "time_us": resource["t_us"], "lifecycle_id": counter["lifecycle_id"],
                        "history_scope": "lifetime_total" if counter["reset_on"] == "never" else "since_last_reset"})
            expected["last_ledger_sequence"] = start+offset
            expected["last_time_us"] = resource["t_us"]
        # Canonical equality retains bool/int and complete row/field distinctions.
        if _canonical(expected_rows) != _canonical(rows) or _canonical(expected) != _canonical(after):
            fail("累计流水或最终状态不符合本笔实际资源、代窗口及控制位置")
        return before, after, visits
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError, ArithmeticError) as exc:
        if isinstance(exc, SharedPoolError):
            raise
        fail("累计附属事实不是完整有界结构")
