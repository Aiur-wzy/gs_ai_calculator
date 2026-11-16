"""Follow real App entry imports for packaging, without executing the App.

This shared closure includes lazy/conditional imports; it is not a collect-all
registry of the repository or a second runtime dispatcher.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from genshin_engine.runtime_dependencies import module_source

APP_RUNTIME_ROOTS = (
    "genshin_app.cli",
    "genshin_app.gui.api",
    # Public compatibility API is otherwise deliberately not activated by
    # the sole native owner. Keep it importable in both frozen entrypoints.
    "genshin_app.agent_chat.native_app_tool_bridge",
    # The retained import facade shares the domain owner's exact functions;
    # no live production entry happens to import the old public name.
    "genshin_app.agent_chat.calculation_coverage",
)
# These modules have entry points outside the frozen desktop app.
# Keep this list exact so the archive guard still rejects unaccounted source.
NON_PORTABLE_RELEASE_ENTRIES = frozenset({
    "genshin_app.cli_client",  # Python client used by callers in source environments.
    "genshin_app.release_pair_feed",
    "genshin_app.setup_frozen_qualification",  # Setup-only frozen acceptance entry.
    "genshin_db.release_hygiene",  # Used when producing release artifacts.
})
_PROJECT_PACKAGES = ("genshin_app", "genshin_db", "genshin_engine", "genshin_runtime")


def app_runtime_module_closure(source_root: Path, *, roots=APP_RUNTIME_ROOTS) -> dict[str, Path]:
    """Conservative conditional/relative import closure from exact entries."""
    pending, result = set(roots), {}
    while pending:
        name = pending.pop()
        if name in result:
            continue
        path = module_source(source_root, name)
        if path is None:
            raise ValueError(f"app_runtime_source_missing:{name}")
        result[name] = path
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        dependencies = {name.rpartition(".")[0]}
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = (importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                          if node.level else node.module or "")
                dependencies.add(prefix)
                dependencies.update(prefix + "." + alias.name for alias in node.names)
        for dependency in dependencies:
            if (dependency.split(".", 1)[0] in _PROJECT_PACKAGES
                    and dependency not in result
                    and module_source(source_root, dependency) is not None):
                pending.add(dependency)
    return dict(sorted(result.items()))
