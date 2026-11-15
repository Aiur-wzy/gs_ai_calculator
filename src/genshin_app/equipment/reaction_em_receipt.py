"""Bind reaction EM exports to the actual compiled model and occurrence proof."""
from __future__ import annotations

from genshin_engine.runtime_reaction_em_compiler import PROFILE_ID

from .files import fingerprint, input_view
from .reaction_benefit_receipt import validate_reaction_benefit_shared_receipt
from .reaction_em_execution import verify_reaction_em_execution
from .reaction_em_scope import CERTIFICATE_KIND, scope_fingerprint
from .schema import PlanError


def reaction_em_records(report):
    return [r for r in report.get('upstream_compilations', []) if r.get('profile_id') == PROFILE_ID]


def bindings(plan):
    return [b for b in plan.get('effect_bindings', [])
            if b.get('upstream_recipe', {}).get('scenario', {}).get('profile_id') == PROFILE_ID]


def make_reaction_em_receipt(plan, report, result):
    return {'contract': 'reaction-em-materialized-receipt-v1',
        'compiled_sources_fingerprint': fingerprint(reaction_em_records(report)),
        'input_recipes_fingerprint': fingerprint([{k: b[k] for k in (
            'binding_id', 'provider_character_id', 'owner', 'upstream_recipe')} for b in bindings(plan)]),
        'compiled_model_fingerprint': fingerprint(report['mechanics_model']),
        'input_template_fingerprint': fingerprint(report['mechanics_input_template']),
        'actual_occurrences_fingerprint': fingerprint(result['reaction_em_execution'])}


def validate_reaction_em_result(result, receipt):
    """Also used by read-only exports that do not have the native input plan."""
    records = result.get('reaction_em_compilations')
    proof = result.get('reaction_em_execution')
    recorded = receipt.get('reaction_em')
    if not (records or proof or recorded):
        return
    message = '反应精通的完整来源、实际反应记录或计算回执已变化，请重新计算。'
    try:
        if (not isinstance(records, list) or not records or not isinstance(proof, dict)
                or not isinstance(recorded, dict) or recorded.get('contract') != 'reaction-em-materialized-receipt-v1'
                or recorded.get('compiled_sources_fingerprint') != fingerprint(records)
                or recorded.get('actual_occurrences_fingerprint') != fingerprint(proof)
                or receipt.get('result_fingerprint') != fingerprint(result)
                or verify_reaction_em_execution({'mechanics_trace': result.get('mechanics_trace'),
                    'rotation_check': result.get('rotation_check'), 'result': result}, records) != proof):
            raise ValueError(message)
        complete = (result.get('mechanics') or {}).get('complete_first_cycle')
        interpretation = (result.get('rotation_check') or {}).get('damage_interpretation') or {}
        if (type(complete) is not bool or interpretation.get('complete_first_cycle') is not complete
                or interpretation.get('kind') != ('planned_damage' if complete else 'executed_prefix')
                or not complete and (result.get('summary') or {}).get('dps') is not None):
            raise ValueError(message)
        validate_reaction_benefit_shared_receipt(records, receipt)
        if any(r.get('profile_id') != PROFILE_ID or r.get('status') != 'ready' for r in records):
            raise ValueError(message)
    except (KeyError, TypeError, AttributeError, PlanError) as exc:
        raise ValueError(message) from exc


def validate_reaction_em_receipt(plan, result, report, receipt):
    native = bindings(plan)
    records = result.get('reaction_em_compilations') or []
    compiled = reaction_em_records(report)
    if not (native or records or compiled or receipt.get('reaction_em')):
        return
    message = '反应精通的方案、最终规则或属性分类与交付回执不一致，请回App重新计算。'
    try:
        native_by_id = {b['binding_id']: b for b in native}
        present = {r['binding_id'] for r in records}
        all_compiled = report.get('upstream_compilations', [])
        for identity in native_by_id.keys() - present:
            inactive = [r for r in all_compiled if r['binding_id'] == identity]
            if (len(inactive) != 1 or inactive[0]['status'] != 'inactive'
                    or any(inactive[0].get(k) for k in ('rules', 'parts', 'initial_rules', 'attribute_writes'))
                    or identity in {r['binding_id'] for r in report.get('effect_requirements', [])}):
                raise ValueError(message)
        if not records:
            if compiled or receipt.get('reaction_em') or result.get('reaction_em_execution'):
                raise ValueError(message)
            return
        validate_reaction_em_result(result, receipt)
        if (records != compiled or len(records) != len(present) or not present <= native_by_id.keys()
                or receipt.get('reaction_em') != make_reaction_em_receipt(plan, report, result)
                or receipt.get('input_fingerprint') != fingerprint(input_view(plan))):
            raise ValueError(message)
        model = report['mechanics_model']
        for record in records:
            binding = native_by_id[record['binding_id']]
            scenario = binding['upstream_recipe']['scenario']
            certificate = record['coverage_certificate']
            scope = record['app_scope']
            if (record['execution_status'] != 'ready' or record['projection_status'] != 'ready'
                    or record['whole_source_status'] != 'partial' or scope['status'] != 'ready'
                    or certificate['kind'] != CERTIFICATE_KIND or certificate['source_complete'] is not False
                    or certificate['projection_complete'] is not True
                    or certificate['execution_gate'] != 'final_actual_model_checked'
                    or scope_fingerprint(scope['final_model_scope']) != scope['final_model_scope_fingerprint']
                    or certificate['final_model_scope_fingerprint'] != scope['final_model_scope_fingerprint']
                    or scenario['accepted_assumptions_fingerprint'] != record['assumptions_fingerprint']
                    or record['source_identity']['provider'] != binding['provider_character_id']):
                raise ValueError(message)
            for rule in record['rules']:
                actual = [r for r in model['rules'] if r['id'] == rule['id']]
                if actual != [rule]:
                    raise ValueError(message)
            for initial in record['initial_rules']:
                if [r for r in model['initial_state']['rules'] if r['rule_id'] == initial['rule_id']] != [initial]:
                    raise ValueError(message)
            for fact in record['attribute_writes']:
                rules = [r for r in record['rules'] if r['id'] == fact['effect_ref']['rule_id']]
                rows = [r for r in model['attribute_model']['effect_classifications'] if r['effect_ref'] == fact['effect_ref']]
                if (len(rules) != 1 or len(rows) != 1 or rows[0]['nonextra_inclusion'] != 'included'
                        or rows[0]['status'] not in ('confirmed', 'assumed')
                        or rows[0]['component']['provider'] != fact['provider']
                        or rows[0]['component']['part_id'] != rules[0]['source_id']
                        or rows[0]['component']['effect_id'] != fact['effect_ref']['effect_id']):
                    raise ValueError(message)
    except (KeyError, TypeError, AttributeError, PlanError) as exc:
        raise ValueError(message) from exc
