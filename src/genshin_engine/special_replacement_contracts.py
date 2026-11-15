"""Closed transition binding metadata and deterministic private state identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


def replacement_state_fingerprint(state: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        dict(state), ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf8")).hexdigest()


def replacement_schedule_fingerprint(schedule: list[dict]) -> str:
    return hashlib.sha256(json.dumps(
        schedule, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplacementFamilyBinding[ProposalT, TransitionT]:
    family: str
    contract_version: int
    implementation_version: int
    handler: Callable[[ProposalT], TransitionT]
    profile_schema_version: int = 1
    capabilities: tuple[str, ...] = ()


__all__ = [
    "ReplacementFamilyBinding",
    "replacement_schedule_fingerprint",
    "replacement_state_fingerprint",
]

