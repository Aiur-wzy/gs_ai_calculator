"""Closed, versioned database attribute facts; consistency is not authority.

No raw token, fact, or caller supplied review activates a game source here.
The packaged source compiler and the App's actual-parent qualification own that.
"""
from __future__ import annotations

import copy
from datetime import datetime

from .mechanism_semantics import (
    _id,
    _list,
    _number,
    _object,
    _pointer,
    _sha,
    _text,
    fingerprint,
    finite_json,
    require,
)

FACT_KINDS = frozenset({'attribute_read', 'attribute_output', 'attribute_sample'})
GRAMMAR = 'rpn-parameter-stat-mul-v1'
READS = {'FIGHT_PROP_ELEMENT_MASTERY': 'total', 'FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY': 'nonextra'}
OUTPUTS = {'Actor_FireAddHurtExtraDelta': 'pyro', 'Actor_WaterAddHurtExtraDelta': 'hydro',
           'Actor_IceAddHurtExtraDelta': 'cryo', 'Actor_ElecAddHurtExtraDelta': 'electro'}
SAMPLE = {'event': 'elemental_reaction', 'phase': 'post', 'sample': 'trigger', 'subject': 'owner',
          'output_domain': 'self', 'freeze_scope': 'instance',
          'causal_scope': 'qualified_ability_reaction_callback'}


def _pointer_text(value):
    _text(value, 4096)
    require(value.startswith('/'), '属性事实必须指向原节点字段')


def _validate_attribute_claim(kind, value):
    """Validate a fact claim, without granting source or execution admission."""
    finite_json(value, 32 * 1024)
    base = ('schema_version', 'rule_key', 'effect_id')
    require(kind in FACT_KINDS, '未知属性事实类型')
    if kind == 'attribute_read':
        _object(value, (*base, 'node_id', 'pointer', 'expression_sha256', 'grammar',
            'parameter_ref', 'parameter_symbol', 'raw_stat', 'stat', 'basis', 'subject', 'unit'))
        _sha(value['expression_sha256'])
        _id(value['parameter_ref'])
        _text(value['parameter_symbol'])
        require(value['grammar'] == GRAMMAR and value['raw_stat'] in READS
                and value['basis'] == READS[value['raw_stat']] and value['stat'] == 'em'
                and value['subject'] == 'owner' and value['unit'] == 'flat', '属性读取映射或单位不符')
    elif kind == 'attribute_output':
        _object(value, (*base, 'node_id', 'pointer', 'raw_property', 'benefit_type_id',
                       'domain', 'unit', 'nonextra_inclusion'))
        require(value['raw_property'] in OUTPUTS
                and value['benefit_type_id'] == 'benefit.dmg_bonus.' + OUTPUTS[value['raw_property']]
                and value['domain'] == 'self' and value['unit'] == 'ratio'
                and value['nonextra_inclusion'] == 'not_applicable', '属性输出通道或域不符')
        require(value['pointer'].split('/')[-1] == value['raw_property'], '输出事实未指向对应原字段')
    else:
        _object(value, (*base, *SAMPLE))
        require(all(value[k] == v for k, v in SAMPLE.items()), '取样事实超出受审回调范围')
    require(type(value['schema_version']) is int and value['schema_version'] == 1, '属性事实schema不受支持')
    for key in ('rule_key', 'effect_id'):
        _id(value[key])
    if kind != 'attribute_sample':
        _id(value['node_id'])
        _pointer_text(value['pointer'])
    return copy.deepcopy(value)


def validate_attribute_claim(kind, value):
    try:
        return _validate_attribute_claim(kind, value)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('属性事实结构无效') from exc


def compile_attribute_expression(raw, *, claim, parameter):
    """Keep the raw order/coefficient; only one reviewed multiply is accepted."""
    c = validate_attribute_claim('attribute_read', claim)
    finite_json([raw, parameter], 64 * 1024)
    require(type(raw) is list and len(raw) == 3
            and raw == [c['parameter_symbol'], c['raw_stat'], 'MUL']
            and fingerprint(raw) == c['expression_sha256'], '属性原表达式不符合完整封闭语法')
    require(parameter['ref'] == c['parameter_ref'] and parameter['symbol'] == c['parameter_symbol']
            and parameter['unit'] == 'ratio_per_em', '参数符号或精确用途单位不符')
    _number(parameter['value'])
    require(parameter['fingerprint'] == fingerprint({k: v for k, v in parameter.items() if k != 'fingerprint'}),
            '参数装配指纹不符')
    return {'kind': 'stat', 'subject': 'owner', 'stat': 'em', 'basis': c['basis'],
            'factor': parameter['value'], 'sample': 'trigger'}


def _verify_attribute_facts(facts, *, nodes, parameters, evidence, reviews):
    """Verify a materialized fact set. Its caller must prove registry authority."""
    from .mechanism_semantics import fact_fingerprint, validate_fact
    finite_json([facts, nodes, parameters, evidence, reviews], 2 * 1024 * 1024)
    _list(facts, 3, 24)
    _list(nodes, 1, 32)
    _list(parameters, 1, 32)
    _list(evidence, 1, 64)
    ns, ps, es, fs, groups = {}, {}, {}, {}, {}
    for node in nodes:
        _object(node, ('id', 'source_path', 'source_sha256', 'json_pointer', 'node_sha256', 'payload'))
        _id(node['id'])
        _sha(node['source_sha256'])
        _pointer_text(node['json_pointer'])
        _text(node['source_path'], 512)
        require(node['id'] not in ns and node['node_sha256'] == fingerprint(node['payload']), '属性原节点重复或摘要不符')
        ns[node['id']] = node
    for parameter in parameters:
        _object(parameter, ('ref', 'symbol', 'value', 'unit', 'param_index', 'operation_pointer', 'parameter_pointer',
            'source_selection', 'source_fingerprint', 'unlock_fingerprint', 'raw_parent_hashes', 'fingerprint'))
        _id(parameter['ref'])
        _text(parameter['symbol'])
        _number(parameter['value'])
        require(parameter['unit'] in ('ratio_per_em', 'seconds') and type(parameter['param_index']) is int
                and 0 <= parameter['param_index'] <= 7, '属性参数用途或参数槽无效')
        for key in ('operation_pointer', 'parameter_pointer'):
            _pointer_text(parameter[key])
        for key in ('source_fingerprint', 'unlock_fingerprint', 'fingerprint'):
            _sha(parameter[key])
        selection = parameter['source_selection']
        _object(selection, ('character_id', 'skill_depot_id', 'proud_skill_id', 'proud_skill_group_id', 'proud_level', 'opening_index'))
        require(all(type(v) is int and (v >= 0 if k == 'opening_index' else v > 0)
                    for k, v in selection.items()), '参数选择身份或有效等级无效')
        parents = parameter['raw_parent_hashes']
        _object(parents, ('ability', 'talent', 'proud', 'avatar', 'depot', 'promote'))
        require(set(ns) == set(parents) and all(ns[k]['node_sha256'] == value for k, value in parents.items()), '参数完整父节点未闭合')
        proud, talent = ns['proud'], ns['talent']
        expected_pointer = proud['json_pointer'] + '/paramList/' + str(parameter['param_index'])
        require(parameter['parameter_pointer'] == expected_pointer
                and fingerprint(proud['payload']['paramList'][parameter['param_index']]) == fingerprint(parameter['value'])
                and all(proud['payload'][field] == selection[key] for field, key in (
                    ('proudSkillId', 'proud_skill_id'), ('proudSkillGroupId', 'proud_skill_group_id'), ('level', 'proud_level'))),
                '参数原字段、用途槽或有效等级身份不符')
        require(parameter['operation_pointer'].startswith(talent['json_pointer'] + '/'), '参数操作不属于Talent父节点')
        require(type(_pointer(talent['payload'], parameter['operation_pointer'][len(talent['json_pointer']):])) is dict,
                '参数操作原节点无效')
        require(parameter['ref'] not in ps and parameter['fingerprint'] == fingerprint(
            {k: v for k, v in parameter.items() if k != 'fingerprint'}), '属性参数重复或指纹不符')
        ps[parameter['ref']] = parameter
    for item in evidence:
        _object(item, ('id', 'kind', 'locator', 'sha256'))
        _id(item['id'])
        _sha(item['sha256'])
        _text(item['locator'], 4096)
        require(item['id'] not in es and item['kind'] in ('runtime_node', 'primary_test'), '属性证据重复或类型不支持')
        if item['kind'] == 'runtime_node':
            node = ns.get(item['id'])
            require(node is not None and item['sha256'] == node['node_sha256']
                    and item['locator'] == node['source_path'] + '#' + node['json_pointer'], '属性原节点证据不符')
        es[item['id']] = item
    for fact in facts:
        f = validate_fact(fact)
        require(f['kind'] in FACT_KINDS, '属性证明混入不支持的事实')
        ref = f['id'] + '@' + str(f['revision'])
        require(ref not in fs, '属性事实重复')
        require(set(f['evidence_refs']) <= set(es), '属性事实缺证据')
        review = reviews.get(ref)
        _object(review, ('status', 'reviewer', 'reviewed_at'))
        require(review['status'] == 'approved', '属性事实尚未批准或已撤销')
        _text(review['reviewer'], 256)
        _text(review['reviewed_at'], 80)
        datetime.fromisoformat(review['reviewed_at'])
        claim = f['claim']
        key = (claim['rule_key'], claim['effect_id'])
        group = groups.setdefault(key, {})
        require(f['kind'] not in group, '同一部件属性事实重复')
        group[f['kind']] = f
        if f['kind'] == 'attribute_sample':
            require(f['evidence_tier'] == 'primary_test' and any(es[x]['kind'] == 'primary_test'
                for x in f['evidence_refs']), '取样缺独立一手测试证据；原始节点不等于游戏实测')
        else:
            require(claim['node_id'] in f['evidence_refs'] and claim['node_id'] in ns,
                    '属性字段未被事实证据绑定')
        fs[ref] = {'fact': f, 'review': copy.deepcopy(review), 'fingerprint': fact_fingerprint(f)}
    require(set(reviews) == set(fs), '属性审核集合与事实不一致')
    require({ref for row in fs.values() for ref in row['fact']['evidence_refs']} == set(es), '存在未使用的属性事实证据')
    reads = []
    used_parameters = set()
    for (key, effect_id), group in groups.items():
        require(set(group) == FACT_KINDS, '属性读写取样事实未完整闭合')
        read, output = group['attribute_read']['claim'], group['attribute_output']['claim']
        require((read['node_id'], read['pointer']) == (output['node_id'], output['pointer']), '读写事实不属于同一原始表达式')
        require(read['parameter_ref'] in ps, '读取参数缺失')
        value = compile_attribute_expression(_pointer(ns[read['node_id']]['payload'], read['pointer']),
            claim=read, parameter=ps[read['parameter_ref']])
        used_parameters.add(read['parameter_ref'])
        reads.append({'rule_key': key, 'effect_id': effect_id, 'value': value,
            'benefit_type_id': output['benefit_type_id'], 'domain': output['domain'],
            'parameter_ref': read['parameter_ref'], 'fact_refs': sorted(
                f['id'] + '@' + str(f['revision']) for f in group.values())})
    require(all(p['unit'] == 'seconds' or p['ref'] in used_parameters for p in ps.values()), '存在未使用属性参数')
    result = {'verification_scope': 'materialized_consistency_no_source_authority',
              'facts': [fs[k] for k in sorted(fs)], 'parameters': copy.deepcopy(parameters),
              'reads': sorted(reads, key=lambda r: (r['rule_key'], r['effect_id'])),
              'evidence': copy.deepcopy(evidence), 'nodes': copy.deepcopy(nodes)}
    result['fingerprint'] = fingerprint(result)
    return result


def verify_attribute_facts(facts, *, nodes, parameters, evidence, reviews):
    try:
        return _verify_attribute_facts(facts, nodes=nodes, parameters=parameters, evidence=evidence, reviews=reviews)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('属性证明结构无效') from exc
