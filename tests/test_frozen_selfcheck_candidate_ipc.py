"""Real audit-hook processes for the single named QA listener capability."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_app.frozen_selfcheck_isolation import SelfcheckError
from genshin_app.frozen_selfcheck_runtime import _candidate_listener_binding

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import json, socket, sys, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from genshin_app.agent_chat.native_cli_link import LiveCandidateCliLink, _LoopbackCandidateHTTPServer
from genshin_app.frozen_selfcheck_isolation import SelfcheckContext, SelfcheckError
from genshin_app.frozen_selfcheck_runtime import _candidate_listener_binding

root, mode = Path(sys.argv[1]), sys.argv[2]
context = SelfcheckContext.create(root, root/'report.json',
    package_root=Path(sys.executable).parent, executable=sys.executable)
original_bind = _LoopbackCandidateHTTPServer.server_bind
socket.getfqdn = lambda *_args: (_ for _ in ()).throw(AssertionError('DNS metadata is forbidden'))
context.prepare()
denied = []
def reject(name, operation):
    with context.negative_probe():
        try:
            operation()
        except SelfcheckError as error:
            assert error.reason == 'selfcheck_network_denied', (name, error.reason)
            denied.append(name)
        else:
            raise AssertionError('operation was not denied: '+name)

if mode == 'wrapper':
    with _candidate_listener_binding(context):
        link = LiveCandidateCliLink(root/'connections', lambda *_args: None)
        endpoint = link._server.server_address
        assert endpoint[0] == '127.0.0.1' and 1 <= endpoint[1] <= 65535
        assert link._server.server_name == endpoint[0]
        assert link._server.server_port == endpoint[1]
        assert context.local_candidate_ipc_evidence()['active_authorizations'] == 0
        link.close()
    assert _LoopbackCandidateHTTPServer.server_bind is original_bind
elif mode == 'matrix':
    server = _LoopbackCandidateHTTPServer(('127.0.0.1',0), BaseHTTPRequestHandler,
                                          bind_and_activate=False)
    original_socket = server.socket
    def attempt_scope(value):
        with context._candidate_listener_bind(value):
            raise AssertionError('invalid server scope admitted')
    for label, address in [('host',('0.0.0.0',0)),('hostname',('localhost',0)),
                           ('fixed_port',('127.0.0.1',1)),('bool_port',('127.0.0.1',False))]:
        server.server_address = address
        reject('scope_'+label, lambda: attempt_scope(server))
    server.server_address = ('127.0.0.1',0)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.socket = udp
    reject('scope_datagram', lambda: attempt_scope(server))
    udp.close()
    ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    server.socket = ipv6
    reject('scope_ipv6', lambda: attempt_scope(server))
    ipv6.close()
    server.socket = original_socket
    reject('scope_foreign_object', lambda: attempt_scope(object()))
    other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with context._candidate_listener_bind(server):
        reject('other_socket', lambda: other.bind(('127.0.0.1',0)))
        reject('wildcard', lambda: server.socket.bind(('0.0.0.0',0)))
        reject('fixed_port', lambda: server.socket.bind(('127.0.0.1',1)))
        reject('connect', lambda: server.socket.connect(('127.0.0.1',9)))
        reject('dns_forward', lambda: socket.getaddrinfo('127.0.0.1',9))
        reject('dns_reverse', lambda: socket.gethostbyaddr('127.0.0.1'))
        def other_thread():
            errors=[]
            def operation():
                try: server.socket.bind(('127.0.0.1',0))
                except Exception as error: errors.append(error)
            worker=threading.Thread(target=operation)
            worker.start(); worker.join(5)
            assert not worker.is_alive()
            if errors: raise errors[0]
        reject('other_thread', other_thread)
        server.server_bind()
        reject('second_bind_in_scope', lambda: server.socket.bind(('127.0.0.1',0)))
    reject('bind_after_scope', lambda: server.socket.bind(('127.0.0.1',0)))
    reject('new_authorization', lambda: attempt_scope(server))
    other.close(); server.server_close()
elif mode == 'concurrent':
    servers = [_LoopbackCandidateHTTPServer(('127.0.0.1',0), BaseHTTPRequestHandler,
        bind_and_activate=False) for _ in range(2)]
    start=threading.Barrier(3)
    entered=threading.Event(); lost=threading.Event(); release=threading.Event()
    winners=[]; errors=[]
    def compete(server):
        start.wait()
        try:
            with context._candidate_listener_bind(server):
                entered.set()
                assert release.wait(5)
                server.server_bind()
                winners.append(server.server_address)
        except SelfcheckError as error:
            assert error.reason == 'selfcheck_network_denied'
            denied.append('concurrent_authorization');lost.set()
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            server.server_close()
    with context.negative_probe():
        workers=[threading.Thread(target=compete,args=(server,)) for server in servers]
        for worker in workers: worker.start()
        start.wait()
        try:
            assert entered.wait(5) and lost.wait(5)
        finally:
            release.set()
            for worker in workers: worker.join(5)
        assert not any(worker.is_alive() for worker in workers)
    assert not errors and len(winners) == 1
elif mode == 'abort':
    server = _LoopbackCandidateHTTPServer(('127.0.0.1',0), BaseHTTPRequestHandler,
                                          bind_and_activate=False)
    try:
        with context._candidate_listener_bind(server):
            raise RuntimeError('original binding failure')
    except RuntimeError as error:
        assert str(error) == 'original binding failure'
    assert context.local_candidate_ipc_evidence()['active_authorizations'] == 0
    reject('aborted_scope', lambda: server.socket.bind(('127.0.0.1',0)))
    server.server_close()
else:
    raise AssertionError(mode)

context.assert_normal_clean()
evidence=context.local_candidate_ipc_evidence()
if mode != 'abort':
    assert evidence['bind_calls'] == 1 and len(evidence['listeners']) == 1
    assert evidence['listeners'][0]['closed'] is True
else:
    assert evidence['bind_calls'] == 0 and evidence['listeners'] == []
assert evidence['active_authorizations'] == 0
context.write_report({'normal':context.counters['normal'], 'negative':context.counters['negative'],
    'denied':denied, 'local_candidate_ipc':evidence, 'mode':mode})
'''


@pytest.mark.parametrize("mode", ["wrapper", "matrix", "concurrent", "abort"])
def test_real_guard_candidate_listener_scope(tmp_path, mode):
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    sandbox = tmp_path / "sandbox"
    completed = subprocess.run([sys.executable, "-c", SCRIPT, str(sandbox), mode],
        cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads((sandbox / "report.json").read_text(encoding="utf-8"))
    assert evidence["normal"] == {}
    assert evidence["local_candidate_ipc"]["active_authorizations"] == 0
    if mode == "matrix":
        assert len(evidence["denied"]) == 17
        assert evidence["negative"] == {"network": 17}
    elif mode == "concurrent":
        assert evidence["negative"] == {"network": 1}
    elif mode == "wrapper":
        assert evidence["local_candidate_ipc"]["listeners"][0]["closed"] is True


def test_json_or_fake_context_cannot_authorize_candidate_listener():
    with pytest.raises(SelfcheckError, match="selfcheck_context_invalid"), _candidate_listener_binding({
            "contract": "genshin-selfcheck-local-candidate-ipc-v1", "bind_calls": 1}):
        pytest.fail("JSON authorized network access")


def test_fake_context_rejected_before_offline_provider_creates_any_files(tmp_path):
    from genshin_app.frozen_selfcheck_runtime import offline_production_provider

    with pytest.raises(SelfcheckError, match="selfcheck_context_invalid"), offline_production_provider(
            root=tmp_path, data_dir=tmp_path / "data", runtime_root=tmp_path / "runtime",
            assets_root=tmp_path / "assets", package_root=tmp_path / "package",
            selfcheck_context={"bind_calls": 1}):
        pytest.fail("JSON authorized network access")
    assert list(tmp_path.iterdir()) == []
