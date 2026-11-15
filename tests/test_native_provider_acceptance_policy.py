"""Retired acceptance profiles are evidence only, never a current runtime fallback."""
from __future__ import annotations

import hashlib
import json

import pytest

from genshin_app.agent_chat.runtime_integrity import (
    RuntimeIntegrityError,
    verify_dsh_client_shell_manifest,
)
from genshin_app.version import APP_VERSION, NATIVE_AGENT_NODE_VERSION


@pytest.mark.parametrize("profile", ["phase5b_official_turn", "dsh_v2_provider_acceptance", "phase1"])
def test_retired_profile_cannot_be_admitted_by_current_shell(tmp_path, profile):
    body = {
        "contract": "genshin-native-phase5b-runtime-manifest-v1",
        "phase": profile, "app_version": APP_VERSION, "runtime_generation": profile,
        "entry": "src/native-provider-acceptance-sidecar-entry.mjs",
        "node_version": NATIVE_AGENT_NODE_VERSION, "dsh_release": "0.2.1-rc.5",
        "runtime_files": {}, "runtime_closure_hash": "0" * 64,
        "dependency_lock_hash": "0" * 64, "node_binary_sha256": "0" * 64,
        "dependency_cohort": {}, "dependency_payloads": {}, "asset_files": {},
        "asset_closure_hash": "0" * 64, "client_composition_hash": "0" * 64,
        "client_accepted_hash": "0" * 64, "client_accepted_semantic_hash": "0" * 64,
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    manifest = {**body, "manifest_hash": hashlib.sha256(raw).hexdigest()}
    with pytest.raises(RuntimeIntegrityError) as caught:
        verify_dsh_client_shell_manifest(manifest, runtime_root=tmp_path / "absent-runtime",
                                         assets_root=tmp_path / "absent-assets", node_executable=tmp_path / "absent-node")
    assert caught.value.code == "dsh_shell_manifest_identity"
