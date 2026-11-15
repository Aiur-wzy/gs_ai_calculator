"""Independent explicit authority and full-graph view closure boundaries."""
from __future__ import annotations

import pytest

from tests.test_resource_counter_view_sources import raw


@pytest.mark.parametrize('change', [{'status': 'pending'}, {'source': ''}])
def test_source_active_does_not_upgrade_pending_or_empty_authority_rule(change):
    out, issues = raw(mutate=lambda m: m['rules'][1].update(change))
    assert out['view_adoptions'][0]['effective_status'] == 'dependency_pending'
    assert out['views'] is None and 'view' in out['disabled_rules'] and issues
    assert out['counters'] and out['pools'] and out['state']
