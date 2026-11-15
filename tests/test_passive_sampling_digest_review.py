"""Causal scope excludes its derived hash, preserving actual declaration meaning."""
from __future__ import annotations

import copy

from genshin_app.equipment.passive_attribute_scope import inspect_nonextra_sampling
from tests.test_attribute_app_sources import attribute_plan


def case():
    plan = attribute_plan()
    rule = plan['mechanics_model']['rules'][0]
    row = {'effect_ref': {'kind': 'rule', 'rule_id': rule['id'], 'effect_id': rule['effects'][0]['id']},
           'component': {'provider': rule['owner'], 'part_id': rule['source_id'],
                         'effect_id': rule['effects'][0]['id'], 'source_fingerprint': '0'*64},
           'nonextra_inclusion': 'included', 'status': 'assumed', 'source': 'Independent declared classification'}
    plan['mechanics_model']['attribute_model']['effect_classifications'].append(row)
    rule['effects'][0]['value'] = 50
    return plan, row


def test_only_derived_component_hash_is_excluded_without_mutating_native():
    plan, row = case()
    before = copy.deepcopy(plan)
    first = inspect_nonextra_sampling(plan, plan['manual_template'], 'a')
    assert plan == before
    row['component']['source_fingerprint'] = 'f'*64
    assert inspect_nonextra_sampling(plan, plan['manual_template'], 'a') == first
    declaration = first['writers'][0]['classification']
    expected = copy.deepcopy(before['mechanics_model']['attribute_model']['effect_classifications'][0])
    expected['component'].pop('source_fingerprint')
    assert declaration == expected


def test_semantic_provider_part_inclusion_status_and_note_still_change_scope():
    plan, _ = case()
    baseline = inspect_nonextra_sampling(plan, plan['manual_template'], 'a')
    for section, key, value in [('component', 'provider', 'b'), ('component', 'part_id', 'other'),
                              (None, 'nonextra_inclusion', 'excluded'), (None, 'status', 'pending'),
                              (None, 'source', 'Changed classification justification')]:
        changed = copy.deepcopy(plan)
        row = changed['mechanics_model']['attribute_model']['effect_classifications'][0]
        (row[section] if section else row)[key] = value
        assert inspect_nonextra_sampling(changed, changed['manual_template'], 'a') != baseline
