"""Public equipment snapshot identity projection.

Both the composer and panel-basis reader consume this small contract.  Keeping
it outside the composer prevents read-only services from importing a private
function from the orchestration module.
"""

from __future__ import annotations

from typing import Any

from .schema import PlanError


def equipment_data_version(meta: dict[str, Any], version_line: str) -> dict[str, Any]:
    status = (
        meta.get("datasets", {})
        .get("equipment", {})
        .get("lines", {})
        .get(version_line, {})
    )
    if status.get("state") in ("invalid", "incompatible"):
        raise PlanError(
            f"装备资料 {version_line} 损坏或结构版本不受支持",
            "dataset_invalid" if status["state"] == "invalid" else "unsupported_schema",
            status,
        )
    if status.get("state") != "ready":
        raise PlanError(
            f"装备资料 {version_line} 尚未就绪",
            "equipment_unavailable",
            status,
        )
    return {
        "version_line": version_line,
        "upstream_commit": status["upstream_commit"],
        "equipment_schema_version": status["schema_version"],
        "built_at": status["built_at"],
    }


__all__ = ["equipment_data_version"]
