from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat import native_dsh_settings as settings
from genshin_app.cli import main
from genshin_app.equipment.schema import PlanError


def test_migrated_profile_never_accepts_the_legacy_mirror_as_active_settings(tmp_path):
    profile = settings.default_dsh_profile_root(tmp_path)
    settings.update_native_dsh_settings(profile, {"agentLoop": {"maxParallelToolCalls": 7}}, expected_generation=0)
    old = settings.dsh_settings_path(profile).read_bytes()
    marker = profile / "runtime-profile" / "legacy-settings-migration.json"
    marker.parent.mkdir()
    marker.write_text('{"contract":"genshin-dsh-settings-migration-v1"}', encoding="utf-8")
    for operation in (lambda: settings.load_native_dsh_settings(profile),
                      lambda: settings.update_native_dsh_settings(profile, {"agentLoop": {"maxParallelToolCalls": 9}}),
                      lambda: settings.reset_native_dsh_settings(profile)):
        with pytest.raises(PlanError) as caught:
            operation()
        assert caught.value.reason == "native_dsh_settings_migrated"
        assert caught.value.details["active_owner"] == "dsh_config_editor"
    assert settings.dsh_settings_path(profile).read_bytes() == old


def _cli(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    stream = captured.out if code == 0 else captured.err
    return code, json.loads(stream)


def test_builtin_snapshot_tracks_pinned_dsh_defaults_without_pinning_derived_max_tokens(
    tmp_path,
):
    profile = settings.default_dsh_profile_root(tmp_path)
    snapshot = settings.load_native_dsh_settings(profile)

    assert snapshot["contract"] == "genshin-native-dsh-settings-v1"
    assert snapshot["generation"] == 0
    assert snapshot["follows_defaults"] is True
    assert snapshot["apply_scope"] == "next_runtime"
    assert snapshot["effective"]["agentLoop"]["maxParallelToolCalls"] == 10
    assert snapshot["effective"]["persistence"]["listConcurrency"] == 3
    assert snapshot["effective"]["sessionQuery"]["openAt"] == "startup"
    assert "maxTokens" not in snapshot["defaults"]["compaction"]
    assert snapshot["effective"]["compaction"]["maxTokens"] == 65_536
    assert snapshot["default_sources"]["compaction"]["maxTokens"] == {
        "kind": "dsh_derived_default",
        "package": "@deepseek-ai/dsh-compaction-basic",
        "version": "0.2.2-rc.3",
        "field": "maxTokens",
        "expression": "headroomTokens",
    }
    assert snapshot["field_sources"]["compaction"]["maxTokens"]["kind"] == (
        "dsh_derived_default"
    )
    assert not settings.dsh_settings_path(profile).exists()


def test_sparse_headroom_override_keeps_dsh_max_tokens_derivation(tmp_path):
    profile = settings.default_dsh_profile_root(tmp_path)
    updated = settings.update_native_dsh_settings(
        profile,
        {"compaction": {"headroomTokens": 91_234}},
        expected_generation=0,
    )

    assert updated["overrides"] == {"compaction": {"headroomTokens": 91_234}}
    assert updated["effective"]["compaction"]["headroomTokens"] == 91_234
    assert updated["effective"]["compaction"]["maxTokens"] == 91_234
    assert updated["field_sources"]["compaction"]["headroomTokens"] == {
        "kind": "user_override",
        "source": "gui",
        "field": "headroomTokens",
    }
    stored = json.loads(settings.dsh_settings_path(profile).read_text(encoding="utf-8"))
    assert stored["overrides"] == {"compaction": {"headroomTokens": 91_234}}
    assert "effective" not in stored


def test_empty_and_default_equal_sections_are_removed_from_sparse_store(tmp_path):
    profile = settings.default_dsh_profile_root(tmp_path)
    updated = settings.update_native_dsh_settings(
        profile,
        {
            "compaction": {
                "headroomTokens": 65_536,
                "maxTokens": 65_536,
                "auto": True,
            },
            "agentLoop": {"maxParallelToolCalls": 10},
            "sessionQuery": {},
        },
        expected_generation=0,
    )

    assert updated["overrides"] == {}
    assert updated["follows_defaults"] is True
    stored = json.loads(settings.dsh_settings_path(profile).read_text(encoding="utf-8"))
    assert stored["overrides"] == {}


def test_explicit_compaction_routes_and_operational_settings_round_trip(tmp_path):
    profile = settings.default_dsh_profile_root(tmp_path)
    overrides = {
        "compaction": {
            "summarizationProvider": "deepseek-official",
            "summarizationModel": "deepseek-flash",
            "maxTokens": 32_768,
            "modelPolicies": [{
                "provider": "deepseek-official",
                "model": "deepseek-v4-pro",
                "thresholdRatio": 0.75,
                "retainRatio": 0.12,
            }],
        },
        "toolResultPruner": {
            "thresholdChars": 10_000,
            "headChars": 6_000,
            "tailChars": 2_000,
        },
        "agentLoop": {"maxParallelToolCalls": 12},
        "persistence": {"listConcurrency": 4},
        "sessionQuery": {"openAt": "first-search", "defaultLimit": 30},
        "projectionCache": {"writeEveryEvents": 300, "writeIntervalMs": 7_500},
    }
    updated = settings.update_native_dsh_settings(
        profile, overrides, expected_generation=0, source="cli"
    )

    assert updated["overrides"] == overrides
    assert updated["effective"]["compaction"]["maxTokens"] == 32_768
    assert updated["effective"]["agentLoop"]["maxParallelToolCalls"] == 12
    assert settings.load_native_dsh_settings(profile)["overrides"] == overrides


@pytest.mark.parametrize(
    "overrides",
    [
        {"compaction": {"retainRatio": 0.9}},
        {"compaction": {"summarizationProvider": "deepseek-official"}},
        {"compaction": {"summarizationProvider": "other", "summarizationModel": "x"}},
        {"compaction": {
            "summarizationProvider": "deepseek-official",
            "summarizationModel": "not-installed",
        }},
        {"compaction": {"modelPolicies": [{
            "provider": "deepseek-official",
            "model": "not-installed",
        }]}},
        {"toolResultPruner": {"thresholdChars": 100, "headChars": 90, "tailChars": 90}},
        {"sessionQuery": {"defaultLimit": 101, "maxLimit": 100}},
        {"persistence": {"compression": "none"}},
        {"path": "not-allowed"},
    ],
)
def test_invalid_or_unreviewed_settings_fail_closed(tmp_path, overrides):
    profile = settings.default_dsh_profile_root(tmp_path)
    with pytest.raises(PlanError) as caught:
        settings.update_native_dsh_settings(profile, overrides)
    assert caught.value.reason == "native_dsh_settings_invalid"
    assert not settings.dsh_settings_path(profile).exists()


def test_generation_conflict_and_reset_are_atomic(tmp_path):
    profile = settings.default_dsh_profile_root(tmp_path)
    first = settings.update_native_dsh_settings(
        profile, {"agentLoop": {"maxParallelToolCalls": 8}}, expected_generation=0
    )
    before = settings.dsh_settings_path(profile).read_bytes()
    with pytest.raises(PlanError) as caught:
        settings.update_native_dsh_settings(
            profile, {"agentLoop": {"maxParallelToolCalls": 9}}, expected_generation=0
        )
    assert caught.value.reason == "native_dsh_settings_generation_conflict"
    assert settings.dsh_settings_path(profile).read_bytes() == before

    reset = settings.reset_native_dsh_settings(
        profile, expected_generation=first["generation"]
    )
    assert reset["generation"] == 2
    assert reset["overrides"] == {}
    assert reset["follows_defaults"] is True


def test_cli_show_set_and_reset_use_the_production_profile_root(tmp_path, capsys):
    root = ["--data-dir", str(tmp_path), "agent", "dsh-settings"]
    code, shown = _cli([*root, "show"], capsys)
    assert code == 0 and shown["data"]["generation"] == 0

    input_path = tmp_path / "dsh-overrides.json"
    input_path.write_text(
        json.dumps({"compaction": {"headroomTokens": 80_000}}),
        encoding="utf-8",
    )
    code, changed = _cli([*root, "set", "--input", str(input_path)], capsys)
    assert code == 0
    assert changed["data"]["effective"]["compaction"]["maxTokens"] == 80_000

    code, reset = _cli([*root, "reset"], capsys)
    assert code == 0
    assert reset["data"]["generation"] == 2
    assert reset["data"]["follows_defaults"] is True


def test_python_and_node_reviewed_model_contract_match():
    node = os.environ.get("GENSHIN_NATIVE_NODE")
    if not node:
        pytest.skip("set GENSHIN_NATIVE_NODE to verify the cross-language contract")
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [node, str(root / "agent-runtime" / "src" / "native-dsh-settings-probe.mjs")],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    report = json.loads(completed.stdout)
    assert report["status"] == "passed"
    assert report["reviewedModels"] == sorted(settings.REVIEWED_MODEL_IDS)
