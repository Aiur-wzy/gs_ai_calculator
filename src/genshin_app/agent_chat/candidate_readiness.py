"""Read-only scope of existing candidate facts; no new admission authority."""
from collections.abc import Mapping
from typing import Any


def readiness_projection(summary: Mapping[str, Any]) -> dict[str, Any]:
    application_kind = summary.get("application_kind")
    representation = summary.get("representation")
    if representation not in ("whiteboard", "native"):
        representation = "whiteboard" if application_kind == "whiteboard_draft" else "native"
    draft = representation == "whiteboard" or application_kind == "whiteboard_draft"
    acceptance = summary.get("calculation_acceptance")
    status = acceptance.get("status") if isinstance(acceptance, Mapping) else None
    has_calculation = summary.get("has_calculation") is True
    # Legacy calculation producers already validate readiness; artifact adds
    # an explicit acceptance fact. A seal alone proves neither of these.
    formal = has_calculation and summary.get("ready") is True and (status is None or status == "accepted")
    assessment = ("draft" if draft else "formal" if formal else "diagnostic"
                  if has_calculation or status is not None else "unknown")
    return {
        "representation": representation,
        "application_kind": application_kind,
        "ready_scope": "draft_sync" if draft and summary.get("ready") is True else "formal_result" if formal else "none",
        "assessment": assessment,
    }
