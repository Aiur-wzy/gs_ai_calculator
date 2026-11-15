"""Orthogonal candidate state reduction for Harness V3."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from ..candidate_readiness import readiness_projection

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
APPLICATION_STATES = frozenset({
    "none", "prepared", "baseline_advanced", "committed", "aborted",
    "outcome_unknown",
})


class CandidateContractError(ValueError):
    pass


def derive_candidate_state(
    *,
    checkpoint: Mapping[str, Any] | None,
    summary: Mapping[str, Any] | None = None,
    application_state: str = "none",
    database_current: bool | None = None,
    binding_conflict: bool = False,
    review_authority_verified: bool = False,
) -> dict[str, Any]:
    """Reduce existing durable facts without treating existence as reviewability."""

    checkpoint = {} if checkpoint is None else copy.deepcopy(dict(checkpoint))
    summary = {} if summary is None else copy.deepcopy(dict(summary))
    if application_state not in APPLICATION_STATES:
        raise CandidateContractError("candidate_application_state_invalid")
    revision = checkpoint.get("candidate_revision", summary.get("revision", 0))
    if type(revision) is not int or revision < 0:
        raise CandidateContractError("candidate_revision_invalid")
    receipt = checkpoint.get("receipt_fingerprint", summary.get("receipt_fingerprint"))
    if receipt is not None and (
        not isinstance(receipt, str) or _DIGEST.fullmatch(receipt) is None
    ):
        raise CandidateContractError("candidate_receipt_invalid")
    discarded = summary.get("discarded") is True
    superseded = summary.get("superseded") is True
    seal_state = (
        "discarded" if discarded
        else "superseded" if superseded
        else "sealed" if receipt is not None
        else "unsealed"
    )
    scope = readiness_projection(summary)
    assessment = scope["assessment"]
    if database_current is None:
        raw_current = summary.get("database_current")
        database_current = raw_current if type(raw_current) is bool else True
    freshness = "fresh" if database_current else "stale"
    binding_state = "conflict" if binding_conflict else "aligned"
    checks = {
        "receipt": receipt is not None,
        "schema": review_authority_verified,
        "database": database_current is True,
        "document_lineage": not binding_conflict and review_authority_verified,
    }
    reviewable = (
        seal_state == "sealed"
        and freshness == "fresh"
        and binding_state == "aligned"
        and application_state == "none"
        and all(checks.values())
    )
    issues: list[dict[str, Any]] = []
    if seal_state == "unsealed" and revision > 0:
        issues.append({
            "code": "candidate_unsealed",
            "severity": "info",
            "impact_domain": "candidate_review",
        })
    if freshness == "stale":
        issues.append({
            "code": "candidate_database_stale",
            "severity": "error",
            "impact_domain": "candidate_review",
        })
    if binding_state == "conflict":
        issues.append({
            "code": "candidate_document_conflict",
            "severity": "error",
            "impact_domain": "candidate_application",
        })
    if seal_state == "sealed" and not review_authority_verified:
        issues.append({
            "code": "candidate_review_authority_unverified",
            "severity": "warning",
            "impact_domain": "candidate_review",
        })
    return {
        "revision": revision,
        "draft_present": revision > 0,
        "seal_state": seal_state,
        "assessment": assessment,
        "representation": scope["representation"],
        "application_kind": scope["application_kind"],
        "ready_scope": scope["ready_scope"],
        "freshness": freshness,
        "binding_state": binding_state,
        "application_state": application_state,
        "receipt_ref": receipt,
        "review_checks": checks,
        "reviewable": reviewable,
        "issues": issues,
    }


__all__ = ["CandidateContractError", "derive_candidate_state"]
