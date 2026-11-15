"""Actual casts qualified by full identity records, independent of hit delivery."""
from __future__ import annotations

import copy
import math

from genshin_db.runtime_action_resource import ELEMENT_NAMES, _resource_projection

from .energy_generation_sources import _one, _ticks
from .files import fingerprint
from .mechanism_cast_identity import verify_cast_identity
from .mechanism_execution_scope import identities, need
from .mechanism_parent_graph import MAX_PARENTS, SLOT_ACTION, _snapshot_equal

ACTION_ADMISSION_PROFILE = 'successful_action_v2'
ACTION_ADMISSION_COMPILER = 'reviewed-action-admission-compiler-v1'
ACTION_ADMISSION_SOURCE_CONTRACT = 'static-action-admission-source-v1'
STATIC_CERTIFICATE_CONTRACT = 'static-action-admission-certificate-v1'


def _merge(rows, keys, message):
    out = {}
    for row in rows:
        for key in keys:
            if key in row:
                value = row[key]
                need(key not in out or type(out[key]) is type(value) and out[key] == value, message + '：' + key)
                out[key] = value
    return out


def _local_evidence(plan, ability, *, resources, parameters, metadata, actions):
    """Only fresh compilations linked to this ability supply skill-level identity."""
    actor, aid = ability['character'], ability['id']
    rows, receipts = [], []
    for r in resources:
        if r.get('ability_id') == aid:
            receipts.append(r)
            rows.append({**r.get('resolved_selection', {}), **r.get('selection', {})})
    profile_ids = {b['profile_id'] for b in plan.get('action_event_bindings', []) if b['ability_id'] == aid}
    profiles = [p for p in plan.get('action_event_profiles', []) if p['id'] in profile_ids]
    for profile in profiles:
        action = _one(actions, 'profile_id', profile['id'], '关联动作派发回执')
        need(action.get('status') == 'ready' and action.get('actor') == actor
             and action.get('profile_fingerprint') == profile.get('accepted_profile_fingerprint'),
             '关联动作档案尚未接受或未就绪')
        for r in parameters:
            if profile.get('prototype_hit_id') is not None and r.get('hit_id') == profile['prototype_hit_id']:
                receipts.append(r)
                row = copy.deepcopy(r.get('resolved_selection', {}))
                if (r.get('level') or {}).get('effective_level') is not None:
                    row['talent_level'] = r['level']['effective_level']
                rows.append(row)
        for r in metadata:
            if r.get('action_event_profile_id') == profile['id']:
                receipts.append(r)
                rows.append(copy.deepcopy((r.get('query') or {}).get('selection', {})))
    return rows, receipts


def _burst_resources(plan, ability, proof, resource):
    """Recheck executor values; missing upstream charges never default to one."""
    raw = proof['nodes']['skill']['raw']
    record = {**proof['nodes']['skill'], 'fields': raw,
              'field_pointers': {k: proof['nodes']['skill']['json_pointer'] + '/' + k for k in raw}}
    projected = {name: _resource_projection(record, name, field) for name, field in (
        ('base_cooldown_seconds', 'cdTime'), ('base_charge_count', 'maxChargeNum'),
        ('base_energy_cost', 'costElemVal'), ('cost_element', 'costElemType'))}
    need(all(r['status'] == 'ready' for r in projected.values()), '施放爆发缺少明确的基础资源')
    cost = projected['base_energy_cost']['value']
    charges = projected['base_charge_count']['value']
    cd, energy = ability.get('cooldown') or {}, ability.get('energy') or {}
    need(charges == 1 and cost > 0 and cd.get('duration') == projected['base_cooldown_seconds']['value']
         and cd.get('charges') == charges and all(energy.get(k) == cost for k in ('required', 'amount')),
         '施放爆发的实际冷却、次数或耗能不符合基础资源范围')
    actor = _one(plan['rotation_model']['characters'], 'id', ability['character'], '施放角色')
    need(actor.get('element') == ELEMENT_NAMES[raw['costElemType']], '施放角色的能量元素与技能不一致')
    if resource:
        need(resource.get('compiled_values') is not None, '基础资源回执缺少实际交付')
    if energy.get('delivery') != 'action_start':
        for action in plan['rotation_model']['actions']:
            if action['ability_id'] != ability['id']:
                continue
            spends = [e for e in plan['rotation_model']['events'] if e.get('action_id') == action['id']
                      and e.get('type') == 'energy_spend']
            need(len(spends) == 1 and spends[0].get('character') == ability['character']
                 and all(spends[0].get(k) == cost for k in ('required', 'amount')),
                 '成功施放资格需要实际动作扣能关联')
    return projected


def _declared_skill_resources(ability):
    """V2 admits only explicit positive skill CD; this is not a game-data proof."""
    cd = ability.get('cooldown')
    need(type(cd) is dict and type(cd.get('duration')) in (int, float)
         and math.isfinite(cd['duration']) and cd['duration'] > 0
         and type(cd.get('charges')) is int and cd['charges'] > 0
         and isinstance(cd.get('group'), str) and bool(cd['group'].strip())
         and cd.get('start') in ('action_start', 'occupation_end')
         and cd.get('recovery') in ('sequential', 'parallel'), '成功施放战技需要明确的正冷却与次数规则')
    need(ability.get('status') in ('assumed', 'confirmed')
         and isinstance(ability.get('source'), str) and bool(ability['source'].strip()),
         '战技冷却声明需要状态与依据')
    return {'verification_scope': 'declared_not_verified', 'cooldown': copy.deepcopy(cd),
            'status': ability['status'], 'source': ability['source']}


def _admission_dependency(plan, record, ability, linked, actual, snapshot):
    """Bind one fresh App compilation to the exact ability and static certificate."""
    need(record.get('compiler') == ACTION_ADMISSION_COMPILER
         and record.get('status') == 'ready' and record.get('ability_id') == ability['id']
         and record.get('actor') == ability['character'], '动作接纳编译回执未就绪或能力身份不一致')
    need(record.get('action_type_id') == ability['action_type_id']
         and record.get('variant') == ability.get('variant'), '动作接纳动作类型或变体不一致')
    profile = record.get('profile') or {}
    need(profile.get('status') == 'ready' and _snapshot_equal(profile.get('snapshot', {}), snapshot),
         '动作接纳与机制资料不是同一新鲜快照')
    selected = record.get('selection') or {}
    need(profile.get('selection') == selected and profile.get('variant') == record.get('variant')
         and profile.get('action_type_id') == record.get('action_type_id')
         and profile.get('model_ref') == record.get('model_ref')
         and profile.get('compiled_values') == {k: copy.deepcopy((record.get('compiled_values') or {}).get(k))
                                                 for k in ('cooldown', 'energy')},
         '动作接纳资料与编译选择或门禁值不一致')
    for key in ('character_id', 'skill_depot_id', 'skill_id', 'constellation'):
        need(key in selected and type(actual.get(key)) is type(selected[key]) and actual[key] == selected[key],
             '动作接纳选择与实际施放身份不一致：' + key)
    need(selected.get('slot') in actual.get('matching_slots', []), '动作接纳技能槽与实际施放身份不一致')
    bindings = [row for row in plan.get('action_admission_sources', []) if row.get('id') == record.get('id')]
    need(len(bindings) == 1, '动作接纳编译回执没有唯一原始绑定')
    binding = bindings[0]
    need(binding.get('ability_id') == ability['id'] and binding.get('selection') == selected
         and binding.get('variant') == record.get('variant')
         and binding.get('model_ref') == record.get('model_ref')
         and binding.get('version_line') == snapshot.get('version_line'),
         '动作接纳原始绑定与当前能力或快照不一致')
    action_ids = sorted(row['id'] for row in linked)
    group = (ability.get('cooldown') or {}).get('group')
    initial = [row for row in plan['rotation_model'].get('initial_cooldowns', [])
               if row.get('character') == ability['character'] and row.get('group') == group]
    need(len(initial) == 1, '动作接纳受管冷却组必须且只能有一条初态')
    context = plan.get('action_admission_context')
    scope = {'compiler': ACTION_ADMISSION_COMPILER,
             'binding': {key: copy.deepcopy(value) for key, value in binding.items()
                         if key != 'accepted_fingerprint'},
             'ability': {key: copy.deepcopy(ability.get(key)) for key in
                         ('id', 'character', 'action_type_id', 'variant', 'status', 'source')},
             'action_ids': action_ids, 'initial_cooldown': copy.deepcopy(initial[0]),
             'context': copy.deepcopy(context), 'aliases': [], 'capacity_dependencies': [],
             'profile_source_fingerprint': profile.get('source_fingerprint')}
    scope_fingerprint = fingerprint(scope)
    source_fingerprint = fingerprint({'profile_source_fingerprint': profile.get('source_fingerprint'),
                                      'scope_fingerprint': scope_fingerprint})
    need(record.get('scope_fingerprint') == scope_fingerprint
         and record.get('source_fingerprint') == source_fingerprint
         and record.get('required_confirmation') == {'accepted_fingerprint': source_fingerprint}
         and binding.get('accepted_fingerprint') == source_fingerprint,
         '动作接纳编译范围、来源或接受状态与当前方案不一致')
    marker = ability.get('action_admission_source')
    compiled = record.get('compiled_values') or {}
    need(type(marker) is dict and marker == compiled.get('action_admission_source')
         and marker.get('contract') == ACTION_ADMISSION_SOURCE_CONTRACT,
         '能力没有由当前动作接纳编译器交付唯一私有标记')
    certificate = record.get('static_certificate')
    need(type(certificate) is dict and certificate.get('contract') == STATIC_CERTIFICATE_CONTRACT,
         '动作接纳静态证书缺失或合同不受支持')
    static_fingerprint = certificate.get('static_certificate_fingerprint')
    unsigned = copy.deepcopy(certificate)
    unsigned.pop('static_certificate_fingerprint', None)
    need(static_fingerprint == fingerprint(unsigned)
         and marker.get('static_certificate_fingerprint') == static_fingerprint,
         '动作接纳静态证书指纹不一致')
    need(certificate.get('ability_id') == ability['id']
         and sorted(certificate.get('action_ids') or []) == action_ids
         and certificate.get('binding_id') == record.get('id') == marker.get('binding_id')
         and certificate.get('snapshot_id') == snapshot.get('snapshot_id')
         and certificate.get('scope_fingerprint') == scope_fingerprint
         and certificate.get('initial_cooldown') == initial[0]
         and certificate.get('context') == record.get('context') == context
         and certificate.get('aliases') == certificate.get('capacity_dependencies') == []
         and certificate.get('selection') == selected
         and certificate.get('variant') == ability.get('variant')
         and certificate.get('action_type_id') == ability['action_type_id']
         and certificate.get('model_ref') == record.get('model_ref') == marker.get('model_ref')
         and certificate.get('model_receipt') == profile.get('model_receipt')
         and certificate.get('resource_fingerprint') == (profile.get('resource_profile') or {}).get('resource_fingerprint')
         and certificate.get('field_authority') == profile.get('field_authority')
         and certificate.get('declared_not_verified') == profile.get('declared_not_verified')
         and certificate.get('source_fingerprint') == source_fingerprint == marker.get('source_fingerprint'),
         '动作接纳静态证书未覆盖当前能力、动作或来源')
    need(compiled.get('cooldown') == ability.get('cooldown')
         and compiled.get('energy') == ability.get('energy')
         and certificate.get('compiled_values') == {k: copy.deepcopy(compiled[k]) for k in ('cooldown', 'energy')},
         '动作接纳门禁值与实际执行能力不一致')
    need(type(record.get('compilation_fingerprint')) is str
         and record['compilation_fingerprint'] == fingerprint({k: v for k, v in record.items()
             if k != 'compilation_fingerprint'}), '动作接纳编译回执指纹不一致')
    return {'compilation_id': record['id'], 'compilation_fingerprint': record['compilation_fingerprint'],
            'source_fingerprint': record['source_fingerprint'],
            'static_certificate_fingerprint': static_fingerprint,
            'model_ref': copy.deepcopy(record['model_ref']), 'action_ids': action_ids}


def build_mechanism_cast_graph(plan, template, *, binding, material, identity_provider,
                               resource_records=(), action_admission_records=(), action_records=(),
                               parameter_records=(), metadata_records=()):
    """No input mutation; exceptions leave the whole mechanism group pending."""
    q = material['compiler_result']['execution_qualification']
    profile_kind = q['parent']['profile_kind']
    need(q['schema_version'] == 2 and profile_kind in ('successful_action_v1', ACTION_ADMISSION_PROFILE),
         '施放资格版本不匹配')
    cast = binding.get('cast_binding')
    need(type(cast) is dict and bool(cast.get('ability_ids')), '施放来源需关联已有能力')
    actor = binding['actor']
    gear = plan['equipment_inputs'].get(actor, {})
    ref = gear.get('character_ref') or {}
    need(ref.get('version_line') == binding['version_line'] and type(ref.get('db_character_id')) is int,
         '施放提供者需要明确数据库角色引用')
    _one(template['characters'], 'id', actor, '施放提供者')
    rotation = plan['rotation_model']
    abilities = {r['id']: r for r in rotation['abilities']}
    selected = cast['ability_ids']
    need(len(selected) <= 64 and len(set(selected)) == len(selected), '施放能力关联重复或超过限额')
    actor_rows = identities(plan, actor)
    actor_rows.extend({'version_line': other['version_line'], **(other['cast_binding'].get('selection') or {})}
                      for other in plan.get('mechanism_sources', [])
                      if other.get('actor') == actor and other.get('cast_binding'))
    shared = _merge([*actor_rows, cast.get('selection') or {},
                     {'character_id': ref['db_character_id'], 'version_line': binding['version_line']}],
                    ('character_id', 'version_line', 'skill_depot_id', 'constellation'), '施放角色显式身份相互矛盾')
    scope = q['parent']['selection']
    source_selection = material['source_binding']['selection']
    need(type(shared.get('constellation')) is int and shared['constellation'] == scope['constellation'],
         '实际命座缺少声明或不在受审范围内')
    proofs, parents, aliases = [], [], {}
    horizon = _ticks(rotation['period'])
    for aid in selected:
        ab = abilities.get(aid)
        need(ab is not None and ab['character'] == actor and ab['action_type_id'] == SLOT_ACTION[scope['slot']]
             and isinstance(ab.get('variant'), str) and bool(ab['variant'].strip()), '施放能力的角色、类型或变体不明确')
        local, receipts = _local_evidence(plan, ab, resources=resource_records, parameters=parameter_records,
                                           metadata=metadata_records, actions=action_records)
        for r in receipts:
            need(r.get('status') == 'ready' and r.get('actor') == actor
                 and _snapshot_equal(r.get('snapshot', {}), material['snapshot']), '施放来源关联回执未就绪或快照不一致')
        actual = _merge([*local, cast.get('selection') or {}, shared],
                        ('character_id', 'skill_depot_id', 'skill_id', 'constellation', 'talent_level'),
                        '施放能力显式选择矛盾')
        for key in ('skill_depot_id', 'skill_id'):
            if key in source_selection:
                need(key not in actual or actual[key] == source_selection[key], '施放技能与来源受审范围矛盾')
                actual[key] = source_selection[key]
        request = {k: actual[k] for k in ('character_id', 'skill_depot_id', 'skill_id') if k in actual}
        if 'skill_id' not in request:
            request['slot'] = scope['slot']
        proof = identity_provider(request, material['snapshot'])
        resolved = verify_cast_identity(proof, request, material['snapshot'])
        actual.update(resolved)
        actual['variant'] = ab['variant']
        for key in ('character_id', 'skill_depot_id', 'skill_id', 'constellation', 'talent_level', 'slot', 'variant'):
            if key not in source_selection:
                continue
            expected = source_selection[key]
            need(expected in actual['matching_slots'] if key == 'slot' else
                 type(actual.get(key)) is type(expected) and actual[key] == expected,
                 '施放实际选择与来源范围不一致：' + key)
        for key, value in scope.items():
            need(value in actual['matching_slots'] if key == 'slot' else
                 type(actual.get(key)) is type(value) and actual[key] == value,
                 '施放技能选择不符合受审资格：' + key)
        linked = [a for a in rotation['actions'] if a['ability_id'] == aid]
        need(bool(linked), '施放来源关联的能力没有实际动作')
        resource = next((r for r in resource_records if r.get('ability_id') == aid), None)
        resources = (_burst_resources(plan, ab, proof, resource) if scope['slot'] == 'burst'
                     else _declared_skill_resources(ab))
        previous = aliases.setdefault(actual['skill_id'], copy.deepcopy(ab.get('cooldown')))
        need(previous == ab.get('cooldown'), '同技能能力别名不能拆分冷却组或规则')
        for action in linked:
            start, end = _ticks(action['start']), _ticks(action['occupation_end'])
            need(action['repeat'] == {'mode': 'once'} and start <= end and start < horizon,
                 '施放来源需要窗口内明确的单次动作')
            need(len(parents) < MAX_PARENTS, '施放父关系超过限额')
            parents.append({'action_id': action['id'], 'ability_id': aid, 'action_tick': start,
                            'occupation_end_tick': end, 'action_order': action['order']})
        dependency = {'ability_id': aid, 'selection': actual, 'identity_proof': proof,
                      'base_resources': resources}
        if profile_kind == ACTION_ADMISSION_PROFILE:
            matches = [r for r in action_admission_records if r.get('ability_id') == aid]
            need(len(matches) == 1 and resource is None,
                 '接纳施放资格需要唯一动作接纳编译回执，且不能借用旧动作资源回执')
            dependency['action_admission'] = _admission_dependency(
                plan, matches[0], ab, linked, actual, material['snapshot'])
        proofs.append(dependency)
    relevant = {a['id'] for a in rotation['actions'] if abilities[a['ability_id']]['character'] == actor
                and abilities[a['ability_id']]['action_type_id'] in q['coverage']['action_type_ids']}
    need(relevant == {p['action_id'] for p in parents}, '整轴存在未覆盖的相关施放或另一变体')
    result = {'status': 'ready',
              'verification_scope': ('cast_and_action_admission_graph'
                                     if profile_kind == ACTION_ADMISSION_PROFILE else 'cast_graph_only'),
              'execution_status': 'not_evaluated',
              'actor': actor, 'target_id': binding['scenario']['target_id'],
              'parents': sorted(parents, key=lambda r: (r['action_tick'], r['action_order'], r['action_id'])),
              'dependencies': sorted(proofs, key=lambda r: r['ability_id']), 'issues': []}
    result['graph_fingerprint'] = fingerprint(result)
    return result
