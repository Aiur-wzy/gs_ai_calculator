import copy

from genshin_engine import mechanics
from tests.test_damage_report_window import explicit
from tests.test_mechanics_execution import fixture


def test_unchanged_window_keeps_same_calculation_and_proof(monkeypatch):
    data = fixture((.2, .3))
    no_window = mechanics.execute_mechanics(*data)
    original = mechanics.calculate
    calls = []

    def observed(template, **kwargs):
        calls.append(copy.deepcopy(template))
        return original(template, **kwargs)

    monkeypatch.setattr(mechanics, 'calculate', observed)
    output = mechanics.execute_mechanics(*explicit(data, 10))
    assert len(calls) == 1
    for key in ('summary', 'hits', 'damage_explanations', 'damage_explanation_fingerprint'):
        assert output['result'][key] == no_window['result'][key]


def test_duration_or_removed_hit_requires_fresh_formula_projection(monkeypatch):
    original = mechanics.calculate
    calls = []

    def observed(template, **kwargs):
        calls.append(template['loop_duration'])
        return original(template, **kwargs)

    monkeypatch.setattr(mechanics, 'calculate', observed)
    mechanics.execute_mechanics(*explicit(fixture((.2, .3)), .5))
    assert calls == [10, .5]
    calls.clear()
    mechanics.execute_mechanics(*explicit(fixture((.2, .3)), .2))
    assert calls == [10, .2]
