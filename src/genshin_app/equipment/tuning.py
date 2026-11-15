"""Compile optional, rotation-local associations. No mechanism inference occurs here."""
from __future__ import annotations

import copy

from genshin_engine.registry import effect_matches_hit

from .files import context_view, fingerprint
from .schema import PlanError


def tuning_source_fingerprint(sources, versions, records, context, source_fingerprint):
    """Separate match dependencies from complete, acknowledgement-bound audits.

    Native semantic context is independently included by each match compiler.
    Preserve actual compiled scope/parents/rules/template here, but do not feed
    the complete input-plan audit hash (including this match's confirmation)
    back into its own approval basis. Saved record/source hashes stay untouched.
    """
    typed = [row for row in sources if row.get('kind') == 'mechanism']
    if not typed:
        if any(record['status'] == 'ready' for record in records):
            raise PlanError('通用机制就绪记录缺少对应微调来源', 'invalid_tuning_dependencies')
        return source_fingerprint
    from .mechanism_source_schema import CAST_COMPILER, COMPILER, CONTACT_COMPILER
    fields = {'kind', 'id', 'source_fingerprint', 'accepted_fingerprint', 'execution_fingerprint'}
    by_id = {record['id']: record for record in records}
    if (len(by_id) != len(records) or len({row['id'] for row in typed}) != len(typed)
            or {row['id'] for row in typed} != set(by_id) or context is None):
        raise PlanError('微调依赖必须与本次通用机制来源一一对应', 'invalid_tuning_dependencies')
    dependencies = []
    for row in sources:
        if row.get('kind') != 'mechanism':
            dependencies.append(row)
            continue
        record = by_id[row['id']]
        if (set(row) != fields or record['status'] != 'ready'
                or record['compiler'] not in (COMPILER, CAST_COMPILER, CONTACT_COMPILER)
                or any(row[key] != record[key] for key in fields - {'kind'})):
            raise PlanError('微调依赖与通用机制实际编译材料不一致', 'invalid_tuning_dependencies')
        execution = fingerprint({'contract': 'mechanism-tuning-execution-dependencies-v1',
            'compiler': record['compiler'], 'scope': record['scope'], 'parent_graph': record['parent_graph'],
            'rules': record['rules'], 'initial': record['initial_rules'], 'template': context['template']})
        dependencies.append({**row, 'execution_fingerprint': execution})
    return fingerprint({'contract': 'tuning-source-dependencies-v1',
                        'sources': sorted(dependencies + versions, key=fingerprint)})


def generated_id(kind, binding_id, part_id):
    # Hash the tuple so dots, slashes, or another binding's names cannot collide.
    return f"generated.{kind}." + fingerprint([binding_id, part_id])[:32]


def _stable(value):
    if isinstance(value, dict):
        return {k: sorted(v) if k in ("damage_type_ids", "hit_ids", "include_hit_ids", "exclude_hit_ids")
                and isinstance(v, list) else _stable(v)
                for k, v in value.items() if k not in ("hit_name", "name", "label")}
    if isinstance(value, list):
        result = [_stable(v) for v in value]
        # Only collections with stable identities are order-insensitive. Switches and
        # ordinary arrays preserve their execution/source order.
        for key in ("id", "binding_id", "part_id", "contribution_id", "condition_id"):
            if result and all(isinstance(v, dict) and key in v for v in result):
                return sorted(result, key=lambda v: v[key])
        return result
    return value


def compile_matches(template, plan, source_fingerprint, missing, assumptions, *, context_plan=None):
    hits = {h["id"]: h for h in template["hits"]}
    effects = {(b["id"], e["id"]): (b, e) for b in template.get("buffs", []) for e in b["effects"]}
    original_effects = copy.deepcopy(effects)
    matches = plan.get("rotation_tuning", {}).get("buff_hit_matches", [])
    previews = []
    for match in matches:
        ref = match["effect_ref"]
        key = (ref["buff_id"], ref["effect_id"])
        if key not in effects:
            # A real binding can temporarily have no output (unreviewed/source unavailable).
            candidates = [generated_id("buff", b["binding_id"], p["part_id"])
                          for b in plan["effect_bindings"] for p in b["parts"]
                          if p.get("delivery") and p["delivery"]["kind"] == "buff"
                          and p["delivery"]["effect"]["id"] == ref["effect_id"]]
            candidates += [generated_id("buff", b["binding_id"], "upstream") for b in plan["effect_bindings"]
                           if "upstream_recipe" in b]
            if any(ref["buff_id"] == bid or ref["buff_id"].startswith(bid + ".interval.") for bid in candidates):
                missing("effect_pending", f"rotation_tuning.buff_hit_matches.{match['id']}",
                        "来源效果尚未生成，补齐对应部件后再复核关联", "review_match")
                previews.append({"id": match["id"], "effect_ref": ref, "basis_fingerprint": None,
                                 "base_hit_ids": None, "final_hit_ids": None,
                                 "added_hit_ids": [], "removed_hit_ids": []})
                continue
            raise PlanError(f"轴微调 {match['id']} 引用不存在的 Buff/收益")
        _, effect = effects[key]
        buff, original_effect = original_effects[key]
        requested = set(match.get("hit_ids", []) + match.get("include_hit_ids", []) + match.get("exclude_hit_ids", []))
        if not requested <= hits.keys():
            raise PlanError(f"轴微调 {match['id']} 引用不存在的伤害事件", details=sorted(requested - hits.keys()))
        scope = original_effect.get("applies_to")
        base = None if scope is None or any(h.get("damage_type_ids") is None for h in hits.values()) else {
            hid for hid, hit in hits.items() if effect_matches_hit(original_effect, hit)}
        final = set(match["hit_ids"]) if match["mode"] == "replace" else (
            (base | set(match["include_hit_ids"])) - set(match["exclude_hit_ids"]) if base is not None else None)
        basis_template = copy.deepcopy(plan["manual_template"])
        basis_template["hits"] = copy.deepcopy(template["hits"])
        for hit in basis_template["hits"]:
            if hit.get("damage_type_ids"):
                hit["damage_type_ids"] = sorted(hit["damage_type_ids"])
        for candidate_buff in basis_template.get("buffs", []):
            for candidate_effect in candidate_buff["effects"]:
                selector = candidate_effect.get("applies_to")
                if selector:
                    for field in ("hit_ids", "damage_type_ids"):
                        if field in selector:
                            selector[field] = sorted(selector[field])
        # Use the original plan, not previously compiled matches, to avoid ordering dependencies.
        original = context_plan or plan
        extra_context = ({"rotation_model": original.get("rotation_model"),
                          "mechanics_model": original.get("mechanics_model")} if original["plan_version"] == 3 else {})
        basis = fingerprint({"source": source_fingerprint, "context": _stable(context_view({
            "manual_template": {**plan["manual_template"], "hits": basis_template["hits"]},
            "equipment_inputs": original["equipment_inputs"], "effect_bindings": original["effect_bindings"], **extra_context})),
            "buff": _stable(buff), "effect": _stable(original_effect), "match": _stable(context_view(match))})
        preview = {"id": match["id"], "effect_ref": ref, "base_hit_ids": sorted(base) if base is not None else None,
                   "final_hit_ids": sorted(final) if final is not None else None, "basis_fingerprint": basis,
                   "added_hit_ids": sorted(final - base) if base is not None and final is not None else [],
                   "removed_hit_ids": sorted(base - final) if base is not None and final is not None else []}
        previews.append(preview)
        if match["state"] != "confirmed" or match["basis_fingerprint"] != basis or final is None:
            missing("dependency_stale" if match["state"] == "confirmed" else "missing_input",
                    f"rotation_tuning.buff_hit_matches.{match['id']}", "轴关联需要按当前候选和来源确认", "review_match")
            continue
        effect["applies_to"] = {"mode": "hits", "hit_ids": sorted(final)} if final else {"mode": "none"}
        if not final:
            effect.setdefault("extensions", {})["exclusion_reason"] = match["reason"]
        assumptions.append(f"轴微调 {match['id']}: {match['reason']}")
    return previews
