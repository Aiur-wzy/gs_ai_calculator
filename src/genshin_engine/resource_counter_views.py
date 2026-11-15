"""Unwired exact counter arithmetic; this does not qualify a combat effect.

The state may only be trusted after its caller establishes ledger/frame authority.
This module verifies structure and gives detached mathematical readings. It does
not sign snapshots, choose game clocks, mutate a ledger, or implement a threshold
crossing event. A bounded game value never truncates the underlying audit count.
"""
from __future__ import annotations

import copy
import hashlib

from genshin_engine.resource_counter_view_schema import (
    RESOURCE_COUNTER_VIEW_SCHEMA,
    VIEW_LIMITS,
)
from genshin_engine.resource_counters import CounterProgram
from genshin_engine.shared_pools import SharedPoolError, _canonical, _schema


class CounterViewError(SharedPoolError):
    pass


def _fail(code, message, **details):
    raise CounterViewError(code, message, "resource_counter_views", **details)


class CounterViewProgram:
    """Bounded immutable definitions; no execution or source authority implied."""

    def __init__(self, config, counter_program):
        try:
            encoded = _canonical(config)
            if len(encoded) > VIEW_LIMITS["definition_bytes"]:
                _fail("counter_view_limit", "资源读数声明字节超限")
        except (TypeError, ValueError, RecursionError, OverflowError) as exc:
            if isinstance(exc, CounterViewError): raise
            _fail("counter_view_schema", "资源读数声明不是有界JSON")
        issues = []
        _schema(config, RESOURCE_COUNTER_VIEW_SCHEMA, "resource_counter_views", issues)
        if issues: _fail("counter_view_schema", "资源读数声明结构无效", issues=issues)
        if not isinstance(counter_program, CounterProgram):
            _fail("counter_view_reference", "资源读数需要已校验累计定义")
        self._counters = copy.deepcopy(counter_program)
        self._views = {}
        for view in sorted(config["views"], key=lambda row: row["id"]):
            if view["id"] in self._views: _fail("counter_view_duplicate", "资源读数ID重复")
            counter = self._counters._counters.get(view["counter_id"])
            if counter is None or counter["owner"] != view["owner"]:
                _fail("counter_view_reference", "资源读数必须引用同提供者的有效累计")
            key = view["key"]
            if (key["kind"] == "total") != (counter["scope"] == "pool_total"):
                _fail("counter_view_key", "读数键类型与累计范围不一致，不能隐式求和或选角色")
            keys = set(self._counters._keys(counter))
            fixed_key = view["owner"] if key["kind"] == "owner" else key.get("character_id")
            if fixed_key is not None and fixed_key not in keys:
                _fail("counter_view_key", "指定角色不是累计的有效资源键")
            if view["status"] == "pending":
                _fail("counter_view_pending", "资源读数的单位、封顶或档位仍待核验", severity="pending")
            self._views[view["id"]] = copy.deepcopy(view)
        self._fingerprint = hashlib.sha256(_canonical([
            list(self._views.values()), self._counters._fingerprint])).hexdigest()

    def read(self, view_id, state, *, recipient=None):
        """Read an exact rational from a structurally checked caller state.

        Neither a valid definition fingerprint nor a counter generation string
        authenticates this state as an executed snapshot. The real bridge must
        provide that evidence before any later damage projection uses the value.
        """
        if type(view_id) is not str or view_id not in self._views:
            _fail("counter_view_reference", "资源读数不存在")
        try:
            actual = self._counters._state(state)
        except SharedPoolError as exc:
            _fail("counter_view_state", "资源读数需要属于当前定义的完整累计状态",
                  severity=exc.issue.get("severity", "error"), cause=copy.deepcopy(exc.issue))
        view = self._views[view_id]
        counter = self._counters._counters[view["counter_id"]]
        key_kind = view["key"]["kind"]
        key = ("total" if key_kind == "total" else view["owner"] if key_kind == "owner" else
               view["key"]["character_id"] if key_kind == "character" else recipient)
        values = actual["values"][view["counter_id"]]
        if type(key) is not str or key not in values:
            _fail("counter_view_key", "本次接收者不是明确的累计键，不能回退其他角色或零值")
        raw = values[key]
        scale = self._counters._pools[counter["pool_id"]]["unit_scale"]
        transform = view["transform"]
        if transform["kind"] == "scaled":
            unclamped, denominator, remainder = raw, scale, None
            numerator = min(raw, transform.get("max_atoms", raw))
        else:
            unclamped, remainder = divmod(raw, transform["step_atoms"])
            numerator, denominator = min(unclamped, transform.get("max_steps", unclamped)), 1
        life = actual["lifecycles"][counter["lifecycle_id"]]
        return {"contract": "counter-view-reading-v1", "view_id": view_id,
            "source_id": view["source_id"], "counter_source_id": counter["source_id"], "owner": view["owner"],
            "counter_id": view["counter_id"], "pool_id": counter["pool_id"], "key": key,
            "raw_atoms": raw, "unit_scale": scale, "numerator": numerator, "denominator": denominator,
            "uncapped_numerator": unclamped, "cap_applied": numerator != unclamped,
            "remainder_atoms": remainder, "transform": copy.deepcopy(transform),
            "definition_fingerprint": self._fingerprint,
            "counter_cursor": {"control_sequence": actual["last_control_sequence"], "ledger_sequence": actual["last_ledger_sequence"]},
            "lifecycle": {"id": counter["lifecycle_id"], **copy.deepcopy(life)},
            "history_scope": "lifetime_total" if counter["reset_on"] == "never" else "since_last_reset",
            "sampling_authority": "caller_supplied_state", "qualification": "not_evaluated",
            "source_approval": "not_evaluated"}
