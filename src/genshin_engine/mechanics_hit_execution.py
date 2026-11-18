"""Hit execution and sampling over the single Mechanics execution port.

The self argument is the original runtime implementing the typed port.
Subruntimes and callbacks therefore receive the same object and shared state;
this module neither copies that runtime nor creates another scheduler.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math

from genshin_engine import resource_counter_view_runtime as counter_view_runtime
from genshin_engine.attribute_graph import linear_value
from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_stats import STATS as GRAPH_STATS
from genshin_engine.beneficiary_selection import effective_recipient
from genshin_engine.constants import ENEMY_ZONES
from genshin_engine.core import _consumes_effect
from genshin_engine.damage_report_window import CONTRACT as DAMAGE_REPORT_CONTRACT
from genshin_engine.mechanics_execution_port import MechanicsExecutionPort
from genshin_engine.mechanics_snapshot_execution import capture_live_attribute_frame
from genshin_engine.mechanics_validation import _freeze_reference, _selector
from genshin_engine.registry import BENEFIT_TYPES, effect_matches_hit
from genshin_engine.rotation_port import TICKS
from genshin_engine.snapshot_capture import SnapshotCapturePending
from genshin_engine.target_condition_evidence import compact_bytes, compact_condition
from genshin_engine.target_state import (
    GENERAL_TARGET_PROFILE,
    TargetConditionLimit,
    TargetConditionTraceLimit,
    TargetStatePending,
    evaluate_target_predicate,
)


def freeze_runtime_panel(self: MechanicsExecutionPort, snapshot):
    # Opt-in EM lives in the immutable ledger token. Eagerly calculating
    # every actor here could fail an unrelated or never-consumed closure.
    values = {cid: ordinary_runtime_panel(self, cid, snapshot, defer_em=bool(self.attribute_runtime),
                                  defer_atk=bool(self.attribute_runtime and self.attribute_runtime.multistat),
                                  defer_stats=GRAPH_STATS if self.attribute_runtime and self.attribute_runtime.five_stats else ())
              for cid in self.data["chars"]}
    return {"time": snapshot["time"], "stats": {cid: value[0] for cid, value in values.items()},
            **({'health':copy.deepcopy(snapshot['health'])} if 'health' in snapshot else {}),
            "dependencies": {cid: value[1] for cid, value in values.items()},
            **({"attribute_frame": snapshot["attribute_frame"]} if self.attribute_runtime else {})}


def ordinary_runtime_panel(self: MechanicsExecutionPort, cid, snapshot, *, defer_em=False, defer_atk=False, defer_stats=()):
    """Base panel plus constant ordinary bonuses; no implicit conversion iteration."""
    char = next(c for c in self.template["characters"] if c["id"] == cid)
    stats, deps = copy.deepcopy(char["stats"]), []
    stats["energy_recharge"] = self.data["chars"][cid]["energy_recharge"]
    deferred = set(defer_stats) | ({"em"} if defer_em else set()) | ({"atk"} if defer_atk else set())
    if snapshot is not None and "stats" in snapshot:
        frozen = copy.deepcopy(snapshot["stats"][cid])
        for stat in deferred:
            frozen.pop(stat, None)
        return frozen, copy.deepcopy(snapshot["dependencies"][cid])
    current = snapshot or {"time": self.time, "frontline": self.frontline, "state": self.state}
    time = current["time"]
    contributions = []
    for window in self.windows:
        if window["character"] == cid and window["start"] <= time < window["end"]:
            contributions.append(("energy_recharge", "all", window["delta"], {"energy_window_id": window["id"]}))
    for buff in self.manual_buffs(time):
        if not buff["start"] <= time/TICKS < buff["end"] or buff.get("target") not in (None, cid):
            continue
        if buff.get("scope", "team") == "frontline" and current["frontline"] != cid:
            continue
        for effect in buff["effects"]:
            if effect.get("applies_to", {}).get("mode", "all") != "all":
                continue
            contributions.append((effect["zone"], effect["stat"], effect["value"], {"buff_id": buff["id"], "effect_id": effect.get("id")}))
    for rid, state in current["state"].items():
        rule = self.rules[rid]
        for inst in state["instances"]:
            if not effective_recipient(rule, inst, cid, current["frontline"]):
                continue
            for effect in rule["effects"]:
                health_value = isinstance(effect['value'], dict) and effect['value'].get('kind') == 'health'
                if (isinstance(effect["value"], dict) and not health_value) or "filter" in effect or rule.get("quota") or rule.get("resource_gate"):
                    continue
                info = BENEFIT_TYPES.get(effect["benefit_type_id"], {"zone": "energy_recharge", "stat": "all"})
                zone = info.get('zone')
                output_stat = 'em' if zone == 'em' else 'energy_recharge' if zone == 'energy_recharge' else info.get('stat')
                if zone not in ('em', 'energy_recharge', 'stat_add', 'stat_pct') or output_stat in deferred:
                    continue
                value = effect['value']
                if health_value:
                    from genshin_engine.health_mechanics import read_health_expression
                    for layer, sample in enumerate(inst['layer_snapshots']):
                        layer_instance = {**inst, 'trigger_snapshot':sample}
                        sampled = read_health_expression(self, value, rule['owner'], cid, instance=layer_instance, snapshot=current)
                        contributions.append((zone, info.get('stat'), sampled,
                            {'rule_id':rid, 'effect_id':effect['id'], 'layers':1, 'layer_index':layer}))
                    continue
                if self.attribute_runtime and info.get("zone") == "em":
                    contributions.extend(("em", info.get("stat"), value,
                                          {"rule_id": rid, "effect_id": effect["id"], "layers": 1, "layer_id": layer})
                                         for layer in inst["attribute_layers"])
                else:
                    contributions.append((info.get("zone"), info.get("stat"), value*len(inst["expires"]),
                                          {"rule_id": rid, "effect_id": effect["id"], "layers": len(inst["expires"])}))
    stat_terms = {}
    for zone, stat, value, ref in contributions:
        output_stat = "em" if zone == "em" else "energy_recharge" if zone == "energy_recharge" else stat
        if output_stat in deferred and zone in ("em", "energy_recharge", "stat_add", "stat_pct"):
            continue
        if defer_em and (zone == "em" or (zone in ("stat_add", "stat_pct") and stat == "em")):
            continue
        if defer_atk and zone in ("stat_add", "stat_pct") and stat == "atk":
            continue
        if zone == "em":
            key, amount = "em", value
        elif zone == "energy_recharge":
            key, amount = "energy_recharge", value
        elif zone == "stat_add":
            key, amount = stat, value
        elif zone == "stat_pct":
            base = stats.get("base_"+stat)
            if base is None:
                raise ValueError(f"{cid} 缺少 base_{stat}，不能求基础面板比例收益。")
            key, amount = stat, base*value
        else:
            continue
        stat_terms.setdefault(key, [stats.get(key, 0)]).append(amount)
        deps.append({**ref, "zone": zone, "stat": stat, "value": value})
    for key, values in stat_terms.items():
        stats[key] = math.fsum(values)
    for stat in deferred:
        stats.pop(stat, None)
    return stats, deps


def sample_effect_value(self: MechanicsExecutionPort, effect, rule, inst, cid, event, snapshot=None):
    expr = effect["value"]
    if isinstance(expr, (int, float)):
        return expr
    if expr.get('kind') == 'health':
        from genshin_engine.health_mechanics import read_health_expression
        value = read_health_expression(self, expr, rule['owner'], cid, instance=inst, snapshot=snapshot, event=event)
        self.samples.append({'event_id':event['id'], 'cycle':event['origin'], 't':self.time/TICKS,
                             'rule_id':rule['id'], 'effect_id':effect['id'], 'expression':copy.deepcopy(expr),
                             'value':value, 'health_revision':{c:s['revision'] for c,s in self.health_runtime.states.items()}})
        return value
    if counter_view_runtime.is_counter_view(expr):
        return self.counter_views.read(self, expr, rule, effect, inst, cid, event, snapshot)
    sampled = snapshot
    if expr["sample"] == "trigger":
        sampled = inst["trigger_snapshot"]
    elif expr["sample"] == "event":
        if expr["event_id"] in self.captures:
            self.capture_reference(event, expr["event_id"], "expression")
        sampled = self.snapshots.get((event["origin"], expr["event_id"]))
    if expr["sample"] in ("trigger", "event") and sampled is None:
        if self.attribute_runtime and self.attribute_runtime.graph and self.attribute_runtime.supports_read(expr["stat"]):
            raise AttributeLedgerError("attribute_frame_missing", "指定属性取样事件尚未实际执行，不能回退当前帧", effect["id"], status="pending")
        raise ValueError(f"{rule['id']}.{effect['id']} 的采样事件尚未执行或初始冻结值缺失。")
    actor = rule["owner"] if expr["subject"] == "owner" else cid
    attribute_receipt = None
    if self.attribute_runtime and self.attribute_runtime.supports_read(expr["stat"]):
        attribute_receipt = self.attribute_runtime.read(
            actor, expr.get("basis", "total"),
            sampled if sampled is not None else capture_live_attribute_frame(self), stat=expr["stat"],
        )
        raw, deps = attribute_receipt["value"], []
    else:
        stats, deps = self.ordinary_panel(actor, sampled)
        if expr["stat"] not in stats:
            raise ValueError(f"角色 {actor} 缺少表达式属性 {expr['stat']}。")
        raw = stats[expr["stat"]]
    if self.attribute_runtime and self.attribute_runtime.graph and self.attribute_runtime.supports_read(expr["stat"]):
        try:
            value = linear_value(raw, expr)
        except ArithmeticError as error:
            raise AttributeLedgerError("attribute_value_invalid", "属性表达式乘加溢出或不是有限值", effect["id"]) from error
    else:
        value = min(expr.get("max", float("inf")), max(expr.get("min", float("-inf")), raw*expr["factor"]+expr.get("add", 0)))
    self.samples.append({"event_id": event["id"], "cycle": event["origin"], "t": self.time/TICKS,
                         "rule_id": rule["id"], "effect_id": effect["id"], "domain": effect["domain"],
                         "expression": copy.deepcopy(expr), "character": actor, "stat": expr["stat"],
                         "sample_t": (sampled["time"] if sampled else self.time)/TICKS,
                         "base_value": self.data["chars"][actor]["energy_recharge"] if expr["stat"] == "energy_recharge" else next(c for c in self.template["characters"] if c["id"] == actor)["stats"].get(expr["stat"]),
                         "dependencies": deps, "source_value": raw, "value": value,
                         **({"attribute_receipt_id": attribute_receipt["receipt_id"]} if attribute_receipt else {})})
    return value


def project_fixed_effects(self: MechanicsExecutionPort, event, hit, reported, *, missed=False):
    effects, pending = [], None
    for source in self.fixed_sources.values():
        general = source["target_state_profile"] == GENERAL_TARGET_PROFILE
        sample = event.get("target_state_samples", {}).get(source["target_state_profile"], event.get("target_state_sample"))
        for effect in source["effects"]:
            if self.target_condition_checks >= self.target_condition_check_limit:
                raise TargetConditionLimit(self.target_condition_check_limit, self.target_condition_checks)
            self.target_condition_checks += 1
            override = next((m.get("operation", m.get("mode")) for m in self.matches
                             if m.get("fixed_source_id", m.get("effect_ref", {}).get("fixed_source_id")) == source["id"]
                             and m.get("effect_id", m.get("effect_ref", {}).get("effect_id")) == effect["id"]
                             and m.get("hit_id") == hit["id"]), None)
            automatic, applied = None, False
            if missed:
                reason = "missed"
            elif hit["character"] not in source.get("recipient_ids", [source["owner"]]):
                reason = "recipient_mismatch"
            elif (hit.get("damage_system", "classic") not in source.get("damage_systems", ["classic"])
                  or not general and event["event"] != "hit"):
                reason = "damage_scope_mismatch"
            elif not _selector(effect["filter"], event):
                reason = "filter_mismatch"
            else:
                automatic = evaluate_target_predicate(effect["target_predicate"], sample or {})
                if automatic is None:
                    reason, pending = "unknown_target_state", source["id"]
                elif override == "exclude":
                    reason = "agent_excluded"
                elif override == "include":
                    applied, reason = True, "agent_included"
                else:
                    applied, reason = automatic, "condition_true" if automatic else "condition_false"
            receipt = {"type": "target_condition", "fixed_source_id": source["id"], "effect_id": effect["id"],
                       "source_id": source["source_id"], "owner": source["owner"], "source": source["source"],
                       "source_status": source["status"], "event_id": hit["id"], "run_id": f"{event['origin']}:{hit['id']}",
                       "cycle": event["origin"], "t": self.time/TICKS,
                       "target_id": sample["target_id"] if sample else self.elemental.target if self.elemental else None,
                       "target_sample": sample, "predicate": effect["target_predicate"],
                       "automatic_value": automatic, "override": override, "applied": applied, "reason": reason,
                       "reported_damage": reported, "value": effect["value"],
                       "evidence_tier": ((sample or {}).get('evidence_tier', 'declared_elemental_execution')
                                         if general else "experimental_reference")}
            if applied:
                identity = json.dumps([source["id"], effect["id"]], ensure_ascii=False, separators=(",", ":"))
                projected_id = "fixed:" + hashlib.sha256(identity.encode("utf8")).hexdigest()
                receipt["projected_effect_id"] = projected_id
            new_sample = None
            if general:
                receipt, new_sample = compact_condition(self, event, receipt, sample,
                    evaluated=reason not in ("missed", "recipient_mismatch", "damage_scope_mismatch", "filter_mismatch"))
            size = compact_bytes(receipt)
            sample_bytes = compact_bytes({new_sample[0]: new_sample[1]}) if new_sample else 0
            attempted_bytes = self.target_condition_trace_bytes + size + sample_bytes + bool(self.target_condition_trace)
            if attempted_bytes > self.target_condition_trace_byte_limit:
                raise TargetConditionTraceLimit(self.target_condition_trace_byte_limit, self.target_condition_trace_bytes,
                                                attempted_bytes, self.target_condition_checks)
            self.target_condition_trace_bytes = attempted_bytes
            if new_sample:
                self.target_state_samples[new_sample[0]] = new_sample[1]
            if applied:
                info = BENEFIT_TYPES[effect["benefit_type_id"]]
                effects.append({"id": projected_id, "benefit_type_id": effect["benefit_type_id"],
                                "zone": info["zone"], "stat": info["stat"], "value": effect["value"],
                                "applies_to": {"mode": "hits", "hit_ids": [hit["id"]]}})
            self.target_condition_trace.append(copy.deepcopy(receipt))
            if general:
                self.trace.append({key: receipt[key] for key in
                    ("type", "fixed_source_id", "effect_id", "event_id", "run_id", "cycle", "t", "applied", "reason")}
                    | {"condition_trace_index": len(self.target_condition_trace)-1})
            else:
                self.trace.append(copy.deepcopy(receipt))
    if pending:
        raise TargetStatePending(pending)
    return effects


def execute_hit(self: MechanicsExecutionPort, event, payload):
    hit, descriptor = copy.deepcopy(payload["hit"]), payload["descriptor"]
    if event.get('execution_ref'):
        hit.setdefault('extensions', {})['execution_ref'] = copy.deepcopy(event['execution_ref'])
    self.sample_stellar_superconduct(hit, event)
    reaction_damage = event["event"] == "reaction_damage"
    if event.get("simulated_reaction", {}).get("type") in ("vaporize", "melt"):
        hit["reaction"] = {"type": "amplify", "factor": event["simulated_reaction"]["factor"]}
    if self.elemental and not reaction_damage:
        hit.setdefault("extensions", {})["elemental"] = {"mode": "simulated", "ruleset": self.elemental.model["ruleset"],
            "target_id": self.elemental.target, "application_event_id": f"{event['origin']}:{event['id']}",
            "reaction": copy.deepcopy(event.get("simulated_reaction"))}
        if 'area_id' in (descriptor.get('application') or {}):
            # Preserve the actual declared contact area. Do not infer a bucket
            # from the most recently active field or add an implied input.
            hit['extensions']['elemental']['area_id'] = descriptor['application']['area_id']
        if _freeze_reference(self.model):
            hit["extensions"]["elemental"].pop("reaction")
            hit["extensions"]["elemental"].update(reactions=copy.deepcopy(event.get("simulated_reactions", [])),
                reaction_package_id=event.get("reaction_package_id"),
                target_state_before=copy.deepcopy(event.get("target_state_before")),
                target_state_after=copy.deepcopy(event.get("target_state_after")))
    cid = hit["character"]
    graph = self.attribute_runtime and self.attribute_runtime.graph
    multistat = bool(self.attribute_runtime and self.attribute_runtime.multistat)
    em_jobs = []
    self_snapshot = event.get("reaction_snapshot") if reaction_damage else None
    if descriptor.get("self_snapshot_event"):
        if descriptor["self_snapshot_event"] in self.captures:
            self.capture_reference(event, descriptor["self_snapshot_event"], "hit_self")
        self_snapshot = self.snapshots.get((event["origin"], descriptor["self_snapshot_event"]))
        if self_snapshot is None:
            if self.counter_views and self.counter_views.frames:
                raise SnapshotCapturePending("counter_frame_missing", "自身资源读数引用的输出捕获尚未执行，不能改用当前累计。",
                                             descriptor["self_snapshot_event"], hit_id=hit["id"])
            raise ValueError(f"{hit['id']} 自身快照事件尚未执行。")
    current_attribute_snapshot = (
        event.get("reaction_snapshot") if reaction_damage else capture_live_attribute_frame(self)
    ) if graph else None
    condition_start = len(self.target_condition_trace)
    receipts, effects = {}, self.fixed_effects(event, hit, descriptor.get("report", True))
    condition_jobs = []
    if self.attribute_runtime and self.attribute_runtime.five_stats:
        applied_conditions = {row['projected_effect_id']: index
            for index in range(condition_start, len(self.target_condition_trace))
            if (row := self.target_condition_trace[index]).get('applied')}
        for effect in effects:
            output = self.attribute_runtime.output(effect, 'rule')
            if output is not None and _consumes_effect(hit, effect):
                condition_jobs.append({'condition': True, 'effect': effect, 'stat': output['stat'],
                                       'condition_trace_index': applied_conditions[effect['id']]})
    effects.extend(self.stellar_superconduct_effects(hit, event))
    pool_gates = self.shared_pools.consume(self, event, hit) if self.shared_pools else {}
    for rid, state in self.state.items():
        rule, quota = self.rules[rid], self.rules[rid].get("quota")
        for index, inst in enumerate(state["instances"]):
            if cid not in inst["beneficiaries"]:
                continue
            if quota and self.filter(quota["consume"], event, rid):
                key = cid if quota["scope"] == "per_beneficiary" else "team"
                before = inst["quota"].get(key, 0)
                consumed = before > 0
                if consumed:
                    inst["quota"][key] -= 1
                receipts[(rid, inst["instance_id"])] = consumed
                self.trace.append({"type": "quota", "t": self.time/TICKS, "cycle": event["origin"], "event_id": hit["id"],
                                   "rule_id": rid, "pool": key, "before": before, "after": inst["quota"].get(key, 0),
                                   "instance_id": inst["instance_id"],
                                   "consumed": consumed, "reported_damage": descriptor.get("report", True)})
    # Collect self-domain candidates from frozen state; provider/enemy remain current.
    for rule in self.rules.values():
        for effect in rule["effects"]:
            if rule["id"] in self.threshold_classic_rule_ids and (event["event"] != "hit" or hit.get("damage_system") != "classic"):
                self.trace.append({"type": "effect_rejected", "t": self.time/TICKS, "event_id": hit["id"],
                                   "rule_id": rule["id"], "effect_id": effect["id"], "reason": "unsupported_threshold_damage_scope"})
                continue
            if counter_view_runtime.is_counter_view(effect["value"]) and (event["event"] != "hit" or hit.get("damage_system") != "classic"):
                self.trace.append({"type": "effect_rejected", "t": self.time/TICKS, "event_id": hit["id"],
                                   "rule_id": rule["id"], "effect_id": effect["id"], "reason": "unsupported_counter_damage_scope"})
                continue
            frozen = self_snapshot if effect["domain"] == "self" or (reaction_damage and effect["domain"] != "enemy") else None
            states = frozen["state"] if frozen else self.state
            if not states[rule["id"]]["instances"]:
                self.trace.append({"type": "effect_rejected", "t": self.time/TICKS, "event_id": hit["id"],
                                   "rule_id": rule["id"], "effect_id": effect["id"], "reason": "not_active"})
            for index, inst in enumerate(states[rule["id"]]["instances"]):
                override = self.match_effect(rule["id"], effect["id"], hit["id"])
                reason = None
                if effect["domain"] != "enemy" and cid not in inst["beneficiaries"]:
                    reason = "recipient_mismatch"
                elif effect["domain"] != "enemy" and rule["beneficiaries"]["scope"] == "current_frontline" and cid != (frozen["frontline"] if frozen else self.frontline):
                    reason = "not_frontline"
                elif rule.get("quota") and not receipts.get((rule["id"], inst["instance_id"]), False):
                    reason = "no_consumption_receipt"
                elif rule.get("resource_gate") and inst["instance_id"] not in pool_gates.get(rule["id"], set()):
                    reason = "no_shared_pool_consumption_receipt"
                elif override == "exclude":
                    reason = "agent_excluded"
                elif override != "include" and effect.get("filter") and not self.filter(effect["filter"], event, rule["id"]):
                    reason = "filter_mismatch"
                if reason:
                    self.trace.append({"type": "effect_rejected", "t": self.time/TICKS, "event_id": hit["id"], "rule_id": rule["id"],
                                       "effect_id": effect["id"], "reason": reason})
                    continue
                if effect["benefit_type_id"] == "benefit.energy_recharge.all":
                    continue
                info = BENEFIT_TYPES[effect["benefit_type_id"]]
                attribute_output = self.attribute_runtime.output(effect, "rule") if graph else None
                if multistat and attribute_output is not None and not _consumes_effect(hit, info):
                    self.trace.append({"type": "effect_rejected", "t": self.time/TICKS, "event_id": hit["id"],
                        "rule_id": rule["id"], "effect_id": effect["id"], "reason": "formula_does_not_consume_attribute"})
                    continue
                separate = ((isinstance(effect["value"], dict) and effect["value"]["sample"] == "trigger")
                            or (attribute_output is not None) or (self.attribute_runtime and info["zone"] == "em"))
                components = [(sample, 1) for sample in inst["layer_snapshots"]] if separate else [(inst["trigger_snapshot"], len(inst["expires"]))]
                for layer_index, (layer_snapshot, layers) in enumerate(components):
                    projected_id = f"{rule['id']}:{effect['id']}:{index}" + (f":layer{layer_index}" if separate and len(components) > 1 else "")
                    if graph and attribute_output is not None:
                        em_jobs.append({"key": ("rule", rule["id"], effect["id"]), "domain": effect["domain"],
                                        "instance": inst["instance_id"], "layer": inst["attribute_layers"][layer_index],
                                        "effect": effect, "rule": rule, "projected_id": projected_id,
                                        **({"stat": attribute_output["stat"]} if multistat else {}),
                                        "sample_time": (layer_snapshot or frozen or current_attribute_snapshot)["time"]})
                        continue
                    value = self.value(effect, rule, {**inst, "trigger_snapshot": layer_snapshot}, cid, event, frozen)
                    if isinstance(effect["value"], dict):
                        self.samples[-1]["projected_effect_id"] = projected_id
                    value = value**layers if info["value_semantics"] == "multiply" else value*layers
                    effects.append({"id": projected_id, "benefit_type_id": effect["benefit_type_id"],
                                    "zone": info["zone"], "stat": info["stat"], "value": value,
                                    "applies_to": {"mode": "hits", "hit_ids": [hit["id"]]}})
                    self.trace.append({"type": "effect_applied", "t": self.time/TICKS, "cycle": event["origin"], "event_id": hit["id"],
                                       "rule_id": rule["id"], "effect_id": effect["id"], "source_id": rule["source_id"],
                                       "instance_id": inst["instance_id"], "projected_effect_id": projected_id,
                                       "value": value, "layers": layers, "domain": effect["domain"],
                                       "sample_t": layer_snapshot["time"]/TICKS if separate and layer_snapshot is not None else frozen["time"]/TICKS if frozen else self.time/TICKS})
    # Legacy hand effects are sampled per domain, keeping this projection lossless.
    # Collect the union of snapshot/current windows, then sample each effect
    # in its own domain. IDs remain source IDs in the numerical projection.
    manual = {}
    for at in {self.time, self_snapshot["time"] if self_snapshot else self.time}:
        for buff in self.manual_buffs(at):
            manual[(buff["id"], buff["start"])] = buff
    for manual_index, buff in enumerate(manual.values()):
        for effect in buff["effects"]:
            sample = None if effect["zone"] in ENEMY_ZONES else self_snapshot
            time = sample["time"] if sample else self.time
            front = sample["frontline"] if sample else self.frontline
            if not buff["start"] <= time/TICKS < buff["end"] or not effect_matches_hit(effect, hit):
                continue
            if effect["zone"] not in ENEMY_ZONES and (buff.get("target") not in (None, cid) or (buff.get("scope") == "frontline" and front != cid)):
                continue
            clone = copy.deepcopy(effect)
            clone.update(id=f"manual:{buff['id']}:{effect['id']}:{manual_index}", applies_to={"mode": "hits", "hit_ids": [hit["id"]]})
            attribute_output = self.attribute_runtime.output(effect, "manual_buff") if graph else None
            if multistat and attribute_output is not None and not _consumes_effect(hit, effect):
                continue
            if graph and attribute_output is not None:
                from genshin_engine.attribute_runtime import _digest
                em_jobs.append({"key": ("manual_buff", buff["id"], effect["id"]), "domain": "self",
                                "instance": "manual:"+_digest([buff["id"], buff["start"], buff["end"]]), "layer": "0",
                                "effect": effect, "buff": buff, "clone": clone, "projected_id": clone["id"], "sample_time": time,
                                **({"stat": attribute_output["stat"]} if multistat else {})})
                continue
            effects.append(clone)
            self.trace.append({"type": "manual_effect_applied", "projected_effect_id": clone["id"],
                               "event_id": hit["id"], "cycle": event["origin"], "t": self.time/TICKS,
                               "buff_id": buff["id"], "effect_id": effect["id"], "value": effect["value"], "sample_t": time/TICKS})
    five_stats = bool(self.attribute_runtime and self.attribute_runtime.five_stats)
    in_reportable_input_window = (self.time < self.data["period"] or
        self.time == self.data["period"] and self.rotation.get("damage_report", {}).get("contract") == DAMAGE_REPORT_CONTRACT)
    scales = hit.get("damage_system") == "classic" or (hit.get("damage_system") in ("lunar", "stellar") and not hit.get("reaction_base", False))
    audit_stat = (hit.get("scaling_stat", "atk") if five_stats and scales else
                  "atk" if multistat and hit.get("damage_system") == "classic" and hit.get("scaling_stat", "atk") == "atk" else None)
    audit_atk = audit_stat is not None
    reaction_sample = reaction_damage and hit.get("extensions", {}).get("elemental", {}).get("provider_stat_sample")
    if graph and (em_jobs or condition_jobs or reaction_sample or audit_atk):
        if multistat:
            wanted = {job["stat"] for job in [*em_jobs, *condition_jobs]} | ({audit_stat} if audit_atk else set()) | ({"em"} if reaction_sample else set())
            projected_attributes = self.attribute_runtime.project(cid, event, self_snapshot, current_attribute_snapshot, [*em_jobs, *condition_jobs], stats=wanted)
            if audit_atk and event["origin"] == 0 and in_reportable_input_window and descriptor.get("report", True):
                self.attribute_scaling_receipts[hit["id"]] = projected_attributes[audit_stat]
        else:
            em_receipt = self.attribute_runtime.project(cid, event, self_snapshot, current_attribute_snapshot, em_jobs)
        for job in em_jobs:
            if multistat:
                em_receipt = job["receipt"]
            effect, value = job["effect"], job["value"]
            if "rule" in job:
                rule = job["rule"]
                info = BENEFIT_TYPES[effect["benefit_type_id"]]
                effects.append({"id": job["projected_id"], "benefit_type_id": effect["benefit_type_id"],
                                "zone": info["zone"], "stat": info["stat"], "value": value,
                                "applies_to": {"mode": "hits", "hit_ids": [hit["id"]]}})
                self.trace.append({"type": "effect_applied", "t": self.time/TICKS, "cycle": event["origin"], "event_id": hit["id"],
                    "rule_id": rule["id"], "effect_id": effect["id"], "source_id": rule["source_id"], "instance_id": job["instance"],
                    "projected_effect_id": job["projected_id"], "value": value, "layers": 1, "domain": effect["domain"],
                    "sample_t": job["sample_time"]/TICKS, "attribute_receipt_id": em_receipt["receipt_id"], "attribute_node_id": job["node_id"]})
                if isinstance(effect['value'], dict) and effect['value'].get('kind') == 'health':
                    self.samples.append({'event_id':event['id'], 'cycle':event['origin'], 't':self.time/TICKS,
                        'rule_id':rule['id'], 'effect_id':effect['id'], 'expression':copy.deepcopy(effect['value']),
                        'value':value, 'projection_receipt_id':em_receipt['receipt_id'],
                        'attribute_node_id':job['node_id'], 'projected_effect_id':job['projected_id']})
                elif isinstance(effect["value"], dict):
                    source = job["input_receipt"]
                    actor = source["query"]["actor"]
                    self.samples.append({"event_id": event["id"], "cycle": event["origin"], "t": self.time/TICKS,
                        "rule_id": rule["id"], "effect_id": effect["id"], "domain": effect["domain"],
                        "expression": copy.deepcopy(effect["value"]), "character": actor, "stat": source["query"]["stat"] if multistat else "em",
                        "sample_t": source["query"]["frame"]["t_us"]/TICKS,
                        "base_value": next(c for c in self.template["characters"] if c["id"] == actor)["stats"].get(effect["value"]["stat"] if multistat else "em"),
                        "dependencies": [], "source_value": source["value"], "value": value,
                        "attribute_receipt_id": source["receipt_id"], "projection_receipt_id": em_receipt["receipt_id"],
                        "attribute_node_id": job["node_id"], "projected_effect_id": job["projected_id"]})
            else:
                clone = job["clone"]
                clone["value"] = value
                effects.append(clone)
                self.trace.append({"type": "manual_effect_applied", "projected_effect_id": clone["id"],
                    "event_id": hit["id"], "cycle": event["origin"], "t": self.time/TICKS, "buff_id": job["buff"]["id"],
                    "effect_id": effect["id"], "value": value, "sample_t": job["sample_time"]/TICKS,
                    "attribute_receipt_id": em_receipt["receipt_id"], "attribute_node_id": job["node_id"]})
        if reaction_damage and hit.get("extensions", {}).get("elemental", {}).get("provider_stat_sample"):
            sampled_em = hit["extensions"]["elemental"]["provider_stat_sample"]["value"]
            comparison_em = (projected_attributes["em"] if multistat else em_receipt)["value"]
            if five_stats and any(job['stat'] == 'em' for job in condition_jobs):
                # Target-qualified EM is a damage-only root. It does not rewrite
                # the ordinary provider sample frozen when Swirl was created.
                comparison_em = self.attribute_runtime.read(cid, 'total', current_attribute_snapshot, stat='em')['value']
            if sampled_em != comparison_em:
                raise AttributeLedgerError("attribute_projection_mismatch", "扩散生成时取样与实际出伤投影的精通不一致", cid)
    reported = descriptor.get("report", True)
    action = self.data["actions"].get(event.get("action_id"), {})
    self.trace.append({"type": "reaction_damage" if reaction_damage else "hit", "event_id": hit["id"], "cycle": event["origin"], "t": self.time/TICKS,
                       "action_id": event.get("action_id"), "ability_id": action.get("ability_id"),
                       "run_id": event.get("run_id"),
                       **({'execution_ref': copy.deepcopy(event['execution_ref'])} if event.get('execution_ref') else {}),
                       "reported_damage": reported, "status": "executed", "effect_count": len(effects),
                       "in_input_window": in_reportable_input_window,
                       "report_disposition": ("verification_window" if event["origin"] != 0 else
                           "outside_input_window" if not in_reportable_input_window else
                           "reporting_disabled" if not reported else "included_before_report_cutoff"),
                       **(copy.deepcopy(hit["extensions"]["elemental"]) if reaction_damage else {})})
    if event["origin"] == 0 and in_reportable_input_window and reported:
        hit["snapshot"] = False
        hit.pop("cast_t", None)
        self.hit_rows.append(hit)
        if effects:
            self.projected_buffs.append({"id": "mechanics:"+hit["id"], "name": "机制派生收益·"+hit.get("hit_name", hit["id"]),
                                         "source": "声明式机制逐命中投影", "start": hit["t"], "end": round(hit["t"]+1/TICKS, 6),
                                         "scope": "team", "target": cid, "effects": effects})


__all__ = [
    "execute_hit",
    "freeze_runtime_panel",
    "ordinary_runtime_panel",
    "project_fixed_effects",
    "sample_effect_value",
]

