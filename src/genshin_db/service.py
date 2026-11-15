"""数据库 HTTP 服务（db-service 模块）：本地 127.0.0.1，stdlib 零依赖。

端点（api.md 一期）：
- GET    /v1/meta/version              数据版本（game_version/上游 commit/version_lines）
- GET    /v1/meta/capabilities         接口能力自描述（ADR-0002 精神）
- GET    /v1/characters                列表检索（name/element/rarity/version_line/分页）
- GET    /v1/characters/{id}           聚合详情（角色+技能+倍率+命座+extra_info+annotations）
- GET    /v1/skills/{id}               技能详情（PK 含 character_id，同 id 可能多行，返回 items）
- GET    /v1/skill-params/{groupId}    倍率参数与上下文引用（level 过滤）
- GET    /v1/search?q=                 跨角色/技能/命座名称模糊检索
- GET    /v1/annotations               追加描述检索（status 默认 approved）
- POST   /v1/annotations               旧写入名称；已弃用，返回 410
- PATCH  /v1/annotations/{id}          旧写入名称；已弃用，返回 410
- DELETE /v1/annotations/{id}          旧写入名称；已弃用，返回 410
- POST   /v1/db/update                 旧更新名称；已弃用，返回 410
- GET    /v1/db/update/status          更新状态与上次导入信息

version_line 贯穿所有查询接口，默认 release（schema.md 版本线机制）。
当前 API 范围不提供全库导出端点。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import threading
import time
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from genshin_db import queries
from genshin_db.context_query import (
    CONTEXT_CAPABILITIES,
    SECTIONS,
    http_selection,
    query_context,
)
from genshin_db.equipment import MAX_PUBLIC_ID, EquipmentError, equipment_capabilities
from genshin_db.locking import LockBusyError, file_lock, lock_is_held
from genshin_db.queries import MAX_PAGE_SIZE, NotFoundError
from genshin_db.runtime_queries import query_runtime_public, runtime_capabilities
from genshin_db.validation import positive_int

SERVICE_INFO_NAME = "service.json"
TOKEN_NAME = "local-token"

MAX_BODY_BYTES = 1024 * 1024
REJECTED_BODY_DRAIN_SECONDS = 1.0


def _int_param(*, minimum=1, default=None, required=False, nullable=False, maximum=MAX_PUBLIC_ID):
    spec = {"type": "integer", "minimum": minimum, "maximum": maximum, "required": required}
    if default is not None:
        spec["default"] = default
    if nullable:
        spec["empty_means_no_filter"] = True
    return spec


_LINE_PARAM = {"type": "string", "enum": ["release", "beta"], "default": "release"}
_PAGE_PARAMS = {"page": _int_param(default=1), "page_size": {
    **_int_param(default=20), "maximum_returned": 100,
    "description": "大于100的正整数按共享查询层截为100；非法整数拒绝"}}
_CATALOG_PARAMS = {"version_line": _LINE_PARAM, **_PAGE_PARAMS,
                   "catalog_scope": {"type": "string", "enum": ["codex", "all"], "default": "codex"},
                   "name": {"type": "string", "default": "", "description": "名称包含检索，%/_按字面"},
                   "rarity": _int_param(nullable=True, maximum=5)}
_POOL_PARAMS = {"version_line": _LINE_PARAM, **_PAGE_PARAMS,
                "depot_id": _int_param(required=True),
                "prop_type": {"type": "string", "enum_ref": "equipment.properties",
                              "empty_means_no_filter": True}}
_SECTION_PARAMS = {kind: {"type": "array", "items": "string", "enum": list(values), "encoding": "csv"}
                   for kind, values in SECTIONS.items()}

# 路由、严格参数和自描述共用此表；每个端点仍调用唯一共享查询实现。
EQUIPMENT_ENDPOINTS = [
    {"path": "/v1/weapons", "query": "list_weapons", "params": {
        **_CATALOG_PARAMS, "weapon_type": {"type": "string", "enum_ref": "equipment.weapon_types",
                                         "empty_means_no_filter": True}},
     "desc": "武器目录，默认图鉴；成长与被动在详情分别查询",
     "output": ["items", "total", "page", "page_size", "catalog_scope", "data_version"]},
    {"path": "/v1/weapons/{id}", "query": "get_weapon_detail", "params": {
        "version_line": _LINE_PARAM, "sections": _SECTION_PARAMS["weapon"],
        "refinement": _int_param(maximum=5)},
     "desc": "武器正文/来源、成长配置、全部精炼效果及approved注释；不挑默认精炼",
     "output": ["weapon", "effects", "annotations", "warnings", "data_version"]},
    {"path": "/v1/weapons/{id}/stats", "query": "get_weapon_stats", "params": {
        "version_line": _LINE_PARAM, "level": _int_param(required=True),
        "ascension": _int_param(minimum=0, required=True)},
     "desc": "等级与突破同时必填；仅成长属性，不含被动或角色面板",
     "output": ["weapon_id", "version_line", "level", "ascension", "properties",
                "formula_version", "warnings", "data_version"]},
    {"path": "/v1/artifact-sets", "query": "list_artifact_sets", "params": _CATALOG_PARAMS,
     "desc": "套装目录；codex按图鉴星级筛选，all按实际配置星级筛选",
     "output": ["items", "total", "page", "page_size", "catalog_scope", "data_version"]},
    {"path": "/v1/artifact-sets/{id}", "query": "get_artifact_set_detail",
     "params": {"version_line": _LINE_PARAM, "sections": _SECTION_PARAMS["artifact_set"],
                "set_thresholds": {"type": "array", "items": "integer", "minimum": 1, "maximum": 5, "encoding": "csv"}},
     "desc": "套装全部件数效果、approved注释及至多25个图鉴代表；更多单件走分页目录",
     "output": ["artifact_set", "effects", "piece_refs", "piece_refs_total", "piece_refs_truncated",
                "annotations", "warnings", "data_version"]},
    {"path": "/v1/artifacts", "query": "list_artifacts", "params": {
        **_CATALOG_PARAMS, "set_id": _int_param(nullable=True),
        "equip_type": {"type": "string", "enum_ref": "equipment.equip_types", "empty_means_no_filter": True}},
     "desc": "圣遗物单件配置目录，显示强化等级从0开始；配置不代表个人背包物品",
     "output": ["items", "total", "page", "page_size", "catalog_scope", "data_version"]},
    {"path": "/v1/artifacts/{id}", "query": "get_artifact_detail", "params": {"version_line": _LINE_PARAM},
     "desc": "单件原文与来源、套装引用、合法主副词条池入口；不复制套装效果为单件描述",
     "output": ["artifact", "set_ref", "main_prop_query", "substat_query", "annotations", "warnings", "data_version"]},
    {"path": "/v1/artifacts/{id}/main-stats", "query": "get_artifact_main_stats", "params": {
        "version_line": _LINE_PARAM, "level": _int_param(minimum=0, required=True),
        "main_prop_id": _int_param(required=True)},
     "desc": "按单件/显示强化等级/本池主词条ID求值；合法但源缺数值返回null与source_missing",
     "output": ["artifact_id", "version_line", "level", "source_level", "rarity", "main_prop_id",
                "depot_id", "stat", "warnings", "data_version"]},
    {"path": "/v1/artifact-main-props", "query": "list_artifact_main_props", "params": _POOL_PARAMS,
     "desc": "按depot_id分页查主词条候选，不猜等级或默认数值",
     "output": ["depot_id", "items", "total", "page", "page_size", "data_version"]},
    {"path": "/v1/artifact-substats", "query": "list_artifact_substats", "params": _POOL_PARAMS,
     "desc": "按depot_id分页查副词条原始档位；保留ID，不输出随机概率或累计强化值",
     "output": ["depot_id", "items", "total", "page", "page_size", "data_version"]},
    {"path": "/v1/equip-affixes/{id}", "query": "get_equip_affix_detail", "params": {"version_line": _LINE_PARAM},
     "desc": "精确affixId原文/参数/有序add_props/approved注释及全部武器、套装归属引用",
     "output": ["effect", "context_refs", "context_note", "data_version"]},
]
for _endpoint in EQUIPMENT_ENDPOINTS:
    _endpoint.update(method="GET", auth="none", strict_params=True)
    if "{id}" in _endpoint["path"]:
        _endpoint["path_params"] = {"id": _int_param(required=True)}
_EQUIPMENT_ROUTES = [(re.compile(re.escape(e["path"]).replace(r"\{id\}", "([^/]+)")), e)
                     for e in EQUIPMENT_ENDPOINTS]

CAPABILITIES = {
    "service": "genshin-db",
    "api_version": "v1",
    "runtime": runtime_capabilities(),
    "auth": {
        "read": "只读接口免鉴权",
        "write": "活动数据库无可用写入接口；为兼容保留的旧管理名称仍先校验"
                 " X-Local-Token，随后固定返回410且不修改数据库",
    },
    "common_params": {
        "version_line": "版本线：release（默认）/ beta，贯穿所有查询接口",
        "page": "页码，默认 1",
        "page_size": f"每页条数，默认 20，上限 {MAX_PAGE_SIZE}",
    },
    "description_fields": {
        "desc_template": "上游描述的展示文本，保留原意；仅去颜色标签，不用人工注释或机器标签替代",
        "desc_source": {"table": "来源配置表名", "record_id": "来源记录整数id",
                        "text_map_hash": "字符串hash或null", "language": "CHS",
                        "upstream_commit": "来源快照commit或null"},
        "desc_status": "present/source_empty/textmap_missing/legacy_unverified；旧库来源未核实时"
                       "desc_source=null，已有描述仍可读取，不自动迁移",
        "description_variants": "固有天赋的各级描述数组，每项level/desc_template/desc_source/desc_status；"
                                "附属技能或旧库可为空，不替代params数值",
        "annotations": "人工追加机制保留target_type/target_id/status/version_line；只聚合approved及"
                       "当前版本线或共享条目，与原文和effect_type/buff_zone机器粗标分离",
    },
    "endpoints": [
        {"method": "GET", "path": "/v1/meta/version", "auth": "none",
         "desc": "数据版本：game_version、上游 commit、构建时间、库内 version_lines"},
        {"method": "GET", "path": "/v1/meta/capabilities", "auth": "none",
         "desc": "本自描述（端点清单与参数说明）"},
        {"method": "GET", "path": "/v1/display/catalog", "auth": "none",
         "desc": "中文显示身份目录：characters/weapons/artifact_sets 的 display_name、aliases 和来源；"
                 "不含技能正文。内部 ID 不变，旧库只读兼容，别名必须唯一命中。"},
        {"method": "GET", "path": "/v1/display/resolve", "auth": "none",
         "params": {"q": "名称或编号（必填）", "entity_type": "默认 character"},
         "desc": "精确规范化别名解析；返回 resolved/ambiguous/missing 及全部候选，不猜同名身份"},
        {"method": "GET", "path": "/v1/characters", "auth": "none",
         "params": {"name": "名称模糊匹配", "element": "元素（火/水/雷/冰/风/岩/草）",
                    "rarity": "稀有度 4/5"},
         "desc": "角色分页列表，item 含 id/name/element/rarity/weapon_type/90 级面板摘要"},
        {"method": "GET", "path": "/v1/characters/{id}", "auth": "none",
         "desc": "角色聚合详情（面板+技能/命座原始描述+倍率+结构化extra_info+approved追加描述），"
                 "Agent主入口；desc_template与desc_source/desc_status分开，旧库标legacy_unverified；"
                 "extra_info保留content并提供name/desc_template/source_id/proud_skill_group_id/"
                 "description_variants/params；按完整单技能上下文分片读取，保留相关命座/固有天赋"},
        {"method": "GET", "path": "/v1/skills/{id}", "auth": "none",
         "params": {"character_id": "可选，同 id 多行时消歧（如旅行者男女主共享技能 id）"},
         "desc": "技能详情返回items，保留proud_skill_group_id，包含desc_template/desc_source/"
                 "desc_status/params，以及本技能和参数组的approved annotations；context_ref指向角色详情，"
                 "不自动把角色全部被动/命座视为当前技能的无条件机制"},
        {"method": "GET", "path": "/v1/skill-params/{groupId}", "auth": "none",
         "params": {"level": "技能等级；优先保留10/13，组内两级都不存在时保留原有等级"},
         "desc": "倍率参数组：proud_skill_group_id/version_line/items；items保留param_list/param_desc/"
                 "add_props/buff_zone。context_refs列出全部共享归属（kind/source_id/character_id/"
                 "proud_skill_group_id/version_line/path）；附属技能与固有天赋通过角色详情+selector定位。"
                 "无可用关联时context_refs=[]并给context_note，不等于没有原文"},
        {"method": "GET", "path": "/v1/search", "auth": "none",
         "params": {"q": "检索词（必填）"},
         "desc": "跨角色/技能/命座名称模糊检索，每类上限 20 条"},
        {"method": "GET", "path": "/v1/annotations", "auth": "none",
         "params": {"target_type": "character/skill/talent/skill_param/inherent_skill",
                    "target_id": "挂载对象 id", "category": "机制分类标签",
                    "status": "默认 approved；NULL version_line 条目两线通用"},
         "desc": "追加描述分页检索"},
        {"method": "POST", "path": "/v1/annotations", "auth": "token",
         "params": {"target_type": "必填", "target_id": "必填", "content": "必填",
                    "category": "可选", "title": "可选", "version_line": "release/beta/null"},
         "desc": "已弃用：活动数据库不可直接写入；返回410且不修改数据库",
         "lifecycle": "deprecated"},
        {"method": "PATCH", "path": "/v1/annotations/{id}", "auth": "token",
         "params": {"category": "可选", "title": "可选", "content": "可选",
                    "status": "可选（draft/pending/approved/rejected/orphan）"},
         "desc": "已弃用：审核或修改须进入新数据库版本候选；返回410",
         "lifecycle": "deprecated"},
        {"method": "DELETE", "path": "/v1/annotations/{id}", "auth": "token",
         "desc": "已弃用：删除须作为可审计变更进入新数据库版本候选；返回410",
         "lifecycle": "deprecated"},
        {"method": "POST", "path": "/v1/db/update", "auth": "token",
         "params": {"force": "可选 true，忽略更新检测强制重建"},
         "desc": "已弃用：不能绕过完整版本发布事务改活动数据库；返回410",
         "lifecycle": "deprecated"},
        {"method": "GET", "path": "/v1/db/update/status", "auth": "none",
         "desc": "是否正在更新 + 当前库 meta 信息"},
    ],
}
CAPABILITIES["equipment"] = {
    **equipment_capabilities(), "endpoints": EQUIPMENT_ENDPOINTS,
    "dataset_states": ["missing", "ready", "incompatible", "invalid"],
    "data_version_fields": ["version_line", "upstream_commit", "equipment_schema_version", "built_at"],
    "state_paths": {"version": "data.datasets.equipment", "update_status": "data.meta.datasets.equipment"},
    "validation_order": ["parameters", "dataset", "object", "equipment_state", "evaluation"],
    "error_reasons": {"invalid_parameter": 400, "invalid_equipment_state": 400, "not_found": 404,
                      "dataset_unavailable": 409, "unsupported_schema": 409, "dataset_invalid": 500,
                      "update_failed": 500, "update_in_progress": 409},
    "result_envelope": {"success": {"code": 0, "data": "查询结果"},
                        "error": {"code": "HTTP状态", "error": "说明", "reason": "原因码",
                                  "details": "可选结构化详情"}},
    "value_policy": "保留原零值；未知值为null，unknown单位不猜百分比，不自动进入计算",
}
CAPABILITIES["endpoints"].extend(EQUIPMENT_ENDPOINTS)
CAPABILITIES["context_query"] = CONTEXT_CAPABILITIES
CAPABILITIES["endpoints"].append(CONTEXT_CAPABILITIES)
CAPABILITIES["endpoints"].extend([
    {"method": "GET", "path": "/v1/runtime/status", "auth": "none", "desc": "运行配置组件状态；旧库不隐式迁移"},
    {"method": "POST", "path": "/v1/runtime/query", "auth": "none", "desc": "统一有界运行配置查询；请求见runtime自描述"},
    {"method": "GET", "path": "/v1/runtime/definitions/{id}", "auth": "none", "desc": "指定快照的定义摘要"},
    {"method": "POST", "path": "/v1/runtime/fragment", "auth": "none", "desc": "指定定义内有界JSON片段，保留精确原值"},
])
CAPABILITIES["endpoints"].append({"method": "POST", "path": "/v1/annotations/import", "auth": "token",
    "desc": "已弃用：旧实现的dry_run也会开启SQLite写事务；当前统一返回410，不触碰活动数据库",
    "lifecycle": "deprecated", "deprecated_reason": "accepted_database_is_immutable",
    "params": {"bundle": "schema_version=1,id,items数组；最多100项", "dry_run": "可选boolean，默认true"}})
for _endpoint in CAPABILITIES["endpoints"]:
    if _endpoint["path"] in ("/v1/characters/{id}", "/v1/skills/{id}"):
        kind = "character" if "characters" in _endpoint["path"] else "skill"
        _endpoint.setdefault("params", {}).update(sections=_SECTION_PARAMS[kind],
            levels={"type": "array", "items": "integer", "minimum": 1, "maximum": 15, "encoding": "csv"})
    if _endpoint["path"] == "/v1/annotations" and _endpoint["method"] == "GET":
        _endpoint["params"]["target_type"] += "/weapon/artifact_set/artifact/equip_affix"
    if _endpoint["path"] == "/v1/meta/version":
        _endpoint["desc"] += "；datasets.equipment分别报告release/beta组件状态，不靠软件版本猜数据已存在"
        _endpoint["desc"] += "；manual_content_fingerprint按人工条目的完整身份与内容计算，包含删除与审核状态变动"
    if _endpoint["path"] == "/v1/db/update":
        _endpoint["deprecated_reason"] = "accepted_database_is_immutable"


class ServiceError(Exception):
    def __init__(self, status: int, message: str, *, reason=None, details=None):
        super().__init__(message)
        self.status = status
        self.reason, self.details = reason, details


def load_or_create_token(data_dir: Path) -> str:
    """本地管理令牌：保留旧管理名称的鉴权边界。"""
    token_path = data_dir / TOKEN_NAME
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_hex(16)
    token_path.write_text(token, encoding="utf-8")
    return token


class _Handler(BaseHTTPRequestHandler):
    server: GenshinHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默访问日志，走结构化输出
        pass

    # ---- 响应工具 ----

    def _send(self, status: int, payload: dict) -> None:
        # 转义不可编码的代理项，避免异常诊断本身令错误响应断开；正常中文保持可读。
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8", errors="backslashreplace")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if status == 405:
            self.send_header("Allow", getattr(self, "_allowed_methods", ""))
        self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _ok(self, data) -> None:
        self._send(200, {"code": 0, "data": data})

    def _fail(self, status: int, message: str, *, reason=None, details=None) -> None:
        payload = {"code": status, "error": message}
        if reason is not None:
            payload["reason"] = reason
        if details is not None:
            payload["details"] = details
        self._send(status, payload)

    def _check_token(self) -> None:
        if self.headers.get("X-Local-Token", "") != self.server.token:
            raise ServiceError(401, "缺少或错误的本地令牌（X-Local-Token）")

    def _read_body(self) -> dict:
        if getattr(self, "_body", None) is not None:
            return self._body
        if self.headers.get("Transfer-Encoding"):
            raise ServiceError(400, "不支持 Transfer-Encoding，请使用 Content-Length")
        lengths = self.headers.get_all("Content-Length", [])
        raw_length = lengths[0] if lengths else "0"
        if len(lengths) > 1 or not raw_length.isascii() or not raw_length.isdecimal():
            raise ServiceError(400, "Content-Length 必须为非负整数")
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            raise ServiceError(413, "请求体过大")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ServiceError(400, "请求体长度不完整")
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise ServiceError(400, "请求体必须为 JSON 对象")
        return body

    def _discard_rejected_body(self) -> None:
        """Drain bounded, framed bytes without parsing JSON or changing route errors.

        Closing a Windows socket before a separately sent request body arrives can
        abort the connection and discard the response. Never wait indefinitely or
        attempt to interpret unsupported/ambiguous framing for a rejected route.
        """
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1:
            return
        raw_length = lengths[0]
        if not raw_length.isascii() or not raw_length.isdecimal():
            return
        digits = raw_length.lstrip("0") or "0"
        if len(digits) > len(str(MAX_BODY_BYTES)):
            return
        remaining = int(digits)
        if not 0 < remaining <= MAX_BODY_BYTES:
            return
        previous_timeout = self.connection.gettimeout()
        deadline = time.monotonic() + REJECTED_BODY_DRAIN_SECONDS
        try:
            while remaining:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                self.connection.settimeout(timeout if previous_timeout is None else min(timeout, previous_timeout))
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass  # Route status is still authoritative; this connection is closing.
        finally:
            self.connection.settimeout(previous_timeout)

    # ---- 路由 ----

    def _dispatch(self):
        self._equipment_request = False
        try:
            request_path = urlparse(self.path).path.rstrip("/")
            self._equipment_request = any(regex.fullmatch(request_path) for regex, _ in _EQUIPMENT_ROUTES)
            path, qs = self._parse()
            self._body = None
            routes = {
                "/v1/meta/version": {"GET": self._meta_version},
                "/v1/meta/capabilities": {"GET": lambda: CAPABILITIES},
                "/v1/display/catalog": {"GET": lambda: queries.get_display_catalog(
                    self.server.db_path, self._version_line(qs))},
                "/v1/display/resolve": {"GET": lambda: queries.resolve_display_entity(
                    self.server.db_path, qs.get("q", [""])[0],
                    qs.get("entity_type", ["character"])[0], self._version_line(qs))},
                "/v1/characters": {"GET": lambda: self._characters_list(qs)},
                "/v1/search": {"GET": lambda: self._search(qs)},
                "/v1/annotations": {"GET": lambda: self._annotations_list(qs),
                                    "POST": self._create_annotation},
                "/v1/annotations/import": {"POST": self._import_annotations},
                "/v1/context/query": {"POST": lambda: self._context_query(qs)},
                "/v1/runtime/status": {"GET": lambda: self._runtime_request(qs, "status")},
                "/v1/runtime/query": {"POST": lambda: self._runtime_request(qs)},
                "/v1/runtime/fragment": {"POST": lambda: self._runtime_request(qs, "fragment")},
                "/v1/db/update": {"POST": self._db_update},
                "/v1/db/update/status": {"GET": self._db_update_status},
            }
            methods = routes.get(path)
            runtime_match = re.fullmatch(r"/v1/runtime/definitions/([^/]+)", path)
            if methods is None and runtime_match:
                methods = {"GET": lambda: self._runtime_request(qs, "definition", runtime_match.group(1))}
            if methods is None:
                for regex, spec in _EQUIPMENT_ROUTES:
                    match = regex.fullmatch(path)
                    if match:
                        raw_id = match.group(1) if match.groups() else None
                        methods = {"GET": lambda s=spec, ident=raw_id: self._equipment_get(s, ident, qs)}
                        break
            if methods is None:
                match = re.fullmatch(r"/v1/(characters|skills|skill-params|annotations)/([^/]+)", path)
                if match:
                    kind, raw_id = match.groups()
                    object_id = positive_int(raw_id, "id")
                    methods = {
                        "characters": {"GET": lambda: self._character_detail(object_id, qs)},
                        "skills": {"GET": lambda: self._skill_detail(object_id, qs)},
                        "skill-params": {"GET": lambda: self._skill_params(object_id, qs)},
                        "annotations": {"PATCH": lambda: self._patch_annotation(object_id),
                                        "DELETE": lambda: self._delete_annotation(object_id)},
                    }[kind]
            if methods is None:
                self._discard_rejected_body()
                raise ServiceError(404, f"未知路径: {path}")
            if self.command not in methods:
                self._allowed_methods = ", ".join(methods)
                self._discard_rejected_body()
                raise ServiceError(405, "方法不允许")
            # 路由/方法先判定；拒绝正文只按有界帧丢弃，不解析或改变404/405优先级。
            if self.command in ("POST", "PATCH", "DELETE") or self.headers.get("Content-Length"):
                self._body = self._read_body()
            if self.command != "GET" and path not in ("/v1/context/query", "/v1/runtime/query", "/v1/runtime/fragment"):
                self._check_token()
            self._ok(methods[self.command]())
        except ServiceError as e:
            self._fail(e.status, str(e), reason=e.reason, details=e.details)
        except EquipmentError as e:
            self._fail(e.status, str(e), reason=e.reason, details=e.details)
        except (ValueError, UnicodeDecodeError) as e:
            self._fail(400, str(e) or "参数格式错误",
                       reason="invalid_parameter" if self._equipment_request else None)
        except LockBusyError as e:
            self._fail(409, str(e), reason="update_in_progress" if path == "/v1/db/update" else None)
        except Exception as e:  # noqa: BLE001
            self._fail(500, f"服务内部错误: {e}")

    do_GET = _dispatch
    do_POST = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch
    do_PUT = _dispatch
    do_HEAD = _dispatch
    do_OPTIONS = _dispatch
    do_TRACE = _dispatch
    do_CONNECT = _dispatch

    def __getattr__(self, name):
        # BaseHTTPRequestHandler 按 do_<method> 查找处理器；扩展方法也走统一 JSON 路由。
        if name.startswith("do_"):
            return self._dispatch
        raise AttributeError(name)

    def _parse(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query, keep_blank_values=True)
        if any(len(values) != 1 for values in qs.values()):
            raise ValueError("查询参数不能重复")
        return u.path.rstrip("/") or "/", qs

    def _version_line(self, qs: dict) -> str:
        return qs.get("version_line", ["release"])[0]

    # ---- 查询端点（实现唯一源 = genshin_db.queries，与 GUI 桥层同口径） ----

    def _runtime_request(self, qs, action=None, definition_id=None):
        if self.command == "GET":
            allowed = {"version_line"} if action == "status" else {"version_line", "snapshot_id"}
            if set(qs) - allowed:
                raise ValueError("运行资料查询包含未知参数")
            request = {key: value[0] for key, value in qs.items()}
        else:
            if qs:
                raise ValueError("运行资料POST参数只允许放入JSON正文")
            request = dict(self._read_body())
        if action:
            if "action" in request and request["action"] != action:
                raise ValueError("请求action与当前端点不符")
            request["action"] = action
        if definition_id:
            request["definition_id"] = definition_id
        data = query_runtime_public(self.server.db_path, request)
        if request.get("action") == "batch":
            from genshin_db.runtime_batch import BATCH_LIMITS
            # The transport envelope uses spaces, unlike the canonical payload budget.
            body = json.dumps({"code": 0, "data": data}, ensure_ascii=False).encode("utf-8", errors="backslashreplace")
            if len(body) > BATCH_LIMITS["delivery_bytes"]:
                raise ServiceError(413, "batch HTTP响应超过8MiB", reason="response_too_large")
        return data

    @staticmethod
    def _equipment_param(value: str, name: str, definition: dict):
        if value == "" and definition.get("empty_means_no_filter"):
            return None
        if definition["type"] == "array":
            values = value.split(",")
            if definition["items"] == "integer":
                values = [_Handler._equipment_param(v, name, {**definition, "type": "integer"}) for v in values]
            if not values or len(set(values)) != len(values) or any(v == "" for v in values):
                raise ValueError(f"{name} 必须为非空、不重复的列表")
            if "enum" in definition and any(v not in definition["enum"] for v in values):
                raise ValueError(f"非法 {name}；可选 {definition['enum']}")
            return values
        if definition["type"] == "integer":
            if not value.isascii() or not value.isdecimal():
                raise ValueError(f"{name} 必须为十进制整数")
            result = int(value)
            if not definition["minimum"] <= result <= definition["maximum"]:
                raise ValueError(f"{name} 超出整数范围 "
                                 f"[{definition['minimum']}, {definition['maximum']}]")
            return result
        choices = definition.get("enum")
        if "enum_ref" in definition:
            choices = equipment_capabilities()[definition["enum_ref"].split(".")[-1]]
        if choices is not None and value not in choices:
            raise ValueError(f"非法 {name}: {value!r}；可选 {list(choices)}")
        value.encode("utf-8")
        return value

    def _equipment_get(self, spec: dict, raw_id: str | None, qs: dict) -> dict:
        definitions = spec["params"]
        unknown = qs.keys() - definitions.keys()
        if unknown:
            raise ValueError(f"未知查询参数: {', '.join(sorted(unknown))}")
        params = {}
        for name, definition in definitions.items():
            if name in qs:
                params[name] = self._equipment_param(qs[name][0], name, definition)
            elif "default" in definition:
                params[name] = definition["default"]
            elif definition.get("required"):
                raise ValueError(f"缺少必填参数: {name}")
        if (params.get("page", 1) - 1) * min(params.get("page_size", 20), 100) > 2**63 - 1:
            raise ValueError("page 超出分页偏移范围")
        args = [self.server.db_path]
        if raw_id is not None:
            args.append(self._equipment_param(raw_id, "id", spec["path_params"]["id"]))
        try:
            return getattr(queries, spec["query"])(*args, **params)
        except NotFoundError as exc:
            raise ServiceError(404, str(exc), reason="not_found") from exc

    def _meta_version(self) -> dict:
        return queries.get_meta(self.server.db_path)

    def _characters_list(self, qs: dict) -> dict:
        try:
            return queries.list_characters(
                self.server.db_path,
                version_line=self._version_line(qs),
                name=qs["name"][0] if qs.get("name") else None,
                element=qs["element"][0] if qs.get("element") else None,
                rarity=int(qs["rarity"][0]) if qs.get("rarity") else None,
                page=int(qs.get("page", ["1"])[0]),
                page_size=int(qs.get("page_size", ["20"])[0]),
            )
        except NotFoundError as e:
            raise ServiceError(404, str(e)) from e

    def _character_detail(self, char_id: int, qs: dict) -> dict:
        if qs.keys() - {"version_line", "sections", "levels"}:
            raise ValueError("未知角色详情查询参数")
        selection = http_selection(qs, "character")
        try:
            return queries.get_character_detail(
                self.server.db_path, char_id, self._version_line(qs), **selection
            )
        except NotFoundError as e:
            raise ServiceError(404, str(e)) from e

    def _skill_detail(self, skill_id: int, qs: dict) -> dict:
        if qs.keys() - {"version_line", "character_id", "sections", "levels"}:
            raise ValueError("未知技能详情查询参数")
        selection = http_selection(qs, "skill")
        try:
            return queries.get_skill(
                self.server.db_path, skill_id, self._version_line(qs),
                character_id=int(qs["character_id"][0]) if qs.get("character_id") else None,
                **selection,
            )
        except NotFoundError as e:
            raise ServiceError(404, str(e)) from e

    def _skill_params(self, group_id: int, qs: dict) -> dict:
        try:
            return queries.get_skill_params(
                self.server.db_path, group_id, self._version_line(qs),
                level=int(qs["level"][0]) if qs.get("level") else None,
            )
        except NotFoundError as e:
            raise ServiceError(404, str(e)) from e

    def _search(self, qs: dict) -> dict:
        try:
            return queries.search(
                self.server.db_path, qs.get("q", [""])[0], self._version_line(qs)
            )
        except ValueError as e:
            raise ServiceError(400, str(e)) from e

    def _annotations_list(self, qs: dict) -> dict:
        return queries.list_annotations(
            self.server.db_path,
            status=qs.get("status", ["approved"])[0],
            target_type=qs["target_type"][0] if qs.get("target_type") else None,
            target_id=int(qs["target_id"][0]) if qs.get("target_id") else None,
            category=qs["category"][0] if qs.get("category") else None,
            version_line=self._version_line(qs),
            page=int(qs.get("page", ["1"])[0]),
            page_size=int(qs.get("page_size", ["20"])[0]),
        )

    # ---- 写入端点 ----

    def _context_query(self, qs):
        if qs:
            raise ValueError("上下文查询选择只能放在JSON正文中")
        return query_context(self.server.db_path, self._read_body())

    def _import_annotations(self):
        self._reject_live_mutation("POST /v1/annotations/import")

    def _create_annotation(self) -> dict:
        self._reject_live_mutation("POST /v1/annotations")

    def _patch_annotation(self, ann_id: int) -> dict:
        self._reject_live_mutation(f"PATCH /v1/annotations/{ann_id}")

    def _delete_annotation(self, ann_id: int) -> dict:
        self._reject_live_mutation(f"DELETE /v1/annotations/{ann_id}")

    @staticmethod
    def _reject_live_mutation(operation: str) -> None:
        from genshin_db.live_mutation_policy import (
            REASON,
            capability,
            rejection_message,
        )

        raise ServiceError(
            410,
            rejection_message(operation),
            reason=REASON,
            details=capability(operation),
        )

    # ---- 管理端点 ----

    def _db_update(self) -> dict:
        self._reject_live_mutation("POST /v1/db/update")

    def _db_update_status(self) -> dict:
        return {
            "updating": self.server.updating or lock_is_held(self.server.data_dir / ".update.lock"),
            "live_update_supported": False,
            "reason": "accepted_database_is_immutable",
            "meta": queries.get_meta(self.server.db_path),
        }


class GenshinHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, data_dir: Path, port: int = 0):
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "genshin.db"
        if not self.db_path.exists():
            raise ServiceError(500, f"数据库不存在: {self.db_path}，请安装或迁移已发布的数据库版本")
        self.token = load_or_create_token(self.data_dir)
        self.updating = False
        self.update_mutex = threading.Lock()
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def write_service_info(self) -> Path:
        info = {
            "base_url": self.base_url,
            "port": self.server_address[1],
            "pid": __import__("os").getpid(),
            "started_at": datetime.now(UTC).isoformat(),
            "token_path": TOKEN_NAME,
        }
        path = self.data_dir / SERVICE_INFO_NAME
        temp = path.with_name(f".{path.name}.{os.getpid()}.{self.server_address[1]}.tmp")
        temp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
        return path

    def remove_service_info(self) -> None:
        path = self.data_dir / SERVICE_INFO_NAME
        if path.exists():
            with contextlib.suppress(ValueError, OSError):
                if json.loads(path.read_text(encoding="utf-8")).get("base_url") == self.base_url:
                    path.unlink(missing_ok=True)


def check_existing_service(data_dir: Path) -> str | None:
    """单实例探测（ADR-0007 禁止多开）：发现文件指向的服务存活则返回其 base_url；
    发现文件失效（进程已死）则清理并返回 None。"""
    info_path = Path(data_dir) / SERVICE_INFO_NAME
    alive = _probe_service(info_path)
    if alive:
        return alive
    # 启动中或短暂探测失败时不能删除仍由另一个进程持有的发现文件。
    try:
        with file_lock(Path(data_dir) / ".service.lock", timeout=0):
            alive = _probe_service(info_path)
            if alive:
                return alive
            info_path.unlink(missing_ok=True)
    except LockBusyError:
        pass
    return None


def _probe_service(info_path: Path) -> str | None:
    if not info_path.exists():
        return None
    alive: str | None = None
    # 探测出现任何异常一律视为服务已死（发现文件失效）
    with contextlib.suppress(Exception):
        info = json.loads(info_path.read_text(encoding="utf-8"))
        with urllib.request.urlopen(
            info["base_url"] + "/v1/meta/version", timeout=1
        ) as resp:
            if resp.status == 200:
                alive = info["base_url"]
    if alive:
        return alive
    return None


def serve(data_dir: Path, port: int = 0) -> int:
    """前台运行服务（CLI db serve 入口），返回退出码。"""
    try:
        with file_lock(Path(data_dir) / ".service.lock", timeout=0):
            return _serve_locked(data_dir, port)
    except LockBusyError:
        return 1


def _serve_locked(data_dir: Path, port: int) -> int:
    existing = check_existing_service(data_dir)
    if existing:
        return 1
    server = GenshinHTTPServer(data_dir, port)
    info_path = server.write_service_info()
    print(f"服务已启动: {server.base_url}")
    print(f"发现文件: {info_path}")
    print(f"本地管理令牌: {data_dir / TOKEN_NAME}（旧写入名称已弃用）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
    finally:
        server.remove_service_info()
        server.server_close()
    return 0
