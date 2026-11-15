"""Fresh source binding -> private admission model -> offline audit receipt."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc, validate_plan
from genshin_app.equipment.energy_delivery_receipt import (
    validate_automatic_energy_receipt,
    validate_automatic_energy_result_receipt,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from tests.test_action_resources_app import confirm, resource_plan
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811


def automatic_plan():
    plan = resource_plan()
    plan["action_resource_sources"][0]["energy_delivery"] = "action_start"
    plan["rotation_model"]["events"] = []
    return plan


def test_fresh_native_and_complete_offline_receipt(resource_db, monkeypatch):
    plan = automatic_plan()
    preview = compose_equipment_plan(plan, resource_db)
    record = preview["action_resource_compilations"][0]
    assert preview["state"] == "pending" and len(record["energy_delivery"]["links"]) == 1
    assert record["action_spend_links"] == []
    assert len(record["target_paths"]) == 5
    confirm(plan, resource_db)
    original = copy.deepcopy(plan)
    out = run_plan_calc(plan, resource_db)
    assert original == plan and plan["rotation_model"]["abilities"][0]["energy"] == {}
    assert plan["rotation_model"]["events"] == []
    assert out["result"]["rotation_check"]["characters"][0]["final_energy"] == 0
    assert out["report"]["compiled_rotation_model"]["abilities"][0]["energy"]["delivery"] == "action_start"
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: pytest.fail("offline export read DB"))
    validate_automatic_energy_receipt(plan, out["result"], out["report"], out["result_receipt"])
    validate_automatic_energy_result_receipt(out["result"], out["result_receipt"])
    assert result_bytes(out["result"], out["result_receipt"], "json")


def test_legacy_explicit_default_keeps_scope(resource_db):
    plan = confirm(resource_plan(), resource_db)
    before = compose_equipment_plan(plan, resource_db)
    plan["action_resource_sources"][0]["energy_delivery"] = "existing_events"
    after = compose_equipment_plan(plan, resource_db)
    assert before["action_resource_compilations"] == after["action_resource_compilations"]
    assert before["input_fingerprint"] != after["input_fingerprint"]


@pytest.mark.parametrize("bound", [False, True])
def test_native_cannot_forge_private_marker(bound):
    plan = automatic_plan()
    if not bound:
        plan.pop("action_resource_sources")
    plan["rotation_model"]["abilities"][0]["energy"]["delivery"] = "action_start"
    with pytest.raises(PlanError, match="私有"):
        validate_plan(plan)


def test_source_auto_rejects_existing_spend_before_query(resource_db, monkeypatch):
    plan = resource_plan()
    plan["action_resource_sources"][0]["energy_delivery"] = "action_start"
    monkeypatch.setattr("genshin_db.runtime_action_resource.get_runtime_action_resource_profile",
                        lambda *a, **k: pytest.fail("queried before duplicate guard"))
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, resource_db)


def test_rehashed_audit_alone_cannot_change_compiled_amount(resource_db):
    out = run_plan_calc(confirm(automatic_plan(), resource_db), resource_db)
    forged = copy.deepcopy(out)
    check = forged["result"]["rotation_check"]
    audit = next(r for r in check["event_log"] if r.get("delivery") == "automatic_action_start")
    audit["amount"] = 1
    forged["result_receipt"]["result_fingerprint"] = fingerprint(forged["result"])
    with pytest.raises(ValueError):
        validate_automatic_energy_result_receipt(forged["result"], forged["result_receipt"])


def test_policy_source_saved_delivery_is_complete_and_rejects_missing_binding(
        resource_db, tmp_path, monkeypatch):
    from genshin_app.equipment.delivery import (
        deliver_plan_file,
        validate_delivery_calculated,
    )
    from genshin_app.equipment.files import input_view, json_bytes

    plan = confirm(automatic_plan(), resource_db)
    original = copy.deepcopy(plan)
    source = tmp_path / "source.plan.json"
    source.write_bytes(json_bytes(plan))
    published = deliver_plan_file(source, resource_db, tmp_path / "delivery")
    assert published["data"]["publication_status"] == "complete"
    assert published["data"]["metadata"]["calculation_status"] == "complete"
    artifacts = {row["role"]: Path(row["path"]) for row in published["data"]["artifacts"]}
    assert len(artifacts) == 4 and all(path.is_file() for path in artifacts.values())
    restored = json.loads(artifacts["calculated"].read_text(encoding="utf-8"))["calculated"]
    assert plan == original
    assert input_view(restored["plan"]) == input_view(original)
    assert "direct_energy_policy" not in restored["plan"]["rotation_model"]
    assert restored["report"]["compiled_rotation_model"]["direct_energy_policy"] == (
        "energy-direct-base-er-v1")
    assert restored["result_receipt"]["energy_settlement_policy"] == (
        restored["result"]["energy_settlement_policy"])
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: pytest.fail("saved reader queried DB"))
    validate_delivery_calculated(restored)
    forged = copy.deepcopy(restored)
    forged["result_receipt"].pop("energy_settlement_policy")
    with pytest.raises(ValueError):
        validate_delivery_calculated(forged)
