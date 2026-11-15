"""One private identity/claim index and streaming rows; never cache projections."""
from genshin_app.reaction_resource_reporting import (
    CONTRACTS,
    ELEMENTS,
    REACTIONS,
    STATES,
    _index,
    _int,
    reaction_resource_window,
)
from genshin_app.review_batch_json import prepay


class ReviewLineage(dict):
    """Only operation-local identity indexes; disposed with the private snapshot."""
    reaction = None


def _rows_with_claims(result, by_occurrence, links, budget):
    audit = result.get('resource_reactions')
    if not isinstance(audit, dict) or audit.get('contract') not in CONTRACTS:
        return []
    occurrences = audit.get('occurrences', [])
    operations = audit.get('operations', [])
    trace = (result.get('shared_pools') or {}).get('trace', [])
    if not all(isinstance(value, list) for value in (occurrences, operations, trace)):
        return []

    for position, operation in enumerate(operations):
        budget.charge(1)
        if not isinstance(operation, dict):
            continue
        claimed = []
        ident = operation.get('occurrence_id')
        matching = by_occurrence.get(ident, []) if _int(ident) else []
        occurrence = matching[0][1] if len(matching) == 1 else {}
        # Pay only the fields actually scanned by this join, at every access.
        # Shared-pool trace entries outside this operation's span are not read.
        prepay([operation, occurrence], budget)
        problem = None if occurrence else ('反应记录缺失' if not matching else '反应记录不唯一')
        state = operation.get('state')
        row = {'path': f'/resource_reactions/operations/{position}',
               'occurrence_path': f'/resource_reactions/occurrences/{matching[0][0]}' if occurrence else None,
               'operation_id': operation.get('operation_id'), 'pool_id': operation.get('pool_id'),
               'source_id': operation.get('source_id'), 'source_owner': operation.get('source_owner'),
               'reaction_owner': occurrence.get('owner'), 'frontline': occurrence.get('frontline'),
               'parent_event_id': occurrence.get('parent_event_id'), 'parent_run_id': occurrence.get('parent_run_id'),
               'reaction_event_id': occurrence.get('reaction_event_id'), 'reaction_run_id': occurrence.get('reaction_run_id'),
               'time_us': occurrence.get('time_us'), 'target_id': occurrence.get('target_id'),
               'reaction_label': REACTIONS.get(occurrence.get('reaction_type'), '反应种类未记录'),
               'trigger_element_label': ELEMENTS.get(occurrence.get('trigger_element'), '未记录'),
               'aura_element_label': ELEMENTS.get(occurrence.get('aura_element'), '未记录'),
               'decision': operation.get('decision'), 'state': state,
               'state_label': STATES.get(state, '操作状态未说明'), 'association_issue': problem,
               'failure_class': operation.get('failure_class'), 'receipts': [],
               'verification_status': audit.get('verification_status'), 'verified_window': audit.get('verified_window'),
               'display_execution_window': reaction_resource_window(result),
               'source_authority': audit.get('source_authority')}
        if audit['contract'] == 'ordinary-reaction-resource-v2':
            relation, cost = links.get(operation.get('operation_id'), (([], [], []), 80))
            # Fixed qualification operations plus actual metadata string blocks;
            # relation joins were already performed and prepaid once in this index.
            budget.charge(cost)
            qualification = _lifecycle_qualification(result, audit, operation, occurrence, relation)
            row['lifecycle_qualification'] = qualification
            if qualification and qualification['issue']:
                row['association_issue'] = row['association_issue'] or qualification['issue']
        begin, end = operation.get('ledger_begin'), operation.get('actual_end')
        if state not in ('committed', 'committed_inconsistent'):
            # A planned/failed/no-match slot is never rendered as actual income.
            if end is not None:
                row['association_issue'] = '未提交状态与实际资源区间矛盾'
            yield row, claimed
            continue
        count, keys = operation.get('expected_count'), operation.get('keys')
        if (not _int(begin) or not _int(end) or not _int(count) or not isinstance(keys, list)
                or any(not isinstance(key, str) or not key for key in keys)
                or len(set(keys)) != len(keys)
                or end < begin or end > len(trace) or end - begin != count or count != len(keys)):
            row['association_issue'] = '资源流水区间不完整'
            yield row, claimed
            continue
        seen_keys = set()
        for ledger_index in range(begin, end):
            receipt = trace[ledger_index]
            prepay(receipt, budget)
            # The original exact membership comparison scans keys for each receipt.
            budget.charge(len(keys) + sum((len(key) + 255) // 256 for key in keys))
            if not isinstance(receipt, dict):
                row['association_issue'] = '资源流水格式不完整'
                break
            identity = {'type': 'operation', 'operation': 'grant', 'phase': 'post',
                        'operation_id': operation.get('operation_id'), 'pool_id': operation.get('pool_id'),
                        'source_id': operation.get('source_id'), 'run_id': occurrence.get('reaction_run_id'),
                        'event_id': occurrence.get('reaction_event_id'), 't_us': occurrence.get('time_us')}
            if any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value
                   for key, value in identity.items()) or receipt.get('key') not in keys:
                row['association_issue'] = '资源流水与反应操作不一致'
                break
            if receipt['key'] in seen_keys:
                row['association_issue'] = '资源键重复，区间未完整覆盖声明对象'
                break
            seen_keys.add(receipt['key'])
            item = {key: receipt.get(key) for key in ('id', 'key', 'before', 'requested', 'accepted',
                                                     'after', 'wasted', 'unit_scale', 'quantity_unit',
                                                     'reason', 'cooldown_until_us', 'succeeded')}
            item['path'] = f'/shared_pools/trace/{ledger_index}'
            if receipt.get('reason') == 'cooldown':
                item['label'] = '冷却拒绝'
            elif type(receipt.get('accepted')) in (int, float) and receipt['accepted'] > 0:
                item['label'] = '实际收入'
            elif receipt.get('accepted') == 0 and receipt.get('reason') == 'granted':
                item['label'] = '已尝试，实际收入为0'
            else:
                item['label'] = '资源尝试，结果见原账'
            row['receipts'].append(item)
            claimed.append(ledger_index)
        if row['association_issue']:
            row['receipts'] = []
        yield row, claimed


def iter_reaction_rows(result, budget, lineage):
    audit = result.get('resource_reactions') or {}
    if lineage.reaction is None:
        occurrences = audit.get('occurrences', [])
        prepay(occurrences, budget)
        by_occurrence = _index(occurrences)
        links = _lifecycle_links(result, budget)
        claims = {}
        for _, indices in _rows_with_claims(result, by_occurrence, links, budget):
            budget.charge(1 + len(indices))
            for index in indices:
                claims[index] = claims.get(index, 0) + 1
        lineage.reaction = by_occurrence, claims, links
    by_occurrence, claims, links = lineage.reaction
    for row, indices in _rows_with_claims(result, by_occurrence, links, budget):
        budget.charge(1 + len(indices))
        if any(claims[index] > 1 for index in indices):
            row['association_issue'] = '同一资源流水被重复引用'
            row['receipts'] = []
        yield row


def _lifecycle_links(result, budget):
    """Join immutable gate/definition/adoption identity once, retaining no rows."""
    audit = result.get('resource_reactions') or {}
    gates = (audit.get('program') or {}).get('lifecycle_gates', [])
    models = (result.get('resource_reaction_execution') or {}).get('mechanics_model') or {}
    lives = (models.get('state_runtime') or {}).get('lifecycles', [])
    adopted = (result.get('resource_reaction_source_adoption') or {}).get('lifecycle_gates', [])
    prepay([gates, lives, adopted], budget)
    by_operation, by_life, by_adopted = {}, {}, {}
    for gate in gates:
        if isinstance(gate, dict):
            by_operation.setdefault(gate.get('operation_id'), []).append(gate)
    for life in lives:
        if isinstance(life, dict):
            by_life.setdefault(life.get('id'), []).append(life)
    for binding in adopted if isinstance(adopted, list) else []:
        if isinstance(binding, dict) and isinstance(binding.get('gate'), dict):
            by_adopted.setdefault(binding['gate'].get('operation_id'), []).append(binding)
    links = {}
    for operation, matching in by_operation.items():
        gate = matching[0] if len(matching) == 1 else {}
        candidates = by_adopted.get(operation, [])
        prepay(candidates, budget)
        bindings = [binding for binding in candidates if binding['gate'] == gate]
        definitions = by_life.get(gate.get('lifecycle_id'), [])
        life = definitions[0] if len(definitions) == 1 else {}
        binding = bindings[0] if len(bindings) == 1 else {}
        fields = [gate.get(key) for key in ('lifecycle_id', 'window', 'generation_policy')]
        fields.extend(life.get(key) for key in ('name_zh', 'source_id', 'owner'))
        fields.extend(binding.get(key) for key in ('lifecycle_id', 'source_id', 'source_owner', 'status', 'lifecycle_source_status'))
        cost = 80 + sum((len(value) + 255) // 256 for value in fields if isinstance(value, str))
        links[operation] = (matching, definitions, bindings), cost
    return links


def _lifecycle_qualification(result, audit, operation, occurrence, links):
    gates, lives, bindings = links
    raw = operation.get('lifecycle_qualification')
    if not gates and raw is None:
        return None
    gate = gates[0] if len(gates) == 1 else {}
    lid = gate.get('lifecycle_id')
    life = lives[0] if len(lives) == 1 else {}
    row = {'lifecycle_id': lid, 'name_zh': life.get('name_zh') or lid or '领域未记录',
           'source_id': life.get('source_id'), 'source_owner': life.get('owner'),
           'qualified': None, 'reason': None, 'reason_label': '领域资格待核',
           'generation_uid': None, 'revision': None, 'established_us': None, 'end_us': None, 'issue': None}
    if len(gates) != 1:
        row['issue'] = '领域资格与合同关联不唯一'
        return row
    adoption = result.get('resource_reaction_source_adoption')
    binding = bindings[0] if len(bindings) == 1 else {}
    if (len(lives) != 1 or len(bindings) != 1
            or adoption.get('contract') != 'ordinary-reaction-resource-adoption-v2'
            or not isinstance(life.get('source_id'), str) or not life['source_id']
            or not isinstance(life.get('owner'), str) or not life['owner']
            or binding.get('lifecycle_id') != lid or binding.get('source_id') != life['source_id']
            or binding.get('source_owner') != life['owner'] or life['owner'] != operation.get('source_owner')
            or binding.get('status') != 'ready' or binding.get('lifecycle_source_status') != 'ready'):
        row['issue'] = '领域定义、来源与采用记录关联不一致'
        return row
    if raw is None and operation.get('state') in ('no_match', 'unknown'):
        row['reason_label'] = '触发未匹配或待核，未检查领域'
        return row
    identity = {'operation_id': operation.get('operation_id'), 'pool_id': operation.get('pool_id'),
                'lifecycle_id': lid, 'reaction_run_id': occurrence.get('reaction_run_id'),
                'parent_run_id': occurrence.get('parent_run_id'), 'time_us': occurrence.get('time_us'),
                'origin': occurrence.get('origin'),
                'window': gate.get('window'), 'generation_policy': gate.get('generation_policy'),
                'authority': 'actual_completed_state2_current_generation'}
    if (not isinstance(raw, dict) or type(raw.get('qualified')) is not bool
            or any(type(raw.get(k)) is not type(v) or raw.get(k) != v for k, v in identity.items())):
        row['issue'] = '领域资格与实际反应关联不一致'
        return row
    row.update({k: raw.get(k) for k in ('qualified', 'reason', 'generation_uid', 'revision', 'established_us', 'end_us')})
    labels = {'eligible': '领域生效，允许尝试收入', 'not_started': '领域尚未创建',
              'not_established': '领域尚未建立', 'ended': '领域已结束或不在有效窗口'}
    row['reason_label'] = labels.get(raw.get('reason'), '资格原因未分类，见原始记录')
    reason, phase = raw.get('reason'), raw.get('phase')
    t, created, start, end = (raw.get(k) for k in ('time_us', 'created_us', 'established_us', 'end_us'))
    if not _int(t) or not _int(raw.get('origin')):
        row['issue'] = '领域资格的反应时点或窗口身份无效'
    elif reason == 'not_started':
        if (raw['qualified'] or phase != 'not_started'
                or any(raw.get(k) is not None for k in ('generation_uid', 'revision', 'created_us', 'established_us', 'end_us'))):
            row['issue'] = '未创建领域不能带有已建立的状态'
    elif (reason not in ('eligible', 'not_established', 'ended')
            or phase not in ('created', 'established', 'closing', 'ended')
            or not isinstance(raw.get('generation_uid'), str) or not raw['generation_uid']
            or not _int(raw.get('revision')) or raw['revision'] < 1
            or not all(_int(v) for v in (created, start, end)) or not created <= start < end or t < created):
        row['issue'] = '领域资格的状态、代标识或时间窗无效'
    else:
        # Compare recorded fields only: do not run the lifecycle or resource engine.
        eligible = phase == 'established' and start <= t < end
        expected_reason = 'eligible' if eligible else 'not_established' if phase == 'created' and t < end else 'ended'
        if raw['qualified'] != eligible or reason != expected_reason:
            row['issue'] = '领域资格与已记录阶段、判定窗口矛盾'
    if ((operation.get('state') == 'blocked_lifecycle' and raw['qualified'])
            or operation.get('state') in ('committed', 'committed_inconsistent') and not raw['qualified']):
        row['issue'] = '领域资格与资源提交状态矛盾'
    return row
