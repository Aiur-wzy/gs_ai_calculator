"""Pure-leaf ownership and independent canonical validation under live faults."""
from __future__ import annotations

import ast
import copy
import inspect

import pytest
from test_direct_stellar_damage_contract import direct_stellar_profile
from test_special_reactions import model

from genshin_engine import special_reaction_contracts as contracts
from genshin_engine import special_reaction_profiles as profiles
from genshin_engine import special_reaction_validation as validation
from genshin_engine import special_reactions as owner


def test_profile_validation_facade_delegates_to_state_free_leaves():
    assert owner.validate_special_reaction_model is validation.validate_special_reaction_model
    assert owner.special_reaction_capabilities is validation.special_reaction_capabilities
    assert owner.validate_direct_stellar_damage_profile is profiles.validate_direct_stellar_damage_profile
    assert owner.direct_lunar_damage_profile_content_hash is profiles.direct_lunar_damage_profile_content_hash
    for module in (contracts, profiles, validation):
        tree = ast.parse(inspect.getsource(module))
        assert not any(isinstance(item, ast.ClassDef) for item in tree.body)
        assert not any(isinstance(item, ast.ImportFrom)
                       and item.module in {"genshin_engine.special_reactions",
                                           "genshin_engine.elemental_world"}
                       for item in tree.body)


@pytest.mark.parametrize("family", contracts.SPECIAL_REACTION_TYPES)
def test_model_validation_and_issue_order_equal_independent_frozen_graph(family):
    candidate = model(family)
    row = candidate["replacements"][family]
    row["qualification"]["status"] = "unknown"
    row["qualification"]["provider_ids"] = []
    row["mechanics"]["profile_content_hash"] = "0" * 64
    row["mechanics"]["status"] = "assumed"
    row["mechanics"]["override_reason"] = ""
    before = copy.deepcopy(candidate)
    reference = owner._REFERENCE_SPECIAL_GLOBALS["validate_special_reaction_model"]
    assert reference is not validation.validate_special_reaction_model
    assert reference.__globals__ is not validation.__dict__
    assert validation.validate_special_reaction_model(candidate) == reference(candidate)
    assert candidate == before


def test_live_scalar_validator_fault_does_not_change_canonical_model_checks(monkeypatch):
    candidate = model("stellar_superconduct")
    reference = owner._REFERENCE_SPECIAL_GLOBALS["validate_special_reaction_model"]
    assert reference(candidate) == validation.validate_special_reaction_model(candidate) == []
    monkeypatch.setattr(validation, "_finite_number", lambda *args, **kwargs: False)
    assert validation.validate_special_reaction_model(candidate)
    assert reference(candidate) == []


def test_live_catalog_injection_cannot_promote_canonical_direct_profile(monkeypatch):
    profile = direct_stellar_profile(status="confirmed")
    key = (profile["profile_id"], profile["profile_revision"])
    reference = owner._REFERENCE_SPECIAL_GLOBALS["validate_direct_stellar_damage_profile"]
    assert [issue["code"] for issue in reference(profile)] == ["reviewed_profile"]
    monkeypatch.setattr(profiles, "_load_reviewed_direct_special_damage_profiles",
                        lambda: {key: profile})
    assert profiles.validate_direct_stellar_damage_profile(profile) == []
    assert [issue["code"] for issue in reference(profile)] == ["reviewed_profile"]
    assert owner._REFERENCE_SPECIAL_GLOBALS["load_reviewed_direct_special_damage_profiles"]() == {}


def test_frozen_profile_loaders_have_independent_caches_and_globals():
    for name in ("_load_reviewed_direct_special_damage_profiles",
                 "load_reviewed_special_reaction_profiles"):
        live = getattr(profiles, name)
        reference = owner._REFERENCE_SPECIAL_GLOBALS[name]
        assert reference is not live
        assert reference.__wrapped__.__globals__ is not live.__wrapped__.__globals__
        assert reference.cache_parameters() == live.cache_parameters()
        expected = live()
        reference.cache_clear()
        before = live.cache_info()
        assert reference() == expected
        assert reference.cache_info().misses == 1
        assert live.cache_info().misses == before.misses


def test_live_reviewed_profile_mutation_cannot_modify_frozen_validation(monkeypatch):
    candidate = model("stellar_superconduct")
    reference = owner._REFERENCE_SPECIAL_GLOBALS["validate_special_reaction_model"]
    changed = copy.deepcopy(profiles.REVIEWED_SPECIAL_REACTION_PROFILES["stellar_superconduct"])
    changed["parameters"]["field_duration_seconds"] += 1
    monkeypatch.setitem(profiles.REVIEWED_SPECIAL_REACTION_PROFILES,
                        "stellar_superconduct", changed)
    assert "profile_override_status" in {
        row["code"] for row in validation.validate_special_reaction_model(candidate)}
    assert reference(candidate) == []
