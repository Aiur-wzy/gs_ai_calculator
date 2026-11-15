"""Direct DSH QA keeps the production-safe OS env and isolated global home."""
from genshin_app.agent_chat.native_runtime_environment import (
    build_native_runtime_environment,
)
from tests.dsh_fixture_environment import isolated_runtime_environment


def test_direct_fixture_preserves_os_keys_and_derives_home_without_ambient_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", "C:/fixture-windows")
    monkeypatch.setenv("COMSPEC", "C:/fixture-windows/cmd.exe")
    monkeypatch.setenv("DSH_HOME", "C:/unrelated-user-home")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "engineering-sentinel-never-inherited")
    monkeypatch.setenv("NODE_OPTIONS", "--import unrelated.mjs")
    profile = tmp_path / "fixture-profile"
    actual = build_native_runtime_environment(isolated_runtime_environment(profile))
    assert actual["SYSTEMROOT"] == "C:/fixture-windows"
    assert actual["COMSPEC"] == "C:/fixture-windows/cmd.exe"
    assert actual["DSH_HOME"] == str(profile.resolve() / "dsh-home")
    assert actual["GENSHIN_NATIVE_DSH_PROFILE_ROOT"] == str(profile)
    assert not {"DEEPSEEK_API_KEY", "NODE_OPTIONS"} & set(actual)
