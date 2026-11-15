"""Describe the installed CLI honestly, without scanning PATH or launching it."""
from __future__ import annotations

import sys
from pathlib import Path

from genshin_app.paths import is_frozen, resource_dir


def cli_launch_descriptor():
    root = resource_dir()
    if is_frozen():
        binary = Path(sys.executable).with_name("GenshinCalc.exe")
        if not binary.is_file():
            return {"available": False, "reason": "packaged_cli_missing"}
        return {"available": True, "argv_prefix": [str(binary)],
                "working_directory": str(binary.parent), "environment": {}}
    source = root / "src"
    if not (source / "genshin_app" / "__main__.py").is_file():
        return {"available": False, "reason": "source_cli_missing"}
    return {"available": True, "argv_prefix": [sys.executable, "-m", "genshin_app"],
            "working_directory": str(root), "environment": {"PYTHONPATH": str(source)}}
