from __future__ import annotations

import copy
import sqlite3
import uuid

import pytest

from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.staged_candidate import StagedCandidateSession
from genshin_app.equipment.provisional_entities import CONTRACT, ProvisionalEntityError
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import new_whiteboard
from genshin_db import queries


def _call(session, name, **args):
    return session.call_tool(name, args)


def _transition(session, target):
    return _call(
        session,
        "gs_transition_phase",
        target_phase=target,
        expected_revision=session.revision,
        expected_stage_revision=session.stage_state["stage_revision"],
        reason="工程夹具推进阶段",
    )


def _to_event(session):
    assert _call(
        session,
        "gs_add_character",
        expected_revision=session.revision,
        reason="添加工程角色",
        db_character_id=1,
    )["code"] == 0
    assert _transition(session, "action_checkpoint")["code"] == 0
    action = {
        "id": "action.skill",
        "character": "character-1",
        "action_type_id": "action.elemental_skill",
        "label": "元素战技",
        "start": 1,
        "variant": "tap",
        "notes": "",
    }
    assert _call(
        session,
        "gs_patch_whiteboard",
        expected_revision=session.revision,
        reason="建立动作骨架",
        operations=[{"op": "add", "path": "/actions/-", "value": action}],
    )["code"] == 0
    assert _transition(session, "event_topology")["code"] == 0


@pytest.fixture
def staged(tmp_path, monkeypatch):
    db = tmp_path / "engineering.db"
    db.touch()
    meta = {
        "version_lines": ["release"],
        "datasets": {"equipment": {"lines": {"release": {
            "state": "ready", "schema_version": 1,
            "upstream_commit": "a" * 40,
            "built_at": "2026-10-05T00:00:00Z",
        }}}},
    }
    monkeypatch.setattr(queries, "get_meta", lambda _: copy.deepcopy(meta))
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {
        "character": {
            "id": 1, "name": "工程角色",
            "weapon_type": "WEAPON_SWORD_ONE_HAND",
            "atk_90": 300, "hp_90": 12000, "def_90": 800,
        },
    })
    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {
        "items": [], "total": 0, "page": 1, "page_size": 100,
    })
    session = StagedCandidateSession(
        uuid.uuid4().hex, tmp_path / "session", db,
        "whiteboard", new_whiteboard(),
    )
    _to_event(session)
    return session


def _draft(provisional_id, display_name, *, authorization_kind="database_missing",
           values=None):
    return {
        "contract": CONTRACT,
        "provisional_id": provisional_id,
        "entity_type": "weapon",
        "authorization_kind": authorization_kind,
        "display_name": display_name,
        "version_claim": {"version_line": "release", "game_version": "7.0"},
        "values": copy.deepcopy(values or {
            "base_atk": 741.0, "weapon_type": "WEAPON_SWORD_ONE_HAND",
        }),
        "source_refs": [{
            "kind": "agent_research",
            "reference": "https://example.invalid/reviewed-source",
            "status": "verified",
            "fingerprint": "b" * 64,
            "note": "工程夹具中的已核实来源",
        }],
        "reason": "当前数据库未提供所需公共资料",
        "necessity": "当前方案需要该值",
        "impact": "影响基础面板与后续伤害",
        "risk": "外部来源可能与当前快照存在偏差",
        "alternatives": ["等待数据库更新"],
        "database_lookup": None,
        "public_data_diff": None,
        "scope": "current_plan_only",
        "database_authority": False,
        "payload_fingerprint": None,
        "attestation": None,
        "approval": None,
    }


def _install(session, rows):
    result = _call(
        session,
        "gs_patch_candidate",
        expected_revision=session.revision,
        reason="登记待核对的临时公共资料提案",
        operations=[{
            "op": "add", "path": "/provisional_entities",
            "value": copy.deepcopy(rows),
        }],
    )
    assert result["code"] == 0, result


def _install_internal_consumer_refs(session, refs):
    """Install a synthetic typed-consumer input outside the Agent tool surface.

    The loadout representation choice is intentionally unresolved. This test
    harness exercises the shared trusted resolver/publisher facade without
    pretending that a real character/weapon/set consumer is already wired.
    """

    prepared = copy.deepcopy(session.candidate)
    prepared.setdefault("extensions", {})["d2_consumer_test_refs"] = copy.deepcopy(refs)
    session.replace_candidate(
        prepared, session.revision, "工程夹具安装通用消费者引用",
    )


def _verify(session, provisional_id):
    return _call(
        session,
        "gs_verify_public_data_lookup",
        expected_revision=session.revision,
        provisional_id=provisional_id,
        reason="让App核对固定数据库快照",
    )


def _attest(session, provisional_id):
    return _call(
        session,
        "gs_attest_database_missing",
        expected_revision=session.revision,
        provisional_id=provisional_id,
        reason="登记已核实来源并披露数据库缺失",
    )


def _normalized_question(question):
    options = []
    for index, option in enumerate(question["options"], start=1):
        options.append({
            "value_id": f"option-{index}",
            "label": option["label"],
            "description": option.get("description"),
        })
    return {
        "question_id": question["id"],
        "kind": "single",
        "prompt": question["question"],
        "detail": question.get("detail"),
        "header": question.get("header"),
        "intent": None,
        "options": options,
        "allow_skip": question["allow_skip"],
    }


def test_d2_tools_stay_closed_in_whiteboard_phases(staged):
    # The fixture already reached native event topology. A separate fresh
    # staged session is unnecessary: phase allowlists are restored explicitly
    # and must still reject both whiteboard phases.
    for phase in ("loadout_draft", "action_checkpoint"):
        staged.stage_state["phase"] = phase
        result = _verify(staged, "does.not.exist")
        assert result["reason"] == "agent_tool_forbidden"


def test_native_handoff_checkpoint_has_matching_authority_basis(staged):
    loaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert loaded.workflow_stage == "event_topology"
    assert loaded.provisional_authority["authorization_basis_fingerprint"] == (
        staged.provisional_authority["authorization_basis_fingerprint"]
    )


def test_two_database_missing_attestations_survive_sibling_authority_writes(
    staged, monkeypatch,
):
    refs = [
        {"kind": "provisional", "entity_type": "weapon",
         "provisional_id": "local.weapon.one"},
        {"kind": "provisional", "entity_type": "weapon",
         "provisional_id": "local.weapon.two"},
    ]
    _install_internal_consumer_refs(staged, refs)
    _install(staged, [
        _draft("local.weapon.one", "临时武器一"),
        _draft("local.weapon.two", "临时武器二"),
    ])
    real_save = staged.save

    def fail_save():
        raise OSError("checkpoint unavailable")

    before_candidate = copy.deepcopy(staged.candidate)
    before_authority = copy.deepcopy(staged.provisional_authority)
    before_revision = staged.revision
    monkeypatch.setattr(staged, "save", fail_save)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        staged.verify_provisional_lookup(
            provisional_id="local.weapon.one",
            expected_revision=staged.revision,
            reason="故障注入核对",
        )
    assert staged.candidate == before_candidate
    assert staged.provisional_authority == before_authority
    assert staged.revision == before_revision
    reloaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert reloaded.candidate == before_candidate
    assert reloaded.provisional_authority == before_authority
    assert reloaded.revision == before_revision

    monkeypatch.setattr(staged, "save", real_save)
    assert _verify(staged, "local.weapon.one")["code"] == 0
    before_candidate = copy.deepcopy(staged.candidate)
    before_authority = copy.deepcopy(staged.provisional_authority)
    before_revision = staged.revision
    monkeypatch.setattr(staged, "save", fail_save)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        staged.attest_provisional_database_missing(
            provisional_id="local.weapon.one",
            expected_revision=staged.revision,
            reason="故障注入登记",
        )
    assert staged.candidate == before_candidate
    assert staged.provisional_authority == before_authority
    assert staged.revision == before_revision
    reloaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert reloaded.candidate == before_candidate
    assert reloaded.provisional_authority == before_authority
    assert reloaded.revision == before_revision

    monkeypatch.setattr(staged, "save", real_save)
    assert _attest(staged, "local.weapon.one")["code"] == 0
    first = copy.deepcopy(staged.candidate["provisional_entities"][0]["attestation"])

    assert _verify(staged, "local.weapon.two")["code"] == 0
    assert _attest(staged, "local.weapon.two")["code"] == 0
    assert staged.candidate["provisional_entities"][0]["attestation"] == first
    assert set(staged.provisional_authority["attestation_ledger"]) == {
        first["attestation_id"],
        staged.candidate["provisional_entities"][1]["attestation"]["attestation_id"],
    }

    loaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert loaded.candidate["provisional_entities"] == staged.candidate[
        "provisional_entities"
    ]
    assert loaded.provisional_authority == staged.provisional_authority
    previews = [
        loaded.resolve_provisional_entity_ref(
            ref=ref, consumer="identity_preview",
        )
        for ref in refs
    ]
    assert {row["authority"] for row in previews} == {
        "agent_reported_database_missing",
    }
    with pytest.raises(ProvisionalEntityError) as caught:
        loaded.resolve_provisional_entity_ref(
            ref=refs[0], consumer="agent_materialization",
        )
    assert caught.value.reason == "provisional_entity_consumer_unsupported"
    assert loaded.provisional_usage_receipt() is None

    formal_uses = [
        loaded.resolve_provisional_entity_ref(
            ref=ref, consumer="equipment_composer",
        )
        for ref in refs
    ]
    before_authority = copy.deepcopy(loaded.provisional_authority)
    real_save = loaded.save

    monkeypatch.setattr(loaded, "save", fail_save)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        loaded.publish_provisional_usage_receipt(resolved_uses=formal_uses)
    assert loaded.provisional_authority == before_authority
    assert loaded.provisional_usage_receipt() is None

    monkeypatch.setattr(loaded, "save", real_save)
    published = loaded.publish_provisional_usage_receipt(
        resolved_uses=formal_uses,
    )
    assert loaded.provisional_usage_receipt() == published


def test_database_unavailable_never_becomes_missing_proof(staged, monkeypatch):
    _install(staged, [_draft("local.weapon.unavailable", "不可用查询武器")])
    before_candidate = copy.deepcopy(staged.candidate)
    before_authority = copy.deepcopy(staged.provisional_authority)
    before_revision = staged.revision
    monkeypatch.setattr(
        queries, "list_weapons",
        lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("busy")),
    )
    result = _verify(staged, "local.weapon.unavailable")
    assert result["reason"] == "provisional_entity_lookup_unavailable"
    row = staged.candidate["provisional_entities"][0]
    assert row["database_lookup"] is None
    assert row["attestation"] is None
    assert staged.candidate == before_candidate
    assert staged.provisional_authority == before_authority
    assert staged.revision == before_revision
    assert _attest(staged, "local.weapon.unavailable")["code"] == 1


@pytest.mark.parametrize(
    ("authorization_kind", "candidate_count", "expected_reason"),
    [
        ("database_missing", 1, "provisional_entity_database_present"),
        ("public_data_patch", 0, "provisional_entity_database_missing"),
        ("public_data_patch", 2, "provisional_entity_lookup_ambiguous"),
    ],
)
def test_rejected_lookup_is_atomic_for_candidate_and_authority(
        staged, monkeypatch, authorization_kind, candidate_count, expected_reason):
    name = "核对冲突武器"
    _install(staged, [_draft(
        "local.weapon.rejected", name,
        authorization_kind=authorization_kind,
        values={"weapon": {"base_atk": 741.0}},
    )])
    items = [{"id": 9000 + index, "name": name}
             for index in range(1, candidate_count + 1)]
    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {
        "items": copy.deepcopy(items), "total": len(items),
        "page": 1, "page_size": 100,
    })
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": int(a[1]), "name": name, "base_atk": 740.0},
    })
    before_candidate = copy.deepcopy(staged.candidate)
    before_authority = copy.deepcopy(staged.provisional_authority)
    before_revision = staged.revision

    result = _verify(staged, "local.weapon.rejected")

    assert result["reason"] == expected_reason
    assert staged.candidate == before_candidate
    assert staged.provisional_authority == before_authority
    assert staged.revision == before_revision


def test_public_patch_question_shows_exact_values_and_only_host_answer_approves(
    staged, monkeypatch,
):
    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {
        "items": [{"id": 9001, "name": "数据库武器"}],
        "total": 1, "page": 1, "page_size": 100,
    })
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": 9001, "name": "数据库武器", "base_atk": 740.0},
    })
    ref = {"kind": "provisional", "entity_type": "weapon",
           "provisional_id": "local.weapon.patch"}
    _install_internal_consumer_refs(staged, [ref])
    _install(staged, [_draft(
        "local.weapon.patch", "数据库武器",
        authorization_kind="public_data_patch",
        values={"weapon": {"base_atk": 741.0}},
    )])
    assert _verify(staged, "local.weapon.patch")["code"] == 0
    real_save = staged.save

    def fail_save():
        raise OSError("checkpoint unavailable")

    before_request_candidate = copy.deepcopy(staged.candidate)
    before_request_authority = copy.deepcopy(staged.provisional_authority)
    before_request_revision = staged.revision
    monkeypatch.setattr(staged, "save", fail_save)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        staged.request_provisional_public_patch_approval(
            provisional_id="local.weapon.patch",
            expected_revision=staged.revision,
            reason="故障注入请求批准",
        )
    assert staged.candidate == before_request_candidate
    assert staged.provisional_authority == before_request_authority
    assert staged.revision == before_request_revision
    reloaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert reloaded.candidate == before_request_candidate
    assert reloaded.provisional_authority == before_request_authority
    assert reloaded.revision == before_request_revision

    monkeypatch.setattr(staged, "save", real_save)
    requested = _call(
        staged,
        "gs_request_public_data_patch_approval",
        expected_revision=staged.revision,
        provisional_id="local.weapon.patch",
        reason="请求用户确认精确差异",
    )
    assert requested["code"] == 0, requested
    question = requested["data"]["question"]
    assert '"base_atk":740.0' in question["detail"]
    assert '"base_atk":741.0' in question["detail"]
    assert staged.candidate["provisional_entities"][0]["approval"] is None

    normalized = _normalized_question(question)
    tampered = copy.deepcopy(normalized)
    tampered["detail"] = tampered["detail"].replace("741.0", "999.0")
    with pytest.raises(PlanError) as caught:
        staged.bind_provisional_approval_question(
            batch_id="batch-tampered", call_id="call-tampered", items=[tampered],
        )
    assert caught.value.reason == "provisional_entity_question_stale"
    before_bind = copy.deepcopy(staged.provisional_authority)

    monkeypatch.setattr(staged, "save", fail_save)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        staged.bind_provisional_approval_question(
            batch_id="batch-reviewed", call_id="call-reviewed", items=[normalized],
        )
    assert staged.provisional_authority == before_bind
    monkeypatch.setattr(staged, "save", real_save)
    staged.bind_provisional_approval_question(
        batch_id="batch-reviewed", call_id="call-reviewed", items=[normalized],
    )
    before_reject = copy.deepcopy(staged.provisional_authority)
    monkeypatch.setattr(staged, "save", fail_save)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        staged.settle_provisional_approval_question(
            question={
                "question_id": "batch-reviewed",
                "answer_receipt": "answer-rejected",
                "items": [normalized],
                "answer": {"items": [{
                    "question_id": normalized["question_id"],
                    "value_ids": [normalized["options"][1]["value_id"]],
                    "text": None,
                    "skipped": False,
                }]},
            },
            reason="工程夹具拒绝答案",
        )
    assert staged.provisional_authority == before_reject
    monkeypatch.setattr(staged, "save", real_save)
    resumed = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    before_approve_candidate = copy.deepcopy(resumed.candidate)
    before_approve_authority = copy.deepcopy(resumed.provisional_authority)
    before_approve_revision = resumed.revision
    resumed_save = resumed.save
    monkeypatch.setattr(resumed, "save", fail_save)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        resumed.settle_provisional_approval_question(
            question={
                "question_id": "batch-reviewed",
                "answer_receipt": "answer-approved-failure",
                "items": [normalized],
                "answer": {"items": [{
                    "question_id": normalized["question_id"],
                    "value_ids": [normalized["options"][0]["value_id"]],
                    "text": None,
                    "skipped": False,
                }]},
            },
            reason="故障注入应用用户批准",
        )
    assert resumed.candidate == before_approve_candidate
    assert resumed.provisional_authority == before_approve_authority
    assert resumed.revision == before_approve_revision
    reloaded = CandidateSession.load(resumed.session_id, resumed.root, resumed.db_path)
    assert reloaded.candidate == before_approve_candidate
    assert reloaded.provisional_authority == before_approve_authority
    assert reloaded.revision == before_approve_revision

    monkeypatch.setattr(resumed, "save", resumed_save)
    approved = resumed.settle_provisional_approval_question(
        question={
            "question_id": "batch-reviewed",
            "answer_receipt": "answer-reviewed",
            "items": [normalized],
            "answer": {"items": [{
                "question_id": normalized["question_id"],
                "value_ids": [normalized["options"][0]["value_id"]],
                "text": None,
                "skipped": False,
            }]},
        },
        reason="Host持久化用户回答后应用",
    )
    assert approved["status"] == "approved"
    receipt = resumed.candidate["provisional_entities"][0]["approval"]
    assert receipt["issued_by"] == "user_decision_ledger"
    assert receipt["answer_receipt"] == "answer-reviewed"
    loaded = CandidateSession.load(resumed.session_id, resumed.root, resumed.db_path)
    resolved = loaded.resolve_provisional_entity_ref(
        ref=ref, consumer="identity_preview",
    )
    assert resolved["approval_id"] == receipt["approval_id"]
    with pytest.raises(ProvisionalEntityError):
        loaded.resolve_provisional_entity_ref(
            ref=ref, consumer="agent_materialization",
        )


def test_public_patch_question_fails_closed_above_host_detail_limit(
    staged, monkeypatch,
):
    large_before = {f"field_{index}": "a" * 32 for index in range(90)}
    large_after = {f"field_{index}": "b" * 32 for index in range(90)}
    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {
        "items": [{"id": 9002, "name": "大型差异武器"}],
        "total": 1, "page": 1, "page_size": 100,
    })
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": 9002, "name": "大型差异武器", **large_before},
    })
    _install(staged, [_draft(
        "local.weapon.large", "大型差异武器",
        authorization_kind="public_data_patch",
        values={"weapon": large_after},
    )])
    assert _verify(staged, "local.weapon.large")["code"] == 0
    result = _call(
        staged,
        "gs_request_public_data_patch_approval",
        expected_revision=staged.revision,
        provisional_id="local.weapon.large",
        reason="大型差异必须完整展示",
    )
    assert result["reason"] == "provisional_entity_patch_diff_too_large"
    assert staged.candidate["provisional_entities"][0]["approval"] is None
    assert staged.provisional_authority["pending_approvals"] == {}


def test_missing_and_user_patch_authorities_coexist_in_one_plan(
    staged, monkeypatch,
):
    missing_ref = {
        "kind": "provisional", "entity_type": "weapon",
        "provisional_id": "local.weapon.missing",
    }
    patch_ref = {
        "kind": "provisional", "entity_type": "weapon",
        "provisional_id": "local.weapon.patch",
    }
    _install_internal_consumer_refs(staged, [missing_ref, patch_ref])
    _install(staged, [
        _draft("local.weapon.missing", "缺失武器"),
        _draft(
            "local.weapon.patch", "数据库武器",
            authorization_kind="public_data_patch",
            values={"weapon": {"base_atk": 741.0}},
        ),
    ])
    assert _verify(staged, "local.weapon.missing")["code"] == 0
    assert _attest(staged, "local.weapon.missing")["code"] == 0
    missing_proof = copy.deepcopy(
        staged.candidate["provisional_entities"][0]["attestation"]
    )

    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {
        "items": [{"id": 9001, "name": "数据库武器"}],
        "total": 1, "page": 1, "page_size": 100,
    })
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": 9001, "name": "数据库武器", "base_atk": 740.0},
    })
    assert _verify(staged, "local.weapon.patch")["code"] == 0
    requested = _call(
        staged,
        "gs_request_public_data_patch_approval",
        expected_revision=staged.revision,
        provisional_id="local.weapon.patch",
        reason="请求确认公共数据修正",
    )
    question = _normalized_question(requested["data"]["question"])
    staged.bind_provisional_approval_question(
        batch_id="batch-mixed", call_id="call-mixed", items=[question],
    )
    approved = staged.settle_provisional_approval_question(
        question={
            "question_id": "batch-mixed",
            "answer_receipt": "answer-mixed",
            "items": [question],
            "answer": {"items": [{
                "question_id": question["question_id"],
                "value_ids": [question["options"][0]["value_id"]],
                "text": None,
                "skipped": False,
            }]},
        },
        reason="Host应用用户精确批准",
    )
    assert approved["status"] == "approved"
    assert staged.candidate["provisional_entities"][0]["attestation"] == missing_proof
    resolved = [
        staged.resolve_provisional_entity_ref(
            ref=ref, consumer="identity_preview",
        )
        for ref in (missing_ref, patch_ref)
    ]
    assert {row["authority"] for row in resolved} == {
        "agent_reported_database_missing",
        "user_confirmed_public_data_patch",
    }


def test_business_edit_clears_old_trusted_proofs_even_if_content_is_restored(staged):
    _install(staged, [_draft("local.weapon.one", "临时武器一")])
    assert _verify(staged, "local.weapon.one")["code"] == 0
    assert _attest(staged, "local.weapon.one")["code"] == 0
    original = copy.deepcopy(staged.candidate)
    assert staged.provisional_authority["attestation_ledger"]

    changed = copy.deepcopy(staged.candidate)
    changed["provisional_entities"][0]["reason"] = "新的业务理由"
    changed["provisional_entities"][0]["payload_fingerprint"] = None
    changed["provisional_entities"][0]["attestation"] = None
    staged.replace_candidate(changed, staged.revision, "修改真实业务输入")
    assert staged.provisional_authority["attestation_ledger"] == {}

    restored = copy.deepcopy(original)
    restored["provisional_entities"][0]["payload_fingerprint"] = None
    restored["provisional_entities"][0]["attestation"] = None
    staged.replace_candidate(restored, staged.revision, "恢复原始业务字节")
    assert staged.provisional_authority["attestation_ledger"] == {}
