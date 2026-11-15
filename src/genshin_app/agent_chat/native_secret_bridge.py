"""Transient Secret control frames for the native DSH runtime.

The returned frame is intentionally unsuitable for persistence or logging: it
contains the live API key.  Callers must write it directly to the authenticated
private pipe, discard every reference, and retain only :func:`secret_receipt`.
"""
from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any

from .bridge_protocol import (
    JSON_SAFE_INTEGER_MAX,
    BridgeProtocolError,
    make_frame,
    validate_frame,
)

DEEPSEEK_PROVIDER_ID = "deepseek-official"
MAX_CREDENTIAL_CHARS = 512
_SET_KEYS = {"provider", "credential_generation", "secret"}
_CLEAR_KEYS = {"provider", "credential_generation"}


class NativeSecretBridgeError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeSecretBridgeError(code)


def _generation(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail("secret_generation_invalid")
    return value


def _secret(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CREDENTIAL_CHARS
        or any(character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
               for character in value)
    ):
        _fail("secret_value_invalid")
    return value


def _common(
    *,
    frame_type: str,
    payload: dict[str, Any],
    runtime_incarnation: str,
    event_seq: int,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    epoch_id: str,
) -> dict:
    try:
        return make_frame(
            kind="control",
            frame_type=frame_type,
            message_id=str(uuid.uuid4()),
            runtime_incarnation=runtime_incarnation,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            epoch_id=epoch_id,
            event_seq=event_seq,
            payload=payload,
        )
    except (BridgeProtocolError, TypeError, ValueError):
        raise NativeSecretBridgeError("secret_frame_invalid") from None


def make_secret_set_control(
    *,
    api_key: str,
    credential_generation: int,
    runtime_incarnation: str,
    event_seq: int,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    epoch_id: str,
) -> dict:
    """Build one live, non-persistable ``secret.set`` control frame."""
    return _common(
        frame_type="secret.set",
        payload={
            "provider": DEEPSEEK_PROVIDER_ID,
            "credential_generation": _generation(credential_generation),
            "secret": _secret(api_key),
        },
        runtime_incarnation=runtime_incarnation,
        event_seq=event_seq,
        conversation_id=conversation_id,
        turn_id=turn_id,
        step_id=step_id,
        epoch_id=epoch_id,
    )


def make_secret_clear_control(
    *,
    credential_generation: int,
    runtime_incarnation: str,
    event_seq: int,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    epoch_id: str,
) -> dict:
    """Build one live, non-persistable ``secret.clear`` control frame."""
    return _common(
        frame_type="secret.clear",
        payload={
            "provider": DEEPSEEK_PROVIDER_ID,
            "credential_generation": _generation(credential_generation),
        },
        runtime_incarnation=runtime_incarnation,
        event_seq=event_seq,
        conversation_id=conversation_id,
        turn_id=turn_id,
        step_id=step_id,
        epoch_id=epoch_id,
    )


def validate_secret_control(value: Mapping[str, Any]) -> dict:
    """Validate an in-memory Secret frame without reflecting its value."""
    try:
        frame = validate_frame(value)
    except (BridgeProtocolError, TypeError, ValueError):
        _fail("secret_frame_invalid")
    if frame["kind"] != "control" or frame["type"] not in {"secret.set", "secret.clear"}:
        _fail("secret_frame_invalid")
    payload = frame["payload"]
    expected = _SET_KEYS if frame["type"] == "secret.set" else _CLEAR_KEYS
    if set(payload) != expected or payload.get("provider") != DEEPSEEK_PROVIDER_ID:
        _fail("secret_frame_invalid")
    _generation(payload.get("credential_generation"))
    if frame["type"] == "secret.set":
        _secret(payload.get("secret"))
    return copy.deepcopy(frame)


def secret_receipt(value: Mapping[str, Any]) -> dict:
    """Project the only fields that may outlive the pipe write."""
    frame = validate_secret_control(value)
    return {
        "provider": DEEPSEEK_PROVIDER_ID,
        "credential_generation": frame["payload"]["credential_generation"],
        "operation": "set" if frame["type"] == "secret.set" else "clear",
        "message_id": frame["message_id"],
    }


__all__ = [
    "DEEPSEEK_PROVIDER_ID",
    "NativeSecretBridgeError",
    "make_secret_clear_control",
    "make_secret_set_control",
    "secret_receipt",
    "validate_secret_control",
]
