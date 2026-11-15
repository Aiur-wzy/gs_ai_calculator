"""Source-bound attribute metadata; game classifications require explicit facts."""
from __future__ import annotations

import copy
import re

from genshin_engine.attribute_budget import validate_calculation_options
from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_stat_native import effect_output
from genshin_engine.attribute_stats import CONTRACT as MULTISTAT_CONTRACT
from genshin_engine.attribute_stats import STATS as MULTISTAT_STATS
from genshin_engine.attribute_stats import effect_output as multistat_effect_output
from genshin_engine.beneficiary_selection import possible_recipients
from genshin_engine.registry import BENEFIT_TYPES

from .attribute_input import (
    adopt_preview_components,
    resolve_component,
    validate_author_attribute_model,
)
from .files import context_view, fingerprint, input_view
from .schema import PlanError

CONTRACT = 'attribute-source-adoption-v1'
STAT_CONTRACT = 'attribute-source-adoption-v2'


def _output(effect, kind, multi_stat, five_stats=False):
    """Infer dimensions from an actual effect, never its declared component."""
    if multi_stat:
        try:
            output=(multistat_effect_output if five_stats else effect_output)(effect,kind=kind)
        except AttributeLedgerError as exc:
            raise PlanError('属性来源的真实收益类型或维度不一致','invalid_attribute_source',exc.issue) from exc
        return {'output_stat':output['stat'],'value_unit':output['value_unit'],
                'application':copy.deepcopy(output['application'])} if output else None
    zone=BENEFIT_TYPES.get(effect['benefit_type_id'],{}).get('zone') if kind=='rule' else effect.get('zone')
    if zone == 'em':
        return {'output_stat': 'em', 'value_unit': 'flat', 'application': {'kind': 'flat'}}
    return None


def attribute_source_contract():
    return {'contract': CONTRACT, 'field': 'mechanics_model.attribute_model',
            'review_field': 'attribute_review',
            'approval': '整份属性声明需要当前manual_context及非空说明；分类pending只保留unknown，不推断NONEXTRA。',
            'supported_models': ['attribute-ledger-v1', 'attribute-ledger-v2', 'attribute-ledger-v3', MULTISTAT_CONTRACT],
            'stat_adoption_contract': STAT_CONTRACT,
            'identities': 'App从真实规则/手工Buff生成provider、完整source_id作为part_id、effect_id及当前来源指纹。v1/v2仅精通；v3解析精通与攻击；v4另闭合最大生命、防御及充能效率。真实输出单位由收益类型推导，不能自拟。',
            'initial_components': '只接受能解析到同一实际属性来源的组件；初始总面板已含的组成不重复加算。攻击/生命/防御百分比以受益者相应base折算一次；缺失base保持待核实，不从total反推。ER使用1.0=100%。无法定位组成时保持basis明确假设和空组件，不能假造组件来源。',
            'preview': 'attribute_source_adoption.components提供expected_component；automatic_unknown只填身份，不批准分类。',
            'adoption': 'prepare_attribute_review先规范组件，再对最终候选计算context；调用者须已核对游戏含义并填写说明。'}


def _key(component):
    return tuple(component[k] for k in ('provider', 'part_id', 'effect_id'))


def prepare_attribute_model(plan, model, template, source_fp, missing, *, context_plan=None, fresh_classifications=()):
    """Compile into an execution copy; preserve the original native declaration."""
    if not isinstance(fresh_classifications, (list, tuple)) or len(fresh_classifications)>10000:
        raise PlanError('新鲜来源分类须为有界的内部事实列表','invalid_attribute_source')
    config = model.get('attribute_model') if model is not None else None
    if config is None:
        if fresh_classifications:
            raise PlanError('新鲜来源分类需要显式属性账','invalid_attribute_source')
        if plan.get('attribute_review') is not None:
            raise PlanError('属性确认需要显式attribute_model', 'invalid_attribute_source')
        return None, None
    limits = validate_calculation_options(model.get('execution_options', {'schema_version':1}))['effective']
    validate_author_attribute_model(config, limits=limits)
    five_stats = config['contract'] == MULTISTAT_CONTRACT
    multi_stat = five_stats or config['contract'] == 'attribute-ledger-v3'
    contract = STAT_CONTRACT if multi_stat else CONTRACT
    source_word = '属性' if multi_stat else '精通'
    context_fp = fingerprint(context_view(input_view(context_plan or plan)))
    actors = {c['id'] for c in template['characters']}
    registry, recipients, effects, automatic, dimensions = {}, {}, {}, [], {}

    def register(ref, owner, source_id, effect_id, active, possible_recipients, output):
        if owner not in actors or not isinstance(source_id, str) or not source_id.strip():
            missing('attribute_source_missing', 'mechanics_model.attribute_model',
                    f'{source_word}来源需要实际提供者及source_id，不能由受益者反推', 'review_attributes')
            return
        component = {'provider': owner, 'part_id': source_id, 'effect_id': effect_id}
        component['source_fingerprint'] = fingerprint({'contract': contract, 'source_fingerprint': source_fp,
                                                      'context_fingerprint': context_fp, **component})
        key, ref_key = _key(component), fingerprint(ref)
        if key in registry or ref_key in effects:
            raise PlanError(f'同一{source_word}来源组件被重复交付', 'duplicate_attribute_source')
        registry[key] = component
        recipients[key] = possible_recipients
        dimensions[key] = output
        effects[ref_key] = {'effect_ref': ref, 'component': component, 'active': active}

    active_rules = {r['id'] for r in model['rules']}
    excluded_rules = {p['rule']['rule_id'] for b in plan['effect_bindings'] for p in b['parts']
                      if p['state'] == 'excluded' and (p.get('rule') or {}).get('kind') == 'mechanics'}
    # Native rules include explicitly excluded declarations, so initial included
    # components and inactive classifications still use exactly one resolver.
    for rule in plan['mechanics_model']['rules']:
        possible = set(possible_recipients(rule, actors))
        for effect in rule['effects']:
            output = _output(effect,'rule',multi_stat,five_stats)
            if output:
                register({'kind':'rule','rule_id':rule['id'],'effect_id':effect['id']},
                         rule['owner'], rule['source_id'], effect['id'], rule['id'] in active_rules, possible, output)
    for buff in template.get('buffs', []):
        for effect in buff['effects']:
            output = _output(effect,'manual_buff',multi_stat,five_stats)
            if output:
                identity = buff.get('extensions', {})
                register({'kind':'manual_buff','buff_id':buff['id'],'effect_id':effect['id']},
                         identity.get('owner'), identity.get('source_id'), effect['id'], True,
                         {buff['target']} if buff.get('target') is not None else actors, output)
    fresh={}
    if fresh_classifications:
        if config['contract']!='attribute-ledger-v2':
            raise PlanError('本来源分类接口仅支持有界的显式属性账v2','invalid_attribute_source')
        live={r['id']:r for r in model['rules']}
        fields={'effect_ref','part_id','provider','stat','nonextra_inclusion','classification_authority','fact_fingerprint'}
        for fact in fresh_classifications:
            if (not isinstance(fact,dict) or set(fact)!=fields or fact.get('stat')!='em'
                    or fact.get('nonextra_inclusion')!='included' or fact.get('classification_authority')!='this_admitted_source_fact'
                    or any(not isinstance(fact.get(k),str) or not fact[k].strip() for k in ('provider','part_id'))
                    or not isinstance(fact.get('fact_fingerprint'),str) or not re.fullmatch(r'[0-9a-f]{64}',fact['fact_fingerprint'])):
                raise PlanError('新鲜来源分类不满足封闭的受审事实合同','invalid_attribute_source')
            ref=fact['effect_ref']
            if (not isinstance(ref,dict) or set(ref)!={'kind','rule_id','effect_id'} or ref.get('kind')!='rule'
                    or any(not isinstance(ref.get(k),str) or not ref[k].strip() for k in ('rule_id','effect_id'))):
                raise PlanError('新鲜来源分类须精确引用活跃规则收益','invalid_attribute_source')
            ref_key=fingerprint(ref);resolved=effects.get(ref_key);rule=live.get(ref['rule_id'])
            effect=next((e for e in rule['effects'] if e['id']==ref['effect_id']),None) if rule else None
            if (ref_key in fresh or resolved is None or not resolved['active'] or not effect
                    or rule['owner']!=fact['provider'] or rule['source_id']!=fact['part_id']
                    or resolved['component']['provider']!=fact['provider'] or resolved['component']['part_id']!=fact['part_id']
                    or BENEFIT_TYPES.get(effect['benefit_type_id'],{}).get('zone')!='em'
                    or dimensions[_key(resolved['component'])]['output_stat']!='em'):
                raise PlanError('新鲜分类与当前活跃来源、提供者或真实精通收益不一致','invalid_attribute_source')
            fresh[ref_key]=copy.deepcopy(fact)
    result, records, seen = copy.deepcopy(config), [], set()
    automatic_sources=[]
    kept = []
    for index, row in enumerate(result['effect_classifications']):
        ref_key = fingerprint(row['effect_ref'])
        if ref_key in seen or ref_key not in effects:
            raise PlanError(f'属性分类重复、悬空或未引用实际{source_word}效果', 'invalid_attribute_source')
        seen.add(ref_key)
        resolved = effects[ref_key]
        path = f'mechanics_model.attribute_model.effect_classifications[{index}].component'
        resolve_component(row, resolved['component'], path, missing, active=resolved['active'])
        output = dimensions[_key(resolved['component'])]
        if ref_key in fresh and row['nonextra_inclusion']!='included':
            missing('attribute_source_fact_conflict',f'mechanics_model.attribute_model.effect_classifications[{index}]',
                    '当前显式分类与已审来源事实不同或仍未知，请核对后明确采用；不会自动覆盖用户声明','review_attributes')
        if multi_stat and not five_stats and output['output_stat'] == 'atk' and row['nonextra_inclusion'] != 'unknown':
            raise PlanError('攻击力来源暂不支持NONEXTRA分类', 'invalid_attribute_source')
        path = f'mechanics_model.attribute_model.effect_classifications[{index}].component'
        records.append({'path':path, 'expected_component':resolved['component'],
                        'status':('active' if resolved['active'] else
                                  'excluded' if row['effect_ref'].get('rule_id') in excluded_rules else 'pending')})
        if multi_stat:
            records[-1].update(copy.deepcopy(output))
        if resolved['active']:
            kept.append(row)
    for ref_key, resolved in effects.items():
        if ref_key in seen or not resolved['active']:
            continue
        row = {k:copy.deepcopy(resolved[k]) for k in ('effect_ref','component')}
        if ref_key in fresh:
            fact=fresh[ref_key]
            row.update(nonextra_inclusion='included',status='confirmed',source='当前受审来源分类；事实指纹：'+fact['fact_fingerprint'])
            automatic_sources.append({**copy.deepcopy(fact),'component':copy.deepcopy(row['component'])})
        else:
            row.update(nonextra_inclusion='unknown', status='pending', source='App自动识别来源身份；NONEXTRA归属尚无明确依据')
        kept.append(row)
        if ref_key not in fresh:
            automatic.append(copy.deepcopy(row))
    result['effect_classifications'] = kept
    basis_keys = set()
    for index, basis in enumerate(result['initial_basis']):
        if basis['actor'] not in actors:
            raise PlanError('初始属性基底角色不存在', 'invalid_attribute_source')
        if multi_stat:
            basis_key = (basis['actor'], basis.get('stat'))
            if basis.get('stat') not in (MULTISTAT_STATS if five_stats else ('em', 'atk')) or basis_key in basis_keys:
                raise PlanError('初始属性基底需要唯一的角色与属性组合', 'invalid_attribute_source')
            basis_keys.add(basis_key)
        for position, row in enumerate(basis['included_components']):
            component = registry.get(_key(row['component']))
            if component is None:
                raise PlanError(f'已含属性组件必须解析到实际{source_word}来源，不能自拟身份', 'invalid_attribute_source')
            output = dimensions[_key(component)]
            if multi_stat and output['output_stat'] != basis['stat']:
                raise PlanError('已含属性组件与基底的属性种类不一致', 'invalid_attribute_source')
            if multi_stat and not five_stats and basis['stat'] == 'atk' and row['nonextra_inclusion'] != 'unknown':
                raise PlanError('已含攻击力组件暂不支持NONEXTRA分类', 'invalid_attribute_source')
            if basis['actor'] not in recipients[_key(component)]:
                raise PlanError('已含属性角色不在来源允许的受益者范围内', 'invalid_attribute_source')
            path = f'mechanics_model.attribute_model.initial_basis[{index}].included_components[{position}].component'
            records.append({'path':path, 'expected_component':component, 'status':'included_in_panel'})
            if multi_stat:
                records[-1].update(copy.deepcopy(output), included_value_unit='flat')
            if row['component'] != component:
                missing('attribute_source_stale', path, '已含属性组件来源须对齐当前实际来源', 'review_attributes')
    review = plan.get('attribute_review') or {}
    ready = (review.get('mode') == 'manual_context' and review.get('context_fingerprint') == context_fp
             and isinstance(review.get('note'), str) and bool(review['note'].strip()))
    if not ready:
        missing('attribute_scope_unreviewed', 'attribute_review', '请核对当前基底、已含组件与NONEXTRA分类并确认整份上下文', 'review_attributes')
    adoption = {'contract':contract, 'context_fingerprint':context_fp, 'review_status':'ready' if ready else 'pending',
                'components':records, 'automatic_unknown':automatic}
    if multi_stat:
        adoption['source_effects'] = [{**copy.deepcopy(row), **copy.deepcopy(dimensions[_key(row['component'])])}
                                      for row in effects.values()]
    if automatic_sources:
        adoption['automatic_source_classifications']=automatic_sources
    return result, adoption


def prepare_attribute_review(plan, note, db_path):
    """Explicit caller adoption; fill identities then confirm the final context once."""
    from .composer import compose_equipment_plan
    from .schema import validate_plan
    validate_plan(plan)
    if not isinstance(note, str) or not note.strip() or len(note) > 4096:
        raise PlanError('属性核对需要非空、至多4096字的说明', 'invalid_attribute_review')
    if not (plan.get('mechanics_model') or {}).get('attribute_model'):
        raise PlanError('请先声明属性基底和分类', 'invalid_attribute_review')
    candidate = copy.deepcopy(plan)
    candidate['attribute_review'] = {'mode':'manual_context','context_fingerprint':None,'note':note}
    first = compose_equipment_plan(candidate, db_path)['attribute_source_adoption']
    config = candidate['mechanics_model']['attribute_model']
    # Do not copy automatic unknowns into the native input: these are compiler
    # products, and adding them would unnecessarily change the review context.
    adopt_preview_components(config, first['components'])
    # Adding a formerly absent identity changes the factual context. Resolve
    # its fingerprint once more after that deterministic shape is present;
    # fingerprints themselves are excluded from context, so this is stable.
    normalized = compose_equipment_plan(candidate, db_path)['attribute_source_adoption']
    adopt_preview_components(config, normalized['components'])
    final = compose_equipment_plan(candidate, db_path)
    candidate['attribute_review']['context_fingerprint'] = final['context_fingerprint']
    candidate['revision'] += 1
    return compose_equipment_plan(candidate, db_path)
