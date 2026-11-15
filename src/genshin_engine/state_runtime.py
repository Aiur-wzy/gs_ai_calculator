"""A bounded, pure lifecycle clock driven by an external ordered event queue.

The service emits dispatch commands, never applies effects/resources or checks
gameplay FILTER/source authority. ``eligible=True`` is supplied by the caller.
Actual event ``run_id`` must identify one occurrence, not a rotation cycle.

All methods return copies. Generated events are authenticated against their
complete issued payload; only the adapter may insert them into the real queue.
``reserve_events(n)`` is an optional atomic shared-budget callback. Cancellation
does not refund scheduled work. No lifecycle or counter state is reset per cycle.
"""
from __future__ import annotations

import copy
import hashlib
import json

from genshin_engine.shared_pools import _schema
from genshin_engine.state_continuation_positions import (
    PositionIndex,
    PositionLimitError,
)
from genshin_engine.state_position_reference import PositionError
from genshin_engine.state_runtime_schema import (
    LIMITS,
    STATE_RUNTIME_SCHEMA,
    STATE_RUNTIME_V2_SCHEMA,
)


class StateRuntimeError(ValueError):
    def __init__(self, code, message, path="state_runtime", *, severity="error", **details):
        self.issue = {"code": code, "message": message, "path": path, "severity": severity, "details": details}
        super().__init__(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")


def _error(code, message, path, **details):
    return StateRuntimeError(code, message, path, **details).issue


def _integer(value, name, *, minimum=0, maximum=None):
    if type(value) is not int or not minimum <= value <= (LIMITS["time_us"] if maximum is None else maximum):
        raise StateRuntimeError("state_context", "必须为范围内的整数", name)
    return value


def _text(value, name, maximum=128):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise StateRuntimeError("state_context", "身份必须为非空有界文本", name)
    return value


def _timer_count(life, timer):
    start = (life["establishment_delay_us"] if timer["anchor"] == "established" else 0) + timer["first_offset_us"]
    end = life["establishment_delay_us"] + life["duration_us"] - (timer["end_tick"] == "exclude")
    return min(timer["max_ticks"], max(0, (end-start)//timer["interval_us"]+1))


def validate_state_runtime(config, *, character_ids, rule_ids=None, operation_ids=None, source_parts=None):
    """Validate closed declarations/references, not runtime FILTER or source approval."""
    try:
        if not isinstance(character_ids, (list, tuple, set, frozenset)) or not 1 <= len(character_ids) <= LIMITS["characters"]:
            raise StateRuntimeError("state_context", "角色集合数量或类型错误")
        for value in character_ids: _text(value, "character_ids")
        if len(set(character_ids)) != len(character_ids): raise StateRuntimeError("state_context", "角色集合重复")
        for name, collection in (("rule_ids", rule_ids), ("operation_ids", operation_ids), ("source_parts", source_parts)):
            if collection is None: continue
            if not isinstance(collection, (list, tuple, set, frozenset)) or len(collection) > LIMITS["events"]:
                raise StateRuntimeError("state_context", "引用集合类型或数量错误", name)
            for value in collection: _text(value, name)
            if len(set(collection)) != len(collection): raise StateRuntimeError("state_context", "引用集合重复", name)
        if len(_json(config)) > LIMITS["declaration_bytes"]:
            raise StateRuntimeError("state_declaration_limit", "生命周期声明字节超限")
    except StateRuntimeError as exc:
        return [exc.issue]
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        return [_error("state_schema", "声明不是有限有界JSON", "state_runtime", reason=str(exc))]
    problems = []
    _schema(config, STATE_RUNTIME_V2_SCHEMA if config.get("schema_version") == 2 else STATE_RUNTIME_SCHEMA, "state_runtime", problems)
    for problem in problems: problem["code"] = "state_schema"
    if problems: return problems
    lives, sources, starts, ends, ordinary, terminal = {}, {}, {}, {}, {}, {}
    for kind in ("lifecycles", "timers"):
        seen = set()
        for index, row in enumerate(config[kind]):
            path = f"state_runtime.{kind}[{index}]"
            if row["id"] in seen: problems.append(_error("state_duplicate_id", "定义ID重复", path+".id"))
            seen.add(row["id"])
            if source_parts is not None and row["source_id"] not in source_parts:
                problems.append(_error("state_source_reference", "未登记来源部件", path+".source_id"))
            if row["status"] == "pending": problems.append(_error("state_evidence_pending", "声明证据未确认", path+".status", severity="pending"))
            if kind == "lifecycles":
                lives[row["id"]] = row
                if row["owner"] not in character_ids: problems.append(_error("state_owner_reference", "提供者不在队伍中", path+".owner"))
                key = row["owner"], row["source_id"]
                if key in sources: problems.append(_error("state_duplicate_source", "同提供者来源部件不能建立多个独立生命周期", path, conflicts_with=sources[key]))
                sources[key] = path
                for field, orders, key in (("start_order", starts, (row["start"].get("phase", row["start"].get("binding_id")), row["start_order"])),
                                           ("end_order", ends, row["end_order"]),
                                           ("establishment_order", ordinary, row["establishment_order"])):
                    if key in orders: problems.append(_error("state_order_conflict", "可能共触发的顺序冲突", path+"."+field, conflicts_with=orders[key]))
                    orders[key] = path+"."+field
                if row["establishment_delay_us"]+row["duration_us"] > LIMITS["time_us"]:
                    problems.append(_error("state_time_limit", "建立加持续超过时间上限", path))
                for field in ("start", "end_trigger"):
                    selector = row.get(field, {})
                    for key, values in selector.items():
                        if isinstance(values, list) and (not values or len(set(values)) != len(values)):
                            problems.append(_error("state_selector", "筛选列表为空或重复", path+"."+field+"."+key))
                    if not set(selector.get("characters", [])) <= set(character_ids):
                        problems.append(_error("state_selector", "筛选角色不存在", path+"."+field))
                for stage, hooks in row["hooks"].items():
                    orders = set()
                    for hook in hooks:
                        if hook["order"] in orders: problems.append(_error("state_order_conflict", "同阶段hook顺序冲突", path+".hooks."+stage))
                        orders.add(hook["order"])
                        allowed = rule_ids if hook["kind"] == "dispatch_rule" else operation_ids
                        if allowed is not None and hook["ref_id"] not in allowed:
                            problems.append(_error("state_payload_reference", "hook目标未登记", path+".hooks."+stage))
                continue
            life = lives.get(row["lifecycle_id"])
            if life is None:
                problems.append(_error("state_lifecycle_reference", "timer生命周期不存在", path+".lifecycle_id"))
                continue
            if row["order"] in ordinary:
                problems.append(_error("state_order_conflict", "timer/延迟建立可能同刻，须不同order", path+".order", conflicts_with=ordinary[row["order"]]))
            ordinary[row["order"]] = path+".order"
            if row["end_tick"] == "before_expiry":
                key = row["lifecycle_id"], row.get("terminal_order")
                if row.get("terminal_order") is None or key in terminal:
                    problems.append(_error("state_terminal_order", "末跳需要唯一显式terminal_order", path))
                terminal[key] = path
            elif "terminal_order" in row:
                problems.append(_error("state_terminal_order", "exclude不能带terminal_order", path))
            allowed = rule_ids if row["payload"]["kind"] == "dispatch_rule" else operation_ids
            if allowed is not None and row["payload"]["ref_id"] not in allowed:
                problems.append(_error("state_payload_reference", "timer目标未登记", path+".payload"))
    if sum(_timer_count(lives[t["lifecycle_id"]], t) for t in config["timers"] if t["lifecycle_id"] in lives) > LIMITS["events"]:
        problems.append(_error("state_event_limit", "单次全组调度数量超过公共事件预算", "state_runtime.timers"))
    return problems


def _key(event):
    return event["time_us"], event["system_priority"], event["order"], tuple(event.get("causal", []))


class StateRuntime:
    """Pure finite service. The external adapter owns real qualification/execution."""

    def __init__(self, config, *, character_ids, reserve_events=None, external_events=(),
                 rule_ids=None, operation_ids=None, source_parts=None, emit_ordered_steps=False, resource_limits=None):
        if type(emit_ordered_steps) is not bool:
            raise StateRuntimeError("state_context", "emit_ordered_steps必须为布尔")
        problems = validate_state_runtime(config, character_ids=character_ids, rule_ids=rule_ids,
                                          operation_ids=operation_ids, source_parts=source_parts)
        if problems:
            first = problems[0]
            raise StateRuntimeError(first["code"], first["message"], first["path"], severity=first["severity"], issues=problems)
        self.config = copy.deepcopy(config)
        self._position_index = PositionIndex(limits=resource_limits) if config["schema_version"] == 2 else None
        self._position_draft = None
        if self._position_index is not None and not emit_ordered_steps:
            raise StateRuntimeError("state_context", "clock2必须启用真实ordered控制交付")
        self.lives = {r["id"]: r for r in self.config["lifecycles"]}
        self.timers = {r["id"]: r for r in self.config["timers"]}
        self.by_life = {lid: sorted([t for t in self.timers.values() if t["lifecycle_id"] == lid], key=lambda x: x["order"]) for lid in self.lives}
        self._states, self._pending, self._issued, self._processed, self._starts = {}, {}, {}, set(), set()
        self._contexts, self._context_phases, self._start_orders, self._generation_life = {}, {}, {}, {}
        self._last_event_key, self._last_event_uid = None, None
        self._trace, self._trace_bytes, self._reserved, self._time = [], 2, 0, -1
        self._reserve_external = reserve_events
        self._emit_ordered_steps = emit_ordered_steps
        self._control_sequence, self._batch_sequence = 0, 0
        self._next_control_claim = 1
        self._control_batches = {}
        self._external = {}
        if not isinstance(external_events, (list, tuple)) or len(external_events) > LIMITS["events"]:
            raise StateRuntimeError("state_context", "外部队列类型错误或数量超限")
        for row in external_events:
            if not isinstance(row, dict) or set(row) != {"event_id", "t_us", "order"}:
                raise StateRuntimeError("state_context", "外部事件只接受event_id/t_us/order")
            _text(row["event_id"], "external.event_id")
            _integer(row["t_us"], "external.t_us")
            _integer(row["order"], "external.order", minimum=-1000000, maximum=1000000)
            key = row["t_us"], row["order"]
            if key in self._external: raise StateRuntimeError("state_order_conflict", "外部队列同刻order重复")
            self._external[key] = row["event_id"]

    @property
    def trace(self):
        return copy.deepcopy(self._trace)

    def pending(self):
        return copy.deepcopy(sorted(self._pending.values(), key=_key))

    def _check_pending_scan(self):
        guard = getattr(self, '_pending_scan_guard', None)
        if guard is not None:
            guard()

    def _context(self, event):
        if self._position_index is not None:
            return self._context_v2(event)
        if not isinstance(event, dict) or set(event) != {"event_id", "run_id", "t_us", "order", "phase", "causal"}:
            raise StateRuntimeError("state_context", "源事件上下文字段不完整或含未知字段")
        _text(event["event_id"], "event_id", 512); _text(event["run_id"], "run_id", 512)
        _integer(event["t_us"], "t_us"); _integer(event["order"], "order", minimum=-1000000, maximum=1000000)
        if event["phase"] not in ("pre", "post") or not isinstance(event["causal"], list) or len(event["causal"]) > 32:
            raise StateRuntimeError("state_context", "源事件阶段/因果路径错误")
        for value in event["causal"]: _integer(value, "causal", minimum=-1000000, maximum=1000000)
        signature = {key: event[key] for key in ("event_id", "run_id", "t_us", "order", "causal")}
        known = self._contexts.get(event["run_id"])
        if known is not None and known != signature:
            raise StateRuntimeError("state_context_identity", "同一实际run_id不能改变事件身份/时间/顺序")
        if known is None and len(self._contexts) >= LIMITS["events"]:
            raise StateRuntimeError("state_context_limit", "实际事件上下文数量超过公共上限", severity="pending")
        incoming = event["t_us"], 0, event["order"], tuple(event["causal"])
        self._check_committed_order(incoming, event["run_id"], event["phase"])
        if any(_key(row) < incoming for row in self._pending.values()):
            raise StateRuntimeError("state_due_event", "必须先执行更早或更高优先级的派生事件")
        return copy.deepcopy(event)

    def _context_v2(self, event):
        if not isinstance(event, dict) or set(event) != {"event_id", "run_id", "t_us", "order", "phase", "causal", "position"}:
            raise StateRuntimeError("state_context", "clock2只接受完整位置context，不能混用旧格式")
        _text(event["event_id"], "event_id", 512); _text(event["run_id"], "run_id", 512)
        _integer(event["t_us"], "t_us"); _integer(event["order"], "order", minimum=-1000000, maximum=1000000)
        frame = event["position"]
        try:
            prepared = self._position_index.prepare(frame, issued=self._issued.get(frame.get("anchor", {}).get("queue_run_id")))
        except PositionLimitError as exc:
            raise StateRuntimeError("state_position_limit", str(exc), severity="pending") from exc
        except (PositionError, AttributeError, TypeError) as exc:
            raise StateRuntimeError("state_position", str(exc)) from exc
        anchor = prepared["anchor"]
        source = frame.get("source")
        run_id = source["dispatch_run_id"] if source else anchor["queue_run_id"]
        source_phase = source["phase"] if source else frame["phase"]
        if (event["run_id"] != run_id or event["event_id"] != (run_id if source else anchor["queue_event_id"])
                or event["t_us"] != anchor["time_us"]
                or event["order"] != anchor["order"] or event["causal"] != anchor["causal"]
                or event["phase"] != source_phase):
            raise StateRuntimeError("state_position", "实际来源context与排队anchor不一致")
        signature = {key: copy.deepcopy(event[key]) for key in ("event_id", "run_id", "t_us", "order", "causal")}
        known = self._contexts.get(event["run_id"])
        if known is not None and known != signature:
            raise StateRuntimeError("state_context_identity", "同实际run不能改变完整身份")
        if known is None and len(self._contexts) >= LIMITS["events"]:
            raise StateRuntimeError("state_context_limit", "实际事件上下文超过上限", severity="pending")
        key = anchor["time_us"], anchor["system_priority"], anchor["order"], tuple(anchor["causal"])
        self._check_committed_order(key, anchor["queue_run_id"], frame["phase"])
        if any(_key(row) < key for row in self._pending.values()):
            raise StateRuntimeError("state_due_event", "须先执行更早派生事件，不得借continuation越过")
        self._position_draft = {"prepared": prepared, "signature": signature, "key": key,
            "source_run_id": event["run_id"], "queue_run_id": anchor["queue_run_id"], "phase": frame["phase"]}
        return copy.deepcopy(event)

    def _check_committed_order(self, key, run_id, phase):
        """Check the full committed queue position, including completed stale work.

        Several lifecycle operations may share one actual event and phase. They
        must not reopen an earlier event/phase or let a different run borrow its
        queue slot. State is committed only after the atomic budget reservation.
        """
        if self._last_event_key is not None:
            if key < self._last_event_key:
                raise StateRuntimeError("state_time_order", "实际事件的时间/优先级/顺序/因果路径不能回退")
            if key == self._last_event_key and run_id != self._last_event_uid:
                raise StateRuntimeError("state_order_conflict", "不同实际事件不能共用已执行的队列顺序")
        if self._context_phases.get(run_id) == "post" and phase == "pre":
            raise StateRuntimeError("state_phase_order", "同一实际事件不能从post退回pre阶段")

    def _remember_context(self, event, *, priority=0):
        if self._position_index is not None:
            return  # Published with the same clock draft after its reservation.
        self._contexts[event["run_id"]] = copy.deepcopy({key: event[key] for key in ("event_id", "run_id", "t_us", "order", "causal")})
        self._context_phases[event["run_id"]] = event["phase"]
        self._last_event_key = event["t_us"], priority, event["order"], tuple(event["causal"])
        self._last_event_uid = event["run_id"]

    def _begin(self):
        return {"states": copy.deepcopy(self._states), "pending": copy.deepcopy(self._pending),
                "scheduled": [], "dispatches": [], "trace": [], "transitions": 0, "steps": []}

    def _finish(self, draft, time):
        size = sum(len(_json(row))+1 for row in draft["trace"])
        count = len(draft["scheduled"])+len(draft["dispatches"])+draft["transitions"]
        position = self._position_draft if self._position_index is not None else None
        if self._position_index is not None and position is None:
            raise StateRuntimeError("state_position", "clock2提交缺少位置候选")
        if position is not None:
            count += 1
            size += position["prepared"]["extra_bytes"]
        batch = None
        if self._emit_ordered_steps:
            batch = {"contract": "state-ordered-controls-v1", "sequence": self._batch_sequence+1,
                     "steps": copy.deepcopy(draft["steps"])}
            batch["id"] = "@state_batch:"+hashlib.sha256(_json(batch)).hexdigest()
            size += len(_json(batch))
            count += 1  # Even an empty signed control delivery is bounded work.
        if self._reserved+count > LIMITS["events"]:
            raise StateRuntimeError("state_event_limit", "有限生命周期公共预算超限", severity="pending")
        if self._trace_bytes+size > LIMITS["trace_bytes"]:
            raise StateRuntimeError("state_trace_limit", "生命周期流水存储超限", severity="pending")
        result_v2 = copy.deepcopy({key: draft[key] for key in ("scheduled", "dispatches", "trace")}) if position is not None else None
        scheduled_v2 = {row["run_id"]: copy.deepcopy(row) for row in draft["scheduled"]} if position is not None else None
        batch_hash_v2 = hashlib.sha256(_json(batch)).hexdigest() if position is not None and batch is not None else None
        if self._reserve_external is not None: self._reserve_external(count)
        self._states, self._pending = draft["states"], draft["pending"]
        self._issued.update(scheduled_v2 if position is not None else {row["run_id"]: copy.deepcopy(row) for row in draft["scheduled"]})
        self._trace.extend(draft["trace"])
        self._reserved += count; self._trace_bytes += size; self._time = time
        result = result_v2 if position is not None else copy.deepcopy({key: draft[key] for key in ("scheduled", "dispatches", "trace")})
        if batch is not None:
            self._batch_sequence += 1
            self._control_sequence += len(draft["steps"])
            self._control_batches[batch["id"]] = (batch, batch_hash_v2 if position is not None else hashlib.sha256(_json(batch)).hexdigest())
            result["ordered_steps"] = batch
        if position is not None:
            self._position_index.publish(position["prepared"])
            self._contexts[position["source_run_id"]] = position["signature"]
            self._context_phases[position["queue_run_id"]] = position["phase"]
            self._last_event_key, self._last_event_uid = position["key"], position["queue_run_id"]
            self._position_draft = None
        return result

    def _control(self, draft, state, kind, time, context, **details):
        if not self._emit_ordered_steps:
            return
        row = {"kind": kind, "sequence": self._control_sequence+len(draft["steps"])+1,
               "lifecycle_id": state["id"], "generation_uid": state["generation_uid"],
               "revision": state["revision"], "event_run_id": context["run_id"], "time_us": time,
               "owner": self.lives[state["id"]]["owner"], "source_id": self.lives[state["id"]]["source_id"],
               "context": copy.deepcopy(context), **copy.deepcopy(details)}
        draft["steps"].append(row)

    def consume_ordered_steps(self, batch):
        """Claim one original, unchanged signed batch; claiming is NOT execution.

        A bridge must apply the returned steps in order and retain its actual
        prefix on failure. Display trace, JSON copies and a claimed batch cannot
        authorize another execution. Counter arithmetic is outside this service.
        """
        self._check_pending_scan()
        known = self._control_batches.get(batch.get("id")) if isinstance(batch, dict) and type(batch.get("id")) is str else None
        if known is None or known[0] is not batch:
            raise StateRuntimeError("state_control_authority", "生命周期控制批不是本服务尚未领取的签发对象")
        try:
            valid = hashlib.sha256(_json(batch)).hexdigest() == known[1]
        except (TypeError, ValueError, OverflowError, RecursionError):
            valid = False
        if not valid:
            raise StateRuntimeError("state_control_authority", "生命周期控制批顺序或内容在签发后改变")
        if batch["sequence"] != self._next_control_claim:
            raise StateRuntimeError("state_control_order", "生命周期控制批必须按实际签发顺序领取")
        result = copy.deepcopy(batch["steps"])
        self._control_batches.pop(batch["id"])
        self._next_control_claim += 1
        return result

    def _record(self, draft, type_, state, time, **data):
        draft["trace"].append({"type": type_, "lifecycle_id": state["id"], "generation_uid": state["generation_uid"],
                               "revision": state["revision"], "time_us": time, **data})

    def _dispatch(self, draft, state, time, event, payload, context, *, timer=None, ordinal=None, terminal=False, order=0):
        row = {"event": event, "lifecycle_id": state["id"], "generation_uid": state["generation_uid"],
               "revision": state["revision"], "owner": self.lives[state["id"]]["owner"], "time_us": time,
               "source_id": timer["source_id"] if timer is not None else self.lives[state["id"]]["source_id"],
               "payload": copy.deepcopy(payload), "order": order, "causal_parent": copy.deepcopy(context),
               "source_event": copy.deepcopy(state["parent"]),
               "frame": "terminal_preclose" if terminal else "after_parent", "qualification": "not_evaluated"}
        if timer is not None: row.update(timer_id=timer["id"], ordinal=ordinal)
        identity = [state["generation_uid"], state["revision"], event, timer["id"] if timer else None, ordinal, order, payload]
        row["run_id"] = "@state_dispatch:"+hashlib.sha256(_json(identity)).hexdigest()
        if terminal:
            row["terminal_permit"] = {"generation_uid": state["generation_uid"], "revision": state["revision"],
                                      "timer_id": timer["id"], "ordinal": ordinal, "scope": "this_dispatch_only"}
        draft["dispatches"].append(row)
        self._record(draft, "dispatch", state, time, **row)
        self._control(draft, state, "dispatch", time, context, dispatch=row)

    def _hooks(self, draft, state, stage, time, context):
        for hook in sorted(self.lives[state["id"]]["hooks"][stage], key=lambda item: item["order"]):
            self._dispatch(draft, state, time, "lifecycle_"+stage, {k: hook[k] for k in ("kind", "ref_id")}, context, order=hook["order"])

    def _schedule(self, draft, state, kind, time, order, *, timer=None, ordinal=None, priority=0):
        _integer(time, "scheduled.time_us")
        if priority == 0 and (time, order) in self._external:
            raise StateRuntimeError("state_order_conflict", "派生事件与外部队列同刻order冲突", conflicts_with=self._external[time, order])
        identity = [state["generation_uid"], state["revision"], kind, timer, ordinal]
        uid = "@state:"+hashlib.sha256(_json(identity)).hexdigest()
        row = {"id": uid, "run_id": uid, "kind": kind, "time_us": time, "order": order, "system_priority": priority,
               "causal": [], "lifecycle_id": state["id"], "generation_uid": state["generation_uid"], "revision": state["revision"],
               "parent_run_id": state["parent"]["run_id"], "source_event": copy.deepcopy(state["parent"]),
               "timer_id": timer, "ordinal": ordinal}
        if any(_key(other) == _key(row) for other in draft["pending"].values()):
            raise StateRuntimeError("state_order_conflict", "派生事件同刻顺序冲突", lifecycle_id=state["id"])
        draft["pending"][uid] = row; draft["scheduled"].append(row)

    def _next_timer(self, draft, state, timer):
        tick = state["timers"][timer["id"]]
        if tick["remaining"] and tick["next_us"] < state["end_us"]:
            self._schedule(draft, state, "state_tick", tick["next_us"], timer["order"], timer=timer["id"], ordinal=tick["ordinal"])

    def _tick(self, draft, state, timer, time, context, *, terminal=False):
        tick = state["timers"][timer["id"]]
        if not tick["remaining"] or time != tick["next_us"]:
            raise StateRuntimeError("state_tick_identity", "tick序号或时点不匹配")
        self._dispatch(draft, state, time, "timer_tick", timer["payload"], context, timer=timer, ordinal=tick["ordinal"],
                       terminal=terminal, order=timer["terminal_order"] if terminal else timer["order"])
        tick["remaining"] -= 1; tick["ordinal"] += 1; tick["next_us"] += timer["interval_us"]
        if not terminal: self._next_timer(draft, state, timer)

    def _start_timers(self, draft, state, anchor, time, context):
        for timer in self.by_life[state["id"]]:
            if timer["anchor"] != anchor: continue
            tick = state["timers"][timer["id"]]
            if not tick["remaining"]: continue
            if tick["next_us"] == time:
                self._tick(draft, state, timer, time, context)
            else: self._next_timer(draft, state, timer)

    def _establish(self, draft, state, time, context):
        state["phase"] = "active"
        draft["transitions"] += 1
        self._record(draft, "lifecycle_established", state, time)
        self._control(draft, state, "lifecycle_established", time, context)
        self._hooks(draft, state, "established", time, context)
        self._start_timers(draft, state, "established", time, context)

    def _close(self, draft, state, time, context, reason):
        state["phase"] = "closing"
        self._control(draft, state, "lifecycle_closing", time, context, reason=reason)
        if self._emit_ordered_steps:
            draft["transitions"] += 1
        if reason == "natural_expiry":
            for timer in sorted(self.by_life[state["id"]], key=lambda row: row.get("terminal_order", 0)):
                tick = state["timers"][timer["id"]]
                if timer["end_tick"] == "before_expiry" and tick["remaining"] and tick["next_us"] == time:
                    self._tick(draft, state, timer, time, context, terminal=True)
        self._hooks(draft, state, "end", time, context)
        for uid, row in list(draft["pending"].items()):
            if row["generation_uid"] == state["generation_uid"]:
                draft["pending"].pop(uid)
                self._record(draft, "event_cancelled", state, time, event_id=uid, reason=reason)
        state["phase"] = "ended"; state["reason"] = reason
        draft["transitions"] += 1
        self._record(draft, "lifecycle_end", state, time, reason=reason)
        self._control(draft, state, "lifecycle_end", time, context, reason=reason)

    def start(self, lifecycle_id, event, *, eligible):
        """Handle one actual qualified start; duplicate run/lifecycle calls reject."""
        self._check_pending_scan()
        _text(lifecycle_id, "lifecycle_id")
        if lifecycle_id not in self.lives or type(eligible) is not bool:
            raise StateRuntimeError("state_context", "生命周期或资格类型错误")
        context = self._context(event); time = context["t_us"]
        identity = lifecycle_id, context["run_id"]
        if identity in self._starts: raise StateRuntimeError("state_duplicate_start", "同实际事件重复创建生命周期")
        life = self.lives[lifecycle_id]
        typed = life["start"].get("kind") == "resource_threshold"
        if typed != (self._position_index is not None and context["position"]["stage"] in ("resource_tail", "batch_tail")):
            raise StateRuntimeError("state_start_phase", "typed端点与实际continuation入口不匹配")
        if typed and context["position"]["binding_id"] != life["start"]["binding_id"]:
            raise StateRuntimeError("state_start_phase", "continuation没有绑定此typed启动端点")
        if not typed and context["phase"] != life["start"]["phase"]:
            raise StateRuntimeError("state_start_phase", "调用阶段与声明的生命周期启动阶段不一致")
        order_key = context["run_id"], context["phase"]
        previous_order = self._start_orders.get(order_key)
        if previous_order is not None and life["start_order"] <= previous_order:
            raise StateRuntimeError("state_start_order", "同实际源事件的启动须按显式start_order递增调用")
        draft = self._begin(); old = draft["states"].get(lifecycle_id)
        if eligible and old and old["phase"] == "active" and time < old["end_us"] and life["restart"] == "refresh_duration":
            # Keep the same generation and timer phase. Only the expiry moves.
            end = max(old["end_us"], time + life["duration_us"])
            _integer(end, "lifecycle.end_us")
            previous_end = old["end_us"]
            draft["transitions"] += 1
            if end == previous_end:
                self._record(draft, "lifecycle_keep", old, time, parent_run_id=context["run_id"])
            else:
                old["end_us"] = end
                old["revision"] += 1
                live_timers = set()
                for uid, pending in list(draft["pending"].items()):
                    if pending["generation_uid"] != old["generation_uid"]:
                        continue
                    if pending["kind"] == "state_expire":
                        draft["pending"].pop(uid)
                        self._record(draft, "event_cancelled", old, time, event_id=uid, reason="duration_refresh")
                    elif pending["kind"] == "state_tick":
                        live_timers.add(pending["timer_id"])
                details = {"previous_end_us": previous_end, "end_us": end}
                self._record(draft, "lifecycle_duration_refresh", old, time, parent_run_id=context["run_id"], **details)
                self._control(draft, old, "lifecycle_duration_refresh", time, context, **details)
                for timer in self.by_life[lifecycle_id]:
                    tick = old["timers"][timer["id"]]
                    last = end - (timer["end_tick"] == "exclude")
                    tick["remaining"] = min(timer["max_ticks"] - tick["ordinal"] + 1,
                                            max(0, (last - tick["next_us"]) // timer["interval_us"] + 1))
                    for ordinal in range(tick["remaining"]):
                        tick_time = tick["next_us"] + ordinal * timer["interval_us"]
                        if time < tick_time < end and (tick_time, timer["order"]) in self._external:
                            raise StateRuntimeError("state_order_conflict", "未来timer与外部队列同刻order冲突")
                    if timer["id"] not in live_timers:
                        self._next_timer(draft, old, timer)
                self._schedule(draft, old, "state_expire", end, life["end_order"], priority=-2)
        elif not eligible or old and old["phase"] != "ended" and life["restart"] in ("keep", "refresh_duration"):
            draft["transitions"] += 1
            draft["trace"].append({"type": "lifecycle_ineligible" if not eligible else "lifecycle_keep", "lifecycle_id": lifecycle_id,
                                   "time_us": time, "parent_run_id": context["run_id"]})
        else:
            if old and old["phase"] != "ended": self._close(draft, old, time, context, "replaced")
            end = time+life["establishment_delay_us"]+life["duration_us"]
            _integer(end, "lifecycle.end_us")
            generation_uid = "@generation:"+hashlib.sha256(_json([lifecycle_id, context["run_id"]])).hexdigest()
            state = {"id": lifecycle_id, "generation_uid": generation_uid, "revision": 1,
                     "phase": "pending_establishment", "parent": context, "created_us": time,
                     "established_us": time+life["establishment_delay_us"], "end_us": end, "timers": {}}
            draft["states"][lifecycle_id] = state
            for timer in self.by_life[lifecycle_id]:
                count = _timer_count(life, timer)
                first = (state["established_us"] if timer["anchor"] == "established" else time)+timer["first_offset_us"]
                state["timers"][timer["id"]] = {"ordinal": 1, "remaining": count, "next_us": first}
                # Validate all possible ordinary collisions before any initial hook
                # escapes; later eligibility/cancellation must not hide ambiguity.
                for ordinal in range(count):
                    tick_time = first+ordinal*timer["interval_us"]
                    if time < tick_time < end and (tick_time, timer["order"]) in self._external:
                        raise StateRuntimeError("state_order_conflict", "未来timer与外部队列同刻order冲突")
            draft["transitions"] += 1
            self._record(draft, "lifecycle_start", state, time, parent_run_id=context["run_id"])
            self._control(draft, state, "lifecycle_start", time, context)
            self._hooks(draft, state, "create", time, context)
            self._start_timers(draft, state, "create", time, context)
            if life["establishment_delay_us"]:
                self._schedule(draft, state, "state_establish", state["established_us"], life["establishment_order"])
            else: self._establish(draft, state, time, context)
            self._schedule(draft, state, "state_expire", end, life["end_order"], priority=-2)
        out = self._finish(draft, time)
        self._starts.add(identity)
        self._start_orders[order_key] = life["start_order"]
        self._remember_context(context)
        if eligible and lifecycle_id in self._states:
            state = self._states[lifecycle_id]
            self._generation_life[state["generation_uid"]] = lifecycle_id
        return out

    def handle(self, event):
        """Execute exactly an issued scheduled occurrence, or retain its cancellation."""
        self._check_pending_scan()
        if not isinstance(event, dict) or type(event.get("run_id")) is not str or self._issued.get(event["run_id"]) != event:
            raise StateRuntimeError("state_event_identity", "未知或被修改的派生事件")
        uid = event["run_id"]
        if uid in self._processed: raise StateRuntimeError("state_duplicate_event", "派生事件已处理")
        self._check_committed_order(_key(event), uid, "post")
        # Cancelled events still advance the queue clock, so they cannot jump
        # over live work from a replacement generation.
        if any(row["run_id"] != uid and _key(row) <= _key(event) for row in self._pending.values()):
            raise StateRuntimeError("state_due_event", "必须先执行更早的派生事件")
        context = {"event_id": event["id"], "run_id": uid, "t_us": event["time_us"], "order": event["order"], "phase": "post", "causal": []}
        if self._position_index is not None:
            context["position"] = {"anchor": {"kind": "scheduled", "queue_event_id": event["id"], "queue_run_id": uid,
                "time_us": event["time_us"], "system_priority": event["system_priority"], "order": event["order"],
                "causal": list(event["causal"])}, "phase": "post", "stage": "scheduled_handle"}
            context = self._context(context)
        draft = self._begin(); state = draft["states"][event["lifecycle_id"]]
        retained_timer = (event["kind"] == "state_tick"
                          and self.lives[event["lifecycle_id"]]["restart"] == "refresh_duration"
                          and event["revision"] < state["revision"])
        if (uid not in draft["pending"] or state["generation_uid"] != event["generation_uid"]
                or state["revision"] != event["revision"] and not retained_timer):
            draft["trace"].append({"type": "stale_event", "event_id": uid, "lifecycle_id": event["lifecycle_id"],
                                   "time_us": event["time_us"], "reason": "generation_or_revision_cancelled"})
        else:
            draft["pending"].pop(uid)
            if event["kind"] == "state_establish": self._establish(draft, state, event["time_us"], context)
            elif event["kind"] == "state_tick": self._tick(draft, state, self.timers[event["timer_id"]], event["time_us"], context)
            else: self._close(draft, state, event["time_us"], context, "natural_expiry")
        out = self._finish(draft, event["time_us"])
        self._processed.add(uid)
        self._remember_context(context, priority=event["system_priority"])
        return out

    def cancel_source(self, parent_run_id, event):
        """Cancel only not-yet-established instances explicitly allowing it."""
        self._check_pending_scan()
        _text(parent_run_id, "parent_run_id", 512)
        context = self._context(event); draft = self._begin()
        for lid in sorted(draft["states"], key=lambda lid: self.lives[lid]["end_order"]):
            state, life = draft["states"][lid], self.lives[lid]
            if (state["parent"]["run_id"] == parent_run_id and state["phase"] == "pending_establishment"
                    and life["pending_cancel"] == "source_action_cancel_before_establishment"):
                self._close(draft, state, context["t_us"], context, "cancelled_before_establishment")
        out = self._finish(draft, context["t_us"])
        self._remember_context(context)
        return out

    def observe_position(self, event):
        """Clock2 records a real native phase even when no lifecycle matches."""
        self._check_pending_scan()
        if self._position_index is None:
            raise StateRuntimeError("state_position", "仅clock2支持位置观察")
        context = self._context(event)
        if context["position"]["stage"] != "native_observe":
            raise StateRuntimeError("state_position", "观察入口不能代替资源continuation")
        return self._finish(self._begin(), context["t_us"])

    def end(self, lifecycle_id, event, *, eligible):
        """Explicit caller-qualified end; this module does not evaluate end_trigger."""
        self._check_pending_scan()
        _text(lifecycle_id, "lifecycle_id")
        if lifecycle_id not in self.lives or type(eligible) is not bool: raise StateRuntimeError("state_context", "结束资格/生命周期无效")
        context = self._context(event)
        if self._position_index is not None:
            endpoint = self.lives[lifecycle_id].get("end_trigger", {})
            typed = endpoint.get("kind") == "resource_threshold"
            continuation = context["position"]["stage"] in ("resource_tail", "batch_tail")
            if typed != continuation or typed and endpoint["binding_id"] != context["position"]["binding_id"]:
                raise StateRuntimeError("state_end_phase", "continuation没有绑定此typed结束端点")
        draft = self._begin(); state = draft["states"].get(lifecycle_id)
        if eligible and state and state["phase"] != "ended": self._close(draft, state, context["t_us"], context, "explicit_end")
        out = self._finish(draft, context["t_us"])
        self._remember_context(context)
        return out

    def active(self, lifecycle_id, t_us):
        _text(lifecycle_id, "lifecycle_id")
        _integer(t_us, "t_us")
        state = self._states.get(lifecycle_id)
        return bool(state and state["phase"] == "active" and state["established_us"] <= t_us < state["end_us"])

    def boundary(self, t_us, *, retained_captures=()):
        """Normalize time/UID, preserving caller-supplied future snapshot values.

        Captures use a stable semantic ``slot``, lifecycle/generation references,
        and immutable JSON payload. This service cannot discover omitted captures
        or validate the payload's game meaning; the adapter owns that obligation.
        """
        self._check_pending_scan()
        _integer(t_us, "t_us")
        if t_us < self._time or any(row["time_us"] < t_us for row in self._pending.values()):
            raise StateRuntimeError("state_due_event", "边界前存在未执行事件或时间回退")
        try:
            if not isinstance(retained_captures, (list, tuple)):
                raise StateRuntimeError("state_capture_context", "保留捕获数据必须为列表或元组")
            if len(retained_captures) > 128 or len(_json(retained_captures)) > LIMITS["capture_bytes"]:
                raise StateRuntimeError("state_capture_limit", "保留捕获数据超限", severity="pending")
        except (ValueError, TypeError, RecursionError, OverflowError) as exc:
            if isinstance(exc, StateRuntimeError): raise
            raise StateRuntimeError("state_capture_context", "捕获数据不是有限JSON") from exc
        states, relations, slots, captures = {}, {}, set(), []
        for lid in sorted(self.lives):
            state = self._states.get(lid)
            if not state or state["phase"] == "ended":
                states[lid] = {"phase": "inactive"}
                continue
            relations[state["generation_uid"]] = "active:"+lid
            states[lid] = {"phase": state["phase"], "establish_in_us": max(0, state["established_us"]-t_us),
                           "end_in_us": state["end_us"]-t_us,
                           "timers": {tid: {"next_in_us": tick["next_us"]-t_us if tick["remaining"] else None,
                                            "remaining": tick["remaining"], "ordinal": tick["ordinal"]}
                                      for tid, tick in sorted(state["timers"].items())}}
        for capture in retained_captures:
            if not isinstance(capture, dict) or set(capture) != {"slot", "lifecycle_id", "generation_uid", "payload"}:
                raise StateRuntimeError("state_capture_context", "捕获引用字段错误")
            _text(capture["slot"], "capture.slot"); _text(capture["generation_uid"], "capture.generation_uid", 1024)
            _text(capture["lifecycle_id"], "capture.lifecycle_id")
            if capture["slot"] in slots or capture["lifecycle_id"] not in self.lives:
                raise StateRuntimeError("state_capture_context", "捕获slot重复或生命周期不存在")
            if self._generation_life.get(capture["generation_uid"]) != capture["lifecycle_id"]:
                raise StateRuntimeError("state_capture_context", "捕获必须引用此执行中实际产生的同生命周期代")
            slots.add(capture["slot"])
        old = 0
        for capture in sorted(retained_captures, key=lambda row: row["slot"]):
            uid = capture["generation_uid"]
            if uid not in relations:
                relations[uid] = f"old:{old}"; old += 1
            captures.append({"slot": capture["slot"], "lifecycle_id": capture["lifecycle_id"],
                             "generation_relation": relations[uid], "payload": copy.deepcopy(capture["payload"])})
        return {"lifecycles": states, "retained_captures": captures}
