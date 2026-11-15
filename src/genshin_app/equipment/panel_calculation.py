"""Shared static panel arithmetic and character-basis projection.

This layer does no I/O and owns neither the plan nor any database transaction.
"""

from __future__ import annotations

import copy
import math

from genshin_db.extractor import ASCENSION_PROP_CN

from .files import fingerprint
from .schema import STATS, PlanError

PROP_MAP = {
    "FIGHT_PROP_BASE_ATTACK": ("base_atk", "flat"),
    "FIGHT_PROP_ATTACK": ("atk", "flat"), "FIGHT_PROP_ATTACK_PERCENT": ("atk", "percent"),
    "FIGHT_PROP_HP": ("hp", "flat"), "FIGHT_PROP_HP_PERCENT": ("hp", "percent"),
    "FIGHT_PROP_DEFENSE": ("def", "flat"), "FIGHT_PROP_DEFENSE_PERCENT": ("def", "percent"),
    "FIGHT_PROP_CRITICAL": ("crit_rate", "flat"), "FIGHT_PROP_CRITICAL_HURT": ("crit_dmg", "flat"),
    "FIGHT_PROP_ELEMENT_MASTERY": ("em", "flat"), "FIGHT_PROP_CHARGE_EFFICIENCY": ("energy_recharge", "flat"),
    "FIGHT_PROP_ADD_HURT": ("dmg_bonus:all", "flat"),
    **{f"FIGHT_PROP_{source}_ADD_HURT": (f"dmg_bonus:{element}", "flat") for source, element in (
        ("PHYSICAL", "physical"), ("FIRE", "pyro"), ("WATER", "hydro"), ("ELEC", "electro"),
        ("ICE", "cryo"), ("WIND", "anemo"), ("ROCK", "geo"), ("GRASS", "dendro"))},
}
ASCENSION_MAP = {cn: PROP_MAP[prop] for prop, cn in ASCENSION_PROP_CN.items() if prop in PROP_MAP}


def character_source(meta, cid, line, level, character):
    # Character provenance belongs to the actual character snapshot, not an equipment label.
    refs = {"kind": "database", "character_id": cid, "version_line": line, "level": level,
            "upstream_commit": meta.get(f"upstream_commit:{line}", meta.get("upstream_commit")),
            "built_at": meta.get(f"built_at:{line}", meta.get("built_at")),
            "fields": [f"{s}_{level}" for s in ("atk", "hp", "def")] +
                      ["crit_rate", "crit_dmg", "charge_efficiency", "ascension_prop", "ascension_value"]}
    refs["value_fingerprint"] = fingerprint(character)
    return refs


def build_character_basis(meta, detail, cid, level, line):
    character = detail["character"]
    source = character_source(meta, cid, line, level, character)
    ascension = []
    prop, value = character.get("ascension_prop"), character.get("ascension_value")
    mapped = PROP_MAP.get(prop) or ASCENSION_MAP.get(prop)
    if prop and value is not None and mapped:
        stat, operation = mapped
        ascension.append({"contribution_id": "character.ascension", "stat": stat,
                          "operation": operation, "value": value, "source_refs": [source]})
    basis = {"mode": "database", **{f"character_base_{s}": character.get(f"{s}_{level}")
             if level in (90, 100) else None for s in ("atk", "hp", "def")},
             "crit_rate": character.get("crit_rate"), "crit_dmg": character.get("crit_dmg"),
             "energy_recharge": character.get("charge_efficiency"), "em": 0,
             "dmg_bonus": {}, "ascension_contributions": ascension, "source_refs": [source]}
    if prop and not mapped and value:
        basis["source_refs"][0]["unsupported_ascension_prop"] = prop
    return basis


def calculate_static_panel(base, contributions, *, allow_unknown=False):
    out = {s: base[s] for s in STATS}
    out["dmg_bonus"] = copy.deepcopy(base["dmg_bonus"])
    for stat in ("atk", "hp", "def"):
        unknown = base[stat] is None or any(c["value"] is None for c in contributions if c["stat"] == stat)
        if unknown and allow_unknown:
            out[stat] = None
        else:
            flat = sum(c["value"] for c in contributions if c["stat"] == stat and c["operation"] == "flat")
            percent = sum(c["value"] for c in contributions if c["stat"] == stat and c["operation"] == "percent")
            out[stat] = base[stat] * (1 + percent) + flat
    for c in contributions:
        stat = c["stat"]
        if stat.startswith("dmg_bonus:"):
            element = stat.split(":", 1)[1]
            previous = out["dmg_bonus"].get(element, 0)
            out["dmg_bonus"][element] = (None if allow_unknown and (previous is None or c["value"] is None)
                                         else previous + c["value"])
        elif stat not in ("atk", "hp", "def"):
            out[stat] = (None if allow_unknown and (out[stat] is None or c["value"] is None)
                         else out[stat] + c["value"])
    out.update({f"base_{s}": base[s] for s in ("atk", "hp", "def")})
    if any(not math.isfinite(v) for v in [*out.values(), *out["dmg_bonus"].values()]
           if isinstance(v, (float, int))):
        raise PlanError("组合面板数值溢出", "unsupported_effect")
    return out


__all__ = [
    "PROP_MAP",
    "build_character_basis",
    "calculate_static_panel",
    "character_source",
]

