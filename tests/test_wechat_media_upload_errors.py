"""Outbound WeChat media route, retry, timeout, and privacy regressions."""
from __future__ import annotations

import asyncio
import json
import ssl
from pathlib import Path

import httpx
import pytest

from lingtai.mcp_servers.wechat import media
from lingtai.mcp_servers.wechat import manager as manager_mod
from lingtai.mcp_servers.wechat.manager import WechatManager
from lingtai.mcp_servers.wechat.types import GetUploadUrlResp


_PRESIGNED = (
    "https://upload.example.invalid/c2c/private-object?"
    "token=SECRET_QUERY&recipient=SECRET_USER"
)
_CDN_BASE = "https://static-cdn.example.invalid/c2c"
_UPLOAD_PARAM = " upload /?&+=!~*'()SECRET_UPLOAD_PARAM "
_FILEKEY = "file/key ?&+=!~*'()SECRET_FILEKEY"
_DOWNLOAD_PARAM = "download-encrypted-param"


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        text: str = "",
        body: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self._body = body if body is not None else {}

    def json(self) -> dict:
        return self._body


class SequenceClient:
    outcomes: list[FakeResponse | BaseException] = []
    calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        type(self).calls.append((url, kwargs))
        if not type(self).outcomes:
            raise AssertionError("unexpected CDN attempt")
        outcome = type(self).outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _manager(tmp_path: Path, *, cdn_base_url: str = _CDN_BASE) -> WechatManager:
    return WechatManager(
        token="SECRET_BOT_TOKEN",
        user_id="test-bot",
        cdn_base_url=cdn_base_url,
        working_dir=tmp_path,
        on_inbound=lambda event: None,
    )


def _source(tmp_path: Path, name: str = "outbound.png") -> Path:
    source = tmp_path / name
    source.write_bytes(b"deterministic outbound bytes")
    return source


def _run(coro):
    return asyncio.run(coro)


def _success_response() -> FakeResponse:
    return FakeResponse(200, headers={"x-encrypted-param": _DOWNLOAD_PARAM})


def _install_upload_mocks(
    monkeypatch,
    *,
    upload_resp: GetUploadUrlResp,
    outcomes: list[FakeResponse | BaseException],
    filekey: str = "a" * 32,
) -> type[SequenceClient]:
    async def get_upload_url(*args, **kwargs):
        return upload_resp

    class Client(SequenceClient):
        pass

    Client.outcomes = list(outcomes)
    Client.calls = []
    monkeypatch.setattr(media.api, "get_upload_url", get_upload_url)
    monkeypatch.setattr(media.httpx, "AsyncClient", Client)
    monkeypatch.setattr(media.secrets, "token_hex", lambda size: filekey)
    return Client


def _upload(source: Path, *, cdn_base_url: str = _CDN_BASE):
    return _run(
        media.upload_media(
            source,
            "https://ilink.example.invalid",
            "SECRET_BOT_TOKEN",
            "SECRET_USER",
            cdn_base_url=cdn_base_url,
        )
    )


def _assert_redacted(value: object, *, local_path: Path | None = None) -> None:
    rendered = json.dumps(value, ensure_ascii=False)
    for secret in (
        _PRESIGNED,
        "SECRET_QUERY",
        "SECRET_USER",
        "SECRET_BOT_TOKEN",
        "SECRET_UPLOAD_PARAM",
        "SECRET_FILEKEY",
        "RAW_PROVIDER_BODY",
        "RAW_X_ERROR_MESSAGE",
    ):
        assert secret not in rendered
    if local_path is not None:
        assert str(local_path) not in rendered


def test_official_static_url_is_primary_and_exactly_percent_encodes_components(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(
            upload_param=_UPLOAD_PARAM,
            upload_full_url=_PRESIGNED,
        ),
        outcomes=[_success_response()],
        filekey=_FILEKEY,
    )

    info = _upload(source, cdn_base_url=_CDN_BASE + "/")

    assert info.cdn_media.encrypt_query_param == _DOWNLOAD_PARAM
    assert len(client.calls) == 1
    assert client.calls[0][0] == (
        _CDN_BASE
        + "/upload?encrypted_query_param="
        + "%20upload%20%2F%3F%26%2B%3D!~*'()SECRET_UPLOAD_PARAM%20"
        + "&filekey=file%2Fkey%20%3F%26%2B%3D!~*'()SECRET_FILEKEY"
    )
    assert _PRESIGNED not in client.calls[0][0]
    assert client.calls[0][1]["timeout"] == media.CDN_UPLOAD_TIMEOUT_SECONDS


def test_dynamic_upload_full_url_fallback_only_when_upload_param_absent(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_full_url=_PRESIGNED),
        outcomes=[_success_response()],
    )

    _upload(source)

    assert [url for url, _ in client.calls] == [_PRESIGNED]


def test_present_whitespace_upload_param_still_uses_official_static_route(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(
            upload_param="   ",
            upload_full_url=_PRESIGNED,
        ),
        outcomes=[_success_response()],
    )

    _upload(source)

    assert len(client.calls) == 1
    assert client.calls[0][0] == (
        _CDN_BASE + "/upload?encrypted_query_param=%20%20%20&filekey=" + "a" * 32
    )
    assert _PRESIGNED not in client.calls[0][0]


def test_present_empty_upload_param_does_not_use_dynamic_fallback(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(
            upload_param="",
            upload_full_url=_PRESIGNED,
        ),
        outcomes=[_success_response()],
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "get_upload_url_response",
        "message": "WeChat iLink returned unusable media upload parameters.",
        "retryable": False,
    }
    assert client.calls == []
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_present_upload_param_with_invalid_cdn_base_does_not_use_dynamic_fallback(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(
            upload_param=_UPLOAD_PARAM,
            upload_full_url=_PRESIGNED,
        ),
        outcomes=[_success_response()],
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source, cdn_base_url="not-an-http-url?SECRET_QUERY")

    assert raised.value.as_dict() == {
        "stage": "get_upload_url_response",
        "message": "WeChat iLink returned unusable media upload parameters.",
        "retryable": False,
    }
    assert client.calls == []
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_missing_both_upload_parameters_fails_at_redacted_get_upload_stage(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(),
        outcomes=[_success_response()],
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "get_upload_url_response",
        "message": "WeChat iLink did not return usable media upload parameters.",
        "retryable": False,
    }
    assert client.calls == []
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_get_upload_url_http_failure_is_stage_aware_and_redacted(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    request = httpx.Request(
        "POST", "https://ilink.example.invalid/getuploadurl?token=SECRET_QUERY"
    )
    response = httpx.Response(429, request=request, text="RAW_PROVIDER_BODY")

    async def fail_get_upload_url(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "RAW_PROVIDER_BODY SECRET_BOT_TOKEN",
            request=request,
            response=response,
        )

    monkeypatch.setattr(media.api, "get_upload_url", fail_get_upload_url)

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "get_upload_url_http",
        "message": "WeChat iLink rejected the upload-URL request (HTTP 429).",
        "retryable": True,
    }
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_get_upload_url_unusable_response_is_stage_aware_and_redacted(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)

    async def fail_get_upload_url(*args, **kwargs):
        raise ValueError("RAW_PROVIDER_BODY SECRET_QUERY")

    monkeypatch.setattr(media.api, "get_upload_url", fail_get_upload_url)

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "get_upload_url_response",
        "message": "WeChat iLink returned an unusable upload-URL response.",
        "retryable": False,
    }
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_get_upload_url_transport_failure_is_stage_aware_and_redacted(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    request = httpx.Request(
        "POST", "https://ilink.example.invalid/getuploadurl?token=SECRET_QUERY"
    )

    async def fail_get_upload_url(*args, **kwargs):
        raise httpx.ConnectError("contains SECRET_BOT_TOKEN", request=request)

    monkeypatch.setattr(media.api, "get_upload_url", fail_get_upload_url)

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "get_upload_url_transport",
        "message": "Could not reach WeChat iLink to obtain a media upload URL.",
        "retryable": True,
    }
    _assert_redacted(raised.value.as_dict(), local_path=source)


@pytest.mark.parametrize("first_status", [503, 429])
def test_retryable_http_status_retries_then_succeeds(
    first_status, tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[
            FakeResponse(
                first_status,
                headers={"x-error-message": "RAW_X_ERROR_MESSAGE"},
                text="RAW_PROVIDER_BODY",
            ),
            _success_response(),
        ],
    )

    info = _upload(source)

    assert info.cdn_media.encrypt_query_param == _DOWNLOAD_PARAM
    assert len(client.calls) == 2


def test_transport_tls_failure_retries_then_succeeds(tmp_path, monkeypatch):
    source = _source(tmp_path)
    request = httpx.Request("POST", _CDN_BASE + "/upload?SECRET_QUERY")
    tls_error = httpx.ConnectError(
        "TLS RAW_PROVIDER_BODY",
        request=request,
    )
    tls_error.__cause__ = ssl.SSLError("SECRET_QUERY TLS handshake failure")
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[tls_error, _success_response()],
    )

    info = _upload(source)

    assert info.cdn_media.encrypt_query_param == _DOWNLOAD_PARAM
    assert len(client.calls) == 2


def test_other_4xx_fails_once_without_provider_detail_leak(tmp_path, monkeypatch):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[
            FakeResponse(
                403,
                headers={"x-error-message": "RAW_X_ERROR_MESSAGE"},
                text="RAW_PROVIDER_BODY SECRET_QUERY",
            ),
            _success_response(),
        ],
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "cdn_upload_http",
        "message": "The WeChat CDN rejected the media upload (HTTP 403).",
        "retryable": False,
        "endpoint_host": "static-cdn.example.invalid",
    }
    assert len(client.calls) == 1
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_retryable_failure_stops_after_exactly_three_attempts_and_stays_redacted(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[
            FakeResponse(
                503,
                headers={"x-error-message": "RAW_X_ERROR_MESSAGE"},
                text="RAW_PROVIDER_BODY SECRET_QUERY",
            )
            for _ in range(media.CDN_UPLOAD_MAX_ATTEMPTS)
        ] + [_success_response()],
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "cdn_upload_http",
        "message": "The WeChat CDN rejected the media upload (HTTP 503).",
        "retryable": True,
        "endpoint_host": "static-cdn.example.invalid",
    }
    assert len(client.calls) == media.CDN_UPLOAD_MAX_ATTEMPTS == 3
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_http_200_missing_encrypted_metadata_retries_then_succeeds(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[
            FakeResponse(200, text="RAW_PROVIDER_BODY"),
            FakeResponse(200, body={"download_param": _DOWNLOAD_PARAM}, text="{}"),
        ],
    )

    info = _upload(source)

    assert info.cdn_media.encrypt_query_param == _DOWNLOAD_PARAM
    assert len(client.calls) == 2


def test_http_200_unusable_header_falls_through_to_valid_json_reference(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[FakeResponse(
            200,
            headers={"x-encrypted-param": "   "},
            body={"encrypt_query_param": 123, "download_param": _DOWNLOAD_PARAM},
            text="{}",
        )],
    )

    info = _upload(source)

    assert info.cdn_media.encrypt_query_param == _DOWNLOAD_PARAM
    assert len(client.calls) == 1


def test_http_200_nonstring_metadata_is_missing_and_retried(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[
            FakeResponse(
                200,
                body={"encrypt_query_param": {"secret": "RAW_PROVIDER_BODY"}},
                text="{}",
            ),
            _success_response(),
        ],
    )

    info = _upload(source)

    assert info.cdn_media.encrypt_query_param == _DOWNLOAD_PARAM
    assert len(client.calls) == 2


def test_http_200_missing_metadata_exhaustion_is_structured_and_redacted(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[
            FakeResponse(200, text="RAW_PROVIDER_BODY SECRET_QUERY")
            for _ in range(media.CDN_UPLOAD_MAX_ATTEMPTS)
        ],
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "cdn_upload_response",
        "message": (
            "The WeChat CDN accepted the upload but did not return the required "
            "encrypted media reference."
        ),
        "retryable": True,
        "endpoint_host": "static-cdn.example.invalid",
    }
    assert len(client.calls) == 3
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_cdn_client_construction_exception_is_redacted_response_failure(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)

    async def get_upload_url(*args, **kwargs):
        return GetUploadUrlResp(upload_full_url=_PRESIGNED)

    monkeypatch.setattr(media.api, "get_upload_url", get_upload_url)
    monkeypatch.setattr(
        media.httpx,
        "AsyncClient",
        lambda: (_ for _ in ()).throw(ValueError("RAW_PROVIDER_BODY SECRET_QUERY")),
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "cdn_upload_response",
        "message": "The WeChat CDN upload request could not be prepared.",
        "retryable": False,
        "endpoint_host": "upload.example.invalid",
    }
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_unexpected_cdn_client_exception_is_redacted_response_failure(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_full_url=_PRESIGNED),
        outcomes=[httpx.InvalidURL("RAW_PROVIDER_BODY SECRET_QUERY")],
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "cdn_upload_response",
        "message": "The WeChat CDN upload request could not be prepared.",
        "retryable": False,
        "endpoint_host": "upload.example.invalid",
    }
    assert len(client.calls) == 1
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_cdn_tls_exhaustion_exposes_hostname_only(tmp_path, monkeypatch):
    source = _source(tmp_path)
    failures = []
    for _ in range(media.CDN_UPLOAD_MAX_ATTEMPTS):
        request = httpx.Request("POST", _PRESIGNED)
        exc = httpx.ConnectError("RAW_PROVIDER_BODY", request=request)
        exc.__cause__ = ssl.SSLError("SECRET_QUERY TLS failure")
        failures.append(exc)
    client = _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_full_url=_PRESIGNED),
        outcomes=failures,
    )

    with pytest.raises(media.OutboundMediaError) as raised:
        _upload(source)

    assert raised.value.as_dict() == {
        "stage": "cdn_upload_transport",
        "message": "Could not connect to or upload bytes to the WeChat CDN.",
        "retryable": True,
        "endpoint_host": "upload.example.invalid",
    }
    assert len(client.calls) == 3
    _assert_redacted(raised.value.as_dict(), local_path=source)


def test_upload_success_preserves_encryption_sizes_and_media_item_behavior(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    monkeypatch.setattr(media.secrets, "token_bytes", lambda size: b"k" * size)
    _install_upload_mocks(
        monkeypatch,
        upload_resp=GetUploadUrlResp(upload_param="param"),
        outcomes=[_success_response()],
    )

    info = _upload(source)
    item = media.make_media_item(info, source)

    assert info.raw_size == len(source.read_bytes())
    assert info.ciphertext_size == media._encrypted_size(info.raw_size)
    assert info.cdn_media.encrypt_query_param == _DOWNLOAD_PARAM
    assert info.cdn_media.encrypt_type == 1
    assert info.cdn_media.aes_key == "NmI2YjZiNmI2YjZiNmI2YjZiNmI2YjZiNmI2YjZiNmI="
    assert item.image_item is not None
    assert item.image_item.media is info.cdn_media
    assert item.image_item.mid_size == info.ciphertext_size


def test_manager_passes_configured_cdn_base(tmp_path, monkeypatch):
    source = _source(tmp_path, "report.html")
    configured = "https://configured-cdn.example.invalid/custom"
    manager = _manager(tmp_path, cdn_base_url=configured)
    captured: dict[str, object] = {}

    async def fake_upload(path, base_url, token, user_id, *, cdn_base_url):
        captured.update({
            "path": path,
            "base_url": base_url,
            "token": token,
            "user_id": user_id,
            "cdn_base_url": cdn_base_url,
        })
        return object()

    async def fake_send(*args, **kwargs):
        return None

    def fake_run(coro, **kwargs):
        return _run(coro)

    monkeypatch.setattr(media, "upload_media", fake_upload)
    monkeypatch.setattr(media, "make_media_item", lambda info, path: object())
    monkeypatch.setattr(manager_mod.api, "send_message", fake_send)
    monkeypatch.setattr(manager, "_run_async", fake_run)

    result = manager._handle_send({
        "user_id": "SECRET_USER",
        "media_path": str(source),
    })

    assert result["status"] == "ok"
    assert captured["cdn_base_url"] == configured
    assert captured["path"] == source


def test_media_operation_timeout_budget_exceeds_all_inner_request_budgets():
    complete_inner_budget = (
        media.GET_UPLOAD_URL_TIMEOUT_SECONDS
        + media.CDN_UPLOAD_MAX_ATTEMPTS * media.CDN_UPLOAD_TIMEOUT_SECONDS
    )
    assert manager_mod.MEDIA_OPERATION_TIMEOUT_SECONDS > complete_inner_budget
    assert manager_mod.MEDIA_OPERATION_TIMEOUT_SECONDS == 390.0


def test_run_async_outer_timeout_cancels_reconciles_and_raises_structured_failure(
    tmp_path,
):
    manager = _manager(tmp_path)
    loop = asyncio.new_event_loop()
    thread = manager_mod.threading.Thread(
        target=loop.run_forever,
        daemon=True,
    )
    thread.start()
    manager._loop = loop
    started = manager_mod.threading.Event()
    reconciled = manager_mod.threading.Event()

    async def operation():
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            reconciled.set()

    expected = media.OutboundMediaError(
        stage="media_operation_timeout",
        message="The local WeChat media operation exceeded its deadline.",
        retryable=True,
    )
    try:
        with pytest.raises(media.OutboundMediaError) as raised:
            manager._run_async(operation(), timeout=0.05, timeout_error=expected)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        loop.close()

    assert started.is_set()
    assert reconciled.is_set()
    assert raised.value is expected
    assert raised.value.as_dict() == {
        "stage": "media_operation_timeout",
        "message": "The local WeChat media operation exceeded its deadline.",
        "retryable": True,
    }


def _timeout_fake_run(calls: list[tuple[str, float, str | None]]):
    def fake_run(coro, *, timeout=30, timeout_error=None):
        name = coro.cr_code.co_name
        stage = getattr(timeout_error, "stage", None)
        calls.append((name, timeout, stage))
        coro.close()
        if name == "upload_media":
            assert isinstance(timeout_error, media.OutboundMediaError)
            raise timeout_error
        return None

    return fake_run


def test_outer_timeout_after_text_delivery_persists_one_nonretryable_partial(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path, "report.html")
    manager = _manager(tmp_path)
    calls: list[tuple[str, float, str | None]] = []
    monkeypatch.setattr(manager, "_run_async", _timeout_fake_run(calls))

    result = manager._handle_send({
        "user_id": "SECRET_USER",
        "text": "already delivered",
        "media_path": str(source),
    })

    assert result["status"] == "partial"
    assert result["partial_delivery"] is True
    assert result["text_status"] == "sent"
    assert result["media_status"] == "failed"
    assert result["automatic_retry_allowed"] is False
    assert result["failure"] == {
        "stage": "media_operation_timeout",
        "message": "The local WeChat media operation exceeded its deadline.",
        "retryable": True,
    }
    assert calls == [
        ("send_message", 30, None),
        (
            "upload_media",
            manager_mod.MEDIA_OPERATION_TIMEOUT_SECONDS,
            "media_operation_timeout",
        ),
    ]
    persisted = manager._load_sent_messages()
    assert len(persisted) == 1
    assert persisted[0]["status"] == "partial"
    assert persisted[0]["text"] == "already delivered"
    assert persisted[0]["media_name"] == "report.html"
    assert persisted[0]["automatic_retry_allowed"] is False
    assert "media_path" not in persisted[0]
    _assert_redacted(result, local_path=source)
    _assert_redacted(persisted[0]["failure"], local_path=source)


def test_media_only_outer_timeout_is_not_persisted(tmp_path, monkeypatch):
    source = _source(tmp_path, "report.html")
    manager = _manager(tmp_path)
    calls: list[tuple[str, float, str | None]] = []
    monkeypatch.setattr(manager, "_run_async", _timeout_fake_run(calls))

    result = manager._handle_send({
        "user_id": "SECRET_USER",
        "media_path": str(source),
    })

    assert result == {
        "status": "failed",
        "error": "The local WeChat media operation exceeded its deadline.",
        "media_status": "failed",
        "failure": {
            "stage": "media_operation_timeout",
            "message": "The local WeChat media operation exceeded its deadline.",
            "retryable": True,
        },
        "automatic_retry_allowed": True,
    }
    assert manager._load_sent_messages() == []
    assert calls == [
        (
            "upload_media",
            manager_mod.MEDIA_OPERATION_TIMEOUT_SECONDS,
            "media_operation_timeout",
        ),
    ]
    _assert_redacted(result, local_path=source)


def test_text_plus_media_upload_failure_returns_and_persists_partial_result(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path, "report.html")
    manager = _manager(tmp_path)

    def fake_run(coro, *, timeout=30, timeout_error=None):
        name = coro.cr_code.co_name
        coro.close()
        if name == "upload_media":
            raise media.OutboundMediaError(
                stage="cdn_upload_transport",
                message="Could not connect to or upload bytes to the WeChat CDN.",
                endpoint_host="upload.example.invalid",
                retryable=True,
            )
        return None

    monkeypatch.setattr(manager, "_run_async", fake_run)
    result = manager._handle_send({
        "user_id": "SECRET_USER",
        "text": "already delivered",
        "media_path": str(source),
    })

    assert result["status"] == "partial"
    assert result["automatic_retry_allowed"] is False
    assert result["failure"]["stage"] == "cdn_upload_transport"
    persisted = manager._load_sent_messages()
    assert len(persisted) == 1
    assert persisted[0]["status"] == "partial"
    assert persisted[0]["media_name"] == "report.html"
    assert "media_path" not in persisted[0]
    _assert_redacted(result, local_path=source)


def test_final_media_message_failure_is_partial_and_redacted(tmp_path, monkeypatch):
    source = _source(tmp_path, "report.html")
    manager = _manager(tmp_path)
    send_calls = 0

    def fake_run(coro, *, timeout=30, timeout_error=None):
        nonlocal send_calls
        name = coro.cr_code.co_name
        coro.close()
        if name == "upload_media":
            return object()
        if name == "send_message":
            send_calls += 1
            if send_calls == 2:
                request = httpx.Request(
                    "POST",
                    "https://ilink.example.invalid/sendmessage?token=SECRET_QUERY",
                )
                raise httpx.ConnectError(
                    "RAW_PROVIDER_BODY SECRET_USER", request=request
                )
        return None

    monkeypatch.setattr(manager, "_run_async", fake_run)
    monkeypatch.setattr(media, "make_media_item", lambda info, path: object())
    result = manager._handle_send({
        "user_id": "SECRET_USER",
        "text": "already delivered",
        "media_path": str(source),
    })

    assert result["status"] == "partial"
    assert result["failure"] == {
        "stage": "media_message_transport",
        "message": "Could not reach WeChat iLink while sending the media message.",
        "retryable": True,
    }
    assert result["automatic_retry_allowed"] is False
    _assert_redacted(result, local_path=source)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            httpx.HTTPStatusError(
                "RAW_PROVIDER_BODY SECRET_QUERY",
                request=httpx.Request("POST", "https://ilink.example.invalid/send"),
                response=httpx.Response(
                    503,
                    request=httpx.Request("POST", "https://ilink.example.invalid/send"),
                ),
            ),
            {
                "stage": "media_message_http",
                "message": "WeChat iLink rejected the media message (HTTP 503).",
                "retryable": True,
            },
        ),
        (
            ValueError("RAW_PROVIDER_BODY SECRET_QUERY"),
            {
                "stage": "media_message_response",
                "message": "WeChat iLink did not accept the media message.",
                "retryable": False,
            },
        ),
    ],
)
def test_final_media_message_http_and_response_stages_are_redacted(exc, expected):
    failure = media.media_message_failure(exc).as_dict()
    assert failure == expected
    _assert_redacted(failure)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"media_name": "report.html"}, "[media: report.html]"),
        (
            {"media_path": "/private/historical/secret/report.html"},
            "[media: report.html]",
        ),
    ],
)
def test_handle_check_media_only_preview_uses_basename_only(
    record, expected, tmp_path,
):
    manager = _manager(tmp_path)
    msg_id = "legacy-or-new"
    msg_dir = tmp_path / "wechat" / "sent" / msg_id
    msg_dir.mkdir(parents=True)
    (msg_dir / "message.json").write_text(
        json.dumps({
            "id": msg_id,
            "to_user_id": "peer-user",
            "text": "",
            "date": "2026-08-12T00:00:00+00:00",
            **record,
        }),
        encoding="utf-8",
    )

    result = manager._handle_check({})

    assert result["conversations"][0]["latest"] == expected
    rendered = json.dumps(result)
    assert "/private/historical/secret" not in rendered


def test_successful_new_media_record_persists_basename_not_path(tmp_path):
    manager = _manager(tmp_path)
    msg_id = manager._persist_sent(
        user_id="peer-user",
        text="",
        status="sent",
        sent=["media (report.html)"],
        media_name="report.html",
    )

    persisted = manager._load_sent_messages()[0]
    assert persisted["id"] == msg_id
    assert persisted["media_name"] == "report.html"
    assert "media_path" not in persisted
    assert manager._message_display_text({
        **persisted,
        "_direction": "outgoing",
    }) == "[media: report.html]"
