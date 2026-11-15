"""Controlled proxy hand-off for the App-native DeepSeek runtime.

The native Node child never inherits the App process environment.  A provider
acceptance entry may explicitly resolve the current-user Windows manual proxy
through this module and pass the returned, narrow mapping to
``build_native_runtime_environment``.  Proxy credentials and auto-configuration
are deliberately unsupported because neither can be represented in a safe,
stable child contract without exposing secrets or evaluating PAC code.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

NATIVE_PROVIDER_PROXY_MODE = "GENSHIN_NATIVE_PROVIDER_PROXY_MODE"
NATIVE_PROVIDER_PROXY_EXPLICIT = "explicit"
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
_BYPASS_ENTRY = re.compile(r"[^\s,;@/\\<>\x00-\x1f\x7f]{1,255}\Z")


class NativeProviderProxyError(RuntimeError):
    """A path-free, credential-free proxy configuration failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise NativeProviderProxyError(code)


def normalize_native_provider_proxy_url(value: object) -> str:
    """Validate one uncredentialed HTTP(S) proxy URL and canonicalize it."""
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("native_provider_proxy_url_invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail("native_provider_proxy_url_invalid")
    candidate = value if "://" in value else f"http://{value}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except (TypeError, ValueError):
        _fail("native_provider_proxy_url_invalid")
    if parsed.scheme.lower() not in {"http", "https"}:
        _fail("native_provider_proxy_scheme_unsupported")
    if parsed.username is not None or parsed.password is not None:
        _fail("native_provider_proxy_credentials_forbidden")
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        _fail("native_provider_proxy_url_invalid")
    try:
        parsed.hostname.encode("ascii")
    except UnicodeEncodeError:
        _fail("native_provider_proxy_url_invalid")
    if port is not None and not (1 <= port <= 65535):
        _fail("native_provider_proxy_url_invalid")
    # Keep brackets and an explicit port from the original netloc, while
    # dropping the harmless root slash.  Userinfo has already been rejected.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))


def _normalize_no_proxy(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 4096:
        _fail("native_provider_proxy_bypass_invalid")
    entries: list[str] = []
    for raw in re.split(r"[;,]", value):
        entry = raw.strip()
        if not entry:
            continue
        if entry.lower() == "<local>":
            # WinINET's token means every single-label hostname.  The native
            # provider uses fixed public DNS names, so the token has no target
            # in this App and must not be widened to ``*``.  Other explicit
            # bypass entries remain intact.
            continue
        if _BYPASS_ENTRY.fullmatch(entry) is None:
            _fail("native_provider_proxy_bypass_invalid")
        candidate = entry.casefold().removeprefix("*.").removeprefix(".")
        host = candidate.rsplit(":", 1)[0] if candidate.count(":") == 1 else candidate
        # DSH canonicalizes a DNS bypass entry by dropping its trailing root
        # dot before suffix matching.  Apply the same normalization here so
        # ``api.deepseek.com.`` cannot pass validation and then bypass the
        # mandatory proxy inside DSH.
        host = host.rstrip(".")
        if host == "*" or host == "deepseek.com" or host.endswith(".deepseek.com"):
            _fail("native_provider_proxy_official_bypass_forbidden")
        entries.append(entry)
    return ",".join(entries) or None


def make_native_provider_proxy_environment(
    *,
    http_proxy: object,
    https_proxy: object | None = None,
    no_proxy: object | None = None,
) -> dict[str, str]:
    """Build the only proxy environment shape admitted to the Node child."""
    http = normalize_native_provider_proxy_url(http_proxy)
    https = normalize_native_provider_proxy_url(
        http_proxy if https_proxy is None else https_proxy,
    )
    output = {
        NATIVE_PROVIDER_PROXY_MODE: NATIVE_PROVIDER_PROXY_EXPLICIT,
        "HTTP_PROXY": http,
        "HTTPS_PROXY": https,
    }
    bypass = _normalize_no_proxy(no_proxy)
    if bypass is not None:
        output["NO_PROXY"] = bypass
    return output


def validate_native_provider_proxy_environment(
    source: Mapping[str, str],
) -> dict[str, str]:
    """Revalidate an explicit child mapping without reading ambient proxies."""
    if not isinstance(source, Mapping):
        raise TypeError("source must be a mapping")
    mode = source.get(NATIVE_PROVIDER_PROXY_MODE)
    if mode is None:
        return {}
    if mode != NATIVE_PROVIDER_PROXY_EXPLICIT:
        _fail("native_provider_proxy_mode_invalid")
    # ALL_PROXY and lower-case aliases are never part of the App contract.
    forbidden = {
        "ALL_PROXY", "all_proxy", "http_proxy", "https_proxy", "no_proxy",
    }
    if any(name in source for name in forbidden):
        _fail("native_provider_proxy_environment_invalid")
    if "HTTP_PROXY" not in source or "HTTPS_PROXY" not in source:
        _fail("native_provider_proxy_environment_incomplete")
    return make_native_provider_proxy_environment(
        http_proxy=source["HTTP_PROXY"],
        https_proxy=source["HTTPS_PROXY"],
        no_proxy=source.get("NO_PROXY"),
    )


def resolve_windows_proxy_settings(settings: Mapping[str, object]) -> dict[str, str]:
    """Convert a WinINET manual-proxy snapshot into the narrow child shape."""
    if not isinstance(settings, Mapping):
        raise TypeError("settings must be a mapping")
    auto_config = settings.get("AutoConfigURL")
    if auto_config is not None and auto_config != "":
        _fail("native_provider_proxy_pac_unsupported")
    if settings.get("ProxyEnable") != 1:
        _fail("native_provider_proxy_not_configured")
    raw = settings.get("ProxyServer")
    if not isinstance(raw, str) or not raw.strip():
        _fail("native_provider_proxy_not_configured")
    value = raw.strip()
    http_proxy: str | None = None
    https_proxy: str | None = None
    if "=" not in value:
        http_proxy = https_proxy = value
    else:
        routes: dict[str, str] = {}
        for item in value.split(";"):
            if not item.strip() or "=" not in item:
                _fail("native_provider_proxy_system_format_unsupported")
            scheme, endpoint = (part.strip() for part in item.split("=", 1))
            normalized_scheme = scheme.lower()
            if normalized_scheme not in {"http", "https"} or not endpoint:
                _fail("native_provider_proxy_system_format_unsupported")
            if normalized_scheme in routes:
                _fail("native_provider_proxy_system_format_unsupported")
            routes[normalized_scheme] = endpoint
        http_proxy = routes.get("http")
        https_proxy = routes.get("https")
        if http_proxy is None and https_proxy is None:
            _fail("native_provider_proxy_system_format_unsupported")
        # A single declared route is intentionally shared.  This matches the
        # DSH policy while avoiding a silent direct path for the other scheme.
        http_proxy = http_proxy or https_proxy
        https_proxy = https_proxy or http_proxy
    return make_native_provider_proxy_environment(
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        no_proxy=settings.get("ProxyOverride"),
    )


def read_windows_native_provider_proxy() -> dict[str, str]:
    """Read the current-user WinINET manual proxy without returning its source."""
    if os.name != "nt":
        _fail("native_provider_proxy_windows_required")
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            settings: dict[str, object] = {}
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
                try:
                    settings[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    settings[name] = None
    except NativeProviderProxyError:
        raise
    except OSError:
        _fail("native_provider_proxy_windows_read_failed")
    return resolve_windows_proxy_settings(settings)


def read_windows_native_provider_proxy_or_direct() -> dict[str, str]:
    """Return the reviewed manual proxy, or an empty direct-mode contract.

    A missing/disabled WinINET manual proxy is the only condition that means
    direct access.  PAC, credential-bearing endpoints, malformed settings and
    registry read failures remain hard errors so the release App cannot
    silently bypass a configured network policy.
    """

    try:
        return read_windows_native_provider_proxy()
    except NativeProviderProxyError as error:
        if error.code == "native_provider_proxy_not_configured":
            return {}
        raise


__all__ = [
    "NATIVE_PROVIDER_PROXY_EXPLICIT",
    "NATIVE_PROVIDER_PROXY_MODE",
    "NativeProviderProxyError",
    "make_native_provider_proxy_environment",
    "normalize_native_provider_proxy_url",
    "read_windows_native_provider_proxy",
    "read_windows_native_provider_proxy_or_direct",
    "resolve_windows_proxy_settings",
    "validate_native_provider_proxy_environment",
]
