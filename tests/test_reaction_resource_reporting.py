import copy
import json
from pathlib import Path

from genshin_app.reaction_resource_reporting import (
    reaction_resource_rows,
    reaction_resource_sheet,
)

HERE = Path(__file__).resolve().parent

def sample(name='ordinary-swirl-income'):
    return json.loads((HERE / 'fixtures/resource_reaction_reporting.json').read_text('utf8'))['cases'][name]


def test_actual_income_and_identity_without_input_mutation():
    result = sample()
    before = copy.deepcopy(result)
    rows = reaction_resource_rows(result)
    assert len(rows) == 3
    assert [row['receipts'][0]['accepted'] for row in rows] == [3, 3, 3]
    assert all(row['association_issue'] is None for row in rows)
    assert rows[0]['time_us'] == 200000
    assert rows[0]['receipts'][0]['path'] == '/shared_pools/trace/1'
    assert result == before


def test_no_match_and_complete_zero_are_distinct():
    rows = reaction_resource_rows(sample('actual-selector-no-match'))
    assert len(rows) == 3 and all(row['state'] == 'no_match' and not row['receipts'] for row in rows)
    assert reaction_resource_rows(sample('complete-zero-reaction')) == []
    assert reaction_resource_rows({'hits': []}) == []


def test_missing_or_duplicate_occurrence_not_inferred_by_time():
    for mode in ('missing', 'duplicate'):
        result = sample()
        occurrences = result['resource_reactions']['occurrences']
        if mode == 'missing':
            occurrences.pop(0)
        else:
            occurrences.append(copy.deepcopy(occurrences[0]))
        first = reaction_resource_rows(result)[0]
        assert first['association_issue'] and first['receipts'] == []


def test_other_parent_receipt_cannot_be_claimed():
    result = sample()
    result['shared_pools']['trace'][1]['run_id'] = 'other-parent'
    first = reaction_resource_rows(result)[0]
    assert first['association_issue'] and not first['receipts']


def test_duplicate_span_not_double_reported():
    result = sample()
    result['resource_reactions']['operations'].append(copy.deepcopy(result['resource_reactions']['operations'][0]))
    rows = reaction_resource_rows(result)
    assert rows[0]['association_issue'] == rows[-1]['association_issue'] == '同一资源流水被重复引用'
    assert not rows[0]['receipts'] and not rows[-1]['receipts']


def test_time_zero_is_kept_and_source_actor_not_guessed():
    result = sample()
    operation = result['resource_reactions']['operations'][0]
    result['resource_reactions']['occurrences'][0].update(time_us=0, owner='triggerer')
    operation['source_owner'] = 'provider'
    result['shared_pools']['trace'][1]['t_us'] = 0
    row = reaction_resource_rows(result)[0]
    assert row['time_us'] == 0 and row['source_owner'] == 'provider' and row['reaction_owner'] == 'triggerer'
    assert row['association_issue'] is None


def test_pending_slot_never_renders_income():
    result = sample()
    result['resource_reactions']['operations'][0]['state'] = 'planned'
    first = reaction_resource_rows(result)[0]
    assert first['association_issue'] and not first['receipts']


def test_full_pool_and_cooldown_are_not_positive_income():
    for reason, expected in [('granted', '已尝试，实际收入为0'), ('cooldown', '冷却拒绝')]:
        result = sample()
        result['shared_pools']['trace'][1].update(accepted=0, reason=reason)
        row = reaction_resource_rows(result)[0]
        assert row['receipts'][0]['label'] == expected


def test_sheet_has_exact_raw_and_no_calculation_formulas():
    result = sample()
    rows = reaction_resource_sheet(result)
    raw = ''.join(row[1] for row in rows if isinstance(row[0], str) and row[0].startswith('原文块 '))
    assert json.loads(raw)['resource_reactions'] == result['resource_reactions']
    assert rows[6][10:13] == [3, 3, 0]
    assert len(rows[5]) == len(rows[6]) == 17
    assert not any(isinstance(cell, str) and cell.startswith('=') for row in rows for cell in row)


def test_excluded_source_is_not_invented_zero_reaction_execution():
    result = {'resource_reaction_source_adoption': {'status': 'excluded', 'operations': []}}
    rows = reaction_resource_sheet(result)
    assert rows[2][1] == '无执行记录' and rows[4][1] == '已排除'
    assert reaction_resource_rows(result) == []
    assert reaction_resource_sheet({}) is None
