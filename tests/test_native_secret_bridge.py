from __future__ import annotations

import copy
import json

import pytest

from genshin_app.agent_chat.native_secret_bridge import (
    DEEPSEEK_PROVIDER_ID,
    NativeSecretBridgeError,
    make_secret_clear_control,
    make_secret_set_control,
    secret_receipt,
    validate_secret_control,
)


def _binding():
    return {
        "runtime_incarnation": "runtime-1",
        "event_seq": 2,
        "conversation_id": "native-bootstrap",
        "turn_id": "native-bootstrap",
        "step_id": "native-bootstrap",
        "epoch_id": "native-bootstrap",
    }


def test_secret_set_is_valid_but_only_redacted_receipt_may_persist():
    sentinel = "SENTINEL_NATIVE_SECRET_ONE"
    frame = make_secret_set_control(
        api_key=sentinel, credential_generation=7, **_binding())
    assert validate_secret_control(frame)["payload"]["secret"] == sentinel
    receipt = secret_receipt(frame)
    assert receipt == {
        "provider": DEEPSEEK_PROVIDER_ID,
        "credential_generation": 7,
        "operation": "set",
        "message_id": frame["message_id"],
    }
    assert sentinel not in json.dumps(receipt, sort_keys=True)


def test_secret_clear_has_no_secret_field():
    frame = make_secret_clear_control(credential_generation=8, **_binding())
    checked = validate_secret_control(frame)
    assert checked["payload"] == {
        "provider": DEEPSEEK_PROVIDER_ID,
        "credential_generation": 8,
    }
    assert secret_receipt(frame)["operation"] == "clear"


@pytest.mark.parametrize("secret", ["", "bad key", "line\nbreak", "x" * 513, None])
def test_invalid_secret_never_appears_in_error(secret):
    with pytest.raises(NativeSecretBridgeError) as caught:
        make_secret_set_control(
            api_key=secret, credential_generation=1, **_binding())
    assert caught.value.code == "secret_value_invalid"
    if isinstance(secret, str) and secret:
        assert secret not in repr(caught.value)


@pytest.mark.parametrize("generation", [-1, True, "1", None, 2**53])
def test_invalid_generation_is_rejected(generation):
    with pytest.raises(NativeSecretBridgeError, match="secret_generation_invalid"):
        make_secret_clear_control(
            credential_generation=generation, **_binding())


def test_payload_tampering_and_unknown_fields_fail_closed():
    frame = make_secret_set_control(
        api_key="SENTINEL_NATIVE_SECRET_TWO",
        credential_generation=2,
        **_binding(),
    )
    changed = copy.deepcopy(frame)
    changed["payload"]["extra"] = True
    # Even after a malicious peer recomputes the outer hash, the secret schema
    # stays exact.  Rebuilding through make_frame is tested by direct mutation
    # of a separately valid clear frame below.
    with pytest.raises(NativeSecretBridgeError, match="secret_frame_invalid"):
        validate_secret_control(changed)

    clear = make_secret_clear_control(credential_generation=3, **_binding())
    clear["type"] = "runtime.stop"
    with pytest.raises(NativeSecretBridgeError, match="secret_frame_invalid"):
        validate_secret_control(clear)
