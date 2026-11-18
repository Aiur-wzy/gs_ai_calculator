
import pytest

from genshin_db import atomic_io


def busy(code=5):
    error=PermissionError('temporary Windows file lock')
    error.winerror=code
    return error


@pytest.mark.parametrize('code',[5,32,33])
@pytest.mark.parametrize('directory',[False,True])
def test_transient_lock_retries_same_atomic_operation(tmp_path,monkeypatch,code,directory):
    source,target=tmp_path/'candidate',tmp_path/'published'
    if directory:
        source.mkdir()
        (source/'content').write_bytes(b'new')
    else:
        source.write_bytes(b'new')
        target.write_bytes(b'old')
    replace=atomic_io.os.replace
    calls=[]
    delays=[]
    def locked(s,t):
        calls.append((s,t))
        if len(calls)<=2:
            assert source.exists()
            if not directory:assert target.read_bytes()==b'old'
            raise busy(code)
        replace(s,t)
    monkeypatch.setattr(atomic_io,'_WINDOWS',True)
    monkeypatch.setattr(atomic_io.os,'replace',locked)
    monkeypatch.setattr(atomic_io.time,'sleep',delays.append)
    atomic_io.atomic_replace(source,target)
    assert calls==[(source,target)]*3 and delays==[.02,.05]
    assert not source.exists()
    assert (target/'content' if directory else target).read_bytes()==b'new'


def test_persistent_denial_is_not_swallowed_or_replaced_with_nonatomic_copy(tmp_path,monkeypatch):
    source,target=tmp_path/'candidate',tmp_path/'published'
    source.write_bytes(b'new');target.write_bytes(b'old')
    errors=[]
    def denied(*_):
        error=busy();errors.append(error);raise error
    monkeypatch.setattr(atomic_io,'_WINDOWS',True)
    monkeypatch.setattr(atomic_io.os,'replace',denied)
    monkeypatch.setattr(atomic_io.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError) as caught:atomic_io.atomic_replace(source,target)
    assert len(errors)==7 and caught.value is errors[-1]
    assert source.read_bytes()==b'new' and target.read_bytes()==b'old'


@pytest.mark.parametrize('windows,code',[(False,5),(True,17),(True,None)])
def test_other_errors_are_immediate(monkeypatch,windows,code):
    error=busy(code)
    calls=[]
    def fail(*args):calls.append(args);raise error
    monkeypatch.setattr(atomic_io,'_WINDOWS',windows)
    monkeypatch.setattr(atomic_io.os,'replace',fail)
    monkeypatch.setattr(atomic_io.time,'sleep',lambda _:pytest.fail('must not retry unrelated failure'))
    with pytest.raises(PermissionError) as caught:atomic_io.atomic_replace('a','b')
    assert caught.value is error and calls==[('a','b')]


@pytest.mark.parametrize('module_name,writer',[
    ('genshin_app.release_pair_update','_write_json_atomic'),
    ('genshin_db.release_migration','_write_json_atomic'),
    ('genshin_db.release_migration','_write_bytes_atomic'),
])
def test_transaction_journal_survives_transient_lock_and_cleans_temp(tmp_path,monkeypatch,module_name,writer):
    import importlib
    import json
    module=importlib.import_module(module_name)
    target=tmp_path/'journal.json';target.write_bytes(b'old')
    replace=atomic_io.os.replace
    attempts=[]
    def locked(source,destination):
        attempts.append((source,destination))
        if len(attempts)==1:
            assert target.read_bytes()==b'old'
            raise busy(5)
        replace(source,destination)
    monkeypatch.setattr(atomic_io,'_WINDOWS',True)
    monkeypatch.setattr(atomic_io.os,'replace',locked)
    monkeypatch.setattr(atomic_io.time,'sleep',lambda _:None)
    value={'state':'committed'}
    getattr(module,writer)(target,json.dumps(value).encode() if writer=='_write_bytes_atomic' else value)
    assert json.loads(target.read_bytes())==value
    assert len(attempts)==2 and attempts[0]==attempts[1]
    assert list(tmp_path.iterdir())==[target]
