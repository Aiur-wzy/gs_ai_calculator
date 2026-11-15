"""Reviewable fact bundles use the existing annotation table, never executable rules."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from genshin_db.validation import TARGET_TYPES, enum_value, positive_int, text_field
from genshin_db.validation import version_line as validate_line

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")
_FIELDS = {"id", "target_type", "target_id", "version_line", "category", "title", "conclusion",
           "conditions", "numeric_pattern", "exceptions", "sources", "unconfirmed", "status",
           "reviewer", "reviewed_at"}
_SOURCE_FIELDS = {"title", "url", "locator", "source_version", "source_date", "retrieved_at",
                  "evidence_sha256", "evidence_range", "source_kind"}


def _identifier(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{name} 必须为至多80位的小写字母/数字/点/横线/下划线标识")
    return value


def _date(value, name, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须为ISO日期或时间")  # noqa: TRY004 - public validation maps ValueError to HTTP 400
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须为ISO日期或时间") from exc
    return value


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or bundle.keys() - {"schema_version", "id", "items"}:
        raise ValueError("机制资料包只允许schema_version、id、items")
    if type(bundle.get("schema_version")) is not int or bundle["schema_version"] != 1:
        raise ValueError("机制资料包schema_version必须为1")
    bundle_id = _identifier(bundle.get("id"), "bundle.id")
    items = bundle.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("机制资料包items必须包含1–100项")
    normalized, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or item.keys() - _FIELDS:
            raise ValueError("机制资料包含未知字段")
        ident = _identifier(item.get("id"), "item.id")
        if ident in seen:
            raise ValueError("机制资料id不得重复")
        seen.add(ident)
        enum_value(item.get("target_type"), "target_type", TARGET_TYPES)
        target_id = positive_int(item.get("target_id"), "target_id")
        line = item.get("version_line")
        validate_line(line, nullable=True)
        for field in ("category", "title", "conclusion"):
            text_field(item.get(field), field, required=True)
        for field in ("conditions", "numeric_pattern", "exceptions", "unconfirmed"):
            text_field(item.get(field, ""), field)
        status = item.get("status", "draft")
        enum_value(status, "status", ("draft", "pending", "approved"))
        reviewer, reviewed_at = item.get("reviewer"), item.get("reviewed_at")
        if status == "approved":
            text_field(reviewer, "reviewer", required=True)
            _date(reviewed_at, "reviewed_at")
        elif reviewer is not None or reviewed_at is not None:
            raise ValueError("只有approved资料可包含reviewer和reviewed_at")
        sources = item.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 10:
            raise ValueError("每项机制必须有1–10项来源")
        for source in sources:
            if not isinstance(source, dict) or source.keys() - _SOURCE_FIELDS:
                raise ValueError("来源包含未知字段")
            for field in ("title", "locator"):
                text_field(source.get(field), "source." + field, required=True)
            kind = source.get("source_kind", "web")
            if kind not in {"web", "user_statement", "database_snapshot"}:
                raise ValueError("未知知识来源类型")
            if kind == "web":
                text_field(source.get("url"), "source.url", required=True)
                parsed = urlparse(source["url"])
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    raise ValueError("来源URL必须为http(s)网址")
            elif (source.get("url") is not None or not isinstance(source.get("evidence_sha256"), str)
                  or not re.fullmatch(r"[a-f0-9]{64}", source["evidence_sha256"])):
                raise ValueError("本地证据须无伪造URL并提供精确证据SHA256")
            text_field(source.get("source_version") or "", "source_version")
            _date(source.get("source_date"), "source_date", nullable=True)
            _date(source.get("retrieved_at"), "retrieved_at")
            digest = source.get("evidence_sha256")
            if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
                raise ValueError("evidence_sha256必须为SHA256十六进制摘要")
            text_field(source.get("evidence_range", ""), "evidence_range")
        content = render_fact(item)
        normalized.append({"fact_id": ident, "target_type": item["target_type"], "target_id": target_id,
                           "version_line": line, "category": item["category"], "title": item["title"],
                           "content": content, "status": status, "author": f"mechanics:{bundle_id}:{ident}",
                           "reviewer": reviewer, "reviewed_at": reviewed_at})
    return bundle_id, normalized


def render_fact(item):
    parts = []
    for label, key in (("结论", "conclusion"), ("条件", "conditions"), ("数值/模式", "numeric_pattern"),
                       ("例外", "exceptions"), ("未确认项", "unconfirmed")):
        parts.append(f"{label}：{item.get(key) or '未提供；不得自行推断。'}")
    parts.append("来源位置与原测试版本（查阅日期不等于游戏版本复验）：")
    for source in item["sources"]:
        origin = source.get("url") or ("用户陈述（非独立实测）" if source.get("source_kind") == "user_statement" else "固定数据库快照")
        parts.append(f"- {source['title']}：{origin}；位置：{source['locator']}；"
                     f"来源版本：{source.get('source_version') or '未知'}；"
                     f"来源日期：{source.get('source_date') or '未知'}；查阅日期：{source['retrieved_at']}"
                     + (f"；证据SHA256：{source['evidence_sha256']}；范围：{source.get('evidence_range', '未提供')}" if source.get("evidence_sha256") else ""))
    return "\n\n".join(parts)
