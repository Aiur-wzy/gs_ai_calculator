"""Frozen pre-extraction special-damage formula functions; no production fallback."""

from __future__ import annotations

import copy
import math
from types import FunctionType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genshin_engine.formulas import (  # noqa: TC004 - injected canonical globals
        amplify_em_bonus,
        lunar_stellar_damage,
    )
    from genshin_engine.special_reactions import (  # noqa: TC004 - injected canonical globals
        LUNAR_STELLAR_WEIGHTS,
        WEIGHTED_REACTION_TYPES,
        _factor_for,
        _finite_number,
        _validate_contributor,
        _validate_stellar_swirl_role_provenance,
        _weighted_outcome_ranking,
        calculate_direct_special_damage,
        calculate_weighted_special_damage,
    )


def _canonical_calculate_weighted_special_damage_v1(
    reaction_type: str,
    contributors: list[dict],
    *,
    amplify: dict | None = None,
    role_provenance: dict | None = None,
    event_id: str = "calculate_weighted_special_damage",
) -> dict:
    """Calculate one reaction-based Lunar/Stellar event with 0.6/0.3/.05/.05.

    Every contributor rolls Crit independently.  The runtime then ranks the
    *actual damage in that outcome* and applies the first/second/third/fourth
    weights.  With at most four contributors this is an exact finite joint
    enumeration of at most sixteen outcomes.  Ranking independent expected
    values first is observably wrong when a lower base component can overtake
    another component only on Crit.
    """
    if reaction_type not in WEIGHTED_REACTION_TYPES:
        raise ValueError("该反应不是受支持的多贡献者反应伤害。")
    if not isinstance(contributors, list) or not 1 <= len(contributors) <= 4:
        raise ValueError("反应月曜/星烁必须提供1至4个附着贡献者。")
    rows = [_validate_contributor(value, index) for index, value in enumerate(contributors)]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("附着贡献者ID不得重复。")
    if reaction_type.startswith("stellar_swirl_"):
        role_provenance = _validate_stellar_swirl_role_provenance(
            reaction_type, [row["id"] for row in rows], role_provenance,
            event_id=event_id,
        )
    kind, factor = _factor_for(reaction_type)
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
        result = lunar_stellar_damage(ctx, kind)
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
                or not _finite_number(amplify["em"], minimum=0)
                or not _finite_number(amplify["reaction_bonus"])):
            raise ValueError("融化增幅输入非法。")
        if amplify["owner_id"] not in {row["id"] for row in rows}:
            raise ValueError("融化唯一伤害源必须属于本次星扩散贡献者。")
        multiplier = 1.5 * (1 + amplify_em_bonus(amplify["em"]) + amplify["reaction_bonus"])
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
        ranked = _weighted_outcome_ranking(
            reaction_type, actual, role_provenance)
        ranking = []
        weighted_terms = []
        for rank, value in enumerate(ranked, start=1):
            weight = LUNAR_STELLAR_WEIGHTS[rank-1]
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
        "weights": list(LUNAR_STELLAR_WEIGHTS),
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


def _canonical_calculate_direct_special_damage_v1(
    reaction_type: str,
    contributor: dict,
    *,
    multiplier: float,
    scaling: float,
    layers: int | None = None,
) -> dict:
    """Resolve direct Lunar/Stellar damage such as a direct Star-Superconduct hit."""
    row = _validate_contributor(contributor, 0, reaction_base=False)
    if not _finite_number(multiplier, minimum=0) or not _finite_number(scaling, minimum=0):
        raise ValueError("直伤倍率与结算属性必须是有限非负数。")
    kind, factor = _factor_for(reaction_type, layers=layers)
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
    result = lunar_stellar_damage(ctx, kind)
    return {
        "damage_system": kind,
        "reaction_type": reaction_type,
        "reaction_base": False,
        "layers": layers if reaction_type == "stellar_superconduct" else None,
        "contributor_id": row["id"],
        "context": ctx,
        "result": result,
    }


def _canonical_resolve_special_damage_event_v1(
    event: dict,
    contributors: list[dict],
    *,
    amplify: dict | None = None,
    scaling: float | None = None,
) -> dict:
    """Attach an auditable numeric result to a generated reaction damage request."""
    if not isinstance(event, dict) or event.get("type") != "special_reaction_damage_request":
        raise ValueError("只能结算星月运行时生成的伤害请求。")
    if event.get("reaction_base") is False:
        if amplify is not None:
            raise ValueError("能力直伤星月请求不接受反应基数伤害的增幅参数。")
        if not isinstance(contributors, list) or len(contributors) != 1:
            raise ValueError("能力直伤星月请求必须且只能提供伤害角色本人。")
        if scaling is None:
            raise ValueError("能力直伤星月请求必须提供事件时刻采样后的结算属性。")
        result = calculate_direct_special_damage(
            event["reaction_type"], contributors[0],
            multiplier=event.get("multiplier"), scaling=scaling,
            layers=event.get("layers"))
    else:
        if scaling is not None:
            raise ValueError("反应基数星月伤害不接受直伤结算属性。")
        result = calculate_weighted_special_damage(
            event["reaction_type"], contributors, amplify=amplify,
            role_provenance=event.get("role_provenance"),
            event_id=event.get("event_id", "special_reaction_damage_request"),
        )
    return {
        **copy.deepcopy(event),
        "type": "special_reaction_damage",
        "status": "executed",
        "sample_t": event["t"],
        "damage": result,
    }


_REFERENCE_FORMULAS = {
    "calculate_weighted_special_damage": _canonical_calculate_weighted_special_damage_v1,
    "calculate_direct_special_damage": _canonical_calculate_direct_special_damage_v1,
    "resolve_special_damage_event": _canonical_resolve_special_damage_event_v1,
}


def build_reference_special_formulas_v1(namespace: dict[str, Any]) -> dict[str, FunctionType]:
    result = {}
    for name, source in _REFERENCE_FORMULAS.items():
        clone = FunctionType(source.__code__, namespace, name, source.__defaults__, source.__closure__)
        clone.__kwdefaults__ = copy.deepcopy(source.__kwdefaults__)
        clone.__annotations__ = copy.deepcopy(source.__annotations__)
        result[name] = clone
    return result

