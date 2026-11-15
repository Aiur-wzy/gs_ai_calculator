"""Fixed-origin DeepSeek account model catalog probe.

DSH's installed catalog describes local route capability; it does not prove
which models the current API key's account reports.  This narrow adapter makes
one read-only request to the fixed official ``/models`` endpoint and returns
only bounded model identifiers.  Provider bodies and the bearer secret are
never retained in raised errors.
"""

from __future__ import annotations

import json
from urllib import error, request
from urllib.parse import unquote, urlsplit

from .native_provider_proxy import validate_native_provider_proxy_environment

DEEPSEEK_MODELS_URL = "https://api.deepseek.com/models"
MAX_ACCOUNT_CATALOG_BYTES = 2 * 1024 * 1024
MAX_ACCOUNT_MODELS = 256
MAX_CREDENTIAL_CHARS = 512


class AccountCatalogError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise AccountCatalogError(code)


def _credential(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CREDENTIAL_CHARS
        or any(
            character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    ):
        _fail("account_catalog_credential_invalid")
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _ExplicitProxyHandler(request.ProxyHandler):
    """Apply one reviewed proxy without consulting ambient bypass settings."""

    def proxy_open(self, req, proxy, proxy_type):
        original_type = req.type
        try:
            parsed = urlsplit(proxy)
            host_port = unquote(parsed.netloc)
        except (TypeError, ValueError):
            _fail("account_catalog_proxy_invalid")
        if (
            parsed.scheme not in {"http", "https"}
            or not host_port
            or parsed.username is not None
            or parsed.password is not None
        ):
            _fail("account_catalog_proxy_invalid")
        # This intentionally omits urllib.request.proxy_bypass().  The
        # acceptance launcher already resolved the reviewed App policy and an
        # ambient WinINET/NO_PROXY rule must never turn a failed proxy into a
        # direct provider request.
        req.set_proxy(host_port, parsed.scheme or proxy_type)
        if original_type == parsed.scheme or original_type == "https":
            return None
        return self.parent.open(req, timeout=req.timeout)


def _model_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or not value[0].isalnum()
        or any(
            not (character.isascii() and (character.isalnum() or character in "._:-"))
            for character in value
        )
    ):
        _fail("account_catalog_model_invalid")
    return value


def _probe_sensitive(opener, api_key: str, timeout_seconds: int) -> dict:
    """Run the request in a frame that never escapes in a public error."""
    key = _credential(api_key)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
        _fail("account_catalog_timeout_invalid")
    req = request.Request(
        DEEPSEEK_MODELS_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "GenshinCalc-NativeAgent/2",
        },
        method="GET",
    )
    response = None
    failure_code = None
    try:
        response = opener.open(req, timeout=timeout_seconds)
        if response.geturl() != DEEPSEEK_MODELS_URL:
            _fail("account_catalog_redirect")
        raw = response.read(MAX_ACCOUNT_CATALOG_BYTES + 1)
        if len(raw) > MAX_ACCOUNT_CATALOG_BYTES:
            _fail("account_catalog_response_too_large")
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except AccountCatalogError:
        raise
    except error.HTTPError as exc:
        try:
            if exc.code in {401, 403}:
                failure_code = "account_catalog_auth"
            elif exc.code == 429:
                failure_code = "account_catalog_rate_limit"
            elif 300 <= exc.code < 400:
                failure_code = "account_catalog_redirect"
            else:
                failure_code = "account_catalog_http"
        finally:
            # HTTPError is also the response object.  Never read its body,
            # but still release the socket before returning a sanitized
            # error code to the App.
            exc.close()
    except (error.URLError, TimeoutError, OSError):
        failure_code = "account_catalog_network"
    except (UnicodeError, ValueError, TypeError, RecursionError):
        failure_code = "account_catalog_protocol"
    finally:
        if response is not None:
            try:
                response.close()
            except OSError:
                pass

    # Raise outside every provider/parser exception handler. Otherwise
    # Python attaches the original exception as ``__context__`` and a
    # caller that logs the chain could expose response or network details.
    if failure_code is not None:
        raise AccountCatalogError(failure_code) from None

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        _fail("account_catalog_protocol")
    rows = payload["data"]
    if not 1 <= len(rows) <= MAX_ACCOUNT_MODELS:
        _fail("account_catalog_empty" if not rows else "account_catalog_model_limit")
    model_ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            _fail("account_catalog_model_invalid")
        model_ids.append(_model_id(row.get("id")))
    if len(set(model_ids)) != len(model_ids):
        _fail("account_catalog_model_duplicate")
    return {"data": [{"id": model_id} for model_id in model_ids]}


class DeepSeekAccountCatalogProbe:
    """Perform a fixed-host, no-redirect, bounded account catalog read."""

    def __init__(self, *, opener=None, proxy_environment=None):
        if opener is not None and proxy_environment is not None:
            raise TypeError("opener and proxy_environment are mutually exclusive")
        if opener is not None:
            self._opener = opener
            return
        proxies: dict[str, str] = {}
        if proxy_environment is not None:
            checked = validate_native_provider_proxy_environment(proxy_environment)
            if not checked:
                raise TypeError("proxy_environment must be explicitly configured")
            proxies = {
                "http": checked["HTTP_PROXY"],
                "https": checked["HTTPS_PROXY"],
            }
        # Supplying ProxyHandler explicitly, including the empty direct case,
        # prevents build_opener from auto-loading ambient environment/WinINET.
        proxy_handler = (
            _ExplicitProxyHandler(proxies)
            if proxies
            else request.ProxyHandler({})
        )
        self._opener = request.build_opener(proxy_handler, _NoRedirect())

    def probe(self, api_key: str, *, timeout_seconds: int = 30) -> dict:
        """Return a bounded catalog without retaining secrets in failures.

        The sensitive worker may hold the key, Authorization request and raw
        provider body.  It is never allowed to contribute a traceback to the
        public exception.  This outer frame drops every reference first, then
        creates a fresh stable error suitable for crash-report collection.
        """
        opener = self._opener
        result = None
        failure_code = None
        try:
            result = _probe_sensitive(opener, api_key, timeout_seconds)
        except AccountCatalogError as caught:
            failure_code = caught.code
        except Exception:  # noqa: BLE001 - sanitize the complete HTTP/parser boundary
            failure_code = "account_catalog_protocol"
        finally:
            del api_key, opener, self
        if failure_code is not None:
            raise AccountCatalogError(failure_code) from None
        return result


__all__ = [
    "DEEPSEEK_MODELS_URL",
    "AccountCatalogError",
    "DeepSeekAccountCatalogProbe",
]
