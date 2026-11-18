"""Declared dynamic runtime/ABI roots, without importing the implementations.

This is a generic family contract shared by runtime fingerprinting, packaging
and package qualification. Ordinary static imports are followed by the build
tool; this is not a collect-all list of the project or a character registry.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import MappingProxyType

CONTRACT = "genshin-engine-runtime-dependencies-v1"
FROZEN_MANIFEST = "engine-runtime-dependencies.json"
PUBLIC_ENGINE_EXPORTS = MappingProxyType({
    "calculate": "core",
    "execute_mechanics": "mechanics",
    "mechanics_capabilities": "mechanics_schema",
    "validate_mechanics_model": "mechanics_validation",
    "effect_matches_hit": "registry", "get_registry": "registry",
    **{name: "template" for name in ("PatchError", "TemplateError", "apply_patch",
                                     "convert_v1_to_v2", "validate_template")},
})
_ABI_COMMON = (
    "genshin_engine.special_replacement_contracts", "genshin_engine.special_damage_formulas",
    "genshin_engine.constants", "genshin_engine.formulas", "genshin_engine.special_reactions",
    "genshin_engine.elemental_world", "genshin_engine.special_reaction_contracts",
    "genshin_engine.special_reaction_profiles", "genshin_engine.special_reaction_validation",
)
_FAMILIES = MappingProxyType({
    "stellar_superconduct": ("genshin_engine.special_replace_stellar_field",
                             ("genshin_engine.special_deadline_stellar_field",)),
    "stellar_swirl": ("genshin_engine.special_replace_stellar_swirl",
                      ("genshin_engine.special_replace_stellar_packet",
                       "genshin_engine.special_stellar_initial_upgrade",
                       "genshin_engine.special_stellar_detonation")),
    "lunar_electro": ("genshin_engine.special_replace_lunar_electro",
                      ("genshin_engine.special_lunar_tick_admission",)),
    "lunar_bloom": ("genshin_engine.special_replace_lunar_bloom",
                    ("genshin_engine.special_deadline_lunar_bloom",)),
    "lunar_crystallize": ("genshin_engine.special_replace_lunar_crystallize", ()),
})
_DATA = (
    "genshin_db/resources/mechanics/special-reaction-profiles.json",
    "genshin_db/resources/mechanics/direct-special-damage-profiles.json",
    "genshin_db/resources/mechanics/special-reaction-capabilities.json",
)


def family_runtime_modules(family: str, *, handler_module: str) -> tuple[str, ...]:
    handler, extras = _FAMILIES[family]
    if handler_module != handler:
        raise ValueError(f"engine_dependency_handler_undeclared:{family}:{handler_module}")
    return tuple(sorted({handler, *_ABI_COMMON, *extras}))


def validate_registered_runtime_dependencies(bindings) -> None:
    """Independent actual registry enumeration detects new undeclared roots."""
    if {binding.family for binding in bindings} != set(_FAMILIES):
        raise ValueError("engine_dependency_family_set_mismatch")
    for binding in bindings:
        family_runtime_modules(binding.family, handler_module=binding.handler.__module__)
        if (binding.contract_version, binding.implementation_version) != (1, 2):
            raise ValueError(f"engine_dependency_handler_version_undeclared:{binding.family}")


def engine_runtime_dependency_descriptor() -> dict:
    return {
        "contract": CONTRACT,
        "families": {
            family: {"execution_roots": [handler],
                     "abi_roots": list(family_runtime_modules(family, handler_module=handler)),
                     "contract_version": 1, "implementation_version": 2}
            for family, (handler, _) in _FAMILIES.items()
        },
        "package_data": list(_DATA),
        "public_api_roots": sorted({"genshin_engine." + name for name in PUBLIC_ENGINE_EXPORTS.values()}),
        "native_extensions": [
            {"module": "_decimal", "platform": "win32"},
            {"module": "_sqlite3", "platform": "win32"},
        ],
    }


def module_source(source_root: Path, module: str) -> Path | None:
    base = source_root.joinpath(*module.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def engine_runtime_module_closure(source_root: Path) -> dict[str, Path]:
    """Conservative static closure of declared dynamic roots, build-time only.

    Follow conditional/relative imports too. Attribute imports are only
    treated as modules if the exact source module exists. Missing declared
    roots fail; no handler import or source execution is needed here.
    """
    pending = {"genshin_engine.runtime_dependencies", "genshin_engine.special_handler_registry",
               *("genshin_engine." + name for name in PUBLIC_ENGINE_EXPORTS.values())}
    for family, (handler, _) in _FAMILIES.items():
        pending.update(family_runtime_modules(family, handler_module=handler))
    result = {}
    while pending:
        name = pending.pop()
        if name in result:
            continue
        path = module_source(source_root, name)
        if path is None:
            raise ValueError(f"engine_dependency_source_missing:{name}")
        result[name] = path
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        dependencies = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = (importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                          if node.level else node.module or "")
                dependencies.add(prefix)
                dependencies.update(prefix + "." + alias.name for alias in node.names)
        # Importing an exact project module also initializes its parents.
        parent = name.rpartition(".")[0]
        if parent:
            dependencies.add(parent)
        for dependency in dependencies:
            if (dependency.startswith(("genshin_engine", "genshin_app", "genshin_db", "genshin_runtime"))
                    and dependency not in result and module_source(source_root, dependency) is not None):
                pending.add(dependency)
    return dict(sorted(result.items()))


def verify_frozen_engine_dependencies() -> dict:
    """Execute the declared import graph using the real frozen loader only.

    No fake sys.frozen flag, working-tree import or installed package fallback
    qualifies. The build-generated closure is itself checked against the
    compiled descriptor's roots; both are covered by the package manifest.
    """
    if not getattr(sys, "frozen", False) or not getattr(sys, "_MEIPASS", None):
        raise RuntimeError("engine_dependency_execution_not_frozen")
    bundle = Path(sys._MEIPASS).resolve(strict=True)
    manifest = json.loads((bundle / "genshin_engine" / FROZEN_MANIFEST).read_text(encoding="utf-8"))
    descriptor = engine_runtime_dependency_descriptor()
    modules = manifest.get("modules")
    if (set(manifest) != {"contract", "modules"} or manifest["contract"] != CONTRACT
            or not isinstance(modules, list) or not modules
            or any(not isinstance(name, str) or not name.startswith("genshin_") for name in modules)
            or modules != sorted(set(modules))):
        raise RuntimeError("engine_dependency_manifest_invalid")
    required = {"genshin_engine.runtime_dependencies", "genshin_engine.special_handler_registry",
                *descriptor["public_api_roots"]}
    for family in descriptor["families"].values():
        required.update(family["abi_roots"])
    if not required <= set(modules):
        raise RuntimeError("engine_dependency_manifest_missing_root")
    for name in modules:
        module = importlib.import_module(name)
        spec = getattr(module, "__spec__", None)
        loader = getattr(spec, "loader", None)
        origin = getattr(spec, "origin", None)
        if (not isinstance(origin, str) or not Path(origin).resolve().is_relative_to(bundle)
                or type(loader).__name__ != "PyiFrozenLoader"
                or not type(loader).__module__.endswith("pyimod02_importers")):
            raise RuntimeError(f"engine_dependency_loader_not_frozen:{name}")
    from genshin_engine.special_replacement_handlers import (
        registered_replacement_bindings,
    )
    validate_registered_runtime_dependencies(registered_replacement_bindings())
    native = []
    for extension in descriptor["native_extensions"]:
        if extension["platform"] != sys.platform:
            continue
        module = importlib.import_module(extension["module"])
        origin = getattr(module.__spec__, "origin", None)
        if not isinstance(origin, str) or not Path(origin).resolve().is_relative_to(bundle):
            raise RuntimeError(f"engine_native_extension_outside_bundle:{extension['module']}")
        native.append(extension["module"])
    return {"contract": "genshin-engine-frozen-dependency-execution-v1",
            "layer": "frozen_cli_execution", "modules": modules,
            "families": sorted(descriptor["families"]), "native_extensions": native,
            "source_fallback": False}
