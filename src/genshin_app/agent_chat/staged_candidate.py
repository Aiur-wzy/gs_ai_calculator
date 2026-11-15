"""One persistent Agent session with progressively disclosed workflow phases."""
from __future__ import annotations

import copy
from itertools import pairwise

from genshin_app.equipment.event_topology import (
    event_topology_capabilities,
    event_topology_fingerprint,
    lint_event_topology,
    new_event_topology,
)
from genshin_app.equipment.event_topology_binding import (
    event_topology_binding_capabilities,
    lint_event_topology_bindings,
    new_event_topology_bindings,
)
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.task_capability import blocking_task_issues, classify_task_issues
from genshin_app.whiteboard import make_handoff

from .board_candidate import BoardCandidateSession
from .calculation_qualification import (
    ENERGY_INTENTS,
    pending_acceptance,
    qualify_calculation,
)
from .candidate import CandidateSession, _diff

PHASES = ("loadout_draft", "action_checkpoint", "event_topology",
          "numeric_model", "calculation_closure")
_NATIVE_PHASES = frozenset(PHASES[2:])


def _phase_index(phase):
    try:
        return PHASES.index(phase)
    except ValueError as exc:
        raise PlanError("未知分阶段工作流阶段", "agent_stage_invalid") from exc


def _valid_resource_descriptor(name, value):
    return (isinstance(value, dict) and set(value) == {
                "id", "stage", "query_kind", "action", "entities", "selectors",
                "fingerprint", "bytes"}
            and value.get("id") == name and name.startswith("query-")
            and name[6:].isdigit() and value.get("stage") in PHASES
            and value.get("query_kind") in ("context", "runtime", "appendix", "appendix_index")
            and (value.get("action") is None or isinstance(value.get("action"), str)
                 and len(value["action"]) <= 128)
            and isinstance(value.get("entities"), list) and len(value["entities"]) <= 16
            and all(isinstance(item, str) and len(item) <= 128
                    for item in value["entities"])
            and isinstance(value.get("selectors"), list) and len(value["selectors"]) <= 32
            and all(isinstance(item, str) and len(item) <= 128
                    for item in value["selectors"])
            and isinstance(value.get("fingerprint"), str)
            and len(value["fingerprint"]) == 64
            and all(character in "0123456789abcdef" for character in value["fingerprint"])
            and type(value.get("bytes")) is int and 0 <= value["bytes"] <= 4 * 1024 * 1024)


def _descriptor_entities(descriptor, context_kind, runtime_field):
    """Return normalized entity identities declared by one saved query request."""
    values = descriptor.get("entities", []) if isinstance(descriptor, dict) else []
    return {
        row for row in values
        if row.startswith((context_kind + ":", runtime_field + "="))
    }


def _descriptor_covers_id(descriptor, context_kind, runtime_field, entity_id):
    expected = {f"{context_kind}:{entity_id}", f"{runtime_field}={entity_id}"}
    return bool(_descriptor_entities(descriptor, context_kind, runtime_field) & expected)


def _descriptor_annotation_scope(descriptor, *, identities=None):
    """Bind an appendix query to the relevant annotation target identity."""
    if not isinstance(descriptor, dict):
        return False
    expected = set(identities or ())
    if descriptor.get("query_kind") == "appendix":
        entities = set(descriptor.get("entities", []))
        return bool(entities & expected) if expected else bool(entities)
    if descriptor.get("query_kind") != "context":
        return False
    for selector in descriptor.get("selectors", []):
        prefix, separator, value = selector.partition(".sections=")
        if not separator or expected and prefix not in expected:
            continue
        if "annotations" in value.split(","):
            return True
    return False


def _appendix_authority(descriptor, resource):
    """Return the review authority bound to an App-saved appendix query."""
    if not isinstance(descriptor, dict) or not isinstance(resource, dict):
        return None
    statuses = [selector.removeprefix("status=")
                for selector in descriptor.get("selectors", [])
                if selector.startswith("status=")]
    if len(statuses) != 1 or statuses[0] not in ("approved", "pending"):
        return None
    status = statuses[0]
    selection = resource.get("selection")
    target = resource.get("target")
    if (resource.get("contract_id") != "agent-appendix-query-v1"
            or not isinstance(selection, dict)
            or not isinstance(target, dict)
            or selection.get("status") != status
            or resource.get("annotation_authority") != status
            or resource.get("execution_authority") != "none"):
        return None
    items = resource.get("items")
    if not isinstance(items, list) or not items or any(
            not isinstance(item, dict)
            or item.get("status") != status
            or item.get("target_type") != target.get("target_type")
            or item.get("target_id") != target.get("target_id")
            or item.get("category") != selection.get("category")
            or item.get("version_line") not in (None, selection.get("version_line"))
            or item.get("annotation_authority") != status
            or item.get("execution_authority") != "none"
            for item in items):
        return None
    return status


def _appendix_owner_matches(resource, category, owner):
    """Verify an appendix child target belongs to the selected board owner."""
    if not isinstance(resource, dict) or not isinstance(owner, dict):
        return False
    scope = resource.get("owner_scope")
    if not isinstance(scope, dict) or set(scope) != {
            "target", "character_ids", "weapon_ids", "artifact_set_ids"}:
        return False
    target = resource.get("target")
    if scope.get("target") != target:
        return False
    for key in ("character_ids", "weapon_ids", "artifact_set_ids"):
        values = scope.get(key)
        if (not isinstance(values, list) or len(values) > 64
                or any(type(value) is not int or value <= 0 for value in values)
                or values != sorted(set(values))):
            return False
    if category in ("character", "talent", "constellation"):
        return owner.get("db_character_id") in scope["character_ids"]
    if category == "weapon":
        weapon = owner.get("weapon")
        weapon_id = weapon.get("weapon_id") if isinstance(weapon, dict) else None
        return weapon_id in scope["weapon_ids"]
    if category == "artifact":
        selected = {row.get("set_id") for row in owner.get("artifact_sets", [])
                    if isinstance(row, dict)}
        return bool(selected & set(scope["artifact_set_ids"]))
    return True


def compact_topology_receipt(receipt, *, issue_limit=20):
    if not isinstance(receipt, dict):
        return receipt
    issues = receipt.get("issues") if isinstance(receipt.get("issues"), list) else []
    coverage = copy.deepcopy(receipt.get("coverage") or {})
    actions = coverage.get("actions") if isinstance(coverage, dict) else None
    if isinstance(actions, dict):
        for key in ("missing_action_ids", "duplicate_action_ids"):
            if isinstance(actions.get(key), list) and len(actions[key]) > issue_limit:
                actions[key] = actions[key][:issue_limit]
                actions[key + "_truncated"] = True
    issue_total = receipt.get("issues_total", len(issues))
    return {key: copy.deepcopy(receipt.get(key)) for key in (
        "contract_id", "schema_version", "topology_id", "topology_fingerprint",
        "action_checkpoint_fingerprint", "dependencies_fingerprint", "receipt_fingerprint",
        "authority", "character_semantics", "status", "stages", "counts") if key in receipt} | {
        "coverage": coverage,
        "issues": copy.deepcopy(issues[:issue_limit]),
        "issues_total": issue_total,
        "issues_truncated": bool(receipt.get("issues_truncated")) or issue_total > issue_limit,
        "detail_resource": "event_topology_receipt",
    }


def compact_topology_binding_receipt(receipt, *, issue_limit=20):
    if not isinstance(receipt, dict):
        return receipt
    issues = receipt.get("issues") if isinstance(receipt.get("issues"), list) else []
    coverage = copy.deepcopy(receipt.get("coverage") or {})
    missing = coverage.get("missing_node_ids") if isinstance(coverage, dict) else None
    if isinstance(missing, list) and len(missing) > issue_limit:
        coverage["missing_node_ids"] = missing[:issue_limit]
        coverage["missing_node_ids_truncated"] = True
    return {key: copy.deepcopy(receipt.get(key)) for key in (
        "contract_id", "schema_version", "status", "topology_fingerprint",
        "bindings_fingerprint", "target_projection_fingerprint", "receipt_fingerprint",
        "character_semantics", "counts") if key in receipt} | {
        "coverage": coverage,
        "issues": copy.deepcopy(issues[:issue_limit]),
        "issues_total": receipt.get("issues_total", len(issues)),
        "issues_truncated": bool(receipt.get("issues_truncated") or len(issues) > issue_limit),
        "detail_resource": "event_topology_binding_receipt",
    }


class StagedCandidateSession(BoardCandidateSession):
    """Whiteboard-to-calculation workflow that stays in the same provider thread."""

    workflow = "staged"

    def __init__(self, *args, **kwargs):
        self.stage_state = {
            "contract": "staged-agent-workflow-v1",
            "phase": PHASES[0],
            "stage_revision": 0,
            "representation": "whiteboard",
            "energy_intent": None,
            "calculation_basis": None,
            "resource_stages": {},
            "resource_descriptors": {},
            "finding_stages": [],
            "history": [],
        }
        super().__init__(*args, **kwargs)

    @property
    def workflow_stage(self):
        return self.stage_state["phase"]

    def _is_board(self, value=None):
        candidate = self.candidate if value is None else value
        return isinstance(candidate, dict) and candidate.get("kind") == "genshin-whiteboard"

    def _validate_intent(self, candidate):
        if self._is_board(candidate):
            return BoardCandidateSession._validate_intent(self, candidate)
        return CandidateSession._validate_intent(self, candidate)

    def _current_board(self, candidate=None):
        value = self.candidate if candidate is None else candidate
        return value if self._is_board(value) else self._candidate_board(value)

    def _current_action_checkpoint(self, candidate=None):
        from genshin_app.equipment.action_authoring_projection import action_checkpoint
        return action_checkpoint(self._current_board(candidate), self.revision)

    def _action_structure_fingerprint(self, candidate=None):
        board = self._current_board(candidate)
        return fingerprint({
            "duration": board["duration"],
            "actions": [{"id": row["id"], "character": row["character"],
                         "action_type_id": row["action_type_id"],
                         "variant": row.get("variant", ""), "order": index}
                        for index, row in enumerate(board["actions"])],
        })

    @staticmethod
    def _action_timing_insensitive_fingerprint(candidate):
        """Fingerprint every candidate input except an atomic action-time move.

        Native time edits also advance the retained board revision and clear its
        action-admission preview.  Those fields are deterministic bookkeeping,
        not extra author intent, so they are excluded together with the paired
        board/native start fields.  Event topology and execution bindings remain
        in this projection: changing either one makes the edit non-timing-only.
        """
        projection = copy.deepcopy(candidate)
        projection.pop("revision", None)
        for action in projection.get("rotation_model", {}).get("actions", []):
            if isinstance(action, dict):
                action.pop("start", None)
        handoff = projection.get("extensions", {}).get("whiteboard_handoff", {})
        if isinstance(handoff, dict):
            handoff.pop("board_fingerprint", None)
            handoff.pop("action_admission_proposals", None)
            board = handoff.get("board")
            if isinstance(board, dict):
                board.pop("revision", None)
                for action in board.get("actions", []):
                    if isinstance(action, dict):
                        action.pop("start", None)
        for source in projection.get("action_admission_sources", []):
            if isinstance(source, dict):
                source.pop("accepted_fingerprint", None)
        return fingerprint(projection)

    def _is_pure_action_timing_change(self, previous_candidate, before_topology):
        return (
            before_topology == self._topology()
            and self._action_timing_insensitive_fingerprint(previous_candidate)
            == self._action_timing_insensitive_fingerprint(self.candidate)
        )

    def _loadout_structure_fingerprint(self, candidate=None):
        board = self._current_board(candidate)
        return fingerprint([{
            "id": actor["id"], "db_character_id": actor["db_character_id"],
            "level": actor["level"], "constellation": actor["constellation"],
            "weapon": actor.get("weapon"), "artifact_sets": actor.get("artifact_sets", []),
            "panel_basis_mode": actor.get("panel_basis_mode", "database"),
        } for actor in board["team"]])

    def _topology(self):
        if self._is_board():
            return None
        return self.candidate.get("extensions", {}).get("event_topology")

    def _topology_bindings(self):
        if self._is_board():
            return None
        return self.candidate.get("extensions", {}).get("event_topology_bindings")

    def topology_receipt(self):
        topology = self._topology()
        if not isinstance(topology, dict):
            return None
        receipt = lint_event_topology(topology)
        if receipt.get("status") == "invalid":
            return receipt
        current = self._current_action_checkpoint()
        saved = topology["action_checkpoint"]
        integration_issues = []
        if (saved.get("fingerprint") != current["fingerprint"]
                or saved.get("action_ids") != current["action_ids"]):
            integration_issues.append({
                "code": "event_topology_action_checkpoint_stale",
                "stage": "topology",
                "severity": "error",
                "path": "topology.action_checkpoint",
                "message": "动作检查点已变化，事件拓扑必须重新绑定。",
            })
        expected_database = fingerprint(self.database_snapshot)
        database_dependencies = [row for row in topology.get("dependencies", [])
                                 if row.get("kind") == "database_snapshot" and row.get("required") is True]
        if (len(database_dependencies) != 1
                or database_dependencies[0].get("fingerprint") != expected_database):
            integration_issues.append({
                "code": "event_topology_database_dependency_stale",
                "stage": "topology", "severity": "error", "path": "topology.dependencies",
                "message": "事件拓扑没有绑定本会话唯一的数据库快照。",
            })
        board = self._current_board()
        actors = {row.get("id"): row for row in board.get("team", [])
                  if isinstance(row, dict) and isinstance(row.get("id"), str)}
        descriptors = self.stage_state.get("resource_descriptors", {})
        resource_stages = self.stage_state.get("resource_stages", {})
        for source_index, source in enumerate(topology.get("sources", [])):
            for ref_index, source_ref in enumerate(source.get("source_refs", [])):
                if source_ref.get("kind") not in ("database", "database_appendix"):
                    continue
                resource_name = source_ref.get("reference")
                resource = self.resources.get(resource_name)
                path = f"topology.sources[{source_index}].source_refs[{ref_index}]"
                if source_ref.get("status") != "verified":
                    integration_issues.append({
                        "code": "event_topology_database_source_unverified",
                        "stage": "topology", "severity": "error", "path": path,
                        "message": "数据库来源必须使用App查询回执签发的verified引用。",
                    })
                    continue
                if resource is None:
                    integration_issues.append({
                        "code": "event_topology_source_resource_missing",
                        "stage": "topology", "severity": "error", "path": path,
                        "message": "已验证的数据库来源必须引用本会话缓存资料。",
                    })
                    continue
                resource_fingerprint = fingerprint(resource)
                if source_ref.get("fingerprint") != resource_fingerprint:
                    integration_issues.append({
                        "code": "event_topology_source_resource_stale",
                        "stage": "topology", "severity": "error", "path": path,
                        "message": "数据库来源指纹与本会话缓存资料不一致。",
                    })
                    continue
                descriptor = descriptors.get(resource_name) if isinstance(
                    descriptors, dict) else None
                if descriptor is None:
                    integration_issues.append({
                        "code": "event_topology_source_metadata_missing",
                        "stage": "topology", "severity": "error", "path": path,
                        "message": "数据库来源缺少App保存的原查询范围，不能证明资料归属。",
                    })
                    continue
                descriptor_stage = descriptor.get("stage") if isinstance(
                    descriptor, dict) else None
                metadata_valid = (
                    _valid_resource_descriptor(resource_name, descriptor)
                    and descriptor.get("fingerprint") == resource_fingerprint
                    and descriptor.get("bytes") == len(json_bytes(resource))
                    and resource_stages.get(resource_name) == descriptor_stage
                    and self._source_descriptor_visible(descriptor_stage)
                )
                if not metadata_valid:
                    integration_issues.append({
                        "code": "event_topology_source_metadata_invalid",
                        "stage": "topology", "severity": "error", "path": path,
                        "message": "数据库来源的查询范围、阶段或正文指纹与缓存资料不一致。",
                    })
                    continue

                query_kind = descriptor.get("query_kind")
                source_ref_kind = source_ref.get("kind")
                if ((source_ref_kind == "database_appendix") !=
                        (query_kind == "appendix")):
                    integration_issues.append({
                        "code": "event_topology_source_kind_mismatch",
                        "stage": "topology", "severity": "error", "path": path,
                        "message": "database与database_appendix引用必须匹配App保存的查询种类。",
                    })
                    continue

                category = source.get("category")
                owner = actors.get(source.get("owner_ref"))
                source_ref_kind = source_ref.get("kind")
                if source_ref_kind == "database_appendix":
                    target = resource.get("target") if isinstance(resource, dict) else None
                    target_identity = ({f"{target.get('target_type')}:{target.get('target_id')}"}
                                       if isinstance(target, dict) else set())
                    scope_valid = _appendix_owner_matches(resource, category, owner)
                elif category in ("character", "talent", "constellation"):
                    character_id = owner.get("db_character_id") if owner else None
                    scope_valid = (character_id is not None and _descriptor_covers_id(
                        descriptor, "character", "character_id", character_id))
                elif category == "weapon":
                    weapon = owner.get("weapon") if owner else None
                    weapon_id = weapon.get("weapon_id") if isinstance(weapon, dict) else None
                    scope_valid = (weapon_id is not None and _descriptor_covers_id(
                        descriptor, "weapon", "weapon_id", weapon_id))
                elif category == "artifact":
                    set_ids = {
                        selected.get("set_id") for selected in (
                            owner.get("artifact_sets", []) if owner else [])
                        if isinstance(selected, dict) and selected.get("set_id") is not None
                    }
                    scope_valid = any(_descriptor_covers_id(
                        descriptor, "artifact_set", "artifact_set_id", set_id)
                        for set_id in set_ids)
                else:
                    scope_valid = True
                if not scope_valid:
                    integration_issues.append({
                        "code": "event_topology_source_owner_mismatch",
                        "stage": "topology", "severity": "error", "path": path,
                        "message": "数据库查询范围没有覆盖该来源owner_ref当前对应的角色或装备。",
                    })
                    continue
                if source_ref_kind == "database_appendix":
                    if not _descriptor_annotation_scope(
                            descriptor, identities=target_identity):
                        integration_issues.append({
                            "code": "event_topology_appendix_scope_invalid",
                            "stage": "topology", "severity": "error", "path": path,
                            "message": "数据库附录来源必须来自对相应实体显式选择annotations的附录查询。",
                        })
                    appendix_authority = _appendix_authority(descriptor, resource)
                    if appendix_authority is None:
                        integration_issues.append({
                            "code": "event_topology_appendix_authority_invalid",
                            "stage": "topology", "severity": "error", "path": path,
                            "message": "数据库附录的审核状态与App保存的查询回执不一致。",
                        })
                    elif (appendix_authority == "pending"
                          and source.get("disposition") != "pending"):
                        integration_issues.append({
                            "code": "event_topology_appendix_not_executable",
                            "stage": "topology", "severity": "error", "path": path,
                            "message": "待审附录只有来源证明，必须保持pending且不能授予建模或执行权限。",
                        })
        if integration_issues:
            receipt = copy.deepcopy(receipt)
            receipt["status"] = "draft"
            receipt["stages"]["topology"] = {
                "status": "blocked", "blocking_issue_count": len(integration_issues)}
            receipt["stages"]["numeric_binding"] = {
                "status": "blocked", "blocking_issue_count": len(integration_issues)}
            existing_total = receipt.get("issues_total", len(receipt["issues"]))
            # Session integration failures are more actionable than repeated
            # standalone graph diagnostics, so retain them first.
            combined = [*integration_issues, *receipt["issues"]]
            receipt["issues"] = combined[:512]
            receipt["issues_total"] = existing_total + len(integration_issues)
            receipt["issues_truncated"] = (bool(receipt.get("issues_truncated"))
                                            or len(combined) > 512)
            receipt["receipt_fingerprint"] = fingerprint({
                key: value for key, value in receipt.items() if key != "receipt_fingerprint"})
        return receipt

    def validate_topology(self):
        receipt = self.topology_receipt()
        if receipt is None:
            raise PlanError("当前阶段尚未建立事件拓扑", "agent_stage_resource_unavailable")
        return receipt

    def _source_descriptor_visible(self, phase):
        return _phase_index(phase) <= _phase_index(self.workflow_stage)

    def topology_binding_receipt(self):
        topology = self._topology()
        bindings = self._topology_bindings()
        if not isinstance(topology, dict) or not isinstance(bindings, dict):
            return None
        return lint_event_topology_bindings(self.candidate, topology, bindings)

    def validate_topology_bindings(self):
        receipt = self.topology_binding_receipt()
        if receipt is None:
            raise PlanError("当前阶段尚未建立事件拓扑执行绑定",
                            "agent_stage_resource_unavailable")
        return receipt

    def _acceptance_projection(self, acceptance, *, issue_limit=20):
        """Expose only the acceptance dimensions owned by the current phase."""
        phase_index = _phase_index(self.workflow_stage)
        if phase_index < _phase_index("event_topology"):
            return None
        keys = [
            "contract", "status", "can_accept", "diagnostic_attempt",
            "character_semantics", "event_inventory",
        ]
        # Event topology has its own coverage receipt. Acceptance issues also
        # mention not-yet-readable execution bindings, so do not echo them
        # until the numeric phase owns those inputs.
        issues = []
        if phase_index >= _phase_index("numeric_model"):
            keys.extend(("energy_intent", "numeric_and_binding_validation"))
            issues = acceptance.get("issues") or []
        if phase_index >= _phase_index("calculation_closure"):
            keys.append("generic_engine_validation")
            keys.append("calculation_coverage")
        return {
            key: copy.deepcopy(acceptance.get(key)) for key in keys
            if key in acceptance
        } | {
            "issues": copy.deepcopy(issues[:issue_limit]),
            "issues_total": len(issues),
            "issues_truncated": len(issues) > issue_limit,
        }

    def compact_tool_response(self, data=None):
        from .tools import staged_read_chain

        summary = data if isinstance(data, dict) else self.summary()
        acceptance = summary.get("calculation_acceptance") or self.calculation_acceptance()
        gate = summary.get("eligible_next_phase") or self._next_gate()
        phase_index = _phase_index(self.workflow_stage)
        read_chain = staged_read_chain(self)
        response = {
            "workflow": self.workflow,
            "workflow_stage": self.workflow_stage,
            "stage_revision": self.stage_state["stage_revision"],
            "candidate_revision": self.revision,
            "representation": self.stage_state["representation"],
            "stage_status": summary.get("stage_status"),
            "stage_completion": copy.deepcopy(summary.get("stage_completion")),
            "eligible_next_phase": {
                "target": gate.get("target"), "allowed": bool(gate.get("allowed")),
                "issues": copy.deepcopy((gate.get("issues") or [])[:10]),
            },
            "saveable": bool(summary.get("saveable")),
            "computable": bool(summary.get("computable")),
            "applicable": bool(summary.get("applicable")),
            "has_calculation": self.calculated is not None,
            "required_context_reads": read_chain["required"],
            "recommended_context_reads": read_chain["recommended"],
            # Compatibility alias retained for existing clients.  It now
            # follows the stage-specific required order rather than guessing
            # candidate_board is sufficient in every phase.
            "read_next": copy.deepcopy(read_chain["required"]),
        }
        # Acceptance is also progressively disclosed.  A rollback must not
        # reveal that a later topology, binding or calculation once existed.
        acceptance_projection = self._acceptance_projection(acceptance, issue_limit=10)
        if acceptance_projection is not None:
            response["calculation_acceptance"] = acceptance_projection
        if phase_index >= _phase_index("event_topology"):
            topology = (summary.get("event_topology_coverage_receipt")
                        or self.topology_receipt())
            response["event_topology_coverage_receipt"] = compact_topology_receipt(
                topology, issue_limit=10)
        if phase_index >= _phase_index("numeric_model"):
            bindings = (summary.get("event_topology_execution_binding_receipt")
                        or self.topology_binding_receipt())
            response["event_topology_execution_binding_receipt"] = (
                compact_topology_binding_receipt(bindings, issue_limit=10))
            response["task_capability"] = copy.deepcopy(
                summary.get("task_capability")
                or classify_task_issues((self.report or {}).get("missing_inputs", [])))
        return response

    def _transition_gate(self, target, *, energy_intent=None):
        board = self._current_board()
        issues = []
        if target == "action_checkpoint" and not board["team"]:
            issues.append({"code": "loadout_team_empty", "message": "进入动作阶段前至少需要一名角色"})
        if target == "event_topology":
            if not board["team"]:
                issues.append({"code": "action_team_empty", "message": "事件拓扑需要至少一名角色"})
            if not board["actions"]:
                issues.append({"code": "action_checkpoint_empty", "message": "事件拓扑需要至少一个稳定动作"})
        receipt = self.topology_receipt()
        if target == "numeric_model" and (not receipt or receipt.get("status") not in ("topology_ready", "numeric_ready")):
            issues.append({"code": "event_topology_incomplete", "message": "事件盘点、来源或关系仍未完成"})
        if target == "calculation_closure":
            if not receipt or receipt.get("status") != "numeric_ready":
                issues.append({"code": "event_topology_numeric_incomplete", "message": "计算必需的拓扑数值槽仍未绑定"})
            binding_receipt = self.topology_binding_receipt()
            if not binding_receipt or binding_receipt.get("status") != "complete":
                issues.append({"code": "event_topology_execution_binding_incomplete",
                               "message": "活跃事件尚未完整绑定当前可执行输入"})
            effective_intent = (self.stage_state.get("energy_intent")
                                if energy_intent is None else energy_intent)
            if effective_intent not in ENERGY_INTENTS:
                issues.append({"code": "energy_intent_missing", "message": "需要声明单窗口、多轮或稳态能量证明范围"})
        return issues

    def transition_phase(self, target, expected_revision, expected_stage_revision, reason,
                         *, energy_intent=None):
        self._expect(expected_revision)
        if expected_stage_revision != self.stage_state["stage_revision"]:
            raise PlanError("工作流阶段已变化，请读取最新阶段摘要", "agent_stage_stale",
                            {"stage_revision": self.stage_state["stage_revision"]})
        if target not in PHASES:
            raise PlanError("未知目标阶段", "agent_stage_invalid")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise PlanError("阶段转换需要简要说明", "agent_tool_input")
        current = self.workflow_stage
        previous_intent = self.stage_state.get("energy_intent")
        if energy_intent is not None and energy_intent not in ENERGY_INTENTS:
            raise PlanError("未知能量证明范围", "agent_tool_input")
        if energy_intent is not None and target != "calculation_closure":
            raise PlanError("能量证明范围只在进入或停留计算闭环时声明",
                            "agent_stage_energy_intent_forbidden",
                            {"workflow_stage": current, "target_phase": target})
        if target == current:
            if energy_intent is not None and energy_intent != previous_intent:
                previous = (
                    copy.deepcopy(self.stage_state), self.calculated, self.report,
                    copy.deepcopy(self.provisional_authority),
                )
                try:
                    self.stage_state["energy_intent"] = energy_intent
                    # Energy policy is part of result acceptance.  A result
                    # calculated under another policy may remain useful as a
                    # diagnostic artifact, but it must never become accepted
                    # merely because the policy was weakened afterwards.
                    self.calculated, self.report = None, None
                    self.stage_state["calculation_basis"] = None
                    destination = ("numeric_model"
                                   if current == "calculation_closure" else current)
                    self.stage_state["phase"] = destination
                    self.stage_state["stage_revision"] += 1
                    self.stage_state["history"] = [*self.stage_state["history"], {
                        "stage_revision": self.stage_state["stage_revision"],
                        "from": current,
                        "to": destination,
                        "candidate_revision": self.revision,
                        "reason": reason.strip(),
                    }][-100:]
                    from .provisional_authority import invalidate_after_candidate_change
                    invalidate_after_candidate_change(self)
                    self.save()
                except Exception:
                    (self.stage_state, self.calculated, self.report,
                     self.provisional_authority) = previous
                    raise
                return self.summary()
            self.save()
            return self.summary()
        if _phase_index(target) > _phase_index(current) + 1:
            raise PlanError("阶段不能跳过；请先完成当前阶段", "agent_stage_skip")
        issues = (self._transition_gate(target, energy_intent=energy_intent)
                  if _phase_index(target) > _phase_index(current) else [])
        if issues:
            raise PlanError("当前阶段尚未满足转换条件", "agent_stage_incomplete", issues)

        previous = (copy.deepcopy(self.stage_state), copy.deepcopy(self.candidate),
                    copy.deepcopy(self.baseline), self.calculated, self.report,
                    copy.deepcopy(self.provisional_authority))
        try:
            if energy_intent is not None:
                self.stage_state["energy_intent"] = energy_intent
            elif _phase_index(target) < _phase_index("numeric_model"):
                # Returning to event/action design invalidates an older result
                # policy. It must be declared again when the rebuilt candidate
                # next enters calculation closure.
                self.stage_state["energy_intent"] = None
            if target == "event_topology":
                if self._is_board():
                    # Keep the handoff and all later provenance on the same
                    # immutable database snapshot owned by this session.
                    with self._read_db():
                        plan = make_handoff(self.candidate, self.db_path)
                    self.candidate = plan
                    self.baseline = copy.deepcopy(plan)
                    self.stage_state["representation"] = "native"
                    # Whiteboard proposals survive as inert declarations, but
                    # their lookup/approval ledgers are not portable to the new
                    # native plan identity.  Re-run the D2 chain in this exact
                    # plan/session before any provisional value is consumed.
                    from .provisional_authority import new_state
                    self.provisional_authority = new_state(
                        self.session_id, fingerprint(self.database_snapshot),
                        self.candidate,
                    )
                extensions = self.candidate.setdefault("extensions", {})
                # Entering from the action checkpoint starts a new graph.  A
                # backward transition from numeric/closure is an editing
                # operation and must retain the Agent's reviewed event work.
                if current == "action_checkpoint" or not isinstance(
                        extensions.get("event_topology"), dict):
                    checkpoint = self._current_action_checkpoint()
                    extensions["event_topology"] = new_event_topology(
                        "topology." + self.candidate["plan_id"],
                        action_checkpoint=checkpoint,
                        dependencies=[{
                            "dependency_id": "database_snapshot",
                            "kind": "database_snapshot",
                            "fingerprint": fingerprint(self.database_snapshot),
                            "required": True,
                        }],
                        note="角色特有事件由Agent声明；引擎只校验通用拓扑。",
                    )
                    extensions["event_topology_bindings"] = new_event_topology_bindings(
                        extensions["event_topology"])
                elif not isinstance(extensions.get("event_topology_bindings"), dict):
                    extensions["event_topology_bindings"] = new_event_topology_bindings(
                        extensions["event_topology"])
                self.calculated, self.report = None, None
                self.stage_state["calculation_basis"] = None
            elif target == "calculation_closure":
                # A fresh compose is the numeric/binding preflight for this exact revision.
                CandidateSession.compose(self, self.revision)
                missing = (self.report or {}).get("missing_inputs") or []
                blocking = blocking_task_issues(missing)
                if blocking:
                    raise PlanError("数值范式仍有当前任务必需缺项", "agent_stage_incomplete",
                                    blocking[:80])
            elif _phase_index(target) < _phase_index(current):
                self.calculated = None
                self.stage_state["calculation_basis"] = None
                if target in ("loadout_draft", "action_checkpoint", "event_topology"):
                    self.report = None

            self.stage_state["phase"] = target
            self.stage_state["stage_revision"] += 1
            self.stage_state["history"] = [*self.stage_state["history"], {
                "stage_revision": self.stage_state["stage_revision"],
                "from": current,
                "to": target,
                "candidate_revision": self.revision,
                "reason": reason.strip(),
            }][-100:]
            from .provisional_authority import invalidate_after_candidate_change
            invalidate_after_candidate_change(self)
            self.save()
        except Exception:
            (self.stage_state, self.candidate, self.baseline, self.calculated,
             self.report, self.provisional_authority) = previous
            raise
        return self.summary()

    def _candidate_replace_extension_state(self):
        return copy.deepcopy(self.stage_state)

    def _restore_candidate_replace_extension_state(self, state):
        self.stage_state = state

    def _record_stage_invalidation(self, target, reason):
        old = self.workflow_stage
        if target == old:
            return
        self.stage_state["phase"] = target
        self.stage_state["stage_revision"] += 1
        self.stage_state["history"] = [*self.stage_state["history"], {
            "stage_revision": self.stage_state["stage_revision"], "from": old, "to": target,
            "candidate_revision": self.revision, "reason": reason,
        }][-100:]

    def _after_candidate_replaced(self, previous_candidate):
        self.stage_state["calculation_basis"] = None
        if self._is_board(self.candidate):
            if (self.workflow_stage != "loadout_draft"
                    and self._loadout_structure_fingerprint(previous_candidate)
                    != self._loadout_structure_fingerprint(self.candidate)):
                self._record_stage_invalidation(
                    "loadout_draft", "角色、命座、武器或套装变化使后续阶段失效")
            return

        before_checkpoint = self._current_action_checkpoint(previous_candidate)
        before_structure = self._action_structure_fingerprint(previous_candidate)
        before_loadout = self._loadout_structure_fingerprint(previous_candidate)
        before_topology = copy.deepcopy(previous_candidate.get("extensions", {}).get("event_topology"))
        after_checkpoint = self._current_action_checkpoint(self.candidate)
        after_structure = self._action_structure_fingerprint(self.candidate)
        after_loadout = self._loadout_structure_fingerprint(self.candidate)
        topology_changed = before_topology != self._topology()
        target = None
        if before_loadout != after_loadout:
            target = "loadout_draft"
        elif before_checkpoint["fingerprint"] != after_checkpoint["fingerprint"]:
            if (before_structure == after_structure and isinstance(self._topology(), dict)
                    and self._is_pure_action_timing_change(
                        previous_candidate, before_topology)):
                # Relative topology anchors remain valid for a pure time move.
                # Rebind the checkpoint and force numeric execution to be run
                # again because reactions, resources and overlaps may change.
                self._topology()["action_checkpoint"] = copy.deepcopy(after_checkpoint)
                bindings = self._topology_bindings()
                if isinstance(bindings, dict):
                    # A pure timing move keeps stable topology-node identities.
                    # Rebind the contract to the refreshed action checkpoint;
                    # target freshness is still recomputed against this candidate.
                    bindings["topology_id"] = self._topology()["topology_id"]
                    bindings["topology_fingerprint"] = event_topology_fingerprint(self._topology())
                if self.workflow_stage == "calculation_closure":
                    target = "numeric_model"
            elif before_structure == after_structure and isinstance(self._topology(), dict):
                # Never sign a topology or binding edited in the same batch as
                # the action times.  Its old checkpoint stays stale until the
                # Agent explicitly reviews it again in the topology phase.
                if _phase_index(self.workflow_stage) > _phase_index("event_topology"):
                    target = "event_topology"
            else:
                target = "action_checkpoint"
        elif topology_changed and _phase_index(self.workflow_stage) > _phase_index("event_topology"):
            receipt = self.topology_receipt() or {}
            target = "numeric_model" if receipt.get("status") == "numeric_ready" else "event_topology"
        elif self.workflow_stage == "calculation_closure":
            target = "numeric_model"
        if target is not None:
            self._record_stage_invalidation(target, "候选修改使后续阶段失效")

    def _after_document_checkpoint_restored(self, previous_candidate):
        """Invalidate every stage fact that belonged to the old lineage."""

        del previous_candidate
        previous = self.workflow_stage
        next_revision = self.stage_state["stage_revision"] + 1
        history = [*self.stage_state.get("history", []), {
            "stage_revision": next_revision,
            "from": previous,
            "to": PHASES[0],
            "candidate_revision": self.revision,
            "reason": "文档恢复点建立了新的文档lineage",
        }][-100:]
        self.stage_state = {
            "contract": "staged-agent-workflow-v1",
            "phase": PHASES[0],
            "stage_revision": next_revision,
            "representation": "whiteboard",
            "energy_intent": None,
            "calculation_basis": None,
            "resource_stages": {},
            "resource_descriptors": {},
            "finding_stages": [],
            "history": history,
        }

    def replace_candidate(self, candidate, expected_revision, reason):
        if self._is_board(candidate):
            return BoardCandidateSession.replace_candidate(
                self, candidate, expected_revision, reason)
        return CandidateSession.replace_candidate(self, candidate, expected_revision, reason)

    def add_character(self, *args, **kwargs):
        if not self._is_board():
            raise PlanError("原生交接后请通过候选补丁同步修改角色与白板意图", "agent_tool_forbidden")
        return BoardCandidateSession.add_character(self, *args, **kwargs)

    def compose(self, expected_revision):
        if self.workflow_stage not in ("numeric_model", "calculation_closure"):
            raise PlanError("组合检查只在数值范式阶段后开放", "agent_stage_tool_forbidden")
        return CandidateSession.compose(self, expected_revision)

    def calculation_acceptance(self):
        if self.calculated is None:
            topology = self.topology_receipt() or {}
            bindings = self.topology_binding_receipt() or {}
            return pending_acceptance(
                energy_intent=self.stage_state.get("energy_intent"),
                topology_status=topology.get("status"),
                binding_status=bindings.get("status"),
            )
        acceptance = qualify_calculation(
            self.calculated,
            topology_receipt=self.topology_receipt() or {},
            topology_binding_receipt=self.topology_binding_receipt() or {},
            energy_intent=self.stage_state.get("energy_intent"),
            trusted_provisional_usage_receipt=self.provisional_usage_receipt(),
            candidate_revision=self.revision,
            stage_revision=self.stage_state["stage_revision"],
            database_snapshot_fingerprint=fingerprint(self.database_snapshot),
        )
        if self.stage_state.get("calculation_basis") != self._current_calculation_basis():
            acceptance = copy.deepcopy(acceptance)
            acceptance.update(status="rejected", can_accept=False, diagnostic_attempt=True,
                              generic_engine_validation="failed")
            acceptance["issues"] = [*acceptance["issues"], {
                "code": "calculation_basis_stale", "path": "stage_state.calculation_basis",
                "message": "计算结果不属于当前候选、拓扑、阶段修订或能量证明范围。",
            }]
        with self._read_db(check=False):
            database_current = (
                fingerprint(self._db_snapshot()) == fingerprint(self.database_snapshot)
            )
        if not database_current:
            acceptance = copy.deepcopy(acceptance)
            acceptance.update(status="rejected", can_accept=False, diagnostic_attempt=True,
                              generic_engine_validation="failed")
            acceptance["issues"] = [*acceptance["issues"], {
                "code": "agent_database_changed", "path": "database_snapshot",
                "message": "资料库来源已变化；草稿仍可保存，但旧结果不能计算、应用或接纳。",
            }]
        return acceptance

    def _current_calculation_basis(self):
        receipt = self.topology_receipt() or {}
        binding_receipt = self.topology_binding_receipt() or {}
        return {
            "candidate_fingerprint": fingerprint(self.candidate),
            "topology_receipt_fingerprint": receipt.get("receipt_fingerprint"),
            "topology_binding_receipt_fingerprint": binding_receipt.get("receipt_fingerprint"),
            "energy_intent": self.stage_state.get("energy_intent"),
            "stage_revision": self.stage_state["stage_revision"],
            "provisional_usage_receipt_fingerprint": fingerprint(
                self.provisional_usage_receipt())
            if self.provisional_usage_receipt() is not None else None,
        }

    def _after_calculated(self):
        self.stage_state["calculation_basis"] = self._current_calculation_basis()

    def _after_calculation_save_failed(self):
        self.stage_state["calculation_basis"] = None

    def calculate(self, expected_revision):
        if self.workflow_stage != "calculation_closure":
            raise PlanError("计算仅在计算闭环阶段开放", "agent_stage_tool_forbidden")
        CandidateSession.calculate(self, expected_revision)
        acceptance = self.calculation_acceptance()
        if acceptance["status"] != "accepted":
            raise PlanError("本次计算已保留为诊断尝试，尚不能应用", "agent_calculation_rejected",
                            acceptance["issues"][:80])
        return self.summary()

    def _next_gate(self):
        index = _phase_index(self.workflow_stage)
        if index == len(PHASES) - 1:
            return {"target": None, "allowed": False, "issues": []}
        target = PHASES[index + 1]
        issues = self._transition_gate(target)
        return {"target": target, "allowed": not issues, "issues": issues[:20]}

    def _energy_status(self, acceptance):
        if self.calculated is None:
            return "not_checked"
        energy_codes = {"energy_intent_missing", "explicit_window_energy_failed",
                        "sustainable_energy_unproven"}
        return ("failed" if any(row.get("code") in energy_codes
                                for row in acceptance.get("issues", [])) else "passed")

    def _stage_history_projection(self):
        history = self.stage_state["history"]
        current_index = _phase_index(self.workflow_stage)
        visible = []
        for row in history[-8:]:
            projected = copy.deepcopy(row)
            involves_future_stage = any(
                _phase_index(projected[key]) > current_index for key in ("from", "to"))
            if involves_future_stage:
                projected["reason"] = "后续阶段记录已在回退时隐藏"
                projected["reason_redacted"] = True
            else:
                projected["reason"] = projected["reason"][:512]
                projected["reason_redacted"] = False
            visible.append(projected)
        return {
            "stage_history": visible,
            "stage_history_total": len(history),
            "stage_history_truncated": len(history) > len(visible),
        }

    def summary(self):
        if self._is_board():
            result = BoardCandidateSession.summary(self)
        else:
            result = CandidateSession.summary(self)
            if self.workflow_stage != "calculation_closure":
                for key in ("missing_inputs", "issues"):
                    rows = [row for row in result.get(key, [])
                            if row.get("code") != "agent_calculation_required"]
                    result[key] = rows
                    result[key + "_total"] = len(rows)
                    result[key + "_truncated"] = False
            intent_diff, intent_total = _diff(self.document, self._candidate_board())
            for row in intent_diff:
                raw_path = row["path"]
                row["path"] = "/extensions/whiteboard_handoff/board" + raw_path
                for side, resource in (("before", "original_board"),
                                       ("after", "candidate_board")):
                    value = row.get(side)
                    if isinstance(value, dict) and "preview" in value:
                        value["resource"] = resource
                        value["pointer"] = raw_path
            result["intent_diff"] = intent_diff
            result["intent_diff_total"] = intent_total
            result["intent_diff_truncated"] = len(intent_diff) < intent_total
        acceptance = self.calculation_acceptance()
        topology = self.topology_receipt()
        topology_bindings = self.topology_binding_receipt()
        gate = self._next_gate()
        phase_index = _phase_index(self.workflow_stage)
        if phase_index < _phase_index("numeric_model"):
            # CandidateSession.summary() compares the retained native plan
            # against its native baseline. After a rollback that diff may
            # contain numeric inputs and execution bindings from a later phase.
            # Public/UI summaries obey the same disclosure boundary as model
            # tools; the saved candidate itself is retained for later reuse.
            result.update(
                changes=[], diff=[], diff_total=0, diff_truncated=False,
                summary={}, receipt_fingerprint=None,
            )
            if self.workflow_stage == "loadout_draft":
                prefixes = (
                    "/extensions/whiteboard_handoff/board/title",
                    "/extensions/whiteboard_handoff/board/notes",
                    "/extensions/whiteboard_handoff/board/team",
                )
            elif self.workflow_stage == "action_checkpoint":
                prefixes = (
                    "/extensions/whiteboard_handoff/board/duration",
                    "/extensions/whiteboard_handoff/board/actions",
                )
            else:
                prefixes = ()
            visible_intent = ([
                row for row in result.get("intent_diff", [])
                if row.get("path", "").startswith(prefixes)
            ] if prefixes else [])
            result["intent_diff"] = visible_intent
            result["intent_diff_total"] = len(visible_intent)
            result["intent_diff_truncated"] = False
        finding_stages = self.stage_state.get("finding_stages", [])
        result["finding_count"] = sum(
            _phase_index(stage) <= phase_index for stage in finding_stages)
        phase_status = "ready" if gate["allowed"] else "in_progress"
        if self.workflow_stage == "calculation_closure":
            phase_status = "accepted" if acceptance["status"] == "accepted" else (
                "diagnostic" if acceptance["diagnostic_attempt"] else "in_progress")
        calculation_accepted = (
            self.calculated is not None
            and acceptance.get("status") == "accepted"
        )
        # A staged whiteboard is converted to the native plan representation
        # at the event-topology boundary, but its public application target
        # remains the originating whiteboard.  Preserve that product identity
        # once the calculated result is accepted so GUI/headless delivery can
        # apply the reviewed board and its calculated package through the
        # whiteboard transaction instead of misclassifying it as a native-only
        # result.
        application_kind = "whiteboard_draft" if self._is_board() else (
            "whiteboard_calculated"
            if self.kind == "whiteboard" and calculation_accepted
            else "calculated_result"
            if calculation_accepted
            else "native_calculation_draft"
        )
        minimum_structure_ready = bool(self._current_board().get("team"))
        task_capability = classify_task_issues(
            (self.report or {}).get("missing_inputs", []))
        agent_audit = "not_declared"
        if phase_index >= _phase_index("event_topology"):
            agent_audit = "in_progress"
        if (self.workflow_stage == "calculation_closure"
                and acceptance["status"] == "accepted"):
            agent_audit = "accepted"
        result.update({
            "workflow": self.workflow,
            "application_kind": application_kind,
            "workflow_stage": self.workflow_stage,
            "stage_revision": self.stage_state["stage_revision"],
            "stage_status": phase_status,
            **self._stage_history_projection(),
            "eligible_next_phase": gate,
            "stage_completion": {
                # One selected character is enough for the minimum document
                # structure. It says nothing about equipment, semantics or
                # calculation quality.
                "minimum_structure": (
                    "ready" if minimum_structure_ready else "incomplete"
                ),
                # The Agent audit is deliberately independent of transition
                # eligibility and only becomes accepted after final closure.
                "agent_audit": agent_audit,
                "next_stage_eligible": bool(gate["allowed"]),
            },
            # A draft is durable user work even when its database dependency
            # has gone stale.  Freshness still gates new calculation and final
            # application below; it must never prevent preserving the draft.
            "saveable": True,
            "computable": bool(result.get("database_current")
                               and phase_index >= _phase_index("numeric_model")
                               and not task_capability["blocking"]),
            "applicable": False,
        })
        acceptance_projection = self._acceptance_projection(acceptance)
        if acceptance_projection is not None:
            result["calculation_acceptance"] = acceptance_projection
        if phase_index >= _phase_index("event_topology"):
            result["event_topology_coverage_receipt"] = compact_topology_receipt(topology)
        if phase_index >= _phase_index("numeric_model"):
            result.update(
                energy_status=self._energy_status(acceptance),
                energy_detail={"intent": self.stage_state.get("energy_intent")},
                task_capability=task_capability,
                event_topology_execution_binding_receipt=compact_topology_binding_receipt(
                    topology_bindings),
            )
        if not self._is_board():
            can_accept = (self.workflow_stage == "calculation_closure"
                          and acceptance["status"] == "accepted"
                          and result.get("database_current"))
            result["can_accept"] = result["ready"] = bool(can_accept)
            result["applicable"] = bool(can_accept)
            if not can_accept:
                result["receipt_fingerprint"] = None
        from .candidate_readiness import readiness_projection
        result.update(readiness_projection(result))
        return result

    def accept(self, kind, current_document, expected_revision, expected_receipt):
        if self._is_board():
            return BoardCandidateSession.accept(
                self, kind, current_document, expected_revision, expected_receipt)
        acceptance = self.calculation_acceptance()
        if self.workflow_stage != "calculation_closure" or acceptance["status"] != "accepted":
            raise PlanError("只有通过计算闭环验收的结果可以应用", "agent_calculation_rejected",
                            acceptance["issues"][:80])
        return CandidateSession.accept(
            self, kind, current_document, expected_revision, expected_receipt)

    def resume_document(self, document):
        if self._is_board():
            return BoardCandidateSession.resume_document(self, document)
        if (self._document_fingerprint(document) != self.document_fingerprint
                and (self.workflow_stage != "calculation_closure"
                     or self.calculation_acceptance()["status"] != "accepted")):
            acceptance = self.calculation_acceptance()
            raise PlanError("诊断计算不能推进白板基线", "agent_calculation_rejected",
                            acceptance["issues"][:80])
        return CandidateSession.resume_document(self, document)

    def tool_definitions(self):
        from .tools import definitions
        return definitions(self.workflow)

    def active_tool_definitions(self):
        """Current-stage schema projection for stateless native providers."""
        from .tools import definitions_for_session

        return definitions_for_session(self)

    def stage_context(self):
        from .tools import staged_disclosure

        result = {
            "contract": self.stage_state["contract"],
            "workflow_stage": self.workflow_stage,
            "stage_revision": self.stage_state["stage_revision"],
            "representation": self.stage_state["representation"],
            "eligible_next_phase": self._next_gate(),
            "context_policy": (
                "全部捆绑Skill资料可按需读取；领域上下文只读取当前阶段列出的资源，"
                "查询结果通过resource ID复用。"
            ),
            **self._stage_history_projection(),
            **(staged_disclosure(self) or {}),
        }
        if _phase_index(self.workflow_stage) >= _phase_index("numeric_model"):
            result["energy_intent"] = self.stage_state.get("energy_intent")
        if _phase_index(self.workflow_stage) >= _phase_index("event_topology"):
            result.update(
                event_topology_capabilities_resource="event_topology_capabilities",
            )
        if _phase_index(self.workflow_stage) >= _phase_index("numeric_model"):
            result["event_topology_binding_capabilities_resource"] = (
                "event_topology_binding_capabilities")
        return result

    def validate_restored_stage(self):
        """Re-run semantic phase gates after all checkpoint artifacts are loaded.

        The structural history checks below prevent malformed state, but the
        checkpoint is also the authority used by tool and disclosure policy.
        Therefore a self-consistent edited history must not be able to promote
        an event draft into the numeric or closure phases after restart.
        """
        phase = self.workflow_stage
        for name, descriptor in self.stage_state["resource_descriptors"].items():
            resource = self.resources.get(name)
            if (resource is None or fingerprint(resource) != descriptor["fingerprint"]
                    or len(json_bytes(resource)) != descriptor["bytes"]):
                raise PlanError("分阶段查询资料描述与保存正文不一致", "agent_checkpoint_invalid")
        if self.calculated is not None and phase != "calculation_closure":
            raise PlanError("分阶段计算结果只能属于计算闭环阶段", "agent_checkpoint_invalid")
        if phase not in _NATIVE_PHASES:
            return
        if not isinstance(self._topology(), dict):
            raise PlanError("原生分阶段检查点缺少事件拓扑", "agent_checkpoint_invalid")
        receipt = self.topology_receipt() or {}
        status = receipt.get("status")
        if phase == "numeric_model" and status not in ("topology_ready", "numeric_ready"):
            raise PlanError("数值阶段检查点没有通过事件拓扑阶段门", "agent_checkpoint_invalid")
        if phase == "calculation_closure":
            if status != "numeric_ready":
                raise PlanError("计算闭环检查点没有通过数值绑定阶段门", "agent_checkpoint_invalid")
            binding_receipt = self.topology_binding_receipt() or {}
            if binding_receipt.get("status") != "complete":
                raise PlanError("计算闭环检查点缺少完整且新鲜的事件执行绑定",
                                "agent_checkpoint_invalid")
            if self.stage_state.get("energy_intent") not in ENERGY_INTENTS:
                raise PlanError("计算闭环检查点缺少能量证明范围", "agent_checkpoint_invalid")

    def restore_stage_state(self, state):
        # Checkpoints created before staged query descriptors remain readable.
        # New queries always add descriptors; legacy resources stay unlisted
        # rather than reconstructing selectors from their response bodies.
        if isinstance(state, dict) and "resource_descriptors" not in state:
            state = copy.deepcopy(state)
            state["resource_descriptors"] = {}
        if (not isinstance(state, dict) or set(state) != {
                    "contract", "phase", "stage_revision", "representation", "energy_intent",
                    "calculation_basis", "resource_stages", "resource_descriptors",
                    "finding_stages", "history"}
                or state.get("contract") != "staged-agent-workflow-v1"
                or state.get("phase") not in PHASES or type(state.get("stage_revision")) is not int
                or state["stage_revision"] < 0 or state.get("representation") not in ("whiteboard", "native")
                or state.get("energy_intent") not in ENERGY_INTENTS | {None}
                or not isinstance(state.get("history"), list) or len(state["history"]) > 100
                or not isinstance(state.get("resource_stages"), dict)
                or any(type(name) is not str or phase not in PHASES
                       for name, phase in state.get("resource_stages", {}).items())
                or not isinstance(state.get("resource_descriptors"), dict)
                or not set(state.get("resource_descriptors", {})) <= set(
                    state.get("resource_stages", {}))
                or any(not _valid_resource_descriptor(name, descriptor)
                       or descriptor["stage"] != state["resource_stages"].get(name)
                       for name, descriptor in state.get("resource_descriptors", {}).items())
                or not isinstance(state.get("finding_stages"), list)
                or any(phase not in PHASES for phase in state.get("finding_stages", []))):
            raise PlanError("分阶段会话检查点不合法", "agent_checkpoint_invalid")
        history = state["history"]
        valid_history = all(
            isinstance(row, dict) and set(row) == {
                "stage_revision", "from", "to", "candidate_revision", "reason"}
            and type(row["stage_revision"]) is int and row["stage_revision"] > 0
            and row["from"] in PHASES and row["to"] in PHASES
            and type(row["candidate_revision"]) is int and row["candidate_revision"] >= 0
            and isinstance(row["reason"], str) and 0 < len(row["reason"]) <= 4096
            for row in history)
        valid_sequence = all(
            left["stage_revision"] + 1 == right["stage_revision"]
            and left["to"] == right["from"]
            for left, right in pairwise(history))
        if (not valid_history or not valid_sequence
                or (not history) != (state["stage_revision"] == 0)
                or (history and (history[-1]["stage_revision"] != state["stage_revision"]
                                 or history[-1]["to"] != state["phase"]))):
            raise PlanError("分阶段会话历史与当前阶段不一致", "agent_checkpoint_invalid")
        basis = state.get("calculation_basis")
        if (isinstance(basis, dict)
                and "provisional_usage_receipt_fingerprint" not in basis
                and set(basis) == {
                    "candidate_fingerprint", "topology_receipt_fingerprint",
                    "topology_binding_receipt_fingerprint", "energy_intent",
                    "stage_revision",
                }):
            basis = copy.deepcopy(basis)
            basis["provisional_usage_receipt_fingerprint"] = None
            state = copy.deepcopy(state)
            state["calculation_basis"] = basis
        if basis is not None and (not isinstance(basis, dict) or set(basis) != {
                "candidate_fingerprint", "topology_receipt_fingerprint",
                "topology_binding_receipt_fingerprint", "energy_intent", "stage_revision",
                "provisional_usage_receipt_fingerprint"}):
            raise PlanError("分阶段计算依据不合法", "agent_checkpoint_invalid")
        self.stage_state = copy.deepcopy(state)
        if (_phase_index(self.stage_state["phase"]) < _phase_index("numeric_model")
                and self.stage_state.get("energy_intent") is not None):
            # Older staged checkpoints could persist a result policy in an
            # action/event phase. Treat it as stale migration data, never as a
            # fresh declaration for the next calculation closure.
            self.stage_state["energy_intent"] = None


__all__ = ["PHASES", "StagedCandidateSession", "compact_topology_binding_receipt",
           "event_topology_binding_capabilities", "event_topology_capabilities"]
