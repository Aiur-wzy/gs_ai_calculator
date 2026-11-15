from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _ROOT / "agent-runtime"
_PROBE = _RUNTIME / "src" / "dsh-file-upload-qualification-probe.mjs"


def test_file_upload_qualification_uses_only_public_rc5_package_roots():
    source = _PROBE.read_text(encoding="utf-8")
    assert "@deepseek-ai/dsh-client-file-upload'" in source
    assert "@deepseek-ai/dsh-client-connection'" in source
    assert "/src/" not in source
    assert "/lib/" not in source
    assert "PRODUCTION_OWNER = false" in source

    package = json.loads((_RUNTIME / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["@deepseek-ai/dsh-client-file-upload"] == "0.2.1-rc.5"
    assert package["scripts"]["probe:s0-file-upload"].endswith(
        "src/dsh-file-upload-qualification-probe.mjs"
    )


def test_pinned_node_runs_public_file_upload_qualification():
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
        [str(node), str(_PROBE)],
        cwd=_RUNTIME,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stderr == ""
    receipt = json.loads(completed.stdout)
    assert receipt["contract"] == "genshin-dsh-file-upload-qualification-v1"
    assert receipt["productionOwner"] is False
    assert receipt["status"] == "passed"
    assert receipt["uploadedBytes"] == sum(receipt["chunks"])
    assert receipt["receiptScoped"] is True
    assert receipt["cancellationSettled"] is True
    assert receipt["packages"] == [
        {"name": "@deepseek-ai/dsh-client-file-upload", "version": "0.2.1-rc.5"},
        {"name": "@deepseek-ai/dsh-client-connection", "version": "0.2.1-rc.5"},
    ]
