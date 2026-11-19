"""Detached fixed-key arithmetic; a candidate cannot authorize any dispatch.

Only the ledger can certify that these caller-supplied rows actually committed.
Every real resource span, including an unmatched pool/cause, advances the cursor.
"""
from __future__ import annotations

import copy
import hashlib

from genshin_engine.resource_threshold_schema import (
    RESOURCE_THRESHOLD_SCHEMA,
    THRESHOLD_LIMITS,
)
from genshin_engine.shared_pools import (
    SharedPoolError,
    _canonical,
    _schema,
    pool_capacity_limit,
    pool_capacity_mode,
    validate_shared_pools,
)


class ResourceThresholdError(SharedPoolError):
    pass


def _fail(code, message, **details):
    raise ResourceThresholdError(code, message, "resource_threshold_observers", **details)


def _int(value, maximum=THRESHOLD_LIMITS["sequence"]):
    return type(value) is int and 0 <= value <= maximum


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 512


class ThresholdObserverProgram:
    """Immutable input definitions plus detached candidate chaining, not a clock."""

    def __init__(self, config, pools, *, character_ids, counter_program=None):
        from genshin_engine.resource_counters import CounterProgram
        from genshin_engine.resource_threshold_v2_schema import (
            RESOURCE_THRESHOLD_V2_SCHEMA,
        )
        try:
            if len(_canonical(config)) > THRESHOLD_LIMITS["definition_bytes"]:
                _fail("threshold_limit", "阈值定义字节超限")
        except (TypeError, ValueError, RecursionError, OverflowError) as exc:
            if isinstance(exc, ResourceThresholdError):
                raise
            _fail("threshold_schema", "阈值定义不是有界JSON")
        problems = []
        schema = RESOURCE_THRESHOLD_V2_SCHEMA if isinstance(config, dict) and config.get("schema_version") == 2 else RESOURCE_THRESHOLD_SCHEMA
        _schema(config, schema, "resource_threshold_observers", problems)
        if problems:
            _fail("threshold_schema", "阈值定义结构错误", issues=problems)
        if not isinstance(pools, dict) or type(pools.get("schema_version")) is not int or pools["schema_version"] != 2:
            _fail("threshold_unit", "余额阈值需要显式原子单位v2池")
        problems = validate_shared_pools(pools, character_ids=character_ids)
        if problems:
            _fail("threshold_reference", "依赖池不可用", issues=problems)
        self._config, self._pool_config = copy.deepcopy(config), copy.deepcopy(pools)
        self._pools = {r["id"]: r for r in self._pool_config["pools"]}
        self._commands = {"operation": {r["id"]: r for r in self._pool_config["operations"]},
                          "consume": {r["id"]: r for r in self._pool_config["consumers"]}}
        has_counter = any(row["input"]["kind"] == "counter_value_key" for row in self._config["observers"])
        if has_counter and (not isinstance(counter_program, CounterProgram) or counter_program._pool_config != self._pool_config):
            _fail("threshold_counter_reference", "累计阈值必须关联此完整资源图的已核累计程序")
        self._counter_program = copy.deepcopy(counter_program) if has_counter else None
        self._counter_index = {}
        self._index, ids, identities = {}, set(), set()
        for row in sorted(self._config["observers"], key=lambda r: r["id"]):
            source = row["input"]
            counter = self._counter_program._counters.get(source["counter_id"]) if source["kind"] == "counter_value_key" else None
            if source["kind"] == "counter_value_key" and (counter is None or counter["owner"] != row["owner"]
                    or source["key"] not in self._counter_program._keys(counter)
                    or row["causes"]["consumer_ids"] or not set(row["causes"]["operation_ids"]) <= set(counter["operation_ids"])):
                _fail("threshold_counter_reference", "累计阈值须为同提供者明确累计键及其spend操作")
            pool = self._pools.get(counter["pool_id"] if counter is not None else source["pool_id"])
            if pool is None or pool["owner"] != row["owner"] or (counter is None and source["key"] not in pool["initial"]):
                _fail("threshold_reference", "观察必须绑定同提供者的现有固定池键")
            if (counter is None and pool_capacity_mode(pool) == "bounded"
                    and row["threshold_atoms"] > pool["capacity"]):
                _fail("threshold_value", "阈值超过池容量")
            identity = (row["owner"], row["source_id"], source["kind"], source.get("counter_id", pool["id"]), source["key"], row["comparison"], row["threshold_atoms"])
            if row["id"] in ids or identity in identities:
                _fail("threshold_duplicate", "阈值观察ID或同源同对象阈值重复")
            ids.add(row["id"])
            identities.add(identity)
            if row["status"] == "pending":
                _fail("threshold_pending", "观察来源仍待确认", severity="pending")
            if not any(row["causes"].values()):
                _fail("threshold_cause", "至少需要一个真实操作或消费者")
            for field, kind in (("operation_ids", "operation"), ("consumer_ids", "consume")):
                refs = row["causes"][field]
                if len(refs) != len(set(refs)):
                    _fail("threshold_cause", "观察cause重复")
                for ref in refs:
                    command = self._commands[kind].get(ref)
                    if command is None or command["pool_id"] != pool["id"]:
                        _fail("threshold_cause", "观察cause不属于所选池")
                    if counter is not None:
                        self._counter_index.setdefault((kind, ref), []).append(row)
                    else:
                        self._index.setdefault((pool["id"], source["key"], kind, ref), []).append(row)
        fingerprint = [self._config, self._pool_config]
        if self._counter_program is not None:
            fingerprint.append(self._counter_program._fingerprint)
        self._fingerprint = hashlib.sha256(_canonical(fingerprint)).hexdigest()

    def initial_state(self):
        return {"definition_fingerprint": self._fingerprint, "last_ledger_sequence": 0,
                "evaluation_count": 0, "index_visits": 0, "execution": "candidate_only"}

    @property
    def counter_dependencies(self):
        """Detached checked dependencies keyed by observer ID, not approvals."""
        if self._counter_program is None:
            return {}
        result = {}
        for row in self._config["observers"]:
            if row["input"]["kind"] != "counter_value_key":
                continue
            counter = self._counter_program._counters[row["input"]["counter_id"]]
            result[row["id"]] = {"counter_id": counter["id"], "key": row["input"]["key"],
                "pool_id": counter["pool_id"], "lifecycle_id": counter["lifecycle_id"],
                "operation_ids": list(row["causes"]["operation_ids"])}
        return result

    def _state(self, state):
        try:
            if (len(_canonical(state)) > THRESHOLD_LIMITS["state_bytes"]
                    or set(state) != set(self.initial_state())
                    or state["definition_fingerprint"] != self._fingerprint or state["execution"] != "candidate_only"
                    or not _int(state["last_ledger_sequence"])
                    or not _int(state["evaluation_count"], THRESHOLD_LIMITS["receipts"])
                    or not _int(state["index_visits"], THRESHOLD_LIMITS["index_visits"])):
                _fail("threshold_state", "观察候选状态无效或不属于此定义")
            return copy.deepcopy(state)
        except (TypeError, ValueError, KeyError, RecursionError, OverflowError) as exc:
            if isinstance(exc, ResourceThresholdError):
                raise
            _fail("threshold_state", "观察状态不是有界完整结构")

    def plan_commit(self, state, candidate):
        """Validate one continuous all-key transaction; no actual ledger authority."""
        updated = self._state(state)
        try:
            fields = {"receipt_start", "receipt_end", "receipts"} | ({"counter_span"} if self._counter_program is not None else set())
            if not isinstance(candidate, dict) or set(candidate) != fields:
                _fail("threshold_candidate", "资源候选span结构错误")
            start, end, rows = candidate["receipt_start"], candidate["receipt_end"], candidate["receipts"]
            if (not _int(start) or not _int(end) or start != updated["last_ledger_sequence"]+1
                    or not isinstance(rows, list) or not 1 <= len(rows) <= 16 or end != start+len(rows)-1):
                _fail("threshold_order", "候选必须覆盖下一连续资源回执区间")
            result, keys, identity, visits = [], set(), None, 0
            for offset, receipt in enumerate(rows):
                kind = receipt["type"]
                if kind not in self._commands:
                    _fail("threshold_candidate", "资源类型不是操作或消费者")
                command_id = receipt["operation_id" if kind == "operation" else "consumer_id"]
                command = self._commands[kind].get(command_id)
                pool = self._pools.get(receipt["pool_id"])
                key = receipt["key"]
                if (command is None or pool is None or command["pool_id"] != pool["id"]
                        or receipt["source_id"] != command["source_id"] or key not in pool["initial"] or key in keys):
                    _fail("threshold_candidate", "资源候选引用或键无效")
                keys.add(key)
                current = (kind, command_id, pool["id"], receipt["event_id"], receipt["run_id"], receipt["t_us"], receipt["phase"])
                if identity is not None and identity != current:
                    _fail("threshold_candidate", "一次提交不能混合不同实际操作或事件")
                identity = current
                if (not _text(receipt["id"]) or not _text(receipt["event_id"]) or not _text(receipt["run_id"])
                        or not _int(receipt["t_us"]) or receipt["phase"] not in ("pre", "post")
                        or type(receipt["unit_scale"]) is not int or receipt["unit_scale"] != pool["unit_scale"]
                        or receipt["quantity_unit"] != "atoms" or type(receipt["qualified"]) is not bool):
                    _fail("threshold_candidate", "实际事件、单位或资格类型无效")
                before, after, accepted = receipt["before"], receipt["after"], receipt["accepted"]
                if (not all(_int(v, pool_capacity_limit(pool)) for v in (before, after, accepted))
                        or type(receipt["delta"]) is not int or receipt["delta"] != after-before
                        or not _int(receipt["requested"], 1000000)
                        or receipt.get("capacity_mode", "bounded") != pool_capacity_mode(pool)):
                    _fail("threshold_candidate", "资源前后数量必须是有界原子整数且delta一致")
                operation = command["operation"] if kind == "operation" else "consume"
                requested = receipt["requested"]
                declared_request = before if operation == "spend" and command["amount"] == "all_available" else command["amount"]
                if kind == 'operation':
                    from genshin_engine.shared_pool_probability import validate_probability_receipt
                    validate_probability_receipt(command, pool, receipt)
                    if 'random_effect' in command:
                        declared_request = receipt['requested']  # Independently rederived by the checked trial above.
                if requested != declared_request or accepted > requested:
                    _fail("threshold_candidate", "请求量必须等于声明原子量，接受量不可超过请求量")
                if (operation in ("consume", "spend") and after != before-accepted
                        or operation == "grant" and after != before+accepted
                        or operation == "set" and not (after == (requested if pool_capacity_mode(pool) == "unbounded"
                                                                  else min(pool["capacity"], requested)) and accepted == after
                                                       or after == before and accepted == 0)
                        or operation == "consume" and accepted not in (0, requested)
                        or operation == "spend" and command["spend_policy"] == "all_or_nothing" and accepted not in (0, requested)
                        or operation == "spend" and command["spend_policy"] == "up_to_available" and accepted not in (0, min(before, requested))
                        or operation == "grant" and accepted not in (0, requested if pool_capacity_mode(pool) == "unbounded"
                                                                       else min(pool["capacity"]-before, requested))
                        or kind == "operation" and receipt["operation"] != operation
                        or not receipt["qualified"] and (after != before or accepted != 0)):
                    _fail("threshold_candidate", "资源数量变化不符合声明操作")
                if (kind == "operation" and receipt["phase"] != command["trigger"]["phase"]
                        or kind == "consume" and receipt["phase"] != "pre"):
                    _fail("threshold_candidate", "资源阶段不符合cause声明")
                observers = self._index.get((pool["id"], key, kind, command_id), ())
                visits += len(observers)
                for observer in observers:
                    threshold = observer["threshold_atoms"]
                    crossed = before < threshold <= after if observer["comparison"] == "enter_gte" else before > threshold >= after
                    result.append({"type": "observer_evaluation", "observer_id": observer["id"],
                        "source_id": observer["source_id"], "owner": observer["owner"], "pool_id": pool["id"], "key": key,
                        "unit_scale": pool["unit_scale"], "quantity_unit": "atoms", "threshold_atoms": threshold,
                        "comparison": observer["comparison"], "before": before, "after": after, "crossed": crossed,
                        "command_kind": kind, "command_id": command_id, "event_id": receipt["event_id"],
                        "run_id": receipt["run_id"], "t_us": receipt["t_us"], "phase": receipt["phase"],
                        "stage": 1 if kind == "consume" else 0 if receipt["phase"] == "pre" else 2,
                        "resource_receipt_id": receipt["id"], "resource_sequence": start+offset,
                        "receipt_span": [start, end], "authority": "audit_no_dispatch_authority",
                        "source_approval": "not_evaluated_by_engine", "execution": "candidate_only"})
            command = self._commands[identity[0]][identity[1]]
            expected = set(self._pools[identity[2]]["initial"]) if identity[0] == "operation" and command["targets"]["mode"] == "all_pool_keys" else set(command["targets"]["characters"]) if identity[0] == "operation" else keys
            if keys != expected:
                _fail("threshold_candidate", "资源候选必须包含此次操作的全部目标键")
            if self._counter_program is not None:
                from genshin_engine.resource_threshold_counter_span import (
                    validate_counter_span,
                )
                first, final, counter_visits = validate_counter_span(self._counter_program, candidate["counter_span"], rows, start, end)
                observers = self._counter_index.get((identity[0], identity[1]), ())
                visits += counter_visits+len(observers)
                for observer in observers:
                    source = observer["input"]
                    ident, key = source["counter_id"], source["key"]
                    counter = self._counter_program._counters[ident]
                    before, after = first["values"][ident][key], final["values"][ident][key]
                    threshold = observer["threshold_atoms"]
                    crossed = before < threshold <= after if observer["comparison"] == "enter_gte" else before > threshold >= after
                    life = first["lifecycles"][counter["lifecycle_id"]]
                    result.append({"type": "observer_evaluation", "observer_id": observer["id"],
                        "source_id": observer["source_id"], "owner": observer["owner"], "pool_id": counter["pool_id"], "key": key,
                        "input_kind": "counter_value_key", "counter_id": ident, "counter_key": key,
                        "unit_scale": self._pools[counter["pool_id"]]["unit_scale"], "quantity_unit": "atoms", "threshold_atoms": threshold,
                        "comparison": observer["comparison"], "before": before, "after": after, "crossed": crossed,
                        "command_kind": identity[0], "command_id": identity[1], "event_id": receipt["event_id"],
                        "run_id": receipt["run_id"], "t_us": receipt["t_us"], "phase": receipt["phase"],
                        "stage": 0 if receipt["phase"] == "pre" else 2,
                        "resource_receipt_id": receipt["id"], "resource_receipt_ids": [r["id"] for r in rows], "resource_sequence": end,
                        "receipt_span": [start, end], "authority": "audit_no_dispatch_authority", "execution": "candidate_only",
                        "counter_before_cursor": {"ledger_sequence": first["last_ledger_sequence"], "control_sequence": first["last_control_sequence"]},
                        "counter_after_cursor": {"ledger_sequence": final["last_ledger_sequence"], "control_sequence": final["last_control_sequence"]},
                        "generation_uid": life["generation_uid"], "lifecycle_id": counter["lifecycle_id"],
                        "history_scope": "lifetime_total" if counter["reset_on"] == "never" else "since_last_reset",
                        "source_approval": "not_evaluated_by_engine"})
            updated.update(last_ledger_sequence=end, evaluation_count=updated["evaluation_count"]+len(result),
                           index_visits=updated["index_visits"]+visits)
            if updated["evaluation_count"] > THRESHOLD_LIMITS["receipts"] or updated["index_visits"] > THRESHOLD_LIMITS["index_visits"]:
                _fail("threshold_limit", "阈值观察累计工作超限", severity="pending")
            return {"state": updated, "observer_receipts": result, "extra_event_count": len(result), "index_visits": visits}
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
            if isinstance(exc, ResourceThresholdError):
                raise
            _fail("threshold_candidate", "资源候选不是有界完整结构")
