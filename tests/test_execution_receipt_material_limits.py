"""Real bounds, cross-root accounting and operation lifecycle regression."""
import copy
import json
import threading

import pytest

from genshin_app.equipment import execution_verification as subject


def session(**limits):
    return subject.ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': limits})


def test_actual_input_limit_is_not_relaxed_by_output_limit():
    large = [0] * 200000
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session(input_nodes=200000).scan_receipt_materials(inputs=(large,))
    assert caught.value.code == 'verification_input_nodes'
    assert session().scan_receipt_materials(outputs=(large,))['output']['nodes'] == 200002


def test_output_roots_share_one_node_envelope(monkeypatch):
    assert session(output_nodes=9).scan_receipt_materials(outputs=([1, 2, 3], [4, 5, 6]))['output']['nodes'] == 9
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session(output_nodes=9).scan_receipt_materials(outputs=([1, 2, 3], [4, 5, 6, 7]))
    assert caught.value.code == 'verification_output_nodes'


def test_input_roots_share_one_node_envelope(monkeypatch):
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session(input_nodes=8).scan_receipt_materials(inputs=([1, 2, 3], [4, 5, 6]))
    assert caught.value.code == 'verification_input_nodes'


@pytest.mark.parametrize('role', ['input', 'output'])
def test_bytes_are_exact_and_aggregate_across_roots(role, monkeypatch):
    roots = ('中文\\\n', {'a': 1.0})
    expected = len(json.dumps(roots, ensure_ascii=False, separators=(',', ':')).encode('utf8'))
    assert session(**{role + '_bytes': expected}).scan_receipt_materials(**{role + 's': roots})[role]['bytes'] == expected
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session(**{role + '_bytes': expected - 1}).scan_receipt_materials(**{role + 's': roots})
    assert caught.value.code == 'verification_' + role + '_bytes'


def test_aliases_charge_every_occurrence_without_copy_or_mutation():
    item = {'中文': [1, 2]}
    before = copy.deepcopy(item)
    left, right = session(), session()
    a = left.scan_receipt_materials(outputs=(item, item))
    b = right.scan_receipt_materials(outputs=(item, copy.deepcopy(item)))
    assert a == b and left.report()['usage'] == right.report()['usage']
    assert item == before
    single = session().scan_receipt_materials(outputs=(item,))
    assert a['output']['nodes'] == 2 * single['output']['nodes'] - 1


def test_same_definition_embedded_in_output_remains_strict_input():
    item = {'template': {'illegal_tuple': ()}}
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session().scan_receipt_materials(inputs=(item,), outputs=({'execution': item},))
    assert caught.value.code == 'verification_material_shape'
    session().scan_receipt_materials(outputs=({'native_tuple': (1, 2)},))


@pytest.mark.parametrize('item', [float('nan'), float('inf'), '\ud800', 1 << 4097, {1: 'a'}, object()])
def test_bad_scalars_and_keys_fail_after_charging(item):
    run = session()
    with pytest.raises(subject.ExecutionVerificationError):
        run.scan_receipt_materials(outputs=(item,))
    assert run.report()['usage']['work'] > 0


def test_cycle_depth_and_failures_never_reset_work(monkeypatch):
    run = session(depth=2)
    item = []
    item.append(item)
    with pytest.raises(subject.ExecutionVerificationError):
        run.scan_receipt_materials(outputs=(item,))
    prior = run.report()['usage']['work']
    with pytest.raises(subject.ExecutionVerificationError):
        run.scan_receipt_materials(outputs=([[[0]]],))
    assert run.report()['usage']['work'] > prior


def test_budget_exhaustion_happens_before_encoding_and_stops_all_helpers(monkeypatch):
    probe = session()
    probe.scan_receipt_materials(outputs=(1,))
    used = probe.report()['usage']['work']
    run = session(work=used)
    run.scan_receipt_materials(outputs=(1,))
    monkeypatch.setattr(subject.json, 'dumps', lambda *args, **kwargs: pytest.fail('unpaid encoding'))
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        run.scan_receipt_materials(outputs=(1,))
    assert caught.value.code == 'verification_budget'
    assert run.report()['stopped']
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        run.replay({})
    assert caught.value.code == 'verification_stopped'
    assert run.report()['usage']['work'] == used


def test_thread_closed_and_reentry_rejected():
    run = session()
    errors = []
    def child():
        try:
            run.scan_receipt_materials(outputs=(1,))
        except subject.ExecutionVerificationError as exc:
            errors.append(exc.code)
    thread = threading.Thread(target=child)
    thread.start()
    thread.join()
    assert errors == ['verification_thread']
    run._busy = True
    with pytest.raises(subject.ExecutionVerificationError):
        run.scan_receipt_materials()
    run._busy = False
    run.close()
    with pytest.raises(subject.ExecutionVerificationError):
        run.scan_receipt_materials()
