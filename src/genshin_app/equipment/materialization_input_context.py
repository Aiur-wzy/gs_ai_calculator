"""Role-scoped projections for the two independent materialization approvals.

The game-fact graph and acknowledgements of that graph have different owners.
Neither projection certifies a source: Composer still validates its coverage,
source identity, condition state and reviewed dependency separately. Full input
and execution receipts continue to bind the complete unprojected document.
"""
from __future__ import annotations

import copy

_DOCUMENT_PATHS = frozenset({
    ('extensions', 'agent_execution_materialization'),
    ('extensions', 'whiteboard_handoff', 'board', 'extensions', 'agent_execution_materialization'),
})


def materialization_fact_context(value, path):
    """Return facts for a known document role, or ``None`` for other values.

    In particular, source/payload hashes and source withdrawal states are facts;
    they must never be discarded by a recursive same-name key filter.
    """
    if path not in _DOCUMENT_PATHS or not isinstance(value, dict):
        return None
    facts = copy.deepcopy(value)
    binding = facts.get('plan_binding')
    if isinstance(binding, dict):
        for key in ('input_fingerprint', 'revision', 'plan_id'):
            binding.pop(key, None)
    return facts


def materialization_review_input(value):
    """Copy native input, excluding only precise downstream review hashes.

    Coverage state/note, source references and payloads, actual covered parts,
    dependency mode/note, and every condition remain bound. New or withdrawn
    approval therefore cannot acquire authority merely by having the same
    materialization fingerprint.
    """
    result = copy.deepcopy(value)
    _project_native_reviews(result)
    extensions = result.get('extensions')
    handoff = extensions.get('whiteboard_handoff') if isinstance(extensions, dict) else None
    if isinstance(handoff, dict) and isinstance(handoff.get('board'), dict):
        _project_native_reviews(handoff['board'])
    return result


def _project_native_reviews(plan):
    bindings = plan.get('effect_bindings')
    for binding in bindings if isinstance(bindings, list) else []:
        if not isinstance(binding, dict):
            continue
        binding.pop('source_fingerprint', None)
        coverage = binding.get('coverage')
        if isinstance(coverage, dict):
            coverage.pop('source_fingerprint', None)
            coverage.pop('reviewed_by', None)
        parts = binding.get('parts')
        for part in parts if isinstance(parts, list) else []:
            dependencies = part.get('dependencies') if isinstance(part, dict) else None
            if isinstance(dependencies, dict):
                dependencies.pop('context_fingerprint', None)
    dependencies = plan.get('manual_effect_dependencies')
    for dependency in dependencies if isinstance(dependencies, list) else []:
        if isinstance(dependency, dict):
            dependency.pop('context_fingerprint', None)
    review = plan.get('attribute_review')
    if isinstance(review, dict):
        review.pop('context_fingerprint', None)
    tuning = plan.get('rotation_tuning')
    if isinstance(tuning, dict):
        matches = tuning.get('buff_hit_matches')
        for match in matches if isinstance(matches, list) else []:
            if isinstance(match, dict):
                match.pop('basis_fingerprint', None)
