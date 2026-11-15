"""Axis qualification for typed mechanisms; entity identifiers never select code."""
from __future__ import annotations

import copy
import math

from genshin_engine.beneficiary_selection import possible_recipients
from genshin_engine.elemental import validate_elemental_model
from genshin_engine.elemental_schema import FREEZE_RULESET
from genshin_engine.registry import BENEFIT_TYPES

from .energy_generation_sources import _one, _Pending, _ticks
from .files import fingerprint


def need(condition, message):
    if not condition:
        raise _Pending(message)


def identities(plan, actor):
    hits = {h['id']: h for h in plan['manual_template']['hits']}
    abilities = {a['id']: a for a in plan['rotation_model']['abilities']}
    profiles = {p['id']: p for p in plan.get('action_event_profiles', [])}
    rows = []
    for field in ('hit_parameter_sources', 'action_resource_sources', 'action_admission_sources',
                  'attack_metadata_sources'):
        for row in plan.get(field, []):
            owner = (hits.get(row.get('hit_id'), {}).get('character') if field == 'hit_parameter_sources' else
                     abilities.get(row.get('ability_id'), {}).get('character')
                     if field in ('action_resource_sources', 'action_admission_sources') else
                     hits.get(profiles.get(row.get('action_event_profile_id'), {}).get('prototype_hit_id'), {}).get('character'))
            if owner == actor:
                rows.append({'origin': field, 'version_line': row['version_line'], **row['selection']})
    for row in plan.get('effect_bindings', []):
        if row['provider_character_id'] == actor and row['owner']['kind'] == 'character_skill':
            rows.append({'origin': 'effect_bindings', **row['owner']})
    for row in plan.get('passive_sources', []):
        if row['actor'] == actor:
            rows.append({k: row[k] for k in ('constellation', 'skill_depot_id') if k in row})
    return rows


def qualify_identity(plan, template, binding, material, graph):
    actor, selected = binding['actor'], material['source_binding']['selection']
    gear = plan['equipment_inputs'].get(actor, {})
    character = _one(template['characters'], 'id', actor, '机制提供者')
    rows = identities(plan, actor)
    expected = {k: selected[k] for k in ('character_id', 'skill_depot_id', 'constellation') if k in selected}
    expected['version_line'] = binding['version_line']
    for row in rows:
        need(all(type(row[k]) is type(v) and row[k] == v for k, v in expected.items() if k in row),
             '显式来源角色、资料线、命座或技能组互相矛盾')
    ref = gear.get('character_ref')
    if ref:
        need(ref['db_character_id'] == selected['character_id'] and ref['version_line'] == binding['version_line'],
             '装备角色引用与受审机制主体矛盾')
    # Parent qualification checks explicit C and skill scope without confusing a
    # passive proud record with its triggering skill's parameter record.
    qselection = material['compiler_result']['execution_qualification']['parent']['selection']
    for key in ('constellation', 'skill_id', 'talent_level', 'slot', 'variant'):
        if key in selected:
            need(qselection.get(key) == selected[key], '来源资格与父技能选择不一致：' + key)
    if 'character_level' in selected:
        need(character['level'] == selected['character_level'], '实际角色等级不符合受审资格')
    if 'ascension' in selected:
        progression = gear.get('progression') or {}
        need(progression.get('ascension') == selected['ascension']
             and progression.get('status') in ('assumed', 'confirmed') and bool(progression.get('source', '').strip()),
             '角色突破缺少明确声明或与来源不一致')
    return {'subject': copy.deepcopy(material['source_binding']['subject']),
            'selection': copy.deepcopy(selected), 'explicit_sources': rows,
            'character_ref': copy.deepcopy(ref), 'level': character['level'],
            'progression': copy.deepcopy(gear.get('progression')), 'parent_dependencies': graph['dependencies']}


def qualify_scene(plan, binding, qualification):
    scene = binding['scenario']
    need(scene['status'] in ('assumed', 'confirmed') and bool(scene['source'].strip()), '机制场景尚待明确依据')
    for key, value in qualification['scenario_requirements'].items():
        actual = scene['values'].get(key)
        need(type(actual) is type(value) and actual == value, '场景条件未满足：' + key)
    model = plan['mechanics_model']
    elemental = model.get('elemental_model') or {}
    if qualification.get('schema_version') == 2 and qualification['target_dependency']['mode'] == 'none':
        need(scene['target_id'] is None, '无目标依赖的来源不应指定目标')
        need('elemental_model' not in model or model['elemental_model'] is None
             or not validate_elemental_model(model['elemental_model']), '显式元素模型仍需有效')
        return copy.deepcopy(scene)
    need(elemental.get('ruleset') == FREEZE_RULESET and not validate_elemental_model(elemental),
         '机制需要已核对的普通冻结参考模型')
    target = (elemental.get('target') or {}).get('id')
    need(target is not None and scene['target_id'] == target, '机制目标与实际元素模型不一致')
    targets = {r['application'].get('target_id') for field in ('hit_events', 'hit_patterns', 'application_events')
               for r in model.get(field, []) if r.get('application')}
    need(not targets - {target}, '当前受审机制仅支持单一目标')
    return copy.deepcopy(scene)


def qualify_activation(plan, binding, graph, projection):
    if not any(r['rule']['trigger']['event'] == 'action_start' for r in projection['rule_templates']):
        return {'delay': 0, 'parents': copy.deepcopy(graph['parents'])}
    calibration = binding.get('calibration') or {}
    qualification = projection['execution_qualification']
    cast = qualification.get('schema_version') == 2
    if cast and qualification['parent']['activation']['offset_policy'] == 'fixed_zero':
        need(binding.get('calibration') is None, '固定零延迟来源不接受额外生效校准')
        return {'delay': 0, 'parents': copy.deepcopy(graph['parents']), 'policy': 'fixed_zero'}
    need(calibration.get('status') in ('assumed', 'confirmed') and bool(calibration.get('source', '').strip()),
         '动作机制需要明确生效校准')
    delay = _ticks(calibration.get('activation_offset_seconds'))
    precision = _ticks(calibration.get('precision_seconds'))
    need(delay >= 0 and precision > 0, '生效时点及精度必须为有效微秒')
    rotation = plan['rotation_model']
    abilities = {a['id']: a for a in rotation['abilities']}
    links = copy.deepcopy(graph['parents'])
    for parent in links:
        start = parent['action_tick']
        activation = start + delay
        need(start <= activation <= parent['occupation_end_tick'] and activation < _ticks(rotation['period']),
             '机制生效时点超出动作占用或计算窗口')
        if not cast:
            hit = parent['absolute_tick']
            need(activation < hit or activation == hit and (delay == 0 or parent['action_order'] < parent['order']),
                 '当前队列顺序不能证明收益先于初始命中')
        for action in rotation['actions']:
            if action['id'] == parent['action_id']:
                continue
            ability = abilities[action['ability_id']]
            tick = _ticks(action['start'])
            cancels = ability['action_type_id'] == 'action.switch' or ability['character'] == binding['actor']
            if cancels and start <= tick <= activation:
                # Equal ticks have an explicit order; delayed children may run
                # after an equal-order plain event, so equality is conservative.
                after_start = tick > start or action['order'] > parent['action_order']
                before_activation = tick < activation or action['order'] <= parent['action_order']
                need(not (after_start and before_activation), '实际轴在收益生效前有切人或动作取消')
        parent['activation_tick'] = activation
    return {'delay': delay / 1_000_000, 'parents': links, 'calibration': copy.deepcopy(calibration)}


def _receiver(rule, actor):
    return actor in possible_recipients(rule, [actor])


def stat_scope(plan, template, actor, rules, projection):
    """Match D20 ordinary reads, scanning the entire combined candidate model."""
    reads = sorted({e['value']['stat'] for r in projection['rule_templates'] for e in r['rule']['effects']
                    if isinstance(e['value'], dict)})
    fixed = 'fixed_nonextra_em_equality' in projection['required_execution_checks']
    no_em_writes = fixed or 'no_dynamic_em' in projection['required_execution_checks']
    if no_em_writes and 'em' not in reads:
        reads.append('em')
    gear = plan['equipment_inputs'].get(actor, {})
    stats = _one(template['characters'], 'id', actor, '机制提供者面板')['stats']
    if fixed:
        need(gear.get('mode') == 'panel' and gear.get('panel_policy') == 'static_included',
             'NONEXTRA相等口径需要已含常驻属性的明确固定面板')
    matches = {tuple(sorted(r['effect_ref'].items())) for r in plan.get('rotation_tuning', {}).get('buff_hit_matches', [])}
    checked = []
    for stat in reads:
        value = stats.get(stat)
        need(type(value) in (int, float) and math.isfinite(value) and value >= 0, '缺少有限非负提供者属性：' + stat)
        paths = []
        for buff in template.get('buffs', []):
            if buff.get('target') not in (None, actor):
                continue
            for effect in buff['effects']:
                writes = (effect['zone'] == 'em' and stat == 'em'
                          or effect['zone'] in ('stat_add', 'stat_pct') and effect.get('stat') == stat)
                ref = tuple(sorted({'buff_id': buff['id'], 'effect_id': effect['id']}.items()))
                if writes and (no_em_writes and stat == 'em' or effect.get('applies_to', {}).get('mode', 'all') != 'all' or ref in matches):
                    paths.append('buffs.' + buff['id'] + '.' + effect['id'])
        for rule in rules:
            if not _receiver(rule, actor):
                continue
            for effect in rule['effects']:
                info = BENEFIT_TYPES.get(effect['benefit_type_id'], {})
                writes = (info.get('zone') == 'em' and stat == 'em'
                          or info.get('zone') in ('stat_add', 'stat_pct') and info.get('stat') == stat)
                if writes and (no_em_writes and stat == 'em' or isinstance(effect['value'], dict) or 'filter' in effect or rule.get('quota')):
                    paths.append('rules.' + rule['id'] + '.' + effect['id'])
                if writes and info.get('zone') == 'stat_pct':
                    need('base_' + stat in stats, '百分比属性贡献缺少基础面板：' + stat)
        if no_em_writes and stat == 'em':
            for row in plan.get('effect_bindings', []):
                for part in row['parts']:
                    delivery = part.get('delivery') or {}
                    if (part['state'] != 'excluded' and (part.get('rule') or {}).get('kind') == 'stat_scale'
                            and delivery.get('kind') == 'panel' and delivery.get('stat') == stat
                            and delivery.get('target_character_id') == actor):
                        paths.append('effect_bindings.' + row['binding_id'] + '.' + part['part_id'])
        need(not paths, '提供者属性存在不能完整取样的贡献或固定NONEXTRA写入：' + ', '.join(paths))
        checked.append({'stat': stat, 'base_value': value, 'fixed_nonextra': fixed and stat == 'em'})
    return checked


def _possible(filter_, hit, row, ability):
    if filter_.get('event') not in (None, 'hit'):
        return False
    values = {'elements': [hit.get('element')], 'damage_type_ids': hit.get('damage_type_ids'),
              'characters': [hit.get('character')], 'action_ids': [row.get('action_id')],
              'action_type_ids': [ability.get('action_type_id')]}
    for key, known in values.items():
        known = [x for x in known or [] if x is not None]
        if key in filter_ and known and not set(known) & set(filter_[key]):
            return False
    return True  # Unknown metadata or derived event IDs cannot prove irrelevance.


def consumer_scope(plan, template, qualification, rules):
    expected = qualification['consumer']
    if expected is None:
        return []
    need(not any(e.get('filter', {}).get('event') == 'reaction_damage' for r in rules for e in r['effects']),
         '消费者资格尚不能核验引擎派生反应伤害；未按普通命中替代')
    hits = {h['id']: h for h in template['hits']}
    actions = {a['id']: a for a in plan['rotation_model']['actions']}
    abilities = {a['id']: a for a in plan['rotation_model']['abilities']}
    selectors = [r['quota']['consume'] for r in rules if r.get('quota')]
    selectors.extend(e.get('filter', {}) for r in rules for e in r['effects'])
    consumers = []
    for field in ('hit_events', 'hit_patterns'):
        for row in plan['mechanics_model'].get(field, []):
            hit = hits[row['hit_id'] if field == 'hit_events' else row['prototype_hit_id']]
            ability = abilities.get(actions.get(row.get('action_id'), {}).get('ability_id'), {})
            if not any(_possible(s, hit, row, ability) for s in selectors):
                continue
            tags = hit.get('damage_type_ids') or []
            need(hit['damage_system'] in expected['damage_systems'] and type(hit.get('count', 1)) is int
                 and hit.get('count', 1) == expected['count'] and bool(tags) and set(tags) <= set(expected['damage_type_ids']),
                 '潜在受益命中超出受审伤害系统、单次次数或伤害种类：' + hit['id'])
            consumers.append({'kind': field, 'hit_id': hit['id'], 'character': hit['character'],
                              'descriptor': copy.deepcopy(row), 'hit_fingerprint': fingerprint(hit)})
    return consumers


def duplicate_scope(plan, template, records):
    seen = []
    for record in records:
        material = record['source_material']
        q = record['projection']['execution_qualification']['duplicate_identity']
        # A data branch identifies evidence, not a second in-game stacking
        # domain. Cross-branch providers still compete for the same source.
        identity = (q['kind'], q['source_id'])
        for old_identity, parts in seen:
            need(identity != old_identity or not set(q['parts']) & parts, '同一真实来源重复或存在跨提供者竞争')
        seen.append((identity, set(q['parts'])))
        actor = record['actor']
        for old in plan.get('passive_sources', []):
            need(not (q['kind'] == 'proud' and old['proud_skill_id'] == q['source_id']), '旧固有来源与通用来源重复')
        if q['kind'] == 'skill':
            profiles = {p['id']: p for p in plan.get('action_event_profiles', [])}
            hits = {h['id']: h for h in template['hits']}
            for old in plan.get('limited_hit_benefit_sources', []):
                profile = profiles.get(old.get('action_event_profile_id'), {})
                owner = hits.get(profile.get('prototype_hit_id'), {}).get('character')
                # Legacy finite-add bindings have no source ID of their own;
                # their typed parent actor and overlapping benefit prove risk.
                need(not (owner == actor and 'damage.limited_hit_base_add' in q['parts']), '旧有限次数来源与通用来源重复')
        for old in plan.get('effect_bindings', []):
            owner = old['owner']
            same_subject = owner.get('character_id') == material['source_binding']['subject']['target_id']
            if material['source_binding']['subject'].get('target_type') == 'artifact_set':
                same_subject = owner.get('kind') == 'artifact_set' and owner.get('set_id') == material['source_binding']['subject']['target_id']
            if material['source_binding']['subject'].get('target_type') == 'weapon':
                same_subject = owner.get('kind') == 'weapon' and owner.get('weapon_id') == material['source_binding']['subject']['target_id']
            refs = old.get('source_refs', [])
            same_ref = any((r.get('proud_skill_id') == q['source_id'] or (r.get('key') or {}).get('proudSkillId') == q['source_id'])
                           for r in refs) if q['kind'] == 'proud' else False
            provider = record['projection']['execution_qualification'].get('provider') or {}
            if q['kind'] in ('artifact_set', 'weapon') and provider.get('affix_id') is not None:
                affix = provider['affix_id']
                same_ref = owner.get('affix_id') == affix or any(
                    r.get('affix_id') == affix or (r.get('key') or {}).get('affixId') == affix for r in refs)
            new_benefits = {e['benefit_type_id'] for r in record['rules'] for e in r['effects']}
            existing_rules = {r['id']: r for r in plan['mechanics_model']['rules']}
            overlap = False
            for part in old['parts']:
                if part['state'] == 'excluded':
                    continue
                old_rule = existing_rules.get((part.get('rule') or {}).get('rule_id'), {})
                benefits = {e['benefit_type_id'] for e in old_rule.get('effects', [])}
                benefits.add(((part.get('delivery') or {}).get('effect') or {}).get('benefit_type_id'))
                overlap |= part['part_id'] in q['parts'] or bool(new_benefits & benefits)
            need(not (same_ref or same_subject and old['provider_character_id'] == actor and overlap),
                 '已标注的手工或装备来源可能重复；未自动删除')
        for buff in template.get('buffs', []):
            ext = buff.get('extensions') or {}
            refs = ext.get('source_refs', [])
            same = q['kind'] == 'proud' and any(r.get('proud_skill_id') == q['source_id']
                     or (r.get('key') or {}).get('proudSkillId') == q['source_id'] for r in refs)
            need(not same and not ext.get('source_id', '').startswith(record['id'] + ':'), '已标注手工增益与机制来源重复')
