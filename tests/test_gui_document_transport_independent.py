"""Independent JSON bridge checks: transport is not an approval mechanism."""
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app import calculation_delivery_files
from genshin_app.agent_chat import sessions
from genshin_app.equipment.files import fingerprint
from genshin_app.gui import document_transport as wire
from genshin_app.gui.api import GuiApi


def test_gui_envelope_has_headroom_around_96_mib_calculated_artifact():
    assert wire.MAX_BYTES == 192 * 1024 * 1024
    assert sessions.MAX_CALCULATED == 96 * 1024 * 1024
    assert calculation_delivery_files.LIMITS['calculated_bytes'] == sessions.MAX_CALCULATED
    assert wire.MAX_BYTES == 2 * sessions.MAX_CALCULATED


def endpoint(fn):
    return SimpleNamespace(inspect_calculation_diagnostics=fn)


def test_actual_whitelist_names_exist_and_cannot_call_bridge_or_private_methods():
    assert all(callable(getattr(GuiApi, name, None)) for name in wire.DOCUMENT_METHODS)
    assert 'document_json_call' not in wire.DOCUMENT_METHODS
    assert not any(name.startswith('_') for name in wire.DOCUMENT_METHODS)


def test_every_frontend_document_method_is_backend_allowlisted():
    source = (Path(wire.__file__).parent / 'web' / 'json-transport.js').read_text(encoding='utf8')
    assert 'const MAX_BYTES = 192 * 1024 * 1024' in source
    block = source.split('const methods = Object.freeze([', 1)[1].split(']);', 1)[0]
    frontend_methods = frozenset(re.findall(r'"([^"\\]+)"', block))

    assert frontend_methods <= wire.DOCUMENT_METHODS


@pytest.mark.parametrize('method', [
    'action_capacity_model_candidates',
    'whiteboard_action_capacity_model_candidates',
    'action_admission_model_candidates',
    'action_admission_preview',
    'whiteboard_action_admission_model_candidates',
])
def test_catalog_and_admission_document_methods_dispatch_without_loss(method):
    calls = []
    api = SimpleNamespace(**{
        method: lambda *args: calls.append(args) or {'code': 0, 'data': args[0]},
    })

    response = json.loads(wire.dispatch(api, method, '[{"value":1.0}]'))

    assert response == {'code': 0, 'data': {'value': 1.0}}
    assert len(calls) == 1
    assert type(calls[0][0]['value']) is float


def test_fingerprints_remain_type_sensitive_and_exact_wire_restores_them():
    text = '[{"a":1.0,"b":1,"negative":-0.0,"tiny":1e-07,"big":9007199254740993}]'
    original = json.loads(text)[0]
    parsed = []
    response = json.loads(wire.dispatch(endpoint(lambda v: parsed.append(v) or {'code': 0, 'data': v}),
                                        'inspect_calculation_diagnostics', text))
    assert fingerprint(response['data']) == fingerprint(original)
    assert type(parsed[0]['a']) is float and type(parsed[0]['b']) is int
    assert parsed[0]['big'] == 9007199254740993  # Backend never rounds; JS must reject unsafe editing.
    assert math.copysign(1, parsed[0]['negative']) == -1
    assert fingerprint({'a': 1}) != fingerprint({'a': 1.0})


@pytest.mark.parametrize('text', ['[{"a":1,"\\u0061":2}]', '[{"__proto__":1,"__proto__":2}]',
                                 '[{"nested":{"x":1,"x":1}}]', '[{"x":-Infinity}]',
                                 '[{"x":1e400}]', '["\\ud800"]', '["\\udfff"]'])
def test_decoded_duplicates_and_nonfinite_are_rejected_before_endpoint(text):
    def forbidden(*args):
        raise AssertionError('invalid transport reached existing API')
    result = json.loads(wire.dispatch(endpoint(forbidden), 'inspect_calculation_diagnostics', text))
    assert result['code'] == 1 and result['reason'] == 'document_transport'


def test_original_api_error_envelope_is_preserved_without_approval():
    denial = {'code': 73, 'reason': 'source_context_stale', 'data': {'confirmed': False}}
    result = json.loads(wire.dispatch(endpoint(lambda value: denial), 'inspect_calculation_diagnostics', '[{}]'))
    assert result == denial


def test_escaped_brackets_do_not_fake_nesting_or_reject_valid_text(monkeypatch):
    monkeypatch.setattr(wire, 'MAX_DEPTH', 3)
    text = json.dumps([{'x': '[]' * 100 + '\\"{' + '汉😀'}], ensure_ascii=False)
    response = json.loads(wire.dispatch(endpoint(lambda x: x), 'inspect_calculation_diagnostics', text))
    assert response == json.loads(text)[0]


@pytest.mark.parametrize('text', ['[[[[0]]]]', '["汉汉汉汉汉汉汉汉汉汉"]'])
def test_depth_and_utf8_bytes_prevent_dispatch(monkeypatch, text):
    monkeypatch.setattr(wire, 'MAX_DEPTH', 3)
    monkeypatch.setattr(wire, 'MAX_BYTES', 25)
    called = []
    response = json.loads(wire.dispatch(endpoint(lambda x: called.append(x)), 'inspect_calculation_diagnostics', text))
    assert not called
    assert response['reason'] == 'document_transport'


def test_overlarge_response_is_explicit_failure_not_lossy_fallback(monkeypatch):
    monkeypatch.setattr(wire, 'MAX_BYTES', 30)
    calls = []
    def fn(value):
        calls.append(value)
        return {'data': '汉' * 20}
    result = json.loads(wire.dispatch(endpoint(fn), 'inspect_calculation_diagnostics', '[1.0]'))
    assert len(calls) == 1 and type(calls[0]) is float
    assert result['reason'] == 'document_transport'


def test_request_accepts_exact_utf8_budget_and_rejects_one_extra_byte_before_call(monkeypatch):
    exact = '["\u6c49"]'
    maximum = len(exact.encode('utf8'))
    monkeypatch.setattr(wire, 'MAX_BYTES', maximum)
    calls = []
    api = endpoint(lambda value: calls.append(value) or {})

    accepted = json.loads(wire.dispatch(api, 'inspect_calculation_diagnostics', exact))
    rejected = json.loads(wire.dispatch(api, 'inspect_calculation_diagnostics', exact + ' '))

    assert accepted == {}
    assert calls == ['\u6c49']
    assert rejected['reason'] == 'document_transport'
    assert rejected['operation_may_have_completed'] is False


def test_recovery_response_accepts_exact_budget_and_rejects_one_extra_byte_after_call(monkeypatch):
    maximum = 32
    monkeypatch.setattr(wire, 'MAX_BYTES', maximum)
    calls = []
    exact = {'x': 'a' * 24}
    assert len(json.dumps(exact, ensure_ascii=False, separators=(',', ':')).encode('utf8')) == maximum
    api = SimpleNamespace(agent_recover_candidate_application=lambda: calls.append('exact') or exact)
    assert json.loads(wire.dispatch(api, 'agent_recover_candidate_application', '[]')) == exact

    over = {'x': 'a' * 25}
    api = SimpleNamespace(agent_recover_candidate_application=lambda: calls.append('over') or over)
    rejected = json.loads(wire.dispatch(api, 'agent_recover_candidate_application', '[]'))
    assert calls == ['exact', 'over']
    assert rejected['reason'] == 'document_transport'
    assert rejected['operation_may_have_completed'] is True
    assert '勿直接重复提交' in rejected['message']


def test_no_hidden_persistent_approval_or_numeric_token_state_between_calls():
    calls = []
    api = endpoint(lambda value: calls.append(value) or value)
    first = json.loads(wire.dispatch(api, 'inspect_calculation_diagnostics', '[{"x":1.0}]'))
    second = json.loads(wire.dispatch(api, 'inspect_calculation_diagnostics', '[{"x":1}]'))
    assert type(first['x']) is float and type(second['x']) is int
    assert len(calls) == 2
