"""App-owned research journal for DSH calls and isolated external task claims.

The ledger is an audit barrier, not a search allowlist.  It validates only the
fixed tool schemas, size bounds, session identity and the existing no-secret /
no-local-path invariant.  Topics, domains and whether a result resembles an
answer are deliberately outside this module.

Web page bodies are never accepted.  Reproducibility is represented by the
actual query or URL, bounded presentation metadata, content hashes and stable
resource identifiers.

External CLI tasks use an explicit ``external_declared`` record family and
candidate binding in the same storage owner. They cannot become DSH execution
facts, settle native calls, or take over a native conversation's ledger.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .native_session_store import redact_visible_text
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-research-ledger-v1"
REPORT_CONTRACT = "research-usage-report-v1"
LEDGER_FILE = "native-research-ledger-v1.json"
LOCK_FILE = ".native-research-ledger-v1.lock"
MAX_LEDGER_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 32_768
MAX_REPORTS = 256
MAX_QUERIES = 4
MAX_QUERY_CHARS = 1024
MAX_URL_CHARS = 2048
MAX_TITLE_CHARS = 256
MAX_SNIPPET_CHARS = 512
MAX_SOURCES = 16
MAX_RESOURCE_IDS = 16
MAX_REPORT_TEXT_CHARS = 1024
MAX_APPLIED_TO = 32
MAX_DSH_ANSWER_CHARS = 200_000
MAX_SAFE_INTEGER = 2**53 - 1

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RESEARCH_ID = re.compile(r"research_[A-Za-z0-9_-]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TOOLS = frozenset({"web_search", "web_fetch"})
_TERMINAL = frozenset({"completed", "failed", "cancelled", "outcome_unknown"})
_DISPOSITIONS = frozenset({"adopted", "background", "rejected"})
_GAP_CLASSES = frozenset(
    {"database_missing", "dynamic_web_preferred", "verification_only", "unused"}
)
_WRITER_FENCE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])wf_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"
)
_SECRET_TOKEN = re.compile(
    r"(?ix)(?:\bsk-[A-Za-z0-9_-]{8,}"
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\b(?:authorization|proxy-authorization)\s*:\s*"
    r"(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\b(?:cookie|set-cookie)\s*:\s*[^\r\n]+)"
)
_LABELLED_SECRET = re.compile(
    r"(?ix)\b(?:api[\s_-]*key|access[\s_-]*token|token|secret|password|"
    r"passwd|pwd)\b\s*[:=]\s*(?:bearer\s+)?"
    r"(?P<value>[A-Za-z0-9._~+/=-]{8,})"
)
_FILE_URI = re.compile(r"(?i)\bfile:(?://)?[^\s<>\"'，。；、]+")
_UNC_PATH = re.compile(r"(?<![:/\\A-Za-z0-9])(?:\\\\|//)[^\r\n<>\"'，。；、]+")
_WINDOWS_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])[^\r\n<>\"'，。；、]+"
)
_RELATIVE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.])\.{1,2}[\\/][^\s<>\"'，。；、]+"
)
_POSIX_LOCAL_PATH = re.compile(
    r"(?i)(?<![:/\\A-Za-z0-9_])/(?:Users|home|etc|tmp|var|opt|root|mnt|"
    r"Volumes|private|usr|dev|proc|sys|run|srv|workspace)(?:/|\\|\Z)"
)


def _fail(message: str, reason: str = "agent_checkpoint_invalid", details=None) -> None:
    raise PlanError(message, reason, details)


def _identity(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不合法")
    return value


def _counter(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _fail(f"{name}不合法")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}不合法")
    return value


def _optional_text(value: object, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 0x20 and character not in "\t\n" for character in value)
    ):
        _fail(f"{name}不合法")
    return value.strip()


def _looks_like_secret(value: str) -> bool:
    if _WRITER_FENCE_TOKEN.search(value) or _SECRET_TOKEN.search(value):
        return True
    for match in _LABELLED_SECRET.finditer(value):
        candidate = match.group("value")
        if any(character.isdigit() for character in candidate):
            return True
    return False


def _looks_like_local_path(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (
            _FILE_URI,
            _UNC_PATH,
            _WINDOWS_PATH,
            _RELATIVE_PATH,
            _POSIX_LOCAL_PATH,
        )
    )


def _safe_external_text(
    value: object,
    name: str,
    maximum: int,
    *,
    check_local_path: bool = True,
) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(
            ord(character) < 0x20 and character not in "\t\n"
            for character in value
        )
    ):
        _fail(f"{name}不能为空")
    # Preserve the exact official-tool argument.  This is deliberately a
    # high-confidence secret/path barrier, not the broader display redactor:
    # ordinary searches such as ``KQM / HoYoLab`` must remain available.
    if _looks_like_secret(value) or check_local_path and _looks_like_local_path(value):
        _fail(f"{name}包含凭据或本地路径", "research_sensitive_argument")
    return value


def _public_url(value: object, name: str = "url") -> str:
    url = _safe_external_text(
        value,
        name,
        MAX_URL_CHARS,
        check_local_path=False,
    )
    if any(character.isspace() for character in url):
        _fail(f"{name}不合法")
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        _fail(f"{name}不合法")
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port is not None and not 1 <= port <= 65535
    ):
        _fail(f"{name}必须是无凭据的HTTP(S) URL")
    decoded_query = unquote(parts.query)
    if (
        any(
            pattern.search(url) is not None
            for pattern in (_FILE_URI, _UNC_PATH, _WINDOWS_PATH)
        )
        or _looks_like_secret(decoded_query)
        or _looks_like_local_path(decoded_query)
    ):
        _fail(f"{name}包含凭据或本地路径", "research_sensitive_argument")
    # urlsplit is validation-only.  The audit hash and UI must retain the exact
    # string the official DSH tool received, including an empty path/fragment.
    return url


def _timestamp(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        _fail(f"{name}不合法")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(f"{name}不合法")
    if parsed.tzinfo is None:
        _fail(f"{name}缺少时区")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_research_id() -> str:
    # 24 bytes => 32 URL-safe base64 characters, without padding.
    return "research_" + secrets.token_urlsafe(24)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _empty(conversation_id: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "conversation_id": _identity(conversation_id, "conversation_id"),
        "revision": 0,
        "entries": [],
        "reports": [],
    }


def _args(tool: str, value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("Web工具参数必须是对象", "agent_tool_input")
    if tool == "web_search":
        if set(value) != {"queries"}:
            _fail("web_search参数字段不合法", "agent_tool_input")
        queries = value.get("queries")
        if (
            not isinstance(queries, Sequence)
            or isinstance(queries, (str, bytes))
            or not 1 <= len(queries) <= MAX_QUERIES
        ):
            _fail("web_search查询数量不合法", "agent_tool_input")
        checked = [
            _safe_external_text(query, "query", MAX_QUERY_CHARS) for query in queries
        ]
        # Official dsh-tool-web accepts duplicate strings and deduplicates only
        # at execution.  The audit row must preserve the actual call arguments.
        return {"queries": checked}
    if set(value) != {"url"}:
        _fail("web_fetch参数字段不合法", "agent_tool_input")
    return {"url": _public_url(value.get("url"))}


def validate_research_arguments(
    tool: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one official Web call without persisting it.

    The event projector uses the same admission rule as the durable ledger so
    a rejected secret or local path cannot first appear in a public tool-call
    projection while the pre-dispatch ledger barrier is failing closed.
    """

    if tool not in _TOOLS:
        _fail("研究工具不在固定目录", "agent_tool_rejected")
    return copy.deepcopy(_args(tool, value))


def _sources(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) > MAX_SOURCES
    ):
        _fail("研究来源列表不合法")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) - {
            "url",
            "title",
            "snippet",
            "publishedAt",
            "date",
        }:
            _fail("研究来源字段不合法")
        if "publishedAt" in item and "date" in item:
            _fail("研究来源日期字段重复")
        url = _public_url(item.get("url"), "source_url")
        if url in seen:
            continue
        seen.add(url)
        title_value = item.get("title")
        snippet_value = item.get("snippet")
        published_value = item.get("publishedAt", item.get("date"))
        title = (
            None
            if isinstance(title_value, str) and not title_value.strip()
            else _optional_text(title_value, "source_title", MAX_TITLE_CHARS)
        )
        snippet = (
            None
            if isinstance(snippet_value, str) and not snippet_value.strip()
            else _optional_text(snippet_value, "source_snippet", MAX_SNIPPET_CHARS)
        )
        published = (
            None
            if isinstance(published_value, str) and not published_value.strip()
            else _optional_text(published_value, "source_published_at", 64)
        )
        result.append(
            {
                "url": url,
                "title": None if title is None else redact_visible_text(title),
                "snippet": None if snippet is None else redact_visible_text(snippet),
                "publishedAt": (
                    None if published is None else redact_visible_text(published)
                ),
            }
        )
    return result


def _resource_ids(value: object) -> list[str]:
    if value is None:
        return []
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) > MAX_RESOURCE_IDS
    ):
        _fail("研究resource列表不合法")
    result = [_identity(item, "resource_id") for item in value]
    if len(set(result)) != len(result):
        _fail("研究resource不能重复")
    return result


def _dsh_presentation(
    tool: str,
    value: object,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reduce official DSH presentation metadata without retaining result bodies."""

    if not isinstance(value, Mapping):
        _fail("DSH展示元数据不合法", "invalid_tool_result")
    metadata = dict(value)
    if tool == "web_search":
        if not {"sources", "truncated"} <= set(metadata) or set(metadata) - {
            "sources",
            "truncated",
            "answer",
        }:
            _fail("web_search展示元数据字段不合法", "invalid_tool_result")
        if type(metadata["truncated"]) is not bool:
            _fail("web_search截断标记不合法", "invalid_tool_result")
        answer = metadata.get("answer")
        if answer is not None and (
            not isinstance(answer, str) or len(answer) > MAX_DSH_ANSWER_CHARS
        ):
            _fail("web_search摘要元数据不合法", "invalid_tool_result")
        return (
            {
                "kind": "search",
                "truncated": metadata["truncated"],
                # The answer remains in the transient DSH result.  Only its
                # presence is durable; content_hash binds the complete render.
                "answer_present": answer is not None,
            },
            _sources(metadata["sources"]),
        )
    if tool == "web_fetch":
        if set(metadata) != {"url", "statusCode", "truncated"}:
            _fail("web_fetch展示元数据字段不合法", "invalid_tool_result")
        status_code = metadata["statusCode"]
        if type(status_code) is not int or not 100 <= status_code <= 599:
            _fail("web_fetch状态码不合法", "invalid_tool_result")
        if type(metadata["truncated"]) is not bool:
            _fail("web_fetch截断标记不合法", "invalid_tool_result")
        return (
            {
                "kind": "fetch",
                "url": _public_url(metadata["url"], "final_url"),
                "statusCode": status_code,
                "truncated": metadata["truncated"],
            },
            [],
        )
    _fail("研究工具不合法", "invalid_tool_result")


def _stored_presentation(tool: str, value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("研究展示摘要不合法")
    metadata = copy.deepcopy(dict(value))
    if tool == "web_search":
        if set(metadata) != {"kind", "truncated", "answer_present"}:
            _fail("web_search展示摘要字段不合法")
        if (
            metadata["kind"] != "search"
            or type(metadata["truncated"]) is not bool
            or type(metadata["answer_present"]) is not bool
        ):
            _fail("web_search展示摘要不合法")
        return metadata
    if tool == "web_fetch":
        if set(metadata) != {"kind", "url", "statusCode", "truncated"}:
            _fail("web_fetch展示摘要字段不合法")
        if (
            metadata["kind"] != "fetch"
            or type(metadata["statusCode"]) is not int
            or not 100 <= metadata["statusCode"] <= 599
            or type(metadata["truncated"]) is not bool
        ):
            _fail("web_fetch展示摘要不合法")
        metadata["url"] = _public_url(metadata["url"], "final_url")
        return metadata
    _fail("研究工具不合法")


def _validate_entry(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping) and "authority" in value:
        from .native_research_external import validate_entry
        return validate_entry(value)
    keys = {
        "research_id", "call_id", "turn_id", "epoch_id", "workflow_stage", "tool",
        "arguments", "arguments_hash", "raw_arguments_hash", "started_at", "status",
        "completed_at", "content_hash", "sources", "resource_ids",
        "presentation_meta", "usage",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail("研究记录字段不完整")
    row = copy.deepcopy(dict(value))
    if not isinstance(row["research_id"], str) or _RESEARCH_ID.fullmatch(row["research_id"]) is None:
        _fail("research_id不合法")
    for field in ("call_id", "turn_id", "epoch_id", "workflow_stage"):
        _identity(row[field], field)
    if row["tool"] not in _TOOLS:
        _fail("研究工具不合法")
    row["arguments"] = _args(row["tool"], row["arguments"])
    _hash(row["arguments_hash"], "arguments_hash")
    if row["arguments_hash"] != fingerprint(row["arguments"]):
        _fail("研究参数指纹不匹配")
    _hash(row["raw_arguments_hash"], "raw_arguments_hash")
    _timestamp(row["started_at"], "started_at")
    if row["status"] == "pending":
        if any(
            row[field] is not None and row[field] != []
            for field in (
                "completed_at",
                "content_hash",
                "sources",
                "resource_ids",
                "presentation_meta",
                "usage",
            )
        ):
            _fail("未结算研究记录含有终态数据")
        row["sources"] = []
        row["resource_ids"] = []
    elif row["status"] in _TERMINAL:
        _timestamp(row["completed_at"], "completed_at")
        if row["content_hash"] is not None:
            _hash(row["content_hash"], "content_hash")
        row["sources"] = _sources(row["sources"])
        row["resource_ids"] = _resource_ids(row["resource_ids"])
        if row["status"] == "completed":
            if row["content_hash"] is None or row["presentation_meta"] is None:
                _fail("成功研究记录缺少内容指纹或DSH展示摘要")
            row["presentation_meta"] = _stored_presentation(
                row["tool"], row["presentation_meta"]
            )
            if row["tool"] == "web_fetch" and row["sources"]:
                _fail("web_fetch研究记录不能携带搜索来源")
        elif row["presentation_meta"] is not None or row["sources"]:
            _fail("非成功研究记录不能携带DSH展示摘要或搜索来源")
        if row["usage"] is not None:
            row["usage"] = _validate_usage(row["usage"])
    else:
        _fail("研究状态不合法")
    return row


def _validate_usage(value: object) -> dict[str, Any]:
    keys = {
        "research_id", "disposition", "claim_summary", "applied_to",
        "system_gap_class", "reason",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail("研究采用记录字段不完整", "agent_tool_input")
    result = copy.deepcopy(dict(value))
    if not isinstance(result["research_id"], str) or _RESEARCH_ID.fullmatch(result["research_id"]) is None:
        _fail("research_id不合法", "agent_tool_input")
    if result["disposition"] not in _DISPOSITIONS:
        _fail("研究采用状态不合法", "agent_tool_input")
    if result["system_gap_class"] not in _GAP_CLASSES:
        _fail("系统缺失分类不合法", "agent_tool_input")
    result["claim_summary"] = _optional_text(
        result["claim_summary"], "claim_summary", MAX_REPORT_TEXT_CHARS
    )
    result["reason"] = _optional_text(result["reason"], "reason", MAX_REPORT_TEXT_CHARS)
    if result["claim_summary"] is not None:
        result["claim_summary"] = redact_visible_text(result["claim_summary"])
    if result["reason"] is not None:
        result["reason"] = redact_visible_text(result["reason"])
    applied = result["applied_to"]
    if (
        not isinstance(applied, Sequence)
        or isinstance(applied, (str, bytes))
        or len(applied) > MAX_APPLIED_TO
    ):
        _fail("研究采用位置不合法", "agent_tool_input")
    result["applied_to"] = [_identity(item, "applied_to") for item in applied]
    if len(set(result["applied_to"])) != len(result["applied_to"]):
        _fail("研究采用位置不能重复", "agent_tool_input")
    return result


def _validate_report(value: object) -> dict[str, Any]:
    keys = {
        "contract", "turn_id", "research_ledger_revision", "entries",
        "complete_for_turn", "reported_at",
    }
    external = isinstance(value, Mapping) and "external_binding" in value
    if external:
        keys |= {"external_binding", "authority"}
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail("研究使用报告字段不完整")
    report = copy.deepcopy(dict(value))
    expected_contract = REPORT_CONTRACT
    if external:
        from .native_research_external import AUTHORITY, binding, turn_id
        from .native_research_external import REPORT_CONTRACT as EXTERNAL_REPORT
        expected_contract = EXTERNAL_REPORT
        report["external_binding"] = binding(report["external_binding"])
        if report["authority"] != AUTHORITY or report["turn_id"] != turn_id(report["external_binding"]):
            _fail("外部研究报告身份不匹配", "research_authority_mismatch")
    if report["contract"] != expected_contract:
        _fail("研究使用报告合同不匹配")
    _identity(report["turn_id"], "turn_id")
    _counter(report["research_ledger_revision"], "research_ledger_revision")
    if type(report["complete_for_turn"]) is not bool:
        _fail("complete_for_turn不合法")
    if not isinstance(report["entries"], list) or len(report["entries"]) > MAX_ENTRIES:
        _fail("研究使用报告条目不合法")
    report["entries"] = sorted(
        (_validate_usage(item) for item in report["entries"]),
        key=lambda item: item["research_id"],
    )
    _timestamp(report["reported_at"], "reported_at")
    return report


def _validate_ledger(value: object, *, conversation_id: str) -> dict[str, Any]:
    keys = {"contract", "conversation_id", "revision", "entries", "reports"}
    if isinstance(value, Mapping) and "external_context" in value:
        keys.add("external_context")
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail("研究账字段不完整")
    ledger = copy.deepcopy(dict(value))
    if ledger["contract"] != CONTRACT:
        _fail("研究账合同不匹配")
    if ledger["conversation_id"] != _identity(conversation_id, "conversation_id"):
        _fail("研究账不属于当前会话", "stale_context")
    _counter(ledger["revision"], "research_ledger_revision")
    if not isinstance(ledger["entries"], list) or len(ledger["entries"]) > MAX_ENTRIES:
        _fail("研究账条目不合法")
    ledger["entries"] = [_validate_entry(item) for item in ledger["entries"]]
    if "external_context" in ledger:
        from .native_research_external import validate_context_rows
        ledger["external_context"] = validate_context_rows(ledger)
    elif any("authority" in row for row in ledger["entries"]):
        _fail("外部研究缺少任务绑定", "research_binding_invalid")
    ids = [item["research_id"] for item in ledger["entries"]]
    calls = [item["call_id"] for item in ledger["entries"]]
    if len(set(ids)) != len(ids) or len(set(calls)) != len(calls):
        _fail("研究账身份重复", "call_id_conflict")
    if ledger["revision"] != len(ledger["entries"]):
        _fail("研究账revision与调用数量不一致")
    for entry in ledger["entries"]:
        if (
            entry["usage"] is not None
            and entry["usage"]["research_id"] != entry["research_id"]
        ):
            _fail("研究记录采用信息与research_id不匹配")
    if not isinstance(ledger["reports"], list) or len(ledger["reports"]) > MAX_REPORTS:
        _fail("研究使用报告列表不合法")
    ledger["reports"] = [_validate_report(item) for item in ledger["reports"]]
    report_keys = set()
    latest_by_turn: dict[str, dict[str, Any]] = {}
    for report in ledger["reports"]:
        external_binding = report.get("external_binding")
        if bool(external_binding) != ("external_context" in ledger):
            _fail("研究报告权威类型与账本不匹配", "research_authority_mismatch")
        if external_binding is not None:
            current = ledger["external_context"]
            if (any(external_binding[k] != current[k] for k in ("task_id", "candidate_id"))
                    or external_binding["candidate_revision"] > current["candidate_revision"]
                    or external_binding["candidate_revision"] == current["candidate_revision"] and external_binding != current):
                _fail("外部研究报告候选绑定不一致", "research_binding_invalid")
        key = (report["turn_id"], report["research_ledger_revision"],
               fingerprint(external_binding) if external_binding is not None else None)
        if key in report_keys:
            _fail("研究使用报告身份重复", "settlement_conflict")
        report_keys.add(key)
        report_revision = report["research_ledger_revision"]
        if report_revision > ledger["revision"] or not report["complete_for_turn"]:
            _fail("研究使用报告revision或闭合状态不合法")
        expected_ids = {
            item["research_id"]
            for item in ledger["entries"][:report_revision]
            if item["turn_id"] == report["turn_id"]
        }
        if external_binding is not None and any(
            item["task_binding"]["candidate_revision"] > external_binding["candidate_revision"]
            for item in ledger["entries"][:report_revision] if item["turn_id"] == report["turn_id"]
        ):
            _fail("外部报告引用了未来候选研究", "research_binding_invalid")
        if any(
            item["status"] == "pending"
            for item in ledger["entries"][:report_revision]
            if item["turn_id"] == report["turn_id"]
        ):
            _fail("研究使用报告覆盖了未结算调用")
        reported_ids = [item["research_id"] for item in report["entries"]]
        if len(set(reported_ids)) != len(reported_ids) or set(reported_ids) != expected_ids:
            _fail("研究使用报告没有精确覆盖对应revision")
        latest = latest_by_turn.get(report["turn_id"])
        rank = (report_revision, (external_binding or {}).get("candidate_revision", -1))
        latest_rank = (-1, -1) if latest is None else (latest["research_ledger_revision"],
            (latest.get("external_binding") or {}).get("candidate_revision", -1))
        if rank > latest_rank:
            latest_by_turn[report["turn_id"]] = report
    entries_by_id = {item["research_id"]: item for item in ledger["entries"]}
    for report in latest_by_turn.values():
        for usage in report["entries"]:
            if entries_by_id[usage["research_id"]]["usage"] != usage:
                _fail("研究记录采用信息与最新报告不匹配")
    if len(json_bytes(ledger)) > MAX_LEDGER_BYTES:
        _fail("研究账超过保存上限", "agent_size_limit")
    return ledger


class NativeResearchLedger:
    """Durable owner for one native conversation or one external task audit."""

    def __init__(
        self,
        root: str | Path,
        *,
        conversation_id: str,
        clock: Callable[[], str] = _now,
        id_factory: Callable[[], str] = _new_research_id,
    ) -> None:
        self.root = Path(root).absolute()
        self.conversation_id = _identity(conversation_id, "conversation_id")
        self.path = safe_path(self.root, LEDGER_FILE)
        self.lock_path = safe_path(self.root, LOCK_FILE)
        self._clock = clock
        self._id_factory = id_factory

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty(self.conversation_id)
        if self.path.is_symlink() or self.path.stat().st_size > MAX_LEDGER_BYTES:
            _fail("研究账不可读取", "agent_storage")
        try:
            value = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (OSError, UnicodeError, ValueError, RecursionError):
            _fail("研究账不是严格JSON", "agent_checkpoint_invalid")
        return _validate_ledger(value, conversation_id=self.conversation_id)

    def read(self) -> dict[str, Any]:
        with file_lock(self.lock_path):
            return copy.deepcopy(self._read_unlocked())

    def _write_unlocked(self, ledger: dict[str, Any]) -> None:
        checked = _validate_ledger(ledger, conversation_id=self.conversation_id)
        self.root.mkdir(parents=True, exist_ok=True)
        write_owned(self.root, LEDGER_FILE, checked, MAX_LEDGER_BYTES)

    def register(
        self,
        *,
        expected_revision: int,
        turn_id: str,
        epoch_id: str,
        workflow_stage: str,
        call_id: str,
        tool: str,
        arguments: Mapping[str, Any],
        raw_arguments_hash: str,
    ) -> dict[str, Any]:
        """Persist a Web attempt before the official DSH tool may go outbound."""

        _counter(expected_revision, "expected_research_ledger_revision")
        turn = _identity(turn_id, "turn_id")
        epoch = _identity(epoch_id, "epoch_id")
        stage = _identity(workflow_stage, "workflow_stage")
        call = _identity(call_id, "call_id")
        if tool not in _TOOLS:
            _fail("研究工具不在固定目录", "agent_tool_rejected")
        checked_args = _args(tool, arguments)
        checked_raw_hash = _hash(raw_arguments_hash, "raw_arguments_hash")
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            if "external_context" in ledger:
                _fail("外部任务不能登记为DSH调用", "research_authority_mismatch")
            existing = next(
                (item for item in ledger["entries"] if item["call_id"] == call),
                None,
            )
            if existing is not None:
                if (
                    existing["turn_id"] != turn
                    or existing["epoch_id"] != epoch
                    or existing["workflow_stage"] != stage
                    or existing["tool"] != tool
                    or existing["arguments"] != checked_args
                    or existing["raw_arguments_hash"] != checked_raw_hash
                ):
                    _fail("研究call_id已经绑定其他调用", "call_id_conflict")
                # The durable pre-dispatch write may have succeeded while its
                # ACK was lost.  Exact replay recovers the original identity
                # and is explicitly non-dispatchable: the coordinator must
                # reconcile the pending attempt instead of issuing it twice.
                return {
                    "status": "already_registered",
                    "research_id": existing["research_id"],
                    "research_ledger_revision": ledger["revision"],
                    "arguments_hash": existing["arguments_hash"],
                    "raw_arguments_hash": existing["raw_arguments_hash"],
                }
            if ledger["revision"] != expected_revision:
                _fail("研究账revision已变化", "stale_context")
            if len(ledger["entries"]) >= MAX_ENTRIES:
                _fail("研究账已满，请新建对话", "agent_size_limit")
            if any(item["call_id"] == call for item in ledger["entries"]):
                _fail("研究call_id已经存在", "call_id_conflict")
            research_id = self._id_factory()
            if not isinstance(research_id, str) or _RESEARCH_ID.fullmatch(research_id) is None:
                _fail("research_id生成失败", "agent_checkpoint_invalid")
            if any(item["research_id"] == research_id for item in ledger["entries"]):
                _fail("research_id已经存在", "call_id_conflict")
            row = _validate_entry(
                {
                    "research_id": research_id,
                    "call_id": call,
                    "turn_id": turn,
                    "epoch_id": epoch,
                    "workflow_stage": stage,
                    "tool": tool,
                    "arguments": checked_args,
                    "arguments_hash": fingerprint(checked_args),
                    "raw_arguments_hash": checked_raw_hash,
                    "started_at": self._clock(),
                    "status": "pending",
                    "completed_at": None,
                    "content_hash": None,
                    "sources": [],
                    "resource_ids": [],
                    "presentation_meta": None,
                    "usage": None,
                }
            )
            ledger["entries"].append(row)
            ledger["revision"] += 1
            self._write_unlocked(ledger)
            return {
                "status": "registered",
                "research_id": research_id,
                "research_ledger_revision": ledger["revision"],
                "arguments_hash": row["arguments_hash"],
                "raw_arguments_hash": row["raw_arguments_hash"],
            }

    def settle(
        self,
        *,
        research_id: str,
        status: str,
        content_hash: str | None = None,
        presentation_meta: Mapping[str, Any] | None = None,
        resource_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(research_id, str) or _RESEARCH_ID.fullmatch(research_id) is None:
            _fail("research_id不合法")
        if status not in _TERMINAL:
            _fail("研究终态不合法")
        if content_hash is not None:
            _hash(content_hash, "content_hash")
        checked_resources = _resource_ids(resource_ids)
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            if "external_context" in ledger:
                _fail("外部声明不能结算为DSH执行状态", "research_authority_mismatch")
            row = next(
                (item for item in ledger["entries"] if item["research_id"] == research_id),
                None,
            )
            if row is None:
                _fail("研究账没有对应research_id", "unknown_settlement")
            if status == "completed":
                if content_hash is None or presentation_meta is None:
                    _fail(
                        "成功研究结算缺少内容指纹或DSH展示元数据",
                        "invalid_tool_result",
                    )
                checked_presentation, checked_sources = _dsh_presentation(
                    row["tool"], presentation_meta
                )
            else:
                if presentation_meta is not None:
                    _fail(
                        "非成功研究结算不能携带DSH展示元数据",
                        "invalid_tool_result",
                    )
                checked_presentation, checked_sources = None, []
            proposed = copy.deepcopy(row)
            proposed.update(
                status=status,
                completed_at=(
                    row["completed_at"]
                    if row["status"] != "pending"
                    else self._clock()
                ),
                content_hash=content_hash,
                sources=checked_sources,
                resource_ids=checked_resources,
                presentation_meta=checked_presentation,
            )
            proposed = _validate_entry(proposed)
            if row["status"] != "pending":
                if proposed != row:
                    _fail("研究终态冲突", "settlement_conflict")
                return copy.deepcopy(row)
            index = ledger["entries"].index(row)
            ledger["entries"][index] = proposed
            self._write_unlocked(ledger)
            return copy.deepcopy(proposed)

    def list_calls(
        self,
        *,
        turn_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        turn = _identity(turn_id, "turn_id")
        _counter(offset, "offset")
        if type(limit) is not int or not 1 <= limit <= 100:
            _fail("limit不合法")
        ledger = self.read()
        if "external_context" in ledger:
            _fail("外部声明应使用外部任务查询", "research_authority_mismatch")
        rows = [item for item in ledger["entries"] if item["turn_id"] == turn]
        page = rows[offset:offset + limit]
        return {
            "contract": "recent-research-calls-v1",
            "research_ledger_revision": ledger["revision"],
            "turn_id": turn,
            "items": [
                {
                    "research_id": row["research_id"],
                    "tool": row["tool"],
                    "arguments": copy.deepcopy(row["arguments"]),
                    "status": row["status"],
                    "sources": copy.deepcopy(row["sources"]),
                    "resource_ids": copy.deepcopy(row["resource_ids"]),
                    "presentation_meta": copy.deepcopy(row["presentation_meta"]),
                }
                for row in page
            ],
            "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
            "total": len(rows),
        }

    def report_usage(
        self,
        *,
        turn_id: str,
        research_ledger_revision: int,
        entries: Sequence[Mapping[str, Any]],
        complete_for_turn: bool,
    ) -> dict[str, Any]:
        return self._report_usage(turn_id=turn_id,
            research_ledger_revision=research_ledger_revision, entries=entries,
            complete_for_turn=complete_for_turn)

    def _report_usage(
        self, *, turn_id: str, research_ledger_revision: int,
        entries: Sequence[Mapping[str, Any]], complete_for_turn: bool,
        external_binding=None,
    ) -> dict[str, Any]:
        turn = _identity(turn_id, "turn_id")
        _counter(research_ledger_revision, "research_ledger_revision")
        if type(complete_for_turn) is not bool:
            _fail("complete_for_turn不合法", "agent_tool_input")
        if (
            not isinstance(entries, Sequence)
            or isinstance(entries, (str, bytes))
            or len(entries) > MAX_ENTRIES
        ):
            _fail("研究使用报告条目不合法", "agent_tool_input")
        checked = sorted(
            (_validate_usage(item) for item in entries),
            key=lambda item: item["research_id"],
        )
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            if external_binding is None and "external_context" in ledger:
                _fail("外部任务需使用外部采用报告", "research_authority_mismatch")
            if external_binding is not None:
                from .native_research_external import check_context
                check_context(ledger, external_binding)
                ledger["external_context"] = external_binding
            if ledger["revision"] != research_ledger_revision:
                return {
                    "status": "stale_research_report",
                    "expected_revision": ledger["revision"],
                    "reported_revision": research_ledger_revision,
                }
            calls = [item for item in ledger["entries"] if item["turn_id"] == turn]
            expected_ids = {item["research_id"] for item in calls}
            reported_ids = [item["research_id"] for item in checked]
            duplicate_ids = sorted(
                item for item, count in Counter(reported_ids).items() if count > 1
            )
            unknown_ids = sorted(set(reported_ids) - expected_ids)
            missing_ids = sorted(expected_ids - set(reported_ids))
            unsettled_ids = sorted(
                item["research_id"] for item in calls if item["status"] == "pending"
            )
            if (
                not complete_for_turn
                or duplicate_ids
                or unknown_ids
                or missing_ids
                or unsettled_ids
            ):
                return {
                    "status": "incomplete_research_report",
                    "research_ledger_revision": ledger["revision"],
                    "missing_ids": missing_ids,
                    "duplicate_ids": duplicate_ids,
                    "unknown_ids": unknown_ids,
                    "unsettled_ids": unsettled_ids,
                    "complete_for_turn": complete_for_turn,
                }
            existing = next(
                (
                    item
                    for item in ledger["reports"]
                    if item["turn_id"] == turn
                    and item["research_ledger_revision"] == ledger["revision"]
                    and item.get("external_binding") == external_binding
                ),
                None,
            )
            if existing is not None:
                if existing["entries"] != checked or not existing["complete_for_turn"]:
                    _fail("同一revision的研究报告冲突", "settlement_conflict")
                return {
                    "status": "complete",
                    "research_ledger_revision": ledger["revision"],
                    "reported_count": len(checked),
                }
            usage_by_id = {item["research_id"]: item for item in checked}
            for item in calls:
                item["usage"] = copy.deepcopy(usage_by_id[item["research_id"]])
            report = _validate_report(
                {
                    "contract": REPORT_CONTRACT if external_binding is None else "external-research-usage-report-v1",
                    "turn_id": turn,
                    "research_ledger_revision": ledger["revision"],
                    "entries": checked,
                    "complete_for_turn": True,
                    "reported_at": self._clock(),
                    **({"external_binding": external_binding, "authority": "external_declared"}
                       if external_binding is not None else {}),
                }
            )
            if len(ledger["reports"]) >= MAX_REPORTS:
                ledger["reports"].pop(0)
            ledger["reports"].append(report)
            self._write_unlocked(ledger)
            return {
                "status": "complete",
                "research_ledger_revision": ledger["revision"],
                "reported_count": len(checked),
            }

    def append_external(self, *, context, request):
        from .native_research_external import append
        return append(self, context, request)

    def bind_external(self, *, context, expected_revision):
        from .native_research_external import bind
        return bind(self, context, expected_revision)

    def list_external(self, *, context, offset=0, limit=50):
        from .native_research_external import listing
        return listing(self, context, offset, limit)

    def report_external(self, *, context, research_ledger_revision, entries, complete_for_turn):
        from .native_research_external import binding, turn_id
        checked = binding(context)
        return self._report_usage(turn_id=turn_id(checked),
            research_ledger_revision=research_ledger_revision, entries=entries,
            complete_for_turn=complete_for_turn, external_binding=checked)


__all__ = [
    "CONTRACT",
    "LEDGER_FILE",
    "LOCK_FILE",
    "MAX_LEDGER_BYTES",
    "REPORT_CONTRACT",
    "NativeResearchLedger",
    "validate_research_arguments",
]
