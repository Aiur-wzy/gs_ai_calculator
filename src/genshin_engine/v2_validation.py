"""v2 附加契约校验，不修改输入；基础数值校验仍复用 template.py。"""

from __future__ import annotations

from genshin_engine.registry import BENEFIT_TYPES, DAMAGE_TYPES, ID_REGISTRY_VERSION


def _keys(obj: dict, allowed: set[str], path: str, errors: list[str]) -> None:
    for key in obj:
        if key not in allowed:
            errors.append(f"{path}.{key}: 未知字段（计算字段不能静默忽略）")
    if "extensions" in obj and not isinstance(obj["extensions"], dict):
        errors.append(f"{path}.extensions: 必须为 JSON 对象")


def _text(value: object, path: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}: 必须为非空字符串")
        return False
    return True


def _string_set(value: object, path: str, errors: list[str],
                allowed: set[str] | None = None) -> set[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{path}: 必须为非空、去重的字符串列表；全不匹配使用 none")
        return set()
    seen: set[str] = set()
    for i, item in enumerate(value):
        where = f"{path}[{i}]"
        if not _text(item, where, errors):
            continue
        if item in seen:
            errors.append(f"{where}: 重复 ID {item!r}")
        seen.add(item)
        if allowed is not None and item not in allowed:
            errors.append(f"{where}: 未登记或不存在的 ID {item!r}")
    return seen


def _objects(value: object):
    if isinstance(value, list):
        for i, obj in enumerate(value):
            if isinstance(obj, dict):
                yield i, obj


def _source_refs(value: object, path: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{path}: 自定义类别必须提供非空来源对象列表")
        return
    for i, ref in enumerate(value):
        if not isinstance(ref, dict) or not ref:
            errors.append(f"{path}[{i}]: 必须为非空来源对象")


def check_v2(t: dict, errors: list[str]) -> None:
    """补充版本 2 的身份、引用与字段校验，畸形值只收集领域错误。"""
    _keys(t, {"version", "form", "meta", "enemy", "characters", "switches", "buffs",
              "hits", "loop_duration", "id_registry_version", "type_definitions", "extensions"},
          "template", errors)
    version = t.get("id_registry_version")
    if type(version) is not int or version != ID_REGISTRY_VERSION:
        errors.append(f"id_registry_version: 仅支持整数 {ID_REGISTRY_VERSION}")
    known_types = set(DAMAGE_TYPES)
    definitions = t.get("type_definitions")
    if not isinstance(definitions, dict):
        errors.append("type_definitions: 必须为含 damage_types 的对象")
    else:
        _keys(definitions, {"damage_types"}, "type_definitions", errors)
        rows = definitions.get("damage_types")
        if not isinstance(rows, list):
            errors.append("type_definitions.damage_types: 必须为列表")
        else:
            for i, row in enumerate(rows):
                path = f"type_definitions.damage_types[{i}]"
                if not isinstance(row, dict):
                    errors.append(f"{path}: 必须为 JSON 对象")
                    continue
                _keys(row, {"id", "label", "description", "source_refs"}, path, errors)
                ident = row.get("id")
                if _text(ident, f"{path}.id", errors):
                    if (not ident.startswith("custom.damage.")
                            or not ident.removeprefix("custom.damage.").strip()):
                        errors.append(f"{path}.id: 自定义类别必须使用 custom.damage.<名称>")
                    if ident in known_types:
                        errors.append(f"{path}.id: 类型 ID 重复或覆盖标准 ID {ident!r}")
                    known_types.add(ident)
                _text(row.get("label"), f"{path}.label", errors)
                _text(row.get("description"), f"{path}.description", errors)
                _source_refs(row.get("source_refs"), f"{path}.source_refs", errors)

    enemy = t.get("enemy")
    if isinstance(enemy, dict):
        _keys(enemy, {"level", "resistance", "defense_reduction", "defense_ignore", "extensions"},
              "enemy", errors)
    for i, char in _objects(t.get("characters")):
        path = f"characters[{i}]"
        _keys(char, {"id", "level", "stats", "name", "description", "extensions"}, path, errors)
        stats = char.get("stats")
        if isinstance(stats, dict):
            _keys(stats, {"atk", "hp", "def", "em", "crit_rate", "crit_dmg", "energy_recharge",
                          "dmg_bonus", "base_atk", "base_hp", "base_def"}, f"{path}.stats", errors)
    for i, switch in _objects(t.get("switches")):
        _keys(switch, {"t", "character", "extensions"}, f"switches[{i}]", errors)

    hit_ids: set[str] = set()
    for i, hit in _objects(t.get("hits")):
        path = f"hits[{i}]"
        _keys(hit, {"id", "damage_type_ids", "t", "character", "skill", "hit_name", "description",
                   "multiplier", "damage_system", "element", "scaling_stat", "reaction",
                   "snapshot", "cast_t", "reaction_base", "count", "result_weight",
                   "extensions"}, path, errors)
        ident = hit.get("id")
        if _text(ident, f"{path}.id", errors):
            if ident in hit_ids:
                errors.append(f"{path}.id: hit ID 重复 {ident!r}")
            hit_ids.add(ident)
        _string_set(hit.get("damage_type_ids"), f"{path}.damage_type_ids", errors, known_types)
        if isinstance(hit.get("reaction"), dict):
            _keys(hit["reaction"], {"type", "factor", "layers"}, f"{path}.reaction", errors)

    for i, buff in _objects(t.get("buffs")):
        path = f"buffs[{i}]"
        _keys(buff, {"id", "name", "description", "source", "scope", "start", "end", "target", "effects", "extensions"},
              path, errors)
        ids: set[str] = set()
        for j, effect in _objects(buff.get("effects")):
            ep = f"{path}.effects[{j}]"
            _keys(effect, {"id", "benefit_type_id", "zone", "stat", "value", "applies_to",
                           "name", "description", "extensions"}, ep, errors)
            ident = effect.get("id")
            if _text(ident, f"{ep}.id", errors):
                if ident in ids:
                    errors.append(f"{ep}.id: 同一 Buff 内 effect ID 重复 {ident!r}")
                ids.add(ident)
            benefit = effect.get("benefit_type_id")
            if _text(benefit, f"{ep}.benefit_type_id", errors):
                entry = BENEFIT_TYPES.get(benefit)
                if entry is None:
                    errors.append(f"{ep}.benefit_type_id: 未登记的收益类型 {benefit!r}")
                elif effect.get("zone") != entry["zone"] or effect.get("stat") != entry["stat"]:
                    errors.append(f"{ep}.benefit_type_id: 必须与注册表 zone/stat 一致 "
                                  f"({entry['zone']}/{entry['stat']})")
            selector = effect.get("applies_to")
            sp = f"{ep}.applies_to"
            if not isinstance(selector, dict):
                errors.append(f"{sp}: 必须为明确选择器；未知范围不能进入 resolved")
                continue
            mode = selector.get("mode")
            if mode in ("all", "none"):
                _keys(selector, {"mode"}, sp, errors)
            elif mode == "hits":
                _keys(selector, {"mode", "hit_ids"}, sp, errors)
                _string_set(selector.get("hit_ids"), f"{sp}.hit_ids", errors, hit_ids)
            elif mode == "types":
                _keys(selector, {"mode", "damage_type_ids"}, sp, errors)
                _string_set(selector.get("damage_type_ids"), f"{sp}.damage_type_ids", errors,
                            known_types)
            else:
                errors.append(f"{sp}.mode: 必须为 all/types/hits/none")


def reject_v2_fields_in_v1(t: dict, errors: list[str]) -> None:
    """旧模板可以有历史展示扩展，但不得将新计算语义静默当扩展。"""
    for field in ("id_registry_version", "type_definitions", "rotation_tuning", "match",
                  "buff_hit_matches", "applies_to", "damage_type_ids", "benefit_type_id"):
        if field in t:
            errors.append(f"{field}: v1 不支持 v2 分类/匹配字段，请显式转换版本")
    for i, hit in _objects(t.get("hits")):
        for field in ("damage_type_ids", "applies_to", "match", "benefit_type_id"):
            if field in hit:
                errors.append(f"hits[{i}].{field}: v1 不支持 v2 分类/匹配字段")
    for i, buff in _objects(t.get("buffs")):
        for field in ("applies_to", "match", "damage_type_ids", "benefit_type_id"):
            if field in buff:
                errors.append(f"buffs[{i}].{field}: v1 不支持 v2 分类/匹配字段")
        for j, effect in _objects(buff.get("effects")):
            for field in ("applies_to", "benefit_type_id", "match", "damage_type_ids"):
                if field in effect:
                    errors.append(f"buffs[{i}].effects[{j}].{field}: v1 不支持 v2 分类/匹配字段")
