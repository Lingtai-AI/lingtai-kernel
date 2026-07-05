import threading
import time

from lingtai.core.daemon import DaemonManager, _daemon_path_label, _daemon_quota_error_extra


class FakeQuotaError(Exception):
    status_code = 429
    body = {
        "error": {
            "type": "usage_limit_reached",
            "message": "The usage limit has been reached",
            "plan_type": "pro",
            "resets_at": 1783393160,
            "resets_in_seconds": 170117,
            "eligible_promo": None,
        }
    }


def test_daemon_quota_error_extra_extracts_openai_body():
    meta = _daemon_quota_error_extra(FakeQuotaError("boom"), provider="codex")

    quota = meta["quota"]
    assert quota["provider"] == "codex"
    assert quota["status_code"] == 429
    assert quota["error_type"] == "usage_limit_reached"
    assert quota["plan_type"] == "pro"
    assert quota["resets_at"] == 1783393160
    assert quota["resets_in_seconds"] == 170117
    assert quota["retry_after_seconds"] == 170117
    assert quota["resets_at_iso"].endswith("+00:00")


def test_daemon_quota_error_extra_includes_codex_auth_path_label():
    meta = _daemon_quota_error_extra(
        FakeQuotaError("boom"),
        provider="codex",
        session_metadata={
            "codex_auth_path_source": "explicit",
            "codex_auth_path_label": "~/.lingtai-tui/codex-auth/codex.json",
        },
    )

    quota = meta["quota"]
    assert quota["codex_auth_path_source"] == "explicit"
    assert quota["codex_auth_path_label"] == "~/.lingtai-tui/codex-auth/codex.json"


def test_daemon_path_label_collapses_home_and_fingerprints_external_absolute(tmp_path):
    home_label = _daemon_path_label("~/.lingtai-tui/codex-auth/codex.json")
    assert home_label == "~/.lingtai-tui/codex-auth/codex.json"

    external = tmp_path / "secrets" / "codex.json"
    label = _daemon_path_label(str(external))
    assert label.startswith("external:codex.json:")
    assert str(tmp_path) not in label




class StringOnlyQuotaError(Exception):
    def __str__(self):
        return (
            "Error code: 429 - {'error': {'type': 'usage_limit_reached', "
            "'message': 'The usage limit has been reached', 'plan_type': 'pro', "
            "'resets_at': 1783393160, 'resets_in_seconds': 170117}}"
        )


def test_daemon_quota_error_extra_parses_string_payload():
    meta = _daemon_quota_error_extra(StringOnlyQuotaError(), provider="codex")

    quota = meta["quota"]
    assert quota["error_type"] == "usage_limit_reached"
    assert quota["resets_at"] == 1783393160


class FakeResponse:
    status_code = 429
    headers = {
        "Retry-After": "17",
        "X-Request-Id": "req_123",
        "X-RateLimit-Reset-Requests": "3m12s",
        "Authorization": "Bearer should-not-leak",
    }

    def json(self):
        raise ValueError("no json")


class HeaderOnlyQuotaError(Exception):
    response = FakeResponse()

    def __str__(self):
        return "Error code: 429 - rate limit"


def test_daemon_quota_error_extra_extracts_allowlisted_headers():
    meta = _daemon_quota_error_extra(HeaderOnlyQuotaError(), provider="codex")

    quota = meta["quota"]
    assert quota["status_code"] == 429
    assert quota["retry_after_seconds"] == 17
    assert quota["headers"] == {
        "retry-after": "17",
        "x-request-id": "req_123",
        "x-ratelimit-reset-requests": "3m12s",
    }
    assert "authorization" not in quota["headers"]


class AccountStreamCapError(Exception):
    status_code = 429
    body = {
        "error": {
            "type": "rate_limit_exceeded",
            "code": "account_stream_cap",
            "message": "Too many concurrent streams for this account",
        }
    }


def test_daemon_quota_error_extra_classifies_account_stream_cap():
    meta = _daemon_quota_error_extra(AccountStreamCapError(), provider="codex")

    quota = meta["quota"]
    assert quota["status_code"] == 429
    assert quota["error_type"] == "rate_limit_exceeded"
    assert quota["code"] == "account_stream_cap"
    assert quota["limit_reason"] == "account_stream_cap"


def test_daemon_quota_error_extra_ignores_non_quota_error():
    assert _daemon_quota_error_extra(ValueError("plain boom"), provider="mimo") is None


def test_daemon_manager_records_provider_quota_backoff():
    manager = object.__new__(DaemonManager)
    manager._daemon_provider_quota_backoff = {}
    manager._daemon_provider_quota_lock = threading.Lock()
    reset = int(time.time()) + 120

    manager._remember_daemon_provider_quota_backoff(
        "codex",
        {
            "quota": {
                "error_type": "usage_limit_reached",
                "plan_type": "pro",
                "resets_at": reset,
                "resets_at_iso": "2026-07-07T00:00:00+00:00",
            }
        },
    )

    backoff = manager._active_daemon_provider_quota_backoff("codex")
    assert backoff["provider"] == "codex"
    assert backoff["error_type"] == "usage_limit_reached"
    assert backoff["plan_type"] == "pro"
    assert backoff["resets_at"] == reset
    assert backoff["retry_after_seconds"] > 0


def test_daemon_manager_ignores_expired_provider_quota_backoff():
    manager = object.__new__(DaemonManager)
    manager._daemon_provider_quota_backoff = {
        "codex": {"provider": "codex", "backoff_until": time.time() - 1}
    }
    manager._daemon_provider_quota_lock = threading.Lock()

    assert manager._active_daemon_provider_quota_backoff("codex") is None
    assert manager._daemon_provider_quota_backoff == {}
