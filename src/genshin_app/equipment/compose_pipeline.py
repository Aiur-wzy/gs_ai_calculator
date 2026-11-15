"""Typed orchestration primitives for equipment composition.

The production composer declares static passes for input normalization, action
preparation, loadout/source compilation, baseline/overlay/effective graphs,
admission and report projection. Each artifact has one producing pass, and all
passes share the facade's read scope. Internal traces do not change the public
``compose_equipment_plan`` result or its fingerprints.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from genshin_runtime.diagnostics import diagnostic_stage


class ComposePipelineError(RuntimeError):
    """Raised when the internal pass graph violates its declared contract."""


@dataclass(frozen=True, slots=True)
class ComposeRequest:
    plan: dict[str, Any]
    db_path: Path
    candidate_session: Any = None
    formal_provisional: bool = False
    resolved_provisional_uses: Any = None
    readonly_provisional_context: Any = None


@dataclass(frozen=True, slots=True)
class PassReceipt:
    pass_id: str
    status: str
    required_artifacts: tuple[str, ...]
    produced_artifacts: tuple[str, ...]


@dataclass(slots=True)
class ComposeTrace:
    receipts: list[PassReceipt] = field(default_factory=list)

    def append(self, receipt: PassReceipt) -> None:
        self.receipts.append(receipt)


@dataclass(slots=True)
class ComposeArtifacts:
    """Private, typed-by-name artifacts shared between composition passes."""

    _values: dict[str, Any] = field(default_factory=dict)

    def has(self, name: str) -> bool:
        return name in self._values

    def get(self, name: str) -> Any:
        if name not in self._values:
            raise ComposePipelineError(f"composition artifact is unavailable: {name}")
        return self._values[name]

    def put(self, name: str, value: Any) -> None:
        if name in self._values:
            raise ComposePipelineError(f"composition artifact already has an owner: {name}")
        self._values[name] = value


PassHandler = Callable[[ComposeRequest, ComposeArtifacts], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ComposePass:
    pass_id: str
    handler: PassHandler
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()


def run_compose_pipeline(
    request: ComposeRequest,
    passes: Iterable[ComposePass],
) -> tuple[Any, ComposeTrace]:
    """Execute declared passes once and return the final private artifact.

    Each artifact has one owner.  Missing requirements and undeclared outputs
    fail before they can turn into a partially trusted calculation result.
    """

    artifacts = ComposeArtifacts()
    trace = ComposeTrace()
    for compose_pass in passes:
        with diagnostic_stage(
            "composer", compose_pass.pass_id,
            inputs=lambda: {"plan": request.plan},
            prestate=lambda: {"available_artifacts": sorted(artifacts._values)},
        ) as audit:
            missing = tuple(name for name in compose_pass.requires if not artifacts.has(name))
            if missing:
                raise ComposePipelineError(
                    f"composition pass {compose_pass.pass_id} is missing: {', '.join(missing)}"
                )
            produced = compose_pass.handler(request, artifacts)
            if not isinstance(produced, dict):
                raise ComposePipelineError(
                    f"composition pass {compose_pass.pass_id} returned a non-object payload"
                )
            unexpected = tuple(sorted(set(produced) - set(compose_pass.produces)))
            absent = tuple(name for name in compose_pass.produces if name not in produced)
            if unexpected or absent:
                raise ComposePipelineError(
                    f"composition pass {compose_pass.pass_id} violated its output contract"
                )
            for name in compose_pass.produces:
                artifacts.put(name, produced[name])
            trace.append(PassReceipt(
                pass_id=compose_pass.pass_id,
                status="completed",
                required_artifacts=compose_pass.requires,
                produced_artifacts=compose_pass.produces,
            ))
            audit.result(outputs=produced,
                         poststate={"available_artifacts": sorted(artifacts._values)},
                         changed_paths=tuple(f"artifacts.{name}" for name in compose_pass.produces))
    if not artifacts.has("final_report"):
        raise ComposePipelineError("composition pipeline did not produce final_report")
    return artifacts.get("final_report"), trace


__all__ = [
    "ComposeArtifacts",
    "ComposePass",
    "ComposePipelineError",
    "ComposeRequest",
    "ComposeTrace",
    "PassReceipt",
    "run_compose_pipeline",
]
