"""Actual input scope for a source-bound ordinary reaction EM projection.

This module does not infer event ownership from an action or grant execution from
a scenario flag. The executor still creates and filters actual reactions.
"""
from __future__ import annotations

import copy

from genshin_engine.elemental import validate_elemental_model
from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET
from genshin_engine.runtime_reaction_em_compiler import PROFILE_ID

from .files import context_view, fingerprint
from .reaction_benefits import _affix

CERTIFICATE_KIND = 'reaction_flat_em_projection_certificate'
_ACKS = {'accepted_assumptions_fingerprint', 'accepted_fingerprint',
         'coverage_certificate', 'attribute_source_adoption'}


def semantic_scope(value):
    """Keep semantics and identities, excluding recursively derived approvals."""
    def strip(item):
        if isinstance(item, dict):
            return {k: strip(v) for k, v in item.items() if k not in _ACKS}
        if isinstance(item, list):
            return [strip(v) for v in item]
        return item
    return strip(context_view(value))


def source_scope(plan, binding, requirement, *, inactive_binding_ids=()):
    """Early equipment/source identity checks, before all compilers have run."""
    actor, owner = binding['provider_character_id'], requirement['owner']
    gear = plan['equipment_inputs'].get(actor, {})
    issues = []

    def issue(code, path, message):
        issues.append({'code': code, 'path': path, 'message': message})

    if (binding['owner'] != owner or owner.get('kind') != 'artifact_set'
            or owner.get('required_count') != 4 or not any(
                s['set_id'] == owner.get('set_id') and s['count'] >= 4 for s in gear.get('artifact_sets', []))):
        issue('reaction_em_equipment_scope', 'equipment_inputs.' + actor,
              '反应精通来源需要实际提供者启用的同一四件套')
    if any(g.get('mode') != 'panel' or g.get('panel_policy') != 'static_included'
           for g in plan['equipment_inputs'].values()):
        issue('reaction_em_panel_scope', 'equipment_inputs', '本来源要求给定面板已包含常驻两件属性')
    if binding['upstream_recipe'].get('ability_refs'):
        issue('reaction_em_action_scope', 'upstream_recipe.ability_refs', '反应精通来源不接受爆发动作引用')
    for other_actor, other in plan['equipment_inputs'].items():
        if other_actor != actor and any(s['set_id'] == owner.get('set_id') and s['count'] >= 4
                                        for s in other.get('artifact_sets', [])):
            issue('reaction_em_unique_scope', 'equipment_inputs.' + other_actor,
                  '多位提供者装备同源四件套的覆盖次序尚未核实')
    for other in plan['effect_bindings']:
        if other['binding_id'] == binding['binding_id'] or other['binding_id'] in inactive_binding_ids:
            continue
        if other['owner'] == owner or any(_affix(ref) is not None and str(_affix(ref)) == str(owner.get('affix_id'))
                                          for ref in other.get('source_refs', [])):
            issue('reaction_em_duplicate_source', 'effect_bindings.' + other['binding_id'],
                  '另一分项已声明同一反应精通装备来源')
    for buff in plan['manual_template'].get('buffs', []):
        refs = (buff.get('extensions') or {}).get('source_refs', [])
        if isinstance(refs, list) and any(isinstance(ref, dict) and _affix(ref) is not None
                and str(_affix(ref)) == str(owner.get('affix_id')) for ref in refs):
            issue('reaction_em_duplicate_source', 'manual_template.buffs.' + buff['id'],
                  '手工增益已引用同一自动精通来源')
    proof = semantic_scope({'scope': 'actual_reaction_em_equipment', 'provider': actor, 'owner': owner,
                            'equipment': plan['equipment_inputs'], 'binding': binding,
                            'inactive_binding_ids': sorted(inactive_binding_ids)})
    return issues, proof


def final_scope(plan, template, binding, compiled):
    """Inspect the final generated model, not just user-authored rule fragments."""
    model = plan.get('mechanics_model') or {}
    elemental = model.get('elemental_model') or {}
    scenario = binding['upstream_recipe']['scenario']
    target = elemental.get('target') or {}
    issues = []

    def issue(code, path, message):
        issues.append({'code': code, 'path': path, 'message': message})

    if elemental.get('ruleset') != DYNAMIC_SWIRL_RULESET:
        issue('reaction_em_elemental_scope', 'mechanics_model.elemental_model',
              '自动反应精通首片需要普通动态扩散v3，其他元素契约未继承准入')
    elif validate_elemental_model(elemental):
        issue('reaction_em_elemental_scope', 'mechanics_model.elemental_model', '自动元素模型尚未通过验证')
    if (model.get('attribute_model') or {}).get('contract') != 'attribute-ledger-v2':
        issue('reaction_em_attribute_scope', 'mechanics_model.attribute_model', '本来源当前需要已核对的精通属性账v2')
    if scenario.get('target_id') is None or scenario['target_id'] != target.get('id'):
        issue('reaction_em_target_scope', 'upstream_recipe.scenario.target_id', '来源原目标须与当前元素模型唯一目标相同')
    if model.get('application_events'):
        issue('reaction_em_parent_scope', 'mechanics_model.application_events', '首片未认证无伤害附着、环境或自身反应回调')
    if model.get('manual_buff_repeat', 'each_cycle') != 'each_cycle':
        issue('reaction_em_window_scope', 'mechanics_model.manual_buff_repeat', '本来源需要完整检查窗口的一致属性口径')
    if model.get('snapshot_captures'):
        issue('reaction_em_snapshot_scope', 'mechanics_model.snapshot_captures', '本来源尚未认证独立快照动作与旧属性帧')
    for collection in ('hit_events', 'hit_patterns'):
        for index, hit in enumerate(model.get(collection, [])):
            path = f'mechanics_model.{collection}[{index}]'
            application = hit.get('application') or {}
            if not application or application.get('target_id') != target.get('id'):
                issue('reaction_em_parent_scope', path + '.application', '每个实际命中须使用自动原目标附着档案')
            if hit.get('self_snapshot_event') is not None:
                issue('reaction_em_snapshot_scope', path + '.self_snapshot_event', '不能将本来源当前精通等价为未经审核的旧自身快照')
    for index, hit in enumerate(template.get('hits', [])):
        if hit.get('damage_system') != 'classic' or hit.get('reaction') is not None:
            issue('reaction_em_damage_scope', f'hits[{index}]', '首片原始命中只接普通直伤；反应由实际元素层产生')
        if hit.get('snapshot'):
            issue('reaction_em_snapshot_scope', f'hits[{index}].snapshot', '旧模板快照的精通取样尚未核实')
    readers = []
    for collection in ('rules', 'fixed_effect_sources'):
        for rule in model.get(collection, []):
            for effect in rule.get('effects', []):
                value = effect.get('value')
                if isinstance(value, dict) and value.get('kind') == 'stat' and value.get('stat') == 'em' and value.get('basis', 'total') != 'base':
                    path = f'mechanics_model.{collection}.{rule["id"]}.{effect["id"]}'
                    readers.append({'path': path, 'owner': rule.get('owner'), 'value': copy.deepcopy(value),
                                    'trigger': copy.deepcopy(rule.get('trigger')), 'domain': effect.get('domain')})
                    issue('reaction_em_reader_scope', path,
                          '最终模型存在读取动态精通的转换，须另核取样域及同次回调次序；来源确认不能自动批准')
    generated_ids = {r['id'] for r in compiled.get('rules', [])}
    # This check runs before prepare_mechanics adds execution metadata. There
    # are no permitted rule rewrites between the fresh compiler and this gate.
    # A matching ID alone cannot certify changed values, ownership or triggers.
    for expected in compiled.get('rules', []):
        actual = [r for r in model.get('rules', []) if r['id'] == expected['id']]
        if len(actual) != 1 or actual[0] != expected:
            issue('reaction_em_source_rule_mismatch', 'mechanics_model.rules.' + expected['id'],
                  '最终反应精通规则必须与本次重新编译的来源完全一致；缺失、重复或修改后须重新核验')
    payloads = []
    state = model.get('state_runtime') or {}
    for life in state.get('lifecycles', []):
        payloads.extend(p for items in life.get('hooks', {}).values() for p in items)
    payloads.extend(t['payload'] for t in state.get('timers', []))
    if any(p.get('kind') == 'dispatch_rule' and p.get('ref_id') in generated_ids for p in payloads):
        issue('reaction_em_dispatch_scope', 'mechanics_model.state_runtime', '生命周期不能再次派发本反应来源')
    for initial in model.get('initial_state', {}).get('rules', []):
        if initial['rule_id'] in generated_ids and (initial.get('instances') or initial.get('cooldown_remaining')):
            issue('reaction_em_initial_scope', 'mechanics_model.initial_state', '本来源声明无初始动态增益，不能预先注入实例或冷却')
    proof = semantic_scope({'scope': 'ordinary_reaction_em_final_model', 'profile_id': PROFILE_ID,
        'provider': binding['provider_character_id'], 'target_id': target.get('id'),
        'scenario': scenario, 'model': model, 'template': template, 'readers': readers,
        'source_attribute_facts': compiled.get('attribute_writes', []),
        'provider_authority': 'actual_elemental_reaction_occurrence_checked_after_execution',
        'order_authority': 'no_cross_listener_order'})
    return issues, proof


def scope_fingerprint(proof):
    return fingerprint(semantic_scope(proof))
