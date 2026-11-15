"""Private, bounded receipts for source-bound action admission.

The App compiler is the only authority that may attach an
``action_admission_source`` marker to an ability.  This module does not grant
that authority and does not enqueue receipts as game events.  It only records
that the core rotation gate admitted the marked action and committed its
resource changes.
"""
from __future__ import annotations

import hashlib
import json

SOURCE_CONTRACT = "static-action-admission-source-v1"
CONTRACT = "action-admission-execution-v1"
PREFIX = "@action_admission:"
MAX_ACTION_ADMISSION_RECEIPTS = 40000
MAX_ACTION_ADMISSION_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_NODES = 2048


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def bound(ability):
    return "action_admission_source" in ability


def source(ability):
    return ability["action_admission_source"]


def certificate_id(action_id):
    return PREFIX + fingerprint([CONTRACT, action_id])


def capabilities():
    return {
        "contract": CONTRACT,
        "source_contract": SOURCE_CONTRACT,
        "phase": "action_admission",
        "queued": False,
        "listener": False,
        "limits": {
            "max_receipts": MAX_ACTION_ADMISSION_RECEIPTS,
            "max_receipt_bytes": MAX_ACTION_ADMISSION_RECEIPT_BYTES,
            "max_receipt_nodes": MAX_RECEIPT_NODES,
        },
        "semantics": "仅证明核心动作门禁和资源状态已提交；不证明命中、附着、产球或伤害。",
    }


def namespace_conflicts(value, path=""):
    """Reject the private receipt namespace only in typed identities/references."""
    from .automatic_energy import _IDENTITIES, _REFERENCES

    found = []

    def visit(node, location):
        if isinstance(node, dict):
            for key, child in node.items():
                where = f"{location}.{key}" if location else key
                if (
                    key in _IDENTITIES
                    or key
                    in {
                        "binding_id",
                        "group",
                        "character",
                        "switch_to",
                        "initial_frontline",
                    }
                ) and isinstance(child, str) and child.startswith(PREFIX):
                    found.append(where)
                elif key in _REFERENCES and isinstance(child, list):
                    found.extend(
                        f"{where}[{index}]"
                        for index, item in enumerate(child)
                        if isinstance(item, str) and item.startswith(PREFIX)
                    )
                if isinstance(child, (dict, list)):
                    visit(child, where)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{location}[{index}]")

    visit(value, path)
    return found


def _certificate_reference(receipt):
    return {
        "contract": CONTRACT,
        "certificate_id": receipt["id"],
        "run_id": receipt["run_id"],
    }


def mark_parent(row, event, abilities, status, reason=None, receipt=None):
    if event["kind"] != "action":
        return
    ability = abilities[event["data"]["ability_id"]]
    if not bound(ability):
        return
    marker = source(ability)
    admission = {
        "contract": CONTRACT,
        "binding_id": marker["binding_id"],
        "source_fingerprint": marker["source_fingerprint"],
        "static_certificate_fingerprint": marker["static_certificate_fingerprint"],
        "status": status,
    }
    if receipt is not None:
        admission["certificate"] = _certificate_reference(receipt)
    if reason:
        admission["reason"] = reason
    row.update(
        action_id=event["id"],
        ability_id=ability["id"],
        character=ability["character"],
        action_admission=admission,
    )


def audit_row(
    event,
    ability,
    cycle,
    *,
    frontline_before,
    frontline_after,
    energy_before,
    energy_after,
    charges_before,
    charges_after,
    next_ready_at,
):
    marker = source(ability)
    identity = certificate_id(event["id"])
    energy_rule = ability.get("energy")
    cooldown = ability.get("cooldown")
    row = {
        "id": identity,
        "run_id": f"{event['origin']}:{identity}",
        "contract": CONTRACT,
        "phase": "action_admission",
        "parent_run_id": event["run_id"],
        "binding_id": marker["binding_id"],
        "model_ref": marker["model_ref"],
        "source_fingerprint": marker["source_fingerprint"],
        "static_certificate_fingerprint": marker["static_certificate_fingerprint"],
        "actor": ability["character"],
        "ability_id": ability["id"],
        "action_id": event["id"],
        "action_type_id": ability["action_type_id"],
        "variant": ability.get("variant"),
        "cycle": cycle,
        "source_cycle": event["origin"],
        "t": event["time"] / 1_000_000,
        "gates": {
            "frontline": "passed",
            "occupation": "passed",
            "energy": "passed",
            "cooldown": "passed",
        },
        "energy_before": energy_before,
        "energy_after": energy_after,
        "energy_required": energy_rule.get("required") if energy_rule else 0,
        "energy_amount": energy_rule.get("amount") if energy_rule else 0,
        "charges_before": charges_before,
        "charges_after": charges_after,
        "next_ready_at": next_ready_at,
        "cooldown_group": cooldown.get("group") if cooldown else None,
        "frontline_before": frontline_before,
        "frontline_after": frontline_after,
        "status": "committed",
    }
    return row


class AuditLimit(Exception):
    def __init__(self, count, size, *, reason="receipt_budget"):
        self.issue = {
            "code": "action_admission_receipt_limit",
            "severity": "pending",
            "message": "动作接纳证书达到上限，下一动作未提交；有限前缀不能作为完整结果。",
            "path": "rotation_model",
            "attempted_count": count,
            "attempted_bytes": size,
            "reason": reason,
            **capabilities()["limits"],
        }
        super().__init__(self.issue["message"])


class Ledger:
    def __init__(self, expanded, abilities, horizon):
        self.planned = sum(
            event["kind"] == "action"
            and event["time"] < horizon
            and bound(abilities[event["data"]["ability_id"]])
            for event in expanded
        )
        self.count = 0
        self.bytes = 2
        self.receipts = []

    def _guard(self, value):
        nodes = 0
        text_bytes = 0
        remaining = max(0, MAX_ACTION_ADMISSION_RECEIPT_BYTES - self.bytes)

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
                if len(node) > remaining - text_bytes:
                    raise AuditLimit(
                        self.count + 1, self.bytes + text_bytes + len(node)
                    )
                text_bytes += len(node.encode("utf-8"))
                if text_bytes > remaining:
                    raise AuditLimit(self.count + 1, self.bytes + text_bytes)

        visit(value)

    def prepare(self, event, ability, cycle, **state):
        if self.count + 1 > MAX_ACTION_ADMISSION_RECEIPTS:
            raise AuditLimit(self.count + 1, self.bytes)
        self._guard(
            (
                event["id"],
                event["run_id"],
                ability["id"],
                ability["character"],
                ability.get("variant"),
                source(ability),
                state,
            )
        )
        row = audit_row(event, ability, cycle, **state)
        return row, self.reserve(row)

    def reserve(self, row):
        if self.count + 1 > MAX_ACTION_ADMISSION_RECEIPTS:
            raise AuditLimit(self.count + 1, self.bytes)
        self._guard(row)
        size = self.bytes + len(canonical(row)) + bool(self.count)
        if size > MAX_ACTION_ADMISSION_RECEIPT_BYTES:
            raise AuditLimit(self.count + 1, size)
        return size

    def commit(self, row, size):
        self.receipts.append(row)
        self.count += 1
        self.bytes = size

    def finish(self, result, termination=None):
        parents = [
            row
            for row in result["event_log"]
            if row.get("type") == "action" and "action_admission" in row
        ]
        committed = sum(
            row["action_admission"]["status"] == "committed" for row in parents
        )
        rejected = sum(
            row["action_admission"]["status"] == "rejected" for row in parents
        )
        not_committed = sum(
            row["action_admission"]["status"] == "not_committed" for row in parents
        )
        not_verified = sum(
            row["action_admission"]["status"] == "not_verified" for row in parents
        )
        reported = committed + rejected + not_committed + not_verified
        result["action_admission_execution"] = {
            "contract": CONTRACT,
            "count_scope": "reported_action_rows",
            "planned_actions": self.planned,
            "reported_actions": reported,
            "unreported_actions": self.planned - reported,
            "committed_actions": committed,
            "rejected_actions": rejected,
            "not_committed_actions": not_committed,
            "not_verified_actions": not_verified,
            "receipts": self.receipts,
            "receipt_count": self.count,
            "receipts_fingerprint": fingerprint(self.receipts),
            "receipt_bytes": self.bytes,
            "limits": capabilities()["limits"],
        }
        if termination:
            result["action_admission_execution"]["termination"] = termination


__all__ = [
    "CONTRACT",
    "PREFIX",
    "SOURCE_CONTRACT",
    "AuditLimit",
    "Ledger",
    "bound",
    "capabilities",
    "certificate_id",
    "fingerprint",
    "mark_parent",
    "namespace_conflicts",
]
