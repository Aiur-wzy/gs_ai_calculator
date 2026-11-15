"""Pre-admission drafts recover supported conversions without granting facts."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attribute_recovery import (
    preview_attribute_recovery,
    read_recovery_input,
)
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError, validate_plan
from tests.test_attribute_graph_app import graph_plan
from tests.test_plan_mechanics import native, review


def one_way():
    plan = graph_plan('total')
    model = plan['mechanics_model']
    model['rules'] = [r for r in model['rules'] if r['id'] != 'a_to_b']
    model['initial_state']['rules'] = [r for r in model['initial_state']['rules'] if r['rule_id'] != 'a_to_b']
    plan['effect_bindings'] = [r for r in plan['effect_bindings'] if r['binding_id'] != 'bind_a_to_b']
    model.pop('attribute_model')
    return plan


def apply_draft(plan, draft):
    candidate = copy.deepcopy(plan)
    candidate['mechanics_model']['attribute_model'] = copy.deepcopy(draft['operations'][0]['value'])
    return candidate


def test_invalid_legacy_can_get_draft_before_compose_and_then_finish_numeric_loop(tmp_path):
    plan = one_way()
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError, match='机制模型') as exc:
        compose_equipment_plan(plan, tmp_path/'absent.db')
    assert any(row['code'] == 'unsupported_dependency' and not row['known_cycle'] for row in exc.value.details)
    draft = preview_attribute_recovery(plan)
    assert plan == before
    assert draft['status'] == 'draft_only' and not draft['executes'] and not draft['adopted']
    assert draft['required_contract'] == 'attribute-ledger-v2'
    candidate = apply_draft(plan, draft)
    validate_plan(candidate)
    db = tmp_path/'absent.db'
    review(candidate, db)
    adopted = prepare_attribute_review(candidate, '核对虚构角色和单向total分享；没有声明NONEXTRA事实', db)
    result = run_plan_calc(adopted['plan'], db)
    assert result['result']['mechanics']['complete_first_cycle'] is True
    assert any(row['query']['actor'] == 'a' and row['value'] == 140
               for row in result['result']['attribute_ledger']['receipts'])
    assert plan == before


def test_draft_cannot_turn_actual_same_frame_feedback_into_accepted_result(tmp_path):
    plan = graph_plan('total')
    plan['mechanics_model'].pop('attribute_model')
    candidate = apply_draft(plan, preview_attribute_recovery(plan))
    db = tmp_path/'absent.db'
    review(candidate, db)
    adopted = prepare_attribute_review(candidate, '虚构同帧互授反例，不能因采用就允许真实环', db)['plan']
    with pytest.raises(PlanError) as exc:
        run_plan_calc(adopted, db)
    assert 'attribute_dependency_cycle' in str(exc.value.details)


def test_original_wrong_shape_is_not_authorized_by_recovery():
    for mutation in ('malformed-effect', 'reference-shape', 'nonfinite'):
        plan = one_way()
        if mutation == 'malformed-effect':
            plan['mechanics_model']['rules'][0]['effects'] = None
        elif mutation == 'reference-shape':
            plan['mechanics_model']['hit_events'][0]['hit_id'] = True
        else:
            plan['manual_template']['characters'][0]['stats']['em'] = float('nan')
        with pytest.raises(PlanError):
            preview_attribute_recovery(plan)


def test_plain_legal_inputs_are_not_forced_to_opt_in():
    value = preview_attribute_recovery(native())
    assert value['status'] == 'not_required' and value['operations'] == []


def test_reader_rejects_duplicate_keys_without_semantic_admission(tmp_path):
    path = tmp_path/'input.json'
    path.write_text('{"plan_version":3,"plan_version":3}', encoding='utf-8')
    with pytest.raises(PlanError, match='重复'):
        read_recovery_input(path)
    plan = one_way()
    path.write_text(json.dumps(plan), encoding='utf-8')
    assert read_recovery_input(path) == plan


def test_native_tool_previews_uncommitted_invalid_patch_without_session_or_permission_changes(tmp_path):
    starting = graph_plan('total')
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path/'session', tmp_path/'absent.db',
                                       'native', starting)
    before = fingerprint(session.candidate)
    response = session.call_tool('gs_preview_attribute_recovery', {'expected_revision':session.revision,
        'operations':[{'op':'remove','path':'/mechanics_model/attribute_model'}]})
    assert response['code'] == 0, response
    assert response['data']['status'] == 'draft_only'
    assert fingerprint(session.candidate) == before and session.revision == 0
    assert session.calculated is None


def test_cli_invalid_input_guides_to_recovery_then_publishes_only_a_draft(tmp_path, capsys, monkeypatch):
    from genshin_app.cli import main
    monkeypatch.setattr('genshin_app.cli._require_runtime_database', lambda _: {'can_start':True})
    path, draft_path = tmp_path/'invalid.json', tmp_path/'draft.json'
    original = one_way()
    path.write_text(json.dumps(original), encoding='utf-8')
    assert main(['equipment','validate','--plan',str(path)]) == 1
    rejected = json.loads(capsys.readouterr().err)
    assert rejected['operation'] == 'cli.equipment.validate'
    assert rejected['recovery']['action'] == 'preview_attribute_recovery'
    assert main(['equipment','attribute-draft','--plan',str(path),'--output',str(draft_path)]) == 0
    capsys.readouterr()
    published = json.loads(draft_path.read_text(encoding='utf-8'))
    assert published['status'] == 'draft_only' and published['adopted'] is False
    assert json.loads(path.read_text()) == original
