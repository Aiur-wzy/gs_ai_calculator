from __future__ import annotations

import base64

import pytest

from genshin_app import release_pair_trust as trust


def _key():
    # The dispatcher validates key shape/strength before the isolated PSS
    # primitive is called.  Cryptographic interoperability is covered by the
    # release signing verification job; these tests keep unit fixtures small.
    return {
        "key_id": "release-2026",
        "algorithm": trust.ALGORITHM,
        "modulus_hex": hex((1 << 2047) | 0x12345)[2:],
        "exponent": 65_537,
    }


def test_empty_key_set_is_explicitly_unconfigured():
    assert trust.build_trust_verifier(()) is None


def test_pinned_dispatcher_accepts_only_matching_algorithm_and_key(monkeypatch):
    observed = {}

    def verify(message, signature, *, modulus, exponent):
        observed.update(
            message=message,
            signature=signature,
            modulus=modulus,
            exponent=exponent,
        )
        return True

    monkeypatch.setattr(trust, "_verify_pss_sha256", verify)
    verifier = trust.build_trust_verifier((_key(),))
    encoded = base64.urlsafe_b64encode(b"signed").decode().rstrip("=")
    assert verifier(
        {"z": 1, "a": "资料"},
        {
            "algorithm": trust.ALGORITHM,
            "key_id": "release-2026",
            "value": encoded,
        },
    ) is True
    assert observed["message"] == b'{"a":"\xe8\xb5\x84\xe6\x96\x99","z":1}'
    assert observed["signature"] == b"signed"
    assert observed["exponent"] == 65_537
    assert verifier.verifier_contract == trust.VERIFIER_CONTRACT
    assert verifier.key_ids == ("release-2026",)
    assert verifier(
        {"a": 1},
        {"algorithm": "other", "key_id": "release-2026", "value": encoded},
    ) is False
    assert verifier(
        {"a": 1},
        {"algorithm": trust.ALGORITHM, "key_id": "unknown", "value": encoded},
    ) is False


def test_invalid_or_duplicate_compiled_keys_fail_closed():
    with pytest.raises(trust.ReleasePairTrustError) as weak:
        trust.build_trust_verifier(({**_key(), "modulus_hex": "ff"},))
    assert weak.value.reason == "release_pair_trust_key_invalid"
    with pytest.raises(trust.ReleasePairTrustError) as duplicate:
        trust.build_trust_verifier((_key(), _key()))
    assert duplicate.value.reason == "release_pair_trust_key_duplicate"


def test_rsa_pss_sha256_interoperates_with_node_crypto_vector():
    """Keep the dependency-free verifier pinned to a real external signer.

    The fixture was produced by Node.js ``crypto.sign`` with a 2048-bit RSA
    key, PSS padding and a 32-byte SHA-256 salt over the canonical UTF-8 JSON
    payload.  This catches bit-length and leading-bit mistakes that dispatcher
    mocks cannot detect.
    """

    key = {
        "key_id": "node-interop-2026",
        "algorithm": trust.ALGORITHM,
        "modulus_hex": (
            "ed8d274f274e2dbdce7643d60697417112b56c969ae07c115636621de60e7ebe"
            "5b552ba25c6d13f35ae9470021bd940a170d584694cdf673753955014fbf37af"
            "c85234cacb540850544848d6db11f913bd0a0b6e411f1a61cdcebfefc5f6cb"
            "fdc0d2d8309fc684628e5518bb736093876ceef8a687c5fa3f3b5d204074ce9"
            "a5670d56ef179432c90918e59b775926485af67cd598633d602c4920de7ab67"
            "6320de1e063d41a2db365a6f782c86fab099d356a5a8ef547c9f02f7ec9e41"
            "ec6bf402ff693da6907c0b7d8d0261ee0d2a10cc8afa4ecb39d3c5b3d0664b"
            "66c1e49d059e91b26ae784d99624934d4d16e92822d292320a8cdfeebcd71cb"
            "5f6fa006b"
        ),
        "exponent": 65_537,
    }
    signature = {
        "algorithm": trust.ALGORITHM,
        "key_id": key["key_id"],
        "value": (
            "sgsJoX86E95s1eudl4g7rA5yKnwEKkn5-1lvwXDZFBkr6cLM6l8h_ovgJHCg"
            "f2UU_zZfjPwZSVpiQSdgEMeS0RPpi_x4Kg0v0iJPRi29LkUrlKdz3wezYJVdg"
            "-m70N6COcOUtJkit0gtLXciDJl87TLG09a6e9gBftpvCZqyjkR_4oKighyAfig"
            "MUPjYstGQOqCBvX8csfzPkPNDKDw1Bsi5y_GYAEMn_GksvsVL9qVvHYXqvmY"
            "0ouXeI7r9FlEtQPfzcBl87w0DJOZ3fckGJi01C8pRBNeW4hSzQ6Sy39RbGxuZ"
            "0li3rLeitJRdtSNgx8HuI9yLsb4KOIZALdVjRQ"
        ),
    }
    verifier = trust.build_trust_verifier((key,))
    assert verifier({"z": 1, "a": "资料"}, signature) is True
    assert verifier({"z": 2, "a": "资料"}, signature) is False
