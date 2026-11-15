"""Field foundation is separate from the periodic settled-layer benefit."""
import pytest

from tests.test_elemental_world_stellar_superconduct_deadline import (
    AREA,
    world_with_stellar_field,
)


def test_foundation_before_settlement_and_independent_lifetimes():
    world = world_with_stellar_field(layers=3)
    rt = world.special_runtime
    before = rt.stellar_superconduct_snapshot(AREA)
    assert before['active'] and not before['settled_buff_active']
    assert before['layers'] == 0 and before['system_factor'] == 1
    assert before['ordinary_cryo_electro_bonus'] == pytest.approx(.2)
    assert before['settled_cryo_electro_bonus'] == 0
    assert before['physical_res_delta'] == pytest.approx(-.4)
    assert rt.stellar_superconduct_snapshot('other')['ordinary_cryo_electro_bonus'] == 0
    world.advance(4_000_000)
    settled = rt.stellar_superconduct_snapshot(AREA)
    assert settled['ordinary_cryo_electro_bonus'] == pytest.approx(.31)
    assert settled['field_base_cryo_electro_bonus'] == pytest.approx(.2)
    world.advance(6_000_000)
    expired = rt.stellar_superconduct_snapshot(AREA)
    assert not expired['active'] and expired['settled_buff_active']
    assert expired['field_base_cryo_electro_bonus'] == 0
    assert expired['physical_res_delta'] == 0
    assert expired['ordinary_cryo_electro_bonus'] == pytest.approx(.31)
    world.advance(8_000_000)
    assert rt.stellar_superconduct_snapshot(AREA)['ordinary_cryo_electro_bonus'] == 0


def test_field_creation_does_not_retroactively_boost_parent_hit():
    from genshin_engine.mechanics import execute_mechanics
    from tests.test_elemental_multiaura_v4 import aura
    from tests.test_mechanics_execution import EVIDENCE
    from tests.test_special_reaction_mechanics_projection import (
        _application_fixture,
        _enable,
    )
    t, r, m = _application_fixture('electro', initial=[aura('cryo', .8, 'a')], times=(.2, .2))
    _enable(m, 'stellar_superconduct')
    m['elemental_model']['profiles'].append({'id': 'none', 'element': 'cryo',
                                            'application': 'none', **EVIDENCE})
    m['hit_events'][1]['application'] = {'profile_id': 'none', 'target_id': 'enemy', 'landed': True}
    result = execute_mechanics(t, r, m)
    assert [hit['expected'] for hit in result['result']['hits'][:2]] == pytest.approx([45, 54])
    benefits = [row for row in result['mechanics_trace'] if row['type'] == 'special_reaction_effect_applied']
    assert len(benefits) == 1 and benefits[0]['event_id'] == 'h1'
    assert benefits[0]['effect_lifetime'] == 'field'
