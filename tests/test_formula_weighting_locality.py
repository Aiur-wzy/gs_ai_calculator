"""A frozen multi-owner distribution must not lock unrelated scalar damage."""
from __future__ import annotations

import pytest
from formula_workbook_fixtures import mechanics_envelope
from test_elemental_multiaura_v4 import aura
from test_mechanics_execution import second_character
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_app.formula_workbook import _Projection


def projection():
    template, rotation, model = _application_fixture('electro', initial=[aura('hydro', .8, 'b')])
    second_character(template, rotation, 'b')
    template['characters'][1]['stats'].update(em=350, crit_rate=.6, crit_dmg=1.2)
    _enable(model, 'lunar_electro')
    return _Projection(mechanics_envelope((template, rotation, model)), _session=None)


def test_unrelated_classic_multiplier_is_editable_but_weighted_members_stay_guarded():
    item = projection()
    item.prepare_inputs()
    assert item.weighting['readonly_all']
    # A local scalar changes neither the reaction input nor joint outcome rank.
    assert item.template['hits'][0]['damage_system'] == 'classic'
    path = 'hits[0].multiplier'
    row = item.inputs.rows[item.rows_by_path[path] - 1]
    assert row[4].value == '数值公式重算'
    weighted = item.weighting['groups'][0]['hit_ids'][0]
    guard = item._weight_guard(weighted)
    assert guard
    dependencies = [row[1].value for row in item.weight_dependencies.rows[1:]]
    assert path not in dependencies
    index = next(i for i, hit in enumerate(item.template['hits']) if hit['id'] == weighted)
    assert f'hits[{index}].multiplier' in dependencies
    assert 'characters[0].stats.em' in dependencies
    assert item._weighted_parameter('enemy.resistance.default')


@pytest.mark.parametrize('unknown', [True, False])
def test_unknown_weight_provenance_and_membership_never_gain_scalar_permission(unknown):
    item = projection()
    index = 0
    if unknown:
        item.weighting['unknown_provenance'] = True
    else:
        index = next(i for i, hit in enumerate(item.template['hits'])
                     if hit['id'] in item.weighting['hit_group'])
    assert item._weighted_parameter(f'hits[{index}].multiplier')


def test_guarded_text_does_not_feed_arithmetic_baseline_difference():
    item = projection().finish()
    for row_number, row in enumerate(item.events.rows, 1):
        if len(row) >= 25 and getattr(row[24], 'formula', None):
            assert f'ISNUMBER(V{row_number})' in row[24].formula
            assert '重新生成' in row[24].formula
