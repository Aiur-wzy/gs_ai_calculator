"""Real Artifact reads expose existing input contracts without granting authority."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.tools import definitions_for_session
from genshin_app.equipment import new_plan
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PLAN_SCHEMA
from genshin_app.whiteboard import new_whiteboard


@pytest.fixture(params=["whiteboard", "native"])
def session(request, tmp_path):
    document = new_whiteboard() if request.param == "whiteboard" else new_plan()
    return ArtifactCandidateSession(
        uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", request.param, document)


def _read(session, resource, pointer=""):
    result = session.call_tool("gs_read_context", {
        "resource": resource, "pointer": pointer, "limit": 200})
    assert result["code"] == 0, result
    return result["data"]


def _object(value):
    assert value["kind"] == "object" and value["next_offset"] is None
    return {row["key"]: row["value"] for row in value["items"]}


def _state(session):
    return copy.deepcopy({name: getattr(session, name) for name in (
        "candidate", "document", "revision", "stage_state", "resources", "database_snapshot",
        "calculated", "report", "changes", "provisional_authority")})


def test_real_artifact_reads_every_unconditional_schema_navigation_without_mutation(session):
    before = _state(session)
    tool_definitions = definitions_for_session(session)
    index = _object(_read(session, "capability_index"))
    assert {"capabilities", "plan_schema", "whiteboard_schema"} <= set(index["resources"])
    assert index["resources_exhaustive"] is False
    assert index["resources_are_permission_allowlist"] is False
    navigation = index["plan_contract"]
    assert navigation["write_authority"] == "none"
    assert set(navigation["sections"]) == set(PLAN_SCHEMA["properties"])
    assert len(json_bytes(navigation)) < 16000  # Remains inline in the normal compact index page.
    for reference in [navigation["schema"], navigation["whiteboard_schema"],
                      *navigation["sections"].values()]:
        assert set(reference) == {"resource", "pointer"}
        _read(session, **reference)
    assert "composition_drafts" not in navigation  # No preview invented before compose.
    assert _state(session) == before
    assert definitions_for_session(session) == tool_definitions
    assert not session.db_path.exists()


def test_effect_and_dependency_fields_can_be_drilled_from_discovered_plan_sections(session):
    before = _state(session)
    navigation = _object(_read(session, "capability_index"))["plan_contract"]
    binding = navigation["sections"]["effect_bindings"]
    binding_schema = _object(_read(session, binding["resource"], binding["pointer"] + "/items"))
    assert {"binding_id", "provider_character_id", "source_refs", "coverage", "parts"} <= set(
        binding_schema["required"])
    part = _object(_read(session, binding["resource"], binding["pointer"] + "/items/properties/parts/items"))
    assert {"part_id", "state", "rule", "delivery", "conditions", "dependencies", "reason"} <= set(part["required"])
    dependency = navigation["sections"]["manual_effect_dependencies"]
    dependency_schema = _object(_read(session, dependency["resource"], dependency["pointer"] + "/items"))
    assert dependency_schema["required"] == ["effect_ref", "mode", "context_fingerprint", "note"]
    assert _state(session) == before
    assert not session.db_path.exists()


def test_expanded_capabilities_navigation_declares_actual_representation_condition(session):
    before = _state(session)
    details = _object(_read(session, "capability_index"))["plan_contract"]["native_details"]
    condition = details["available_when"]
    actual_kind = _read(session, condition["resource"], condition["pointer"])["text"]
    assert condition["equals"] == "genshin-plan"
    if actual_kind == condition["equals"]:
        for reference in details["references"].values():
            _read(session, **reference)
        assert details["references"]["expanded_schema"] == {"resource": "capabilities", "pointer": "/schema"}
    else:
        assert actual_kind == "genshin-whiteboard"
        assert session.call_tool("gs_read_context", details["references"]["expanded_schema"])["code"] == 1
    # The CLI-only /plan_contract wrapper must never enter native navigation.
    assert "/plan_contract/" not in str(details)
    assert _state(session) == before
    assert not session.db_path.exists()


def test_plan_navigation_tracks_actual_schema_properties_and_escapes_json_pointer(session, monkeypatch):
    before = _state(session)
    monkeypatch.setitem(PLAN_SCHEMA["properties"], "future~/section", {"type": "null"})
    navigation = _object(_read(session, "capability_index"))["plan_contract"]
    reference = navigation["sections"]["future~/section"]
    assert reference == {"resource": "plan_schema", "pointer": "/properties/future~0~1section"}
    assert _object(_read(session, **reference)) == {"type": "null"}
    assert _state(session) == before


def test_compose_preview_navigation_uses_current_real_report_without_approval(tmp_path):
    plan = new_plan()
    plan["manual_template"].update(loop_duration=2, characters=[
        {"id": "a", "name": "工程角色", "level": 90, "stats": {"atk": 100, "crit_rate": 0, "crit_dmg": .5}}],
        hits=[{"id": "h", "t": 1, "character": "a", "skill": "工程命中", "multiplier": 1,
               "damage_type_ids": ["damage.elemental_skill"], "damage_system": "classic", "element": "cryo"}])
    session = ArtifactCandidateSession(
        uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", "native", plan)
    composed = session.call_tool("gs_compose", {"expected_revision": 0})
    assert composed["code"] == 0, composed
    before = _state(session)
    previews = _object(_read(session, "capability_index"))["plan_contract"]["composition_drafts"]
    assert previews["execution_authority"] == "none"
    assert set(previews["references"]) == {"effect_requirements", "manual_dependency_previews"}
    for reference in previews["references"].values():
        _read(session, **reference)
    assert _state(session) == before
    assert not session.db_path.exists()
