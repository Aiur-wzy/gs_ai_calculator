"""Prepare the same task-specific entity notice for App and standalone CLI."""
from __future__ import annotations

import re

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_db.equipment_queries import borrow_read_connection, equipment_read_session
from genshin_db.publication_evidence import ENTITY_TYPES, publication_by_id

CONTRACT = "genshin-publication-usage-v1"
APPROVE = "同意本任务使用"
DECLINE = "不同意使用"
_ENTITY_LABELS = {"character": "角色", "weapon": "武器", "artifact_set": "圣遗物套装"}
_STATUS_LABELS = {"unreleased": "尚未正式上线", "unknown": "目前无法核实是否已上线",
                  "conflicting": "上线证据存在冲突，暂不能核实"}
_PURPOSES = {"answer": "回答本任务的问题", "calculation": "完成本任务的配装、排轴及计算"}
_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_ENTITIES = 24


def prepare_publication_usage(db_path, request, *, task_id, records=()):
    if (not isinstance(request, dict) or set(request) - {"entities", "purpose", "scope"}
            or not isinstance(request.get("purpose"), str) or request["purpose"] not in _PURPOSES):
        raise PlanError("资料采用请求需要 entities 与 purpose（answer 或 calculation）", "invalid_publication_usage")
    entities = request.get("entities")
    scope = request.get("scope", "current-task")
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        raise PlanError("scope 使用同一任务内稳定的标识，仅在明确扩大用途时另设",
                        "invalid_publication_usage")
    if not isinstance(entities, list) or not 1 <= len(entities) <= _MAX_ENTITIES:
        raise PlanError(f"列出本任务实际拟采用的 1–{_MAX_ENTITIES} 个角色、武器或套装",
                        "invalid_publication_usage")
    groups = {}
    for entity in entities:
        if (not isinstance(entity, dict) or set(entity) - {"type", "id", "version_line"}
                or entity.get("type") not in ENTITY_TYPES or type(entity.get("id")) is not int
                or not 1 <= entity["id"] <= 2**53 - 1
                or entity.get("version_line", "release") not in ("release", "beta")):
            raise PlanError("每个实体需要有效 type、数据库原始 id 和可选 version_line",
                            "invalid_publication_usage")
        group = (entity["type"], entity.get("version_line", "release"))
        groups.setdefault(group, set()).add(entity["id"])
    if not db_path.is_file():
        raise PlanError("当前没有可读取的数据库，无法核对所列实体", "database_missing")
    rows = []
    with equipment_read_session(db_path):
        connection = borrow_read_connection(db_path)
        for (kind, line), ids in sorted(groups.items()):
            markers = ",".join("?" for _ in ids)
            # kind is restricted to ENTITY_TYPES above. The names come from
            # the same snapshot as the evidence, never from model arguments.
            present = dict(connection.execute(
                f'SELECT id,name FROM "{kind}" WHERE version_line=? AND id IN ({markers})',
                (line, *sorted(ids))).fetchall())
            if set(present) != ids:
                raise PlanError("所列实体在指定资料线不存在，请从目录查询准确身份",
                                "publication_usage_entity_not_found",
                                {"entity_type": kind, "version_line": line, "ids": sorted(ids - present.keys())})
            states = publication_by_id(connection, kind, sorted(ids), line)
            for ident, row in states.items():
                name = " ".join((present[ident] or "").split())[:160] or f"ID {ident}"
                status = _STATUS_LABELS.get(row["entity_status"])
                rows.append({**row, "name": name, "usage_key": f"{kind}:{ident}",
                             "question_label": f"{name}（{_ENTITY_LABELS[kind]}，{status}）" if status else None})
    # Entity usage does not become a separate gate on a new content snapshot.
    # Keep any selected line's more cautious state without duplicating the
    # same entity in a user's question.
    distinct = {}
    for row in rows:
        old = distinct.get(row["usage_key"])
        if old is None or row["requires_usage_confirmation"] and not old["requires_usage_confirmation"]:
            distinct[row["usage_key"]] = row
    ordered = [distinct[name] for name in sorted(distinct)]
    value = {"contract": CONTRACT, "task_id": task_id, "purpose": request["purpose"],
             "scope": scope, "entities": ordered,
             "question_prompt": {
                 "prefix": f"为{_PURPOSES[request['purpose']]}，拟使用",
                 "suffix": "。资料来自上游公开仓库，可能变化，仅供学习参考。是否同意本任务使用这些资料？",
             },
             "authority": "usage_notice_only", "acceptance_effect": "none"}
    value = project_host_answer_references(value, records)
    return {**value, "question": publication_question(value),
            "decision_source": "host_user_response_required"}


def publication_question(value):
    pending = [row for row in value["entities"] if row["requires_usage_confirmation"]
               and row.get("response", {}).get("status", "ask_user") == "ask_user"]
    if not pending:
        return None
    identity = fingerprint([value["task_id"], value["scope"], value["purpose"],
                            sorted(row["usage_key"] for row in pending)])[:24]
    descriptions = "、".join(row["question_label"] for row in pending)
    template = value["question_prompt"]
    return {"id": f"genshin-publication-{identity}", "header": "确认使用资料",
            "question": template["prefix"] + descriptions + template["suffix"],
            "options": [{"label": APPROVE}, {"label": DECLINE}]}


def project_host_answer_references(value, records):
    """Standalone CLI indexes host answer references without attesting to their contents."""
    responses = {}
    for row in records:
        if row.get("type") != "decision":
            continue
        for ref in row.get("source_refs", []):
            if (ref.get("kind") != "host_publication_answer" or ref.get("scope") != value["scope"]
                    or ref.get("purpose") != value["purpose"] or ref.get("answer") not in ("approved", "declined")
                    or not isinstance(ref.get("reference"), str) or not ref["reference"].strip()
                    or not isinstance(ref.get("entities"), list) or not 1 <= len(ref["entities"]) <= _MAX_ENTITIES
                    or any(not isinstance(entity, str) for entity in ref["entities"])):
                continue
            subject = sorted(set(ref["entities"]))
            expected = fingerprint([value["task_id"], value["scope"], value["purpose"], subject])[:24]
            if ref.get("question_id") != f"genshin-publication-{expected}":
                continue
            for entity in ref["entities"]:
                responses[entity] = {"status": "host_answer_reference", "answer": ref["answer"],
                                     "reference": ref["reference"], "question_id": ref["question_id"],
                                     "verified_by_app": False}
    return {**value, "entities": [
        {**row, "response": (responses.get(row["usage_key"], {"status": "ask_user"})
                             if row["requires_usage_confirmation"] else {"status": "not_required"})}
        for row in value["entities"]
    ]}
