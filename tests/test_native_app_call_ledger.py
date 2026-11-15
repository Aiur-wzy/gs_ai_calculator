from __future__ import annotations

import pytest

from genshin_app.agent_chat import native_app_call_ledger as ledger_module
from genshin_app.agent_chat.native_app_call_ledger import (
    MAX_CALLS_PER_TURN,
    RECOVERY_APPLIED,
    RECOVERY_CONFLICT,
    RECOVERY_NOT_APPLIED,
    SETTLED_FAILURE,
    SETTLED_SUCCESS,
    UNKNOWN,
    NativeAppCallLedger,
    make_app_call_identity,
)
from genshin_app.equipment.schema import PlanError

HASH = "a" * 64


@pytest.mark.parametrize("kind,increment", [("artifact_focus", 0), ("artifact_preparation", 1)])
def test_artifact_fact_settles_without_reintroducing_phase_authority(tmp_path, kind, increment):
    store = ledger(tmp_path)
    current = {**identity(), "tool_name": "gs_transition_phase"}
    transition = {"contract": "genshin-native-stage-transition-v1", "kind": kind,
        "from_stage": "artifact", "to_stage": "artifact", "pre_stage_revision": current["stage_revision"],
        "post_stage_revision": current["stage_revision"] + increment,
        "pre_tool_manifest_hash": current["tool_manifest_hash"], "post_tool_manifest_hash": current["tool_manifest_hash"]}
    store.begin(identity=current, effect="stage_transition", before_state_hash=HASH)
    settled = store.settle(identity=current, effect="stage_transition", state=SETTLED_SUCCESS,
        after_state_hash="b" * 64, result={"code": 0}, stage_transition=transition)
    assert settled["entry"]["stage_transition"] == transition
    assert store.read()["entries"]["call-1"]["state"] == SETTLED_SUCCESS


@pytest.mark.parametrize("change", [{"post_stage_revision": 9}, {"from_stage": "numeric_model"},
    {"post_tool_manifest_hash": "f" * 64}])
def test_artifact_focus_cannot_forge_permission_or_semantic_generation(tmp_path, change):
    store = ledger(tmp_path)
    current = {**identity(), "tool_name": "gs_transition_phase"}
    transition = {"contract": "genshin-native-stage-transition-v1", "kind": "artifact_focus",
        "from_stage": "artifact", "to_stage": "artifact", "pre_stage_revision": 2,
        "post_stage_revision": 2, "pre_tool_manifest_hash": current["tool_manifest_hash"],
        "post_tool_manifest_hash": current["tool_manifest_hash"], **change}
    store.begin(identity=current, effect="stage_transition", before_state_hash=HASH)
    with pytest.raises(PlanError):
        store.settle(identity=current, effect="stage_transition", state=SETTLED_SUCCESS,
            after_state_hash="b" * 64, result={"code": 0}, stage_transition=transition)
    assert store.read()["entries"]["call-1"]["state"] == "inflight"


def identity(call_id="call-1", *, raw_hash=HASH, catalog_hash="d" * 64, turn_id="turn-1"):
    return make_app_call_identity(
        conversation_id="conversation-1",
        turn_id=turn_id,
        step_id="step-1",
        epoch_id="epoch-1",
        document_id="document-1",
        call_id=call_id,
        tool_name="gs_read_context",
        raw_arguments_hash=raw_hash,
        runtime_generation="dsh-v2",
        candidate_revision=1,
        stage_revision=2,
        database_fingerprint="b" * 64,
        tool_manifest_hash="c" * 64,
        catalog_hash=catalog_hash,
        writer_fence="fence-1",
    )


def ledger(tmp_path):
    return NativeAppCallLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=lambda: "2026-10-23T00:00:00+00:00",
    )


def test_exact_terminal_replay_and_call_id_conflict(tmp_path):
    store = ledger(tmp_path)
    current = identity()
    assert store.begin(identity=current, effect="none", before_state_hash=HASH)["disposition"] == "execute"
    settled = store.settle(
        identity=current,
        effect="none",
        state=SETTLED_SUCCESS,
        after_state_hash=HASH,
        result={"code": 0, "data": {"ok": True}},
    )
    assert settled["status"] == "settled"
    replay = store.begin(identity=current, effect="none", before_state_hash=HASH)
    assert replay["disposition"] == "replay"
    assert replay["result"]["data"]["ok"] is True

    with pytest.raises(PlanError) as caught:
        store.begin(
            identity=identity(raw_hash="d" * 64),
            effect="none",
            before_state_hash=HASH,
        )
    assert caught.value.reason == "call_id_conflict"

    with pytest.raises(PlanError) as catalog_conflict:
        store.begin(
            identity=identity(catalog_hash="e" * 64),
            effect="none",
            before_state_hash=HASH,
        )
    assert catalog_conflict.value.reason == "call_id_conflict"


def test_successful_stage_effect_requires_a_reviewed_transition_proof(tmp_path):
    store = ledger(tmp_path)
    current = identity()
    store.begin(
        identity=current,
        effect="stage_transition",
        before_state_hash=HASH,
    )

    with pytest.raises(PlanError, match="成功阶段工具缺少阶段切换证明"):
        store.settle(
            identity=current,
            effect="stage_transition",
            state=SETTLED_SUCCESS,
            after_state_hash="e" * 64,
            result={"code": 0},
        )


def test_crash_recovery_closes_inflight_without_replay(tmp_path):
    store = ledger(tmp_path)
    current = identity()
    store.begin(identity=current, effect="candidate", before_state_hash=HASH)

    assert store.recover_inflight(after_state_hash="e" * 64) == ["call-1"]
    replay = store.begin(identity=current, effect="candidate", before_state_hash=HASH)
    assert replay["disposition"] == "replay"
    assert replay["entry"]["state"] == UNKNOWN
    assert replay["entry"]["after_state_hash"] == "e" * 64
    assert "result" not in replay
    reconciliation = store.classify_recovery(
        call_id="call-1", observed_state_hash="e" * 64,
    )
    assert reconciliation["classification"] == RECOVERY_CONFLICT
    assert reconciliation["reason"] == "interruption_has_no_commit_proof"


def test_generic_recovery_classifies_only_proven_terminal_boundaries(tmp_path):
    store = ledger(tmp_path)
    applied = identity("call-applied")
    store.begin(identity=applied, effect="candidate", before_state_hash=HASH)
    store.settle(
        identity=applied,
        effect="candidate",
        state=UNKNOWN,
        after_state_hash="e" * 64,
        terminal_reason="effect_mask_violation",
    )
    assert store.classify_recovery(
        call_id="call-applied", observed_state_hash="e" * 64,
    )["classification"] == RECOVERY_APPLIED

    unchanged = identity("call-unchanged")
    store.begin(identity=unchanged, effect="none", before_state_hash=HASH)
    store.settle(
        identity=unchanged,
        effect="none",
        state=SETTLED_FAILURE,
        after_state_hash=HASH,
        result={"code": "tool_failure"},
        terminal_reason="tool_failure",
    )
    assert store.classify_recovery(
        call_id="call-unchanged", observed_state_hash=HASH,
    )["classification"] == RECOVERY_NOT_APPLIED


def test_distinct_call_transport_capacity_is_enforced(tmp_path, monkeypatch):
    assert MAX_CALLS_PER_TURN == 16_384
    monkeypatch.setattr(ledger_module, "MAX_CALLS_PER_TURN", 8)
    store = ledger(tmp_path)
    for index in range(8):
        current = identity(f"call-{index}")
        store.begin(identity=current, effect="none", before_state_hash=HASH)
        store.settle(
            identity=current,
            effect="none",
            state=SETTLED_SUCCESS,
            after_state_hash=HASH,
            result={"code": 0},
        )
    with pytest.raises(PlanError) as caught:
        store.begin(identity=identity("call-9"), effect="none", before_state_hash=HASH)
    assert caught.value.reason == "native_app_call_budget_exhausted"


def test_second_inflight_call_is_rejected(tmp_path):
    store = ledger(tmp_path)
    store.begin(identity=identity("call-1"), effect="none", before_state_hash=HASH)
    with pytest.raises(PlanError) as caught:
        store.begin(identity=identity("call-2"), effect="none", before_state_hash=HASH)
    assert caught.value.reason == "native_app_call_concurrency"


def test_checkpoint_projection_detects_tamper_and_missing_ledger(tmp_path):
    store = ledger(tmp_path)
    current = identity()
    store.begin(identity=current, effect="none", before_state_hash=HASH)
    store.settle(
        identity=current,
        effect="none",
        state=SETTLED_SUCCESS,
        after_state_hash=HASH,
        result={"code": 0},
    )
    projection = store.checkpoint_projection()
    assert projection["conversation_id"] == "conversation-1"
    assert projection["ledger_revision"] == 2
    assert projection["session_ref"].startswith("appcalls.v1.2.")
    store.verify_checkpoint_projection(projection)
    store.verify_checkpoint_ref(projection["session_ref"])

    changed = dict(projection)
    changed["ledger_hash"] = "f" * 64
    with pytest.raises(PlanError) as tampered:
        store.verify_checkpoint_projection(changed)
    assert tampered.value.reason == "agent_checkpoint_invalid"

    store.path.unlink()
    with pytest.raises(PlanError) as missing:
        store.verify_checkpoint_ref(projection["session_ref"])
    assert missing.value.reason == "stale_context"
