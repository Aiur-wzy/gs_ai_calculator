"""EC08 bounded condition work; tiny limits exercise real full-window dispatch."""
import copy
import json

import pytest
from test_mechanics_execution import fixture, second_character
from test_target_state_mechanics import fixed_inputs

from genshin_engine import mechanics


def limited(monkeypatch, data, limit, matches=None):
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_CHECKS", limit)
    before = copy.deepcopy(data)
    out = mechanics.execute_mechanics(*data, matches=matches)
    assert data == before
    return out


def assert_stopped(out, limit):
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    issue = next(i for i in out["issues"] if i["code"] == "target_condition_limit")
    assert issue["limit"] == issue["checks_completed"] == limit
    assert len(out["target_condition_trace"]) == limit
    assert len([r for r in out["mechanics_trace"] if r["type"] == "target_condition"]) == limit


def test_exact_limit_finishes_but_next_check_never_publishes_partial_damage(monkeypatch):
    data = fixed_inputs(("cryo", "physical"))
    out = limited(monkeypatch, data, 4)
    assert out["status"] in ("passed", "unproven") and out["result"] is not None
    assert len(out["target_condition_trace"]) == 4
    out = limited(monkeypatch, data, 3)
    assert_stopped(out, 3)
    assert any(r["type"] == "hit" for r in out["mechanics_trace"])


@pytest.mark.parametrize("mode", ["filter", "owner", "miss", "include", "exclude", "unreported"])
def test_structural_rejections_and_agent_tuning_still_spend_the_budget(monkeypatch, mode):
    data = fixed_inputs(("physical", "physical"))
    source = data[2]["fixed_effect_sources"][0]
    matches = None
    if mode == "filter":
        for effect in source["effects"]:
            effect["filter"]["elements"] = ["cryo"]
    elif mode == "owner":
        second_character(data[0], data[1])
        source["owner"] = "b"
    elif mode == "miss":
        for descriptor in data[2]["hit_events"]:
            descriptor["application"]["landed"] = False
    elif mode == "unreported":
        for descriptor in data[2]["hit_events"]:
            descriptor["report"] = False
    else:
        matches = [{"fixed_source_id": source["id"], "effect_id": effect["id"], "hit_id": hit,
                    "operation": mode} for effect in source["effects"] for hit in ("h0", "h1")]
    assert_stopped(limited(monkeypatch, data, 3, matches), 3)


def test_check_budget_never_resets_at_rotation_boundary(monkeypatch):
    data = fixed_inputs(("physical",), (.2,))
    data[1]["actions"][0]["repeat"] = {"mode": "each_cycle"}
    out = limited(monkeypatch, data, 3)
    assert_stopped(out, 3)
    assert [r["cycle"] for r in out["target_condition_trace"]] == [0, 0, 1]


def test_zero_mock_budget_leaves_no_condition_rows_and_old_path_unchanged(monkeypatch):
    assert_stopped(limited(monkeypatch, fixed_inputs(("physical",)), 0), 0)
    legacy = fixture()
    baseline = mechanics.execute_mechanics(*legacy)
    result = limited(monkeypatch, legacy, 0)
    assert result == baseline
    assert "target_condition_trace" not in result


def test_budget_is_public_and_does_not_replace_the_event_limit():
    caps = mechanics.mechanics_capabilities()
    assert caps["target_state_conditions"]["limits"]["max_target_condition_checks"] == 20000
    assert caps["target_state_conditions"]["limits"]["max_trace_bytes"] == 8 * 1024 * 1024
    assert caps["limits"]["max_expanded_events"] == 40000


def ledger_bytes(rows):
    return len(json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8"))


def test_trace_byte_budget_includes_utf8_brackets_and_commas(monkeypatch):
    data = fixed_inputs(("cryo", "physical"))
    data[2]["fixed_effect_sources"][0]["source"] = "完整中文依据，不能裁剪"
    baseline = mechanics.execute_mechanics(*data)
    full_bytes = ledger_bytes(baseline["target_condition_trace"])
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_TRACE_BYTES", full_bytes)
    assert mechanics.execute_mechanics(*data)["result"] is not None
    prefix = baseline["target_condition_trace"][:1]
    prefix_bytes = ledger_bytes(prefix)
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_TRACE_BYTES", prefix_bytes)
    out = mechanics.execute_mechanics(*data)
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert out["target_condition_trace"] == prefix
    issue = next(i for i in out["issues"] if i["code"] == "target_condition_trace_limit")
    assert issue["limit"] == issue["bytes_used"] == prefix_bytes
    assert issue["attempted_bytes"] == ledger_bytes(baseline["target_condition_trace"][:2])
    assert issue["checks_attempted"] == 2


def test_long_first_receipt_is_rejected_before_deepcopy_or_append(monkeypatch):
    data = fixed_inputs(("physical",))
    data[2]["fixed_effect_sources"][0]["source"] = "长依据" * 500
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_TRACE_BYTES", 512)
    original_copy = copy.deepcopy

    def checked_copy(value, *args, **kwargs):
        assert not (isinstance(value, dict) and value.get("type") == "target_condition"), "oversized receipt was copied"
        return original_copy(value, *args, **kwargs)

    monkeypatch.setattr(mechanics.copy, "deepcopy", checked_copy)
    out = mechanics.execute_mechanics(*data)
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert out["target_condition_trace"] == []
    issue = next(i for i in out["issues"] if i["code"] == "target_condition_trace_limit")
    assert issue["bytes_used"] == 2 and issue["attempted_bytes"] > 512 and issue["checks_attempted"] == 1
    assert "长依据" not in json.dumps(issue, ensure_ascii=False)
