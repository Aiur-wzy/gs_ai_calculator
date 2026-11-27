"""Read-only evidence captured by the actual damage pipeline, never a solver.

Tables are content addressed and shared between hits. Capacity omissions happen
before sealing, with no dangling references; they do not reject numeric damage.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

CONTRACT = "damage-explanation-v1"
# Match the existing attribute evidence envelope; this is an optional evidence
# budget, not an action, numeric, model-context or transport admission limit.
DEFAULT_MAX_BYTES = 32 * 1024 * 1024
TABLES = ("sources", "panels", "modifiers", "formulas")
HIT_DISPLAY_FIELDS = frozenset({"description", "hit_name", "name"})


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def new_document():
    return {"contract": CONTRACT, "schema_version": 1, "status": "complete",
            "coverage": {"scope": "reported_numeric_events_only", "eligible": 0,
                         "explained": 0, "omitted": 0, "reasons": []},
            **{name: {} for name in TABLES}, "events": {}}


def consumed_inputs(hit, ctx, raw_crit_rate):
    """Select existing resolved scalars, without reading any new attribute."""
    system = hit["damage_system"]
    reaction = (hit.get("reaction") or {}).get("type")
    scaling = system == "classic" or system in ("lunar", "stellar") and not ctx["reaction_base"]
    keys = {"resistance"}
    if scaling:
        keys.update(("multiplier", "scaling"))
    if system == "classic":
        keys.update(("level", "enemy_level", "def_reduction", "def_ignore", "dmg_bonus", "base_add", "base_mult"))
        if reaction == "amplify":
            keys.add("amplify_factor")
    else:
        keys.update(("system_factor", "reaction_flat", "reaction_bonus", "em", "reaction_base"))
        if not scaling:
            keys.add("level")
        if system in ("lunar", "stellar"):
            keys.update((f"{system}_base", f"{system}_mult", f"{system}_elevate"))
    if system != "classic" or reaction in ("amplify", "aggravate", "spread") or scaling and hit.get("scaling_stat", "atk") == "em":
        keys.add("em")
    if reaction in ("amplify", "aggravate", "spread"):
        keys.add("reaction_bonus")
    values = {key: ctx[key] for key in sorted(keys) if key in ctx}
    if system != "transformative":
        values.update(crit_rate=ctx["crit_rate"], crit_dmg=ctx["crit_dmg"])
        values["crit_rate_raw"] = raw_crit_rate
        values["crit_rate_effective"] = min(1.0, ctx["crit_rate"])
    return values


def normalization(hit, ctx, recipe):
    system = hit["damage_system"]
    point = "base_after_flat" if system == "classic" else "before_reaction_flat"
    value = recipe["checkpoints"].get(point)
    reference = ctx.get("scaling")
    base = {"status": "not_applicable", "reference_stat": hit.get("scaling_stat", "atk"),
            "reference_value": reference, "checkpoint": point,
            "includes": (["base_mult", "base_add"] if system == "classic" else
                         ["system_factor", "base_bonus", "em_zone", "big_mult"]),
            "excludes": (["aggravate_flat", "downstream_zones"] if system == "classic" else
                         ["reaction_flat", "resistance", "elevate", "crit_expected"]),
            "meaning": "只读参考属性折算；不是原技能倍率，不回写或再次参与计算。"}
    if system == "transformative" or ctx.get("reaction_base"):
        return {**base, "reason": "level_based_reaction_has_no_scaling_reference"}
    if not isinstance(reference, (int, float)) or reference == 0 or value is None:
        return {**base, "reason": "zero_or_unavailable_reference"}
    ratio = value / reference
    if not math.isfinite(ratio):
        return {**base, "reason": "normalization_not_finite"}
    return {**base, "status": "available", "value": ratio, "checkpoint_value": value}


class ExplanationBuilder:
    def __init__(self, max_bytes=DEFAULT_MAX_BYTES):
        self.document = new_document()
        self.max_bytes = max_bytes
        self.bytes = len(encoded(self.document))
        self.occurrences = {}

    def add_hit(self, hit, ctx, raw_crit_rate, effects, recipe, sampled_at, outcome, base_stats):
        document = self.document
        document["coverage"]["eligible"] += 1
        source = {key: hit[key] for key in ("character", "skill", "hit_name", "element", "damage_system", "damage_type_ids") if key in hit}
        source.update(action_id=None, ability_id=None, component_id=None,
                      lineage_status="declared_hit_only")
        special_reaction = (hit.get("extensions") or {}).get("special_reaction")
        coefficient = special_reaction.get("reaction_coefficient") if isinstance(special_reaction, dict) else None
        if isinstance(coefficient, dict) and coefficient.get("effective_factor") == ctx.get("system_factor"):
            source["reaction_coefficient"] = copy.deepcopy(coefficient)
        # Legacy inputs have no event ID. Keep their fallback stable across
        # pure display edits; retain every calculation/association field,
        # including legacy ``skill`` filters. Duplicate semantic occurrences
        # still receive separate keys below, without using a display label.
        identity = hit.get("id") or "legacy:" + digest({
            key: value for key, value in hit.items() if key not in HIT_DISPLAY_FIELDS})
        occurrence = self.occurrences.get(identity, 0)
        self.occurrences[identity] = occurrence + 1
        event_key = "hit:" + identity if occurrence == 0 else f"hit:{identity}:occurrence:{occurrence}"
        values = consumed_inputs(hit, ctx, raw_crit_rate)
        panel = {"character": hit["character"], "domain": "self",
                 "sample_t": sampled_at, "sampling": "core_resolved_input",
                 "scaling_stat": hit.get("scaling_stat", "atk"), "inputs": values,
                 "unconsumed_attributes": "not_evaluated_for_explanation"}
        baseline_keys = {hit.get("scaling_stat", "atk")} if "scaling" in values else set()
        if "scaling" in values and any(e["zone"] == "stat_pct" and e["stat"] == hit.get("scaling_stat", "atk") for e in effects):
            baseline_keys.add("base_" + hit.get("scaling_stat", "atk"))
        baseline_keys.update(key for key in ("em", "crit_rate", "crit_dmg", "dmg_bonus") if key in values)
        panel["baseline"] = {key: base_stats[key] for key in sorted(baseline_keys) if key in base_stats}
        if isinstance(panel["baseline"].get("dmg_bonus"), dict):
            panel["baseline"]["dmg_bonus"] = {key: value for key, value in panel["baseline"]["dmg_bonus"].items()
                                               if key in ("all", hit["element"])}
        additions = {name: {} for name in TABLES}

        def intern(table, value):
            key = digest(value)
            if key not in document[table]:
                additions[table][key] = value
            return key

        source_ref, panel_ref = intern("sources", source), intern("panels", panel)
        formula = {key: recipe[key] for key in ("kind", "version", "expression")}
        formula_ref = intern("formulas", formula)
        modifiers = [intern("modifiers", effect) for effect in effects]
        event = {"event_id": hit.get("id"), "t": hit["t"], "character": hit["character"],
                 "skill": hit["skill"], "source_ref": source_ref, "panel_ref": panel_ref,
                 "modifier_refs": modifiers, "formula_ref": formula_ref,
                 "checkpoints": recipe["checkpoints"], "zones": recipe["zones"],
                 "normalization": normalization(hit, ctx, recipe),
                 "outcome": {"origin": "engine_formula", **outcome}, "evidence": {}}
        cost = len(encoded(event)) + len(encoded(additions)) + len(event_key) + 16
        if self.bytes + cost > self.max_bytes:
            document["status"] = "partial"
            document["coverage"]["omitted"] += 1
            document["coverage"]["reasons"] = ["explanation_capacity"]
            return None
        for name in TABLES:
            document[name].update(additions[name])
        document["events"][event_key] = event
        document["coverage"]["explained"] += 1
        self.bytes += cost
        return event_key

    def finish(self):
        self.document["budget"] = {"maximum_bytes": self.max_bytes, "accounted_bytes": self.bytes,
                                   "scope": "optional_explanation_evidence"}
        return self.document
