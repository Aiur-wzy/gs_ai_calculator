"""Complete DSH receives normal Windows paths, never ambient secrets/hooks."""
from __future__ import annotations

from genshin_app.agent_chat.native_runtime_environment import (
    build_native_runtime_environment,
)
from genshin_app.frozen_selfcheck_isolation import clean_environment


def test_standard_windows_discovery_and_user_paths_survive_with_one_canonical_key():
    standard = {
        "Path": "C:/tools;C:/Windows/System32",
        "ProgramFiles": "C:/Program Files",
        "ProgramFiles(x86)": "C:/Program Files (x86)",
        "ProgramW6432": "C:/Program Files",
        "CommonProgramFiles": "C:/Program Files/Common Files",
        "CommonProgramFiles(x86)": "C:/Program Files (x86)/Common Files",
        "CommonProgramW6432": "C:/Program Files/Common Files",
        "PSModulePath": "C:/user/modules;C:/Windows/modules",
        "UserProfile": "C:/Users/ordinary",
        "HomeDrive": "C:",
        "HomePath": "/Users/ordinary",
        "AppData": "C:/Users/ordinary/AppData/Roaming",
        "LocalAppData": "C:/Users/ordinary/AppData/Local",
    }
    value = build_native_runtime_environment(standard)
    assert value == {**{key.upper(): item for key, item in standard.items()},
                     "DSH_TELEMETRY_DISABLED": "1", "NO_COLOR": "1"}
    aliases = build_native_runtime_environment({"Path": standard["Path"], "PATH": standard["Path"],
        "UserProfile": standard["UserProfile"], "USERPROFILE": standard["UserProfile"]})
    assert [key for key in aliases if key.casefold() == "path"] == ["PATH"]
    assert [key for key in aliases if key.casefold() == "userprofile"] == ["USERPROFILE"]


def test_normal_environment_never_inherits_secrets_hooks_or_ambient_proxy(monkeypatch):
    forbidden = {
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN", "UNRELATED_SECRET", "NODE_OPTIONS", "NODE_PATH", "PYTHONPATH",
        "PYTHONHOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "DSH_HOME",
    }
    for key in forbidden:
        monkeypatch.setenv(key, "must-never-be-inherited")
    monkeypatch.setenv("Path", "C:/ordinary-tools")
    value = build_native_runtime_environment()
    assert not forbidden.intersection(value)
    assert value["PATH"] == "C:/ordinary-tools"
    explicit = build_native_runtime_environment({**{key: "must-never-be-inherited" for key in forbidden},
        "Path": "C:/ordinary-tools"})
    assert explicit == {"PATH": "C:/ordinary-tools", "DSH_TELEMETRY_DISABLED": "1", "NO_COLOR": "1"}


def test_ordinary_windows_home_does_not_become_app_dsh_storage(tmp_path):
    root = tmp_path / "app-profile"
    value = build_native_runtime_environment({"GENSHIN_NATIVE_DSH_PROFILE_ROOT": str(root),
        "USERPROFILE": "C:/Users/ordinary", "APPDATA": "C:/Users/ordinary/AppData/Roaming",
        "LOCALAPPDATA": "C:/Users/ordinary/AppData/Local", "DSH_HOME": "C:/old-global-dsh"})
    assert value["DSH_HOME"] == str(root.resolve() / "dsh-home")
    assert value["USERPROFILE"] == "C:/Users/ordinary"
    assert build_native_runtime_environment(value) == value


def test_frozen_qa_clean_environment_keeps_its_separate_strict_scope(tmp_path):
    root = tmp_path / "selfcheck"
    ordinary = build_native_runtime_environment({
        "SystemRoot": "C:/Windows", "Path": "C:/tools", "ProgramFiles": "C:/Program Files",
        "PSModulePath": "C:/user/modules", "USERPROFILE": "C:/outside-user",
        "APPDATA": "C:/outside-roaming", "LOCALAPPDATA": "C:/outside-local",
        "DEEPSEEK_API_KEY": "never", "NODE_OPTIONS": "never",
    })
    clean = clean_environment(root, ordinary)
    assert not {"PATH", "PROGRAMFILES", "PSMODULEPATH", "DEEPSEEK_API_KEY", "NODE_OPTIONS"}.intersection(clean)
    for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "TEMP", "TMP"):
        assert str(root.resolve()) in clean[key]
    child = build_native_runtime_environment({**clean, "GENSHIN_NATIVE_DSH_PROFILE_ROOT": str(root / "profile")})
    assert child["USERPROFILE"] == clean["USERPROFILE"]
    assert child["DSH_HOME"] == str((root / "profile").resolve() / "dsh-home")
