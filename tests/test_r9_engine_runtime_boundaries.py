"""Regression cases for r8 dynamic imports, static views and input ordering."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app.equipment.schema import PlanError, new_plan, validate_plan
from genshin_engine.mechanics_schema import empty_mechanics_model
from genshin_engine.runtime_dependencies import (
    engine_runtime_module_closure,
    validate_registered_runtime_dependencies,
)
from genshin_engine.template import TemplateError

ROOT = Path(__file__).resolve().parents[1]


def _plan():
    plan = new_plan()
    plan.update(plan_version=3, mechanics_model=empty_mechanics_model(), rotation_model={
        "schema_version": 1, "characters": [], "abilities": [], "actions": [], "events": []})
    return plan


@pytest.mark.parametrize("field", ["abilities", "actions", "characters", "events"])
@pytest.mark.parametrize("value", ["text", 1, True, {}, [0], [None], ["text"], [[]], [False], [[{}]]])
def test_deep_rotation_shape_rejected_before_resource_traversal(field, value):
    plan = _plan()
    plan["rotation_model"][field] = value
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as error:
        validate_plan(plan)
    assert error.value.reason == "invalid_plan"
    assert any(f"rotation_model.{field}" in issue["path"] for issue in error.value.details)
    assert plan == before


@pytest.mark.parametrize("field", ["cooldown", "energy"])
@pytest.mark.parametrize("value", [0, False, "text", [], [None]])
def test_deep_ability_properties_reject_with_path(field, value):
    plan = _plan()
    plan["rotation_model"]["abilities"] = [{"id": "a", "character": "c", "action_type_id": "action.elemental_skill", field: value}]
    with pytest.raises(PlanError) as error:
        validate_plan(plan)
    assert any(f"abilities[0].{field}" in issue["path"] for issue in error.value.details)


@pytest.mark.parametrize("field", ["hit_events", "rules", "hit_patterns", "initial_state"])
def test_mechanics_shape_rejected_before_consumers(field):
    plan = _plan()
    plan["mechanics_model"][field] = 1
    with pytest.raises(PlanError) as error:
        validate_plan(plan)
    assert any(f"mechanics_model.{field}" in issue["path"] for issue in error.value.details)


def test_unknown_draft_values_are_not_zeroed():
    plan = _plan()
    plan["rotation_model"]["abilities"] = [{"id": "a", "character": "c", "action_type_id": "action.elemental_skill",
                                            "cooldown": None, "energy": None}]
    before = copy.deepcopy(plan)
    validate_plan(plan)
    assert before == plan


def test_template_error_classification_is_typed_and_overridable():
    error = TemplateError("a custom.damage namespace is malformed")
    assert error.category == "request_error" and error.blocking_scope == "request"
    unsupported = TemplateError("known unsupported feature", code="feature_missing",
                                category="engine_capability_missing")
    assert unsupported.category == "engine_capability_missing"
    assert unsupported.blocking_scope == "engine_capability"


def test_static_views_do_not_load_runtime_handlers_in_fresh_interpreter():
    script = r'''
import builtins, json
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in {"genshin_engine.elemental_world", "genshin_engine.special_replacement_handlers"}:
        raise ModuleNotFoundError("injected runtime dependency unavailable", name=name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from genshin_app.agent_chat.native_static_capability_views import independent_capability_document
cases = [("mechanics_capabilities", ["schema"]), ("mechanics_capabilities", ["empty_model"]),
         ("mechanics_capabilities", ["target_scene"]),
         ("special_reaction_capabilities", ["input_contract", "schemas"]),
         ("special_reaction_capabilities", ["qualification_schema"]),
         ("special_reaction_capabilities", ["engine_catalog", "profiles"])]
for resource, tokens in cases:
    document = independent_capability_document(resource, tokens)
    for token in tokens:
        document = document[token]
    assert document
assert independent_capability_document("mechanics_capabilities", []) is None
assert independent_capability_document("special_reaction_capabilities", ["engine_catalog", "handler_registry"]) is None
print(json.dumps({"views": len(cases)}))
'''
    proc = subprocess.run([sys.executable, "-c", script], cwd=ROOT, text=True,
                          capture_output=True, env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"views": 6}


def test_declared_roots_match_actual_registry_and_reject_new_family():
    from genshin_engine.special_replacement_handlers import (
        registered_replacement_bindings,
    )
    bindings = registered_replacement_bindings()
    validate_registered_runtime_dependencies(bindings)
    with pytest.raises(ValueError, match="family_set_mismatch"):
        validate_registered_runtime_dependencies([*bindings, SimpleNamespace(family="undeclared_family")])
    altered = list(bindings)
    first = altered[0]
    altered[0] = SimpleNamespace(family=first.family, handler=SimpleNamespace(__module__="genshin_engine.undeclared"))
    with pytest.raises(ValueError, match="handler_undeclared"):
        validate_registered_runtime_dependencies(altered)


def test_closure_includes_dynamic_abi_root_and_transitive_dependencies():
    closure = engine_runtime_module_closure(ROOT / "src")
    assert "genshin_engine.elemental_world" in closure
    assert "genshin_engine.special_reaction_reference_deadlines_v1" in closure
    assert "genshin_engine.mechanics" in closure
    assert "genshin_engine.core" in closure
    assert all(path.is_file() for path in closure.values())


def _release_module():
    spec = importlib.util.spec_from_file_location("r9_engine_release", ROOT / "scripts/engine_runtime_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_guard_rejects_legal_inventory_missing_one_dynamic_root(monkeypatch, tmp_path):
    release = _release_module()
    closure = engine_runtime_module_closure(ROOT / "src")
    missing = "genshin_engine.elemental_world"
    inventory = {name: (0, 0, 0) for name in closure if name != missing}
    # A syntactically accepted archive inventory is insufficient if the
    # descriptor's independent dynamic requirement is absent.
    archive = SimpleNamespace(toc={"PYZ.pyz": (0, 0, 0, 0, "z")},
                              open_embedded_archive=lambda _: SimpleNamespace(toc=inventory))
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: archive)
    (tmp_path / "GenshinCalc.exe").write_bytes(b"fixture archive")
    with pytest.raises(release.EngineRuntimePackageError) as error:
        release.validate_engine_package_archive(tmp_path)
    assert error.value.reason == "engine_dynamic_dependency_missing"
    assert missing in error.value.details["modules"]


def test_archive_code_comparison_preserves_constants_not_paths():
    release = _release_module()
    one = compile("def f():\n return 1\n", "one.py", "exec", dont_inherit=True)
    two = compile("def f():\n return 1\n", "other/location.py", "exec", dont_inherit=True)
    three = compile("def f():\n return 2\n", "one.py", "exec", dont_inherit=True)
    assert release._code_contract(one) == release._code_contract(two)
    assert release._code_contract(one) != release._code_contract(three)


def test_static_view_values_are_exact_authoritative_values():
    from genshin_app.agent_chat.native_special_reaction_contracts import (
        special_reaction_capabilities,
    )
    from genshin_app.agent_chat.native_static_capability_views import (
        independent_capability_document,
    )
    from genshin_engine.mechanics_schema import mechanics_capabilities
    for name, producer, prefixes in [
        ("mechanics_capabilities", mechanics_capabilities, [("schema",), ("empty_model",), ("target_scene",)]),
        ("special_reaction_capabilities", special_reaction_capabilities,
         [("input_contract", "schemas"), ("qualification_schema",), ("engine_catalog", "profiles")]),
    ]:
        full = producer()
        for prefix in prefixes:
            selected = independent_capability_document(name, list(prefix))
            expected = full
            for token in prefix:
                selected, expected = selected[token], expected[token]
            assert selected == expected


def test_native_plan_static_views_preserve_representation_and_absolute_path():
    from genshin_app.agent_chat.native_static_capability_views import (
        independent_capability_document,
    )
    from genshin_app.equipment.service import plan_capabilities
    full = plan_capabilities()
    for name in ("schema", "semantic_constraints"):
        result = independent_capability_document("capabilities", [name], representation="genshin-plan")
        assert result[name] == full[name]
        assert independent_capability_document("capabilities", [name], representation="whiteboard") is None
        assert independent_capability_document("capabilities", [name]) is None


def test_source_interpreter_cannot_claim_frozen_dependency_execution():
    from genshin_engine.runtime_dependencies import verify_frozen_engine_dependencies
    with pytest.raises(RuntimeError, match="not_frozen"):
        verify_frozen_engine_dependencies()
