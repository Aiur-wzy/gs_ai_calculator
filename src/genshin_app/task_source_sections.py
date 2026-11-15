"""Index existing source boundaries without inferring distinct game effects."""
from __future__ import annotations


def source_blocks(value):
    rows = []

    def add(node, pointer, kind, keys, *, extra_identity=None):
        if not isinstance(node, dict):
            return
        identity = {key: node[key] for key in keys if node.get(key) is not None}
        identity.update(extra_identity or {})
        rows.append({"kind": kind, "pointer": pointer, "identity": identity,
                     "text_pointers": [f"{pointer}/{key}" for key in ("description", "desc_template", "content")
                                       if isinstance(node.get(key), str) and node[key]],
                     "effect_coverage": "unspecified"})

    def visit(node, path):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("data"), dict) and "entity_type" in node:
            visit(node["data"], path + "/data")
        for group, kind in (("skills", "skill"), ("talents", "talent"), ("extra_info", "subskill_or_passive"),
                            ("annotations", "annotation")):
            for index, item in enumerate(node.get(group, []) if isinstance(node.get(group), list) else []):
                add(item, f"{path}/{group}/{index}", kind, ("id", "proud_skill_group_id", "info_type", "status", "level"))
        for index, effect in enumerate(node.get("effects", []) if isinstance(node.get("effects"), list) else []):
            if not isinstance(effect, dict):
                continue
            pointer = f"{path}/effects/{index}"
            refinements = effect.get("refinements")
            if isinstance(refinements, list):
                for rank, entry in enumerate(refinements):
                    add(entry, f"{pointer}/refinements/{rank}", "weapon_refinement", ("refinement", "affix_id"))
            elif isinstance(effect.get("effect"), dict):
                add(effect["effect"], f"{pointer}/effect", "set_threshold", ("affix_id",),
                    extra_identity={"required_count": effect.get("required_count")})
            else:
                add(effect, pointer, "equipment_effect", ("required_count", "affix_id"))
        for index, item in enumerate(node.get("items", []) if isinstance(node.get("items"), list) else []):
            visit(item, f"{path}/items/{index}")

    visit(value, "")
    return rows
