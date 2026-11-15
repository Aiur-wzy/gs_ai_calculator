"""JSON 模板校验与原子结构化补丁（engine.md §5.1/§6）。"""

from __future__ import annotations

import copy
import math
import re
from numbers import Real
from uuid import uuid4

from genshin_engine.constants import (
    AGGRAVATE_FACTOR,
    BUFF_SCOPES,
    BUFF_ZONES,
    DAMAGE_SYSTEMS,
    ELEMENTS,
    LUNAR_FACTOR,
    MULTIPLICATIVE_ZONES,
    REACTION_BASE_VALUE,
    SCALING_STATS,
    STELLAR_FACTOR,
    TRANSFORMATIVE_FACTOR,
)
from genshin_engine.registry import (
    BENEFIT_TYPES,
    ID_REGISTRY_VERSION,
    SUPPORTED_TEMPLATE_VERSIONS,
)
from genshin_engine.v2_validation import check_v2, reject_v2_fields_in_v1


class TemplateError(ValueError):
    """A deterministic input validation failure, never evidence of missing code.

    Keep ValueError/text compatibility for CLI callers while allowing the App
    to classify the failure without searching a localized message for words.
    """

    category = "request_error"
    blocking_scope = "request"

    def __init__(self, message, *, code="invalid_template", details=None,
                 category="request_error"):
        super().__init__(message)
        self.code = code
        self.details = [] if details is None else details
        if category not in {"request_error", "engine_capability_missing"}:
            raise ValueError("unsupported TemplateError category")
        self.category = category
        self.blocking_scope = "request" if category == "request_error" else "engine_capability"


class PatchError(ValueError):
    reason = "invalid_patch"


def _copy_json(value: object, where: str,
               error_type: type[TemplateError | PatchError]):
    """复制可能先于 schema 校验；深度失败仍使用调用入口的领域异常。"""
    try:
        return copy.deepcopy(value)
    except RecursionError as exc:
        raise error_type(f"{where}: JSON 结构嵌套过深，无法安全复制") from exc


def _is_num(value: object) -> bool:
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _json_errors(value: object, where: str, errors: list[str], parents: set[int]) -> None:
    """连扩展字段也保持 JSON 兼容；拒绝非有限值及 Python 调用者传入的循环容器。"""
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        if not _is_num(value):
            errors.append(f"{where}: 数值必须有限（不接受 NaN/Infinity 或溢出）")
        return
    if not isinstance(value, (dict, list)):
        errors.append(f"{where}: 必须为 JSON 兼容值")
        return
    if id(value) in parents or len(parents) >= 100:
        errors.append(f"{where}: JSON 结构循环引用或嵌套过深")
        return
    parents.add(id(value))
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{where}: 对象键必须为字符串")
            _json_errors(item, f"{where}.{key}", errors, parents)
    else:
        for i, item in enumerate(value):
            _json_errors(item, f"{where}[{i}]", errors, parents)
    parents.remove(id(value))


def _number(value: object, where: str, errors: list[str], *, minimum: float | None = None,
            positive: bool = False, integer: bool = False, maximum: float | None = None) -> bool:
    if (not _is_num(value) or (integer and not isinstance(value, int))
            or (minimum is not None and value < minimum)
            or (maximum is not None and value > maximum) or (positive and value <= 0)):
        kind = "整数" if integer else "数值"
        limit = "正" if positive else "非负" if minimum == 0 else ""
        bounds = f"，范围 [{minimum}, {maximum}]" if maximum is not None else ""
        errors.append(f"{where}: 必须为{limit}有限{kind}{bounds}（布尔值不算数字）")
        return False
    return True


def _string(value: object, where: str, errors: list[str], *, nonempty: bool = True) -> bool:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        errors.append(f"{where}: 必须为{'非空' if nonempty else ''}字符串")
        return False
    return True


def _objects(value: object, where: str, errors: list[str], *, nonempty: bool = False):
    if not isinstance(value, list) or (nonempty and not value):
        errors.append(f"{where}: 必须为{'非空' if nonempty else ''}列表")
        return
    for i, item in enumerate(value):
        path = f"{where}[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: 必须为 JSON 对象")
            continue
        yield path, item


def _number_map(value: object, where: str, allowed: tuple[str, ...], errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where}: 必须为数值字典")
        return
    for key, number in value.items():
        if key not in allowed:
            errors.append(f"{where}.{key}: 未知元素键，允许 {allowed}")
        _number(number, f"{where}.{key}", errors)


def _reference(value: object, where: str, chars: dict, errors: list[str]) -> None:
    if not isinstance(value, str) or value not in chars:
        label = "未知 target 角色" if where.endswith(".target") else "未知角色"
        errors.append(f"{where}: {label} {value!r}")


def _check_reaction(hit: dict, system: str, errors: list[str], where: str) -> str | None:
    reaction = hit.get("reaction")
    path = f"{where}.reaction"
    if reaction is None:
        if system != "classic":
            errors.append(f"{path}.type: {system} 体系必须提供 reaction.type")
        return None
    if not isinstance(reaction, dict):
        errors.append(f"{path}: 必须为 JSON 对象")
        return None
    rtype = reaction.get("type")
    if not _string(rtype, f"{path}.type", errors):
        return None
    factor_ok = False
    if "factor" in reaction:
        factor_ok = _number(reaction["factor"], f"{path}.factor", errors, positive=True)
    if "layers" in reaction:
        _number(reaction["layers"], f"{path}.layers", errors, minimum=0, integer=True)
    if system == "classic":
        if rtype == "amplify":
            if "factor" not in reaction:
                errors.append(f"{path}.factor: classic amplify 需要 reaction.factor 为正数")
        elif rtype not in AGGRAVATE_FACTOR:
            errors.append(f"{path}.type: classic 不支持 {rtype!r}")
    elif not factor_ok:
        known = {"transformative": TRANSFORMATIVE_FACTOR, "lunar": LUNAR_FACTOR,
                 "stellar": {**STELLAR_FACTOR, "stellar_superconduct": None}}[system]
        if rtype not in known:
            errors.append(f"{path}.type: 未知 {system} 反应 {rtype!r}")
    return rtype


def validate_template(t: dict, *, allow_empty_hits: bool = False) -> None:
    """Validate numerical input; an explicit causal caller may supply no base hits.

    Ordinary core callers retain the nonempty requirement. The mechanics caller
    opts in only for v2 application events which can generate independent damage.
    """
    errors: list[str] = []
    if not isinstance(t, dict):
        raise TemplateError("模板必须是 JSON 对象")
    _json_errors(t, "template", errors, set())
    if type(t.get("version")) is not int or t["version"] not in SUPPORTED_TEMPLATE_VERSIONS:
        errors.append("version 必须为整数 1 或 2")
    elif t["version"] == 2:
        check_v2(t, errors)
    else:
        reject_v2_fields_in_v1(t, errors)
    if t.get("form") != "resolved":
        errors.append("core 只接受 form='resolved' 的全数值模板（unresolved 需先经 resolver，二期）")
    meta = t.get("meta", {})
    if not isinstance(meta, dict):
        errors.append("meta: 必须为 JSON 对象")
    else:
        for key in ("name", "version_line"):
            if key in meta:
                _string(meta[key], f"meta.{key}", errors, nonempty=False)
        assumptions = meta.get("assumptions", [])
        if not isinstance(assumptions, list):
            errors.append("meta.assumptions: 必须为字符串列表")
        else:
            for i, item in enumerate(assumptions):
                _string(item, f"meta.assumptions[{i}]", errors, nonempty=False)

    enemy = t.get("enemy")
    if not isinstance(enemy, dict):
        errors.append("enemy: 必须为 JSON 对象")
    else:
        _number(enemy.get("level"), "enemy.level", errors, positive=True, integer=True)
        if "resistance" in enemy:
            _number_map(enemy["resistance"], "enemy.resistance", ("default",) + ELEMENTS, errors)
        for key in ("defense_reduction", "defense_ignore"):
            if key in enemy:
                _number(enemy[key], f"enemy.{key}", errors, minimum=0)

    chars: dict[str, dict] = {}
    for where, char in _objects(t.get("characters"), "characters", errors, nonempty=True):
        cid = char.get("id")
        if _string(cid, f"{where}.id", errors):
            if cid in chars:
                errors.append(f"{where}.id: id 重复 {cid!r}")
            chars[cid] = char
        for key in ("name", "description"):
            if key in char:
                _string(char[key], f"{where}.{key}", errors, nonempty=False)
        _number(char.get("level"), f"{where}.level", errors, positive=True, integer=True)
        stats = char.get("stats")
        if not isinstance(stats, dict):
            errors.append(f"{where}.stats: 必须为字典")
            continue
        for key, value in stats.items():
            if key == "dmg_bonus":
                _number_map(value, f"{where}.stats.dmg_bonus", ("all",) + ELEMENTS, errors)
            else:
                _number(value, f"{where}.stats.{key}", errors, minimum=0,
                        maximum=3 if key == "crit_rate" else None)

    for where, sw in _objects(t.get("switches", []), "switches", errors):
        _number(sw.get("t"), f"{where}.t", errors, minimum=0)
        _reference(sw.get("character"), f"{where}.character", chars, errors)

    buff_ids: set[str] = set()
    for where, buff in _objects(t.get("buffs", []), "buffs", errors):
        bid = buff.get("id")
        if _string(bid, f"{where}.id", errors):
            if bid in buff_ids:
                errors.append(f"{where}.id: Buff id 重复 {bid!r}")
            buff_ids.add(bid)
        for key in ("name", "source", "description"):
            if key in buff:
                _string(buff[key], f"{where}.{key}", errors, nonempty=False)
        start_ok = _number(buff.get("start"), f"{where}.start", errors, minimum=0)
        end_ok = _number(buff.get("end"), f"{where}.end", errors, minimum=0)
        if start_ok and end_ok and buff["end"] <= buff["start"]:
            errors.append(f"{where}.end: 需要 end > start")
        if buff.get("scope", "team") not in BUFF_SCOPES:
            errors.append(f"{where}.scope: 必须为 team/frontline")
        if buff.get("target") is not None:
            _reference(buff["target"], f"{where}.target", chars, errors)
        for path, effect in _objects(buff.get("effects"), f"{where}.effects", errors,
                                     nonempty=True):
            for key in ("name", "description"):
                if key in effect:
                    _string(effect[key], f"{path}.{key}", errors, nonempty=False)
            zone = effect.get("zone")
            if zone not in BUFF_ZONES:
                errors.append(f"{path}.zone: 未知 zone {zone!r}")
            _string(effect.get("stat"), f"{path}.stat", errors)
            if zone in ("stat_add", "stat_pct") and effect.get("stat") not in ("atk", "hp", "def"):
                errors.append(f"{path}.stat: {zone} 仅支持 atk/hp/def")
            _number(effect.get("value"), f"{path}.value", errors,
                    minimum=0 if zone in MULTIPLICATIVE_ZONES else None)

    for where, hit in _objects(t.get("hits"), "hits", errors, nonempty=not allow_empty_hits):
        time_ok = _number(hit.get("t"), f"{where}.t", errors, minimum=0)
        cid = hit.get("character")
        _reference(cid, f"{where}.character", chars, errors)
        _string(hit.get("skill"), f"{where}.skill", errors)
        for key in ("hit_name", "description"):
            if key in hit:
                _string(hit[key], f"{where}.{key}", errors, nonempty=False)
        system = hit.get("damage_system")
        reaction_type = None
        if system not in DAMAGE_SYSTEMS:
            errors.append(f"{where}.damage_system: 必须为 {DAMAGE_SYSTEMS}")
        else:
            reaction_type = _check_reaction(hit, system, errors, where)
        if hit.get("element") not in ELEMENTS:
            errors.append(f"{where}.element: 必须为 {ELEMENTS}")
        for key in ("snapshot", "reaction_base"):
            if key in hit and not isinstance(hit[key], bool):
                errors.append(f"{where}.{key}: 必须为布尔值")
        scaling = hit.get("scaling_stat", "atk")
        if scaling not in SCALING_STATS:
            errors.append(f"{where}.scaling_stat: 必须为 {SCALING_STATS}")
        needs_scaling = system == "classic" or (
            system in ("lunar", "stellar") and hit.get("reaction_base") is not True)
        if needs_scaling or "multiplier" in hit:
            _number(hit.get("multiplier"), f"{where}.multiplier", errors, minimum=0)
        char = chars.get(cid) if isinstance(cid, str) else None
        if needs_scaling and char is not None and scaling in SCALING_STATS:
            stats = char.get("stats")
            if isinstance(stats, dict) and scaling not in stats:
                errors.append(f"{where}.scaling_stat: 角色 {cid!r} 缺少 stats.{scaling}")
        needs_level_base = (system == "transformative"
                            or (system == "classic" and reaction_type in AGGRAVATE_FACTOR)
                            or (system in ("lunar", "stellar")
                                and hit.get("reaction_base") is True))
        if needs_level_base and char is not None:
            level = char.get("level")
            if (not isinstance(level, int) or isinstance(level, bool)
                    or level not in REACTION_BASE_VALUE):
                errors.append(f"{where}.character: 反应基础值不支持角色 {cid!r} 的等级 {level!r}；"
                              f"仅支持 {tuple(REACTION_BASE_VALUE)}")
        if "count" in hit:
            _number(hit["count"], f"{where}.count", errors, positive=True, integer=True)
        if "result_weight" in hit:
            _number(hit["result_weight"], f"{where}.result_weight", errors,
                    minimum=0, positive=True)
            if isinstance(hit["result_weight"], (int, float)) and hit["result_weight"] > 1:
                errors.append(f"{where}.result_weight: 必须不大于 1")
        if "cast_t" in hit:
            cast_ok = _number(hit["cast_t"], f"{where}.cast_t", errors, minimum=0)
            if hit.get("snapshot") is True and cast_ok and time_ok and hit["cast_t"] > hit["t"]:
                errors.append(f"{where}.cast_t: cast_t 不应晚于 hit 时刻 t")

    if "loop_duration" in t:
        _number(t["loop_duration"], "loop_duration", errors, positive=True)
    if errors:
        raise TemplateError("; ".join(errors), details=list(errors))


def convert_v1_to_v2(template: dict, *, hit_damage_types: list[list[str]],
                     type_definitions: dict | None = None) -> dict:
    """显式转换有效 v1；分类由调用方按旧事件顺序提供，不猜机制。

    返回有效 v2 新副本。新身份为 UUID，与数组位置/技能名无关；应保存
    转换产物并继续编辑，不在每次计算时重新转换。旧模板未限定的范围显式 all。
    v1 允许但没有登记的收益 stat 不猜测迁移，报告准确位置供调用方确认。
    原生 pending 草稿（如未分类事件）由应用层维护，不能经此接口伪装 resolved。
    """
    t = _copy_json(template, "template", TemplateError)
    validate_template(t)
    if t["version"] != 1:
        raise TemplateError("version: convert_v1_to_v2 仅接受 v1 模板")
    types = _copy_json(hit_damage_types, "hit_damage_types", TemplateError)
    if not isinstance(types, list) or len(types) != len(t["hits"]):
        raise TemplateError("hit_damage_types: 必须按旧 hits 顺序为每个事件明确提供分类列表")
    t["version"] = 2
    t["id_registry_version"] = ID_REGISTRY_VERSION
    t["type_definitions"] = _copy_json(
        {"damage_types": []} if type_definitions is None else type_definitions,
        "type_definitions", TemplateError)
    for hit, categories in zip(t["hits"], types, strict=True):
        hit["id"] = f"hit.{uuid4()}"
        hit["damage_type_ids"] = categories
    for i, buff in enumerate(t.get("buffs", [])):
        for j, effect in enumerate(buff["effects"]):
            benefit = f"benefit.{effect['zone']}.{effect['stat']}"
            if benefit not in BENEFIT_TYPES:
                raise TemplateError(f"buffs[{i}].effects[{j}].stat: 旧收益没有对应注册项 "
                                    f"{benefit!r}，需要显式确认迁移")
            effect["id"] = f"effect.{uuid4()}"
            effect["benefit_type_id"] = benefit
            effect["applies_to"] = {"mode": "all"}
    validate_template(t)
    return t


def _list_index(key: str, size: int, *, adding: bool = False) -> int:
    if adding and key == "-":
        return size
    if re.fullmatch(r"0|[1-9][0-9]*", key) is None:
        raise PatchError(f"数组索引必须为非负整数（add 可用 - 追加）: {key!r}")
    index = int(key)
    if index > size or (index == size and not adding):
        raise PatchError(f"数组索引越界: {key!r}（长度 {size}）")
    return index


def _resolve_parent(root: object, path: object) -> tuple[object, str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise PatchError("path 必须是以 / 开头的非根 JSON Pointer 字符串")
    if re.search(r"~(?![01])", path):
        raise PatchError("path 含非法转义（仅支持 ~0 与 ~1）")
    segments = [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]
    node = root
    for segment in segments[:-1]:
        if isinstance(node, list):
            node = node[_list_index(segment, len(node))]
        elif isinstance(node, dict):
            if segment not in node:
                raise PatchError(f"路径键不存在: {segment!r}")
            node = node[segment]
        else:
            raise PatchError("路径不可达")
    return node, segments[-1]


def apply_patch(template: dict, ops: list[dict]) -> dict:
    """应用补丁并整体验证；异常含操作位置，原模板及操作值均不被修改。"""
    if not isinstance(ops, list):
        raise PatchError("补丁必须为操作列表")
    if not isinstance(template, dict):
        raise PatchError("补丁模板必须为 JSON 对象")
    t = _copy_json(template, "补丁模板", PatchError)
    for i, op in enumerate(ops):
        path = op.get("path") if isinstance(op, dict) else None
        where = f"补丁[{i}] path={path!r}"
        try:
            if not isinstance(op, dict):
                raise PatchError("操作必须为 JSON 对象")
            action = op.get("op")
            if action not in ("add", "remove", "replace"):
                raise PatchError("op 必须为 add/remove/replace")
            parent, key = _resolve_parent(t, path)
            if action in ("add", "replace") and "value" not in op:
                raise PatchError(f"{action} 需要 value")
            if isinstance(parent, list):
                index = _list_index(key, len(parent), adding=action == "add")
                if action == "add":
                    parent.insert(index, _copy_json(op["value"], "value", PatchError))
                elif action == "replace":
                    parent[index] = _copy_json(op["value"], "value", PatchError)
                else:
                    parent.pop(index)
            elif isinstance(parent, dict):
                if action in ("remove", "replace") and key not in parent:
                    raise PatchError(f"键不存在 {key!r}")
                if action == "remove":
                    del parent[key]
                else:
                    parent[key] = _copy_json(op["value"], "value", PatchError)
            else:
                raise PatchError("路径终点不可写")
        except (PatchError, ValueError, TypeError, IndexError, KeyError) as exc:
            raise PatchError(f"{where}: {exc}") from exc
    try:
        validate_template(t)
    except RecursionError as exc:
        raise PatchError("补丁应用后模板: JSON 结构嵌套过深，无法安全校验") from exc
    except TemplateError as exc:
        raise PatchError(f"补丁应用后模板校验失败: {exc}") from exc
    return t
