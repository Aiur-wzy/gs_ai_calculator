"""Exact legacy comparison plus independent checks of new read-only evidence."""
from __future__ import annotations

from genshin_app.damage_explanation_reporting import validate_damage_explanations
from tests.test_mechanics_scheduling import pre_observation_output


def reaction_observation_view(output, rotation):
    """Use only the already-reviewed scheduling observation boundary.

    Seven complete fixture executions were independently matched against the
    isolated 01eef88 engine before this adapter was adopted. No event, timing,
    numeric zone, resource balance or engineering work counter is normalized.
    """
    result = output["result"]
    assert validate_damage_explanations(result) == "complete"
    document = result["damage_explanations"]
    assert document["coverage"]["eligible"] == len(result["hits"])
    assert document["coverage"]["omitted"] == 0
    assert all(hit["explanation_ref"] in document["events"] for hit in result["hits"])
    actions = {action["id"]: action for action in rotation["actions"]}
    for row in output["mechanics_trace"]:
        if row.get("type") not in ("hit", "reaction_damage"):
            continue
        assert {"action_id", "ability_id", "run_id"} <= row.keys()
        if row["action_id"] is None:
            assert row["ability_id"] is None
        else:
            assert row["ability_id"] == actions[row["action_id"]]["ability_id"]
        assert row["run_id"] is None or isinstance(row["run_id"], str)
    return pre_observation_output(output)
