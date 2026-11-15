import copy

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.reaction_em_receipt import (
    validate_reaction_em_receipt,
    validate_reaction_em_result,
)
from genshin_app.reaction_em_reporting import reaction_em_rows
from tests.test_reaction_em_app import em_plan as em_plan  # noqa: PLC0414
from tests.test_reaction_em_app import prepared


def test_complete_source_and_occurrence_rows_are_not_editable_formulas(em_plan):
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    result = out['result']
    validate_reaction_em_result(result, out['result_receipt'])
    validate_reaction_em_receipt(out['plan'], result, out['report'], out['result_receipt'])
    rows = reaction_em_rows(result)
    assert any(row[3:6] == ['全队元素精通', 120, 8] for row in rows)
    assert sum('0:h0:reaction:0' in row for row in rows) == 1
    assert reaction_em_rows({}) == []


@pytest.mark.parametrize('field', ['missing_record', 'missing_proof', 'reaction_parent', 'actual_owner',
                                  'record_rule_value', 'scope', 'rule', 'initial', 'classification', 'native'])
def test_edited_source_or_execution_cannot_export_as_current(em_plan, field):
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    bad = copy.deepcopy(out)
    r, report, receipt = bad['result'], bad['report'], bad['result_receipt']
    if field == 'missing_record': r.pop('reaction_em_compilations')
    elif field == 'missing_proof': r.pop('reaction_em_execution')
    elif field == 'reaction_parent': r['reaction_em_execution']['activations'][0]['parent_run_id'] = 'other'
    elif field == 'actual_owner':
        next(row for row in r['mechanics_trace'] if row['type'] == 'elemental_reaction')['owner'] = 'b'
    elif field == 'record_rule_value': r['reaction_em_compilations'][0]['rules'][0]['effects'][0]['value'] = 999
    elif field == 'scope': report['upstream_compilations'][0]['app_scope']['final_model_scope']['provider'] = 'b'
    elif field == 'rule': report['mechanics_model']['rules'][0]['effects'][0]['value'] = 999
    elif field == 'initial': report['mechanics_model']['initial_state']['rules'][0]['cooldown_remaining'] = 1
    elif field == 'classification': report['mechanics_model']['attribute_model']['effect_classifications'][0]['nonextra_inclusion'] = 'excluded'
    else: bad['plan']['effect_bindings'][0]['upstream_recipe']['scenario']['target_id'] = 'other'
    # A shallow top-level rehash does not repair semantic lineage.
    receipt['result_fingerprint'] = fingerprint(r)
    with pytest.raises(ValueError):
        validate_reaction_em_receipt(bad['plan'], r, report, receipt)


def test_actual_occurrence_cannot_be_replaced_even_with_its_local_proof_rehash(em_plan):
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    result, receipt = out['result'], out['result_receipt']
    result['reaction_em_execution']['activations'][1]['parent_run_id'] = '0:h0'
    receipt['result_fingerprint'] = fingerprint(result)
    receipt['reaction_em']['actual_occurrences_fingerprint'] = fingerprint(result['reaction_em_execution'])
    with pytest.raises(ValueError):
        validate_reaction_em_result(result, receipt)
