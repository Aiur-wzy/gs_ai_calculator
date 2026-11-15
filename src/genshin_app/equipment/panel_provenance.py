"""Optional authorship and construction evidence, never a panel authority."""
from __future__ import annotations

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "origin": {"enum": ["user_panel", "agent_constructed", "unknown"]},
        "entered_by": {"enum": ["user", "agent", "unknown"]},
        "note": {"type": "string"},
        "reference_request": {"type": "object"},
    },
    "required": ["origin", "entered_by"],
}


def provenance_of(value):
    """Legacy presence in the GUI is not evidence that the user chose values."""
    record = value.get("panel_provenance")
    return record if isinstance(record, dict) else {"origin": "unknown", "entered_by": "unknown"}
