"""Queue notifications do not change the existing one-shot file authority."""
from __future__ import annotations

import threading

from tests.test_native_app_action_controller import _controller


def test_resource_generation_and_unsubscribe_without_held_gate_lock(tmp_path):
    controller, _, _ = _controller(tmp_path)
    reads = []

    def changed():
        thread = threading.Thread(target=lambda: reads.append(controller.resource_snapshot()))
        thread.start()
        thread.join(1)
        assert not thread.is_alive(), "callback must run outside the serial-gate lock"

    initial = controller.resource_snapshot()
    dispose = controller.subscribe_resource(changed)
    with controller.agent_call():
        during = controller.resource_snapshot()
        assert during["busy"] and during["generation"] > initial["generation"]
    assert len(reads) == 1 and reads[0]["busy"] is False
    assert reads[0]["generation"] > during["generation"]
    dispose()
    with controller.agent_call():
        pass
    assert len(reads) == 1
