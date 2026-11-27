"""Select independent views before invoking a large capability producer.

Returned documents retain the caller's absolute JSON Pointer. They are only
ancestors of one selected view, never advertised as a partial root catalog.
Input tokens are decoded/validated by the existing native tool pointer parser.
"""
from __future__ import annotations

import copy


def _mechanics_schema():
    from genshin_engine.mechanics_schema import MECHANICS_SCHEMA
    return copy.deepcopy(MECHANICS_SCHEMA)


def _empty_model():
    from genshin_engine.mechanics_schema import empty_mechanics_model
    return empty_mechanics_model()


def _target_scene():
    from genshin_engine.mechanics_schema import target_scene_capabilities
    return target_scene_capabilities()


def _special_schemas():
    from genshin_engine.special_reaction_schema import special_reaction_input_schemas
    return copy.deepcopy(special_reaction_input_schemas())


def _qualification_schema():
    from genshin_engine.special_reaction_schema import QUALIFICATION_SCHEMA
    return copy.deepcopy(QUALIFICATION_SCHEMA)


def _reaction_profiles():
    from genshin_engine.special_reaction_profiles import (
        load_reviewed_special_reaction_profiles,
    )
    return copy.deepcopy(load_reviewed_special_reaction_profiles())


def _plan_schema():
    from genshin_app.equipment.plan_description import plan_description_schema
    return plan_description_schema()


def _plan_semantics():
    from genshin_app.equipment.plan_description import plan_semantic_constraints
    return plan_semantic_constraints()


_VIEWS = {
    "mechanics_capabilities": {
        ("schema",): _mechanics_schema,
        ("empty_model",): _empty_model,
        ("target_scene",): _target_scene,
    },
    "special_reaction_capabilities": {
        ("input_contract", "schemas"): _special_schemas,
        ("qualification_schema",): _qualification_schema,
        ("engine_catalog", "profiles"): _reaction_profiles,
    },
}


def independent_capability_document(resource: str, pointer_tokens: list[str], *, representation: str | None = None):
    """Return a selected ancestor document, or None for the full producer.

    Registry/fingerprint/capability claims intentionally have no independent
    fallback: they still load and attest the actual implementation.
    """
    views = _VIEWS.get(resource, {})
    if resource == "capabilities" and representation == "genshin-plan":
        views = {("schema",): _plan_schema, ("semantic_constraints",): _plan_semantics}
    for prefix, producer in views.items():
        if tuple(pointer_tokens[:len(prefix)]) == prefix:
            value = producer()
            for token in reversed(prefix):
                value = {token: value}
            return value
    return None


def independent_capability_paths(resource: str) -> list[str]:
    return ["/" + "/".join(prefix) for prefix in _VIEWS.get(resource, {})]
