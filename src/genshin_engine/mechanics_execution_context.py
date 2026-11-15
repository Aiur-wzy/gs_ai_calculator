"""Per-call artifacts for the single Mechanics admission and execution chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_UNSET = object()


@dataclass(frozen=True, slots=True)
class MechanicsExecutionRequest:
    template: dict
    rotation_model: dict
    mechanics_model: dict
    matches: list[dict] | None = None


@dataclass(slots=True)
class MechanicsExecutionContext:
    request: MechanicsExecutionRequest
    output: dict = field(default_factory=dict)
    captures: Any = None
    capture_ids: Any = None
    projected: Any = None
    rotation: Any = None
    runtime_events: Any = None
    chars: Any = None
    fixed_sources: Any = None
    rotation_context: Any = None
    data: Any = None
    expanded: Any = None
    windows: Any = None
    finite_until: Any = None
    all_ids: Any = None
    damage_types: Any = None
    conditions: Any = None
    simulation_result: Any = None
    current_pass: str | None = None
    ready: set[str] = field(default_factory=set)
    _executor: Any = field(default=_UNSET, repr=False)

    @property
    def has_executor(self) -> bool:
        return self._executor is not _UNSET

    @property
    def executor(self) -> Any:
        if self._executor is _UNSET:
            # Preserve the former entry function's local-variable semantics:
            # a failed constructor does not create a partially assigned owner.
            raise UnboundLocalError("cannot access local variable 'executor' where it is not associated with a value")
        return self._executor

    @executor.setter
    def executor(self, value: Any) -> None:
        self._executor = value
