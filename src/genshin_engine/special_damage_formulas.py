"""Pure Lunar/Stellar numeric projection, independent of runtime state and DB."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SpecialFormulaServices:
    weighted_types: tuple[str, ...]
    weights: tuple[float, ...]
    factor_for: Callable[..., tuple[str, float]]
    finite_number: Callable[..., bool]
    validate_contributor: Callable[..., dict[str, Any]]
    validate_roles: Callable[..., dict[str, Any]]
    rank_outcome: Callable[..., list[dict[str, Any]]]
    lunar_damage: Callable[..., dict[str, Any]]
    amplify_bonus: Callable[..., float]
    weighted_damage: Callable[..., dict[str, Any]]
    direct_damage: Callable[..., dict[str, Any]]


def _resolved_stellar_coefficient(services, reaction_type, factor, evidence):
    if evidence is None:
        return factor
    if not isinstance(evidence, dict):
        raise ValueError("星扩散基础系数修正必须为对象。")
    multiplier = evidence.get("factor_multiplier")
    effective = evidence.get("effective_factor")
    if (not reaction_type.startswith("stellar_swirl")
            or evidence.get("base_factor") != factor
            or not services.finite_number(multiplier, positive=True)
            or not services.finite_number(effective, positive=True)
            or effective != factor * multiplier):
        raise ValueError("星扩散基础系数修正与该伤害分项不一致。")
    return effective


def calculate_weighted_special_damage(
    services: SpecialFormulaServices,
    reaction_type: str,
    contributors: list[dict],
    *,
    amplify: dict | None = None,
    role_provenance: dict | None = None,
    event_id: str = "calculate_weighted_special_damage",
    reaction_coefficient: dict | None = None,
) -> dict:
    """Calculate one reaction-based Lunar/Stellar event with 0.6/0.3/.05/.05.

    Every contributor rolls Crit independently.  The runtime then ranks the
    *actual damage in that outcome* and applies the first/second/third/fourth
    weights.  With at most four contributors this is an exact finite joint
    enumeration of at most sixteen outcomes.  Ranking independent expected
    values first is observably wrong when a lower base component can overtake
    another component only on Crit.
    """
    if reaction_type not in services.weighted_types:
        raise ValueError("该反应不是受支持的多贡献者反应伤害。")
    if not isinstance(contributors, list) or not 1 <= len(contributors) <= 4:
        raise ValueError("反应月曜/星烁必须提供1至4个附着贡献者。")
    rows = [services.validate_contributor(value, index) for index, value in enumerate(contributors)]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("附着贡献者ID不得重复。")
    if reaction_type.startswith("stellar_swirl_"):
        role_provenance = services.validate_roles(
            reaction_type, [row["id"] for row in rows], role_provenance,
            event_id=event_id,
        )
    kind, factor = services.factor_for(reaction_type)
    factor = _resolved_stellar_coefficient(services, reaction_type, factor, reaction_coefficient)
    components = []
    for row in rows:
        ctx = {
            "level": row["level"],
            "reaction_base": True,
            "system_factor": factor,
            "em": row["em"],
            "crit_rate": row["crit_rate"],
            "crit_dmg": row["crit_dmg"],
            "reaction_bonus": row.get("reaction_bonus", 0.0),
            "reaction_flat": row.get("reaction_flat", 0.0),
            "resistance": row.get("resistance", 0.0),
        }
        for suffix in ("base", "mult", "elevate"):
            key = f"{kind}_{suffix}"
            if key in row:
                ctx[key] = row[key]
        result = services.lunar_damage(ctx, kind)
        components.append({
            "contributor_id": row["id"],
            "source_refs": copy.deepcopy(row.get("source_refs", [])),
            "context": ctx,
            "result": result,
        })
    amplify_context = None
    if amplify is not None:
        if reaction_type not in ("stellar_swirl_cryo_lv2", "stellar_swirl_cryo_lv3"):
            raise ValueError("只有反应星扩散冰分项接受融化增幅。")
        if not isinstance(amplify, dict) or set(amplify) != {"owner_id", "em", "reaction_bonus"}:
            raise ValueError("融化增幅必须明确唯一伤害源owner_id、em与reaction_bonus。")
        if (not isinstance(amplify["owner_id"], str) or not amplify["owner_id"].strip()
                or not services.finite_number(amplify["em"], minimum=0)
                or not services.finite_number(amplify["reaction_bonus"])):
            raise ValueError("融化增幅输入非法。")
        if amplify["owner_id"] not in {row["id"] for row in rows}:
            raise ValueError("融化唯一伤害源必须属于本次星扩散贡献者。")
        multiplier = 1.5 * (1 + services.amplify_bonus(amplify["em"]) + amplify["reaction_bonus"])
        amplify_context = {**copy.deepcopy(amplify), "base_factor": 1.5, "multiplier": multiplier}
        for component in components:
            component["result"]["expected"] *= multiplier
            component["result"]["crit"] *= multiplier
            component["result"]["zones"]["amplify"] = multiplier

    for component, row in zip(components, rows):
        crit_probability = min(row["crit_rate"], 1.0)
        crit_multiplier = 1 + row["crit_dmg"]
        no_crit = component["result"]["crit"] / crit_multiplier
        component.update(
            crit_probability=crit_probability,
            no_crit_damage=no_crit,
            crit_damage=component["result"]["crit"],
            expected_weight_share=0.0,
            expected_weighted_damage=0.0,
            rank_probabilities={str(index+1): 0.0 for index in range(len(components))},
        )

    joint_outcomes = []
    expected_terms = []
    all_crit_total = None
    no_crit_total = None
    for mask in range(1 << len(components)):
        probability = 1.0
        actual = []
        for index, component in enumerate(components):
            did_crit = bool(mask & (1 << index))
            crit_probability = component["crit_probability"]
            probability *= crit_probability if did_crit else 1-crit_probability
            actual.append({
                "contributor_id": component["contributor_id"],
                "did_crit": did_crit,
                "damage": (component["crit_damage"] if did_crit
                           else component["no_crit_damage"]),
                "component_index": index,
            })
        ranked = services.rank_outcome(
            reaction_type, actual, role_provenance)
        ranking = []
        weighted_terms = []
        for rank, value in enumerate(ranked, start=1):
            weight = services.weights[rank-1]
            weighted_damage = value["damage"]*weight
            weighted_terms.append(weighted_damage)
            ranking.append({
                "rank": rank,
                "contributor_id": value["contributor_id"],
                "did_crit": value["did_crit"],
                "unweighted_damage": value["damage"],
                "weight": weight,
                "weighted_damage": weighted_damage,
            })
            component = components[value["component_index"]]
            component["expected_weight_share"] += probability*weight
            component["expected_weighted_damage"] += probability*weighted_damage
            component["rank_probabilities"][str(rank)] += probability
        total = math.fsum(weighted_terms)
        expected_terms.append(probability*total)
        if mask == 0:
            no_crit_total = total
        if mask == (1 << len(components))-1:
            all_crit_total = total
        joint_outcomes.append({
            "crit_mask": mask,
            "probability": probability,
            "ranking": ranking,
            "total": total,
        })

    expected = math.fsum(expected_terms)
    probability_total = math.fsum(row["probability"] for row in joint_outcomes)
    for component in components:
        component["possible_weights"] = sorted({
            row["weight"]
            for outcome in joint_outcomes
            for row in outcome["ranking"]
            if row["contributor_id"] == component["contributor_id"]
        }, reverse=True)
    return {
        "damage_system": kind,
        "reaction_type": reaction_type,
        "reaction_base": True,
        "system_factor": factor,
        "weights": list(services.weights),
        "components": components,
        "amplify": amplify_context,
        "ranking_policy": (
            "stellar_swirl_role_slots_then_independent_actual_damage"
            if reaction_type.startswith("stellar_swirl_")
            else "independent_crit_then_actual_damage_desc"),
        "role_provenance": copy.deepcopy(role_provenance),
        "joint_outcome_count": len(joint_outcomes),
        "joint_probability_total": probability_total,
        "joint_outcomes": joint_outcomes,
        "no_crit": no_crit_total,
        "expected": expected,
        "crit": all_crit_total,
    }


def calculate_direct_special_damage(
    services: SpecialFormulaServices,
    reaction_type: str,
    contributor: dict,
    *,
    multiplier: float,
    scaling: float,
    layers: int | None = None,
    reaction_coefficient: dict | None = None,
) -> dict:
    """Resolve direct Lunar/Stellar damage such as a direct Star-Superconduct hit."""
    row = services.validate_contributor(contributor, 0, reaction_base=False)
    if not services.finite_number(multiplier, minimum=0) or not services.finite_number(scaling, minimum=0):
        raise ValueError("直伤倍率与结算属性必须是有限非负数。")
    kind, factor = services.factor_for(reaction_type, layers=layers)
    factor = _resolved_stellar_coefficient(services, reaction_type, factor, reaction_coefficient)
    ctx = {
        "level": row["level"],
        "multiplier": multiplier,
        "scaling": scaling,
        "reaction_base": False,
        "system_factor": factor,
        "em": row["em"],
        "crit_rate": row["crit_rate"],
        "crit_dmg": row["crit_dmg"],
        "reaction_bonus": row.get("reaction_bonus", 0.0),
        "reaction_flat": row.get("reaction_flat", 0.0),
        "resistance": row.get("resistance", 0.0),
    }
    for suffix in ("base", "mult", "elevate"):
        key = f"{kind}_{suffix}"
        if key in row:
            ctx[key] = row[key]
    result = services.lunar_damage(ctx, kind)
    return {
        "damage_system": kind,
        "reaction_type": reaction_type,
        "reaction_base": False,
        "layers": layers if reaction_type == "stellar_superconduct" else None,
        "contributor_id": row["id"],
        "context": ctx,
        "result": result,
    }


def resolve_special_damage_event(
    services: SpecialFormulaServices,
    event: dict,
    contributors: list[dict],
    *,
    amplify: dict | None = None,
    scaling: float | None = None,
) -> dict:
    """Attach an auditable numeric result to a generated reaction damage request."""
    if not isinstance(event, dict) or event.get("type") != "special_reaction_damage_request":
        raise ValueError("只能结算星月运行时生成的伤害请求。")
    coefficient = event.get("reaction_coefficient")
    if event.get("reaction_base") is False:
        if amplify is not None:
            raise ValueError("能力直伤星月请求不接受反应基数伤害的增幅参数。")
        if not isinstance(contributors, list) or len(contributors) != 1:
            raise ValueError("能力直伤星月请求必须且只能提供伤害角色本人。")
        if scaling is None:
            raise ValueError("能力直伤星月请求必须提供事件时刻采样后的结算属性。")
        result = services.direct_damage(
            event["reaction_type"], contributors[0],
            multiplier=event.get("multiplier"), scaling=scaling,
            layers=event.get("layers"),
            **({"reaction_coefficient": coefficient} if coefficient is not None else {}),
        )
    else:
        if scaling is not None:
            raise ValueError("反应基数星月伤害不接受直伤结算属性。")
        result = services.weighted_damage(
            event["reaction_type"], contributors, amplify=amplify,
            role_provenance=event.get("role_provenance"),
            event_id=event.get("event_id", "special_reaction_damage_request"),
            **({"reaction_coefficient": coefficient} if coefficient is not None else {}),
        )
    return {
        **copy.deepcopy(event),
        "type": "special_reaction_damage",
        "status": "executed",
        "sample_t": event["t"],
        "damage": result,
    }

