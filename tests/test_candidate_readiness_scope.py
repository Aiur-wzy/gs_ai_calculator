from genshin_app.agent_chat.candidate_readiness import readiness_projection
from genshin_app.agent_chat.harness_v3 import derive_candidate_state


def test_ready_whiteboard_draft_remains_reviewable_without_numeric_acceptance():
    summary = {"application_kind": "whiteboard_draft", "kind": "whiteboard", "ready": True,
               "has_calculation": False, "missing_inputs_total": 3, "database_current": True}
    scope = readiness_projection(summary)
    assert scope == {"application_kind": "whiteboard_draft", "representation": "whiteboard",
                     "ready_scope": "draft_sync", "assessment": "draft"}
    actual = derive_candidate_state(checkpoint={"candidate_revision": 2, "receipt_fingerprint": "a" * 64},
                                    summary=summary, review_authority_verified=True)
    assert actual["reviewable"] is True
    assert actual["assessment"] == "draft"
    assert actual["ready_scope"] == "draft_sync"


def test_seal_is_not_calculation_acceptance_and_scope_is_not_a_new_admission_gate():
    actual = derive_candidate_state(checkpoint={"candidate_revision": 2, "receipt_fingerprint": "a" * 64},
                                    summary={}, review_authority_verified=True)
    assert actual["reviewable"] is True  # Existing exact review authority is unchanged.
    assert actual["assessment"] == "unknown"
    assert actual["ready_scope"] == "none"


def test_rejected_calculation_is_diagnostic_even_with_seal_and_ready_flag():
    actual = readiness_projection({"representation": "native", "ready": True, "has_calculation": True,
                                  "calculation_acceptance": {"status": "rejected"}})
    assert actual["assessment"] == "diagnostic"
    assert actual["ready_scope"] == "none"


def test_whiteboard_calculation_target_is_not_a_whiteboard_input_representation():
    actual = readiness_projection({"application_kind": "whiteboard_calculated", "ready": True,
                                  "has_calculation": True, "calculation_acceptance": {"status": "accepted"}})
    assert actual["representation"] == "native"
    assert actual["ready_scope"] == "formal_result"
