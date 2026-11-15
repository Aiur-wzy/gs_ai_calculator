"""Complete delivery keeps real isolated resource evidence and admission receipts."""
import json
from pathlib import Path

import pytest

from genshin_app.equipment import service
from genshin_app.equipment.delivery import (
    deliver_plan_file,
    validate_delivery_calculated,
)
from genshin_app.equipment.files import fingerprint, json_bytes
from tests.test_action_resources_app import confirm
from tests.test_automatic_energy_app import automatic_plan
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811


def test_source_bound_delivery_reads_and_executes_once_then_goes_offline(resource_db, monkeypatch):
    plan = confirm(automatic_plan(), resource_db)
    path = resource_db.parent / "native-input.plan.json"
    path.write_bytes(json_bytes(plan))
    calls = {"calc": 0, "compose": 0, "execute": 0}
    original_calc, original_compose = service.run_plan_calc, service.compose_equipment_plan
    from genshin_engine import mechanics
    original_execute = mechanics.execute_mechanics

    def compose(*a, **k):
        calls["compose"] += 1
        return original_compose(*a, **k)

    def execute(*a, **k):
        calls["execute"] += 1
        return original_execute(*a, **k)

    def calculate(*a, **k):
        calls["calc"] += 1
        calculated = original_calc(*a, **k)
        monkeypatch.setattr("sqlite3.connect", lambda *a, **k: pytest.fail("late delivery DB access"))
        return calculated

    monkeypatch.setattr(service, "compose_equipment_plan", compose)
    monkeypatch.setattr(mechanics, "execute_mechanics", execute)
    monkeypatch.setattr(service, "run_plan_calc", calculate)
    delivered = deliver_plan_file(path, resource_db, resource_db.parent / "delivery")
    assert calls == {"calc": 1, "compose": 1, "execute": 1}
    generation = Path(delivered["data"]["generation_path"])
    package = json.loads((generation / "calculated.json").read_text("utf8"))
    native = json.loads((generation / "native.plan.json").read_text("utf8"))
    assert native["rotation_model"]["events"] == []
    assert native["rotation_model"]["abilities"][0]["energy"] == {}
    assert native["action_resource_sources"][0]["energy_delivery"] == "action_start"
    calculated = package["calculated"]
    validate_delivery_calculated(calculated)
    assert calculated["result"]["rotation_check"]["characters"][0]["final_energy"] == 0
    assert calculated["result_receipt"]["action_resources"]["automatic_energy_delivery"]
    assert calculated["report"]["action_resource_compilations"][0]["resource_fingerprint"]


@pytest.mark.parametrize("remove", ["audit", "proof", "record"])
def test_complete_builder_does_not_replace_specialized_energy_validation(resource_db, remove):
    out = service.run_plan_calc(confirm(automatic_plan(), resource_db), resource_db, capture_display=True)
    if remove == "audit":
        events = out["result"]["rotation_check"]["event_log"]
        events[:] = [row for row in events if row.get("delivery") != "automatic_action_start"]
        out["result_receipt"]["result_fingerprint"] = fingerprint(out["result"])
    elif remove == "proof":
        out["result_receipt"]["action_resources"].pop("automatic_energy_delivery")
    else:
        out["result"].pop("action_resource_compilations")
        out["result_receipt"]["result_fingerprint"] = fingerprint(out["result"])
    with pytest.raises(ValueError):
        validate_delivery_calculated(out)
