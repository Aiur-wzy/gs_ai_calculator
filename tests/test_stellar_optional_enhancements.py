"""Optional sourced parameters preserve base profiles and world invariants."""
import pytest
from test_elemental_multiaura_v4 import model as elemental_model
from test_special_reactions import mechanics, model, reaction, runtime

from genshin_engine.elemental_world import ElementalWorldRuntime
from genshin_engine.special_reaction_reference import (
    copy_runtime,
    replace_special_reaction,
)
from genshin_engine.special_reactions import validate_special_reaction_model


def enhanced(family, **parameters):
    value = model(family)
    value['replacements'][family]['mechanics'] = mechanics(family, **parameters)
    assert validate_special_reaction_model(value) == []
    return runtime(family, value=value)


@pytest.mark.parametrize('clock,seconds,tick', [(None,4,4_000_000),('experimental_384f',3.340596,3_340_596)])
def test_field_clock_refresh_preserves_cadence_and_buff_boundary(clock, seconds, tick):
    params = {'settlement_bonus_layers': 2, 'settlement_interval_seconds': seconds}
    if clock:
        params['settlement_clock'] = clock
    rt = enhanced('stellar_superconduct', **params)
    rt.replace(reaction('stellar_superconduct', area_id='arena'), time=0)
    rt.replace(reaction('stellar_superconduct', index=2, area_id='arena'), time=2_000_000)
    rt.advance(tick-1)
    assert rt.stellar_superconduct_snapshot('arena')['layers'] == 0
    rt.advance(tick)
    settled = rt.stellar_superconduct_snapshot('arena')
    assert settled['layers'] == 2
    assert settled['buff_expires_at'] == 2*seconds
    if clock:
        assert settled['settlement_clock'] == clock
        assert settled['settlement_interval_seconds'] == seconds
    assert rt.stellar_field_by_area['arena']['stellar_superconduct_next_settlement'] == 2*tick


@pytest.mark.parametrize('fields', [
    {'settlement_interval_seconds': 3.340596},
    {'settlement_clock':'experimental_384f', 'settlement_interval_seconds':4},
    {'settlement_clock':'unknown', 'settlement_interval_seconds':3.340596},
    {'settlement_clock':'experimental_384f', 'settlement_interval_seconds':3.340596, 'status':'confirmed'},
])
def test_fast_clock_requires_matching_assumed_preset(fields):
    m = model('stellar_superconduct')
    m['replacements']['stellar_superconduct']['mechanics'] = mechanics('stellar_superconduct', **fields)
    assert any(x['code']=='mechanics_contract' for x in validate_special_reaction_model(m))


def test_initial_grant_is_not_an_application_and_refresh_does_not_regrant():
    rt = enhanced('stellar_superconduct', initial_grant_layers=2, settlement_bonus_layers=2)
    shadow = copy_runtime(rt)
    proposal = reaction('stellar_superconduct', area_id='arena')
    actual = rt.replace(proposal, time=0)
    expected = replace_special_reaction(shadow, proposal, time=0)
    assert actual == expected
    snapshot = rt.stellar_superconduct_snapshot('arena')
    assert snapshot['layers'] == 2 and snapshot['system_factor'] == 1.5
    assert rt.stellar_field_by_area['arena']['stellar_superconduct_layers'] == 0
    world = ElementalWorldRuntime(special_runtime=rt)
    target = elemental_model([], freezable=False)
    target['target']['id'] = 'enemy'
    world.register_target(target, area_ids=('arena',))
    world.advance(4_000_000)
    assert rt.stellar_superconduct_snapshot('arena')['layers'] == 2
    assert rt.stellar_superconduct_snapshot('arena')['buff_expires_at'] == 8
    world.advance(5_000_000)
    rt.replace(reaction('stellar_superconduct', index=2, area_id='arena'), time=5_000_000)
    assert rt.stellar_superconduct_snapshot('arena')['buff_expires_at'] == 8
    world.advance(8_000_000)
    assert rt.stellar_superconduct_snapshot('arena')['layers'] == 2
    from genshin_engine.special_state_timeline import build_special_state_timeline
    timeline = build_special_state_timeline(rt.trace, duration=8, execution_limit=8, execution_status='passed')
    bonuses = [s for s in timeline['states'] if s['kind']=='settled_damage_bonus']
    assert timeline['status']=='complete', timeline
    assert bonuses[0]['start']==0 and bonuses[0]['layers']==2
    assert all(b['system_factor']==1.5 for b in bonuses)


def test_extra_settlement_clamps_without_corrupting_application_count():
    rt = enhanced('stellar_superconduct', initial_grant_layers=2, settlement_bonus_layers=2)
    rt.replace(reaction('stellar_superconduct', area_id='arena'), time=0)
    for i in range(12):
        rt.record_stellar_superconduct_application(event_id=f'a{i}', target_id='enemy',
            actor='trigger.actor', element='cryo', time=0, application_allowed=True,
            atomic_parent_id=f'a{i}', area_id='arena')
    rt.advance(4_000_000)
    assert rt.stellar_superconduct_snapshot('arena')['layers'] == 12
    assert rt.stellar_field_by_area['arena']['stellar_superconduct_layers'] == 0
    rt.advance(8_000_000)
    assert not rt.stellar_superconduct_snapshot('arena')['settled_buff_active']


@pytest.mark.parametrize('packet', [False, True])
def test_initial_upgrade_shared_cooldown_does_not_suppress_normal_reactions(packet):
    rt = enhanced('stellar_swirl', initial_vortex_count=3, initial_upgrade_cooldown_seconds=1)
    def create(area, tick, index):
        row = reaction('stellar_swirl', index=index, area_id=area)
        if packet:
            rt.replace_stellar_swirl_packet([row], time=tick, area_id=area)
        else:
            rt.replace(row, time=tick)
    create('a', 0, 1)
    assert rt.vortex_by_area['a']['stellar_swirl_count'] == 3
    assert rt.vortex_by_area['a']['stellar_swirl_level'] == 2
    create('b', 500_000, 2)
    assert rt.vortex_by_area['b']['stellar_swirl_count'] == 1
    create('b', 500_000, 3)
    assert rt.vortex_by_area['b']['stellar_swirl_count'] == 2
    create('c', 1_000_000, 4)
    assert rt.vortex_by_area['c']['stellar_swirl_count'] == 3


@pytest.mark.parametrize('field,value', [('initial_grant_layers', -1), ('initial_grant_layers', True),
    ('settlement_bonus_layers', 13), ('settlement_bonus_layers', 1.5)])
def test_invalid_grant_parameters_rejected(field, value):
    m = model('stellar_superconduct')
    m['replacements']['stellar_superconduct']['mechanics'] = mechanics('stellar_superconduct', **{field:value})
    assert any(x['path'].endswith(field) for x in validate_special_reaction_model(m))


def test_assumed_extension_cannot_claim_database_review():
    m = model('stellar_superconduct')
    m['replacements']['stellar_superconduct']['mechanics']['initial_grant_layers'] = 2
    assert any(x['code'] == 'profile_override_status' for x in validate_special_reaction_model(m))


def test_enhanced_single_reactions_match_independent_reference_across_areas():
    rt=enhanced('stellar_swirl',initial_vortex_count=3,initial_upgrade_cooldown_seconds=1)
    shadow=copy_runtime(rt)
    for index,(area,tick) in enumerate([('a',0),('b',500000),('b',500000),('c',1000000)],1):
        proposal=reaction('stellar_swirl',index=index,area_id=area)
        assert rt.replace(proposal,time=tick)==replace_special_reaction(shadow,proposal,time=tick)
        assert rt.vortex_by_area==shadow.vortex_by_area
        assert rt.party_state==shadow.party_state
