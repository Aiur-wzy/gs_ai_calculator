"""Same-read level/action material, with no dispatch authority."""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass

from genshin_db import runtime_talent_level as level_module
from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint
from genshin_db.runtime_talent_level import (
    get_level_parameter_context,
    project_action_parameter_at_level,
)

LIMITS = {'max_operations': 128, 'max_references': 512, 'max_candidates': 64,
          'max_nodes': 50000, 'max_depth': 64, 'max_bytes': 2 * 1024 * 1024}
_KINDS = {'AddTalentExtraLevel': 'skill_group_level', 'ModifyAbility': 'ability_parameter',
          'UnlockTalentParam': 'ability_control', 'AddAbility': 'ability_creation',
          'ModifySkillPoint': 'action_charge', 'ModifySkillCD': 'action_cooldown'}
_PARAMETER = re.compile(r'(-?)%([1-9][0-9]?)\Z')


def action_level_context_capabilities():
    return {'contract': 'level-action-context-v1', 'limits': dict(LIMITS),
            'selection': 'slot与skill_id二选一；base_level默认10、范围1..10；不接受talent_level或effective_level输入。',
            'scope': '同快照静态等级、原始参数父链及全部激活命座的目标定位；不求值ModifyAbility，不判定与所选攻击无关。',
            'states': ['static_level_status', 'source_assembly_status', 'footprint_status'],
            'target_domains': dict(_KINDS), 'source_authority': 'not_granted',
            'operation_execution': 'not_evaluated', 'action_dispatch': 'pending',
            'whole_skill_coverage': 'partial', 'legacy_queries': 'unchanged'}


def _error(message, reason='context_inconsistent'):
    raise RuntimeConfigError(message, reason, 413 if reason == 'response_too_large' else 409)


def _bounded(value):
    """Bound raw work before copying or canonicalizing retained source material."""
    stack, nodes, estimate = [(value, 0)], 0, 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > LIMITS['max_nodes'] or depth > LIMITS['max_depth']:
            _error('动作等级资料节点/深度超限', 'response_too_large')
        if isinstance(item, dict):
            if len(item) > LIMITS['max_nodes']:
                _error('动作等级资料容器超限', 'response_too_large')
            if any(type(key) is not str for key in item):
                _error('动作等级资料包含非法JSON键', 'dataset_invalid')
            stack.extend((part, depth + 1) for pair in item.items() for part in pair)
            estimate += 2 + len(item) + max(0, len(item) - 1)
        elif isinstance(item, list):
            if len(item) > LIMITS['max_nodes']:
                _error('动作等级资料容器超限', 'response_too_large')
            stack.extend((part, depth + 1) for part in item)
            estimate += 2 + max(0, len(item) - 1)
        elif type(item) is str:
            try:
                estimate += len(item.encode('utf8')) + 2
            except UnicodeEncodeError as exc:
                raise RuntimeConfigError('动作等级资料含非法Unicode字符', 'dataset_invalid', 409) from exc
        elif type(item) is int:
            if item.bit_length() > 4096:
                _error('动作等级资料整数超限', 'response_too_large')
            estimate += len(str(item))
        elif type(item) is float:
            if not math.isfinite(item):
                _error('动作等级资料含非有限数值', 'dataset_invalid')
            estimate += len(dumps(item))
        elif item is None or type(item) is bool:
            estimate += 5 if item is False else 4
        else:
            _error('动作等级资料包含非JSON数据', 'dataset_invalid')
        if estimate > LIMITS['max_bytes']:
            _error('动作等级资料字节超限', 'response_too_large')
    size = len(dumps(value).encode('utf8'))
    if size > LIMITS['max_bytes']:
        _error('动作等级资料字节超限', 'response_too_large')
    return size


class _Budget:
    def __init__(self):
        self.bytes = 0

    def add(self, value):
        self.bytes += _bounded(value)
        if self.bytes > LIMITS['max_bytes']:
            _error('动作等级完整材料累计超限', 'response_too_large')


@dataclass(frozen=True)
class LevelActionContext:
    # A Python type is not proof of a fresh DB read. Future compilers must obtain
    # this material through their own transaction; this slice never authorizes one.
    _parameter_context: dict
    _public: dict


def project_level_action_context(context):
    if not isinstance(context, LevelActionContext):
        raise ValueError('需要本次动作等级资料上下文')  # noqa: TRY004
    return copy.deepcopy(context._public)


def project_level_action_parameter(context, param_index):
    if not isinstance(context, LevelActionContext):
        raise ValueError('需要本次动作等级资料上下文')  # noqa: TRY004
    return project_action_parameter_at_level(context._parameter_context, param_index)


def _normalize_operation(op, recipe):
    if not isinstance(op, dict):
        return copy.deepcopy(op)
    result = {}
    for key, value in op.items():
        key = recipe['fields'].get(key, key)
        if key in result:
            return None
        if key == '$type' and isinstance(value, str):
            value = recipe['types'].get(value, value)
        result[key] = copy.deepcopy(value)
    return result


def _agree(level, facts):
    if level['snapshot'] != facts['snapshot'] or level['selection'] != facts['selection']:
        _error('动作资料与等级资料不是同一选择/快照')
    if level['level']['effective_level'] != facts['level']['effective_level']:
        _error('动作资料使用的有效等级不一致')
    for name in ('avatar', 'depot', 'skill', 'proud_skill'):
        left, right = level['records'].get(name), facts['records'].get(name)
        if left is None or right is None:
            _error('动作/等级上下文缺少共同父记录')
        for key in ('definition', 'source_path', 'source_sha256', 'json_pointer'):
            if left[key] != right[key]:
                _error('动作/等级上下文父记录身份不同')
        if any(left['record'].get(k) != v for k, v in right['fields'].items()):
            _error('动作/等级上下文父记录字段不一致')


def get_level_action_context(db_path, *, character_id, slot=None, skill_id=None,
                             skill_depot_id=None, constellation=0, base_level=10,
                             version_line='release', snapshot_id=None):
    from genshin_db.runtime_action_facts import get_runtime_action_facts
    from genshin_db.runtime_queries import (
        _definition_summary,
        _lookup,
        _Nodes,
        _read,
        _references,
    )

    selection = {'character_id': character_id, 'slot': slot, 'skill_id': skill_id,
                 'skill_depot_id': skill_depot_id, 'constellation': constellation,
                 'base_level': base_level, 'version_line': version_line, 'snapshot_id': snapshot_id}
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        parameter = get_level_parameter_context(db_path, **selection)
        budget = _Budget()
        budget.add(parameter)
        level, recipe = parameter['level_profile'], level_module._recipe()
        if level['snapshot'] != state:
            _error('等级上下文未绑定本次读取快照')
        facts = None
        if level['status'] == 'ready':
            args = {k: v for k, v in selection.items() if k != 'base_level'}
            args.update(snapshot_id=state['snapshot_id'], talent_level=level['level']['effective_level'])
            facts = get_runtime_action_facts(db_path, **args)
            budget.add(facts)
            _agree(level, facts)
        nodes = _Nodes(conn, version_line)
        footprints, target_nodes, target_cache, diagnostics = [], {}, {}, []
        references_count = 0
        def operation_key(origin):
            return tuple(origin[key] for key in ('source_path', 'source_sha256', 'json_pointer', 'node_sha256'))

        reviewed = {operation_key(op['origin']): op
                    for op in level['other_operations'] if op['status'] == 'outside_static_level_scope'}
        levels = {operation_key(op['origin']): op
                  for op in level['contributions']}
        mapping_ready = state['upstream_commit'] == recipe['commit']
        if len(level['records']['talents']) != constellation:
            diagnostics.append({'code': 'active_talent_records_incomplete',
                                'expected': constellation, 'actual': len(level['records']['talents'])})
        if not mapping_ready:
            diagnostics.append({'code': 'operation_mapping_unreviewed'})

        def materialize(row):
            ident = row['definition_id']
            if ident not in target_nodes:
                raw, origin = nodes.node(row)
                budget.add(raw)
                target_nodes[ident] = {'definition': _definition_summary(row), **origin, 'raw': copy.deepcopy(raw)}
            return ident

        def ability_target(name):
            if name not in target_cache:
                found = _lookup(conn, version_line, 'ability', name)
                if len(found) > LIMITS['max_candidates']:
                    _error('动作等级Ability候选数量超限', 'response_too_large')
                ids = [materialize(row) for row in found]
                target_cache[name] = {'kind': 'ability', 'key': name, 'candidate_ids': ids,
                                      'status': 'resolved' if len(ids) == 1 else 'missing' if not ids else 'ambiguous'}
            return copy.deepcopy(target_cache[name])

        for talent in level['records']['talents']:
            config = talent.get('config')
            if config is None:
                diagnostics.append({'code': 'active_talent_config_missing', 'rank': talent['rank'],
                                    'source_path': talent['source_path'], 'json_pointer': talent['json_pointer']})
                continue
            raw = config['raw']
            definition = config['definition']
            if not isinstance(raw, list):
                diagnostics.append({'code': 'active_talent_operations_invalid', 'rank': talent['rank'],
                                    'definition_id': definition['definition_id']})
                continue
            if len(footprints) + len(raw) > LIMITS['max_operations']:
                _error('动作等级操作数量超限', 'response_too_large')
            refs = _references(conn, version_line, definition['definition_id'], nodes)
            references_count += len(refs)
            if references_count > LIMITS['max_references']:
                _error('动作等级引用数量超限', 'response_too_large')
            budget.add(refs)
            normalized = nodes.normalization.normalize(raw, kind='talent_config') if nodes.normalization else raw
            if facts is not None:
                matches = [t for t in facts['talents'] if t['role'] == 'constellation'
                           and t['definition']['definition_id'] == definition['definition_id']]
                if len(matches) != 1 or matches[0]['operations_raw'] != raw or matches[0]['operations_normalized'] != normalized:
                    _error('激活命座的完整原始/标准化操作不一致')
                if matches[0]['param_list'] != talent['record'].get('paramList', []):
                    _error('激活命座参数表不同')
                expected_parent = {k: talent[k] for k in ('source_path', 'source_sha256', 'json_pointer')}
                if (matches[0]['parameter_record_origin'] != expected_parent
                        or matches[0]['references'] != refs
                        or any(matches[0][k] != config[k] for k in ('source_path', 'source_sha256', 'json_pointer'))):
                    _error('激活命座父来源或完整引用不一致')
            for index, op in enumerate(normalized):
                pointer = definition['json_pointer'] + '/' + str(index)
                value = _normalize_operation(op, recipe)
                origin = {'source_path': config['source_path'], 'source_sha256': config['source_sha256'],
                          'json_pointer': pointer, 'node_sha256': fingerprint(raw[index])}
                key = operation_key(origin)
                accepted = mapping_ready and (key in reviewed or key in levels)
                kind = value.get('$type') if isinstance(value, dict) else None
                typed = _KINDS.get(kind, 'unknown') if isinstance(kind, str) else 'unknown'
                op_refs = [ref for ref in refs if ref['json_pointer'].startswith(pointer + '/')]
                if len(op_refs) > 64:
                    _error('动作等级单操作引用数量超限', 'response_too_large')
                row = {'rank': talent['rank'], 'talent_id': talent['record']['talentId'],
                       'operation_index': index, 'parent_definition_id': definition['definition_id'],
                       'parameter_record_definition_id': talent['definition']['definition_id'],
                       'source': origin,
                       'raw': copy.deepcopy(raw[index]), 'normalized': value, 'references': copy.deepcopy(op_refs),
                       'target_domain': typed if accepted else 'unknown', 'status': 'pending',
                       'selected_dependency_status': 'not_evaluated', 'operation_execution': 'not_evaluated'}
                if accepted and typed.startswith('ability_'):
                    target = ability_target(value['abilityName'])
                    relation = [r for r in op_refs if r['field_name'] == 'abilityName']
                    if (len(relation) != 1 or relation[0]['target_key'] != target['key']
                            or sorted(relation[0]['candidate_ids']) != sorted(target['candidate_ids'])):
                        _error('操作Ability精确定位与索引引用不一致')
                    row['target'] = target
                    if target['status'] == 'resolved':
                        row['status'] = 'target_identified'
                    if typed == 'ability_parameter':
                        row['symbol'] = value['paramSpecial']
                        expr = value['paramDelta']
                        match = _PARAMETER.fullmatch(expr)
                        params = talent['record'].get('paramList', [])
                        location = {'expression_raw': expr, 'status': 'unresolved', 'execution': 'not_evaluated'}
                        if match and isinstance(params, list) and int(match[2]) <= len(params):
                            offset = int(match[2]) - 1
                            location.update(status='table_input_located', param_index=offset,
                                            sign=-1 if match[1] else 1, raw_input=copy.deepcopy(params[offset]),
                                            json_pointer=talent['json_pointer'] + f'/paramList/{offset}')
                        row['parameter_location'] = location
                    elif typed == 'ability_control':
                        row['symbol'] = value['talentParam']
                elif accepted and typed == 'skill_group_level':
                    contribution = levels[key]
                    target = contribution['target_skill']
                    row['target'] = {'kind': 'skill_group', 'skill_id': target['record']['id'],
                                     'group_id': target['record']['proudSkillGroupId'],
                                     'definition_id': target['definition']['definition_id'],
                                     'matches_selected_group': contribution['matches_selected_group'],
                                     'extra_level': contribution['extra_level']}
                    row['status'] = 'target_identified'
                elif accepted and typed in ('action_charge', 'action_cooldown'):
                    found = _lookup(conn, version_line, 'excel_record', {'id': value['skillID']},
                                    'ExcelBinOutput/AvatarSkillExcelConfigData.json')
                    if len(found) > LIMITS['max_candidates']:
                        _error('动作约束技能候选数量超限', 'response_too_large')
                    ids = [materialize(entry) for entry in found]
                    row['target'] = {'kind': 'skill', 'skill_id': value['skillID'], 'candidate_ids': ids}
                    if len(ids) == 1:
                        row['status'] = 'target_identified'
                if not accepted:
                    row['issue'] = 'unreviewed_operation_shape_or_mapping'
                budget.add(row)
                footprints.append(row)
        targets_identified = not diagnostics and all(r['status'] == 'target_identified' for r in footprints)
        assembly_ready = facts is not None and facts['status'] == 'ready' and targets_identified
        result = {'contract': 'level-action-context-v1', 'snapshot': copy.deepcopy(state),
                  'selection': copy.deepcopy(level['selection']), 'level': copy.deepcopy(level['level']),
                  'static_level_status': level['status'],
                  'source_assembly_status': 'ready' if assembly_ready else 'pending',
                  'footprint_status': 'target_identified' if targets_identified else 'pending',
                  'level_profile': copy.deepcopy(level), 'facts': copy.deepcopy(facts),
                  'parameter_descriptions': copy.deepcopy(parameter['descriptions']),
                  'footprints': footprints, 'target_material': list(target_nodes.values()), 'issues': diagnostics,
                  'scope': 'same_read_source_material_only', 'source_authority': 'not_granted',
                  'action_dispatch': 'pending', 'whole_skill_coverage': 'partial',
                  'operation_execution': 'not_evaluated', 'limits': dict(LIMITS)}
        _bounded(result)
        result['content_fingerprint'] = fingerprint(result)
        _bounded(result)
        return LevelActionContext(copy.deepcopy(parameter), result)


def get_runtime_action_level_context(db_path, **selection):
    return project_level_action_context(get_level_action_context(db_path, **selection))
