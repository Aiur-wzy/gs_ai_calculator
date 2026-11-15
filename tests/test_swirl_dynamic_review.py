"""Independent v3 review: audit projection, event order, and source refusal."""
import copy

import pytest
from test_mechanics_execution import add, rule
from test_mechanics_swirl import swirls
from test_swirl_dynamic_v3 import manual_em, sampled, setup

from genshin_app.gui.api import GuiApi
from genshin_engine.mechanics import execute_mechanics
from tests.test_runtime_pyro_source import pyro_db as pyro_db  # noqa: PLC0414
from tests.test_runtime_reaction_damage import SOURCE, change
from tests.test_runtime_reaction_damage import fake_repo as fake_repo  # noqa: PLC0414
from tests.test_runtime_reaction_damage import (
    reaction_db as reaction_db,  # noqa: PLC0414
)
from tests.test_runtime_reaction_damage import (
    stub_fetcher as stub_fetcher,  # noqa: PLC0414
)


def oracle(em):
    return 1446.85 * .6 * .9 * (1 + 16 * em / (2000 + em))


def test_same_time_parent_post_writer_only_changes_later_ordered_child():
    t, r, m = setup((.2, .2))
    add(m, rule('em', 'benefit.em.all', 137,
                trigger={'event':'hit','phase':'post','event_ids':['h0']}))
    out=execute_mechanics(t,r,m)
    assert [s['value'] for s in sampled(out)] == [0,137]
    assert [h['expected'] for h in swirls(out)] == pytest.approx([oracle(0),oracle(137)])


def test_reaction_damage_post_writer_does_not_retroactively_change_itself():
    t,r,m=setup((.2,.3))
    add(m,rule('em','benefit.em.all',91,
               trigger={'event':'reaction_damage','phase':'post'}))
    out=execute_mechanics(t,r,m)
    assert [s['value'] for s in sampled(out)] == [0,91]
    assert [h['expected'] for h in swirls(out)] == pytest.approx([oracle(0),oracle(91)])


def test_nondamage_application_samples_dynamic_em_without_classic_parent():
    t,r,m=setup()
    t['hits']=[]
    m['hit_events']=[]
    m['application_events']=[{'id':'contact','action_id':'cast','t':.2,'order':10,
        'application':{'profile_id':'anemo','target_id':'enemy','landed':True}}]
    t['buffs']=[manual_em(173)]
    out=execute_mechanics(t,r,m)
    assert len(out['result']['hits'])==1
    assert sampled(out)[0]['value']==173
    assert out['result']['hits'][0]['expected']==pytest.approx(oracle(173))


def test_multiple_contributors_reconcile_audit_and_numerical_em():
    t,r,m=setup((.2,.7))
    t['characters'][0]['stats']['em']=237
    one=manual_em(113,end=.7); two=manual_em(-17,end=.3)
    two['id']='adjustment'
    t['buffs']=[one,two]
    add(m,rule('em','benefit.em.all',71))
    original=copy.deepcopy((t,r,m))
    out=execute_mechanics(t,r,m)
    assert [s['value'] for s in sampled(out)]==[404,308]
    assert [h['expected'] for h in swirls(out)]==pytest.approx([oracle(404),oracle(308)])
    assert (t,r,m)==original
    for s,h in zip(sampled(out),swirls(out),strict=True):
        assert s['base_value']+sum(d['value'] for d in s['dependencies'])==s['value']
        assert h['zones']['em_zone']==pytest.approx(1+16*s['value']/(2000+s['value']))


def test_finite_cancellation_cannot_publish_damage_inconsistent_with_sample():
    t,r,m=setup()
    t['characters'][0]['stats']['em']=1e16
    t['buffs']=[manual_em(-1e16)]
    add(m,rule('tiny','benefit.em.all',1))
    out=execute_mechanics(t,r,m)
    # Accepted finite constants may be rejected explicitly; a published audit
    # sample must explain the actual numerical EM used by the child.
    if out['status']=='pending':
        assert out['result'] is None
        return
    s=sampled(out)[0]
    h=swirls(out)[0]
    assert h['zones']['em_zone']==pytest.approx(1+16*s['value']/(2000+s['value']))
    assert h['expected']==pytest.approx(oracle(s['value']))


def test_gui_real_missing_fire_refuses_instead_of_borrowing_ice(reaction_db):
    response=GuiApi(reaction_db.parent).query_runtime({'action':'reaction_damage_profile','element':'pyro'})
    assert response['code']!=0
    assert response['reason']=='not_found'


def test_gui_changed_fire_attack_refuses_before_any_projection(pyro_db,fake_repo):
    gui=GuiApi(pyro_db.parent)
    positive=gui.query_runtime({'action':'reaction_damage_profile','element':'pyro'})
    assert positive['code']==0
    def tamper(rows):
        # Change the scoped ordinary Fire attack itself, preserving the names
        # and the attenuation group. Equal old ice/water sequences cannot help.
        rows[-1]['Default']['GPPBBDEICKP']['Swirl_Fire']['EMGBJJOIENB'][2]['unreviewed_attack_value']=1
    change(pyro_db,fake_repo,SOURCE,tamper)
    negative=gui.query_runtime({'action':'reaction_damage_profile','element':'pyro'})
    assert negative['code']!=0
    assert negative['reason']=='unsupported_source'
