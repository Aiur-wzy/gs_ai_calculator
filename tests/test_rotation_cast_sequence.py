from __future__ import annotations

import copy

from genshin_engine.rotation import check_rotation, validate_rotation_model


def evidence():
    return {"status": "confirmed", "source": "generic cast-sequence regression fixture"}


def repeat(mode="once"):
    return {"mode": mode}


def ability(identifier, stage, *, cooldown=None, window=None, max_stages=3):
    if window is None and stage < max_stages:
        window = 4
    return {
        "id": identifier,
        "character": "actor",
        "action_type_id": "action.elemental_skill",
        "cooldown": cooldown,
        "energy": None,
        "cast_sequence": {
            "group": "three-stage-skill",
            "stage": stage,
            "max_stages": max_stages,
            "continuation_window": window,
            "cooldown_start": "sequence_start",
        },
        **evidence(),
    }


def action(identifier, ability_id, start, order):
    return {
        "id": identifier,
        "ability_id": ability_id,
        "start": start,
        "occupation_end": round(start + .1, 6),
        "order": order,
        "repeat": repeat(),
        **evidence(),
    }


def case(times=(0, 3.9, 7.8)):
    cooldown = {
        "group": "base-skill",
        "duration": 10,
        "start": "action_start",
        "charges": 1,
        "recovery": "sequential",
    }
    model = {
        "schema_version": 1,
        "period": 12,
        "max_cycles": 3,
        "initial_frontline": "actor",
        "characters": [{
            "id": "actor", "element": "pyro", "energy_recharge": 1,
            "energy_capacity": 0, "initial_energy": 0, **evidence(),
        }],
        "abilities": [
            ability("skill-stage-1", 1, cooldown=cooldown),
            ability("skill-stage-2", 2),
            ability("skill-stage-3", 3),
        ],
        "actions": [
            action("stage-1", "skill-stage-1", times[0], 0),
            action("stage-2", "skill-stage-2", times[1], 1),
            action("stage-3", "skill-stage-3", times[2], 2),
        ],
        "events": [],
        "initial_cooldowns": [{
            "character": "actor", "group": "base-skill", "ready_in": [],
        }],
    }
    template = {
        "version": 2,
        "characters": [{"id": "actor"}],
        "hits": [],
        "switches": [],
        "loop_duration": 12,
    }
    return template, model


def row(result, identifier):
    return next(item for item in result["event_log"] if item["id"] == identifier)


def test_three_stage_skill_continues_while_base_cooldown_is_running():
    template, model = case()
    result = check_rotation(template, model)

    assert result["status"] == "unproven"
    first, second, third = (row(result, identifier) for identifier in
                            ("stage-1", "stage-2", "stage-3"))
    assert first["next_ready_at"] == 10
    assert first["cast_sequence"] == {
        "group": "three-stage-skill", "stage": 1, "max_stages": 3,
        "cooldown_start": "sequence_start", "admission": "sequence_start",
        "base_cooldown": {"group": "base-skill", "running": True, "ready_at": 10},
        "next_stage": 2,
        "continuation_expires_at": 4,
    }
    assert second["status"] == "passed"
    assert second["cast_sequence"]["admission"] == "continuation_window"
    assert second["cast_sequence"]["base_cooldown"] == {
        "group": "base-skill", "running": True, "ready_at": 10,
    }
    assert second["cast_sequence"]["continuation_expires_at"] == 7.9
    assert third["status"] == "passed"
    assert third["cast_sequence"]["completed"] is True
    assert "next_ready_at" not in second and "next_ready_at" not in third
    assert result["cycles"][0]["boundary_state"]["cooldown_ready_in"] == {
        '["actor","base-skill"]': [],
    }
    assert result["cycles"][0]["boundary_state"]["cast_sequences"] == {}


def test_continuation_window_is_refreshed_by_each_successful_stage():
    template, model = case(times=(0, 3.9, 7.8))
    result = check_rotation(template, model)
    assert row(result, "stage-3")["status"] == "passed"


def test_followups_have_no_extra_cooldown_beyond_action_occupancy():
    template, model = case(times=(0, .1, .2))
    result = check_rotation(template, model)

    assert [row(result, identifier)["status"] for identifier in
            ("stage-1", "stage-2", "stage-3")] == ["passed", "passed", "passed"]
    assert row(result, "stage-2")["cast_sequence"]["admission"] == "continuation_window"
    assert row(result, "stage-3")["cast_sequence"]["completed"] is True


def test_each_transition_can_have_its_own_continuation_window():
    template, model = case(times=(0, 3.9, 5.8))
    model["abilities"][1]["cast_sequence"]["continuation_window"] = 2
    result = check_rotation(template, model)

    assert row(result, "stage-2")["cast_sequence"]["continuation_expires_at"] == 5.9
    assert row(result, "stage-3")["status"] == "passed"


def test_expired_continuation_is_rejected_without_restarting_cooldown():
    template, model = case(times=(0, 4, 4.5))
    result = check_rotation(template, model)
    second = row(result, "stage-2")

    assert result["status"] == "failed"
    assert second["status"] == "failed"
    assert second["cast_sequence"] == {
        "group": "three-stage-skill", "stage": 2, "max_stages": 3,
        "expected_stage": 1,
    }
    assert any(issue["code"] == "cast_sequence_unavailable" for issue in result["issues"])
    assert row(result, "stage-3")["status"] == "not_verified"


def test_sequence_rejects_skipping_a_stage():
    template, model = case(times=(0, 1, 2))
    model["actions"] = [model["actions"][0], model["actions"][2]]
    result = check_rotation(template, model)

    third = row(result, "stage-3")
    assert third["status"] == "failed"
    assert third["cast_sequence"]["expected_stage"] == 2
    assert any(issue["code"] == "cast_sequence_stage" for issue in result["issues"])


def test_sequence_completion_does_not_grant_an_early_fourth_cast():
    template, model = case(times=(0, 1, 2))
    model["actions"].append(action("early-new-stage-1", "skill-stage-1", 2.5, 3))
    result = check_rotation(template, model)

    fourth = row(result, "early-new-stage-1")
    assert fourth["status"] == "failed"
    assert fourth["next_ready_at"] == 10
    assert any(issue["code"] == "cooldown_unavailable" for issue in result["issues"])


def test_new_sequence_can_start_when_first_stage_cooldown_finishes():
    template, model = case(times=(0, 1, 2))
    model["actions"].append(action("next-stage-1", "skill-stage-1", 10, 3))
    result = check_rotation(template, model)

    next_first = row(result, "next-stage-1")
    assert next_first["status"] == "passed"
    assert next_first["cast_sequence"]["base_cooldown"]["ready_at"] == 20
    assert next_first["cast_sequence"]["continuation_expires_at"] == 14


def test_sequence_definition_requires_one_cooldown_owner_and_complete_stages():
    _, model = case()
    model["abilities"][1]["cooldown"] = copy.deepcopy(model["abilities"][0]["cooldown"])
    model["abilities"].pop()
    validation = check_rotation(None, model)

    assert validation["status"] == "invalid"
    codes = {issue["code"] for issue in validation["issues"]}
    assert {"cast_sequence_cooldown", "cast_sequence_stage"} <= codes


def test_cast_sequence_schema_rejects_unknown_cooldown_policy():
    _, model = case()
    model["abilities"][0]["cast_sequence"]["cooldown_start"] = "sequence_complete"
    result = validate_rotation_model(model)
    assert result["status"] == "invalid"


def test_final_stage_requires_null_continuation_window():
    template, model = case()
    assert model["abilities"][2]["cast_sequence"]["continuation_window"] is None
    model["abilities"][2]["cast_sequence"]["continuation_window"] = 4

    result = check_rotation(template, model)
    assert result["status"] == "invalid"
    assert any(issue["code"] == "cast_sequence_window" for issue in result["issues"])


def source_binding(stage):
    return {
        "contract": "cast-sequence-source-binding-v1",
        "profile_id": "reviewed-generic-three-stage-v1",
        "character_id": 90000001,
        "selected_skill_id": 91000 + stage,
        "selected_stage": stage,
        "version_line": "release",
        "snapshot_id": "a" * 64,
        "upstream_commit": "b" * 40,
        "content_fingerprint": "c" * 64,
        "resource_fingerprint": "d" * 64,
        "sequence_cooldown_seconds": 10,
        "sequence_cooldown_charges": 1,
        "sequence_cooldown_recovery": "sequential",
        "verification_mode": "saved_metadata_only",
    }


def bind_sources(model):
    for stage, row in enumerate(model["abilities"], 1):
        row["cast_sequence"]["source_binding"] = source_binding(stage)


def test_saved_source_binding_round_trips_without_becoming_execution_authority():
    template, model = case(times=(0, .1, .2))
    bind_sources(model)
    before = copy.deepcopy(model)

    assert validate_rotation_model(model)["status"] == "valid"
    result = check_rotation(template, model)

    assert result["status"] == "unproven"
    assert row(result, "stage-3")["status"] == "passed"
    assert model == before
    assert model["abilities"][1]["cast_sequence"]["source_binding"]["verification_mode"] == "saved_metadata_only"


def test_saved_source_binding_rejects_wrong_first_stage_cooldown():
    template, model = case()
    bind_sources(model)
    model["abilities"][0]["cooldown"]["duration"] = 8

    result = check_rotation(template, model)

    assert result["status"] == "invalid"
    assert any(issue["code"] == "cast_sequence_source_binding" for issue in result["issues"])


def test_saved_source_binding_requires_complete_auditable_shape():
    _, model = case()
    bind_sources(model)
    del model["abilities"][0]["cast_sequence"]["source_binding"]["resource_fingerprint"]

    result = validate_rotation_model(model)

    assert result["status"] == "invalid"
    assert any(issue["path"].endswith("source_binding.resource_fingerprint")
               for issue in result["issues"])
