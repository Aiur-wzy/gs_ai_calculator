import copy

import pytest
from test_elemental_multiaura_v4 import model as elemental_model
from test_special_reactions import mechanics, model, reaction, runtime

from genshin_engine.elemental_world import ElementalWorldPending, ElementalWorldRuntime
from genshin_engine.special_reaction_reference import (
    copy_runtime,
    replace_special_reaction,
)
from genshin_engine.special_reactions import validate_special_reaction_model

CONFIG = {'owner_id': 'qualification.provider', 'area_id': 'arena', 'interval_seconds': 1.5,
              'lifetime_seconds': 6, 'capacity': 4, 'window_seconds': 3, 'extended_window_seconds': 6,
              'refresh_mode': 'extend_window_preserve_cadence'}


def make():
    value = model('stellar_superconduct')
    value['replacements']['stellar_superconduct']['mechanics'] = mechanics('stellar_superconduct', periodic_resource=copy.deepcopy(CONFIG))
    assert not validate_special_reaction_model(value)
    return runtime('stellar_superconduct', value=value)


def op(rt, operation, time, id='op', **kw):
    return rt.operate_stellar_resource(event_id=id, actor='qualification.provider', area_id='arena',
                                       time=int(time*1e6), operation=operation, **kw)


def test_reaction_generation_preserves_phase_refreshes_field_and_expires():
    rt = make()
    rt.replace(reaction('stellar_superconduct', area_id='arena'), time=0)
    assert not rt.stellar_superconduct_snapshot('arena')['active']
    rt.replace(reaction('stellar_superconduct', index=2, area_id='arena'), time=1_000_000)
    assert not rt.stellar_superconduct_snapshot('arena')['active']
    rows = rt.advance(4_000_000)
    created = [r for r in rows if r.get('resource_type') and r['operation']=='created']
    assert [r['t'] for r in created] == [1.5, 3]
    fields = [r for r in rows if r.get('operation') == 'establish_or_refresh_field']
    assert [r['t'] for r in fields] == [1.5, 3]
    assert fields[0]['next_settlement_at'] == 5.5
    assert rt.stellar_superconduct_snapshot('arena')['field_expires_at'] == 9
    rt.advance(10_000_000)
    assert not rt.stellar_superconduct_snapshot('arena')['active']
    assert rt.party_state['stellar_periodic_resource']['resource']['count'] == 0


def test_core_mode_overflow_and_teardown_have_actual_consumption_receipts():
    rt = make()
    op(rt, 'set_mode', 0, 'core', mode='overflow_consume', duration_seconds=5)
    op(rt, 'grant', 0, 'q', amount=3)
    op(rt, 'grant', 1, 'e', amount=3)
    ledger = rt.party_state['stellar_periodic_resource']
    assert ledger['resource']['count']==4 and ledger['counters']['overflow_consumed']==2
    rt.advance(5_000_000)
    ledger = rt.party_state['stellar_periodic_resource']
    assert ledger['resource']['count']==0 and ledger['counters']['consumed']==6
    assert op(rt, 'observe', 5, 'c4', counter='consumed', claim_id='c4', divisor=5)['resource_event']['amount']==1
    assert op(rt, 'observe', 5, 'c4-again', counter='consumed', claim_id='c4', divisor=5)['resource_event']['amount']==0


def test_failed_consume_rolls_back_deadlines_and_resources():
    rt=make()
    rt.replace(reaction('stellar_superconduct', area_id='arena'), time=0)
    before=rt.transaction_snapshot()
    with pytest.raises(ValueError, match='insufficient resource'):
        op(rt, 'consume', 1.5, amount=2)
    assert rt.transaction_snapshot()==before


def test_resource_derived_damage_requires_actual_same_time_ordinal_and_deduplicates():
    from test_direct_stellar_damage_contract import direct_stellar_profile
    rt=make()
    op(rt,'grant',0,amount=2)
    observation=next(e for e in rt.trace if e.get('resource_type')=='periodic_resource' and e.get('operation')=='created')
    payload={'event_id': 'damage','target_id': 'enemy','area_id': 'arena','actor': 'qualification.provider',
                 'action_id': 'e','ability_id': 'ability.star','ability_variant': 'tap','ability_effective_level': 10,
                 'time': 0,'ability_profile': direct_stellar_profile()}
    before=rt.transaction_snapshot()
    for trigger in ({'event_id':'invented','ordinal':1},
                    {'event_id':observation['event_id'],'ordinal':3},
                    {'event_id':observation['event_id'],'ordinal':True}):
        with pytest.raises(ValueError):
            rt.request_direct_stellar_damage(**payload,resource_trigger=trigger)
        assert rt.transaction_snapshot()==before
    trigger={'event_id':observation['event_id'],'ordinal':1}
    rt.request_direct_stellar_damage(**payload,resource_trigger=trigger)
    committed=rt.transaction_snapshot()
    from genshin_engine.special_reactions import SpecialReactionPending
    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_stellar_damage(**{**payload,'event_id':'other-id'},resource_trigger=trigger)
    assert error.value.issue['code']=='direct_special_delivery_duplicate'
    assert rt.transaction_snapshot()==committed


def test_world_deadline_reference_and_chunked_runtime_match():
    rt=make()
    shadow=copy_runtime(rt)
    react=reaction('stellar_superconduct', area_id='arena')
    assert rt.replace(react,time=0)==replace_special_reaction(shadow,react,time=0)
    world=ElementalWorldRuntime(special_runtime=rt)
    target=elemental_model([], freezable=False)
    target['target']['id']='enemy'
    world.register_target(target,area_ids=('arena',))
    world.advance(10_000_000)
    shadow.advance(10_000_000)
    assert rt.stellar_field_by_area==shadow.stellar_field_by_area
    assert rt.party_state==shadow.party_state


def test_extended_generation_yields_four_and_consumption_keeps_remaining_clock():
    rt=make()
    op(rt,'set_mode',0,'core',mode='extended_generation',duration_seconds=10)
    rt.replace(reaction('stellar_superconduct', area_id='arena'),time=0)
    op(rt,'consume',1.5,'ca',amount=1)
    rt.advance(6_000_000)
    assert rt.party_state['stellar_periodic_resource']['resource']['count']==3
    assert rt.party_state['stellar_periodic_resource']['counters']['created']==4


@pytest.mark.parametrize('field', ['count', 'next_generation', 'consumed'])
def test_world_rejects_forged_resource_poststate_even_with_correct_rows(monkeypatch,field):
    rt=make()
    rt.replace(reaction('stellar_superconduct',area_id='arena'),time=0)
    world=ElementalWorldRuntime(special_runtime=rt)
    target=elemental_model([],freezable=False)
    target['target']['id']='enemy'
    world.register_target(target,area_ids=('arena',))
    before=copy.deepcopy(world.state())
    original=rt.advance_world_pre_expiry
    def corrupt(time):
        rows=original(time)
        ledger=rt.party_state['stellar_periodic_resource']
        if field=='consumed':
            ledger['counters'][field]+=1
        else:
            ledger['resource'][field]+=1
        return rows
    monkeypatch.setattr(rt,'advance_world_pre_expiry',corrupt)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(1_500_000)
    assert error.value.issue['code']=='world_stellar_resource_deadline_invalid'
    assert world.state()==before
