"""Regression coverage for independent P2: pay before every summary sort."""
import builtins
import copy

import pytest

from genshin_app import calculation_review as single
from genshin_app import calculation_review_batch as batch
from genshin_app import review_batch_json as bounded
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from tests.test_calculation_review import _saved


def saved(*, groups=1, width=0, types=1):
    value = _saved()
    hit = value['result']['hits'][0]
    value['result']['action_event_compilations'] = []
    value['result']['hits'] = [dict(copy.deepcopy(hit), id=f'row.{i}', skill='字' * width + f'skill.{i}',
                                    damage_type_ids=[f'type.{j:04}' for j in range(types, 0, -1)])
                               for i in range(groups)]
    value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])
    return value


def test_no_type_sort_after_scan_exhausts_fixed_total(monkeypatch):
    value = saved(types=4096)
    scans, sorts = [], []
    original = bounded.prepay

    def scanned(item, budget, **kwargs):
        result = original(item, budget, **kwargs)
        if budget.role == 'projection' and type(item) is dict and item.get('id') == 'row.0':
            scans.append(budget.used)
        return result

    def watched(value, *args, **kwargs):
        if type(value) is list and len(value) == 4096:
            sorts.append(len(value))
        return builtins.sorted(value, *args, **kwargs)

    monkeypatch.setattr(bounded, 'prepay', scanned)
    monkeypatch.setattr(single, 'sorted', watched, raising=False)
    batch.review_calculation_batch(value, {'requests': [{'view': 'summary'}]})
    assert len(scans) == 1 and sorts == [4096]
    monkeypatch.setattr(batch, 'PROJECTION_WORK', scans[0])
    sorts.clear()
    with pytest.raises(PlanError) as caught:
        batch.review_calculation_batch(value, {'requests': [{'view': 'summary'}]})
    assert caught.value.reason == 'review_work_limit' and sorts == []


@pytest.mark.parametrize('width', [0, 4096])
def test_group_sort_prepaid_before_allocating_sorted_keys(monkeypatch, width):
    value = saved(groups=16, width=width)
    budgets, sorts = [], []
    original = bounded.Budget.__init__

    def created(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if self.role == 'projection':
            budgets.append(self)

    def watched(value, *args, **kwargs):
        if type(value) is dict and len(value) == 16:
            sorts.append(budgets[-1].used)
        return builtins.sorted(value, *args, **kwargs)

    monkeypatch.setattr(bounded.Budget, '__init__', created)
    monkeypatch.setattr(single, 'sorted', watched, raising=False)
    batch.review_calculation_batch(value, {'requests': [{'view': 'summary', 'limit': 3}]})
    assert len(sorts) == 1
    monkeypatch.setattr(batch, 'PROJECTION_WORK', sorts[0] - 1)
    sorts.clear()
    with pytest.raises(PlanError) as caught:
        batch.review_calculation_batch(value, {'requests': [{'view': 'summary', 'limit': 3}]})
    assert caught.value.reason == 'review_work_limit' and sorts == []


def test_long_keys_cost_more_and_duplicate_summary_does_not_refresh_budget(monkeypatch):
    captured = []
    original = bounded.Budget.__init__

    def created(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if self.role == 'projection':
            captured.append(self)

    monkeypatch.setattr(bounded.Budget, '__init__', created)
    for width in (0, 4096):
        batch.review_calculation_batch(saved(groups=16, width=width),
                                       {'requests': [{'view': 'summary', 'limit': 3}]})
    assert captured[1].used > captured[0].used
    monkeypatch.setattr(batch, 'PROJECTION_WORK', captured[1].used)
    with pytest.raises(PlanError) as caught:
        batch.review_calculation_batch(saved(groups=16, width=4096),
                                       {'requests': [{'view': 'summary', 'limit': 3}] * 2})
    assert caught.value.reason == 'review_work_limit' and captured[-1].closed
