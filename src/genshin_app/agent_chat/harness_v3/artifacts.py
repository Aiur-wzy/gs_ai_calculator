"""Extensible artifact registry and dependency freshness reduction.

Artifact state is deliberately orthogonal.  ``blocked`` is never persisted as
an artifact state: command blockers are derived later by ``projection.py``.
The registry is mechanism-neutral and must not contain character-specific
rules.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_KIND = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")
_ARTIFACT_ID = re.compile(r"[a-z][a-z0-9_]{0,95}:[A-Za-z0-9_.-]{1,160}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

PRESENCE_STATES = frozenset({"absent", "draft", "present"})
VALIDATION_STATES = frozenset({"unchecked", "valid", "invalid"})
FRESHNESS_STATES = frozenset({"fresh", "stale"})
COMPLETENESS_STATES = frozenset({"unknown", "partial", "complete"})


class ArtifactContractError(ValueError):
    """Raised when a Host artifact fact or registry entry is malformed."""


def _kind(value: object, field: str = "kind") -> str:
    if not isinstance(value, str) or _KIND.fullmatch(value) is None:
        raise ArtifactContractError(f"{field}_invalid")
    return value


def _revision(value: object, field: str = "revision") -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ArtifactContractError(f"{field}_invalid")
    return value


@dataclass(frozen=True, slots=True)
class ArtifactKindSpec:
    """One registered artifact kind.

    Dependencies are defaults for new artifacts.  A concrete artifact records
    the exact dependency revisions/fingerprints it consumed, so invalidation
    follows real edges rather than a global phase reset.
    """

    kind: str
    schema_version: int
    required_dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    validator_id: str = "host_schema"

    def __post_init__(self) -> None:
        _kind(self.kind)
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ArtifactContractError("schema_version_invalid")
        required = tuple(_kind(item, "dependency_kind") for item in self.required_dependencies)
        optional = tuple(_kind(item, "dependency_kind") for item in self.optional_dependencies)
        if len(set(required)) != len(required) or len(set(optional)) != len(optional):
            raise ArtifactContractError("dependency_kind_duplicate")
        if set(required) & set(optional) or self.kind in {*required, *optional}:
            raise ArtifactContractError("dependency_kind_cycle")
        _kind(self.validator_id, "validator_id")


class ArtifactRegistry:
    """Append-only-in-use registry with cycle validation and graph queries."""

    def __init__(self, specs: Iterable[ArtifactKindSpec] = ()) -> None:
        self._specs: dict[str, ArtifactKindSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ArtifactKindSpec) -> None:
        if not isinstance(spec, ArtifactKindSpec):
            raise ArtifactContractError("artifact_spec_invalid")
        if spec.kind in self._specs:
            raise ArtifactContractError("artifact_kind_exists")
        proposed = {**self._specs, spec.kind: spec}
        for row in proposed.values():
            unknown = {
                *row.required_dependencies,
                *row.optional_dependencies,
            } - proposed.keys()
            # Forward declarations are not accepted.  This makes deployment
            # ordering explicit and prevents a typo from silently disabling
            # invalidation.
            if unknown:
                raise ArtifactContractError("artifact_dependency_unknown")
        self._assert_acyclic(proposed)
        self._specs[spec.kind] = spec

    @staticmethod
    def _assert_acyclic(specs: Mapping[str, ArtifactKindSpec]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(kind: str) -> None:
            if kind in visited:
                return
            if kind in visiting:
                raise ArtifactContractError("artifact_dependency_cycle")
            visiting.add(kind)
            spec = specs[kind]
            for dependency in (*spec.required_dependencies, *spec.optional_dependencies):
                visit(dependency)
            visiting.remove(kind)
            visited.add(kind)

        for kind in specs:
            visit(kind)

    def get(self, kind: str) -> ArtifactKindSpec:
        kind = _kind(kind)
        try:
            return self._specs[kind]
        except KeyError:
            raise ArtifactContractError("artifact_kind_unknown") from None

    def specifications(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": spec.kind,
                "schema_version": spec.schema_version,
                "required_dependencies": list(spec.required_dependencies),
                "optional_dependencies": list(spec.optional_dependencies),
                "validator_id": spec.validator_id,
            }
            for spec in self._specs.values()
        ]

    def descendants(self, changed_kinds: Iterable[str]) -> set[str]:
        pending = {_kind(item, "changed_kind") for item in changed_kinds}
        descendants: set[str] = set()
        while pending:
            changed = pending.pop()
            for spec in self._specs.values():
                if spec.kind in descendants:
                    continue
                if changed in {
                    *spec.required_dependencies,
                    *spec.optional_dependencies,
                }:
                    descendants.add(spec.kind)
                    pending.add(spec.kind)
        return descendants


def default_artifact_registry() -> ArtifactRegistry:
    """Return the initial mechanism-neutral artifact graph.

    This is a registry factory, not a closed enum.  Future mechanism families
    add kinds through registrations instead of expanding an ``if/elif`` chain.
    """

    registry = ArtifactRegistry()
    for spec in (
        ArtifactKindSpec("task_contract", 1),
        ArtifactKindSpec("user_constraints", 1, ("task_contract",)),
        ArtifactKindSpec("database_binding", 1),
        ArtifactKindSpec(
            "loadout", 1,
            ("task_contract", "user_constraints", "database_binding"),
        ),
        ArtifactKindSpec(
            "action_graph", 1, ("task_contract", "user_constraints"),
            ("loadout",),
        ),
        ArtifactKindSpec(
            "mechanic_materialization", 1,
            ("action_graph",), ("database_binding", "loadout"),
        ),
        ArtifactKindSpec(
            "event_topology", 1,
            ("action_graph",), ("loadout", "mechanic_materialization"),
        ),
        ArtifactKindSpec(
            "numeric_bindings", 1,
            ("loadout", "event_topology", "database_binding"),
        ),
        ArtifactKindSpec(
            "energy_proof", 1,
            ("loadout", "action_graph", "event_topology"),
        ),
        ArtifactKindSpec(
            "calculation_attempt", 1,
            ("loadout", "action_graph", "event_topology", "numeric_bindings"),
            ("energy_proof",),
        ),
        ArtifactKindSpec("calculation_review", 1, ("calculation_attempt",)),
        ArtifactKindSpec("report", 1, ("calculation_review",)),
    ):
        registry.register(spec)
    return registry


def _issues(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 1024:
        raise ArtifactContractError("artifact_issues_invalid")
    result: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ArtifactContractError("artifact_issue_invalid")
        code = _kind(raw.get("code"), "issue_code")
        severity = raw.get("severity", "warning")
        if severity not in {"info", "warning", "error"}:
            raise ArtifactContractError("artifact_issue_severity_invalid")
        result.append({
            "code": code,
            "severity": severity,
            "source": raw.get("source") if isinstance(raw.get("source"), str) else "host",
            "impact_domain": (
                raw.get("impact_domain")
                if isinstance(raw.get("impact_domain"), str)
                else "artifact"
            ),
            **(
                {"details": copy.deepcopy(raw["details"])}
                if "details" in raw
                else {}
            ),
        })
    return result


def normalize_artifact_fact(value: Mapping[str, Any], registry: ArtifactRegistry) -> dict[str, Any]:
    """Validate and copy one orthogonal artifact fact."""

    if not isinstance(value, Mapping):
        raise ArtifactContractError("artifact_fact_invalid")
    kind = _kind(value.get("kind"))
    spec = registry.get(kind)
    artifact_id = value.get("artifact_id")
    if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise ArtifactContractError("artifact_id_invalid")
    if not artifact_id.startswith(f"{kind}:"):
        raise ArtifactContractError("artifact_id_kind_mismatch")
    schema_version = value.get("schema_version", spec.schema_version)
    if schema_version != spec.schema_version:
        raise ArtifactContractError("artifact_schema_version_invalid")
    revision = _revision(value.get("revision", 0))
    content_fingerprint = value.get("content_fingerprint")
    if content_fingerprint is not None and (
        not isinstance(content_fingerprint, str)
        or _DIGEST.fullmatch(content_fingerprint) is None
    ):
        raise ArtifactContractError("artifact_fingerprint_invalid")
    presence = value.get("presence", "absent")
    validation = value.get("validation", "unchecked")
    freshness = value.get("freshness", "fresh")
    completeness = value.get("completeness", "unknown")
    if presence not in PRESENCE_STATES:
        raise ArtifactContractError("artifact_presence_invalid")
    if validation not in VALIDATION_STATES:
        raise ArtifactContractError("artifact_validation_invalid")
    if freshness not in FRESHNESS_STATES:
        raise ArtifactContractError("artifact_freshness_invalid")
    if completeness not in COMPLETENESS_STATES:
        raise ArtifactContractError("artifact_completeness_invalid")
    dependencies: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_dependencies = value.get("dependencies", [])
    if not isinstance(raw_dependencies, list):
        raise ArtifactContractError("artifact_dependencies_invalid")
    for raw in raw_dependencies:
        if not isinstance(raw, Mapping):
            raise ArtifactContractError("artifact_dependency_invalid")
        dependency_id = raw.get("artifact_id")
        if not isinstance(dependency_id, str) or _ARTIFACT_ID.fullmatch(dependency_id) is None:
            raise ArtifactContractError("artifact_dependency_id_invalid")
        dependency_kind = dependency_id.split(":", 1)[0]
        if dependency_kind not in {
            *spec.required_dependencies,
            *spec.optional_dependencies,
        }:
            raise ArtifactContractError("artifact_dependency_not_registered")
        if dependency_id in seen:
            raise ArtifactContractError("artifact_dependency_duplicate")
        seen.add(dependency_id)
        fingerprint = raw.get("fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or _DIGEST.fullmatch(fingerprint) is None
        ):
            raise ArtifactContractError("artifact_dependency_fingerprint_invalid")
        dependencies.append({
            "artifact_id": dependency_id,
            "revision": _revision(raw.get("revision", 0), "dependency_revision"),
            "fingerprint": fingerprint,
        })
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "schema_version": schema_version,
        "revision": revision,
        "content_fingerprint": content_fingerprint,
        "bindings": copy.deepcopy(value.get("bindings", {})),
        "dependencies": dependencies,
        "presence": presence,
        "validation": validation,
        "freshness": freshness,
        "freshness_causes": list(value.get("freshness_causes", [])),
        "completeness": completeness,
        "issues": _issues(value.get("issues")),
        "source": value.get("source", "host"),
    }


def propagate_artifact_freshness(
    values: Iterable[Mapping[str, Any]],
    registry: ArtifactRegistry,
) -> list[dict[str, Any]]:
    """Propagate stale state through the concrete dependency graph.

    Missing default dependencies do not fabricate edges.  They are reported as
    validation/completeness issues by the producer.  Once a concrete artifact
    declares a consumed dependency, however, revision/fingerprint mismatch or
    stale upstream state deterministically invalidates its freshness.
    """

    facts = [normalize_artifact_fact(value, registry) for value in values]
    by_id = {row["artifact_id"]: row for row in facts}
    if len(by_id) != len(facts):
        raise ArtifactContractError("artifact_id_duplicate")
    changed = True
    while changed:
        changed = False
        for fact in facts:
            causes = list(fact["freshness_causes"])
            for dependency in fact["dependencies"]:
                target = by_id.get(dependency["artifact_id"])
                reason = None
                if target is None or target["presence"] == "absent":
                    reason = "dependency_absent"
                elif target["freshness"] == "stale":
                    reason = "dependency_stale"
                elif target["revision"] != dependency["revision"]:
                    reason = "dependency_revision_changed"
                elif (
                    dependency["fingerprint"] is not None
                    and target["content_fingerprint"] != dependency["fingerprint"]
                ):
                    reason = "dependency_fingerprint_changed"
                if reason is not None:
                    cause = {
                        "code": reason,
                        "artifact_id": dependency["artifact_id"],
                    }
                    if cause not in causes:
                        causes.append(cause)
            if causes and fact["freshness"] != "stale":
                fact["freshness"] = "stale"
                changed = True
            fact["freshness_causes"] = causes
    return copy.deepcopy(facts)


__all__ = [
    "ArtifactContractError",
    "ArtifactKindSpec",
    "ArtifactRegistry",
    "default_artifact_registry",
    "normalize_artifact_fact",
    "propagate_artifact_freshness",
]
