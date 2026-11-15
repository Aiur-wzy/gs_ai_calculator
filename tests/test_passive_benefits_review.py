"""Independent EC12 source projection and declared stat-basis counterexamples."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_freeze_mechanics_review import setup as freeze_setup
from test_freeze_review import EVIDENCE, SAFE_CONTACT
from test_mechanics_execution import ability, action, second_character

from genshin_engine import runtime_passive_benefit_compiler as compiler
from genshin_engine.mechanics import execute_mechanics
from genshin_runtime.passive_source_identity import (
    passive_source_fingerprint,
    passive_unlock_fingerprint,
)


def profile():
    return json.loads((Path(__file__).parent / "fixtures/runtime_passive_kazuha_current.json").read_text("utf8"))


def historical_profile():
    """Retain the prior accepted snapshot as an explicit stale-input oracle."""
    return json.loads((Path(__file__).parent / "fixtures/runtime_passive_kazuha.json").read_text("utf8"))


def scenario():
    result = compiler.passive_benefit_compiler_caps()["default_scenario"]
    result.update(target_id="enemy", origin_target_range="within_source_range",
        recipient_range="all_party_within_source_range", initial_source_effects="none",
        recipient_bonus_policy="not_included", stat_basis="fixed_panel_nonextra_em_equals_total_em",
        event_order_policy="declared_atomic_hit_order", status="assumed", source="Independent fixed-panel scope; parent eligibility is checked by App")
    return result


def compile_source(data=None, scope=None, **changes):
    return compiler.compile_passive_benefit_source(profile() if data is None else data,
        **{"owner": "a", "source_id": "a4-test", "scenario": scenario() if scope is None else scope, **changes})


def rehash(data):
    # Recompute public envelope digests after a mutation so these tests challenge
    # the structural admission, rather than merely tripping a stale hash.
    data["source_fingerprint"] = passive_source_fingerprint(data)
    data["unlock_fingerprint"] = passive_unlock_fingerprint(data)
    data["content_fingerprint"] = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != "content_fingerprint"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return data


def no_execution(out):
    assert out["status"] in ("pending", "invalid"), out["issues"]
    assert out["parts"] == out["rules"] == out["initial_rules"] == out["unique_keys"] == []
    assert out["coverage_certificate"]["projection_complete"] is False


def test_prior_pair_fixture_is_stale_and_cannot_be_reaccepted():
    out = compile_source(historical_profile())
    no_execution(out)
    assert any(issue["code"] == "unsupported_value"
               and issue["path"] == "/source_identity/upstream_commit"
               for issue in out["issues"])


def test_current_pair_fixture_preserves_all_six_admitted_nodes():
    previous, current = historical_profile(), profile()
    compared = (
        (previous["entry_records"]["avatar"]["record"], current["entry_records"]["avatar"]["record"]),
        (previous["entry_records"]["depot"]["record"], current["entry_records"]["depot"]["record"]),
        (previous["unlock"]["selected_promote_record"]["record"],
         current["unlock"]["selected_promote_record"]["record"]),
        (previous["entry_records"]["proud_skill"]["record"],
         current["entry_records"]["proud_skill"]["record"]),
        (previous["source_bundle"]["abilities"][0]["payload"],
         current["source_bundle"]["abilities"][0]["payload"]),
        (previous["source_bundle"]["talent"]["operations"],
         current["source_bundle"]["talent"]["operations"]),
    )
    assert all(before == after for before, after in compared)
    assert previous["entry_records"]["proud_skill"]["json_pointer"] == "/3534"
    assert current["entry_records"]["proud_skill"]["json_pointer"] == "/3535"
    assert current["source_bundle"]["param_list"][:2] == [.00039999998989515007, 8]


def test_review_exact_source_projects_nonextra_alias_without_claiming_full_game_semantics():
    data, scope = profile(), scenario()
    before = copy.deepcopy((data, scope))
    out = compile_source(data, scope)
    assert out["status"] == "ready", out["issues"]
    assert (data, scope) == before
    assert out["whole_source_status"] == "partial"
    assert out["gameplay_semantics_status"] == "declared_not_verified"
    assert out["execution_binding"]["eligibility"] == "fresh_calibrated_c0_skill_parent_required_by_app"
    assert {p["part_id"] for p in out["parts"]} == {"passive.dmg_bonus.hydro", "passive.dmg_bonus.cryo"}
    assert len(out["rules"]) == len(out["initial_rules"]) == 2
    for rule in out["rules"]:
        element = rule["trigger"]["reaction_elements"][0]
        assert rule["trigger"]["owner_presence"] == "any"
        assert rule["source_id"] == "a4-test:passive.dmg_bonus." + element
        assert rule["beneficiaries"] == {"mode": "team", "scope": "locked"}
        assert rule["timing"]["duration"] == 8
        assert rule["stacking"] == {"max": 1, "add": 1, "policy": "refresh"}
        effect = rule["effects"][0]
        assert effect["domain"] == "self"
        assert effect["benefit_type_id"] == "benefit.dmg_bonus." + element
        assert effect["value"] == {"kind": "stat", "subject": "owner", "stat": "em", "factor": .00039999998989515007, "sample": "trigger"}
        assert effect["value"]["factor"] * 900 == pytest.approx(.35999999090563506)
    assert all(not row["instances"] for row in out["initial_rules"])
    assert any(row.get("excluded_reactions") == ["StarSwirlIce"] for row in out["branches"])


@pytest.mark.parametrize("field", ["origin_target_range", "recipient_range", "initial_source_effects",
    "recipient_bonus_policy", "stat_basis", "event_order_policy"])
def test_review_unknown_scope_cannot_become_ready_from_an_available_source(field):
    scope = scenario()
    scope[field] = "unknown"
    no_execution(compile_source(scope=scope))


@pytest.mark.parametrize("mutation", ["ability-extra-control", "proud-parameter", "talent-flag", "opening-path", "reference-target", "missing-branch"])
def test_review_rehashed_unknown_or_mismatched_parent_cannot_pass_admission(mutation):
    data = profile()
    source = data["source_bundle"]
    if mutation == "ability-extra-control":
        source["abilities"][0]["payload"]["independent_unknown_side_effect"] = {"$type": "Unknown"}
    elif mutation == "proud-parameter":
        source["parameter_record"]["record"]["paramList"][0] = .04
        source["param_list"][0] = .04
        data["entry_records"]["proud_skill"]["record"]["paramList"][0] = .04
    elif mutation == "talent-flag":
        raw = source["talent"]["operations"][0]
        boolean_keys = [key for key, value in raw.items() if isinstance(value, bool)]
        assert len(boolean_keys) == 1
        raw[boolean_keys[0]] = False
    elif mutation == "opening-path":
        data["entry_records"]["selected_opening"]["json_pointer"] += "/wrong"
    elif mutation == "reference-target":
        source["references"][0]["resolved_definition_id"] = "0" * 64
    else:
        source["references"].pop()
    no_execution(compile_source(rehash(data)))


@pytest.mark.parametrize("mutation", ["source-status", "locked", "level", "ascension", "top-snapshot", "inner-snapshot"])
def test_review_source_or_progression_identity_cannot_be_upgraded_by_rehash(mutation):
    data = profile()
    if mutation == "source-status":
        data["source_status"] = "pending"
    elif mutation == "locked":
        data["unlock"]["status"] = "locked"
    elif mutation in ("level", "ascension"):
        data["unlock"]["supplied"]["character_level" if mutation == "level" else "ascension"] = 80 if mutation == "level" else 4
    elif mutation == "top-snapshot":
        data["snapshot"]["snapshot_id"] = "f" * 64
    else:
        data["source_bundle"]["snapshot"]["version_line"] = "beta"
    no_execution(compile_source(rehash(data)))


@pytest.mark.parametrize("mutation", ["requirement", "checks", "origin"])
def test_review_unlock_claims_must_remain_consistent_with_raw_promote_evidence(mutation):
    data = profile()
    if mutation == "requirement":
        data["unlock"]["requirement"]["required_ascension"] = 9
    elif mutation == "checks":
        data["unlock"]["checks"][0]["status"] = "fail"
    else:
        data["unlock"]["requirement"]["proud_break_level"]["origin"]["requested_pointer"] += "/wrong"
    no_execution(compile_source(rehash(data)))


def test_review_ready_return_mutation_cannot_pollute_next_source_compilation():
    out = compile_source()
    assert out["status"] == "ready"
    out["rules"][0]["effects"][0]["value"]["factor"] = 99
    out["provenance"]["reference_sources"].clear()
    out["parts"].clear()
    other = compile_source()
    assert len(other["parts"]) == 2
    assert all(r["effects"][0]["value"]["factor"] == .00039999998989515007 for r in other["rules"])
    assert other["provenance"]["reference_sources"]


def ordinary_engine_inputs(times):
    t, r, m = freeze_setup(tuple("anemo" if i == 0 else "cryo" for i in range(len(times))),
        (1,) * len(times), times, aura={"element": "cryo", "remaining_gu": 3.2,
        "decay_gu_per_second": .01, "source": "independent initial cryo"})
    t["characters"][0]["stats"]["em"] = 900
    m["elemental_model"]["profiles"].append({"id": "no-ice", "element": "cryo", "application": "none", **EVIDENCE})
    for descriptor in m["hit_events"][1:]:
        descriptor["application"]["profile_id"] = "no-ice"
        descriptor["contact"] = copy.deepcopy(SAFE_CONTACT)
    projected = compile_source()
    assert projected["status"] == "ready", projected["issues"]
    m["rules"] = projected["rules"]
    m["initial_state"]["rules"] = projected["initial_rules"]
    return t, r, m


def execute(values):
    before = copy.deepcopy(values)
    out = execute_mechanics(*values)
    assert out["result"] is not None, out["issues"]
    assert values == before
    return out


def test_review_provider_trigger_sample_is_not_the_recipients_damage_snapshot():
    t, r, m = ordinary_engine_inputs((.2, 1, 1))
    r["abilities"].append(ability("later-cast"))
    r["actions"].append(action("later", "later-cast", .3, .4, 3, repeat={"mode": "once"}))
    m["hit_events"][1]["self_snapshot_event"] = "cast"
    m["hit_events"][2]["self_snapshot_event"] = "later"
    out = execute((t, r, m))
    classic = [h["expected"] for h in out["result"]["hits"] if h["damage_system"] == "classic"]
    assert classic == pytest.approx([45, 45, 61.19999959075358])
    swirl = [h["expected"] for h in out["result"]["hits"] if h["damage_system"] == "transformative"]
    assert swirl == pytest.approx([1446.85 * .6 * (1 + 16 * 900 / 2900) * .9])


def test_review_passive_can_trigger_offfield_and_expires_at_exact_eight_seconds():
    t, r, m = ordinary_engine_inputs((.2, 8.199999, 8.2))
    t["loop_duration"] = r["period"] = 9
    second_character(t, r)
    r["abilities"].append(ability("swap", "a", "action.switch"))
    r["actions"].append(action("switch-away", "swap", .15, .15, 2,
        switch_to="b", repeat={"mode": "once"}))
    out = execute((t, r, m))
    classic = [h["expected"] for h in out["result"]["hits"] if h["damage_system"] == "classic"]
    assert classic == pytest.approx([45, 61.19999959075358, 45])
    activated = [r for r in out["mechanics_trace"] if r["type"] == "activation"]
    assert len(activated) == 1


def test_review_dynamic_em_cannot_enter_fixed_alias_even_when_no_buff_would_fire():
    t, r, m = ordinary_engine_inputs((.2, 1))
    dormant = copy.deepcopy(m["rules"][0])
    dormant["id"], dormant["source_id"] = "unfired-em-source", "manual-em-source"
    dormant["effects"][0].update(benefit_type_id="benefit.em.all", value=100)
    dormant["trigger"]["reaction_elements"] = ["hydro"]
    m["rules"].append(dormant)
    m["initial_state"]["rules"].append({"rule_id": dormant["id"], "cooldown_remaining": 0, "instances": []})
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending" and out["result"] is None
    assert any(i["code"] == "swirl_dynamic_em" for i in out["issues"])
