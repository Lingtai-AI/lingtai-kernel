"""Tests for explicit per-phase HTTP timeout construction in adapters.

Version-dependent failure mode this guards against: an LLM SDK built on the
``httpx2`` fork does not accept a foreign ``httpx.Timeout``, and HOW it breaks
depends on the SDK version. On anthropic 1.4.0/1.5.0 it fail-fasts with a
``TypeError`` at client construction / ``with_options`` / request build. On
anthropic 1.2.0 it does NOT raise — it silently mis-coerces, stuffing the whole
``Timeout`` object into every phase (connect/read/write/pool), so the per-phase
caps are lost with no error. A shallow "does not raise" check therefore
false-greens on 1.2.0; the authoritative check is the production-path test that
drives a real ``messages.create`` through an ``httpx2.MockTransport`` and
asserts the per-phase values the transport actually receives
(``test_*_timeout_reaches_transport_as_per_phase_floats``).

No ``pull_request``-triggered workflow exists in ``.github/workflows`` (the
``*-pr.yml`` jobs trigger on ``release: published`` / ``workflow_dispatch`` and
run only specific shell/windows/wheel test files), and no workflow there selects
this module. Treat these as a LOCAL guard — run the suite locally against a
fresh dependency resolve to exercise them. They also only track the real SDK surface while
``uv.lock`` stays uncommitted; a PR that commits a lockfile (or wires a real
``pull_request`` CI job) must add an explicit SDK version matrix (anthropic
across ``>=1.2`` including the httpx2-fork range), else this group silently
freezes on the locked version. The same premise backstops the
``getattr(..., "Timeout", httpx.Timeout)`` fallback's failure mode (a future SDK
dropping the re-export while still on httpx2 would fall back to a class that is
then rejected/mis-coerced).
"""
from __future__ import annotations

import anthropic
import httpx
import httpx2
import openai
import pytest

from lingtai.llm.openai.adapter import _build_http_timeout as openai_timeout
from lingtai.llm.anthropic.adapter import _build_http_timeout as anthropic_timeout


@pytest.fixture(autouse=True)
def _clear_read_timeout_env(monkeypatch):
    """Default tests must be immune to a developer's local env var."""
    monkeypatch.delenv("LINGTAI_LLM_READ_TIMEOUT", raising=False)


def _assert_timeout(t) -> None:
    # Duck-type the per-phase caps. The concrete class is the SDK's *own* Timeout
    # (``httpx.Timeout`` for older SDKs, ``httpx2.Timeout`` for SDKs that moved to
    # the httpx2 fork), not necessarily ``httpx.Timeout`` — asserting that exact
    # class here is what let the httpx2 incompatibility pass CI (see
    # test_*_timeout_accepted_by_installed_sdk).
    assert t.connect == 30.0
    assert t.read == 300.0
    assert t.write == 30.0
    assert t.pool == 10.0


def test_openai_timeout_caps_read_phase():
    _assert_timeout(openai_timeout(300.0))


def test_anthropic_timeout_caps_read_phase():
    _assert_timeout(anthropic_timeout(300.0))


def test_timeout_respects_shorter_retry_timeout():
    t = openai_timeout(10.0)
    assert t.connect == 10.0
    assert t.read == 10.0
    assert t.write == 10.0
    assert t.pool == 10.0


def test_timeout_read_cap_allows_thinking_models():
    # Thinking models (DeepSeek/GLM extended thinking) can take 60-180s; the
    # read phase must stay under the watchdog's retry_timeout (300s), not a
    # 60s cap that kills mid-thought.
    t = openai_timeout(300.0)
    assert t.read == 300.0
    assert anthropic_timeout(300.0).read == 300.0


def test_timeout_read_cap_default_when_env_unset():
    assert openai_timeout(300.0).read == 300.0
    assert anthropic_timeout(300.0).read == 300.0


def test_timeout_read_cap_env_override():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("LINGTAI_LLM_READ_TIMEOUT", "120")
    try:
        assert openai_timeout(300.0).read == 120.0
        assert anthropic_timeout(300.0).read == 120.0
    finally:
        monkeypatch.undo()


def test_timeout_read_cap_env_cannot_exceed_request_timeout():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("LINGTAI_LLM_READ_TIMEOUT", "120")
    try:
        # request_timeout is the upper bound; the env cap only lowers it.
        assert openai_timeout(60.0).read == 60.0
        assert anthropic_timeout(60.0).read == 60.0
    finally:
        monkeypatch.undo()


def test_timeout_read_cap_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("LINGTAI_LLM_READ_TIMEOUT", "not-a-number")
    assert openai_timeout(300.0).read == 300.0
    assert anthropic_timeout(300.0).read == 300.0


def test_timeout_none_passthrough():
    assert openai_timeout(None) is None
    assert anthropic_timeout(None) is None


# ---------------------------------------------------------------------------
# SDK-acceptance regression (the httpx2 incompatibility).
#
# The attribute-only tests above never hand the object to the SDK, so they
# stayed green while a real turn broke. The construct / with_options checks
# below are NECESSARY BUT NOT SUFFICIENT: they catch the fail-fast versions
# (anthropic 1.4/1.5 raise a TypeError here) but FALSE-GREEN on 1.2.0, which
# accepts the foreign object at these shallow entry points and only mis-coerces
# it deeper, at request build. The authoritative regression is the
# production-path test further down that asserts the per-phase values the
# transport actually receives. All of these make no network call.
# ---------------------------------------------------------------------------


def test_anthropic_timeout_is_sdk_native_class():
    # Positive type guard: a regression that returns a plain float (or any wrong
    # type) would still satisfy the duck-typed attribute checks above, so assert
    # the concrete class IS the anthropic SDK's own Timeout — httpx2.Timeout on
    # SDKs that moved to the fork, httpx.Timeout on older ones.
    # NOTE (self-reference): the expected class is derived with the SAME getattr
    # expression the implementation uses, so this cannot catch the getattr
    # approach itself being wrong — it only catches a wrong/absent return type.
    # The real oracle for "is the approach correct" is the production-path test
    # test_anthropic_timeout_reaches_transport_as_per_phase_floats below.
    assert type(anthropic_timeout(300.0)) is getattr(anthropic, "Timeout", httpx.Timeout)


def test_openai_timeout_is_httpx_timeout():
    # openai is intentionally NOT migrated in this change: openai 3.x accepts a
    # foreign httpx.Timeout (no fail-fast reject could be reproduced). If a future
    # openai adds the reject, test_openai_timeout_accepted_by_installed_sdk (below)
    # goes red and flags that a symmetric migration is then warranted.
    assert isinstance(openai_timeout(300.0), httpx.Timeout)


def test_anthropic_timeout_accepted_by_installed_sdk():
    t = anthropic_timeout(300.0)
    # client-construction path
    client = anthropic.Anthropic(api_key="test-no-network-call", timeout=t)
    # per-request path (adapter sets ``_request_timeout`` -> timeout kwarg)
    client.with_options(timeout=t)


def test_openai_timeout_accepted_by_installed_sdk():
    t = openai_timeout(300.0)
    client = openai.OpenAI(api_key="test-no-network-call", timeout=t)
    client.with_options(timeout=t)


def test_sdk_accepts_numeric_timeout_fallback():
    # A bare float is the documented fallback if per-phase construction is ever
    # unavailable; confirm both installed SDKs accept it on both paths.
    anthropic.Anthropic(api_key="test-no-network-call", timeout=300.0).with_options(
        timeout=300.0
    )
    openai.OpenAI(api_key="test-no-network-call", timeout=300.0).with_options(
        timeout=300.0
    )


# ---------------------------------------------------------------------------
# Production-path regression (the authoritative oracle).
#
# Drives a real ``.create(... timeout=...)`` through an httpx2.MockTransport and
# asserts the per-phase timeout the transport RECEIVES is the intended floats —
# not the shallow "does not raise". This is what catches anthropic 1.2.0's
# silent mis-coercion (the whole Timeout object stuffed into every phase), which
# the construct/with_options checks above false-green on. No network: the mock
# transport captures the request and aborts; ``max_retries=0`` avoids retry
# storms on the resulting transport error.
#
# Red on unfixed code (_build_http_timeout returning a raw httpx.Timeout):
#   - anthropic 1.4/1.5: fail-fast TypeError before the transport => no capture.
#   - anthropic 1.2.0:   transport receives {phase: <httpx.Timeout object>, ...}.
# Green only when the per-phase floats below reach the transport.
# ---------------------------------------------------------------------------

_EXPECTED_PHASES = {"connect": 30.0, "read": 300.0, "write": 30.0, "pool": 10.0}


class _StopAfterCapture(Exception):
    pass


def _timeout_seen_by_transport(make_client, do_request):
    captured: dict = {}

    def handler(request):
        captured["timeout"] = request.extensions.get("timeout")
        raise _StopAfterCapture()

    http_client = httpx2.Client(transport=httpx2.MockTransport(handler))
    client = make_client(http_client)
    # The SDK wraps the transport error; we only care about the captured timeout.
    with pytest.raises(Exception):
        do_request(client)
    return captured.get("timeout")


def test_anthropic_timeout_reaches_transport_as_per_phase_floats():
    seen = _timeout_seen_by_transport(
        lambda hc: anthropic.Anthropic(
            api_key="test-no-network-call", http_client=hc, max_retries=0
        ),
        lambda c: c.messages.create(
            model="claude-sonnet-x",
            max_tokens=1,
            messages=[{"role": "user", "content": "x"}],
            timeout=anthropic_timeout(300.0),
        ),
    )
    assert seen == _EXPECTED_PHASES


def test_openai_timeout_reaches_transport_as_per_phase_floats():
    # openai is intentionally left on httpx.Timeout; this confirms at the real
    # request path (not just "does not raise") that the current openai still
    # converts it to the intended per-phase floats — i.e. it neither rejects nor
    # mis-coerces it. Goes red if a future openai changes either way, which is
    # the signal to migrate openai the same way as anthropic.
    seen = _timeout_seen_by_transport(
        lambda hc: openai.OpenAI(
            api_key="test-no-network-call", http_client=hc, max_retries=0
        ),
        lambda c: c.chat.completions.create(
            model="gpt-x",
            messages=[{"role": "user", "content": "x"}],
            timeout=openai_timeout(300.0),
        ),
    )
    assert seen == _EXPECTED_PHASES
