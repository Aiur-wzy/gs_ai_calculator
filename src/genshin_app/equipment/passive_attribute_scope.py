"""Conservative D-B sampling qualification, independent of graph arithmetic."""
from __future__ import annotations

import copy

from genshin_engine.beneficiary_selection import possible_recipients
from genshin_engine.registry import BENEFIT_TYPES


def inspect_nonextra_sampling(plan, template, actor):
    """Only constant ordinary EM writers have qualified game sampling here.

    Authenticated excluded writers do not enter a NONEXTRA read. Authentication
    remains the attribute-source adoption's responsibility, never this scanner's.
    Trigger/execution-time activity remains the actual engine's responsibility.
    """
    model = plan.get('mechanics_model') or {}
    attr = model.get('attribute_model') or {}
    classifications = {tuple(sorted(row['effect_ref'].items())):row
                       for row in attr.get('effect_classifications', [])}
    excluded = {part['rule']['rule_id'] for binding in plan.get('effect_bindings', [])
                for part in binding['parts'] if part['state']=='excluded'
                and (part.get('rule') or {}).get('kind')=='mechanics'}
    scanned, issues = [], []

    def inspect(ref, value, trigger=None):
        row = classifications.get(tuple(sorted(ref.items())))
        pruned = bool(row and row['status'] in ('assumed','confirmed') and row['nonextra_inclusion']=='excluded')
        status = 'excluded_from_nonextra' if pruned else 'constant_writer'
        if not pruned and isinstance(value, dict):
            status = 'conversion_sampling_pending'
            issues.append({'code':'attribute_sampling_causality_pending','effect_ref':ref,
                           'message':'实际NONEXTRA读取包含属性转换；数学闭包可计算不等于本数据库来源的游戏取样因果已核验'})
        if not pruned and trigger and trigger.get('phase')=='post' and trigger.get('event') in ('hit','elemental_reaction'):
            status = 'same_proc_sampling_pending'
            issues.append({'code':'attribute_sampling_causality_pending','effect_ref':ref,
                           'message':'提供者存在同次命中/反应后精通写入；当前来源尚未核实该回调相对取样的先后'})
        declaration = copy.deepcopy(row)
        if declaration:
            # This is a causal declaration, not a source authentication receipt.
            # The component fingerprint depends on the source collection, which
            # includes this scope. Keeping it here would create a hash cycle.
            # prepare_attribute_model separately authenticates that exact value.
            declaration['component'].pop('source_fingerprint', None)
        scanned.append({'effect_ref':ref,'classification':declaration,'status':status})

    for rule in model.get('rules', []):
        if rule['id'] in excluded:
            continue
        if actor not in possible_recipients(rule, [actor]):
            continue
        for effect in rule['effects']:
            kind = BENEFIT_TYPES.get(effect['benefit_type_id'], {})
            if kind.get('zone')=='em' or kind.get('zone') in ('stat_add','stat_pct') and kind.get('stat')=='em':
                inspect({'kind':'rule','rule_id':rule['id'],'effect_id':effect['id']},effect['value'],rule['trigger'])
    for buff in template.get('buffs', []):
        if buff.get('target') not in (None,actor):
            continue
        for effect in buff['effects']:
            if effect['zone']=='em' or effect['zone'] in ('stat_add','stat_pct') and effect.get('stat')=='em':
                inspect({'kind':'manual_buff','buff_id':buff['id'],'effect_id':effect['id']},effect['value'])
    return {'status':'pending' if issues else 'qualified_constant_nonextra_writers',
            'scope':'constant_dynamic_nonextra_only','provider':actor,'writers':scanned,'issues':issues}
