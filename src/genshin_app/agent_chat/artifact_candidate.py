"""Durable artifacts with advisory focus, reusing generic qualification."""
from __future__ import annotations

import copy
from itertools import pairwise

from genshin_app.equipment.event_topology import new_event_topology
from genshin_app.equipment.event_topology_binding import new_event_topology_bindings
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.failure_analysis import calculation_feedback
from genshin_app.task_capability import classify_task_issues
from genshin_app.whiteboard import make_handoff

from .board_candidate import BoardCandidateSession
from .calculation_qualification import ENERGY_INTENTS
from .candidate import CandidateSession
from .staged_candidate import (
    PHASES,
    StagedCandidateSession,
    _valid_resource_descriptor,
    compact_topology_binding_receipt,
    compact_topology_receipt,
)

CONTRACT = "artifact-candidate-workflow-v1"


def _new_state(representation):
    return {
        "contract": CONTRACT, "phase": PHASES[0], "focus_revision": 0,
        # Domain generation is independent of the reading-focus counter.
        "stage_revision": 0, "representation": representation,
        "energy_intent": None, "calculation_basis": None,
        "resource_stages": {}, "resource_descriptors": {},
        "finding_stages": [], "history": [],
    }


class ArtifactCandidateSession(StagedCandidateSession):
    workflow = "artifact"
    authorization_stage = "artifact"

    def _validate_intent(self, candidate):
        if not self._is_board(candidate):
            from genshin_app.input_editing import (
                input_version_line,
                validate_editable_input,
            )
            validate_editable_input(candidate)
            if self.kind == "whiteboard":
                board = candidate.get("extensions", {}).get("whiteboard_handoff", {}).get("board")
                if not isinstance(board, dict) or input_version_line(candidate) != self.document["version_line"]:
                    raise PlanError("候选不能丢弃当前白板身份或改换资料版本线", "agent_intent_mismatch")
                for key in ("kind", "schema_version", "board_id", "version_line"):
                    if board.get(key) != self.document[key]:
                        raise PlanError("候选白板身份不一致", "agent_intent_mismatch", {"path": key})
            previous = getattr(self, "candidate", None)
            if isinstance(previous, dict) and previous.get("kind") == "genshin-plan":
                for key in ("kind", "plan_id"):
                    if candidate[key] != previous[key]:
                        raise PlanError("候选系统身份不一致", "agent_intent_mismatch", {"path": key})
                if input_version_line(candidate) != input_version_line(previous):
                    raise PlanError("候选版本线不一致", "agent_intent_mismatch")
            return
        return super()._validate_intent(candidate)

    def input_alignment_issues(self):
        if not self._is_board():
            from genshin_app.input_editing import execution_issues
            return execution_issues(self.candidate)
        from .candidate_input_draft import intent_alignment_issues

        return intent_alignment_issues(self.candidate)

    def calculation_acceptance(self):
        if self.calculated is None:
            from genshin_app.calculation_qualification import pending_acceptance
            return pending_acceptance(energy_intent=self.stage_state.get("energy_intent"))
        from genshin_app.calculation_qualification import qualification_for_claim
        return qualification_for_claim(self.calculated, energy_intent=self.stage_state.get("energy_intent"), trusted_context=self)

    def _normalize_candidate_board(self, candidate):
        from genshin_app.native_input_projection import synchronize_native_projection
        synchronize_native_projection(candidate, getattr(self, "candidate", None))

    def __init__(self, *args, **kwargs):
        kind = args[3] if len(args) > 3 else kwargs.get("kind")
        self.stage_state = _new_state("whiteboard" if kind == "whiteboard" else "native")
        if kind == "whiteboard":
            BoardCandidateSession.__init__(self, *args, **kwargs)
        else:
            CandidateSession.__init__(self, *args, **kwargs)
            self._ensure_topology_shells()
            from .provisional_authority import new_state
            self.provisional_authority = new_state(self.session_id, fingerprint(self.database_snapshot), self.candidate)
            self.save()

    def _source_descriptor_visible(self, _phase):
        return True

    def _current_board(self, candidate=None):
        value = self.candidate if candidate is None else candidate
        if self._is_board(value):
            return super()._current_board(value)
        if self.kind == "whiteboard":
            board = value.get("extensions", {}).get("whiteboard_handoff", {}).get("board")
            if isinstance(board, dict):
                return copy.deepcopy(board)
        from genshin_app.native_input_projection import action_view
        return action_view(value)

    def _ensure_topology_shells(self):
        if self._is_board():
            return
        existing = self.candidate.get("extensions", {}).get("event_topology")
        if not isinstance(existing, dict) and not self._current_action_checkpoint()["action_ids"]:
            # Native drafts may legitimately contain only manual inputs.
            # Missing topology remains a qualification issue, not a load
            # or editing failure; never invent an action to satisfy it.
            return
        extensions = self.candidate.setdefault("extensions", {})
        if not isinstance(extensions.get("event_topology"), dict):
            extensions["event_topology"] = new_event_topology(
                "topology." + self.candidate["plan_id"],
                action_checkpoint=self._current_action_checkpoint(),
                dependencies=[{"dependency_id": "database_snapshot", "kind": "database_snapshot",
                               "fingerprint": fingerprint(self.database_snapshot), "required": True}],
                note="Agent盘点场景事件；空拓扑壳不表示角色机制或数值已完整。",
            )
        if not isinstance(extensions.get("event_topology_bindings"), dict):
            extensions["event_topology_bindings"] = new_event_topology_bindings(extensions["event_topology"])

    def transition_phase(self, target, expected_revision, expected_stage_revision, reason,
                         *, energy_intent=None, prepare_calculation=False):
        """Reading focus is unrestricted; native preparation is an explicit edit."""
        self._expect(expected_revision)
        if expected_stage_revision != self.stage_state["stage_revision"]:
            raise PlanError("候选计算结构已变化，请读取当前摘要", "agent_stage_stale")
        if target not in PHASES or not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise PlanError("工作重点和说明不合法", "agent_tool_input")
        if energy_intent is not None and energy_intent not in ENERGY_INTENTS:
            raise PlanError("未知能量证明范围", "agent_tool_input")
        if type(prepare_calculation) is not bool:
            raise PlanError("计算结构准备选项必须是布尔值", "agent_tool_input")
        previous = (copy.deepcopy(self.stage_state), copy.deepcopy(self.candidate),
                    copy.deepcopy(self.baseline), self.revision, self.calculated, self.report,
                    copy.deepcopy(self.provisional_authority))
        try:
            semantic_change = False
            if prepare_calculation and self._is_board():
                with self._read_db():
                    prepared = make_handoff(self.candidate, self.db_path)
                self.candidate = prepared
                self.baseline = copy.deepcopy(prepared)
                self.revision += 1
                self.stage_state["representation"] = "native"
                from .provisional_authority import new_state
                self.provisional_authority = new_state(self.session_id, fingerprint(self.database_snapshot), prepared)
                self._ensure_topology_shells()
                semantic_change = True
            if energy_intent is not None and energy_intent != self.stage_state["energy_intent"]:
                self.stage_state["energy_intent"] = energy_intent
                semantic_change = True
            if semantic_change:
                self.stage_state["stage_revision"] += 1
                self.stage_state["calculation_basis"] = None
                self.calculated, self.report = None, None
                from .provisional_authority import invalidate_after_candidate_change
                invalidate_after_candidate_change(self)
            old = self.workflow_stage
            self.stage_state["phase"] = target
            self.stage_state["focus_revision"] += 1
            self.stage_state["history"] = [*self.stage_state["history"], {
                "focus_revision": self.stage_state["focus_revision"], "from": old, "to": target,
                "candidate_revision": self.revision, "reason": reason.strip(),
            }][-100:]
            self.save()
        except Exception:
            (self.stage_state, self.candidate, self.baseline, self.revision,
             self.calculated, self.report, self.provisional_authority) = previous
            raise
        return self.summary()

    def _after_candidate_replaced(self, previous_candidate):
        self.stage_state["calculation_basis"] = None
        if self._is_board(previous_candidate) != self._is_board():
            self.stage_state["stage_revision"] += 1
        self.stage_state["representation"] = "whiteboard" if self._is_board() else "native"
        self._diagnostic_cache = None
        # Generic relative-time rebinding only. Other changes leave evidence
        # stale for the Agent to repair; they never force a focus rollback.
        from genshin_app.input_editing import execution_issues
        if (not self._is_board() and not self._is_board(previous_candidate)
                and not execution_issues(self.candidate) and not execution_issues(previous_candidate)):
            topology = self._topology()
            old_topology = previous_candidate.get("extensions", {}).get("event_topology")
            if (isinstance(topology, dict)
                    and self._is_pure_action_timing_change(previous_candidate, old_topology)):
                topology["action_checkpoint"] = self._current_action_checkpoint()
                from genshin_app.equipment.event_topology import (
                    event_topology_fingerprint,
                )
                bindings = self._topology_bindings()
                if isinstance(bindings, dict):
                    bindings["topology_fingerprint"] = event_topology_fingerprint(topology)

    def _after_document_checkpoint_restored(self, previous_candidate):
        del previous_candidate
        old = self.stage_state
        self.stage_state = _new_state("whiteboard" if self._is_board() else "native")
        self.stage_state["stage_revision"] = old["stage_revision"] + 1

    def _require_native(self):
        if self._is_board():
            raise PlanError(
                "当前仍为白板草稿；有角色和动作后，用gs_transition_phase的prepare_calculation=true准备计算结构。工作重点只是阅读提示。",
                "agent_native_artifact_required")
        from genshin_app.equipment.schema import validate_plan
        validate_plan(self.candidate)

    def compose(self, expected_revision):
        self._expect(expected_revision)
        self._require_native()
        return CandidateSession.compose(self, expected_revision)

    def calculate(self, expected_revision):
        self._expect(expected_revision)
        self._require_native()
        CandidateSession.calculate(self, expected_revision)
        acceptance = self.calculation_acceptance()
        if acceptance["status"] != "accepted":
            feedback = calculation_feedback(self.calculated)
            raise PlanError("已保留诊断计算；通用规则、事件绑定、数值或能量尚未满足正式接纳要求。",
                            "agent_calculation_rejected", {
                                "issues": acceptance["issues"][:20],
                                "issues_total": len(acceptance["issues"]),
                                "revision": self.revision,
                                "execution_feedback": feedback,
                                "recovery": self._saved_execution_recovery(feedback),
                            })
        return self.summary()

    def _saved_execution_recovery(self, feedback, resource="calculated"):
        failure = feedback.get("failure_summary", {}).get("first_rejected_event", {})
        section = "energy" if failure.get("deficit", 0) > 0 else "all"
        return {"action": "inspect_saved_execution", "retry_same_request": False,
                "tool": "gs_diagnose_result", "resource": resource,
                "expected_revision": self.revision,
                "request": {"view": "assistance", "section": section}}

    def _acceptance_projection(self, acceptance, *, issue_limit=20):
        result = copy.deepcopy(acceptance)
        issues = result.get("issues") or []
        result.update(issues=issues[:issue_limit], issues_total=len(issues), issues_truncated=len(issues) > issue_limit)
        return result

    def _next_gate(self):
        return {"target": None, "allowed": True, "issues": [], "policy": "advisory_focus_only"}

    def _execution_feedback(self):
        if self.calculated is not None:
            return calculation_feedback(self.calculated), "calculated"
        from .operation_attempts import attempt_projection
        latest = attempt_projection(self)
        if latest and latest.get("matches_current_input") is False:
            return None, None
        diagnostic = getattr(self, "_diagnostic_cache", None)
        if diagnostic and diagnostic["revision"] == self.revision:
            value = diagnostic["value"]
            if value.get("reason") == "mechanics_pending":
                return calculation_feedback({"plan": self.candidate, "report": self.report,
                    "result": value["details"], "completion": {"calculation_status": "diagnostic",
                                                               "complete_input_window": False}}), "diagnostic_error"
        return None, None

    def stage_context(self):
        from . import tools
        from .candidate_input_draft_store import historical_resource_descriptors
        from .operation_attempts import attempt_projection
        from .skill_context import recommend_focus
        from genshin_app.task_evidence import session_review
        latest = attempt_projection(self)
        recommendation = recommend_focus({"focus": self.workflow_stage, "revision": self.revision,
            "latest_attempt": latest, "changes": self.changes, "task_review": session_review(self)})
        focus = recommendation["focus"] or self.workflow_stage
        names = tools._STAGE_READ_CHAINS[focus]
        recommended = list(dict.fromkeys([*recommendation["next_resources"],
                                         *names["required"], *names["recommended"]]))
        if self._is_board():
            recommended = [row for row in recommended if row not in {
                "event_topology", "event_topology_receipt", "event_topology_bindings",
                "event_topology_binding_receipt", "agent_execution_materialization_receipt", "report"}]
        feedback, resource = self._execution_feedback()
        repair = (self._saved_execution_recovery(feedback, resource)
                  if feedback and feedback["execution"] == "diagnostic" else None)
        if (latest and latest.get("matches_current_input") and latest.get("state") == "failed"
                and not (repair is not None and latest.get("engine_execution") == "rejected")):
            repair = {"action": "inspect_latest_attempt", "retry_same_request": False,
                "tool": "gs_read_context", "resource": "calculation_attempt",
                "pointer": "/evidence", "attempt_id": latest["attempt_id"],
                "engine_execution": latest["engine_execution"], "user_action": None}
            resource = "calculation_attempt"
        skill_resources = tools._STAGE_RECOMMENDED_SKILL_RESOURCES.get(focus, set())
        recommended_tools = tools.recommended_tools_for_session(self)
        if repair:
            recommended = list(dict.fromkeys([resource, *recommended]))
            skill_resources = {*skill_resources, tools._CURRENT_STAGE_SKILL["calculation_closure"]}
            recommended_tools = list(dict.fromkeys([repair.get("tool", "gs_diagnose_result"), *recommended_tools]))
        return {"contract": CONTRACT, "workflow_stage": self.workflow_stage, "focus": self.workflow_stage,
                "work_card_recommendation": recommendation,
                "focus_revision": self.stage_state["focus_revision"],
                "stage_revision": self.stage_state["stage_revision"],
                "representation": self.stage_state["representation"],
                "energy_intent": self.stage_state["energy_intent"],
                "context_policy": "工作重点只决定推荐阅读顺序；全部Skill、已保存资料和适用工具可按需使用。",
                "required_context_reads": ["workflow_stage"], "recommended_context_reads": recommended,
                "current_stage_skill": tools._CURRENT_STAGE_SKILL.get(focus),
                "recommended_skill_resources": sorted(skill_resources),
                "recommended_tools": recommended_tools,
                **({"current_problem": repair} if repair else {}),
                "historical_resource_index": [{"resource_id": name,
                    "query_kind": descriptor.get("query_kind", "agent_note"),
                    "entities": descriptor.get("entities", []), "status": "pending_verification"}
                    for name, descriptor in historical_resource_descriptors(self).items()
                    if name not in self.stage_state["resource_descriptors"]],
                "read_only_context_does_not_authorize_execution": True}

    def compact_tool_response(self, data=None):
        from .native_tool_receipt import DIRECT_RESULT_MAX_BYTES

        summary = data if isinstance(data, dict) else self.summary()
        stage = self.stage_context()
        value = {key: copy.deepcopy(summary[key]) for key in (
            "workflow", "workflow_stage", "focus_revision", "stage_revision", "revision", "representation",
            "saveable", "computable", "applicable", "has_calculation", "calculation_acceptance",
            "execution_feedback",
            "current_input", "latest_attempt", "saved_calculation", "default_assistance", "validation_scope",
            "event_topology_coverage_receipt", "event_topology_execution_binding_receipt",
            "task_capability", "receipt_fingerprint", "edit_status", "input_fingerprint",
            "source_file", "calculation_invalidated", "changed_roots", "writes") if key in summary} | {
                "read_next": stage["recommended_context_reads"],
                "work_card_recommendation": stage["work_card_recommendation"],
            }
        if len(json_bytes({"code": 0, "data": value})) <= DIRECT_RESULT_MAX_BYTES:
            return value
        # These sections can be read independently from the same revision.
        # Keep the calculation outcome and mutation receipt in the tool reply.
        resources = {
            "event_topology_coverage_receipt": "event_topology_receipt",
            "event_topology_execution_binding_receipt": "event_topology_binding_receipt",
        }
        links = {}
        for key, resource in resources.items():
            if key in value:
                value.pop(key)
                links[key] = {"resource": resource, "pointer": ""}
        if links:
            value["additional_details"] = links
        capability = value.get("task_capability")
        if isinstance(capability, dict) and len(json_bytes({"code": 0, "data": value})) > DIRECT_RESULT_MAX_BYTES:
            value["task_capability"] = {key: capability[key] for key in ("contract", "status", "counts")
                                        if key in capability}
            value.setdefault("additional_details", {})["task_capability"] = {
                "resource": "report", "pointer": "/missing_inputs"}
        return value

    def summary(self):
        result = (BoardCandidateSession.summary(self) if self._is_board() else CandidateSession.summary(self))
        if self._is_board():
            from .operation_attempts import attempt_projection, current_identity
            result.update(current_input=current_identity(self), latest_attempt=attempt_projection(self))
        acceptance = self.calculation_acceptance()
        capability = classify_task_issues((self.report or {}).get("missing_inputs", []))
        if not self._is_board():
            accepted = acceptance["status"] == "accepted" and result["database_current"]
            result.update(ready=accepted, can_accept=accepted, applicable=accepted)
            if not accepted:
                result["receipt_fingerprint"] = None
        result.update(
            workflow_stage=self.workflow_stage, focus=self.workflow_stage,
            focus_revision=self.stage_state["focus_revision"], stage_revision=self.stage_state["stage_revision"],
            representation=self.stage_state["representation"], saveable=True,
            computable=not self._is_board() and result["database_current"],
            applicable=bool(result.get("can_accept")), task_capability=capability,
            input_issues=self.input_alignment_issues(),
            calculation_acceptance=self._acceptance_projection(acceptance),
            event_topology_coverage_receipt=compact_topology_receipt(self.topology_receipt()),
            event_topology_execution_binding_receipt=compact_topology_binding_receipt(self.topology_binding_receipt()),
        )
        from .candidate_readiness import readiness_projection
        result.update(readiness_projection(result))
        feedback, _ = self._execution_feedback()
        if feedback:
            result["execution_feedback"] = feedback
        from genshin_app.task_evidence import session_review
        result["task_review"] = session_review(self, database_current=result.get("database_current"))
        return result

    def accept(self, kind, current_document, expected_revision, expected_receipt):
        if self._is_board():
            return BoardCandidateSession.accept(self, kind, current_document, expected_revision, expected_receipt)
        acceptance = self.calculation_acceptance()
        if acceptance["status"] != "accepted":
            raise PlanError("当前诊断结果不能作为正式计算应用", "agent_calculation_rejected", acceptance["issues"][:80])
        return CandidateSession.accept(self, kind, current_document, expected_revision, expected_receipt)

    def resume_document(self, document):
        if self._is_board():
            return BoardCandidateSession.resume_document(self, document)
        if self._document_fingerprint(document) != self.document_fingerprint and self.calculation_acceptance()["status"] != "accepted":
            raise PlanError("诊断结果不能推进正式白板基线", "agent_calculation_rejected")
        return CandidateSession.resume_document(self, document)

    def restore_stage_state(self, state):
        template = _new_state("whiteboard")
        if (not isinstance(state, dict) or set(state) != set(template) or state.get("contract") != CONTRACT
                or state.get("phase") not in PHASES or state.get("representation") not in {"whiteboard", "native"}
                or state.get("energy_intent") not in ENERGY_INTENTS | {None}
                or any(type(state.get(name)) is not int or state[name] < 0 for name in ("stage_revision", "focus_revision"))
                or not isinstance(state.get("history"), list) or len(state["history"]) > 100
                or not isinstance(state.get("resource_stages"), dict)
                or any(phase not in PHASES for phase in state["resource_stages"].values())
                or not isinstance(state.get("resource_descriptors"), dict)
                or not set(state["resource_descriptors"]) <= set(state["resource_stages"])
                or any(not _valid_resource_descriptor(name, value)
                       or value["stage"] != state["resource_stages"][name]
                       for name, value in state["resource_descriptors"].items())
                or not isinstance(state.get("finding_stages"), list)
                or any(phase not in PHASES for phase in state["finding_stages"])):
            raise PlanError("候选工件检查点结构不合法", "agent_checkpoint_invalid")
        history = state["history"]
        if any(not isinstance(row, dict) or set(row) != {
                "focus_revision", "from", "to", "candidate_revision", "reason"}
                or type(row["focus_revision"]) is not int or not 0 < row["focus_revision"] <= state["focus_revision"]
                or row["from"] not in PHASES or row["to"] not in PHASES
                or type(row["candidate_revision"]) is not int or row["candidate_revision"] < 0
                or not isinstance(row["reason"], str) or not 0 < len(row["reason"]) <= 4096
                for row in history):
            raise PlanError("候选工作重点记录不合法", "agent_checkpoint_invalid")
        if (bool(history) != bool(state["focus_revision"])
                or history and (history[-1]["focus_revision"] != state["focus_revision"]
                                or history[-1]["to"] != state["phase"])
                or any(left["focus_revision"] + 1 != right["focus_revision"]
                       or left["to"] != right["from"] for left, right in pairwise(history))):
            raise PlanError("候选工作重点记录与当前摘要不一致", "agent_checkpoint_invalid")
        self.stage_state = copy.deepcopy(state)

    def validate_restored_stage(self):
        for name, descriptor in self.stage_state["resource_descriptors"].items():
            resource = self.resources.get(name)
            if (resource is None or fingerprint(resource) != descriptor["fingerprint"]
                    or len(json_bytes(resource)) != descriptor["bytes"]):
                raise PlanError("候选缓存来源与已保存资料不一致", "agent_checkpoint_invalid")
        # Invalid/incomplete topology stays resumable; it is never a grant.
        if self.calculated is not None and self.stage_state["calculation_basis"] != self._current_calculation_basis():
            raise PlanError("计算工件与当前输入依据不一致", "agent_checkpoint_invalid")
