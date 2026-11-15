"""Execution limits survive whiteboard handoff without becoming source facts."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.equipment.files import context_view, fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import (
    board_fingerprint,
    load_whiteboard,
    make_handoff,
    prepare_whiteboard_edit,
    save_whiteboard,
    validate_result_intent,
    validate_whiteboard,
    validate_whiteboard_revision_transition,
)
from tests.test_whiteboard import board_fixture


def options(frames=2048):
    return {'schema_version':1,'attribute_limits':{'frames':frames}}


def test_saved_board_and_fresh_handoff_retain_exact_nondefault_request(tmp_path):
    board = board_fixture()
    board['calculation_options'] = options()
    validate_whiteboard(board)
    path = tmp_path/'board.json'
    save_whiteboard(path, board)
    loaded = load_whiteboard(path)['board']
    plan = make_handoff(loaded)
    assert loaded['calculation_options'] == options()
    assert plan['calculation_options'] == options()
    assert plan['extensions']['whiteboard_handoff']['board']['calculation_options'] == options()
    validate_result_intent(loaded, plan)
    plan['calculation_options'] = options(4096)
    with pytest.raises(PlanError) as caught:
        validate_result_intent(loaded, plan)
    assert caught.value.reason == 'whiteboard_intent_mismatch'


def test_budget_changes_execution_identity_but_not_factual_source_context():
    board = board_fixture()
    plan = make_handoff(board)
    changed = copy.deepcopy(plan)
    changed['calculation_options'] = options()
    saved = changed['extensions']['whiteboard_handoff']['board']
    saved['calculation_options'] = options()
    changed['extensions']['whiteboard_handoff']['board_fingerprint'] = board_fingerprint(saved)
    assert fingerprint(input_view(plan)) != fingerprint(input_view(changed))
    assert fingerprint(context_view(input_view(plan))) == fingerprint(context_view(input_view(changed)))
    changed['manual_template']['characters'][0]['stats']['em'] = 99
    assert fingerprint(context_view(input_view(plan))) != fingerprint(context_view(input_view(changed)))


def test_revision_audit_detects_both_added_and_removed_optional_budget():
    original = board_fixture()
    requested = copy.deepcopy(original)
    requested['calculation_options'] = options()
    requested = prepare_whiteboard_edit(original, requested)
    receipt = validate_whiteboard_revision_transition(original, requested)
    assert receipt['fields'] == ['calculation_options']
    assert receipt['next_revision'] == original['revision'] + 1
    cleared = copy.deepcopy(requested)
    cleared.pop('calculation_options')
    cleared = prepare_whiteboard_edit(requested, cleared)
    receipt = validate_whiteboard_revision_transition(requested, cleared)
    assert receipt['fields'] == ['calculation_options']
    assert receipt['next_revision'] == requested['revision'] + 1


def test_native_candidate_mirrors_single_budget_edit_and_cold_restores_both_views(tmp_path):
    plan = make_handoff(board_fixture())
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path/'session', tmp_path/'absent.db','native',plan)
    response = session.call_tool('gs_patch_candidate',{'expected_revision':0,'reason':'明确调整本方案运行预算',
        'operations':[{'op':'add','path':'/calculation_options','value':options()}]})
    assert response['code'] == 0, response
    board = session.candidate['extensions']['whiteboard_handoff']['board']
    assert board['calculation_options'] == options()
    assert board['revision'] == 1
    validate_result_intent(board, session.candidate)
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert restored.candidate == session.candidate
    assert make_handoff(board)['calculation_options'] == options()
    response = session.call_tool('gs_patch_candidate',{'expected_revision':session.revision,'reason':'原生预算为单真源，派生白板不能覆盖执行输入',
        'operations':[{'op':'replace','path':'/calculation_options','value':options(1024)},
            {'op':'replace','path':'/extensions/whiteboard_handoff/board/calculation_options','value':options(4096)}]})
    assert response['code'] == 0, response
    assert session.candidate['calculation_options'] == options(1024)
    projected = session.candidate['extensions']['whiteboard_handoff']['board']
    assert projected['calculation_options'] == options(1024)
    validate_result_intent(projected, session.candidate)
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert restored.candidate == session.candidate


@pytest.mark.parametrize('value',[None,True,{'schema_version':True},{'schema_version':1,'attribute_limits':{'frames':True}},
    {'schema_version':1,'attribute_limits':{'frames':0}},{'schema_version':1,'unknown':1}])
def test_board_rejects_invalid_budget_request_before_handoff(value):
    board=board_fixture()
    board['calculation_options']=value
    with pytest.raises(PlanError):
        validate_whiteboard(board)
