"""Family-planner isolation, exact canonical outputs and coordinator rollback."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from genshin_app.equipment.files import fingerprint
from genshin_engine import special_reactions as producer
from genshin_engine.special_reaction_reference import (
    copy_runtime,
    replace_special_reaction,
    replace_stellar_swirl_packet,
)
from genshin_engine.special_reactions import SpecialReactionPending
from genshin_engine.special_replacement_handlers import (
    legacy_replacement_family_binding as replacement_family_binding,
)
from genshin_engine.special_state_observation import canonical_stellar_deadline_row
from tests.test_special_reactions import reaction, runtime


@pytest.mark.parametrize(("family", "expected"), [
    ("stellar_superconduct", "a5a2393b5814caaa1793efdbf3e4f7a56f7d66e2b709a7acfce1e195f20689e8"),
    ("stellar_swirl", "3ac4c75167715812d3bdd6f6577efc0287c7eb6a36d135ae5c52e66b049b9ab5"),
    ("lunar_electro", "9635c78a7f66e8d79a86836c9275bc545b711d75384ff04075fec5fa1bbf9945"),
    ("lunar_bloom", "f69f916d61d894ef4f1f7e66d33d90444be68988c072ca7ea88ac7368267e280"),
    ("lunar_crystallize", "d530578f7b37fe6e58c02f5e2b2390df65b8fd89cc13833f74f64b97b020f17c"),
])
def test_complete_replacement_sequence_matches_pre_handler_canonical_output(family, expected):
    value = runtime(family)
    receipts = [value.replace(reaction(family, i), time=(i-1)*100_000) for i in range(1, 7)]
    due = [canonical_stellar_deadline_row(row) for row in value.advance(5_000_000)]
    assert fingerprint({"receipts": receipts, "due": due, "state": value.state(),
                        "trace": [canonical_stellar_deadline_row(row) for row in value.trace]}) == expected


def test_stellar_planner_cannot_write_live_field_and_refresh_keeps_field_identity(monkeypatch):
    value = runtime("stellar_superconduct")
    binding = replacement_family_binding("stellar_superconduct")
    seen = []

    def inspect(proposal):
        before = copy.deepcopy(dict(proposal.field))
        with pytest.raises(TypeError):
            proposal.field["stellar_superconduct_active"] = True
        plan = binding.handler(proposal)
        assert dict(proposal.field) == before
        seen.append(plan)
        return plan

    monkeypatch.setattr(producer, "legacy_replacement_family_binding", lambda _family: replace(binding, handler=inspect))
    value.replace(reaction("stellar_superconduct"), time=0)
    field = value.stellar_field_by_area["battlefield.primary"]
    origin = field["stellar_superconduct_origin_applications"]
    value.replace(reaction("stellar_superconduct", 2), time=100_000)
    assert value.stellar_field_by_area["battlefield.primary"] is field
    assert field["stellar_superconduct_origin_applications"] is origin
    assert [plan.was_active for plan in seen] == [False, True]


def test_canonical_replace_is_independent_of_the_actual_family_handler(monkeypatch):
    value = runtime("stellar_superconduct")
    canonical = copy_runtime(value)
    binding = replacement_family_binding("stellar_superconduct")

    def corrupted(proposal):
        plan = binding.handler(proposal)
        return replace(plan, generated_event={**plan.generated_event, "current_layers": 999})

    monkeypatch.setattr(producer, "legacy_replacement_family_binding", lambda _family: replace(binding, handler=corrupted))
    expected = replace_special_reaction(canonical, reaction("stellar_superconduct"), time=0)
    actual = value.replace(reaction("stellar_superconduct"), time=0)
    assert actual != expected
    assert actual["generated_events"][0]["current_layers"] == 999
    assert expected["generated_events"][0]["current_layers"] == 0
    reference = canonical.replace.__func__
    assert reference.__globals__ is not producer.SpecialReactionRuntime.replace.__globals__
    assert reference.__globals__["REPLACEMENT_CONTRACTS"] is not producer.REPLACEMENT_CONTRACTS
    assert reference.__globals__["_seconds_to_ticks"] is not producer._seconds_to_ticks
    assert "replacement_family_binding" not in reference.__code__.co_names


def test_mutation_between_planning_and_commit_rolls_back_the_entire_replacement(monkeypatch):
    value = runtime("stellar_superconduct")
    value.replace(reaction("stellar_superconduct"), time=0)
    before, trace = copy.deepcopy(value.state()), copy.deepcopy(value.trace)
    binding = replacement_family_binding("stellar_superconduct")

    def mutate_live(proposal):
        plan = binding.handler(proposal)
        value.stellar_field_by_area["battlefield.primary"]["stellar_superconduct_layers"] += 1
        return plan

    monkeypatch.setattr(producer, "legacy_replacement_family_binding", lambda _family: replace(binding, handler=mutate_live))
    with pytest.raises(ValueError, match="计划与提交"):
        value.replace(reaction("stellar_superconduct", 2), time=100_000)
    assert value.state() == before and value.trace == trace


@pytest.mark.parametrize("family", producer.SPECIAL_REACTION_TYPES)
def test_handler_pending_restores_pre_advance_state_and_only_publishes_pending_decision(monkeypatch, family):
    value = runtime(family)
    value.replace(reaction(family), time=0)
    before, trace = copy.deepcopy(value.state()), copy.deepcopy(value.trace)
    binding = replacement_family_binding(family)

    def pending(_proposal):
        raise SpecialReactionPending("handler_pending_fixture", "fixture", "ordinary.2")

    monkeypatch.setattr(producer, "legacy_replacement_family_binding", lambda _family: replace(binding, handler=pending))
    with pytest.raises(SpecialReactionPending) as caught:
        value.replace(reaction(family, 2), time=4_000_000)
    assert caught.value.issue["code"] == "handler_pending_fixture"
    assert value.state() == before
    assert value.trace[:-1] == trace
    assert value.trace[-1]["decision"] == "pending"
    assert value.trace[-1]["reason"] == "handler_pending_fixture"


@pytest.mark.parametrize("family", producer.SPECIAL_REACTION_TYPES)
def test_stale_family_transition_cannot_publish_any_part_of_a_replacement(monkeypatch, family):
    value = runtime(family)
    value.replace(reaction(family), time=0)
    before, trace = copy.deepcopy(value.state()), copy.deepcopy(value.trace)
    binding = replacement_family_binding(family)

    def stale(proposal):
        plan = binding.handler(proposal)
        field = ("expected_party_fingerprint" if family == "lunar_crystallize"
                 else "expected_prestate_fingerprint")
        return replace(plan, **{field: "stale"})

    monkeypatch.setattr(producer, "legacy_replacement_family_binding", lambda _family: replace(binding, handler=stale))
    with pytest.raises(ValueError, match="计划与提交"):
        value.replace(reaction(family, 2), time=100_000)
    assert value.state() == before and value.trace == trace


@pytest.mark.parametrize(("family", "state_name"), [
    ("stellar_superconduct", "field"), ("stellar_swirl", "vortex"),
    ("lunar_electro", "cloud"), ("lunar_bloom", "party_state"),
    ("lunar_crystallize", "party_state"),
])
def test_family_planning_cannot_write_the_supplied_state_view(monkeypatch, family, state_name):
    value = runtime(family)
    binding = replacement_family_binding(family)

    def inspect(proposal):
        supplied = getattr(proposal, state_name)
        before = copy.deepcopy(dict(supplied))
        with pytest.raises(TypeError):
            supplied["unregistered_state"] = 1
        plan = binding.handler(proposal)
        assert dict(supplied) == before
        return plan

    monkeypatch.setattr(producer, "legacy_replacement_family_binding", lambda _family: replace(binding, handler=inspect))
    value.replace(reaction(family), time=0)


def test_proposal_validation_still_precedes_family_planning(monkeypatch):
    value = runtime("stellar_superconduct")

    def unexpected(_family):
        pytest.fail("an already committed ordinary reaction must fail before handler dispatch")

    monkeypatch.setattr(producer, "legacy_replacement_family_binding", unexpected)
    invalid = reaction("stellar_superconduct")
    invalid["commit_state"] = "committed"
    with pytest.raises(ValueError, match="proposal"):
        value.replace(invalid, time=0)
    assert value.trace == []


@pytest.mark.parametrize("initial_count", [0, 1, 3, 5])
def test_packet_handler_matches_frozen_v1_for_creation_upgrade_and_early_detonation(initial_count):
    value = runtime("stellar_swirl")
    for index in range(1, initial_count + 1):
        value.replace(reaction("stellar_swirl", index), time=index)
    canonical = copy_runtime(value)
    children = [reaction("stellar_swirl", index, parent="packet", target=f"enemy.{index}")
                for index in range(20, 23)]
    actual = value.replace_stellar_swirl_packet(children, time=100_000)
    expected = replace_stellar_swirl_packet(canonical, children, time=100_000)
    assert actual == expected
    assert value.trace == canonical.trace
    assert value.state() == canonical.state()


def test_packet_canonical_does_not_share_the_actual_handler_and_failure_rolls_back(monkeypatch):
    value = runtime("stellar_swirl")
    canonical = copy_runtime(value)
    binding = producer.stellar_swirl_packet_binding()
    children = [reaction("stellar_swirl", index, parent="packet") for index in range(1, 3)]
    before = value.state()

    def stale(proposal):
        with pytest.raises(TypeError):
            proposal.vortex["stellar_swirl_count"] = 999
        return replace(binding.handler(proposal), expected_prestate_fingerprint="stale")

    monkeypatch.setattr(producer, "stellar_swirl_packet_binding", lambda: replace(binding, handler=stale))
    expected = replace_stellar_swirl_packet(canonical, children, time=100_000)
    assert expected["reactions"][0]["route"] == "special"
    with pytest.raises(ValueError, match="计划与提交"):
        value.replace_stellar_swirl_packet(children, time=100_000)
    assert value.state() == before and value.trace == []
    reference = canonical.replace_stellar_swirl_packet.__func__
    assert "stellar_swirl_packet_binding" not in reference.__code__.co_names
