"""Plan-derived capacity catalog service; synthetic plans and stubs only."""
# ruff: noqa: F811 - pytest discovers fixtures imported into this module.
from __future__ import annotations

import copy

import pytest
from capacity_fixture import context
from test_capacity_catalog import clone_model
from test_capacity_inputs import input_plan

from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.gui.api import GuiApi
from genshin_db.runtime_catalog import RuntimeConfigError
from tests.action_resource_fixtures import (  # noqa: F401
    action_resource_synthetic_db_only,
)
from tests.test_action_resource_sources import source_db  # noqa: F401
from tests.test_capacity_model import ready  # noqa: F401
from tests.test_pipeline import fake_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def catalog_plan(*, typed=True):
    plan = input_plan()
    plan["action_capacity_sources"] = []
    if typed:
        plan["action_resource_sources"] = [{
            "ability_id": "e",
            "version_line": "release",
            "selection": {
                "character_id": 90000001,
                "constellation": 1,
                "skill_id": 90012,
                "skill_depot_id": 9001,
            },
            "purpose": "ordinary_fixed_burst_base_resources",
            "variant": "tap",
            "status": "confirmed",
            "source": "synthetic typed identity",
            "expected_snapshot_id": None,
            "expected_resource_fingerprint": None,
            "expected_scope_fingerprint": None,
        }]
    validate_plan(plan)
    return plan


def extracted(plan=None, *, actor="a", members=("e",)):
    from genshin_app.equipment.action_capacity_catalog import catalog_filter_from_plan

    return catalog_filter_from_plan(plan or catalog_plan(), actor, list(members))


def test_extracts_exact_single_actor_typed_inputs_and_c0():
    plan = catalog_plan()
    plan["equipment_inputs"]["a"]["progression"]["constellation"] = 0
    plan["action_resource_sources"][0]["selection"]["constellation"] = 0
    validate_plan(plan)
    result = extracted(plan)
    assert result == {
        "version_line": "release",
        "character_id": 90000001,
        "observed": {
            "target": {"skill_depot_id": 9001, "skill_id": 90012, "variant": "tap"},
            "progression": {"character_level": 90, "ascension": 6, "constellation": 0, "base_level": 10},
            "weapon": {"id": 11401, "level": 1, "refinement": 1},
            "artifacts": "all_empty",
            "team": "self_only",
            "context": context(),
        },
        "unknown_paths": [],
    }


def test_missing_refinement_and_unknown_artifact_slot_are_not_defaulted():
    plan = catalog_plan()
    plan["equipment_inputs"]["a"]["weapon"].pop("refinement")
    plan["equipment_inputs"]["a"]["artifact_presence"]["EQUIP_DRESS"] = "unknown"
    validate_plan(plan)
    result = extracted(plan)
    assert "refinement" not in result["observed"]["weapon"]
    assert "artifacts" not in result["observed"]
    assert "weapon.refinement" in result["unknown_paths"]
    assert "artifacts" in result["unknown_paths"]


@pytest.mark.parametrize("with_set", [False, True])
def test_any_equipped_slot_or_set_is_explicit_not_all_empty(with_set):
    plan = catalog_plan()
    plan["equipment_inputs"]["a"]["artifact_presence"]["EQUIP_BRACER"] = "equipped"
    if with_set:
        plan["equipment_inputs"]["a"]["artifact_presence"]["EQUIP_NECKLACE"] = "equipped"
        plan["equipment_inputs"]["a"]["artifact_sets"] = [{"set_id": 14001, "count": 2}]
    validate_plan(plan)
    assert extracted(plan)["observed"]["artifacts"] == "not_all_empty"


def test_multi_character_team_is_explicit_not_self_only():
    plan = catalog_plan()
    teammate = copy.deepcopy(plan["manual_template"]["characters"][0])
    teammate["id"] = "b"
    plan["manual_template"]["characters"].append(teammate)
    rotation_teammate = copy.deepcopy(plan["rotation_model"]["characters"][0])
    rotation_teammate["id"] = "b"
    plan["rotation_model"]["characters"].append(rotation_teammate)
    validate_plan(plan)
    assert extracted(plan)["observed"]["team"] == "not_self_only"


def test_absent_context_is_unknown_instead_of_ordinary_default():
    plan = catalog_plan()
    plan.pop("action_capacity_context")
    validate_plan(plan)
    result = extracted(plan)
    assert "context" not in result["observed"]
    assert "context" in result["unknown_paths"]


def test_chinese_skill_label_never_infers_skill_identity():
    plan = catalog_plan(typed=False)
    plan["manual_template"]["hits"][0]["skill"] = "中文元素战技名称"
    validate_plan(plan)
    result = extracted(plan)
    assert result["observed"]["target"] == {"variant": "tap"}
    assert {"target.skill_depot_id", "target.skill_id"} <= set(result["unknown_paths"])


def test_existing_typed_capacity_target_can_filter_without_writing_selection():
    plan = input_plan()
    before = copy.deepcopy(plan)
    result = extracted(plan)
    assert result["observed"]["target"] == {
        "skill_depot_id": 9001, "skill_id": 90012, "variant": "tap"}
    assert plan == before


@pytest.mark.parametrize("mutation,match", [
    ("wrong_actor", "属于所选角色"),
    ("wrong_type", "元素战技"),
    ("duplicate_member", "不能重复"),
    ("missing_member", "不存在"),
])
def test_ability_membership_actor_and_type_fail_closed(mutation, match):
    plan = catalog_plan()
    members = ["e"]
    if mutation == "wrong_actor":
        plan["rotation_model"]["abilities"][0]["character"] = "someone-else"
    elif mutation == "wrong_type":
        plan["rotation_model"]["abilities"][0]["action_type_id"] = "action.elemental_burst"
    elif mutation == "duplicate_member":
        members.append("e")
    else:
        members = ["missing"]
    with pytest.raises(PlanError, match=match):
        extracted(plan, members=members)


def test_missing_variant_is_unknown_and_conflicting_variants_are_rejected():
    plan = catalog_plan(typed=False)
    plan["rotation_model"]["abilities"][0].pop("variant")
    validate_plan(plan)
    result = extracted(plan)
    assert "variant" not in result["observed"].get("target", {})
    assert "target.variant" in result["unknown_paths"]

    other = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    other.update(id="e2", variant="hold")
    plan["rotation_model"]["abilities"][0]["variant"] = "tap"
    plan["rotation_model"]["abilities"].append(other)
    with pytest.raises(PlanError, match="变体"):
        extracted(plan, members=("e", "e2"))


def test_service_builds_query_and_passes_explicit_pagination(monkeypatch):
    from genshin_app.equipment import action_capacity_catalog as service

    captured = {}

    def fake(db_path, request):
        captured.update(db_path=db_path, request=copy.deepcopy(request))
        return {"contract": "action-capacity-model-catalog-page-v1", "items": [], "next_cursor": None}

    monkeypatch.setattr(service, "list_action_capacity_model_candidates", fake)
    db_path = "synthetic-never-opened.db"
    page = service.action_capacity_model_candidates(
        catalog_plan(), "a", ["e"], db_path,
        cursor="opaque", page_size=7, view="matching",
    )
    assert page["contract"] == "action-capacity-model-catalog-page-v1"
    assert captured["db_path"] == db_path
    assert captured["request"]["view"] == "matching"
    assert captured["request"]["page_size"] == 7
    assert captured["request"]["cursor"] == "opaque"
    assert captured["request"]["contract"] == "action-capacity-model-catalog-query-v1"
    assert "action_capacity_sources" not in captured["request"]


def test_service_pages_and_rejects_changed_synthetic_inventory(ready):
    from genshin_app.equipment.action_capacity_catalog import (
        action_capacity_model_candidates,
    )
    from genshin_db.runtime_capacity_delta import CapacityError

    db, model, _ = ready
    clone_model(db, model, "fixture.capacity.service.second")
    first = action_capacity_model_candidates(
        catalog_plan(), "a", ["e"], db, cursor=None, page_size=1, view="all")
    assert first["has_more"] is True and first["next_cursor"]
    second = action_capacity_model_candidates(
        catalog_plan(), "a", ["e"], db, cursor=first["next_cursor"], page_size=1, view="all")
    assert second["items"] and second["items"] != first["items"]

    clone_model(db, model, "fixture.capacity.service.changed")
    with pytest.raises(CapacityError) as exc:
        action_capacity_model_candidates(
            catalog_plan(), "a", ["e"], db, cursor=first["next_cursor"], page_size=1, view="all")
    assert exc.value.reason == "capacity_catalog_changed" and exc.value.status == 409


def test_gui_api_preserves_structured_catalog_snapshot_error(monkeypatch):
    from genshin_app import equipment

    def fail(*args, **kwargs):
        raise RuntimeConfigError("snapshot changed", "snapshot_changed", 409,
                                 {"snapshot_id": "new-snapshot"})

    monkeypatch.setattr(equipment, "action_capacity_model_candidates", fail)
    result = GuiApi("synthetic-never-opened").action_capacity_model_candidates(
        catalog_plan(), "a", ["e"], cursor=None, page_size=16, view="all")
    assert result == {
        "code": 1,
        "message": "snapshot changed",
        "reason": "snapshot_changed",
        "details": {"snapshot_id": "new-snapshot"},
    }


def test_public_exports_and_capabilities_never_grant_execution():
    from genshin_app import equipment

    assert equipment.action_capacity_model_candidates
    contract = equipment.plan_capabilities()["action_capacity_catalog_contract"]
    assert contract["query_contract"] == "action-capacity-model-catalog-query-v1"
    assert contract["page_contract"] == "action-capacity-model-catalog-page-v1"
    assert contract["execution_authority"] is False
    assert contract["writes_plan"] is False
    assert contract["selection"] == "reference_only_requires_fresh_compose_confirmation"
