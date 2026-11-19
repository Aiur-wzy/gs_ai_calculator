"""Reviewed identity and generic mechanisms, not a character damage benchmark."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_mechanics_execution import add, rule
from test_periodic_resource_benefits import damage_rule, resource_hits
from test_special_reaction_integration import equipment_snapshot
from test_stellar_resource_public_operations import fixture

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.special_reaction_capabilities import (
    load_reviewed_special_reaction_capabilities,
    project_special_reaction_party_capabilities,
)


def test_mitya_review_retains_source_and_previous_catalog():
    review = json.loads((Path(__file__).resolve().parents[1] /
        'docs/pm/audits/2026-11-18-mitya-special-capability-registration.json').read_text('utf8'))
    payload = {k:v for k,v in review.items() if k != 'review_fingerprint'}
    assert review['review_fingerprint'] == hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    catalog = load_reviewed_special_reaction_capabilities(review["source_upstream_commit"])
    assert review['catalog_content_hash'] == catalog['content_hash']
    before = {e['character_id']:e for e in review['previous_catalog']['entries']}
    for entry in catalog['entries']:
        if entry['character_id'] != 10000136:
            assert entry == before[entry['character_id']]
        else:
            assert entry == review['entry']
            assert entry['runtime_support_status'] == 'supported'
    raw = review['raw_records']
    assert raw['AvatarExcelConfigData.json'][0]['skillDepotId'] == 13601
    assert raw['AvatarSkillDepotExcelConfigData.json'][0]['id'] == 13601
    passive = next(r for r in raw['ProudSkillExcelConfigData.json'] if r['proudSkillId'] == 1362301)
    assert str(passive['descTextMapHash']) == review['entry']['evidence']['desc_text_map_hash']
    assert '每1.5秒' in review['texts_zh'][str(passive['descTextMapHash'])]
    c6 = next(r for r in raw['AvatarTalentExcelConfigData.json'] if r['talentId'] == 1366)
    assert c6['paramList'][0] == pytest.approx(.34)


@pytest.mark.parametrize('outcome', [None, False, True])
def test_mitya_public_identity_c6_refund_c1_recovery_and_c4_threshold_replay(outcome):
    t,r,m = fixture()
    m['special_reaction_party_capabilities'] = project_special_reaction_party_capabilities(
        {'a':equipment_snapshot(10000136, skill_depot_id=13601)})
    config = m['special_reaction_model']['replacements']['stellar_superconduct']['mechanics']['periodic_resource']
    config.update(capacity=5, lifetime_seconds=10)
    base = copy.deepcopy(m['special_reaction_events'][0])
    m['special_reaction_events'].insert(0, {**base, 'id':'overload', 't':.25,
        'operation':'set_mode', 'mode':'extended_generation', 'duration_seconds':8})
    m['special_reaction_events'][0].pop('amount')
    # C6 is checked per consumed beacon, not once for an arbitrary batch.
    consume = m['special_reaction_events'][2]
    consume.pop('amount')
    consume.update(operation='trial', trial={'cost':1, 'base_grant':0,
        'success_grant':1, 'success_waives_cost':False, 'chance':.34})
    if outcome is not None:
        consume['outcome'] = outcome
    for i in range(1,5):
        m['special_reaction_events'].append({**copy.deepcopy(consume), 'id':f'consume-{i}', 't':1.8+i*1.5})
    r['characters'][0].update(initial_energy=0, energy_capacity=100)
    recovery = rule('c1-recovery', effects=[], duration=0,
        trigger={'event':'resource_change', 'phase':'post', 'resource_operations':['created']},
        timing={'delay':0,'duration':0,'cooldown':8,'cooldown_anchor':'success'},
        resources=[{'id':'energy','kind':'energy_direct','delay':0,'amounts':{'a':10},'source_part':'creation'}])
    add(m,recovery)
    m['resource_ownership']=[{'rule_id':recovery['id'],'source_part':'creation',
        'source_ref':{'contract':'resource-trigger-source-v1','kind':'current_trigger'},'replaces_event_ids':[]}]
    damage_rule(m,every=5)['trigger']['resource_every']['scope']='matching'
    m['rules'][-1]['trigger']['resource_modes']=['extended_generation','overflow_consume']
    before=copy.deepcopy((t,r,m))
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    trials=[x['trial'] for x in out['special_reaction_trace'] if x.get('trial') and x.get('type')=='stellar_resource_receipt']
    assert len(trials)==5 and all(x['executed'] for x in trials)
    assert sum(x['created_units'] for x in trials)==pytest.approx(1.7 if outcome is None else 5*int(outcome))
    assert sum(x['consumed_units'] for x in trials)==5
    assert len(resource_hits(out))==1
    credits=[e for e in out['rotation_check']['event_log'] if e['type']=='energy_direct']
    assert len(credits)==1 and credits[0]['t']==.3
    assert (t,r,m)==before
    assert execute_mechanics(*copy.deepcopy(before))==out


def test_mitya_cannot_silently_fall_back_to_ordinary_crystal():
    t,r,m=fixture()
    m['special_reaction_party_capabilities']=project_special_reaction_party_capabilities(
        {'a':equipment_snapshot(10000136, skill_depot_id=13601)})
    m['special_reaction_model']['replacements']['stellar_superconduct']['mechanics'].pop('periodic_resource')
    m['special_reaction_events']=[]
    out=execute_mechanics(t,r,m)
    assert any(x['code']=='special_capability_resource_profile_required' for x in out['issues'])
    assert not out.get('result')
