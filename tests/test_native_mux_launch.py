from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_mux_launch import (
    MAX_MUX_LAUNCH_BYTES,
    MUX_LAUNCH_CONTRACT,
    MUX_TRANSPORT_CODEC,
    PRODUCTION_OWNER,
    NativeMuxLaunchError,
    decode_mux_launch_contract,
    encode_mux_launch_contract,
)

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _ROOT / "agent-runtime"
_VECTORS = json.loads(
    (_RUNTIME / "native-mux-launch-v1-vectors.json").read_text(encoding="utf-8")
)


def _error(code: str, operation) -> NativeMuxLaunchError:
    with pytest.raises(NativeMuxLaunchError) as caught:
        operation()
    assert caught.value.code == code
    return caught.value


def _encode(**overrides) -> bytes:
    value = {**_VECTORS["python_input"], **overrides}
    return encode_mux_launch_contract(**value)


def _rewrite(packet: bytes, value: dict, *, canonical: bool = True) -> bytes:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=canonical,
        separators=(",", ":") if canonical else None,
    ).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def test_python_and_node_share_one_canonical_mux_launch_packet():
    expected = base64.b64decode(_VECTORS["packet_base64"])
    assert _encode() == expected
    decoded = decode_mux_launch_contract(expected)
    assert decoded.pipe_name == _VECTORS["python_input"]["pipe_name"]
    assert decoded.nonce == _VECTORS["python_input"]["nonce"]
    assert decoded.transport_codec == MUX_TRANSPORT_CODEC
    assert decoded.connection_generation == 7
    assert decoded.connection_id == "connection-qualification-7"
    assert decoded.runtime_incarnation == "runtime-qualification-7"
    assert PRODUCTION_OWNER is False


def test_legacy_and_mux_contracts_are_mutually_rejected_without_fallback():
    legacy = base64.b64decode(_VECTORS["legacy_packet_base64"])
    _error("mux_launch_contract_shape", lambda: decode_mux_launch_contract(legacy))

    value = json.loads(_encode()[4:])
    value["contract"] = "genshin-native-launch-v1"
    _error(
        "mux_launch_contract_version",
        lambda: decode_mux_launch_contract(_rewrite(_encode(), value)),
    )
    _error("mux_launch_transport_codec", lambda: _encode(transport_codec="legacy"))


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"connection_generation": True}, "mux_launch_connection_generation"),
        ({"connection_generation": 0}, "mux_launch_connection_generation"),
        ({"connection_id": "bad identity"}, "mux_launch_connection_id"),
        ({"runtime_incarnation": ""}, "mux_launch_runtime_incarnation"),
        ({"nonce": "short"}, "mux_launch_nonce"),
        ({"pipe_name": r"\\.\pipe\other"}, "mux_launch_pipe_name"),
    ],
)
def test_identity_and_generation_grammar_is_strict(override, code):
    _error(code, lambda: _encode(**override))


def test_shape_canonical_utf8_and_packet_bounds_fail_closed():
    expected = _encode()
    value = json.loads(expected[4:])
    _error(
        "mux_launch_contract_shape",
        lambda: decode_mux_launch_contract(_rewrite(expected, {**value, "extra": True})),
    )
    value.pop("connection_id")
    _error(
        "mux_launch_contract_shape",
        lambda: decode_mux_launch_contract(_rewrite(expected, value)),
    )
    _error(
        "mux_launch_contract_noncanonical",
        lambda: decode_mux_launch_contract(_rewrite(expected, json.loads(expected[4:]), canonical=False)),
    )
    duplicate = expected[4:-1] + b',"nonce":"' + b"x" * 32 + b'"}'
    _error(
        "mux_launch_contract_noncanonical",
        lambda: decode_mux_launch_contract(struct.pack(">I", len(duplicate)) + duplicate),
    )
    invalid_utf8 = b'{"contract":"\xff"}'
    _error(
        "mux_launch_contract_json",
        lambda: decode_mux_launch_contract(struct.pack(">I", len(invalid_utf8)) + invalid_utf8),
    )
    bom_body = b"\xef\xbb\xbf" + expected[4:]
    _error(
        "mux_launch_contract_json",
        lambda: decode_mux_launch_contract(struct.pack(">I", len(bom_body)) + bom_body),
    )
    _error("mux_launch_contract_truncated", lambda: decode_mux_launch_contract(b"\0\0\0\1"))
    _error(
        "mux_launch_contract_length",
        lambda: decode_mux_launch_contract(struct.pack(">I", MAX_MUX_LAUNCH_BYTES + 1) + b"x"),
    )


def test_rejections_do_not_reflect_inherited_values():
    secret = "MUX_LAUNCH_SECRET_MUST_NOT_APPEAR"
    error = _error("mux_launch_nonce", lambda: _encode(nonce=secret + "!"))
    assert secret not in f"{error!s}\n{error!r}\n{vars(error)!r}"


def test_node_mux_launch_probe_with_pinned_runtime():
    configured = os.environ.get("GENSHIN_NATIVE_NODE")
    if not configured:
        pytest.skip("set GENSHIN_NATIVE_NODE to the pinned Node executable")
    node = Path(configured).resolve()
    assert node.is_file()
    version = subprocess.run(
        [str(node), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert version.stdout.strip() == "v24.19.0"
    completed = subprocess.run(
        [str(node), str(_RUNTIME / "src" / "native-mux-launch-probe.mjs")],
        cwd=_RUNTIME,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stderr == ""
    receipt = json.loads(completed.stdout)
    assert receipt == {
        "contract": MUX_LAUNCH_CONTRACT,
        "transportCodec": MUX_TRANSPORT_CODEC,
        "productionOwner": False,
        "status": "passed",
    }


def test_retired_python_owner_is_absent_and_current_owner_uses_mux_only():
    assert not (
        _ROOT / "src" / "genshin_app" / "agent_chat" / "native_runtime_process.py"
    ).exists()
    source = (_ROOT / "src/genshin_app/agent_chat/native_dsh_mux_process.py").read_text(encoding="utf-8")
    assert "from .native_mux_launch import encode_mux_launch_contract" in source
    assert "from .native_runtime_process" not in source
