"""Explicit generic handler identities survive packaging and never select newest."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_mechanics_execution import fixture
from test_special_reactions import model, reaction, runtime

from genshin_app.equipment import new_plan
from genshin_app.equipment.mechanics import prepare_mechanics
from genshin_engine import special_reactions as owner
from genshin_engine.special_handler_registry import (
    BINDING_FIELDS,
    HandlerBindingUnavailable,
    _code_contract,
    _module_contract,
    freeze_special_reaction_handler_bindings,
    resolve_exact_handler_binding,
    resolved_handler_binding,
    special_handler_registry_capabilities,
)
from genshin_engine.special_reaction_reference import (
    copy_runtime,
    replace_special_reaction,
)
from genshin_engine.special_reactions import (
    SpecialReactionPending,
    validate_special_reaction_model,
)


def test_registry_and_binding_contracts_are_exact_versioned_and_detached():
    catalog = special_handler_registry_capabilities()
    assert len(catalog["handlers"]) == 5
    assert {row["family"] for row in catalog["handlers"]} == set(owner.SPECIAL_REACTION_TYPES)
    for row in catalog["handlers"]:
        assert set(row) == BINDING_FIELDS
        assert row["contract_version"] == row["profile_schema_version"] == 1
        assert row["implementation_version"] == 2
        assert row["capabilities"] == sorted(set(row["capabilities"]))
        assert row["registry_fingerprint"] == catalog["registry_fingerprint"]
    catalog["handlers"][0]["capabilities"].clear()
    assert special_handler_registry_capabilities()["handlers"][0]["capabilities"]


def test_normalized_code_contract_ignores_absolute_path_and_source_line_number():
    def original(value):
        return value * 1.5

    moved = original.__code__.replace(co_filename="Z:/another/install/application.py", co_firstlineno=999)
    assert _code_contract(original.__code__) == _code_contract(moved)


def test_profile_leaf_abi_includes_underlying_catalog_code_without_cache_contents():
    from genshin_engine.special_reaction_profiles import (
        load_reviewed_special_reaction_profiles,
    )

    before = _module_contract("genshin_engine.special_reaction_profiles")
    loader = before["functions"]["load_reviewed_special_reaction_profiles"]
    assert loader["wrapped"] == _code_contract(load_reviewed_special_reaction_profiles.__wrapped__.__code__)
    assert loader["cache_policy"] == {"maxsize": 1, "typed": False}
    load_reviewed_special_reaction_profiles.cache_clear()
    load_reviewed_special_reaction_profiles()
    assert _module_contract("genshin_engine.special_reaction_profiles") == before


def test_registry_fingerprint_is_stable_across_process_hash_seeds():
    command = [sys.executable, "-c", "from genshin_engine.special_handler_registry import special_handler_registry_capabilities as c; print(c()['registry_fingerprint'])"]
    values = [subprocess.check_output(command, env={**os.environ, "PYTHONHASHSEED": str(seed)},
                                      text=True, timeout=30).strip() for seed in (1, 99)]
    assert values[0] == values[1]


def test_freezing_never_enables_disabled_reactions_or_mutates_user_input():
    value = model("stellar_superconduct", "lunar_bloom")
    value["replacements"]["lunar_bloom"]["enabled"] = False
    before = copy.deepcopy(value)
    frozen = freeze_special_reaction_handler_bindings(value)
    assert value == before
    assert frozen["schema_version"] == 2
    assert set(frozen["handler_bindings"]) == {"stellar_superconduct"}
    assert frozen["replacements"] == value["replacements"]
    assert freeze_special_reaction_handler_bindings(model()) == model()


@pytest.mark.parametrize("family", owner.SPECIAL_REACTION_TYPES)
def test_v2_pinned_execution_has_identical_numeric_state_trace_and_reference(family):
    legacy = runtime(family)
    pinned = runtime(family, value=freeze_special_reaction_handler_bindings(model(family)))
    canonical = copy_runtime(pinned)
    for index in range(1, 7):
        proposal = reaction(family, index)
        expected = legacy.replace(proposal, time=(index-1)*100_000)
        assert pinned.replace(proposal, time=(index-1)*100_000) == expected
        assert replace_special_reaction(canonical, proposal, time=(index-1)*100_000) == expected
    assert legacy.advance(5_000_000) == pinned.advance(5_000_000)
    assert legacy.state() == pinned.state() and legacy.trace == pinned.trace


@pytest.mark.parametrize(("field", "new_value", "reason"), [
    ("implementation_version", 999, "special_handler_version_unavailable"),
    ("contract_version", 999, "special_handler_version_unavailable"),
    ("profile_schema_version", 999, "special_handler_binding_stale"),
    ("registry_fingerprint", "0"*64, "special_handler_binding_stale"),
    ("handler_fingerprint", "0"*64, "special_handler_binding_stale"),
    ("capabilities", ["replace"], "special_handler_binding_stale"),
])
def test_unknown_or_stale_binding_stays_pending_without_fallback_or_partial_state(field, new_value, reason):
    value = freeze_special_reaction_handler_bindings(model("stellar_superconduct"))
    value["handler_bindings"]["stellar_superconduct"][field] = new_value
    assert validate_special_reaction_model(value) == []  # Shape is valid; availability is consumption-scoped.
    assert freeze_special_reaction_handler_bindings(value) == value  # Never silently repin a v2 file.
    actual = runtime("stellar_superconduct", value=value)
    before = copy.deepcopy(actual.state())
    with pytest.raises(SpecialReactionPending) as caught:
        actual.replace(reaction("stellar_superconduct"), time=0)
    assert caught.value.issue["code"] == reason
    assert actual.state() == before
    assert len(actual.trace) == 1 and actual.trace[0]["decision"] == "pending"


def test_unused_disabled_binding_does_not_block_ordinary_execution():
    value = freeze_special_reaction_handler_bindings(model("stellar_superconduct"))
    value["replacements"]["stellar_superconduct"]["enabled"] = False
    value["handler_bindings"]["stellar_superconduct"]["implementation_version"] = 999
    actual = runtime("stellar_superconduct", value=value)
    assert actual.replace(reaction("stellar_superconduct"), time=0)["reaction"]["route"] == "ordinary"


def test_live_v2_deadline_cannot_continue_under_a_stale_binding():
    actual = runtime("stellar_superconduct", value=freeze_special_reaction_handler_bindings(model("stellar_superconduct")))
    actual.replace(reaction("stellar_superconduct"), time=0)
    actual.model["handler_bindings"]["stellar_superconduct"]["handler_fingerprint"] = "0"*64
    before, trace = copy.deepcopy(actual.state()), copy.deepcopy(actual.trace)
    with pytest.raises(SpecialReactionPending) as caught:
        actual.advance(4_000_000)
    assert caught.value.issue["code"] == "special_handler_binding_stale"
    assert actual.state() == before and actual.trace == trace


def test_existing_qualification_issue_still_precedes_handler_availability():
    value = freeze_special_reaction_handler_bindings(model("stellar_superconduct"))
    value["replacements"]["stellar_superconduct"]["qualification"]["status"] = "pending"
    value["handler_bindings"]["stellar_superconduct"]["implementation_version"] = 999
    actual = runtime("stellar_superconduct", value=value)
    with pytest.raises(SpecialReactionPending) as caught:
        actual.replace(reaction("stellar_superconduct"), time=0)
    assert caught.value.issue["code"] == "special_qualification_pending"


@pytest.mark.parametrize("family", owner.SPECIAL_REACTION_TYPES)
def test_pre_profile_split_signed_bindings_are_explicitly_retired_not_relabelled(family):
    old_catalog = json.loads((Path(__file__).parent / "fixtures" /
                              "special-handler-registry-pre-profile-split-v1.json").read_text("utf8"))
    old_binding = next(row for row in old_catalog["handlers"] if row["family"] == family)
    before = copy.deepcopy(old_binding)
    with pytest.raises(HandlerBindingUnavailable) as caught:
        resolve_exact_handler_binding(family, old_binding)
    assert caught.value.code == "special_handler_version_unavailable"
    assert caught.value.details == {"family": family, "contract_version": 1,
                                    "implementation_version": 1}
    assert old_binding == before
    current = resolved_handler_binding(family)
    assert current["implementation_version"] == 2
    assert current["handler_fingerprint"] != old_binding["handler_fingerprint"]
    assert current["registry_fingerprint"] != old_binding["registry_fingerprint"]

    value = freeze_special_reaction_handler_bindings(model(family))
    value["handler_bindings"][family] = old_binding
    assert freeze_special_reaction_handler_bindings(value) == value
    actual = runtime(family, value=value)
    state = copy.deepcopy(actual.state())
    with pytest.raises(SpecialReactionPending) as pending:
        actual.replace(reaction(family), time=0)
    assert pending.value.issue["code"] == "special_handler_version_unavailable"
    assert actual.state() == state


def test_app_materializer_freezes_generic_registry_without_changing_original_knowledge():
    template, rotation, mechanics = fixture()
    mechanics["special_reaction_model"] = model("stellar_superconduct")
    plan = new_plan(template)
    plan.update(plan_version=3, rotation_model=rotation, mechanics_model=mechanics)
    before = copy.deepcopy(plan)
    missing = []
    resolved, *_ = prepare_mechanics(plan, [], template, "0"*64,
                                     lambda *args, **kwargs: missing.append((args, kwargs)))
    assert plan == before
    special = resolved["special_reaction_model"]
    assert special["schema_version"] == 2
    assert special["handler_bindings"]["stellar_superconduct"] == resolved_handler_binding("stellar_superconduct")
    assert special["replacements"] == mechanics["special_reaction_model"]["replacements"]
    assert json.dumps(special, sort_keys=True)  # A plain persisted JSON contract, no executable callback.
