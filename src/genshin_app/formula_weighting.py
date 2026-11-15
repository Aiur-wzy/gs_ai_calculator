"""Validated weighting provenance and fixed-distribution workbook edit scope.

This is an offline projection of execution facts, not another reaction solver.
The engine's joint-outcome ranking remains authoritative for this saved run.
"""
from __future__ import annotations

import math


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _close(left, right):
    return _number(left) and _number(right) and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-7)


def _hit_index(hits):
    if not isinstance(hits, list):
        raise ValueError("特殊贡献分配命中必须是列表。")  # noqa: TRY004 - saved proof validation
    indexed = {}
    for hit in hits:
        if not isinstance(hit, dict) or not isinstance(hit.get("id"), str) or not hit["id"] or hit["id"] in indexed:
            raise ValueError("特殊贡献分配命中身份缺失或重复。")
        indexed[hit["id"]] = hit
    return indexed


def weighting_projection(template, result, *, declared_mechanics=False):
    hits = template.get("hits", [])
    by_id = _hit_index(hits)
    results = _hit_index(result.get("hits", []))
    groups = (result.get("special_reactions") or {}).get("damage_weighting", [])
    if not isinstance(groups, list):
        raise ValueError("特殊贡献分配证明必须是列表。")  # noqa: TRY004 - saved proof validation
    mapped, identities, group_rows = {}, set(), []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("特殊贡献分配证明不是对象。")  # noqa: TRY004 - saved proof validation
        identity = (group.get("cycle"), group.get("request_id"))
        if (type(identity[0]) is not int or identity[0] < 0 or not isinstance(identity[1], str)
                or not identity[1] or identity in identities or group.get("status") != "executed"
                or group.get("type") != "special_reaction_weighting"):
            raise ValueError("特殊贡献分配组身份重复、缺失或尚未执行。")
        identities.add(identity)
        ranking = group.get("ranking")
        if not isinstance(ranking, list) or not 1 <= len(ranking) <= 4:
            raise ValueError("特殊贡献分配组成员不完整。")
        members = []
        for row in ranking:
            hid = row.get("hit_id") if isinstance(row, dict) else None
            hit, actual = by_id.get(hid), results.get(hid)
            if not isinstance(hid, str) or hid in mapped or hit is None or actual is None:
                raise ValueError("特殊贡献分配组引用缺失或重复的命中。")
            meta = (hit.get("extensions") or {}).get("special_reaction") or {}
            weight = hit.get("result_weight", 1)
            if (row.get("contributor_id") != hit.get("character")
                    or row.get("result_weight_semantics") != "expected_equivalent_projection"
                    or meta.get("result_weight_semantics") != "expected_equivalent_projection"
                    or meta.get("request_id") != identity[1]
                    or (hit.get("extensions") or {}).get("elemental", {}).get("cycle") != identity[0]
                    or meta.get("ranking_policy") != group.get("policy")
                    or meta.get("reaction_type") != group.get("reaction_type")
                    or meta.get("role_provenance") != group.get("role_provenance")
                    or not _close(hit.get("t"), group.get("t"))
                    or not _close(row.get("result_weight"), weight)
                    or not _close(meta.get("result_weight"), weight)
                    or not _close(actual.get("result_weight", 1), weight)
                    or not _close(row.get("expected_weighted_damage"), actual.get("expected"))
                    or not _close(row.get("unweighted_expected"), meta.get("unweighted_expected"))
                    or not _close(row.get("expected_weight_share"), meta.get("expected_weight_share"))
                    or not _close(row.get("expected_weighted_damage"), meta.get("expected_weighted_damage"))
                    or meta.get("contributor_rank_pending") is not False
                    or not _number(weight) or not 0 < weight <= 1):
                raise ValueError("特殊贡献分配证明、命中权重与实际结果不一致。")
            probabilities = row.get("rank_probabilities")
            if (not isinstance(probabilities, dict)
                    or set(probabilities) != {str(i) for i in range(1, len(ranking) + 1)}
                    or any(not _number(v) or not 0 <= v <= 1 for v in probabilities.values())
                    or not _close(sum(probabilities.values()), 1)):
                raise ValueError("特殊贡献分配排名概率不完整。")
            mapped[hid] = identity
            members.append(hid)
        if not _close(sum(results[hid]["expected"] for hid in members), group.get("expected")):
            raise ValueError("特殊贡献分配合计与命中结果不一致。")
        outcomes = group.get("joint_outcomes")
        if (not isinstance(outcomes, list) or len(outcomes) != 1 << len(members)
                or group.get("joint_outcome_count") != len(outcomes)
                or not _close(group.get("joint_probability_total"), 1)):
            raise ValueError("特殊贡献分配缺少完整联合分布证明。")
        masks, probability_sum = set(), 0.0
        weighted = {hid: [] for hid in members}
        shares = {hid: [] for hid in members}
        ranks = {hid: {str(i): [] for i in range(1, len(members) + 1)} for hid in members}
        for outcome in outcomes:
            probability = outcome.get("probability") if isinstance(outcome, dict) else None
            mask = outcome.get("crit_mask") if isinstance(outcome, dict) else None
            rows = outcome.get("ranking") if isinstance(outcome, dict) else None
            if (not _number(probability) or not 0 <= probability <= 1 or type(mask) is not int
                    or not 0 <= mask < len(outcomes) or mask in masks or not isinstance(rows, list)
                    or len(rows) != len(members) or {r.get("hit_id") for r in rows if isinstance(r, dict)} != set(members)):
                raise ValueError("特殊贡献联合分布身份、概率或成员不一致。")
            masks.add(mask)
            probability_sum += probability
            for rank, row in enumerate(rows, 1):
                if (not _number(row.get("unweighted_damage")) or row["unweighted_damage"] < 0
                        or row.get("rank") != rank or row.get("contributor_id") != by_id[row["hit_id"]].get("character")
                        or not _number(row.get("weight")) or not 0 < row["weight"] <= 1
                        or not _close(row.get("weighted_damage"), row["unweighted_damage"] * row["weight"])):
                    raise ValueError("特殊贡献联合分布乘区不一致。")
                weighted[row["hit_id"]].append(probability * row["weighted_damage"])
                shares[row["hit_id"]].append(probability * row["weight"])
                ranks[row["hit_id"]][str(rank)].append(probability)
            if not _close(outcome.get("total"), sum(r["weighted_damage"] for r in rows)):
                raise ValueError("特殊贡献联合分布小计不一致。")
        if not _close(probability_sum, 1) or any(not _close(math.fsum(weighted[hid]), results[hid]["expected"]) for hid in members):
            raise ValueError("特殊贡献联合分布与期望结果不一致。")
        for row in ranking:
            hid = row["hit_id"]
            if (not _close(row.get("expected_weight_share"), math.fsum(shares[hid]))
                    or any(not _close(row["rank_probabilities"][rank], math.fsum(probabilities))
                           for rank, probabilities in ranks[hid].items())
                    or not _close(row["unweighted_expected"] * row["result_weight"], results[hid]["expected"])):
                raise ValueError("特殊贡献排名概率、分配份额与实际投影不一致。")
        group_rows.append({"identity": identity, "hit_ids": members,
                           "actors": sorted({by_id[hid]["character"] for hid in members}),
                           "dependency_scope": "unknown" if declared_mechanics or group.get("policy") not in {
                               "stellar_swirl_role_slots_then_independent_actual_damage", "independent_crit_then_actual_damage_desc"}
                           else "resolved_numeric"})
    unknown = False
    for hit in hits:
        meta = (hit.get("extensions") or {}).get("special_reaction") or {}
        semantics = meta.get("result_weight_semantics")
        if semantics == "expected_equivalent_projection" and hit.get("id") not in mapped:
            raise ValueError("命中声明了特殊派生权重，但缺少对应贡献组执行证明。")
        if semantics is not None and semantics != "expected_equivalent_projection":
            unknown = True
    return {"groups": group_rows, "hit_group": mapped,
            "readonly_all": unknown or any(row["dependency_scope"] == "unknown" for row in group_rows),
            "has_derived_weights": bool(groups), "unknown_provenance": unknown}
