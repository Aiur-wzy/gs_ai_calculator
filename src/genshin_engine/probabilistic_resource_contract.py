"""Shared declarative trial contract; resource owners supply balance and capacity."""
from genshin_engine.probabilistic_resource import ResourceTrial, probability


TRIAL_FIELDS = {'cost', 'base_grant', 'success_grant', 'success_waives_cost', 'chance'}
TRIAL_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': sorted(TRIAL_FIELDS),
    'properties': {
        **{key: {'type': 'integer', 'minimum': 0} for key in ('cost', 'base_grant', 'success_grant')},
        'success_waives_cost': {'type': 'boolean'},
        'chance': {'type': 'number', 'minimum': 0, 'maximum': 1},
    },
}


def resource_trial(value, capacity):
    if not isinstance(value, dict) or set(value) != TRIAL_FIELDS:
        raise ValueError('概率资源操作必须明确cost、base_grant、success_grant、success_waives_cost和chance。')
    trial = ResourceTrial(**value, capacity=capacity)
    if not (trial.cost or trial.base_grant or trial.success_grant):
        raise ValueError('概率资源操作必须有实际消耗或获得。')
    if trial.success_waives_cost and not trial.cost:
        raise ValueError('免消耗效果必须声明正数cost。')
    return trial


def validate_custom_outcome(trial, outcome):
    if type(outcome) is not bool:
        raise ValueError('自定义概率情景必须明确这次判定的成功或失败，不会自动掷骰。')
    chance = probability(trial.chance)
    if (chance == 0 and outcome) or (chance == 1 and not outcome):
        raise ValueError('指定结果与0%或100%的确定性概率冲突。')


def trial_mode(trial, mode=None, outcome=None):
    # Old saved inputs explicitly contain outcome. Preserve their scenario.
    mode = mode or ('custom' if outcome is not None else 'expected')
    if mode == 'custom':
        validate_custom_outcome(trial, outcome)
    elif mode != 'expected' or outcome is not None:
        raise ValueError('默认期望次数不接受outcome；指定情景使用custom并明确结果。')
    return mode
