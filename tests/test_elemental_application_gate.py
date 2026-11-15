"""A periodic application cadence is distinct from a miss and from ICD."""
from __future__ import annotations

from test_mechanics_execution import fixture

from genshin_engine.elemental import ElementalRuntime
from genshin_engine.elemental_schema import elemental_capabilities
from genshin_engine.mechanics import execute_mechanics

EVIDENCE = {"status": "confirmed", "source": "application gate fixture"}


def _model():
    return {
        "schema_version": 1,
        "ruleset": "gauge-basic-v1",
        "target": {
            "id": "enemy", "kind": "ordinary", "knowledge": "known",
            "initial_aura": None, **EVIDENCE,
        },
        "profiles": [{
            "id": "cryo-periodic", "element": "cryo",
            "application": "apply", "gauge_units": 1,
            "icd": {"kind": "sequence", "group": "periodic-shared",
                    "reset_seconds": 10, "sequence": [1, 0, 1]},
            **EVIDENCE,
        }],
    }


def _attempt(runtime, ordinal, *, attempted=True, landed=True):
    return runtime.attempt(
        event_id=f"hit-{ordinal}", action_id="cast", actor="actor",
        time=ordinal * 100_000,
        application={
            "profile_id": "cryo-periodic", "target_id": "enemy",
            "landed": landed, "attempted": attempted,
        },
    )


def test_landed_cadence_gap_does_not_advance_icd_or_change_aura():
    runtime = ElementalRuntime(_model())
    first = _attempt(runtime, 1)
    before = runtime.state()
    skipped = _attempt(runtime, 2, attempted=False)
    after = runtime.state()
    third = _attempt(runtime, 3)

    assert first["application_allowed"] is True
    assert skipped["application_attempted"] is False
    assert skipped["application_allowed"] is False
    assert skipped["reason"] == "application_schedule_blocked"
    assert skipped["icd_audit"]["consumed"] is False
    # Time still advances, so ordinary aura/ICD timers decay.  The skipped
    # contact must neither refresh the aura source nor advance the counter.
    assert after["aura"]["source"] == before["aura"]["source"] == "hit-1"
    assert after["aura"]["element"] == before["aura"]["element"] == "cryo"
    assert after["aura"]["remaining_gu"] < before["aura"]["remaining_gu"]
    assert after["icd"][0]["hits"] == before["icd"][0]["hits"] == 1
    assert after["icd"][0]["anchor_t"] == before["icd"][0]["anchor_t"]
    assert third["icd_audit"]["attempt_number"] == 2
    assert third["application_allowed"] is False


def test_omitted_gate_remains_attempted_and_miss_is_still_distinct():
    runtime = ElementalRuntime(_model())
    implicit = runtime.attempt(
        event_id="implicit", action_id="cast", actor="actor", time=0,
        application={"profile_id": "cryo-periodic", "target_id": "enemy", "landed": True},
    )
    missed = _attempt(runtime, 1, attempted=False, landed=False)
    assert implicit["application_attempted"] is True
    assert missed["reason"] == "missed"
    assert missed["application_attempted"] is False


def test_mechanics_keeps_damage_for_landed_gate_blocked_contact():
    template, rotation, mechanics = fixture((.2, .3))
    mechanics["elemental_model"] = _model()
    for index, (hit, descriptor) in enumerate(
            zip(template["hits"], mechanics["hit_events"], strict=True)):
        hit["element"] = "cryo"
        descriptor["application"] = {
            "profile_id": "cryo-periodic", "target_id": "enemy",
            "landed": True, "attempted": index == 0,
        }
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] in ("passed", "unproven"), output["issues"]
    assert len(output["result"]["hits"]) == 2
    assert [row["reason"] for row in output["elemental_trace"][:2]] == [
        "first_hit", "application_schedule_blocked",
    ]
    assert output["elemental_trace"][1]["after"]["icd"][0]["hits"] == 1


def test_capability_publishes_optional_gate_without_mutable_leak():
    caps = elemental_capabilities()
    application = caps["schema"]["properties"]["profiles"]
    assert caps["application_gate_contract"]["default"] is True
    # The event application object is shared through mechanics_schema; the
    # contract itself is the public discoverable statement for producers.
    assert "application.attempted" == caps["application_gate_contract"]["field"]
    caps["application_gate_contract"]["default"] = False
    assert elemental_capabilities()["application_gate_contract"]["default"] is True
    assert application["type"] == "array"
