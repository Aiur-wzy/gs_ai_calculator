"""Stable rotation boundary used by the mechanics coordinator.

The rotation module predates the mechanics runtime and historically exposed its
implementation through underscore-prefixed helpers.  This module is the only
compatibility boundary allowed to depend on those helpers while the rotation
runtime is migrated behind a compiled-program API.  Callers outside the
rotation subsystem must use the names exported here.

The wrappers intentionally preserve object identity, exception behaviour and
ordering.  They do not add a second scheduler or reinterpret rotation data.
"""

from __future__ import annotations

from typing import Any

from genshin_engine import rotation as _rotation

TICKS = _rotation.TICKS


def rotation_issue(code, message, path="", severity="error", **details):
    return _rotation._issue(code, message, path, severity, **details)


def collect_schema_errors(value, schema, path, issues):
    return _rotation._schema_errors(value, schema, path, issues)


def analyze_rotation(model, template):
    """Return the current semantic context and normalized rotation data."""

    return _rotation._semantic(model, template)


def repeat_origins(repeat, cycles):
    return _rotation._origins(repeat, cycles)


def expand_rotation(data, model, context):
    """Expand one analyzed model without changing its existing event IDs."""

    return _rotation._expand(data, model, context)


def rotation_result(status, issues=None, assumptions=None):
    return _rotation._base_result(status, issues, assumptions)


def event_sort_key(event):
    return _rotation._event_sort_key(event)


def run_rotation(
    model: dict[str, Any],
    data: dict[str, Any],
    expanded: list[dict[str, Any]],
    windows: Any,
    finite_until: Any,
    context: Any,
    *,
    observer: Any = None,
):
    """Run the single authoritative rotation scheduler.

    ``observer`` may request derived events through the existing rotation
    contract, but this function remains the only owner of global event order.
    """

    return _rotation._simulate(
        model,
        data,
        expanded,
        windows,
        finite_until,
        context,
        observer=observer,
    )


validate_rotation_model = _rotation.validate_rotation_model


__all__ = [
    "TICKS",
    "analyze_rotation",
    "collect_schema_errors",
    "event_sort_key",
    "expand_rotation",
    "repeat_origins",
    "rotation_issue",
    "rotation_result",
    "run_rotation",
    "validate_rotation_model",
]
