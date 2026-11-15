"""Independent EC11 contract and queue review; expected damage is hand-derived."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest
from test_freeze_mechanics_review import setup as freeze_setup
from test_freeze_review import EVIDENCE, SAFE_CONTACT
from test_mechanics_execution import ability, action, second_character

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.runtime_compiler import _hash

SOURCE = Path(__file__).parent / "fixtures/runtime_reaction_benefit_vv_current.json"
BONUS = .6000000238418579
SHRED = .4000000059604645
SWIRL = 1597.3224279414294
STATIC_SWIRL = 1250.0784186276196
CLASSIC_SHRED = 57.50000014901161


def api():
    return importlib.import_module("genshin_engine.runtime_reaction_benefit_compiler")


def bundle():
    return json.loads(SOURCE.read_text("utf8"))


def scope():
    value = api().reaction_benefit_compiler_caps()["default_scenario"]
    value.update(target_id="enemy", origin_target_range="within_source_range",
                 initial_source_effects="none", event_order_policy="declared_atomic_hit_order",
                 status="assumed", source="Independent source/queue engineering scope; no current-client claim")
    return value


def project(data=None, scenario=None, *, accept=True, owner="a", source_id="set:upstream", period=12):
    data = bundle() if data is None else data
    scenario = scope() if scenario is None else scenario
    result = api().compile_reaction_benefit_source(data, owner=owner, source_id=source_id, scenario=scenario, period=period)
    if accept:
        scenario = {**scenario, **result["required_confirmation"]}
        result = api().compile_reaction_benefit_source(data, owner=owner, source_id=source_id, scenario=scenario, period=period)
    return result


def rehash(data):
    data["content_fingerprint"] = _hash({k: v for k, v in data.items() if k != "content_fingerprint"})


def unexecuted(result):
    assert result["status"] in ("pending", "invalid"), result
    assert result["parts"] == result["static_parts"] == result["rules"] == result["initial_rules"] == []
    assert result["coverage_certificate"]["projection_complete"] is False


def lower(values):
    """Only materialize the public three-part outputs; do not invent their rules."""
    t, r, m = values
    compiled = project(period=r["period"])
    assert compiled["status"] == "ready", compiled["issues"]
    m["rules"].extend(copy.deepcopy(compiled["rules"]))
    m["initial_state"]["rules"].extend(copy.deepcopy(compiled["initial_rules"]))
    for part in compiled["parts"]:
        if part["delivery"]["kind"] != "buff":
            continue
        d = part["delivery"]
        t["buffs"].append({"id": "compiled:" + part["part_id"], "source": "EC11 independent materialization",
            "start": d["start"], "end": d["end"], "scope": d["scope"], "target": d["target"],
            "effects": [{**copy.deepcopy(d["effect"]), "value": part["rule"]["value"]}],
            "extensions": {"owner": "a", "source_id": "set:" + part["part_id"]}})
    return values, compiled


def result(values):
    prior = copy.deepcopy(values)
    out = execute_mechanics(*values)
    assert out["result"] is not None, out["issues"]
    assert prior == values
    return out


def damages(out):
    return [h["expected"] for h in out["result"]["hits"]]


def activations(out, element=None):
    rows = [x for x in out["mechanics_trace"] if x["type"] == "activation"]
    if element:
        ids = {r["id"] for r in project()["rules"] if r["trigger"]["reaction_elements"] == [element]}
        rows = [x for x in rows if x["rule_id"] in ids]
    return rows


def cryo_wind(times=(.2,), *, gu=3.2):
    values = freeze_setup(["anemo"] * len(times), [1] * len(times), times,
        aura={"element": "cryo", "remaining_gu": gu, "decay_gu_per_second": .001, "source": "explicit independent initial cryo"})
    return values


def test_review_three_parts_are_distinct_closed_sources_and_partial_projection():
    data, scenario = bundle(), scope()
    before = copy.deepcopy((data, scenario))
    preview = project(data, scenario, accept=False)
    unexecuted(preview)
    assert preview["required_confirmation"]["accepted_assumptions_fingerprint"]
    out = project(data, scenario)
    assert out["status"] == out["execution_status"] == out["projection_status"] == "ready"
    assert out["whole_source_status"] == "partial"
    assert out["coverage_certificate"]["source_complete"] is False
    assert out["coverage_certificate"]["projection_complete"] is True
    assert out["execution_binding"]["kind"] == "elemental_reaction"
    assert len(out["parts"]) == 3 and len(out["rules"]) == len(out["initial_rules"]) == 2
    parts = {p["part_id"]: p for p in out["parts"]}
    assert set(parts) == {"upstream.swirl_bonus", "upstream.shred.hydro", "upstream.shred.cryo"}
    static = parts["upstream.swirl_bonus"]
    assert static["rule"] == {"kind": "constant", "value": BONUS}
    assert static["delivery"]["scope"] == "team" and static["delivery"]["target"] == "a"
    assert static["delivery"]["start"] == 0 and static["delivery"]["end"] == 12
    assert static["delivery"]["effect"]["zone"] == "reaction_bonus"
    assert static["delivery"]["effect"]["stat"] == "swirl"
    assert static["delivery"]["effect"]["benefit_type_id"] == "benefit.reaction_bonus.swirl"
    for r in out["rules"]:
        element, = r["trigger"]["reaction_elements"]
        part = parts["upstream.shred." + element]
        assert r["source_id"] == "set:" + part["part_id"]
        assert part["rule"]["rule_id"] == r["id"]
        assert r["trigger"]["characters"] == ["a"] and r["trigger"]["owner_presence"] == "frontline"
        assert r["timing"]["duration"] == 10 and r["stacking"] == {"max": 1, "add": 1, "policy": "refresh"}
        assert r["effects"][0]["domain"] == "enemy" and r["effects"][0]["value"] == SHRED
        assert r["effects"][0]["benefit_type_id"] == "benefit.res_shred." + element
    assert all(x["instances"] == [] for x in out["initial_rules"])
    assert (data, scenario) == before


@pytest.mark.parametrize("field,value", [
    ("target_id", None), ("origin_target_range", "unknown"), ("initial_source_effects", "unknown"),
    ("event_order_policy", "unknown"), ("status", "pending"), ("simulation_clock", "hitlag"),
    ("reaction_bonus_policy", "included"), ("source", "   "),
])
def test_review_confirmation_does_not_supply_missing_scope_or_upgrade_pending(field, value):
    scenario = scope()
    scenario[field] = value
    preview = project(scenario=scenario, accept=False)
    if preview.get("required_confirmation"):
        scenario.update(preview["required_confirmation"])
    unexecuted(project(scenario=scenario, accept=False))


@pytest.mark.parametrize("change", ["owner", "source_id", "period", "target", "evidence", "reference"])
def test_review_previous_acceptance_is_not_reusable_after_semantic_context_change(change, monkeypatch):
    data, scenario = bundle(), scope()
    scenario.update(project(data, scenario, accept=False)["required_confirmation"])
    kwargs = {"owner": "a", "source_id": "set:upstream", "period": 12}
    if change in kwargs:
        kwargs[change] = {"owner": "b", "source_id": "renamed:upstream", "period": 13}[change]
    elif change == "target":
        scenario["target_id"] = "another-enemy"
    elif change == "evidence":
        scenario["source"] += "; revised evidence"
    else:
        # Exact reference identity is part of semantic acceptance even if numbers happen to remain equal.
        contract = api().REFERENCE_CONTRACT
        monkeypatch.setitem(contract, "contract_fingerprint", "0" * 64)
    unexecuted(project(data, scenario, accept=False, **kwargs))


@pytest.mark.parametrize("place", ["root", "sibling", "listener", "modifier"])
def test_review_untested_control_paths_cannot_be_removed_to_make_source_ready(place):
    data = bundle()
    raw = data["abilities"][0]["payload"]
    mods = raw["GPPBBDEICKP"]
    if place == "root":
        raw["unreviewedCallback"] = [{"$type": "ApplyModifier"}]
    elif place == "sibling":
        mods["unreviewed_sibling"] = {"unknown": 1}
    elif place == "listener":
        mods["UNIQUE_Relic_ReactionWindEnhance_Handler"]["LFIJIMIMBIH"][1]["extraControl"] = True
    else:
        mods["UNIQUE_Relic_ElementWaterDefDown"]["sideEffect"] = []
    rehash(data)
    unexecuted(project(data, accept=False))


def test_review_source_cache_is_not_shared_by_returned_mutable_receipts():
    first = project()
    first["rules"][0]["effects"][0]["value"] = 99
    first["parts"][0]["rule"] = {"kind": "constant", "value": 99}
    second = project()
    assert all(r["effects"][0]["value"] == SHRED for r in second["rules"])
    assert next(p for p in second["parts"] if p["part_id"] == "upstream.swirl_bonus")["rule"]["value"] == BONUS


def test_review_compiled_double_packet_has_two_independent_one_layer_shreds():
    values, _ = lower(freeze_setup())
    out = result(values)
    assert damages(out) == pytest.approx([45, 45, 45, SWIRL, SWIRL])
    rows = activations(out)
    assert len(rows) == 2
    assert all(x["expires_at"] == [10] and x["layers"] == 1 for x in rows)


def test_review_later_hydro_only_refresh_cannot_extend_cryo_or_stack_either():
    t, r, m = freeze_setup(("hydro", "cryo", "anemo", "hydro", "anemo", "cryo", "hydro", "hydro"),
                          (2, 1, 2, 1, 1, 1, 1, 1), (0, 0, 0, 2, 2, 10, 10, 12))
    t["loop_duration"] = r["period"] = 13
    for element in ("cryo", "hydro"):
        m["elemental_model"]["profiles"].append({"id": element + "-none", "element": element, "application": "none", **EVIDENCE})
    for h, d in zip(t["hits"][5:], m["hit_events"][5:], strict=True):
        d["application"]["profile_id"] = h["element"] + "-none"
    values, compiled = lower((t, r, m))
    out = result(values)
    assert damages(out) == pytest.approx([45, 45, 45, SWIRL, SWIRL, CLASSIC_SHRED, 45, SWIRL,
                                         45, CLASSIC_SHRED, 45])
    elements = {rule["id"]: rule["trigger"]["reaction_elements"][0] for rule in compiled["rules"]}
    assert [(elements[x["rule_id"]], x["t"], x["expires_at"], x["layers"]) for x in activations(out)] == [
        ("hydro", 0, [10], 1), ("cryo", 0, [10], 1), ("hydro", 2, [12], 1)]


def test_review_actual_reaction_refreshes_even_if_damage_icd_blocks_child():
    values, _ = lower(cryo_wind((.2, .3, .4)))
    out = result(values)
    assert damages(out) == pytest.approx([45, SWIRL, 45, SWIRL, 45])
    assert [x["expires_at"] for x in activations(out)] == [[10.2], [10.3], [10.4]]
    assert all(x["layers"] == 1 for x in activations(out))


@pytest.mark.parametrize("case", ["miss", "none", "empty", "icd"])
def test_review_no_reaction_is_not_a_refresh(case):
    t, r, m = cryo_wind((.2, .3))
    if case == "miss":
        m["hit_events"][1]["application"]["landed"] = False
    elif case == "none":
        m["elemental_model"]["profiles"].append({"id": "no-wind", "element": "anemo", "application": "none", **EVIDENCE})
        m["hit_events"][1]["application"]["profile_id"] = "no-wind"
    elif case == "empty":
        m["elemental_model"]["target"]["initial_state"]["ordinary_aura"] = None
    else:
        p = next(p for p in m["elemental_model"]["profiles"] if p["id"] == "anemo1")
        p["icd"] = {"kind": "standard", "group": "wind-shared"}
    values, _ = lower((t, r, m))
    out = result(values)
    assert len(activations(out)) == (0 if case == "empty" else 1)


@pytest.mark.parametrize("other_actor", [False, True])
def test_review_static_bonus_owner_and_dynamic_frontline_gate_are_different(other_actor):
    t, r, m = cryo_wind()
    second_character(t, r)
    if other_actor:
        r["abilities"][0]["character"] = "b"
        r["initial_frontline"] = "b"
        t["hits"][0]["character"] = "b"
    switch_from, switch_to = ("b", "a") if other_actor else ("a", "b")
    r["abilities"].append(ability("switch", switch_from, "action.switch"))
    r["actions"].append(action("swap", "switch", .15, .15, 5, switch_to=switch_to, repeat={"mode": "once"}))
    values, _ = lower((t, r, m))
    out = result(values)
    assert damages(out) == pytest.approx([90, 781.299] if other_actor else [45, STATIC_SWIRL])
    assert not activations(out)


def test_review_enemy_expiry_is_half_open_even_with_early_self_snapshot():
    t, r, m = cryo_wind((.2, 10.199999, 10.2))
    t["loop_duration"] = r["period"] = 11
    m["elemental_model"]["profiles"].append({"id": "cryo-none", "element": "cryo", "application": "none", **EVIDENCE})
    for hit, descriptor in zip(t["hits"][1:], m["hit_events"][1:], strict=True):
        hit["element"] = "cryo"
        descriptor["application"]["profile_id"] = "cryo-none"
        descriptor["self_snapshot_event"] = "cast"
        descriptor["contact"] = SAFE_CONTACT.copy()
    values, _ = lower((t, r, m))
    out = result(values)
    assert damages(out) == pytest.approx([45, SWIRL, CLASSIC_SHRED, 45])
    assert len(activations(out)) == 1
