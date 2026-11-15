"""Bridge finite clocks to the existing mechanics phase/pool interfaces.

The main queue hooks are installed separately. ``ctx.phase`` must support scoped
rule/operation sets and call ``authorize_dispatch`` before executing a reserved
route. Source approval remains an App obligation. No arithmetic is reimplemented.
"""
from __future__ import annotations

import copy
import hashlib
import json

from genshin_engine.shared_pools import _schema
from genshin_engine.state_runtime import (
    StateRuntime,
    StateRuntimeError,
    validate_state_runtime,
)
from genshin_engine.state_runtime_mechanics_schema import (
    DISPATCH_SELECTOR,
    RESERVED_PREFIXES,
    START_END_EVENTS,
)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode("utf8")).hexdigest()


def route_event_id(kind, ref_id):
    if kind not in ("dispatch_rule", "resource_operation") or type(ref_id) is not str or not ref_id:
        raise StateRuntimeError("state_route", "内部路由的类型或目标ID无效")
    return "@state_route:" + _hash([kind, ref_id])


def reserved_id(value):
    return type(value) is str and value.startswith(RESERVED_PREFIXES)


def validate_state_runtime_bindings(model, **references):
    """Return structured diagnostics; core still validates its full model schema."""
    try:
        for name, values in references.items():
            if not isinstance(values, (list, tuple, set, frozenset)) or any(type(v) is not str for v in values):
                raise StateRuntimeError("state_binding_schema", "引用目录必须为文本集合", name)
        return _validate_state_runtime_bindings(model, **references)
    except StateRuntimeError as exc:
        return [exc.issue]
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        return [StateRuntimeError("state_binding_schema", "生命周期桥引用图结构无效", reason=str(exc)).issue]


def _validate_state_runtime_bindings(model, *, character_ids, event_ids, action_ids, conditions,
                                     damage_type_ids=(), action_type_ids=()):
    """Validate the graph and caller's complete expanded ID catalogue, not approval."""
    config = model["state_runtime"]
    rules = {r["id"]: r for r in model["rules"]}
    shared = model.get("shared_pools", {"pools": [], "operations": []})
    operations, pools = {r["id"]: r for r in shared["operations"]}, {r["id"]: r for r in shared["pools"]}
    issues = validate_state_runtime(config, character_ids=character_ids, rule_ids=list(rules), operation_ids=list(operations))
    if issues: return issues

    def problem(code, message, path, *, severity="error", **details):
        issues.append({"code": code, "message": message, "path": path, "severity": severity, "details": details})

    # Callers must pass IDs after action/hit/application/implicit expansion. The
    # local inputs are also inspected so skipping a native condition is not safe.
    native = [*event_ids, *action_ids, *(r.get("id") for r in model.get("condition_events", [])),
              *(r.get("hit_id") for r in model.get("hit_events", [])),
              *(r.get("id") for r in model.get("hit_patterns", [])),
              *(r.get("id") for r in model.get("application_events", []))]
    for ident in native:
        if reserved_id(ident): problem("state_reserved_identity", "原生或隐式事件不能使用桥内部身份", str(ident))
    lives = {r["id"]: r for r in config["lifecycles"]}
    targets = {}
    for life in lives.values():
        for field in ("start", "end_trigger"):
            selector = life.get(field)
            if selector is None: continue
            path = "state_runtime.lifecycles."+life["id"]+"."+field
            if selector.get("kind") == "resource_threshold":
                binding = next((b for b in model.get("resource_threshold_triggers", {}).get("bindings", []) if b["id"] == selector["binding_id"]), None)
                expected = {"kind": "start_lifecycle" if field == "start" else "end_lifecycle", "ref_id": life["id"]}
                if (model.get("resource_threshold_triggers", {}).get("schema_version") not in (3, 4) or binding is None
                        or binding["payload"] != expected or binding["owner"] != life["owner"] or binding["source_id"] != life["source_id"]):
                    problem("state_threshold_binding", "typed端点没有完整双向v3来源绑定", path)
                continue
            if selector["event"] not in START_END_EVENTS:
                problem("state_parent_scope", "首片只接动作、切人和显式条件，命中/反应嵌套生命周期待后续", path)
            for key, allowed in (("event_ids", event_ids), ("action_ids", action_ids), ("characters", character_ids),
                                 ("damage_type_ids", damage_type_ids), ("action_type_ids", action_type_ids)):
                if key in selector and not set(selector[key]) <= set(allowed):
                    problem("state_selector_reference", "生命周期筛选引用不存在", path+"."+key)
            if "condition" in selector and selector["condition"] not in conditions:
                problem("state_condition_missing", "生命周期条件无显式资料，不推测为真", path+".condition", severity="pending")
        for stage, hooks in life["hooks"].items():
            for hook in hooks:
                targets.setdefault((hook["kind"], hook["ref_id"]), []).append((life, life["source_id"], "lifecycle_"+stage))
    for timer in config["timers"]:
        payload = timer["payload"]
        targets.setdefault((payload["kind"], payload["ref_id"]), []).append((lives[timer["lifecycle_id"]], timer["source_id"], "timer_tick"))
    for (kind, ident), bindings in targets.items():
        path = ("rules." if kind == "dispatch_rule" else "shared_pools.operations.")+ident
        if len(bindings) != 1:
            problem("state_dispatch_ownership", "首片每个目标只能由一个hook或timer拥有", path)
        life, source, _ = bindings[0]
        target = (rules if kind == "dispatch_rule" else operations)[ident]
        checks = []
        _schema(target["trigger"], DISPATCH_SELECTOR, path+".trigger", checks)
        issues.extend({**r, "code": "state_dispatch_selector"} for r in checks)
        if target["trigger"].get("event_ids") != [route_event_id(kind, ident)]:
            phase = target["trigger"].get("phase", "pre")
            problem("state_dispatch_ownership", "目标必须唯一绑定其桥内部路由，不能再由手工条件触发", path+".trigger",
                route_contract="state-runtime-route-id-v1", target_kind=kind, target_ref_id=ident,
                expected_selector={"event": "condition", "phase": phase if phase in ("pre", "post") else "pre",
                                   "event_ids": [route_event_id(kind, ident)]},
                authority="none")
        if target["source_id"] != source:
            problem("state_dispatch_source", "指令来源与目标来源不一致", path)
        owner = target.get("owner") if kind == "dispatch_rule" else pools[target["pool_id"]]["owner"]
        if owner != life["owner"]: problem("state_dispatch_owner", "生命周期与执行目标提供者不一致", path)
        if "characters" in target["trigger"] and not set(target["trigger"]["characters"]) <= set(character_ids):
            problem("state_selector_reference", "目标受控筛选角色不存在", path+".trigger.characters")
        if "condition" in target["trigger"] and target["trigger"]["condition"] not in conditions:
            problem("state_condition_missing", "目标筛选条件没有显式资料", path+".trigger.condition", severity="pending")
        if kind == "dispatch_rule":
            if (target.get("quota") is not None or target.get("resource_gate") is not None or target.get("resources")
                    or target["timing"]["delay"] != 0 or target["beneficiaries"]["mode"] == "next_switch"
                    or target["stacking"] != {"policy": "refresh", "max": 1, "add": 1}):
                problem("state_rule_scope", "周期目标首片只接受零延迟同源refresh一层，不带quota/gate/产能/next_switch", path)
        elif target["operation"] not in (("set", "grant", "spend") if shared.get("schema_version") == 2 else ("set", "grant")):
            problem("state_operation_scope", "资源目标支持set/grant；显式计量v2另支持spend", path)
    for kind, collection in (("dispatch_rule", rules), ("resource_operation", operations)):
        for ident, row in collection.items():
            ids = row["trigger"].get("event_ids", [])
            if any(reserved_id(x) for x in ids) and (kind, ident) not in targets:
                problem("state_dispatch_ownership", "内部路由目标没有唯一生命周期授权", ident)
    return issues


class _PendingScan:
    __slots__ = ('bridge', 'clock', 'ctx', 'cursor', 'delivery', 'end', 'queue', 'sequence', 'time')

    def __init__(self, bridge, queue, cursor):
        self.bridge, self.ctx, self.clock = bridge, bridge.ctx, bridge.clock
        self.queue, self.cursor, self.end = queue, cursor, len(queue)
        self.time, self.delivery = bridge.ctx.time, bridge._active_delivery
        self.sequence = bridge.clock._control_sequence


class StateRuntimeMechanics:
    """Finite adapter. Only the caller's trusted queue can observe actual events.

    ``ctx.phase`` is scoped but retains FILTER, internal cooldown, snapshot and
    beneficiary logic. It must authorize the non-JSON object token before using
    scopes or suppressing recursive observation. String IDs cannot grant access.
    """

    def __init__(self, model, ctx, *, character_ids, event_ids, action_ids, conditions,
                 external_events, damage_type_ids=(), action_type_ids=()):
        issues = validate_state_runtime_bindings(model, character_ids=character_ids, event_ids=event_ids,
            action_ids=action_ids, conditions=conditions, damage_type_ids=damage_type_ids, action_type_ids=action_type_ids)
        if issues:
            first = issues[0]
            raise StateRuntimeError(first["code"], first["message"], first["path"], severity=first["severity"], issues=issues)
        self.ctx = ctx
        self._counter_enabled = "resource_counters" in model
        self._positioned = model["state_runtime"]["schema_version"] == 2
        self._ordered_enabled = self._counter_enabled or self._positioned
        self.clock = StateRuntime(model["state_runtime"], character_ids=character_ids,
            reserve_events=lambda count: ctx._reserve_events(count, "state_runtime"), external_events=external_events,
            rule_ids=[r["id"] for r in model["rules"]],
            operation_ids=[r["id"] for r in model.get("shared_pools", {}).get("operations", [])],
            emit_ordered_steps=self._ordered_enabled,
            resource_limits=model.get('execution_options', {}).get('resource_limits'))
        self._authority, self._active_authorization = object(), None
        self._active_dispatch_objects = None
        self._authorization_phase = None
        self._origins, self._parents, self._queued, self._applied, self._trace = {}, {}, {}, set(), []
        self._queue_fingerprints = {}
        self._target_fingerprints = {("dispatch_rule", r["id"]): _hash(r) for r in model["rules"]}
        self._target_fingerprints.update({("resource_operation", r["id"]): _hash(r)
                                         for r in model.get("shared_pools", {}).get("operations", [])})
        self._failed = False
        self._pending_scan = None
        self._active_delivery = None
        self._applied_control_sequence = 0
        self._life_command_authority = object()
        self._lifecycle_fingerprints = {r["id"]: _hash(r) for r in model["state_runtime"]["lifecycles"]}
        self._v2_delivery_bytes = 2
        self._applied_lifecycles = {}
        self._lifecycle_task_records = {}
        config = model["state_runtime"]
        lives = {row["id"]: row for row in config["lifecycles"]}
        timers = {row["id"]: row for row in config["timers"]}
        self._clock_definition_fingerprint = _hash({"config": config, "lives": lives, "timers": timers,
            "by_life": {lid: sorted([t for t in timers.values() if t["lifecycle_id"] == lid], key=lambda t: t["order"])
                        for lid in lives}}) if self._positioned else None
        self._verify_clock_definition()

    def _verify_clock_definition(self):
        """Bind clock2's actual directories as well as declarative timer rows.

        A copied/reindexed timer must not evade the original complete graph by
        retaining an unchanged life row or changing only a derived lookup list.
        This is execution integrity, not App evidence/source approval.
        """
        if not self._positioned:
            return
        observer = getattr(self, '_resource_qualification', None)
        if observer is not None:
            observer.verify_clock_guard(self)
            return
        try:
            actual = _hash({"config": self.clock.config, "lives": self.clock.lives,
                "timers": self.clock.timers, "by_life": self.clock.by_life})
        except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
            actual = None
        if actual != self._clock_definition_fingerprint:
            self._failed = True
            raise StateRuntimeError("state_threshold_authority", "实际生命周期、定时器或分组索引已偏离完整核验图")

    def _ready(self):
        self._reject_pending_scan()
        observer = getattr(self, "_resource_qualification", None)
        if observer: observer.before_control()
        if self._failed: raise StateRuntimeError("state_bridge_failed", "桥前一指令失败；只可报告已完成前缀，不能继续执行")
        self._verify_clock_definition()

    def _reject_pending_scan(self):
        if self._pending_scan is not None:
            self._failed = True
            raise StateRuntimeError('state_pending_scan', '只读队列扫描期间不能重入状态控制', severity='pending')

    def _scan_identity(self, scan):
        observer = getattr(self, '_resource_qualification', None)
        if (type(scan) is not _PendingScan or self._pending_scan is not scan
                or scan.bridge is not self or scan.ctx is not self.ctx or scan.clock is not self.clock
                or self.ctx.state_runtime is not self or observer is None
                or scan.time != self.ctx.time or scan.delivery is not self._active_delivery
                or scan.sequence != self.clock._control_sequence or len(scan.queue) != scan.end):
            raise StateRuntimeError('state_pending_scan', '只读扫描凭据、原队列或状态控制上下文已改变', severity='pending')
        observer.identities()

    def _scan_keep_pending(self, scan, queued):
        self._scan_identity(scan)
        if scan.cursor >= scan.end or scan.queue[scan.cursor] is not queued:
            raise StateRuntimeError('state_pending_scan', '不能重放或替换只读扫描的当前事件', severity='pending')
        self._resource_qualification.charge_pending_item()
        scan.cursor += 1
        if queued.get('kind') != 'state_runtime_event':
            return True
        if type(queued.get('run_id')) is not str or self._queued.get(queued['run_id']) is not queued:
            raise StateRuntimeError('state_queue_authority', '未知内部排队对象')
        if _hash(queued) != self._queue_fingerprints[queued['run_id']]:
            raise StateRuntimeError('state_queue_authority', '内部队列对象内容在签发后改变')
        return queued['run_id'] in {r['run_id'] for r in self.clock.pending()}

    def _filter_pending(self, expanded, cursor):
        self._reject_pending_scan()
        observer = getattr(self, '_resource_qualification', None)
        if (observer is None or not self._positioned or type(expanded) is not list
                or type(cursor) is not int or not 0 <= cursor <= len(expanded)):
            raise StateRuntimeError('state_pending_scan', '只读扫描需要原实际状态桥和有限队列', severity='pending')
        observer.reserve_pending_scan(len(expanded)-cursor)
        scan = _PendingScan(self, expanded, cursor)
        self._pending_scan = scan
        try:
            self._scan_identity(scan)
            self._verify_clock_definition()
            if self._failed:
                raise StateRuntimeError('state_bridge_failed', '失败状态桥不能继续过滤队列')
            result = []
            while scan.cursor < scan.end:
                item = expanded[scan.cursor]
                if self._scan_keep_pending(scan, item) and self.ctx._keep_pending_elemental(item):
                    result.append(item)
            return result
        finally:
            try:
                # Even an empty queue, predicate failure or cancelled last
                # item executes the complete guard before publishing a list.
                self._scan_identity(scan)
                self._verify_clock_definition()
                if self._failed:
                    raise StateRuntimeError('state_pending_scan', '扫描期间状态控制失败，不能提交过滤结果', severity='pending')
            finally:
                self._pending_scan = None

    def _context(self, event, phase, parent):
        if reserved_id(event["id"]) or reserved_id(parent["id"]):
            raise StateRuntimeError("state_reserved_identity", "不能把内部派发重新当原生生命周期父事件")
        run_id, causal = parent["run_id"], list(parent.get("causal", ()))
        if event["event"] == "switch":
            run_id += ":switch"
            # Logical switch callbacks finish inside the action before its
            # queued children (the existing enqueue serials are positive).
            # Instant action_end already owns the reserved -1 child position.
            # Logical switch must finish strictly before that queued end.
            causal.append(-2)
        elif event['event'] == 'elemental_reaction':
            # A synchronous reaction completes after its native hit post phase
            # and before any queued positive-serial child. It has its own real
            # run identity and must not reuse the parent's queue position.
            causal.append(0)
        result = {"event_id": event["id"], "run_id": run_id, "t_us": parent["time"],
                  "order": parent["order"], "phase": phase, "causal": causal}
        if self._positioned:
            result["position"] = {"anchor": {"kind": "native", "queue_event_id": event["id"], "queue_run_id": run_id,
                "time_us": parent["time"], "system_priority": parent.get("system_priority", 0),
                "order": parent["order"], "causal": causal}, "phase": phase, "stage": "native_observe"}
        return result

    def observe(self, event, phase, parent, *, successful):
        self._ready()
        if type(successful) is not bool: raise StateRuntimeError("state_context", "成功资格必须为布尔")
        if not successful: return []
        if self._positioned and event["event"] not in {"action_start", "action_end", "switch", "hit", "condition"}:
            return []  # Synchronous reaction callbacks are not clock2 native anchors.
        context = self._context(event, phase, parent)
        if self.ctx.time != context["t_us"]: raise StateRuntimeError("state_context", "桥与机制时钟不一致")
        queued = []
        if self._positioned:
            queued.extend(self._deliver(self.clock.observe_position(context)))
        for field, order in (("end_trigger", "end_order"), ("start", "start_order")):
            for life in sorted(self.clock.lives.values(), key=lambda row: row[order]):
                selector = life.get(field)
                if selector is None or selector.get("kind") == "resource_threshold" or selector["phase"] != phase:
                    continue
                if not self.ctx.filter(selector, event, "state_runtime."+life["id"]+"."+field, owner=life["owner"]):
                    continue
                out = (self.clock.start if field == "start" else self.clock.end)(life["id"], context, eligible=True)
                self._origins[context["run_id"]] = event["origin"]
                self._parents[context["run_id"]] = copy.deepcopy(event)
                queued.extend(self._deliver(out))
        return queued

    def cancel_source(self, source_run_id, event, phase, parent):
        self._ready()
        context = self._context(event, phase, parent)
        if self.ctx.time != context["t_us"]: raise StateRuntimeError("state_context", "桥与机制时钟不一致")
        return self._deliver(self.clock.cancel_source(source_run_id, context))

    def _deliver(self, out):
        self._reject_pending_scan()
        if self._positioned:
            return self._deliver_v2(out)
        queued = []
        for issued in out["scheduled"]:
            parent_run = issued["source_event"]["run_id"]
            row = {"id": issued["id"], "run_id": issued["run_id"], "kind": "state_runtime_event",
                   "time": issued["time_us"], "order": issued["order"], "system_priority": issued["system_priority"],
                   "causal": tuple(issued["causal"]), "origin": self._origins[parent_run], "data": copy.deepcopy(issued)}
            self._queued[row["run_id"]] = row
            self._queue_fingerprints[row["run_id"]] = _hash(row)
            queued.append(row)
        if self._counter_enabled:
            try:
                steps = self.clock.consume_ordered_steps(out["ordered_steps"])
            except Exception:
                self._failed = True
                raise
        else:
            steps = [{"kind": "dispatch", "dispatch": dispatch} for dispatch in out["dispatches"]]
        for step in steps:
            dispatch = step.get("dispatch")
            try:
                if self._counter_enabled:
                    self.ctx.shared_pools.ledger.apply_counter_control(step)
                    self._trace.append({"type": "state_control_completed", "kind": step["kind"],
                        "control_sequence": step["sequence"], "time_us": step["time_us"],
                        "lifecycle_id": step["lifecycle_id"], "generation_uid": step["generation_uid"],
                        "event_run_id": step["event_run_id"]})
                if dispatch is not None: self._dispatch(dispatch)
            except Exception as exc:
                self._failed = True
                self._trace.append({"type": "state_dispatch_failed" if dispatch is not None else "state_control_failed",
                    "run_id": dispatch["run_id"] if dispatch is not None else step["event_run_id"],
                    "payload": copy.deepcopy(dispatch["payload"]) if dispatch is not None else {"kind": step["kind"]},
                    **({key: step[key] for key in ("time_us", "lifecycle_id", "generation_uid", "event_run_id")}
                       | {"control_sequence": step["sequence"]} if self._counter_enabled else {}),
                    "message": str(exc)})
                raise
        return queued

    def _dispatch(self, dispatch):
        self._reject_pending_scan()
        self._verify_clock_definition()
        uid = dispatch["run_id"]
        if uid in self._applied: raise StateRuntimeError("state_dispatch_replay", "同一生命周期指令不能再次执行")
        kind, ident = dispatch["payload"]["kind"], dispatch["payload"]["ref_id"]
        target = self.ctx.rules.get(ident) if kind == "dispatch_rule" else next(
            (r for r in self.ctx.shared_pools.config["operations"] if r["id"] == ident), None) if self.ctx.shared_pools else None
        if target is None or _hash(target) != self._target_fingerprints.get((kind, ident)):
            raise StateRuntimeError("state_dispatch_target_changed", "执行目标在桥核验后改变，不能沿用旧资格或来源")
        source_run = dispatch["source_event"]["run_id"]
        source = self._parents[source_run]
        event = {"event": "condition", "id": route_event_id(kind, ident), "run_id": uid,
                 "origin": self._origins[source_run], "conditions": dict(self.ctx.conditions),
                 "character": dispatch["owner"], "source_event_id": source["id"], "source_run_id": source_run,
                 "state_event": dispatch["event"], "lifecycle_id": dispatch["lifecycle_id"],
                 "timer_id": dispatch.get("timer_id"), "ordinal": dispatch.get("ordinal"),
                 "generation_uid": dispatch["generation_uid"], "state_frame": dispatch["frame"],
                 "source_id": dispatch["source_id"]}
        if "action_id" in source: event["action_id"] = source["action_id"]
        if "terminal_permit" in dispatch: event["terminal_permit"] = copy.deepcopy(dispatch["terminal_permit"])
        parent = {"id": event["id"], "run_id": uid, "origin": event["origin"], "time": dispatch["time_us"],
                  "order": dispatch["order"], "causal": ()}
        rule_ids = frozenset({ident}) if kind == "dispatch_rule" else frozenset()
        operation_ids = frozenset({ident}) if kind == "resource_operation" else frozenset()
        if self.ctx.time != dispatch["time_us"]: raise StateRuntimeError("state_context", "派发时间与当前机制时钟不一致")
        self._active_authorization = (copy.deepcopy(event), copy.deepcopy(parent), rule_ids, operation_ids)
        self._active_dispatch_objects = (event, parent)
        self._authorization_phase = "pre"
        issue_start, trace_start = len(self.ctx.issues), len(self.ctx.trace)
        pool_start = len(self.ctx.shared_pools.trace) if self.ctx.shared_pools else 0
        try:
            for phase in ("pre", "post"):
                self.ctx.phase(event, phase, parent, allowed_rule_ids=rule_ids, allowed_operation_ids=operation_ids,
                               observe_state=False, state_authority=self._authority)
                if self._authorization_phase == phase:
                    raise StateRuntimeError("state_dispatch_authority", "机制phase未核验桥签发授权，不能认证执行完成")
        finally:
            self._active_authorization = None
            self._active_dispatch_objects = None
            self._authorization_phase = None
        self._applied.add(uid)
        activations = [r for r in self.ctx.trace[trace_start:] if r.get("type") == "activation" and r.get("rule_id") == ident]
        operations = [r for r in self.ctx.shared_pools.trace[pool_start:] if r.get("operation_id") == ident] if self.ctx.shared_pools else []
        # Global issues are deduplicated by the existing FILTER. Its per-event
        # rejection trace remains occurrence-specific even on later ticks.
        pending = (any(r.get("severity") == "pending" for r in self.ctx.issues[issue_start:])
                   or any(r.get("reason") == "unknown_condition" and r.get("event_id") == event["id"]
                          for r in self.ctx.trace[trace_start:]))
        outcome = "pending" if pending else "activated" if activations else "resource_operation" if operations else "not_activated"
        self._trace.append({"type": "state_dispatch_completed", "outcome": outcome, "run_id": uid, "event": dispatch["event"],
            "lifecycle_id": dispatch["lifecycle_id"], "timer_id": dispatch.get("timer_id"),
            "ordinal": dispatch.get("ordinal"), "time_us": dispatch["time_us"], "frame": dispatch["frame"],
            "payload": copy.deepcopy(dispatch["payload"]), "source_event_id": source["id"],
            "source_run_id": source_run, "activation_count": len(activations), "operation_count": len(operations),
            "qualification": "existing_phase_filter_and_cooldown"})
        return outcome

    def _v2_trace_candidate(self, row):
        size = len(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8"))+1
        if self._v2_delivery_bytes+size > 32*1024*1024:
            raise StateRuntimeError("state_trace_limit", "实际控制交付流水字节超限", severity="pending")
        return row, size

    def _deliver_v2(self, out):
        self._reject_pending_scan()
        previous = self._active_delivery
        if previous is not None and not previous["complete"]:
            raise StateRuntimeError("state_applied_barrier", "前一控制批没有实际交付完，不准插入新控制")
        batch = out["ordered_steps"]
        steps = self.clock.consume_ordered_steps(batch)
        record = {"batch": batch, "fingerprint": _hash(batch), "steps": steps, "applied": 0,
            "complete": False, "tasks": [], "anchor": copy.deepcopy(self.clock._position_index.cursor["anchor"])}
        self._active_delivery = record
        queued, step = [], None
        observer = getattr(self, "_resource_qualification", None)
        hooks = getattr(self, "_resource_qualification_hooks", None)
        observation_token = None
        try:
            if observer:
                if hooks is None: raise StateRuntimeError("state_applied_barrier", "实际控制钩子未绑定", severity="pending")
                observation_token = hooks[0](record)
            for issued in out["scheduled"]:
                source = issued["source_event"]["run_id"]
                row = {"id": issued["id"], "run_id": issued["run_id"], "kind": "state_runtime_event",
                    "time": issued["time_us"], "order": issued["order"], "system_priority": issued["system_priority"],
                    "causal": tuple(issued["causal"]), "origin": self._origins[source], "data": copy.deepcopy(issued)}
                self._queued[row["run_id"]] = row
                self._queue_fingerprints[row["run_id"]] = _hash(row)
                queued.append(row)
            for step in steps:
                if step["sequence"] != self._applied_control_sequence+1:
                    raise StateRuntimeError("state_applied_barrier", "实际控制应用不能跳序")
                row, size = self._v2_trace_candidate({"type": "state_control_completed", "kind": step["kind"],
                    "control_sequence": step["sequence"], **{k: step[k] for k in ("time_us", "lifecycle_id", "generation_uid", "event_run_id")}})
                applied = {"kind": step["kind"], "generation_uid": step["generation_uid"],
                    "revision": step["revision"], "time_us": step["time_us"], "control_sequence": step["sequence"]}
                applied["phase"] = {"lifecycle_start": "created", "lifecycle_established": "established",
                    "lifecycle_closing": "closing", "lifecycle_end": "ended"}.get(step["kind"],
                        self._applied_lifecycles.get(step["lifecycle_id"], {}).get("phase", "inactive"))
                if self._counter_enabled:
                    self.ctx.shared_pools.ledger.apply_counter_control(step)
                else:
                    self.ctx._reserve_events(1, "state_runtime")
                self._applied_control_sequence = step["sequence"]
                self._applied_lifecycles[step["lifecycle_id"]] = applied
                self._trace.append(row)
                self._v2_delivery_bytes += size
                if step.get("dispatch") is not None:
                    outcome = self._dispatch(step["dispatch"])
                    if outcome == "pending":
                        raise StateRuntimeError("state_dispatch_pending", "当前派发资格未知，控制批不能标为实际完成", severity="pending")
                    if outcome not in ("activated", "resource_operation", "not_activated"):
                        raise StateRuntimeError("state_applied_barrier", "派发未返回明确完成结果")
                record["applied"] += 1
            if (record["applied"] != len(steps) or _hash(batch) != record["fingerprint"]
                    or self._counter_enabled and self.ctx.shared_pools.ledger._counter_state["last_control_sequence"] != self._applied_control_sequence):
                raise StateRuntimeError("state_applied_barrier", "原控制批身份或实际末游标不一致")
            row, size = self._v2_trace_candidate({"type": "state_batch_completed", "batch_id": batch["id"],
                "control_sequence": self._applied_control_sequence, "time_us": self.ctx.time, "step_count": len(steps)})
            self.ctx._reserve_events(1, "state_runtime")
            record["complete"] = True
            self._trace.append(row)
            self._v2_delivery_bytes += size
            if observer: hooks[1](record, observation_token)
            for task in record["tasks"]:
                # Every preceding target's fresh control batch must itself have
                # completed before this next target may create another one.
                if (not record["complete"] or self.clock._control_sequence != self._applied_control_sequence
                        or self._counter_enabled and self.ctx.shared_pools.ledger._counter_state["last_control_sequence"] != self._applied_control_sequence):
                    raise StateRuntimeError("state_applied_barrier", "新目标不能越过未真实交付的新控制批")
                self._apply_lifecycle_task(task, source_record=record)
        except Exception as exc:
            self._failed = True
            for task in record["tasks"]:
                if task["command"]["dispatch_run_id"] in task["runtime"]._lifecycle_commands:
                    task["runtime"].fail_lifecycle_command(task["command"], authority=task["authority"], exc=exc, time_us=self.ctx.time)
            self._trace.append({"type": "state_delivery_failed", "batch_id": batch["id"], "time_us": self.ctx.time,
                "applied_steps": record["applied"], "control_sequence": self._applied_control_sequence,
                "complete": False, "code": getattr(exc, "issue", {}).get("code", "state_delivery_error"), "message": str(exc)[:1024]})
            raise
        finally:
            try:
                if observer and observation_token is not None: hooks[2](record, observation_token)
            except Exception:
                self._failed = True
                raise
            finally:
                self._active_delivery = previous
        return queued

    def accept_lifecycle_command(self, runtime, command, event, phase, parent, batch_context, *, qualified, authority):
        self._ready()
        if not self._positioned or runtime is not self.ctx.shared_pools.threshold_runtime:
            raise StateRuntimeError("state_threshold_authority", "没有本次真实阈值runtime")
        runtime.authorize_lifecycle_command(command, authority=authority, active=True)
        origin = runtime.program._operation_origins[batch_context["command_id"]]
        record = None
        if origin["kind"] == "ordinary_timer":
            self.authorize_threshold_operation(event, phase, parent, batch_context["command_id"], origin)
            record = self._active_delivery
            if (record is None or record["complete"] or len(record["steps"]) != 1
                    or record["steps"][0].get("dispatch", {}).get("event") != "timer_tick"):
                raise StateRuntimeError("state_applied_barrier", "life阈值来源只准单一普通timer派发批")
            anchor = copy.deepcopy(record["anchor"])
        else:
            anchor = self._context(event, phase, parent)["position"]["anchor"]
        task = {"runtime": runtime, "command": command, "command_hash": _hash(command), "authority": authority,
            "anchor": anchor, "source_phase": phase, "source": copy.deepcopy(event), "qualified": qualified,
            "batch_context": copy.deepcopy(batch_context), "source_record": record,
            "guard": self._life_command_authority}
        self._lifecycle_task_records[command["dispatch_run_id"]] = task, self._lifecycle_task_hash(task)
        if record is None:
            self._apply_lifecycle_task(task)
        else:
            if any(t["batch_context"]["receipt_span"] != task["batch_context"]["receipt_span"] for t in record["tasks"]):
                raise StateRuntimeError("state_applied_barrier", "首片同timer批不能混入多个资源事务")
            self.ctx._reserve_events(1, "resource_threshold_dispatch")
            record["tasks"].append(task)

    @staticmethod
    def _lifecycle_task_hash(task):
        return _hash({k: task[k] for k in ("command", "command_hash", "anchor", "source_phase", "source", "qualified", "batch_context")}
            | {"source_batch": task["source_record"]["batch"]["id"] if task["source_record"] is not None else None})

    def _apply_lifecycle_task(self, task, *, source_record=None):
        self._reject_pending_scan()
        observer = getattr(self, "_resource_qualification", None)
        if observer: observer.before_control()
        runtime, command = task["runtime"], task["command"]
        known = self._lifecycle_task_records.get(command["dispatch_run_id"])
        if (known is None or known[0] is not task or known[1] != self._lifecycle_task_hash(task)
                or task["guard"] is not self._life_command_authority or _hash(command) != task["command_hash"]
                or task["source_record"] is not source_record
                or source_record is not None and (source_record is not self._active_delivery or not source_record["complete"]
                    or type(source_record["applied"]) is not int or source_record["applied"] != len(source_record["steps"])
                    or _hash(source_record["batch"]) != source_record["fingerprint"])):
            raise StateRuntimeError("state_threshold_authority", "生命周期任务缺少原控制批实际完成及原命令身份")
        runtime.authorize_lifecycle_command(command, authority=task["authority"], active=False)
        self._verify_clock_definition()
        lid, kind = command["payload"]["ref_id"], command["payload"]["kind"]
        if _hash(self.clock.lives.get(lid)) != self._lifecycle_fingerprints.get(lid):
            raise StateRuntimeError("state_threshold_authority", "生命周期目标在认证后改变")
        if not task["qualified"]:
            runtime.complete_lifecycle_command(command, authority=task["authority"], outcome="not_activated", lifecycle_id=lid, time_us=self.ctx.time)
            self._lifecycle_task_records.pop(command["dispatch_run_id"])
            return
        position = {"anchor": copy.deepcopy(task["anchor"]), "phase": "post" if source_record is not None else task["source_phase"],
            "stage": "batch_tail" if source_record is not None else "resource_tail",
            "resource_span": copy.deepcopy(task["batch_context"]["receipt_span"]),
            "binding_id": command["binding_id"], "binding_order": command["order"],
            "continuation_sequence": (self.clock._position_index.continuation["continuation_sequence"] if self.clock._position_index.continuation else 0)+1,
            "source": {"dispatch_run_id": command["dispatch_run_id"], "operation_run_id": task["batch_context"]["run_id"],
                "operation_id": task["batch_context"]["command_id"], "phase": task["source_phase"]}}
        context = {"event_id": command["dispatch_run_id"], "run_id": command["dispatch_run_id"],
            "t_us": self.ctx.time, "order": position["anchor"]["order"], "phase": task["source_phase"],
            "causal": copy.deepcopy(position["anchor"]["causal"]), "position": position}
        self.ctx._reserve_events(1, "resource_threshold_dispatch")
        previous_life = self.clock._states.get(lid)
        prior_active = previous_life is not None and previous_life["phase"] != "ended"
        outcome = ("no_active_instance" if not prior_active else "lifecycle_ended") if kind == "end_lifecycle" else (
            "lifecycle_kept" if prior_active and self.clock.lives[lid]["restart"] == "keep" else "lifecycle_started")
        out = (self.clock.start if kind == "start_lifecycle" else self.clock.end)(lid, context, eligible=True)
        if kind == "start_lifecycle" and prior_active and self.clock.lives[lid]["restart"] == "refresh_duration":
            outcome = ("lifecycle_refreshed" if any(row["type"] == "lifecycle_duration_refresh"
                                                   for row in out["trace"]) else "lifecycle_kept")
        self._origins[command["dispatch_run_id"]] = task["source"]["origin"]
        self._parents[command["dispatch_run_id"]] = task["source"]
        self.ctx.queue.extend(self._deliver(out))
        runtime.complete_lifecycle_command(command, authority=task["authority"], outcome=outcome,
            lifecycle_id=lid, time_us=self.ctx.time,
            target_generation_uid=self.clock._states.get(lid, {}).get("generation_uid"))
        self._lifecycle_task_records.pop(command["dispatch_run_id"])

    def authorize_dispatch(self, event, phase, parent, *, allowed_rule_ids, allowed_operation_ids, authority):
        self._reject_pending_scan()
        expected = self._active_authorization
        if (authority is not self._authority or expected is None or phase != self._authorization_phase
                or type(allowed_rule_ids) is not frozenset or type(allowed_operation_ids) is not frozenset
                or _hash([event, parent]) != _hash(expected[:2])
                or (allowed_rule_ids, allowed_operation_ids) != expected[2:]):
            raise StateRuntimeError("state_dispatch_authority", "桥内部事件或目标范围缺少本次不可复制执行授权")
        self._authorization_phase = "post" if phase == "pre" else "completed"
        return True

    def authorize_threshold_operation(self, event, phase, parent, operation_id, expected_origin):
        """Read current ordinary-timer provenance, without consuming phase twice.

        The original event/parent objects must still be inside the authorized
        phase. This grants no clock/control operation and cannot be copied as
        JSON; a separate original ledger batch is required to execute a target.
        """
        self._ready()
        active, expected = self._active_dispatch_objects, self._active_authorization
        if (active is None or expected is None or event is not active[0] or parent is not active[1]
                or phase not in ("pre", "post")
                or self._authorization_phase != ("post" if phase == "pre" else "completed")
                or expected[2] or expected[3] != frozenset({operation_id})
                or _hash([event, parent]) != _hash(expected[:2])
                or event.get("state_event") != "timer_tick" or event.get("state_frame") != "after_parent"
                or "terminal_permit" in event or expected_origin != {"kind": "ordinary_timer",
                    "timer_id": event.get("timer_id"), "lifecycle_id": event.get("lifecycle_id")}):
            raise StateRuntimeError("state_threshold_authority", "阈值来源缺少此时正在执行的唯一普通timer原对象及阶段授权")
        return True

    def handle(self, queued):
        self._ready()
        if not isinstance(queued, dict) or type(queued.get("run_id")) is not str or self._queued.get(queued["run_id"]) is not queued:
            raise StateRuntimeError("state_queue_authority", "事件不是本桥交付给队列的实际对象")
        if _hash(queued) != self._queue_fingerprints[queued["run_id"]]:
            raise StateRuntimeError("state_queue_authority", "内部队列对象内容在签发后改变")
        if self.ctx.time != queued["time"]: raise StateRuntimeError("state_context", "队列时间与机制时钟不一致")
        return self._deliver(self.clock.handle(queued["data"]))

    def keep_pending(self, queued):
        self._verify_clock_definition()
        if queued.get("kind") != "state_runtime_event": return True
        if type(queued.get("run_id")) is not str or self._queued.get(queued["run_id"]) is not queued:
            raise StateRuntimeError("state_queue_authority", "未知内部排队对象")
        if _hash(queued) != self._queue_fingerprints[queued["run_id"]]:
            raise StateRuntimeError("state_queue_authority", "内部队列对象内容在签发后改变")
        return queued["run_id"] in {r["run_id"] for r in self.clock.pending()}

    def boundary(self, time, *, retained_captures=()):
        self._reject_pending_scan()
        self._verify_clock_definition()
        return self.clock.boundary(time, retained_captures=retained_captures)

    def report(self):
        return {"contract": "finite-state-mechanics-bridge-v2" if self._positioned else "finite-state-mechanics-bridge-v1", "engine_queue_integration": "adapter_protocol_only",
                **({'position_budget': {'policy': copy.deepcopy(self.clock._position_index.policy),
                    'usage': {'steps': self.clock._position_index.steps, 'bytes': self.clock._position_index.bytes}}}
                   if self._positioned else {}),
                "status": "failed" if self._failed else "active", "issued_trace": self.clock.trace,
                "execution_trace": copy.deepcopy(self._trace), "source_approval": "not_evaluated_by_engine",
                **({"state_semantics": "clock_issued_state_not_applied_on_failure", "applied_control_sequence": self._applied_control_sequence,
                    "applied_lifecycles": copy.deepcopy(self._applied_lifecycles)} if self._positioned else {})}
