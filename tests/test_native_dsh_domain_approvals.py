"""User-only D2 decisions survive restart without becoming generic questions."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.native_dsh_domain_approvals import (
    approval_cards,
    decide_approval,
)
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_db import queries
from tests.test_calculation_delivery import delivery_plan
from tests.test_provisional_authority import _draft, _install, _verify
from tests.test_provisional_authority import staged as staged  # noqa: PLC0414


def prepare(session, monkeypatch):
    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {
        "items": [{"id": 9001, "name": "数据库武器"}], "total": 1, "page": 1, "page_size": 100})
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": 9001, "name": "数据库武器", "base_atk": 740.0}})
    _install(session, [_draft("local.weapon.patch", "数据库武器",
        authorization_kind="public_data_patch", values={"weapon": {"base_atk": 741.0}})])
    assert _verify(session, "local.weapon.patch")["code"] == 0
    session.request_provisional_public_patch_approval(provisional_id="local.weapon.patch",
        expected_revision=session.revision, reason="修正经核实的资料差异")
    card = approval_cards(session)[0]
    return {key: card[key] for key in ("question_id", "expected_candidate_revision", "expected_stage_revision")}


def test_user_decision_is_durable_exact_and_replayable_after_restart(staged, monkeypatch):
    request = {**prepare(staged, monkeypatch), "decision": "approve"}
    detail = approval_cards(staged)[0]["question"]["detail"]
    assert '"base_atk":740.0' in detail and '"base_atk":741.0' in detail
    result = decide_approval(staged, request)
    assert result["status"] == "approved"
    assert approval_cards(staged) == []
    approval = staged.candidate["provisional_entities"][0]["approval"]
    assert approval["answer_receipt"] == result["decision_receipt"]
    resumed = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert decide_approval(resumed, request)["status"] == "already_approved"
    with pytest.raises(NativeDshMuxError, match="decision_conflict"):
        decide_approval(resumed, {**request, "decision": "reject"})
    assert resumed.candidate["provisional_entities"][0]["approval"] == approval


def test_stale_and_rejected_user_decisions_do_not_grant_authority(staged, monkeypatch):
    request = {**prepare(staged, monkeypatch), "decision": "reject"}
    with pytest.raises(NativeDshMuxError, match="approval_stale"):
        decide_approval(staged, {**request, "expected_candidate_revision": staged.revision - 1})
    assert decide_approval(staged, request)["status"] == "rejected"
    assert staged.candidate["provisional_entities"][0]["approval"] is None


def test_durable_user_click_can_finish_after_candidate_checkpoint_failure(staged, monkeypatch):
    request = {**prepare(staged, monkeypatch), "decision": "approve"}
    save = staged.save
    monkeypatch.setattr(staged, "save", lambda: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError, match="disk unavailable"):
        decide_approval(staged, request)
    assert staged.candidate["provisional_entities"][0]["approval"] is None
    assert len(list(staged.root.glob("dsh-domain-decision-*.json"))) == 1
    monkeypatch.setattr(staged, "save", save)
    assert decide_approval(staged, request)["status"] == "approved"


def test_artifact_reading_focus_does_not_forbid_native_data_correction(staged, monkeypatch):
    artifact = ArtifactCandidateSession(uuid.uuid4().hex, staged.root.parent / "artifact", staged.db_path,
        "native", copy.deepcopy(delivery_plan()))
    assert artifact.workflow_stage == "loadout_draft"
    request = {**prepare(artifact, monkeypatch), "decision": "approve"}
    assert decide_approval(artifact, request)["status"] == "approved"
    assert artifact.workflow_stage == "loadout_draft"
