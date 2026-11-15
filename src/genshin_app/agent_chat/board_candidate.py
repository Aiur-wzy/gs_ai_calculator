"""Reviewed whiteboard construction, deliberately separate from calculation repair."""
from __future__ import annotations

import copy
import json
import threading
import uuid
from pathlib import Path

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.panel_basis import character_panel_basis
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import (
    default_panel,
    pending_items,
    prepare_whiteboard_edit,
    validate_whiteboard,
    validate_whiteboard_revision_transition,
)
from genshin_db import queries
from genshin_db.equipment import EquipmentError

from .candidate import CandidateSession, _diff
from .sessions import safe_path

_IDENTITY = ("kind", "schema_version", "board_id", "version_line")
_BASE_KEYS = ("base_atk", "base_hp", "base_def")


def whiteboard_schema():
    """Bounded examples provide the editable structure without inventing game facts."""
    return {"workflow": "build", "kind": "genshin-whiteboard", "schema_version": 3,
            "identity_locked": list(_IDENTITY), "revision": "由App递增，不可手动修改",
            "editable": ["title", "notes", "duration", "team", "actions", "capacity_intents",
                         "action_admission_intents", "provisional_entities"],
            "examples_are_placeholders": "以下ID和名称仅表示结构；公共身份必须先用gs_search_catalog查询，名称按数据库归一。",
            "actor": {"id": "actor-1", "db_character_id": 1, "name": "数据库角色名", "level": 90,
                      "character_ref": {"kind": "database", "entity_type": "character",
                                        "database_id": 1, "version_line": "release"},
                      "constellation": 0, "weapon": None, "artifact_sets": [], "panel": default_panel(),
                      "panel_basis_mode": "database", "notes": ""},
            "weapon": {"weapon_id": 1, "entity_ref": {"kind": "database", "entity_type": "weapon",
                        "database_id": 1, "version_line": "release"},
                        "name": "数据库武器名", "level": 90, "ascension": 6, "refinement": 1},
            "artifact_set": {"set_id": 1, "entity_ref": {"kind": "database",
                             "entity_type": "artifact_set", "database_id": 1,
                             "version_line": "release"}, "name": "数据库套装名", "count": 4},
            "provisional_identity_policy": (
                "资料查询未找到所需公共实体时，白板可保存current_plan_only临时实体提案并由team的"
                "discriminated entity_ref引用；此阶段不得携带database_lookup、attestation、"
                "approval或payload_fingerprint，也不把普通搜索当成正式缺失证明。交接后必须在当前原生Agent方案重新走D2精确核对链，"
                "否则Composer拒绝计算。临时实体不得伪造数据库ID或写回公共库。"
            ),
            "action": {"id": "action-1", "character": "actor-1", "action_type_id": "action.elemental_skill",
                       "label": "元素战技", "start": 0, "variant": "", "notes": ""},
            "capacity_intent": {"id": "capacity-1", "actor": "actor-1", "action_ids": ["action-1"],
                "version_line": "release", "model_ref": {"id": "已查得模型ID", "revision": 1},
                "target": {"character_id": 1, "skill_depot_id": 1, "skill_id": 1, "variant": "tap"},
                "cooldown": None, "initial_ready_in": None, "context": None},
            "action_admission_intent": {
                "id": "admission-1", "actor": "actor-1", "action_id": "action-1",
                "version_line": "release",
                "selection": {"character_id": 1, "skill_depot_id": 1, "skill_id": 1,
                              "slot": "burst", "constellation": 0},
                "variant": "default",
                "model_ref": {"id": "已查得模型ID", "revision": 1},
                "initial_ready_in": None, "context": None,
            },
            "limits": {"team": 4, "actions": 1000, "duration": 3600, "artifact_piece_total": 5},
            "actions_policy": "只能引用队内角色及capabilities列出的action_types，按start升序且0≤start<duration，同秒列表顺序即动作顺序。",
            "panel_policy": "未知成品面板保留null；database模式基础三维由App读取，manual模式允许显式填写。",
            "capacity_policy": "意图只记录已查得模型的精确revision与动作集合；context默认null。非空场景必须一致，混合null时保持待补，不签发执行确认或容量值。",
            "action_admission_policy": "首片只接受release、C0、default元素爆发；每个动作最多一个意图。context与initial_ready_in默认均为null，只有用户显式选择普通开放世界预设才同时写固定场景与[]。草稿不保存accepted_fingerprint或私有marker。",
            "application": "每轮只生成候选草稿，用户审核应用后可继续下一轮；准备完整后切换计算完善。"}


def _source(actor):
    weapon = actor.get("weapon") or {}
    return (actor.get("character_ref"), actor.get("db_character_id"), actor["level"],
            weapon.get("entity_ref"),
            *(weapon.get(key) for key in ("weapon_id", "level", "ascension")))


def _entity_ref(value, entity_type, database_id, line):
    return copy.deepcopy(value) if value is not None else {
        "kind": "database", "entity_type": entity_type,
        "database_id": database_id, "version_line": line,
    }


class BoardCandidateSession(CandidateSession):
    workflow = "build"

    def __init__(self, session_id, root, db_path, kind, document, *, delivery_root=None):
        uuid.UUID(session_id)
        if kind != "whiteboard":
            raise PlanError("共创工作流只接受白板", "agent_document_kind")
        validate_whiteboard(document)
        self.session_id, self.root, self.db_path, self.kind = session_id, Path(root).absolute(), Path(db_path).resolve(), kind
        self.delivery_root = Path(delivery_root).absolute() if delivery_root else self.db_path.parent / "agent_deliveries"
        self._lock, self.closed = threading.RLock(), False
        if safe_path(self.root, "checkpoint.json").exists():
            raise PlanError("会话已存在，请载入历史记录", "agent_session_exists")
        self.document = copy.deepcopy(document)
        self.document_fingerprint = fingerprint(self.document)
        self.candidate, self.baseline = copy.deepcopy(document), copy.deepcopy(document)
        with self._read_db(check=False):
            self.database_snapshot = self._db_snapshot()
        from .provisional_authority import new_state as new_provisional_authority
        self.provisional_authority = new_provisional_authority(
            self.session_id, fingerprint(self.database_snapshot), self.candidate,
        )
        self.revision, self.calculated, self.report = 0, None, None
        self.resources, self.findings, self.changes = {}, [], []
        self.save()

    def _validate_intent(self, candidate):
        validate_whiteboard(candidate)
        for key in _IDENTITY:
            if candidate[key] != self.document[key]:
                raise PlanError("白板身份和版本线不能通过共创修改", "agent_intent_mismatch", {"path": key})

    def _sync_basis(self, candidate, *, previous_candidate=None):
        source = self.candidate if previous_candidate is None else previous_candidate
        previous = {actor["id"]: actor for actor in source["team"]}
        for actor in candidate["team"]:
            old = previous.get(actor["id"])
            character_ref = _entity_ref(
                actor.get("character_ref"), "character",
                actor.get("db_character_id"), candidate["version_line"])
            weapon = actor.get("weapon") or {}
            weapon_ref = (_entity_ref(
                weapon.get("entity_ref"), "weapon", weapon.get("weapon_id"),
                candidate["version_line"])
                if weapon else None)
            if actor.get("panel_basis_mode", "database") != "database":
                # A change of character, level or weapon invalidates inherited
                # manual white values. Different values supplied in this same
                # edit are deliberate replacements and stay available to review.
                if old is not None and _source(old) != _source(actor):
                    for key in _BASE_KEYS:
                        if actor["panel"].get(key) == old["panel"].get(key):
                            actor["panel"][key] = None
                continue
            if (character_ref["kind"] == "provisional"
                    or weapon_ref is not None and weapon_ref["kind"] == "provisional"):
                # Whiteboard editing preserves the declaration but never
                # consumes an Agent-authored public value.  Native handoff and
                # the D2 resolver own that authority boundary.
                if old is not None and _source(old) != _source(actor):
                    for key in _BASE_KEYS:
                        if actor["panel"].get(key) == old["panel"].get(key):
                            actor["panel"][key] = None
                continue
            if (old is not None and old.get("panel_basis_mode", "database") == "database"
                    and _source(old) == _source(actor)
                    and all(old["panel"].get(key) == actor["panel"].get(key) for key in _BASE_KEYS)):
                continue
            basis = character_panel_basis(self.db_path, actor["db_character_id"], actor["level"],
                                          candidate["version_line"], actor["weapon"])
            actor["panel"].update(copy.deepcopy(basis["panel_base"]))

    def _normalize_catalog(self, candidate, *, previous_candidate=None):
        source = self.candidate if previous_candidate is None else previous_candidate
        previous = {actor["id"]: actor for actor in source["team"]}
        line = candidate["version_line"]
        registry = {(row["entity_type"], row["provisional_id"]): row
                    for row in candidate.get("provisional_entities", [])}
        for actor in candidate["team"]:
            old = previous.get(actor["id"])
            character_ref = _entity_ref(actor.get("character_ref"), "character",
                                        actor.get("db_character_id"), line)
            changed_character = old is None or any(
                actor.get(key) != old.get(key)
                for key in ("character_ref", "db_character_id", "name"))
            weapon, old_weapon = actor["weapon"], old["weapon"] if old else None
            changed_weapon = weapon is not None and (changed_character or old_weapon is None
                or any(weapon.get(key) != old_weapon.get(key)
                       for key in ("entity_ref", "weapon_id", "name")))
            try:
                character = None
                if character_ref["kind"] == "provisional":
                    row = registry.get(("character", character_ref["provisional_id"]))
                    if row is None:
                        raise PlanError("临时角色不属于当前白板登记表", "entity_ref_registry_missing")
                    actor["name"] = row["display_name"]
                elif changed_character or changed_weapon:
                    detail = queries.get_character_detail(self.db_path, actor["db_character_id"], line, sections=["character"])
                    character = detail["character"]
                    actor["name"] = character["name"]
                if changed_weapon:
                    weapon_ref = _entity_ref(weapon.get("entity_ref"), "weapon",
                                             weapon.get("weapon_id"), line)
                    if weapon_ref["kind"] == "provisional":
                        row = registry.get(("weapon", weapon_ref["provisional_id"]))
                        if row is None:
                            raise PlanError("临时武器不属于当前白板登记表", "entity_ref_registry_missing")
                        weapon["name"] = row["display_name"]
                    else:
                        detail = queries.get_weapon_detail(self.db_path, weapon["weapon_id"], line, sections=["weapon"])
                        selected = detail["weapon"]
                        if character is None and character_ref["kind"] == "database":
                            detail = queries.get_character_detail(
                                self.db_path, actor["db_character_id"], line,
                                sections=["character"])
                            character = detail["character"]
                        if character is not None and (
                                not character.get("weapon_type") or not selected.get("weapon_type")):
                            raise PlanError("资料缺少角色或武器类型，无法确认配装", "agent_weapon_type_unknown")
                        if (character is not None
                                and character["weapon_type"] != selected["weapon_type"]):
                            raise PlanError("所选武器类型与角色不匹配", "agent_weapon_type_mismatch")
                        weapon["name"] = selected["name"]
                old_sets = {
                    json.dumps(item.get("entity_ref") or item.get("set_id"), sort_keys=True): item
                    for item in old["artifact_sets"]
                } if old else {}
                for selected in actor["artifact_sets"]:
                    set_ref = _entity_ref(selected.get("entity_ref"), "artifact_set",
                                          selected.get("set_id"), line)
                    previous_set = old_sets.get(json.dumps(
                        selected.get("entity_ref") or selected.get("set_id"), sort_keys=True))
                    if previous_set is None or previous_set["name"] != selected["name"]:
                        if set_ref["kind"] == "provisional":
                            row = registry.get(("artifact_set", set_ref["provisional_id"]))
                            if row is None:
                                raise PlanError("临时套装不属于当前白板登记表", "entity_ref_registry_missing")
                            selected["name"] = row["display_name"]
                        else:
                            detail = queries.get_artifact_set_detail(self.db_path, selected["set_id"], line, sections=["artifact_set"])
                            selected["name"] = detail["artifact_set"]["name"]
            except (queries.NotFoundError, EquipmentError) as exc:
                raise PlanError("角色或装备身份未被当前数据库确认，请先搜索公共资料", "agent_catalog_identity_invalid") from exc

    def replace_candidate(self, candidate, expected_revision, reason):
        with self._lock, self._read_db():
            self._expect(expected_revision)
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
                raise PlanError("修改需简要说明理由", "agent_tool_input")
            self._validate_intent(candidate)
            prepared = copy.deepcopy(candidate)
            prepared["revision"] = self.candidate["revision"]
            self._normalize_catalog(prepared)
            self._sync_basis(prepared)
            self._validate_intent(prepared)
            if prepared == self.candidate:
                return {**self.summary(), "edit_status": "no_op"}
            fields = sorted(key for key in prepared if prepared[key] != self.candidate[key])
            prepared = prepare_whiteboard_edit(self.candidate, prepared)
            from .skill_context import input_change_paths
            input_paths = input_change_paths(self.candidate, prepared)
            previous = (self.candidate, self.revision, self.changes,
                        copy.deepcopy(self.provisional_authority),
                        self._candidate_replace_extension_state())
            previous_candidate = self.candidate
            self.candidate, self.revision = prepared, self.revision + 1
            self.changes = [*self.changes, {"revision": self.revision, "message": reason, "fields": fields,
                "input_paths": input_paths}][-100:]
            try:
                self._after_candidate_replaced(previous_candidate)
                from .provisional_authority import invalidate_after_candidate_change
                invalidate_after_candidate_change(self)
                self.save()
            except Exception:
                (self.candidate, self.revision, self.changes,
                 self.provisional_authority, extension_state) = previous
                self._restore_candidate_replace_extension_state(extension_state)
                raise
            return {**self.summary(), "edit_status": "committed", "input_fingerprint": fingerprint(self.candidate)}

    def add_character(self, db_character_id, expected_revision, reason, *, actor_id=None, level=90, constellation=0):
        self._expect(expected_revision)
        with self._read_db():
            try:
                detail = queries.get_character_detail(self.db_path, db_character_id, self.candidate["version_line"],
                                                      sections=["character"])
            except queries.NotFoundError as exc:
                raise PlanError("当前资料版本未收录该角色，请先查询数据库角色ID", "agent_character_not_found") from exc
            character = detail["character"]
            actor = {"id": actor_id or "character-" + str(db_character_id), "db_character_id": db_character_id,
                     "character_ref": {"kind": "database", "entity_type": "character",
                                       "database_id": db_character_id,
                                       "version_line": self.candidate["version_line"]},
                     "name": character["name"], "level": level, "constellation": constellation,
                     "weapon": None, "artifact_sets": [], "panel": default_panel(), "notes": "",
                     "panel_basis_mode": "database"}
            value = copy.deepcopy(self.candidate)
            value["team"].append(actor)
            return self.replace_candidate(value, expected_revision, reason)

    def compose(self, expected_revision):
        raise PlanError("白板共创只构建草稿；应用后切换到计算完善", "agent_tool_forbidden")

    calculate = compose

    def _receipt(self):
        return fingerprint({"workflow": self.workflow, "revision": self.revision, "board": self.candidate})

    def summary(self):
        with self._read_db(check=False):
            same_database = fingerprint(self._db_snapshot()) == fingerprint(self.database_snapshot)
        pending = pending_items(self.candidate)
        for actor in self.candidate["team"]:
            unknown = [key for key in _BASE_KEYS if actor["panel"].get(key) is None]
            if unknown:
                labels = {"base_atk": "基础攻击", "base_hp": "基础生命", "base_def": "基础防御"}
                pending.append({"code": "unknown_panel_basis", "path": "team." + actor["id"] + ".panel",
                                "message": actor["name"] + "尚未确定" + "、".join(labels[key] for key in unknown)
                                           + "；草稿可应用，计算前需确认。"})
        if not same_database:
            pending.append({"code": "agent_database_changed", "message": "资料库来源已变化，草稿不能应用"})
        rows, total = _diff(self.baseline, self.candidate)
        changed = total > 0
        missing = [{"code": item["code"], "path": item.get("path", ""), "message": item["message"][:512]}
                   for item in pending[:80]]
        result = {"session_id": self.session_id, "kind": self.kind, "workflow": self.workflow,
                "application_kind": "whiteboard_draft", "revision": self.revision,
                "has_calculation": False, "receipt_fingerprint": self._receipt(),
                "can_accept": changed and same_database, "ready": changed and same_database,
                "missing_inputs": missing, "missing_inputs_total": len(pending),
                "missing_inputs_truncated": len(missing) < len(pending),
                "issues": copy.deepcopy(missing), "issues_total": len(pending),
                "issues_truncated": len(missing) < len(pending),
                "changes": [{**row, "message": row["message"][:512]} for row in self.changes[-30:]],
                "diff": rows, "diff_total": total, "diff_truncated": len(rows) < total,
                "summary": {"characters": len(self.candidate["team"]), "actions": len(self.candidate["actions"]),
                            "duration": self.candidate["duration"]},
                "database_current": same_database, "finding_count": len(self.findings)}
        from .candidate_readiness import readiness_projection
        result.update(readiness_projection(result))
        return result

    def accept(self, kind, current_document, expected_revision, expected_receipt):
        # Returning a reviewed value is not evidence that the UI has applied it.
        with self._lock, self._read_db():
            self._expect(expected_revision)
            validate_whiteboard(current_document)
            if kind != self.kind or fingerprint(current_document) != self.document_fingerprint:
                raise PlanError("当前白板已变化，不能覆盖；候选可保留查看", "agent_document_changed")
            if expected_receipt != self._receipt() or _diff(self.baseline, self.candidate)[1] == 0:
                raise PlanError("审核的草稿回执已变化或没有新修改", "agent_stale_receipt")
            self._validate_intent(self.candidate)
            validate_whiteboard_revision_transition(current_document, self.candidate)
            return {"kind": "whiteboard", "workflow": self.workflow, "application_kind": "whiteboard_draft",
                    "revision": self.revision, "receipt_fingerprint": expected_receipt,
                    "board": copy.deepcopy(self.candidate)}

    def resume_document(self, document):
        """Advance the baseline only when the next turn sends the applied board."""
        with self._lock, self._read_db():
            validate_whiteboard(document)
            if fingerprint(document) == self.document_fingerprint:
                return self.summary()
            if document != self.candidate:
                raise PlanError("当前白板在会话外已变化，请建立新的共创会话", "agent_document_changed")
            validate_whiteboard_revision_transition(self.document, document)
            previous = self.document, self.baseline, self.document_fingerprint, self.changes
            self.document, self.baseline = copy.deepcopy(document), copy.deepcopy(document)
            self.document_fingerprint, self.changes = fingerprint(document), []
            try:
                self.save()
            except Exception:
                self.document, self.baseline, self.document_fingerprint, self.changes = previous
                raise
            return self.summary()
