"""Host-side validation for commands derived by a Harness V3 projection.

The projection is a short-lived optimistic capability catalogue. It is not a
durable approval and never replaces domain CAS, receipt, writer fence, or the
side-effect transaction checked by the command's eventual owner.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

PROJECTION_CONTRACT = "native-harness-projection-v3"
COMMAND_CONTRACT = "native-harness-command-v3"


class HarnessCommandError(ValueError):
    """A stable command-admission failure safe to expose through Host ports."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _scope(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HarnessCommandError("harness_command_scope_invalid")
    return copy.deepcopy(dict(value))


def validate_harness_command(
    projection: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a request against a freshly rebuilt Host projection.

    Successful validation only admits routing. The target owner must still
    perform its own durable domain CAS and authorization checks.
    """

    if set(request) != {
        "contract",
        "command_id",
        "scope",
        "payload",
        "expected_projection_revision",
    }:
        raise HarnessCommandError("harness_command_request_invalid")
    if projection.get("contract") != PROJECTION_CONTRACT:
        raise HarnessCommandError("harness_projection_contract_invalid")
    if request.get("contract") != COMMAND_CONTRACT:
        raise HarnessCommandError("harness_command_contract_invalid")
    projection_revision = projection.get("projection_revision")
    expected_revision = request.get("expected_projection_revision")
    if type(projection_revision) is not int or projection_revision < 0:
        raise HarnessCommandError("harness_projection_revision_invalid")
    if type(expected_revision) is not int or expected_revision < 0:
        raise HarnessCommandError("harness_command_projection_revision_invalid")
    if expected_revision != projection_revision:
        raise HarnessCommandError("harness_projection_revision_conflict")
    command_id = request.get("command_id")
    if not isinstance(command_id, str) or not command_id:
        raise HarnessCommandError("harness_command_id_invalid")
    request_scope = _scope(request.get("scope"))
    commands = projection.get("available_commands")
    if not isinstance(commands, list):
        raise HarnessCommandError("harness_projection_commands_invalid")
    matches = [
        row for row in commands
        if isinstance(row, Mapping) and row.get("command_id") == command_id
    ]
    if not matches:
        raise HarnessCommandError("harness_command_unavailable")
    exact = [row for row in matches if _scope(row.get("scope")) == request_scope]
    if not exact:
        raise HarnessCommandError("harness_command_scope_conflict")
    if len(exact) != 1:
        raise HarnessCommandError("harness_command_ambiguous")
    command = exact[0]
    if command.get("expected_projection_revision") != projection_revision:
        raise HarnessCommandError("harness_projection_command_revision_invalid")
    if command.get("enabled") is not True:
        raise HarnessCommandError(
            command.get("reason_code")
            if isinstance(command.get("reason_code"), str)
            else "harness_command_disabled"
        )
    payload = request.get("payload", {})
    if not isinstance(payload, Mapping):
        raise HarnessCommandError("harness_command_payload_invalid")
    return {
        "contract": COMMAND_CONTRACT,
        "command_id": command_id,
        "scope": request_scope,
        "payload": copy.deepcopy(dict(payload)),
        "risk": command.get("risk"),
        "invocation_kind": command.get("invocation_kind", "host_command"),
        "expected_projection_revision": projection_revision,
    }


__all__ = [
    "COMMAND_CONTRACT",
    "HarnessCommandError",
    "validate_harness_command",
]
