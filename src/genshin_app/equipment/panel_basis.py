"""Read-only database basis for fixed-panel editors, never a finished build.

Only the existing curated level columns are consumed. A weapon selection is
required to establish total base attack; missing selections are not zero attack.
The caller decides whether to adopt these values into a user-owned draft.
"""
from __future__ import annotations

import copy
import math
import sqlite3
from pathlib import Path

from genshin_db import queries
from genshin_db.equipment import EquipmentError
from genshin_db.validation import version_line as validate_line

from .data_version import equipment_data_version
from .files import fingerprint
from .schema import PlanError

SUPPORTED_LEVELS = (90, 100)


def _known_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def character_panel_basis(db_path, db_character_id, level=90, version_line="release", weapon=None):
    """Return available white values without editing plans or composing totals."""
    if not _integer(db_character_id, 1, 1_000_000_000):
        raise PlanError("角色数据库 ID 必须为正整数", "invalid_character_id")
    if not _integer(level, 1, 100):
        raise PlanError("角色等级必须为 1–100 的整数", "invalid_character_level")
    validate_line(version_line)
    if weapon is not None and not isinstance(weapon, dict):
        raise PlanError("武器选择必须为对象或空值", "invalid_weapon_selection")
    result = {
        "state": "unavailable", "db_character_id": db_character_id, "name": None,
        "level": level, "version_line": version_line, "supported_levels": list(SUPPORTED_LEVELS),
        **{f"character_base_{key}": None for key in ("atk", "hp", "def")},
        "panel_base": {"base_atk": None, "base_hp": None, "base_def": None},
        "weapon_contribution": {
            "state": "unavailable",
            **{key: weapon.get(key) if weapon and _integer(weapon.get(key), minimum, 1_000_000_000) else None
               for key, minimum in (("weapon_id", 1), ("level", 1), ("ascension", 0))},
            "base_atk": None, "source_refs": []},
        "source_refs": [], "data_version": None, "missing_inputs": [], "warnings": [],
        "semantics": {
            "character_base": "所选等级的角色基础生命、攻击、防御，含数据库已计入的突破基础加值。",
            "panel_base_atk": "角色基础攻击力 + 所选武器当前等级与突破阶段的基础攻击力。",
            "panel_base_hp_def": "角色自身基础生命值、基础防御力。",
            "fixed_panel": "不推算或修改攻击、生命、防御、双暴、精通、充能、增伤等成品面板；不追加武器副属性、被动或套装常驻属性。",
            "level_policy": "仅读取数据库已有的90/100级列；其他等级及缺失值不外推、不回退。",
        },
    }

    def missing(code, path, message):
        result["missing_inputs"].append({"code": code, "path": path, "message": message})

    if level not in SUPPORTED_LEVELS:
        missing("unsupported_character_level", "level", "当前基础面板查询仅支持数据库已有的90级和100级；不会沿用90级数值。")
        return result
    db_path = Path(db_path)
    if not db_path.is_file():
        missing("database_unavailable", "database", "公共资料库不存在，基础值保留未知。")
        return result
    try:
        with queries.equipment_read_session(db_path):
            try:
                detail = queries.get_character_detail(db_path, db_character_id, version_line,
                                                      sections=["character"])
            except queries.NotFoundError:
                missing("character_unavailable", "db_character_id", "当前资料版本未收录所选角色。")
                return result
            character = detail["character"]
            result["name"] = character.get("name")
            if "publication" in character:
                result["owner_publication"] = [character["publication"]]
            meta = queries.get_meta(db_path)
            try:
                result["data_version"] = equipment_data_version(meta, version_line)
            except PlanError as exc:
                result["warnings"].append({"code": exc.reason, "message": str(exc)})
            fields = [f"{key}_{level}" for key in ("atk", "hp", "def")]
            ref = {"kind": "database", "table": "character", "character_id": db_character_id,
                   "version_line": version_line, "level": level, "fields": fields,
                   "upstream_commit": meta.get(f"upstream_commit:{version_line}", meta.get("upstream_commit")),
                   "built_at": meta.get(f"built_at:{version_line}", meta.get("built_at")),
                   "value_fingerprint": fingerprint({key: character.get(key) if _known_number(character.get(key)) else None
                                                      for key in fields})}
            result["source_refs"].append(ref)
            for key in ("atk", "hp", "def"):
                value = character.get(f"{key}_{level}")
                if not _known_number(value):
                    missing("character_basis_missing", f"character_base_{key}",
                            f"数据库缺少可用的{level}级角色基础{ {'atk': '攻击力', 'hp': '生命值', 'def': '防御力'}[key]}。")
                    continue
                result[f"character_base_{key}"] = value
                if key in ("hp", "def"):
                    result["panel_base"][f"base_{key}"] = value
            _weapon_basis(db_path, character, version_line, weapon, result, missing)
            if result["weapon_contribution"]["base_atk"] is not None and result["character_base_atk"] is not None:
                total = result["character_base_atk"] + result["weapon_contribution"]["base_atk"]
                if _known_number(total):
                    result["panel_base"]["base_atk"] = total
                else:
                    missing("basis_overflow", "panel_base.base_atk", "角色与武器基础攻击合计超出可用范围。")
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        # A failed snapshot must not be mistaken for a successful partial query.
        for key in ("atk", "hp", "def"):
            result[f"character_base_{key}"] = None
            result["panel_base"][f"base_{key}"] = None
        result["weapon_contribution"].update(state="unavailable", base_atk=None, source_refs=[])
        result["source_refs"] = []
        result["data_version"] = None
        missing("database_read_failed", "database", f"读取基础面板资料失败，未应用任何基础值（{type(exc).__name__}）。")
        return result
    known = [value is not None for value in result["panel_base"].values()]
    result["state"] = "ready" if all(known) else "partial" if any(known) else "unavailable"
    return result


def _weapon_basis(db_path, character, line, weapon, result, missing):
    if not weapon:
        missing("weapon_selection_required", "weapon", "未指定武器；基础攻击包含武器白值，不能只填角色自身攻击。")
        return
    for key, minimum in (("weapon_id", 1), ("level", 1), ("ascension", 0)):
        if not _integer(weapon.get(key), minimum, 1_000_000_000):
            missing("weapon_state_required", f"weapon.{key}",
                    f"武器{ {'weapon_id': '身份', 'level': '等级', 'ascension': '突破阶段'}[key]}尚未明确或格式不正确。")
    if any(item["path"].startswith("weapon.") for item in result["missing_inputs"]):
        return
    try:
        detail = queries.get_weapon_detail(db_path, weapon["weapon_id"], line, sections=["weapon"])
        actual = detail["weapon"]
        if "publication" in actual:
            result.setdefault("owner_publication", []).append(actual["publication"])
        required, selected = character.get("weapon_type"), actual.get("weapon_type")
        if not required or not selected:
            missing("weapon_type_unknown", "weapon.weapon_id", "数据库缺少角色或武器类型，无法核对该配装。")
            return
        if required != selected:
            missing("weapon_type_mismatch", "weapon.weapon_id", "所选武器类型与角色可装备类型不匹配。")
            return
        stats = queries.get_weapon_stats(db_path, weapon["weapon_id"], weapon["level"],
                                         weapon["ascension"], line)
        entries = [item for item in stats["properties"] if item.get("prop_type") == "FIGHT_PROP_BASE_ATTACK"]
        if len(entries) != 1 or not _known_number(entries[0].get("value")):
            missing("weapon_basis_missing", "weapon.base_atk", "数据库没有唯一且有效的武器基础攻击力，未代入0。")
            return
        contribution = result["weapon_contribution"]
        contribution.update(state="ready", base_atk=entries[0]["value"],
                            source_refs=copy.deepcopy(entries[0].get("source_refs", [])),
                            data_version=copy.deepcopy(stats.get("data_version")),
                            calculation=copy.deepcopy(entries[0].get("calculation")))
        result["source_refs"].extend(copy.deepcopy(contribution["source_refs"]))
        # The stats query may report unrelated property limitations; retain them
        # as warnings without pretending they prevent knowing white attack.
        result["warnings"].extend(copy.deepcopy(stats.get("warnings", [])))
    except EquipmentError as exc:
        missing("weapon_" + exc.reason, "weapon", str(exc))
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        missing("weapon_data_unavailable", "weapon", "武器资料读取失败；基础攻击仍未知，角色生命和防御基础可独立使用。")
