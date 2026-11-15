"""No-window qualification of the production frontend document application.

The optional development projection uses existing frontend test dependencies.
Those dependencies are deliberately not added to a portable production bundle.
Frozen qualification records Host-protocol scope and uses separate source
frontend evidence, rather than claiming a desktop window has been tested.
"""
from __future__ import annotations

import json
import os
import subprocess

from genshin_app.paths import is_frozen, resource_dir


def apply_frontend_document(*, board, reviewed, conversation_id, path=None, file_sha=None):
    if is_frozen():
        return None
    root = resource_dir()
    script = root / "frontend" / "scripts" / "app-document-projection.mjs"
    node = root / "agent-runtime" / "node.exe"
    if not script.is_file() or not node.is_file():
        raise RuntimeError("production_document_projection_unavailable")
    raw = json.dumps({"kind": "whiteboard", "board": board, "reviewed": reviewed,
                      "conversation_id": conversation_id, "path": path, "file_sha": file_sha},
                     ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    process = subprocess.run([str(node), str(script)], input=raw, capture_output=True,
        text=True, encoding="utf-8", cwd=root, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={key: value for key, value in os.environ.items()
             if key.upper() in {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC"}})
    if process.returncode:
        raise RuntimeError("production_document_projection_failed")
    value = json.loads(process.stdout)
    if (not isinstance(value, dict) or value.get("contract") != "genshin-production-document-projection-v1"
            or value.get("scope") != "production_frontend_document_application"
            or value.get("real_gui_tested") is not False
            or not isinstance(value.get("document"), dict)
            or not isinstance(value.get("application_payload"), dict)):
        raise RuntimeError("production_document_projection_receipt_invalid")
    return value
