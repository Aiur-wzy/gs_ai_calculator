"""EC06 reference integration cases; abstract controlled actions, not team claims."""
import copy

import pytest
from test_mechanics_execution import add, fixture, rule

from genshin_engine.elemental import ElementalRuntime, validate_elemental_model
from genshin_engine.elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT
from genshin_engine.mechanics import execute_mechanics

EVIDENCE = {"status": "assumed", "source": "抽象测试来源；明确接受参考模型"}
CONTACT = {"kind": "none", **EVIDENCE}


def model(aura=None):
    return {"schema_version": 1, "ruleset": FREEZE_RULESET,
            "reference_acceptance": {**{k: REFERENCE_CONTRACT[k] for k in ("contract_id", "contract_fingerprint")},
                                     "acknowledged": True, "source": EVIDENCE["source"]},
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", **EVIDENCE,
                       "initial_state": {"ordinary_aura": aura, "frozen": None, "freeze_history_seconds": 0, **EVIDENCE},
                       "freeze": {"freezable": True, "resistance": 0, **EVIDENCE}},
            "profiles": [{"id": e, "element": e, "application": "apply", "gauge_units": 1,
                          "icd": {"kind": "none"}, **EVIDENCE} for e in ("hydro", "cryo", "anemo")]
                         + [{"id": "physical", "element": "physical", "application": "none", **EVIDENCE}],
            "contact_profiles": [],
            "swirl_damage": {"icd": {"kind": "sequence", "reset_seconds": .5, "sequence": [1, 1, 0]}, **EVIDENCE}}


def inputs(elements=("hydro", "cryo", "anemo"), times=(0, .1, .2), aura=None):
    template, rotation, mechanics = fixture(times)
    rotation["max_cycles"] = 3
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    mechanics["elemental_model"] = model(aura)
    for hit, descriptor, element in zip(template["hits"], mechanics["hit_events"], elements, strict=True):
        hit["element"] = element
        descriptor.update(application={"profile_id": element, "target_id": "enemy", "landed": True}, contact=CONTACT)
    return template, rotation, mechanics


def execute(data):
    original = copy.deepcopy(data)
    output = execute_mechanics(*data)
    assert data == original
    assert output["result"] is not None, output["issues"]
    return output


def test_continuous_reference_integration_and_expiry():
    output = execute(inputs())
    applications = [r for r in output["elemental_trace"] if r["type"] == "elemental_application"]
    assert applications[1]["reactions"][0]["type"] == "freeze"
    assert applications[2]["reactions"][0]["type"] == "swirl"
    assert any(r["reason"] == "frozen_expired" for r in output["elemental_trace"] if r["type"] == "elemental_state_transition")
    hits = output["result"]["hits"]
    assert hits[-1]["id"] == "h2:reaction_damage:swirl:cryo"
    assert hits[-1]["expected"] == pytest.approx(781.299)
    assert output["result"]["elemental"]["evidence_tier"] == "experimental_reference"
    assert output["rotation_check"]["cycles"][0]["executed_action_count"] == 1


def test_contact_on_first_freeze_is_required_and_does_not_publish_damage():
    data = inputs()
    del data[2]["hit_events"][1]["contact"]
    output = execute_mechanics(*data)
    assert output["status"] == "pending" and output["result"] is None
    assert output["issues"][-1]["code"] == "freeze_contact_unknown"


def test_unknown_acceptance_is_pending():
    data = inputs()
    data[2]["elemental_model"]["reference_acceptance"]["acknowledged"] = False
    output = execute_mechanics(*data)
    assert output["status"] == "pending" and output["result"] is None


def test_public_initial_history_is_not_supported():
    data = model()
    data["target"]["initial_state"]["freeze_history_seconds"] = 1
    assert validate_elemental_model(data)


def double_inputs():
    # Same-time H=.8 -> F1.6 -> H.8 -> W2 crosses H then F.
    data = inputs(("hydro", "cryo", "hydro", "anemo"), (0, 0, 0, 0))
    data[2]["elemental_model"]["profiles"][2]["gauge_units"] = 2
    return data


def test_double_swirl_ids_and_independent_damage():
    out = execute(double_inputs())
    hits = [h for h in out["result"]["hits"] if h["damage_system"] == "transformative"]
    assert [h["id"] for h in hits] == ["h3:reaction_damage:swirl:hydro", "h3:reaction_damage:swirl:cryo"]
    assert [h["expected"] for h in hits] == pytest.approx([781.299, 781.299])


@pytest.mark.parametrize("cross", [False, True])
def test_package_own_shred_or_cross_reject(cross):
    data = double_inputs()
    for element in ("hydro", "cryo"):
        effect_element = ({"hydro": "cryo", "cryo": "hydro"}[element] if cross else element)
        add(data[2], rule(element, "benefit.res_shred."+effect_element, .4, domain="enemy",
            trigger={"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"], "reaction_elements": [element]}))
    if cross:
        output = execute_mechanics(*data)
        assert output["status"] == "pending" and output["result"] is None
        assert output["issues"][-1]["code"] == "freeze_package_effect_ambiguous"
    else:
        output = execute(data)
        assert sum(h["expected"] for h in output["result"]["hits"] if h["damage_system"] == "transformative") == pytest.approx(1996.653)


def test_direct_runtime_preview_leaves_icd_unchanged():
    runtime = ElementalRuntime(model({"element": "hydro", "remaining_gu": .5, "decay_gu_per_second": .1, "source": "test"}))
    runtime.profiles["cryo"]["icd"] = {"kind": "standard", "group": "g"}
    before = copy.deepcopy(runtime.icd)
    runtime.preview_copy().attempt(event_id="h", action_id="a", actor="a", time=0,
        application={"profile_id": "cryo", "target_id": "enemy", "landed": True}, contact=CONTACT)
    assert runtime.icd == before


def test_expiry_transitions_share_total_event_limit(monkeypatch):
    monkeypatch.setattr("genshin_engine.mechanics.MAX_MECHANICS_EVENTS", 4)
    output = execute_mechanics(*inputs(("cryo",), (0,)))
    assert output["status"] == "invalid" and output["result"] is None
    assert any("总事件数" in issue["message"] for issue in output["issues"])


def test_far_future_deadline_is_not_enqueued_or_overflowed():
    aura = {"element": "cryo", "remaining_gu": 3.2, "decay_gu_per_second": 1e-308, "source": "finite slow decay boundary"}
    output = execute(inputs(("physical",), (0,), aura))
    assert not any(e["type"] == "elemental_state_deadline" for e in output["rotation_check"]["event_log"])
    assert output["result"]["elemental"]["final_checked_state"]["ordinary_aura"]["remaining_gu"] == 3.2
