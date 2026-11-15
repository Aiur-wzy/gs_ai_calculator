"""Public App research service for standalone CLI tasks.

The plan file is the current candidate authority; NativeResearchLedger remains
the sole journal/locking/validation owner. No arbitrary ledger root, DSH owner,
turn ID, execution status or model identity is accepted from CLI requests.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path

from genshin_app.agent_chat.native_research_external import (
    APPEND_REQUIRED,
    AUTHORITY,
    REPORT_CONTRACT,
)
from genshin_app.agent_chat.native_research_ledger import (
    MAX_ENTRIES,
    MAX_LEDGER_BYTES,
    MAX_QUERY_CHARS,
    MAX_RESOURCE_IDS,
    MAX_SNIPPET_CHARS,
    MAX_SOURCES,
    MAX_TITLE_CHARS,
    MAX_URL_CHARS,
    NativeResearchLedger,
    _counter,
    _fail,
    _identity,
    _strict_object,
    _validate_usage,
)
from genshin_app.agent_chat.native_research_tools import _USAGE_PROPERTIES
from genshin_app.equipment.files import fingerprint, input_view, read_plan
from genshin_db.locking import file_lock

REQUEST_CONTRACT = "external-research-request-v1"
SERVICE_CONTRACT = "genshin-external-research-service-v1"
_SCHEMAS = {
    "append": {"type": "object", "additionalProperties": False,
        "required": sorted(APPEND_REQUIRED),
        "properties": {
            "contract": {"const": REQUEST_CONTRACT},
            "request_id": {"type": "string", "maxLength": 128},
            "expected_ledger_revision": {"type": "integer", "minimum": 0},
            "candidate_revision": {"type": "integer", "minimum": 0},
            "query": {"type": ["string", "null"], "minLength": 1, "maxLength": MAX_QUERY_CHARS},
            "sources": {"type": "array", "maxItems": MAX_SOURCES,
                "items": {"type": "object", "additionalProperties": False,
                    "required": ["url"], "properties": {
                        "url": {"type": "string", "maxLength": MAX_URL_CHARS},
                        "title": {"type": ["string", "null"], "maxLength": MAX_TITLE_CHARS},
                        "snippet": {"type": ["string", "null"], "maxLength": MAX_SNIPPET_CHARS},
                        "publishedAt": {"type": ["string", "null"], "maxLength": 64},
                        "date": {"type": ["string", "null"], "maxLength": 64}}}},
            "source_observed_at": {"type": ["string", "null"], "maxLength": 64},
            "content_hash": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
            "resource_ids": {"type": "array", "maxItems": MAX_RESOURCE_IDS,
                "items": {"type": "string", "maxLength": 128}}}},
    "bind": {"type": "object", "additionalProperties": False,
        "required": ["expected_ledger_revision", "candidate_revision"],
        "properties": {"expected_ledger_revision": {"type": "integer", "minimum": 0},
                       "candidate_revision": {"type": "integer", "minimum": 0}}},
    "report": {"type": "object", "additionalProperties": False,
        "required": ["contract", "candidate_revision", "research_ledger_revision", "entries", "complete_for_turn"],
        "properties": {"contract": {"const": REPORT_CONTRACT},
            "candidate_revision": {"type": "integer", "minimum": 0},
            "research_ledger_revision": {"type": "integer", "minimum": 0},
            "complete_for_turn": {"type": "boolean"},
            "entries": {"type": "array", "maxItems": MAX_ENTRIES,
                "items": {"type": "object", "additionalProperties": False,
                    "required": list(_USAGE_PROPERTIES), "properties": copy.deepcopy(_USAGE_PROPERTIES)}}}},
}


def research_capabilities():
    return {"contract": SERVICE_CONTRACT, "authority": AUTHORITY,
        "storage_owner": "NativeResearchLedger", "network_execution_verified": False,
        "public_knowledge_authority": "none", "database_write": False,
        "candidate": "--plan实际读取的plan_id、revision及input指纹；--task-id只用于外部任务命名空间",
        "native_facts": "DSH原始调用、时间、工具参数、模型身份与执行状态只读；本接口不能写入或伪造",
        "rebind": "同任务候选ID固定；revision前进后先research bind，旧记录原绑定保留；同revision不同输入拒绝",
        "request_bytes_limit": MAX_LEDGER_BYTES,
        "schemas": copy.deepcopy(_SCHEMAS),
        "usage": ["research append --plan PLAN --task-id TASK --input REQUEST.json",
                  "research list --plan PLAN --task-id TASK --offset 0 --limit 50",
                  "research bind --plan PLAN --task-id TASK --input BIND.json",
                  "research report --plan PLAN --task-id TASK --input REPORT.json"],
        "timestamps": "registered_at由App产生；source_observed_at仅为调用者声明的查阅时间，不证明已联网"}


def read_request(path):
    source = Path(path)
    from genshin_app.equipment.files import read_input_bytes
    raw = read_input_bytes(source, maximum_bytes=MAX_LEDGER_BYTES)
    if len(raw) > MAX_LEDGER_BYTES:
        _fail("研究请求超过既有研究账封套", "agent_size_limit")
    try:
        value = json.loads(raw.decode("utf-8-sig"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (UnicodeError, ValueError, RecursionError) as error:
        _fail("研究请求必须是严格JSON对象", "research_external_input", {"error_kind": type(error).__name__})
    if type(value) is not dict:
        _fail("研究请求必须是对象", "research_external_input")
    return value


def _reference_ids(plan):
    result = {plan["plan_id"]}
    def walk(value):
        if isinstance(value, Mapping):
            for key, item in value.items():
                # App contracts use typed identities such as node_id, part_id,
                # channel_id and source_id, not only generic id. Keep the scope
                # tied to this exact candidate rather than an ever-growing list
                # which rejects newly supported topology/parameter families.
                if (key == "id" or key.endswith("_id")) and isinstance(item, str) and item:
                    result.add(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(input_view(plan))
    return result


def ledger_for_external_task(data_dir, task_id):
    task = _identity(task_id, "task_id")
    conversation = "external-task." + fingerprint(task)[:48]
    return NativeResearchLedger(Path(data_dir).resolve() / "native-dsh-research" / conversation,
        conversation_id=conversation)


class ExternalResearchService:
    def __init__(self, data_dir, plan_path, task_id):
        self.data_dir = Path(data_dir).resolve()
        self.plan_path = Path(plan_path).resolve()
        self.task_id = _identity(task_id, "task_id")
        self.conversation_id = "external-task." + fingerprint(self.task_id)[:48]

    def _ledger(self):
        return ledger_for_external_task(self.data_dir, self.task_id)

    def call(self, operation, request=None, *, offset=0, limit=50):
        with file_lock(self.plan_path.with_name(f".{self.plan_path.name}.plan.lock")):
            plan = read_plan(self.plan_path)["plan"]
            context = {"task_id": self.task_id, "candidate_id": plan["plan_id"],
                       "candidate_revision": plan["revision"], "candidate_fingerprint": fingerprint(input_view(plan))}
            store = self._ledger()
            if operation == "list":
                return store.list_external(context=context, offset=offset, limit=limit)
            if not isinstance(request, Mapping):
                _fail("研究操作需要JSON请求", "research_external_input")
            if request.get("candidate_revision") != context["candidate_revision"]:
                _fail("请求中的候选版本与实际文件不同", "stale_research_binding")
            _counter(request["candidate_revision"], "candidate_revision")
            if operation == "append":
                return store.append_external(context=context, request=request)
            if operation == "bind":
                if set(request) != set(_SCHEMAS["bind"]["required"]):
                    _fail("绑定请求字段不合法", "research_external_input")
                return store.bind_external(context=context, expected_revision=request["expected_ledger_revision"])
            if operation == "report":
                required = set(_SCHEMAS["report"]["required"])
                if set(request) != required or request["contract"] != REPORT_CONTRACT:
                    _fail("外部采用报告字段不合法", "research_external_input")
                allowed = _reference_ids(plan)
                with file_lock(store.lock_path):
                    snapshot = store._read_unlocked()
                    for row in snapshot["entries"]:
                        allowed.update(row.get("resource_ids", []))
                    if not isinstance(request["entries"], list):
                        _fail("采用记录需要列表", "research_external_input")
                    checked_entries = [_validate_usage(entry) for entry in request["entries"]]
                    for entry in checked_entries:
                        if any(ref not in allowed for ref in entry["applied_to"]):
                            _fail("采用引用不属于当前候选或本任务资料", "research_reference_out_of_scope")
                        if entry.get("disposition") == "adopted" and not entry["applied_to"]:
                            _fail("采用条目必须引用当前候选或资料ID", "research_reference_required")
                    result = store.report_external(context=context,
                        research_ledger_revision=request["research_ledger_revision"], entries=request["entries"],
                        complete_for_turn=request["complete_for_turn"])
                return {**result, "authority": AUTHORITY, "binding": context, "network_execution_verified": False}
            _fail("未知外部研究操作", "research_external_input")
