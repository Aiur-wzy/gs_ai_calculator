from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from genshin_app.gui.agent_window import AgentWindowError
from tests import test_agent_window_lifecycle as window_tests

fixture = window_tests.fixture


def begin_return(state):
    controller, _, _, _, _, webview = state
    opened = controller.open({"session_id": "active", "draft": {"text": "original"}})
    bridge = webview.create_window.call_args.kwargs["js_api"]
    identity = bridge._identity
    controller.ready(identity, {"generation": opened["generation"]})
    controller.update_draft(identity, {"generation": opened["generation"], "session_id": "active",
        "draft": {"text": "source latest", "files": ["binary identity"]},
        "drafts": {"other": {"text": "second unsent draft"}}})
    returning = controller.return_to_main(identity)
    return returning, identity, bridge


def test_cancel_return_preserves_source_peer_and_permits_fresh_return(fixture):
    controller, _, application, peer, window, _ = fixture
    returning, identity, bridge = begin_return(fixture)
    assert controller.state()["return_source_id"] == identity
    assert not hasattr(bridge, "dsh_window_cancel_return")
    after = controller.cancel_return({"generation": returning["generation"], "source_window_id": identity})
    assert after["presentation"] == "detached" and after["generation"] > returning["generation"]
    peer.close.assert_not_called()
    window.destroy.assert_not_called()
    application.close.assert_not_called()
    with pytest.raises(AgentWindowError, match="handoff_stale"):
        controller.main_ready({"generation": returning["generation"]})
    controller.update_draft(identity, {"generation": after["generation"], "session_id": "active", "draft": "edited again"})
    fresh = controller.return_to_main(identity)
    assert fresh["handoff"]["draft"] == "edited again"
    controller.main_ready({"generation": fresh["generation"]})
    peer.close.assert_called_once()
    window.destroy.assert_called_once()


@pytest.mark.parametrize("mutation", ["generation", "identity", "boolean", "closing", "missing_source", "window_failure"])
def test_invalid_cancel_never_discards_pending_transfer(fixture, mutation):
    controller, _, _, peer, window, _ = fixture
    returned, identity, _ = begin_return(fixture)
    request = {"generation": returned["generation"], "source_window_id": identity}
    before = controller.state()["handoff"]
    if mutation == "generation":
        request["generation"] -= 1
    elif mutation == "identity":
        request["source_window_id"] = "other-window"
    elif mutation == "boolean":
        request["generation"] = True
    elif mutation == "closing":
        controller._closing = True
    elif mutation == "missing_source":
        controller._window = None
    else:
        window.show.side_effect = RuntimeError("native window unavailable")
    with pytest.raises(AgentWindowError):
        controller.cancel_return(request)
    assert controller.state()["presentation"] == "returning"
    assert controller.state()["handoff"] == before
    peer.close.assert_not_called()
    window.destroy.assert_not_called()


def test_ack_commit_between_cancel_visibility_and_cas_cannot_be_undone(fixture):
    controller, _, _, peer, window, _ = fixture
    returned, identity, _ = begin_return(fixture)
    window.show.side_effect = lambda: controller.main_ready({"generation": returned["generation"]})
    with pytest.raises(AgentWindowError, match="handoff_stale"):
        controller.cancel_return({"generation": returned["generation"], "source_window_id": identity})
    assert controller.state()["presentation"] == "main"
    peer.close.assert_called_once()
    window.destroy.assert_called_once()


def test_cancel_after_ack_precheck_prevents_late_ack_destroying_source(fixture, monkeypatch):
    controller, _, _, peer, window, _ = fixture
    returned, identity, _ = begin_return(fixture)
    reached, release = Event(), Event()
    original = controller._commit_main

    def delayed_commit(*args, **kwargs):
        reached.set()
        assert release.wait(5), "test did not release its delayed ACK"
        return original(*args, **kwargs)

    monkeypatch.setattr(controller, "_commit_main", delayed_commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(controller.main_ready, {"generation": returned["generation"]})
        try:
            assert reached.wait(5), "ACK did not reach its pre-commit seam"
            controller.cancel_return({"generation": returned["generation"], "source_window_id": identity})
        finally:
            release.set()
        with pytest.raises(AgentWindowError, match="handoff_stale"):
            future.result(timeout=5)
    assert controller.state()["presentation"] == "detached"
    peer.close.assert_not_called()
    window.destroy.assert_not_called()
