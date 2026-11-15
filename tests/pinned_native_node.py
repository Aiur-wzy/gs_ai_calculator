"""Controlled real Node for tests; synthetic identity fixtures stay independent."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from genshin_app.agent_chat.runtime_integrity import audit_node_executable
from genshin_app.version import (
    APP_RELEASE_ID,
    NATIVE_AGENT_NODE_BINARY_SHA256,
    NATIVE_AGENT_NODE_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]


def node_candidate(*, root=ROOT, environment=None, app_release_id=APP_RELEASE_ID):
    env = os.environ if environment is None else environment
    configured = env.get("GENSHIN_NATIVE_NODE", "").strip()
    if configured:
        return Path(configured).absolute()
    source = Path(root) / "agent-runtime/node.exe"
    if source.is_file():
        return source.absolute()
    if not re.fullmatch(r"app-[A-Za-z0-9][A-Za-z0-9._-]*", app_release_id):
        raise RuntimeError("pinned_test_node_release_id_invalid")
    candidate = Path(root) / f"dist/release-{app_release_id.removeprefix('app-')}/GenshinCalc/_internal/agent-runtime/node.exe"
    return candidate.absolute() if candidate.is_file() else None


def pinned_native_node(*, required=True, root=ROOT, environment=None, app_release_id=APP_RELEASE_ID):
    path = node_candidate(root=root, environment=environment, app_release_id=app_release_id)
    if path is None:
        if required:
            raise RuntimeError("pinned_test_node_not_provisioned")
        return None
    identity = audit_node_executable(path)
    if identity["node_binary_sha256"] != NATIVE_AGENT_NODE_BINARY_SHA256:
        raise RuntimeError("pinned_test_node_sha_mismatch")
    result = subprocess.run([str(path), "--version"], capture_output=True, text=True,
                            check=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.stdout.strip() != f"v{NATIVE_AGENT_NODE_VERSION}" or result.stderr:
        raise RuntimeError("pinned_test_node_version_mismatch")
    return path
