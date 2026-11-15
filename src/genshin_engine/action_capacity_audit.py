"""Private source-bound observations of the real cooldown admission queue.

This module grants no database or gameplay authority. The App compiler owns the
source marker; these receipts only attest to the core queue that actually ran.
They are not game events and are never dispatched to mechanism listeners.
"""
from __future__ import annotations

import hashlib
import json

SOURCE_CONTRACT = "action-capacity-source-v1"
CONTRACT = "action-capacity-consumption-v1"
PREFIX = "@capacity_consumption:"
MAX_ACTION_CAPACITY_RECEIPTS = 40000
MAX_ACTION_CAPACITY_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_NODES = 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def bound(ability):
    return "capacity_source" in (ability.get("cooldown") or {})


def source(ability):
    return ability["cooldown"]["capacity_source"]


def receipt_id(action_id):
    return PREFIX + fingerprint([CONTRACT, action_id])


def capabilities():
    return {"contract": CONTRACT, "source_contract": SOURCE_CONTRACT,
            "phase": "action_admission", "queued": False, "listener": False,
            "limits": {"max_receipts": MAX_ACTION_CAPACITY_RECEIPTS,
                       "max_receipt_bytes": MAX_ACTION_CAPACITY_RECEIPT_BYTES,
                       "max_receipt_nodes": MAX_RECEIPT_NODES}}


def namespace_conflicts(value, path=""):
    # Match typed identities/references only; source comments remain free text.
    from .automatic_energy import _IDENTITIES, _REFERENCES
    found = []
    def visit(node, location):
        if isinstance(node, dict):
            for key, child in node.items():
                where = f"{location}.{key}" if location else key
                if (key in _IDENTITIES or key in {"binding_id", "group", "character", "switch_to", "initial_frontline"}) and isinstance(child, str) and child.startswith(PREFIX):
                    found.append(where)
                elif key in _REFERENCES and isinstance(child, list):
                    found.extend(f"{where}[{i}]" for i, item in enumerate(child)
                                 if isinstance(item, str) and item.startswith(PREFIX))
                if isinstance(child, (dict, list)):
                    visit(child, where)
        elif isinstance(node, list):
            for i, child in enumerate(node):
                visit(child, f"{location}[{i}]")
    visit(value, path)
    return found


def mark_parent(row, event, abilities, status, reason=None, receipt=None):
    if event["kind"] != "action":
        return
    ability = abilities[event["data"]["ability_id"]]
    if not bound(ability):
        return
    marker = source(ability)
    row.update(action_id=event["id"], ability_id=ability["id"], character=ability["character"])
    admission = {"contract": CONTRACT, "binding_id": marker["binding_id"],
                 "source_fingerprint": marker["source_fingerprint"], "status": status}
    if receipt is not None:
        admission.update(receipt_id=receipt["id"], receipt_run_id=receipt["run_id"])
    if reason:
        admission["reason"] = reason
    row["capacity_admission"] = admission


def audit_row(event, ability, cycle, queue, ready, *, rejected=False):
    cooldown = ability["cooldown"]
    marker = source(ability)
    identity = receipt_id(event["id"])
    after = sorted([*queue, ready]) if not rejected else list(queue)
    return {"id": identity, "run_id": f"{event['origin']}:{identity}",
            "contract": CONTRACT, "phase": "action_admission",
            "action_id": event["id"], "ability_id": ability["id"],
            "character": ability["character"], "variant": ability.get("variant"),
            "parent_run_id": event["run_id"], "cycle": cycle, "source_cycle": event["origin"],
            "t": event["time"] / 1_000_000, "binding_id": marker["binding_id"],
            "source_fingerprint": marker["source_fingerprint"], "group": cooldown["group"],
            "pool_key": json.dumps([ability["character"], cooldown["group"]], ensure_ascii=False, separators=(",", ":")),
            "capacity": cooldown["charges"], "before": cooldown["charges"] - len(queue),
            "after": cooldown["charges"] - len(after),
            "queue_before": [x / 1_000_000 for x in queue],
            "queue_after": [x / 1_000_000 for x in after],
            "ready_at": None if rejected else ready / 1_000_000,
            "next_ready_at": min(after) / 1_000_000 if after else None,
            "status": "rejected" if rejected else "committed",
            "reason": "cooldown_unavailable" if rejected else None,
            "committed_at_action_start": not rejected}


class AuditLimit(Exception):
    def __init__(self, count, size, *, reason="receipt_budget"):
        self.issue = {"code": "action_capacity_receipt_limit", "severity": "pending",
                      "message": "动作容量审计达到上限，下一动作未提交；有限前缀不能作为完整结果。",
                      "path": "rotation_model", "attempted_count": count, "attempted_bytes": size,
                      "reason": reason, **capabilities()["limits"]}
        super().__init__(self.issue["message"])


class Ledger:
    def __init__(self, expanded, abilities, horizon):
        self.planned = sum(e["kind"] == "action" and e["time"] < horizon
                           and bound(abilities[e["data"]["ability_id"]]) for e in expanded)
        self.count = 0
        self.bytes = 2
        self.receipts = []

    def _guard(self, value):
        """Debit container children before traversal and strings before encoding."""
        nodes, text_bytes = 0, 0
        remaining = max(0, MAX_ACTION_CAPACITY_RECEIPT_BYTES - self.bytes)
        def visit(node):
            nonlocal nodes, text_bytes
            if isinstance(node, (dict, list, tuple)):
                nodes += len(node)
                if nodes > MAX_RECEIPT_NODES:
                    raise AuditLimit(self.count + 1, self.bytes, reason="receipt_nodes")
                children = node.values() if isinstance(node, dict) else node
                for child in children:
                    visit(child)
            elif isinstance(node, str):
                # A Unicode code point needs at least one byte. Reject before
                # allocating its UTF-8 representation when that lower bound fails.
                if len(node) > remaining - text_bytes:
                    raise AuditLimit(self.count + 1, self.bytes + text_bytes + len(node))
                text_bytes += len(node.encode("utf-8"))
                if text_bytes > remaining:
                    raise AuditLimit(self.count + 1, self.bytes + text_bytes)
        visit(value)

    def prepare(self, event, ability, cycle, queue, ready, *, rejected=False):
        # Check count and bounded construction inputs before hashing or copying.
        if self.count + 1 > MAX_ACTION_CAPACITY_RECEIPTS:
            raise AuditLimit(self.count + 1, self.bytes)
        self._guard((event["id"], event["run_id"], ability["id"], ability["character"],
                     ability.get("variant"), source(ability), ability["cooldown"]["group"], queue))
        row = audit_row(event, ability, cycle, queue, ready, rejected=rejected)
        return row, self.reserve(row)

    def reserve(self, row):
        if self.count + 1 > MAX_ACTION_CAPACITY_RECEIPTS:
            raise AuditLimit(self.count + 1, self.bytes)
        self._guard(row)
        size = self.bytes + len(canonical(row)) + bool(self.count)
        if size > MAX_ACTION_CAPACITY_RECEIPT_BYTES:
            raise AuditLimit(self.count + 1, size)
        return size

    def commit(self, row, size):
        self.receipts.append(row)
        self.count += 1
        self.bytes = size

    def finish(self, result, termination=None):
        parents = [r for r in result["event_log"] if r.get("type") == "action" and "capacity_admission" in r]
        result["action_capacity_consumption"] = {
            "contract": CONTRACT, "count_scope": "reported_action_rows",
            "max_cycles_planned_actions": self.planned, "unreported_actions": self.planned - len(parents),
            "planned_actions": len(parents),
            **{f"{status}_actions": sum(r["capacity_admission"]["status"] == status for r in parents)
               for status in ("committed", "rejected", "not_checked", "not_committed", "not_verified")},
            "receipts": self.receipts, "receipt_count": self.count,
            "receipt_fingerprint": fingerprint(self.receipts), "receipt_bytes": self.bytes,
            "limits": capabilities()["limits"]}
        if termination:
            result["action_capacity_consumption"]["termination"] = termination
