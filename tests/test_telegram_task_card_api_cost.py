"""Telegram Task Card per-call API list-price line (hermetic, no network).

Unit/adapter-wire coverage for ``task_card/api_cost.py`` and the neutral
``usage_billing`` facts behind it. Manager live-append / rehydrate integration
lives in ``test_telegram_task_card_event_tail.py``; the Codex wire test lives in
``test_codex_prompt_cache_key.py``.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from lingtai.kernel.llm.base import UsageMetadata, checked_count, safe_billing_model
from lingtai.kernel.session import _usage_billing_for_event
from lingtai.llm.anthropic.adapter import _anthropic_billing_fields, _parse_response
from lingtai.llm.claude_code.adapter import _map_usage as claude_code_map_usage
from lingtai.llm.openai.adapter import (
    _parse_response as openai_chat_parse,
    _parse_responses_api_response,
)
from lingtai.mcp_servers.task_card.event_projection import TaskCardEventProjection
from lingtai.mcp_servers.telegram.task_card import api_cost

# Rates copied from the public LiteLLM gpt-6.1-sol entry (fixture use only).
SOL = {
    "input_cost_per_token": 2e-06,
    "output_cost_per_token": 1e-05,
    "cache_read_input_token_cost": 1e-07,
    "cache_creation_input_token_cost": 2.5e-06,
    "input_cost_per_token_above_272k_tokens": 4e-06,
    "output_cost_per_token_above_272k_tokens": 1.5e-05,
    "cache_read_input_token_cost_above_272k_tokens": 2e-07,
    "cache_creation_input_token_cost_above_272k_tokens": 5e-06,
}
TTL = {**SOL, "cache_creation_input_token_cost_above_1hr": 4e-06}


def _catalog_bytes(models: dict) -> bytes:
    return json.dumps({"sample_spec": {"x": 1}, **models}).encode()


def _ready_catalog(models: dict) -> api_cost.PriceCatalog:
    catalog = api_cost.PriceCatalog(
        lambda url, timeout, limit: _catalog_bytes(models), wall=lambda: 0
    )
    catalog.lookup("m")
    _wait(lambda: catalog.lookup("m")[0] != "loading")
    return catalog


def _wait(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


# ---------------------------------------------------------------- estimation


def test_standard_costs_do_not_double_charge_cache():
    bill = {"input": 1000, "cached": 400, "cache_write_tokens": 100, "billable_output_tokens": 50}
    costs = api_cost.estimate_costs(bill, SOL)
    assert set(costs) == {"miss", "hit", "output"}
    # ↑ cache miss = uncached input + priced cache writes; | hit = cache reads.
    assert costs["miss"] == pytest.approx(500 * 2e-06 + 100 * 2.5e-06)
    assert costs["hit"] == pytest.approx(400 * 1e-07)
    assert costs["output"] == pytest.approx(50 * 1e-05)


def test_no_cache_write_price_means_writes_bill_as_input():
    # OpenAI-style catalog entry: no cache_creation price at all, so the
    # whole cache-miss input bills at the input rate with or without a
    # reported write count.
    entry = {"input_cost_per_token": 1.25e-06, "output_cost_per_token": 1e-05,
             "cache_read_input_token_cost": 1.25e-07}
    bill = {"input": 294_200, "cached": 291_400, "billable_output_tokens": 63}
    costs = api_cost.estimate_costs(bill, entry)
    assert costs["miss"] == pytest.approx(2_800 * 1.25e-06)
    assert costs["hit"] == pytest.approx(291_400 * 1.25e-07)
    assert costs["output"] == pytest.approx(63 * 1e-05)
    assert api_cost.estimate_costs({**bill, "cache_write_tokens": 500}, entry)["miss"] == pytest.approx(2_800 * 1.25e-06)


def test_large_context_tier_uses_total_input_including_cache():
    # Uncached input is only 200k, but the TOTAL 300k crosses the 272k tier.
    bill = {"input": 300_000, "cached": 100_000, "cache_write_tokens": 0, "billable_output_tokens": 1000}
    costs = api_cost.estimate_costs(bill, SOL)
    assert costs["miss"] == pytest.approx(200_000 * 4e-06)
    assert costs["hit"] == pytest.approx(100_000 * 2e-07)
    assert costs["output"] == pytest.approx(1000 * 1.5e-05)


def test_present_but_invalid_tier_rate_never_falls_back_to_cheaper_base_rate():
    bill = {"input": 300_000, "cached": 0, "cache_write_tokens": 0, "billable_output_tokens": 10}
    invalid_tier = {**SOL, "input_cost_per_token_above_272k_tokens": None}
    costs = api_cost.estimate_costs(bill, invalid_tier)
    assert costs["miss"] is None  # unknown, NOT 300k * base 2e-06
    assert costs["output"] == pytest.approx(10 * 1.5e-05)
    # Same through the real parser: a junk tier price stays PRESENT (as None).
    models = api_cost.parse_catalog(_catalog_bytes({"m": {
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 1e-05,
        "input_cost_per_token_above_272k_tokens": "junk",
        "output_cost_per_token_above_272k_tokens": -5,
    }}))
    assert models["m"]["input_cost_per_token_above_272k_tokens"] is None
    costs = api_cost.estimate_costs(bill, models["m"])
    assert costs["miss"] is None and costs["output"] is None
    # Below the threshold the base rates still apply.
    small = {**bill, "input": 1000}
    assert api_cost.estimate_costs(small, models["m"])["miss"] == pytest.approx(1000 * 2e-06)


def test_ttl_split_and_unknown_split():
    known = {"input": 500, "cached": 0, "cache_write_tokens": 100,
             "cache_write_1h_tokens": 40, "billable_output_tokens": 0}
    assert api_cost.estimate_costs(known, TTL)["miss"] == pytest.approx(
        400 * 2e-06 + 60 * 2.5e-06 + 40 * 4e-06)
    unsplit = {k: v for k, v in known.items() if k != "cache_write_1h_tokens"}
    assert api_cost.estimate_costs(unsplit, TTL)["miss"] is None
    # Without a distinct 1h price the split cannot change the amount.
    assert api_cost.estimate_costs(unsplit, SOL)["miss"] == pytest.approx(400 * 2e-06 + 100 * 2.5e-06)


@pytest.mark.parametrize("entry", [TTL, SOL])
def test_one_hour_larger_than_total_write_is_rejected_not_negative(entry):
    bill = {"input": 500, "cached": 0, "cache_write_tokens": 100,
            "cache_write_1h_tokens": 150, "billable_output_tokens": 0}
    costs = api_cost.estimate_costs(bill, entry)
    assert costs["miss"] is None  # incoherent write split: the ↑ cost is unknown
    assert costs["hit"] == 0.0 and costs["output"] == 0.0
    # 1h with no write at all is equally incoherent.
    assert api_cost.estimate_costs({**bill, "cache_write_tokens": 0}, entry)["miss"] is None


@pytest.mark.parametrize("bad", [-1, True, 1.5, "3", float("nan")])
def test_malformed_one_hour_bucket_is_unknown_even_without_1h_price(bad):
    bill = {"input": 500, "cached": 0, "cache_write_tokens": 100,
            "cache_write_1h_tokens": bad, "billable_output_tokens": 0}
    for entry in (TTL, SOL):
        assert api_cost.estimate_costs(bill, entry)["miss"] is None
    for value in api_cost.estimate_costs(bill, TTL).values():
        assert value is None or value >= 0


def test_unknown_missing_invalid_never_become_zero():
    base = {"input": 100, "cached": 10, "cache_write_tokens": 0, "billable_output_tokens": 5}
    no_write = {k: v for k, v in base.items() if k != "cache_write_tokens"}
    costs = api_cost.estimate_costs(no_write, SOL)  # writes priced, count unknown
    assert costs["miss"] is None
    assert costs["hit"] is not None and costs["output"] is not None
    for bad in (-1, True, 1.5, float("nan"), "3", None):
        assert api_cost.estimate_costs({**base, "billable_output_tokens": bad}, SOL)["output"] is None
    absent = {k: v for k, v in base.items() if k != "billable_output_tokens"}
    assert api_cost.estimate_costs(absent, SOL)["output"] is None
    # cache read + write larger than total input is incoherent.
    assert api_cost.estimate_costs({**base, "cache_write_tokens": 500}, SOL)["miss"] is None
    # Missing rate: positive count unknown, known zero count stays a real 0.
    rateless = {"input_cost_per_token": 1e-06}
    costs = api_cost.estimate_costs(base, rateless)
    assert costs["output"] is None and costs["hit"] is None
    assert api_cost.estimate_costs({**base, "cached": 0, "billable_output_tokens": 0}, rateless)["hit"] == 0.0


def test_overflow_and_nonfinite_inputs_fail_unknown_without_raising():
    huge = 10**400
    bill = {"input": huge, "cached": huge, "cache_write_tokens": 0, "billable_output_tokens": huge}
    costs = api_cost.estimate_costs(bill, SOL)  # int*float raises OverflowError
    assert costs["hit"] is None and costs["output"] is None
    # Finite rate x finite count that overflows float range is unknown, not inf.
    bill = {"input": 10, "cached": 10, "cache_write_tokens": 0, "billable_output_tokens": 10}
    costs = api_cost.estimate_costs(bill, {"output_cost_per_token": 1e308, "cache_read_input_token_cost": 1e308})
    assert costs["output"] is None and costs["hit"] is None
    # A giant JSON rate (int too large for float, or 1e999) is dropped as invalid.
    payload = b'{"m": {"input_cost_per_token": 1e-6, "output_cost_per_token": 1' + b"0" * 400 + b"}}"
    assert api_cost.parse_catalog(payload)["m"]["output_cost_per_token"] is None
    assert api_cost.parse_catalog(b'{"m": {"input_cost_per_token": 1e-6, "output_cost_per_token": 1e999}}')[
        "m"]["output_cost_per_token"] is None


def test_line_never_leaks_inf_or_nan_and_total_overflow_is_unknown():
    entry = {"input_cost_per_token": 1e-06, "output_cost_per_token": 1e308,
             "cache_read_input_token_cost": 1e308}
    catalog = _ready_catalog({"big": entry})
    bill = {"model": "big", "input": 1, "cached": 1, "cache_write_tokens": 0, "billable_output_tokens": 1}
    line = api_cost.usage_line(1.0, {"output": 1, "bill": bill}, catalog)
    assert "inf" not in line.lower() and "nan" not in line.lower()
    assert line.startswith("cost ?")  # finite parts sum to inf: unknown


# ---------------------------------------------------------------- catalog


def test_parse_catalog_keeps_present_invalid_as_none_and_rejects_non_catalog():
    models = api_cost.parse_catalog(_catalog_bytes({
        "ok": {"input_cost_per_token": 1e-06, "output_cost_per_token": -1, "junk": 3,
               "cache_read_input_token_cost": None},
        "bad": {"input_cost_per_token": True},
        "notdict": 5,
    }))
    assert models == {"ok": {"input_cost_per_token": 1e-06, "output_cost_per_token": None,
                            "cache_read_input_token_cost": None}}
    with pytest.raises(ValueError):
        api_cost.parse_catalog(b"[]")


def test_line_complete_partial_and_unknown():
    catalog = _ready_catalog({"sol": SOL})
    full = {"output": 50, "bill": {"model": "sol", "input": 1000, "cached": 400,
                                    "cache_write_tokens": 120, "billable_output_tokens": 50}}
    line = api_cost.usage_line(2.0, full, catalog)
    assert line == "$0.0018 · ↓$0.0005 ↑$0.0013 | <$0.0001"
    assert "?" not in line and "+" not in line
    partial = {"output": 50, "bill": {"model": "sol", "input": 1000, "cached": 400, "billable_output_tokens": 50}}
    text = api_cost.usage_line(2.0, partial, catalog)
    # Writes priced separately but no write count recorded: ↑ is a lower bound
    # (all 600 cache-miss tokens at the cheaper input rate), marked "+", and so
    # is the total.
    assert text == "$0.0017+ · ↓$0.0005 ↑$0.0012+ | <$0.0001"
    nothing = {"output": 5, "bill": {"model": "sol", "input": 100, "cached": 0}}
    # Output unknown: "?" there; ↑ floor and the total are lower bounds.
    assert api_cost.usage_line(1.0, nothing, catalog) == "$0.0002+ · ↓? ↑$0.0002+ | $0"
    unpriced = {"output": 5, "bill": {"model": "sol", "input": 100, "cached": 10}}
    assert "↑$0.0002+" in api_cost.usage_line(1.0, unpriced, catalog)
    assert api_cost.usage_line(None, {"output": 5}, catalog) == "cost n/a (model unknown)"
    unlisted = {"output": 5, "bill": {"model": "nope", "input": 10, "cached": 0}}
    assert api_cost.usage_line(0, unlisted, catalog).endswith("n/a (model not listed)")
    estimated = {"output": 5, "bill": {"estimated": True}}
    assert api_cost.usage_line(1.0, estimated, catalog) == "cost n/a (estimated tokens)"
    assert api_cost.usage_line(1.0, None, catalog) == ""


def test_missing_write_count_floor_uses_cheapest_rate_and_never_exact():
    bill = {"input": 1000, "cached": 400, "billable_output_tokens": 50}
    costs, floors = api_cost.estimate_parts(bill, SOL)
    assert floors == {"miss"} and costs["miss"] == pytest.approx(600 * 2e-06)
    # The exact API still reports a floor-only part as unknown.
    assert api_cost.estimate_costs(bill, SOL)["miss"] is None
    # The floor uses the cheapest applicable rate, incl. a distinct 1h price.
    cheap_write = {**TTL, "cache_creation_input_token_cost": 1e-06}
    costs, floors = api_cost.estimate_parts(bill, cheap_write)
    assert floors == {"miss"} and costs["miss"] == pytest.approx(600 * 1e-06)
    # A present-but-invalid rate leaves no safe floor: still unknown.
    invalid = {**SOL, "cache_creation_input_token_cost": None}
    costs, floors = api_cost.estimate_parts(bill, invalid)
    assert costs["miss"] is None and not floors
    catalog = _ready_catalog({"bad": invalid})
    line = api_cost.usage_line(1.0, {"output": 50, "bill": {**bill, "model": "bad"}}, catalog)
    assert "↑?" in line and line.startswith("$") and "+ ·" in line
    # A recorded write count is exact: no floor, no "+".
    costs, floors = api_cost.estimate_parts({**bill, "cache_write_tokens": 0}, SOL)
    assert not floors and costs["miss"] == pytest.approx(600 * 2e-06)


def test_line_marks_estimate_and_never_invoice_or_routing_claims():
    catalog = _ready_catalog({"sol": SOL})
    priced = {"output": 50, "bill": {"model": "sol", "input": 1000, "cached": 400,
                                      "cache_write_tokens": 120, "billable_output_tokens": 50}}
    for line in (api_cost.usage_line(1.0, priced, catalog),
                 api_cost.usage_line(1.0, {"output": 5}, api_cost.PriceCatalog(lambda *a: b""))):
        assert line.startswith(("$", "<$", "cost "))  # a price or an explicit cost note
        for forbidden in ("bill", "invoice", "charged", "discount", "priority", "batch"):
            assert forbidden not in line.lower()


@pytest.mark.parametrize("delay", [None, 0, 2.0, 5e-324, float("inf"), float("nan")])
def test_line_never_shows_a_speed(delay):
    # The displayed API gap includes waiting/prefill/streaming, so no tok/s
    # figure is ever rendered, whatever the delay or token count.
    catalog = _ready_catalog({"sol": SOL})
    for usage in ({"output": 10}, {"output": 10**400},
                  {"output": 50, "bill": {"model": "sol", "input": 1000, "cached": 400,
                                          "cache_write_tokens": 120, "billable_output_tokens": 50}}):
        line = api_cost.usage_line(delay, usage, catalog)
        assert "tok/s" not in line and "inf" not in line.lower()


def test_catalog_first_use_is_nonblocking_and_single_flight():
    release = threading.Event()
    calls = []

    def fetch(url, timeout, limit):
        calls.append((url, timeout, limit))
        release.wait(2)
        return _catalog_bytes({"sol": SOL})

    catalog = api_cost.PriceCatalog(fetch)
    started = time.monotonic()
    assert [catalog.lookup("sol")[0] for _ in range(20)] == ["loading"] * 20
    assert time.monotonic() - started < 1.0
    release.set()
    _wait(lambda: catalog.lookup("sol")[0] == "ok")
    assert len(calls) == 1
    assert calls[0] == (api_cost.CATALOG_URL, api_cost.FETCH_TIMEOUT_S, api_cost.MAX_CATALOG_BYTES)


def test_catalog_failure_backs_off_then_recovers_and_stale_is_labelled():
    now = [0.0]
    outcomes = [ValueError("offline"), _catalog_bytes({"sol": SOL}), ValueError("offline again")]
    calls = []

    def fetch(url, timeout, limit):
        calls.append(now[0])
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    catalog = api_cost.PriceCatalog(
        fetch, clock=lambda: now[0], wall=lambda: 0, refresh_after_s=100, retry_after_s=10,
    )
    catalog.lookup("sol")
    _wait(lambda: catalog.lookup("sol")[0] == "unavailable")
    assert len(calls) == 1  # retry pacing: no new fetch inside the backoff window
    now[0] = 11.0
    catalog.lookup("sol")
    _wait(lambda: catalog.lookup("sol")[0] == "ok")
    now[0] = 500.0  # stale: refresh fails but the old snapshot stays, labelled
    catalog.lookup("sol")
    _wait(lambda: len(calls) == 3)
    _wait(lambda: not catalog._inflight)
    status, entry, as_of = catalog.lookup("sol")
    assert (status, as_of) == ("stale", "1970-01-01") and entry == SOL
    stale_line = api_cost.usage_line(
        1.0, {"output": 5, "bill": {"model": "sol", "input": 100, "cached": 0,
                                    "cache_write_tokens": 0, "billable_output_tokens": 5}}, catalog)
    assert stale_line.endswith(" stale prices")


def test_thread_start_failure_releases_inflight_and_paces_retry(monkeypatch):
    starts = []

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            starts.append(1)

        def start(self):
            raise RuntimeError("can't start new thread")

    now = [5.0]
    catalog = api_cost.PriceCatalog(
        lambda *a: _catalog_bytes({"sol": SOL}), clock=lambda: now[0], retry_after_s=10,
    )
    monkeypatch.setattr(api_cost.threading, "Thread", BrokenThread)
    assert catalog.lookup("sol")[0] == "unavailable"  # never stuck on "loading"
    assert catalog._inflight is False
    now[0] = 8.0
    assert catalog.lookup("sol")[0] == "unavailable" and len(starts) == 1  # paced
    monkeypatch.undo()
    now[0] = 16.0
    catalog.lookup("sol")
    _wait(lambda: catalog.lookup("sol")[0] == "ok")


class _FakeResponse:
    """Fake HTTP body: ``read1`` returns scripted chunks; records request sizes."""

    def __init__(self, chunks, on_read=None):
        self._chunks = iter(chunks)
        self._on_read = on_read
        self.sizes: list[int] = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def read1(self, n):
        self.sizes.append(n)
        if self._on_read:
            self._on_read()
        return next(self._chunks, b"")


def _fetch_with(response, **kwargs):
    seen = {}

    def opener(url, timeout):
        seen["call"] = (url, timeout)
        return response

    result = api_cost._http_fetch(api_cost.CATALOG_URL, 10.0, kwargs.pop("max_bytes", 100), opener=opener, **kwargs)
    return result, seen["call"]


def test_actual_fetch_reads_bounded_chunks_and_joins():
    response = _FakeResponse([b"abc", b"def", b"g"])
    data, call = _fetch_with(response, max_bytes=100)
    assert data == b"abcdefg" and response.closed
    assert call == (api_cost.CATALOG_URL, 10.0)
    assert max(response.sizes) <= api_cost._READ_CHUNK
    assert response.sizes[0] == min(api_cost._READ_CHUNK, 101)


def test_actual_fetch_size_cap_is_enforced_by_real_read_logic():
    assert _fetch_with(_FakeResponse([b"1234", b"5678", b"90"]), max_bytes=10)[0] == b"1234567890"
    response = _FakeResponse([b"1234", b"5678", b"901"] + [b"x"] * 1000)
    with pytest.raises(ValueError, match="too large"):
        _fetch_with(response, max_bytes=10)
    assert response.closed and len(response.sizes) == 3  # stopped at the cap, not drained


def test_actual_fetch_slow_drip_hits_monotonic_deadline():
    clock = [100.0]

    def tick():
        clock[0] += 1.0  # each drip costs one second of monotonic time

    response = _FakeResponse(iter(lambda: b"x", None), on_read=tick)  # endless 1-byte drip
    with pytest.raises(TimeoutError, match="deadline"):
        _fetch_with(response, max_bytes=10**9, deadline_s=5.0, monotonic=lambda: clock[0])
    assert response.closed and len(response.sizes) <= 6


def test_actual_fetch_deadline_constants_are_documented_bounds():
    assert api_cost.FETCH_DEADLINE_S > api_cost.FETCH_TIMEOUT_S > 0
    assert api_cost.MAX_CATALOG_BYTES == 8 * 1024 * 1024


def test_oversize_payload_degrades_via_catalog_failure():
    def fetch(url, timeout, limit):
        raise ValueError("catalog too large")

    catalog = api_cost.PriceCatalog(fetch)
    catalog.lookup("sol")
    _wait(lambda: catalog.lookup("sol")[0] == "unavailable")


# ---------------------------------------------------------------- projection


def _llm_event(**extra):
    return {"type": "llm_response", "api_call_id": "a1", "input_tokens": 1000,
            "output_tokens": 50, "thinking_tokens": 20, "cached_tokens": 400, **extra}


def test_projection_keeps_only_validated_billing_facts():
    _, usage = TaskCardEventProjection.project_llm_response_usage(_llm_event(usage_billing={
        "model": "sol", "cache_write_tokens": 100, "cache_write_1h_tokens": True,
        "billable_output_tokens": -3, "api_key": "secret",
    }))
    assert usage["bill"] == {"input": 1000, "cached": 400, "model": "sol", "cache_write_tokens": 100}
    # Legacy events and estimated rounds gain no invented facts.
    _, legacy = TaskCardEventProjection.project_llm_response_usage(_llm_event())
    assert "bill" not in legacy
    _, estimated = TaskCardEventProjection.project_llm_response_usage(
        _llm_event(estimated=True, usage_billing={"model": "sol"}))
    assert estimated["bill"] == {"estimated": True}
    # A hostile model string is dropped (model unknown), counts are kept.
    _, hostile = TaskCardEventProjection.project_llm_response_usage(_llm_event(usage_billing={
        "model": "https://evil.example/x?token=1", "billable_output_tokens": 5}))
    assert hostile["bill"] == {"input": 1000, "cached": 400, "billable_output_tokens": 5}


def test_carrier_cannot_overwrite_precise_facts_across_batches():
    _, from_llm = TaskCardEventProjection.project_llm_response_usage(
        _llm_event(usage_billing={"model": "sol", "billable_output_tokens": 50}))
    carrier = {"output": 50, "cache_miss": 600, "context": 1000}
    group = {"events": [{"_tool_call_id": "t1", "_api_call_id": "a1"}]}
    # Same batch: tool-id carrier wins the base numbers but inherits the bill.
    assert TaskCardEventProjection.apply_tool_usages(
        [group], {"t1": carrier, "a1": from_llm}) is True
    assert group["events"][0]["_usage"]["bill"]["model"] == "sol"
    # Later batch: only the carrier arrives; the row keeps its bill.
    TaskCardEventProjection.apply_tool_usages([group], {"t1": {**carrier, "cache_miss": 601}})
    assert group["events"][0]["_usage"]["cache_miss"] == 601
    assert group["events"][0]["_usage"]["bill"]["model"] == "sol"
    # A different round's facts are never attached.
    other = {"events": [{"_tool_call_id": "t2", "_api_call_id": "a2"}]}
    TaskCardEventProjection.apply_tool_usages([other], {"t2": carrier, "a1": from_llm})
    assert "bill" not in other["events"][0]["_usage"]


def _groups(usage):
    row = {"kind": "tool", "tool": "email", "tool_action": "send", "reasoning": "r",
           "done": True, "status": "success", "_ts": 1.0, "api_delay_s": 2.0, "_usage": usage}
    return [{"events": [row]}]


def test_render_hook_adds_exactly_one_line_after_metrics_and_default_is_unchanged():
    usage = {"output": 155, "cache_miss": 5000, "context": 314000, "cache_rate": 0.984}
    groups = _groups(usage)
    default = TaskCardEventProjection.render_event_groups(groups, normal_rows=3)
    same = TaskCardEventProjection.render_event_groups(groups, normal_rows=3, usage_line=lambda d, u: "")
    assert same == default
    calls = []

    def hook(delay, u):
        calls.append((delay, u))
        return "EXTRA-LINE"

    text = TaskCardEventProjection.render_event_groups(groups, normal_rows=3, usage_line=hook)
    lines = text.splitlines()
    metrics = next(i for i, line in enumerate(lines) if "↻ 2.0s" in line)
    assert lines[metrics + 1] == "EXTRA-LINE" and text.count("EXTRA-LINE") == 1
    assert calls == [(2.0, usage)]


def test_default_shared_render_ignores_bill_byte_for_byte():
    """Feishu/non-Telegram callers pass no hook: ``bill`` must be inert to them."""
    plain = {"output": 155, "cache_miss": 5000, "context": 314000, "cache_rate": 0.984}
    billed = {**plain, "bill": {"model": "sol", "input": 314000, "cached": 309000,
                                "cache_write_tokens": 0, "billable_output_tokens": 155}}
    without = TaskCardEventProjection.render_event_groups(_groups(plain), normal_rows=3)
    with_bill = TaskCardEventProjection.render_event_groups(_groups(billed), normal_rows=3)
    assert with_bill == without
    assert "STANDARD" not in with_bill and "LiteLLM" not in with_bill and "$" not in with_bill


def test_render_pure_text_group_with_bill_gets_line():
    _, usage = TaskCardEventProjection.project_llm_response_usage(
        _llm_event(usage_billing={"model": "sol", "cache_write_tokens": 0, "billable_output_tokens": 50}))
    catalog = _ready_catalog({"sol": SOL})
    text = TaskCardEventProjection.render_event_groups(
        [{"events": [{"kind": "text", "text": "hello", "_ts": 1.0, "api_delay_s": 2.0, "_usage": usage}]}],
        normal_rows=3, usage_line=lambda d, u: api_cost.usage_line(d, u, catalog))
    assert "\n$0." in text and " · ↓$" in text and " ↑" in text and " | " in text


# ---------------------------------------------------------------- providers


def test_checked_count_and_safe_billing_model_helpers():
    assert [checked_count(v) for v in (0, 7, None, -1, True, 1.5, "3")] == [0, 7, None, None, None, None, None]
    for ok in ("gpt-5.5", "openai/gpt-5.5", " sol ", "claude-sonnet-4-5-20250929", "gemini/gemini-2.5-pro"):
        assert safe_billing_model(ok) == ok.strip()
    for bad in ("", "  ", None, 5, "https://x.example/m", "m?key=1", "a b", "m\nx", "x" * 129,
                "../m", "a//b", "m#frag", "m;rm", "Bearer sk-1 x", "m=1"):
        assert safe_billing_model(bad) is None


def test_session_billing_event_helper_is_bounded_and_backward_compatible():
    assert UsageMetadata().cache_write_tokens is None
    usage = UsageMetadata(cache_write_tokens=7, billable_output_tokens=9,
                          extra={"api_key": "secret"})
    assert _usage_billing_for_event(usage, " sol ") == {
        "model": "sol", "cache_write_tokens": 7, "billable_output_tokens": 9}
    assert _usage_billing_for_event(UsageMetadata(), None) is None
    assert _usage_billing_for_event(UsageMetadata(), "x" * 200) is None
    # Exact provider-prefixed catalog names pass; URLs/queries/newlines do not.
    assert _usage_billing_for_event(UsageMetadata(), "openai/gpt-5.5") == {"model": "openai/gpt-5.5"}
    for bad in ("https://h/m", "m?x=1", "m\nn", "m n"):
        assert _usage_billing_for_event(UsageMetadata(), bad) is None
    bad_counts = UsageMetadata(cache_write_tokens=True, billable_output_tokens=-1)
    assert _usage_billing_for_event(bad_counts, "sol") == {"model": "sol"}


def test_anthropic_wire_preserves_write_ttl_and_billable_output():
    raw = SimpleNamespace(content=[], stop_reason="end_turn", usage=SimpleNamespace(
        input_tokens=10, output_tokens=30, cache_read_input_tokens=200,
        cache_creation_input_tokens=100,
        cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=40, ephemeral_5m_input_tokens=60)))
    usage = _parse_response(raw).usage
    assert (usage.input_tokens, usage.cached_tokens, usage.output_tokens) == (310, 200, 30)
    assert (usage.cache_write_tokens, usage.cache_write_1h_tokens, usage.billable_output_tokens) == (100, 40, 30)
    # A wire without cache_creation fields states nothing about writes.
    bare = _parse_response(SimpleNamespace(content=[], stop_reason="end_turn", usage=SimpleNamespace(
        input_tokens=10, output_tokens=3))).usage
    assert bare.cache_write_tokens is None and bare.cache_write_1h_tokens is None


def test_anthropic_stream_helper_only_reports_explicit_valid_counts():
    """The stream path builds its billing fields with this same helper."""
    stream_usage = SimpleNamespace(
        input_tokens=5, output_tokens=12, cache_creation_input_tokens=20,
        cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=8))
    assert _anthropic_billing_fields(stream_usage) == {
        "cache_write_tokens": 20, "cache_write_1h_tokens": 8, "billable_output_tokens": 12}
    # 1h > write is an incoherent split: the TTL part is not reported at all.
    incoherent = SimpleNamespace(output_tokens=1, cache_creation_input_tokens=5,
                                 cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=9))
    assert _anthropic_billing_fields(incoherent) == {"cache_write_tokens": 5, "billable_output_tokens": 1}
    assert _anthropic_billing_fields(SimpleNamespace(output_tokens=True, cache_creation_input_tokens=-1)) == {}
    assert _anthropic_billing_fields(SimpleNamespace()) == {}


def _chat_raw(usage):
    message = SimpleNamespace(content="hi", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def test_openai_chat_wire_billable_output_and_unknown_write():
    usage = openai_chat_parse(_chat_raw(SimpleNamespace(
        prompt_tokens=100, completion_tokens=30,
        prompt_tokens_details=SimpleNamespace(cached_tokens=10),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=5)))).usage
    # completion_tokens already includes reasoning: not summed again.
    assert (usage.input_tokens, usage.output_tokens, usage.thinking_tokens) == (100, 30, 5)
    assert usage.billable_output_tokens == 30
    assert usage.cache_write_tokens is None and usage.cache_write_1h_tokens is None
    absent = openai_chat_parse(_chat_raw(SimpleNamespace(prompt_tokens=100, completion_tokens=None))).usage
    assert absent.billable_output_tokens is None and absent.output_tokens == 0
    for bad in (None, -1, True):
        got = openai_chat_parse(_chat_raw(SimpleNamespace(prompt_tokens=1, completion_tokens=bad))).usage
        assert got.billable_output_tokens is None


def test_openai_responses_wire_billable_output_includes_reasoning_once():
    def raw(usage):
        return SimpleNamespace(output=[], usage=usage)

    usage = _parse_responses_api_response(raw(SimpleNamespace(
        input_tokens=100, output_tokens=40,
        input_tokens_details=SimpleNamespace(cached_tokens=30),
        output_tokens_details=SimpleNamespace(reasoning_tokens=25)))).usage
    assert (usage.output_tokens, usage.thinking_tokens, usage.billable_output_tokens) == (40, 25, 40)
    assert usage.cache_write_tokens is None
    missing = _parse_responses_api_response(raw(SimpleNamespace(input_tokens=100))).usage
    assert missing.billable_output_tokens is None


def test_claude_code_usage_preserves_explicit_write_and_output_else_unknown():
    usage = claude_code_map_usage({
        "input_tokens": 10, "output_tokens": 5,
        "cache_read_input_tokens": 20, "cache_creation_input_tokens": 7})
    assert (usage.input_tokens, usage.cached_tokens, usage.output_tokens) == (37, 20, 5)
    assert (usage.cache_write_tokens, usage.billable_output_tokens) == (7, 5)
    bare = claude_code_map_usage({"input_tokens": 10})
    assert bare.cache_write_tokens is None and bare.billable_output_tokens is None
    assert claude_code_map_usage(None).cache_write_tokens is None


# ---------------------------------------------------------------- session total

SOL2 = {name: rate * 2 for name, rate in SOL.items()}
_SOL_BILLING = {"cache_write_tokens": 120, "billable_output_tokens": 50}  # $0.0018 on SOL


def _v1_llm(index, *, molt=1, model="sol", billing=None, total=1000, cached=400, out=50, **extra):
    """A coherent kernel ``llm_response`` whose v1 snapshot has ``index`` equal calls."""
    cum_in, cum_cached = total * index, cached * index
    event = {
        "type": "llm_response", "api_call_id": f"api-{molt}-{index}", "input_tokens": total,
        "output_tokens": out, "thinking_tokens": 0, "cached_tokens": cached, "estimated": False,
        "usage_billing": {"model": model, **_SOL_BILLING} if billing is None else billing,
        "session_usage": {
            "schema": TaskCardEventProjection.SESSION_USAGE_SCHEMA,
            "molt_count": molt, "api_call_index": index, "api_calls": index,
            "input_tokens": cum_in, "output_tokens": out * index, "cached_tokens": cum_cached,
            "avg_input_tokens_per_api_call": total,
            "session_cache_rate": round(cum_cached / cum_in, 5) if cum_in else 0.0,
            "cache_miss_tokens": cum_in - cum_cached, "cache_miss_budget": 1_000_000,
            "cache_miss_remaining_tokens": 1_000_000 - (cum_in - cum_cached),
            "context_tokens": total,
        },
    }
    event.update(extra)
    return event


def _fold(events, cost=None, session=None):
    for event in events:
        session = TaskCardEventProjection.reduce_session_usage_event(session, event)
        cost = api_cost.fold_session_cost(cost, session, event)
    return cost, session


def test_session_cost_sums_each_round_at_its_own_recorded_model():
    catalog = _ready_catalog({"sol": SOL, "sol2": SOL2})
    cost, _ = _fold([_v1_llm(1, model="sol"), _v1_llm(2, model="sol2")])
    # 0.0018 (sol) + 0.0036 (sol2); neither model applied to the 2k aggregate
    # (0.0036 / 0.0072) — each response keeps its own billing model.
    assert api_cost.session_cost_text(cost, catalog) == "total ~$0.0054 · in $0.0029 · write $0.0009 · read $0.0001 · out $0.0015"
    single, _ = _fold([_v1_llm(1)])
    assert api_cost.session_cost_text(single, catalog) == "total ~$0.0018 · in $0.0010 · write $0.0003 · read <$0.0001 · out $0.0005"


def test_session_cost_counts_each_response_once_across_replays_and_carriers():
    catalog = _ready_catalog({"sol": SOL})
    first, second = _v1_llm(1), _v1_llm(2)
    carrier = {"type": "notification_block_injected", "call_id": "t1", "_meta": {"agent_meta": {
        "agent_state": {"token_usage": {"current_call": {"output": 50}, "session": {"api_calls": 9}}}}}}
    # Two tool calls of one API round add no response; replays and an older
    # snapshot after a newer one are not recounted.
    cost, _ = _fold([first, carrier, carrier, second, second, first, carrier])
    assert sorted(cost["bills"]) == [1, 2] and cost["latest"] == 2
    assert api_cost.session_cost_text(cost, catalog) == "total ~$0.0036 · in $0.0019 · write $0.0006 · read <$0.0001 · out $0.0010"


def test_session_cost_molt_resets_and_unknown_generation_drops_the_total():
    catalog = _ready_catalog({"sol": SOL})
    cost, session = _fold([_v1_llm(1), _v1_llm(2)])
    cost, session = _fold([{"type": "psyche_molt", "molt_count": 2}], cost, session)
    assert api_cost.session_cost_text(cost, catalog) == "total ~$0 · in $0 · write $0 · read $0 · out $0"
    cost, session = _fold([_v1_llm(1, molt=2)], cost, session)
    assert api_cost.session_cost_text(cost, catalog) == "total ~$0.0018 · in $0.0010 · write $0.0003 · read <$0.0001 · out $0.0005"
    # A stale molt for an older generation is ignored like the SESSION reducer.
    cost, session = _fold([{"type": "psyche_molt", "molt_count": 1}], cost, session)
    assert api_cost.session_cost_text(cost, catalog) == "total ~$0.0018 · in $0.0010 · write $0.0003 · read <$0.0001 · out $0.0005"
    cost, session = _fold([{"type": "psyche_molt"}], cost, session)
    assert cost is None and api_cost.session_cost_text(cost, catalog) == ""


def test_session_cost_unseen_or_rejected_history_is_partial_never_complete():
    catalog = _ready_catalog({"sol": SOL})
    # Bounded rehydrate window that starts at call 3: calls 1-2 are unseen.
    cost, _ = _fold([_v1_llm(3), _v1_llm(4)])
    assert api_cost.session_cost_text(cost, catalog) == "total ≥$0.0036 · in $0.0019+ · write $0.0006+ · read <$0.0001+ · out $0.0010+ · partial"
    # An incoherent v1 snapshot advances the index without a recorded response.
    broken = _v1_llm(2)
    broken["session_usage"]["cache_miss_tokens"] += 1
    cost, _ = _fold([_v1_llm(1), broken, _v1_llm(3)])
    assert sorted(cost["bills"]) == [1, 3] and cost["latest"] == 3
    assert api_cost.session_cost_text(cost, catalog).startswith("total ≥$0.0036")
    # Legacy history without any v1 snapshot renders no session total at all.
    legacy = _v1_llm(1)
    legacy.pop("session_usage")
    assert _fold([legacy])[0] is None


_PARTIAL_ONE = "total ≥$0.0018 · in $0.0010+ · write $0.0003+ · read <$0.0001+ · out $0.0005+ · partial"


@pytest.mark.parametrize("change, expected", [
    ({"usage_billing": None}, _PARTIAL_ONE),  # pre-feature round: no billing facts
    ({"billing": {"billable_output_tokens": 50}}, _PARTIAL_ONE),  # model unknown
    ({"model": "unlisted"}, _PARTIAL_ONE),
    ({"estimated": True}, _PARTIAL_ONE),
    # Requested tier without a usable estimate: SOL has no priority fields, and
    # flex/auto/invalid tiers are never silently priced as standard.
    *[({"billing": {"model": "sol", "service_tier": tier, **_SOL_BILLING}}, _PARTIAL_ONE)
      for tier in ("flex", "auto", "")],
    ({"billing": {"model": "sol", "service_tier": "priority", **_SOL_BILLING}},
     _PARTIAL_ONE + " · requested-tier est."),
    # Writes priced but not recorded: only a floor (600 at input rate) is known.
    ({"billing": {"model": "sol", "billable_output_tokens": 50}},
     "total ≥$0.0035 · in $0.0010+ · write $0.0003+ · read <$0.0001 · out $0.0010 · partial"),
])
def test_session_cost_missing_model_price_or_usage_is_partial_never_zero(change, expected):
    catalog = _ready_catalog({"sol": SOL})
    cost, _ = _fold([_v1_llm(1), _v1_llm(2, **change)])
    assert api_cost.session_cost_text(cost, catalog) == expected
    # A coherent v1 round without usable usage counts is unknown, not $0.
    zero, _ = _fold([_v1_llm(1, total=0, cached=0)])
    assert api_cost.session_cost_text(zero, catalog) == "total ? · in ? · write ? · read ? · out ? · partial"


def test_session_cost_with_nothing_priced_is_n_a_and_never_blocks():
    cost, _ = _fold([_v1_llm(1), _v1_llm(2, model="unlisted")])
    started = time.monotonic()
    unavailable = api_cost.PriceCatalog(lambda *a: b"")  # loading, then unavailable
    assert api_cost.session_cost_text(cost, unavailable) == "total ? · in ? · write ? · read ? · out ? · partial"
    assert time.monotonic() - started < 1.0
    assert api_cost.session_cost_text(None, unavailable) == ""


def test_session_cost_row_is_opt_in_shared_metadata_and_default_is_unchanged():
    base = {"model": "sol", "api_calls": 2, "working_dir": "/w/.lingtai/a"}
    default = TaskCardEventProjection.format_metadata(base)
    row = "total ~$0.0054 · in $0.0029 · write $0.0009 · read $0.0001 · out $0.0015"
    lines = TaskCardEventProjection.format_metadata({**base, "session_cost": row})
    assert lines == [default[0], f"Cost · {row}", *default[1:]]
    for inert in ("", "  ", "x" * 193, 5, None):
        assert TaskCardEventProjection.format_metadata({**base, "session_cost": inert}) == default
    # No Session section means no orphan Cost row.
    identity = {"working_dir": "/w/.lingtai/a"}
    assert TaskCardEventProjection.format_metadata({**identity, "session_cost": row}) == (
        TaskCardEventProjection.format_metadata(identity))
    # The row is a Session row inside the 500-char metadata budget: a long
    # Identity is shortened first and the Cost row survives.
    crowded = {"model": "m" * 128, "thinking": "t" * 48, "service_tier": "s" * 48,
               "endpoint": "e" * 96, "device_short_name": "d" * 64,
               "working_dir": "/" + "p" * 219, "session_cost": row}
    lines = TaskCardEventProjection.format_metadata(crowded)
    assert lines[1] == f"Cost · {row}"
    assert len("\n".join(lines)) <= TaskCardEventProjection.METADATA_MAX_CHARS
    assert lines[-1].startswith("Identity · ") and lines[-1].endswith("…")


@pytest.mark.parametrize("null_rate", [None, "junk", -1])
def test_null_or_invalid_declared_tier_stays_unknown(null_rate):
    entry = api_cost.parse_catalog(_catalog_bytes({"m": {
        "input_cost_per_token": 1e-6,
        "input_cost_per_token_above_200k_tokens": null_rate,
    }}))["m"]
    bill = {"input": 300_000, "cached": 0, "cache_write_tokens": 0}
    assert "input_cost_per_token_above_200k_tokens" in entry
    assert api_cost.estimate_costs(bill, entry)["miss"] is None


def test_unknown_declared_one_hour_price_never_becomes_base_write_price():
    bill = {"input": 100, "cached": 0, "cache_write_tokens": 50}
    entry = {"input_cost_per_token": 1e-6,
             "cache_creation_input_token_cost": 2e-6,
             "cache_creation_input_token_cost_above_1hr": None}
    assert api_cost.estimate_costs(bill, entry)["miss"] is None
    # A known zero 1h share needs no 1h rate: all writes use the 5m rate.
    assert api_cost.estimate_costs({**bill, "cache_write_1h_tokens": 0}, entry)["miss"] == pytest.approx(
        50 * 1e-6 + 50 * 2e-6)
    # If a model advertises 1h pricing but lacks that large-context variant,
    # an unknown TTL split still cannot be priced at the 5m rate.
    entry["cache_creation_input_token_cost_above_1hr"] = 4e-6
    entry["cache_creation_input_token_cost_above_200k_tokens"] = 3e-6
    assert api_cost.estimate_costs({**bill, "input": 300_000}, entry)["miss"] is None


def test_session_split_known_write_unknown_write_and_ttl():
    plain = {"input_cost_per_token": 2e-6, "cache_read_input_token_cost": 1e-7, "output_cost_per_token": 1e-5}
    bill = {"input": 1000, "cached": 400, "cache_write_tokens": 120, "billable_output_tokens": 50}
    amounts, _ = api_cost.estimate_parts(bill, plain)
    ordinary, write = api_cost._split_miss(bill, plain, amounts["miss"])
    assert ordinary == pytest.approx(480 * 2e-6)
    assert write == pytest.approx(120 * 2e-6)
    assert ordinary + write == pytest.approx(amounts["miss"])
    unknown = {**bill, "cache_write_tokens": None}
    assert api_cost._split_miss(unknown, plain, amounts["miss"]) == (None, None)
    cost, _ = _fold([_v1_llm(1, billing={"model": "plain", "billable_output_tokens": 50})])
    text = api_cost.session_cost_text(cost, _ready_catalog({"plain": plain}))
    assert text == "total ~$0.0017 · in ? · write ? · read <$0.0001 · out $0.0005"
    ttl = {**bill, "cache_write_1h_tokens": 40}
    parts, floors = api_cost.estimate_parts(ttl, TTL)
    ordinary, write = api_cost._split_miss(ttl, TTL, parts["miss"])
    assert not floors
    assert write == pytest.approx(80 * 2.5e-6 + 40 * 4e-6)
    assert ordinary + write == pytest.approx(parts["miss"])


def test_session_cost_rejected_same_index_cannot_poison_known_bill():
    event = _v1_llm(1)
    cost, session = _fold([event])
    bad = _v1_llm(1, model="sol2")
    bad["session_usage"]["cache_miss_tokens"] += 1
    cost, _ = _fold([bad], cost, session)
    assert cost["bills"][1]["model"] == "sol"
    assert "partial" in api_cost.session_cost_text(cost, _ready_catalog({"sol": SOL}))


# ---------------------------------------------------------------- requested tier

# LiteLLM-named ``*_priority`` rates (fixture values; input is deliberately not a
# clean multiple of the standard rate: nothing here is a guessed multiplier).
PRIORITY = {
    "input_cost_per_token_priority": 3e-06,
    "output_cost_per_token_priority": 2e-05,
    "cache_read_input_token_cost_priority": 2e-07,
    "cache_creation_input_token_cost_priority": 5e-06,
    "input_cost_per_token_above_272k_tokens_priority": 7e-06,
    "output_cost_per_token_above_272k_tokens_priority": 3e-05,
    "cache_read_input_token_cost_above_272k_tokens_priority": 4e-07,
    "cache_creation_input_token_cost_above_272k_tokens_priority": 1e-05,
}
SOLP = {**SOL, **PRIORITY}
_SMALL = {"input": 1000, "cached": 400, "cache_write_tokens": 100, "billable_output_tokens": 50}
_BIG = {"input": 300_000, "cached": 100_000, "cache_write_tokens": 0, "billable_output_tokens": 1000}
_STANDARD_RESULT = {
    "small": {"miss": 500 * 2e-06 + 100 * 2.5e-06, "hit": 400 * 1e-07, "output": 50 * 1e-05},
    "big": {"miss": 200_000 * 4e-06, "hit": 100_000 * 2e-07, "output": 1000 * 1.5e-05},
}
_PRIORITY_RESULT = {
    "small": {"miss": 500 * 3e-06 + 100 * 5e-06, "hit": 400 * 2e-07, "output": 50 * 2e-05},
    "big": {"miss": 200_000 * 7e-06, "hit": 100_000 * 4e-07, "output": 1000 * 3e-05},
}


@pytest.mark.parametrize("size, bill", [("small", _SMALL), ("big", _BIG)])
@pytest.mark.parametrize("extra, expected", [
    ({}, _STANDARD_RESULT),  # legacy / untiered round: standard, as before
    ({"service_tier": "default"}, _STANDARD_RESULT),
    ({"service_tier": "priority"}, _PRIORITY_RESULT),
])
def test_requested_tier_selects_standard_or_priority_rates(size, bill, extra, expected):
    # >272k ("big") takes the 272k context tier of the REQUESTED service tier;
    # cache read/write allocation and output use the same estimator either way.
    assert api_cost.estimate_costs({**bill, **extra}, SOLP) == pytest.approx(expected[size])


def test_parse_catalog_keeps_priority_fields_and_present_invalid_priority_as_none():
    models = api_cost.parse_catalog(_catalog_bytes({"m": {
        **SOLP, "output_cost_per_token_priority": "junk", "unrelated_priority": 1}}))
    assert models["m"] == {**SOLP, "output_cost_per_token_priority": None}


@pytest.mark.parametrize("tier", ["auto", "flex", "", "Priority", 3])
def test_other_requested_tiers_are_unknown_never_standard(tier):
    bill = {**_SMALL, "service_tier": tier}
    assert api_cost.estimate_costs(bill, SOLP) == {"miss": None, "hit": None, "output": None}
    assert api_cost._split_miss(bill, SOLP, 1.0) == (None, None)


@pytest.mark.parametrize("entry, bill, unknown", [
    (SOL, _SMALL, {"miss", "hit", "output"}),  # no priority fields at all
    ({**SOLP, "input_cost_per_token_priority": None}, _SMALL, {"miss"}),
    ({k: v for k, v in SOLP.items() if k != "output_cost_per_token_priority"}, _SMALL, {"output"}),
    ({**SOLP, "input_cost_per_token_above_272k_tokens_priority": None}, _BIG, {"miss"}),
    ({k: v for k, v in SOLP.items() if k != "cache_read_input_token_cost_above_272k_tokens_priority"},
     _BIG, {"hit"}),
    # Standard has the >272k tier but no priority >272k field exists: unknown,
    # not the cheaper priority base rate.
    ({k: v for k, v in SOLP.items() if not k.endswith("_above_272k_tokens_priority")},
     _BIG, {"miss", "hit", "output"}),
    # Standard prices cache writes but the priority write rate is absent: the
    # 100 written tokens are unknown, not ordinary priority input.
    ({k: v for k, v in SOLP.items()
      if not (k.startswith("cache_creation_input_token_cost") and k.endswith("_priority"))},
     _SMALL, {"miss"}),
])
def test_missing_or_malformed_priority_rate_stays_unknown_without_standard_fallback(entry, bill, unknown):
    costs = api_cost.estimate_costs({**bill, "service_tier": "priority"}, entry)
    assert {name for name, value in costs.items() if value is None} == unknown
    # The known parts are priority prices, never the cheaper standard ones.
    size = "small" if bill is _SMALL else "big"
    for name, value in costs.items():
        if value is not None:
            assert value == pytest.approx(_PRIORITY_RESULT[size][name])


def test_single_line_prices_priority_request_differently_from_standard():
    catalog = _ready_catalog({"sol": SOLP})
    bill = {"model": "sol", **_SOL_BILLING, "input": 1000, "cached": 400}
    standard = api_cost.usage_line(2.0, {"output": 50, "bill": bill}, catalog)
    priority = api_cost.usage_line(2.0, {"output": 50, "bill": {**bill, "service_tier": "priority"}}, catalog)
    assert standard == "$0.0018 · ↓$0.0005 ↑$0.0013 | <$0.0001"
    assert priority == "$0.0031 · ↓$0.0010 ↑$0.0020 | <$0.0001 priority est."
    # Other tiers (default/none) keep the unlabelled standard form.
    assert api_cost.usage_line(
        2.0, {"output": 50, "bill": {**bill, "service_tier": "default"}}, catalog) == standard


def test_session_cost_prices_each_round_at_its_own_requested_tier():
    catalog = _ready_catalog({"sol": SOLP})
    priority = {"model": "sol", "service_tier": "priority", **_SOL_BILLING}
    mixed, _ = _fold([_v1_llm(1), _v1_llm(2, billing=priority), _v1_llm(3)])
    # normal 0.0018 + priority 0.0031 + normal 0.0018: each round keeps its own bill.
    assert api_cost.session_cost_text(mixed, catalog) == (
        "total ~$0.0067 · in $0.0034 · write $0.0012 · read $0.0002 · out $0.0020"
        " · requested-tier est.")
    # A pure-normal session is unchanged by the priority fields in the catalog.
    normal, _ = _fold([_v1_llm(1)])
    assert api_cost.session_cost_text(normal, catalog) == (
        "total ~$0.0018 · in $0.0010 · write $0.0003 · read <$0.0001 · out $0.0005")


@pytest.mark.parametrize("raw, projected", [
    ("priority", "priority"), ("default", "default"), ("flex", "flex"),
    ("PRIORITY", ""), ("p r", ""), (5, ""), (None, ""), ("x" * 40, ""),
])
def test_projection_keeps_requested_tier_per_round_and_invalid_stays_present(raw, projected):
    _, usage = TaskCardEventProjection.project_llm_response_usage(
        _llm_event(usage_billing={"model": "sol", "service_tier": raw}))
    assert usage["bill"]["service_tier"] == projected
    # Legacy rounds (no key) gain no tier, so they keep standard semantics.
    _, legacy = TaskCardEventProjection.project_llm_response_usage(
        _llm_event(usage_billing={"model": "sol"}))
    assert "service_tier" not in legacy["bill"]


def test_session_billing_event_helper_carries_requested_tier_only_when_safe():
    assert UsageMetadata().requested_service_tier is None
    usage = UsageMetadata(requested_service_tier="priority")
    assert _usage_billing_for_event(usage, "sol") == {"model": "sol", "service_tier": "priority"}
    assert _usage_billing_for_event(UsageMetadata(), "sol") == {"model": "sol"}
    for bad in ("", "Fast", "p r", "priority\n", 5, "x" * 40):
        assert _usage_billing_for_event(UsageMetadata(requested_service_tier=bad), "sol") == {"model": "sol"}
