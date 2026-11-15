"""Pure current-input projection shared by native candidates and CLI authors."""
from __future__ import annotations

import copy

from .files import fingerprint
from .schema import PlanError


def action_checkpoint(board, revision):
    projection = [{'id': row['id'], 'character': row['character'],
        'action_type_id': row['action_type_id'], 'start': row['start'],
        'variant': row.get('variant', ''), 'order': row.get('order', index)}
        for index, row in enumerate(board['actions'])]
    return {'revision': revision, 'fingerprint': fingerprint({'duration': board['duration'], 'actions': projection}),
            'action_ids': [row['id'] for row in projection]}


def native_action_board(value, *, allow_incomplete=False):
    from genshin_engine.rotation import validate_rotation_model

    template = value.get('manual_template', {})
    rotation = value.get('rotation_model')
    actions, issues = [], []
    if rotation is not None:
        checked = validate_rotation_model(rotation)
        if checked['status'] == 'invalid' and not allow_incomplete:
            raise PlanError('原生动作结构尚不可投影，请按动作合同修正。',
                'agent_native_action_projection_invalid', {'issues': checked['issues'][:20]})
        if not isinstance(rotation, dict):
            rotation = {}
        def rows(items):
            return [row for row in items if isinstance(row, dict)] if isinstance(items, list) else []
        abilities = {}
        for ability in rows(rotation.get('abilities')):
            if isinstance(ability.get('id'), str):
                abilities.setdefault(ability['id'], []).append(ability)
        owners = {row['id'] for row in rows(template.get('characters')) if isinstance(row.get('id'), str)}
        rotation_owners = {row['id'] for row in rows(rotation.get('characters')) if isinstance(row.get('id'), str)}
        for index, action in enumerate(rows(rotation.get('actions'))):
            matches = abilities.get(action.get('ability_id'), []) if isinstance(action.get('ability_id'), str) else []
            path = f'rotation_model.actions[{index}].ability_id'
            if len(matches) != 1:
                issues.append({'code': 'native_action_ability_missing' if not matches else 'native_action_ability_ambiguous',
                    'path': path, 'message': '动作必须精确引用一个唯一技能定义。'})
            ability = matches[0] if len(matches) == 1 else {}
            owner = ability.get('character')
            if ability and (not isinstance(owner, str) or owner not in owners or owner not in rotation_owners):
                issues.append({'code': 'native_action_owner_missing', 'path': path,
                               'message': '动作所引用技能的角色必须同时存在于模板和动作队伍。'})
                owner = None
            if ability.get('action_type_id') == 'action.switch':
                owner = action.get('switch_to')
            actions.append({'id': action.get('id', 'unresolved.'+str(index)), 'character': owner,
                'action_type_id': ability.get('action_type_id'), 'variant': ability.get('variant', ''),
                'start': action.get('start'), 'order': action.get('order', index)})
        if issues and not allow_incomplete:
            raise PlanError('原生动作关系尚未明确，请修复技能引用或所属角色后继续。',
                'agent_native_action_projection_invalid', {'issues': issues[:20], 'issues_total': len(issues)})
    return {'duration': rotation.get('period') if rotation is not None else template.get('loop_duration'),
            'team': copy.deepcopy(template.get('characters', [])), 'actions': actions,
            **({'issues': issues} if issues else {})}
