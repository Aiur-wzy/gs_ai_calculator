from __future__ import annotations

import pytest

from genshin_app.agent_chat import native_compatibility_identity as identity
from genshin_app.agent_chat.native_compatibility_identity import (
    app_artifact_identity_hash,
    build_skill_bundle_manifest,
    release_pair_identity_hash,
    skill_bundle_hash,
)


def test_pending_shell_release_does_not_derive_a_trusted_pin(monkeypatch):
    monkeypatch.setattr(identity, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", None)
    assert identity.shell_release_identity_descriptor() == {
        "contract": "genshin-native-dsh-shell-release-identity-v1",
        "runtime_generation": "dsh_client_shell", "state": "pending",
        "manifest_sha256": None,
    }
    assert identity.IDENTITY_CONTRACT.endswith("-v2")


def test_source_identity_changes_when_reviewed_shell_pin_is_accepted_or_replaced(tmp_path, monkeypatch):
    source = tmp_path / "app"
    source.mkdir()
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    roots = (("app", source),)
    monkeypatch.setattr(identity, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", None)
    pending = app_artifact_identity_hash(source_roots=roots)
    monkeypatch.setattr(identity, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", "a" * 64)
    pinned = app_artifact_identity_hash(source_roots=roots)
    assert identity.shell_release_identity_descriptor()["state"] == "pinned"
    monkeypatch.setattr(identity, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", "b" * 64)
    replaced = app_artifact_identity_hash(source_roots=roots)
    assert len({pending, pinned, replaced}) == 3


@pytest.mark.parametrize("pin", ["", "not-a-sha", "A" * 64, True])
def test_malformed_shell_pin_never_becomes_a_development_fallback(monkeypatch, pin):
    monkeypatch.setattr(identity, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", pin)
    with pytest.raises(RuntimeError, match="native_shell_release_pin_invalid"):
        identity.shell_release_identity_descriptor()


def test_skill_bundle_hash_binds_exact_sanitized_markdown(tmp_path):
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# 说明\n", encoding="utf-8")
    (root / "references" / "stage.md").write_text("阶段一\n", encoding="utf-8")

    first = build_skill_bundle_manifest(root)
    first_hash = skill_bundle_hash(root)
    (root / "references" / "stage.md").write_text("阶段二\n", encoding="utf-8")
    second_hash = skill_bundle_hash(root)

    assert [row["resource"] for row in first["resources"]] == [
        "SKILL.md",
        "references/stage.md",
    ]
    assert first_hash != second_hash


def test_source_app_artifact_hash_binds_content_not_absolute_path(tmp_path):
    first = tmp_path / "first" / "app"
    second = tmp_path / "second" / "app"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (second / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    first_hash = app_artifact_identity_hash(source_roots=(("app", first),))
    second_hash = app_artifact_identity_hash(source_roots=(("app", second),))
    (second / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert first_hash == second_hash
    assert first_hash != app_artifact_identity_hash(
        source_roots=(("app", second),),
    )


def test_default_source_artifact_binds_runtime_code_and_profiles(
    monkeypatch, tmp_path,
):
    for package in (
        "genshin_app", "genshin_db", "genshin_engine", "genshin_runtime",
    ):
        root = tmp_path / "src" / package
        root.mkdir(parents=True)
        (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime = tmp_path / "src" / "genshin_runtime"
    (runtime / "profile.json").write_text('{"version":1}\n', encoding="utf-8")
    monkeypatch.setattr(
        "genshin_app.agent_chat.native_compatibility_identity.resource_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "genshin_app.agent_chat.native_compatibility_identity.is_frozen",
        lambda: False,
    )

    original = app_artifact_identity_hash()
    (runtime / "profile.json").write_text('{"version":2}\n', encoding="utf-8")
    profile_changed = app_artifact_identity_hash()
    (runtime / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    code_changed = app_artifact_identity_hash()

    assert original != profile_changed
    assert profile_changed != code_changed


def test_release_pair_identity_is_a_stable_digest():
    value = release_pair_identity_hash()
    assert len(value) == 64
    assert value == release_pair_identity_hash()
