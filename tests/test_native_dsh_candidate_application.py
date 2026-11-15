"""True domain review/application transactions on the new DSH workspace."""
from __future__ import annotations

import copy

import pytest

from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from tests.test_calculation_delivery import delivery_plan
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def ready_registry(tmp_path, db_path, sid="one"):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    registry.prepare_document(sid, "native", delivery_plan())
    scope = registry.activate(sid)
    scope.candidate_session.calculate(0)
    return registry, scope


def command(registry, sid, name, args, projection=None):
    projection = projection or registry.domain_projection(sid)["projection"]
    cid = registry.lookup_binding(sid)["conversation_id"]
    return registry.candidate_command({"conversation_id": cid, "command_id": name,
        "expected_projection_revision": projection["projection_revision"], "arguments": args})


def test_review_prepare_apply_ack_uses_existing_transaction_and_durable_proof(tmp_path, db_path):
    registry, scope = ready_registry(tmp_path, db_path)
    candidate = scope.candidate_session
    baseline = copy.deepcopy(candidate.document)
    receipt = candidate.summary()["receipt_fingerprint"]
    base = {"kind": "native", "expected_revision": 0, "expected_receipt": receipt}
    reviewed = command(registry, "one", "candidate.review", {**base, "document": baseline})
    target = reviewed["result"]["plan"]
    baseline_payload = {"kind": "native", "plan": baseline}
    target_payload = {"kind": "native", "plan": target}
    prepared = command(registry, "one", "candidate.apply.prepare", {**base, "document": target,
        "baseline_payload": baseline_payload, "target_payload": target_payload})
    assert prepared["result"]["status"] == "prepared"
    assert candidate.document == baseline  # Renderer has not applied anything.
    assert not any(item["command_id"] == "candidate.review" for item in prepared["projection"]["available_commands"])
    applied = command(registry, "one", "application.ack", {**base, "document": target,
        "actual_payload": target_payload}, projection=prepared["projection"])
    assert applied["result"]["status"] == "applied"
    proof = registry.application_port.applied_candidate_proof(scope.conversation_id, current_payload=target_payload)
    assert proof["status"] == "applied"
    registry.close()


def test_stale_review_and_changed_renderer_baseline_do_not_apply(tmp_path, db_path):
    registry, scope = ready_registry(tmp_path, db_path)
    projection = registry.domain_projection("one")["projection"]
    summary = scope.candidate_session.summary()
    args = {"kind": "native", "document": copy.deepcopy(scope.candidate_session.document),
            "expected_revision": 0, "expected_receipt": summary["receipt_fingerprint"]}
    args["document"]["manual_template"]["meta"]["name"] += " outside edit"
    with pytest.raises(Exception, match="agent_document_changed"):
        command(registry, "one", "candidate.review", args, projection=projection)
    bad = copy.deepcopy(projection)
    bad["projection_revision"] += 1
    with pytest.raises(NativeDshMuxError, match="dsh_candidate_projection_stale"):
        command(registry, "one", "candidate.review", args, projection=bad)
    assert registry.application_port.candidate_application_intent(scope.conversation_id)["status"] == "none"
    registry.close()


def test_prepared_application_freezes_target_writes_but_not_chat_or_other_candidate(tmp_path, db_path):
    registry, scope = ready_registry(tmp_path, db_path)
    registry.prepare_document("two", "native", delivery_plan())
    other = registry.activate("two")
    summary = scope.candidate_session.summary()
    base = {"kind": "native", "expected_revision": 0, "expected_receipt": summary["receipt_fingerprint"]}
    reviewed = command(registry, "one", "candidate.review", {**base, "document": scope.candidate_session.document})
    target = reviewed["result"]["plan"]
    command(registry, "one", "candidate.apply.prepare", {**base, "document": target,
        "baseline_payload": {"kind": "native", "plan": scope.candidate_session.document},
        "target_payload": {"kind": "native", "plan": target}})
    with (pytest.raises(NativeDshMuxError, match="dsh_candidate_application_pending"),
          registry._tool_boundary(scope, "gs_patch_candidate")):
        pytest.fail("must not write a target that is being applied")
    with registry._tool_boundary(scope, "gs_read_skill"):
        pass
    with registry._tool_boundary(other, "gs_patch_candidate"):
        pass
    registry.close()


def test_review_new_explicitly_creates_real_seed_before_normal_application(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path)
    registry.prepare_document("new", None, None)
    scope = registry.activate("new")
    candidate = scope.candidate_session
    edited = candidate.call_tool("gs_patch_whiteboard", {"expected_revision": 0,
        "reason": "用户从聊天构建新白板", "operations": [
            {"op": "replace", "path": "/title", "value": "已准备的新队伍"}]})
    assert edited["code"] == 0
    receipt = candidate.summary()["receipt_fingerprint"]
    base = {"kind": "whiteboard", "expected_revision": 1, "expected_receipt": receipt}
    reviewed = command(registry, "new", "candidate.review.new", {**base, "document": None})["result"]
    creation = reviewed["document_creation"]
    assert creation["expected_absent"] is True
    assert creation["seed"] == candidate.document
    assert creation["seed"] != reviewed["board"]
    # The Renderer creates an actual unsaved document from this explicit seed
    # only after confirmation. The following transaction receives that real
    # baseline, never a fabricated "currently open" document.
    actual_formal = copy.deepcopy(creation["seed"])
    target = reviewed["board"]
    prepared = command(registry, "new", "candidate.apply.prepare", {**base, "document": target,
        "baseline_payload": {"kind": "whiteboard", "board": actual_formal, "calculated": None},
        "target_payload": {"kind": "whiteboard", "board": target, "calculated": None}})
    actual_formal = copy.deepcopy(target)
    done = command(registry, "new", "application.ack", {**base, "document": actual_formal,
        "actual_payload": {"kind": "whiteboard", "board": actual_formal, "calculated": None}},
        projection=prepared["projection"])
    assert done["result"]["status"] == "applied"
    assert candidate.document == actual_formal
    assert not any(item["command_id"] == "candidate.review.new" for item in done["projection"]["available_commands"])
    registry.close()
    # DSH history recovery and App document recovery are independent. A new
    # process reads the same committed domain artifacts, but never overwrites
    # an unsaved Renderer document without a new explicit user confirmation.
    reopened = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path)
    cid = reopened.lookup_binding("new")["conversation_id"]
    current = {"current_document": creation["seed"],
        "current_payload": {"kind": "whiteboard", "board": creation["seed"], "calculated": None}}
    assert reopened.whiteboard_projection(cid, "query", current)["status"] == "restore_unsaved_target"
    with pytest.raises(Exception, match="confirmation_required"):
        reopened.whiteboard_projection(cid, "recover", {**current, "allow_unsaved_target": False})
    recovered = reopened.whiteboard_projection(cid, "recover", {**current, "allow_unsaved_target": True})
    assert recovered["board"] == actual_formal
    assert recovered["calculated_delivery"] is None
    saved = reopened.saved_whiteboard(cid, "inspect", {})
    assert saved["status"] == "available" and saved["board"] == actual_formal
    assert "delivery_capability" not in saved
    with pytest.raises(NativeDshMuxError, match="request_invalid"):
        reopened.saved_whiteboard(cid, "restore_new", {"expected_absent": False,
            "expected_revision": saved["revision"], "expected_receipt": saved["receipt_fingerprint"]})
    restored = reopened.saved_whiteboard(cid, "restore_new", {"expected_absent": True,
        "expected_revision": saved["revision"], "expected_receipt": saved["receipt_fingerprint"]})
    assert restored["document_creation"] == {"kind": "whiteboard", "seed": actual_formal, "expected_absent": True}
    reopened.close()


def test_existing_editor_document_cannot_claim_new_document_review(tmp_path, db_path):
    registry, scope = ready_registry(tmp_path, db_path)
    base = {"kind": "native", "document": None, "expected_revision": 0,
            "expected_receipt": scope.candidate_session.summary()["receipt_fingerprint"]}
    with pytest.raises(NativeDshMuxError, match="dsh_candidate_command_unavailable"):
        command(registry, "one", "candidate.review.new", base)
    registry.close()
