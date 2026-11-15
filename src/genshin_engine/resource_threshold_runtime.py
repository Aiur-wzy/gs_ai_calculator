"""Synchronous operation-tail adapter for authenticated constant-only targets.

There is no generic `dispatch(JSON)` or recursive mechanics phase call. The hook
can only claim the latest real batch held by the current ledger, then prepares
each target fully before its own shared-budget reservation and publication.
"""
from __future__ import annotations

import copy
import hashlib

from genshin_engine.resource_threshold_dispatch import DISPATCH_LIMITS, NATIVE_CAUSES
from genshin_engine.resource_thresholds import ResourceThresholdError
from genshin_engine.shared_pools import _canonical


def _fail(code, message, **details):
    raise ResourceThresholdError(code, message, "resource_threshold_triggers", **details)


class ThresholdRuntime:
    def __init__(self, program, shared_pools):
        self.program, self.shared_pools = program, shared_pools
        if shared_pools.ledger._dispatch_authority is None:
            _fail("threshold_dispatch_binding", "运行桥必须拥有真实账本签发器")
        shared_pools.ledger._dispatch_authority.validate_program(program)
        self._target_hashes = {rid: hashlib.sha256(_canonical(rule)).hexdigest() for rid, rule in program._rules.items()}
        self._trace, self._bytes, self._failed = [], 2, False
        self._lifecycle_authority = object()
        self._lifecycle_commands = {}
        self._active_lifecycle_command = None

    def authorize_lifecycle_command(self, command, *, authority, active):
        self.ensure_ready()
        known = self._lifecycle_commands.get(command.get("dispatch_run_id")) if isinstance(command, dict) else None
        if (authority is not self._lifecycle_authority or known is None or known[0] is not command
                or hashlib.sha256(_canonical(command)).hexdigest() != known[1]
                or active and self._active_lifecycle_command is not command):
            _fail("threshold_dispatch_authority", "生命周期任务不是当前真实阈值runtime尚未交付的原命令")
        self.shared_pools.ledger._dispatch_authority.validate_program(self.program)
        return True

    def complete_lifecycle_command(self, command, *, authority, outcome, lifecycle_id, time_us, target_generation_uid=None):
        self.authorize_lifecycle_command(command, authority=authority, active=False)
        row = {**copy.deepcopy(self._lifecycle_commands[command["dispatch_run_id"]][2]),
            "type": "threshold_dispatch_completed", "outcome": outcome, "binding_id": command["binding_id"],
            "dispatch_run_id": command["dispatch_run_id"], "lifecycle_id": lifecycle_id, "time_us": time_us,
            "payload": copy.deepcopy(command["payload"]), "observer_id": command["observer_id"],
            "target_generation_uid": target_generation_uid}
        extra = len(_canonical(row))+1
        if self._bytes+extra > DISPATCH_LIMITS["trace_bytes"]:
            _fail("threshold_dispatch_limit", "生命周期阈值流水字节超限", severity="pending")
        self.shared_pools.ledger._dispatch_authority.validate_program(self.program)
        self._trace.append(row)
        self._bytes += extra
        self._lifecycle_commands.pop(command["dispatch_run_id"])

    def fail_lifecycle_command(self, command, *, authority, exc, time_us):
        known = self._lifecycle_commands.get(command.get("dispatch_run_id")) if isinstance(command, dict) else None
        if authority is not self._lifecycle_authority or known is None or known[0] is not command:
            _fail("threshold_dispatch_authority", "不能用复制的命令改变阈值失败状态")
        if self._failed:
            return
        self._failed = True
        self._trace.append({**copy.deepcopy(known[2]), "type": "threshold_dispatch_failed", "outcome": "pending" if getattr(exc, "issue", {}).get("severity") == "pending" else "invalid",
            "binding_id": command["binding_id"], "dispatch_run_id": command["dispatch_run_id"], "time_us": time_us,
            "lifecycle_id": command["payload"]["ref_id"], "code": getattr(exc, "issue", {}).get("code", "state_delivery_error"), "message": str(exc)[:1024]})

    def ensure_ready(self):
        if self._failed:
            _fail("threshold_dispatch_failed", "阈值桥已失败，仅允许报告实际完成前缀", severity="pending")

    def after_operation(self, ctx, event, phase, parent, operation_id, run_id):
        """Called after that operation's receipt trace, before the next operation."""
        self.ensure_ready()
        authority = self.shared_pools.ledger._dispatch_authority
        batch = authority._last_batch
        if batch is None:
            return
        command = None
        try:
            authority.validate_program(self.program)
            expected_run = parent["run_id"]+(":switch" if event["event"] == "switch" else "")
            if (run_id != expected_run
                    or ctx.time != parent["time"] or type(event["origin"]) is not int
                    or event["origin"] != parent["origin"]):
                _fail("threshold_dispatch_origin", "阈值只能由当前真实原生操作的事务尾执行")
            origin = self.program._operation_origins.get(operation_id)
            if origin is not None and origin["kind"] == "ordinary_timer":
                bridge = getattr(ctx, "state_runtime", None)
                if bridge is None:
                    _fail("threshold_dispatch_origin", "普通timer阈值需要当前真实生命周期桥授权")
                bridge.authorize_threshold_operation(event, phase, parent, operation_id, origin)
            elif origin == {'kind': 'actual_reaction'}:
                bridge = getattr(ctx, 'resource_reactions', None)
                if bridge is None:
                    _fail('threshold_dispatch_origin', '反应阈值缺少实际反应审计桥')
                bridge.authorize_threshold_operation(event, phase, parent, operation_id)
            elif origin != {"kind": "native"} or event["event"] not in NATIVE_CAUSES or event.get("state_event"):
                _fail("threshold_dispatch_origin", "来源不属于已核的原生操作")
            commands = authority.claim(batch, event_id=event["id"], run_id=run_id, t_us=ctx.time,
                                       phase=phase, command_id=operation_id)
            kinds = {c["payload"]["kind"] == "activate_rule" for c in commands}
            if len(kinds) > 1:
                _fail("threshold_lifecycle_scope", "整批预验拒绝常量收益与生命周期混排，尚未执行任何目标")
            for command in commands:
                binding = self.program._by_id[command["binding_id"]]
                rid = command["payload"]["ref_id"]
                qualifier = binding.get("qualification", {})
                condition = qualifier.get("condition")
                qualified = not (qualifier.get("owner_presence") == "frontline" and ctx.frontline != binding["owner"])
                if qualified and condition is not None and ctx.conditions.get(condition) is None:
                    _fail("threshold_condition_unknown", "当前阈值目标条件未知，不能推断为触发或不触发", severity="pending", condition=condition)
                if qualified and condition is not None:
                    qualified = ctx.conditions[condition] is True
                if command["payload"]["kind"] != "activate_rule":
                    bridge = getattr(ctx, "state_runtime", None)
                    if bridge is None:
                        _fail("threshold_dispatch_origin", "生命周期阈值缺少实际clock桥")
                    lineage = {"owner": command["owner"], "source_id": command["source_id"], "phase": phase,
                        "source_run_id": run_id, "source_event_id": event["id"],
                        "receipt_span": copy.deepcopy(batch["context"]["receipt_span"]),
                        **{key: command[key] for key in ("before", "after", "comparison", "threshold_atoms")}}
                    if "counter_observation" in command:
                        lineage["counter_observation"] = copy.deepcopy(command["counter_observation"])
                    if origin["kind"] == "ordinary_timer":
                        lineage["source_origin"] = {**origin, "generation_uid": event["generation_uid"], "ordinal": event["ordinal"]}
                    self._lifecycle_commands[command["dispatch_run_id"]] = command, hashlib.sha256(_canonical(command)).hexdigest(), lineage
                    self._active_lifecycle_command = command
                    try:
                        bridge.accept_lifecycle_command(self, command, event, phase, parent, batch["context"],
                            qualified=qualified, authority=self._lifecycle_authority)
                    finally:
                        self._active_lifecycle_command = None
                    continue
                rule = ctx.rules.get(rid)
                if rule is None or hashlib.sha256(_canonical(rule)).hexdigest() != self._target_hashes[rid]:
                    _fail("threshold_dispatch_target_changed", "目标规则在完整验证后改变，不得沿用旧许可")
                frame = {"time_us": ctx.time, "origin": event["origin"], "dispatch_run_id": command["dispatch_run_id"],
                         "source_run_id": run_id, "source_event_id": event["id"], "phase": phase}
                prepared = self.program.prepare_constant_refresh(command["binding_id"], ctx.state[rid],
                    beneficiaries=ctx.beneficiaries(rule, event), frame=frame, qualified=qualified,
                    attribute_layer_serial=ctx.attribute_layer_serial if ctx.attribute_runtime is not None else None)
                trace = {**prepared["trace"], "receipt_span": copy.deepcopy(batch["context"]["receipt_span"]),
                         "observer_id": command["observer_id"], "before": command["before"], "after": command["after"],
                         "threshold_atoms": command["threshold_atoms"], "comparison": command["comparison"]}
                if "counter_observation" in command:
                    trace["counter_observation"] = copy.deepcopy(command["counter_observation"])
                if origin["kind"] == "ordinary_timer":
                    trace["source_origin"] = {**origin, "generation_uid": event["generation_uid"], "ordinal": event["ordinal"]}
                activation = prepared["activation"]
                extra = len(_canonical(trace))+1-int(not self._trace)
                if activation is not None:
                    extra += len(_canonical(activation))+1
                if self._bytes+extra > DISPATCH_LIMITS["trace_bytes"]:
                    _fail("threshold_dispatch_limit", "阈值执行流水字节超限", severity="pending")
                ctx._reserve_events(1, "resource_threshold_dispatch")
                # Only no-fail publication follows this target's reservation.
                ctx.state[rid] = prepared["state"]
                if ctx.attribute_runtime is not None:
                    ctx.attribute_layer_serial = prepared["attribute_layer_serial"]
                if activation is not None:
                    ctx.trace.append(activation)
                self._trace.append(trace)
                self._bytes += extra
        except Exception as exc:
            self._failed = True
            # One bounded failure suffix; no retry loop can append indefinitely.
            self._trace.append({"type": "threshold_dispatch_failed", "outcome": "pending" if getattr(exc, "issue", {}).get("severity") == "pending" else "invalid",
                "binding_id": command["binding_id"] if command is not None else None,
                "dispatch_run_id": command["dispatch_run_id"] if command is not None else None,
                "source_run_id": run_id, "time_us": ctx.time, "phase": phase,
                "receipt_span": copy.deepcopy(batch.get("context", {}).get("receipt_span")),
                "code": getattr(exc, "issue", {}).get("code", "threshold_dispatch_error"), "message": str(exc)[:1024]})
            raise

    def report(self):
        version = self.program._config["schema_version"]
        scope = "native_operation_tail_constant_classic_provider" if version == 1 else "native_and_ordinary_timer_operation_tail_constant_classic_provider"
        if version in (3, 4):
            scope = "positioned_operation_tail_finite_lifecycle_and_constant_classic_provider"
        return {"contract": f"resource-threshold-trigger-v{version}", "scope": scope,
                "status": "failed" if self._failed else "active", "trace": copy.deepcopy(self._trace),
                "authority": self.shared_pools.ledger.threshold_dispatch_report(),
                "source_approval": "not_evaluated_by_engine",
                **({"unapplied_lifecycle_commands": sorted(self._lifecycle_commands)} if version in (3, 4) else {})}
