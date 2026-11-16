"""Disposable process used only by installation acceptance tests."""
import json
import os
import sys
import time
from pathlib import Path

from genshin_app.installation_runtime import installation_runtime_lease

root, marker, release, mode = map(Path, sys.argv[1:5])
try:
    with installation_runtime_lease(root):
        marker.write_text(json.dumps({"status": "entered"}), encoding="utf-8")
        if str(mode) == "crash":
            os._exit(17)
        deadline = time.monotonic() + 30
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("owned test process was not released")
            time.sleep(0.02)
except Exception as error:  # noqa: BLE001 - child reports unexpected errors to its owning test
    marker.write_text(json.dumps({"status": "rejected", "reason": getattr(error, "reason", type(error).__name__)}), encoding="utf-8")
    sys.exit(2)
