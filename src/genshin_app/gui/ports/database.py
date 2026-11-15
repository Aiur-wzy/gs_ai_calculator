"""DatabaseGuiPort: concrete domain adapter; no GUI Facade callbacks."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from genshin_db import queries
from genshin_db.queries import NotFoundError

from .responses import GuiResponses


class DatabaseGuiPort(GuiResponses):
    def __init__(self, database_path):
        self._db_path = Path(database_path)

    def get_display_catalog(self, version_line: str = "release") -> dict:
        """中文名称与别名目录；只读，旧数值模板不依赖数据库升级或改写。"""
        from genshin_db.validation import version_line as validate_line

        try:
            validate_line(version_line)
            if not self._db_path.is_file():
                return self._ok({"locale": "zh-CN", "version_line": version_line,
                                 "characters": [], "weapons": [], "artifact_sets": [],
                                 "state": "missing", "message": "公共资料库尚未就绪，中文名称可手动补充"})
            return self._ok(queries.get_display_catalog(self._db_path, version_line))
        except (sqlite3.Error, ValueError, NotFoundError, OSError) as exc:
            return self._err(f"读取中文名称目录失败：{exc}")

    def db_overview(self) -> dict:
        """库就绪状态 + meta；活动库只能由完整版本迁移提供。"""
        if not self._db_path.exists():
            return {"code": 1, "reason": "database_not_installed", "message": "请先在资料页安装数据库。"}
        try:
            return self._ok({"db_path": str(self._db_path), "meta": queries.get_meta(self._db_path)})
        except (sqlite3.Error, OSError, ValueError) as e:
            return self._err(f"读取数据库失败 {self._db_path}: {e}")

    def _equipment_query(self, method: str, *args, **kwargs) -> dict:
        if not self._db_path.is_file():
            return {"code": 1, "reason": "database_not_installed",
                    "message": "请先在资料页安装数据库。"}
        try:
            return self._ok(getattr(queries, method)(self._db_path, *args, **kwargs))
        except (ValueError, NotFoundError, sqlite3.Error, OSError) as e:
            return {"code": 1, "message": str(e), "reason": getattr(e, "reason", None)}

    def db_capabilities(self) -> dict:
        return self._ok(queries.equipment_capabilities())

    def list_weapons(self, name="", weapon_type="", rarity=0, catalog_scope="codex",
                     version_line="release", page=1, page_size=50) -> dict:
        return self._equipment_query("list_weapons", name=name or None,
                                     weapon_type=weapon_type or None, rarity=rarity or None,
                                     catalog_scope=catalog_scope, version_line=version_line,
                                     page=page, page_size=page_size)

    def weapon_detail(self, weapon_id, version_line="release") -> dict:
        return self._equipment_query("get_weapon_detail", weapon_id, version_line=version_line)

    def weapon_stats(self, weapon_id, level, ascension, version_line="release") -> dict:
        return self._equipment_query("get_weapon_stats", weapon_id, level=level,
                                     ascension=ascension, version_line=version_line)

    def list_artifact_sets(self, name="", rarity=0, catalog_scope="codex",
                           version_line="release", page=1, page_size=50) -> dict:
        return self._equipment_query("list_artifact_sets", name=name or None, rarity=rarity or None,
                                     catalog_scope=catalog_scope, version_line=version_line,
                                     page=page, page_size=page_size)

    def artifact_set_detail(self, set_id, version_line="release") -> dict:
        return self._equipment_query("get_artifact_set_detail", set_id, version_line=version_line)

    def list_artifacts(self, name="", set_id=None, equip_type="", rarity=0,
                       catalog_scope="codex", version_line="release", page=1, page_size=50) -> dict:
        return self._equipment_query("list_artifacts", name=name or None, set_id=set_id,
                                     equip_type=equip_type or None, rarity=rarity or None,
                                     catalog_scope=catalog_scope, version_line=version_line,
                                     page=page, page_size=page_size)

    def artifact_detail(self, artifact_id, version_line="release") -> dict:
        return self._equipment_query("get_artifact_detail", artifact_id, version_line=version_line)

    def artifact_main_props(self, depot_id, prop_type="", version_line="release", page=1, page_size=50) -> dict:
        return self._equipment_query("list_artifact_main_props", depot_id=depot_id,
                                     prop_type=prop_type or None, version_line=version_line,
                                     page=page, page_size=page_size)

    def artifact_substats(self, depot_id, prop_type="", version_line="release", page=1, page_size=50) -> dict:
        return self._equipment_query("list_artifact_substats", depot_id=depot_id,
                                     prop_type=prop_type or None, version_line=version_line,
                                     page=page, page_size=page_size)

    def artifact_main_stats(self, artifact_id, level, main_prop_id, version_line="release") -> dict:
        return self._equipment_query("get_artifact_main_stats", artifact_id, level=level,
                                     main_prop_id=main_prop_id, version_line=version_line)

    def equip_affix_detail(self, affix_id, version_line="release") -> dict:
        return self._equipment_query("get_equip_affix_detail", affix_id, version_line=version_line)

    def list_characters(self, name: str = "", element: str = "", rarity: int = 0,
                        version_line: str = "release", page: int = 1,
                        page_size: int = 50) -> dict:
        try:
            return self._ok(queries.list_characters(
                self._db_path, version_line=version_line, name=name or None,
                element=element or None, rarity=rarity or None,
                page=page, page_size=page_size,
            ))
        except (ValueError, NotFoundError, sqlite3.Error, OSError) as e:
            return self._err(str(e))

    def character_detail(self, char_id: int, version_line: str = "release") -> dict:
        try:
            return self._ok(queries.get_character_detail(self._db_path, char_id, version_line))
        except (NotFoundError, ValueError, sqlite3.Error, OSError) as e:
            return self._err(str(e))

    def search_data(self, q: str, version_line: str = "release") -> dict:
        try:
            return self._ok(queries.search(self._db_path, q, version_line))
        except (ValueError, sqlite3.Error, OSError) as e:
            return self._err(str(e))

    def list_annotations(self, target_type: str = "", target_id: int = 0,
                         status: str = "all", version_line: str = "release",
                         page: int = 1, page_size: int = 100) -> dict:
        """status='all' 表示全部状态（ADR-0006 编辑场景）。"""
        try:
            return self._ok(queries.list_annotations(
                self._db_path,
                status=None if status == "all" else status,
                target_type=target_type or None, target_id=target_id or None,
                version_line=version_line or None, page=page, page_size=page_size,
            ))
        except (ValueError, NotFoundError, sqlite3.Error, OSError) as e:
            return self._err(str(e))

    def create_annotation(self, target_type: str, target_id: int, content: str,
                          category: str = "", title: str = "", version_line: str | None = None) -> dict:
        return self._deprecated_database_write("GUI create_annotation")

    def update_annotation(self, ann_id: int, fields: dict) -> dict:
        return self._deprecated_database_write("GUI update_annotation")

    def delete_annotation(self, ann_id: int) -> dict:
        return self._deprecated_database_write("GUI delete_annotation")

    @staticmethod
    def _deprecated_database_write(operation: str) -> dict:
        from genshin_db.live_mutation_policy import (
            REASON,
            capability,
            rejection_message,
        )

        return {
            "code": 1,
            "message": rejection_message(operation),
            "reason": REASON,
            "details": capability(operation),
        }

