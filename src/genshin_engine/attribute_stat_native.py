"""A2b.0 closed native facts; panels are read once, no runtime/source authority."""
from __future__ import annotations

import copy
import math

from genshin_engine.attribute_budget import (
    declaration_capacity_issue,
    limits_for_schema,
    normalize_attribute_budget,
)
from genshin_engine.attribute_ledger import (
    AttributeLedgerError,
    _bounded_text,
    _canonical,
    attribute_schema_error,
)
from genshin_engine.attribute_runtime_schema import (
    BASE_BASIS,
    CLASSIFICATION,
    ID,
    INCLUDED_COMPONENT,
    NONEXTRA_BASIS,
)
from genshin_engine.execution_control import ExecutionCancelled
from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.rotation import _schema_errors
from genshin_engine.rotation_schema import arr, obj

ATK_INCLUDED = copy.deepcopy(INCLUDED_COMPONENT)
ATK_INCLUDED['properties']['nonextra_inclusion'] = {'const':'unknown'}
EM_BASIS = obj({'actor':ID,'stat':{'const':'em'},'included_components':arr(INCLUDED_COMPONENT),
                'base':BASE_BASIS,'nonextra':NONEXTRA_BASIS},['actor','stat','included_components'])
ATK_BASIS = obj({'actor':ID,'stat':{'const':'atk'},'included_components':arr(ATK_INCLUDED)},
               ['actor','stat','included_components'])
NATIVE_SCHEMA = obj({'contract':{'const':'attribute-ledger-v3'},
                     'initial_basis':arr({'oneOf':[EM_BASIS,ATK_BASIS]}),
                     'effect_classifications':arr(CLASSIFICATION)},
                    ['contract','initial_basis','effect_classifications'])


def effect_output(effect, *, kind):
    if kind not in ('rule','manual_buff'):
        raise AttributeLedgerError('attribute_component_reference','未知来源引用类型')
    # Native mechanics has this explicit non-damage benefit outside the damage registry.
    if kind=='rule' and effect.get('benefit_type_id')=='benefit.energy_recharge.all':
        return None
    info=BENEFIT_TYPES.get(effect.get('benefit_type_id'))
    if info is None:
        raise AttributeLedgerError('attribute_component_reference','来源不是已知收益类型')
    zone,stat=info['zone'],info['stat']
    if kind=='manual_buff' and (effect.get('zone')!=zone or effect.get('stat')!=stat):
        raise AttributeLedgerError('attribute_component_reference','手工收益的类型与真实zone/stat不一致')
    if zone=='em':
        return {'stat':'em','value_unit':'flat','application':{'kind':'flat'}}
    if zone in ('stat_add','stat_pct') and stat=='atk':
        return {'stat':'atk','value_unit':'ratio' if zone=='stat_pct' else 'flat',
                'application':{'kind':'recipient_base_fraction' if zone=='stat_pct' else 'flat'}}
    return None


def prepare_initials(template, model, *, limits=None):
    """No second total/base input; returned seeds remain caller panel facts."""
    try:
        limits=normalize_attribute_budget(limits)['effective']
        encoded=_canonical(model)
        if len(encoded)>limits['declaration_bytes']:
            issue=declaration_capacity_issue('declaration_bytes',len(encoded),limits['declaration_bytes'],'attribute_model')
            raise AttributeLedgerError(issue['code'],issue['message'],issue['path'],status=issue['severity'],
                **{k:v for k,v in issue.items() if k not in ('code','message','path','severity')})
        issues=[]
        from genshin_engine.attribute_contract import attribute_model_schema
        _schema_errors(model,limits_for_schema(attribute_model_schema()['oneOf'][1],limits),'attribute_model',issues)
        if issues:
            raise attribute_schema_error(issues)
        _bounded_text(model,max_depth=limits['declaration_depth'])
        chars={c['id']:c for c in template['characters']}
        if len(chars)!=len(template['characters']):
            raise AttributeLedgerError('attribute_basis_reference','角色ID重复')
        bases={}
        for row in model['initial_basis']:
            identity=(row['actor'],row['stat'])
            if identity in bases or row['actor'] not in chars or row['stat'] not in chars[row['actor']]['stats']:
                raise AttributeLedgerError('attribute_basis_reference','原生基底角色/属性不存在或重复')
            bases[identity]=row

        def number(value):
            if type(value) not in (float,int) or not math.isfinite(value) or value<0:
                raise AttributeLedgerError('attribute_value_invalid','当前面板和白值必须是有限非负数值')
            return value

        initials=[]
        for actor,char in chars.items():
            for stat in ('atk','em'):
                if stat not in char['stats']:
                    continue
                basis=bases.get((actor,stat),{})
                components=[{**copy.deepcopy(c),'component':{**c['component'],'recipient':actor}}
                            for c in basis.get('included_components',[])]
                if any(c['component']['provider'] not in chars for c in components):
                    raise AttributeLedgerError('attribute_component_reference','已含属性组件提供者不在当前队伍')
                initial={'actor':actor,'stat':stat,'total':number(char['stats'][stat]),
                         'included_components':components,'source':'当前解析角色面板；已含组成仅作说明不再加算'}
                if stat=='atk':
                    if 'base_atk' in char['stats']:
                        initial['base']=number(char['stats']['base_atk'])
                else:
                    for key in ('nonextra','base'):
                        declared=basis.get(key,{})
                        if declared.get('status')=='pending' or declared.get('mode','unknown')=='unknown':
                            continue
                        if declared['mode']=='panel_total':
                            if any(c['nonextra_inclusion']=='unknown' or c['nonextra_inclusion']=='excluded' and c['value']!=0 for c in components):
                                raise AttributeLedgerError('attribute_panel_total_conflict','已含组成不支持全额NONEXTRA声明')
                            initial[key]=initial['total']
                        else:
                            initial[key]=number(declared['value'])
                initials.append(initial)
        return initials
    except (ExecutionCancelled, AttributeLedgerError):
        raise
    except (ValueError,TypeError,ArithmeticError,KeyError,RecursionError) as exc:
        raise AttributeLedgerError('attribute_schema','属性原生输入或当前面板不满足有限封闭合同') from exc
