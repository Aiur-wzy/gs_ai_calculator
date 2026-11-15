"""Shared task-scoped capability issue contract.

Database/App release compatibility is checked separately.  This module asks a
narrower question: does the current effective graph consume this missing
input?  Legacy producers remain conservatively required and consumed until
they opt into the explicit contract.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .domain_errors import normalize_issue

CONTRACT = "genshin-task-capability-issues-v1"
_CATEGORIES = frozenset({
    "request_error", "core_data_missing", "knowledge_missing",
    "engine_capability_missing", "release_identity_failure",
})
_BLOCKING_SCOPES = frozenset({
    "request", "task_capability", "agent_materialization",
    "engine_capability", "release_identity",
})
_RESOLUTION_AUTHORITIES = frozenset({
    "effective_graph_recomputed",
    "verified_materialization_receipt",
    "verified_override_receipt",
    "verified_public_data_receipt",
    "verified_fixed_event_receipt",
})


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_fingerprint(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


@dataclass(frozen=True, slots=True)
class _TrustedIssueResolution:
    """In-process proof that the App repaired one exact diagnostic.

    This type is deliberately private and cannot be reconstructed from plan
    JSON. Agent input supplies materialized data; the App verifies it,
    rebuilds the effective graph and only then creates this resolution.
    """

    issue_id: str
    authority: str
    resolved_by: str
    proof_fingerprint: str
    superseded_by: str | None = None


@dataclass(frozen=True, slots=True)
class _TrustedEffectiveGraphScope:
    """App-derived consumer state for one exact effective graph."""

    graph_fingerprint: str
    consumers: tuple[tuple[str, bool, bool], ...]


def issue_identity(value: Any) -> str:
    """Return the stable identity of one normalized unresolved condition."""

    row = normalize_task_issue(value)
    return row["issue_id"]


def build_trusted_issue_resolution(
    issue: Any,
    *,
    authority: str,
    resolved_by: str,
    proof_fingerprint: str,
    superseded_by: str | None = None,
) -> object:
    """Create an internal resolution after a receipt has been verified.

    Callers must not pass plan fields here. ``proof_fingerprint`` binds the
    resolution to a verified materialization/override/public-data receipt.
    Similarly named fields embedded in an issue dictionary are ignored.
    """

    if authority not in _RESOLUTION_AUTHORITIES:
        raise ValueError("任务能力修复权威不受支持")
    if not isinstance(resolved_by, str) or not resolved_by.strip() or len(resolved_by) > 256:
        raise ValueError("任务能力修复来源不合法")
    if not _is_fingerprint(proof_fingerprint):
        raise ValueError("任务能力修复证明指纹不合法")
    if superseded_by is not None and (
        not isinstance(superseded_by, str)
        or not superseded_by.strip()
        or len(superseded_by) > 256
    ):
        raise ValueError("任务能力替代消费者不合法")
    return _TrustedIssueResolution(
        issue_id=issue_identity(issue),
        authority=authority,
        resolved_by=resolved_by.strip(),
        proof_fingerprint=proof_fingerprint,
        superseded_by=superseded_by.strip() if superseded_by is not None else None,
    )


def build_trusted_effective_graph_scope(
    consumers: Iterable[Mapping[str, Any]], *, graph_fingerprint: str,
) -> object:
    """Freeze App-derived required/consumed states for one effective graph.

    The result is an in-process capability object, not a serializable Agent
    field. Legacy consumers absent from this scope remain conservative until
    their producer is migrated.
    """

    if not _is_fingerprint(graph_fingerprint):
        raise ValueError("有效图指纹不合法")
    rows: list[tuple[str, bool, bool]] = []
    seen: set[str] = set()
    for raw in consumers:
        if not isinstance(raw, Mapping):
            raise TypeError("有效图消费者不是对象")
        consumer = raw.get("consumer")
        required = raw.get("required")
        consumed = raw.get("consumed")
        if (
            not isinstance(consumer, str)
            or not consumer.strip()
            or len(consumer) > 128
            or type(required) is not bool
            or type(consumed) is not bool
        ):
            raise ValueError("有效图消费者状态不合法")
        consumer = consumer.strip()
        if consumer in seen:
            raise ValueError("有效图消费者身份重复")
        seen.add(consumer)
        rows.append((consumer, required, consumed))
    return _TrustedEffectiveGraphScope(
        graph_fingerprint=graph_fingerprint,
        consumers=tuple(sorted(rows)),
    )


def normalize_task_issue(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        value = {"code": "task_capability_issue", "message": str(value)}
    raw = copy.deepcopy(dict(value))
    normalized = normalize_issue(raw)
    consumer = raw.get("consumer")
    if not isinstance(consumer, str) or not consumer.strip():
        consumer = "legacy_effective_graph"
    required = raw.get("required", True)
    consumed = raw.get("consumed", True)
    impact = raw.get("impact", "high" if required and consumed else "informational")
    if type(required) is not bool or type(consumed) is not bool:
        required = consumed = True
    if impact not in {"informational", "low", "medium", "high", "critical"}:
        impact = "high" if required and consumed else "informational"
    category = normalized.get("category")
    if category not in _CATEGORIES:
        category = "request_error"
    blocking_scope = normalized.get("blocking_scope")
    if blocking_scope not in _BLOCKING_SCOPES:
        blocking_scope = "request"
    retryable = normalized.get("retryable")
    if type(retryable) is not bool:
        retryable = False
    result = {
        "code": str(raw.get("code") or raw.get("reason") or "task_capability_issue")[:128],
        "path": str(raw.get("path") or raw.get("field") or "")[:512],
        "message": str(raw.get("message") or raw.get("code") or value)[:2048],
        "category": category,
        "consumer": consumer[:128],
        "required": required,
        "consumed": consumed,
        "impact": impact,
        "blocking_scope": blocking_scope,
        "retryable": retryable,
        "repair_hint": str(raw.get("repair_hint") or raw.get("action") or "")[:1024],
    }
    # Plan/Agent supplied ``resolved`` or ``resolved_by`` fields are
    # intentionally not copied. Only an in-process trusted resolution can
    # change blocking status.
    result["issue_id"] = _fingerprint({
        key: result[key]
        for key in ("code", "path", "message", "category", "consumer")
    })
    return result


def classify_task_issues(
    values: Iterable[Any] | None,
    *,
    trusted_resolutions: Iterable[object] | None = None,
    effective_graph_scope: object | None = None,
) -> dict[str, Any]:
    rows = [normalize_task_issue(item) for item in (values or [])]
    graph_fingerprint = None
    if effective_graph_scope is not None:
        if not isinstance(effective_graph_scope, _TrustedEffectiveGraphScope):
            raise TypeError("有效图消费状态必须由App重建")
        graph_fingerprint = effective_graph_scope.graph_fingerprint
        scope_by_consumer = {
            consumer: (required, consumed)
            for consumer, required, consumed in effective_graph_scope.consumers
        }
        for row in rows:
            state = scope_by_consumer.get(row["consumer"])
            if state is not None:
                row["required"], row["consumed"] = state
                if not (state[0] and state[1]):
                    row["impact"] = "informational"

    resolution_by_issue: dict[str, _TrustedIssueResolution] = {}
    for value in trusted_resolutions or ():
        if not isinstance(value, _TrustedIssueResolution):
            raise TypeError("任务能力修复必须来自App验证回执")
        if value.issue_id in resolution_by_issue:
            raise ValueError("任务能力修复身份重复")
        resolution_by_issue[value.issue_id] = value
    known = {row["issue_id"] for row in rows}
    if set(resolution_by_issue) - known:
        raise ValueError("任务能力修复与当前缺项不一致")
    for issue_id in resolution_by_issue:
        if sum(row["issue_id"] == issue_id for row in rows) != 1:
            raise ValueError("任务能力修复目标不唯一")

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row in rows:
        resolution = resolution_by_issue.get(row["issue_id"])
        if resolution is None:
            unresolved.append(row)
            continue
        repaired = copy.deepcopy(row)
        repaired["resolution"] = {
            "authority": resolution.authority,
            "resolved_by": resolution.resolved_by,
            "proof_fingerprint": resolution.proof_fingerprint,
            "superseded_by": resolution.superseded_by,
        }
        resolved.append(repaired)

    blocking = [row for row in unresolved if row["required"] and row["consumed"]]
    degraded = [row for row in unresolved if row not in blocking]
    return {
        "contract": CONTRACT,
        "status": "blocked" if blocking else ("degraded" if degraded else "ready"),
        "issues": rows,
        "unresolved": unresolved,
        "resolved": resolved,
        "blocking": blocking,
        "degraded": degraded,
        "derivation": {
            "consumer_authority": (
                "effective_graph" if effective_graph_scope is not None else "producer_or_legacy"
            ),
            "effective_graph_fingerprint": graph_fingerprint,
            "resolution_authority": "app_verified_receipts_only",
        },
        "counts": {
            "total": len(rows),
            "blocking": len(blocking),
            "degraded": len(degraded),
        },
    }


def blocking_task_issues(
    values: Iterable[Any] | None,
    *,
    trusted_resolutions: Iterable[object] | None = None,
    effective_graph_scope: object | None = None,
) -> list[dict[str, Any]]:
    return classify_task_issues(
        values,
        trusted_resolutions=trusted_resolutions,
        effective_graph_scope=effective_graph_scope,
    )["blocking"]


__all__ = [
    "CONTRACT", "blocking_task_issues", "build_trusted_effective_graph_scope",
    "build_trusted_issue_resolution", "classify_task_issues", "issue_identity",
    "normalize_task_issue",
]
