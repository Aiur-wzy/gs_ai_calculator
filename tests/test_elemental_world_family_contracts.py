"""Family proof leaves inspect roots; World alone commits and publishes them."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest
from test_elemental_world_lunar_electro_integrity import _world
from test_elemental_world_stellar_superconduct_deadline import world_with_stellar_field
from test_elemental_world_stellar_swirl_deadline_adversarial import (
    seed_vortex,
    world_with_targets,
)

from genshin_engine import elemental_world as owner
from genshin_engine.special_reaction_reference import copy_runtime


@pytest.mark.parametrize("family", ["lunar_electro", "stellar_swirl", "stellar_field"])
def test_family_proofs_do_not_change_world_state_cursor_or_root_identity(family):
    world = (_world() if family == "lunar_electro"
             else world_with_stellar_field(layers=3) if family == "stellar_field"
             else world_with_targets([("enemy", (), "known")]))
    if family == "stellar_swirl":
        seed_vortex(world)
    state = copy.deepcopy(world.state())
    events = copy.deepcopy(world.drain_generated_events())
    roots = (world.special_runtime, world.crystallize_world, world.bloom_core_state)
    if family == "lunar_electro":
        clouds = copy.deepcopy(world.special_runtime.lunar_cloud_by_area)
        world._derive_lunar_electro_schedule_contract(
            boundary_tick=250_000, clouds_before=clouds,
            reference_runtime=copy_runtime(world.special_runtime))
    elif family == "stellar_field":
        world._freeze_stellar_superconduct_deadlines(4_000_000)
    else:
        world._freeze_stellar_swirl_deadlines(3_000_000)
    assert world.state() == state
    assert world.drain_generated_events() == events
    assert all(actual is expected for actual, expected in zip(
        (world.special_runtime, world.crystallize_world, world.bloom_core_state), roots, strict=True))


def test_family_delegate_receives_the_original_world_once(monkeypatch):
    world = _world()
    seen = []

    def prove(received, *, boundary_tick, clouds_before, reference_runtime):
        seen.append((received, boundary_tick, clouds_before, reference_runtime))
        return {"fixture": 1}, {}

    monkeypatch.setattr(owner, "derive_lunar_electro_schedule_contract", prove)
    clouds, reference = {}, copy_runtime(world.special_runtime)
    assert world._derive_lunar_electro_schedule_contract(
        boundary_tick=1, clouds_before=clouds, reference_runtime=reference) == ({"fixture": 1}, {})
    assert len(seen) == 1 and seen[0][0] is world
    assert seen[0][2] is clouds and seen[0][3] is reference


def test_readonly_port_has_no_commit_publish_restore_or_advance_methods():
    root = Path(owner.__file__).parent
    port = ast.parse((root / "elemental_world_reaction_port.py").read_text(encoding="utf-8"))
    contract = next(node for node in port.body if isinstance(node, ast.ClassDef))
    names = {node.name for node in contract.body if isinstance(node, ast.FunctionDef)}
    assert not any(word in name for word in ("commit", "publish", "restore", "advance") for name in names)
    for family in ("lunar_electro", "stellar_swirl", "stellar_field"):
        tree = ast.parse((root / f"elemental_world_{family}_contracts.py").read_text(encoding="utf-8"))
        assert not any(isinstance(node, ast.ImportFrom)
                       and node.module == "genshin_engine.elemental_world" for node in ast.walk(tree))
        assert not any(isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                       and isinstance(node.value, ast.Name) and node.value.id == "world"
                       for node in ast.walk(tree))
