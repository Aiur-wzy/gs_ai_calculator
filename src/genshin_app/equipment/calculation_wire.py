"""Canonical calculation writers shared by publication and fresh capacity checks.

This module performs no validation, repair, source read or calculation.  A
producer may measure its private candidate; a delivery writer supplies the
already-validated canonical bytes and integrity record without reserializing.
"""
from __future__ import annotations

import hashlib

from .files import fingerprint, json_bytes

PACKAGE_SCHEMA = "genshin.calculated-package.v1"
PACKAGE_KIND = "historical_calculation_snapshot"
_HASHED_MEMBERS = {
    "report_fingerprint": "report",
    "display_context_fingerprint": "display_context",
    "equipment_summary_fingerprint": "equipment_summary",
}


def package_integrity(calculated, encoded):
    return {"calculated_fingerprint": hashlib.sha256(encoded).hexdigest(),
            **{field: fingerprint(calculated.get(member)) for field, member in _HASHED_MEMBERS.items()}}


def calculated_package_bytes(calculated, *, encoded=None, integrity=None):
    if encoded is None:
        encoded = json_bytes(calculated)
    if integrity is None:
        integrity = package_integrity(calculated, encoded)
    producer = {"app_version": calculated["result_receipt"]["engine_version"],
                "composer_version": calculated["report"]["composer_version"]}
    tail = {"integrity": integrity, "kind": PACKAGE_KIND,
            "package_schema": PACKAGE_SCHEMA, "producer": producer}
    return b'{"calculated":' + encoded + b',' + json_bytes(tail)[1:]


def _result_envelope(result, receipt, equipment_summary):
    envelope = {"result": result, "result_receipt": receipt,
                **({"equipment_summary": equipment_summary} if equipment_summary else {})}
    if receipt.get("provisional_entity_usage_receipt") is not None:
        from genshin_app.export import (
            PROVISIONAL_RESULT_FIDELITY,
            PROVISIONAL_RESULT_LABEL,
        )
        envelope["export_metadata"] = {
            "result_fidelity": PROVISIONAL_RESULT_FIDELITY,
            "human_label": PROVISIONAL_RESULT_LABEL,
            "scope": "current_plan_only", "database_authority": False}
    return envelope


def result_only_bytes(result, receipt, equipment_summary=None):
    return json_bytes(_result_envelope(result, receipt, equipment_summary))


def _object_size(value, result_size):
    """Count canonical object punctuation and member encodings."""
    return 2 + max(0, len(value) - 1) + sum(
        len(json_bytes(key)) + 1 + (result_size if key == "result" else len(json_bytes(member)))
        for key, member in value.items()
    )


def calculation_wire_sizes(calculated):
    """Exact canonical writer lengths, including each role's wrapper."""
    result = calculated["result"]
    result_size = len(json_bytes(result))
    result_envelope = _result_envelope(result, calculated["result_receipt"], calculated["equipment_summary"])
    # Every canonical SHA-256 field is an ASCII string of exactly 64 bytes.
    integrity = dict.fromkeys(("calculated_fingerprint", *_HASHED_MEMBERS), "0" * 64)
    wrapper_size = len(calculated_package_bytes(calculated, encoded=b"", integrity=integrity))
    return {"calculated": wrapper_size + _object_size(calculated, result_size),
            "result_only": _object_size(result_envelope, result_size)}
