from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _ROOT / "agent-runtime"
_PROBE = _RUNTIME / "src" / "fixed-root-filesystem-probe.mjs"


def test_fixed_root_adapter_uses_only_the_public_dsh_fs_entrypoint():
    source = (_RUNTIME / "src" / "fixed-root-filesystem.mjs").read_text(
        encoding="utf-8"
    )

    assert "from '@deepseek-ai/dsh-fs'" in source
    assert "dsh-fs-local" not in source
    assert "/lib/" not in source
    assert "/src/" not in source
    assert "#root" in source
    assert "#targets" in source
    assert "#record" in source
    assert "#target" in source
    assert "this._root" not in source
    assert "this._targets" not in source
    assert "_afterReadHandleOpened" not in source


def test_fixed_root_contract_keeps_general_file_capabilities_disabled():
    source = (
        _RUNTIME / "src" / "fixed-root-filesystem-contract.mjs"
    ).read_text(encoding="utf-8")

    assert "productionOwner: false" in source
    assert "generalFileToolRegistered: false" in source
    assert "capabilityState: 'ecmascript-private'" in source
    assert "sameProcessExtensions: 'trusted-computing-base'" in source
    assert "hardlinksAccepted: false" in source
    assert "symlinksAccepted: false" in source
    assert "host-mirror-prevalidation-required" in source
    assert "boundedReadRequired: true" in source
    assert "sessionControllerIntegrated: false" in source


def test_fixed_root_node_probe_with_pinned_runtime():
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
    assert version.stderr == ""
    assert version.stdout.strip() == "v24.19.0"

    completed = subprocess.run(
        [str(node), str(_PROBE)],
        cwd=_RUNTIME,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stderr == ""
    receipt = json.loads(completed.stdout)
    assert receipt["contract"] == "genshin-dsh-fixed-root-filesystem-v1"
    assert receipt["status"] == "passed"
    assert receipt["productionOwner"] is False
    assert receipt["activeServices"] == ["fs"]
    assert receipt["listenerAttempts"] == 0
    assert receipt["realHostPathRejected"] is True
    assert receipt["outsideMappingRejected"] is True
    assert receipt["forgedTargetRejected"] is True
    assert receipt["unboundedLeafCache"] is False
    assert receipt["privateCapabilityState"] is True
    assert receipt["hardlinkRejected"] is True
    assert receipt["boundedRead"] is True
    assert receipt["growthRejected"] is True
    assert receipt["preAbortRejected"] is True
    assert receipt["midReadAbortRejected"] is True
    assert receipt["rootDisclosureDetected"] is False
    assert receipt["sessionControllerIntegrated"] is False
