"""Bounded reaction source relations; not a native ability interpreter."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib import resources

from genshin_db.runtime_attenuation import _assemble_attenuation_profile
from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    dumps,
    fingerprint,
    resolve_pointer,
    walk,
)
from genshin_runtime.normalization import Profile

REACTION_LIMITS = {"max_bytes": 131072, "max_nodes": 4096, "max_operations": 16, "max_typed_nodes": 64,
                   "max_modifiers": 2, "max_issues": 32}
REACTION_SEMANTICS = "ordinary-swirl-origin-damage-source-v1"
STAR_ABILITIES = ("ElementAbility_StarSwirl_Ice", "ElementAbility_StarSwirl_Ice_Gadget")


_LEGACY_COMMIT = "224708edc8056f27e9ffc4eacabe797d75274636"
_F11_COMMIT = "f11cf545a7eee32a29999f960ebfba305f896ed3"
_NOV18_COMMIT = "6044f5aae7dd79fbbe15c5748ef99b654f6132b7"
_RECIPES = {
    _LEGACY_COMMIT: "reaction_damage_profiles.json",
    _F11_COMMIT: "reaction_damage_f11cf545_profile.json",
    _NOV18_COMMIT: "reaction_damage_f11cf545_profile.json",
}


@lru_cache(maxsize=6)
def _recipe(element=None, *, commit=_LEGACY_COMMIT):
    filename = _RECIPES.get(commit)
    if filename is None:
        raise RuntimeConfigError("当前提交没有已审定的普通扩散资料提取配方", "unsupported_source", 409)
    path = resources.files("genshin_runtime").joinpath("resources", filename)
    recipe = json.loads(path.read_text(encoding="utf8"))
    if commit == _NOV18_COMMIT and recipe["commit"] == _F11_COMMIT:
        # ConfigAbility_Element.json is byte-identical in both Git trees.
        # All whole-node hash checks still apply to the selected database.
        recipe["evidence_reference"]["identical_source_commit"] = _F11_COMMIT
        recipe.update(commit=commit, recipe_id="ordinary-swirl-origin-source-6044f5aa-v3")
    if recipe["commit"] != commit:
        raise RuntimeConfigError("普通扩散资料提取配方与精确提交不匹配", "unsupported_source", 409)
    if element == "pyro" and commit == _LEGACY_COMMIT:
        extension_path = resources.files("genshin_runtime").joinpath("resources/reaction_damage_pyro_profile.json")
        extension = json.loads(extension_path.read_text(encoding="utf8"))
        if extension["base_recipe_id"] != recipe["recipe_id"] or extension["commit"] != recipe["commit"]:
            raise RuntimeConfigError("火扩散配方与基础映射版本不匹配，需重新审定", "unsupported_source", 409)
        # The legacy recipe identity and fingerprints remain unchanged. Fire
        # has its own whole-node evidence; equal damage sequences are not proof.
        recipe.update(recipe_id=extension["recipe_id"], recipe_version=extension["recipe_version"],
                      elements={"pyro": extension["element"]})
        recipe["evidence_reference"]["pyro_scope_evidence"] = extension["evidence"]
    return recipe


def reaction_damage_capabilities():
    return {"profile_version": 1, "reaction_types": ["swirl"], "elements": ["hydro", "cryo", "pyro"],
            "semantics": REACTION_SEMANTICS, "limits": dict(REACTION_LIMITS),
            "selection": "按精确能力身份及已审定提交/节点指纹读取普通原目标伤害引用",
            "execution": "只投影独立二元damageSequence及计时，整能力/owner/调度未执行",
            "unsupported": ["Alter", "Star", "propagation", "native_attacker_defaults"]}


def _unique_ability(conn, state, name, source_path):
    from genshin_db.runtime_queries import _lookup

    rows = _lookup(conn, state["version_line"], "ability", name, source_path)
    if len(rows) != 1:
        raise RuntimeConfigError("扩散能力不存在或不唯一", "not_found" if not rows else "ambiguous_reference",
                                 404 if not rows else 409, {"ability_name": name, "count": len(rows)})
    return rows[0]


def _origin(nodes, row):
    raw, origin = nodes.node(row)
    return raw, {**origin, "definition_id": row["definition_id"], "node_sha256": row["node_sha256"]}


def _scoped_profile(nodes, recipe):
    # This mapping is intentionally local: the historical evidence does not
    # establish the same tokens' semantics in arbitrary ability contexts.
    return Profile(id=recipe["recipe_id"], commits=(recipe["commit"],),
                   fields={**nodes.normalization.fields, **recipe["fields"]},
                   types={**nodes.normalization.types, **recipe["types"]})


def get_runtime_reaction_damage_profile(db_path, *, element, reaction_type="swirl",
                                       version_line="release", snapshot_id=None):
    from genshin_db.runtime_queries import _Nodes, _read

    if reaction_type != "swirl" or element not in ("hydro", "cryo", "pyro"):
        raise ValueError("reaction_damage_profile仅支持swirl的hydro/cryo/pyro普通原目标资料")
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        recipe = _recipe("pyro" if element == "pyro" else None, commit=state["upstream_commit"])
        selected = recipe["elements"][element]
        nodes = _Nodes(conn, version_line)
        row = _unique_ability(conn, state, selected["ability_name"], recipe["source_path"])
        raw, ability_source = _origin(nodes, row)
        if row["node_sha256"] != selected["expected_node_sha256"]:
            raise RuntimeConfigError("普通扩散能力节点已变化，需重新审定字段与分支", "unsupported_source", 409,
                                     {"source": ability_source, "recipe_id": recipe["recipe_id"]})
        if sum(1 for _ in walk(raw)) > REACTION_LIMITS["max_nodes"]:
            raise RuntimeConfigError("扩散能力节点预算超限", "response_too_large", 413)
        profile = _scoped_profile(nodes, recipe)
        view = profile.normalize(raw, kind="ability")
        modifiers = view["modifiers"]
        typed_nodes = [v for _, v in walk(view) if isinstance(v, dict) and "$type" in v]
        operations = len(view["onAbilityStart"]) + sum(
            len(modifier.get("onAdded", [])) + len(modifier.get("modifierMixins", []))
            for modifier in modifiers.values())
        # VFX/shape nodes count too: never hide nested operation growth.
        if (len(modifiers) > REACTION_LIMITS["max_modifiers"] or operations > REACTION_LIMITS["max_operations"]
                or len(typed_nodes) > REACTION_LIMITS["max_typed_nodes"]):
            raise RuntimeConfigError("扩散能力结构预算超限", "response_too_large", 413)
        mod = selected["modifier_name"]
        ordinary = modifiers[mod]
        origins = [(i, action) for i, action in enumerate(ordinary["onAdded"])
                   if action.get("$type") == "DamageByAttackValue"]
        if len(origins) != 1:
            raise RuntimeConfigError("普通扩散原目标攻击未唯一装配", "unsupported_source", 409)
        index, attack = origins[0]
        attack_canonical = f"/modifiers/{mod}/onAdded/{index}"

        def fragment(canonical):
            relative = profile.raw_pointer(raw, canonical, kind="ability")
            node = resolve_pointer(raw, relative)
            return {"owner_definition_id": row["definition_id"], "source_path": row["source_path"],
                    "source_sha256": ability_source["source_sha256"],
                    "json_pointer": row["json_pointer"] + relative, "node_sha256": fingerprint(node), "raw": node}

        group = attack["attackInfo"]["attenuationGroup"]
        attenuation = _assemble_attenuation_profile(conn, state, nodes, group, "damage")
        origin = fragment(attack_canonical)
        group_origin = fragment(attack_canonical + "/attackInfo/attenuationGroup")
        expression = attack["attackInfo"]["attackProperty"]["damageExtra"]
        specials = view["abilitySpecials"]
        raw_nodes = {"entries": fragment("/onAbilityStart"), "ordinary_modifier": fragment("/modifiers/" + mod),
                     "alter_modifier": fragment("/modifiers/" + mod + "_Alter"),
                     "ability_specials": fragment("/abilitySpecials"),
                     "attenuation": {**attenuation["source"], "raw": attenuation["raw_record"]}}
        separate_star = []
        for name in STAR_ABILITIES:
            star_row = _unique_ability(conn, state, name, recipe["source_path"])
            _, source = _origin(nodes, star_row)
            separate_star.append({**source, "ability_name": name, "execution": "not_evaluated"})
        sequence_status = attenuation["execution"]["status"]
        issues = [*attenuation["execution"]["issues"],
                  {"code": "native_attacker_context_not_evaluated"},
                  {"code": "base_reaction_damage_injection_unknown"},
                  {"code": "native_branch_defaults_not_evaluated"},
                  {"code": "alter_unsupported"}, {"code": "star_unsupported"},
                  {"code": "propagation_unsupported"}]
        if "attacker" not in attack:
            issues.append({"code": "missing_attacker_default"})
        if len(issues) > REACTION_LIMITS["max_issues"]:
            raise RuntimeConfigError("扩散资料问题回执超限", "response_too_large", 413)
        projection = {**attenuation["projection"], "semantics": REACTION_SEMANTICS,
                      "sequence_semantics": attenuation["projection"]["semantics"], "group": group,
                      "attacker": {"present": "attacker" in attack, "value": attack.get("attacker"),
                                   "default": "not_inferred"},
                      "factor": {"raw": specials[expression[1]], "execution": "not_compiled"},
                      "base_reaction_damage": {"raw_initial": specials[expression[0]], "runtime_injection": "unknown"}}
        extraction_identity = {"recipe_id": recipe["recipe_id"], "recipe_version": recipe["recipe_version"],
                               "commit": recipe["commit"], "mapping_fingerprint": profile.mapping_fingerprint,
                               "expected_node_sha256": selected["expected_node_sha256"]}
        result = {"profile_version": 1, "status": "ready", "snapshot": state,
                  "selection": {"reaction_type": reaction_type, "element": element, "branch": "ordinary", "scope": "origin_target"},
                  "sources": {"ability": ability_source, "origin_attack": {k: v for k, v in origin.items() if k != "raw"},
                              "group_reference": group_origin, "attenuation": attenuation["source"]},
                  "projection": projection,
                  "execution": {"sequence_status": sequence_status, "ability_execution": "pending",
                                "owner_resolution": "not_evaluated", "reaction_dispatch": "not_evaluated",
                                "semantics": "source_relation_only"},
                  "raw_nodes": raw_nodes, "separate_star_abilities": separate_star,
                  "mapping_evidence": {**extraction_identity,
                      **copy.deepcopy({k: recipe[k] for k in ("evidence_reference", "field_evidence", "type_evidence")})},
                  "issues": issues, "limits": dict(REACTION_LIMITS)}
        result["content_fingerprint"] = fingerprint({
            "profile_version": 1, "selection": result["selection"], "snapshot_id": state["snapshot_id"],
            "sources": result["sources"], "separate_star_abilities": separate_star,
            "extraction": extraction_identity, "projection": projection, "execution": result["execution"],
        })
        if len(dumps(result).encode("utf8")) > REACTION_LIMITS["max_bytes"]:
            raise RuntimeConfigError("扩散资料响应字节数超限", "response_too_large", 413)
        # Do not expose the borrowed read-session cache to caller mutation.
        return copy.deepcopy(result)
