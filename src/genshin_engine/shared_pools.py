"""Pure integer-resource ledger, separate from combat qualification.

``run_id`` identifies ONE actual queue event occurrence, e.g. ``0:hit.a``.
It MUST NOT be an axis/cycle id: distinct hits in one cycle need distinct IDs;
the same stable hit id in another cycle needs another run_id. The ledger checks
reuse consistency but cannot discover an omitted event or certify game facts.

The caller evaluates FILTER, actor/frontline relationships, live benefit rules,
landed/classic eligibility, and source acceptance. An eligible=True argument is
only an input, never a claim that this module has independently checked them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from decimal import Decimal

from genshin_engine.shared_pool_schema import LIMITS, SHARED_POOLS_SCHEMA
from genshin_engine.shared_pool_v2_schema import SCALED_POOLS_SCHEMA


def _issue(code, message, path, *, severity="error", **details):
    return {"code": code, "message": message, "path": path, "severity": severity, "details": details}


class SharedPoolError(ValueError):
    def __init__(self, code, message, path="shared_pools", **details):
        self.issue = _issue(code, message, path, **details)
        super().__init__(message)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")


def pool_capacity_mode(pool):
    """Return the declared gameplay-capacity mode, separate from safety limits."""
    return "unbounded" if pool["capacity"] == "unbounded" else "bounded"


def pool_capacity_limit(pool):
    """Return the largest safe stored value; not a gameplay cap for unbounded pools."""
    return LIMITS["count"] if pool_capacity_mode(pool) == "unbounded" else pool["capacity"]


def apply_pool_capacity(pool, value):
    """Apply a gameplay cap while failing, rather than clipping, at the safety bound."""
    if pool_capacity_mode(pool) == "bounded":
        return min(pool["capacity"], value)
    if value > LIMITS["count"]:
        raise SharedPoolError(
            "pool_balance_limit", "无上限资源余额超过工程安全界", "shared_pools.pools." + pool["id"],
            severity="pending", requested=value, safety_limit=LIMITS["count"])
    return value


def _schema(value, schema, path, issues):
    def fail(message):
        issues.append(_issue("pool_schema", message, path))
    if "oneOf" in schema:
        alternatives = []
        for branch in schema["oneOf"]:
            errors = []
            _schema(value, branch, path, errors)
            alternatives.append(errors)
        valid = sum(not errors for errors in alternatives)
        if valid == 1:
            return
        if valid:
            fail("字段同时匹配多个互斥结构")
            return
        if isinstance(value, dict):
            for branch, errors in zip(schema["oneOf"], alternatives, strict=True):
                if branch.get("properties", {}).get("operation", {}).get("const") == value.get("operation"):
                    issues.extend(errors)
                    return
        issues.extend(min(alternatives, key=len))
        return
    kind = schema.get("type")
    types = {"object": (dict,), "array": (list,), "string": (str,), "integer": (int,), "number": (int, float)}
    if kind in types and type(value) not in types[kind]:
        fail("字段类型错误")
        return
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        fail("字段常量错误")
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        fail("字段枚举错误")
    if kind == "object":
        for key in schema.get("required", []):
            if key not in value:
                issues.append(_issue("pool_schema", "缺少必填字段", path+"."+key))
        for key, item in value.items():
            if type(key) is not str:
                fail("对象键必须为字符串")
                continue
            child = schema.get("properties", {}).get(key, schema.get("additionalProperties", True))
            if child is False:
                issues.append(_issue("pool_schema", "不支持此字段", path+"."+key))
            elif isinstance(child, dict):
                _schema(item, child, path+"."+key, issues)
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 128):
            fail("数组数量超限")
        for index, item in enumerate(value[:max(schema.get("maxItems", 128), 1)]):
            _schema(item, schema["items"], f"{path}[{index}]", issues)
    elif kind == "string":
        if not value.strip() or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 128):
            fail("文本为空或超限")
    elif kind in ("number", "integer"):
        if (type(value) is float and not math.isfinite(value)) or not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            fail("数值必须有限且在范围内")


def _ids(value, name):
    if not isinstance(value, (list, tuple, set, frozenset)) or any(type(v) is not str or not v.strip() or len(v) > 128 for v in value):
        raise SharedPoolError("pool_context", "身份集合类型错误", name)
    if len(set(value)) != len(value):
        raise SharedPoolError("pool_context", "身份集合重复", name)
    return set(value)


def validate_shared_pools(config, *, character_ids, rule_ids=None, source_parts=None):
    """Return issues; optional reference checks do not certify source evidence."""
    try:
        chars = _ids(character_ids, "character_ids")
        rules = None if rule_ids is None else _ids(rule_ids, "rule_ids")
        sources = None if source_parts is None else _ids(source_parts, "source_parts")
        if not 1 <= len(chars) <= LIMITS["characters"]:
            raise SharedPoolError("pool_context", "角色数量超限", "character_ids")
        raw = _canonical(config)
        if len(raw) > LIMITS["declaration_bytes"]:
            raise SharedPoolError("pool_limit", "共享池声明字节超限")
    except SharedPoolError as exc:
        return [exc.issue]
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        return [_issue("pool_schema", "声明不是有限有界JSON", "shared_pools", reason=str(exc))]
    issues = []
    schema = SCALED_POOLS_SCHEMA if isinstance(config, dict) and type(config.get("schema_version")) is int and config["schema_version"] == 2 else SHARED_POOLS_SCHEMA
    _schema(config, schema, "shared_pools", issues)
    if issues:
        return issues
    pools, orders, gates, pool_sources = {}, {}, set(), set()
    for collection in ("pools", "operations", "consumers"):
        ids = set()
        for index, row in enumerate(config[collection]):
            path = f"shared_pools.{collection}[{index}]"
            if row["id"] in ids:
                issues.append(_issue("pool_duplicate_id", "同类对象ID重复", path+".id"))
            ids.add(row["id"])
            if sources is not None and row["source_id"] not in sources:
                issues.append(_issue("pool_source_reference", "来源部件不存在", path+".source_id"))
            if row["status"] == "pending":
                issues.append(_issue("pool_pending_evidence", "资源声明仍待确认", path+".status", severity="pending"))
            if collection == "pools":
                pools[row["id"]] = row
                identity = (row["source_id"], row["owner"], row["scope"])
                if identity in pool_sources:
                    issues.append(_issue("pool_duplicate_source", "同一提供者来源部件和范围不能创建多个独立池", path+".source_id"))
                pool_sources.add(identity)
                if row["owner"] not in chars:
                    issues.append(_issue("pool_character_reference", "提供者不在队伍中", path+".owner"))
                keys = {"team"} if row["scope"] == "team" else chars
                if set(row["initial"]) != keys:
                    issues.append(_issue("pool_initial_keys", "初态须精确覆盖池键", path+".initial", expected=sorted(keys)))
                if (pool_capacity_mode(row) == "bounded"
                        and any(v > row["capacity"] for v in row["initial"].values())):
                    issues.append(_issue("pool_initial_capacity", "初态超过容量", path+".initial"))
                continue
            pool = pools.get(row["pool_id"])
            if pool is None:
                issues.append(_issue("pool_reference", "引用不存在的池", path+".pool_id"))
                continue
            selector = row["trigger"] if collection == "operations" else row["filter"]
            for key, values in selector.items():
                if isinstance(values, list) and len(set(values)) != len(values):
                    issues.append(_issue("pool_selector_duplicate", "筛选列表重复", path+"."+key))
            if not set(selector.get("characters", [])) <= chars:
                issues.append(_issue("pool_character_reference", "筛选角色不在队伍中", path+".characters"))
            if collection == "operations":
                if row["operation"] == "spend" and row["amount"] == "all_available" and row["spend_policy"] != "up_to_available":
                    issues.append(_issue("pool_spend_policy", "all_available只接受up_to_available", path+".spend_policy"))
                key = (row["pool_id"], selector["phase"], row["order"])
                if key in orders:
                    issues.append(_issue("pool_operation_order", "同池同阶段操作order可能冲突；请给不同order", path+".order", conflicts_with=orders[key]))
                orders[key] = path
                ticks = Decimal(str(row["cooldown"]["seconds"])) * 1000000
                if ticks != ticks.to_integral_value():
                    issues.append(_issue("pool_time_precision", "冷却必须精确到微秒", path+".cooldown.seconds"))
                targets = row["targets"]
                if targets["mode"] == "all_pool_keys":
                    if "characters" in targets:
                        issues.append(_issue("pool_target_keys", "all_pool_keys不得附带characters", path+".targets"))
                elif pool["scope"] != "per_character" or not targets.get("characters") or not set(targets["characters"]) <= chars or len(set(targets["characters"])) != len(targets["characters"]):
                    issues.append(_issue("pool_target_keys", "角色目标与池范围不符", path+".targets"))
            else:
                if (selector["event"], selector["phase"]) != ("hit", "pre"):
                    issues.append(_issue("pool_consume_phase", "首片只支持hit/pre消费", path+".filter"))
                if rules is not None and row["eligibility_rule_id"] not in rules:
                    issues.append(_issue("pool_rule_reference", "消费资格规则不存在", path+".eligibility_rule_id"))
                if row["eligibility_rule_id"] in gates:
                    issues.append(_issue("pool_gate_duplicate", "同一规则只能有一个消费gate", path+".eligibility_rule_id"))
                gates.add(row["eligibility_rule_id"])
    return issues


class SharedPoolLedger:
    """Integer accounting only; run_id means one event occurrence, never a cycle."""

    def __init__(self, config, *, character_ids, rule_ids=None, source_parts=None,
                 counter_program=None, reserve_events=None, observer_program=None, dispatch_program=None):
        issues = validate_shared_pools(config, character_ids=character_ids, rule_ids=rule_ids, source_parts=source_parts)
        if issues:
            raise SharedPoolError("pool_invalid", "共享池声明不能执行", issues=issues)
        self._config = copy.deepcopy(config)
        self._characters = set(character_ids)
        self._pools = {p["id"]: p for p in self._config["pools"]}
        self._operations = {p["id"]: p for p in self._config["operations"]}
        self._consumers = {p["id"]: p for p in self._config["consumers"]}
        self._balances = {key: copy.deepcopy(row["initial"]) for key, row in self._pools.items()}
        self._cooldowns = dict.fromkeys(self._operations, 0)
        self._time = 0
        self._event_identity, self._event_stage = {}, {}
        self._operation_seen, self._consumption, self._phase_order = set(), {}, {}
        self._operation_benefits = set()
        self._receipts, self._receipt_bytes = [], 2
        self._counter_program, self._reserve_counter_events = counter_program, reserve_events
        self._counter_state = None
        self._counter_receipts, self._counter_receipt_bytes, self._counter_work = [], 2, 0
        self._observer_program, self._observer_state = observer_program, None
        self._observer_receipts, self._observer_receipt_bytes, self._observer_work = [], 2, 0
        self._dispatch_authority = None
        if counter_program is not None:
            # Local import avoids a second exception hierarchy or ledger.
            from genshin_engine.resource_counters import CounterProgram
            if not isinstance(counter_program, CounterProgram) or counter_program._pool_config != self._config:
                raise SharedPoolError("counter_reference", "累计程序必须绑定此资源账的完整定义")
            if reserve_events is not None and not callable(reserve_events):
                raise SharedPoolError("counter_budget", "累计公共预算入口必须为可调用对象")
            self._counter_state = counter_program.initial_state()
        if observer_program is not None:
            from genshin_engine.resource_thresholds import ThresholdObserverProgram
            if not isinstance(observer_program, ThresholdObserverProgram) or observer_program._pool_config != self._config:
                raise SharedPoolError("threshold_reference", "观察程序必须绑定此资源账的完整定义")
            if observer_program._counter_program is not None and (counter_program is None
                    or observer_program._counter_program._fingerprint != counter_program._fingerprint):
                raise SharedPoolError("threshold_counter_reference", "累计观察须绑定此账实际使用的完整累计程序")
            if reserve_events is not None and not callable(reserve_events):
                raise SharedPoolError("threshold_budget", "观察公共预算入口必须为可调用对象")
            self._observer_state = observer_program.initial_state()
        if dispatch_program is not None:
            from genshin_engine.resource_threshold_authority import (
                ThresholdDispatchAuthority,
            )
            from genshin_engine.resource_threshold_dispatch import (
                ThresholdTriggerProgram,
            )
            if (observer_program is None or not isinstance(dispatch_program, ThresholdTriggerProgram)
                    or dispatch_program._observer_config != observer_program._config
                    or dispatch_program._pool_config != self._config
                    or dispatch_program._counter_fingerprint != (observer_program._counter_program._fingerprint
                        if observer_program._counter_program is not None else None)):
                raise SharedPoolError("threshold_dispatch_binding", "派发程序必须绑定此账已启用的完整阈值观察定义")
            self._dispatch_authority = ThresholdDispatchAuthority(dispatch_program)

    def _dispatch_usage(self):
        authority = self._dispatch_authority
        return (2*authority._issued_sequence, authority._bytes, authority._work) if authority is not None else (0, 0, 0)

    def _prepare_counter(self, plan, *, resource_count=0, resource_bytes=0):
        state = self._counter_program._state(plan["state"])
        rows = copy.deepcopy(plan["counter_receipts"])
        extra = sum(len(_canonical(r))+1 for r in rows) - int(not self._counter_receipts and bool(rows))
        observer_bytes = self._observer_receipt_bytes if self._observer_program is not None else 0
        dispatch_count, dispatch_bytes, dispatch_work = self._dispatch_usage()
        if (len(self._receipts)+resource_count+len(self._counter_receipts)+len(rows)+len(self._observer_receipts)+dispatch_count > LIMITS["receipts"]
                or self._receipt_bytes+resource_bytes+self._counter_receipt_bytes+extra+observer_bytes+dispatch_bytes > LIMITS["receipt_bytes"]):
            raise SharedPoolError("counter_limit", "累计与资源回执共用的数量或字节预算超限", severity="pending")
        work = 1+plan["extra_event_count"]
        if self._counter_work+work+self._observer_work+dispatch_work > LIMITS["receipts"]:
            raise SharedPoolError("counter_limit", "累计执行工作超限", severity="pending")
        return state, rows, extra, work

    def _reserve_counter(self, work):
        if self._reserve_counter_events is not None:
            self._reserve_counter_events(work)

    def _publish_counter(self, prepared):
        self._counter_state, rows, extra, work = prepared
        self._counter_receipts.extend(rows)
        self._counter_receipt_bytes += extra
        self._counter_work += work

    def apply_counter_control(self, verified_step):
        """Apply one bridge-verified control; this pure ledger does not sign it.

        Control execution itself costs one work even when it emits no counter
        row. All copies, validation and budgets precede the final reservation.
        """
        if self._counter_program is None:
            raise SharedPoolError("counter_reference", "当前账未启用累计控制")
        self._require_dispatch_claimed()
        plan = self._counter_program.plan_control(self._counter_state, verified_step)
        prepared = self._prepare_counter(plan)
        result = copy.deepcopy(prepared[1])
        self._reserve_counter(prepared[3])
        self._publish_counter(prepared)
        return result

    def counter_snapshot(self):
        return copy.deepcopy(self._counter_state)

    def counter_receipts(self):
        return copy.deepcopy(self._counter_receipts)

    def _prepare_observer(self, receipts, *, resource_bytes, counter):
        from genshin_engine.resource_threshold_schema import THRESHOLD_LIMITS
        for row in receipts:
            if row["before"] != self._balances[row["pool_id"]][row["key"]]:
                raise SharedPoolError("threshold_receipt_origin", "观察前余额与真实资源账不符")
        candidate = {"receipt_start": len(self._receipts)+1, "receipt_end": len(self._receipts)+len(receipts), "receipts": receipts}
        if self._observer_program._counter_program is not None:
            if counter is None:
                raise SharedPoolError("threshold_counter_reference", "累计观察缺少本笔真实已准备累计候选")
            candidate["counter_span"] = {"before": self._counter_state, "after": counter[0], "receipts": counter[1]}
        plan = self._observer_program.plan_commit(self._observer_state, candidate)
        state = self._observer_program._state(plan["state"])
        rows = copy.deepcopy(plan["observer_receipts"])
        for row in rows:
            row["execution"] = "ledger_committed_audit"
        extra = sum(len(_canonical(r))+1 for r in rows)-int(not self._observer_receipts and bool(rows))
        counter_rows = len(counter[1]) if counter is not None else 0
        counter_bytes = counter[2] if counter is not None else 0
        counter_work = counter[3] if counter is not None else 0
        work = plan["extra_event_count"] + (0 if counter is not None else 1)
        dispatch_count, dispatch_bytes, dispatch_work = self._dispatch_usage()
        if (len(self._observer_receipts)+len(rows) > THRESHOLD_LIMITS["receipts"]
                or self._observer_receipt_bytes+extra > THRESHOLD_LIMITS["trace_bytes"]
                or len(self._receipts)+len(receipts)+len(self._counter_receipts)+counter_rows+len(self._observer_receipts)+len(rows)+dispatch_count > LIMITS["receipts"]
                or self._receipt_bytes+resource_bytes+(self._counter_receipt_bytes if self._counter_program is not None else 0)
                    +counter_bytes+self._observer_receipt_bytes+extra+dispatch_bytes > LIMITS["receipt_bytes"]
                or self._counter_work+counter_work+self._observer_work+work+dispatch_work > LIMITS["receipts"]):
            raise SharedPoolError("threshold_limit", "资源/累计/观察联合数量、字节或工作超限", severity="pending")
        return state, rows, extra, work

    def _publish_observer(self, prepared):
        self._observer_state, rows, extra, work = prepared
        self._observer_receipts.extend(rows)
        self._observer_receipt_bytes += extra
        self._observer_work += work

    def observer_snapshot(self):
        """Committed audit cursor only, never an event-dispatch permit."""
        result = copy.deepcopy(self._observer_state)
        if result is not None:
            result["execution"] = "ledger_committed_audit"
        return result

    def observer_receipts(self):
        return copy.deepcopy(self._observer_receipts)

    def _prepare_threshold_dispatch(self, observer, counter, *, resource_count, resource_bytes):
        candidate = self._dispatch_authority.prepare(observer[1])
        if candidate is None:
            return None
        used_count, used_bytes, used_work = self._dispatch_usage()
        counter_count = len(counter[1]) if counter is not None else 0
        counter_bytes = counter[2] if counter is not None else 0
        counter_work = counter[3] if counter is not None else 0
        if (len(self._receipts)+resource_count+len(self._counter_receipts)+counter_count
                +len(self._observer_receipts)+len(observer[1])+used_count+candidate["rows"] > LIMITS["receipts"]
                or self._receipt_bytes+resource_bytes+(self._counter_receipt_bytes if self._counter_program is not None else 0)
                    +counter_bytes+self._observer_receipt_bytes+observer[2]+used_bytes+candidate["bytes"] > LIMITS["receipt_bytes"]
                or self._counter_work+counter_work+self._observer_work+observer[3]+used_work+candidate["work"] > LIMITS["receipts"]):
            raise SharedPoolError("threshold_dispatch_limit", "签发与资源/累计/观察联合预算超限", severity="pending")
        return candidate

    def threshold_dispatch_report(self):
        return self._dispatch_authority.report() if self._dispatch_authority is not None else None

    def _require_dispatch_claimed(self):
        if self._dispatch_authority is not None and self._dispatch_authority._known:
            raise SharedPoolError("threshold_dispatch_unclaimed", "真实阈值派发尚未领取，不可跳到下一资源或控制事务", severity="pending")

    def _context(self, event_id, run_id, t_us, stage):
        self._require_dispatch_claimed()
        for name, value in (("event_id", event_id), ("run_id", run_id)):
            if type(value) is not str or not value.strip() or len(value) > 512:
                raise SharedPoolError("pool_event_identity", "实际事件身份无效", name)
        if type(t_us) is not int or not 0 <= t_us <= LIMITS["time_us"]:
            raise SharedPoolError("pool_event_time", "事件时间须为有界整数微秒", "t_us")
        if t_us < self._time:
            raise SharedPoolError("pool_event_order", "事件不能回溯时间", "t_us")
        if run_id in self._event_identity and self._event_identity[run_id] != (event_id, t_us):
            raise SharedPoolError("pool_event_identity", "run_id已用于另一次实际事件身份", "run_id")
        if stage < self._event_stage.get(run_id, -1):
            raise SharedPoolError("pool_event_phase_order", "同一次实际事件不能从后置返回前置/消费", "phase")

    def _commit_receipts(self, receipts, event_id, run_id, t_us, stage):
        extra = sum(len(_canonical(r)) + 1 for r in receipts) - int(not self._receipts and bool(receipts))
        if len(self._receipts) + len(receipts) > LIMITS["receipts"] or self._receipt_bytes + extra > LIMITS["receipt_bytes"]:
            raise SharedPoolError("pool_receipt_limit", "共享池回执预算超限", severity="pending")
        ready = copy.deepcopy(receipts)
        counter, observer, dispatch = None, None, None
        if self._counter_program is not None:
            wrapped = [{"ledger_sequence": len(self._receipts)+i+1,
                        "control_sequence": self._counter_state["last_control_sequence"], "receipt": row}
                       for i, row in enumerate(ready)]
            plan = self._counter_program.plan_spend(self._counter_state, wrapped)
            counter = self._prepare_counter(plan, resource_count=len(ready), resource_bytes=extra)
        if self._observer_program is not None:
            observer = self._prepare_observer(ready, resource_bytes=extra, counter=counter)
        if self._dispatch_authority is not None:
            dispatch = self._prepare_threshold_dispatch(observer, counter, resource_count=len(ready), resource_bytes=extra)
        if counter is not None or observer is not None:
            self._reserve_counter((counter[3] if counter is not None else 0)+(observer[3] if observer is not None else 0)
                                  +(dispatch["work"] if dispatch is not None else 0))
        # No validation, serialization, copying or callbacks below this point.
        self._time = t_us
        self._event_identity[run_id] = (event_id, t_us)
        self._event_stage[run_id] = stage
        self._receipts.extend(ready)
        self._receipt_bytes += extra
        if counter is not None: self._publish_counter(counter)
        if observer is not None: self._publish_observer(observer)
        if self._dispatch_authority is not None: self._dispatch_authority.publish(dispatch)

    def _receipt(self, kind, row, event_id, run_id, t_us, phase, key, before, requested, accepted, after, reason):
        identity = _canonical([kind, row["id"], run_id, key])
        pool = self._pools[row["pool_id"]]
        result = {"id": "pool:" + hashlib.sha256(identity).hexdigest(), "type": kind,
                "pool_id": row["pool_id"], "source_id": row["source_id"],
                "operation_id" if kind == "operation" else "consumer_id": row["id"],
                "event_id": event_id, "run_id": run_id, "t_us": t_us, "phase": phase,
                "key": key, "before": before, "requested": requested, "accepted": accepted,
                "after": after, "delta": after-before, "reason": reason,
                "qualification_scope": "caller_supplied_not_evaluated"}
        if pool_capacity_mode(pool) == "unbounded":
            result["capacity_mode"] = "unbounded"
        if self._config["schema_version"] == 2:
            result.update(unit_scale=self._pools[row["pool_id"]]["unit_scale"], quantity_unit="atoms")
        return result

    def apply_operation(self, operation_id, *, event_id, run_id, t_us, phase, event_kind, keys, eligible):
        """Apply one caller-qualified operation; duplicate delivery is an error."""
        row = self._operations.get(operation_id) if type(operation_id) is str else None
        if row is None:
            raise SharedPoolError("pool_operation_reference", "资源操作不存在", "operation_id")
        self._context(event_id, run_id, t_us, 0 if phase == "pre" else 2)
        if type(eligible) is not bool:
            raise SharedPoolError("pool_eligibility", "eligible必须为布尔", "eligible")
        if (event_kind, phase) != (row["trigger"]["event"], row["trigger"]["phase"]):
            raise SharedPoolError("pool_operation_phase", "事件类别/阶段与声明不符", "phase")
        if (operation_id, run_id) in self._operation_seen:
            raise SharedPoolError("pool_duplicate_operation", "同一次实际事件重复投递资源操作", "run_id")
        actual = _ids(keys, "keys")
        expected = set(self._balances[row["pool_id"]]) if row["targets"]["mode"] == "all_pool_keys" else set(row["targets"]["characters"])
        if actual != expected:
            raise SharedPoolError("pool_operation_keys", "操作目标键与声明不一致", "keys", expected=sorted(expected))
        # source_id is the graph's normalized source component, not the free
        # source description or operation/handler alias. Different real source
        # parts retain distinct IDs; one occurrence cannot pay a part twice.
        benefit_keys = {(run_id, phase, self._pools[row['pool_id']]['owner'], row['source_id'],
                         row['pool_id'], row['operation'], key) for key in actual}
        collisions = self._operation_benefits & benefit_keys
        if collisions:
            prior_operations = set()
            remaining = set(collisions)
            for receipt in reversed(self._receipts):
                if receipt['type'] != 'operation':
                    continue
                identity = (receipt['run_id'], receipt['phase'],
                    self._pools[receipt['pool_id']]['owner'], receipt['source_id'],
                    receipt['pool_id'], receipt['operation'], receipt['key'])
                if identity in remaining:
                    prior_operations.add(receipt['operation_id'])
                    remaining.remove(identity)
                    if not remaining:
                        break
            raise SharedPoolError('pool_duplicate_benefit', '同一真实发生的规范来源收益已有执行者，不能借操作别名重复入账', 'source_id',
                pool_id=row['pool_id'], source_id=row['source_id'], owner=self._pools[row['pool_id']]['owner'],
                incoming_operation_id=operation_id, existing_operation_ids=sorted(prior_operations),
                event_id=event_id, run_id=run_id, phase=phase, operation=row['operation'], keys=sorted(actual))
        order_key = (run_id, row["pool_id"], phase)
        if row["order"] <= self._phase_order.get(order_key, -1):
            raise SharedPoolError("pool_operation_order", "同池同阶段操作未按显式order递增提交", "order")
        cooldown = self._cooldowns[operation_id]
        reason = "cooldown" if t_us < cooldown else "ineligible" if not eligible else "set" if row["operation"] == "set" else "granted"
        effective = reason in ("set", "granted")
        amount, pool = row["amount"], self._pools[row["pool_id"]]
        receipts = []
        for key in sorted(actual):
            before = self._balances[row["pool_id"]][key]
            if row["operation"] == "spend":
                requested = before if amount == "all_available" else amount
                accepted = (min(before, requested) if row["spend_policy"] == "up_to_available"
                            else requested if before >= requested else 0) if effective else 0
                spend_reason = (reason if not effective else "spent" if accepted else
                                "insufficient" if row["spend_policy"] == "all_or_nothing" else "empty")
                receipt = self._receipt("operation", row, event_id, run_id, t_us, phase, key,
                                        before, requested, accepted, before-accepted, spend_reason)
                receipt.update(unfulfilled=requested-accepted, qualified=eligible,
                               succeeded=accepted > 0, operation="spend", spend_policy=row["spend_policy"])
            else:
                target = amount if row["operation"] == "set" else before+amount
                after = apply_pool_capacity(pool, target) if effective else before
                accepted = (after if row["operation"] == "set" else after-before) if effective else 0
                receipt = self._receipt("operation", row, event_id, run_id, t_us, phase, key, before, amount, accepted, after, reason)
                receipt.update(wasted=max(0, amount-accepted) if effective else 0, qualified=eligible,
                               succeeded=effective, operation=row["operation"])
            receipts.append(receipt)
        successful = any(r["succeeded"] for r in receipts) if row["operation"] == "spend" else eligible
        if reason != "cooldown" and (successful or row["cooldown"]["anchor"] == "attempt"):
            cooldown = t_us + int(Decimal(str(row["cooldown"]["seconds"])) * 1000000)
        if cooldown > LIMITS["time_us"]:
            raise SharedPoolError("pool_time_limit", "冷却结束超过时间上限", "cooldown", severity="pending")
        for receipt in receipts:
            receipt["cooldown_until_us"] = cooldown
        result = copy.deepcopy(receipts)
        self._commit_receipts(receipts, event_id, run_id, t_us, 0 if phase == "pre" else 2)
        for receipt in receipts:
            self._balances[row["pool_id"]][receipt["key"]] = receipt["after"]
        self._cooldowns[operation_id] = cooldown
        self._phase_order[order_key] = row["order"]
        self._operation_seen.add((operation_id, run_id))
        self._operation_benefits.update(benefit_keys)
        return result

    def try_consume(self, consumer_id, *, event_id, run_id, t_us, actor, eligible):
        """Read/reuse one whole-cost receipt per consumer and actual event run_id."""
        row = self._consumers.get(consumer_id) if type(consumer_id) is str else None
        if row is None:
            raise SharedPoolError("pool_consumer_reference", "消费者不存在", "consumer_id")
        if type(run_id) is not str:
            raise SharedPoolError("pool_event_identity", "实际事件身份无效", "run_id")
        # Duplicate reads may be made after later events; no second mutation.
        signature = (event_id, t_us, actor, eligible)
        old = self._consumption.get((consumer_id, run_id))
        if old is not None:
            if old[0] != signature or type(eligible) is not bool or type(t_us) is not int:
                raise SharedPoolError("pool_event_identity", "重复消费读取的实际事件身份或资格改变", "run_id")
            return copy.deepcopy(old[1])
        self._context(event_id, run_id, t_us, 1)
        if type(actor) is not str or actor not in self._characters:
            raise SharedPoolError("pool_actor_reference", "消费角色不存在", "actor")
        if type(eligible) is not bool:
            raise SharedPoolError("pool_eligibility", "eligible必须为布尔", "eligible")
        pool = self._pools[row["pool_id"]]
        key = "team" if pool["scope"] == "team" else actor
        before, amount = self._balances[row["pool_id"]][key], row["amount"]
        reason = "ineligible" if not eligible else "insufficient" if before < amount else "consumed"
        accepted = amount if reason == "consumed" else 0
        receipt = self._receipt("consume", row, event_id, run_id, t_us, "pre", key, before, amount, accepted, before-accepted, reason)
        receipt.update(actor=actor, qualified=eligible, gate_open=bool(accepted))
        saved, result = copy.deepcopy(receipt), copy.deepcopy(receipt)
        self._commit_receipts([receipt], event_id, run_id, t_us, 1)
        self._balances[row["pool_id"]][key] -= accepted
        self._consumption[(consumer_id, run_id)] = (signature, saved)
        return result

    def snapshot(self):
        result = {"schema_version": self._config["schema_version"], "t_us": self._time, "balances": copy.deepcopy(self._balances),
                  "cooldown_until_us": dict(self._cooldowns), "upper_layer_qualification": "not_evaluated"}
        if any(pool_capacity_mode(row) == "unbounded" for row in self._pools.values()):
            result["capacity_modes"] = {key: pool_capacity_mode(row) for key, row in self._pools.items()}
        if self._config["schema_version"] == 2:
            result.update(quantity_unit="atoms", unit_scales={key: row["unit_scale"] for key, row in self._pools.items()})
        return result

    def boundary(self):
        """Accounting state only; not a complete scheduler checkpoint/restore."""
        return json.loads(_canonical(self.snapshot()))

    def receipt_cursor(self):
        """Current committed resource-receipt count, without copying balances."""
        return len(self._receipts)

    def receipts(self):
        return copy.deepcopy(self._receipts)
