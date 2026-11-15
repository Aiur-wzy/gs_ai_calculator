import ast
from pathlib import Path

import pytest

from genshin_app.equipment.compose_pipeline import (
    ComposePass,
    ComposePipelineError,
    ComposeRequest,
    run_compose_pipeline,
)
from genshin_engine.runtime_event_router import RuntimeEventRouter

ROOT = Path(__file__).resolve().parents[1]


def test_compose_pipeline_enforces_single_owner_and_declared_outputs():
    request = ComposeRequest(plan={}, db_path=Path("unused.sqlite"))
    result, trace = run_compose_pipeline(request, (
        ComposePass("first", lambda _request, _artifacts: {"draft": {"ok": True}},
                    produces=("draft",)),
        ComposePass("final", lambda _request, artifacts: {
            "final_report": artifacts.get("draft")
        }, requires=("draft",), produces=("final_report",)),
    ))
    assert result == {"ok": True}
    assert [receipt.pass_id for receipt in trace.receipts] == ["first", "final"]


def test_compose_pipeline_rejects_undeclared_output():
    request = ComposeRequest(plan={}, db_path=Path("unused.sqlite"))
    with pytest.raises(ComposePipelineError, match="output contract"):
        run_compose_pipeline(request, (
            ComposePass("bad", lambda _request, _artifacts: {"other": 1},
                        produces=("final_report",)),
        ))


def test_mechanics_does_not_import_private_rotation_or_special_helpers():
    path = ROOT / "src" / "genshin_engine" / "mechanics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"genshin_engine.rotation", "genshin_engine.special_reactions"}
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in forbidden
    }
    assert imports == set()


def test_mechanics_reporting_has_one_projection_owner():
    path = ROOT / "src" / "genshin_engine" / "mechanics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    local_functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert not local_functions.intersection({
        "_attach_attribute_report",
        "_attach_buff_state_timeline",
        "_attach_shared_pool_report",
        "_attach_special_reaction_report",
        "_attach_state_runtime_report",
    })
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "genshin_engine.mechanics_reporting"
    ]
    assert len(imports) == 5


def test_special_damage_projection_has_one_module_owner():
    path = ROOT / "src" / "genshin_engine" / "mechanics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    local_functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert not local_functions.intersection({
        "_apply_special_damage_weights",
        "_direct_lunar_damage_hit",
        "_direct_profile_evidence_label",
        "_direct_stellar_damage_hit",
        "_special_damage_hit",
    })
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "genshin_engine.mechanics_special_damage"
        for node in tree.body
    )


def test_mechanics_validation_has_one_module_owner():
    path = ROOT / "src" / "genshin_engine" / "mechanics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    local_functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert "validate_mechanics_model" not in local_functions
    assert "validate_elemental_counter_bindings" not in local_functions
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "genshin_engine.mechanics_validation"
        for node in tree.body
    )


def test_damage_reporting_uses_public_projection_and_validation_ports():
    path = ROOT / "src" / "genshin_engine" / "damage_reporting.py"
    source = path.read_text(encoding="utf-8")
    assert "from .mechanics import _project" not in source
    assert "from .mechanics_projection import project_mechanics_inputs" in source
    assert "from .mechanics_validation import validate_mechanics_model" in source


def test_post_materialization_uses_public_mechanics_validation_port():
    path = ROOT / "src" / "genshin_app" / "equipment" / "compose_post_materialization.py"
    source = path.read_text(encoding="utf-8")
    assert "from genshin_engine.mechanics import validate_elemental_counter_bindings" not in source
    assert (
        "from genshin_engine.mechanics_validation import "
        "validate_elemental_counter_bindings"
    ) in source


def test_world_and_packet_use_public_canonical_reference_port():
    for relative in ("elemental_world.py", "elemental_stellar_packet.py"):
        path = ROOT / "src" / "genshin_engine" / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "genshin_engine.special_reactions"
        ]
        assert all(not alias.name.startswith("_REFERENCE_")
                   and alias.name != "_copy_special_runtime_for_reference"
                   for node in imports for alias in node.names)


def test_panel_basis_does_not_import_composer_private_helpers():
    path = ROOT / "src" / "genshin_app" / "equipment" / "panel_basis.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "composer"
        and any(alias.name.startswith("_") for alias in node.names)
        for node in ast.walk(tree)
    )


def test_runtime_event_router_has_one_frozen_owner_per_type():
    seen = []
    router = RuntimeEventRouter({"event.test": lambda event, cycle, parent: seen.append(
        (event["id"], cycle, parent["id"])
    )})
    assert router.dispatch({"type": "event.test", "id": "e1"}, 2, {"id": "p1"}) is True
    assert router.dispatch({"type": "event.unknown", "id": "e2"}, 2, {"id": "p1"}) is False
    assert seen == [("e1", 2, "p1")]
    assert router.event_types == ("event.test",)
