"""Resource position capacity is a controlled limit, not invalid game input."""
import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics
from tests.test_resource_threshold_lifecycle_execution import life_case


@pytest.mark.parametrize("capacity", ["bytes", "steps"])
def test_position_capacity_failure_keeps_resource_and_actual_hit_prefix(monkeypatch, capacity):
    original = StateRuntimeMechanics._apply_lifecycle_task
    def apply(bridge, task, **kwargs):
        setattr(bridge.clock._position_index, capacity, 8*1024*1024 if capacity == "bytes" else 40000)
        return original(bridge, task, **kwargs)
    monkeypatch.setattr(StateRuntimeMechanics, "_apply_lifecycle_task", apply)
    args = life_case()
    args[0]["hits"][0]["t"] = .15
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    assert out["shared_pool_trace"][-1]["after"] == 3
    assert any(r["id"] == "h0" for r in out["rotation_check"]["event_log"])
    assert not any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in out["state_runtime"]["issued_trace"])
