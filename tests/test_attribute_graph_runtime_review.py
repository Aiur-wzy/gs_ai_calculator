"""Independent L2b queue oracles and receipt closure, using invented facts."""
import copy
import hashlib
import json

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_graph_runtime import (
    classify,
    conversion,
    enable_graph,
    mutual,
    run,
)
from tests.test_attribute_runtime import reader
from tests.test_mechanics_execution import add, condition, damages, fixture, rule
from tests.test_snapshot_captures import capture
from tests.test_swirl_dynamic_v3 import sampled, setup


@pytest.mark.parametrize('reverse', [False, True])
def test_same_phase_trigger_is_prestate_while_current_observes_committed_writers(reverse):
    t, r, m = fixture((.2,))
    enable_graph(t, r, m, a=0, b=100)
    gain = rule('gain', 'benefit.em.all', 200, owner='b', beneficiaries={'mode': 'self', 'scope': 'locked'})
    sources = [conversion('old', sample='trigger'), conversion('new', sample='current'), gain, reader()]
    add(m, *(reversed(sources) if reverse else sources))
    classify(m, 'old', 'b')
    classify(m, 'new', 'b')
    classify(m, 'gain', 'b', 'included')
    before = copy.deepcopy((t, r, m))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([81])
    rows = {x['rule_id']: x for x in out['sampling_trace'] if x['cycle'] == 0}
    assert rows['old']['source_value'] == 100 and rows['old']['value'] == 20
    assert rows['new']['source_value'] == 300 and rows['new']['value'] == 60
    assert rows['old']['attribute_receipt_id'] != rows['new']['attribute_receipt_id']
    assert (t, r, m) == before


@pytest.mark.parametrize('basis,ready', [('total', True), ('nonextra', False)])
def test_unknown_classification_only_blocks_the_recursive_nonextra_dependency(basis, ready):
    t, r, m = fixture((.2,))
    enable_graph(t, r, m, a=0, b=100)
    add(m, conversion(basis=basis), reader(), rule('gain', 'benefit.em.all', 50, owner='b',
                                                 beneficiaries={'mode': 'self', 'scope': 'locked'}))
    classify(m, 'share', 'b')
    classify(m, 'gain', 'b', 'unknown')
    out = execute_mechanics(t, r, m)
    if ready:
        assert out['result'] is not None, out['issues']
        assert damages(out) == pytest.approx([58.5])
    else:
        assert out['status'] == 'pending' and out['result'] is None
        assert any(row['code'] == 'attribute_classification_unknown' for row in out['issues'])


def test_missing_dependency_component_is_not_hidden_by_an_approved_top_actor():
    t, r, m = fixture((.2,))
    enable_graph(t, r, m, a=0, b=100)
    add(m, conversion(basis='total'), rule('unclassified_gain', 'benefit.em.all', 50, owner='b',
                                         beneficiaries={'mode': 'self', 'scope': 'locked'}))
    classify(m, 'share', 'b')
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(row['code'] == 'attribute_component_missing' for row in out['issues'])


@pytest.mark.parametrize('domain,em', [('self', 60), ('provider', 0)])
def test_expired_self_qualification_and_live_provider_qualification_stay_separate(domain, em):
    t, r, m = fixture((.3,))
    enable_graph(t, r, m, a=0, b=100)
    t['hits'][0]['reaction'] = {'type': 'amplify', 'factor': 2}
    r['actions'][0]['occupation_end'] = .4
    m['snapshot_captures'] = [capture(offset=.2)]
    m['hit_events'][0]['self_snapshot_event'] = 'cast'
    source = conversion(sample='event', domain=domain, duration=.05)
    source['effects'][0]['value']['event_id'] = 'sample'
    condition(m, 'increase', .1)
    add(m, source, rule('gain', 'benefit.em.all', 200, owner='b', beneficiaries={'mode': 'self', 'scope': 'locked'},
                        trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['increase']}))
    classify(m, 'share', 'b')
    classify(m, 'gain', 'b', 'included')
    out = run(t, r, m)
    assert damages(out) == pytest.approx([90 * (1 + 2.78 * em / (1400 + em))])
    rows = [x for x in out['mechanics_trace'] if x['type'] == 'effect_applied' and x['rule_id'] == 'share']
    assert (rows and all(x['value'] == 60 for x in rows)) if domain == 'self' else not rows


def test_unrelated_cross_stat_writer_does_not_block_closed_actor_swirl():
    t, r, m = setup((.2,))
    enable_graph(t, r, m, a=100, b=200)
    add(m, rule('other_writer', 'benefit.em.all', {'kind': 'stat', 'subject': 'owner', 'stat': 'atk',
                                                'factor': .5, 'sample': 'current'}, owner='b',
                beneficiaries={'mode': 'self', 'scope': 'locked'}),
        rule('ordinary_a', 'benefit.em.all', 40, beneficiaries={'mode': 'self', 'scope': 'locked'}))
    classify(m, 'other_writer', 'b')
    classify(m, 'ordinary_a', 'a', 'included')
    out = run(t, r, m)
    assert sampled(out)[0]['value'] == 140


def test_catalog_is_closed_and_hashes_include_projection_provenance():
    t, r, m = fixture((.2, .4))
    mutual(t, r, m)
    out = run(t, r, m)
    receipts = out['attribute_ledger']['receipts']
    ids = {row['receipt_id'] for row in receipts}
    assert any(row['query']['purpose'] == 'damage_projection' for row in receipts)
    for row in receipts:
        raw = {k: v for k, v in row.items() if k != 'receipt_id'}
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        assert hashlib.sha256(canonical).hexdigest() == row['receipt_id']
        assert all(node['input_receipt_id'] in ids for node in row['contributions'] if 'input_receipt_id' in node)


def test_catalog_only_budget_failure_keeps_executed_prefix_but_no_result(monkeypatch):
    t, r, m = fixture((.2, .4))
    mutual(t, r, m)
    complete = run(t, r, m)
    catalog = complete['attribute_ledger']['receipts']
    catalog_bytes = len(json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    read_bytes = complete['attribute_ledger']['usage']['output_bytes'] - catalog_bytes
    m.setdefault('execution_options', {'schema_version':1, 'attribute_limits':{}})['attribute_limits']['output_bytes'] = read_bytes + 1
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None and out['resolved_template'] is None
    assert out['attribute_ledger']['export_issue']['code'] == 'attribute_output_limit'
    assert any(row['type'] == 'hit' and row['event_id'] == 'h1' for row in out['mechanics_trace'])


def test_lazy_unsupported_writer_still_checks_static_duplicate_before_nonextra_pruning():
    from genshin_engine.attribute_runtime import _RuntimeGraphLedger
    from tests.test_attribute_graph import model, node
    data = model()
    component = node('unsupported')['component']
    data['initials'][0].update(total=100, nonextra=80, included_components=[{
        'component': component, 'value': 20, 'nonextra_inclusion': 'excluded'}])
    missing = {'f0': [{'actor': 'a', 'source': ['rule', 'unsupported', 'value'],
                       'component': component, 'instance_id': 'instance', 'layer_id': 'layer',
                       'code': 'attribute_expression_unsupported', 'inclusion': 'excluded'}]}
    service = _RuntimeGraphLedger(data, missing)
    rejected = service.read_stat('a', 'em', 'nonextra', 'f0')
    assert rejected['status'] == 'invalid' and rejected['issues'][0]['code'] == 'attribute_duplicate_delivery'
    assert service.read_stat('a', 'em', 'base', 'f0')['value'] == 0
    assert service.read_stat('b', 'em', 'total', 'f0')['value'] == 200


def test_lazy_unsupported_excluded_writer_prunes_only_when_not_duplicate():
    from genshin_engine.attribute_runtime import _RuntimeGraphLedger
    from tests.test_attribute_graph import model, node
    data = model()
    missing = {'f0': [{'actor': 'a', 'source': ['rule', 'unsupported', 'value'],
                       'component': node('unsupported')['component'], 'instance_id': 'instance', 'layer_id': 'layer',
                       'code': 'attribute_expression_unsupported', 'inclusion': 'excluded'}]}
    service = _RuntimeGraphLedger(data, missing)
    assert service.read_stat('a', 'em', 'nonextra', 'f0')['value'] == 100
    assert service.read_stat('a', 'em', 'total', 'f0')['issues'][0]['code'] == 'attribute_expression_unsupported'
