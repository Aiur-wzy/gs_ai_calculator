"""Adversarial contracts for the generic world pre-routing seam."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.elemental import ElementalPending, ElementalRuntime
from genshin_engine.elemental_schema import MULTI_AURA_RULESET


def _model(*elements: str) -> dict:
    return {
        "schema_version": 1,
        "ruleset": MULTI_AURA_RULESET,
        "target": {
            "id": "enemy",
            "kind": "ordinary",
            "knowledge": "known",
            "initial_auras": [
                {
                    "element": element,
                    "remaining_gu": .2,
                    "decay_gu_per_second": .001,
                    "source": "world route fixture",
                    "source_owner": element + ".owner",
                }
                for element in elements
            ],
            "freeze": {
                "freezable": False,
                "resistance": 0,
                "unfreezable_resolution": "coexist",
                "status": "assumed",
                "source": "world route fixture",
            },
            "status": "assumed",
            "source": "world route fixture",
        },
        "profiles": [
            {
                "id": "anemo",
                "element": "anemo",
                "application": "apply",
                "gauge_units": 1,
                "icd": {"kind": "none"},
                "status": "assumed",
                "source": "world route fixture",
            }
        ],
        "swirl_damage": {
            "icd": {
                "kind": "sequence",
                "reset_seconds": .5,
                "sequence": [1, 1, 0],
            },
            "status": "assumed",
            "source": "world route fixture",
        },
    }


def _attempt(event_id: str = "apply.anemo") -> dict:
    return {
        "event_id": event_id,
        "stable_event_id": event_id,
        "action_id": "actor.skill",
        "actor": "actor",
        "time": 0,
        "application": {
            "profile_id": "anemo",
            "target_id": "enemy",
            "landed": True,
        },
        "damage_element": "anemo",
    }


def _ordinary_route(proposal: dict) -> dict:
    return {
        "expected_proposal": copy.deepcopy(proposal),
        "replacement": {
            **copy.deepcopy(proposal),
            "commit_state": "committed",
            "route": "ordinary",
            "initial_gauge_commit_policy": "commit_elemental_proposal_once",
        },
        "decision": None,
    }


def _special_route(proposal: dict) -> dict:
    replacement = {
        **copy.deepcopy(proposal),
        "id": proposal["id"] + ":special:fixture_special",
        "type": "fixture_special",
        "ordinary_reaction_id": proposal["id"],
        "replaces": proposal["type"],
        "ordinary_damage_suppressed": True,
        "ordinary_side_effects_committed": False,
        "commit_state": "committed",
        "route": "special",
        "initial_gauge_commit_policy": "commit_elemental_proposal_once",
        "mechanics_source": "world route fixture",
        "profile": {"contract": "world-route-fixture-v1"},
        "reaction_trigger_tags": ["fixture_special"],
    }
    return {
        "expected_proposal": copy.deepcopy(proposal),
        "replacement": replacement,
        "decision": {
            "type": "special_reaction_decision",
            "event_id": proposal["id"] + ":special-decision",
            "t": 0.0,
            "time_tick": 0,
            "ordinary_reaction_id": proposal["id"],
            "ordinary_reaction_type": proposal["type"],
            "special_reaction_type": replacement["type"],
            "decision": "replace",
            "reason": "explicit_switch_and_present_capability",
            "status": "executed",
            "profile": copy.deepcopy(replacement["profile"]),
        },
    }


def _preview(runtime: ElementalRuntime) -> list[dict]:
    value = runtime.preview_copy()._preview_with_world_reaction_routes(**_attempt())
    return value["proposals"]


def test_empty_route_list_commits_a_verified_no_reaction_application():
    runtime = ElementalRuntime(_model())

    assert _preview(runtime) == []
    receipt = runtime._attempt_with_world_reaction_routes(
        reaction_routes=[], **_attempt())

    assert receipt["application_allowed"] is True
    assert receipt["reaction_occurred"] is False


def test_empty_route_list_rejects_a_commit_time_reaction_proposal():
    runtime = ElementalRuntime(_model())
    assert _preview(runtime) == []
    runtime.multi_state.add_or_refresh(
        "pyro", 1, "commit-time drift", "pyro.owner")

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[], **_attempt())

    assert error.value.issue["code"] == "world_reaction_replacement_missing"


def test_one_ordinary_route_is_committed_without_a_special_runtime():
    runtime = ElementalRuntime(_model("pyro"))
    proposals = _preview(runtime)
    assert [row["type"] for row in proposals] == ["swirl"]

    receipt = runtime._attempt_with_world_reaction_routes(
        reaction_routes=[_ordinary_route(proposals[0])], **_attempt())

    assert [row["type"] for row in receipt["reactions"]] == ["swirl"]
    assert receipt["reactions"][0]["route"] == "ordinary"


def test_one_application_can_commit_mixed_ordinary_and_special_routes():
    runtime = ElementalRuntime(_model("hydro", "cryo"))
    proposals = _preview(runtime)
    assert [row["type"] for row in proposals] == ["swirl", "swirl"]
    assert {row["aura_element"] for row in proposals} == {"hydro", "cryo"}
    routes = [
        (_special_route(row) if row["aura_element"] == "cryo"
         else _ordinary_route(row))
        for row in proposals
    ]

    receipt = runtime._attempt_with_world_reaction_routes(
        reaction_routes=routes, **_attempt())

    assert {row["type"] for row in receipt["reactions"]} == {
        "swirl", "fixture_special",
    }
    special = next(row for row in receipt["reactions"]
                   if row["type"] == "fixture_special")
    original = next(row for row in proposals
                    if row["aura_element"] == "cryo")
    for field in (
        "owner", "area_id", "target_id", "consumed_gu",
        "trigger_element", "aura_element", "reaction_element",
    ):
        assert special[field] == original[field]
    assert len(receipt["special_reaction_decisions"]) == 1


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("owner", "forged.owner"),
        ("area_id", "forged.area"),
        ("consumed_gu", 99),
        ("trigger_element", "pyro"),
        ("aura_element", "electro"),
        ("reaction_element", "electro"),
    ],
)
def test_special_route_cannot_rewrite_causal_proposal_fields(
    field: str,
    forged_value,
):
    runtime = ElementalRuntime(_model("cryo"))
    proposal = _preview(runtime)[0]
    route = _special_route(proposal)
    route["replacement"][field] = forged_value

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[route], **_attempt())

    assert error.value.issue["code"] == "world_reaction_replacement_invalid"
    assert error.value.issue["changed_proposal_fields"] == [field]


def test_special_route_rejects_unreviewed_metadata():
    runtime = ElementalRuntime(_model("cryo"))
    proposal = _preview(runtime)[0]
    route = _special_route(proposal)
    route["replacement"]["forged_side_effect"] = {"damage": 999999}

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[route], **_attempt())

    assert error.value.issue["code"] == "world_reaction_replacement_invalid"
    assert error.value.issue["unexpected_replacement_fields"] == [
        "forged_side_effect",
    ]


def test_ordinary_route_rejects_a_special_decision():
    runtime = ElementalRuntime(_model("pyro"))
    proposal = _preview(runtime)[0]
    route = _ordinary_route(proposal)
    route["decision"] = _special_route(proposal)["decision"]

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[route], **_attempt())

    assert error.value.issue["code"] == "world_reaction_decision_invalid"
    assert error.value.issue["changed_fields"] == ["decision"]


def test_special_route_requires_a_decision():
    runtime = ElementalRuntime(_model("cryo"))
    proposal = _preview(runtime)[0]
    route = _special_route(proposal)
    route["decision"] = None

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[route], **_attempt())

    assert error.value.issue["code"] == "world_reaction_decision_invalid"
    assert error.value.issue["changed_fields"] == ["decision"]


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("type", "forged_decision"),
        ("event_id", "forged:decision"),
        ("t", 1.0),
        ("time_tick", 1),
        ("ordinary_reaction_id", "forged:ordinary"),
        ("ordinary_reaction_type", "melt"),
        ("special_reaction_type", "forged_special"),
        ("decision", "ordinary"),
        ("reason", "switch_disabled"),
        ("status", "pending"),
        ("profile", {"contract": "forged"}),
    ],
)
def test_special_route_decision_fields_are_bound_to_the_route(
    field: str,
    forged_value,
):
    runtime = ElementalRuntime(_model("cryo"))
    proposal = _preview(runtime)[0]
    route = _special_route(proposal)
    route["decision"][field] = forged_value

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[route], **_attempt())

    assert error.value.issue["code"] == "world_reaction_decision_invalid"
    assert error.value.issue["changed_fields"] == [field]


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_special_route_decision_rejects_missing_or_extra_metadata(mutation: str):
    runtime = ElementalRuntime(_model("cryo"))
    proposal = _preview(runtime)[0]
    route = _special_route(proposal)
    if mutation == "missing":
        del route["decision"]["reason"]
    else:
        route["decision"]["unused_metadata"] = True

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[route], **_attempt())

    assert error.value.issue["code"] == "world_reaction_decision_invalid"
    detail = ("missing_fields" if mutation == "missing"
              else "unexpected_fields")
    expected = "reason" if mutation == "missing" else "unused_metadata"
    assert error.value.issue[detail] == [expected]


def test_special_route_decision_binds_the_replacement_identity():
    runtime = ElementalRuntime(_model("cryo"))
    proposal = _preview(runtime)[0]
    route = _special_route(proposal)
    route["replacement"]["id"] = proposal["id"] + ":special:forged"

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[route], **_attempt())

    assert error.value.issue["code"] == "world_reaction_decision_invalid"
    assert error.value.issue["changed_fields"] == ["replacement.id"]


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        ("missing", "decision"),
        ("extra", "unused_metadata"),
    ],
)
def test_route_binding_rejects_missing_or_unused_metadata(
    mutation: str,
    field: str,
):
    runtime = ElementalRuntime(_model("cryo"))
    proposal = _preview(runtime)[0]
    route = _special_route(proposal)
    if mutation == "missing":
        del route[field]
    else:
        route[field] = True

    with pytest.raises(ElementalPending) as error:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[route], **_attempt())

    assert error.value.issue["code"] == "world_reaction_route_binding_invalid"
    detail = ("missing_binding_fields" if mutation == "missing"
              else "unexpected_binding_fields")
    assert error.value.issue[detail] == [field]


def test_unused_and_duplicate_route_bindings_fail_closed():
    runtime = ElementalRuntime(_model("pyro"))
    proposal = _preview(runtime)[0]
    extra = copy.deepcopy(proposal)
    extra["id"] += ":unused"

    with pytest.raises(ElementalPending) as unused:
        runtime._attempt_with_world_reaction_routes(
            reaction_routes=[_ordinary_route(proposal), _ordinary_route(extra)],
            **_attempt(),
        )
    assert unused.value.issue["code"] == "world_reaction_replacement_unused"

    pristine = ElementalRuntime(_model("pyro"))
    proposal = _preview(pristine)[0]
    with pytest.raises(ValueError, match="不得重复"):
        pristine._attempt_with_world_reaction_routes(
            reaction_routes=[_ordinary_route(proposal), _ordinary_route(proposal)],
            **_attempt("apply.duplicate"),
        )


def test_legacy_special_only_wrapper_rejects_ordinary_routes():
    runtime = ElementalRuntime(_model("pyro"))
    proposal = _preview(runtime)[0]

    with pytest.raises(ValueError, match="只接受特殊反应"):
        runtime._attempt_with_world_reaction_replacements(
            reaction_replacements=[_ordinary_route(proposal)], **_attempt())
