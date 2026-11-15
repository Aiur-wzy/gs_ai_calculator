"""Source subject, equipped provider and actual caster are distinct identities."""
from __future__ import annotations

import copy

from .energy_generation_sources import _one
from .files import fingerprint
from .mechanism_execution_scope import need


def qualify_equipped_provider(plan, template, binding, material, equipment_evidence, requirements):
    """Shared provider proof; source subject never substitutes for the actor."""
    actor = binding['actor']
    gear = plan['equipment_inputs'][actor]
    character = _one(template['characters'], 'id', actor, '机制提供者')
    source = material['source_binding']
    q = material['compiler_result']['execution_qualification']
    expected = source['selection']
    actual_ref = gear['character_ref']
    coverage = []
    proof = {'actor': actor, 'character_ref': copy.deepcopy(actual_ref), 'level': character['level'],
             'progression': copy.deepcopy(gear.get('progression'))}
    for key, value in expected.items():
        if key == 'character_level':
            need(character['level'] == value, '角色等级不在来源范围内')
        elif key == 'ascension':
            progression = gear.get('progression') or {}
            need(progression.get('ascension') == value and progression.get('status') in ('assumed', 'confirmed')
                 and bool(progression.get('source', '').strip()), '角色突破缺少依据或不在来源范围内')
    if q['provider']['relation'] == 'source_character':
        need(source['subject']['target_type'] == 'character'
             and actual_ref['db_character_id'] == source['subject']['target_id'], '来源主体与实际提供者不一致')
    elif q['provider']['relation'] == 'equipped_weapon':
        from .mechanism_contact_scope import qualify_weapon
        item = qualify_weapon(plan, binding, material, equipment_evidence, requirements)
        if q['provider']['requirement_coverage'] == 'complete_selected_effect_reference':
            coverage.append(item)
        proof['equipment'] = copy.deepcopy(item)
    else:
        provider = q['provider']
        set_id = source['subject']['target_id']
        data_version = gear.get('data_version') or {}
        need(data_version.get('version_line') == binding['version_line']
             and data_version.get('upstream_commit') == material['snapshot']['upstream_commit'],
             '装备版本与施放来源不是同一上游资料')
        evidence = equipment_evidence.get(actor, {})
        count = sum(r['count'] for r in gear.get('artifact_sets', []) if r['set_id'] == set_id)
        for slot, selected in gear.get('artifacts', {}).items():
            if selected is None or selected['state'] == 'empty':
                continue
            item = (evidence.get('artifacts', {}).get(slot) or {}).get('artifact') or {}
            need(item.get('id') == selected['artifact_id'] and item.get('equip_type') == slot,
                 '装备来源的实际圣遗物身份尚未核实')
            count += int(item.get('set_id') == set_id)
        need(count >= provider['required_count'], '提供者没有装备足够件数的来源套装')
        owner = {'kind': 'artifact_set', 'set_id': set_id,
                 'required_count': provider['required_count'], 'affix_id': provider['affix_id']}
        matches = [r for r in requirements if r['provider_character_id'] == actor and r['owner'] == owner]
        need(len(matches) == 1, '当前装备效果与受审来源不存在唯一关联')
        req = matches[0]
        effect = req.get('effect') or {}
        need(effect.get('affix_id') == provider['affix_id'] and effect.get('source_level') == provider['source_level'],
             '装备效果层级与受审资格不一致')
        # Keep full unknown effect requirements separate. Claim only this exact
        # reviewed effect; the set's other thresholds retain their own issues.
        item = {'provider': actor, 'owner': owner, 'count': count, 'source_level': provider['source_level'],
                'binding_id': req['binding_id'], 'requirement_fingerprint': req['source_fingerprint'],
                'parts': copy.deepcopy(q['duplicate_identity']['parts'])}
        if provider['requirement_coverage'] == 'complete_selected_effect_reference':
            coverage.append(item)
        proof['equipment'] = copy.deepcopy(item)
    return proof, coverage


def qualify_cast_provider(plan, template, binding, material, graph, equipment_evidence, requirements):
    proof, coverage = qualify_equipped_provider(plan, template, binding, material, equipment_evidence, requirements)
    cast = [{k: copy.deepcopy(row[k]) for k in ('ability_id', 'selection', 'identity_proof', 'base_resources')}
            for row in graph['dependencies']]
    identity = {'source_subject': copy.deepcopy(material['source_binding']['subject']),
                'provider_proof': proof, 'cast_identity': cast}
    if material['compiler_result']['execution_qualification']['parent']['profile_kind'] == 'successful_action_v2':
        identity['action_admission'] = [copy.deepcopy(row['action_admission'])
                                        for row in graph['dependencies']]
    return identity, coverage


def reconcile_equipment_coverage(records, requirements, issues):
    """Called only on fresh group success; never invent or mutate native bindings."""
    claims = {}
    for record in records:
        if record['status'] != 'ready':
            continue
        for claim in record.get('equipment_coverage', []):
            bid = claim['binding_id']
            req = _one(requirements, 'binding_id', bid, '被认领的装备效果')
            need(req['provider_character_id'] == claim['provider'] and req['owner'] == claim['owner']
                 and req['source_fingerprint'] == claim['requirement_fingerprint'] and bid not in claims,
                 '装备覆盖认领冲突或资料发生变化')
            claims[bid] = {'mechanism_source_id': record['id'], 'parts': copy.deepcopy(claim['parts']),
                           'coverage_fingerprint': fingerprint(claim)}
    for req in requirements:
        if req['binding_id'] in claims:
            req['mechanism_coverage'] = claims[req['binding_id']]
    issues[:] = [r for r in issues if not (r['code'] == 'effect_pending' and r.get('binding_id') in claims
                                          and r['path'] == 'effect_bindings.' + r['binding_id'])]
