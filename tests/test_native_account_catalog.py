from __future__ import annotations

import io
import json
import os
from pathlib import Path
from urllib import error, request

import pytest

from genshin_app.agent_chat.native_account_catalog import (
    DEEPSEEK_MODELS_URL,
    AccountCatalogError,
    DeepSeekAccountCatalogProbe,
)
from genshin_app.agent_chat.native_provider_proxy import (
    make_native_provider_proxy_environment,
)


class Response:
    def __init__(self, payload, *, url=DEEPSEEK_MODELS_URL):
        self._raw = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )
        self._url = url
        self.closed = False

    def geturl(self):
        return self._url

    def read(self, maximum):
        return self._raw[:maximum]

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _proxy_handler(probe, *, required=True):
    found = next((
        handler
        for handler in probe._opener.handlers
        if isinstance(handler, request.ProxyHandler)
    ), None)
    if required:
        assert found is not None
    return found


def test_default_catalog_probe_disables_ambient_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:8765")
    probe = DeepSeekAccountCatalogProbe()
    assert _proxy_handler(probe, required=False) is None


def test_explicit_catalog_proxy_uses_reviewed_mapping_and_ignores_ambient_bypass(
    monkeypatch,
):
    monkeypatch.setenv("NO_PROXY", "api.deepseek.com")
    proxy = make_native_provider_proxy_environment(
        http_proxy="127.0.0.1:7890",
        https_proxy="127.0.0.1:7891",
    )
    probe = DeepSeekAccountCatalogProbe(proxy_environment=proxy)
    handler = _proxy_handler(probe)
    assert handler.proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7891",
    }
    outbound = request.Request(DEEPSEEK_MODELS_URL)
    assert handler.proxy_open(outbound, handler.proxies["https"], "https") is None
    assert outbound.host == "127.0.0.1:7891"
    assert outbound._tunnel_host == "api.deepseek.com"
    assert os.environ["NO_PROXY"] == "api.deepseek.com"


def test_catalog_proxy_constructor_rejects_ambiguous_sources():
    with pytest.raises(TypeError, match="mutually exclusive"):
        DeepSeekAccountCatalogProbe(
            opener=Opener(None),
            proxy_environment=make_native_provider_proxy_environment(
                http_proxy="127.0.0.1:7890",
            ),
        )


def test_models_probe_reaches_only_the_explicit_proxy_even_with_ambient_bypass(
    monkeypatch,
):
    observed = []

    def capture_transport(_handler, req):
        observed.append({
            "host": req.host,
            "tunnel_host": req._tunnel_host,
            "url": req.full_url,
        })
        raise error.URLError("offline proxy fixture")

    monkeypatch.setattr(request.HTTPSHandler, "https_open", capture_transport)
    monkeypatch.setenv("NO_PROXY", "api.deepseek.com")
    probe = DeepSeekAccountCatalogProbe(
        proxy_environment=make_native_provider_proxy_environment(
            http_proxy="127.0.0.1:7890",
        ),
    )
    with pytest.raises(AccountCatalogError) as caught:
        probe.probe("SAFE_TEST_KEY", timeout_seconds=2)

    assert caught.value.code == "account_catalog_network"
    assert len(observed) == 1
    assert observed[0] == {
        "host": "127.0.0.1:7890",
        "tunnel_host": "api.deepseek.com",
        "url": DEEPSEEK_MODELS_URL,
    }
    assert "SAFE_TEST_KEY" not in json.dumps(observed)


def _module_traceback_values(exc):
    """Collect plain data reachable from account-probe traceback locals."""
    seen = set()

    def visit(value, depth=0):
        if depth > 8 or id(value) in seen:
            return []
        seen.add(id(value))
        if isinstance(value, str):
            return [value]
        if isinstance(value, bytes):
            return [value.decode("utf-8", errors="replace")]
        if isinstance(value, dict):
            found = []
            for key, item in value.items():
                found.extend(visit(key, depth + 1))
                found.extend(visit(item, depth + 1))
            return found
        if isinstance(value, (list, tuple, set, frozenset)):
            found = []
            for item in value:
                found.extend(visit(item, depth + 1))
            return found
        fields = getattr(value, "__dict__", None)
        return visit(fields, depth + 1) if isinstance(fields, dict) else []

    values = []
    trace = exc.__traceback__
    while trace is not None:
        if Path(trace.tb_frame.f_code.co_filename).name == "native_account_catalog.py":
            values.extend(visit(dict(trace.tb_frame.f_locals)))
        trace = trace.tb_next
    return values


def test_fixed_official_probe_returns_only_bounded_model_ids():
    sentinel = "SENTINEL_ACCOUNT_KEY"
    response = Response(
        {
            "object": "list",
            "data": [
                {"id": "deepseek-flash", "owned_by": "account", "secret": "discard"},
                {"id": "deepseek-v4-pro", "object": "model"},
            ],
        }
    )
    opener = Opener(response)
    result = DeepSeekAccountCatalogProbe(opener=opener).probe(sentinel)
    assert result == {
        "data": [
            {"id": "deepseek-flash"},
            {"id": "deepseek-v4-pro"},
        ]
    }
    req, timeout = opener.calls[0]
    assert req.full_url == DEEPSEEK_MODELS_URL
    assert req.get_method() == "GET" and timeout == 30
    assert req.headers["Authorization"] == f"Bearer {sentinel}"
    assert sentinel not in json.dumps(result)
    assert response.closed is True


@pytest.mark.parametrize("secret", ["", "bad key", "line\nbreak", "x" * 513, None])
def test_invalid_key_is_rejected_before_io_without_echo(secret):
    opener = Opener(Response({"data": [{"id": "never"}]}))
    with pytest.raises(AccountCatalogError) as caught:
        DeepSeekAccountCatalogProbe(opener=opener).probe(secret)
    assert caught.value.code == "account_catalog_credential_invalid"
    if isinstance(secret, str) and secret:
        assert secret not in repr(caught.value)
    assert opener.calls == []


def test_redirect_duplicate_and_malformed_catalog_fail_closed():
    cases = [
        (
            Response({"data": [{"id": "x"}]}, url="https://example.com/models"),
            "account_catalog_redirect",
        ),
        (Response(b'{"data":[{"id":"x","id":"y"}]}'), "account_catalog_protocol"),
        (Response({"data": [{"id": "bad model"}]}), "account_catalog_model_invalid"),
        (
            Response({"data": [{"id": "x"}, {"id": "x"}]}),
            "account_catalog_model_duplicate",
        ),
        (Response({"data": []}), "account_catalog_empty"),
    ]
    for response, code in cases:
        with pytest.raises(AccountCatalogError, match=code):
            DeepSeekAccountCatalogProbe(opener=Opener(response)).probe("SAFE_TEST_KEY")
        assert response.closed is True


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "account_catalog_auth"),
        (403, "account_catalog_auth"),
        (429, "account_catalog_rate_limit"),
        (500, "account_catalog_http"),
    ],
)
def test_http_errors_are_stable_and_do_not_read_provider_body(status, code):
    sentinel = "SENTINEL_PROVIDER_BODY"
    failure = error.HTTPError(
        DEEPSEEK_MODELS_URL, status, sentinel, {}, io.BytesIO(sentinel.encode())
    )
    with pytest.raises(AccountCatalogError) as caught:
        DeepSeekAccountCatalogProbe(opener=Opener(failure)).probe("SAFE_TEST_KEY")
    assert caught.value.code == code
    assert sentinel not in repr(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert failure.fp.closed is True


def test_network_and_timeout_validation_are_sanitized():
    failure = error.URLError("SENTINEL_NETWORK")
    with pytest.raises(AccountCatalogError, match="account_catalog_network") as caught:
        DeepSeekAccountCatalogProbe(
            opener=Opener(failure),
        ).probe("SAFE_TEST_KEY")
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    with pytest.raises(AccountCatalogError, match="account_catalog_timeout_invalid"):
        DeepSeekAccountCatalogProbe(opener=Opener(None)).probe(
            "SAFE_TEST_KEY", timeout_seconds=True
        )


def test_protocol_error_drops_parser_exception_chain():
    response = Response(b'{"data": [}')
    with pytest.raises(AccountCatalogError, match="account_catalog_protocol") as caught:
        DeepSeekAccountCatalogProbe(opener=Opener(response)).probe("SAFE_TEST_KEY")
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert response.closed is True


@pytest.mark.parametrize(
    "failure",
    [
        error.HTTPError(
            DEEPSEEK_MODELS_URL,
            401,
            "SENTINEL_HTTP_REASON",
            {},
            io.BytesIO(b"SENTINEL_HTTP_BODY"),
        ),
        Response(b'{"data":"SENTINEL_PROVIDER_BODY"'),
    ],
)
def test_failure_traceback_locals_do_not_retain_key_request_or_provider_body(failure):
    api_key = "SENTINEL_ACCOUNT_KEY"
    with pytest.raises(AccountCatalogError) as caught:
        DeepSeekAccountCatalogProbe(opener=Opener(failure)).probe(api_key)
    retained = "\n".join(_module_traceback_values(caught.value))
    assert api_key not in retained
    assert "SENTINEL_HTTP_REASON" not in retained
    assert "SENTINEL_HTTP_BODY" not in retained
    assert "SENTINEL_PROVIDER_BODY" not in retained
