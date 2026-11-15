"""Public R3 admission remains opt-in; old or unknown contracts cannot inherit it."""
import copy

import pytest

from genshin_engine.mechanics import validate_mechanics_model
from genshin_engine.mechanics_schema import mechanics_capabilities
from tests.test_resource_threshold_lifecycle_execution import life_case


@pytest.mark.parametrize("version", [3, 4])
def test_public_native_schema_accepts_explicit_reviewed_lifecycle_contract(version):
    model = life_case()[2]
    model["resource_threshold_triggers"]["schema_version"] = version
    assert validate_mechanics_model(model)["status"] == "valid"


@pytest.mark.parametrize("field,version", [("resource_threshold_triggers", 1), ("resource_threshold_triggers", 2),
    ("resource_threshold_triggers", 5), ("state_runtime", 1), ("state_runtime", 3)])
def test_old_or_unknown_version_cannot_inherit_new_lifecycle_payload_permission(field, version):
    model = copy.deepcopy(life_case()[2])
    model[field]["schema_version"] = version
    assert validate_mechanics_model(model)["status"] == "invalid"


def test_native_input_cannot_supply_an_internal_position_as_authority():
    model = copy.deepcopy(life_case()[2])
    model["state_runtime"]["lifecycles"][1]["position"] = {"stage": "batch_tail"}
    assert validate_mechanics_model(model)["status"] == "invalid"


def test_public_capabilities_describe_versioned_scope_and_chinese_targets():
    caps = mechanics_capabilities()
    assert caps["resource_threshold_triggers"]["supported_versions"] == [1, 2, 3, 4]
    assert caps["state_runtime"]["supported_versions"] == [1, 2]
    schemas = caps["resource_threshold_triggers"]["schema"]["oneOf"]
    assert schemas[0]["properties"]["bindings"]["items"]["properties"]["payload"]["properties"]["kind"]["const"] == "activate_rule"
    payload = schemas[2]["properties"]["bindings"]["items"]["properties"]["payload"]["properties"]
    assert payload["kind"]["x-enum-labels"]["start_lifecycle"] == "建立有限生命周期"
    assert payload["ref_id"]["x-label"] == "目标规则或生命周期"
    assert "连续游戏时钟或自动来源批准" in caps["resource_threshold_triggers"]["unsupported"]
