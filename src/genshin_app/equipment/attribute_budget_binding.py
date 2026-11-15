"""Offline budget evidence binding; no live policy fallback or result re-signing."""
from __future__ import annotations

from genshin_engine.attribute_budget import (
    BINDING_CONTRACT,
    NOT_APPLICABLE_CONTRACT,
    POLICY_CONTRACT,
    PRODUCER_CONTRACT,
    budget_fingerprint,
    normalize_attribute_budget,
    validate_calculation_options,
)


def attach_nonconsumer_budget(result, execution_model):
    """Stamp only a newly executed App result, never a restored historical file."""
    if isinstance(execution_model, dict) and execution_model.get('attribute_model') is not None:
        if result.get('calculation_producer_contract') != PRODUCER_CONTRACT:
            raise ValueError('实际属性执行缺少生产者预算证明')
        return
    if 'attribute_ledger' in result or 'attribute_execution_budget' in result:
        raise ValueError('无属性消费者的执行与保存账本不一致')
    binding = {'contract': NOT_APPLICABLE_CONTRACT, 'state':'not_applicable',
               'reason':'no_attribute_runtime', 'execution_model_fingerprint':budget_fingerprint(execution_model)}
    binding['binding_fingerprint'] = budget_fingerprint(binding)
    result['attribute_execution_budget'] = binding
    result['calculation_producer_contract'] = PRODUCER_CONTRACT


def validate_attribute_budget_binding(result, receipt=None, plan=None, *, require_current=False,
                                      execution_model=...):
    if (not isinstance(result, dict) or receipt is not None and not isinstance(receipt, dict)
            or plan is not None and not isinstance(plan, dict)):
        raise ValueError('属性执行预算的结果、回执和输入必须是对象')
    marker = result.get('calculation_producer_contract')
    binding = result.get('attribute_execution_budget')
    receipt_binding = receipt.get('attribute_execution_budget') if isinstance(receipt, dict) else None
    if not require_current and marker is None and binding is None and receipt_binding is None:
        return None  # Historical output is not rewritten with today's defaults.
    if marker != PRODUCER_CONTRACT or not isinstance(binding, dict):
        raise ValueError('当前计算缺少受支持的属性执行预算证明')
    if binding.get('contract') == NOT_APPLICABLE_CONTRACT:
        keys = {'contract','state','reason','execution_model_fingerprint','binding_fingerprint'}
        if (set(binding) != keys or binding.get('state') != 'not_applicable'
                or binding.get('reason') != 'no_attribute_runtime' or 'attribute_ledger' in result):
            raise ValueError('无属性消费者的预算证明不完整或与账本冲突')
        if (not isinstance(binding['execution_model_fingerprint'],str)
                or len(binding['execution_model_fingerprint']) != 64
                or any(c not in '0123456789abcdef' for c in binding['execution_model_fingerprint'])
                or budget_fingerprint({k:v for k,v in binding.items() if k!='binding_fingerprint'}) != binding['binding_fingerprint']):
            raise ValueError('无属性消费者的预算证明指纹无效')
        if receipt is not None and (receipt_binding != binding or receipt.get('calculation_producer_contract') != marker):
            raise ValueError('结果回执缺少一致的无属性消费者证明')
        if plan is not None and (plan.get('mechanics_model') or {}).get('attribute_model') is not None:
            raise ValueError('原生属性消费者不能声明不适用预算')
        if (execution_model is not ...
                and ((isinstance(execution_model,dict) and execution_model.get('attribute_model') is not None)
                     or binding['execution_model_fingerprint'] != budget_fingerprint(execution_model))):
            raise ValueError('不适用预算声明与实际编译执行不一致')
        return binding
    keys = {'contract', 'policy', 'model_contract', 'usage_fingerprint', 'declaration_fingerprint', 'binding_fingerprint'}
    if set(binding) != keys or binding['contract'] != BINDING_CONTRACT:
        raise ValueError('属性执行预算证明版本或字段不受支持')
    policy = binding['policy']
    if not isinstance(policy, dict) or set(policy) != {'contract', 'requested', 'effective', 'policy_fingerprint'} or policy['contract'] != POLICY_CONTRACT:
        raise ValueError('属性执行预算策略不受支持')
    # Version v1 owns its defaults. New defaults must use a new policy version.
    if normalize_attribute_budget(policy['requested']) != policy:
        raise ValueError('属性执行预算策略指纹或有效值不一致')
    if budget_fingerprint({k:v for k,v in binding.items() if k!='binding_fingerprint'}) != binding['binding_fingerprint']:
        raise ValueError('属性执行预算证明指纹不一致')
    report = result.get('attribute_ledger')
    if (not isinstance(report, dict) or report.get('contract') not in
            ('attribute-ledger-v1','attribute-ledger-v2','attribute-ledger-v3','attribute-ledger-v4')
            or binding['model_contract'] != report.get('contract')):
        raise ValueError('属性执行预算与实际属性账合同不一致')
    if not isinstance(report.get('declarations'), dict) or report['declarations'].get('contract') != report['contract']:
        raise ValueError('属性执行预算缺少一致的实际声明')
    if (binding['usage_fingerprint'] != budget_fingerprint(report.get('usage'))
            or binding['declaration_fingerprint'] != budget_fingerprint(report.get('declarations'))):
        raise ValueError('属性执行预算与实际取样记录不一致')
    if execution_model is not ...:
        actual = execution_model.get('attribute_model') if isinstance(execution_model,dict) else None
        if (not isinstance(actual,dict) or budget_fingerprint(actual) != binding['declaration_fingerprint']
                or validate_calculation_options(execution_model.get('execution_options', {'schema_version':1})) != policy):
            raise ValueError('属性执行预算与本次编译模型、声明或策略不一致')
    usage = report['usage']
    effective = policy['effective']
    if not isinstance(usage, dict):
        raise ValueError('属性执行预算缺少实际累计计量')  # noqa: TRY004 - malformed saved evidence
    expected_ledger_keys = set(effective) - {'dependency_depth','output_bytes'}
    if not isinstance(usage.get('limits'), dict) or set(usage['limits']) != expected_ledger_keys:
        raise ValueError('属性执行预算缺少完整的实际计量限额')
    if (report['contract'] in ('attribute-ledger-v2','attribute-ledger-v3','attribute-ledger-v4')
            and (not isinstance(usage.get('graph_limits'), dict)
                 or set(usage['graph_limits']) != {'dependency_depth','output_bytes'})):
        raise ValueError('属性执行图预算缺少实际计量限额')
    for key, value in usage.get('limits', {}).items():
        if type(value) is not int or key not in effective or value != effective[key]:
            raise ValueError('属性执行预算与实际累计计量限额不一致')
    for key, value in usage.get('graph_limits', {}).items():
        if type(value) is not int or key not in effective or value != effective[key]:
            raise ValueError('属性执行图预算与实际计量不一致')
    for key in ('query_work','registration_work','execution_work','declaration_bytes','receipt_bytes','frames','contributions'):
        value = usage.get(key)
        if type(value) is not int or not 0 <= value <= effective[key]:
            raise ValueError('属性执行预算累计使用量缺失或超出实际限额')
    capture = usage.get('capture_work')
    if type(capture) is not int or not 0 <= capture <= usage['execution_work']:
        raise ValueError('属性捕获工作量缺失或与实际执行不一致')
    if report['contract'] in ('attribute-ledger-v2','attribute-ledger-v3','attribute-ledger-v4'):
        value = usage.get('output_bytes')
        if type(value) is not int or not 0 <= value <= effective['output_bytes']:
            raise ValueError('属性图输出计量缺失或超出实际限额')
    if receipt is not None and (receipt_binding != binding or receipt.get('calculation_producer_contract') != marker):
        raise ValueError('结果回执缺少一致的执行预算证明')
    if plan is not None:
        requested = (plan.get('calculation_options') or {}).get('attribute_limits', {})
        if requested != policy['requested']:
            raise ValueError('原生输入的执行预算与实际采用策略不一致')
    return binding
