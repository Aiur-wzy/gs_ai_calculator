import hashlib
import json
from pathlib import Path

import pytest

from genshin_app import calculation_review_batch_cli as publication
from genshin_app import review_batch_json as bounded
from genshin_app.calculation_review_batch import review_calculation_batch
from genshin_app.cli import main
from genshin_app.equipment.files import fingerprint, json_bytes
from tests.test_calculation_review import _saved


def inputs(tmp_path, query=None):
    saved, request, output = (tmp_path / name for name in ('calculated.json', 'request.json', 'batch.json'))
    saved.write_bytes(json_bytes(_saved()))
    request.write_bytes(json_bytes({'requests': query or [{'view': 'hits', 'limit': 7}, {'view': 'summary'}]}))
    return saved, request, output


def call(saved, request, output=None, *, pretty=False, data_dir=None):
    args = ['--data-dir', str(data_dir or saved.parent)]
    if pretty:
        args += ['--pretty']
    args += ['equipment', 'review-batch', '--calculated', str(saved), '--request', str(request)]
    if output:
        args += ['--output', str(output), '--overwrite']
    return main(args)


@pytest.mark.parametrize('pretty', [False, True])
def test_cli_public_output_and_file_receipt_exact(tmp_path, capsys, pretty):
    saved, request, output = inputs(tmp_path)
    assert call(saved, request, output, pretty=pretty) == 0
    captured = capsys.readouterr()
    response = json.loads(captured.out)
    data = response['data']
    files = data.pop('files')
    assert data == json.loads(output.read_text(encoding='utf-8'))
    assert data == review_calculation_batch(_saved(), json.loads(request.read_text()))
    assert files['published_paths'] == [str(output.resolve())]
    assert files['file_shas'][str(output.resolve())] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert len(captured.out.encode('utf-8')) <= bounded.BATCH_BYTES
    assert not (tmp_path / 'genshin.db').exists()


@pytest.mark.parametrize('raw', ['{"requests":[],"requests":[{}]}', '{"requests":[{"limit":NaN}]}',
    '{"requests":[{"filters":{"character":"\\ud800"}}]}', '{"requests":[{}, {"limit":true}]}',
    '{"requests":[{}, {"unknown":1}]}', '{"requests":[{}],"prepared":true}'])
def test_bad_request_precedes_big_input_and_never_publishes(tmp_path, capsys, monkeypatch, raw):
    saved, request, output = inputs(tmp_path)
    request.write_text(raw, encoding='utf-8')
    original = Path.open

    def open_guard(path, *args, **kwargs):
        if path == saved:
            pytest.fail('invalid request reached calculated file read')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_guard)
    assert call(saved, request, output) == 1
    assert not output.exists() and not capsys.readouterr().out


@pytest.mark.parametrize('target', ['calculated', 'request', 'database', 'request_backup', 'request_lock'])
def test_cli_protects_input_request_database_and_auxiliary_paths(tmp_path, capsys, target):
    saved, request, output = inputs(tmp_path)
    db = tmp_path / 'genshin.db'
    db.write_bytes(b'database sentinel')
    if target == 'calculated':
        output = saved
    elif target == 'request':
        output = request
    elif target == 'database':
        output = db
    elif target == 'request_backup':
        request = tmp_path / 'batch.json.previous'
        request.write_text('{"requests":[{}]}')
    else:
        request = tmp_path / '.batch.json.plan.lock'
        request.write_text('{"requests":[{}]}')
    before = {p: p.read_bytes() for p in (saved, request, db)}
    assert call(saved, request, output) == 1
    assert json.loads(capsys.readouterr().err)['reason'] == 'file_conflict'
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_cli_protects_input_hardlink(tmp_path, capsys):
    saved, request, output = inputs(tmp_path)
    try:
        output.hardlink_to(saved)
    except OSError as exc:
        pytest.skip(f"当前测试卷或ACL不允许创建硬链接：{exc}")
    before = saved.read_bytes()
    assert call(saved, request, output) == 1
    assert json.loads(capsys.readouterr().err)['reason'] == 'file_conflict'
    assert saved.read_bytes() == before


def test_second_projection_failure_preserves_previous_output(tmp_path, capsys):
    saved, request, output = inputs(tmp_path, [{'view': 'hits'}, {'view': 'sampling'}])
    value = _saved()
    value['result']['sampling_trace'] = [False]
    value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])
    saved.write_bytes(json_bytes(value))
    output.write_bytes(b'existing successful output')
    assert call(saved, request, output) == 1
    assert not capsys.readouterr().out and output.read_bytes() == b'existing successful output'


@pytest.mark.parametrize('pretty', [False, True])
def test_final_envelope_and_file_receipt_preflight_before_publish(tmp_path, capsys, monkeypatch, pretty):
    saved, request, output = inputs(tmp_path)
    expected = review_calculation_batch(_saved(), json.loads(request.read_text()))
    # Data itself fits; the real CLI envelope and file receipt do not.
    monkeypatch.setattr(publication, 'BATCH_BYTES', len(json_bytes(expected)) + 1)
    monkeypatch.setattr(publication, 'publish_files', lambda *a, **k: pytest.fail('published before final envelope check'))
    assert call(saved, request, output, pretty=pretty) == 1
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)['reason'] == 'review_size_limit'
    assert not output.exists()


def test_atomic_publish_failure_does_not_return_success(tmp_path, capsys, monkeypatch):
    saved, request, output = inputs(tmp_path)
    output.write_text('previous good output')

    def failure(*args, **kwargs):
        raise OSError('injected atomic publication failure')

    monkeypatch.setattr(publication, 'publish_files', failure)
    assert call(saved, request, output) == 1
    captured = capsys.readouterr()
    assert not captured.out and 'injected' not in captured.err
    from genshin_app.app_system_fault import validate_system_failure
    validate_system_failure(json.loads(captured.err))
    assert output.read_text() == 'previous good output'


@pytest.mark.parametrize('extra', [[], ['--calculated', 'not-read.json'], ['--request', 'not-read.json']])
def test_missing_cli_required_paths_fail_without_read(extra, capsys):
    assert main(['equipment', 'review-batch', *extra]) == 1
    assert not capsys.readouterr().out


def test_calculated_json_duplicate_keys_not_accepted(tmp_path, capsys):
    saved, request, output = inputs(tmp_path)
    saved.write_text('{"result":{},"result":{}}')
    assert call(saved, request, output) == 1
    assert not output.exists() and not capsys.readouterr().out
