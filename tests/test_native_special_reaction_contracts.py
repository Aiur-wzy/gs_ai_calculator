"""Existing special-reaction contracts are discoverable, detached and non-executing."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.native_special_reaction_contracts import (
    special_reaction_capabilities,
)
from genshin_app.agent_chat.tools import definitions_for_session
from genshin_app.equipment import new_plan
from genshin_app.equipment.files import json_bytes
from genshin_app.whiteboard import new_whiteboard
from genshin_engine.special_reaction_schema import (
    QUALIFICATION_SCHEMA,
    special_reaction_input_capabilities,
)
from genshin_engine.special_reactions import (
    SpecialReactionPending,
    SpecialReactionRuntime,
    validate_special_reaction_model,
)
from genshin_engine.special_reactions import (
    special_reaction_capabilities as engine_capabilities,
)


def _object(value):
    assert value["kind"] == "object" and value["next_offset"] is None
    return {row["key"]: row["value"] for row in value["items"]}


def _read(session, resource, pointer=""):
    reply = session.call_tool("gs_read_context", {"resource": resource, "pointer": pointer, "limit": 200})
    assert reply["code"] == 0, reply
    return reply["data"]


def _state(session):
    return copy.deepcopy({name: getattr(session, name) for name in (
        "candidate", "document", "revision", "stage_state", "resources", "database_snapshot",
        "calculated", "report", "changes", "provisional_authority")})


def _proposal(catalog, family):
    contract = catalog["types"][family]
    elements = list(contract["elements"])
    trigger = next((element for element in ("anemo", "geo") if element in elements), elements[0])
    aura = next(element for element in elements if element != trigger)
    return {"id": "example.reaction", "type": contract["ordinary_type"], "commit_state": "proposal",
            "aura_element": aura, "trigger_element": trigger, "target_id": "example.target",
            "parent_event_id": "example.hit", "owner": "example.provider"}


@pytest.mark.parametrize("kind", ["whiteboard", "native"])
def test_real_artifact_reads_every_special_navigation_and_keeps_all_authority_unchanged(tmp_path, kind):
    session = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", kind,
                                       new_whiteboard() if kind == "whiteboard" else new_plan())
    before = _state(session)
    definitions = definitions_for_session(session)
    checkpoint = (session.root / "checkpoint.json").read_bytes()
    index = _object(_read(session, "capability_index"))
    assert "special_reaction_capabilities" in index["resources"]
    for reference in index["special_reactions"].values():
        assert set(reference) == {"resource", "pointer"}
        _read(session, **reference)
    assert len(json_bytes(index["special_reactions"])) < 16000
    _read(session, "special_reaction_capabilities", "/engine_catalog/profiles/stellar_superconduct/parameters")
    _read(session, "special_reaction_capabilities", "/examples/by_family/stellar_superconduct/catalog_model")
    _read(session, "special_reaction_capabilities", "/input_contract/schemas/direct_stellar_damage_profile/properties/ability_binding")
    hash_guidance = _object(_read(session, "special_reaction_capabilities", "/filling"))["hash"]
    _read(session, **hash_guidance["contract_ref"])
    for hash_kind in hash_guidance["kinds"]:
        hashed = session.call_tool("gs_hash_input", {"kind": hash_kind, "value": {"format_only": True}})
        assert hashed["code"] == 0
        assert hashed["data"]["authority"] == "none" and hashed["data"]["game_facts_verified"] is False
    assert _state(session) == before and definitions_for_session(session) == definitions
    assert (session.root / "checkpoint.json").read_bytes() == checkpoint
    assert not session.db_path.exists()


def test_contracts_are_the_public_engine_producers_and_return_detached_views():
    resource = special_reaction_capabilities()
    assert resource["engine_catalog"] == engine_capabilities()
    assert resource["input_contract"] == special_reaction_input_capabilities()
    assert resource["qualification_schema"] == QUALIFICATION_SCHEMA
    resource["engine_catalog"]["profiles"]["stellar_superconduct"]["parameters"]["field_duration_seconds"] = -1
    resource["qualification_schema"]["required"].clear()
    resource["input_contract"]["schemas"].clear()
    fresh = special_reaction_capabilities()
    assert fresh["engine_catalog"] == engine_capabilities()
    assert fresh["input_contract"] == special_reaction_input_capabilities()
    assert fresh["qualification_schema"] == QUALIFICATION_SCHEMA


@pytest.mark.parametrize("family", list(engine_capabilities()["profiles"]))
def test_current_five_profile_examples_match_identity_parameters_and_actual_validators(family):
    resource = special_reaction_capabilities()
    catalog = resource["engine_catalog"]
    assert len(catalog["profiles"]) == 5
    profile = catalog["profiles"][family]
    model = resource["examples"]["by_family"][family]["catalog_model"]
    assert not validate_special_reaction_model(model)
    replacement = model["replacements"][family]
    assert set(replacement["qualification"]) == set(QUALIFICATION_SCHEMA["required"])
    mechanics = replacement["mechanics"]
    assert {name: mechanics[name] for name in profile["parameters"]} == profile["parameters"]
    assert all(mechanics[name] == profile[name] for name in ("profile_id", "profile_revision", "profile_content_hash"))
    assert mechanics["status"] == ("confirmed" if profile["confirmed_ready"] else "pending")
    assert replacement["qualification"]["status"] == "pending"
    with pytest.raises(SpecialReactionPending) as caught:
        SpecialReactionRuntime(model).replace(_proposal(catalog, family), time=0)
    assert caught.value.issue["code"] == "special_qualification_pending"
    if not profile["confirmed_ready"]:
        assert "assumed_syntax" not in resource["examples"]["by_family"][family]
        replacement["qualification"]["status"] = "confirmed"
        runtime = SpecialReactionRuntime(model, party_member_ids=["example.provider"],
            party_capability_tags={"example.provider": [replacement["qualification"]["capability_tag"]]})
        with pytest.raises(SpecialReactionPending) as caught:
            runtime.replace(_proposal(catalog, family), time=0)
        assert caught.value.issue["code"] == "special_mechanics_pending"


def test_disabled_sparse_drafts_remain_valid_without_qualification_or_mechanics():
    resource = special_reaction_capabilities()
    disabled = resource["examples"]["disabled_sparse"]
    family = next(iter(disabled["replacements"]))
    disabled["replacements"][family]["qualification"] = {"status": "pending"}
    disabled["replacements"][family]["mechanics"] = {"source": "unfinished disabled note"}
    assert not validate_special_reaction_model(disabled)
    decision = SpecialReactionRuntime(disabled).replace(_proposal(resource["engine_catalog"], family), time=0)
    assert decision["decision"]["decision"] == "ordinary"


def test_confirmed_and_assumed_semantics_stay_owned_by_original_validator():
    resource = special_reaction_capabilities()
    by_family = resource["examples"]["by_family"]
    confirmed = copy.deepcopy(by_family["stellar_superconduct"]["catalog_model"])
    confirmed["replacements"]["stellar_superconduct"]["mechanics"]["field_duration_seconds"] += 1
    assert "profile_override_status" in {row["code"] for row in validate_special_reaction_model(confirmed)}
    pending = copy.deepcopy(by_family["lunar_crystallize"]["catalog_model"])
    pending["replacements"]["lunar_crystallize"]["mechanics"]["status"] = "confirmed"
    assert "profile_not_confirmed_ready" in {row["code"] for row in validate_special_reaction_model(pending)}
    for examples in by_family.values():
        if "assumed_syntax" not in examples:
            continue
        model = copy.deepcopy(examples["assumed_syntax"])
        assert not validate_special_reaction_model(model)
        row = next(iter(model["replacements"].values()))
        row["mechanics"].pop("override_reason")
        assert "profile_override_reason" in {issue["code"] for issue in validate_special_reaction_model(model)}


def test_hash_guidance_uses_dedicated_input_tool_without_claiming_fact_verification():
    guidance = special_reaction_capabilities()["filling"]["hash"]
    assert guidance["contract_ref"] == {"resource": "tool_contracts", "pointer": "/tools/gs_hash_input"}
    assert guidance["kinds"] == ["direct_stellar_damage_profile", "direct_lunar_damage_profile"]
    assert guidance["authority"] == "none"
