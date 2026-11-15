"""Fresh source adapter and final-model gate for ordinary reaction flat EM."""
from __future__ import annotations

import copy

from genshin_engine.runtime_reaction_em_compiler import (
    PROFILE_ID,
    PROFILE_VERSION,
    compile_reaction_em_source,
)

from .files import fingerprint
from .reaction_em_scope import (
    CERTIFICATE_KIND,
    final_scope,
    scope_fingerprint,
    source_scope,
)


def compile_projection(bundle, plan, binding, requirement, *, inactive_binding_ids=()):
    compiled = compile_reaction_em_source(bundle, owner=binding['provider_character_id'],
        source_id=binding['binding_id'] + ':upstream', scenario=binding['upstream_recipe']['scenario'],
        period=(plan.get('rotation_model') or {}).get('period'))
    issues, proof = source_scope(plan, binding, requirement, inactive_binding_ids=inactive_binding_ids)
    if issues:
        compiled['status'] = 'pending'
        compiled.setdefault('issues', []).extend(issues)
    ready = compiled['status'] == 'ready'
    # Projection readiness is only permission to assemble the private candidate.
    # The final gate is mandatory before that candidate can execute or be saved.
    compiled.update(execution_status='pending', projection_status=compiled['status'],
        gameplay_semantics_status='unverified', execution_binding={'kind': 'elemental_reaction'},
        assumptions_fingerprint=compiled['required_confirmation']['accepted_assumptions_fingerprint'],
        app_scope={'status': 'awaiting_final_model' if ready else 'pending',
            'source_scope_fingerprint': scope_fingerprint(proof),
            'target_id': binding['upstream_recipe']['scenario'].get('target_id')},
        coverage_certificate={'kind': CERTIFICATE_KIND, 'source_complete': False,
            'projection_complete': ready, 'projection_status': compiled['status'],
            'gameplay_verified': False, 'execution_gate': 'final_actual_model_required'})
    compiled['provenance'].update(
        profile_id=PROFILE_ID, profile_version=PROFILE_VERSION
    )
    return compiled, proof


def finalize_projections(plan, template, records, applicable, missing):
    """Run after every source compiler; return only fresh active attribute facts."""
    blocked, evidence, facts = set(), [], []
    bindings = {b['binding_id']: b for b in plan['effect_bindings']}
    for record in records:
        if record.get('profile_id') != PROFILE_ID or record.get('status') != 'ready':
            continue
        binding = bindings[record['binding_id']]
        issues, proof = final_scope(plan, template, binding, record)
        ready = not issues
        scope_fp = scope_fingerprint(proof)
        record['app_scope'].update(status='ready' if ready else 'pending',
            final_model_scope=proof, final_model_scope_fingerprint=scope_fp)
        record['execution_status'] = record['status'] = record['projection_status'] = 'ready' if ready else 'pending'
        record['gameplay_semantics_status'] = 'assumed_in_scope' if ready else 'unverified'
        record['coverage_certificate'].update(projection_complete=ready,
            projection_status=record['status'], gameplay_verified=ready,
            execution_gate='final_actual_model_checked', final_model_scope_fingerprint=scope_fp)
        record['compilation_fingerprint'] = fingerprint({
            'fresh_source_compilation': record['compilation_fingerprint'], 'actual_final_scope': proof})
        evidence.append({'kind': 'reaction_em_final_scope', 'binding_id': record['binding_id'],
                         'compilation_fingerprint': record['compilation_fingerprint'], 'proof': proof})
        if issues:
            blocked.add(record['binding_id'])
            record.setdefault('issues', []).extend(copy.deepcopy(issues))
            for issue in issues:
                missing(issue['code'], issue['path'], issue['message'], 'review_reaction_source',
                        binding_id=record['binding_id'])
            # Preserve the failed proof but withdraw every executable delivery.
            for field in ('parts', 'rules', 'initial_rules', 'static_parts', 'attribute_writes'):
                record[field] = []
        else:
            for source_fact in record['attribute_writes']:
                fact = copy.deepcopy(source_fact)
                # The compiler uses a binding-local part ID; the shared ledger
                # resolves components by the full, namespaced source identity.
                fact['part_id'] = record['binding_id'] + ':' + fact['part_id']
                facts.append(fact)
    return [(binding, part) for binding, part in applicable if binding['binding_id'] not in blocked], evidence, facts
