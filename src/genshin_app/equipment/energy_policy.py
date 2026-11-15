"""Production energy semantics binding; saved legacy numbers remain frozen."""
from __future__ import annotations

import copy

POLICY = 'energy-direct-base-er-v1'
CONTRACT = 'energy-settlement-policy-v1'


def mark_current_energy_policy(plan):
    """Called only for a new authoring/compose draft, never saved proof reads."""
    rotation = plan.get('rotation_model')
    if (plan.get('direct_energy_policy', POLICY) != POLICY
            or isinstance(rotation, dict) and rotation.get('direct_energy_policy', POLICY) != POLICY):
        from .schema import PlanError
        raise PlanError('不支持的直接回能作者政策；请按当前基础量政策重新材料化。', 'energy_policy_incompatible')
    plan['direct_energy_policy'] = POLICY
    if isinstance(plan.get('rotation_model'), dict):
        plan['rotation_model']['direct_energy_policy'] = POLICY
    return plan


def require_current_energy_rotation(rotation, *, path='rotation_model', allow_none=True):
    """A current claim requires a real compiled marker; never repair one here."""
    if rotation is None and allow_none:
        return
    if not isinstance(rotation, dict) or rotation.get('direct_energy_policy') != POLICY:
        from .schema import PlanError
        raise PlanError('执行轴缺少当前直接回能政策，不能声明或补造当前政策证明。',
            'energy_policy_execution_unmarked', {'path': path, 'expected_policy': POLICY,
                'actual_policy': rotation.get('direct_energy_policy') if isinstance(rotation, dict) else None})


def compile_current_energy_rotation(rotation):
    """Compile App execution metadata without rewriting the author document."""
    if rotation is None:
        return None
    if not isinstance(rotation, dict) or rotation.get('direct_energy_policy', POLICY) != POLICY:
        from .schema import PlanError
        raise PlanError('不支持的执行直接回能政策。', 'energy_policy_incompatible')
    compiled = copy.deepcopy(rotation)
    compiled['direct_energy_policy'] = POLICY
    return compiled


def validate_author_energy_policy(plan):
    """Validate optional declarations; do not mark or upgrade saved inputs."""
    rotation = plan.get('rotation_model')
    if (plan.get('direct_energy_policy', POLICY) != POLICY
            or isinstance(rotation, dict) and rotation.get('direct_energy_policy', POLICY) != POLICY):
        from .schema import PlanError
        raise PlanError('不支持的直接回能作者政策。', 'energy_policy_incompatible')


def uses_direct_energy(rotation, mechanics=None):
    return (any(e.get('type') == 'energy_direct' for e in (rotation or {}).get('events', []))
            or any(r.get('kind') == 'energy_direct' for rule in (mechanics or {}).get('rules', []) for r in rule.get('resources', [])))


def attach_energy_policy(result, rotation, mechanics=None):
    from .files import fingerprint
    require_current_energy_rotation(rotation, allow_none=not uses_direct_energy(rotation, mechanics))
    binding = {'contract': CONTRACT, 'policy_id': POLICY,
        'direct_energy_present': uses_direct_energy(rotation, mechanics),
        'execution_rotation_fingerprint': fingerprint(rotation)}
    binding['binding_fingerprint'] = fingerprint(binding)
    result['energy_settlement_policy'] = binding


def validate_energy_policy_binding(result, receipt=None, *, rotation=..., require_current=False):
    from .files import fingerprint
    binding = result.get('energy_settlement_policy')
    other = receipt.get('energy_settlement_policy') if isinstance(receipt, dict) else None
    if binding is None and other is None and not require_current:
        return None  # Historical frozen result: no upgrade and no default stamp.
    keys = {'contract', 'policy_id', 'direct_energy_present', 'execution_rotation_fingerprint', 'binding_fingerprint'}
    if (not isinstance(binding, dict) or set(binding) != keys or binding.get('contract') != CONTRACT
            or binding.get('policy_id') != POLICY or type(binding.get('direct_energy_present')) is not bool
            or fingerprint({k: v for k, v in binding.items() if k != 'binding_fingerprint'}) != binding['binding_fingerprint']
            or receipt is not None and other != binding):
        raise ValueError('直接回能政策生产证明缺失或不一致，请按当前基础量政策重新材料化并计算。')
    if rotation is ...:
        check = result.get('rotation_check')
        if not isinstance(check, dict) or 'resolved_rotation_model' not in check:
            raise ValueError('当前回能政策声明缺少实际执行轴上下文。')
        rotation = check['resolved_rotation_model']
    require_current_energy_rotation(rotation, allow_none=not binding['direct_energy_present'])
    if binding['execution_rotation_fingerprint'] != fingerprint(rotation):
        raise ValueError('直接回能政策未绑定实际执行的动作资源输入。')
    return binding


def require_replay_energy_policy(execution):
    rotation, mechanics = execution.get('rotation_model'), execution.get('mechanics_model')
    if uses_direct_energy(rotation, mechanics) and (rotation or {}).get('direct_energy_policy') != POLICY:
        from .execution_verification import ExecutionVerificationError
        error = ExecutionVerificationError('energy_policy_replay_incompatible',
            '旧直接回能执行材料没有基础量×ER政策标识，不能以当前引擎重放旧最终量；保留旧结果并由Agent重建当前输入。')
        error.reason = error.code
        error.details = {'path': '/rotation_model/direct_energy_policy', 'required_policy': POLICY,
                         'historical_result_modified': False, 'action': 'rematerialize_and_fresh_calculate'}
        raise error


def reconstruct_energy_policy_rotation(rotation, result, receipt):
    """Rebuild only the policy part of a stored private compilation.

    A known, validated saved declaration selects the same compiler as fresh
    execution. An old record without that declaration is copied unchanged;
    this is not a default upgrade, a new proof, or validation of other fields.
    The source consumer must still compare the complete reconstructed model.
    """
    binding = validate_energy_policy_binding(result, receipt,
        require_current=isinstance(rotation, dict)
        and rotation.get('direct_energy_policy') == POLICY)
    if binding is None:
        return copy.deepcopy(rotation)
    return compile_current_energy_rotation(rotation)
