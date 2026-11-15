"""Adapt shared integer accounting to the existing mechanics event runner.

No database authority lives here. Declarations, actual event selectors and active
benefit instances are checked, while source approval remains an App obligation.
"""
from __future__ import annotations

import copy

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.reaction_event_capabilities import (
    reaction_event_available,
    reaction_listener_phase_supported,
)
from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.resource_counters import CounterProgram
from genshin_engine.resource_threshold_dispatch import ThresholdTriggerProgram
from genshin_engine.resource_threshold_runtime import ThresholdRuntime
from genshin_engine.resource_thresholds import ThresholdObserverProgram
from genshin_engine.shared_pools import (
    SharedPoolError,
    SharedPoolLedger,
    validate_shared_pools,
)
from genshin_engine.state_runtime import StateRuntimeError

ALLOWED_ZONES = {"base_add", "dmg_bonus", "crit_rate", "crit_dmg", "base_mult"}


def issue(code, message, path, *, severity="error", **details):
    return {"code": code, "message": message, "path": path, "severity": severity, "details": details}


def validate_bindings(model, *, characters=None):
    config = model.get("shared_pools")
    counter_program = None
    rules = {r["id"]: r for r in model["rules"]}
    if "resource_counters" in model:
        if config is None or "state_runtime" not in model:
            return [issue("counter_reference", "累计需要同时声明计量v2共享池与生命周期", "resource_counters")]
        if characters is not None:
            try:
                counter_program = CounterProgram(model["resource_counters"], config, model["state_runtime"], character_ids=characters)
            except (SharedPoolError, AttributeLedgerError, StateRuntimeError) as exc:
                return [copy.deepcopy(exc.issue)]
    if "resource_threshold_observers" in model or "resource_threshold_triggers" in model:
        if config is None or "resource_threshold_observers" not in model:
            return [issue("threshold_reference", "资源阈值需要观察定义及显式v2共享池", "resource_threshold_observers")]
        if characters is not None:
            try:
                ThresholdObserverProgram(model["resource_threshold_observers"], config, character_ids=characters, counter_program=counter_program)
                if "resource_threshold_triggers" in model:
                    ThresholdTriggerProgram(model, character_ids=characters)
            except SharedPoolError as exc:
                return [copy.deepcopy(exc.issue)]
    if "resource_threshold_triggers" not in model and any(r["trigger"].get("kind") == "resource_threshold" for r in rules.values()):
        return [issue("threshold_reference", "typed阈值规则缺少唯一联动定义", "resource_threshold_triggers")]
    if config is None:
        return [issue("pool_gate_reference", "resource_gate需要shared_pools声明", r["id"]) for r in rules.values() if r.get("resource_gate")]
    problems = []
    for operation in config["operations"]:
        selector = operation["trigger"]
        path = "shared_pools.operations." + operation["id"] + ".trigger"
        if not reaction_event_available(model, selector["event"]):
            problems.append(issue("pool_reaction_event_unavailable",
                "资源反应监听缺少能产生该事件的元素模型；手填反应倍率不能代替实际反应。", path))
        if not reaction_listener_phase_supported(selector):
            problems.append(issue("pool_reaction_phase_unsupported",
                "资源反应及反应伤害监听只支持post相位；pre不会被实际队列调度。", path))
    if characters is not None:
        problems.extend(validate_shared_pools(config, character_ids=characters, rule_ids=list(rules)))
        if problems:
            return problems
    pools = {p["id"]: p for p in config["pools"]}
    consumers = {c["id"]: c for c in config["consumers"]}
    pool_users = set()
    for consumer in config["consumers"]:
        path = "shared_pools.consumers." + consumer["id"]
        rule = rules.get(consumer["eligibility_rule_id"])
        pool = pools.get(consumer["pool_id"])
        if pool is None or rule is None:
            problems.append(issue("pool_gate_reference", "消费者引用不存在的池或规则", path))
            continue
        if consumer["pool_id"] in pool_users:
            problems.append(issue("pool_consumer_order", "首片同一池只接受一个消费者，避免隐含抢额度顺序", path))
        pool_users.add(consumer["pool_id"])
        if rule.get("resource_gate") != {"consumer_id": consumer["id"]}:
            problems.append(issue("pool_gate_reference", "消费者与规则gate必须一一对应", path))
        if rule["source_id"] != consumer["source_id"] or rule["owner"] != pool["owner"]:
            problems.append(issue("pool_gate_owner", "消费者来源须等于权威规则来源，规则与池提供者须相同", path))
    for rule in rules.values():
        gate = rule.get("resource_gate")
        if gate is None:
            continue
        path = "rules." + rule["id"] + ".resource_gate"
        consumer = consumers.get(gate["consumer_id"])
        if consumer is None or consumer["eligibility_rule_id"] != rule["id"]:
            problems.append(issue("pool_gate_reference", "gate未关联唯一权威消费者", path))
        if rule.get("quota") is not None or rule.get("resources"):
            problems.append(issue("pool_gate_scope", "gate不得与旧quota或产能资源一起交付", path))
        for effect in rule["effects"]:
            if (BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("zone") not in ALLOWED_ZONES
                    or effect["domain"] == "enemy"
                    or effect.get("filter", {}).get("event", "hit") != "hit"):
                problems.append(issue("pool_gate_scope", "gate首片只接受普通命中直伤收益，不写普通属性、敌方属性或能量", path, effect_id=effect["id"]))
    return problems


def validate_references(config, *, all_ids, action_ids, character_ids, damage_types,
                        action_types, conditions, reaction_ids, reaction_damage_ids, capture_ids,
                        state_operation_routes=None):
    problems = []
    for collection, field in (("operations", "trigger"), ("consumers", "filter")):
        for row in config[collection]:
            selector, path = row[field], f"shared_pools.{collection}.{row['id']}.{field}"
            event_ids = reaction_ids if selector["event"] == "elemental_reaction" else reaction_damage_ids if selector["event"] == "reaction_damage" else all_ids
            if collection == "operations" and row["id"] in (state_operation_routes or {}):
                event_ids = event_ids | {state_operation_routes[row["id"]]}
            for key, allowed in (("event_ids", event_ids), ("action_ids", action_ids), ("characters", character_ids),
                                 ("damage_type_ids", damage_types), ("action_type_ids", action_types)):
                values = selector.get(key, [])
                if (key in selector and not values) or len(values) != len(set(values)) or not set(values) <= allowed:
                    problems.append(issue("pool_selector_reference", "筛选引用不存在、重复或为空", path+"."+key))
            if set(selector.get("event_ids", [])) & capture_ids:
                problems.append(issue("pool_selector_reference", "捕获不能用作资源操作监听", path+".event_ids"))
            if "condition" in selector and selector["condition"] not in conditions:
                problems.append(issue("pool_condition_missing", "资源筛选条件没有显式事件，不能推测", path+".condition", severity="pending"))
    return problems


class SharedPoolRuntime:
    def __init__(self, model, characters, *, reserve_events=None):
        self.config = model["shared_pools"]
        self.pools = {p["id"]: p for p in self.config["pools"]}
        self.counter_program = (CounterProgram(model["resource_counters"], self.config, model["state_runtime"], character_ids=characters)
                                if "resource_counters" in model else None)
        self.observer_program = (ThresholdObserverProgram(model["resource_threshold_observers"], self.config, character_ids=characters, counter_program=self.counter_program)
                                 if "resource_threshold_observers" in model else None)
        self.threshold_program = ThresholdTriggerProgram(model, character_ids=characters) if "resource_threshold_triggers" in model else None
        self.ledger = SharedPoolLedger(self.config, character_ids=characters,
            rule_ids=[r["id"] for r in model["rules"]], counter_program=self.counter_program, reserve_events=reserve_events,
            observer_program=self.observer_program, dispatch_program=self.threshold_program)
        self.trace = []
        self.threshold_runtime = ThresholdRuntime(self.threshold_program, self) if self.threshold_program is not None else None

    def phase(self, ctx, event, phase, parent, *, allowed_operation_ids=None):
        if self.threshold_runtime is not None:
            self.threshold_runtime.ensure_ready()
        # A switch listener is a logical sub-event after action_start callbacks.
        run_id = parent["run_id"] + (":switch" if event["event"] == "switch" else "")
        # Legacy v1 supplies the hit as reaction parent, unlike v2's dedicated
        # reaction occurrence. Never alias that derived event to the hit's UID.
        if event["event"] == "elemental_reaction" and event["id"] != parent["id"]:
            run_id = f"{event['origin']}:{event['id']}"
        audit = getattr(ctx, "resource_reactions", None)
        for op in sorted(self.config["operations"], key=lambda item: (item["order"], item["pool_id"], item["id"])):
            if allowed_operation_ids is not None and op["id"] not in allowed_operation_ids:
                continue
            if op["trigger"]["phase"] != phase:
                continue
            if audit and audit.excludes_operation(event, phase, parent, op):
                continue
            pool = self.pools[op["pool_id"]]
            sink = audit.sink(event, phase, parent, op, pool, self.ledger) if audit else None
            selected = ctx.filter(op["trigger"], event, "shared_pools.operations."+op["id"], owner=pool["owner"],
                                  **({"decision_sink": sink} if sink is not None else {}))
            if not selected:
                continue
            if sink is not None and not audit.admit_operation(op):
                continue
            try:
                if self.counter_program is None and self.observer_program is None: ctx._count_event("shared_pool_operation")
                keys = (audit.keys(op["id"]) if sink is not None else
                    sorted(self.ledger.snapshot()["balances"][pool["id"]]) if op["targets"]["mode"] == "all_pool_keys" else op["targets"]["characters"])
                receipts = self.ledger.apply_operation(op["id"], event_id=event["id"], run_id=run_id,
                    t_us=ctx.time, phase=phase, event_kind=event["event"], keys=keys, eligible=True)
            except SharedPoolError as exc:
                if sink is not None:
                    audit.failed(op["id"], exc)
                raise
            try:
                if sink is not None:
                    audit.committed(op["id"])
            finally:
                self.trace.extend({**r, "cycle": event["origin"], "qualification_scope": "engine_declared_event", "t": ctx.time/1000000} for r in receipts)
            if self.threshold_runtime is not None:
                self.threshold_runtime.after_operation(ctx, event, phase, parent, op["id"], run_id)
        if audit:
            audit.finish_scan(event, phase, parent)

    def consume(self, ctx, event, hit):
        if self.threshold_runtime is not None:
            self.threshold_runtime.ensure_ready()
        granted = {}
        if event["event"] != "hit" or hit.get("damage_system") != "classic":
            return granted
        for consumer in sorted(self.config["consumers"], key=lambda row: (row["pool_id"], row["id"])):
            rule = ctx.rules[consumer["eligibility_rule_id"]]
            cid, relation = hit["character"], consumer["actor_relation"]
            relation_ok = (relation == "party_member" or relation == "owner" and cid == rule["owner"]
                           or relation == "frontline" and cid == ctx.frontline
                           or relation == "other_frontline" and cid == ctx.frontline and cid != rule["owner"])
            instances = [inst["instance_id"] for inst in ctx.state[rule["id"]]["instances"]
                         if cid in inst["beneficiaries"] and (rule["beneficiaries"]["scope"] != "current_frontline" or cid == ctx.frontline)]
            selected = ctx.filter(consumer["filter"], event, rule["id"])
            eligible = bool(relation_ok and instances and selected)
            if self.counter_program is None and self.observer_program is None: ctx._count_event("shared_pool_consume")
            receipt = self.ledger.try_consume(consumer["id"], event_id=event["id"], run_id=event["run_id"],
                                              t_us=ctx.time, actor=cid, eligible=eligible)
            receipt.update(cycle=event["origin"], t=ctx.time/1000000, qualification_scope="engine_declared_event",
                           eligible_instance_ids=instances if eligible else [],
                           eligibility_reason="eligible" if eligible else "actor_relation" if not relation_ok else "not_active_recipient" if not instances else "selector")
            self.trace.append(receipt)
            if receipt["gate_open"]:
                granted[rule["id"]] = set(instances)
        return granted

    def boundary(self, time):
        state = self.ledger.snapshot()
        result = {"balances": state["balances"],
                  "cooldown_remaining_us": {k: max(0, v-time) for k, v in state["cooldown_until_us"].items()}}
        if self.config["schema_version"] == 2:
            result.update(quantity_unit="atoms", unit_scales=state["unit_scales"])
        if self.counter_program is not None: result["resource_counters"] = self.counter_boundary(time)
        return result

    def counter_boundary(self, time):
        """Future-relevant accounting only; no counter projection/captures yet."""
        state = self.ledger.counter_snapshot()
        result = {"values": state["values"], "lifecycles": {}}
        for lid, life in state["lifecycles"].items():
            active = life["phase"] in {"created", "established", "closing"}
            result["lifecycles"][lid] = {"phase": life["phase"], "established_observed": life["established_observed"],
                "establishment_remaining_us": max(0, life["established_us"]-time) if active else None,
                "end_remaining_us": max(0, life["end_us"]-time) if active else None}
        return result

    def counter_report(self, time):
        if self.counter_program is None: return None
        return {"contract": "resource-counters-v1", "execution": "mechanics_event_queue",
            "scope": "declared_actual_spend_accounting", "state": self.counter_boundary(time),
            "applied_state": self.ledger.counter_snapshot(), "trace": self.ledger.counter_receipts(),
            "quantity_unit": "atoms", "unit_scales": {p["id"]: p["unit_scale"] for p in self.config["pools"]},
            "counter_units": {c["id"]: self.pools[c["pool_id"]]["unit_scale"] for c in self.counter_program._config["counters"]},
            "source_approval": "not_evaluated_by_engine", "damage_projection": "unsupported"}

    def observer_report(self):
        if self.observer_program is None:
            return None
        version = self.observer_program._config["schema_version"]
        return {"contract": f"resource-threshold-observers-v{version}", "execution": "mechanics_event_queue",
            "scope": "fixed_pool_key_committed_audit" if version == 1 else "fixed_pool_or_counter_key_committed_audit", "state": self.ledger.observer_snapshot(),
            "trace": self.ledger.observer_receipts(), "authority": "audit_no_dispatch_authority",
            "source_approval": "not_evaluated_by_engine"}

    def threshold_report(self):
        return self.threshold_runtime.report() if self.threshold_runtime is not None else None

    def report(self, time):
        state = self.boundary(time)
        modes = self.ledger.snapshot().get("capacity_modes")
        if modes is not None:
            state["capacity_modes"] = modes
        return {"schema_version": self.config["schema_version"],
                "scope": "declared_shared_atom_pools" if self.config["schema_version"] == 2 else "declared_shared_integer_pools",
                "state": state, "trace": copy.deepcopy(self.trace),
                "database_source_approval": "not_evaluated_by_engine"}
