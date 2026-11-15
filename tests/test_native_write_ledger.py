from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from contextlib import contextmanager

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.bridge_protocol import raw_json_hash
from genshin_app.agent_chat.native_write_ledger import (
    SETTLED_FAILURE,
    SETTLED_SUCCESS,
    UNKNOWN,
    NativeWriteCoordinator,
    SessionCandidatePatchAdapter,
    SessionWriteAuthority,
    begin_write,
    create_write_ledger,
    lookup_write,
    make_write_identity,
    normalize_write_ledger,
    recover_interrupted_writes,
    settle_write,
    snapshot_write_ledger,
)
from genshin_app.agent_chat.staged_candidate import StagedCandidateSession
from genshin_app.equipment.event_topology import (
    AUTHORITY,
    CONTRACT_ID,
    event_topology_fingerprint,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import new_whiteboard
from genshin_db import queries
from genshin_db.locking import database_lock

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _binding(**changes):
    value = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "step_id": "step-1",
        "epoch_id": "epoch-1",
        "document_id": "document-1",
        "runtime_generation": "dsh-v2",
        "database_fingerprint": HASH_A,
        "tool_manifest_hash": HASH_B,
        "writer_fence": "fence-1",
    }
    value.update(changes)
    return value


def _identity(*, call_id="call-1", args_hash=HASH_C, **changes):
    value = {
        **_binding(),
        "call_id": call_id,
        "tool_name": "gs_patch_candidate",
        "canonical_args_hash": args_hash,
        "candidate_revision": 7,
        "stage_revision": 3,
    }
    value.update(changes)
    return make_write_identity(**value)


class _Adapter:
    def __init__(self, *, mode="success"):
        self.lock = threading.RLock()
        self.mode = mode
        self.executions = 0
        self.document_id = "document-1"
        self.candidate_revision = 7
        self.stage_revision = 3
        self.database_fingerprint = HASH_A
        self.tool_manifest_hash = HASH_B
        self.writer_fence = "fence-1"
        self.value = 1
        self.workflow_phase = "event_topology"

    @contextmanager
    def atomic_boundary(self):
        with self.lock:
            yield

    def current_context(self):
        return {
            "document_id": self.document_id,
            "candidate_revision": self.candidate_revision,
            "stage_revision": self.stage_revision,
            "database_fingerprint": self.database_fingerprint,
            "tool_manifest_hash": self.tool_manifest_hash,
            "writer_fence": self.writer_fence,
            "domain_fingerprint": fingerprint({
                "candidate": {
                    "revision": self.candidate_revision,
                    "value": self.value,
                },
                "workflow_phase": self.workflow_phase,
                "stage_state": {"stage_revision": self.stage_revision,
                                "phase": self.workflow_phase},
            }),
        }

    def execute(self, tool_name, arguments):
        assert tool_name == "gs_patch_candidate"
        assert arguments["expected_revision"] == 7
        self.executions += 1
        if self.mode == "failure":
            return {"code": 1, "reason": "agent_tool_input", "message": "bad"}
        if self.mode == "raise_after_mutation":
            self.value += 1
            self.candidate_revision += 1
            raise RuntimeError("provider-visible details must not escape")
        if self.mode == "failure_after_mutation":
            self.value += 1
            self.candidate_revision += 1
            return {"code": 1, "reason": "broken_tool", "message": "bad"}
        if self.mode == "failure_after_stage_change":
            self.stage_revision += 1
            self.workflow_phase = "action_checkpoint"
            return {"code": 1, "reason": "broken_tool", "message": "bad"}
        if self.mode == "failure_after_manifest_change":
            self.tool_manifest_hash = HASH_C
            return {"code": 1, "reason": "broken_tool", "message": "bad"}
        if self.mode == "failure_after_database_change":
            self.database_fingerprint = HASH_C
            return {"code": 1, "reason": "broken_tool", "message": "bad"}
        if self.mode == "fence_changes_during_write":
            self.value += 1
            self.candidate_revision += 1
            self.writer_fence = "fence-2"
            return {"code": 0, "data": {"revision": self.candidate_revision}}
        if self.mode == "success_without_revision":
            self.value += 1
            return {"code": 0, "data": {"revision": self.candidate_revision}}
        if self.mode == "success_with_stage_rollback":
            self.value += 1
            self.candidate_revision += 1
            self.stage_revision += 1
            self.workflow_phase = "action_checkpoint"
            self.tool_manifest_hash = HASH_C
            return {"code": 0, "data": {"revision": self.candidate_revision}}
        self.value += 1
        self.candidate_revision += 1
        return {"code": 0, "data": {"revision": self.candidate_revision}}


def _arguments(reason="phase 4 write"):
    return json.dumps({
        "expected_revision": 7,
        "reason": reason,
        "operations": [{"op": "replace", "path": "/title", "value": "new"}],
    }, ensure_ascii=False, separators=(",", ":"))


def _coordinator(adapter=None, *, persist=None, ledger=None, binding=None):
    adapter = adapter or _Adapter()
    ledger = ledger or create_write_ledger("conversation-1")
    persisted = []

    def save(value):
        if persist is not None:
            persist(value)
        persisted.append(copy.deepcopy(value))

    coordinator = NativeWriteCoordinator(
        adapter,
        binding=binding or _binding(),
        ledger=ledger,
        persist=save,
    )
    return coordinator, adapter, ledger, persisted


def _execute(coordinator, *, call_id="call-1", raw=None):
    raw = raw or _arguments()
    return coordinator.execute_patch(
        call_id=call_id,
        arguments_json=raw,
        canonical_args_hash=raw_json_hash(raw),
        candidate_revision=7,
        stage_revision=3,
    )


@pytest.fixture
def real_staged(tmp_path, monkeypatch):
    db = tmp_path / "engineering.db"
    db.touch()
    meta = {"version_lines": ["release"], "datasets": {"equipment": {"lines": {
        "release": {"state": "ready", "schema_version": 1,
                    "upstream_commit": "a" * 40,
                    "built_at": "2026-10-05T00:00:00Z"},
    }}}}
    monkeypatch.setattr(queries, "get_meta", lambda _: copy.deepcopy(meta))
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {
        "character": {"id": 1, "name": "工程角色",
                      "weapon_type": "WEAPON_SWORD_ONE_HAND",
                      "atk_90": 300, "hp_90": 12000, "def_90": 800},
    })
    return StagedCandidateSession(
        uuid.uuid4().hex, tmp_path / "session", db,
        "whiteboard", new_whiteboard(),
    )


def _stage_call(session, name, **arguments):
    return session.call_tool(name, arguments)


def _stage_transition(session, target, **extra):
    return _stage_call(
        session,
        "gs_transition_phase",
        target_phase=target,
        expected_revision=session.revision,
        expected_stage_revision=session.stage_state["stage_revision"],
        reason="完成当前阶段",
        **extra,
    )


def _topology(session):
    checkpoint = session._current_action_checkpoint()
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": 1,
        "topology_id": "topology.native-ledger",
        "authority": AUTHORITY,
        "declaration": {
            "character_semantics": "agent_declared",
            "completeness": "declared_complete",
            "note": "事务账真实会话夹具",
        },
        "action_checkpoint": checkpoint,
        "dependencies": [{
            "dependency_id": "database_snapshot",
            "kind": "database_snapshot",
            "fingerprint": fingerprint(session.database_snapshot),
            "required": True,
        }],
        "sources": [{
            "source_id": "source.skill",
            "category": "talent",
            "owner_ref": "character-1",
            "description": "夹具动作来源",
            "disposition": "modeled",
            "node_ids": ["node.action"],
            "reason": None,
            "source_refs": [{
                "kind": "agent_research",
                "reference": "fixture reviewed declaration",
                "fingerprint": None,
                "status": "declared",
            }],
        }],
        "nodes": [{
            "node_id": "node.action",
            "kind": "action_anchor",
            "status": "bound",
            "schedule": {
                "kind": "point",
                "anchor": {
                    "kind": "action",
                    "ref_id": checkpoint["action_ids"][0],
                    "phase": "start",
                },
                "offset_seconds": 0,
                "order": 0,
            },
            "source_ids": ["source.skill"],
            "parameter_slots": [],
            "note": "",
        }],
        "edges": [],
    }


def _advance_real_session_to_numeric(session):
    assert _stage_call(
        session, "gs_add_character", expected_revision=session.revision,
        reason="添加工程角色", db_character_id=1,
    )["code"] == 0
    assert _stage_transition(session, "action_checkpoint")["code"] == 0
    action = {
        "id": "action.skill",
        "character": "character-1",
        "action_type_id": "action.elemental_skill",
        "label": "元素战技",
        "start": 1,
        "variant": "tap",
        "notes": "",
    }
    assert _stage_call(
        session, "gs_patch_whiteboard", expected_revision=session.revision,
        reason="建立动作检查点", operations=[{
            "op": "add", "path": "/actions/-", "value": action,
        }],
    )["code"] == 0
    assert _stage_transition(session, "event_topology")["code"] == 0
    assert _stage_call(
        session, "gs_patch_candidate", expected_revision=session.revision,
        reason="写入完整事件声明", operations=[{
            "op": "replace", "path": "/extensions/event_topology",
            "value": _topology(session),
        }],
    )["code"] == 0
    assert _stage_transition(session, "numeric_model")["code"] == 0
    topology = session._topology()
    assert _stage_call(
        session, "gs_patch_candidate", expected_revision=session.revision,
        reason="绑定空执行合同", operations=[
            {
                "op": "replace",
                "path": "/extensions/event_topology_bindings/topology_id",
                "value": topology["topology_id"],
            },
            {
                "op": "replace",
                "path": "/extensions/event_topology_bindings/topology_fingerprint",
                "value": event_topology_fingerprint(topology),
            },
        ],
    )["code"] == 0
    assert session.workflow_stage == "numeric_model"


def test_success_is_durable_and_exact_replay_never_executes_twice():
    coordinator, adapter, ledger, persisted = _coordinator()

    first = _execute(coordinator)
    second = _execute(coordinator)

    assert first == second == {"code": 0, "data": {"revision": 8}}
    assert adapter.executions == 1
    assert len(persisted) == 2  # inflight is durable before execution, then settlement
    entry = ledger["entries"]["call-1"]
    assert entry["state"] == SETTLED_SUCCESS
    assert entry["before_fingerprint"] != entry["after_fingerprint"]
    assert entry["post_candidate_revision"] == 8
    assert entry["post_stage_revision"] == 3
    assert entry["post_tool_manifest_hash"] == HASH_B
    assert json.loads(entry["result_json"]) == first
    normalize_write_ledger(snapshot_write_ledger(ledger))
    # Raw arguments and user reason are not persisted in the write ledger.
    assert "phase 4 write" not in json.dumps(ledger, ensure_ascii=False)


def test_reconstructed_coordinator_replays_durable_settlement_without_execution():
    coordinator, adapter, ledger, _ = _coordinator()
    first = _execute(coordinator)
    durable = snapshot_write_ledger(ledger)
    replay_persists = []
    reconstructed = NativeWriteCoordinator(
        adapter,
        binding=_binding(),
        ledger=durable,
        persist=lambda value: replay_persists.append(value),
    )

    second = _execute(reconstructed)

    assert second == first
    assert adapter.executions == 1
    assert replay_persists == []


def test_same_call_with_different_raw_argument_identity_is_conflict():
    coordinator, adapter, ledger, _ = _coordinator()
    _execute(coordinator)
    changed = _arguments(reason="same meaning, different bytes")

    with pytest.raises(PlanError) as error:
        _execute(coordinator, raw=changed)

    assert error.value.reason == "call_id_conflict"
    assert adapter.executions == 1
    assert ledger["entries"]["call-1"]["state"] == SETTLED_SUCCESS


def test_same_call_bound_to_different_document_is_conflict():
    ledger = create_write_ledger("conversation-1")
    first = _identity(document_id="document-1")
    begin_write(ledger, identity=first, before_fingerprint=HASH_A)

    with pytest.raises(PlanError) as error:
        begin_write(
            ledger,
            identity=_identity(document_id="document-2"),
            before_fingerprint=HASH_A,
        )

    assert error.value.reason == "call_id_conflict"
    assert ledger["entries"]["call-1"]["identity"]["document_id"] == "document-1"
    assert ledger["entries"]["call-1"]["document_id"] == "document-1"


@pytest.mark.parametrize("document_id", ["", "contains spaces", "secret:value"])
def test_document_identity_uses_strict_bridge_identity_syntax(document_id):
    with pytest.raises(PlanError) as error:
        _coordinator(binding=_binding(document_id=document_id))

    assert error.value.reason == "native_write_ledger_invalid"


def test_clean_tool_failure_is_settled_and_replayed_without_execution():
    coordinator, adapter, ledger, _ = _coordinator(_Adapter(mode="failure"))

    first = _execute(coordinator)
    second = _execute(coordinator)

    assert first == second
    assert first["code"] == 1
    assert adapter.executions == 1
    entry = ledger["entries"]["call-1"]
    assert entry["state"] == SETTLED_FAILURE
    assert entry["before_fingerprint"] == entry["after_fingerprint"]


@pytest.mark.parametrize("mode", ["raise_after_mutation", "failure_after_mutation"])
def test_ambiguous_mutation_becomes_unknown_and_is_never_replayed(mode):
    coordinator, adapter, ledger, _ = _coordinator(_Adapter(mode=mode))

    with pytest.raises(PlanError) as first_error:
        _execute(coordinator)
    with pytest.raises(PlanError) as retry_error:
        _execute(coordinator)

    assert first_error.value.reason == "write_outcome_unknown"
    assert retry_error.value.reason == "write_outcome_unknown"
    assert adapter.executions == 1
    entry = ledger["entries"]["call-1"]
    assert entry["state"] == UNKNOWN
    assert entry["result_json"] is None
    assert entry["result_hash"] is None


@pytest.mark.parametrize(
    "mode",
    ["failure_after_stage_change", "failure_after_manifest_change"],
)
def test_failure_with_only_stage_or_manifest_change_becomes_unknown(mode):
    coordinator, adapter, ledger, _ = _coordinator(_Adapter(mode=mode))

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "write_outcome_unknown"
    assert adapter.executions == 1
    entry = ledger["entries"]["call-1"]
    assert entry["state"] == UNKNOWN
    assert entry["terminal_reason"] == "failure_changed_state"


def test_failure_with_only_post_database_change_becomes_unknown():
    coordinator, adapter, ledger, _ = _coordinator(
        _Adapter(mode="failure_after_database_change"),
    )

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "write_outcome_unknown"
    assert adapter.executions == 1
    entry = ledger["entries"]["call-1"]
    assert entry["state"] == UNKNOWN
    assert entry["terminal_reason"] == "context_changed_during_write"


def test_writer_fence_change_during_execution_becomes_unknown():
    coordinator, adapter, ledger, _ = _coordinator(
        _Adapter(mode="fence_changes_during_write"),
    )

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "write_outcome_unknown"
    assert adapter.executions == 1
    assert ledger["entries"]["call-1"]["state"] == UNKNOWN
    assert ledger["entries"]["call-1"]["terminal_reason"] == (
        "context_changed_during_write"
    )


def test_success_without_exact_candidate_revision_advance_becomes_unknown():
    coordinator, adapter, ledger, _ = _coordinator(
        _Adapter(mode="success_without_revision"),
    )

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "write_outcome_unknown"
    assert adapter.executions == 1
    entry = ledger["entries"]["call-1"]
    assert entry["state"] == UNKNOWN
    assert entry["terminal_reason"] == "success_revision_mismatch"
    assert entry["post_candidate_revision"] == 7


def test_success_may_legitimately_advance_stage_and_change_manifest():
    coordinator, adapter, ledger, _ = _coordinator(
        _Adapter(mode="success_with_stage_rollback"),
    )

    result = _execute(coordinator)
    replay = _execute(coordinator)

    assert replay == result
    assert adapter.executions == 1
    entry = ledger["entries"]["call-1"]
    assert entry["state"] == SETTLED_SUCCESS
    assert entry["post_candidate_revision"] == 8
    assert entry["post_stage_revision"] == 4
    assert entry["post_tool_manifest_hash"] == HASH_C


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_id", "document-2"),
        ("writer_fence", "fence-2"),
        ("database_fingerprint", HASH_C),
    ],
)
def test_exact_replay_rejects_old_document_fence_or_database(field, value):
    coordinator, adapter, _ledger, _ = _coordinator()
    _execute(coordinator)
    setattr(adapter, field, value)

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "stale_context"
    assert adapter.executions == 1


@pytest.mark.parametrize("changed", ["candidate", "stage", "manifest"])
def test_exact_replay_requires_recorded_post_checkpoint(changed):
    coordinator, adapter, _ledger, _ = _coordinator()
    _execute(coordinator)
    if changed == "candidate":
        adapter.candidate_revision += 1
        adapter.value += 1
    elif changed == "stage":
        adapter.stage_revision += 1
        adapter.workflow_phase = "numeric_model"
    else:
        adapter.tool_manifest_hash = HASH_C

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "stale_context"
    assert adapter.executions == 1


def test_real_staged_patch_records_legal_stage_rollback(real_staged):
    _advance_real_session_to_numeric(real_staged)
    assert real_staged.workflow_stage == "numeric_model"
    before_revision = real_staged.revision
    before_stage_revision = real_staged.stage_state["stage_revision"]

    manifest_hash = lambda: fingerprint(
        tools.definitions_for_session(real_staged),
    )
    authority = SessionWriteAuthority(
        real_staged, writer_fence="fence-real-1",
    )
    adapter = SessionCandidatePatchAdapter(
        real_staged,
        tool_manifest_hash=manifest_hash,
        authority=authority,
    )
    context = adapter.current_context()
    assert context["document_id"] == real_staged.document["board_id"]
    ledger = create_write_ledger("conversation-real")
    persisted = []
    coordinator = NativeWriteCoordinator(
        adapter,
        binding={
            "conversation_id": "conversation-real",
            "turn_id": "turn-real",
            "step_id": "step-real",
            "epoch_id": "epoch-real",
            "document_id": context["document_id"],
            "runtime_generation": "dsh-v2",
            "database_fingerprint": context["database_fingerprint"],
            "tool_manifest_hash": context["tool_manifest_hash"],
            "writer_fence": context["writer_fence"],
        },
        ledger=ledger,
        persist=lambda value: persisted.append(copy.deepcopy(value)),
    )
    arguments_json = json.dumps({
        "expected_revision": before_revision,
        "reason": "增加待绑定参数槽并回退事件阶段继续完善",
        "operations": [{
            "op": "add",
            "path": "/extensions/event_topology/nodes/0/parameter_slots/-",
            "value": {
                "slot_id": "slot.ledger.pending",
                "semantic": "mechanism.parameter",
                "value_type": "number",
                "required_for": "calculation",
                "status": "pending",
                "value": None,
                "unit": None,
                "source_ids": [],
                "note": "事务账结构补丁夹具",
            },
        }],
    }, ensure_ascii=False, separators=(",", ":"))

    result = coordinator.execute_patch(
        call_id="call-real-1",
        arguments_json=arguments_json,
        canonical_args_hash=raw_json_hash(arguments_json),
        candidate_revision=before_revision,
        stage_revision=before_stage_revision,
    )
    replay = coordinator.execute_patch(
        call_id="call-real-1",
        arguments_json=arguments_json,
        canonical_args_hash=raw_json_hash(arguments_json),
        candidate_revision=before_revision,
        stage_revision=before_stage_revision,
    )

    assert replay == result
    assert real_staged.revision == before_revision + 1
    assert real_staged.workflow_stage == "event_topology"
    assert real_staged.stage_state["stage_revision"] == before_stage_revision + 1
    entry = ledger["entries"]["call-real-1"]
    assert entry["state"] == SETTLED_SUCCESS
    assert entry["post_candidate_revision"] == before_revision + 1
    assert entry["post_stage_revision"] == before_stage_revision + 1
    assert entry["post_tool_manifest_hash"] == manifest_hash()
    assert entry["post_tool_manifest_hash"] != context["tool_manifest_hash"]
    assert len(persisted) == 2


def test_real_staged_rejected_patch_does_not_advance_revisions(real_staged):
    _advance_real_session_to_numeric(real_staged)
    before_revision = real_staged.revision
    before_stage_revision = real_staged.stage_state["stage_revision"]
    manifest_hash = lambda: fingerprint(
        tools.definitions_for_session(real_staged),
    )
    authority = SessionWriteAuthority(
        real_staged, writer_fence="fence-real-2",
    )
    adapter = SessionCandidatePatchAdapter(
        real_staged,
        tool_manifest_hash=manifest_hash,
        authority=authority,
    )
    context = adapter.current_context()
    assert context["document_id"] == real_staged.document["board_id"]
    ledger = create_write_ledger("conversation-real-error")
    coordinator = NativeWriteCoordinator(
        adapter,
        binding={
            "conversation_id": "conversation-real-error",
            "turn_id": "turn-real-error",
            "step_id": "step-real-error",
            "epoch_id": "epoch-real-error",
            "document_id": context["document_id"],
            "runtime_generation": "dsh-v2",
            "database_fingerprint": context["database_fingerprint"],
            "tool_manifest_hash": context["tool_manifest_hash"],
            "writer_fence": context["writer_fence"],
        },
        ledger=ledger,
        persist=lambda _value: None,
    )
    arguments_json = json.dumps({
        "expected_revision": before_revision,
        "reason": "数值阶段越权修改动作结构应被拒绝",
        "operations": [{
            "op": "replace",
            "path": "/rotation_model/actions/0/start",
            "value": 1.25,
        }],
    }, ensure_ascii=False, separators=(",", ":"))

    result = coordinator.execute_patch(
        call_id="call-real-error-1",
        arguments_json=arguments_json,
        canonical_args_hash=raw_json_hash(arguments_json),
        candidate_revision=before_revision,
        stage_revision=before_stage_revision,
    )

    assert result["code"] == 1
    assert result["reason"] == "agent_stage_patch_forbidden"
    assert real_staged.revision == before_revision
    assert real_staged.stage_state["stage_revision"] == before_stage_revision
    entry = ledger["entries"]["call-real-error-1"]
    assert entry["state"] == SETTLED_FAILURE
    assert entry["post_candidate_revision"] == before_revision
    assert entry["post_stage_revision"] == before_stage_revision
    assert entry["post_tool_manifest_hash"] == context["tool_manifest_hash"]


def test_writer_transfer_and_database_update_wait_for_terminal_persist(real_staged):
    """The old commit window is covered by all three authority locks."""
    _advance_real_session_to_numeric(real_staged)
    before_revision = real_staged.revision
    before_stage_revision = real_staged.stage_state["stage_revision"]
    manifest_hash = lambda: fingerprint(tools.definitions_for_session(real_staged))
    authority = SessionWriteAuthority(
        real_staged, writer_fence="fence-atomic-old",
    )
    adapter = SessionCandidatePatchAdapter(
        real_staged, tool_manifest_hash=manifest_hash, authority=authority,
    )
    context = adapter.current_context()
    ledger = create_write_ledger("conversation-atomic")
    settlement_entered = threading.Event()
    allow_settlement = threading.Event()
    persist_count = 0

    def persist(_value):
        nonlocal persist_count
        persist_count += 1
        if persist_count == 2:
            # The authoritative tool has already saved the candidate, while
            # the terminal ledger publication is still in progress.
            assert real_staged.revision == before_revision + 1
            settlement_entered.set()
            assert allow_settlement.wait(5)

    coordinator = NativeWriteCoordinator(
        adapter,
        binding={
            "conversation_id": "conversation-atomic",
            "turn_id": "turn-atomic",
            "step_id": "step-atomic",
            "epoch_id": "epoch-atomic",
            "document_id": context["document_id"],
            "runtime_generation": "dsh-v2",
            "database_fingerprint": context["database_fingerprint"],
            "tool_manifest_hash": context["tool_manifest_hash"],
            "writer_fence": context["writer_fence"],
        },
        ledger=ledger,
        persist=persist,
    )
    arguments_json = json.dumps({
        "expected_revision": before_revision,
        "reason": "验证原子结算窗口",
        "operations": [{
            "op": "add",
            "path": "/extensions/event_topology/nodes/0/parameter_slots/-",
            "value": {
                "slot_id": "slot.atomic.pending",
                "semantic": "mechanism.parameter",
                "value_type": "number",
                "required_for": "calculation",
                "status": "pending",
                "value": None,
                "unit": None,
                "source_ids": [],
                "note": "原子边界夹具",
            },
        }],
    }, ensure_ascii=False, separators=(",", ":"))
    write_result = []
    failures = []

    def write_call():
        try:
            write_result.append(coordinator.execute_patch(
                call_id="call-atomic",
                arguments_json=arguments_json,
                canonical_args_hash=raw_json_hash(arguments_json),
                candidate_revision=before_revision,
                stage_revision=before_stage_revision,
            ))
        except (PlanError, OSError, RuntimeError, AssertionError) as exc:
            failures.append(exc)

    writer = threading.Thread(target=write_call)
    writer.start()
    assert settlement_entered.wait(5)

    transfer_attempted = threading.Event()
    transfer_done = threading.Event()
    database_attempted = threading.Event()
    database_acquired = threading.Event()

    def transfer():
        transfer_attempted.set()
        try:
            authority.transfer(
                expected_fence="fence-atomic-old",
                new_fence="fence-atomic-new",
            )
        except (PlanError, OSError, RuntimeError, AssertionError) as exc:
            failures.append(exc)
        finally:
            transfer_done.set()

    def update_database():
        database_attempted.set()
        try:
            with database_lock(real_staged.db_path):
                database_acquired.set()
        except (PlanError, OSError, RuntimeError, AssertionError) as exc:
            failures.append(exc)

    transfer_thread = threading.Thread(target=transfer)
    database_thread = threading.Thread(target=update_database)
    transfer_thread.start()
    database_thread.start()
    assert transfer_attempted.wait(2) and database_attempted.wait(2)
    assert not transfer_done.wait(0.15)
    assert not database_acquired.wait(0.15)

    allow_settlement.set()
    writer.join(5)
    transfer_thread.join(5)
    database_thread.join(5)
    assert not writer.is_alive() and not transfer_thread.is_alive()
    assert not database_thread.is_alive()
    assert failures == []
    assert write_result and write_result[0]["code"] == 0
    assert ledger["entries"]["call-atomic"]["state"] == SETTLED_SUCCESS
    assert transfer_done.is_set() and database_acquired.is_set()

    with pytest.raises(PlanError) as stale_replay:
        coordinator.execute_patch(
            call_id="call-atomic",
            arguments_json=arguments_json,
            canonical_args_hash=raw_json_hash(arguments_json),
            candidate_revision=before_revision,
            stage_revision=before_stage_revision,
        )
    assert stale_replay.value.reason == "stale_context"


def test_database_change_fails_before_write_admission(real_staged, monkeypatch):
    manifest_hash = lambda: fingerprint(tools.definitions_for_session(real_staged))
    authority = SessionWriteAuthority(
        real_staged, writer_fence="fence-db-old",
    )
    adapter = SessionCandidatePatchAdapter(
        real_staged, tool_manifest_hash=manifest_hash, authority=authority,
    )
    context = adapter.current_context()
    ledger = create_write_ledger("conversation-db-change")
    coordinator = NativeWriteCoordinator(
        adapter,
        binding={
            "conversation_id": "conversation-db-change",
            "turn_id": "turn-db-change",
            "step_id": "step-db-change",
            "epoch_id": "epoch-db-change",
            "document_id": context["document_id"],
            "runtime_generation": "dsh-v2",
            "database_fingerprint": context["database_fingerprint"],
            "tool_manifest_hash": context["tool_manifest_hash"],
            "writer_fence": context["writer_fence"],
        },
        ledger=ledger,
        persist=lambda _value: None,
    )
    monkeypatch.setattr(
        queries,
        "get_meta",
        lambda _: {"state": "replaced", "commit": "b" * 40},
    )
    raw = json.dumps({
        "expected_revision": real_staged.revision,
        "reason": "数据库变化后不得执行",
        "operations": [],
    }, ensure_ascii=False, separators=(",", ":"))

    with pytest.raises(PlanError) as stale:
        coordinator.execute_patch(
            call_id="call-db-change",
            arguments_json=raw,
            canonical_args_hash=raw_json_hash(raw),
            candidate_revision=real_staged.revision,
            stage_revision=real_staged.stage_state["stage_revision"],
        )
    assert stale.value.reason == "stale_context"
    assert ledger["entries"] == {}


def test_unpublished_candidate_change_fails_before_write_admission(real_staged):
    authority = SessionWriteAuthority(
        real_staged, writer_fence="fence-checkpoint",
    )
    manifest_hash = lambda: fingerprint(tools.definitions_for_session(real_staged))
    adapter = SessionCandidatePatchAdapter(
        real_staged, tool_manifest_hash=manifest_hash, authority=authority,
    )
    context = adapter.current_context()
    ledger = create_write_ledger("conversation-checkpoint")
    coordinator = NativeWriteCoordinator(
        adapter,
        binding={
            "conversation_id": "conversation-checkpoint",
            "turn_id": "turn-checkpoint",
            "step_id": "step-checkpoint",
            "epoch_id": "epoch-checkpoint",
            "document_id": context["document_id"],
            "runtime_generation": "dsh-v2",
            "database_fingerprint": context["database_fingerprint"],
            "tool_manifest_hash": context["tool_manifest_hash"],
            "writer_fence": context["writer_fence"],
        },
        ledger=ledger,
        persist=lambda _value: None,
    )
    # Simulate a competing path that mutated memory but did not publish the
    # CandidateSession checkpoint under the document authority boundary.
    real_staged.candidate["revision"] += 1
    raw = json.dumps({
        "expected_revision": real_staged.revision,
        "reason": "未持久化内存候选不得继续写",
        "operations": [],
    }, ensure_ascii=False, separators=(",", ":"))

    with pytest.raises(PlanError) as stale:
        coordinator.execute_patch(
            call_id="call-checkpoint",
            arguments_json=raw,
            canonical_args_hash=raw_json_hash(raw),
            candidate_revision=real_staged.revision,
            stage_revision=real_staged.stage_state["stage_revision"],
        )
    assert stale.value.reason == "stale_context"
    assert ledger["entries"] == {}


def test_restart_restores_old_fence_then_cas_transfers_to_new_owner(real_staged):
    old = SessionWriteAuthority(
        real_staged, writer_fence="fence-before-restart",
    )
    restored_session = real_staged.__class__.load(
        real_staged.session_id,
        real_staged.root,
        real_staged.db_path,
    )

    # A second App cannot invent a replacement fence while the persisted
    # conversation still owns the old one.
    with pytest.raises(PlanError) as competing:
        SessionWriteAuthority(
            restored_session, writer_fence="fence-invented-by-second-app",
        )
    assert competing.value.reason == "stale_context"

    # Phase 5 restores this exact old fence from the durable conversation.
    resumed = SessionWriteAuthority(
        restored_session, writer_fence="fence-before-restart",
    )
    replacement = resumed.transfer(
        expected_fence="fence-before-restart",
        new_fence="fence-after-restart",
    )
    assert replacement["generation"] == 1
    assert replacement["writer_fence"] == "fence-after-restart"

    with pytest.raises(PlanError) as stale_old_process:
        old.current_fence()
    assert stale_old_process.value.reason == "stale_context"
    recovered_new_owner = SessionWriteAuthority(
        restored_session, writer_fence="fence-after-restart",
    )
    assert recovered_new_owner.current_fence() == "fence-after-restart"


def test_persisting_inflight_failure_prevents_domain_execution():
    calls = 0

    def fail_before_execute(_value):
        nonlocal calls
        calls += 1
        raise OSError("disk full")

    coordinator, adapter, ledger, _ = _coordinator(persist=fail_before_execute)

    with pytest.raises(OSError):
        _execute(coordinator)

    assert calls == 1
    assert adapter.executions == 0
    assert ledger["entries"]["call-1"]["state"] == "inflight"
    with pytest.raises(PlanError) as retry:
        _execute(coordinator)
    assert retry.value.reason == "write_call_inflight"
    assert adapter.executions == 0


def test_inflight_write_then_raise_remains_admitted_and_never_executes():
    durable = {}

    def write_then_raise(value):
        durable.clear()
        durable.update(copy.deepcopy(value))
        raise OSError("fsync acknowledgement lost")

    coordinator, adapter, ledger, _ = _coordinator(persist=write_then_raise)

    with pytest.raises(OSError):
        _execute(coordinator)

    assert adapter.executions == 0
    assert ledger["entries"]["call-1"]["state"] == "inflight"
    assert durable["entries"]["call-1"]["state"] == "inflight"
    with pytest.raises(PlanError) as retry:
        _execute(coordinator)
    assert retry.value.reason == "write_call_inflight"
    assert adapter.executions == 0


def test_settlement_persist_failure_is_converted_to_unknown_without_retry():
    calls = 0
    durable = {}

    def fail_second_write(value):
        nonlocal calls
        calls += 1
        durable.clear()
        durable.update(copy.deepcopy(value))
        if calls == 2:
            raise OSError("settlement fsync failed")

    coordinator, adapter, ledger, _ = _coordinator(persist=fail_second_write)

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "write_outcome_unknown"
    assert adapter.executions == 1
    assert ledger["entries"]["call-1"]["state"] == UNKNOWN
    assert durable["entries"]["call-1"]["state"] == UNKNOWN
    with pytest.raises(PlanError) as retry:
        _execute(coordinator)
    assert retry.value.reason == "write_outcome_unknown"
    assert adapter.executions == 1


def test_persistent_settlement_failure_keeps_local_unknown_and_never_retries():
    calls = 0

    def fail_after_admission(_value):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("storage remains unavailable")

    coordinator, adapter, ledger, _ = _coordinator(persist=fail_after_admission)

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "write_unknown_unpersisted"
    assert calls == 3
    assert adapter.executions == 1
    assert ledger["entries"]["call-1"]["state"] == UNKNOWN
    with pytest.raises(PlanError) as retry:
        _execute(coordinator)
    assert retry.value.reason == "write_outcome_unknown"
    assert calls == 3
    assert adapter.executions == 1


def test_success_written_then_unknown_persist_failure_never_claims_durable_unknown():
    calls = 0
    durable = {}

    def success_ack_lost_then_unknown_rejected(value):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("unknown settlement was not written")
        durable.clear()
        durable.update(copy.deepcopy(value))
        if calls == 2:
            raise OSError("success settlement acknowledgement lost")

    coordinator, adapter, ledger, _ = _coordinator(
        persist=success_ack_lost_then_unknown_rejected,
    )

    with pytest.raises(PlanError) as error:
        _execute(coordinator)

    assert error.value.reason == "write_unknown_unpersisted"
    assert calls == 3
    assert adapter.executions == 1
    # The live process is poisoned against replay, while a restart must trust
    # the last record that was actually confirmed on disk.
    assert ledger["entries"]["call-1"]["state"] == UNKNOWN
    assert durable["entries"]["call-1"]["state"] == SETTLED_SUCCESS
    with pytest.raises(PlanError) as retry:
        _execute(coordinator)
    assert retry.value.reason == "write_outcome_unknown"
    assert adapter.executions == 1


def test_recovery_marks_persisted_inflight_unknown_and_never_reexecutes():
    ledger = create_write_ledger("conversation-1")
    identity = _identity()
    begin_write(ledger, identity=identity, before_fingerprint=HASH_A)
    saved = []

    changed = recover_interrupted_writes(
        ledger,
        observed_document_id="document-1",
        observed_fingerprint=HASH_B,
        observed_candidate_revision=7,
        observed_stage_revision=3,
        observed_tool_manifest_hash=HASH_B,
        persist=lambda value: saved.append(value),
    )

    assert len(changed) == 1
    assert changed[0]["state"] == UNKNOWN
    assert saved[0]["entries"]["call-1"]["state"] == UNKNOWN
    assert lookup_write(ledger, identity=identity)["state"] == UNKNOWN


def test_recovery_rejects_inflight_from_another_document():
    ledger = create_write_ledger("conversation-1")
    begin_write(ledger, identity=_identity(), before_fingerprint=HASH_A)
    saved = []

    with pytest.raises(PlanError) as error:
        recover_interrupted_writes(
            ledger,
            observed_document_id="document-2",
            observed_fingerprint=HASH_B,
            observed_candidate_revision=7,
            observed_stage_revision=3,
            observed_tool_manifest_hash=HASH_B,
            persist=lambda value: saved.append(value),
        )

    assert error.value.reason == "stale_context"
    assert ledger["entries"]["call-1"]["state"] == "inflight"
    assert saved == []


def test_recovery_write_then_raise_fixes_first_unknown_for_retry():
    ledger = create_write_ledger("conversation-1")
    begin_write(ledger, identity=_identity(), before_fingerprint=HASH_A)
    first_durable = {}

    def write_then_raise(value):
        first_durable.clear()
        first_durable.update(copy.deepcopy(value))
        raise OSError("unknown settlement acknowledgement lost")

    with pytest.raises(OSError):
        recover_interrupted_writes(
            ledger,
            observed_document_id="document-1",
            observed_fingerprint=HASH_B,
            observed_candidate_revision=7,
            observed_stage_revision=3,
            observed_tool_manifest_hash=HASH_B,
            persist=write_then_raise,
        )

    fixed = copy.deepcopy(ledger["entries"]["call-1"])
    assert fixed["state"] == UNKNOWN
    assert first_durable["entries"]["call-1"] == fixed
    retried = []
    changed = recover_interrupted_writes(
        ledger,
        observed_document_id="document-1",
        observed_fingerprint=HASH_C,
        observed_candidate_revision=99,
        observed_stage_revision=99,
        observed_tool_manifest_hash=HASH_C,
        persist=lambda value: retried.append(copy.deepcopy(value)),
    )

    assert changed == []
    assert ledger["entries"]["call-1"] == fixed
    assert retried[0]["entries"]["call-1"] == fixed


def test_old_writer_fence_and_stale_context_fail_before_admission():
    adapter = _Adapter()
    coordinator, adapter, ledger, _ = _coordinator(adapter)
    adapter.writer_fence = "fence-2"

    with pytest.raises(PlanError) as fence_error:
        _execute(coordinator)
    assert fence_error.value.reason == "stale_context"
    assert ledger["entries"] == {}
    assert adapter.executions == 0

    adapter.writer_fence = "fence-1"
    adapter.database_fingerprint = HASH_C
    with pytest.raises(PlanError) as db_error:
        _execute(coordinator)
    assert db_error.value.reason == "stale_context"
    assert ledger["entries"] == {}


def test_only_one_distinct_write_may_be_inflight():
    ledger = create_write_ledger("conversation-1")
    begin_write(ledger, identity=_identity(call_id="call-1"), before_fingerprint=HASH_A)

    with pytest.raises(PlanError) as error:
        begin_write(
            ledger,
            identity=_identity(call_id="call-2"),
            before_fingerprint=HASH_A,
        )

    assert error.value.reason == "native_write_concurrency"
    assert set(ledger["entries"]) == {"call-1"}


def test_normalizer_rejects_multiple_persisted_inflight_records():
    ledger = create_write_ledger("conversation-1")
    begin_write(ledger, identity=_identity(call_id="call-1"), before_fingerprint=HASH_A)
    second = copy.deepcopy(ledger["entries"]["call-1"])
    second["identity"]["call_id"] = "call-2"
    ledger["entries"]["call-2"] = second

    with pytest.raises(PlanError) as error:
        normalize_write_ledger(ledger)

    assert error.value.reason == "native_write_ledger_invalid"


def test_terminal_settlement_is_immutable():
    ledger = create_write_ledger("conversation-1")
    identity = _identity()
    begin_write(ledger, identity=identity, before_fingerprint=HASH_A)
    settled = settle_write(
        ledger,
        identity=identity,
        state=SETTLED_SUCCESS,
        after_fingerprint=HASH_B,
        post_candidate_revision=8,
        post_stage_revision=3,
        post_tool_manifest_hash=HASH_B,
        result={"code": 0},
        result_code=0,
    )
    replay = settle_write(
        ledger,
        identity=identity,
        state=SETTLED_SUCCESS,
        after_fingerprint=HASH_B,
        post_candidate_revision=8,
        post_stage_revision=3,
        post_tool_manifest_hash=HASH_B,
        result={"code": 0},
        result_code=0,
    )
    assert settled["disposition"] == "settled"
    assert replay["disposition"] == "replay"

    with pytest.raises(PlanError) as conflict:
        settle_write(
            ledger,
            identity=identity,
            state=SETTLED_SUCCESS,
            after_fingerprint=HASH_C,
            post_candidate_revision=8,
            post_stage_revision=3,
            post_tool_manifest_hash=HASH_B,
            result={"code": 0, "different": True},
            result_code=0,
        )
    assert conflict.value.reason == "settlement_conflict"


def test_normalizer_rejects_disk_forged_success_with_unchanged_domain():
    coordinator, _adapter, ledger, _ = _coordinator()
    _execute(coordinator)
    forged = copy.deepcopy(ledger)
    entry = forged["entries"]["call-1"]
    entry["after_fingerprint"] = entry["before_fingerprint"]

    with pytest.raises(PlanError) as error:
        normalize_write_ledger(forged)

    assert error.value.reason == "native_write_ledger_invalid"


def test_normalizer_rejects_disk_forged_entry_document_identity():
    coordinator, _adapter, ledger, _ = _coordinator()
    _execute(coordinator)
    forged = copy.deepcopy(ledger)
    forged["entries"]["call-1"]["document_id"] = "document-other"

    with pytest.raises(PlanError) as error:
        normalize_write_ledger(forged)

    assert error.value.reason == "native_write_ledger_invalid"


@pytest.mark.parametrize("tamper", ["result_code", "result_json"])
def test_normalizer_rejects_disk_forged_success_result_link(tamper):
    coordinator, _adapter, ledger, _ = _coordinator()
    _execute(coordinator)
    forged = copy.deepcopy(ledger)
    entry = forged["entries"]["call-1"]
    if tamper == "result_code":
        entry["result_code"] = 1
    else:
        value = json.loads(entry["result_json"])
        value["code"] = 1
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        entry["result_json"] = encoded.decode("utf-8")
        entry["result_hash"] = hashlib.sha256(encoded).hexdigest()

    with pytest.raises(PlanError) as error:
        normalize_write_ledger(forged)

    assert error.value.reason == "native_write_ledger_invalid"


def test_normalizer_rejects_disk_forged_failure_reason_link():
    coordinator, _adapter, ledger, _ = _coordinator(_Adapter(mode="failure"))
    _execute(coordinator)
    forged = copy.deepcopy(ledger)
    entry = forged["entries"]["call-1"]
    entry["result_code"] = "different_failure"
    entry["terminal_reason"] = "different_failure"

    with pytest.raises(PlanError) as error:
        normalize_write_ledger(forged)

    assert error.value.reason == "native_write_ledger_invalid"


def test_normalizer_rejects_disk_forged_unknown_result_code():
    coordinator, _adapter, ledger, _ = _coordinator(
        _Adapter(mode="failure_after_mutation"),
    )
    with pytest.raises(PlanError):
        _execute(coordinator)
    forged = copy.deepcopy(ledger)
    forged["entries"]["call-1"]["result_code"] = "tool_failure"

    with pytest.raises(PlanError) as error:
        normalize_write_ledger(forged)

    assert error.value.reason == "native_write_ledger_invalid"


def test_settlement_rejects_negative_zero_and_string_zero_result_codes():
    ledger = create_write_ledger("conversation-1")
    identity = _identity()
    begin_write(ledger, identity=identity, before_fingerprint=HASH_A)

    with pytest.raises(PlanError) as negative_zero:
        settle_write(
            ledger,
            identity=identity,
            state=SETTLED_FAILURE,
            after_fingerprint=HASH_A,
            post_candidate_revision=7,
            post_stage_revision=3,
            post_tool_manifest_hash=HASH_B,
            result={"code": 1, "value": -0.0},
            result_code=1,
            terminal_reason="1",
        )
    assert negative_zero.value.reason == "native_write_ledger_invalid"

    with pytest.raises(PlanError) as string_zero:
        settle_write(
            ledger,
            identity=identity,
            state=SETTLED_FAILURE,
            after_fingerprint=HASH_A,
            post_candidate_revision=7,
            post_stage_revision=3,
            post_tool_manifest_hash=HASH_B,
            result={"code": "0"},
            result_code="0",
            terminal_reason="0",
        )
    assert string_zero.value.reason == "native_write_ledger_invalid"


def test_real_session_adapter_uses_authoritative_tool_gate(monkeypatch, real_staged):
    session = real_staged
    called = []
    monkeypatch.setattr(
        tools,
        "definitions_for_session",
        lambda _session: [{"name": "gs_patch_candidate"}],
    )

    def call_tool(actual_session, name, arguments):
        called.append((actual_session, name, arguments))
        actual_session.candidate["revision"] += 1
        actual_session.revision += 1
        actual_session.save()
        return {"code": 0, "data": {"revision": actual_session.revision}}

    monkeypatch.setattr(tools, "call_tool", call_tool)
    authority = SessionWriteAuthority(session, writer_fence="fence-1")
    adapter = SessionCandidatePatchAdapter(
        session,
        tool_manifest_hash=lambda: HASH_B,
        authority=authority,
    )

    before = adapter.current_context()
    response = adapter.execute(
        "gs_patch_candidate", {"expected_revision": session.revision},
    )
    after = adapter.current_context()

    assert response["code"] == 0
    assert before["document_id"] == after["document_id"] == session.document["board_id"]
    assert called[0][0:2] == (session, "gs_patch_candidate")
    assert before["domain_fingerprint"] != after["domain_fingerprint"]
    assert before["database_fingerprint"] == fingerprint(session.database_snapshot)


def test_session_adapter_requires_or_derives_stable_document_identity(real_staged):
    session = real_staged
    authority = SessionWriteAuthority(session, writer_fence="fence-1")
    adapter = SessionCandidatePatchAdapter(
        session,
        tool_manifest_hash=lambda: HASH_B,
        authority=authority,
    )
    assert adapter.current_context()["document_id"] == session.document["board_id"]
    session.document["board_id"] = "board-replaced"
    with pytest.raises(PlanError) as disappeared:
        adapter.current_context()
    assert disappeared.value.reason == "stale_context"

    other = real_staged.__class__.load(
        real_staged.session_id, real_staged.root, real_staged.db_path,
    )
    with pytest.raises(PlanError) as mismatch:
        SessionWriteAuthority(
            other,
            writer_fence="fence-other",
            document_id="board-other",
        )
    assert mismatch.value.reason == "stale_context"
