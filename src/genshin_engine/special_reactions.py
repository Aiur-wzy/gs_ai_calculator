"""Input-driven native runtime for Stellar and Lunar reaction replacements.

This module deliberately contains no character-name dispatch.  A caller must
explicitly enable a replacement and declare the qualifying providers and its
evidence.  Missing or pending mechanics stop that event with an auditable
``SpecialReactionPending`` issue instead of falling back to an invented rule.

The elemental gauge runtime owns aura consumption and determines that an
ordinary reaction occurred.  This module is the next pipeline stage: it may
replace that ordinary reaction, update the replacement-specific state, and
emit damage *requests*.  The requests are resolved through the existing
Lunar/Stellar formula in :func:`resolve_special_damage_event` after the caller
samples the contributors at the damage event time.
"""
from __future__ import annotations

import copy
from functools import lru_cache
from types import FunctionType, MappingProxyType
from typing import Any

from genshin_engine import special_reaction_contracts as _special_contracts
from genshin_engine import special_reaction_profiles as _special_profiles
from genshin_engine import special_reaction_validation as _special_validation
from genshin_engine.constants import (
    LUNAR_FACTOR,
    STELLAR_FACTOR,
    stellar_superconduct_factor,
)
from genshin_engine.formulas import amplify_em_bonus, lunar_stellar_damage
from genshin_engine.rotation import TICKS
from genshin_engine.special_damage_formulas import (
    SpecialFormulaServices,
)
from genshin_engine.special_damage_formulas import (
    calculate_direct_special_damage as _calculate_direct_special_damage_impl,
)
from genshin_engine.special_damage_formulas import (
    calculate_weighted_special_damage as _calculate_weighted_special_damage_impl,
)
from genshin_engine.special_damage_formulas import (
    resolve_special_damage_event as _resolve_special_damage_event_impl,
)
from genshin_engine.special_deadline_lunar_bloom import (
    LunarBloomDeadlineStep,
    plan_lunar_bloom_deadline_steps,
)
from genshin_engine.special_deadline_stellar_field import (
    StellarFieldDeadlineStep,
    plan_stellar_field_deadline_steps,
)
from genshin_engine.special_handler_registry import (
    HandlerBindingUnavailable,
    resolve_exact_handler_binding,
)
from genshin_engine.special_lunar_tick_admission import (
    LunarTickAdmissionPlan,
    LunarTickAdmissionProposal,
    plan_lunar_tick_admission,
)
from genshin_engine.special_reaction_contracts import (
    _BASE_MECHANICS_KEYS,  # noqa: F401 -- preserved public/diagnostic facade
    _CONTRIBUTOR_KEYS,
    _MECHANICS_KEYS,
    _PROFILE_META_KEYS,
    _QUALIFICATION_KEYS,  # noqa: F401 -- preserved public/diagnostic facade
    DIRECT_ABILITY_BINDING_KEYS,  # noqa: F401 -- preserved public/diagnostic facade
    DIRECT_LUNAR_ELEMENTS,  # noqa: F401 -- preserved public/diagnostic facade
    DIRECT_LUNAR_SCALING_STATS,  # noqa: F401 -- preserved public/diagnostic facade
    DIRECT_STELLAR_ELEMENTS,  # noqa: F401 -- preserved public/diagnostic facade
    DIRECT_STELLAR_SCALING_STATS,  # noqa: F401 -- preserved public/diagnostic facade
    LUNAR_STELLAR_WEIGHTS,
    REPLACEMENT_CONTRACTS,
    REQUIRED_CAPABILITY_TAGS,
    SPECIAL_REACTION_SCHEMA_VERSION,  # noqa: F401 -- preserved public/diagnostic facade
    SPECIAL_REACTION_TYPES,  # noqa: F401 -- preserved public/diagnostic facade
    WEIGHTED_REACTION_TYPES,
    _finite_number,
    _issue,  # noqa: F401 -- preserved public/diagnostic facade
    _seconds_to_ticks,
)
from genshin_engine.special_reaction_profiles import (
    REVIEWED_SPECIAL_REACTION_PROFILES,
    _load_reviewed_direct_special_damage_profiles,  # noqa: F401 -- preserved public/diagnostic facade
    _profile_hash,
    _validate_direct_ability_binding,  # noqa: F401 -- preserved public/diagnostic facade
    _validate_reviewed_direct_profile_registration,  # noqa: F401 -- preserved public/diagnostic facade
    direct_lunar_damage_profile_content_hash,  # noqa: F401 -- preserved public/diagnostic facade
    direct_stellar_damage_profile_content_hash,  # noqa: F401 -- preserved public/diagnostic facade
    load_reviewed_direct_special_damage_profiles,  # noqa: F401 -- preserved public/diagnostic facade
    load_reviewed_special_reaction_profiles,  # noqa: F401 -- preserved public/diagnostic facade
    validate_direct_lunar_damage_profile,
    validate_direct_stellar_damage_profile,
)
from genshin_engine.special_reaction_reference_deadlines_v1 import (
    build_reference_special_deadlines_v1,
)
from genshin_engine.special_reaction_reference_formulas_v1 import (
    build_reference_special_formulas_v1,
)
from genshin_engine.special_reaction_reference_lunar_tick_v1 import (
    build_reference_lunar_tick_v1,
)
from genshin_engine.special_reaction_reference_packet_v1 import (
    build_reference_packet_v1,
)
from genshin_engine.special_reaction_reference_readiness_v1 import (
    build_reference_readiness_v1,
)
from genshin_engine.special_reaction_reference_replace_v1 import (
    build_reference_replace_v1,
)
from genshin_engine.special_reaction_validation import (
    special_reaction_capabilities,  # noqa: F401 -- preserved public/diagnostic facade
    validate_special_reaction_model,
)
from genshin_engine.special_replace_stellar_packet import (
    StellarSwirlPacketProposal,
    stellar_swirl_packet_binding,
)
from genshin_engine.special_replacement_handlers import (
    LunarBloomProposal,
    LunarBloomTransition,
    LunarCrystallizeProposal,
    LunarCrystallizeTransition,
    LunarElectroProposal,
    LunarElectroTransition,
    StellarFieldProposal,
    StellarFieldTransition,
    StellarSwirlProposal,
    StellarSwirlTransition,
    legacy_replacement_family_binding,
    replacement_schedule_fingerprint,
    replacement_state_fingerprint,
)
from genshin_engine.special_stellar_detonation import (
    StellarVortexDetonationProposal,
    plan_stellar_vortex_detonation,
)


class SpecialReactionPending(ValueError):
    """A supported path whose required game fact is still unknown."""

    def __init__(self, code: str, message: str, event_id: str, details: dict | None = None):
        super().__init__(message)
        self.issue = {
            "code": code,
            "message": message,
            "event_id": event_id,
            "severity": "pending",
            **copy.deepcopy(details or {}),
        }


def _reaction_elements(reaction: dict) -> frozenset[str]:
    values = []
    for field in ("aura_element", "trigger_element"):
        value = reaction.get(field)
        if isinstance(value, str):
            values.append(value)
    # Existing swirl receipts additionally expose reaction_element.  It is the
    # aura element and therefore only fills a missing aura field.
    if not values and isinstance(reaction.get("reaction_element"), str):
        values.append(reaction["reaction_element"])
    return frozenset(values)


def _factor_for(reaction_type: str, *, layers: int | None = None) -> tuple[str, float]:
    if reaction_type == "stellar_superconduct":
        if type(layers) is not int or not 0 <= layers <= 1_000_000:
            raise ValueError("星超导直伤必须显式提供非负整数layers。")
        return "stellar", stellar_superconduct_factor(layers)
    if reaction_type in STELLAR_FACTOR:
        return "stellar", STELLAR_FACTOR[reaction_type]
    if reaction_type in LUNAR_FACTOR:
        return "lunar", LUNAR_FACTOR[reaction_type]
    raise ValueError(f"未知月曜/星烁伤害类型: {reaction_type!r}")


def _validate_contributor(value: Any, index: int, *, reaction_base: bool = True) -> dict:
    path = f"contributors[{index}]"
    if not isinstance(value, dict):
        raise ValueError(path+"必须是对象。")  # noqa: TRY004 - uniform input validation
    extra = set(value) - _CONTRIBUTOR_KEYS
    if extra:
        raise ValueError(path+"包含公式不会读取的字段: "+", ".join(sorted(extra)))
    if not isinstance(value.get("id"), str) or not value["id"].strip():
        raise ValueError(path+".id不能为空。")
    if type(value.get("level")) is not int or value["level"] <= 0:
        raise ValueError(path+".level必须是正整数。")
    if reaction_base and value["level"] not in (70, 80, 90, 95, 100):
        raise ValueError(path+".level必须是反应基础值表支持的70/80/90/95/100。")
    for name in ("em", "crit_rate", "crit_dmg"):
        if not _finite_number(value.get(name), minimum=0):
            raise ValueError(path+f".{name}必须是有限非负数。")
    for name in set(_CONTRIBUTOR_KEYS) - {"id", "level", "em", "crit_rate", "crit_dmg", "source_refs"}:
        if name in value and not _finite_number(value[name]):
            raise ValueError(path+f".{name}必须是有限数值。")
    refs = value.get("source_refs", [])
    if not isinstance(refs, list) or any(not isinstance(ref, dict) or not ref for ref in refs):
        raise ValueError(path+".source_refs必须是对象数组。")
    return copy.deepcopy(value)


def _validate_stellar_swirl_role_provenance(
    reaction_type: str,
    contributor_ids: list[str],
    provenance: Any,
    *,
    event_id: str,
) -> dict:
    """Validate the finite role slots used by the two Stellar-Swirl components."""
    if not isinstance(provenance, dict):
        raise SpecialReactionPending(
            "stellar_swirl_role_provenance_missing",
            "星扩散伤害缺少风触发者与冰附着者角色来源，不能套用全局排序。",
            event_id,
        )
    expected = {
        "contract", "component_policy", "participant_ids",
        "anemo_trigger_ids", "cryo_applier_ids", "status",
    }
    if set(provenance) != expected or provenance.get("contract") != (
            "stellar-swirl-role-provenance-v1") or provenance.get("status") != "ready":
        raise SpecialReactionPending(
            "stellar_swirl_role_provenance_invalid",
            "星扩散角色来源没有满足受审有限契约。",
            event_id, {"role_provenance": copy.deepcopy(provenance)},
        )
    lists = {}
    for name in ("participant_ids", "anemo_trigger_ids", "cryo_applier_ids"):
        values = provenance.get(name)
        if (not isinstance(values, list) or not values
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))):
            raise SpecialReactionPending(
                "stellar_swirl_role_provenance_invalid",
                "星扩散角色来源列表必须是非空、唯一角色ID数组。",
                event_id, {"field": name},
            )
        lists[name] = values
    participants = set(lists["participant_ids"])
    if participants != set(contributor_ids):
        raise SpecialReactionPending(
            "stellar_swirl_role_provenance_mismatch",
            "星扩散角色来源必须完整覆盖且只覆盖本次贡献者。",
            event_id,
            {"participant_ids": lists["participant_ids"],
             "contributor_ids": copy.deepcopy(contributor_ids)},
        )
    anemo = set(lists["anemo_trigger_ids"])
    cryo = set(lists["cryo_applier_ids"])
    if not anemo <= participants or not cryo <= participants:
        raise SpecialReactionPending(
            "stellar_swirl_role_provenance_mismatch",
            "星扩散风/冰角色来源必须属于本次参与者。",
            event_id,
        )
    collision = sorted(anemo & cryo)
    if collision:
        raise SpecialReactionPending(
            "stellar_swirl_dual_role_collision_pending",
            "同一角色同时占风触发与冰附着候选时的槽位优先级尚未核实。",
            event_id, {"dual_role_character_ids": collision},
        )
    expected_policy = (
        "immediate_anemo_trigger_fixed_first"
        if reaction_type == "stellar_swirl_anemo"
        else "vortex_cryo_best_then_anemo_best"
    )
    if provenance.get("component_policy") != expected_policy:
        raise SpecialReactionPending(
            "stellar_swirl_role_policy_mismatch",
            "星扩散角色来源策略与伤害分项不一致。",
            event_id, {"expected_component_policy": expected_policy},
        )
    if reaction_type == "stellar_swirl_anemo" and len(anemo) != 1:
        raise SpecialReactionPending(
            "stellar_swirl_anemo_trigger_ambiguous",
            "即时风分项必须绑定当前child唯一风触发者。",
            event_id, {"anemo_trigger_ids": sorted(anemo)},
        )
    return copy.deepcopy(provenance)


def _weighted_outcome_ranking(
    reaction_type: str,
    actual: list[dict],
    role_provenance: dict | None,
) -> list[dict]:
    """Assign 0.6/.3/.05/.05 to one concrete independent-Crit outcome."""
    by_id = {row["contributor_id"]: row for row in actual}
    if not reaction_type.startswith("stellar_swirl_"):
        return sorted(actual, key=lambda value: (
            -value["damage"], value["contributor_id"]))
    anemo_ids = role_provenance["anemo_trigger_ids"]
    cryo_ids = role_provenance["cryo_applier_ids"]
    assigned: list[dict] = []
    if reaction_type == "stellar_swirl_anemo":
        assigned.append(by_id[anemo_ids[0]])
    else:
        assigned.append(min(
            (by_id[value] for value in cryo_ids),
            key=lambda value: (-value["damage"], value["contributor_id"])))
        assigned.append(min(
            (by_id[value] for value in anemo_ids),
            key=lambda value: (-value["damage"], value["contributor_id"])))
    assigned_ids = {row["contributor_id"] for row in assigned}
    remainder = sorted(
        (row for row in actual if row["contributor_id"] not in assigned_ids),
        key=lambda value: (-value["damage"], value["contributor_id"]),
    )
    return [*assigned, *remainder]


def _formula_services() -> SpecialFormulaServices:
    return SpecialFormulaServices(
        WEIGHTED_REACTION_TYPES, LUNAR_STELLAR_WEIGHTS, _factor_for,
        _finite_number, _validate_contributor, _validate_stellar_swirl_role_provenance,
        _weighted_outcome_ranking, lunar_stellar_damage, amplify_em_bonus,
        calculate_weighted_special_damage, calculate_direct_special_damage,
    )


def calculate_weighted_special_damage(
    reaction_type: str,
    contributors: list[dict],
    *,
    amplify: dict | None = None,
    role_provenance: dict | None = None,
    event_id: str = "calculate_weighted_special_damage",
) -> dict:
    """Project through the pure formula layer with the existing public contract."""
    return _calculate_weighted_special_damage_impl(
        _formula_services(), reaction_type, contributors, amplify=amplify,
        role_provenance=role_provenance, event_id=event_id)


def calculate_direct_special_damage(
    reaction_type: str,
    contributor: dict,
    *,
    multiplier: float,
    scaling: float,
    layers: int | None = None,
) -> dict:
    """Project through the pure formula layer with the existing public contract."""
    return _calculate_direct_special_damage_impl(
        _formula_services(), reaction_type, contributor,
        multiplier=multiplier, scaling=scaling, layers=layers)


def resolve_special_damage_event(
    event: dict,
    contributors: list[dict],
    *,
    amplify: dict | None = None,
    scaling: float | None = None,
) -> dict:
    """Project through the pure formula layer with the existing public contract."""
    return _resolve_special_damage_event_impl(
        _formula_services(), event, contributors, amplify=amplify, scaling=scaling)


class SpecialReactionRuntime:
    """Deterministic special-reaction state for the unified mechanics queue."""

    def __init__(
        self,
        model: dict,
        *,
        party_member_ids: list[str] | tuple[str, ...] | None = None,
        party_capability_tags: dict[str, list[str] | tuple[str, ...]] | None = None,
    ):
        issues = validate_special_reaction_model(model)
        if issues:
            raise ValueError("星月反应模型无效: " + "; ".join(
                f"{row['path']}: {row['message']}" for row in issues))
        self.model = copy.deepcopy(model)
        self.party_member_ids: tuple[str, ...] | None = None
        self.party_capability_tags: dict[str, tuple[str, ...]] = {}
        if party_member_ids is not None:
            self._set_party(party_member_ids, party_capability_tags)
        elif party_capability_tags is not None:
            raise ValueError("提供party_capability_tags时必须同时绑定party_member_ids。")
        self.time = 0
        self.trace: list[dict] = []
        self.scheduled: list[dict] = []
        self.state_by_target: dict[str, dict] = {}
        self.party_state: dict[str, Any] = {
            "lunar_bloom_dew": 0,
            "lunar_bloom_backlog": 0,
            "lunar_bloom_backlog_sources": [],
            "lunar_bloom_fill_progress": 0.0,
            "lunar_bloom_next_dew_at": None,
            "lunar_bloom_atomic_parents": [],
            "lunar_bloom_full_cap_unresolved": [],
            "lunar_crystallize_harmony_count": 0,
            "lunar_crystallize_contributor_ids": [],
            "lunar_crystallize_harmony_ledger": [],
            "lunar_crystallize_last_harmony_attack_at": None,
        }
        self.stellar_field_by_area: dict[str, dict] = {}
        self.vortex_by_area: dict[str, dict] = {}
        self.moondrift_by_area: dict[str, dict] = {}
        self.lunar_cloud_by_area: dict[str, dict] = {}
        # A world-owned deadline uses a narrow two-stage protocol for Lunar-
        # Charged.  The scheduled tick at the exact cloud endpoint must sample
        # the still-live cloud, while the expiry itself remains part of the
        # same outer world transaction.  This private batch exists only
        # between ``advance_world_pre_expiry`` and
        # ``finalize_world_lunar_electro_expiry``; ordinary ``advance`` never
        # exposes or leaves a half-finished deadline.
        self._pending_world_lunar_electro_expiry: dict | None = None
        self._world_defer_lunar_electro_expiry = False

    def _set_party(self, party_member_ids, party_capability_tags=None) -> None:
        if (not isinstance(party_member_ids, (list, tuple))
                or not 1 <= len(party_member_ids) <= 4
                or any(not isinstance(value, str) or not value.strip() for value in party_member_ids)
                or len(set(party_member_ids)) != len(party_member_ids)):
            raise ValueError("party_member_ids必须是1至4个唯一非空角色ID。")
        self.party_member_ids = tuple(party_member_ids)
        tags = party_capability_tags or {}
        if not isinstance(tags, dict) or any(key not in self.party_member_ids for key in tags):
            raise ValueError("party_capability_tags只能为实际队伍成员提供能力标签。")
        normalized: dict[str, tuple[str, ...]] = {}
        for member_id in self.party_member_ids:
            values = tags.get(member_id, ())
            if (not isinstance(values, (list, tuple))
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError("每名队员的能力标签必须是唯一非空字符串数组。")
            normalized[member_id] = tuple(values)
        self.party_capability_tags = normalized

    def bind_party(
        self,
        party_member_ids: list[str] | tuple[str, ...],
        party_capability_tags: dict[str, list[str] | tuple[str, ...]] | None = None,
    ) -> None:
        """Bind the real party before execution; qualification strings alone are insufficient."""
        if (self.trace or self.scheduled or self.state_by_target
                or self.stellar_field_by_area or self.vortex_by_area
                or self.moondrift_by_area or self.lunar_cloud_by_area
                or self.time):
            raise ValueError("星月反应运行开始后不能更换队伍绑定。")
        self._set_party(party_member_ids, party_capability_tags)

    def _target_state(self, target_id: str) -> dict:
        return self.state_by_target.setdefault(target_id, {})

    @staticmethod
    def _empty_stellar_field_state() -> dict:
        return {
            "stellar_superconduct_active": False,
            "stellar_superconduct_layers": 0,
            "stellar_superconduct_settled_layers": 0,
            "stellar_superconduct_factor": 1.0,
            "stellar_superconduct_ordinary_bonus": 0.0,
            "stellar_superconduct_physical_res_delta": 0.0,
            "stellar_superconduct_buff_expires_at": None,
            "stellar_superconduct_expires_at": None,
            "stellar_superconduct_next_settlement": None,
            "stellar_superconduct_origin_applications": [],
            "stellar_superconduct_atomic_parents": [],
        }

    def _stellar_field_state(self, area_id: str) -> dict:
        if not isinstance(area_id, str) or not area_id:
            raise ValueError("area_id必须是非空空间桶ID。")
        return self.stellar_field_by_area.setdefault(
            area_id, self._empty_stellar_field_state())

    def _vortex_state(self, area_id: str) -> dict:
        if not isinstance(area_id, str) or not area_id:
            raise ValueError("area_id必须是非空空间桶ID。")
        return self.vortex_by_area.setdefault(area_id, {
            "stellar_swirl_count": 0,
            "stellar_swirl_level": 0,
            "stellar_swirl_detonate_at": None,
            "stellar_swirl_owner": None,
            "stellar_swirl_action_id": None,
            "stellar_swirl_parent_reaction_id": None,
            "stellar_swirl_contributor_ids": [],
            "stellar_swirl_anemo_trigger_ids": [],
            "stellar_swirl_cryo_applier_ids": [],
        })

    def _moondrift_state(self, area_id: str) -> dict:
        if not isinstance(area_id, str) or not area_id:
            raise ValueError("area_id必须是非空空间桶ID。")
        return self.moondrift_by_area.setdefault(area_id, {
            "lunar_crystallize_cages": 0,
            "lunar_crystallize_expires_at": None,
            "lunar_crystallize_last_harmony_attack_at": None,
        })

    def _lunar_cloud_state(self, area_id: str) -> dict:
        if not isinstance(area_id, str) or not area_id:
            raise ValueError("area_id必须是非空空间桶ID。")
        return self.lunar_cloud_by_area.setdefault(area_id, {
            "lunar_electro_active": False,
            "lunar_electro_expires_at": None,
            "lunar_electro_target_ids": [],
            "lunar_electro_last_attack_by_target": {},
            "lunar_electro_phase_by_target": {},
        })

    def _lunar_electro_tick_request(
        self,
        *,
        area_id: str,
        target_id: str,
        phase: dict,
        tick_index: int,
        time_tick: int,
    ) -> dict:
        mechanics = self._config("lunar_electro")["mechanics"]
        return {
            "type": "special_reaction_tick_request",
            "event_id": (
                f"{area_id}:lunar-electro:target:{target_id}:"
                f"phase:{phase['first_attack_at']}:tick:{tick_index}"),
            "t": time_tick/TICKS,
            "time_tick": time_tick,
            "target_id": target_id,
            "area_id": area_id,
            "target_selection_policy": "bound_area_entity",
            "owner": phase["owner"],
            "parent_reaction_id": phase["parent_reaction_id"],
            "reaction_type": "lunar_electro",
            "component": "thundercloud",
            "tick_index": tick_index,
            "requirements": {
                "coexisting_elements": ["hydro", "electro"],
                "consume_each_gu": mechanics["aura_consumption_gu"],
                "per_target_attack_cooldown_seconds": (
                    mechanics["tick_interval_seconds"]),
                "owner_residual_contract": (
                    "same-element-owner-residual-max-v1"),
                "contributor_application_window_seconds": 5,
            },
            "contributor_ids": [],
            "contributor_sample_policy": (
                "tick_time_live_positive_owner_residual_and_recent_five_seconds"),
            "schedule_key": ("area", area_id, "lunar_electro"),
            "profile": self._profile_audit("lunar_electro"),
            "status": "ready_for_aura_check",
        }

    def expected_world_lunar_electro_ticks(self, time: int) -> list[dict]:
        """Derive the complete due tick set from cloud phase, not the queue.

        The queue is an execution cache, not the authority.  Rebuilding every
        not-yet-emitted tick through ``time`` from first attack, interval,
        endpoint and phase index makes a deleted middle queue row observable.
        Returned rows use the same public shape as :meth:`advance`.
        """
        if type(time) is not int or time < self.time:
            raise ValueError("月感电预期跳数时点必须单调且为整数微秒。")
        config = self._config("lunar_electro")
        if config is None:
            if self.lunar_cloud_by_area:
                raise RuntimeError("未开启月感电却存在雷暴云状态。")
            return []
        interval = _seconds_to_ticks(
            config["mechanics"]["tick_interval_seconds"],
            "tick_interval_seconds",
        )
        if interval <= 0:
            raise RuntimeError("月感电跳数间隔必须为正整数微秒。")
        emitted = {
            row.get("event_id") for row in self.trace
            if (isinstance(row, dict)
                and row.get("type") == "special_reaction_tick_request"
                and row.get("reaction_type") == "lunar_electro")
        }
        expected: list[dict] = []
        for area_id in sorted(self.lunar_cloud_by_area):
            cloud = self.lunar_cloud_by_area[area_id]
            if not cloud.get("lunar_electro_active"):
                continue
            expires = cloud.get("lunar_electro_expires_at")
            targets = cloud.get("lunar_electro_target_ids")
            phases = cloud.get("lunar_electro_phase_by_target")
            if (type(expires) is not int
                    or not isinstance(targets, list)
                    or len(targets) != len(set(targets))
                    or any(not isinstance(value, str) or not value
                           for value in targets)
                    or not isinstance(phases, dict)
                    or set(phases) != set(targets)):
                raise SpecialReactionPending(
                    "lunar_electro_phase_contract_invalid",
                    "月感电雷暴云的到期、目标或相位绑定无法完整推导跳数。",
                    f"{area_id}:lunar-electro:phase",
                )
            for target_id in targets:
                phase = phases[target_id]
                if (not isinstance(phase, dict)
                        or set(phase) != {
                            "first_attack_at", "next_tick_index", "owner",
                            "parent_reaction_id",
                        }
                        or type(phase.get("first_attack_at")) is not int
                        or type(phase.get("next_tick_index")) is not int
                        or phase["next_tick_index"] < 1
                        or not isinstance(phase.get("owner"), str)
                        or not phase["owner"]
                        or not isinstance(phase.get("parent_reaction_id"), str)
                        or not phase["parent_reaction_id"]):
                    raise SpecialReactionPending(
                        "lunar_electro_phase_contract_invalid",
                        "月感电目标相位记录缺少可推导的首跳、索引或来源。",
                        f"{area_id}:lunar-electro:target:{target_id}:phase",
                    )
                first = phase["first_attack_at"]
                scheduled_last_index = (
                    0 if first > expires else (expires-first)//interval + 1)
                if phase["next_tick_index"] != scheduled_last_index+1:
                    raise SpecialReactionPending(
                        "lunar_electro_phase_contract_invalid",
                        "月感电相位的next_tick_index与到期前应排完的跳数不一致。",
                        f"{area_id}:lunar-electro:target:{target_id}:phase",
                        {"expected_next_tick_index": scheduled_last_index+1,
                         "actual_next_tick_index": phase["next_tick_index"]},
                    )
                if scheduled_last_index > 1_000_000:
                    raise SpecialReactionPending(
                        "lunar_electro_phase_contract_invalid",
                        "月感电相位派生跳数超过安全上限。",
                        f"{area_id}:lunar-electro:target:{target_id}:phase",
                    )
                for index in range(1, scheduled_last_index+1):
                    tick_at = first+(index-1)*interval
                    if tick_at > time:
                        break
                    row = self._lunar_electro_tick_request(
                        area_id=area_id,
                        target_id=target_id,
                        phase=phase,
                        tick_index=index,
                        time_tick=tick_at,
                    )
                    if row["event_id"] in emitted:
                        continue
                    row.pop("schedule_key", None)
                    expected.append(row)
        expected.sort(key=lambda row: (row["time_tick"], row["event_id"]))
        return expected

    def state(self) -> dict:
        scheduled = copy.deepcopy(sorted(
            self.scheduled, key=lambda row: (row["time_tick"], row["event_id"])))
        for row in scheduled:
            row.pop("schedule_key", None)
        party = copy.deepcopy(self.party_state)
        bloom_config = self._config("lunar_bloom")
        next_dew = party.get("lunar_bloom_next_dew_at")
        if bloom_config and next_dew is not None and party["lunar_bloom_backlog"] > 0:
            interval = _seconds_to_ticks(
                bloom_config["mechanics"]["dew_interval_seconds"], "dew_interval_seconds")
            party["lunar_bloom_fill_progress"] = max(
                0.0, min(1.0, 1 - (next_dew-self.time)/interval))
        else:
            party["lunar_bloom_fill_progress"] = 0.0
        return {
            "t": self.time / TICKS,
            "party_member_ids": list(self.party_member_ids) if self.party_member_ids else None,
            "party_capability_tags": {
                key: list(value) for key, value in self.party_capability_tags.items()
            },
            "party": party,
            "stellar_areas": copy.deepcopy(self.stellar_field_by_area),
            "areas": copy.deepcopy(self.vortex_by_area),
            "moondrift_areas": copy.deepcopy(self.moondrift_by_area),
            "lunar_electro_areas": copy.deepcopy(self.lunar_cloud_by_area),
            "targets": copy.deepcopy(self.state_by_target),
            "scheduled": scheduled,
            "next_deadline": self.next_deadline(),
        }

    def next_deadline(self) -> int | None:
        """Earliest microsecond deadline to enqueue beside ordinary mechanics events."""
        values = [row["time_tick"] for row in self.scheduled]
        for state in self.stellar_field_by_area.values():
            for key in (
                "stellar_superconduct_next_settlement", "stellar_superconduct_expires_at",
                "stellar_superconduct_buff_expires_at",
            ):
                value = state.get(key)
                if type(value) is int and value >= self.time:
                    values.append(value)
        for state in self.vortex_by_area.values():
            value = state.get("stellar_swirl_detonate_at")
            if type(value) is int and value >= self.time:
                values.append(value)
        for state in self.moondrift_by_area.values():
            value = state.get("lunar_crystallize_expires_at")
            if type(value) is int and value >= self.time:
                values.append(value)
        for state in self.lunar_cloud_by_area.values():
            value = state.get("lunar_electro_expires_at")
            if type(value) is int and value >= self.time:
                values.append(value)
        for key in ("lunar_bloom_next_dew_at",):
            value = self.party_state.get(key)
            if type(value) is int and value >= self.time:
                values.append(value)
        values.extend(pool["expires_at"] for pool in self.party_state.get("lunar_bloom_reserve_pools", {}).values())
        return min(values) if values else None

    def _config(self, reaction_type: str) -> dict | None:
        row = self.model["replacements"].get(reaction_type)
        return row if row and row["enabled"] else None

    def _profile_audit(self, reaction_type: str) -> dict:
        """Return immutable profile provenance and any explicit assumption delta."""
        config = self._config(reaction_type)
        if config is None:
            raise ValueError("内部错误：未开启的替换没有profile。")
        mechanics = config["mechanics"]
        reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[reaction_type]
        parameters = {
            name: mechanics.get(name)
            for name in _MECHANICS_KEYS[reaction_type] - _PROFILE_META_KEYS
        }
        differences = {
            name: {"reviewed": reviewed["parameters"].get(name), "configured": value}
            for name, value in sorted(parameters.items())
            if value != reviewed["parameters"].get(name)
        }
        qualification = config["qualification"]
        present_providers = sorted(
            set(qualification["provider_ids"]) & set(self.party_member_ids or ()))
        proven_providers = [
            provider_id for provider_id in present_providers
            if qualification["capability_tag"] in self.party_capability_tags.get(provider_id, ())
        ]
        native_confidence = (
            "reviewed_native"
            if (mechanics["status"] == "confirmed"
                and qualification["status"] == "confirmed" and not differences)
            else "assumed_override"
        )
        return {
            "profile_id": mechanics["profile_id"],
            "profile_revision": mechanics["profile_revision"],
            "profile_content_hash": mechanics["profile_content_hash"],
            "evidence_refs": copy.deepcopy(reviewed["evidence_refs"]),
            "mechanics_status": mechanics["status"],
            "qualification_status": qualification["status"],
            "native_confidence": native_confidence,
            "override_differences": differences,
            "override_reason": mechanics.get("override_reason"),
            "capability_tag": qualification["capability_tag"],
            "proven_provider_ids": proven_providers,
        }

    def _require_ready(self, reaction_type: str, event_id: str) -> dict:
        row = self._config(reaction_type)
        if row is None:
            raise ValueError("内部错误：未开启的替换不能进入执行。")
        qualification = row["qualification"]
        mechanics = row["mechanics"]
        if qualification["status"] == "pending":
            raise SpecialReactionPending(
                "special_qualification_pending", "星月反应资格尚未核实。", event_id,
                {"reaction_type": reaction_type,
                 "provider_ids": copy.deepcopy(qualification["provider_ids"]),
                 "source": qualification["source"]})
        if self.party_member_ids is None:
            raise SpecialReactionPending(
                "special_party_unbound", "尚未绑定实际队伍，不能仅凭资格字符串开启替换。", event_id,
                {"reaction_type": reaction_type,
                 "provider_ids": copy.deepcopy(qualification["provider_ids"])})
        present = sorted(set(qualification["provider_ids"]) & set(self.party_member_ids))
        if not present:
            raise SpecialReactionPending(
                "special_provider_absent", "声明的星月资格提供者不在实际队伍中。", event_id,
                {"reaction_type": reaction_type,
                 "provider_ids": copy.deepcopy(qualification["provider_ids"]),
                 "party_member_ids": list(self.party_member_ids)})
        capability_tag = qualification["capability_tag"]
        proven = [
            provider_id for provider_id in present
            if capability_tag in self.party_capability_tags.get(provider_id, ())
        ]
        if not proven:
            raise SpecialReactionPending(
                "special_capability_unproven",
                "队伍成员存在，但数据库能力标签没有证明其具备该星月反应资格。",
                event_id,
                {"reaction_type": reaction_type, "provider_ids": present,
                 "required_capability_tag": capability_tag,
                 "party_capability_tags": {
                     provider_id: list(self.party_capability_tags.get(provider_id, ()))
                     for provider_id in present
                 }},
            )
        if mechanics["status"] == "pending":
            raise SpecialReactionPending(
                "special_mechanics_pending", "星月反应机制档案尚未核实。", event_id,
                {"reaction_type": reaction_type, "source": mechanics["source"]})
        if self.model["schema_version"] == 2:
            self._bound_replacement_handler(reaction_type, event_id)
        return row

    def _bound_replacement_handler(self, reaction_type, event_id, *, capability=None):
        if self.model["schema_version"] == 1:
            return legacy_replacement_family_binding(reaction_type)
        requested = self.model["handler_bindings"].get(reaction_type)
        try:
            return resolve_exact_handler_binding(reaction_type, requested, capability=capability)
        except HandlerBindingUnavailable as exc:
            raise SpecialReactionPending(
                exc.code, "当前方案的引擎绑定不可用，需按当前通用能力重新材料化。", event_id,
                {"reaction_type": reaction_type, "binding_contract": "resolved-reaction-handler-v1",
                 **exc.details}) from exc

    def _decision(
        self, reaction: dict, reaction_type: str | None, reason: str, *, record: bool = True,
    ) -> dict:
        event_id = reaction.get("id") or reaction.get("event_id") or "reaction"
        row = {
            "type": "special_reaction_decision",
            "event_id": event_id+":special-decision",
            "t": self.time / TICKS,
            "time_tick": self.time,
            "ordinary_reaction_id": event_id,
            "ordinary_reaction_type": reaction.get("type"),
            "special_reaction_type": reaction_type,
            "decision": "replace" if reaction_type else "ordinary",
            "reason": reason,
            "status": "executed",
        }
        if reaction_type:
            row["profile"] = self._profile_audit(reaction_type)
        if record:
            self.trace.append(copy.deepcopy(row))
        return row

    def _contributors(self, reaction: dict) -> list[str]:
        declared = reaction.get("contributor_ids")
        if declared is not None:
            if (not isinstance(declared, list) or not declared
                    or any(not isinstance(value, str) or not value for value in declared)
                    or len(declared) > 4 or len(set(declared)) != len(declared)):
                raise ValueError("contributor_ids必须是1至4个唯一非空角色ID。")
            result = list(declared)
        else:
            owner = reaction.get("owner")
            result = [owner] if isinstance(owner, str) and owner else []
        if self.party_member_ids is not None:
            absent = sorted(set(result) - set(self.party_member_ids))
            if absent:
                raise ValueError("反应贡献者必须属于实际队伍: "+", ".join(absent))
        return result

    def _stellar_swirl_roles(self, reaction: dict, contributors: list[str]) -> dict:
        """Derive audited role provenance from one uncommitted Cryo-Swirl proposal."""
        event_id = reaction.get("id") or reaction.get("event_id") or "stellar_swirl"
        trigger = reaction.get("trigger_owner") or reaction.get("owner")
        if (reaction.get("trigger_element") != "anemo"
                or reaction.get("aura_element") != "cryo"
                or not isinstance(trigger, str) or not trigger
                or trigger not in contributors):
            raise SpecialReactionPending(
                "stellar_swirl_role_provenance_missing",
                "星扩散提案必须保留当前child的风触发者与冰底来源。",
                event_id,
            )
        explicit_aura_owners = reaction.get("aura_owners")
        if explicit_aura_owners is None:
            explicit_aura_owners = [
                value for value in contributors if value != trigger]
            aura_owner = reaction.get("aura_owner")
            if isinstance(aura_owner, str) and aura_owner:
                explicit_aura_owners = list(dict.fromkeys(
                    [*explicit_aura_owners, aura_owner]))
        if (not isinstance(explicit_aura_owners, list)
                or not explicit_aura_owners
                or any(not isinstance(value, str) or not value
                       for value in explicit_aura_owners)
                or len(explicit_aura_owners) != len(set(explicit_aura_owners))):
            raise SpecialReactionPending(
                "stellar_swirl_role_provenance_missing",
                "星扩散提案没有可审计的冰附着者角色来源。",
                event_id,
            )
        if not set(explicit_aura_owners) <= set(contributors):
            raise SpecialReactionPending(
                "stellar_swirl_role_provenance_mismatch",
                "星扩散冰附着者不在本次贡献者集合中。",
                event_id,
            )
        collision = sorted(set(explicit_aura_owners) & {trigger})
        if collision:
            raise SpecialReactionPending(
                "stellar_swirl_dual_role_collision_pending",
                "同一角色同时作为风触发者和冰附着者时的槽位优先级尚未核实。",
                event_id, {"dual_role_character_ids": collision},
            )
        return {
            "contract": "stellar-swirl-role-provenance-v1",
            "component_policy": "immediate_anemo_trigger_fixed_first",
            "participant_ids": copy.deepcopy(contributors),
            "anemo_trigger_ids": [trigger],
            "cryo_applier_ids": copy.deepcopy(explicit_aura_owners),
            "status": "ready",
        }

    @staticmethod
    def _merge_stellar_swirl_roles(state: dict, provenance: dict, event_id: str) -> None:
        anemo = list(dict.fromkeys([
            *state["stellar_swirl_anemo_trigger_ids"],
            *provenance["anemo_trigger_ids"],
        ]))
        cryo = list(dict.fromkeys([
            *state["stellar_swirl_cryo_applier_ids"],
            *provenance["cryo_applier_ids"],
        ]))
        collision = sorted(set(anemo) & set(cryo))
        if collision:
            raise SpecialReactionPending(
                "stellar_swirl_dual_role_collision_pending",
                "风旋生命周期内同一角色跨反应同时进入风与冰候选，槽位优先级尚未核实。",
                event_id, {"dual_role_character_ids": collision},
            )
        participants = list(dict.fromkeys([
            *state["stellar_swirl_contributor_ids"],
            *provenance["participant_ids"],
        ]))
        if len(participants) > 4:
            raise ValueError("风旋生命周期最多保留四名实际队伍参与者。")
        state["stellar_swirl_contributor_ids"] = participants
        state["stellar_swirl_anemo_trigger_ids"] = anemo
        state["stellar_swirl_cryo_applier_ids"] = cryo

    @staticmethod
    def _stellar_swirl_vortex_provenance(state: dict) -> dict:
        return {
            "contract": "stellar-swirl-role-provenance-v1",
            "component_policy": "vortex_cryo_best_then_anemo_best",
            "participant_ids": copy.deepcopy(
                state["stellar_swirl_contributor_ids"]),
            "anemo_trigger_ids": copy.deepcopy(
                state["stellar_swirl_anemo_trigger_ids"]),
            "cryo_applier_ids": copy.deepcopy(
                state["stellar_swirl_cryo_applier_ids"]),
            "status": "ready",
        }

    def _damage_request(self, *, event_id: str, reaction_type: str, target_id: str | None,
                        owner: str | None, parent_reaction_id: str, time_tick: int,
                        component: str | None = None, contributor_ids: list[str] | None = None,
                        sample_policy: str = "event_time_declared_contributors",
                        area_id: str | None = None,
                        role_provenance: dict | None = None) -> dict:
        kind, factor = _factor_for(reaction_type)
        profile_type = "stellar_swirl" if reaction_type.startswith("stellar_swirl_") else reaction_type
        request = {
            "type": "special_reaction_damage_request",
            "event_id": event_id,
            "t": time_tick / TICKS,
            "time_tick": time_tick,
            "target_id": target_id,
            "area_id": area_id,
            "target_selection_policy": (
                "event_time_entities_in_area" if target_id is None and area_id else "declared_target"),
            "owner": owner,
            "parent_reaction_id": parent_reaction_id,
            "reaction_type": reaction_type,
            "damage_system": kind,
            "reaction_base": True,
            "system_factor": factor,
            "application": None,
            "component": component,
            "contributor_ids": copy.deepcopy(contributor_ids or []),
            "contributor_sample_policy": sample_policy,
            "profile": self._profile_audit(profile_type),
            "status": "ready_for_stat_sample",
        }
        if role_provenance is not None:
            request["role_provenance"] = copy.deepcopy(role_provenance)
        return request

    @staticmethod
    def _ordinary_bonus(layers: int) -> float:
        if layers <= 0:
            return .2
        if layers == 1:
            return .29
        return .28 + .01 * min(layers, 12)

    def _stellar_swirl_detonation(self, area_id: str, state: dict, time_tick: int,
                                   reason: str) -> list[dict]:
        if self.model["schema_version"] == 2:
            self._bound_replacement_handler("stellar_swirl", f"{area_id}:vortex:{time_tick}",
                                             capability="deadline_production")
        mechanics = self._config("stellar_swirl")["mechanics"]
        profile = self._profile_audit("stellar_swirl")
        transition = plan_stellar_vortex_detonation(StellarVortexDetonationProposal(
            vortex=MappingProxyType(copy.deepcopy(state)),
            mechanics=MappingProxyType(mechanics), profile=MappingProxyType(profile),
            area_id=area_id, time_tick=time_tick, reason=reason,
            damage_request=self._damage_request,
            vortex_provenance=self._stellar_swirl_vortex_provenance,
        ))
        self._commit_stellar_swirl_transition(state, transition)
        return transition.generated_events

    def _commit_stellar_field_transition(self, field, transition):
        if not isinstance(transition, StellarFieldTransition):
            raise TypeError("星超导field handler必须返回强类型状态变更计划。")
        if replacement_state_fingerprint(field) != transition.expected_prestate_fingerprint:
            raise ValueError("星超导field状态在计划与提交之间发生变化。")
        if not transition.was_active:
            field["stellar_superconduct_next_settlement"] = transition.next_settlement
            field["stellar_superconduct_layers"] = 0
            field["stellar_superconduct_atomic_parents"] = []
        field["stellar_superconduct_active"] = True
        field["stellar_superconduct_expires_at"] = transition.expires_at
        field["stellar_superconduct_physical_res_delta"] = -.4
        if not transition.was_active:
            field["stellar_superconduct_origin_applications"] = [transition.establishing_application]

    def _commit_lunar_bloom_transition(self, transition):
        if not isinstance(transition, LunarBloomTransition):
            raise TypeError("月绽放handler必须返回强类型资源变更计划。")
        if replacement_state_fingerprint(self.party_state) != transition.expected_prestate_fingerprint:
            raise ValueError("月绽放资源在计划与提交之间发生变化。")
        if transition.operation == "duplicate":
            return
        if transition.operation not in {"pending", "discard", "retain", "queue"}:
            raise ValueError("未知的月绽放资源变更。")
        self.party_state["lunar_bloom_atomic_parents"] = transition.atomic_parents
        if transition.operation == "pending":
            self.party_state["lunar_bloom_full_cap_unresolved"] = transition.full_cap_unresolved
        elif transition.operation == "retain":
            self.party_state["lunar_bloom_backlog"] = transition.backlog
            self.party_state["lunar_bloom_backlog_sources"] = transition.backlog_sources
        elif transition.operation == "queue":
            self.party_state["lunar_bloom_backlog"] = transition.backlog
            self.party_state["lunar_bloom_backlog_sources"].append(transition.atomic_parent)
            if transition.start_fill:
                self.party_state["lunar_bloom_next_dew_at"] = transition.next_dew_at

    def _commit_lunar_electro_transition(self, cloud, transition):
        if not isinstance(transition, LunarElectroTransition):
            raise TypeError("月感电handler必须返回强类型云与tick计划。")
        if (replacement_state_fingerprint(cloud) != transition.expected_prestate_fingerprint
                or replacement_schedule_fingerprint(self.scheduled) != transition.expected_schedule_fingerprint):
            raise ValueError("月感电云或tick计划在计划与提交之间发生变化。")
        cloud["lunar_electro_active"] = True
        cloud["lunar_electro_expires_at"] = transition.expires_at
        if transition.added_target_id is not None:
            cloud["lunar_electro_target_ids"].append(transition.added_target_id)
            cloud["lunar_electro_phase_by_target"][transition.added_target_id] = transition.new_target_phase
        for target_id, next_index in transition.phase_indices:
            cloud["lunar_electro_phase_by_target"][target_id]["next_tick_index"] = next_index
        self.scheduled.extend(transition.scheduled_requests)

    def _commit_stellar_swirl_transition(self, vortex, transition):
        if not isinstance(transition, StellarSwirlTransition):
            raise TypeError("星扩散handler必须返回强类型风旋变更计划。")
        if replacement_state_fingerprint(vortex) != transition.expected_prestate_fingerprint:
            raise ValueError("星扩散风旋在计划与提交之间发生变化。")
        vortex.update(
            stellar_swirl_count=transition.count, stellar_swirl_level=transition.level,
            stellar_swirl_detonate_at=transition.detonate_at,
            stellar_swirl_owner=transition.owner, stellar_swirl_action_id=transition.action_id,
            stellar_swirl_parent_reaction_id=transition.parent_reaction_id,
            stellar_swirl_contributor_ids=transition.contributor_ids,
            stellar_swirl_anemo_trigger_ids=transition.anemo_trigger_ids,
            stellar_swirl_cryo_applier_ids=transition.cryo_applier_ids,
        )

    def _commit_lunar_crystallize_transition(self, moondrift, transition):
        if not isinstance(transition, LunarCrystallizeTransition):
            raise TypeError("月结晶handler必须返回强类型月笼/谐奏计划。")
        if (replacement_state_fingerprint(self.party_state) != transition.expected_party_fingerprint
                or replacement_state_fingerprint(moondrift) != transition.expected_moondrift_fingerprint):
            raise ValueError("月结晶月笼或共享谐奏资源在计划与提交之间发生变化。")
        if transition.new_set:
            moondrift["lunar_crystallize_cages"] = transition.cages
            moondrift["lunar_crystallize_expires_at"] = transition.expires_at
        ledger = self.party_state["lunar_crystallize_harmony_ledger"]
        if transition.appended_ledger_entry is not None:
            ledger.append(transition.appended_ledger_entry)
        if transition.ledger_consume_count:
            del ledger[:transition.ledger_consume_count]
        self.party_state["lunar_crystallize_harmony_count"] = transition.harmony_count
        self.party_state["lunar_crystallize_contributor_ids"] = transition.contributor_ids
        if transition.global_attack_write:
            self.party_state["lunar_crystallize_last_harmony_attack_at"] = transition.global_last_attack
        if transition.area_attack_write:
            moondrift["lunar_crystallize_last_harmony_attack_at"] = transition.area_last_attack
        if transition.harmony_executed:
            moondrift["lunar_crystallize_expires_at"] = transition.expires_at

    def replace(self, reaction: dict, *, time: int, payload: dict | None = None) -> dict:
        """Route an uncommitted ordinary proposal, then commit exactly one path."""
        if type(time) is not int or time < self.time:
            raise ValueError("星月反应时间必须是单调非负整数微秒。")
        if not isinstance(reaction, dict) or not isinstance(reaction.get("type"), str):
            raise ValueError("普通反应回执缺少type。")  # noqa: TRY004 - public validation
        if reaction.get("commit_state") != "proposal":
            raise ValueError(
                "replace只接受commit_state='proposal'的普通反应提案；"
                "普通伤害、传播、晶片或减抗副作用必须在路由后再提交。")
        snapshot = (
            self.time, copy.deepcopy(self.state_by_target),
            copy.deepcopy(self.party_state), copy.deepcopy(self.stellar_field_by_area),
            copy.deepcopy(self.vortex_by_area), copy.deepcopy(self.moondrift_by_area),
            copy.deepcopy(self.lunar_cloud_by_area), copy.deepcopy(self.scheduled),
        )
        trace_mark = len(self.trace)
        ordinary_type = reaction["type"]
        event_id = reaction.get("id") or reaction.get("event_id") or "reaction"
        candidates = [name for name, contract in REPLACEMENT_CONTRACTS.items()
                      if contract["ordinary_type"] == ordinary_type and self._config(name)]
        if not candidates:
            due = self.advance(time)
            decision = self._decision(reaction, None, "switch_disabled")
            ordinary = {
                **copy.deepcopy(reaction), "commit_state": "committed", "route": "ordinary",
                "initial_gauge_commit_policy": "commit_elemental_proposal_once",
            }
            return {"decision": decision, "reaction": ordinary,
                    "generated_events": due, "state": self.state()}
        elements = _reaction_elements(reaction)
        reaction_type = next((name for name in candidates
                              if REPLACEMENT_CONTRACTS[name]["elements"] == elements), None)
        if reaction_type is None:
            due = self.advance(time)
            decision = self._decision(reaction, None, "element_scope_not_matched")
            ordinary = {
                **copy.deepcopy(reaction), "commit_state": "committed", "route": "ordinary",
                "initial_gauge_commit_policy": "commit_elemental_proposal_once",
            }
            return {"decision": decision, "reaction": ordinary,
                    "generated_events": due, "state": self.state()}
        if reaction_type == "stellar_swirl":
            packet_size = reaction.get("application_packet_size", 1)
            if type(packet_size) is not int or packet_size < 1:
                raise ValueError("application_packet_size必须是正整数。")
            if packet_size != 1:
                raise ValueError(
                    "多child星扩散必须通过replace_stellar_swirl_packet原子提交，"
                    "不能逐目标调用replace。")

        due = self.advance(time)
        try:
            config = self._require_ready(reaction_type, event_id)
            qualification, mechanics = config["qualification"], config["mechanics"]
            target_id = reaction.get("target_id")
            if not isinstance(target_id, str) or not target_id:
                raise ValueError("普通反应回执缺少target_id。")
            owner = reaction.get("owner")
            contributor_ids = self._contributors(reaction)
            stellar_swirl_roles = (
                self._stellar_swirl_roles(reaction, contributor_ids)
                if reaction_type == "stellar_swirl" else None)
            replaced = {
                **copy.deepcopy(reaction),
                "id": event_id+":special:"+reaction_type,
                "type": reaction_type,
                "replaces": ordinary_type,
                "ordinary_reaction_id": event_id,
                "ordinary_damage_suppressed": True,
                "ordinary_side_effects_committed": False,
                "commit_state": "committed",
                "route": "special",
                "initial_gauge_commit_policy": "commit_elemental_proposal_once",
                "qualification": {
                    "provider_ids": copy.deepcopy(qualification["provider_ids"]),
                    "present_provider_ids": sorted(
                        set(qualification["provider_ids"]) & set(self.party_member_ids or ())),
                    "status": qualification["status"],
                    "source": qualification["source"],
                    "capability_tag": qualification["capability_tag"],
                },
                "mechanics_source": mechanics["source"],
                "profile": self._profile_audit(reaction_type),
            }
            # Lunar Crystallize replaces ordinary Hydro Crystallize damage,
            # shard creation and GCD, while still satisfying mechanics that
            # listen for the broader Crystallize family.  Stable trigger tags
            # expose that alias once without fabricating a second reaction.
            replaced["reaction_trigger_tags"] = (
                ["crystallize", "lunar_crystallize"]
                if reaction_type == "lunar_crystallize"
                else [reaction_type]
            )
            generated: list[dict] = list(due)
            if reaction_type == "stellar_superconduct":
                area_id = reaction.get("area_id") or (payload or {}).get("area_id") or "battlefield.primary"
                field = self._stellar_field_state(area_id)
                binding = self._bound_replacement_handler(reaction_type, event_id, capability="replace")
                transition = binding.handler(StellarFieldProposal(
                    field=MappingProxyType(copy.deepcopy(field)),
                    mechanics=MappingProxyType(mechanics),
                    reaction=MappingProxyType(reaction),
                    area_id=area_id, event_id=event_id,
                    replacement_id=replaced["id"], target_id=target_id,
                    time=time, seconds_to_ticks=_seconds_to_ticks,
                ))
                self._commit_stellar_field_transition(field, transition)
                generated.append(transition.generated_event)
            elif reaction_type == "stellar_swirl":
                replaced["role_provenance"] = copy.deepcopy(stellar_swirl_roles)
                area_id = reaction.get("area_id") or (payload or {}).get("area_id") or "battlefield.primary"
                vortex = self._vortex_state(area_id)
                binding = self._bound_replacement_handler(reaction_type, event_id, capability="replace")
                transition = binding.handler(StellarSwirlProposal(
                    vortex=MappingProxyType(copy.deepcopy(vortex)),
                    mechanics=MappingProxyType(mechanics), reaction=MappingProxyType(reaction),
                    roles=stellar_swirl_roles, contributor_ids=contributor_ids,
                    area_id=area_id, event_id=event_id, replacement_id=replaced["id"],
                    owner=owner, target_id=target_id, time=time,
                    seconds_to_ticks=_seconds_to_ticks,
                    merge_roles=self._merge_stellar_swirl_roles,
                    damage_request=self._damage_request,
                    detonate=self._stellar_swirl_detonation,
                ))
                self._commit_stellar_swirl_transition(vortex, transition)
                generated.extend(transition.generated_events)
            elif reaction_type == "lunar_electro":
                area_id = (reaction.get("area_id") or (payload or {}).get("area_id")
                           or "battlefield.primary")
                cloud = self._lunar_cloud_state(area_id)
                binding = self._bound_replacement_handler(reaction_type, event_id, capability="replace")
                transition = binding.handler(LunarElectroProposal(
                    cloud=MappingProxyType(copy.deepcopy(cloud)),
                    mechanics=MappingProxyType(mechanics), area_id=area_id,
                    target_id=target_id, owner=owner, replacement_id=replaced["id"],
                    time=time, scheduled_basis=replacement_schedule_fingerprint(self.scheduled),
                    seconds_to_ticks=_seconds_to_ticks,
                    make_tick_request=self._lunar_electro_tick_request,
                ))
                self._commit_lunar_electro_transition(cloud, transition)
                generated.extend(transition.generated_events)
            elif reaction_type == "lunar_bloom":
                binding = self._bound_replacement_handler(reaction_type, event_id, capability="replace")
                transition = binding.handler(LunarBloomProposal(
                    party_state=MappingProxyType(copy.deepcopy(self.party_state)),
                    mechanics=MappingProxyType(mechanics),
                    reaction=MappingProxyType(reaction),
                    event_id=event_id, replacement_id=replaced["id"],
                    target_id=target_id, time=time, seconds_to_ticks=_seconds_to_ticks,
                ))
                self._commit_lunar_bloom_transition(transition)
                generated.extend(transition.generated_events)
            else:  # lunar_crystallize
                duration = _seconds_to_ticks(mechanics["cage_duration_seconds"], "cage_duration_seconds")
                area_id = reaction.get("area_id") or (payload or {}).get("area_id") or "battlefield.primary"
                moondrift = self._moondrift_state(area_id)
                binding = self._bound_replacement_handler(reaction_type, event_id, capability="replace")
                transition = binding.handler(LunarCrystallizeProposal(
                    moondrift=MappingProxyType(copy.deepcopy(moondrift)),
                    party_state=MappingProxyType(copy.deepcopy(self.party_state)),
                    mechanics=MappingProxyType(mechanics), contributor_ids=contributor_ids,
                    area_id=area_id, event_id=event_id, replacement_id=replaced["id"],
                    owner=owner, target_id=target_id, time=time, duration_ticks=duration,
                    seconds_to_ticks=_seconds_to_ticks, pending_error=SpecialReactionPending,
                    damage_request=self._damage_request,
                ))
                self._commit_lunar_crystallize_transition(moondrift, transition)
                generated.extend(transition.generated_events)
            profile = self._profile_audit(reaction_type)
            for row in generated[len(due):]:
                row.setdefault("profile", copy.deepcopy(profile))
            decision = self._decision(
                reaction, reaction_type,
                "explicit_switch_and_present_capability",
                record=False,
            )
            # The decision and replacement side effects become visible
            # together.  Exceptions above therefore cannot leave a false
            # successful route record in the audit stream.
            self.trace.append(copy.deepcopy(decision))
            self.trace.extend(copy.deepcopy(generated[len(due):]))
            return {"decision": decision, "reaction": replaced,
                    "generated_events": generated, "state": self.state()}
        except SpecialReactionPending as exc:
            (self.time, self.state_by_target, self.party_state,
             self.stellar_field_by_area, self.vortex_by_area,
             self.moondrift_by_area, self.lunar_cloud_by_area,
             self.scheduled) = snapshot
            del self.trace[trace_mark:]
            pending = {
                "type": "special_reaction_decision", "event_id": event_id+":special-decision",
                "t": time/TICKS, "time_tick": time, "ordinary_reaction_id": event_id,
                "ordinary_reaction_type": ordinary_type, "special_reaction_type": reaction_type,
                "decision": "pending", "reason": exc.issue["code"],
                "issue": copy.deepcopy(exc.issue), "status": "pending",
            }
            self.trace.append(pending)
            raise
        except Exception:
            (self.time, self.state_by_target, self.party_state,
             self.stellar_field_by_area, self.vortex_by_area,
             self.moondrift_by_area, self.lunar_cloud_by_area,
             self.scheduled) = snapshot
            del self.trace[trace_mark:]
            raise

    def replace_stellar_swirl_packet(
        self,
        reactions: list[dict],
        *,
        time: int,
        area_id: str = "battlefield.primary",
        parent_application_id: str | None = None,
    ) -> dict:
        """Atomically route all Star-Swirl children of one application packet.

        The packet boundary is required for AoE: when the packet starts with
        no vortex, every child deals its immediate Anemo component but the
        whole packet creates only one level-one vortex.  When a vortex already
        exists, every child advances its count independently.
        """
        if type(time) is not int or time < self.time:
            raise ValueError("星扩散packet时间必须是单调非负整数微秒。")
        if not isinstance(reactions, list) or not reactions:
            raise ValueError("星扩散packet必须包含至少一个普通反应提案。")
        if not isinstance(area_id, str) or not area_id:
            raise ValueError("area_id必须是非空空间桶ID。")
        ids: list[str] = []
        inferred_parents: list[str] = []
        for index, reaction in enumerate(reactions):
            if (not isinstance(reaction, dict) or reaction.get("type") != "swirl"
                    or reaction.get("commit_state") != "proposal"):
                raise ValueError(
                    f"reactions[{index}]必须是未提交的普通扩散提案。")
            event_id = reaction.get("id") or reaction.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError(f"reactions[{index}]缺少唯一事件ID。")
            ids.append(event_id)
            # Cross-target proposals keep their target-local package/event
            # identities.  ``atomic_parent_id`` is the explicit shared parent
            # selected by the world coordinator and therefore takes priority;
            # legacy direct callers may still use one common package/parent.
            packet_parent = (reaction.get("atomic_parent_id")
                             or reaction.get("package_id")
                             or reaction.get("parent_event_id"))
            if not isinstance(packet_parent, str) or not packet_parent:
                raise ValueError(f"reactions[{index}]缺少package_id或parent_event_id。")
            inferred_parents.append(packet_parent)
            declared_size = reaction.get("application_packet_size")
            if declared_size is not None and declared_size != len(reactions):
                raise ValueError("application_packet_size必须等于实际packet child数量。")
            if _reaction_elements(reaction) != REPLACEMENT_CONTRACTS["stellar_swirl"]["elements"]:
                raise ValueError("星扩散packet只能包含冰扩散。")
        if len(set(ids)) != len(ids):
            raise ValueError("星扩散packet内反应ID不得重复。")
        packet_parent = parent_application_id or inferred_parents[0]
        if any(value != packet_parent for value in inferred_parents):
            raise ValueError("星扩散packet的所有child必须属于同一父级附着。")

        snapshot = (
            self.time, copy.deepcopy(self.state_by_target),
            copy.deepcopy(self.party_state), copy.deepcopy(self.stellar_field_by_area),
            copy.deepcopy(self.vortex_by_area), copy.deepcopy(self.moondrift_by_area),
            copy.deepcopy(self.lunar_cloud_by_area), copy.deepcopy(self.scheduled),
        )
        trace_mark = len(self.trace)
        due = self.advance(time)
        if self._config("stellar_swirl") is None:
            decisions = [self._decision(row, None, "switch_disabled") for row in reactions]
            ordinary = [
                {**copy.deepcopy(row), "commit_state": "committed", "route": "ordinary",
                 "initial_gauge_commit_policy": "commit_elemental_proposal_once"}
                for row in reactions
            ]
            return {"decisions": decisions, "reactions": ordinary,
                    "generated_events": due, "state": self.state()}

        packet_event_id = packet_parent+":stellar-swirl-packet"
        try:
            config = self._require_ready("stellar_swirl", packet_event_id)
            qualification = config["qualification"]
            mechanics = config["mechanics"]
            profile = self._profile_audit("stellar_swirl")
            vortex = self._vortex_state(area_id)
            replaced_rows: list[dict] = []
            decisions: list[dict] = []
            generated: list[dict] = list(due)
            packet_roles: list[dict] = []
            for reaction, event_id in zip(reactions, ids):
                target_id = reaction.get("target_id")
                if not isinstance(target_id, str) or not target_id:
                    raise ValueError("星扩散child缺少target_id。")
                owner = reaction.get("owner")
                contributors = self._contributors(reaction)
                roles = self._stellar_swirl_roles(reaction, contributors)
                packet_roles.append(roles)
                replaced = {
                    **copy.deepcopy(reaction),
                    "id": event_id+":special:stellar_swirl",
                    "type": "stellar_swirl",
                    "replaces": "swirl",
                    "ordinary_reaction_id": event_id,
                    "ordinary_damage_suppressed": True,
                    "ordinary_side_effects_committed": False,
                    "commit_state": "committed",
                    "route": "special",
                    "initial_gauge_commit_policy": "commit_elemental_proposal_once",
                    "area_id": area_id,
                    "qualification": {
                        "provider_ids": copy.deepcopy(qualification["provider_ids"]),
                        "present_provider_ids": profile["proven_provider_ids"],
                        "capability_tag": qualification["capability_tag"],
                        "status": qualification["status"],
                        "source": qualification["source"],
                    },
                    "mechanics_source": mechanics["source"],
                    "role_provenance": copy.deepcopy(roles),
                    "profile": copy.deepcopy(profile),
                }
                replaced_rows.append(replaced)
                decisions.append(self._decision(
                    reaction, "stellar_swirl", "explicit_switch_packet_and_present_capability",
                    record=False,
                ))
                generated.append(self._damage_request(
                    event_id=replaced["id"]+":immediate:anemo",
                    reaction_type="stellar_swirl_anemo", target_id=target_id,
                    owner=owner, parent_reaction_id=replaced["id"], time_tick=time,
                    component="immediate_anemo", contributor_ids=contributors,
                    area_id=area_id, role_provenance=roles))

            transition = stellar_swirl_packet_binding().handler(StellarSwirlPacketProposal(
                vortex=MappingProxyType(copy.deepcopy(vortex)),
                mechanics=MappingProxyType(mechanics), profile=MappingProxyType(profile),
                reactions=tuple(MappingProxyType(copy.deepcopy(row)) for row in reactions),
                replacement_rows=tuple(MappingProxyType(copy.deepcopy(row)) for row in replaced_rows),
                packet_roles=tuple(copy.deepcopy(packet_roles)),
                area_id=area_id, event_id=packet_event_id, time=time,
                seconds_to_ticks=_seconds_to_ticks, merge_roles=self._merge_stellar_swirl_roles,
                detonate=self._stellar_swirl_detonation,
            ))
            self._commit_stellar_swirl_transition(vortex, transition)
            generated.extend(transition.generated_events)
            self.trace.extend(copy.deepcopy(decisions))
            self.trace.extend(copy.deepcopy(generated[len(due):]))
            return {"decisions": decisions, "reactions": replaced_rows,
                    "generated_events": generated, "state": self.state()}
        except SpecialReactionPending as exc:
            (self.time, self.state_by_target, self.party_state,
             self.stellar_field_by_area, self.vortex_by_area,
             self.moondrift_by_area, self.lunar_cloud_by_area,
             self.scheduled) = snapshot
            del self.trace[trace_mark:]
            self.trace.append({
                "type": "special_reaction_decision",
                "event_id": packet_event_id+":decision", "t": time/TICKS,
                "time_tick": time, "ordinary_reaction_id": packet_parent,
                "ordinary_reaction_type": "swirl", "special_reaction_type": "stellar_swirl",
                "decision": "pending", "reason": exc.issue["code"],
                "issue": copy.deepcopy(exc.issue), "status": "pending",
            })
            raise
        except Exception:
            (self.time, self.state_by_target, self.party_state,
             self.stellar_field_by_area, self.vortex_by_area,
             self.moondrift_by_area, self.lunar_cloud_by_area,
             self.scheduled) = snapshot
            del self.trace[trace_mark:]
            raise

    def record_stellar_superconduct_application(
        self, *, event_id: str, target_id: str, actor: str, element: str, time: int,
        application_allowed: bool, direct_hit: bool = True,
        atomic_parent_id: str | None = None,
        area_id: str = "battlefield.primary",
    ) -> dict:
        """Count a successful later Cryo/Electro application in the current 4s window."""
        due = self.advance(time)
        self._require_ready("stellar_superconduct", event_id)
        state = self._stellar_field_state(area_id)
        atomic_parent = atomic_parent_id or event_id
        if not isinstance(atomic_parent, str) or not atomic_parent:
            raise ValueError("atomic_parent_id必须是非空事件ID。")
        reason, counted = "field_inactive", False
        if atomic_parent in state["stellar_superconduct_origin_applications"]:
            reason = "establishing_application_excluded"
        elif not state["stellar_superconduct_active"]:
            reason = "field_inactive"
        elif not application_allowed:
            reason = "application_not_allowed"
        elif not direct_hit:
            reason = "derived_damage_excluded"
        elif element not in ("cryo", "electro"):
            reason = "element_not_eligible"
        elif atomic_parent in state["stellar_superconduct_atomic_parents"]:
            reason = "atomic_parent_already_counted"
        else:
            before = state["stellar_superconduct_layers"]
            state["stellar_superconduct_layers"] = min(12, before+1)
            state["stellar_superconduct_atomic_parents"].append(atomic_parent)
            counted = state["stellar_superconduct_layers"] > before
            reason = "layer_added" if counted else "layer_cap_reached"
        row = {
            "type": "special_reaction_state", "event_id": event_id+":stellar-layer",
            "t": time/TICKS, "time_tick": time, "target_id": target_id,
            "area_id": area_id,
            "character": actor, "element": element, "reaction_type": "stellar_superconduct",
            "operation": "count_successful_application", "counted": counted,
            "reason": reason, "atomic_parent_id": atomic_parent,
            "layers": state["stellar_superconduct_layers"],
            "profile": self._profile_audit("stellar_superconduct"),
            "status": "executed",
        }
        self.trace.append(copy.deepcopy(row))
        return {"event": row, "generated_events": [*due, row], "state": self.state()}

    def stellar_superconduct_snapshot(self, area_id: str = "battlefield.primary") -> dict:
        if not isinstance(area_id, str) or not area_id:
            raise ValueError("area_id必须是非空空间桶ID。")
        state = self.stellar_field_by_area.get(area_id)
        if state is None:
            state = self._empty_stellar_field_state()
        settled_active = (state["stellar_superconduct_buff_expires_at"] is not None
                          and state["stellar_superconduct_buff_expires_at"] > self.time)
        # Field foundation and the last settlement have different lifetimes.
        # The reviewed reaction's zero-layer bonus is the foundation; a
        # completed settlement already includes it and must not be added twice.
        foundation = self._ordinary_bonus(0) if state["stellar_superconduct_active"] else 0.0
        settled_bonus = state["stellar_superconduct_ordinary_bonus"] if settled_active else 0.0
        return {
            "area_id": area_id,
            "active": state["stellar_superconduct_active"],
            "settled_buff_active": settled_active,
            "layers": state["stellar_superconduct_settled_layers"],
            "system_factor": state["stellar_superconduct_factor"],
            "ordinary_cryo_electro_bonus": settled_bonus if settled_active else foundation,
            "field_base_cryo_electro_bonus": foundation,
            "settled_cryo_electro_bonus": settled_bonus,
            "field_expires_at": (state["stellar_superconduct_expires_at"] / TICKS
                                 if state["stellar_superconduct_expires_at"] is not None else None),
            "physical_res_delta": state["stellar_superconduct_physical_res_delta"],
            "buff_expires_at": (
                state["stellar_superconduct_buff_expires_at"] / TICKS
                if state["stellar_superconduct_buff_expires_at"] is not None else None
            ),
            "sample_policy": "last_completed_four_second_window",
        }

    def _admit_lunar_electro_tick_impl(
        self,
        tick: dict,
        aura_state: dict,
    ) -> dict:
        """Check live Hydro+Electro coexistence and return the 0.4U-consumed state."""
        if not isinstance(tick, dict) or tick.get("type") != "special_reaction_tick_request":
            raise ValueError("只能核验运行时生成的月感电跳数请求。")
        event_id = tick["event_id"]
        self._require_ready("lunar_electro", event_id)
        area_id = tick.get("area_id")
        if not isinstance(area_id, str) or not area_id:
            raise ValueError("月感电跳数请求缺少area_id。")
        cloud = self._lunar_cloud_state(area_id)
        if (not cloud["lunar_electro_active"]
                or tick["target_id"] not in cloud["lunar_electro_target_ids"]):
            raise SpecialReactionPending(
                "lunar_electro_cloud_scope_unbound",
                "月感电跳数没有绑定到当前区域雷暴云及其目标实体。",
                event_id, {"area_id": area_id, "target_id": tick["target_id"]})
        if not isinstance(aura_state, dict) or aura_state.get("knowledge") != "known":
            raise SpecialReactionPending(
                "lunar_electro_aura_unknown", "月感电跳数需要已知的实时水雷共存状态。", event_id,
                {"target_id": tick["target_id"]})
        plan = plan_lunar_tick_admission(LunarTickAdmissionProposal(
            cloud=MappingProxyType(copy.deepcopy(cloud)), tick=MappingProxyType(tick),
            aura_state=MappingProxyType(aura_state), party_member_ids=self.party_member_ids,
            area_id=area_id, event_id=event_id, seconds_to_ticks=_seconds_to_ticks,
            finite_number=_finite_number, pending_error=SpecialReactionPending,
        ))
        if not isinstance(plan, LunarTickAdmissionPlan):
            raise TypeError("月感电接纳handler必须返回强类型接纳计划。")
        if replacement_state_fingerprint(cloud) != plan.expected_cloud_fingerprint:
            raise ValueError("月感电接纳云状态在计划与提交之间发生变化。")
        if plan.attack_at is not None:
            cloud["lunar_electro_last_attack_by_target"][tick["target_id"]] = plan.attack_at
        row = plan.admission
        row["profile"] = self._profile_audit("lunar_electro")
        if plan.attack_at is None:
            self.trace.append(copy.deepcopy(row))
            return {"admission": row, "aura_before": plan.aura_before,
                    "aura_after": plan.aura_after, "damage_event": None}
        contributor_ids = row["contributor_ids"]
        damage = self._damage_request(
            event_id=event_id+":damage", reaction_type="lunar_electro",
            target_id=tick["target_id"], owner=tick.get("owner"),
            parent_reaction_id=tick["parent_reaction_id"], time_tick=tick["time_tick"],
            component="thundercloud", contributor_ids=contributor_ids,
            sample_policy=(
                "tick_time_live_positive_owner_residual_and_recent_five_seconds"),
            area_id=area_id)
        self.trace.extend((copy.deepcopy(row), copy.deepcopy(damage)))
        return {"admission": row, "aura_before": plan.aura_before, "aura_after": plan.aura_after,
                "damage_event": damage}

    def admit_lunar_electro_tick(self, tick: dict, aura_state: dict) -> dict:
        """Admit one tick for the ordinary single-target execution path."""
        return self._admit_lunar_electro_tick_impl(tick, aura_state)

    def admit_lunar_electro_tick_for_world(
        self,
        tick: dict,
        aura_state: dict,
    ) -> dict:
        """Admit and audit one world tick against the canonical state machine.

        A world consumer may be wrapped or instrumented.  The returned receipt
        and every mutation must still equal an independent run of the private
        canonical implementation.  This closes success paths where a wrapper
        forges consumed gauge, aura proposals, damage fields, trace rows, or an
        unrelated cloud field.
        """
        oracle = copy.deepcopy(self)
        expected = oracle._admit_lunar_electro_tick_impl(
            copy.deepcopy(tick), copy.deepcopy(aura_state))
        actual = self.admit_lunar_electro_tick(tick, aura_state)
        if actual != expected or self.__dict__ != oracle.__dict__:
            event_id = (tick.get("event_id") if isinstance(tick, dict)
                        else "lunar-electro:world-admission")
            raise SpecialReactionPending(
                "lunar_electro_world_admission_mismatch",
                "世界月感电接纳回执或共享状态改写与规范状态机不一致。",
                event_id,
                {"target_id": tick.get("target_id")
                 if isinstance(tick, dict) else None},
            )
        return copy.deepcopy(actual)

    def transaction_snapshot(self) -> dict:
        """Capture live state and the append position of the reaction ledger."""
        return {
            "time": self.time,
            "trace_length": len(self.trace),
            "scheduled": copy.deepcopy(self.scheduled),
            "state_by_target": copy.deepcopy(self.state_by_target),
            "party_state": copy.deepcopy(self.party_state),
            "stellar_field_by_area": copy.deepcopy(self.stellar_field_by_area),
            "vortex_by_area": copy.deepcopy(self.vortex_by_area),
            "moondrift_by_area": copy.deepcopy(self.moondrift_by_area),
            "lunar_cloud_by_area": copy.deepcopy(self.lunar_cloud_by_area),
            "pending_world_lunar_electro_expiry": copy.deepcopy(
                self._pending_world_lunar_electro_expiry),
            "world_defer_lunar_electro_expiry": self._world_defer_lunar_electro_expiry,
        }

    def restore_transaction(self, snapshot: dict) -> None:
        self.time = snapshot["time"]
        del self.trace[snapshot["trace_length"]:]
        self.scheduled = snapshot["scheduled"]
        self.state_by_target = snapshot["state_by_target"]
        self.party_state = snapshot["party_state"]
        self.stellar_field_by_area = snapshot["stellar_field_by_area"]
        self.vortex_by_area = snapshot["vortex_by_area"]
        self.moondrift_by_area = snapshot["moondrift_by_area"]
        self.lunar_cloud_by_area = snapshot["lunar_cloud_by_area"]
        self._pending_world_lunar_electro_expiry = snapshot[
            "pending_world_lunar_electro_expiry"]
        self._world_defer_lunar_electro_expiry = snapshot[
            "world_defer_lunar_electro_expiry"]

    def _execute_direct_damage_atomically(self, operation):
        """Rollback clocks, deadlines and traces if any direct request fails."""
        snapshot = self.transaction_snapshot()
        try:
            return operation()
        except Exception:
            self.restore_transaction(snapshot)
            raise

    def _validate_direct_delivery(
        self,
        *,
        event_id: str,
        target_id: str,
        area_id: str,
        actor: str,
        action_id: str,
        time: int,
        ability_profile: dict,
        ability_id: str | None,
        ability_variant: str | None,
        ability_effective_level: int | None,
        reaction_type: str,
    ) -> str:
        for name, value in (
                ("event_id", event_id), ("target_id", target_id),
                ("area_id", area_id), ("actor", actor), ("action_id", action_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}必须是非空字符串。")
        if type(time) is not int or time < self.time:
            raise ValueError("直伤星月事件时间必须是单调非负整数微秒。")
        if self.party_member_ids is None:
            raise SpecialReactionPending(
                "special_party_unbound", "直伤星月请求前必须绑定实际队伍。", event_id)
        if actor not in self.party_member_ids:
            raise ValueError("直伤星月伤害角色必须属于实际队伍。")
        binding = ability_profile.get("ability_binding") if isinstance(
            ability_profile, dict) else None
        if not isinstance(binding, dict):
            raise SpecialReactionPending(
                "direct_special_ability_binding_required",
                "直伤星月执行必须使用绑定具体角色与技能的v2 ability_binding。",
                event_id,
            )
        if (binding.get("character_id") != actor
                or ability_id is None
                or binding.get("ability_id") != ability_id
                or not isinstance(ability_variant, str)
                or binding.get("variant_id") != ability_variant
                or type(ability_effective_level) is not int
                or binding.get("effective_level")
                != ability_effective_level):
            raise SpecialReactionPending(
                "direct_special_ability_binding_mismatch",
                "直伤星月profile没有绑定当前动作的角色与技能。",
                event_id,
                {"actor": actor, "ability_id": ability_id,
                 "ability_variant": ability_variant,
                 "ability_effective_level": ability_effective_level,
                 "ability_binding": copy.deepcopy(binding)},
            )
        required_tag = REQUIRED_CAPABILITY_TAGS[reaction_type]
        if required_tag not in self.party_capability_tags.get(actor, ()):
            raise SpecialReactionPending(
                "direct_special_owner_capability_unproven",
                "直伤星月角色本人缺少对应的数据库能力标签。",
                event_id,
                {"actor": actor, "required_capability_tag": required_tag},
            )
        component = ((ability_profile.get("parameters") or {}).get("component")
                     if isinstance(ability_profile, dict) else None)
        delivery_identity = _profile_hash({
            "action_id": action_id,
            "target_id": target_id,
            "area_id": area_id,
            "time_tick": time,
            "component": component,
        })
        if any(row.get("event_id") == event_id for row in self.trace + self.scheduled):
            raise SpecialReactionPending(
                "direct_special_event_duplicate",
                "直伤星月事件ID已经被运行时使用。", event_id)
        if any(row.get("type") == "special_reaction_damage_request"
               and row.get("delivery_identity") == delivery_identity
               for row in self.trace):
            raise SpecialReactionPending(
                "direct_special_delivery_duplicate",
                "同一动作组件、目标、区域及时点不能换ID重复交付。",
                event_id,
                {"delivery_identity": delivery_identity},
            )
        return delivery_identity

    @staticmethod
    def _reject_direct_deadline_id_collision(event_id: str, due: list[dict]) -> None:
        """Reject IDs materialized by advancing to the direct-hit boundary."""
        if any(row.get("event_id") == event_id for row in due):
            raise SpecialReactionPending(
                "direct_special_event_duplicate",
                "直伤星月事件ID与同一时点生成的运行时截止事件冲突。",
                event_id,
            )

    def request_direct_lunar_damage(
        self,
        *,
        event_id: str,
        target_id: str,
        area_id: str,
        actor: str,
        action_id: str,
        time: int,
        ability_profile: dict,
        ability_id: str | None = None,
        ability_variant: str | None = None,
        ability_effective_level: int | None = None,
        dew_consumption: dict | None = None,
        bloom_core_absorption: dict | None = None,
    ) -> dict:
        """Emit an explicit ability-owned Direct Lunar damage request.

        Reaction creation never authorizes an ability-owned Direct Lunar hit.
        A caller must provide a finite, content-hashed database projection or
        a clearly labelled assumption that owns reaction type, element,
        multiplier, scaling stat, simultaneous hit count and component name.
        """
        issues = validate_direct_lunar_damage_profile(ability_profile)
        if issues:
            raise SpecialReactionPending(
                "direct_lunar_ability_profile_invalid",
                "直伤月曜缺少可审计的能力数值profile。",
                event_id,
                {"issues": copy.deepcopy(issues)},
            )
        parameters = ability_profile["parameters"]
        reaction_type = parameters["reaction_type"]
        self._require_ready(reaction_type, event_id)
        reaction_profile = self._profile_audit(reaction_type)
        delivery_identity = self._validate_direct_delivery(
            event_id=event_id, target_id=target_id, area_id=area_id,
            actor=actor, action_id=action_id, time=time,
            ability_profile=ability_profile, ability_id=ability_id,
            ability_variant=ability_variant,
            ability_effective_level=ability_effective_level,
            reaction_type=reaction_type,
        )

        def commit():
            due = self.advance(time)
            self._reject_direct_deadline_id_collision(event_id, due)
            request = {
                "type": "special_reaction_damage_request",
                "event_id": event_id,
                "t": time/TICKS,
                "time_tick": time,
                "target_id": target_id,
                "area_id": area_id,
                "target_selection_policy": "declared_target",
                "owner": actor,
                "action_id": action_id,
                "parent_reaction_id": None,
                "reaction_type": reaction_type,
                "damage_system": "lunar",
                "reaction_base": False,
                "system_factor": LUNAR_FACTOR[reaction_type],
                "factor_policy": "engine_constant",
                "element": parameters["element"],
                "multiplier": parameters["multiplier"],
                "scaling_stat": parameters["scaling_stat"],
                "count": parameters["count"],
                "component": parameters["component"],
                "delivery_identity": delivery_identity,
                "contributor_ids": [],
                "contributor_sample_policy": None,
                "actor_sample_policy": "event_time_owner_attribute",
                "application": None,
                "profile": reaction_profile,
                "ability_profile": copy.deepcopy(ability_profile),
                "status": "ready_for_stat_sample",
            }
            if dew_consumption is not None:
                if (reaction_type != "lunar_bloom" or type(dew_consumption.get("actual_dew")) is not int
                        or not 1 <= dew_consumption["actual_dew"] <= 3
                        or dew_consumption.get("mode") not in {"require", "scale_multiplier"}):
                    raise ValueError("月绽放直伤的草露消费回执无效。")
                request["dew_consumption"] = copy.deepcopy(dew_consumption)
            if bloom_core_absorption is not None:
                count = bloom_core_absorption.get("actual_cores")
                bonus = bloom_core_absorption.get("multiplier_bonus_per_core")
                if (reaction_type != "lunar_bloom" or type(count) is not int or not 0 <= count <= 256
                        or type(bonus) not in (int, float) or not 0 <= bonus <= 1000):
                    raise ValueError("月绽放直伤的草原核吸收回执无效。")
                request["bloom_core_absorption"] = copy.deepcopy(bloom_core_absorption)
            self.trace.append(copy.deepcopy(request))
            return {"damage_event": request, "generated_events": [*due, request],
                    "state": self.state()}

        return self._execute_direct_damage_atomically(commit)

    def request_direct_stellar_damage(
        self,
        *,
        event_id: str,
        target_id: str,
        area_id: str,
        actor: str,
        action_id: str,
        ability_id: str,
        ability_variant: str,
        ability_effective_level: int,
        time: int,
        ability_profile: dict,
    ) -> dict:
        """Emit one ability-owned Star-Superconduct hit using runtime layers."""
        issues = validate_direct_stellar_damage_profile(ability_profile)
        if issues:
            raise SpecialReactionPending(
                "direct_stellar_ability_profile_invalid",
                "直伤星超导缺少可审计且绑定能力的数值profile。",
                event_id,
                {"issues": copy.deepcopy(issues)},
            )
        parameters = ability_profile["parameters"]
        reaction_type = parameters["reaction_type"]
        self._require_ready(reaction_type, event_id)
        if area_id not in self.stellar_field_by_area:
            raise SpecialReactionPending(
                "direct_stellar_area_unbound",
                "直伤星超导必须引用已经由反应运行时建立的领域区域。",
                event_id,
                {"area_id": area_id,
                 "known_area_ids": sorted(self.stellar_field_by_area)},
            )
        reaction_profile = self._profile_audit(reaction_type)
        delivery_identity = self._validate_direct_delivery(
            event_id=event_id, target_id=target_id, area_id=area_id,
            actor=actor, action_id=action_id, time=time,
            ability_profile=ability_profile, ability_id=ability_id,
            ability_variant=ability_variant,
            ability_effective_level=ability_effective_level,
            reaction_type=reaction_type,
        )

        def commit():
            due = self.advance(time)
            self._reject_direct_deadline_id_collision(event_id, due)
            # Area membership was checked before advance.  Expiry clears the
            # state in place, so this read cannot create a typo bucket.
            snapshot = self.stellar_superconduct_snapshot(area_id)
            request = {
                "type": "special_reaction_damage_request",
                "event_id": event_id,
                "t": time/TICKS,
                "time_tick": time,
                "target_id": target_id,
                "area_id": area_id,
                "target_selection_policy": "declared_target",
                "owner": actor,
                "action_id": action_id,
                "parent_reaction_id": None,
                "reaction_type": "stellar_superconduct",
                "damage_system": "stellar",
                "reaction_base": False,
                "layers": snapshot["layers"],
                "system_factor": snapshot["system_factor"],
                "factor_policy": "event_time_last_completed_four_second_window",
                "settled_snapshot": copy.deepcopy(snapshot),
                "element": parameters["element"],
                "multiplier": parameters["multiplier"],
                "scaling_stat": parameters["scaling_stat"],
                "count": parameters["count"],
                "component": parameters["component"],
                "delivery_identity": delivery_identity,
                "contributor_ids": [],
                "contributor_sample_policy": None,
                "actor_sample_policy": "event_time_owner_attribute",
                "application": None,
                "profile": reaction_profile,
                "ability_profile": copy.deepcopy(ability_profile),
                "status": "ready_for_stat_sample",
            }
            self.trace.append(copy.deepcopy(request))
            return {"damage_event": request, "generated_events": [*due, request],
                    "state": self.state()}

        return self._execute_direct_damage_atomically(commit)

    def request_direct_lunar_bloom_damage(
        self,
        *,
        event_id: str,
        target_id: str,
        actor: str,
        action_id: str,
        time: int,
        ability_profile: dict,
        area_id: str = "battlefield.primary",
        ability_id: str | None = None,
        ability_variant: str | None = None,
        ability_effective_level: int | None = None,
    ) -> dict:
        """Compatibility wrapper for the former Lunar-Bloom-only API."""
        parameters = ability_profile.get("parameters") if isinstance(
            ability_profile, dict) else None
        if not isinstance(parameters, dict) or parameters.get("reaction_type") != "lunar_bloom":
            raise SpecialReactionPending(
                "direct_lunar_ability_profile_invalid",
                "月绽放兼容接口只接收reaction_type='lunar_bloom'的能力profile。",
                event_id,
            )
        return self.request_direct_lunar_damage(
            event_id=event_id, target_id=target_id, area_id=area_id,
            actor=actor, action_id=action_id, time=time,
            ability_profile=ability_profile, ability_id=ability_id,
            ability_variant=ability_variant,
            ability_effective_level=ability_effective_level,
        )

    def grant_lunar_bloom_reserve(self, *, event_id: str, target_id: str, actor: str,
                                time: int, pool_id: str, amount: int, capacity: int,
                                duration_seconds: float, source: str) -> dict:
        """Grant a caller-evidenced independent Dew-equivalent resource pool."""
        self._require_ready("lunar_bloom", event_id)
        if (type(pool_id) is not str or not 1 <= len(pool_id) <= 128 or pool_id == "ordinary"
                or type(amount) is not int or not 1 <= amount <= 100
                or type(capacity) is not int or not 1 <= capacity <= 100):
            raise ValueError("替代草露池标识、数量或容量无效。")
        duration = _seconds_to_ticks(duration_seconds, "duration_seconds")
        if duration <= 0 or time + duration > 10**15:
            raise ValueError("替代草露须有正有效期且到期时间不超过 10 亿秒。")
        due = self.advance(time)
        pools = self.party_state.setdefault("lunar_bloom_reserve_pools", {})
        previous = pools.get(pool_id)
        if previous and (previous["owner"] != actor or previous["capacity"] != capacity):
            raise SpecialReactionPending("lunar_bloom_reserve_identity_changed",
                "同一有效替代草露池的提供者或容量不能改变。", event_id)
        if previous is None and len(pools) >= 16:
            raise SpecialReactionPending("lunar_bloom_reserve_limit", "有效替代草露池超过 16 个。", event_id)
        before = previous["balance"] if previous else 0
        after = min(capacity, before + amount)
        pools[pool_id] = {"owner": actor, "capacity": capacity, "balance": after,
                          "expires_at": time + duration}
        row = {"type": "special_reaction_resource", "event_id": event_id,
               "t": time/TICKS, "time_tick": time, "target_id": target_id,
               "reaction_type": "lunar_bloom", "operation": "grant_dew_reserve", "pool_id": pool_id,
               "owner": actor, "requested": amount, "amount": after-before, "capacity": capacity,
               "before": before, "after": after, "expires_at": (time+duration)/TICKS,
               "resource_scope": "party", "source": source, "status": "executed",
               "profile": self._profile_audit("lunar_bloom")}
        self.trace.append(copy.deepcopy(row))
        return {"resource_event": row, "generated_events": due, "state": self.state()}

    def consume_lunar_bloom(self, *, event_id: str, target_id: str, actor: str,
                            time: int, dew: int = 1, reserve_pool_ids: tuple[str, ...] = (),
                            spend_policy: str = "all_or_nothing") -> dict:
        """Consume accumulated Dew; reaction creation itself never emits Lunar damage."""
        due = self.advance(time)
        config = self._require_ready("lunar_bloom", event_id)
        if type(dew) is not int or not 1 <= dew <= 3:
            raise ValueError("草露消耗量须为 1 至 3 的整数。")
        if spend_policy not in {"all_or_nothing", "up_to_available"}:
            raise ValueError("草露消耗策略不受支持。")
        if (len(reserve_pool_ids) > 16 or any(type(key) is not str or key == "ordinary" for key in reserve_pool_ids)
                or len(set(reserve_pool_ids)) != len(reserve_pool_ids)):
            raise ValueError("替代草露池须是唯一标识，普通草露固定最先扣除。")
        ordinary = self.party_state["lunar_bloom_dew"]
        reserves = self.party_state.get("lunar_bloom_reserve_pools", {})
        balances = [("ordinary", ordinary), *((key, reserves.get(key, {}).get("balance", 0)) for key in reserve_pool_ids)]
        before = sum(balance for _, balance in balances)
        spent = min(before, dew) if spend_policy == "up_to_available" else dew
        if spent == 0 or before < spent:
            row = {
                "type": "special_reaction_resource", "event_id": event_id,
                "t": time/TICKS, "time_tick": time, "target_id": target_id,
                "reaction_type": "lunar_bloom", "operation": "consume_dew",
                "requested": dew, "available": before, "status": "rejected",
                "reason": "insufficient_dew", "resource_scope": "party",
                "profile": self._profile_audit("lunar_bloom"),
            }
            if reserve_pool_ids:
                row["pool_balances"] = [{"pool_id": key, "available": balance} for key, balance in balances]
            self.trace.append(copy.deepcopy(row))
            return {"resource_event": row, "generated_events": due, "state": self.state()}
        unresolved = self.party_state["lunar_bloom_full_cap_unresolved"]
        ordinary_spent = min(ordinary, spent)
        if ordinary_spent and unresolved and config["mechanics"]["full_cap_queue_policy"] == "pending":
            raise SpecialReactionPending(
                "lunar_bloom_full_cap_queue_pending",
                "草露满3后触发的月绽放是否保留填充窗口尚未核实；消耗草露会使该差异可观察。",
                event_id,
                {
                    "unresolved_atomic_parent_ids": copy.deepcopy(unresolved),
                    "allowed_assumed_policies": [
                        "discard_new", "retain_active_fill_window"],
                },
            )
        remaining = spent
        changes = []
        for pool_id, balance in balances:
            used = min(balance, remaining)
            remaining -= used
            if not used:
                continue
            after = balance - used
            if pool_id == "ordinary":
                self.party_state["lunar_bloom_dew"] = after
            else:
                reserves[pool_id]["balance"] = after
            changes.append({"pool_id": pool_id, "before": balance, "amount": used, "after": after})
        if (ordinary_spent and self.party_state["lunar_bloom_backlog"] > 0
                and self.party_state["lunar_bloom_next_dew_at"] is None):
            interval = _seconds_to_ticks(
                config["mechanics"]["dew_interval_seconds"], "dew_interval_seconds")
            self.party_state["lunar_bloom_next_dew_at"] = time + interval
        row = {
            "type": "special_reaction_resource", "event_id": event_id,
            "t": time/TICKS, "time_tick": time, "target_id": target_id,
            "reaction_type": "lunar_bloom", "operation": "consume_dew",
            "resource_scope": "party", "amount": spent, "before": before,
            "after": before - spent,
            "qualification": copy.deepcopy(config["qualification"]), "status": "executed",
            "profile": self._profile_audit("lunar_bloom"),
        }
        if reserve_pool_ids:
            row["pool_debits"] = changes
            row["actor"] = actor
        if spend_policy != "all_or_nothing":
            row.update(requested=dew, spend_policy=spend_policy)
        # Dew is a party resource.  Consuming it is not, by itself, a damage
        # formula: Direct Lunar-Bloom damage belongs to the reviewed character
        # ability that consumed the resource.  Keeping these operations split
        # prevents a generic ``consume_dew`` input from inventing one
        # reaction-base hit for every Dew spent.
        row["lunar_damage_emitted"] = False
        self.trace.append(copy.deepcopy(row))
        return {"resource_event": row, "generated_events": due, "state": self.state()}

    def _commit_stellar_field_deadline_step(self, field, step):
        if not isinstance(step, StellarFieldDeadlineStep):
            raise TypeError("领域截止handler必须返回强类型状态步骤。")
        if replacement_state_fingerprint(field) != step.expected_prestate_fingerprint:
            raise ValueError("领域截止状态在计划与提交之间发生变化。")
        if step.operation == "settled_layers":
            field["stellar_superconduct_settled_layers"] = step.layers
        elif step.operation == "factor":
            field["stellar_superconduct_factor"] = step.factor
        elif step.operation == "settle":
            field["stellar_superconduct_ordinary_bonus"] = step.bonus
            field["stellar_superconduct_buff_expires_at"] = step.buff_expires_at
            field["stellar_superconduct_layers"] = 0
            field["stellar_superconduct_atomic_parents"] = []
        elif step.operation == "schedule":
            field["stellar_superconduct_next_settlement"] = step.next_settlement
        elif step.operation == "expire":
            field["stellar_superconduct_active"] = False
            field["stellar_superconduct_layers"] = 0
            field["stellar_superconduct_next_settlement"] = None
            field["stellar_superconduct_expires_at"] = None
            field["stellar_superconduct_origin_applications"] = []
            field["stellar_superconduct_atomic_parents"] = []
            field["stellar_superconduct_physical_res_delta"] = 0.0
        elif step.operation == "buff_expire":
            field["stellar_superconduct_settled_layers"] = 0
            field["stellar_superconduct_factor"] = 1.0
            field["stellar_superconduct_ordinary_bonus"] = 0.0
            field["stellar_superconduct_buff_expires_at"] = None
        else:
            raise ValueError("领域截止步骤不在闭合操作集合内。")

    def _commit_lunar_bloom_deadline_step(self, step):
        if not isinstance(step, LunarBloomDeadlineStep):
            raise TypeError("月露截止handler必须返回强类型资源步骤。")
        party = self.party_state
        if replacement_state_fingerprint(party) != step.expected_prestate_fingerprint:
            raise ValueError("月露截止资源在计划与提交之间发生变化。")
        if step.operation == "fill":
            party["lunar_bloom_dew"] = step.dew
            party["lunar_bloom_backlog"] = step.backlog
            if party["lunar_bloom_backlog_sources"]:
                party["lunar_bloom_backlog_sources"].pop(0)
        elif step.operation == "full_cap":
            if step.full_cap_policy == "pending":
                party["lunar_bloom_full_cap_unresolved"] = step.unresolved
            party["lunar_bloom_backlog"] = step.backlog
            party["lunar_bloom_backlog_sources"] = step.sources
        elif step.operation == "next":
            party["lunar_bloom_next_dew_at"] = step.next_dew_at
        else:
            raise ValueError("月露截止步骤不在闭合操作集合内。")

    def _advance_deadlines(
        self,
        time: int,
        *,
        defer_world_lunar_electro_expiry: bool,
    ) -> list[dict]:
        """Advance once, optionally reserving Lunar-Charged expiry for world commit."""
        if type(time) is not int or time < self.time:
            raise ValueError("星月反应时间必须是单调非负整数微秒。")
        if self._pending_world_lunar_electro_expiry is not None:
            raise RuntimeError("上一个世界月感电截止批次尚未完成。")
        due: list[dict] = []
        deferred_lunar_electro_expiries: list[dict] = []
        for area_id in sorted(self.stellar_field_by_area):
            field = self.stellar_field_by_area[area_id]
            config = self._config("stellar_superconduct")
            if (self.model["schema_version"] == 2
                    and (field["stellar_superconduct_active"]
                         or field["stellar_superconduct_buff_expires_at"] is not None)):
                self._bound_replacement_handler("stellar_superconduct", f"{area_id}:field:{time}",
                                                 capability="deadline_production")
            for step in plan_stellar_field_deadline_steps(
                    MappingProxyType(copy.deepcopy(field)), config, area_id=area_id, time=time,
                    seconds_to_ticks=_seconds_to_ticks,
                    factor_for_layers=stellar_superconduct_factor,
                    ordinary_bonus=lambda layers: self._ordinary_bonus(layers)):
                self._commit_stellar_field_deadline_step(field, step)
                if step.event is not None:
                    step.event["profile"] = self._profile_audit("stellar_superconduct")
                    due.append(step.event)
        for area_id in sorted(self.vortex_by_area):
            vortex = self.vortex_by_area[area_id]
            detonate = vortex["stellar_swirl_detonate_at"]
            if detonate is not None and detonate <= time:
                due.extend(self._stellar_swirl_detonation(
                    area_id, vortex, detonate, "three_second_timeout"))
        for area_id in sorted(self.lunar_cloud_by_area):
            cloud = self.lunar_cloud_by_area[area_id]
            expires = cloud["lunar_electro_expires_at"]
            if expires is not None and expires <= time:
                if self.model["schema_version"] == 2:
                    self._bound_replacement_handler("lunar_electro", f"{area_id}:cloud:{time}",
                                                     capability="deadline_production")
                old_targets = copy.deepcopy(cloud["lunar_electro_target_ids"])
                expiry_event = {
                    "type": "special_reaction_state",
                    "event_id": f"{area_id}:lunar-electro:expire:{expires}",
                    "t": expires/TICKS, "time_tick": expires,
                    "target_id": None, "area_id": area_id,
                    "reaction_type": "lunar_electro",
                    "operation": "expire_area_thundercloud",
                    "previous_target_ids": old_targets,
                    "profile": self._profile_audit("lunar_electro"),
                    "status": "executed",
                }
                if defer_world_lunar_electro_expiry:
                    deferred_lunar_electro_expiries.append({
                        "area_id": area_id,
                        "expires_at": expires,
                        "target_ids": old_targets,
                        "phase_by_target": copy.deepcopy(
                            cloud["lunar_electro_phase_by_target"]),
                        "event": expiry_event,
                    })
                else:
                    cloud.update(
                        lunar_electro_active=False,
                        lunar_electro_expires_at=None,
                        lunar_electro_target_ids=[],
                        lunar_electro_last_attack_by_target={},
                        lunar_electro_phase_by_target={},
                    )
                    due.append(expiry_event)
        for area_id in sorted(self.moondrift_by_area):
            moondrift = self.moondrift_by_area[area_id]
            expires = moondrift["lunar_crystallize_expires_at"]
            if expires is not None and expires <= time:
                if self.model["schema_version"] == 2:
                    self._bound_replacement_handler("lunar_crystallize", f"{area_id}:moondrift:{time}",
                                                     capability="deadline_production")
                moondrift["lunar_crystallize_cages"] = 0
                moondrift["lunar_crystallize_expires_at"] = None
                due.append({
                    "type": "special_reaction_state",
                    "event_id": f"{area_id}:lunar-crystallize:expire:{expires}",
                    "t": expires/TICKS, "time_tick": expires, "target_id": None,
                    "area_id": area_id, "reaction_type": "lunar_crystallize",
                    "operation": "expire_moondrift_set",
                    "profile": self._profile_audit("lunar_crystallize"),
                    "status": "executed",
                })
        config = self._config("lunar_bloom")
        if (self.model["schema_version"] == 2 and config
                and self.party_state["lunar_bloom_backlog"] > 0):
            self._bound_replacement_handler("lunar_bloom", f"party:dew:{time}",
                                             capability="deadline_production")
        for step in plan_lunar_bloom_deadline_steps(
                MappingProxyType(copy.deepcopy(self.party_state)), config, time=time,
                seconds_to_ticks=_seconds_to_ticks):
            self._commit_lunar_bloom_deadline_step(step)
            if step.event is not None:
                step.event["profile"] = self._profile_audit("lunar_bloom")
                due.append(step.event)
        pools = self.party_state.get("lunar_bloom_reserve_pools", {})
        for pool_id, pool in list(pools.items()):
            expiry = pool["expires_at"]
            if expiry <= time:
                del pools[pool_id]
                due.append({"type": "special_reaction_resource", "event_id": f"dew-reserve:{pool_id}:expire:{expiry}",
                            "t": expiry/TICKS, "time_tick": expiry, "target_id": None,
                            "reaction_type": "lunar_bloom", "operation": "expire_dew_reserve",
                            "pool_id": pool_id, "owner": pool["owner"], "before": pool["balance"],
                            "after": 0, "resource_scope": "party", "status": "executed",
                            "profile": self._profile_audit("lunar_bloom")})
        scheduled = [row for row in self.scheduled if row["time_tick"] <= time]
        self.scheduled = [row for row in self.scheduled if row["time_tick"] > time]
        due.extend(scheduled)
        due.sort(key=lambda row: (row["time_tick"], row["event_id"]))
        self.time = time
        if defer_world_lunar_electro_expiry:
            self._pending_world_lunar_electro_expiry = {
                "time_tick": time,
                "entries": deferred_lunar_electro_expiries,
            }
        cleaned = copy.deepcopy(due)
        for row in cleaned:
            row.pop("schedule_key", None)
        self.trace.extend(copy.deepcopy(cleaned))
        return cleaned

    def advance(self, time: int) -> list[dict]:
        """Advance to a queue timestamp and emit all deadlines at or before it.

        This is the ordinary single-stage API.  World orchestration uses the
        paired methods below so a Lunar-Charged tick exactly at cloud expiry
        can be admitted before the cloud is cleared.
        """
        return self._advance_deadlines(
            time,
            defer_world_lunar_electro_expiry=(
                self._world_defer_lunar_electro_expiry),
        )

    def advance_world_pre_expiry(self, time: int) -> list[dict]:
        """Advance once while reserving due Lunar-Charged expiry for commit.

        Only :class:`ElementalWorldRuntime` should use this half of the paired
        protocol.  It may preview and commit the returned tick requests while
        the cloud remains live, then it must call
        :meth:`finalize_world_lunar_electro_expiry` at the same boundary.
        """
        if self._world_defer_lunar_electro_expiry:
            raise RuntimeError("世界月感电预推进不得重入。")
        self._world_defer_lunar_electro_expiry = True
        try:
            # Keep ``advance`` as the single clock-advance interception point.
            # Existing instrumentation and adversarial wrappers therefore
            # still observe exactly one advance call per world boundary.
            return self.advance(time)
        finally:
            self._world_defer_lunar_electro_expiry = False

    def finalize_world_lunar_electro_expiry(self, time: int) -> list[dict]:
        """Clear the cloud expiries reserved by the matching world advance.

        Tick admission is allowed to update only the per-target last-attack
        ledger between the two stages.  Scope, endpoint and phase identity are
        revalidated here, so the world never has to mutate special-runtime
        internals to impose endpoint order.
        """
        pending = self._pending_world_lunar_electro_expiry
        if pending is None or pending.get("time_tick") != time:
            raise RuntimeError("世界月感电到期提交缺少同边界预推进。")
        due: list[dict] = []
        for entry in pending["entries"]:
            area_id = entry["area_id"]
            event = entry["event"]
            cloud = self.lunar_cloud_by_area.get(area_id)
            if (not isinstance(cloud, dict)
                    or set(cloud) != {
                        "lunar_electro_active",
                        "lunar_electro_expires_at",
                        "lunar_electro_target_ids",
                        "lunar_electro_last_attack_by_target",
                        "lunar_electro_phase_by_target",
                    }
                    or cloud.get("lunar_electro_active") is not True
                    or cloud.get("lunar_electro_expires_at")
                    != entry["expires_at"]
                    or cloud.get("lunar_electro_target_ids")
                    != entry["target_ids"]
                    or cloud.get("lunar_electro_phase_by_target")
                    != entry["phase_by_target"]):
                raise SpecialReactionPending(
                    "lunar_electro_expiry_prestate_conflict",
                    "月感电跳数提交后的雷暴云边界、目标或相位与预推进冻结状态不一致。",
                    event["event_id"],
                    {"area_id": area_id, "time_tick": time},
                )
            cloud.update(
                lunar_electro_active=False,
                lunar_electro_expires_at=None,
                lunar_electro_target_ids=[],
                lunar_electro_last_attack_by_target={},
                lunar_electro_phase_by_target={},
            )
            due.append(copy.deepcopy(event))
        self._pending_world_lunar_electro_expiry = None
        due.sort(key=lambda row: (row["time_tick"], row["event_id"]))
        self.trace.extend(copy.deepcopy(due))
        return due


# Stable entry points for world two-phase consumers.  Keeping these aliases in
# the producer module means lazy imports cannot accidentally snapshot a test or
# instrumentation wrapper installed on the class later.
#
# Merely preserving class descriptors is not enough: every Python function
# keeps a live ``__globals__`` mapping, so patching one module helper (for
# example ``_factor_for``) would otherwise corrupt both the live producer and
# its reference oracle in exactly the same way.  Build one private namespace,
# clone every module-local Python function into it, then clone all runtime
# descriptors against that same namespace.  Calls made anywhere in the
# canonical method graph therefore resolve the import-time helper graph rather
# than the mutable public module dictionary.  Container-valued constants need
# their own recursive snapshot too: a shallow globals copy would still share
# ``STELLAR_FACTOR`` and the reviewed profile/config dictionaries with live
# code.  Only builtin containers are copied.  Modules, classes, functions, and
# other process resources retain identity instead of being passed to an unsafe
# or unsupported blanket ``deepcopy``.
def _reference_special_container_snapshot(value, memo: dict[int, object]):
    existing = memo.get(id(value))
    if existing is not None:
        return existing
    if type(value) is dict:
        snapshot = {}
        memo[id(value)] = snapshot
        snapshot.update(
            (_reference_special_container_snapshot(key, memo),
             _reference_special_container_snapshot(item, memo))
            for key, item in value.items()
        )
        return snapshot
    if type(value) is list:
        snapshot = []
        memo[id(value)] = snapshot
        snapshot.extend(
            _reference_special_container_snapshot(item, memo)
            for item in value
        )
        return snapshot
    if type(value) is set:
        snapshot = set()
        memo[id(value)] = snapshot
        snapshot.update(
            _reference_special_container_snapshot(item, memo)
            for item in value
        )
        return snapshot
    if type(value) is tuple:
        snapshot = tuple(
            _reference_special_container_snapshot(item, memo)
            for item in value
        )
        memo[id(value)] = snapshot
        return snapshot
    if type(value) is frozenset:
        snapshot = frozenset(
            _reference_special_container_snapshot(item, memo)
            for item in value
        )
        memo[id(value)] = snapshot
        return snapshot
    return value


# Freeze leaf functions and their catalog caches as well as the state Owner.
# Imported live validators must not become the canonical oracle by accident.
_reference_special_namespaces = {
    **vars(_special_contracts),
    **vars(_special_profiles),
    **vars(_special_validation),
    **globals(),
}
_reference_special_source_globals = tuple(_reference_special_namespaces.items())
_reference_special_module_names = frozenset((
    __name__, _special_contracts.__name__, _special_profiles.__name__,
    _special_validation.__name__,
))
_reference_special_container_memo: dict[int, object] = {}
_REFERENCE_SPECIAL_GLOBALS = {
    name: (
        value
        if name == "__builtins__"
        else _reference_special_container_snapshot(
            value, _reference_special_container_memo)
    )
    for name, value in _reference_special_source_globals
}
_REFERENCE_SPECIAL_MODULE_FUNCTIONS = {
    name: value
    for name, value in _REFERENCE_SPECIAL_GLOBALS.items()
    if isinstance(value, FunctionType)
    and value.__module__ in _reference_special_module_names
}
for _reference_name, _reference_function in (
        _REFERENCE_SPECIAL_MODULE_FUNCTIONS.items()):
    _reference_clone = FunctionType(
        _reference_function.__code__,
        _REFERENCE_SPECIAL_GLOBALS,
        name=_reference_function.__name__,
        argdefs=copy.deepcopy(_reference_function.__defaults__),
        closure=_reference_function.__closure__,
    )
    _reference_clone.__kwdefaults__ = copy.deepcopy(
        _reference_function.__kwdefaults__)
    _reference_clone.__annotations__ = copy.deepcopy(
        _reference_function.__annotations__)
    _reference_clone.__dict__.update(copy.deepcopy(_reference_function.__dict__))
    _reference_clone.__module__ = _reference_function.__module__
    _reference_clone.__qualname__ = _reference_function.__qualname__
    _REFERENCE_SPECIAL_GLOBALS[_reference_name] = _reference_clone


for _reference_name, _reference_cached in _reference_special_source_globals:
    _reference_wrapped = getattr(_reference_cached, "__wrapped__", None)
    if (isinstance(_reference_wrapped, FunctionType)
            and _reference_wrapped.__module__ in _reference_special_module_names
            and hasattr(_reference_cached, "cache_parameters")):
        _reference_clone = FunctionType(
            _reference_wrapped.__code__, _REFERENCE_SPECIAL_GLOBALS,
            name=_reference_wrapped.__name__,
            argdefs=copy.deepcopy(_reference_wrapped.__defaults__),
            closure=_reference_wrapped.__closure__,
        )
        _reference_clone.__kwdefaults__ = copy.deepcopy(_reference_wrapped.__kwdefaults__)
        _REFERENCE_SPECIAL_GLOBALS[_reference_name] = lru_cache(
            **_reference_cached.cache_parameters())(_reference_clone)


def _reference_special_descriptor(descriptor):
    if isinstance(descriptor, staticmethod):
        return staticmethod(FunctionType(
            descriptor.__func__.__code__,
            _REFERENCE_SPECIAL_GLOBALS,
            name=descriptor.__func__.__name__,
            argdefs=copy.deepcopy(descriptor.__func__.__defaults__),
            closure=descriptor.__func__.__closure__,
        ))
    if isinstance(descriptor, classmethod):
        return classmethod(FunctionType(
            descriptor.__func__.__code__,
            _REFERENCE_SPECIAL_GLOBALS,
            name=descriptor.__func__.__name__,
            argdefs=copy.deepcopy(descriptor.__func__.__defaults__),
            closure=descriptor.__func__.__closure__,
        ))
    if isinstance(descriptor, property):
        return property(
            FunctionType(descriptor.fget.__code__, _REFERENCE_SPECIAL_GLOBALS,
                         descriptor.fget.__name__, descriptor.fget.__defaults__,
                         descriptor.fget.__closure__) if descriptor.fget else None,
            FunctionType(descriptor.fset.__code__, _REFERENCE_SPECIAL_GLOBALS,
                         descriptor.fset.__name__, descriptor.fset.__defaults__,
                         descriptor.fset.__closure__) if descriptor.fset else None,
            FunctionType(descriptor.fdel.__code__, _REFERENCE_SPECIAL_GLOBALS,
                         descriptor.fdel.__name__, descriptor.fdel.__defaults__,
                         descriptor.fdel.__closure__) if descriptor.fdel else None,
            descriptor.__doc__,
        )
    if isinstance(descriptor, FunctionType):
        clone = FunctionType(
            descriptor.__code__,
            _REFERENCE_SPECIAL_GLOBALS,
            name=descriptor.__name__,
            argdefs=copy.deepcopy(descriptor.__defaults__),
            closure=descriptor.__closure__,
        )
        clone.__kwdefaults__ = copy.deepcopy(descriptor.__kwdefaults__)
        clone.__annotations__ = copy.deepcopy(descriptor.__annotations__)
        clone.__dict__.update(copy.deepcopy(descriptor.__dict__))
        clone.__module__ = descriptor.__module__
        clone.__qualname__ = descriptor.__qualname__
        return clone
    return descriptor


_REFERENCE_SPECIAL_GLOBALS.update(build_reference_special_formulas_v1(
    _REFERENCE_SPECIAL_GLOBALS))


_REFERENCE_SPECIAL_RUNTIME_DESCRIPTORS = {
    name: _reference_special_descriptor(descriptor)
    for name, descriptor in SpecialReactionRuntime.__dict__.items()
    if (callable(descriptor)
        or isinstance(descriptor, (staticmethod, classmethod, property)))
}
_REFERENCE_SPECIAL_RUNTIME_DESCRIPTORS["replace"] = build_reference_replace_v1(
    _REFERENCE_SPECIAL_GLOBALS)
_REFERENCE_SPECIAL_RUNTIME_DESCRIPTORS["replace_stellar_swirl_packet"] = build_reference_packet_v1(
    _REFERENCE_SPECIAL_GLOBALS)
_REFERENCE_SPECIAL_RUNTIME_DESCRIPTORS.update(build_reference_special_deadlines_v1(
    _REFERENCE_SPECIAL_GLOBALS))
_REFERENCE_SPECIAL_RUNTIME_DESCRIPTORS["_admit_lunar_electro_tick_impl"] = build_reference_lunar_tick_v1(
    _REFERENCE_SPECIAL_GLOBALS)
_REFERENCE_SPECIAL_RUNTIME_DESCRIPTORS["_require_ready"] = build_reference_readiness_v1(
    _REFERENCE_SPECIAL_GLOBALS)
_REFERENCE_SPECIAL_RUNTIME_TYPE = type(
    "_ReferenceSpecialReactionRuntime",
    (SpecialReactionRuntime,),
    {
        "__module__": __name__,
        "__doc__": (
            "Import-time canonical SpecialReactionRuntime used only by "
            "isolated producer oracles."
        ),
        **_REFERENCE_SPECIAL_RUNTIME_DESCRIPTORS,
    },
)


def _copy_special_runtime_for_reference(
    runtime: SpecialReactionRuntime,
) -> SpecialReactionRuntime:
    """Deep-copy *runtime* onto the import-time canonical implementation.

    Capturing only a public entry point is insufficient because that method
    dispatches through ``self`` to internal helpers and resolves module helpers
    through the function's ``__globals__`` mapping.  A later class- or
    module-level test, instrumentation wrapper, or accidental patch would
    otherwise affect both the live producer and its supposed reference
    execution and make the oracle agree with the same corruption.  The private
    canonical type shadows every method descriptor captured when this module
    finished defining the runtime, and those descriptors share a private
    namespace containing cloned module-local functions.

    Callable instance attributes are removed before switching the copy's type
    so an instance-level wrapper cannot shadow the canonical descriptor either.
    The returned object is an isolated oracle candidate and is never committed
    into the live world.
    """
    if not isinstance(runtime, SpecialReactionRuntime):
        raise TypeError("runtime必须是SpecialReactionRuntime。")
    candidate = copy.deepcopy(runtime)
    for name in _REFERENCE_SPECIAL_RUNTIME_DESCRIPTORS:
        candidate.__dict__.pop(name, None)
    candidate.__class__ = _REFERENCE_SPECIAL_RUNTIME_TYPE
    return candidate


# Resolve stable entry points through the canonical type as well.  Accessing
# descriptors on the class preserves normal binding semantics: instance
# methods stay unbound callables, static methods stay plain functions, and
# class methods bind to the private reference type.  Pointing these aliases at
# ``SpecialReactionRuntime`` would reintroduce the live module globals through
# the original function body's ``__globals__`` even when ``self`` is a
# canonical candidate.
_REFERENCE_REPLACE_STELLAR_SWIRL_PACKET = (
    _REFERENCE_SPECIAL_RUNTIME_TYPE.replace_stellar_swirl_packet
)
_REFERENCE_REPLACE_SPECIAL_REACTION = _REFERENCE_SPECIAL_RUNTIME_TYPE.replace
_REFERENCE_RECORD_STELLAR_SUPERCONDUCT_APPLICATION = (
    _REFERENCE_SPECIAL_RUNTIME_TYPE.record_stellar_superconduct_application
)
_REFERENCE_STELLAR_SWIRL_DETONATION = (
    _REFERENCE_SPECIAL_RUNTIME_TYPE._stellar_swirl_detonation
)
_REFERENCE_ADVANCE_SPECIAL_REACTION = _REFERENCE_SPECIAL_RUNTIME_TYPE.advance
_REFERENCE_ADVANCE_WORLD_PRE_EXPIRY = (
    _REFERENCE_SPECIAL_RUNTIME_TYPE.advance_world_pre_expiry
)
_REFERENCE_FINALIZE_WORLD_LUNAR_ELECTRO_EXPIRY = (
    _REFERENCE_SPECIAL_RUNTIME_TYPE.finalize_world_lunar_electro_expiry
)
_REFERENCE_ADMIT_LUNAR_ELECTRO_TICK_IMPL = (
    _REFERENCE_SPECIAL_RUNTIME_TYPE._admit_lunar_electro_tick_impl
)
_REFERENCE_CALCULATE_WEIGHTED_SPECIAL_DAMAGE = _REFERENCE_SPECIAL_GLOBALS[
    "calculate_weighted_special_damage"]
_REFERENCE_CALCULATE_DIRECT_SPECIAL_DAMAGE = _REFERENCE_SPECIAL_GLOBALS[
    "calculate_direct_special_damage"]
_REFERENCE_RESOLVE_SPECIAL_DAMAGE_EVENT = _REFERENCE_SPECIAL_GLOBALS[
    "resolve_special_damage_event"]
