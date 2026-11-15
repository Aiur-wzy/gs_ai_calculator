"""Read-only rendering must not label contradictory gate relations passed."""

import pytest

from genshin_app import counter_frame_reporting as candidate


@pytest.mark.parametrize('field', ['event_run_id', 'instance_id'])
def test_gate_relationship_mismatch_never_displays_passed(field):
    row = {'run_id': '0:actual-hit', 'instance_id': 'actual-instance',
           'gate_qualification': {'kind': 'shared_pool', 'consumer_id': 'c',
                                  'event_run_id': '0:actual-hit', 'instance_id': 'actual-instance'}}
    row['gate_qualification'][field] = 'other-occurrence-or-instance'
    assert '已通过' not in candidate.gate_label(row)
