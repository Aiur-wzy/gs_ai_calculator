import pytest
from test_direct_stellar_damage_contract import direct_stellar_profile
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.special_reactions import (
    direct_stellar_damage_profile_content_hash,
    validate_direct_stellar_damage_profile,
)


def direct_fixture():
    template, rotation, model = _application_fixture('electro', initial=[], times=(.2,))
    rotation['abilities'][0].update(variant='tap', effective_level=10)
    _enable(model, 'stellar_swirl')
    profile = direct_stellar_profile(character_id='a', ability_id='e')
    profile['parameters']['reaction_type'] = 'stellar_swirl'
    profile['profile_content_hash'] = direct_stellar_damage_profile_content_hash(profile)
    model['special_reaction_events'] = [{
        'id':'direct-swirl', 'kind':'direct_stellar_damage', 't':.1, 'order':20,
        'action_id':'cast', 'target_id':'enemy', 'area_id':'battlefield.primary',
        'ability_profile':profile, 'status':'assumed', 'source':'explicit fixture'}]
    return template, rotation, model


def test_direct_swirl_uses_ability_coefficient_and_does_not_create_or_consume_vortex():
    template, rotation, model = direct_fixture()
    out = execute_mechanics(template, rotation, model)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    rows = [r for r in out['resolved_template']['hits'] if r['id'].endswith(':direct:a')]
    assert len(rows) == 1
    assert rows[0]['reaction'] == {'type':'stellar_swirl'}
    snapshot = rows[0]['extensions']['special_reaction']['settled_snapshot']
    assert snapshot['layers'] is None and snapshot['system_factor'] == 1
    assert all(r.get('operation') != 'establish_or_refresh_field' for r in out['special_reaction_trace'])


@pytest.mark.parametrize('element,valid', [('cryo',True),('anemo',True),('electro',False)])
def test_swirl_element_contract(element, valid):
    p = direct_stellar_profile()
    p['parameters'].update(reaction_type='stellar_swirl', element=element)
    p['profile_content_hash'] = direct_stellar_damage_profile_content_hash(p)
    assert (not validate_direct_stellar_damage_profile(p)) == valid


def test_app_composition_cli_saved_plan_and_replay(tmp_path, monkeypatch, capsys):
    import json

    from test_plan_mechanics import native
    from test_special_reaction_integration import equipment_snapshot

    import genshin_engine.special_reaction_capabilities as capabilities
    from genshin_app.cli import main
    from genshin_app.equipment import run_plan_calc

    # Isolate only database lookup/boot. The real projector must verify the
    # catalog identity; composition, bindings, formulas and replay all run.
    entry=next(r for r in capabilities.load_reviewed_special_reaction_capabilities()['entries']
               if 'native_reaction_qualification.stellar_swirl' in r['capability_tags'])
    equipment=equipment_snapshot(entry['character_id'],skill_depot_id=entry['required_skill_depot_id'])
    project=capabilities.project_special_reaction_party_capabilities_detailed
    monkeypatch.setattr(capabilities,'project_special_reaction_party_capabilities_detailed',
        lambda inputs,actor_ids=None:project({**inputs,'a':equipment},actor_ids=actor_ids))
    t,r,m=direct_fixture()
    t['characters'][0]['stats']['energy_recharge']=1
    plan=native()
    plan.update(manual_template=t,rotation_model=r,mechanics_model=m,effect_bindings=[])
    db=tmp_path/'absent.db'
    out=run_plan_calc(plan,db)
    assert out['result'],out['report']
    hits=[h for h in out['result']['hits'] if h['damage_system']=='stellar']
    assert len(hits)==1 and hits[0]['expected']>0
    path=tmp_path/'swirl.plan.json'
    path.write_text(json.dumps(out['plan']),encoding='utf-8')
    monkeypatch.setattr('genshin_app.cli._require_runtime_database',lambda _: {'can_start':True})
    assert main(['--data-dir',str(tmp_path),'equipment','calc','--plan',str(path)])==0
    assert json.loads(capsys.readouterr().out)['data']['result']==out['result']
