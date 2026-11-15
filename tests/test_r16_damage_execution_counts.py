from genshin_engine.mechanics import execute_mechanics
from tests.test_damage_report_window import explicit
from tests.test_mechanics_execution import fixture


def test_counts_distinguish_execution_from_report_cutoff_without_inventing_upstream_counts():
    data = explicit(fixture((.2, .3, .4)), .3)
    data[1]['actions'][0]['repeat'] = {'mode': 'once'}
    out = execute_mechanics(*data)
    counts = out['damage_execution_counts']
    assert counts['executed'] == 3
    assert counts['included_before_report_cutoff'] == 3
    assert counts['included_after_report_cutoff'] == 2
    assert counts['dispositions'] == {'included_before_report_cutoff': 3}
    assert counts['scope'] == 'engine_execution_only'
    assert 'declared' not in counts


def test_nonreported_damage_and_verification_cycles_are_not_reported():
    data = fixture((.2, .3))
    data[2]['hit_events'][1]['report'] = False
    out = execute_mechanics(*data)
    counts = out['damage_execution_counts']
    assert counts['included_after_report_cutoff'] == 1
    assert counts['dispositions']['reporting_disabled'] == 1
    assert counts['dispositions']['verification_window'] >= 2
