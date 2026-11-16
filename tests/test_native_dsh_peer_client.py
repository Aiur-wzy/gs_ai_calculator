from __future__ import annotations

from types import SimpleNamespace

import pytest

from genshin_app.agent_chat.native_dsh_application import NativeDshApplication
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_peer_client import NativeDshPeerClient
from genshin_app.agent_chat.native_dsh_production import (
    legacy_credential_import_allowed,
)


class Carrier:
    def __init__(self):
        self.calls = []
        self.closed = False

    def control(self, operation, payload=None):
        self.calls.append((operation, payload))
        if operation == "dsh_runtime_client_create":
            return {**payload, "state": "ready"}
        if operation == "dsh_runtime_client_close":
            return {**payload, "closed": True}
        return {"state": "drained" if operation == "dsh_runtime_drain" else "ready"}

    def boot(self):
        return {"injections": []}

    def bundle(self, url):
        return {"status": 200, "contentType": "text/javascript", "body": b"example"}

    def fetch(self, metadata, body, *, request_id):
        self.calls.append(("fetch", metadata))
        return {"status": 200}, b"{}"

    def open_stream(self, endpoint, payload, **kwargs):
        self.calls.append(("open_stream", kwargs))
        return kwargs["stream_id"]

    def release_stream(self, identity):
        self.calls.append(("release_stream", identity))

    def wait_domain_idle(self):
        pass

    def close(self):
        self.closed = True


def test_window_close_retires_its_peer_and_resources_without_shared_process():
    carrier = Carrier()
    closed = []
    app = NativeDshApplication(SimpleNamespace(client=carrier, close=lambda: closed.append(True)),
        credentials=None, synchronize_legacy=False)
    main = app.create_client("main-window")
    child = app.create_client("child-window")
    child.dsh_client_bundle({"url": "fixture.js"})
    assert child._resources
    child.close()
    assert not child._resources
    assert not carrier.closed and not closed
    assert main.dsh_client_boot() == {"injections": []}
    with pytest.raises(NativeDshMuxError, match="dsh_shell_closed"):
        child.dsh_client_boot()
    app.close()
    assert carrier.closed and closed == [True]


def test_peer_binding_cannot_be_overridden_and_cross_window_stream_access_is_rejected():
    carrier = Carrier()
    a, b = NativeDshPeerClient(carrier, "a"), NativeDshPeerClient(carrier, "b")
    a.fetch({"peer_id": "b"}, b"", request_id="request")
    assert carrier.calls[-1] == ("fetch", {"peer_id": "a"})
    a.open_stream("session/follow", {}, stream_id="owned")
    assert carrier.calls[-1][1]["peer_id"] == "a"
    with pytest.raises(NativeDshMuxError, match="dsh_shell_stream_unknown"):
        b.stream_next("owned")
    a.close()
    assert ("release_stream", "owned") in carrier.calls
    assert not carrier.closed


def test_new_install_never_imports_shared_legacy_credential_on_second_launch(tmp_path):
    assert legacy_credential_import_allowed(tmp_path) is False
    old = tmp_path / "runtime-profile" / "legacy-settings-migration.json"
    old.parent.mkdir()
    old.write_text("{}")
    assert legacy_credential_import_allowed(tmp_path) is False


def test_preexisting_profile_can_migrate_legacy_credential_and_corrupt_policy_is_not_ignored(tmp_path):
    old = tmp_path / "runtime-profile" / "legacy-settings-migration.json"
    old.parent.mkdir()
    old.write_text("{}")
    assert legacy_credential_import_allowed(tmp_path) is True
    (tmp_path / "legacy-credential-import.json").write_text("{broken")
    with pytest.raises(NativeDshMuxError, match="migration_marker_invalid"):
        legacy_credential_import_allowed(tmp_path)


def test_window_cleanup_keeps_draining_after_one_stream_failure():
    carrier = Carrier()
    released = []
    def release(identity):
        released.append(identity)
        raise NativeDshMuxError("synthetic_stream_release_failure")
    carrier.release_stream = release
    callback = []
    peer = NativeDshPeerClient(carrier, "child", on_close=lambda: callback.append(True))
    peer.open_stream("one", {}, stream_id="one")
    peer.open_stream("two", {}, stream_id="two")
    with pytest.raises(NativeDshMuxError, match="synthetic_stream_release_failure"):
        peer.close()
    assert set(released) == {"one", "two"}
    assert ("dsh_runtime_client_close", {"peer_id": "child"}) in carrier.calls
    assert callback == [True] and not carrier.closed
