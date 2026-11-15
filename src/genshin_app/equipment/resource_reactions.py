"""Two-stage App source projection for RQ1; no new game-source authority."""
from __future__ import annotations

import copy

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_engine.resource_reaction_program import Program, ReactionResourceError

CONTRACT = 'ordinary-reaction-resource-adoption-v1'
FIELD = 'resource_reaction_contract'


def prepare_resource_reactions(original, delivered, pool_adoptions, missing, *, characters,
                               source_reviews, context_fingerprint, state_adoptions=()):
    """Read the complete original graph, then only project contract members.

    Source decisions are borrowed from the existing, already closed resource
    source graph. Neither graph nor any of its adoptions is mutated here.
    """
    if FIELD not in original:
        return None, None
    issues = []
    try:
        Program(original, sorted(characters))
    except ReactionResourceError as exc:
        if exc.issue['severity'] == 'error':
            raise PlanError('实际反应资源完整原图无效，排除来源不能隐藏错误',
                            'invalid_resource_reaction_source', exc.issue) from exc
        issues.append(copy.deepcopy(exc.issue))
        missing('resource_reaction_scope_pending', exc.issue['path'], exc.issue['message'], 'review_mechanics')
    declared = sorted(original[FIELD]['operation_ids'])
    version_two = original[FIELD]['schema_version'] == 2
    from .resource_lifecycle_sources import gate_sources, projected_declaration
    gates = (gate_sources(original, delivered, state_adoptions, source_reviews, context_fingerprint)
             if version_two else [])
    gates_by_operation = {row['gate']['operation_id']: row for row in gates}
    original_operations = {row['id']: row for row in original['shared_pools']['operations']}
    original_pools = {row['id']: row for row in original['shared_pools']['pools']}
    live_operations = {row['id']: row for row in delivered.get('shared_pools', {}).get('operations', [])}
    live_rules = {row['id']: row for row in delivered['rules']}
    records_by_id = {}
    for row in pool_adoptions:
        if row['kind'] != 'operations':
            continue
        if row['id'] in records_by_id:
            raise PlanError('反应资源操作的来源采用回执重复', 'duplicate_delivery')
        records_by_id[row['id']] = row
    records, active = [], []
    for identity in declared:
        operation = original_operations[identity]
        pool = original_pools[operation['pool_id']]
        source = records_by_id.get(identity)
        row = {'operation_id': identity, 'source_id': operation['source_id'], 'pool_id': pool['id'],
               'source_owner': pool['owner'], 'status': 'pending'}
        record_path = 'mechanics_model.' + FIELD + '.operation_ids.' + identity
        records.append(row)
        live = live_operations.get(identity)
        if source is not None:
            if source['source_id'] != operation['source_id'] or source['pool_id'] != pool['id']:
                raise PlanError('反应资源来源采用回执与实际操作不一致', 'invalid_resource_reaction_source')
            row['authority_rule_id'] = source.get('authority_rule_id')
            if source['status'] in ('excluded', 'dependency_excluded'):
                if live is not None:
                    raise PlanError('明确排除的反应资源操作仍被实际交付', 'invalid_resource_reaction_source')
                row.update(status=source['status'], reason=source.get('reason', 'explicit_source_exclusion'))
                continue
            rule = live_rules.get(source.get('authority_rule_id'))
            review = source_reviews.get(operation['source_id']) or {}
            current = (review.get('mode') == 'manual_context'
                       and review.get('context_fingerprint') == context_fingerprint
                       and type(review.get('note')) is str and bool(review['note'].strip()))
            if (source['status'] == 'ready' and live is not None and rule is not None
                    and rule['source_id'] == operation['source_id'] and rule['owner'] == pool['owner']
                    and current):
                if fingerprint(live) != fingerprint(operation):
                    raise PlanError('反应资源实际操作在来源裁剪时被改写', 'invalid_resource_reaction_source')
                gate = gates_by_operation.get(identity)
                if gate is not None and gate['status'] != 'ready':
                    missing('resource_lifecycle_source_pending', record_path,
                            '反应资源需要当前领域来源及完整领域定义，不能移除资格后无条件执行', 'review_mechanics')
                    continue
                row.update(status='ready', review_mode='manual_context')
                active.append(identity)
                continue
        missing('resource_reaction_source_pending', record_path,
                '反应资源操作需要现有资源图的当前manual_context来源采用；待核或缺失不能视为明确排除',
                'review_mechanics')
    pending = bool(issues) or any(row['status'] == 'pending' for row in records)
    declaration = ({'schema_version': 1, 'operation_ids': active} if active else None)
    if version_two:
        declaration = projected_declaration(original[FIELD], active)
    if pending:
        # A pending preview must never disguise itself as a legacy execution.
        declaration = copy.deepcopy(original[FIELD])
    adoption = {'contract': CONTRACT, 'source_authority': 'caller_assumed',
        'context_fingerprint': context_fingerprint, 'declaration_fingerprint': fingerprint(original[FIELD]),
        'declared_operation_ids': declared, 'active_operation_ids': active,
        'status': 'pending' if pending else 'ready' if active else 'excluded',
        'operations': records, 'validation_issues': issues}
    if version_two:
        from .resource_lifecycle_sources import CONTRACT as V2_CONTRACT
        for record in records:
            gate = gates_by_operation.get(record['operation_id'])
            if gate is not None:
                gate['status'] = record['status']
        adoption.update(contract=V2_CONTRACT, original_declaration=copy.deepcopy(original[FIELD]),
                        projected_declaration=projected_declaration(original[FIELD], active), lifecycle_gates=gates)
    return declaration, adoption


def finalize_resource_reactions(adoption, final_model, missing, *, characters, source_fingerprint,
                                context_fingerprint):
    """Only create an adoption receipt after the last graph mutation."""
    if adoption is None:
        return None
    final = copy.deepcopy(adoption)
    if final['context_fingerprint'] != context_fingerprint:
        raise PlanError('反应资源采用的原生上下文已改变', 'invalid_resource_reaction_source')
    final.update(source_fingerprint=source_fingerprint, final_model_fingerprint=fingerprint(final_model))
    if final['status'] == 'pending':
        return final
    declared = final_model.get(FIELD)
    from .resource_lifecycle_sources import CONTRACT as V2_CONTRACT
    from .resource_lifecycle_sources import validate_adoption
    version_two = final['contract'] == V2_CONTRACT
    if version_two:
        try:
            validate_adoption(final, final_model)
        except (ValueError, KeyError, TypeError) as exc:
            raise PlanError('最终领域反应资格与已核来源不一致', 'invalid_resource_reaction_source') from exc
    if final['status'] == 'excluded':
        if declared is not None:
            raise PlanError('全部来源排除后不应保留反应资源合同', 'invalid_resource_reaction_source')
        return final
    expected = (final['projected_declaration'] if version_two else
                {'schema_version': 1, 'operation_ids': final['active_operation_ids']})
    if (declared is None or fingerprint(declared) != fingerprint(expected)):
        raise PlanError('最终反应资源成员与已采用来源不一致', 'invalid_resource_reaction_source')
    # Compilation after prepare may legitimately add rules or adjust values,
    # but cannot reuse a previously admitted source identity for another owner.
    for row in final['operations']:
        if row['status'] != 'ready':
            continue
        authorities = [rule for rule in final_model['rules']
                       if rule.get('id') == row.get('authority_rule_id')]
        operations = [op for op in final_model.get('shared_pools', {}).get('operations', [])
                      if op.get('id') == row['operation_id']]
        pools = [pool for pool in final_model.get('shared_pools', {}).get('pools', [])
                 if pool.get('id') == row['pool_id']]
        if (row.get('review_mode') != 'manual_context' or not row.get('source_id')
                or len(authorities) != 1 or len(operations) != 1 or len(pools) != 1
                or authorities[0].get('source_id') != row['source_id']
                or authorities[0].get('owner') != row['source_owner']
                or operations[0].get('source_id') != row['source_id']
                or operations[0].get('pool_id') != row['pool_id']
                or pools[0].get('owner') != row['source_owner']):
            raise PlanError('最终反应资源来源或提供者与已采用身份不一致',
                            'invalid_resource_reaction_source')
    try:
        program = Program(final_model, sorted(characters)).report()
    except ReactionResourceError as exc:
        final['status'] = 'pending'
        final['validation_issues'].append(copy.deepcopy(exc.issue))
        missing('resource_reaction_final_graph_pending', exc.issue['path'], exc.issue['message'], 'review_mechanics')
        return final
    final.update(program=program, program_fingerprint=program['program_fingerprint'])
    return final
