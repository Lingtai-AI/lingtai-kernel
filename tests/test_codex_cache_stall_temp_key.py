"""Tests for the Codex cache-stall temporary-key protection (Jason's request).

Background: the Codex Responses adapter normally uses one stable
``prompt_cache_key`` / ``session-id`` / ``thread-id`` (all three byte-identical)
to maximize cache affinity. But if the backend keeps returning the *same*
``cached_tokens`` count request after request, the cache slot is stalled — the
prefix is no longer growing, so affinity is buying nothing. To break the stall
the session maintains a rolling queue of the last 5 cache-hit numbers; when all
5 are byte-identical it swaps to a *temporary* affinity id (one shared value for
all three) for the next request only, then reverts to the stable id.

The temporary id is a short, log-safe hash of the trigger event time (to the
second). When the swap happens the session emits an event to
``logs/events.jsonl`` carrying only safe metadata (no token values beyond the
recent cached-hit list, no prompt body, no secrets).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace

from lingtai.llm.openai.adapter import CodexOpenAIAdapter, CodexResponsesSession


@dataclass
class Event:
    type: str
    delta: str | None = None
    item: object | None = None
    response: object | None = None
    item_id: str | None = None
    text: str | None = None


class FakeResponses:
    def __init__(self, events_per_call: list[list[Event]]):
        self._events_per_call = events_per_call
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        idx = len(self.kwargs)
        self.kwargs.append(kwargs)
        yield from self._events_per_call[idx]


class FakeClient:
    def __init__(self, events_per_call: list[list[Event]]):
        self.responses = FakeResponses(events_per_call)


def _usage(cached: int) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        input_tokens_details=SimpleNamespace(cached_tokens=cached),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def _completed(cached: int) -> list[Event]:
    return [
        Event(
            "response.completed",
            response=SimpleNamespace(id="resp_fake", usage=_usage(cached)),
        )
    ]


def _expected_temp_id(epoch_seconds: int) -> str:
    """The temporary affinity id derived from the trigger time (to the second)."""
    token = f"codex-cache-stall:{int(epoch_seconds)}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


STABLE = "stableaa"  # 8-char-ish stable per-agent affinity id used in tests


def _make_session(cached_per_call, *, events=None, clock=None):
    """Build a CodexResponsesSession wired with a stable affinity id.

    ``cached_per_call`` is a list of cached_tokens numbers, one per send().
    ``events`` is an optional list to capture emitted events.
    ``clock`` is an optional zero-arg callable returning epoch seconds.
    """
    client = FakeClient([_completed(c) for c in cached_per_call])
    kw = dict(
        client=client,
        model="gpt-5.5",
        instructions="system prompt",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        prompt_cache_key=STABLE,
        session_id=STABLE,
        thread_id=STABLE,
    )
    if events is not None:
        kw["event_sink"] = events.append
    if clock is not None:
        kw["time_fn"] = clock
    return CodexResponsesSession(**kw)


def test_no_swap_when_cached_values_vary():
    """Five sends with differing cache hits keep the stable id throughout."""
    session = _make_session([10, 20, 30, 40, 50, 60])

    for _ in range(6):
        session.send("hi")

    for sent in session._client.responses.kwargs:
        assert sent["prompt_cache_key"] == STABLE
        assert sent["extra_headers"]["session-id"] == STABLE
        assert sent["extra_headers"]["thread-id"] == STABLE


def test_no_swap_before_five_identical_hits():
    """Four identical hits is not enough — the fifth send still uses stable id."""
    # cached: 5,5,5,5 recorded by sends 1..4; send 5 (the 5th request) decides
    # based on the queue AFTER 4 entries -> only 4 identical, no swap yet.
    session = _make_session([5, 5, 5, 5, 5])

    for _ in range(5):
        session.send("hi")

    for sent in session._client.responses.kwargs:
        assert sent["prompt_cache_key"] == STABLE
        assert sent["extra_headers"]["session-id"] == STABLE


def test_swap_to_temp_key_after_five_identical_hits():
    """Five identical positive cache hits -> the 6th request uses a temp id."""
    clock = lambda: 1_700_000_000  # noqa: E731  fixed trigger time
    session = _make_session([7, 7, 7, 7, 7, 99], clock=clock)

    for _ in range(6):
        session.send("hi")

    sent_sixth = session._client.responses.kwargs[5]
    temp = _expected_temp_id(1_700_000_000)
    # All three affinity levers carry the SAME temporary value on the swap call.
    assert sent_sixth["prompt_cache_key"] == temp
    assert sent_sixth["extra_headers"]["session-id"] == temp
    assert sent_sixth["extra_headers"]["thread-id"] == temp
    assert temp != STABLE

    # First five sends used the stable id.
    for sent in session._client.responses.kwargs[:5]:
        assert sent["prompt_cache_key"] == STABLE


def test_temp_key_is_one_shot_then_reverts_to_stable():
    """The temporary id applies to exactly one request, then stable resumes."""
    clock = lambda: 1_700_000_000  # noqa: E731
    # 5 identical -> swap on 6th. The 6th send records cached=99 (breaks the
    # run), so the 7th send must revert to the stable id.
    session = _make_session([7, 7, 7, 7, 7, 99, 0], clock=clock)

    for _ in range(7):
        session.send("hi")

    temp = _expected_temp_id(1_700_000_000)
    assert session._client.responses.kwargs[5]["prompt_cache_key"] == temp
    assert session._client.responses.kwargs[6]["prompt_cache_key"] == STABLE
    assert session._client.responses.kwargs[6]["extra_headers"]["session-id"] == STABLE


def test_swap_emits_event_to_sink():
    """A swap emits one safe event with the documented fields and no secrets."""
    events: list[dict] = []
    clock = lambda: 1_700_000_000  # noqa: E731
    session = _make_session([7, 7, 7, 7, 7, 99], events=events, clock=clock)

    for _ in range(6):
        session.send("hi")

    swap_events = [e for e in events if e.get("type") == "codex_cache_stall_temp_key"]
    assert len(swap_events) == 1
    ev = swap_events[0]
    temp = _expected_temp_id(1_700_000_000)
    assert ev["temporary_id_hash"] == temp
    assert ev["recent_cached_values"] == [7, 7, 7, 7, 7]
    assert ev["had_stable_id"] is True
    assert ev["model"] == "gpt-5.5"
    assert "reason" in ev and ev["reason"]

    # No secrets / no prompt body / no token-cost leakage beyond the cached list.
    blob = json.dumps(ev, default=str)
    assert STABLE not in blob  # the stable id itself is not disclosed
    assert "system prompt" not in blob
    assert "Authorization" not in blob and "Bearer" not in blob


def test_no_event_without_swap():
    """Varying cache hits never emit a swap event."""
    events: list[dict] = []
    session = _make_session([10, 20, 30, 40, 50, 60], events=events)

    for _ in range(6):
        session.send("hi")

    assert [e for e in events if e.get("type") == "codex_cache_stall_temp_key"] == []


def test_usage_extra_reflects_temp_ids_on_swap_request():
    """UsageMetadata.extra exposes the ACTUAL ids used on the swap request."""
    clock = lambda: 1_700_000_000  # noqa: E731
    session = _make_session([7, 7, 7, 7, 7, 99], clock=clock)

    results = [session.send("hi") for _ in range(6)]

    temp = _expected_temp_id(1_700_000_000)
    # Swap request (6th) carries the temp ids and exposes the cache key marker.
    assert results[5].usage.extra["codex_session_id"] == temp
    assert results[5].usage.extra["codex_thread_id"] == temp
    assert results[5].usage.extra["codex_prompt_cache_key"] == temp
    # Stable requests expose the stable id.
    assert results[0].usage.extra["codex_session_id"] == STABLE
    assert results[0].usage.extra["codex_prompt_cache_key"] == STABLE


def test_zero_cached_hits_do_not_count_toward_stall():
    """cached_tokens == 0 are misses, not hits, and never trigger a swap."""
    session = _make_session([0, 0, 0, 0, 0, 0, 0])

    for _ in range(7):
        session.send("hi")

    for sent in session._client.responses.kwargs:
        assert sent["prompt_cache_key"] == STABLE


# ---------------------------------------------------------------------------
# Default host wiring — the adapter writes swap events to logs/events.jsonl.
# ---------------------------------------------------------------------------


def _completed_event() -> Event:
    return Event("response.completed", response=SimpleNamespace(id="r", usage=_usage(7)))


def test_adapter_writes_swap_event_to_logs_events_jsonl(tmp_path):
    """A real Codex adapter emits the swap event to the agent's events.jsonl."""
    anchor = tmp_path / "init.json"
    anchor.write_text("{}", encoding="utf-8")

    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
        codex_session_anchor=str(anchor),
    )
    # Six requests: five identical positive hits arm the swap on the sixth.
    adapter._client = FakeClient([_completed(7) for _ in range(6)])
    session = adapter.create_chat("gpt-5.5", "system prompt")

    for _ in range(6):
        session.send("hi")

    events_path = tmp_path / "logs" / "events.jsonl"
    assert events_path.exists()
    lines = [json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
    swaps = [e for e in lines if e.get("type") == "codex_cache_stall_temp_key"]
    assert len(swaps) == 1
    assert swaps[0]["recent_cached_values"] == [7, 7, 7, 7, 7]
    assert swaps[0]["had_stable_id"] is True
    # The event carries no prompt body or OAuth secret.
    blob = json.dumps(swaps[0], default=str)
    assert "system prompt" not in blob and "Bearer" not in blob


def test_bare_adapter_has_no_event_sink():
    """A bare adapter (no anchor) builds no sink, so nothing is written."""
    adapter = CodexOpenAIAdapter(
        api_key="fake", base_url="http://fake", use_responses=True, force_responses=True
    )
    assert adapter._build_codex_event_sink() is None
