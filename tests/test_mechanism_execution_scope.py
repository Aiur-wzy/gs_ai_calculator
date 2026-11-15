"""Generic ordinary-stat guards, including contributions from sibling sources."""
import copy

import pytest

from genshin_app.equipment.mechanism_execution_scope import duplicate_scope, stat_scope
from tests.test_mechanics_execution import fixture, rule


def inputs(stat='def', fixed=False):
    template, rotation, mechanics = fixture((0,))
    template['characters'][0]['stats'].update({'def': 500, 'base_def': 100, 'em': 900})
    plan = {'equipment_inputs': {'a': {'mode': 'panel', 'panel_policy': 'static_included'}},
            'effect_bindings': [], 'rotation_model': rotation, 'mechanics_model': mechanics}
    own = rule('own', 'benefit.base_add.all', 0)
    own['effects'][0]['value'] = {'kind': 'stat', 'subject': 'owner', 'stat': stat, 'sample': 'current', 'factor': 1}
    projection = {'rule_templates': [{'key': 'own', 'rule': own}],
                  'required_execution_checks': ['fixed_nonextra_em_equality'] if fixed else []}
    return plan, template, own, projection


def test_new_sibling_source_write_invalidates_fixed_nonextra_scope():
    plan, template, own, projection = inputs('em', True)
    assert stat_scope(plan, template, 'a', [own], projection)[0]['fixed_nonextra']
    sibling = rule('another-source', 'benefit.em.all', 100)
    with pytest.raises(ValueError, match='NONEXTRA'):
        stat_scope(plan, template, 'a', [own, sibling], projection)
    assert plan['mechanics_model']['rules'] == []  # It was a staged sibling, not an input rule.


@pytest.mark.parametrize('unsupported', ['expression', 'filtered', 'quota'])
def test_def_read_scans_each_unavailable_contribution(unsupported):
    plan, template, own, projection = inputs()
    other = rule('def-source', 'benefit.stat_add.def', 100)
    if unsupported == 'expression':
        other['effects'][0]['value'] = copy.deepcopy(own['effects'][0]['value'])
    elif unsupported == 'filtered':
        other['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre'}
    else:
        other['quota'] = {'capacity': 1}
    with pytest.raises(ValueError, match='取样'):
        stat_scope(plan, template, 'a', [own, other], projection)


def test_constant_def_and_unrelated_hp_writes_remain_supported():
    plan, template, own, projection = inputs()
    other = rule('def-source', 'benefit.stat_add.def', 100)
    unrelated = rule('hp-source', 'benefit.stat_add.hp', 100)
    unrelated['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre'}
    assert stat_scope(plan, template, 'a', [own, other, unrelated], projection) == [
        {'stat': 'def', 'base_value': 500, 'fixed_nonextra': False}]


@pytest.mark.parametrize('line', ['release', 'beta'])
def test_source_branch_does_not_create_a_new_stacking_domain(line):
    plan, template, _, _ = inputs()
    def record(ident, actor, version):
        return {'id': ident, 'actor': actor, 'rules': [],
                'source_material': {'snapshot': {'version_line': version}, 'source_binding': {'subject': {'target_id': 123}}},
                'projection': {'execution_qualification': {'duplicate_identity': {
                    'kind': 'proud', 'source_id': 456, 'parts': ['elemental_bonus']}}}}
    records = [record('one', 'a', 'release'), record('two', 'b', line)]
    with pytest.raises(ValueError, match='跨提供者竞争'):
        duplicate_scope(plan, template, records)
    records[1]['projection']['execution_qualification']['duplicate_identity']['source_id'] = 457
    duplicate_scope(plan, template, records)  # Unrelated sources may use another evidence branch.
