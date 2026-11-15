"""Ledger-owned threshold batches. Public audit JSON is never a capability.

``prepare`` does not mutate this service. The ledger budgets the whole candidate,
then calls ``publish`` in its no-fail commit section. Only original, unchanged
objects can be claimed once, in issuance order, by the real event adapter.
"""
from __future__ import annotations

import copy
import hashlib

from genshin_engine.resource_threshold_dispatch import (
    DISPATCH_LIMITS,
    ThresholdTriggerProgram,
)
from genshin_engine.resource_thresholds import ResourceThresholdError
from genshin_engine.shared_pools import _canonical


def _fail(code, message, **details):
    raise ResourceThresholdError(code, message, "resource_threshold_triggers", **details)


class ThresholdDispatchAuthority:
    def __init__(self, program):
        if not isinstance(program, ThresholdTriggerProgram):
            _fail("threshold_dispatch_binding", "派发必须关联已校验的完整目标程序")
        self._program_fingerprint = program.authority_fingerprint()
        self._by_observer = {}
        for row in copy.deepcopy(program.bindings):
            self._by_observer.setdefault(row["observer_id"], []).append(row)
        self._issued_sequence, self._next_claim = 0, 1
        self._known, self._issued_rows, self._claimed_rows = {}, [], []
        self._bytes, self._work, self._last_batch = 4, 0, None

    def validate_program(self, program):
        """Runtime and issuer must share the full checked program, before claim."""
        if (not isinstance(program, ThresholdTriggerProgram)
                or program.authority_fingerprint() != self._program_fingerprint):
            _fail("threshold_dispatch_binding", "运行桥与签发账本的完整目标及资源定义不一致")

    def prepare(self, rows):
        """Only the owner may treat supplied audit rows as genuine ledger facts."""
        try:
            if not isinstance(rows, list) or len(rows) > 64:
                _fail("threshold_dispatch_candidate", "每笔资源事务最多64条固定键观察")
            commands, identities, context = [], set(), None
            for observation in rows:
                if type(observation["crossed"]) is not bool:
                    _fail("threshold_dispatch_candidate", "越界标志必须为布尔")
                if not observation["crossed"]:
                    continue
                for binding in self._by_observer.get(observation["observer_id"], ()):
                    if (observation["command_kind"] != "operation" or observation["authority"] != "audit_no_dispatch_authority"
                            or observation["execution"] != "ledger_committed_audit"):
                        _fail("threshold_dispatch_candidate", "派发只接受本次真实资源操作候选")
                    identity = binding["id"], observation["resource_sequence"]
                    if identity in identities:
                        _fail("threshold_dispatch_candidate", "同一资源观察不能重复派发同一联动")
                    identities.add(identity)
                    actual = {k: copy.deepcopy(observation[k]) for k in
                              ("event_id", "run_id", "t_us", "phase", "command_id", "receipt_span")}
                    if context is not None and context != actual:
                        _fail("threshold_dispatch_candidate", "一个派发批只能对应一个真实资源事务")
                    context = actual
                    uid = "@threshold_dispatch:"+hashlib.sha256(_canonical([binding["id"], observation["resource_receipt_id"], observation["receipt_span"]])).hexdigest()
                    commands.append({"binding_id": binding["id"], "owner": binding["owner"], "source_id": binding["source_id"],
                        "order": binding["order"], "payload": copy.deepcopy(binding["payload"]), "dispatch_run_id": uid,
                        "observer_id": observation["observer_id"], "resource_sequence": observation["resource_sequence"],
                        "before": observation["before"], "after": observation["after"],
                        "comparison": observation["comparison"], "threshold_atoms": observation["threshold_atoms"]})
                    if observation.get("input_kind") == "counter_value_key":
                        commands[-1]["counter_observation"] = {key: copy.deepcopy(observation[key]) for key in (
                            "input_kind", "counter_id", "counter_key", "counter_before_cursor", "counter_after_cursor",
                            "generation_uid", "lifecycle_id", "history_scope", "unit_scale")}
            if not commands:
                return None
            commands.sort(key=lambda r: r["order"])
            sequence = self._issued_sequence+1
            batch = {"contract": "threshold-dispatch-batch-v1", "sequence": sequence,
                     "context": context, "commands": commands}
            raw = _canonical(batch)
            fingerprint = hashlib.sha256(raw).hexdigest()
            claim_record = {"type": "threshold_batch_claimed", "sequence": sequence, "context": copy.deepcopy(context)}
            # Pre-charge both the issued batch and its future claim row. Claiming
            # cannot create an unbudgeted second audit array after the commit.
            extra = len(raw)+len(_canonical(claim_record))+2-int(not self._issued_rows)*2
            if (len(commands) > 64 or sequence > DISPATCH_LIMITS["events"]
                    or self._bytes+extra > DISPATCH_LIMITS["trace_bytes"]):
                _fail("threshold_dispatch_limit", "阈值签发批数量或字节超限", severity="pending")
            # Receipt and object registry share the prepared object. Public reads
            # below return copies; no outside writer obtains it before publish.
            return {"batch": batch, "fingerprint": fingerprint, "claim_record": claim_record,
                    "bytes": extra, "work": 1, "rows": 2}
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError, ArithmeticError) as exc:
            if isinstance(exc, ResourceThresholdError):
                raise
            _fail("threshold_dispatch_candidate", "签发候选不是完整有界结构")

    def publish(self, prepared):
        """No validation/copy/reserve here; called only in the ledger commit tail."""
        if prepared is None:
            self._last_batch = None
            return
        batch = prepared["batch"]
        self._issued_sequence = batch["sequence"]
        self._known[batch["sequence"]] = batch, prepared["fingerprint"], prepared["claim_record"]
        self._issued_rows.append(batch)
        self._bytes += prepared["bytes"]
        self._work += prepared["work"]
        self._last_batch = batch

    def claim(self, batch, *, event_id, run_id, t_us, phase, command_id):
        """Claim authentic order once. A claim is not a successful rule activation."""
        try:
            known = self._known.get(batch.get("sequence")) if isinstance(batch, dict) and type(batch.get("sequence")) is int else None
            if known is None or known[0] is not batch or hashlib.sha256(_canonical(batch)).hexdigest() != known[1]:
                _fail("threshold_dispatch_authority", "派发批不是此资源账尚未领取的原对象或已被修改")
            if batch["sequence"] != self._next_claim:
                _fail("threshold_dispatch_order", "资源派发批不能跳过尚未领取的前批")
            expected = {"event_id": event_id, "run_id": run_id, "t_us": t_us, "phase": phase, "command_id": command_id}
            if any(type(batch["context"][k]) is not type(value) or batch["context"][k] != value for k, value in expected.items()):
                _fail("threshold_dispatch_authority", "派发只能在真实来源事务当前阶段领取")
            result = copy.deepcopy(batch["commands"])
            record = known[2]
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError, ArithmeticError) as exc:
            if isinstance(exc, ResourceThresholdError):
                raise
            _fail("threshold_dispatch_authority", "派发领取参数不是完整结构")
        self._known.pop(batch["sequence"])
        self._next_claim += 1
        self._claimed_rows.append(record)
        return result

    def report(self):
        return {"contract": "threshold-dispatch-batch-v1", "issued": copy.deepcopy(self._issued_rows),
                "claimed": copy.deepcopy(self._claimed_rows), "applied": "reported_by_runtime_not_authority",
                "reserved_bytes": self._bytes, "work": self._work}
