"""Saved RQ1 input/occurrence consistency, not current DB or game approval."""
from __future__ import annotations

import copy
import json
import math
from functools import partial

from genshin_engine.resource_reaction_program import Program

from .counter_frame_receipt import CHECK_APP_KEYS, SECTIONS
from .counter_frame_receipt import _ledger as counter_ledger
from .execution_verification import ExecutionVerificationError
from .files import context_view, fingerprint, input_view
from .replay_numeric_projection import numeric_budget_result
from .schema import PlanError
from .verification_scope import verification_scope

CONTRACT = 'ordinary-reaction-resource-receipt-v1'
FIELD = 'resource_reaction_contract'
ADOPTION = 'resource_reaction_source_adoption'
EXECUTION = 'resource_reaction_execution'
MAX_BYTES = 32 * 1024 * 1024
MESSAGE = '实际反应、资源收入、来源采用或保存的执行材料不一致，请重新计算。'


def _bounded(value, *, byte_limit=MAX_BYTES):
    """Saved JSON plus native-engine tuple rows; no conversion or whole copy."""
    active, nodes, size = set(), 0, 0
    def charge(n):
        nonlocal size
        size += n
        if size > byte_limit:
            raise ValueError('反应资源离线证据超过32MiB；未截断或标为fresh。')
    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        kind = type(item)
        if nodes > 200000 or depth > 64:
            raise ValueError(MESSAGE)
        if kind in (dict, list, tuple):
            if id(item) in active or len(item) > 200000:
                raise ValueError(MESSAGE)
            active.add(id(item));charge(2 + max(0, len(item) - 1))
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:raise ValueError(MESSAGE)
                    visit(key, depth+1);charge(1);visit(child, depth+1)
            else:
                for child in item:visit(child, depth+1)
            active.remove(id(item));return
        if (kind not in (str, int, float, bool, type(None))
                or kind is str and len(item) > 65536 or kind is int and item.bit_length() > 4096
                or kind is float and not math.isfinite(item)):
            raise ValueError(MESSAGE)
        charge(len(json.dumps(item,ensure_ascii=False,separators=(',', ':'),allow_nan=False).encode('utf-8')))
    visit(value,0)


def _fingerprint(value, *, _session=None):
    if _session is not None:
        _session.scan_receipt_materials(outputs=(numeric_budget_result(value),))
    return fingerprint(value)


def _same(left, right, *, _session=None):
    if _session is not None:
        _session.scan_receipt_materials(outputs=(numeric_budget_result(left), numeric_budget_result(right)))
    if fingerprint(left) != fingerprint(right):
        raise ValueError(MESSAGE)


def has_v2_material(result, receipt=None, *, plan=None, report=None):
    """Known version signals choose a policy, never grant source authority."""
    def mapping(value):
        return value if type(value) is dict else {}
    if mapping(mapping(receipt).get('resource_reactions')).get('contract') == 'ordinary-reaction-resource-receipt-v2':
        return True
    for container in (result, plan, report, mapping(plan).get('resolution'),
                      mapping(report).get('resolution'), mapping(report).get('plan'),
                      mapping(mapping(report).get('plan')).get('resolution')):
        container = mapping(container)
        if mapping(container.get(ADOPTION)).get('contract') == 'ordinary-reaction-resource-adoption-v2':
            return True
        models = [container.get('mechanics_model')]
        for field in (EXECUTION, 'counter_frame_execution', 'snapshot_capture_execution', 'mechanism_execution'):
            execution = mapping(container.get(field))
            models.extend((execution.get('mechanics_model'), execution.get('model')))
        if any(mapping(mapping(model).get(FIELD)).get('schema_version') == 2 for model in models):
            return True
    return False


def has_resource_reaction_signal(result, receipt=None, *, plan=None, report=None):
    """Closed known signal locations; deleting one copy never disables proof."""
    def mapping(value):
        return value if isinstance(value, dict) else {}
    containers = [result, plan, report, mapping(plan).get('resolution'),
                  mapping(report).get('resolution'), mapping(report).get('plan'),
                  mapping(mapping(report).get('plan')).get('resolution'),
                  mapping(mapping(result).get('mechanism_replay_context')).get('plan')]
    if isinstance(receipt, dict) and 'resource_reactions' in receipt:
        return True
    for item in containers:
        if not isinstance(item, dict):
            continue
        if any(key in item for key in ('resource_reactions', ADOPTION, EXECUTION)):
            return True
        if isinstance(item.get('mechanics_model'), dict) and FIELD in item['mechanics_model']:
            return True
        for key in ('counter_frame_execution', 'snapshot_capture_execution', 'mechanism_execution'):
            context = item.get(key)
            if isinstance(context, dict) and any(isinstance(context.get(field), dict) and FIELD in context[field]
                                                 for field in ('mechanics_model', 'model')):
                return True
    return False


def _ledger(result, *, _session=None):
    if _session is None:
        value = counter_ledger(result)
    else:
        # Read-only views preserve exact data without repeatedly cloning logs.
        value = {key: result.get(key) for key in SECTIONS}
        for key in ('resource_counter_views', 'resource_counters', 'shared_pools', 'state_runtime'):
            if isinstance(value[key], dict):
                value[key] = {k: v for k, v in value[key].items() if k != 'source_adoptions'}
        value['rotation_check'] = {k: v for k, v in (result.get('rotation_check') or {}).items() if k not in CHECK_APP_KEYS}
    value.update(resource_reactions=result.get('resource_reactions'), elemental=result.get('elemental'))
    return value


def _proof(result, parent, *, _session=None):
    from .resource_lifecycle_sources import CONTRACT as V2_ADOPTION
    contract = ('ordinary-reaction-resource-receipt-v2'
                if result[ADOPTION].get('contract') == V2_ADOPTION else CONTRACT)
    return {'contract': contract, 'verification_scope': 'saved_input_replay_not_current_database_approval',
            **{key: parent.get(key) for key in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint')},
            'execution_fingerprint': _fingerprint(result[EXECUTION], _session=_session),
            'adoption_fingerprint': _fingerprint(result[ADOPTION], _session=_session),
            'ledger_fingerprint': _fingerprint(_ledger(result, _session=_session), _session=_session)}


@verification_scope
def make_resource_reaction_receipt(plan, report, result, *, _session=None):
    if not has_resource_reaction_signal(result, plan=plan, report=report):
        return None
    try:
        execution = {'template': report['mechanics_input_template'],
                     'rotation_model': result['rotation_check']['resolved_rotation_model'],
                     'mechanics_model': report['mechanics_model'], 'matches': report.get('mechanics_matches', [])}
        adoption = report[ADOPTION]
        budget = _session if has_v2_material(result, plan=plan, report=report) else None
        if budget is not None:
            budget.scan_receipt_materials(inputs=(execution,), outputs=(adoption, numeric_budget_result(result)))
        _same(result.get(ADOPTION), adoption, _session=budget)
        if budget is None:
            _bounded([execution, adoption, numeric_budget_result(result)], byte_limit=MAX_BYTES)
        shadow = {**result, EXECUTION: copy.deepcopy(execution)}
        proof = _proof(shadow, report, _session=budget)
        if budget is None:
            _bounded([numeric_budget_result(shadow), proof], byte_limit=MAX_BYTES)
        else:
            budget.scan_receipt_materials(inputs=(shadow[EXECUTION],), outputs=(numeric_budget_result(shadow), proof))
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        boundary = isinstance(exc, ExecutionVerificationError)
        raise PlanError(str(exc) if boundary else MESSAGE, 'resource_reaction_proof_pending',
                        {'status': 'proof_pending', 'fresh': False, 'actual_result': result,
                         **({'boundary_code': exc.code} if boundary else {})}) from exc
    result[EXECUTION] = shadow[EXECUTION]
    return proof


def _adoption(execution, adoption, result, *, _session=None):
    same = partial(_same, _session=_session)
    digest = partial(_fingerprint, _session=_session)
    model = execution['mechanics_model']
    from .resource_lifecycle_sources import CONTRACT as V2_ADOPTION
    from .resource_lifecycle_sources import validate_adoption
    if (type(adoption) is not dict or adoption.get('contract') not in ('ordinary-reaction-resource-adoption-v1', V2_ADOPTION)
            or adoption.get('source_authority') != 'caller_assumed'
            or adoption.get('status') not in ('ready', 'excluded')
            or adoption.get('validation_issues') != []):
        raise ValueError(MESSAGE)
    if adoption['contract'] == V2_ADOPTION:
        validate_adoption(adoption, model)
    elif (model.get(FIELD, {}).get('schema_version') == 2
          or any(key in adoption for key in ('original_declaration', 'projected_declaration', 'lifecycle_gates'))):
        raise ValueError(MESSAGE)
    same(adoption.get('final_model_fingerprint'), digest(model))
    rows = adoption['operations']
    ids = [row['operation_id'] for row in rows]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(MESSAGE)
    same(sorted(ids), adoption['declared_operation_ids'])
    same(sorted(row['operation_id'] for row in rows if row['status'] == 'ready'), adoption['active_operation_ids'])
    live = {row['id']: row for row in model.get('shared_pools', {}).get('operations', [])}
    for row in rows:
        if row['status'] in ('excluded', 'dependency_excluded'):
            if row['operation_id'] in live:
                raise ValueError(MESSAGE)
        elif row['status'] != 'ready':
            raise ValueError(MESSAGE)
    if adoption['status'] == 'excluded':
        if (FIELD in model or 'resource_reactions' in result or 'program' in adoption
                or adoption.get('active_operation_ids') or not adoption.get('operations')
                or any(row['status'] not in ('excluded', 'dependency_excluded') for row in adoption['operations'])):
            raise ValueError(MESSAGE)
        return
    program = Program(model, [c['id'] for c in execution['template']['characters']]).report()
    by_id = {row['id']: row for row in program['operations']}
    for row in rows:
        if row['status'] == 'ready':
            definition = by_id[row['operation_id']]
            for key in ('source_id', 'pool_id', 'source_owner'):
                same(row[key], definition[key])
            authorities = [rule for rule in model['rules']
                           if rule['id'] == row.get('authority_rule_id')]
            if (row.get('review_mode') != 'manual_context' or len(authorities) != 1
                    or authorities[0]['source_id'] != definition['source_id']
                    or authorities[0]['owner'] != definition['source_owner']):
                raise ValueError(MESSAGE)
    same(program, adoption.get('program'))
    same(program['program_fingerprint'], adoption.get('program_fingerprint'))
    same(model[FIELD]['operation_ids'], adoption.get('active_operation_ids'))
    audit = result['resource_reactions']
    same(audit.get('program'), program)
    same(audit.get('program_fingerprint'), program['program_fingerprint'])
    expected_window = ('complete_reported_input' if result.get('mechanics', {}).get('complete_first_cycle') is True
                       else 'executed_prefix')
    if (audit.get('verification_status') != 'verified' or audit.get('verified_window') != expected_window
            or audit.get('source_authority') != 'caller_assumed' or audit.get('execution') != 'mechanics_event_queue'):
        raise ValueError(MESSAGE)


def _validate_result(result, receipt, session):
    from genshin_engine.resource_budget import validate_saved_resource_budget
    policy = result.get('resource_reactions', {}).get('budget_policy')
    if policy is not None:
        validate_saved_resource_budget(policy)
    execution, adoption, proof = result.get(EXECUTION), result.get(ADOPTION), receipt.get('resource_reactions')
    budget = session if has_v2_material(result, receipt) else None
    same = partial(_same, _session=budget)
    digest = partial(_fingerprint, _session=budget)
    if budget is None:
        _bounded([numeric_budget_result(result), receipt], byte_limit=MAX_BYTES)
    else:
        budget.scan_receipt_materials(inputs=(execution,), outputs=(numeric_budget_result(result), receipt))
    if (type(execution) is not dict or set(execution) != {'template', 'rotation_model', 'mechanics_model', 'matches'}
            or type(execution.get('matches')) is not list or type(proof) is not dict):
        raise ValueError(MESSAGE)
    _adoption(execution, adoption, result, _session=budget)
    same(adoption['source_fingerprint'], receipt.get('source_fingerprint'))
    same(proof, _proof(result, receipt, _session=budget))
    same(receipt.get('result_fingerprint'), digest(result))
    same(result['rotation_check']['resolved_rotation_model'], execution['rotation_model'])
    same(result['rotation_check']['input_fingerprint'], digest(execution))
    replay = session.replay(execution)
    expected = replay.get('result')
    if expected is None or replay.get('resolved_template') is None:
        raise ValueError(MESSAGE)
    from .replay_numeric_projection import replay_output_template
    same(digest(replay_output_template(replay['resolved_template'], result, receipt)),
         receipt.get('output_fingerprint'))
    if not expected.get('mechanics', {}).get('complete_first_cycle', False):
        expected['summary']['dps'] = None
    expected.update(rotation_check=replay['rotation_check'], mechanics_trace=replay['mechanics_trace'],
                    sampling_trace=replay.get('sampling_trace', []), mechanics_status=replay['status'],
                    mechanics_issues=replay.get('issues', []))
    from .replay_numeric_projection import replay_numeric_ledgers
    expected_ledger, actual_ledger = replay_numeric_ledgers(
        _ledger(expected, _session=budget), _ledger(result, _session=budget), result, receipt)
    same(expected_ledger, actual_ledger)


@verification_scope
def validate_resource_reaction_result_receipt(result, receipt, *, _session=None):
    if not has_resource_reaction_signal(result, receipt):
        return
    try:
        _validate_result(result, receipt, _session)
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_resource_reaction_receipt(plan, result, report, receipt, *, _session=None):
    if not has_resource_reaction_signal(result, receipt, plan=plan, report=report):
        return
    try:
        budget = _session if has_v2_material(result, receipt, plan=plan, report=report) else None
        same = partial(_same, _session=budget)
        digest = partial(_fingerprint, _session=budget)
        if budget is not None:
            budget.scan_receipt_materials(inputs=(input_view(plan), input_view(report['plan'])))
        # Force full evidence even if every result-only signal was stripped.
        if EXECUTION not in result or ADOPTION not in result or 'resource_reactions' not in receipt:
            raise ValueError(MESSAGE)
        validate_resource_reaction_result_receipt(result, receipt, _session=_session)
        execution, adoption = result[EXECUTION], result[ADOPTION]
        from .resource_lifecycle_sources import CONTRACT as V2_ADOPTION
        native_contract = plan.get('mechanics_model', {}).get(FIELD)
        if (native_contract is not None
                and (native_contract.get('schema_version') == 2) != (adoption.get('contract') == V2_ADOPTION)):
            raise ValueError(MESSAGE)
        same(digest(input_view(plan)), receipt.get('input_fingerprint'))
        same(input_view(plan), input_view(report['plan']))
        for key in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint'):
            same(report[key], receipt.get(key))
        same(report['mechanics_model'], execution['mechanics_model'])
        same(report['mechanics_input_template'], execution['template'])
        same(report.get('mechanics_matches', []), execution['matches'])
        same(report[ADOPTION], adoption)
        same(plan['resolution'][ADOPTION], adoption)
        same(adoption['source_fingerprint'], receipt['source_fingerprint'])
        same(adoption['context_fingerprint'], digest(context_view(input_view(plan))))
        same(adoption['declaration_fingerprint'], digest(plan['mechanics_model'][FIELD]))
        same(digest(plan['resolved_template']), receipt['output_fingerprint'])
        same(digest(report['plan']['resolved_template']), receipt['output_fingerprint'])
        from genshin_app.rotation_check import resolve_rotation_model
        declared = report.get('compiled_rotation_model', plan['rotation_model'])
        if budget is None:
            _bounded([declared, execution['template']], byte_limit=MAX_BYTES)
        else:
            budget.scan_receipt_materials(inputs=(declared, execution['template']))
        resolved, _ = resolve_rotation_model(execution['template'], declared)
        same(resolved, execution['rotation_model'])
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
