"""Reviewed cast-sequence data is exact and remains algorithm-independent."""
from __future__ import annotations

import copy

import pytest

from genshin_db.runtime_action_resource import (
    _cast_sequence_projection,
    _cast_sequence_recipes,
)
from genshin_db.runtime_catalog import RuntimeConfigError


class Nodes:
    def __init__(self, profile):
        skills = [{} for _ in range(97)]
        for stage in profile["stages"]:
            index = int(stage["skill_pointer"][1:])
            skills[index] = {"id": stage["skill_id"], "abilityName": stage["ability_name"],
                             "cdTime": stage["raw_cooldown_seconds"], "maxChargeNum": 1}
        modifiers = {
            "Switch1To2": {profile["ability_source"]["duration_field"]: profile["stages"][0]["continuation_window"]},
            "Switch2To3": {profile["ability_source"]["duration_field"]: profile["stages"][1]["continuation_window"]},
            "Switch3To0": {profile["ability_source"]["duration_field"]: 0.3},
        }
        self.sources = {
            profile["skill_source"]["source_path"]: (profile["skill_source"], skills),
            profile["ability_source"]["source_path"]: (profile["ability_source"],
                [{}, {"Default": {"GPPBBDEICKP": modifiers}}]),
        }

    def source(self, path):
        origin, payload = self.sources[path]
        return {"source_path": path, "source_sha256": origin["source_sha256"]}, payload


def inputs(stage=1):
    profile = copy.deepcopy(_cast_sequence_recipes()["profiles"][0])
    state = {"version_line": profile["version_line"], "upstream_commit": profile["upstream_commit"],
             "normalization": {"profile_id": profile["normalization_profile_id"]}}
    entry = {"selection": {"character_id": profile["character_id"],
             "skill_depot_id": profile["skill_depot_id"],
             "skill_id": profile["stages"][stage - 1]["skill_id"]}}
    return profile, state, entry


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_reviewed_profile_projects_one_generic_engine_contract(stage):
    profile, state, entry = inputs(stage)
    out = _cast_sequence_projection(Nodes(profile), state, entry)
    assert out["status"] == "ready"
    assert out["value"] == {"group": "Diluc_Fire_Blade", "stage": stage, "max_stages": 3,
                            "continuation_window": 4 if stage < 3 else None,
                            "cooldown_start": "sequence_start"}
    assert [row["cooldown_role"] for row in out["stages"]] == [
        "sequence_start", "continuation_bypasses_active_cooldown", "continuation_bypasses_active_cooldown"]
    assert out["resource_policy"]["sequence_cooldown_seconds"] == 10
    assert out["resource_policy"]["sequence_cooldown_charges"] == 1
    assert out["resource_policy"]["sequence_cooldown_recovery"] == "sequential"
    assert out["resource_policy"]["continuation_deadline_semantics"].startswith("half_open_")
    assert out["resource_policy"]["continuation_window_is_latest_deadline_not_minimum_wait"] is True
    assert out["resource_policy"]["raw_followup_cdtime_is_not_a_minimum_stage_delay"] is True
    assert out["resource_policy"]["terminal_stage_restarts_cooldown"] is False
    assert out["content_fingerprint"]


def test_profile_refuses_changed_window_instead_of_guessing():
    profile, state, entry = inputs()
    nodes = Nodes(profile)
    nodes.sources[profile["ability_source"]["source_path"]][1][1]["Default"]["GPPBBDEICKP"][
        "Switch2To3"][profile["ability_source"]["duration_field"]] = 5
    with pytest.raises(RuntimeConfigError) as exc:
        _cast_sequence_projection(nodes, state, entry)
    assert exc.value.reason == "dataset_invalid"


def test_unreviewed_character_does_not_open_or_infer_sources():
    _, state, entry = inputs()
    entry["selection"]["character_id"] += 1

    class Closed:
        def source(self, _path):
            raise AssertionError("unmatched rows must not read raw sources")

    assert _cast_sequence_projection(Closed(), state, entry) is None
