"""Actual contact identity and equipped providers, without entity dispatch."""
from __future__ import annotations

import copy

from .energy_generation_sources import _one
from .mechanism_cast_graph import _merge
from .mechanism_cast_identity import verify_cast_identity
from .mechanism_execution_scope import identities, need
from .mechanism_parent_graph import SLOT_ACTION, _hit_allowed, _selection


def qualify_weapon(plan, binding, material, equipment_evidence, requirements):
    actor = binding['actor']
    gear = plan['equipment_inputs'][actor]
    q = material['compiler_result']['execution_qualification']
    provider, subject = q['provider'], material['source_binding']['subject']
    need(subject['target_type'] == 'weapon', '武器提供者需要武器资料主体')
    weapon = gear.get('weapon') or {}
    need(weapon.get('weapon_id') == subject['target_id'], '提供者没有装备所选来源武器')
    version = gear.get('data_version') or {}
    need(version.get('version_line') == binding['version_line']
         and version.get('upstream_commit') == material['snapshot']['upstream_commit'],
         '装备与命中来源不是同一上游资料')
    evidence = equipment_evidence.get(actor, {}).get('weapon') or {}
    actual = evidence.get('weapon') or {}
    need(actual.get('id') == subject['target_id'], '实际武器资料身份尚未核实')
    raw = _one(material['source_proof']['nodes'], 'id', provider['weapon_node_id'], '武器完整原节点')['payload']
    affix = _one(material['source_proof']['nodes'], 'id', provider['affix_node_id'], '武器效果完整原节点')['payload']
    need(raw.get('id') == subject['target_id'] and raw.get('weaponType') == actual.get('weapon_type'),
         '实际武器类型与完整来源不符')
    candidates = []
    for slot in evidence.get('effects', []):
        for effect in slot.get('refinements', []):
            if effect.get('affix_id') == provider['affix_id']:
                candidates.append((slot, effect))
    need(len(candidates) == 1, '受审武器效果在实际装备中没有唯一精炼槽')
    slot, effect = candidates[0]
    source_index = slot['source_index']
    affixes = raw.get('skillAffix') or []
    need(type(source_index) is int and 0 <= source_index < len(affixes)
         and affixes[source_index] == slot['affix_group_id'] == affix.get('id'),
         '武器精炼槽与完整效果节点关联不一致')
    selected = [r for r in weapon.get('refinements', [])
                if r.get('source_index') == source_index and r.get('affix_group_id') == slot['affix_group_id']]
    need(len(selected) == 1 and selected[0].get('refinement') == provider['refinement']
         and effect.get('refinement') == provider['refinement']
         and effect.get('source_level') == provider['source_level'], '实际武器精炼与所选受审来源不一致')
    owner = {'kind': 'weapon', 'weapon_id': subject['target_id'],
             'source_index': source_index, 'affix_id': provider['affix_id']}
    found = [r for r in requirements if r['provider_character_id'] == actor and r['owner'] == owner]
    need(len(found) == 1, '当前武器效果与受审来源不存在唯一关联')
    req = found[0]
    need(req.get('effect', {}).get('affix_id') == provider['affix_id']
         and req.get('effect', {}).get('source_level') == provider['source_level'], '武器效果要求的层级不一致')
    return {'provider': actor, 'owner': owner, 'refinement': provider['refinement'],
            'source_level': provider['source_level'], 'weapon_type': actual['weapon_type'],
            'binding_id': req['binding_id'], 'requirement_fingerprint': req['source_fingerprint'],
            'parts': copy.deepcopy(q['duplicate_identity']['parts'])}


def qualify_contact_provider(plan, template, binding, material, graph, equipment_evidence,
                             requirements, identity_provider):
    from .mechanism_cast_scope import qualify_equipped_provider

    q = material['compiler_result']['execution_qualification']
    need(q['schema_version'] == 3 and graph['status'] == 'ready', '命中资格或完整父关系尚未就绪')
    need('cast_binding' not in binding and binding.get('calibration') is None,
         '命中触发来源使用实际命中时点，不接受施放绑定或额外生效校准')
    actor = binding['actor']
    gear = plan['equipment_inputs'][actor]
    ref = gear.get('character_ref') or {}
    need(ref.get('version_line') == binding['version_line'] and type(ref.get('db_character_id')) is int,
         '命中提供者需要明确数据库角色引用')
    proof, coverage = qualify_equipped_provider(plan, template, binding, material, equipment_evidence, requirements)
    shared = _merge([*identities(plan, actor), {'character_id': ref['db_character_id'], 'version_line': binding['version_line']}],
                    ('character_id', 'version_line', 'skill_depot_id', 'constellation'), '命中提供者显式身份相互矛盾')
    profiles = {p['id']: p for p in plan.get('action_event_profiles', [])}
    abilities = {a['id']: a for a in plan['rotation_model']['abilities']}
    contacts = []
    for dependency in graph['dependencies']:
        profile = profiles[dependency['profile_id']]
        need(profile['kind'] in q['parent']['allowed_profile_kinds'], '父接触档案种类不在受审范围内')
        selected = _merge([dependency['selection'], shared],
                          ('character_id', 'skill_depot_id', 'skill_id', 'constellation'), '父攻击身份与实际角色矛盾')
        _selection(selected, q['parent']['selection'], profile, {'level': dependency['level']})
        request = {k: selected[k] for k in ('character_id', 'skill_depot_id', 'skill_id')}
        full = identity_provider(request, material['snapshot'])
        resolved = verify_cast_identity(full, request, material['snapshot'])
        need(set(resolved['matching_slots']) == set(dependency['selection']['matching_slots']),
             '完整角色技能节点与父攻击技能槽不一致')
        entries = dependency['parameter']['entry_sources']
        for name, node in full['nodes'].items():
            origin = entries.get(name) or {}
            definition = origin.get('definition') or {}
            need(all(origin.get(k) == node[k] for k in ('source_path', 'source_sha256', 'json_pointer'))
                 and all(definition.get(k) == node[k] for k in ('source_path', 'node_sha256', 'json_pointer')),
                 '父倍率与完整角色技能证明不是同一原节点')
        related = [p for p in graph['parents'] if p['profile_id'] == dependency['profile_id']
                   and p['channel_id'] == dependency['channel_id']]
        need(bool(related), '父攻击来源没有实际命中')
        for parent in related:
            ability = abilities[parent['ability_id']]
            need(ability['character'] == actor and ability['action_type_id'] in
                 [SLOT_ACTION[s] for s in resolved['matching_slots'] if s in SLOT_ACTION],
                 '实际动作类型与完整技能身份不一致')
            need(_hit_allowed(parent['prototype'], q['parent']['hit']), '相关命中超出当前受审派发子集')
        classification = dependency.get('damage_classification') or {}
        if q['parent']['callback_bridge']['hit_filter'].get('damage_type_ids'):
            need(classification.get('source_proven') is True,
                 '触发要求攻击类别，但父攻击标签尚无来源证明')
        if q['provider']['relation'] == 'equipped_weapon':
            need(full['nodes']['avatar']['raw'].get('weaponType') == proof['equipment']['weapon_type'],
                 '实际角色与提供者武器类型不匹配')
        # Stable semantic acceptance excludes absolute axis positions and
        # instance fingerprints. Full graph still binds every execution child.
        contacts.append({k: copy.deepcopy(dependency[k]) for k in (
            'profile_id', 'profile_kind', 'channel_id', 'prototype_hit_id', 'profile_fingerprint',
            'source_dependency_fingerprint', 'selection', 'level', 'parameter',
            'metadata_source_fingerprint', 'metadata_dependency_fingerprint',
            'periodic_source_fingerprint', 'periodic_dependency_fingerprint')}
            | {'identity_proof': full, 'damage_classification': copy.deepcopy(classification)})
    need(bool(contacts), '来源没有可核实的实际接触')
    return {'source_subject': copy.deepcopy(material['source_binding']['subject']),
            'provider_proof': proof, 'contact_identity': contacts}, coverage
