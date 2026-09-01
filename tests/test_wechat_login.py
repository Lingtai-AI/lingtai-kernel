"""Regression tests for transient failures during WeChat QR acquisition."""
from __future__ import annotations

import httpx
import pytest

from lingtai.mcp_servers.wechat import login

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def no_sleep(monkeypatch):
    async def _sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(login.asyncio, "sleep", _sleep)


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/qr")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("QR fetch failed", request=request, response=response)


async def test_qr_fetch_retries_transient_error_then_succeeds(monkeypatch, no_sleep):
    responses = [httpx.ConnectError("temporary connection failure"), {"qrcode": "qr"}]
    calls = 0

    async def fake_get_qrcode(_base_url: str) -> dict:
        nonlocal calls
        calls += 1
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(login.api, "get_qrcode", fake_get_qrcode)

    assert await login._fetch_qr_with_retry("https://example.test") == {"qrcode": "qr"}
    assert calls == 2


async def test_login_initial_fetch_retries_transient_error(monkeypatch, no_sleep):
    responses = [httpx.ConnectError("temporary connection failure"), {"qrcode": "qr"}]
    statuses = iter([{"status": "confirmed", "bot_token": "token"}])

    async def fake_get_qrcode(_base_url: str) -> dict:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def fake_poll_qr_status(_base_url: str, qrcode: str) -> dict:
        assert qrcode == "qr"
        return next(statuses)

    monkeypatch.setattr(login.api, "get_qrcode", fake_get_qrcode)
    monkeypatch.setattr(login.api, "poll_qr_status", fake_poll_qr_status)
    monkeypatch.setattr(login, "_display_qr", lambda _qr_data: None)

    assert await login._login_flow("https://example.test") == {
        "bot_token": "token",
        "user_id": "",
        "base_url": "https://example.test",
    }


async def test_login_refresh_retries_transient_qr_fetch(monkeypatch, no_sleep):
    responses = [
        {"qrcode": "first-qr"},
        httpx.ReadTimeout("temporary read timeout"),
        {"qrcode": "replacement-qr"},
    ]
    fetched = []
    statuses = iter([{"status": "expired"}, {"status": "confirmed", "bot_token": "token"}])

    async def fake_get_qrcode(_base_url: str) -> dict:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        fetched.append(response["qrcode"])
        return response

    async def fake_poll_qr_status(_base_url: str, qrcode: str) -> dict:
        assert qrcode in {"first-qr", "replacement-qr"}
        return next(statuses)

    monkeypatch.setattr(login.api, "get_qrcode", fake_get_qrcode)
    monkeypatch.setattr(login.api, "poll_qr_status", fake_poll_qr_status)
    monkeypatch.setattr(login, "_display_qr", lambda _qr_data: None)

    result = await login._login_flow("https://example.test")

    assert result == {
        "bot_token": "token",
        "user_id": "",
        "base_url": "https://example.test",
    }
    assert fetched == ["first-qr", "replacement-qr"]


async def test_qr_fetch_exhaustion_is_bounded(monkeypatch, no_sleep):
    calls = 0

    async def always_timeout(_base_url: str) -> dict:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("temporary read timeout")

    monkeypatch.setattr(login.api, "get_qrcode", always_timeout)

    with pytest.raises(httpx.ReadTimeout):
        await login._fetch_qr_with_retry("https://example.test")

    assert calls == login.QR_FETCH_MAX_ATTEMPTS


@pytest.mark.parametrize("status_code", [408, 429, 503])
async def test_qr_fetch_retries_selected_http_statuses(
    monkeypatch, no_sleep, status_code: int,
):
    responses = [_status_error(status_code), {"qrcode": "qr"}]
    calls = 0

    async def fake_get_qrcode(_base_url: str) -> dict:
        nonlocal calls
        calls += 1
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(login.api, "get_qrcode", fake_get_qrcode)

    assert await login._fetch_qr_with_retry("https://example.test") == {"qrcode": "qr"}
    assert calls == 2


async def test_qr_fetch_does_not_retry_non_retryable_http_status(monkeypatch, no_sleep):
    calls = 0
    error = _status_error(401)

    async def fail_auth(_base_url: str) -> dict:
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(login.api, "get_qrcode", fail_auth)

    with pytest.raises(httpx.HTTPStatusError):
        await login._fetch_qr_with_retry("https://example.test")

    assert calls == 1
