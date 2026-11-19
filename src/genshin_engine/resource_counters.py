"""Plan counter changes without committing balances, a clock, or combat effects.

The owner must verify ordered lifecycle authority and use authentic, fresh ledger
receipts. ``ledger_sequence`` is the resource ledger's contiguous receipt index,
not an Agent-supplied event ID. This planner checks structure and accounting, not
that an external caller has obtained genuine game events. Its candidates must be
budgeted with balances before either side is committed; no integration is implied.
"""
from __future__ import annotations

import copy
import hashlib

from genshin_engine.resource_counter_schema import (
    COUNTER_LIMITS,
    RESOURCE_COUNTER_SCHEMA,
)
from genshin_engine.shared_pools import (
    SharedPoolError,
    _canonical,
    _schema,
    pool_capacity_limit,
    pool_capacity_mode,
    validate_shared_pools,
)
from genshin_engine.state_runtime import validate_state_runtime


class ResourceCounterError(SharedPoolError):
    pass


def _fail(code, message, **details):
    raise ResourceCounterError(code, message, "resource_counters", **details)


def _int(value, maximum=COUNTER_LIMITS["value"]):
    return type(value) is int and 0 <= value <= maximum


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 512


class CounterProgram:
    """Immutable definitions and detached transaction planning, no second ledger."""

    def __init__(self, config, pools, lifecycles, *, character_ids):
        try:
            if len(_canonical(config)) > COUNTER_LIMITS["definition_bytes"]:
                _fail("counter_limit", "累计声明字节超限")
        except (TypeError, ValueError, RecursionError, OverflowError) as exc:
            if isinstance(exc, ResourceCounterError): raise
            _fail("counter_schema", "累计声明不是有界JSON")
        issues = []
        _schema(config, RESOURCE_COUNTER_SCHEMA, "resource_counters", issues)
        if issues: _fail("counter_schema", "累计声明结构无效", issues=issues)
        if not isinstance(pools, dict) or pools.get("schema_version") != 2:
            _fail("counter_unit", "首片累计需要显式原子计量v2")
        issues = validate_shared_pools(pools, character_ids=character_ids)
        issues += validate_state_runtime(lifecycles, character_ids=character_ids)
        if issues: _fail("counter_reference", "累计依赖的池或生命周期无效", issues=issues)
        self._config, self._pool_config = copy.deepcopy(config), copy.deepcopy(pools)
        self._pools = {p["id"]: p for p in self._pool_config["pools"]}
        self._ops = {p["id"]: p for p in self._pool_config["operations"]}
        self._consumers = {p["id"]: p for p in self._pool_config["consumers"]}
        self._lives = {p["id"]: copy.deepcopy(p) for p in lifecycles["lifecycles"]}
        self._counters, identities = {}, set()
        for counter in sorted(self._config["counters"], key=lambda row: row["id"]):
            ident = counter["id"]
            identity = (counter["owner"], counter["source_id"], counter["pool_id"], counter["scope"])
            if ident in self._counters or identity in identities:
                _fail("counter_duplicate", "累计ID或同源同范围身份重复")
            pool, life = self._pools.get(counter["pool_id"]), self._lives.get(counter["lifecycle_id"])
            if pool is None or life is None or counter["owner"] != pool["owner"] or counter["owner"] != life["owner"]:
                _fail("counter_owner", "累计必须关联同提供者的池与生命周期")
            if len(set(counter["operation_ids"])) != len(counter["operation_ids"]):
                _fail("counter_operation", "累计操作引用重复")
            for operation_id in counter["operation_ids"]:
                op = self._ops.get(operation_id)
                if op is None or op["pool_id"] != pool["id"] or op["operation"] != "spend":
                    _fail("counter_operation", "累计只接受同一池的明确spend操作")
            if counter["status"] == "pending": _fail("counter_pending", "累计声明仍待核验", severity="pending")
            self._counters[ident] = counter
            identities.add(identity)
        self._fingerprint = hashlib.sha256(_canonical([config, pools, lifecycles])).hexdigest()

    def initial_state(self):
        return {"definition_fingerprint": self._fingerprint, "last_control_sequence": 0, "last_ledger_sequence": 0,
                "last_time_us": None, "active_dispatch": None,
                "values": {ident: dict.fromkeys(self._keys(c), 0) for ident, c in self._counters.items()},
                "lifecycles": {ident: {"phase": "inactive", "generation_uid": None, "revision": None,
                    "created_us": None, "established_us": None, "end_us": None,
                    "established_observed": False, "closing_us": None} for ident in self._lives}}

    def _keys(self, counter):
        return ["total"] if counter["scope"] == "pool_total" else sorted(self._pools[counter["pool_id"]]["initial"])

    def _state(self, state):
        try:
            if len(_canonical(state)) > COUNTER_LIMITS["state_bytes"]: _fail("counter_limit", "累计状态字节超限", severity="pending")
            if (set(state) != {"definition_fingerprint", "last_control_sequence", "last_ledger_sequence", "values", "lifecycles", "last_time_us", "active_dispatch"}
                    or state["definition_fingerprint"] != self._fingerprint
                    or not _int(state["last_control_sequence"]) or not _int(state["last_ledger_sequence"])
                    or state["last_time_us"] is not None and not _int(state["last_time_us"])
                    or set(state["values"]) != set(self._counters) or set(state["lifecycles"]) != set(self._lives)):
                _fail("counter_state", "累计状态不属于当前完整定义")
            for ident, values in state["values"].items():
                if set(values) != set(self._keys(self._counters[ident])) or not all(_int(v) for v in values.values()):
                    _fail("counter_state", "累计键或原子值无效")
            for lid, life in state["lifecycles"].items():
                fields = {"phase", "generation_uid", "revision", "created_us", "established_us", "end_us", "established_observed", "closing_us"}
                if (set(life) != fields or life["phase"] not in {"inactive", "created", "established", "closing", "ended"}
                        or type(life["established_observed"]) is not bool):
                    _fail("counter_state", "累计生命周期状态无效")
                if life["phase"] == "inactive":
                    if life["established_observed"] or any(life[k] is not None for k in fields-{"phase", "established_observed"}):
                        _fail("counter_state", "未创建生命周期不能携带历史资格")
                    continue
                declaration = self._lives[lid]
                if (not _text(life["generation_uid"]) or not _int(life["revision"]) or life["revision"] == 0
                        or not all(_int(life[k]) for k in ("created_us", "established_us", "end_us"))
                        or life["established_us"] != life["created_us"]+declaration["establishment_delay_us"]
                        or state["last_time_us"] is None or life["created_us"] > state["last_time_us"]):
                    _fail("counter_state", "累计生命周期代、版本或时点无效")
                base_end = life["established_us"] + declaration["duration_us"]
                if life["revision"] > 1 and declaration["restart"] == "refresh_duration":
                    valid_end = (life["established_observed"]
                                 and base_end < life["end_us"] <= state["last_time_us"] + declaration["duration_us"])
                else:
                    valid_end = life["end_us"] == base_end
                if not valid_end:
                    _fail("counter_state", "累计生命周期到期时点与已声明的刷新方式不一致")
                if life["phase"] in {"closing", "ended"}:
                    if not _int(life["closing_us"]) or not life["created_us"] <= life["closing_us"] <= min(life["end_us"], state["last_time_us"]):
                        _fail("counter_state", "累计关闭时点无效")
                elif life["closing_us"] is not None:
                    _fail("counter_state", "未关闭生命周期不能携带关闭许可")
                if ((life["phase"] == "created" and life["established_observed"])
                        or (life["phase"] == "established" and not life["established_observed"])
                        or life["established_observed"] and life["established_us"] > (life["closing_us"] if life["closing_us"] is not None else state["last_time_us"])):
                    _fail("counter_state", "累计建立资格与阶段时点不一致")
            dispatch = state["active_dispatch"]
            if dispatch is not None:
                if (set(dispatch) != {"lifecycle_id", "generation_uid", "revision", "run_id", "time_us", "kind", "ref_id"}
                        or dispatch["lifecycle_id"] not in state["lifecycles"] or not _text(dispatch["run_id"])
                        or not _int(dispatch["time_us"]) or dispatch["time_us"] > state["last_time_us"]
                        or dispatch["kind"] not in {"dispatch_rule", "resource_operation"} or not _text(dispatch["ref_id"])):
                    _fail("counter_state", "累计dispatch资格结构无效")
                life = state["lifecycles"][dispatch["lifecycle_id"]]
                if life["phase"] not in {"created", "established", "closing"} or any(dispatch[k] != life[k] for k in ("generation_uid", "revision")):
                    _fail("counter_state", "累计dispatch不能引用旧代或已结束代")
            return copy.deepcopy(state)
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
            if isinstance(exc, ResourceCounterError): raise
            _fail("counter_state", "累计状态不是完整有界结构")

    def plan_control(self, state, verified_step):
        candidate = self._state(state)
        try:
            step = verified_step
            lid, kind = step["lifecycle_id"], step["kind"]
            life, current = self._lives[lid], candidate["lifecycles"][lid]
            if (not _int(step["sequence"]) or step["sequence"] != candidate["last_control_sequence"]+1
                    or not _text(step["generation_uid"]) or not _text(step["event_run_id"])
                    or not _int(step["time_us"]) or not _int(step["revision"]) or step["revision"] == 0
                    or step["owner"] != life["owner"] or step["source_id"] != life["source_id"]
                    or step["context"]["run_id"] != step["event_run_id"] or step["context"]["t_us"] != step["time_us"]):
                _fail("counter_control", "累计控制身份、来源或连续序号无效")
            if candidate["last_time_us"] is not None and step["time_us"] < candidate["last_time_us"]:
                _fail("counter_control_order", "控制不能回退到已经接收的资源或控制之前")
            candidate["active_dispatch"] = None
            if kind == "lifecycle_start":
                if current["phase"] not in {"inactive", "ended"} or current["generation_uid"] == step["generation_uid"]:
                    _fail("counter_generation", "新代必须在旧代结束后建立且不能复用旧代")
                current.update(phase="created", generation_uid=step["generation_uid"], revision=step["revision"],
                    created_us=step["time_us"], established_us=step["time_us"]+life["establishment_delay_us"],
                    end_us=step["time_us"]+life["establishment_delay_us"]+life["duration_us"],
                    established_observed=False, closing_us=None)
                if not _int(current["end_us"]): _fail("counter_limit", "累计生命周期时点超限", severity="pending")
            elif kind == "lifecycle_duration_refresh":
                if (life["restart"] != "refresh_duration" or current["phase"] != "established"
                        or current["generation_uid"] != step["generation_uid"]
                        or current["revision"] + 1 != step["revision"]
                        or step["previous_end_us"] != current["end_us"] or step["time_us"] >= current["end_us"]
                        or not _int(step["end_us"]) or step["end_us"] <= current["end_us"]
                        or step["end_us"] != step["time_us"] + life["duration_us"]):
                    _fail("counter_control", "刷新必须沿用活动代、延长到期并保留原累计")
                current.update(end_us=step["end_us"], revision=step["revision"])
            else:
                if current["generation_uid"] != step["generation_uid"] or current["revision"] != step["revision"]:
                    _fail("counter_generation", "旧代或未知代控制不能改变当前累计")
                transitions = {"lifecycle_established": ({"created"}, "established"),
                               "lifecycle_closing": ({"created", "established"}, "closing"),
                               "lifecycle_end": ({"closing"}, "ended")}
                if kind in transitions:
                    allowed, phase = transitions[kind]
                    if current["phase"] not in allowed: _fail("counter_control", "生命周期控制阶段回退或缺失")
                    if kind == "lifecycle_established" and step["time_us"] != current["established_us"]:
                        _fail("counter_control", "建立时点与声明不一致")
                    if kind == "lifecycle_established": current["established_observed"] = True
                    if kind == "lifecycle_closing":
                        if step["time_us"] > current["end_us"]: _fail("counter_control", "关闭控制不能晚于到期")
                        current["closing_us"] = step["time_us"]
                    if kind == "lifecycle_end" and step["time_us"] != current["closing_us"]:
                        _fail("counter_control", "结束控制必须延续同一次关闭事务")
                    current["phase"] = phase
                elif kind != "dispatch" or current["phase"] not in {"created", "established", "closing"}:
                    _fail("counter_control", "不支持此累计控制步骤")
                if kind == "dispatch":
                    dispatch = step["dispatch"]
                    if (any(dispatch[k] != step[k] for k in ("lifecycle_id", "generation_uid", "revision", "time_us"))
                            or not _text(dispatch["run_id"]) or dispatch["payload"]["kind"] not in {"dispatch_rule", "resource_operation"}
                            or not _text(dispatch["payload"]["ref_id"])):
                        _fail("counter_control", "dispatch未绑定当前已签发控制")
                    if current["phase"] == "closing" and step["time_us"] != current["closing_us"]:
                        _fail("counter_control", "关闭许可仅限本次关闭事务")
                    candidate["active_dispatch"] = {**{k: dispatch[k] for k in ("lifecycle_id", "generation_uid", "revision", "run_id", "time_us")}, **dispatch["payload"]}
            rows = []
            for ident, counter in self._counters.items():
                if counter["lifecycle_id"] != lid or kind == "dispatch": continue
                before = copy.deepcopy(candidate["values"][ident])
                reset = (kind == "lifecycle_start" and counter["reset_on"] == "create"
                         or kind == "lifecycle_end" and counter["reset_on"] == "end")
                if reset: candidate["values"][ident] = dict.fromkeys(before, 0)
                rows.append({"type": "counter_reset" if reset else "counter_window", "counter_id": ident,
                    "source_id": counter["source_id"], "lifecycle_id": lid, "generation_uid": step["generation_uid"],
                    "before": before, "after": copy.deepcopy(candidate["values"][ident]), "phase": current["phase"],
                    "control_sequence": step["sequence"], "time_us": step["time_us"]})
            candidate["last_control_sequence"] = step["sequence"]
            candidate["last_time_us"] = step["time_us"]
            return {"state": candidate, "counter_receipts": rows, "extra_event_count": len(rows)}
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            if isinstance(exc, ResourceCounterError): raise
            _fail("counter_control", "累计控制缺少完整的签发结构")

    @staticmethod
    def _spend_window(counter, life, receipt, active_dispatch):
        """Read-only qualification after complete state/receipt validation.

        This neither advances controls nor authenticates caller facts. Sharing
        the check with counter-span verification avoids two lifecycle policies.
        """
        window = life["phase"] in {"created", "established", "closing"} and (life["established_observed"] or counter["accumulate_window"] == "created_until_end")
        if life["phase"] == "closing":
            if receipt["t_us"] != life["closing_us"]:
                _fail("counter_missing_control", "关闭事务结束控制未交付，不能扩展关闭窗口", severity="pending")
            window = window and active_dispatch is not None and all((
                active_dispatch["lifecycle_id"] == counter["lifecycle_id"], active_dispatch["run_id"] == receipt["run_id"],
                active_dispatch["kind"] == "resource_operation", active_dispatch["ref_id"] == receipt["operation_id"]))
        if life["phase"] in {"created", "established"} and receipt["t_us"] >= life["end_us"]:
            _fail("counter_missing_control", "到期控制尚未交付，不能猜测累计窗口", severity="pending")
        if life["phase"] == "created" and receipt["t_us"] > life["established_us"]:
            _fail("counter_missing_control", "建立控制尚未交付，不能猜测累计资格", severity="pending")
        if life["created_us"] is not None and receipt["t_us"] < life["created_us"]:
            _fail("counter_receipt_order", "资源回执不能回到当前代建立之前")
        return window

    def plan_spend(self, state, operation_receipts):
        """Plan from ALL fresh ordered ledger receipts, including non-spend rows.

        Each item is {ledger_sequence, control_sequence, receipt}. The existing
        ledger owns both its index and the lifecycle cursor at operation time;
        copies with invented indices are not
        authenticated by this pure planner. No unbounded seen-ID history is copied.
        """
        candidate, rows = self._state(state), []
        try:
            if type(operation_receipts) is not list or not 1 <= len(operation_receipts) <= 16:
                _fail("counter_receipt", "单次资源回执必须是有界非空列表")
            for item in operation_receipts:
                seq, receipt = item["ledger_sequence"], item["receipt"]
                if not _int(seq) or seq != candidate["last_ledger_sequence"]+1:
                    _fail("counter_receipt_order", "累计必须按真实资源账序号接收，不能跳过或重复")
                if type(item["control_sequence"]) is not int or item["control_sequence"] != candidate["last_control_sequence"]:
                    _fail("counter_receipt_order", "资源回执必须在其真实生命周期控制位置交付")
                if not isinstance(receipt, dict) or receipt.get("type") not in {"operation", "consume"}:
                    _fail("counter_receipt", "资源回执类别无效")
                operation = self._ops.get(receipt.get("operation_id")) if receipt.get("type") == "operation" else self._consumers.get(receipt.get("consumer_id"))
                if operation is None: _fail("counter_receipt", "资源回执引用不存在的操作")
                pool = self._pools[operation["pool_id"]]
                if (receipt["pool_id"] != pool["id"] or receipt["source_id"] != operation["source_id"]
                        or receipt["key"] not in pool["initial"] or type(receipt["unit_scale"]) is not int
                        or receipt["unit_scale"] != pool["unit_scale"]
                        or receipt.get("capacity_mode", "bounded") != pool_capacity_mode(pool)
                        or receipt["quantity_unit"] != "atoms" or not _int(receipt["t_us"])
                        or not _text(receipt["id"]) or not _text(receipt["run_id"])
                        or not all(_int(receipt[key], 1000000) for key in ("before", "requested", "accepted", "after"))
                        or receipt["before"] > pool_capacity_limit(pool)
                        or receipt["after"] > pool_capacity_limit(pool)):
                    _fail("counter_receipt", "资源回执的池、单位或原子值不一致")
                if candidate["last_time_us"] is not None and receipt["t_us"] < candidate["last_time_us"]:
                    _fail("counter_receipt_order", "资源回执不能追溯到已交付控制或资源之前")
                if receipt['type']=='operation':
                    from genshin_engine.shared_pool_probability import validate_probability_receipt
                    validate_probability_receipt(operation, pool, receipt)
                is_spend = receipt["type"] == "operation" and operation["operation"] == "spend"
                if is_spend:
                    if receipt.get("operation") != "spend" or receipt["after"] != receipt["before"]-receipt["accepted"] or receipt["accepted"] > receipt["requested"]:
                        _fail("counter_receipt", "spend回执不符合实际扣量守恒")
                    for ident, counter in self._counters.items():
                        if receipt["operation_id"] not in counter["operation_ids"]: continue
                        life = candidate["lifecycles"][counter["lifecycle_id"]]
                        window = self._spend_window(counter, life, receipt, candidate["active_dispatch"])
                        key = "total" if counter["scope"] == "pool_total" else receipt["key"]
                        before = candidate["values"][ident][key]
                        accepted = receipt["accepted"] if window else 0
                        after = before+accepted
                        if not _int(after): _fail("counter_limit", "真实累计超过有界整数上限", severity="pending")
                        candidate["values"][ident][key] = after
                        rows.append({"type": "counter_accumulation", "counter_id": ident, "source_id": counter["source_id"],
                            "resource_receipt_id": receipt["id"], "ledger_sequence": seq, "pool_id": pool["id"], "key": key,
                            "requested": receipt["requested"], "resource_accepted": receipt["accepted"], "accepted": accepted,
                            "before": before, "after": after, "unit_scale": pool["unit_scale"], "quantity_unit": "atoms",
                            "generation_uid": life["generation_uid"], "reason": "accumulated" if window else "outside_window",
                            "time_us": receipt["t_us"], "lifecycle_id": counter["lifecycle_id"],
                            "history_scope": "lifetime_total" if counter["reset_on"] == "never" else "since_last_reset"})
                candidate["last_ledger_sequence"] = seq
                candidate["last_time_us"] = receipt["t_us"]
            return {"state": candidate, "counter_receipts": rows, "extra_event_count": len(rows)}
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            if isinstance(exc, ResourceCounterError): raise
            _fail("counter_receipt", "累计需要完整的真实资源回执")
