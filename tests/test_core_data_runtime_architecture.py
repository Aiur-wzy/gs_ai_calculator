"""Executable ownership gates for the core data/runtime refactor."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from genshin_app.equipment.action_dispatch_materialization import (
    action_dispatch_materialization_capabilities,
)
from genshin_db.runtime_action_dispatch import (
    action_dispatch_query_capabilities,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _tree(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)


def _imports(tree: ast.AST) -> set[str]:
    values = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def test_database_dispatch_resolver_has_no_app_materializer_or_reverse_import():
    tree = _tree("src/genshin_db/runtime_action_dispatch.py")
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "compile_action_dispatch_inputs" not in functions
    assert not any(name == "genshin_app" or name.startswith("genshin_app.")
                   for name in _imports(tree))
    query = action_dispatch_query_capabilities()
    materialization = action_dispatch_materialization_capabilities()
    assert query["safety"]["execution_authority"] is False
    assert "mapping_targets" not in query
    assert materialization["source_contract_id"] == query["contract_id"]
    assert materialization["mapping_targets"]


def test_production_dispatch_source_uses_app_owned_materializer():
    tree = _tree("src/genshin_app/equipment/action_dispatch_sources.py")
    imported = _imports(tree)
    assert "genshin_app.equipment.action_dispatch_materialization" in imported
    assert "genshin_db.runtime_action_dispatch" in imported
    source = (ROOT / "src/genshin_app/equipment/action_dispatch_sources.py").read_text(
        encoding="utf-8")
    assert "from genshin_db.runtime_action_dispatch import (\n        compile_action_dispatch_inputs" not in source


def test_whiteboard_facade_and_leaf_modules_keep_domain_boundaries():
    facade = _tree("src/genshin_app/whiteboard.py")
    facade_functions = {node.name for node in facade.body if isinstance(node, ast.FunctionDef)}
    assert facade_functions == {"whiteboard_capabilities", "apply_whiteboard_equipment"}
    for relative in (
        "src/genshin_app/whiteboard_schema.py",
        "src/genshin_app/whiteboard_identity.py",
        "src/genshin_app/whiteboard_store.py",
        "src/genshin_app/whiteboard_handoff.py",
        "src/genshin_app/whiteboard_result_binding.py",
    ):
        imported = _imports(_tree(relative))
        assert not any(name.startswith(("genshin_app.gui", "genshin_app.agent_chat"))
                       for name in imported), relative


def test_every_component_fixture_builder_test_is_classified():
    manifest = json.loads((ROOT / "tests/fixtures/candidate_builder_migration.json")
                          .read_text(encoding="utf-8"))
    classified = {row["path"] for row in manifest["entries"]}
    actual = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        if path.resolve() == Path(__file__).resolve():
            continue
        if "fixture_builder.build_database(" in path.read_text(encoding="utf-8"):
            actual.add(path.relative_to(ROOT).as_posix())
    assert classified == actual
    assert len(classified) == len(manifest["entries"])
    assert all(row["class"] in manifest["classes"] for row in manifest["entries"])


def test_retired_builder_has_no_executable_fallback():
    tree = _tree("src/genshin_db/pipeline.py")
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "_run_update" not in functions
    assert "_optimize_runtime_locked" not in functions
    legacy = functions["run_update"]
    assert isinstance(legacy.body[-1], ast.Raise)
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"build_candidate_database", "_run_update", "build_curated_layer"}
        for node in ast.walk(legacy)
    )


def test_deprecated_candidate_builder_is_not_a_production_call_site():
    callers = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "run_update(" in text and path.name != "pipeline.py":
            callers.append(path.relative_to(ROOT).as_posix())
    assert callers == []
