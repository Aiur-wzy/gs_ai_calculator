"""Versioned scalar attribute semantics, without character or source authority."""
from __future__ import annotations

import copy

CONTRACT = "attribute-ledger-v4"
STATS = ("atk", "hp", "def", "em", "energy_recharge")
BASE_STATS = ("atk", "hp", "def")
ALIASES = {"base_" + stat: (stat, "base") for stat in BASE_STATS}
DESCRIPTORS = {
    stat: {"unit": "ratio" if stat == "energy_recharge" else "flat",
           "basis": ["total", "nonextra", "base"],
           "base_source": "resolved_panel.base_" + stat if stat in BASE_STATS else "explicit_source_fact_only",
           "percentage_application": stat in BASE_STATS,
           "meaning": "maximum_hp_panel" if stat == "hp" else "1.0_is_100_percent" if stat == "energy_recharge" else "panel_attribute"}
    for stat in STATS
}


def canonical_read(stat, basis="total"):
    return ALIASES.get(stat, (stat, basis))


def attribute_stat_definitions():
    return {"contract": CONTRACT, "stats": copy.deepcopy(DESCRIPTORS),
            "aliases": {key: {"stat": stat, "basis": basis} for key, (stat, basis) in ALIASES.items()},
            "source_authority": "none", "classification": "explicit_per_source_nonextra_not_inferred",
            "percentage_order": "clamp_ratio_then_multiply_immutable_recipient_base_once",
            "ordinary_projection": "damage_context_is_not_shareable_panel"}


def effect_output(effect, *, kind):
    """Infer dimensions from the existing actual benefit registry, not user tags."""
    from genshin_engine.attribute_ledger import AttributeLedgerError
    from genshin_engine.registry import BENEFIT_TYPES
    if kind not in ("rule", "manual_buff"):
        raise AttributeLedgerError("attribute_component_reference", "未知属性来源类型")
    if kind == "rule" and effect.get("benefit_type_id") == "benefit.energy_recharge.all":
        return {"stat": "energy_recharge", "value_unit": "ratio", "application": {"kind": "flat"}}
    info = BENEFIT_TYPES.get(effect.get("benefit_type_id"))
    if info is None:
        raise AttributeLedgerError("attribute_component_reference", "来源不是已知收益类型")
    zone, stat = info["zone"], info["stat"]
    if kind == "manual_buff" and (effect.get("zone") != zone or effect.get("stat") != stat):
        raise AttributeLedgerError("attribute_component_reference", "手工收益的真实乘区／属性不一致")
    if zone == "em":
        return {"stat": "em", "value_unit": "flat", "application": {"kind": "flat"}}
    if zone in ("stat_add", "stat_pct") and stat in BASE_STATS:
        return {"stat": stat, "value_unit": "ratio" if zone == "stat_pct" else "flat",
                "application": {"kind": "recipient_base_fraction" if zone == "stat_pct" else "flat"}}
    return None
