from __future__ import annotations

import json

import pytest

from genshin_app.agent_chat import native_turn_budget as budget
from genshin_app.cli import main
from genshin_app.equipment.schema import PlanError


def _cli(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    stream = captured.out if code == 0 else captured.err
    return code, json.loads(stream)


def test_defaults_are_versioned_detached_and_freezable(tmp_path):
    first = budget.load_native_turn_budget_policy(tmp_path)
    second = budget.default_budget_policy()

    assert first["contract"] == budget.POLICY_CONTRACT
    assert first["policy_id"] == "complete_table_v1"
    assert first["policy_version"] == 6
    assert first["generation"] == 0
    assert first["follows_defaults"] is True
    assert first["apply_scope"] == "next_turn"
    assert first["effective"]["wall_clock_seconds"] == 7200
    assert first["effective"]["provider_steps"] == 4096
    assert first["effective"]["cumulative_input_hard_tokens"] == 256_000_000
    assert first["effective"]["retry_count"] == 0
    assert first["limits"]["retry_count"]["adjustable"] is False
    assert not budget.budget_policy_path(tmp_path).exists()

    first["effective"]["wall_clock_seconds"] = 60
    first["limits"]["wall_clock_seconds"]["max"] = 60
    assert second["effective"]["wall_clock_seconds"] == 7200
    assert second["limits"]["wall_clock_seconds"]["max"] == 86400

    frozen = budget.freeze_turn_budget(second)
    assert set(frozen) == {
        "contract", "policy_id", "policy_version", "generation", "effective"
    }
    assert frozen["contract"] == budget.TURN_SNAPSHOT_CONTRACT
    frozen["effective"]["wall_clock_seconds"] = 60
    assert second["effective"]["wall_clock_seconds"] == 7200


def test_sparse_replace_persists_atomically_and_reset_follows_defaults(tmp_path):
    updated = budget.update_native_turn_budget_policy(
        tmp_path,
        {"wall_clock_seconds": 14400, "provider_steps": 8192},
        expected_generation=0,
        source="cli",
    )
    assert updated["generation"] == 1
    assert updated["overrides"] == {
        "wall_clock_seconds": 14400,
        "provider_steps": 8192,
    }
    assert updated["effective"]["app_tool_calls"] == 3072
    assert updated["source"] == "cli"
    stored = json.loads(budget.budget_policy_path(tmp_path).read_text(encoding="utf-8"))
    assert stored["overrides"] == updated["overrides"]
    assert "effective" not in stored and "defaults" not in stored

    # The submitted map is a complete replacement, and values equal to the
    # current default are removed rather than pinned.
    replaced = budget.update_native_turn_budget_policy(
        tmp_path,
        {"wall_clock_seconds": 7200},
        expected_generation=1,
        source="gui",
    )
    assert replaced["generation"] == 2
    assert replaced["overrides"] == {}
    assert replaced["follows_defaults"] is True

    reset = budget.reset_native_turn_budget_policy(
        tmp_path, expected_generation=2, source="gui"
    )
    assert reset["generation"] == 3
    assert reset["effective"] == budget.DEFAULT_BUDGET
    assert reset["apply_scope"] == "next_turn"


def test_version_one_sparse_overrides_follow_current_defaults_without_rewriting(tmp_path):
    updated = budget.update_native_turn_budget_policy(
        tmp_path,
        {"wall_clock_seconds": 14400},
        expected_generation=0,
        source="gui",
    )
    path = budget.budget_policy_path(tmp_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["base_policy_version"] = 1
    path.write_text(
        json.dumps(stored, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    migrated = budget.load_native_turn_budget_policy(tmp_path)

    assert updated["generation"] == migrated["generation"] == 1
    assert migrated["policy_version"] == 6
    assert migrated["overrides"] == {"wall_clock_seconds": 14400}
    assert json.loads(path.read_text(encoding="utf-8"))["base_policy_version"] == 1


def test_new_input_ceiling_keeps_frozen_v5_and_explicit_lower_override(tmp_path):
    old = budget.freeze_turn_budget(tmp_path)
    old["policy_version"] = 5
    old["effective"]["cumulative_input_hard_tokens"] = 128_000_000
    assert budget.freeze_turn_budget(old) == old
    policy = budget.update_native_turn_budget_policy(
        tmp_path, {"cumulative_input_hard_tokens": 128_000_000}, expected_generation=0)
    assert budget.freeze_turn_budget(policy)["effective"]["cumulative_input_hard_tokens"] == 128_000_000
    defaults = budget.reset_native_turn_budget_policy(tmp_path, expected_generation=1)
    assert budget.freeze_turn_budget(defaults)["effective"]["cumulative_input_hard_tokens"] == 256_000_000


def test_obsolete_parallel_budget_override_is_retired_on_read(tmp_path):
    budget.update_native_turn_budget_policy(
        tmp_path, {"wall_clock_seconds": 2400}, expected_generation=0
    )
    path = budget.budget_policy_path(tmp_path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["base_policy_version"] = 3
    stored["overrides"]["parallel_tool_calls"] = 1
    path.write_text(json.dumps(stored), encoding="utf-8")

    migrated = budget.load_native_turn_budget_policy(tmp_path)

    assert "parallel_tool_calls" not in migrated["effective"]
    assert migrated["retired_overrides"] == {"parallel_tool_calls": 1}


def test_generation_conflict_and_invalid_relationship_do_not_mutate(tmp_path):
    first = budget.update_native_turn_budget_policy(
        tmp_path, {"wall_clock_seconds": 2400}, expected_generation=0
    )
    before = budget.budget_policy_path(tmp_path).read_bytes()
    with pytest.raises(PlanError) as conflict:
        budget.update_native_turn_budget_policy(
            tmp_path, {"wall_clock_seconds": 3000}, expected_generation=0
        )
    assert conflict.value.reason == "native_turn_budget_generation_conflict"
    assert budget.budget_policy_path(tmp_path).read_bytes() == before
    assert first["generation"] == 1

    with pytest.raises(PlanError) as invalid:
        budget.update_native_turn_budget_policy(
            tmp_path,
            {"total_tool_calls": 1000, "app_tool_calls": 1200},
            expected_generation=1,
        )
    assert invalid.value.reason == "native_turn_budget_invalid"
    assert budget.budget_policy_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"retry_count": 1}, "不可调整"),
        ({"unknown": 1}, "未知字段"),
        ({"wall_clock_seconds": True}, "整数"),
        ({"context_prune_tokens": 800000, "context_stop_tokens": 700000}, "未知字段"),
        ({"web_tool_calls": 0}, "搜索调用"),
    ],
)
def test_invalid_overrides_fail_closed(tmp_path, overrides, message):
    with pytest.raises(PlanError, match=message) as error:
        budget.update_native_turn_budget_policy(tmp_path, overrides)
    assert error.value.reason == "native_turn_budget_invalid"
    assert not budget.budget_policy_path(tmp_path).exists()


def test_corrupt_store_is_not_silently_replaced(tmp_path):
    path = budget.budget_policy_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"contract":"genshin-native-turn-budget-policy-v1",'
        '"contract":"genshin-native-turn-budget-policy-v1"}',
        encoding="utf-8",
    )
    with pytest.raises(PlanError) as error:
        budget.load_native_turn_budget_policy(tmp_path)
    assert error.value.reason == "native_turn_budget_store_invalid"


def test_frozen_snapshot_boundary_is_exact():
    frozen = budget.freeze_turn_budget(budget.default_budget_policy())
    assert budget.validate_turn_budget_snapshot(frozen) == frozen
    with pytest.raises(PlanError):
        budget.validate_turn_budget_snapshot({**frozen, "extra": True})
    with pytest.raises(PlanError):
        budget.validate_turn_budget_snapshot({**frozen, "generation": True})


def test_cli_show_set_conflict_and_reset(tmp_path, capsys):
    root = ["--data-dir", str(tmp_path), "agent", "budget"]
    code, shown = _cli([*root, "show"], capsys)
    assert code == 0 and shown["data"]["generation"] == 0

    code, changed = _cli(
        [*root, "set", "--expected-generation", "0", "--wall-clock-seconds", "2400"],
        capsys,
    )
    assert code == 0
    assert changed["data"]["overrides"] == {"wall_clock_seconds": 2400}

    code, failed = _cli(
        [*root, "set", "--expected-generation", "0", "--wall-clock-seconds", "3000"],
        capsys,
    )
    assert code == 1
    assert failed["reason"] == "native_turn_budget_generation_conflict"

    code, reset = _cli([*root, "reset", "--expected-generation", "1"], capsys)
    assert code == 0 and reset["data"]["follows_defaults"] is True
    assert reset["data"]["generation"] == 2


def test_cli_set_requires_a_field(tmp_path, capsys):
    code, error = _cli(
        ["--data-dir", str(tmp_path), "agent", "budget", "set"], capsys
    )
    assert code == 1
    assert "至少需要一个" in error["message"]


def test_cli_mutations_use_the_generation_the_command_just_read(tmp_path, capsys):
    root = ["--data-dir", str(tmp_path), "agent", "budget"]
    code, changed = _cli([*root, "set", "--wall-clock-seconds", "2100"], capsys)
    assert code == 0
    assert changed["data"]["generation"] == 1
    assert changed["data"]["overrides"] == {"wall_clock_seconds": 2100}

    code, reset = _cli([*root, "reset"], capsys)
    assert code == 0
    assert reset["data"]["generation"] == 2
    assert reset["data"]["follows_defaults"] is True
