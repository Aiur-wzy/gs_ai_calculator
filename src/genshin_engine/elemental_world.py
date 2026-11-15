"""World coordinator for multiple elemental targets.

The existing :class:`~genshin_engine.elemental.ElementalRuntime` deliberately
owns one target.  This module composes those proven state machines without
teaching them guessed spatial rules.  The first slice provides stable target
registration, static area membership, shared ordinary Crystallize/Bloom
objects, atomic cross-target rollback and a replayable generated-event cursor.

Spatial distance, movement, collision and special-reaction target selection
remain consumers of this coordinator.  An unknown area therefore fails closed
instead of silently behaving like an empty battlefield.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from types import FunctionType, ModuleType
from typing import Any, TypeVar

import genshin_engine.elemental as _elemental_module
from genshin_engine.elemental import ElementalPending, ElementalRuntime
from genshin_engine.elemental_bloom_core import BloomCoreState
from genshin_engine.elemental_crystallize import CrystallizeWorldState
from genshin_engine.elemental_reference_kernel_v1 import (
    build_reference_elemental_kernel_v1,
)
from genshin_engine.elemental_reference_multi_v4 import (
    build_reference_multi_resolver_v4,
)
from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.elemental_world_layer_binding import require_stellar_layer_binding
from genshin_engine.elemental_world_lunar_electro_contracts import (
    derive_lunar_electro_schedule_contract,
    validate_lunar_electro_admission_contract,
    validate_lunar_electro_due_contract,
    validate_lunar_electro_tick,
)
from genshin_engine.elemental_world_lunar_planner import (
    finalize_lunar_electro_candidate,
    plan_lunar_electro_world_batch,
)
from genshin_engine.elemental_world_star_planner import plan_stellar_swirl_world_batch
from genshin_engine.elemental_world_stellar_field_contracts import (
    consume_stellar_superconduct_deadline_batch,
    freeze_stellar_superconduct_deadlines,
    validate_stellar_superconduct_field_state,
)
from genshin_engine.elemental_world_stellar_swirl_contracts import (
    freeze_stellar_swirl_deadlines,
    stellar_swirl_pairs,
    validate_stellar_swirl_passthrough,
)
from genshin_engine.rotation_port import TICKS
from genshin_engine.special_reaction_actual import (
    SpecialReactionPending,
    SpecialReactionRuntime,
)
from genshin_engine.special_reaction_reference import (
    admit_lunar_electro_tick as _REFERENCE_ADMIT_LUNAR_ELECTRO_TICK_IMPL,
)
from genshin_engine.special_reaction_reference import (
    advance_world_pre_expiry as _REFERENCE_ADVANCE_WORLD_PRE_EXPIRY,
)
from genshin_engine.special_reaction_reference import (
    copy_runtime as _copy_special_runtime_for_reference,
)
from genshin_engine.special_state_observation import (
    canonical_stellar_deadline_rows,
    canonical_stellar_runtime_snapshot,
    canonical_stellar_trace,
)

_T = TypeVar("_T")
_ACTIVE_ATOMIC_WORLD: ContextVar[object | None] = ContextVar(
    "genshin_active_atomic_world", default=None)

# Keep canonical producer entry points for the second half of the world
# two-phase contract.  Their function bodies and module helper globals both
# come from the import-time frozen SpecialReactionRuntime graph, while the
# live call remains observable and must match the canonical result/post-state.
_REFERENCE_ADMIT_LUNAR_ELECTRO_TICK = (
    _REFERENCE_ADMIT_LUNAR_ELECTRO_TICK_IMPL
)
_REFERENCE_ELEMENTAL_METHOD_NAMES = frozenset(
    name
    for name, value in vars(ElementalRuntime).items()
    if callable(value) or isinstance(value, (classmethod, staticmethod))
)
_SPECIAL_RUNTIME_METHOD_NAMES = frozenset(
    name
    for name, value in vars(SpecialReactionRuntime).items()
    if (callable(value)
        or isinstance(value, (classmethod, staticmethod, property)))
)


def _copy_elemental_global(
    value: Any,
    memo: dict[int, Any],
) -> Any:
    """Copy mutable elemental globals without trying to copy modules.

    Reference methods execute against an import-time module namespace.  A
    separate namespace only freezes rebinding; mutable rule tables must also
    be copied so later in-place edits cannot rewrite the reference contract.
    Types and modules are stable dependencies rather than contract data and
    are deliberately retained by identity.
    """
    if isinstance(value, (ModuleType, type, FunctionType)):
        return value
    try:
        return copy.deepcopy(value, memo)
    except (TypeError, ValueError):
        return value


def _clone_elemental_function(
    function: FunctionType,
    namespace: dict[str, Any],
    memo: dict[int, Any],
) -> FunctionType:
    """Clone one function onto the frozen elemental module namespace."""
    cloned = FunctionType(
        function.__code__,
        namespace,
        name=function.__name__,
        argdefs=_copy_elemental_global(function.__defaults__, memo),
        closure=function.__closure__,
    )
    cloned.__kwdefaults__ = _copy_elemental_global(
        function.__kwdefaults__, memo)
    cloned.__annotations__ = _copy_elemental_global(
        function.__annotations__, memo)
    cloned.__dict__.update(_copy_elemental_global(function.__dict__, memo))
    cloned.__doc__ = function.__doc__
    cloned.__module__ = function.__module__
    cloned.__qualname__ = function.__qualname__
    return cloned


def _freeze_elemental_module_namespace() -> dict[str, Any]:
    """Freeze every module-local helper and mutable rule table as one graph."""
    source = vars(_elemental_module)
    memo: dict[int, Any] = {}
    namespace: dict[str, Any] = {}
    local_functions = {
        name: value
        for name, value in source.items()
        if (isinstance(value, FunctionType)
            and value.__module__ == _elemental_module.__name__)
    }
    for name, value in source.items():
        if name in local_functions:
            continue
        if name == "__builtins__":
            namespace[name] = value
        else:
            namespace[name] = _copy_elemental_global(value, memo)
    for name, function in local_functions.items():
        clone = _clone_elemental_function(function, namespace, memo)
        namespace[name] = clone
    return namespace


def _freeze_elemental_runtime_type() -> type[ElementalRuntime]:
    """Keep an import-time reference implementation for world previews.

    A plain saved method alias is not sufficient here: the saved method can
    still dispatch through other class attributes that a test or integration
    wrapper replaces later.  Copying every descriptor defined by
    ``ElementalRuntime`` into a private subclass freezes the complete target
    method graph while retaining the same instance layout.  Reference copies
    are constructed without calling ``__init__`` and receive a deep copy of
    all mutable instance data, so neither preview can mutate the live target.
    """
    frozen_globals = _freeze_elemental_module_namespace()
    memo: dict[int, Any] = {}
    namespace: dict[str, Any] = {}
    for name, value in vars(ElementalRuntime).items():
        if name in {"__dict__", "__weakref__", "__module__"}:
            continue
        if isinstance(value, FunctionType):
            namespace[name] = _clone_elemental_function(
                value, frozen_globals, memo)
        elif isinstance(value, staticmethod):
            namespace[name] = staticmethod(_clone_elemental_function(
                value.__func__, frozen_globals, memo))
        elif isinstance(value, classmethod):
            namespace[name] = classmethod(_clone_elemental_function(
                value.__func__, frozen_globals, memo))
        elif isinstance(value, property):
            namespace[name] = property(
                (_clone_elemental_function(value.fget, frozen_globals, memo)
                 if value.fget is not None else None),
                (_clone_elemental_function(value.fset, frozen_globals, memo)
                 if value.fset is not None else None),
                (_clone_elemental_function(value.fdel, frozen_globals, memo)
                 if value.fdel is not None else None),
                value.__doc__,
            )
        else:
            namespace[name] = _copy_elemental_global(value, memo)
    namespace.update(build_reference_elemental_kernel_v1(frozen_globals))
    namespace["_apply_multi"] = build_reference_multi_resolver_v4(frozen_globals)
    namespace["__module__"] = __name__
    reference_type = type(
        "_WorldReferenceElementalRuntime",
        (ElementalRuntime,),
        namespace,
    )
    # Functions that explicitly name the runtime should stay inside the same
    # frozen graph.  Current methods mostly dispatch through ``self``; keeping
    # the binding canonical prevents a future helper from silently escaping.
    frozen_globals["ElementalRuntime"] = reference_type
    return reference_type


_REFERENCE_ELEMENTAL_RUNTIME = _freeze_elemental_runtime_type()
_REFERENCE_COMMIT_LUNAR_ELECTRO_TICK = (
    _REFERENCE_ELEMENTAL_RUNTIME._commit_lunar_electro_tick_receipt
)

_ELEMENTAL_RUNTIME_INSTANCE_FIELDS = frozenset({
    "model", "target", "profiles", "icd_rules", "freeze_state",
    "multi_state", "crystallize_state", "special_runtime",
    "bloom_core_state", "generated_events", "aura", "time",
    "recent_elemental_applications", "icd", "counter_bindings",
    "damage_icd", "trace", "window_state",
})
_SPECIAL_RUNTIME_INSTANCE_FIELDS = frozenset({
    "model", "party_member_ids", "party_capability_tags", "time", "trace",
    "scheduled", "state_by_target", "party_state", "stellar_field_by_area",
    "vortex_by_area", "moondrift_by_area", "lunar_cloud_by_area",
    "_pending_world_lunar_electro_expiry",
    "_world_defer_lunar_electro_expiry",
})
_CRYSTALLIZE_WORLD_INSTANCE_FIELDS = frozenset({
    "shard_lifetime_ticks", "shield_lifetime_ticks",
    "overflow_cleanup_ticks", "stable_shard_limit", "shards",
    "active_shield", "overflow_since", "revision",
})
_BLOOM_CORE_INSTANCE_FIELDS = frozenset({
    "lifetime_ticks", "field_limit", "time", "cores", "replacement_cores", "conversions", "revision",
})
_CRYSTALLIZE_WORLD_METHOD_NAMES = frozenset(
    name for name, value in vars(CrystallizeWorldState).items()
    if (callable(value)
        or isinstance(value, (classmethod, staticmethod, property)))
)
_BLOOM_CORE_METHOD_NAMES = frozenset(
    name for name, value in vars(BloomCoreState).items()
    if (callable(value)
        or isinstance(value, (classmethod, staticmethod, property)))
)
_TARGET_IDENTITY_FIELDS = (
    "freeze_state", "multi_state", "crystallize_state", "special_runtime",
    "bloom_core_state",
)


def _plain_json_value(value: Any, *, path: str) -> Any:
    """Return a detached exact-builtin JSON graph or reject before callbacks.

    Exact type checks deliberately precede iteration, equality and deepcopy.
    A dict/list subclass can otherwise execute attacker-controlled hooks while
    the world is deciding whether a transaction may commit.
    """
    value_type = type(value)
    if value_type is dict:
        copied = {}
        for key, item in dict.items(value):
            if type(key) is not str:
                raise TypeError(f"{path}的对象键必须是普通字符串。")
            copied[key] = _plain_json_value(item, path=f"{path}.{key}")
        return copied
    if value_type is list:
        return [
            _plain_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(list.__iter__(value))
        ]
    if value is None or value_type in (str, bool, int):
        return value
    if value_type is float and math.isfinite(value):
        return value
    raise TypeError(f"{path}必须是普通JSON值。")


def _plain_event_batch(events: Any) -> list[dict]:
    if type(events) is not list:
        raise TypeError("events必须是普通JSON数组。")
    rows = _plain_json_value(events, path="events")
    for row in rows:
        if (type(row) is not dict
                or type(row.get("type")) is not str or not row["type"]
                or type(row.get("event_id")) is not str
                or not row["event_id"]):
            raise ValueError("世界派生事件必须包含非空type与event_id。")
    return rows


def _domain_structure_violations(kind: str, state: Any) -> list[str]:
    """Return structural, never business-value, violations for live state.

    Atomic operations are allowed to change gauges, clocks and collections.
    They are not allowed to replace the finite engine containers/scalars with
    unrelated Python objects that only fail later in a different subsystem.
    Optional domain records deliberately admit their documented ``None`` to
    exact-dict transitions.
    """
    fields = object.__getattribute__(state, "__dict__")

    def exact(value: Any, *types: type) -> bool:
        return type(value) in types

    def number(value: Any) -> bool:
        return (type(value) in (int, float)
                and math.isfinite(value))

    def optional_dict(value: Any) -> bool:
        return value is None or type(value) is dict

    def dict_rows(value: Any) -> bool:
        return (type(value) is list
                and all(type(row) is dict for row in list.__iter__(value)))

    def keyed_dict_rows(value: Any) -> bool:
        return (type(value) is dict
                and all(type(key) is str and type(row) is dict
                        for key, row in dict.items(value)))

    predicates: dict[str, Callable[[Any], bool]]
    if kind == "world":
        predicates = {
            "time": lambda value: exact(value, int),
            "_targets": lambda value: exact(value, dict),
            "_areas_by_target": lambda value: exact(value, dict),
            "_targets_by_area": lambda value: exact(value, dict),
            "_generated_events": lambda value: exact(value, list),
            "_transaction_depth": lambda value: exact(value, int),
        }
    elif kind == "elemental_runtime":
        predicates = {
            "model": lambda value: exact(value, dict),
            "target": lambda value: exact(value, str) and bool(value),
            "profiles": lambda value: exact(value, dict),
            "icd_rules": lambda value: exact(value, dict),
            "generated_events": dict_rows,
            "aura": optional_dict,
            "time": lambda value: exact(value, int),
            "recent_elemental_applications": lambda value: exact(value, list),
            "icd": lambda value: exact(value, dict),
            "counter_bindings": lambda value: exact(value, dict),
            "damage_icd": lambda value: exact(value, dict),
            "trace": dict_rows,
            "window_state": optional_dict,
        }
    elif kind == "multi_state":
        predicates = {
            "auras": keyed_dict_rows,
            "freezable": lambda value: exact(value, bool),
            "unfreezable_resolution": (
                lambda value: value is None or exact(value, str)),
            "resolution_origin": lambda value: exact(value, str),
            "legacy_mode": lambda value: value is None or exact(value, str),
            "freeze_contract": lambda value: exact(value, dict),
            "time": number,
        }
    elif kind == "freeze_state":
        predicates = {
            "aura": optional_dict,
            "frozen": optional_dict,
            "history": number,
            "time": number,
            "revision": lambda value: exact(value, int),
        }
    elif kind == "crystallize_state":
        predicates = {
            "target_id": lambda value: exact(value, str) and bool(value),
            "next_reaction_time": lambda value: exact(value, int),
            "revision": lambda value: exact(value, int),
        }
    elif kind == "crystallize_world":
        predicates = {
            "shard_lifetime_ticks": lambda value: exact(value, int),
            "shield_lifetime_ticks": lambda value: exact(value, int),
            "overflow_cleanup_ticks": lambda value: exact(value, int),
            "stable_shard_limit": lambda value: exact(value, int),
            "shards": dict_rows,
            "active_shield": optional_dict,
            "overflow_since": (
                lambda value: value is None or exact(value, int)),
            "revision": lambda value: exact(value, int),
        }
    elif kind == "bloom_core_state":
        predicates = {
            "lifetime_ticks": lambda value: exact(value, int),
            "field_limit": lambda value: exact(value, int),
            "time": lambda value: exact(value, int),
            "cores": dict_rows,
            "replacement_cores": dict_rows,
            "conversions": lambda value: exact(value, dict),
            "revision": lambda value: exact(value, int),
        }
    elif kind == "special_runtime":
        predicates = {
            "model": lambda value: exact(value, dict),
            "party_member_ids": lambda value: exact(value, tuple),
            "party_capability_tags": lambda value: exact(value, dict),
            "time": lambda value: exact(value, int),
            "trace": dict_rows,
            "scheduled": dict_rows,
            "state_by_target": lambda value: exact(value, dict),
            "party_state": lambda value: exact(value, dict),
            "stellar_field_by_area": lambda value: exact(value, dict),
            "vortex_by_area": lambda value: exact(value, dict),
            "moondrift_by_area": lambda value: exact(value, dict),
            "lunar_cloud_by_area": lambda value: exact(value, dict),
            "_pending_world_lunar_electro_expiry": optional_dict,
            "_world_defer_lunar_electro_expiry": (
                lambda value: exact(value, bool)),
        }
    else:
        raise ValueError(f"未知运行时结构类型：{kind}")
    return [
        name for name, valid in predicates.items()
        if name not in fields or not valid(fields[name])
    ]


class ElementalWorldPending(ValueError):
    """A world fact or consumer is missing, so authoritative execution stops."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.issue = {
            "code": code,
            "message": message,
            "severity": "pending",
            **copy.deepcopy(details or {}),
        }


@dataclass(frozen=True, slots=True)
class ElementalWorldSnapshot:
    """Opaque rollback point; callers restore it through the owning world."""

    owner_token: object
    time: int
    registry_fingerprint: tuple[tuple[str, tuple[str, ...]], ...]
    target_states: dict[str, ElementalRuntime]
    crystallize_world: CrystallizeWorldState
    bloom_core_state: BloomCoreState
    special_runtime: SpecialReactionRuntime | None
    generated_events: list[dict]


class ElementalWorldRuntime:
    """Coordinate deterministic target runtimes in one battlefield.

    Area membership is static in this first slice.  ``special_runtime`` is
    world-owned but is intentionally not injected into each target runtime:
    doing so would advance one area state once per target.  Later special
    reaction consumers operate on it inside :meth:`execute_atomic` and route
    target-specific proposals through :meth:`get_target`.
    """

    def reaction_pending(self, code: str, message: str, details: dict | None = None) -> Exception:
        return ElementalWorldPending(code, message, details)

    def target_commit_snapshot(self, runtime):
        return self._target_commit_snapshot(runtime)

    def validate_lunar_electro_tick(self, tick, *, boundary_tick, seen_target_areas):
        return self._validate_lunar_electro_tick(
            tick, boundary_tick=boundary_tick, seen_target_areas=seen_target_areas)

    def validate_lunar_electro_admission_contract(self, tick, admitted, aura_snapshot):
        return self._validate_lunar_electro_admission_contract(tick, admitted, aura_snapshot)

    def target_identity_snapshot(self, runtime):
        return self._target_identity_snapshot(runtime)

    def reference_target_copy(self, runtime, *, preserve_trace=True):
        return self._reference_target_copy(runtime, preserve_trace=preserve_trace)

    def special_commit_snapshot(self, runtime):
        return self._special_commit_snapshot(runtime)

    def bind_stellar_layer_to_target_receipt(self, *args, **kwargs):
        return self._bind_stellar_layer_to_target_receipt(*args, **kwargs)

    def stellar_swirl_pairs(self, events, *, time_tick, source):
        return self._stellar_swirl_pairs(events, time_tick=time_tick, source=source)

    def validate_stellar_swirl_passthrough(self, event, *, time_tick, source):
        return self._validate_stellar_swirl_passthrough(event, time_tick=time_tick, source=source)

    def reaction_pending_from_runtime(self, exc: Exception) -> Exception:
        return self._pending_from_runtime(exc)

    def is_stellar_superconduct_deadline_row(self, event: dict) -> bool:
        return self._is_stellar_superconduct_deadline_row(event)

    def validate_stellar_superconduct_field_state(
        self, state: Any, *, area_id: str, phase: str, boundary_tick: int,
    ) -> None:
        return self._validate_stellar_superconduct_field_state(
            state, area_id=area_id, phase=phase, boundary_tick=boundary_tick)

    def __init__(
        self,
        *,
        crystallize_world: CrystallizeWorldState | None = None,
        bloom_core_state: BloomCoreState | None = None,
        special_runtime: SpecialReactionRuntime | None = None,
    ):
        if (crystallize_world is not None
                and not isinstance(crystallize_world, CrystallizeWorldState)):
            raise TypeError("crystallize_world必须是CrystallizeWorldState或None。")
        if (bloom_core_state is not None
                and not isinstance(bloom_core_state, BloomCoreState)):
            raise TypeError("bloom_core_state必须是BloomCoreState或None。")
        if (special_runtime is not None
                and not isinstance(special_runtime, SpecialReactionRuntime)):
            raise TypeError("special_runtime必须是SpecialReactionRuntime或None。")
        self.crystallize_world = (
            crystallize_world
            if crystallize_world is not None
            else CrystallizeWorldState()
        )
        self.bloom_core_state = (
            bloom_core_state
            if bloom_core_state is not None
            else BloomCoreState()
        )
        self.special_runtime = special_runtime
        self.time = 0
        self._targets: dict[str, ElementalRuntime] = {}
        self._areas_by_target: dict[str, tuple[str, ...]] = {}
        self._targets_by_area: dict[str, set[str]] = {}
        self._generated_events: list[dict] = []
        self._owner_token = object()
        self._transaction_depth = 0

    @staticmethod
    def _normalize_area_ids(area_ids: Iterable[str]) -> tuple[str, ...]:
        if isinstance(area_ids, (str, bytes)):
            raise TypeError("area_ids必须是区域ID集合，不能是单个字符串。")
        values = tuple(area_ids)
        if (not values
                or any(not isinstance(value, str) or not value.strip()
                       for value in values)
                or len(values) != len(set(values))):
            raise ValueError("area_ids必须包含至少一个唯一非空区域ID。")
        return tuple(sorted(values))

    def register_target(
        self,
        model: dict,
        *,
        area_ids: Iterable[str] = ("battlefield.primary",),
    ) -> ElementalRuntime:
        """Create and register one pristine single-target runtime."""
        if self._transaction_depth:
            raise RuntimeError("原子事务执行期间不能改变目标注册表。")
        if self.time != 0:
            raise ElementalWorldPending(
                "world_target_registration_after_start",
                "战场时间开始推进后不能补登记目标；请重新建立世界运行时。",
                {"world_time_tick": self.time},
            )
        if not isinstance(model, dict):
            raise TypeError("元素目标模型必须是对象。")
        if model.get("ruleset") != MULTI_AURA_RULESET:
            raise ValueError("多目标世界运行时首片仅接入gauge-multi-aura-v4。")
        target = (model.get("target") or {}).get("id")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("元素目标模型必须声明非空target.id。")
        if target in self._targets:
            raise ValueError("世界运行时目标ID重复。")
        areas = self._normalize_area_ids(area_ids)
        runtime = ElementalRuntime(
            model,
            crystallize_world=self.crystallize_world,
            bloom_core_state=self.bloom_core_state,
        )
        self._targets[target] = runtime
        self._areas_by_target[target] = areas
        for area_id in areas:
            self._targets_by_area.setdefault(area_id, set()).add(target)
        return runtime

    def get_target(self, target_id: str) -> ElementalRuntime:
        try:
            return self._targets[target_id]
        except (KeyError, TypeError) as exc:
            raise ElementalWorldPending(
                "world_target_unknown",
                "世界运行时没有登记该目标。",
                {"target_id": target_id},
            ) from exc

    def targets_in_area(self, area_id: str) -> tuple[str, ...]:
        if not isinstance(area_id, str) or not area_id.strip():
            raise ValueError("area_id必须是非空字符串。")
        if area_id not in self._targets_by_area:
            raise ElementalWorldPending(
                "world_area_unknown",
                "世界运行时没有登记该区域，不能把未知区域当作空区域。",
                {"area_id": area_id},
            )
        return tuple(sorted(self._targets_by_area[area_id]))

    def area_ids_for_target(self, target_id: str) -> tuple[str, ...]:
        self.get_target(target_id)
        return self._areas_by_target[target_id]

    def _registry_fingerprint(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple(sorted(self._areas_by_target.items()))

    @staticmethod
    def _target_commit_snapshot(runtime: ElementalRuntime) -> dict:
        """Capture target behavior plus mutable execution metadata."""
        actual_fields = frozenset(runtime.__dict__)
        missing_fields = _ELEMENTAL_RUNTIME_INSTANCE_FIELDS-actual_fields
        unexpected_fields = actual_fields-_ELEMENTAL_RUNTIME_INSTANCE_FIELDS
        invalid_fields = {
            name for name in unexpected_fields
            if (name not in _REFERENCE_ELEMENTAL_METHOD_NAMES
                or not callable(runtime.__dict__[name]))
        }
        if missing_fields or invalid_fields:
            raise ElementalWorldPending(
                "world_target_runtime_schema_mismatch",
                "目标运行时实例字段超出受审有限结构，不能提交世界事务。",
                {
                    "target_id": getattr(runtime, "target", None),
                    "missing_fields": sorted(missing_fields),
                    "unexpected_fields": sorted(invalid_fields),
                },
            )
        return {
            "instance_fields": sorted(_ELEMENTAL_RUNTIME_INSTANCE_FIELDS),
            "state": runtime.state(),
            "model": copy.deepcopy(runtime.model),
            "profiles": copy.deepcopy(runtime.profiles),
            "icd_rules": copy.deepcopy(runtime.icd_rules),
            "counter_bindings": copy.deepcopy(runtime.counter_bindings),
            "generated_events": copy.deepcopy(runtime.generated_events),
            "trace": copy.deepcopy(runtime.trace),
            "window_state": copy.deepcopy(runtime.window_state),
        }

    @staticmethod
    def _reference_target_copy(
        runtime: ElementalRuntime,
        *,
        preserve_trace: bool = True,
    ) -> ElementalRuntime:
        """Return an isolated target driven by import-time frozen methods.

        This is the normative side of target two-phase commits.  Dynamic
        methods remain observable on the ordinary preview and live target;
        their complete receipt and post-state must equal this reference run.
        ``preview_copy`` on the returned object also stays on the private
        frozen subclass, so nested previews cannot fall back to a subsequently
        patched production method.
        """
        if not isinstance(runtime, ElementalRuntime):
            raise TypeError("目标参考副本必须来自ElementalRuntime。")
        if not isinstance(preserve_trace, bool):
            raise TypeError("preserve_trace必须是布尔值。")
        reference = _REFERENCE_ELEMENTAL_RUNTIME.__new__(
            _REFERENCE_ELEMENTAL_RUNTIME)
        state = copy.deepcopy(runtime.__dict__)
        for name in _REFERENCE_ELEMENTAL_METHOD_NAMES:
            state.pop(name, None)
        reference.__dict__.update(state)
        if not preserve_trace:
            reference.trace = []
        return reference

    @staticmethod
    def _bind_stellar_layer_to_target_receipt(
        runtime: ElementalRuntime,
        receipt: dict,
        layer_event: dict | None,
        *,
        target_id: str,
        event_id: str,
        issue_code: str,
    ) -> None:
        """Bind one world-owned layer result before receipt verification.

        The target runtime cannot own the shared Stellar-Superconduct field,
        so the world adds this one audited field.  It is part of both the
        returned receipt and the target trace before either object is compared
        with the reference post-state; published rows must never be patched
        after commit verification.
        """
        if (not runtime.trace
                or runtime.trace[-1] != receipt
                or runtime.trace[-1].get("event_id") != event_id):
            raise ElementalWorldPending(
                issue_code,
                "风旋冰附着的目标回执与提交轨迹在计层绑定前已经不一致。",
                {"target_id": target_id, "event_id": event_id},
            )
        field = "stellar_superconduct_application"
        if field in receipt or field in runtime.trace[-1]:
            raise ElementalWorldPending(
                issue_code,
                "风旋冰附着的目标回执提前携带了未经世界核验的星超导计层。",
                {"target_id": target_id, "event_id": event_id},
            )
        if layer_event is None:
            return
        receipt[field] = copy.deepcopy(layer_event)
        runtime.trace[-1][field] = copy.deepcopy(layer_event)

    @staticmethod
    def _special_commit_snapshot(runtime: SpecialReactionRuntime) -> dict:
        """Capture every mutable special-runtime domain used by world commits."""
        actual_fields = frozenset(runtime.__dict__)
        missing_fields = _SPECIAL_RUNTIME_INSTANCE_FIELDS-actual_fields
        unexpected_fields = actual_fields-_SPECIAL_RUNTIME_INSTANCE_FIELDS
        invalid_fields = {
            name for name in unexpected_fields
            if (name not in _SPECIAL_RUNTIME_METHOD_NAMES
                or not callable(runtime.__dict__[name]))
        }
        if missing_fields or invalid_fields:
            raise ElementalWorldPending(
                "world_special_runtime_schema_mismatch",
                "星月运行时实例字段超出受审有限结构，不能提交世界事务。",
                {
                    "missing_fields": sorted(missing_fields),
                    "unexpected_fields": sorted(invalid_fields),
                },
            )
        return {
            "instance_fields": sorted(_SPECIAL_RUNTIME_INSTANCE_FIELDS),
            "state": runtime.state(),
            "model": copy.deepcopy(runtime.model),
            "trace": copy.deepcopy(runtime.trace),
            "pending_world_lunar_electro_expiry": copy.deepcopy(
                runtime._pending_world_lunar_electro_expiry),
            "world_defer_lunar_electro_expiry": (
                runtime._world_defer_lunar_electro_expiry),
        }

    @staticmethod
    def _target_identity_snapshot(runtime: ElementalRuntime) -> dict[str, Any]:
        """Freeze object topology that value snapshots cannot distinguish."""
        identities = {
            name: getattr(runtime, name) for name in _TARGET_IDENTITY_FIELDS
        }
        crystallize = runtime.crystallize_state
        identities["crystallize_state.world"] = (
            None if crystallize is None else crystallize.world)
        multi = runtime.multi_state
        identities["multi_state.freeze_state"] = (
            None if multi is None else getattr(multi, "freeze_state", None))
        return identities

    @staticmethod
    def _target_identity_changes(
        runtime: ElementalRuntime,
        expected: dict[str, Any],
    ) -> list[str]:
        crystallize = runtime.crystallize_state
        multi = runtime.multi_state
        current = {
            name: getattr(runtime, name)
            for name in (
                "freeze_state", "multi_state", "crystallize_state",
                "special_runtime", "bloom_core_state",
            )
        }
        current["crystallize_state.world"] = (
            None if crystallize is None else crystallize.world)
        current["multi_state.freeze_state"] = (
            None if multi is None else getattr(multi, "freeze_state", None))
        return sorted(
            name for name, value in expected.items()
            if current.get(name) is not value
        )

    def snapshot(self) -> ElementalWorldSnapshot:
        registry_fingerprint = tuple(sorted(self._areas_by_target.items()))
        return ElementalWorldSnapshot(
            owner_token=self._owner_token,
            time=self.time,
            registry_fingerprint=registry_fingerprint,
            target_states=copy.deepcopy(self._targets),
            crystallize_world=copy.deepcopy(self.crystallize_world),
            bloom_core_state=copy.deepcopy(self.bloom_core_state),
            special_runtime=copy.deepcopy(self.special_runtime),
            generated_events=copy.deepcopy(self._generated_events),
        )

    @staticmethod
    def _restore_exact_class(target: Any, expected_type: type) -> None:
        """Undo a compatible ``__class__`` drift before touching state."""
        if type(target) is expected_type:
            return
        try:
            object.__setattr__(target, "__class__", expected_type)
        except (AttributeError, TypeError) as exc:
            raise RuntimeError("原子回滚无法恢复运行时对象类型。") from exc
        if type(target) is not expected_type:
            raise RuntimeError("原子回滚后的运行时对象类型仍不一致。")

    @staticmethod
    def _restore_object(
        target: Any,
        snapshot: Any,
        *,
        callable_overrides: dict[str, Any] | None = None,
        deepcopy_value: Callable[[Any], Any] | None = None,
    ) -> Any:
        if target is None or snapshot is None:
            return snapshot
        if type(target) is not type(snapshot):
            raise TypeError("世界运行时共享对象快照类型不一致。")
        if callable_overrides is None:
            callable_overrides = {
                name: value
                for name, value in target.__dict__.items()
                if callable(value)
            }
        if deepcopy_value is None:
            deepcopy_value = copy.deepcopy
        target.__dict__.clear()
        target.__dict__.update(deepcopy_value(snapshot.__dict__))
        # deepcopy(MethodType) rebinds it to the copied owner.  Observable
        # instance wrappers that existed before the transaction must instead
        # remain bound to the original live runtime after rollback.
        target.__dict__.update(callable_overrides or {})
        return target

    def restore(self, snapshot: ElementalWorldSnapshot) -> None:
        if not isinstance(snapshot, ElementalWorldSnapshot):
            raise TypeError("世界运行时快照类型无效。")
        if snapshot.owner_token is not self._owner_token:
            raise ValueError("世界运行时快照属于其他实例。")
        registry_fingerprint = tuple(sorted(self._areas_by_target.items()))
        if snapshot.registry_fingerprint != registry_fingerprint:
            raise RuntimeError("目标注册表已变化，不能恢复旧世界快照。")

        _REFERENCE_CRYSTALLIZE_WORLD_RESTORE(
            self.crystallize_world, snapshot.crystallize_world)
        _REFERENCE_BLOOM_CORE_RESTORE(
            self.bloom_core_state, snapshot.bloom_core_state)
        self.special_runtime = _REFERENCE_WORLD_RESTORE_OBJECT(
            self.special_runtime, snapshot.special_runtime)

        for target_id, runtime in self._targets.items():
            saved = snapshot.target_states[target_id]
            runtime.__dict__.clear()
            runtime.__dict__.update(copy.deepcopy(saved.__dict__))
            if runtime.crystallize_state is not None:
                runtime.crystallize_state.world = self.crystallize_world
            runtime.bloom_core_state = self.bloom_core_state
            # Special state has one world owner.  It is never advanced once per
            # target, and future consumers reach it through the coordinator.
            runtime.special_runtime = None

        self.time = snapshot.time
        self._generated_events = copy.deepcopy(snapshot.generated_events)

        # The reverse index is derived registry state.  Rebuild it from the
        # authoritative target-to-area binding so a corrupted reverse index
        # cannot survive an otherwise valid public restore.
        self._targets_by_area.clear()
        for target_id, area_ids in self._areas_by_target.items():
            for area_id in area_ids:
                self._targets_by_area.setdefault(area_id, set()).add(target_id)

    def _atomic_roots(self) -> dict[str, Any]:
        """Capture coordinator identities and bidirectional registry state."""
        target_objects = dict(self._targets)
        target_identities = {
            target_id: _REFERENCE_WORLD_TARGET_IDENTITY_SNAPSHOT(runtime)
            for target_id, runtime in target_objects.items()
        }
        return {
            "world_type": type(self),
            "world_fields": frozenset(self.__dict__),
            "world_callable_overrides": {
                name: value
                for name, value in self.__dict__.items()
                if callable(value)
            },
            "owner_token": self._owner_token,
            "targets_registry": self._targets,
            "target_objects": target_objects,
            "target_types": {
                target_id: type(runtime)
                for target_id, runtime in target_objects.items()
            },
            "target_identities": target_identities,
            "target_callable_overrides": {
                target_id: {
                    name: value
                    for name, value in runtime.__dict__.items()
                    if (name not in _ELEMENTAL_RUNTIME_INSTANCE_FIELDS
                        and callable(value))
                }
                for target_id, runtime in target_objects.items()
            },
            "target_identity_callable_overrides": {
                target_id: {
                    name: {
                        field: value
                        for field, value in identity.__dict__.items()
                        if callable(value)
                    }
                    for name, identity in identities.items()
                    if (identity is not None
                        and name != "crystallize_state.world"
                        and hasattr(identity, "__dict__"))
                }
                for target_id, identities in target_identities.items()
            },
            "target_identity_schemas": {
                target_id: {
                    name: {
                        "type": type(identity),
                        "fields": frozenset(identity.__dict__),
                        "callables": {
                            field: value
                            for field, value in identity.__dict__.items()
                            if callable(value)
                        },
                    }
                    for name, identity in identities.items()
                    if (identity is not None
                        and name != "crystallize_state.world"
                        and hasattr(identity, "__dict__"))
                }
                for target_id, identities in target_identities.items()
            },
            "areas_registry": self._areas_by_target,
            "areas_state": copy.deepcopy(self._areas_by_target),
            "reverse_registry": self._targets_by_area,
            "reverse_state": copy.deepcopy(self._targets_by_area),
            "reverse_area_sets": dict(self._targets_by_area),
            "crystallize_world": self.crystallize_world,
            "crystallize_type": type(self.crystallize_world),
            "crystallize_callable_overrides": {
                name: value
                for name, value in self.crystallize_world.__dict__.items()
                if callable(value)
            },
            "bloom_core_state": self.bloom_core_state,
            "bloom_type": type(self.bloom_core_state),
            "bloom_callable_overrides": {
                name: value
                for name, value in self.bloom_core_state.__dict__.items()
                if callable(value)
            },
            "special_runtime": self.special_runtime,
            "special_type": (
                type(self.special_runtime)
                if self.special_runtime is not None else None
            ),
            "special_callable_overrides": (
                {
                    name: value
                    for name, value in self.special_runtime.__dict__.items()
                    if (name not in _SPECIAL_RUNTIME_INSTANCE_FIELDS
                        and callable(value))
                }
                if self.special_runtime is not None else {}
            ),
            "generated_events": self._generated_events,
            "generated_prefix": copy.deepcopy(self._generated_events),
        }

    def _restore_atomic_roots(
        self,
        snapshot: ElementalWorldSnapshot,
        roots: dict[str, Any],
        *,
        restore_object: Callable[..., Any],
        restore_exact_class: Callable[[Any, type], None],
        deepcopy_value: Callable[[Any], Any],
    ) -> None:
        """Restore even when the operation corrupted coordinator registries."""
        restore_exact_class(self, roots["world_type"])
        for name in set(self.__dict__)-set(roots["world_fields"]):
            self.__dict__.pop(name, None)
        self.__dict__.update(roots["world_callable_overrides"])
        self._owner_token = roots["owner_token"]

        self._targets = roots["targets_registry"]
        self._targets.clear()
        self._targets.update(roots["target_objects"])
        self._areas_by_target = roots["areas_registry"]
        self._areas_by_target.clear()
        self._areas_by_target.update(deepcopy_value(roots["areas_state"]))
        self._targets_by_area = roots["reverse_registry"]
        for area_id in set(self._targets_by_area)-set(roots["reverse_state"]):
            del self._targets_by_area[area_id]
        for area_id, target_ids in roots["reverse_state"].items():
            area_set = roots["reverse_area_sets"][area_id]
            area_set.clear()
            area_set.update(target_ids)
            self._targets_by_area[area_id] = area_set

        self.crystallize_world = roots["crystallize_world"]
        restore_exact_class(
            self.crystallize_world, roots["crystallize_type"])
        restore_object(
            self.crystallize_world,
            snapshot.crystallize_world,
            callable_overrides=roots["crystallize_callable_overrides"],
            deepcopy_value=deepcopy_value,
        )
        self.bloom_core_state = roots["bloom_core_state"]
        restore_exact_class(self.bloom_core_state, roots["bloom_type"])
        restore_object(
            self.bloom_core_state,
            snapshot.bloom_core_state,
            callable_overrides=roots["bloom_callable_overrides"],
            deepcopy_value=deepcopy_value,
        )
        self.special_runtime = roots["special_runtime"]
        if self.special_runtime is None:
            if snapshot.special_runtime is not None:
                raise RuntimeError("原子回滚丢失了原始星月状态对象。")
        else:
            restore_exact_class(self.special_runtime, roots["special_type"])
            restore_object(
                self.special_runtime,
                snapshot.special_runtime,
                callable_overrides=roots["special_callable_overrides"],
                deepcopy_value=deepcopy_value,
            )

        for target_id, runtime in roots["target_objects"].items():
            restore_exact_class(runtime, roots["target_types"][target_id])
            saved = snapshot.target_states[target_id]
            identities = roots["target_identities"][target_id]
            runtime.__dict__.clear()
            runtime.__dict__.update(deepcopy_value(saved.__dict__))
            runtime.__dict__.update(
                roots["target_callable_overrides"][target_id])
            for name in ("freeze_state", "multi_state", "crystallize_state"):
                identity = identities[name]
                saved_identity = getattr(saved, name)
                if identity is None or saved_identity is None:
                    setattr(runtime, name, saved_identity)
                    continue
                expected_type = roots["target_identity_schemas"][target_id][
                    name
                ]["type"]
                restore_exact_class(identity, expected_type)
                restore_object(
                    identity,
                    saved_identity,
                    callable_overrides=roots[
                        "target_identity_callable_overrides"
                    ][target_id].get(name, {}),
                    deepcopy_value=deepcopy_value,
                )
                setattr(runtime, name, identity)
            nested_freeze = identities["multi_state.freeze_state"]
            saved_nested_freeze = (
                None if saved.multi_state is None
                else getattr(saved.multi_state, "freeze_state", None)
            )
            if runtime.multi_state is not None:
                if nested_freeze is None or saved_nested_freeze is None:
                    runtime.multi_state.freeze_state = saved_nested_freeze
                else:
                    expected_type = roots[
                        "target_identity_schemas"
                    ][target_id]["multi_state.freeze_state"]["type"]
                    restore_exact_class(nested_freeze, expected_type)
                    restore_object(
                        nested_freeze,
                        saved_nested_freeze,
                        callable_overrides=roots[
                            "target_identity_callable_overrides"
                        ][target_id].get("multi_state.freeze_state", {}),
                        deepcopy_value=deepcopy_value,
                    )
                    runtime.multi_state.freeze_state = nested_freeze
            if identities["crystallize_state"] is not None:
                identities["crystallize_state"].world = self.crystallize_world
            runtime.bloom_core_state = self.bloom_core_state
            runtime.special_runtime = identities["special_runtime"]

        self.time = snapshot.time
        self._generated_events = roots["generated_events"]
        self._generated_events.clear()
        self._generated_events.extend(deepcopy_value(snapshot.generated_events))

    def _assert_atomic_commit_invariants(
        self,
        roots: dict[str, Any],
        publications: list[dict],
        *,
        journal_committed: bool,
        identity_changes: Callable[[ElementalRuntime, dict[str, Any]], list[str]],
        clock_guard: Callable[[ElementalWorldRuntime], None],
        normalize_events: Callable[[Any], list[dict]],
        structure_guard: Callable[[str, Any], list[str]],
    ) -> None:
        """Reject coordinator side effects outside the finite world contract."""
        problems: list[str] = []
        if type(self) is not roots["world_type"]:
            problems.append("world_type")
        problems.extend(
            f"world_structure:{field}"
            for field in structure_guard("world", self)
        )
        if frozenset(self.__dict__) != roots["world_fields"]:
            problems.append("world_instance_schema")
        world_callables = {
            name: value
            for name, value in self.__dict__.items()
            if callable(value)
        }
        if (set(world_callables) != set(roots["world_callable_overrides"])
                or any(world_callables[name] is not value
                       for name, value
                       in roots["world_callable_overrides"].items())):
            problems.append("world_callable_overrides")
        identity_fields = {
            "owner_token": self._owner_token,
            "targets_registry": self._targets,
            "areas_registry": self._areas_by_target,
            "reverse_registry": self._targets_by_area,
            "crystallize_world": self.crystallize_world,
            "bloom_core_state": self.bloom_core_state,
            "special_runtime": self.special_runtime,
            "generated_events": self._generated_events,
        }
        problems.extend(
            name for name, value in identity_fields.items()
            if value is not roots[name]
        )
        if (set(self._targets) != set(roots["target_objects"])
                or any(self._targets.get(target_id) is not runtime
                       for target_id, runtime
                       in roots["target_objects"].items())):
            problems.append("target_registry_identity")
        if self._areas_by_target != roots["areas_state"]:
            problems.append("target_area_registry")
        if self._targets_by_area != roots["reverse_state"]:
            problems.append("area_target_registry")
        if (set(self._targets_by_area) != set(roots["reverse_area_sets"])
                or any(self._targets_by_area.get(area_id) is not area_set
                       for area_id, area_set
                       in roots["reverse_area_sets"].items())):
            problems.append("area_target_set_identity")
        expected_journal = copy.deepcopy(roots["generated_prefix"])
        if journal_committed:
            expected_journal.extend(copy.deepcopy(publications))
        try:
            actual_journal = (
                normalize_events(self._generated_events)
                if (self._generated_events is roots["generated_events"]
                    and type(self._generated_events) is list)
                else None
            )
        except (TypeError, ValueError):
            actual_journal = None
        if actual_journal != expected_journal:
            problems.append("generated_event_journal")
        for target_id, runtime in roots["target_objects"].items():
            if type(runtime) is not roots["target_types"][target_id]:
                problems.append(f"target_type:{target_id}")
            problems.extend(
                f"target_structure:{target_id}:{field}"
                for field in structure_guard("elemental_runtime", runtime)
            )
            if identity_changes(
                    runtime, roots["target_identities"][target_id]):
                problems.append(f"target_topology:{target_id}")
            actual_fields = frozenset(runtime.__dict__)
            unexpected_fields = actual_fields-_ELEMENTAL_RUNTIME_INSTANCE_FIELDS
            invalid_fields = {
                name for name in unexpected_fields
                if (name not in _REFERENCE_ELEMENTAL_METHOD_NAMES
                    or not callable(runtime.__dict__[name]))
            }
            if (_ELEMENTAL_RUNTIME_INSTANCE_FIELDS-actual_fields
                    or invalid_fields):
                problems.append(f"target_instance_schema:{target_id}")
            callable_overrides = {
                name: value
                for name, value in runtime.__dict__.items()
                if (name not in _ELEMENTAL_RUNTIME_INSTANCE_FIELDS
                    and callable(value))
            }
            expected_overrides = roots["target_callable_overrides"][target_id]
            if (set(callable_overrides) != set(expected_overrides)
                    or any(callable_overrides[name] is not value
                           for name, value in expected_overrides.items())):
                problems.append(f"target_callable_overrides:{target_id}")
            for name, schema in roots["target_identity_schemas"][
                    target_id].items():
                identity = roots["target_identities"][target_id][name]
                if type(identity) is not schema["type"]:
                    problems.append(f"target_child_type:{target_id}:{name}")
                fields = frozenset(object.__getattribute__(identity, "__dict__"))
                if fields != schema["fields"]:
                    problems.append(f"target_child_schema:{target_id}:{name}")
                callables = {
                    field: value
                    for field, value in object.__getattribute__(
                        identity, "__dict__").items()
                    if callable(value)
                }
                if (set(callables) != set(schema["callables"])
                        or any(callables[field] is not value
                               for field, value
                               in schema["callables"].items())):
                    problems.append(
                        f"target_child_callables:{target_id}:{name}")
                child_kind = {
                    "freeze_state": "freeze_state",
                    "multi_state": "multi_state",
                    "multi_state.freeze_state": "freeze_state",
                    "crystallize_state": "crystallize_state",
                    "special_runtime": "special_runtime",
                    "bloom_core_state": "bloom_core_state",
                }.get(name)
                if child_kind is not None:
                    problems.extend(
                        f"target_child_structure:{target_id}:{name}:{field}"
                        for field in structure_guard(child_kind, identity)
                    )
        if self.special_runtime is not None:
            if type(self.special_runtime) is not roots["special_type"]:
                problems.append("special_runtime_type")
            problems.extend(
                f"special_runtime_structure:{field}"
                for field in structure_guard(
                    "special_runtime", self.special_runtime)
            )
            actual_fields = frozenset(self.special_runtime.__dict__)
            unexpected_fields = actual_fields-_SPECIAL_RUNTIME_INSTANCE_FIELDS
            invalid_fields = {
                name for name in unexpected_fields
                if (name not in _SPECIAL_RUNTIME_METHOD_NAMES
                    or not callable(self.special_runtime.__dict__[name]))
            }
            if (_SPECIAL_RUNTIME_INSTANCE_FIELDS-actual_fields
                    or invalid_fields):
                problems.append("special_instance_schema")
            callable_overrides = {
                name: value
                for name, value in self.special_runtime.__dict__.items()
                if (name not in _SPECIAL_RUNTIME_INSTANCE_FIELDS
                    and callable(value))
            }
            expected_overrides = roots["special_callable_overrides"]
            if (set(callable_overrides) != set(expected_overrides)
                    or any(callable_overrides[name] is not value
                           for name, value in expected_overrides.items())):
                problems.append("special_callable_overrides")
        for problem, runtime, expected_fields, method_names in (
            (
                "crystallize_world_instance_schema",
                self.crystallize_world,
                _CRYSTALLIZE_WORLD_INSTANCE_FIELDS,
                _CRYSTALLIZE_WORLD_METHOD_NAMES,
            ),
            (
                "bloom_core_instance_schema",
                self.bloom_core_state,
                _BLOOM_CORE_INSTANCE_FIELDS,
                _BLOOM_CORE_METHOD_NAMES,
            ),
        ):
            expected_type = roots[
                ("crystallize_type"
                 if runtime is self.crystallize_world else "bloom_type")
            ]
            if type(runtime) is not expected_type:
                problems.append(problem+":type")
            structure_kind = (
                "crystallize_world"
                if runtime is self.crystallize_world
                else "bloom_core_state"
            )
            problems.extend(
                f"{problem}:structure:{field}"
                for field in structure_guard(structure_kind, runtime)
            )
            actual_fields = frozenset(runtime.__dict__)
            unexpected_fields = actual_fields-expected_fields
            invalid_fields = {
                name for name in unexpected_fields
                if (name not in method_names
                    or not callable(runtime.__dict__[name]))
            }
            if expected_fields-actual_fields or invalid_fields:
                problems.append(problem)
            callable_overrides = {
                name: value
                for name, value in runtime.__dict__.items()
                if name not in expected_fields and callable(value)
            }
            expected_overrides = roots[
                ("crystallize_callable_overrides"
                 if runtime is self.crystallize_world
                 else "bloom_callable_overrides")
            ]
            if (set(callable_overrides) != set(expected_overrides)
                    or any(callable_overrides[name] is not value
                           for name, value in expected_overrides.items())):
                problems.append(problem+":callable_overrides")
        if self._transaction_depth != 1:
            problems.append("transaction_depth")
        try:
            clock_guard(self)
        except ElementalWorldPending:
            problems.append("world_clock")
        if problems:
            raise ElementalWorldPending(
                "world_transaction_commit_invariant_mismatch",
                "原子事务修改了未授权的世界协调器状态。",
                {"violations": sorted(set(problems))},
            )

    def execute_atomic(self, operation: Callable[[ElementalWorldRuntime], _T]) -> _T:
        """Run one cross-target operation and roll every participant back."""
        if not callable(operation):
            raise TypeError("operation必须可调用。")
        active_context = _ACTIVE_ATOMIC_WORLD
        if self._transaction_depth or active_context.get() is not None:
            raise RuntimeError("世界运行时不允许同世界或跨世界嵌套原子事务。")
        if "_publish_transaction" in self.__dict__:
            raise RuntimeError("世界运行时保留的事务发布入口已被占用。")

        # Capture every control-plane callable before producer code runs.
        # Rebinding a module alias during the operation can therefore affect
        # neither this transaction's verdict nor its rollback.
        snapshot_impl = _REFERENCE_WORLD_SNAPSHOT
        roots_impl = _REFERENCE_WORLD_ATOMIC_ROOTS
        assert_impl = _REFERENCE_WORLD_ASSERT_ATOMIC_COMMIT
        rollback_impl = _REFERENCE_WORLD_RESTORE_ATOMIC_ROOTS
        restore_object_impl = _REFERENCE_WORLD_RESTORE_OBJECT
        restore_class_impl = _REFERENCE_WORLD_RESTORE_EXACT_CLASS
        identity_changes_impl = _REFERENCE_WORLD_TARGET_IDENTITY_CHANGES
        clock_guard_impl = _REFERENCE_WORLD_ASSERT_CLOCK_SYNCHRONIZED
        structure_guard_impl = _REFERENCE_WORLD_DOMAIN_STRUCTURE_VIOLATIONS
        normalize_events = _plain_event_batch
        deepcopy_impl = copy.deepcopy
        module_namespace = globals()
        reference_aliases = {
            name: value
            for name, value in module_namespace.items()
            if name.startswith("_REFERENCE_")
        }
        control_globals = {
            "_ACTIVE_ATOMIC_WORLD": active_context,
            "_plain_event_batch": normalize_events,
        }

        snapshot = snapshot_impl(self)
        roots = roots_impl(self)
        publications: list[dict] = []

        def stage_publications(events: Any) -> int:
            rows = normalize_events(events)
            old_ids = {
                row["event_id"]
                for row in (*roots["generated_prefix"], *publications)
            }
            new_ids = [row["event_id"] for row in rows]
            if (len(new_ids) != len(set(new_ids))
                    or old_ids.intersection(new_ids)):
                raise ValueError("世界派生事件ID必须全程唯一。")
            publications.extend(rows)
            return len(roots["generated_prefix"])+len(publications)

        self._transaction_depth = 1
        self.__dict__["_publish_transaction"] = stage_publications
        active_token = active_context.set(self)
        try:
            result = operation(self)
            publisher_intact = (
                self.__dict__.get("_publish_transaction")
                is stage_publications
            )
            self.__dict__.pop("_publish_transaction", None)
            if not publisher_intact:
                raise ElementalWorldPending(
                    "world_transaction_commit_invariant_mismatch",
                    "原子事务替换了世界事件发布入口。",
                    {"violations": ["transaction_publisher"]},
                )
            assert_impl(
                self,
                roots,
                publications,
                journal_committed=False,
                identity_changes=identity_changes_impl,
                clock_guard=clock_guard_impl,
                normalize_events=normalize_events,
                structure_guard=structure_guard_impl,
            )
            self._generated_events.extend(deepcopy_impl(publications))
            assert_impl(
                self,
                roots,
                publications,
                journal_committed=True,
                identity_changes=identity_changes_impl,
                clock_guard=clock_guard_impl,
                normalize_events=normalize_events,
                structure_guard=structure_guard_impl,
            )
            return result
        except BaseException:
            self.__dict__.pop("_publish_transaction", None)
            rollback_impl(
                self,
                snapshot,
                roots,
                restore_object=restore_object_impl,
                restore_exact_class=restore_class_impl,
                deepcopy_value=deepcopy_impl,
            )
            raise
        finally:
            self._transaction_depth = 0
            active_context.reset(active_token)
            module_namespace.update(reference_aliases)
            module_namespace.update(control_globals)

    def publish_generated_events(self, events: list[dict]) -> int:
        """Append validated events and return the resulting replay cursor."""
        active_world = _ACTIVE_ATOMIC_WORLD.get()
        if active_world is not None and active_world is not self:
            raise RuntimeError("原子事务不能向其他世界运行时发布事件。")
        if self._transaction_depth:
            publisher = self.__dict__.get("_publish_transaction")
            if not callable(publisher):
                raise RuntimeError("原子事务缺少世界事件发布入口。")
            return publisher(events)
        rows = _plain_event_batch(events)
        old_ids = {row["event_id"] for row in self._generated_events}
        new_ids = [row["event_id"] for row in rows]
        if len(new_ids) != len(set(new_ids)) or old_ids.intersection(new_ids):
            raise ValueError("世界派生事件ID必须全程唯一。")
        self._generated_events.extend(rows)
        return len(self._generated_events)

    def drain_generated_events(self, after_cursor: int = 0) -> dict:
        """Read events after a cursor without consuming them.

        Repeating the same cursor returns byte-for-byte equivalent event data;
        consumers only persist the returned integer cursor after their own
        downstream commit succeeds.
        """
        if (type(after_cursor) is not int or after_cursor < 0
                or after_cursor > len(self._generated_events)):
            raise ValueError("after_cursor必须位于世界事件日志范围内。")
        return {
            "events": copy.deepcopy(self._generated_events[after_cursor:]),
            "cursor": len(self._generated_events),
        }

    def _assert_world_clock_synchronized(self) -> None:
        """Reject targets or special state advanced outside the coordinator."""
        mismatches = []
        for target_id in sorted(self._targets):
            runtime = self._targets[target_id]
            if runtime.time != self.time:
                mismatches.append({
                    "participant": "target",
                    "target_id": target_id,
                    "time_tick": runtime.time,
                })
            if runtime.special_runtime is not None:
                mismatches.append({
                    "participant": "target_special_runtime",
                    "target_id": target_id,
                    "time_tick": runtime.special_runtime.time,
                })
        if (self.special_runtime is not None
                and self.special_runtime.time != self.time):
            mismatches.append({
                "participant": "world_special_runtime",
                "time_tick": self.special_runtime.time,
            })
        if mismatches:
            raise ElementalWorldPending(
                "world_clock_desynchronized",
                "目标或星月状态绕过世界协调器独立推进，不能继续权威计算。",
                {"world_time_tick": self.time,
                 "mismatches": mismatches},
            )

    @staticmethod
    def _pending_from_runtime(exc: Exception) -> ElementalWorldPending:
        issue = getattr(exc, "issue", {})
        details = {
            key: copy.deepcopy(value)
            for key, value in issue.items()
            if key not in {"code", "message", "severity"}
        }
        details["runtime_issue"] = copy.deepcopy(issue)
        return ElementalWorldPending(
            issue.get("code", "world_special_runtime_pending"),
            str(exc),
            details,
        )

    def _advance_targets_to(self, time_tick: int) -> list[dict]:
        """Advance local targets in stable order, without a target-owned special clock."""
        generated: list[dict] = []
        for target_id in sorted(self._targets):
            runtime = self._targets[target_id]
            if runtime.special_runtime is not None:
                raise ElementalWorldPending(
                    "world_special_runtime_multiple_owners",
                    "多目标世界中的星月状态只能由世界协调器持有。",
                    {"target_id": target_id},
                )
            runtime.advance(time_tick)
            generated.extend(runtime.drain_generated_events())
        if not self._targets:
            generated.extend(self.bloom_core_state.advance(time_tick))
            self.crystallize_world.advance(time_tick)
        return generated

    def _validate_lunar_electro_tick(
        self,
        tick: dict,
        *,
        boundary_tick: int,
        seen_target_areas: dict[tuple[int, str], str],
    ) -> ElementalRuntime:
        """Delegate readonly family proof; this World retains commit ownership."""
        return validate_lunar_electro_tick(self, tick, boundary_tick=boundary_tick, seen_target_areas=seen_target_areas)

    def _derive_lunar_electro_schedule_contract(
        self,
        *,
        boundary_tick: int,
        clouds_before: dict[str, dict],
        reference_runtime: SpecialReactionRuntime,
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        """Delegate readonly family proof; this World retains commit ownership."""
        return derive_lunar_electro_schedule_contract(self, boundary_tick=boundary_tick, clouds_before=clouds_before, reference_runtime=reference_runtime)

    def _validate_lunar_electro_due_contract(
        self,
        event: dict,
        *,
        boundary_tick: int,
        clouds_before: dict[str, dict],
    ) -> None:
        """Delegate readonly family proof; this World retains commit ownership."""
        return validate_lunar_electro_due_contract(self, event, boundary_tick=boundary_tick, clouds_before=clouds_before)

    def _validate_lunar_electro_admission_contract(
        self,
        tick: dict,
        admitted: dict,
        aura_snapshot: dict,
    ) -> None:
        """Delegate readonly family proof; this World retains commit ownership."""
        return validate_lunar_electro_admission_contract(self, tick, admitted, aura_snapshot)

    def _validate_special_batch_event_ids(
        self,
        events: list[dict],
        *,
        phase: str,
    ) -> None:
        event_ids = [row.get("event_id") if isinstance(row, dict) else None
                     for row in events]
        published = {row["event_id"] for row in self._generated_events}
        if (any(not isinstance(value, str) or not value
                for value in event_ids)
                or len(event_ids) != len(set(event_ids))
                or published.intersection(event_ids)):
            raise ElementalWorldPending(
                "world_special_generated_event_conflict",
                "星月世界批次包含无效、重复或已经发布的事件ID。",
                {"phase": phase,
                 "event_ids": copy.deepcopy(event_ids),
                 "published_conflicts": sorted(
                     published.intersection(
                         value for value in event_ids
                         if isinstance(value, str)))},
            )

    def _validate_stellar_swirl_passthrough(
        self,
        event: dict,
        *,
        time_tick: int,
        source: str,
    ) -> None:
        """Delegate readonly family proof; this World retains commit ownership."""
        return validate_stellar_swirl_passthrough(self, event, time_tick=time_tick, source=source)

    def _stellar_swirl_pairs(
        self,
        events: list[dict],
        *,
        time_tick: int,
        source: str,
    ) -> tuple[list[dict], set[str]]:
        """Delegate readonly family proof; this World retains commit ownership."""
        return stellar_swirl_pairs(self, events, time_tick=time_tick, source=source)

    def _freeze_stellar_swirl_deadlines(
        self,
        time_tick: int,
    ) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
        """Delegate readonly family proof; this World retains commit ownership."""
        return freeze_stellar_swirl_deadlines(self, time_tick)

    @staticmethod
    def _is_stellar_superconduct_deadline_row(event: dict) -> bool:
        """Return whether *event* is one reviewed Star-Superconduct state row."""
        return bool(
            isinstance(event, dict)
            and event.get("type") == "special_reaction_state"
            and event.get("reaction_type") == "stellar_superconduct"
            and event.get("operation") in {
                "settle_four_second_window",
                "expire_field",
                "expire_settlement_buff",
            }
        )

    def _validate_stellar_superconduct_field_state(
        self,
        state: Any,
        *,
        area_id: str,
        phase: str,
        boundary_tick: int,
    ) -> None:
        """Delegate readonly family proof; this World retains commit ownership."""
        return validate_stellar_superconduct_field_state(self, state, area_id=area_id, phase=phase, boundary_tick=boundary_tick)

    def _freeze_stellar_superconduct_deadlines(
        self,
        time_tick: int,
    ) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
        """Delegate readonly family proof; this World retains commit ownership."""
        return freeze_stellar_superconduct_deadlines(self, time_tick)

    def _consume_stellar_superconduct_deadline_batch(
        self,
        events: list[dict],
        *,
        expected_rows: dict[str, dict],
        time_tick: int,
        source: str,
    ) -> list[dict]:
        """Delegate readonly family proof; this World retains commit ownership."""
        return consume_stellar_superconduct_deadline_batch(self, events, expected_rows=expected_rows, time_tick=time_tick, source=source)

    @staticmethod
    def _is_stellar_swirl_deadline_row(event: dict) -> bool:
        """Return whether a row belongs to a vortex detonation triplet."""
        return bool(
            (event.get("type") == "special_reaction_state"
             and event.get("reaction_type") == "stellar_swirl"
             and event.get("operation") == "detonate_vortex")
            or (event.get("type") == "special_reaction_damage_request"
                and event.get("reaction_type") in {
                    "stellar_swirl_cryo_lv2", "stellar_swirl_cryo_lv3"})
            or (event.get("type")
                == "special_reaction_application_request"
                and event.get("reaction_type") == "stellar_swirl")
        )

    def _consume_stellar_swirl_batch(
        self,
        events: list[dict],
        *,
        time_tick: int,
        source: str,
    ) -> tuple[list[dict], int, set[str]]:
        """Consume all Star-Swirl area applications in one world transaction."""
        if self.special_runtime is None:
            raise RuntimeError("星扩散世界批次缺少SpecialReactionRuntime。")

        prepared = plan_stellar_swirl_world_batch(self, events, time_tick=time_tick, source=source)
        candidate, pairs, paired_ids = prepared.candidate, prepared.pairs, prepared.paired_event_ids
        all_pair_targets, plans = prepared.target_ids, prepared.target_applications

        committed_by_request: dict[str, list[dict]] = {}
        for plan in plans:
            runtime = self.get_target(plan.target_id)
            if (self._target_commit_snapshot(runtime)
                    != plan.target_state_before
                    or self._target_identity_changes(
                        runtime, plan.target_identity_before)):
                raise ElementalWorldPending(
                    "world_stellar_swirl_target_state_changed_before_commit",
                    "风旋冰附着预览后目标状态发生变化，整批停止提交。",
                    {"target_id": plan.target_id,
                     "event_id": plan.target_event_id},
                )
            actual_application = runtime.register_special_application(
                plan.request, target_id=plan.target_id)
            actual_attempt = {
                **plan.attempt,
                "application": actual_application,
            }
            try:
                receipt = runtime._attempt_with_world_reaction_routes(
                    reaction_routes=plan.routes, **actual_attempt)
            except ElementalPending as exc:
                raise self._pending_from_runtime(exc) from exc
            actual_generated = runtime.drain_generated_events()
            live_receipt_before_layer = copy.deepcopy(receipt)
            live_trace_before_layer = copy.deepcopy(runtime.trace[-1])
            live_target_before_layer = self._target_commit_snapshot(runtime)
            self._bind_stellar_layer_to_target_receipt(
                runtime,
                receipt,
                plan.layer_event,
                target_id=plan.target_id,
                event_id=plan.target_event_id,
                issue_code="world_stellar_swirl_target_commit_mismatch",
            )
            require_stellar_layer_binding(self,
                runtime,
                receipt,
                plan.layer_event,
                receipt_before=live_receipt_before_layer,
                trace_before=live_trace_before_layer,
                target_snapshot_before=live_target_before_layer,
                target_id=plan.target_id,
                event_id=plan.target_event_id,
                issue_code="world_stellar_swirl_target_commit_mismatch",
            )
            if receipt.get("after") != runtime.state():
                raise ElementalWorldPending(
                    "world_stellar_swirl_target_commit_mismatch",
                    "风旋冰附着提交回执中的元素后态与目标实时状态不一致。",
                    {"target_id": plan.target_id,
                     "event_id": plan.target_event_id},
                )
            if actual_generated != plan.expected_commit_generated:
                raise ElementalWorldPending(
                    "world_stellar_swirl_target_commit_mismatch",
                    "风旋冰附着提交的普通派生事件与预览不一致。",
                    {"target_id": plan.target_id,
                     "event_id": plan.target_event_id},
                )
            if receipt != plan.expected_commit_receipt:
                raise ElementalWorldPending(
                    "world_stellar_swirl_target_commit_mismatch",
                    "风旋冰附着提交回执与最终路由预览不一致。",
                    {"target_id": plan.target_id,
                     "event_id": plan.target_event_id},
                )
            if (runtime.profiles
                    != plan.expected_profiles_after_registration
                    or runtime.trace
                    != [*plan.target_state_before["trace"], receipt]):
                raise ElementalWorldPending(
                    "world_stellar_swirl_target_commit_mismatch",
                    "风旋附着提交改写了档案或产生了额外轨迹。",
                    {"target_id": plan.target_id,
                     "event_id": plan.target_event_id},
                )
            if (self._target_commit_snapshot(runtime)
                    != plan.expected_target_state_after
                    or self._target_identity_changes(
                        runtime, plan.target_identity_before)):
                raise ElementalWorldPending(
                    "world_stellar_swirl_target_commit_mismatch",
                    "风旋冰附着提交后的目标状态、共享对象拓扑与最终路由预览不一致。",
                    {"target_id": plan.target_id,
                     "event_id": plan.target_event_id,
                     "identity_changed": self._target_identity_changes(
                         runtime, plan.target_identity_before)},
                )
            request_id = plan.request["event_id"]
            committed_by_request.setdefault(request_id, []).extend([
                copy.deepcopy(receipt),
                *copy.deepcopy(actual_generated),
                *copy.deepcopy(plan.routed_generated),
            ])

        restored = self._restore_object(self.special_runtime, candidate)
        if restored is not self.special_runtime:
            raise RuntimeError("风旋世界批次提交破坏了共享特殊状态对象身份。")

        expanded = [copy.deepcopy(row) for row in events
                    if row.get("event_id") not in paired_ids]
        for pair in pairs:
            damage = copy.deepcopy(pair["damage"])
            request = pair["application"]
            target_ids = list(pair["target_ids"])
            selection_event_id = request["event_id"] + ":target-selection"
            damage.update(
                resolved_target_ids=target_ids,
                selection_audit_id=selection_event_id,
                target_snapshot_event_id=selection_event_id,
                target_snapshot_source="static_world_registry_v1",
            )
            selection = {
                "type": "world_area_target_selection",
                "event_id": selection_event_id,
                "t": time_tick / TICKS,
                "time_tick": time_tick,
                "area_id": damage["area_id"],
                "source_application_request_id": request["event_id"],
                "parent_damage_event_id": damage["event_id"],
                "target_selection_policy": "event_time_entities_in_area",
                "target_ids": target_ids,
                "registry_fingerprint": [
                    [target_id, list(area_ids)]
                    for target_id, area_ids in self._registry_fingerprint()
                ],
                "status": "executed",
            }
            expanded.extend((damage, selection))
            expanded.extend(copy.deepcopy(
                committed_by_request.get(request["event_id"], [])))
        self._validate_special_batch_event_ids(
            expanded, phase="stellar_swirl_expanded")
        return expanded, len(pairs), set(all_pair_targets)

    def _consume_special_generated_batch(
        self,
        events: list[dict],
        *,
        time_tick: int,
        source: str,
        simultaneous_order: tuple[str, ...] | None = None,
        expected_stellar_superconduct_rows: dict[str, dict] | None = None,
    ) -> tuple[list[dict], int, int, list[dict]]:
        """Consume one complete special-runtime batch without publishing it."""
        if self._transaction_depth != 1:
            raise RuntimeError("星月世界批消费器只能在世界原子事务中调用。")
        if source not in {"deadline", "reaction_packet"}:
            raise ValueError("特殊世界批次来源无效。")
        if (not isinstance(events, list)
                or any(not isinstance(row, dict) for row in events)):
            raise TypeError("特殊世界批次必须是事件对象列表。")
        self._validate_special_batch_event_ids(events, phase="special_input")
        if any(row.get("time_tick") != time_tick for row in events):
            raise ElementalWorldPending(
                "world_special_batch_time_mismatch",
                "特殊世界批次只能包含当前边界的完整事件集。",
                {"time_tick": time_tick,
                 "event_ids": [row.get("event_id") for row in events]},
            )
        lunar = [row for row in events
                 if row.get("reaction_type") == "lunar_electro"]
        stellar = [row for row in events
                   if row.get("reaction_type") == "stellar_swirl"
                   or str(row.get("reaction_type", "")).startswith(
                       "stellar_swirl_")]
        stellar_superconduct = [
            row for row in events
            if row.get("reaction_type") == "stellar_superconduct"
        ]
        unsupported = [row for row in events
                       if (row not in lunar and row not in stellar
                           and row not in stellar_superconduct)]
        if unsupported:
            raise ElementalWorldPending(
                "world_special_deadline_consumer_missing",
                "该星月派生事件尚未接入多目标世界consumer。",
                {"deadline_tick": time_tick,
                 "reaction_types": sorted({
                     str(row.get("reaction_type")) for row in unsupported}),
                 "event_ids": [row.get("event_id")
                               for row in unsupported]},
            )

        stellar_targets: set[str] = set()
        if stellar:
            pairs, _ = self._stellar_swirl_pairs(
                stellar, time_tick=time_tick, source=source)
            stellar_targets = {
                target_id for pair in pairs for target_id in pair["target_ids"]
            }
        lunar_targets = {
            row["target_id"] for row in lunar
            if row.get("type") == "special_reaction_tick_request"
            and isinstance(row.get("target_id"), str)
        }
        conflict_targets = sorted(stellar_targets & lunar_targets)
        if conflict_targets:
            if simultaneous_order is None:
                raise ElementalWorldPending(
                    "world_special_simultaneous_order_required",
                    "风旋冰附着与月感电跳数同刻修改同一目标，缺少显式顺序。",
                    {"time_tick": time_tick,
                     "target_ids": conflict_targets,
                     "reaction_types": [
                         "lunar_electro", "stellar_swirl"]},
                )
            if simultaneous_order != ("lunar_electro", "stellar_swirl"):
                raise ElementalWorldPending(
                    "world_special_simultaneous_order_unsupported",
                    "当前两阶段协议只受审了月感电先于风旋附着的同刻顺序。",
                    {"time_tick": time_tick,
                     "requested_order": list(simultaneous_order)},
                )

        lunar_rows: list[dict] = []
        lunar_batch_count = 0
        expiry_rows: list[dict] = []
        if source == "deadline":
            lunar_rows, lunar_batch_count, expiry_rows = (
                self._consume_lunar_electro_tick_batch(
                    lunar, boundary_tick=time_tick))
        elif lunar:
            raise ElementalWorldPending(
                "world_special_packet_contains_deadline",
                "星扩散packet批次夹带了未先消费的月感电截止事件。",
                {"time_tick": time_tick,
                 "event_ids": [row.get("event_id") for row in lunar]},
            )

        stellar_rows: list[dict] = []
        stellar_batch_count = 0
        if stellar:
            stellar_rows, stellar_batch_count, _ = (
                self._consume_stellar_swirl_batch(
                    stellar, time_tick=time_tick, source=source))
        stellar_superconduct_rows: list[dict] = []
        if stellar_superconduct:
            protected_target_snapshots = {
                target_id: self._target_commit_snapshot(runtime)
                for target_id, runtime in self._targets.items()
            }
            protected_special_snapshot = self._special_commit_snapshot(
                self.special_runtime
            ) if self.special_runtime is not None else None
            stellar_superconduct_rows = (
                self._consume_stellar_superconduct_deadline_batch(
                    stellar_superconduct,
                    expected_rows=(
                        expected_stellar_superconduct_rows
                        if expected_stellar_superconduct_rows is not None
                        else {}
                    ),
                    time_tick=time_tick,
                    source=source,
                )
            )
            protected_target_changes = [
                target_id
                for target_id, snapshot in protected_target_snapshots.items()
                if self._target_commit_snapshot(self._targets[target_id])
                != snapshot
            ]
            if protected_target_changes:
                raise ElementalWorldPending(
                    "world_stellar_superconduct_target_state_changed",
                    "星超导领域截止consumer改写了目标元素状态。",
                    {"time_tick": time_tick,
                     "target_ids": sorted(protected_target_changes)},
                )
            if (self.special_runtime is None
                    or self._special_commit_snapshot(self.special_runtime)
                    != protected_special_snapshot):
                raise ElementalWorldPending(
                    "world_stellar_superconduct_consumer_state_changed",
                    "星超导领域截止consumer重复改写了共享领域状态。",
                    {"time_tick": time_tick},
                )
            if stellar_superconduct_rows != stellar_superconduct:
                raise ElementalWorldPending(
                    "world_stellar_superconduct_deadline_invalid",
                    "星超导领域截止consumer必须逐字发布受审状态行。",
                    {"time_tick": time_tick,
                     "expected_event_ids": [
                         row.get("event_id")
                         for row in stellar_superconduct],
                     "returned_event_ids": [
                         row.get("event_id")
                         for row in stellar_superconduct_rows
                         if isinstance(row, dict)],
                    },
                )
        elif expected_stellar_superconduct_rows:
            raise ElementalWorldPending(
                "world_stellar_superconduct_deadline_invalid",
                "星超导截止批次漏掉了推进前推导的状态行。",
                {"time_tick": time_tick,
                 "expected_event_ids": sorted(
                     expected_stellar_superconduct_rows)},
            )
        expanded = [
            *lunar_rows,
            *stellar_superconduct_rows,
            *stellar_rows,
        ]
        self._validate_special_batch_event_ids(
            expanded, phase="special_expanded")
        return (expanded, lunar_batch_count,
                stellar_batch_count, expiry_rows)

    def _consume_lunar_electro_tick_batch(
        self,
        due: list[dict],
        *,
        boundary_tick: int,
    ) -> tuple[list[dict], int, list[dict]]:
        """Commit due ticks, then finalize same-boundary cloud expiry once."""
        if self.special_runtime is None:
            raise RuntimeError("月感电世界批次缺少世界级SpecialReactionRuntime。")
        prepared = plan_lunar_electro_world_batch(self, due, boundary_tick=boundary_tick)
        candidate = prepared.candidate

        committed_rows: dict[str, list[dict]] = {}
        for planned in prepared.admissions:
            tick = planned.tick
            event_id = tick["event_id"]
            target = planned.target
            admitted = planned.admitted
            reference_target = self._reference_target_copy(target)
            expected_rows = _REFERENCE_COMMIT_LUNAR_ELECTRO_TICK(
                reference_target,
                copy.deepcopy(tick),
                copy.deepcopy(admitted),
            )
            preview_rows = [copy.deepcopy(
                admitted["admission"])]
            damage = admitted.get("damage_event")
            if damage is not None:
                preview_rows.append(copy.deepcopy(damage))
            if expected_rows != preview_rows:
                raise ElementalWorldPending(
                    "world_lunar_electro_target_commit_mismatch",
                    "月感电规范目标提交没有精确重放已验证预演回执。",
                    {"event_id": event_id,
                     "target_id": tick["target_id"]},
                )
            expected_snapshot = self._target_commit_snapshot(
                reference_target)
            identity_before = {
                name: getattr(target, name)
                for name in (
                    "freeze_state", "multi_state", "crystallize_state",
                    "special_runtime", "bloom_core_state",
                )
            }
            try:
                committed_rows[event_id] = (
                    target._commit_lunar_electro_tick_receipt(
                        tick, admitted))
            except ElementalPending as exc:
                raise self._pending_from_runtime(exc) from exc
            identity_changed = [
                name for name, value in identity_before.items()
                if getattr(target, name) is not value
            ]
            if (committed_rows[event_id] != expected_rows
                    or self._target_commit_snapshot(target)
                    != expected_snapshot
                    or identity_changed):
                raise ElementalWorldPending(
                    "world_lunar_electro_target_commit_mismatch",
                    "月感电目标提交的回执行、顺序或完整后态与规范预演不一致。",
                    {"event_id": event_id,
                     "target_id": tick["target_id"],
                     "identity_changed": identity_changed},
                )

        # Expiry is deliberately the second stage.  The candidate still owns
        # the live endpoint cloud while every tick is admitted and every
        # target receipt is committed.  Only after those operations succeed
        # may the special runtime clear the cloud and publish its expiry row.
        expiry_rows = finalize_lunar_electro_candidate(self, candidate, boundary_tick=boundary_tick)

        restored = self._restore_object(self.special_runtime, candidate)
        if restored is not self.special_runtime:
            raise RuntimeError("世界级星月状态提交破坏了共享对象身份。")

        expanded: list[dict] = []
        for row in due:
            expanded.append(copy.deepcopy(row))
            rows = committed_rows.get(row.get("event_id"))
            if rows:
                expanded.extend(copy.deepcopy(rows))
        expanded.extend(copy.deepcopy(expiry_rows))
        return expanded, int(bool(prepared.admissions)), expiry_rows

    def _advance_special_to(
        self,
        time_tick: int,
    ) -> tuple[list[dict], int, int]:
        if self.special_runtime is None:
            return [], 0, 0
        # Derive every pre-advance contract from the import-time canonical
        # method graph.  Calling a live helper here would let one wrapper
        # mutate the authoritative runtime before both producer candidates
        # are copied, making the same corruption look like valid baseline
        # state on both sides of the later two-phase comparison.
        contract_reference_runtime = _copy_special_runtime_for_reference(
            self.special_runtime)
        (stellar_fields_before, expected_stellar_fields_after,
         expected_stellar_superconduct_rows) = (
            self._freeze_stellar_superconduct_deadlines(time_tick)
        )
        (vortices_before, expected_vortices_after,
         expected_stellar_rows) = self._freeze_stellar_swirl_deadlines(
             time_tick)
        clouds_before = copy.deepcopy(
            self.special_runtime.lunar_cloud_by_area)
        expected_expiries: dict[str, dict] = {}
        for area_id in sorted(clouds_before):
            cloud = clouds_before[area_id]
            expires = cloud.get("lunar_electro_expires_at")
            if expires is None:
                continue
            if type(expires) is not int:
                raise ElementalWorldPending(
                    "world_lunar_electro_tick_invalid",
                    "雷暴云到期时点不是可核验的整数微秒。",
                    {"boundary_tick": time_tick, "area_id": area_id,
                     "expires_at": copy.deepcopy(expires)},
                )
            if expires > time_tick:
                continue
            event_id = f"{area_id}:lunar-electro:expire:{expires}"
            if event_id in expected_expiries:
                raise ElementalWorldPending(
                    "world_lunar_electro_tick_invalid",
                    "雷暴云到期事件ID发生冲突。",
                    {"boundary_tick": time_tick, "event_id": event_id},
                )
            expected_expiries[event_id] = {
                "type": "special_reaction_state",
                "event_id": event_id,
                "t": expires/TICKS,
                "time_tick": expires,
                "target_id": None,
                "area_id": area_id,
                "reaction_type": "lunar_electro",
                "operation": "expire_area_thundercloud",
                "previous_target_ids": copy.deepcopy(
                    cloud.get("lunar_electro_target_ids")),
                "profile": contract_reference_runtime._profile_audit(
                    "lunar_electro"),
                "status": "executed",
            }
        (derived_schedule_by_id, derived_tick_by_id) = (
            self._derive_lunar_electro_schedule_contract(
                boundary_tick=time_tick,
                clouds_before=clouds_before,
                reference_runtime=contract_reference_runtime,
            )
        )
        scheduled_before: dict[str, dict] = {}
        for scheduled in self.special_runtime.scheduled:
            scheduled_time = scheduled.get("time_tick")
            if type(scheduled_time) is not int:
                raise ElementalWorldPending(
                    "world_lunar_electro_tick_invalid",
                    "世界级月感电待执行队列包含无效时点。",
                    {"boundary_tick": time_tick,
                     "scheduled": copy.deepcopy(scheduled)},
                )
            expected = copy.deepcopy(scheduled)
            expected.pop("schedule_key", None)
            event_id = expected.get("event_id")
            if (not isinstance(event_id, str) or not event_id
                    or event_id in scheduled_before):
                raise ElementalWorldPending(
                    "world_lunar_electro_tick_invalid",
                    "世界级月感电待执行队列包含无效或重复事件ID。",
                    {"boundary_tick": time_tick,
                     "event_id": event_id},
                )
            scheduled_before[event_id] = expected
        if scheduled_before != derived_schedule_by_id:
            raise ElementalWorldPending(
                "world_lunar_electro_schedule_conflict",
                "月感电待执行队列与雷暴云相位及到期时点推导的完整剩余跳数集不一致。",
                {"boundary_tick": time_tick,
                 "derived_event_ids": sorted(derived_schedule_by_id),
                 "scheduled_event_ids": sorted(scheduled_before)},
            )
        reference_runtime = _copy_special_runtime_for_reference(
            self.special_runtime)
        try:
            reference_due = _REFERENCE_ADVANCE_WORLD_PRE_EXPIRY(
                reference_runtime, time_tick)
        except SpecialReactionPending as exc:
            raise self._pending_from_runtime(exc) from exc
        due = self.special_runtime.advance_world_pre_expiry(time_tick)
        if (not isinstance(due, list)
                or any(not isinstance(row, dict) for row in due)
                or any(type(row.get("time_tick")) is not int
                       or not isinstance(row.get("event_id"), str)
                       or not row["event_id"] for row in due)):
            raise ElementalWorldPending(
                "world_lunar_electro_tick_invalid",
                "月感电状态机返回了无法审计的截止事件列表。",
                {"boundary_tick": time_tick,
                 "due": copy.deepcopy(due)},
            )
        due_ids = [row["event_id"] for row in due]
        canonical_due = sorted(
            due, key=lambda row: (row["time_tick"], row["event_id"]))
        if (len(due_ids) != len(set(due_ids)) or due != canonical_due):
            raise ElementalWorldPending(
                "world_lunar_electro_tick_invalid",
                "月感电截止事件必须具有唯一ID并保持规范稳定顺序。",
                {"boundary_tick": time_tick,
                 "returned_event_ids": copy.deepcopy(due_ids)},
            )
        returned_stellar_rows = {
            row["event_id"]: row
            for row in due
            if self._is_stellar_swirl_deadline_row(row)
        }
        if returned_stellar_rows != expected_stellar_rows:
            raise ElementalWorldPending(
                "world_stellar_swirl_deadline_invalid",
                "风旋截止事件与推进前冻结状态推导的完整三元组不一致。",
                {
                    "boundary_tick": time_tick,
                    "expected_event_ids": sorted(expected_stellar_rows),
                    "returned_event_ids": sorted(returned_stellar_rows),
                },
            )
        if self.special_runtime.vortex_by_area != expected_vortices_after:
            raise ElementalWorldPending(
                "world_stellar_swirl_deadline_invalid",
                "风旋截止推进没有产生唯一允许的规范后态。",
                {
                    "boundary_tick": time_tick,
                    "before": vortices_before,
                    "expected_after": expected_vortices_after,
                    "actual_after": copy.deepcopy(
                        self.special_runtime.vortex_by_area),
                },
            )
        returned_stellar_superconduct_rows = {
            row["event_id"]: row
            for row in due
            if self._is_stellar_superconduct_deadline_row(row)
        }
        if (canonical_stellar_deadline_rows(returned_stellar_superconduct_rows)
                != canonical_stellar_deadline_rows(expected_stellar_superconduct_rows)):
            raise ElementalWorldPending(
                "world_stellar_superconduct_deadline_invalid",
                "星超导截止事件与推进前隔离推导的完整状态行不一致。",
                {"boundary_tick": time_tick,
                 "expected_event_ids": sorted(
                     expected_stellar_superconduct_rows),
                 "returned_event_ids": sorted(
                     returned_stellar_superconduct_rows)},
            )
        if ((stellar_fields_before or expected_stellar_superconduct_rows)
                and self.special_runtime.stellar_field_by_area
                != expected_stellar_fields_after):
            raise ElementalWorldPending(
                "world_stellar_superconduct_deadline_invalid",
                "星超导截止推进没有产生唯一允许的规范后态。",
                {"boundary_tick": time_tick,
                 "before": stellar_fields_before,
                 "expected_after": expected_stellar_fields_after,
                 "actual_after": copy.deepcopy(
                     self.special_runtime.stellar_field_by_area)},
            )
        unexpected_pre_expiries = [
            row for row in due
            if row.get("type") == "special_reaction_state"
            and row.get("operation") == "expire_area_thundercloud"
        ]
        if unexpected_pre_expiries:
            raise ElementalWorldPending(
                "world_lunar_electro_tick_invalid",
                "月感电预推进阶段不得提前发布雷暴云到期事件。",
                {"boundary_tick": time_tick,
                 "event_ids": [row.get("event_id")
                               for row in unexpected_pre_expiries]},
            )
        clouds_after_pre_advance = self.special_runtime.lunar_cloud_by_area
        unexpected_pre_advance_clouds = {
            area_id: {
                "before": copy.deepcopy(clouds_before.get(area_id)),
                "after": copy.deepcopy(clouds_after_pre_advance.get(area_id)),
            }
            for area_id in sorted(
                set(clouds_before) | set(clouds_after_pre_advance))
            if clouds_after_pre_advance.get(area_id)
            != clouds_before.get(area_id)
        }
        if unexpected_pre_advance_clouds:
            raise ElementalWorldPending(
                "world_lunar_electro_tick_invalid",
                "月感电预推进阶段改写了雷暴云；只允许后续跳数接纳与到期提交改写。",
                {"boundary_tick": time_tick,
                 "unexpected_clouds": unexpected_pre_advance_clouds},
            )
        returned_ticks = [
            row for row in due
            if row.get("type") == "special_reaction_tick_request"
            and row.get("reaction_type") == "lunar_electro"
        ]
        returned_ids = [row.get("event_id") for row in returned_ticks]
        if (len(returned_ids) != len(set(returned_ids))
                or set(returned_ids) != set(derived_tick_by_id)
                or any(row != derived_tick_by_id.get(row.get("event_id"))
                       for row in returned_ticks)):
            raise ElementalWorldPending(
                "world_lunar_electro_tick_invalid",
                "月感电状态机返回的跳数与边界前冻结的调度请求不一致。",
                {"boundary_tick": time_tick,
                 "scheduled_event_ids": sorted(derived_tick_by_id),
                 "returned_event_ids": copy.deepcopy(returned_ids)},
            )
        lunar_due = [row for row in due
                     if row.get("reaction_type") == "lunar_electro"]
        for event in lunar_due:
            self._validate_lunar_electro_due_contract(
                event,
                boundary_tick=time_tick,
                clouds_before=clouds_before,
            )
        if (canonical_stellar_trace(due) != canonical_stellar_trace(reference_due)
                or canonical_stellar_runtime_snapshot(self._special_commit_snapshot(self.special_runtime))
                != canonical_stellar_runtime_snapshot(self._special_commit_snapshot(reference_runtime))):
            raise ElementalWorldPending(
                "world_special_deadline_producer_mismatch",
                "特殊截止生产器的返回事件或完整后态与隔离参考推进不一致。",
                {
                    "boundary_tick": time_tick,
                    "reference_event_ids": [row.get("event_id")
                                            for row in reference_due],
                    "returned_event_ids": [row.get("event_id")
                                           for row in due],
                },
            )
        (expanded, lunar_batch_count, stellar_batch_count,
         returned_expiries) = self._consume_special_generated_batch(
             due,
             time_tick=time_tick,
             source="deadline",
             expected_stellar_superconduct_rows=(
                 expected_stellar_superconduct_rows),
         )
        if self.special_runtime.vortex_by_area != expected_vortices_after:
            raise ElementalWorldPending(
                "world_stellar_swirl_deadline_invalid",
                "风旋截止批消费改写了冻结后态。",
                {"boundary_tick": time_tick,
                 "expected_after": expected_vortices_after,
                 "actual_after": copy.deepcopy(
                     self.special_runtime.vortex_by_area)},
            )

        returned_expiry_ids = [row.get("event_id")
                               for row in returned_expiries]
        if (len(returned_expiry_ids) != len(set(returned_expiry_ids))
                or set(returned_expiry_ids) != set(expected_expiries)
                or any(row != expected_expiries.get(row.get("event_id"))
                       for row in returned_expiries)):
            raise ElementalWorldPending(
                "world_lunar_electro_tick_invalid",
                "雷暴云到期事件与推进前冻结的云状态不一致。",
                {"boundary_tick": time_tick,
                 "expected_expiry_event_ids": sorted(expected_expiries),
                 "returned_expiry_event_ids": copy.deepcopy(
                     returned_expiry_ids)},
            )
        for event in returned_expiries:
            self._validate_lunar_electro_due_contract(
                event,
                boundary_tick=time_tick,
                clouds_before=clouds_before,
            )

        expired_area_ids = {
            row["area_id"] for row in expected_expiries.values()
        }
        canonical_expired_cloud = {
            "lunar_electro_active": False,
            "lunar_electro_expires_at": None,
            "lunar_electro_target_ids": [],
            "lunar_electro_last_attack_by_target": {},
            "lunar_electro_phase_by_target": {},
        }
        invalid_poststates = {
            area_id: copy.deepcopy(
                self.special_runtime.lunar_cloud_by_area.get(area_id))
            for area_id in sorted(expired_area_ids)
            if self.special_runtime.lunar_cloud_by_area.get(area_id)
            != canonical_expired_cloud
        }
        if invalid_poststates:
            raise ElementalWorldPending(
                "world_lunar_electro_tick_invalid",
                "雷暴云到期事件没有产生完整的规范清空后态。",
                {"boundary_tick": time_tick,
                 "invalid_expired_clouds": invalid_poststates},
            )
        return expanded, lunar_batch_count, stellar_batch_count

    def advance(self, time: int) -> dict:
        """Advance all targets and the one world-owned special clock atomically."""
        if type(time) is not int or time < self.time:
            raise ValueError("世界元素状态时间必须单调且为非负整数微秒。")

        def commit(world: ElementalWorldRuntime) -> dict:
            world._assert_world_clock_synchronized()
            generated: list[dict] = []
            lunar_electro_batch_count = 0
            stellar_swirl_batch_count = 0
            while True:
                deadline = (world.special_runtime.next_deadline()
                            if world.special_runtime is not None else None)
                boundary = (deadline if deadline is not None and deadline <= time
                            else time)
                generated.extend(world._advance_targets_to(boundary))
                (special_rows, lunar_batch_count,
                 stellar_batch_count) = world._advance_special_to(boundary)
                generated.extend(special_rows)
                lunar_electro_batch_count += lunar_batch_count
                stellar_swirl_batch_count += stellar_batch_count
                world.time = boundary

                next_deadline = (world.special_runtime.next_deadline()
                                 if world.special_runtime is not None else None)
                if next_deadline is not None and next_deadline <= boundary:
                    raise ElementalWorldPending(
                        "world_special_deadline_stalled",
                        "星月状态推进后仍保留当前或过去的截止事件。",
                        {"boundary_tick": boundary,
                         "next_deadline_tick": next_deadline},
                    )
                if boundary == time:
                    break

            cursor = (
                world.publish_generated_events(generated)
                if generated else len(world._generated_events)
            )
            return {
                "time_tick": time,
                "target_ids": tuple(sorted(world._targets)),
                "generated_event_count": len(generated),
                "cursor": cursor,
                "lunar_electro_batch_count": lunar_electro_batch_count,
                "stellar_swirl_batch_count": stellar_swirl_batch_count,
            }

        return self.execute_atomic(commit)

    def attempt_stellar_swirl_packet(self, children: Iterable[dict]) -> dict:
        """Route and commit one explicit same-parent Star-Swirl packet."""
        from genshin_engine.elemental_stellar_packet import (
            attempt_stellar_swirl_packet,
        )

        return attempt_stellar_swirl_packet(self, children)

    def state(self) -> dict:
        return {
            "time_tick": self.time,
            "target_ids": sorted(self._targets),
            "areas": {
                area_id: sorted(target_ids)
                for area_id, target_ids in sorted(self._targets_by_area.items())
            },
            "targets": {
                target_id: self._targets[target_id].state()
                for target_id in sorted(self._targets)
            },
            "crystallize_world": self.crystallize_world.state(),
            "bloom_core_world": self.bloom_core_state.state(),
            "special_reactions": (
                self.special_runtime.state() if self.special_runtime else None),
            "generated_event_cursor": len(self._generated_events),
        }


# Atomic control flow must not dispatch through mutable instance attributes.
# These import-time class descriptors are deliberately assigned only after the
# class body exists; method globals resolve them when transactions execute.
_REFERENCE_CRYSTALLIZE_WORLD_RESTORE = CrystallizeWorldState.restore
_REFERENCE_BLOOM_CORE_RESTORE = BloomCoreState.restore
_REFERENCE_WORLD_SNAPSHOT = ElementalWorldRuntime.snapshot
_REFERENCE_WORLD_RESTORE_OBJECT = ElementalWorldRuntime._restore_object
_REFERENCE_WORLD_RESTORE_EXACT_CLASS = (
    ElementalWorldRuntime._restore_exact_class
)
_REFERENCE_WORLD_ATOMIC_ROOTS = ElementalWorldRuntime._atomic_roots
_REFERENCE_WORLD_RESTORE_ATOMIC_ROOTS = (
    ElementalWorldRuntime._restore_atomic_roots
)
_REFERENCE_WORLD_ASSERT_ATOMIC_COMMIT = (
    ElementalWorldRuntime._assert_atomic_commit_invariants
)
_REFERENCE_WORLD_ASSERT_CLOCK_SYNCHRONIZED = (
    ElementalWorldRuntime._assert_world_clock_synchronized
)
_REFERENCE_WORLD_TARGET_IDENTITY_SNAPSHOT = (
    ElementalWorldRuntime._target_identity_snapshot
)
_REFERENCE_WORLD_TARGET_IDENTITY_CHANGES = (
    ElementalWorldRuntime._target_identity_changes
)
_REFERENCE_WORLD_DOMAIN_STRUCTURE_VIOLATIONS = _domain_structure_violations
