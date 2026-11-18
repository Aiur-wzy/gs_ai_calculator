"""Special-reaction damage requests projected into ordinary mechanics hits.

This module owns the deterministic projection and contributor-weighting
boundary.  It does not schedule events or mutate elemental/world state.
"""

from __future__ import annotations

import copy
import math
from decimal import Decimal, localcontext
from fractions import Fraction

from genshin_engine.constants import LUNAR_FACTOR, stellar_superconduct_factor
from genshin_engine.elemental import ElementalPending
from genshin_engine.special_reaction_port import (
    LUNAR_STELLAR_WEIGHTS,
)
from genshin_engine.special_reaction_port import (
    validate_stellar_swirl_role_provenance as _validate_stellar_swirl_role_provenance,
)
from genshin_engine.special_reaction_port import (
    weighted_outcome_ranking as _weighted_outcome_ranking,
)

_SPECIAL_DAMAGE_PRESENTATION = {
    "lunar_electro": ("月感电", "雷暴云伤害", "electro"),
    "lunar_bloom": ("月绽放", "草露伤害", "dendro"),
    "lunar_crystallize": ("月结晶", "月笼谐奏", "geo"),
    "stellar_swirl_anemo": ("星扩散", "风元素伤害", "anemo"),
    # The historical suffix stores the base factor, not the displayed vortex level.
    "stellar_swirl_cryo_lv2": ("星扩散", "1级风旋爆发", "cryo"),
    "stellar_swirl_cryo_lv3": ("星扩散", "2级风旋爆发", "cryo"),
}


def _special_damage_hit(request, contributor, index, *, cycle=0):
    """Compile one runtime damage request into a normal v2 reaction-base hit."""
    reaction_type = request.get("reaction_type")
    if reaction_type not in _SPECIAL_DAMAGE_PRESENTATION:
        raise ElementalPending(
            "special_damage_projection_unknown",
            "星月运行时生成了尚未接入数值投影的伤害类型。",
            request.get("event_id", "special_reaction_damage_request"),
            {"reaction_type": reaction_type},
        )
    skill, hit_name, element = _SPECIAL_DAMAGE_PRESENTATION[reaction_type]
    component = request.get("component")
    if component:
        hit_name += f"（{component}）"
    event_id = request.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("special_reaction_damage_request缺少event_id。")
    factor = request.get("system_factor")
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor <= 0:
        raise ValueError("special_reaction_damage_request.system_factor必须是有限正数。")
    hit = {
        "id": f"{event_id}:contributor:{index+1}:{contributor}",
        "t": request["t"],
        "character": contributor,
        "skill": skill,
        "hit_name": hit_name,
        "description": "由星月反应运行时生成，并在事件时刻读取贡献者属性。",
        "damage_type_ids": ["damage.reaction"],
        "damage_system": request["damage_system"],
        "element": element,
        "reaction_base": True,
        "reaction": {"type": reaction_type, "factor": factor},
        "count": 1,
        "result_weight": 1.0,
        "extensions": {
            "elemental": {
                "mode": "special_reaction_runtime",
                "special_reaction_request_id": event_id,
                "parent_reaction_id": request.get("parent_reaction_id"),
                "target_id": request.get("target_id"),
                "area_id": request.get("area_id"),
                "component": component,
                "contributor_sample_policy": request.get("contributor_sample_policy"),
                "role_provenance": copy.deepcopy(request.get("role_provenance")),
                "cycle": cycle,
            },
            "special_reaction": {
                "request_id": event_id,
                "reaction_type": reaction_type,
                "area_id": request.get("area_id"),
                "target_id": request.get("target_id"),
                "component": component,
                "role_provenance": copy.deepcopy(request.get("role_provenance")),
                "contributor_rank_pending": True,
            },
        },
    }
    if request.get('extra_damage_provenance') is not None:
        hit['extensions']['special_damage_echo'] = copy.deepcopy(request['extra_damage_provenance'])
        hit['description'] = '显式近似分支：复用实际父包贡献者与多人公式；不重放附着、GU、自然周期和触发回调。'
    return hit


def _direct_profile_evidence_label(profile):
    """Describe profile authority without promoting an assumption to reviewed."""
    if isinstance(profile, dict):
        if (profile.get("status") == "confirmed"
                and profile.get("authority") == "reviewed_database_projection"):
            return "受审数据库能力profile"
        if (profile.get("status") == "assumed"
                and profile.get("authority") == "explicit_assumption"):
            return "显式假设能力profile"
    return "带审计状态的能力profile"


def _direct_lunar_damage_hit(request, *, cycle=0):
    """Compile an audited ability-owned Lunar hit without contributor weighting."""
    event_id = request.get("event_id")
    owner = request.get("owner")
    parameters = (request.get("ability_profile") or {}).get("parameters") or {}
    contract = {
        "reaction_type": request.get("reaction_type"),
        "element": request.get("element"),
        "multiplier": request.get("multiplier"),
        "scaling_stat": request.get("scaling_stat"),
        "count": request.get("count"),
        "component": request.get("component"),
    }
    if contract != parameters:
        raise ElementalPending(
            "direct_lunar_damage_contract_mismatch",
            "直伤月曜请求与能力profile的数值字段不一致。",
            event_id or "special_reaction_damage_request",
            {"request_parameters": contract, "profile_parameters": copy.deepcopy(parameters)},
        )
    factor = request.get("system_factor")
    if (isinstance(factor, bool) or not isinstance(factor, (int, float))
            or not math.isfinite(factor) or factor <= 0
            or factor != LUNAR_FACTOR.get(request.get("reaction_type"))
            or request.get("factor_policy") != "engine_constant"):
        raise ElementalPending(
            "direct_lunar_damage_contract_mismatch",
            "直伤月曜系统系数必须由引擎常量生成。",
            event_id or "special_reaction_damage_request",
            {"reaction_type": request.get("reaction_type"),
             "system_factor": factor,
             "factor_policy": request.get("factor_policy")},
        )
    component = request["component"]
    reaction_type = request["reaction_type"]
    if reaction_type not in ("lunar_electro", "lunar_bloom", "lunar_crystallize"):
        raise ElementalPending(
            "direct_lunar_damage_type_unsupported",
            "直伤月曜请求包含未接入的反应类型。",
            event_id or "special_reaction_damage_request",
            {"reaction_type": reaction_type})
    skill, base_hit_name, _ = _SPECIAL_DAMAGE_PRESENTATION[reaction_type]
    debit = request.get("dew_consumption")
    absorption = request.get("bloom_core_absorption")
    multiplier = request["multiplier"]
    if debit and debit["mode"] == "scale_multiplier":
        multiplier *= debit["actual_dew"]
    if absorption:
        multiplier *= 1 + absorption["actual_cores"] * absorption["multiplier_bonus_per_core"]
    return {
        "id": f"{event_id}:direct:{owner}", "t": request["t"],
        "character": owner, "skill": skill,
        "hit_name": f"{base_hit_name}·能力直伤（{component}）",
        "description": (
            f"由{_direct_profile_evidence_label(request.get('ability_profile'))}生成；"
            "角色属性与可用增益在伤害事件时刻读取。"
        ),
        "damage_type_ids": ["damage.reaction"], "damage_system": "lunar",
        "element": request["element"], "multiplier": multiplier,
        "scaling_stat": request["scaling_stat"], "reaction_base": False,
        "reaction": {"type": reaction_type, "factor": factor},
        "count": request["count"],
        "extensions": {
            "elemental": {
                "mode": "special_reaction_runtime_direct_ability",
                "special_reaction_request_id": event_id,
                "target_id": request.get("target_id"), "area_id": request.get("area_id"),
                "action_id": request.get("action_id"),
                "actor_sample_policy": request.get("actor_sample_policy"), "cycle": cycle,
                **({"dew_consumption": copy.deepcopy(debit)} if debit else {}),
                **({"bloom_core_absorption": copy.deepcopy(absorption)} if absorption else {}),
            },
            "special_reaction": {
                "request_id": event_id, "reaction_type": reaction_type,
                "reaction_base": False, "component": component,
                "profile": copy.deepcopy(request.get("profile")),
                "ability_profile": copy.deepcopy(request["ability_profile"]),
                "contributor_weighting": "not_applicable",
            },
        },
    }


def _direct_stellar_damage_hit(request, *, cycle=0):
    """Compile an ability-owned Star-Superconduct hit from a runtime snapshot."""
    event_id = request.get("event_id")
    owner = request.get("owner")
    parameters = (request.get("ability_profile") or {}).get("parameters") or {}
    contract = {
        "reaction_type": request.get("reaction_type"),
        "element": request.get("element"),
        "multiplier": request.get("multiplier"),
        "scaling_stat": request.get("scaling_stat"),
        "count": request.get("count"),
        "component": request.get("component"),
    }
    if contract != parameters:
        raise ElementalPending(
            "direct_stellar_damage_contract_mismatch",
            "直伤星超导请求与能力profile的数值字段不一致。",
            event_id or "special_reaction_damage_request",
            {"request_parameters": contract, "profile_parameters": copy.deepcopy(parameters)},
        )
    layers, factor = request.get("layers"), request.get("system_factor")
    snapshot = request.get("settled_snapshot")
    reaction_type = request.get("reaction_type")
    valid_factor = ((type(layers) is int and 0 <= layers <= 12 and factor == stellar_superconduct_factor(layers))
                    if reaction_type == "stellar_superconduct" else
                    (reaction_type == "stellar_swirl" and layers is None and factor == 1.0))
    if (not valid_factor
            or not isinstance(snapshot, dict) or snapshot.get("layers") != layers
            or snapshot.get("system_factor") != factor):
        raise ElementalPending(
            "direct_stellar_runtime_sample_mismatch",
            "直伤星超导层数与系数必须来自同一运行时结算快照。",
            event_id or "special_reaction_damage_request",
            {"layers": layers, "system_factor": factor,
             "settled_snapshot": copy.deepcopy(snapshot)},
        )
    component = request["component"]
    label = "星超导" if reaction_type == "stellar_superconduct" else "星扩散"
    return {
        "id": f"{event_id}:direct:{owner}", "t": request["t"],
        "character": owner, "skill": label,
        "hit_name": f"{label}·能力直伤（{component}）",
        "description": (
            f"由{_direct_profile_evidence_label(request.get('ability_profile'))}生成；"
            + ("星超导层数在事件时刻从最近一次四秒结算读取。" if reaction_type == "stellar_superconduct"
               else "使用能力自身星扩散倍率，不读取自然风旋层级。")
        ),
        "damage_type_ids": ["damage.reaction"], "damage_system": "stellar",
        "element": request["element"], "multiplier": request["multiplier"],
        "scaling_stat": request["scaling_stat"], "reaction_base": False,
        "reaction": ({"type": reaction_type, "layers": layers} if layers is not None else {"type": reaction_type}),
        "count": request["count"],
        "extensions": {
            "elemental": {
                "mode": "special_reaction_runtime_direct_ability",
                "special_reaction_request_id": event_id,
                "target_id": request.get("target_id"), "area_id": request.get("area_id"),
                "action_id": request.get("action_id"),
                "actor_sample_policy": request.get("actor_sample_policy"), "cycle": cycle,
            },
            "special_reaction": {
                "request_id": event_id, "reaction_type": reaction_type,
                "reaction_base": False, "component": component,
                "area_id": request.get("area_id"),
                "profile": copy.deepcopy(request.get("profile")),
                "ability_profile": copy.deepcopy(request["ability_profile"]),
                "settled_snapshot": copy.deepcopy(snapshot),
                "layer_source": request.get("factor_policy"),
                "contributor_weighting": "not_applicable",
            },
        },
    }


def _apply_special_damage_weights(resolved, first_result, groups):
    """Enumerate independent Crit outcomes, then rank each actual outcome.

    A scalar ``result_weight`` cannot describe the per-outcome rank switch.
    The second ordinary calculation therefore receives an
    expectation-equivalent weight for each contributor, while this receipt
    retains the exact joint distribution used to derive it.
    """
    hit_inputs = {row["id"]: row for row in resolved["hits"]}
    result_rows = {row["id"]: row for row in first_result["hits"]}
    receipts = []
    for group_id, group in sorted(groups.items()):
        request = group["request"]
        ids = [hit_id for hit_id in group["hit_ids"] if hit_id in hit_inputs]
        missing = [hit_id for hit_id in ids if hit_id not in result_rows]
        if missing:
            raise ValueError("星月伤害首次投影缺少贡献者命中: "+", ".join(missing))
        if not 1 <= len(ids) <= len(LUNAR_STELLAR_WEIGHTS):
            raise ValueError("单次星月伤害贡献者必须为1至4名。")
        components = []
        for hit_id in ids:
            result = result_rows[hit_id]
            expected = result["expected"]
            crit_damage = result.get("crit")
            crit_expected = (result.get("zones") or {}).get("crit_expected")
            if (crit_damage is None or isinstance(crit_expected, bool)
                    or not isinstance(crit_expected, (int, float))
                    or not math.isfinite(crit_expected) or crit_expected < 1):
                raise ValueError("反应型星月贡献者缺少可反推的暴击期望乘区。")
            no_crit = expected/crit_expected if crit_expected else expected
            if expected == 0:
                # A zero expectation is not permission to collapse a positive
                # subnormal calculation. Prove zero from the saved factors.
                zones = result.get("zones") or {}
                factors = ("base_in", "system_factor", "base_bonus", "em_zone", "big_mult")
                if all(key in zones for key in (*factors, "reaction_flat", "resistance", "elevate")):
                    exact = math.prod(Fraction(zones[key]) for key in factors)
                    exact = (exact + Fraction(zones["reaction_flat"])) * Fraction(zones["resistance"]) * Fraction(zones["elevate"])
                    if exact != 0:
                        raise ValueError("特殊伤害数值低于当前浮点表示范围，不能把正伤害当作零。")
                elif crit_damage != 0:
                    raise ValueError("零期望特殊伤害仍有非零暴击分支，无法形成标量投影。")
                probability = 0.0
                no_crit = crit_damage = 0.0
            else:
                if no_crit == 0:
                    raise ValueError("特殊伤害非暴击基线发生浮点下溢，无法可靠分配贡献。")
                crit_multiplier = crit_damage/no_crit
                if crit_multiplier <= 1+1e-15:
                    probability = 0.0
                else:
                    probability = max(0.0, min(
                        1.0, (crit_expected-1)/(crit_multiplier-1)))
            components.append({
                "hit_id": hit_id,
                "contributor_id": hit_inputs[hit_id]["character"],
                "crit_probability": probability,
                "no_crit_damage": no_crit,
                "crit_damage": crit_damage,
                "unweighted_expected": expected,
                "expected_weight_share": 0.0,
                "expected_weighted_damage": 0.0,
                "normalized_weight_terms": [],
                "rank_probabilities": {
                    str(index+1): 0.0 for index in range(len(ids))},
            })
        reaction_type = request["reaction_type"]
        role_provenance = None
        if str(reaction_type).startswith("stellar_swirl_"):
            role_provenance = _validate_stellar_swirl_role_provenance(
                reaction_type,
                [component["contributor_id"] for component in components],
                request.get("role_provenance"),
                event_id=request.get("event_id", group_id),
            )
        ranking_policy = (
            "stellar_swirl_role_slots_then_independent_actual_damage"
            if str(reaction_type).startswith("stellar_swirl_")
            else "independent_crit_then_actual_damage_desc"
        )
        joint_outcomes = []
        for mask in range(1 << len(components)):
            probability = 1.0
            actual = []
            for index, component in enumerate(components):
                did_crit = bool(mask & (1 << index))
                p = component["crit_probability"]
                probability *= p if did_crit else 1-p
                actual.append({
                    "component_index": index,
                    "hit_id": component["hit_id"],
                    "contributor_id": component["contributor_id"],
                    "did_crit": did_crit,
                    "damage": (component["crit_damage"] if did_crit
                               else component["no_crit_damage"]),
                })
            actual = _weighted_outcome_ranking(
                reaction_type, actual, role_provenance)
            ranking_rows = []
            for rank, value in enumerate(actual, start=1):
                weight = LUNAR_STELLAR_WEIGHTS[rank-1]
                weighted = value["damage"]*weight
                component = components[value["component_index"]]
                component["expected_weight_share"] += probability*weight
                component["expected_weighted_damage"] += probability*weighted
                if probability and component["unweighted_expected"] > 0:
                    # Normalize before summing weighted damage. Multiplying a
                    # subnormal physical value first can incorrectly produce 0.
                    with localcontext() as context:
                        context.prec = 80
                        term = (Decimal(probability) * Decimal(value["damage"])
                                * Decimal(weight) / Decimal(component["unweighted_expected"]))
                    component["normalized_weight_terms"].append(float(term))
                component["rank_probabilities"][str(rank)] += probability
                ranking_rows.append({
                    "rank": rank,
                    "hit_id": value["hit_id"],
                    "contributor_id": value["contributor_id"],
                    "did_crit": value["did_crit"],
                    "unweighted_damage": value["damage"],
                    "weight": weight,
                    "weighted_damage": weighted,
                })
            joint_outcomes.append({
                "crit_mask": mask,
                "probability": probability,
                "ranking": ranking_rows,
                "total": math.fsum(row["weighted_damage"]
                                   for row in ranking_rows),
            })
        ranking = []
        for component in components:
            expected = component["unweighted_expected"]
            effective_weight = (math.fsum(component["normalized_weight_terms"])
                                if expected > 0 else component["expected_weight_share"])
            if not math.isfinite(effective_weight) or not 0 < effective_weight <= 1:
                raise ValueError("特殊伤害贡献权重不是有限正数，无法形成数值投影。")
            if expected > 0 and expected * effective_weight == 0:
                raise ValueError("特殊伤害加权结果发生浮点下溢，不能静默丢失正伤害。")
            if expected > 0 and not math.isclose(
                    expected * effective_weight, component["expected_weighted_damage"], rel_tol=1e-10, abs_tol=0):
                raise ValueError("特殊伤害联合分量低于可靠浮点精度，不能发布不一致的贡献证明。")
            hit_id = component["hit_id"]
            hit_inputs[hit_id]["result_weight"] = effective_weight
            certain_rank = next((int(rank) for rank, probability in
                                 component["rank_probabilities"].items()
                                 if abs(probability-1) <= 1e-12), None)
            hit_inputs[hit_id]["extensions"]["special_reaction"].update(
                contributor_rank=certain_rank,
                contributor_rank_pending=False,
                ranking_policy=ranking_policy,
                role_provenance=copy.deepcopy(role_provenance),
                unweighted_expected=expected,
                result_weight=effective_weight,
                result_weight_semantics="expected_equivalent_projection",
                expected_weight_share=component["expected_weight_share"],
                expected_weighted_damage=component["expected_weighted_damage"],
                rank_probabilities=copy.deepcopy(
                    component["rank_probabilities"]),
            )
            ranking.append({
                "rank": certain_rank,
                "hit_id": hit_id,
                "contributor_id": component["contributor_id"],
                "unweighted_expected": expected,
                "expected_weight_share": component["expected_weight_share"],
                "expected_weighted_damage": component["expected_weighted_damage"],
                "rank_probabilities": copy.deepcopy(
                    component["rank_probabilities"]),
                "result_weight": effective_weight,
                "result_weight_semantics": "expected_equivalent_projection",
            })
        ranking.sort(key=lambda row: (
            row["rank"] is None,
            row["rank"] if row["rank"] is not None else
            math.fsum(int(rank)*probability for rank, probability in
                      row["rank_probabilities"].items()),
            row["hit_id"]))
        receipts.append({
            "type": "special_reaction_weighting",
            "event_id": request["event_id"]+":weighting",
            "request_id": request["event_id"],
            "reaction_type": reaction_type,
            "t": request["t"],
            "cycle": group.get("cycle", 0),
            "policy": ranking_policy,
            "role_provenance": copy.deepcopy(role_provenance),
            "ranking": ranking,
            "joint_outcome_count": len(joint_outcomes),
            "joint_probability_total": math.fsum(
                row["probability"] for row in joint_outcomes),
            "joint_outcomes": joint_outcomes,
            "expected": math.fsum(
                row["expected_weighted_damage"] for row in components),
            "status": "executed",
        })
    return receipts

__all__ = [
    "_apply_special_damage_weights",
    "_direct_lunar_damage_hit",
    "_direct_profile_evidence_label",
    "_direct_stellar_damage_hit",
    "_special_damage_hit",
]
