"""Session-owned calculation candidates with reviewed edits and stable document identity."""
from __future__ import annotations

import copy
import re
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from genshin_app.default_assistance import compact_default_assistance
from genshin_app.equipment.composer import compose_equipment_plan
from genshin_app.equipment.delivery import (
    PACKAGE_KIND,
    PACKAGE_SCHEMA,
    validate_delivery_calculated,
)
from genshin_app.equipment.files import fingerprint, input_view, json_bytes
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.equipment.service import require_ready, run_plan_calc
from genshin_app.whiteboard import (
    board_fingerprint,
    make_handoff,
    prepare_whiteboard_edit,
    validate_result_intent,
    validate_whiteboard,
    validate_whiteboard_capacity_result,
    validate_whiteboard_revision_transition,
)
from genshin_app.whiteboard_equipment import _diagnostics
from genshin_db import queries
from genshin_db.equipment_queries import equipment_read_session

from .sessions import (
    MAX_CALCULATED,
    MAX_CHECKPOINT,
    MAX_NATIVE,
    read_owned,
    safe_path,
    write_owned,
)


def _native(plan):
    from genshin_app.equipment.files import native_input
    return native_input(plan)


def _artifact_name(name, prefix):
    if not isinstance(name, str) or not re.fullmatch(prefix + r"-[0-9a-f]{64}\.json", name):
        raise PlanError("会话内容文件索引不合法", "agent_checkpoint_invalid")
    return name


def _equal(actual, expected, path):
    if fingerprint(actual) != fingerprint(expected):
        raise PlanError("候选文档身份或完整性不一致", "agent_intent_mismatch", {"path": path})


def _diff(before, after):
    rows, total, used = [], 0, 0
    def preview(value, resource, pointer):
        raw = json_bytes(value)
        if len(raw) <= 512:
            return copy.deepcopy(value)
        return {"preview": "值较大，请定向展开", "type": type(value).__name__,
                "count": len(value) if isinstance(value, (dict, list, str)) else None,
                "resource": resource, "pointer": pointer}
    def entry(left, right, path):
        nonlocal total, used
        total += 1
        if len(rows) >= 200:
            return
        row = {"path": path, "before": preview(left, "baseline", path),
               "after": preview(right, "candidate", path)}
        size = len(json_bytes(row))
        if used + size <= 96 * 1024:
            rows.append(row)
            used += size
    def walk(left, right, path=""):
        if type(left) is type(right) and left == right:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                if not path and key in ("revision", "resolution", "resolved_template"):
                    continue
                pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in left or key not in right:
                    entry(left.get(key), right.get(key), pointer)
                else:
                    walk(left[key], right[key], pointer)
        elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
            for index, (old, new) in enumerate(zip(left, right, strict=True)):
                walk(old, new, path + "/" + str(index))
        else:
            entry(left, right, path)
    walk(before, after)
    return rows, total


def validate_native_intent(original, candidate):
    """Validate document identity; permitted game edits are governed by the Skill."""
    validate_plan(candidate)
    for key in ("kind", "plan_id", "plan_version"):
        _equal(candidate.get(key), original.get(key), key)
    _equal(candidate["manual_template"].get("meta", {}).get("version_line"),
           original["manual_template"].get("meta", {}).get("version_line"), "version_line")


class CandidateSession:
    workflow = "calculate"

    def __init__(self, session_id, root, db_path, kind, document, *, delivery_root=None):
        uuid.UUID(session_id)
        if kind not in ("native", "whiteboard"):
            raise PlanError("未知Agent文档类型", "agent_document_kind")
        self.session_id, self.root, self.db_path, self.kind = session_id, Path(root).absolute(), Path(db_path).resolve(), kind
        self.delivery_root = Path(delivery_root).absolute() if delivery_root else self.db_path.parent / "agent_deliveries"
        self._lock, self.closed = threading.RLock(), False
        if safe_path(self.root, "checkpoint.json").exists():
            raise PlanError("会话已存在，请载入历史记录", "agent_session_exists")
        if len(json_bytes(document)) > MAX_NATIVE:
            raise PlanError("Agent输入超过8MiB", "agent_size_limit")
        if kind == "whiteboard":
            validate_whiteboard(document)
            self.document = copy.deepcopy(document)
        else:
            self.document = _native(document)
        self.document_fingerprint = self._document_fingerprint(self.document)
        with self._read_db(check=False):
            self.database_snapshot = self._db_snapshot()
            self.candidate = make_handoff(self.document, self.db_path) if kind == "whiteboard" else _native(document)
        from .provisional_authority import new_state as new_provisional_authority
        self.provisional_authority = new_provisional_authority(
            self.session_id, fingerprint(self.database_snapshot), self.candidate,
        )
        self.baseline = copy.deepcopy(self.candidate)
        self.revision, self.calculated, self.report = 0, None, None
        self.resources, self.findings, self.changes = {}, [], []
        self._review_projection_records, self._review_projection_orphans = {}, []
        self._validate_intent(self.candidate)
        self.save()

    def _document_fingerprint(self, document):
        return fingerprint(input_view(document) if self.kind == "native" else document)

    def _db_snapshot(self):
        return queries.get_meta(self.db_path) if self.db_path.is_file() else {"state": "missing"}

    @contextmanager
    def _read_db(self, *, check=True):
        with equipment_read_session(self.db_path) if self.db_path.is_file() else nullcontext():
            if check and fingerprint(self._db_snapshot()) != fingerprint(self.database_snapshot):
                raise PlanError("资料库来源已变化，请针对当前资料重新建立会话", "agent_database_changed")
            yield

    def _validate_intent(self, candidate):
        if self.kind == "whiteboard":
            board = self._candidate_board(candidate)
            validate_result_intent(board, candidate)
            if hasattr(self, "baseline"):
                validate_native_intent(self.baseline, candidate)
        else:
            validate_native_intent(self.document, candidate)

    def _candidate_board(self, candidate=None):
        plan = self.candidate if candidate is None else candidate
        board = plan.get("extensions", {}).get("whiteboard_handoff", {}).get("board")
        validate_whiteboard(board)
        for key in ("kind", "schema_version", "board_id", "version_line"):
            _equal(board[key], self.document[key], "whiteboard." + key)
        return board

    def _normalize_candidate_board(self, candidate):
        from genshin_app.calculation_options import synchronize_option_request
        options_changed = synchronize_option_request(self.candidate, candidate)
        if self.kind != "whiteboard":
            if options_changed:
                handoff = candidate['extensions']['whiteboard_handoff']
                previous_board = self.candidate.get('extensions', {}).get('whiteboard_handoff', {}).get('board')
                board = handoff['board']
                validate_whiteboard(board)
                if previous_board is not None:
                    board['revision'] = prepare_whiteboard_edit(previous_board, board)['revision']
                handoff['board_fingerprint'] = board_fingerprint(board)
            return
        board = self._candidate_board(candidate)
        previous = self._candidate_board()
        board["revision"] = prepare_whiteboard_edit(previous, board)["revision"]
        handoff = candidate["extensions"]["whiteboard_handoff"]
        handoff["board_fingerprint"] = board_fingerprint(board)
        handoff["constellations"] = {actor["id"]: actor["constellation"] for actor in board["team"]}
        handoff["capacity_intents"] = copy.deepcopy(board["capacity_intents"])
        declared = sum(intent["context"] is not None for intent in board["capacity_intents"])
        handoff["capacity_context_state"] = (
            "none" if not board["capacity_intents"] else
            "unknown" if declared == 0 else
            "declared" if declared == len(board["capacity_intents"]) else
            "mixed_pending"
        )
        admissions_changed = (
            board["action_admission_intents"] != previous["action_admission_intents"]
            or board["actions"] != previous["actions"]
            or [(row["id"], row["db_character_id"], row["constellation"])
                for row in board["team"]]
            != [(row["id"], row["db_character_id"], row["constellation"])
                for row in previous["team"]]
        )
        handoff["action_admission_intents"] = copy.deepcopy(
            board["action_admission_intents"])
        declared_admissions = sum(
            intent["context"] is not None
            for intent in board["action_admission_intents"]
        )
        handoff["action_admission_context_state"] = (
            "none" if not board["action_admission_intents"] else
            "unknown" if declared_admissions == 0 else
            "declared" if declared_admissions == len(board["action_admission_intents"]) else
            "mixed_pending"
        )
        if admissions_changed:
            # Any changed whiteboard scope invalidates an old fresh preview.
            # Keep the typed sources for Agent review, but never carry their
            # confirmation into the new scope.
            for source in candidate.get("action_admission_sources", []):
                source["accepted_fingerprint"] = None
            handoff["action_admission_proposals"] = []
        equipment_inputs = candidate.get("equipment_inputs", {})
        previous_actors = {actor["id"]: actor for actor in previous["team"]}
        for actor in board["team"]:
            equipment = equipment_inputs.get(actor["id"])
            if not isinstance(equipment, dict):
                continue
            progression = equipment.get("progression")
            if progression is None:
                progression = {"ascension": None, "status": "pending",
                               "source": "用户白板角色成长输入"}
                equipment["progression"] = progression
            if not isinstance(progression, dict):
                continue  # The normal schema/intent validator reports malformed input.
            progression["constellation"] = actor["constellation"]
            if "ascension" in actor:
                progression["ascension"] = actor["ascension"]
                progression["status"] = "confirmed" if actor["ascension"] is not None else "pending"
            elif "ascension" in previous_actors.get(actor["id"], {}):
                progression["ascension"] = None
                progression["status"] = "pending"
            if "talent_base_levels" in actor:
                progression["talent_base_levels"] = copy.deepcopy(actor["talent_base_levels"])
            elif "talent_base_levels" in previous_actors.get(actor["id"], {}):
                progression.pop("talent_base_levels", None)

    def _validate_calculated(self, calculated):
        if self.kind == "whiteboard":
            board = self._candidate_board(calculated["plan"])
            checked = validate_whiteboard_capacity_result(board, calculated)
            return {"integrity": checked["_delivery_integrity"]}
        self._validate_intent(_native(calculated["plan"]))
        return validate_delivery_calculated(calculated)

    def _expect(self, revision):
        if type(revision) is not int or revision != self.revision:
            raise PlanError("候选已变化，请读取最新修订后重试", "agent_stale_revision", {"revision": self.revision})

    def _candidate_replace_extension_state(self):
        """Capture subclass-owned state changed by a candidate replacement."""

    def _after_candidate_replaced(self, previous_candidate):
        """Update subclass invariants before the replacement is committed."""

    def _restore_candidate_replace_extension_state(self, state):
        """Restore subclass-owned state when the single checkpoint write fails."""

    def replace_candidate(self, candidate, expected_revision, reason):
        self._expect(expected_revision)
        if self.workflow == "artifact":
            from genshin_app.input_editing import validate_editable_input
            prepared = copy.deepcopy(input_view(candidate))
            prepared.update(resolution={"state": "pending"}, resolved_template=None)
            validate_editable_input(prepared)
        else:
            prepared = _native(candidate)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise PlanError("修改需简要说明理由", "agent_tool_input")
        if input_view(prepared) == input_view(self.candidate):
            return {**self.summary(), "edit_status": "no_op"}
        self._normalize_candidate_board(prepared)
        self._validate_intent(prepared)
        if len(json_bytes(prepared)) > MAX_NATIVE:
            raise PlanError("候选超过8MiB", "agent_size_limit")
        if "plan_id" in self.candidate:
            _equal(prepared["plan_id"], self.candidate["plan_id"], "plan_id")
        if input_view(prepared) == input_view(self.candidate):
            return {**self.summary(), "edit_status": "no_op"}
        prepared["revision"] = self.candidate["revision"] + 1
        previous = (self.candidate, self.revision, self.calculated, self.report, self.changes,
                    copy.deepcopy(self.provisional_authority),
                    self._candidate_replace_extension_state())
        fields = sorted(key for key in prepared if key not in ("revision", "resolution", "resolved_template")
                        and fingerprint(prepared.get(key)) != fingerprint(self.candidate.get(key)))
        from .skill_context import input_change_paths
        input_paths = input_change_paths(self.candidate, prepared)
        previous_candidate = self.candidate
        self.candidate = prepared
        self.revision += 1
        self.calculated, self.report = None, None
        self.changes = [*self.changes, {"revision": self.revision, "message": reason, "fields": fields,
            "input_paths": input_paths}][-100:]
        try:
            self._after_candidate_replaced(previous_candidate)
            from .provisional_authority import invalidate_after_candidate_change
            invalidate_after_candidate_change(self)
            self.save()
        except Exception:
            (self.candidate, self.revision, self.calculated, self.report,
             self.changes, self.provisional_authority, extension_state) = previous
            self._restore_candidate_replace_extension_state(extension_state)
            raise
        return {**self.summary(), "edit_status": "committed", "input_fingerprint": fingerprint(input_view(self.candidate))}

    def tool_definitions(self):
        from .tools import definitions
        return definitions(self.workflow)

    def call_tool(self, name, args):
        from .tools import call_tool
        with self._lock:
            if self.closed:
                return {"code": 1, "reason": "agent_session_closed", "message": "会话已关闭"}
            return call_tool(self, name, args)

    def compose(self, expected_revision):
        self._expect(expected_revision)
        with self._read_db():
            self.report = compose_equipment_plan(
                self.candidate, self.db_path, candidate_session=self)
        return self.summary()

    def calculate(self, expected_revision):
        self._expect(expected_revision)
        author_fingerprint = fingerprint(self.candidate)
        with self._read_db():
            prepared_report = compose_equipment_plan(
                self.candidate, self.db_path, candidate_session=self)
            # Deterministic input rejection is a preflight, not publication of
            # a new report/result.  Keep the original shared readiness gate and
            # DB read transaction; run_plan_calc repeats its own validation.
            require_ready(prepared_report)
            self._diagnostic_cache = None  # Supersede older diagnostics only after preflight passes.
            self.calculated = None
            self.report = prepared_report
            self._operation_phase = "engine_execution"
            value = run_plan_calc(
                self.candidate, self.db_path, capture_display=True,
                candidate_session=self)
        try:
            self._operation_phase = "generated_result_validation"
            self._validate_calculated(value)
        except Exception as error:
            # The engine returned, but its App-owned output did not pass the
            # publication contract. This is not a caller argument failure or
            # an unobserved external write. Keep the invalid output out of the
            # accepted slot and bind a small diagnostic to this exact attempt.
            # Never conceal a validator that changed author input itself.
            from genshin_engine.execution_control import ExecutionCancelled
            if (isinstance(error, ExecutionCancelled) or self.revision != expected_revision
                    or fingerprint(self.candidate) != author_fingerprint):
                raise
            self._after_calculation_save_failed()
            from .calculation_attempt_failure import retain_output_validation_failure
            failure = retain_output_validation_failure(
                self, error, candidate_fingerprint=author_fingerprint)
            # Clear any older accepted calculation in the same atomic
            # checkpoint, or a restart could revive its ready state. If that
            # write has an uncertain outcome it must follow normal UNKNOWN
            # handling, not the exact validation-failure exception below.
            self.save()
            raise failure from error
        self.calculated, self.report = value, value["report"]
        try:
            self._operation_phase = "result_publication"
            self._after_calculated()
            self.save()
        except Exception:
            # A failed calculation publication must not become an apparently
            # accepted result merely because the manager refreshes its summary.
            self.calculated = None
            self._after_calculation_save_failed()
            raise
        return self.summary()

    def _after_calculated(self):
        """Allow specialized workflows to bind acceptance to this exact run."""

    def _after_calculation_save_failed(self):
        """Clear subclass acceptance state when publication did not commit."""

    def read_candidate_view(self, resource, pointer="", offset=0, limit=50):
        """Complete read-only GUI view, independent of model context focus."""
        from .tools import page

        if resource not in {"candidate", "baseline", "calculated"}:
            raise PlanError("候选资料不存在", "agent_resource_missing")
        with self._lock:
            if self.closed:
                raise PlanError("候选会话已关闭", "agent_session_closed")
            value = {"candidate": self.candidate, "baseline": self.baseline,
                     "calculated": self.calculated}[resource]
            return copy.deepcopy(page(value, resource, pointer, offset, limit))

    def summary(self):
        from genshin_app.task_capability import normalize_task_issue

        missing = copy.deepcopy((self.report or {}).get("missing_inputs", []))
        diagnostics = (_diagnostics(self.calculated, energy_intent=getattr(self, "stage_state", {}).get("energy_intent"))
                       if self.calculated else [])
        with self._read_db(check=False):
            same_database = fingerprint(self._db_snapshot()) == fingerprint(self.database_snapshot)
        if not same_database:
            missing.append({"code": "agent_database_changed", "message": "资料库来源已变化，候选不能应用"})
        if not self.calculated and not missing:
            missing.append({"code": "agent_calculation_required", "message": "候选尚未完成一次有效计算"})
        ready = self.calculated is not None and same_database
        result = (self.calculated or {}).get("result", {})
        stats = copy.deepcopy(result.get("calculation_totals") or result.get("summary") or {})
        if len(json_bytes(stats)) > 8192:
            stats = {key: value for key, value in stats.items() if type(value) in (int, float, type(None))}
        stats["reported_hits"] = len(result.get("hits", []))
        receipt = fingerprint(self.calculated["result_receipt"]) if self.calculated else None
        diff, diff_total = _diff(self.baseline, self.candidate)
        issues = missing + diagnostics
        def compact(items):
            rows, used = [], 0
            for item in items[:80]:
                if isinstance(item, dict):
                    classified = normalize_task_issue(item)
                    row = {
                        "code": str(item.get("code", "calculation_issue"))[:128],
                        "path": str(item.get("path", ""))[:512],
                        "message": str(item.get("message", item))[:512],
                        "category": classified["category"],
                        "consumer": classified["consumer"],
                        "required": classified["required"],
                        "consumed": classified["consumed"],
                        "impact": classified["impact"],
                        "blocking": item.get("blocking", classified["required"] and classified["consumed"]),
                        "severity": item.get("severity", "error" if classified["required"] and classified["consumed"] else "info"),
                        "blocking_scope": classified["blocking_scope"],
                        "retryable": classified["retryable"],
                        "repair_hint": classified["repair_hint"],
                    }
                else:
                    row = {"message": str(item)[:512], "category": "request_error",
                           "consumer": "legacy_effective_graph", "required": True,
                           "blocking_scope": "request", "retryable": False,
                           "repair_hint": ""}
                size = len(json_bytes(row))
                if used + size > 16 * 1024:
                    break
                rows.append(row)
                used += size
            return rows
        compact_missing, compact_issues = compact(missing), compact(issues)
        changes = [{**row, "message": row["message"][:512]} for row in self.changes[-30:]]
        application_kind = None
        if self.calculated is not None:
            application_kind = (
                "whiteboard_calculated"
                if self.kind == "whiteboard"
                else "calculated_result"
            )
        result = {"session_id": self.session_id, "kind": self.kind, "workflow": self.workflow,
            "application_kind": application_kind, "revision": self.revision,
            "has_calculation": self.calculated is not None, "receipt_fingerprint": receipt,
            "can_accept": ready, "ready": ready, "missing_inputs": compact_missing,
            "missing_inputs_total": len(missing), "missing_inputs_truncated": len(compact_missing) < len(missing),
            "issues": compact_issues, "issues_total": len(issues), "issues_truncated": len(compact_issues) < len(issues),
            "changes": changes, "diff": diff, "diff_total": diff_total, "diff_truncated": len(diff) < diff_total, "summary": stats,
            "database_current": same_database, "finding_count": len(self.findings)}
        from .candidate_readiness import readiness_projection
        result.update(readiness_projection(result))
        from .operation_attempts import attempt_projection, current_identity
        result["current_input"] = current_identity(self)
        result["latest_attempt"] = attempt_projection(self)
        result["saved_calculation"] = {"available": self.calculated is not None,
            "database_current": same_database, "receipt_fingerprint": receipt,
            "resource": "calculated" if self.calculated is not None else None}
        if self.report is not None and "default_assistance" in self.report:
            result["default_assistance"] = compact_default_assistance(self.report["default_assistance"])
        if self.calculated is not None and "validation_scope" in self.calculated.get("result", {}):
            result["validation_scope"] = copy.deepcopy(self.calculated["result"]["validation_scope"])
        from genshin_app.task_evidence import session_review
        result["task_review"] = session_review(self, database_current=same_database)
        return result

    def accept(self, kind, current_document, expected_revision, expected_receipt):
        with self._lock, self._read_db():
            self._expect(expected_revision)
            if kind != self.kind or self._document_fingerprint(current_document) != self.document_fingerprint:
                raise PlanError("当前文档已变化，不能覆盖；候选可保留查看", "agent_document_changed")
            if not self.calculated or expected_receipt != fingerprint(self.calculated["result_receipt"]):
                raise PlanError("审核的计算回执已变化或不存在", "agent_stale_receipt")
            _equal(input_view(self.calculated["plan"]), input_view(self.candidate), "calculated_input")
            checked = self._validate_calculated(self.calculated)
            data = {"kind": self.kind, "revision": self.revision, "receipt_fingerprint": expected_receipt}
            if kind == "native":
                return {**data, "plan": copy.deepcopy(self.calculated["plan"])}
            validate_whiteboard_revision_transition(
                current_document, self._candidate_board(self.calculated["plan"]),
            )
            package = {"package_schema": PACKAGE_SCHEMA, "kind": PACKAGE_KIND,
                       "calculated": self.calculated, "integrity": checked["integrity"]}
            name = self.session_id + "-" + str(self.revision) + "-" + expected_receipt[:16] + ".calculated.json"
            path = safe_path(self.delivery_root, name)
            if path.exists():
                _equal(read_owned(self.delivery_root, name, MAX_CALCULATED), package, "accepted_package")
            else:
                write_owned(self.delivery_root, name, package, MAX_CALCULATED)
            return {**data, "board": copy.deepcopy(self._candidate_board(self.calculated["plan"])),
                    "result_path": str(path)}

    def resume_document(self, document):
        """Confirm an applied calculation on the next turn, never during accept."""
        with self._lock, self._read_db():
            prepared = copy.deepcopy(document) if self.kind == "whiteboard" else _native(document)
            if self.kind == "whiteboard":
                validate_whiteboard(prepared)
            current = self._document_fingerprint(prepared)
            if current == self.document_fingerprint:
                return self.summary()
            if self.calculated is None:
                raise PlanError("当前文档与会话输入不同，且候选尚未完成有效计算", "agent_document_changed")
            _equal(input_view(self.calculated["plan"]), input_view(self.candidate), "resume.calculated_input")
            self._validate_calculated(self.calculated)
            expected = (self._candidate_board(self.calculated["plan"]) if self.kind == "whiteboard"
                        else self.calculated["plan"])
            if current != self._document_fingerprint(expected):
                raise PlanError("当前文档不是本会话已计算候选，不能覆盖会话基线", "agent_document_changed")
            if self.kind == "whiteboard":
                validate_whiteboard_revision_transition(self.document, prepared)
            previous = self.document, self.baseline, self.document_fingerprint, self.changes
            self.document, self.baseline = prepared, copy.deepcopy(self.candidate)
            self.document_fingerprint, self.changes = current, []
            try:
                self.save()
            except Exception:
                self.document, self.baseline, self.document_fingerprint, self.changes = previous
                raise
            return self.summary()

    def restore_document_checkpoint(
        self,
        document,
        *,
        reason="document_checkpoint_restore",
    ):
        """Rebase onto one Host-verified historical document.

        ``resume_document`` proves application of this session's own latest
        candidate.  A restore point is a different transaction: it abandons
        the present candidate and every calculation derived from it, while the
        session and candidate revisions must continue monotonically.
        """

        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise PlanError("恢复原因不合法", "agent_tool_input")
        with self._lock, self._read_db():
            if self.kind == "native":
                prepared_document = _native(document)
                validate_native_intent(self.document, prepared_document)
                prepared_candidate = copy.deepcopy(prepared_document)
            else:
                validate_whiteboard(document)
                prepared_document = copy.deepcopy(document)
                for key in ("kind", "schema_version", "board_id", "version_line"):
                    _equal(prepared_document.get(key), self.document.get(key), key)
                prepared_candidate = (
                    copy.deepcopy(prepared_document)
                    if self.workflow in {"build", "staged", "artifact"}
                    else make_handoff(prepared_document, self.db_path)
                )
            if (
                self._document_fingerprint(prepared_document)
                == self.document_fingerprint
                and prepared_candidate == self.candidate
                and prepared_candidate == self.baseline
                and self.calculated is None
            ):
                return self.summary()
            previous = (
                self.document,
                self.baseline,
                self.candidate,
                self.document_fingerprint,
                self.revision,
                self.calculated,
                self.report,
                self.changes,
                copy.deepcopy(self.provisional_authority),
                self._candidate_replace_extension_state(),
            )
            previous_candidate = self.candidate
            self.document = prepared_document
            self.candidate = prepared_candidate
            self.baseline = copy.deepcopy(prepared_candidate)
            self.document_fingerprint = self._document_fingerprint(prepared_document)
            self.revision += 1
            self.calculated = None
            self.report = None
            self.changes = [{
                "revision": self.revision,
                "message": reason.strip(),
                "fields": ["document_checkpoint_restore"],
            }]
            try:
                self._after_document_checkpoint_restored(previous_candidate)
                from .provisional_authority import reset_for_document_restore
                reset_for_document_restore(self)
                self._validate_intent(self.candidate)
                self.save()
            except Exception:
                (
                    self.document,
                    self.baseline,
                    self.candidate,
                    self.document_fingerprint,
                    self.revision,
                    self.calculated,
                    self.report,
                    self.changes,
                    self.provisional_authority,
                    extension_state,
                ) = previous
                self._restore_candidate_replace_extension_state(extension_state)
                raise
            return self.summary()

    def _after_document_checkpoint_restored(self, previous_candidate):
        """Reset subclass-owned workflow facts after a full document rebase."""

    def save(self):
        # Immutable artifacts + one final checkpoint replacement keep the last
        # successful candidate readable even when a new publication fails.
        old = read_owned(self.root, "checkpoint.json", MAX_CHECKPOINT) if safe_path(self.root, "checkpoint.json").exists() else {}
        artifacts = {"document_file": "frozen-" + self.document_fingerprint + ".json",
                     "baseline_file": "baseline-" + fingerprint(self.baseline) + ".json",
                     "candidate_file": "candidate-" + fingerprint(self.candidate) + ".json", "calculated_file": None}
        def artifact(name, value, maximum):
            if not safe_path(self.root, name).exists():
                write_owned(self.root, name, value, maximum)
            else:
                _equal(read_owned(self.root, name, maximum), value, "saved_artifact")
        artifact(artifacts["document_file"], self.document, MAX_NATIVE)
        artifact(artifacts["baseline_file"], self.baseline, MAX_NATIVE)
        artifact(artifacts["candidate_file"], self.candidate, MAX_NATIVE)
        if self.calculated is not None:
            artifacts["calculated_file"] = "calculated-" + fingerprint(self.calculated) + ".json"
            artifact(artifacts["calculated_file"], self.calculated, MAX_CALCULATED)
        write_owned(self.root, "checkpoint.json", {"version": 1, "session_id": self.session_id, "kind": self.kind,
            "workflow": self.workflow,
            "stage_state": copy.deepcopy(getattr(self, "stage_state", None)),
            **artifacts,
            "baseline_fingerprint": fingerprint(self.baseline),
            "document_fingerprint": self.document_fingerprint, "candidate_fingerprint": fingerprint(self.candidate),
            "database_snapshot": self.database_snapshot, "revision": self.revision, "changes": self.changes,
            "provisional_authority": copy.deepcopy(self.provisional_authority),
            "findings": self.findings, "resources": list(self.resources),
            "review_projections": getattr(self, "_review_projection_records", {}),
            "receipt_fingerprint": fingerprint(self.calculated["result_receipt"]) if self.calculated else None}, MAX_CHECKPOINT)
        for key in artifacts:
            name = old.get(key)
            if name and name not in artifacts.values():
                try:
                    _artifact_name(name, {"document_file": "frozen", "baseline_file": "baseline",
                                          "candidate_file": "candidate", "calculated_file": "calculated"}[key])
                    safe_path(self.root, name).unlink(missing_ok=True)
                except (OSError, PlanError):
                    pass  # A failed cleanup must not roll back the committed checkpoint.

    @classmethod
    def load(cls, session_id, root, db_path, delivery_root=None):
        uuid.UUID(session_id)
        checkpoint = read_owned(root, "checkpoint.json", MAX_CHECKPOINT)
        if checkpoint.get("version") != 1 or checkpoint.get("session_id") != session_id or checkpoint.get("kind") not in ("native", "whiteboard"):
            raise PlanError("会话检查点身份不符", "agent_checkpoint_invalid")
        workflow = checkpoint.get("workflow", "calculate")
        session_class = cls
        if workflow not in ("calculate", "build", "staged", "artifact"):
            raise PlanError("会话工作流不合法", "agent_checkpoint_invalid")
        if workflow == "build":
            from .board_candidate import BoardCandidateSession
            if (checkpoint["kind"] != "whiteboard" or checkpoint.get("calculated_file") is not None
                    or checkpoint.get("receipt_fingerprint") is not None):
                raise PlanError("白板草稿不能包含计算回执", "agent_checkpoint_invalid")
            session_class = BoardCandidateSession
        elif workflow == "staged":
            from .staged_candidate import StagedCandidateSession
            if checkpoint["kind"] != "whiteboard":
                raise PlanError("分阶段工作流只接受白板输入", "agent_checkpoint_invalid")
            session_class = StagedCandidateSession
        elif workflow == "artifact":
            from .artifact_candidate import ArtifactCandidateSession
            session_class = ArtifactCandidateSession
        elif cls.workflow != "calculate":
            raise PlanError("会话工作流与载入类型不符", "agent_checkpoint_invalid")
        self = session_class.__new__(session_class)
        self.session_id, self.root, self.db_path, self.kind = session_id, Path(root).absolute(), Path(db_path).resolve(), checkpoint["kind"]
        self.delivery_root = Path(delivery_root).absolute() if delivery_root else self.db_path.parent / "agent_deliveries"
        self._lock, self.closed = threading.RLock(), False
        if workflow in {"staged", "artifact"}:
            self.restore_stage_state(checkpoint.get("stage_state"))
        self.document = read_owned(root, _artifact_name(checkpoint["document_file"], "frozen"), MAX_NATIVE)
        self.candidate = read_owned(root, _artifact_name(checkpoint["candidate_file"], "candidate"), MAX_NATIVE)
        self.baseline = read_owned(root, _artifact_name(checkpoint["baseline_file"], "baseline"), MAX_NATIVE)
        if self.kind == "whiteboard":
            validate_whiteboard(self.document)
        else:
            validate_plan(self.document)
        self.document_fingerprint = self._document_fingerprint(self.document)
        _equal(self.document_fingerprint, checkpoint["document_fingerprint"], "checkpoint.document")
        _equal(fingerprint(self.candidate), checkpoint["candidate_fingerprint"], "checkpoint.candidate")
        _equal(fingerprint(self.baseline), checkpoint["baseline_fingerprint"], "checkpoint.baseline")
        self._validate_intent(self.candidate)
        self._validate_intent(self.baseline)
        if workflow == "build":
            _equal(self.baseline, self.document, "checkpoint.baseline_document")
            if self.candidate["revision"] < self.document["revision"]:
                raise PlanError("草稿修订早于冻结输入", "agent_checkpoint_invalid")
        self.database_snapshot, self.revision = checkpoint["database_snapshot"], checkpoint["revision"]
        from .provisional_authority import new_state, validate_state
        authority = checkpoint.get("provisional_authority")
        self.provisional_authority = (
            new_state(
                self.session_id, fingerprint(self.database_snapshot), self.candidate,
            )
            if authority is None else
            validate_state(
                authority, session_id=self.session_id,
                database_snapshot_fingerprint=fingerprint(self.database_snapshot),
                candidate=self.candidate,
            )
        )
        self.changes, self.findings, self.resources = checkpoint["changes"], checkpoint["findings"], {}
        self.report, self.calculated = None, None
        if checkpoint["receipt_fingerprint"]:
            value = read_owned(root, _artifact_name(checkpoint["calculated_file"], "calculated"), MAX_CALCULATED)
            self._validate_calculated(value)
            _equal(input_view(value["plan"]), input_view(self.candidate), "checkpoint.calculated_input")
            _equal(fingerprint(value["result_receipt"]), checkpoint["receipt_fingerprint"], "checkpoint.receipt")
            self.calculated, self.report = value, value["report"]
        if len(checkpoint["resources"]) > 128 or type(self.revision) is not int or self.revision < 0:
            raise PlanError("会话检查点限额或修订不合法", "agent_checkpoint_invalid")
        from .review_projection import NAME, load_document
        self._review_projection_records = checkpoint.get("review_projections", {})
        self._review_projection_orphans = []
        if (type(self._review_projection_records) is not dict
                or type(checkpoint["resources"]) is not list
                or any(type(name) is not str for name in checkpoint["resources"])
                or len(set(checkpoint["resources"])) != len(checkpoint["resources"])
                or set(self._review_projection_records) != {
                    name for name in checkpoint["resources"] if NAME.fullmatch(name)}):
            raise PlanError("历史审查投影检查点索引不一致", "agent_checkpoint_invalid")
        for resource in checkpoint["resources"]:
            if NAME.fullmatch(resource):
                self.resources[resource] = load_document(self, resource, self._review_projection_records[resource])
                continue
            if not isinstance(resource, str) or not resource.startswith("query-") or not resource[6:].isdigit():
                raise PlanError("会话资料索引不合法", "agent_checkpoint_invalid")
            self.resources[resource] = read_owned(root, resource + ".json", 4 * 1024 * 1024)
        if sum(len(json_bytes(item)) for item in self.resources.values()) > 64 * 1024 * 1024:
            raise PlanError("会话查询资料超过64MiB", "agent_checkpoint_invalid")
        if workflow in {"staged", "artifact"}:
            representation = "whiteboard" if self._is_board() else "native"
            if representation != self.stage_state["representation"]:
                raise PlanError("分阶段会话表示与检查点不一致", "agent_checkpoint_invalid")
            if (workflow == "staged" and representation == "whiteboard"
                    and self.workflow_stage not in ("loadout_draft", "action_checkpoint")):
                raise PlanError("白板表示不能越过动作检查点阶段", "agent_checkpoint_invalid")
            if any(name not in checkpoint["resources"]
                   for name in self.stage_state["resource_stages"]):
                raise PlanError("分阶段资料索引与检查点不一致", "agent_checkpoint_invalid")
            if len(self.stage_state["finding_stages"]) != len(self.findings):
                raise PlanError("分阶段资料记录与检查点不一致", "agent_checkpoint_invalid")
            if any(row["candidate_revision"] > self.revision
                   for row in self.stage_state["history"]):
                raise PlanError("分阶段历史引用未来候选修订", "agent_checkpoint_invalid")
            self.validate_restored_stage()
            basis = self.stage_state.get("calculation_basis")
            if (self.calculated is None) != (basis is None):
                raise PlanError("分阶段计算结果与计算依据不一致", "agent_checkpoint_invalid")
            if basis is not None and basis != self._current_calculation_basis():
                raise PlanError("分阶段计算依据已被篡改或失效", "agent_checkpoint_invalid")
        return self

    def verify_provisional_lookup(
        self, *, provisional_id, expected_revision, database_id=None, reason,
    ):
        from .provisional_authority import verify_lookup
        return verify_lookup(
            self, provisional_id=provisional_id,
            expected_revision=expected_revision, database_id=database_id,
            reason=reason,
        )

    def attest_provisional_database_missing(
        self, *, provisional_id, expected_revision, reason,
    ):
        from .provisional_authority import attest_database_missing
        return attest_database_missing(
            self, provisional_id=provisional_id,
            expected_revision=expected_revision, reason=reason,
        )

    def request_provisional_public_patch_approval(
        self, *, provisional_id, expected_revision, reason,
    ):
        from .provisional_authority import request_public_patch_approval
        return request_public_patch_approval(
            self, provisional_id=provisional_id,
            expected_revision=expected_revision, reason=reason,
        )

    def bind_provisional_approval_question(self, *, batch_id, call_id, items):
        from .provisional_authority import bind_question_batch
        return bind_question_batch(
            self, batch_id=batch_id, call_id=call_id, items=items,
        )

    def settle_provisional_approval_question(self, *, question, reason):
        from .provisional_authority import settle_question_answer
        return settle_question_answer(self, question=question, reason=reason)

    def provisional_usage_receipt(self):
        from .provisional_authority import trusted_usage_receipt
        return trusted_usage_receipt(self)

    def resolve_provisional_entity_ref(self, *, ref, consumer):
        """Resolve through the session-owned trusted ledgers for a real consumer.

        This facade is intentionally absent from the Agent tool catalog.  A
        Composer/runtime consumer must call it while processing an actual
        typed reference, then pass the opaque result to the usage publisher.
        """

        from genshin_app.equipment.provisional_entities import resolve_entity_ref

        state = self.provisional_authority
        return resolve_entity_ref(
            self.candidate,
            ref,
            consumer=consumer,
            database_snapshot_fingerprint=fingerprint(self.database_snapshot),
            candidate_revision=self.revision,
            stage_revision=getattr(self, "stage_state", {}).get(
                "stage_revision", 0
            ),
            approval_ledger=state["approval_ledger"],
            database_lookup_ledger=state["lookup_ledger"],
            agent_attestation_ledger=state["attestation_ledger"],
        )

    def publish_provisional_usage_receipt(self, *, resolved_uses):
        from .provisional_authority import publish_usage_receipt
        return publish_usage_receipt(self, resolved_uses=resolved_uses)

    def close(self):
        with self._lock:
            self.save()
            self.closed = True
