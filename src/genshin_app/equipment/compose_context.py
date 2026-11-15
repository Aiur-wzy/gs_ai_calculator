"""The one private work context shared by static composition passes."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .compose_diagnostics import ComposeIssueCollector
from .compose_inputs import normalize_compose_input
from .files import context_view, fingerprint, input_view


@dataclass(slots=True)
class ComposeContext:
    """Own the working plan, ordered diagnostics and accumulated source artifacts.

    All passes receive this single private context. Artifact wrappers may refer
    to its containers but never introduce another mutable plan or DB session.
    """

    plan: dict[str, Any]
    original_plan: dict[str, Any]
    template: dict[str, Any]
    issues: list[dict[str, Any]]
    warnings: list[Any]
    assumptions: list[str]
    requirements: list[Any]
    sources: list[Any]
    versions: list[Any]
    preview: dict[str, Any]
    bases: dict[str, Any]
    contributions: dict[str, Any]
    fixed_panels: dict[str, Any]
    panel_bases: dict[str, Any]
    context_fp: str
    missing: ComposeIssueCollector


def initialize_compose_context(plan: dict[str, Any]) -> ComposeContext:
    # Equipment is a role-to-gear map, not an ordered gameplay declaration.
    # The normalizer follows the explicit roster and preserves the exact old
    # replay-basis copy policy for plans which are rewritten downstream.
    plan, original_plan = normalize_compose_input(plan)
    template = copy.deepcopy(plan["manual_template"])
    issues, warnings, assumptions, requirements, sources, versions = [], [], [], [], [], []
    preview = {"characters": {}, "generated_outputs": [], "equipment_sources": {}}
    bases, contributions, fixed_panels, panel_bases = {}, {}, {}, {}
    context_fp = fingerprint(context_view(input_view(plan)))

    missing = ComposeIssueCollector(issues)

    return ComposeContext(
        plan=plan,
        original_plan=original_plan,
        template=template,
        issues=issues,
        warnings=warnings,
        assumptions=assumptions,
        requirements=requirements,
        sources=sources,
        versions=versions,
        preview=preview,
        bases=bases,
        contributions=contributions,
        fixed_panels=fixed_panels,
        panel_bases=panel_bases,
        context_fp=context_fp,
        missing=missing,
    )


__all__ = ["ComposeContext", "initialize_compose_context"]

