"""Bounded action-admission audits; these records never enter the event queue."""
from __future__ import annotations

import hashlib
import json

CONTRACT = "automatic-energy-delivery-v1"
PREFIX = "@energy_spend:"
MAX_AUTOMATIC_ENERGY_RECEIPTS = 40000
MAX_AUTOMATIC_ENERGY_RECEIPT_BYTES = 8 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def receipt_id(action_id):
    return PREFIX + fingerprint([CONTRACT, action_id])


def automatic(ability):
    return (ability.get("energy") or {}).get("delivery") == "action_start"


def capabilities():
    return {"modes": ["existing_events", "action_start"], "default": "existing_events",
            "contract": CONTRACT, "phase": "action_admission", "native_field": "energy_delivery",
            "execution_field": "ability.energy.delivery", "queued": False, "listener": False,
            "limits": {"max_receipts": MAX_AUTOMATIC_ENERGY_RECEIPTS,
                       "max_receipt_bytes": MAX_AUTOMATIC_ENERGY_RECEIPT_BYTES},
            "semantics": "动作开始门槛与冷却获准后扣除一次；审计为历史提交值，不占用户order。"}


_IDENTITIES = {"id", "action_id", "ability_id", "hit_id", "prototype_hit_id", "rule_id", "event_id",
               "self_snapshot_event", "spawn_id", "source_id", "source_part", "condition"}
_REFERENCES = {"event_ids", "action_ids", "ability_ids", "hit_ids", "rule_ids", "replaces_event_ids"}


def namespace_conflicts(value, path=""):
    """Inspect typed identities/references, never interpret free-text evidence."""
    found = []

    def visit(node, location):
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = f"{location}.{key}" if location else key
                if key in _IDENTITIES and isinstance(child, str) and child.startswith(PREFIX):
                    found.append(child_path)
                elif key in _REFERENCES and isinstance(child, list):
                    found.extend(f"{child_path}[{i}]" for i, item in enumerate(child)
                                 if isinstance(item, str) and item.startswith(PREFIX))
                if isinstance(child, (dict, list)):
                    visit(child, child_path)
        elif isinstance(node, list):
            for i, child in enumerate(node):
                visit(child, f"{location}[{i}]")

    visit(value, path)
    return found


def mark_parent(row, event, abilities, status, reason=None):
    if event["kind"] != "action":
        return
    ability = abilities[event["data"]["ability_id"]]
    if not automatic(ability):
        return
    row.update(action_id=event["id"], ability_id=ability["id"], character=ability["character"])
    row["energy_delivery"] = {"mode": "action_start", "status": status, "receipt_id": receipt_id(event["id"])}
    if reason:
        row["energy_delivery"]["reason"] = reason


def audit_row(event, ability, cycle, before):
    energy = ability["energy"]
    identity = receipt_id(event["id"])
    return {"id": identity, "run_id": f"{event['origin']}:{identity}", "type": "energy_spend",
            "delivery": "automatic_action_start", "phase": "action_admission", "contract": CONTRACT,
            "action_id": event["id"], "ability_id": ability["id"], "character": ability["character"],
            "parent_run_id": event["run_id"], "cycle": cycle, "source_cycle": event["origin"],
            "t": event["time"] / 1_000_000, "status": "passed", "before": before,
            "required": energy["required"], "amount": energy["amount"], "deficit": 0,
            "after": max(0, before - energy["amount"]), "committed_at_action_start": True}


class AuditLimit(Exception):
    def __init__(self, count, size):
        self.issue = {"code": "automatic_energy_receipt_limit", "severity": "pending",
                      "message": "自动扣能审计达到上限，下一动作未提交；有限前缀不能作为完整结果。",
                      "path": "rotation_model", "attempted_count": count, "attempted_bytes": size,
                      "max_receipts": MAX_AUTOMATIC_ENERGY_RECEIPTS,
                      "max_receipt_bytes": MAX_AUTOMATIC_ENERGY_RECEIPT_BYTES}
        super().__init__(self.issue["message"])


class Ledger:
    def __init__(self, expanded, abilities, horizon):
        self.planned = sum(e["kind"] == "action" and e["time"] < horizon
                           and automatic(abilities[e["data"]["ability_id"]]) for e in expanded)
        self.count = 0
        self.bytes = 2

    def reserve(self, row):
        # Count is checked before serializing the next potentially long identity.
        if self.count + 1 > MAX_AUTOMATIC_ENERGY_RECEIPTS:
            raise AuditLimit(self.count + 1, self.bytes)
        size = self.bytes + len(canonical(row)) + bool(self.count)
        if size > MAX_AUTOMATIC_ENERGY_RECEIPT_BYTES:
            raise AuditLimit(self.count + 1, size)
        return size

    def commit(self, size):
        self.count += 1
        self.bytes = size

    def finish(self, result, termination=None):
        parents = [r for r in result["event_log"] if r.get("type") == "action" and "energy_delivery" in r]
        audits = [r for r in result["event_log"] if r.get("delivery") == "automatic_action_start"]
        result["automatic_energy_delivery"] = {
            "contract": CONTRACT, "count_scope": "reported_action_rows",
            "max_cycles_planned_actions": self.planned, "unreported_actions": self.planned - len(parents),
            "planned_actions": len(parents),
            **{f"{status}_actions": sum(r["energy_delivery"]["status"] == status for r in parents)
               for status in ("committed", "not_committed", "not_verified")},
            "receipt_count": len(audits), "receipt_fingerprint": fingerprint(audits),
            "limits": capabilities()["limits"], "receipt_bytes": self.bytes}
        if termination:
            result["automatic_energy_delivery"]["termination"] = termination


__all__ = ["CONTRACT", "PREFIX", "automatic", "capabilities", "namespace_conflicts", "receipt_id"]
