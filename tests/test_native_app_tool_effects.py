from __future__ import annotations

from genshin_app.agent_chat.native_app_tool_effects import (
    STAGE_TRANSITION_FIELDS,
    allowed_changes_for_transition,
    stage_transition_proof,
)


def state(stage: str, revision: int, manifest: str) -> dict:
    return {
        "workflow_stage": stage,
        "stage_revision": revision,
        "tool_manifest_hash": manifest * 64,
    }


def test_explicit_stage_transition_is_a_narrow_conditional_effect() -> None:
    before = state("loadout_draft", 4, "a")
    after = state("action_checkpoint", 5, "b")
    proof = stage_transition_proof("stage_transition", before, after)
    assert proof == {
        "contract": "genshin-native-stage-transition-v1",
        "kind": "explicit_forward",
        "from_stage": "loadout_draft",
        "to_stage": "action_checkpoint",
        "pre_stage_revision": 4,
        "post_stage_revision": 5,
        "pre_tool_manifest_hash": "a" * 64,
        "post_tool_manifest_hash": "b" * 64,
    }
    assert STAGE_TRANSITION_FIELDS <= allowed_changes_for_transition(
        "stage_transition", proof,
    )


def test_candidate_auto_rollback_requires_one_step_backward_proof() -> None:
    before = state("numeric_model", 9, "a")
    after = state("event_topology", 10, "b")
    proof = stage_transition_proof("candidate", before, after)
    assert proof is not None
    assert proof["kind"] == "automatic_invalidation"

    skipped = state("action_checkpoint", 10, "b")
    assert stage_transition_proof("candidate", before, skipped)["kind"] == (
        "automatic_invalidation"
    )
    assert stage_transition_proof(
        "candidate", before, state("event_topology", 11, "b"),
    ) is None


def test_manifest_drift_without_stage_change_is_not_authorized() -> None:
    before = state("event_topology", 7, "a")
    after = state("event_topology", 7, "b")
    assert stage_transition_proof("candidate", before, after) is None
    assert "tool_manifest_hash" not in allowed_changes_for_transition(
        "candidate", None,
    )
