"""App paid-request leases, not a second DSH Turn or retry state machine."""
from __future__ import annotations

import copy
import re
import time
import uuid
from pathlib import Path

from genshin_app.equipment.files import fingerprint
from genshin_db.locking import file_lock

from .native_dsh_mux_client import NativeDshMuxError
from .native_turn_budget import load_native_turn_budget_policy
from .sessions import read_owned, safe_path, write_owned

CONTRACT = "genshin-native-dsh-budget-ledger-v2"
MAX_BYTES = 128 * 1024 * 1024
_FILE = "native-dsh-paid-requests.json"
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _identity(value):
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise NativeDshMuxError("dsh_budget_identity_invalid")
    return value


def _route_label(value):
    # DSH owns provider/catalog validation. These are ledger labels, never paths:
    # compatible gateways legitimately use slashes, colons, and dated aliases.
    if (type(value) is not str or not 1 <= len(value) <= 1024
            or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise NativeDshMuxError("dsh_budget_route_label_invalid")
    return value


def _counter(value, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise NativeDshMuxError("dsh_budget_counter_invalid")
    return value


class NativeDshBudgetLedger:
    """Each physical attempt gets one durable claim, including compaction.

    Healthy in-flight requests have no App wall-time deadline. The App checks
    paid-work capacities only before the next request. DSH owns normal retry,
    context limits and compaction; the obsolete retry_count=0 policy is not
    applied to this composition. A lost usage receipt never replays a request.
    """

    def __init__(self, data_dir: Path, *, policy_loader=None, clock=time.time):
        self.data_dir = Path(data_dir).absolute()
        self.root = self.data_dir / "native-dsh-accounting"
        self._policy = policy_loader or (lambda: load_native_turn_budget_policy(self.data_dir))
        self._clock = clock

    def _read(self):
        # Summary polling and writers must share the same OS lock. On Windows,
        # a reader's live file handle can otherwise reject atomic replacement.
        # A never-used account still has a zero-I/O read-only empty summary.
        if not self.root.exists():
            return {"contract": CONTRACT, "leases": {}, "turn_policies": {}, "tool_claims": {}}
        with file_lock(safe_path(self.root, ".native-dsh-paid-requests.lock")):
            return self._read_unlocked()

    def _read_unlocked(self):
        if not safe_path(self.root, _FILE).exists():
            return {"contract": CONTRACT, "leases": {}, "turn_policies": {}, "tool_claims": {}}
        row = read_owned(self.root, _FILE, MAX_BYTES)
        if type(row) is dict and set(row) == {"contract", "leases"} and row["contract"] == "genshin-native-dsh-budget-ledger-v1":
            row.update(contract=CONTRACT, turn_policies={}, tool_claims={})
        if (type(row) is not dict or set(row) != {"contract", "leases", "turn_policies", "tool_claims"}
                or row["contract"] != CONTRACT or any(type(row[key]) is not dict
                    for key in ("leases", "turn_policies", "tool_claims"))):
            raise NativeDshMuxError("dsh_budget_ledger_invalid")
        return row

    def _save(self, row):
        write_owned(self.root, _FILE, row, MAX_BYTES)

    def _frozen_policy(self, ledger, session_id, turn):
        key = fingerprint({"session_id": _identity(session_id), "turn": _counter(turn, optional=True)})
        if key not in ledger["turn_policies"]:
            policy = self._policy()
            ledger["turn_policies"][key] = {"generation": policy.get("generation", 0),
                "effective": copy.deepcopy(policy["effective"])}
        return ledger["turn_policies"][key]

    def turn_policy(self, session_id, turn):
        """One resource snapshot per actual DSH Turn; edits affect the next."""
        self.root.mkdir(parents=True, exist_ok=True)
        with file_lock(safe_path(self.root, ".native-dsh-paid-requests.lock")):
            ledger = self._read()
            policy = copy.deepcopy(self._frozen_policy(ledger, session_id, turn))
            self._save(ledger)
            return policy

    def summary(self, session_id):
        """Read actual accounting facts without creating or waking a Session."""
        _identity(session_id)
        ledger = self._read()
        rows = [row for row in ledger["leases"].values() if row["request"]["session_id"] == session_id]
        tools = [row for row in ledger["tool_claims"].values() if row["session_id"] == session_id]
        return {"provider_requests": len(rows), "provider_settled": sum(row["settled_at"] is not None for row in rows),
            "provider_unsettled": sum(row["settled_at"] is None for row in rows),
            "usage": {key: sum((row["usage"] or {}).get(key, 0) for row in rows)
                for key in ("inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens")},
            "tool_capacity_reservations": {kind: sum(row["category"] == kind for row in tools)
                for kind in ("read", "mutation", "web", "generic")}}

    def admit_tool(self, *, session_id, turn, claim_id, category):
        """Reserve capacity before effects, never reinterpret tool outcomes.

        Exact replay is free here: the existing App/research effect ledger
        still decides whether anything may execute. This is a resource claim,
        not a second record of tool success or failure.
        """
        if category not in {"read", "mutation", "web", "generic"}:
            raise NativeDshMuxError("dsh_budget_tool_category_invalid")
        _identity(session_id)
        _counter(turn)
        if type(claim_id) is not str or re.fullmatch("[a-f0-9]{64}", claim_id) is None:
            raise NativeDshMuxError("dsh_budget_tool_identity_invalid")
        request = {"session_id": session_id, "turn": turn, "claim_id": claim_id, "category": category}
        self.root.mkdir(parents=True, exist_ok=True)
        with file_lock(safe_path(self.root, ".native-dsh-paid-requests.lock")):
            ledger = self._read()
            prior = ledger["tool_claims"].get(claim_id)
            if prior is not None:
                if prior != request:
                    raise NativeDshMuxError("dsh_budget_tool_identity_conflict")
                return {"status": "already_reserved"}
            policy = self._frozen_policy(ledger, session_id, turn)["effective"]
            rows = [row for row in ledger["tool_claims"].values()
                if row["session_id"] == session_id and row["turn"] == turn]
            app_count = sum(row["category"] in {"read", "mutation"} for row in rows)
            category_count = sum(row["category"] == category for row in rows)
            checks = [(len(rows), policy["total_tool_calls"], "total_tool_calls")]
            if category != "generic":
                checks.append((category_count, policy[{"read": "read_tool_calls", "mutation": "mutation_tool_calls", "web": "web_tool_calls"}[category]], category + "_tool_calls"))
            if category in {"read", "mutation"}:
                checks.append((app_count, policy["app_tool_calls"], "app_tool_calls"))
            for used, limit, name in checks:
                if used >= limit:
                    raise NativeDshMuxError("dsh_budget_" + name)
            ledger["tool_claims"][claim_id] = request
            self._save(ledger)
            return {"status": "reserved"}

    def acquire(self, request):
        fields = {"session_id", "lease_id", "purpose", "turn", "step", "provider", "model", "reasoning_effort", "max_tokens"}
        if type(request) is not dict or set(request) != fields:
            raise NativeDshMuxError("dsh_budget_request_invalid")
        sid = _identity(request["session_id"])
        try:
            if str(uuid.UUID(request["lease_id"])) != request["lease_id"]:
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise NativeDshMuxError("dsh_budget_lease_identity_invalid") from None
        _route_label(request["provider"])
        _route_label(request["model"])
        _counter(request["turn"], optional=True)
        _counter(request["step"], optional=True)
        _counter(request["max_tokens"], optional=True)
        if request["purpose"] not in {"main", "compaction", "session-title"}:
            raise NativeDshMuxError("dsh_budget_purpose_invalid")
        if request["purpose"] == "main" and (request["turn"] is None or request["step"] is None):
            raise NativeDshMuxError("dsh_budget_main_fact_missing")
        self.root.mkdir(parents=True, exist_ok=True)
        with file_lock(safe_path(self.root, ".native-dsh-paid-requests.lock")):
            ledger = self._read()
            policy = self._frozen_policy(ledger, sid, request["turn"])
            effective = policy["effective"]
            lease_id = request["lease_id"]
            previous = ledger["leases"].get(lease_id)
            if previous is not None:
                if previous["request_hash"] != fingerprint(request):
                    raise NativeDshMuxError("dsh_budget_lease_conflict")
                return {"allowed": False, "lease_id": lease_id, "reason": {
                    "code": "dsh_budget_lease_already_claimed", "message": "该收费请求已经登记，不能重放。"}}
            rows = [row for row in ledger["leases"].values() if
                    row["request"]["session_id"] == sid and row["request"]["turn"] == request["turn"]]
            input_tokens = output_tokens = uncached_tokens = elapsed = 0
            for row in rows:
                usage = row["usage"] or {}
                uncached_tokens += usage.get("inputTokens", 0)
                input_tokens += sum(usage.get(key, 0) for key in ("inputTokens", "cacheReadTokens", "cacheWriteTokens"))
                output_tokens += usage.get("outputTokens", 0)
                if row["settled_at"] is not None:
                    elapsed += max(0, row["settled_at"] - row["acquired_at"])
            checks = ((len(rows) >= effective["provider_steps"], "provider_steps"),
                (output_tokens >= effective["cumulative_output_tokens"], "cumulative_output_tokens"),
                (input_tokens >= effective["cumulative_input_hard_tokens"], "cumulative_input_hard_tokens"),
                (uncached_tokens >= effective["cumulative_uncached_input_tokens"], "cumulative_uncached_input_tokens"),
                (elapsed >= effective["wall_clock_seconds"], "wall_clock_seconds"),
                (request["max_tokens"] is not None and request["max_tokens"] > effective["per_request_output_tokens"], "per_request_output_tokens"))
            exhausted = next((name for failed, name in checks if failed), None)
            if exhausted is not None:
                return {"allowed": False, "lease_id": lease_id, "reason": {
                    "code": "dsh_budget_" + exhausted, "message": "已达到你设置的收费工作预算，请调整预算后继续。"}}
            ledger["leases"][lease_id] = {"request": copy.deepcopy(request), "request_hash": fingerprint(request),
                "policy_generation": policy.get("generation", 0), "acquired_at": self._clock(),
                "settled_at": None, "usage": None, "finish": None}
            self._save(ledger)
            return {"allowed": True, "lease_id": lease_id,
                "budget_snapshot": {"policy_generation": policy.get("generation", 0),
                    "provider_attempts": len(rows) + 1, "input_soft_warning": input_tokens >= effective["cumulative_input_soft_tokens"],
                    "retry_owner": "dsh", "context_owner": "dsh"}}

    def settle(self, request):
        fields = {"session_id", "lease_id", "purpose", "turn", "step", "usage", "finish"}
        if type(request) is not dict or set(request) != fields:
            raise NativeDshMuxError("dsh_budget_settlement_invalid")
        _identity(request["session_id"])
        if type(request["finish"]) is not str or not 1 <= len(request["finish"]) <= 128:
            raise NativeDshMuxError("dsh_budget_settlement_invalid")
        usage = request["usage"]
        if usage is not None:
            if type(usage) is not dict:
                raise NativeDshMuxError("dsh_budget_usage_invalid")
            for key in ("inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens"):
                _counter(usage.get(key, 0))
        self.root.mkdir(parents=True, exist_ok=True)
        with file_lock(safe_path(self.root, ".native-dsh-paid-requests.lock")):
            ledger = self._read()
            row = ledger["leases"].get(request["lease_id"])
            if row is None or any(row["request"][key] != request[key] for key in ("session_id", "purpose", "turn", "step")):
                raise NativeDshMuxError("dsh_budget_settlement_identity_mismatch")
            if row["settled_at"] is not None:
                if row["usage"] != usage or row["finish"] != request["finish"]:
                    raise NativeDshMuxError("dsh_budget_settlement_conflict")
                return {"lease_id": request["lease_id"], "status": "already_settled"}
            row.update(settled_at=self._clock(), usage=copy.deepcopy(usage), finish=request["finish"])
            self._save(ledger)
            return {"lease_id": request["lease_id"], "status": "settled"}
