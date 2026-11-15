"""Small independent checks for operation-state and bounded response encoding."""
import json
from types import SimpleNamespace

import pytest

from genshin_app.gui import document_transport as wire


@pytest.mark.parametrize('method,text', [('__getattribute__', '[]'), ('new_plan', '{}'),
                                       ('new_plan', '[NaN]'), ('new_plan', '[{"x":1,"x":2}]')])
def test_rejection_before_call_explicitly_states_not_submitted(method, text):
    api = SimpleNamespace(new_plan=lambda *_: pytest.fail('must not reach method'))
    response = json.loads(wire.dispatch(api, method, text))
    assert response['operation_may_have_completed'] is False
    assert '尚未提交' in response['message']


@pytest.mark.parametrize('bad', [{'x': float('inf')}, {'x': object()}, {'x': '\ud800'}])
def test_called_method_response_failure_is_not_reported_as_unsubmitted(bad):
    calls = []
    api = SimpleNamespace(new_plan=lambda: calls.append('applied') or bad)
    response = json.loads(wire.dispatch(api, 'new_plan', '[]'))
    assert calls == ['applied']
    assert response['operation_may_have_completed'] is True
    assert '勿直接重复提交' in response['message']


def test_existing_denial_is_not_wrapped_or_reclassified():
    expected = {'code': 13, 'reason': 'dependency_stale', 'details': {'x': 1.0}}
    response = json.loads(wire.dispatch(SimpleNamespace(new_plan=lambda: expected), 'new_plan', '[]'))
    assert response == expected
    assert 'operation_may_have_completed' not in response
    assert type(response['details']['x']) is float


def test_response_utf8_limit_counts_aggregate_fragments(monkeypatch):
    monkeypatch.setattr(wire, 'MAX_BYTES', 25)
    calls = []
    api = SimpleNamespace(new_plan=lambda: calls.append(True) or {'x': ['汉汉汉', '汉汉汉']})
    response = json.loads(wire.dispatch(api, 'new_plan', '[]'))
    assert calls == [True]
    assert response['operation_may_have_completed'] is True
    assert response['reason'] == 'document_transport'


def test_encoder_does_not_iterate_past_known_total_limit(monkeypatch):
    monkeypatch.setattr(wire, 'MAX_BYTES', 10)
    visits = []
    class Encoder:
        def __init__(self, **kwargs):
            assert kwargs['allow_nan'] is False and kwargs['ensure_ascii'] is False
        def iterencode(self, response):
            for chunk in ['[', '"汉汉汉"', ',123]']:
                visits.append(chunk)
                yield chunk
    monkeypatch.setattr(wire.json, 'JSONEncoder', Encoder)
    response = json.loads(wire.dispatch(SimpleNamespace(new_plan=list), 'new_plan', '[]'))
    assert visits == ['[', '"汉汉汉"']
    assert response['operation_may_have_completed'] is True


def test_valid_response_exact_numeric_types_and_bounded_error_description():
    original = {'x': 1.0, 'i': 1, 'z': -0.0, 'tiny': 1e-7}
    text = wire.dispatch(SimpleNamespace(new_plan=lambda: original), 'new_plan', '[]')
    assert text == json.dumps(original, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    def fail():
        raise ValueError('x' * 10000)
    response = json.loads(wire.dispatch(SimpleNamespace(new_plan=fail), 'new_plan', '[]'))
    assert len(response['message']) < 1200
    assert response['operation_may_have_completed'] is True
