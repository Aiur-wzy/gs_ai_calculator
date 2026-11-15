"""genshin_engine：原神拉表计算引擎（纯计算核心，不碰数据库）。

规格权威：skill/references/mechanics/damage-formula.md（用户校订版）。
设计契约：docs/design/engine.md（一期定稿）。
"""

from importlib import import_module

from genshin_engine.runtime_dependencies import PUBLIC_ENGINE_EXPORTS as _EXPORTS

# Preserve public objects and their identity without loading the scheduler,
# all handlers and implementation fingerprints merely to import a schema.


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))

__all__ = [
    "PatchError",
    "TemplateError",
    "apply_patch",
    "calculate",
    "convert_v1_to_v2",
    "effect_matches_hit",
    "execute_mechanics",
    "get_registry",
    "mechanics_capabilities",
    "validate_mechanics_model",
    "validate_template",
]
