"""Pure reporting must suppress amounts for incomplete exact key joins."""
from __future__ import annotations

import math

import pytest

from genshin_app import reaction_resource_reporting as reporting
from genshin_app.equipment.schema import PlanError
from tests.test_reaction_resource_reporting import sample


@pytest.mark.parametrize('keys', [['team', 'other'], ['team', 'team']])
def test_each_declared_key_must_have_exactly_one_resource_receipt(keys):
    result = sample()
    operation = result['resource_reactions']['operations'][0]
    operation.update(keys=keys, expected_count=2, actual_end=3)
    result['shared_pools']['trace'][2] = dict(result['shared_pools']['trace'][1], id=3)
    row = reporting.reaction_resource_rows(result)[0]
    assert row['association_issue'] and not row['receipts']


@pytest.mark.parametrize('entry', ['sheet', 'projection'])
def test_time_conversion_never_leaks_overflow(entry):
    result = sample()
    result['resource_reactions']['occurrences'][0]['time_us'] = 10**400
    result['shared_pools']['trace'][1]['t_us'] = 10**400
    try:
        if entry == 'sheet':
            reporting.reaction_resource_sheet(result)
        else:
            row = reporting.reaction_resource_rows(result)[0]
            projection = reporting.reaction_resource_projection(row)
            assert 't' not in projection or math.isfinite(projection['t'])
    except PlanError:
        pass


def test_prefix_execution_display_does_not_claim_complete_action_window():
    result = sample()
    result['mechanics']['complete_first_cycle'] = False
    result['summary']['dps'] = None
    # The actual queue stores complete_reported_input for its evidence even
    # when a later action fails; this must not hide the execution scope.
    assert result['resource_reactions']['verified_window'] == 'complete_reported_input'
    rows = reporting.reaction_resource_sheet(result)
    assert rows[3][1] == '已执行前缀'
