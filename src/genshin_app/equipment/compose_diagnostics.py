"""Ordered diagnostic collector used by composition passes."""

from __future__ import annotations

from typing import Any

from genshin_app.diagnostic_facts import diagnostic_facts

EFFECT_BINDING_REPAIR_HINT = (
    "先用gs_compose重新组合，再用gs_read_context读取报告的/effect_requirements，按本条binding_id定位"
    "proposed_binding和source_fingerprint。核对来源后分项材料化收益或记录有依据的不计；"
    "binding.source_fingerprint与coverage.source_fingerprint均取当前requirement，"
    "不要与属性采用合同中的组件source_fingerprint等混用。按需参阅references/app-mechanics/loadouts.md。"
)


class ComposeIssueCollector:
    """Append one issue without reordering or normalizing legacy producers."""

    def __init__(self, issues: list[dict[str, Any]]):
        self._issues = issues

    def __call__(
        self,
        code,
        path,
        message,
        action="review_effect",
        binding_id=None,
        part_id=None,
        *,
        category=None,
        consumer=None,
        required=None,
        consumed=None,
        impact=None,
        blocking_scope=None,
        retryable=None,
        repair_hint=None,
        diagnostics=None,
    ) -> None:
        row = {
            "code": code,
            "path": path,
            "binding_id": binding_id,
            "part_id": part_id,
            "message": message,
            "action": action,
        }
        # Legacy producers intentionally omit task metadata and remain
        # conservatively required+consumed. Migrated producers opt in without
        # changing this ordered collector.
        for key, value in {
            "category": category,
            "consumer": consumer,
            "required": required,
            "consumed": consumed,
            "impact": impact,
            "blocking_scope": blocking_scope,
            "retryable": retryable,
            "repair_hint": repair_hint,
        }.items():
            if value is not None:
                row[key] = value
        self._issues.append(row)
        row.update(diagnostic_facts(diagnostics))
        if repair_hint == EFFECT_BINDING_REPAIR_HINT and "recovery" not in row:
            row["recovery"] = {"action": "inspect_effect_binding_requirements", "retry_same_request": False}


__all__ = ["EFFECT_BINDING_REPAIR_HINT", "ComposeIssueCollector"]
