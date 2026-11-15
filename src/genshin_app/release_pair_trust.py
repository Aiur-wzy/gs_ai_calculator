"""Pinned signature verification for release-pair discovery documents.

Public release metadata is untrusted until its canonical payload passes a
compiled-in public key.  The verifier is deliberately small and dependency
free: it implements the RSA-PSS/SHA-256 verification operation only.  It does
not sign data, load keys from the user profile, or accept algorithms named by
the downloaded document unless the matching key record is already pinned by
the App build.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

VERIFIER_CONTRACT = "genshin-release-pair-rsa-pss-sha256-v1"
ALGORITHM = "rsa-pss-sha256"
SALT_BYTES = hashlib.sha256().digest_size


class ReleasePairTrustError(ValueError):
    """Stable configuration failure for compiled release trust anchors."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _mgf1(seed: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(output[:length])


def _decode_signature(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 16_384:
        raise ReleasePairTrustError("release_pair_signature_value_invalid")
    try:
        # The wire format is unpadded base64url.  Accepting optional padding
        # keeps external signing tools simple without accepting another
        # textual alphabet.
        raw = value.encode("ascii")
        padded = raw + b"=" * (-len(raw) % 4)
        signature = base64.b64decode(padded, altchars=b"-_", validate=True)
        expected = base64.urlsafe_b64encode(signature)
        if raw not in (expected, expected.rstrip(b"=")):
            raise ValueError("non-canonical base64url signature")
        return signature
    except (UnicodeEncodeError, ValueError) as error:
        raise ReleasePairTrustError(
            "release_pair_signature_value_invalid"
        ) from error


def _verify_pss_sha256(message: bytes, signature: bytes, *, modulus: int, exponent: int) -> bool:
    if (
        modulus.bit_length() < 2048
        or modulus % 2 == 0
        or exponent < 65_537
        or exponent % 2 == 0
        or exponent >= modulus
    ):
        return False
    encoded_length = (modulus.bit_length() - 1 + 7) // 8
    signature_length = (modulus.bit_length() + 7) // 8
    if len(signature) != signature_length:
        return False
    number = int.from_bytes(signature, "big")
    if number >= modulus:
        return False
    encoded_full = pow(number, exponent, modulus).to_bytes(signature_length, "big")
    if len(encoded_full) < encoded_length:
        return False
    encoded = encoded_full[-encoded_length:]
    if any(encoded_full[:-encoded_length]) or encoded[-1:] != b"\xbc":
        return False
    hash_length = hashlib.sha256().digest_size
    data_length = encoded_length - hash_length - 1
    if data_length < SALT_BYTES + 1:
        return False
    masked = encoded[:data_length]
    expected_hash = encoded[data_length:-1]
    unused_bits = 8 * encoded_length - (modulus.bit_length() - 1)
    if unused_bits and masked[0] >> (8 - unused_bits):
        return False
    mask = _mgf1(expected_hash, data_length)
    data = bytearray(left ^ right for left, right in zip(masked, mask, strict=True))
    if unused_bits:
        data[0] &= 0xFF >> unused_bits
    separator = data_length - SALT_BYTES - 1
    if any(data[:separator]) or data[separator] != 1:
        return False
    salt = bytes(data[-SALT_BYTES:])
    message_hash = hashlib.sha256(message).digest()
    actual_hash = hashlib.sha256(b"\0" * 8 + message_hash + salt).digest()
    return actual_hash == expected_hash


def build_trust_verifier(
    keys: Sequence[Mapping[str, Any]],
):
    """Return a discovery verifier for one immutable compiled key set.

    An empty key set intentionally returns ``None`` so callers can distinguish
    an unconfigured product from a verifier which rejected a signature.
    """

    normalized: dict[str, tuple[int, int]] = {}
    for item in keys:
        if not isinstance(item, Mapping) or set(item) != {
            "key_id",
            "algorithm",
            "modulus_hex",
            "exponent",
        }:
            raise ReleasePairTrustError("release_pair_trust_key_invalid")
        key_id = item["key_id"]
        modulus_hex = item["modulus_hex"]
        exponent = item["exponent"]
        if (
            type(key_id) is not str
            or not key_id
            or len(key_id) > 256
            or item["algorithm"] != ALGORITHM
            or type(modulus_hex) is not str
            or not modulus_hex
            or len(modulus_hex) > 4096
            or any(character not in "0123456789abcdef" for character in modulus_hex)
            or type(exponent) is not int
        ):
            raise ReleasePairTrustError("release_pair_trust_key_invalid")
        try:
            modulus = int(modulus_hex, 16)
        except ValueError as error:
            raise ReleasePairTrustError("release_pair_trust_key_invalid") from error
        if (
            modulus.bit_length() < 2048
            or modulus % 2 == 0
            or exponent < 65_537
            or exponent % 2 == 0
            or exponent >= modulus
        ):
            raise ReleasePairTrustError("release_pair_trust_key_invalid")
        if key_id in normalized:
            raise ReleasePairTrustError("release_pair_trust_key_duplicate")
        normalized[key_id] = (modulus, exponent)
    if not normalized:
        return None

    def verify(payload: Mapping[str, Any], signature: Mapping[str, Any]) -> bool:
        if (
            type(signature) is not dict
            or signature.get("algorithm") != ALGORITHM
            or type(signature.get("key_id")) is not str
            or signature["key_id"] not in normalized
            or type(payload) is not dict
        ):
            return False
        try:
            encoded = _decode_signature(signature.get("value"))
        except ReleasePairTrustError:
            return False
        modulus, exponent = normalized[signature["key_id"]]
        return _verify_pss_sha256(
            _canonical(payload),
            encoded,
            modulus=modulus,
            exponent=exponent,
        )

    verify.verifier_contract = VERIFIER_CONTRACT
    verify.key_ids = tuple(sorted(normalized))
    return verify


__all__ = [
    "ALGORITHM",
    "VERIFIER_CONTRACT",
    "ReleasePairTrustError",
    "build_trust_verifier",
]
