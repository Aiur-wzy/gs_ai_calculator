from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat import native_provider_proxy as proxy_module
from genshin_app.agent_chat.native_provider_proxy import (
    NATIVE_PROVIDER_PROXY_MODE,
    NativeProviderProxyError,
    make_native_provider_proxy_environment,
    normalize_native_provider_proxy_url,
    read_windows_native_provider_proxy_or_direct,
    resolve_windows_proxy_settings,
    validate_native_provider_proxy_environment,
)
from genshin_app.agent_chat.native_runtime_environment import (
    build_native_runtime_environment,
)

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _ROOT / "agent-runtime"


def _node() -> str | None:
    configured = os.environ.get("GENSHIN_NATIVE_NODE")
    if configured and Path(configured).is_file():
        return configured
    return None


def _error_code(callback) -> str:
    with pytest.raises(NativeProviderProxyError) as caught:
        callback()
    return caught.value.code


def test_explicit_proxy_is_canonical_and_only_narrow_contract_survives():
    proxy = make_native_provider_proxy_environment(
        http_proxy="127.0.0.1:7890",
        https_proxy="https://proxy.example:8443/",
        no_proxy="localhost; example.test",
    )
    result = build_native_runtime_environment({
        "SystemRoot": r"C:\Windows",
        "DEEPSEEK_API_KEY": "forbidden",
        "UNRELATED": "forbidden",
        **proxy,
    })

    assert result == {
        "SYSTEMROOT": r"C:\Windows",
        NATIVE_PROVIDER_PROXY_MODE: "explicit",
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "HTTPS_PROXY": "https://proxy.example:8443",
        "NO_PROXY": "localhost,example.test",
        "DSH_TELEMETRY_DISABLED": "1",
        "NO_COLOR": "1",
    }


def test_ambient_proxy_is_never_inherited_without_explicit_marker(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://ambient.invalid:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:2")
    result = build_native_runtime_environment()
    assert "HTTP_PROXY" not in result
    assert "HTTPS_PROXY" not in result
    assert NATIVE_PROVIDER_PROXY_MODE not in result


def test_dsh_home_is_derived_only_from_explicit_app_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "ambient-user-home"))
    monkeypatch.setenv("GENSHIN_NATIVE_DSH_PROFILE_ROOT", str(tmp_path / "ambient-profile"))
    assert "DSH_HOME" not in build_native_runtime_environment()
    profile = tmp_path / "owned-profile"
    supplied = {"GENSHIN_NATIVE_DSH_PROFILE_ROOT": str(profile),
                "DSH_HOME": str(tmp_path / "injected-home"), "USERPROFILE": str(tmp_path / "ordinary-user-home")}
    result = build_native_runtime_environment(supplied)
    assert result["DSH_HOME"] == str(profile.resolve() / "dsh-home")
    assert result["USERPROFILE"] == supplied["USERPROFILE"]
    assert build_native_runtime_environment(result)["DSH_HOME"] == result["DSH_HOME"]


def test_relative_app_profile_does_not_select_a_dsh_home():
    with pytest.raises(ValueError, match="profile_root_must_be_absolute"):
        build_native_runtime_environment({"GENSHIN_NATIVE_DSH_PROFILE_ROOT": "relative-profile"})


@pytest.mark.skipif(_node() is None, reason="set GENSHIN_NATIVE_NODE to pinned Node")
def test_node_child_process_accepts_exact_uppercase_proxy_environment():
    proxy = make_native_provider_proxy_environment(
        http_proxy="127.0.0.1:7890",
        no_proxy="localhost",
    )
    environment: dict[str, str] = {
        **proxy,
        "DSH_TELEMETRY_DISABLED": "1",
        "NO_COLOR": "1",
    }
    for name in ("SYSTEMROOT", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            environment[name] = value

    completed = subprocess.run(
        [
            _node(),
            str(_RUNTIME / "src" / "native-provider-proxy-process-env-probe.mjs"),
        ],
        cwd=_RUNTIME,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    report = json.loads(completed.stdout)
    assert report["contract"] == "genshin-native-provider-proxy-process-env-v1"
    assert report["status"] == "passed"
    assert report["exactEnvironmentNames"] is True
    assert report["windowsCaseInsensitiveAliasCovered"] is (os.name == "nt")
    assert report["providerRequests"] == 0
    assert report["networkUsed"] is False


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("socks5://127.0.0.1:7890", "native_provider_proxy_scheme_unsupported"),
        ("http://user:secret@127.0.0.1:7890", "native_provider_proxy_credentials_forbidden"),
        ("http://127.0.0.1:7890/proxy.pac", "native_provider_proxy_url_invalid"),
        ("http://127.0.0.1:7890?x=1", "native_provider_proxy_url_invalid"),
        ("http://127.0.0.1:99999", "native_provider_proxy_url_invalid"),
    ],
)
def test_proxy_url_failures_are_stable_and_never_reflect_values(value, code):
    with pytest.raises(NativeProviderProxyError) as caught:
        normalize_native_provider_proxy_url(value)
    assert caught.value.code == code
    assert value not in str(caught.value)
    assert "secret" not in repr(caught.value)


def test_proxy_environment_rejects_aliases_and_partial_routes():
    partial = {
        NATIVE_PROVIDER_PROXY_MODE: "explicit",
        "HTTP_PROXY": "http://127.0.0.1:7890",
    }
    assert _error_code(lambda: validate_native_provider_proxy_environment(partial)) == (
        "native_provider_proxy_environment_incomplete"
    )
    aliased = {
        **make_native_provider_proxy_environment(http_proxy="127.0.0.1:7890"),
        "all_proxy": "http://other.invalid:1",
    }
    assert _error_code(lambda: validate_native_provider_proxy_environment(aliased)) == (
        "native_provider_proxy_environment_invalid"
    )


@pytest.mark.parametrize(
    "bypass",
    [
        "*",
        "deepseek.com",
        "*.deepseek.com",
        "api.deepseek.com:443",
        "deepseek.com.",
        "*.deepseek.com.",
        "api.deepseek.com.:443",
    ],
)
def test_official_provider_can_never_be_bypassed(bypass):
    assert _error_code(lambda: make_native_provider_proxy_environment(
        http_proxy="127.0.0.1:7890",
        no_proxy=bypass,
    )) == "native_provider_proxy_official_bypass_forbidden"


def test_windows_global_manual_proxy_is_converted_without_registry_metadata():
    result = resolve_windows_proxy_settings({
        "ProxyEnable": 1,
        "ProxyServer": "127.0.0.1:7890",
        "ProxyOverride": "<local>;localhost;*.example.test",
        "AutoConfigURL": None,
    })
    assert result == {
        NATIVE_PROVIDER_PROXY_MODE: "explicit",
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "HTTPS_PROXY": "http://127.0.0.1:7890",
        "NO_PROXY": "localhost,*.example.test",
    }
    assert "ProxyServer" not in json.dumps(result)


def test_windows_per_scheme_manual_proxy_is_supported():
    result = resolve_windows_proxy_settings({
        "ProxyEnable": True,
        "ProxyServer": "http=127.0.0.1:7890;https=https://proxy.example:8443",
        "ProxyOverride": "",
        "AutoConfigURL": "",
    })
    assert result["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert result["HTTPS_PROXY"] == "https://proxy.example:8443"
    assert "NO_PROXY" not in result


def test_optional_windows_proxy_uses_direct_only_when_manual_proxy_is_absent(
    monkeypatch,
):
    def absent():
        raise NativeProviderProxyError("native_provider_proxy_not_configured")

    monkeypatch.setattr(proxy_module, "read_windows_native_provider_proxy", absent)
    assert read_windows_native_provider_proxy_or_direct() == {}


@pytest.mark.parametrize(
    "code",
    [
        "native_provider_proxy_pac_unsupported",
        "native_provider_proxy_credentials_forbidden",
        "native_provider_proxy_system_format_unsupported",
        "native_provider_proxy_windows_read_failed",
    ],
)
def test_optional_windows_proxy_never_turns_invalid_configuration_into_direct(
    monkeypatch,
    code,
):
    def invalid():
        raise NativeProviderProxyError(code)

    monkeypatch.setattr(proxy_module, "read_windows_native_provider_proxy", invalid)
    with pytest.raises(NativeProviderProxyError) as caught:
        read_windows_native_provider_proxy_or_direct()
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("settings", "code"),
    [
        ({"ProxyEnable": 0, "ProxyServer": "127.0.0.1:7890"},
         "native_provider_proxy_not_configured"),
        ({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7890",
          "AutoConfigURL": "http://pac.invalid/proxy.pac"},
         "native_provider_proxy_pac_unsupported"),
        ({"ProxyEnable": 1, "ProxyServer": "socks=127.0.0.1:7890"},
         "native_provider_proxy_system_format_unsupported"),
        ({"ProxyEnable": 1, "ProxyServer": "http=user:secret@127.0.0.1:7890"},
         "native_provider_proxy_credentials_forbidden"),
    ],
)
def test_unsupported_windows_proxy_shapes_fail_closed_without_value(settings, code):
    with pytest.raises(NativeProviderProxyError) as caught:
        resolve_windows_proxy_settings(settings)
    assert caught.value.code == code
    assert "secret" not in repr(caught.value)
    assert "proxy.pac" not in repr(caught.value)
