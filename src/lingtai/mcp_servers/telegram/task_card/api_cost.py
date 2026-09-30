"""Telegram-owned per-call API list-price estimate line for the Task Card.

Pure formatting over already-projected round facts (``usage["bill"]``) plus a
small process-local snapshot of LiteLLM's public price data. Rendering never performs synchronous network I/O: a missing/stale snapshot starts at most one bounded background
refresh and the line degrades honestly until it lands. Prices are STANDARD
public TOKEN list-price ESTIMATES as of the catalog fetch: not an invoice, not
subscription/pool billing, and not batch/priority/routed-tier or discounted
prices; search, grounding and image fixed fees are not included.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from lingtai.kernel.llm.base import checked_count as _count

CATALOG_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
MAX_CATALOG_BYTES = 8 * 1024 * 1024
# ``FETCH_TIMEOUT_S`` bounds each blocking socket operation (connect/recv);
# ``FETCH_DEADLINE_S`` is a monotonic total budget checked between reads, so a
# slow-drip response ends at most one socket timeout after the deadline.
FETCH_TIMEOUT_S = 10.0
FETCH_DEADLINE_S = 30.0
_READ_CHUNK = 64 * 1024
REFRESH_AFTER_S = 6 * 3600.0
RETRY_AFTER_S = 300.0

_TIER_THRESHOLDS = ((272_000, "272k"), (200_000, "200k"))
_BUCKET_FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "read": "cache_read_input_token_cost",
    "write": "cache_creation_input_token_cost",
    "write_1h": "cache_creation_input_token_cost_above_1hr",
}


def _kept_fields() -> tuple[str, ...]:
    names = list(_BUCKET_FIELDS.values())
    for _, label in _TIER_THRESHOLDS:
        names.extend(f"{name}_above_{label}_tokens" for name in _BUCKET_FIELDS.values())
    return tuple(names)


_KEPT_FIELDS = _kept_fields()


def _rate(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def parse_catalog(payload: bytes) -> dict[str, dict[str, float | None]]:
    """Reduce LiteLLM's JSON to the standard-rate fields this line uses.

    A present-but-invalid rate is kept as ``None`` (rate unknown, field still
    PRESENT) so a tier's presence never silently falls back to the base rate;
    only an absent field is omitted. A non-object payload raises.
    """
    raw = json.loads(payload.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("catalog is not an object")
    models: dict[str, dict[str, float | None]] = {}
    for name, entry in raw.items():
        if name == "sample_spec" or not isinstance(name, str) or not isinstance(entry, dict):
            continue
        kept = {
            field: _rate(entry[field])
            for field in _KEPT_FIELDS
            if field in entry
        }
        if any(rate is not None for rate in kept.values()):
            models[name] = kept
    if not models:
        raise ValueError("catalog has no priced models")
    return models


def _http_fetch(
    url: str,
    timeout: float,
    max_bytes: int,
    *,
    deadline_s: float = FETCH_DEADLINE_S,
    opener: Callable[..., Any] = urllib.request.urlopen,
    monotonic: Callable[[], float] = time.monotonic,
) -> bytes:
    """Fetch ``url`` in bounded chunks under a size cap and a total deadline.

    A single ``read(n)`` can sit in a slow-drip body indefinitely (the socket
    timeout only bounds each recv), so this reads with ``read1`` and re-checks
    the monotonic deadline and the byte cap between every chunk.
    """
    deadline = monotonic() + deadline_s
    chunks: list[bytes] = []
    size = 0
    with opener(url, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        read = getattr(response, "read1", None) or response.read
        while True:
            if monotonic() >= deadline:
                raise TimeoutError("catalog fetch deadline exceeded")
            chunk = read(min(_READ_CHUNK, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("catalog too large")
            chunks.append(chunk)
    return b"".join(chunks)


class PriceCatalog:
    """Process-local snapshot with one bounded in-flight background refresh."""

    def __init__(
        self,
        fetch: Callable[[str, float, int], bytes] = _http_fetch,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        refresh_after_s: float = REFRESH_AFTER_S,
        retry_after_s: float = RETRY_AFTER_S,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._wall = wall
        self._refresh_after_s = refresh_after_s
        self._retry_after_s = retry_after_s
        self._lock = threading.Lock()
        self._models: dict[str, dict[str, float | None]] | None = None
        self._loaded_at = 0.0
        self._as_of = ""
        self._inflight = False
        self._next_attempt = 0.0

    def lookup(self, model: str) -> tuple[str, dict[str, float | None] | None, str]:
        """Return ``(status, entry, as_of)``; never blocks on the network.

        ``status`` is ``loading`` (nothing yet), ``unavailable`` (failed, no
        snapshot), ``unlisted`` (exact model absent), ``ok`` or ``stale``.
        """
        start = False
        with self._lock:
            now = self._clock()
            models = self._models
            due = models is None or now - self._loaded_at >= self._refresh_after_s
            if due and not self._inflight and now >= self._next_attempt:
                self._inflight = True
                start = True
            failed = models is None and not self._inflight and self._next_attempt > 0
            stale = models is not None and now - self._loaded_at >= self._refresh_after_s
            as_of = self._as_of
        if start:
            try:
                threading.Thread(
                    target=self._refresh, name="api-cost-catalog", daemon=True
                ).start()
            except Exception:
                # Thread could not start: release the in-flight slot and pace
                # the retry instead of showing "loading" forever.
                with self._lock:
                    self._inflight = False
                    self._next_attempt = self._clock() + self._retry_after_s
                failed = models is None
        if models is None:
            return ("unavailable" if failed else "loading"), None, ""
        entry = models.get(model)
        if entry is None:
            return "unlisted", None, as_of
        return ("stale" if stale else "ok"), entry, as_of

    def _refresh(self) -> None:
        try:
            models = parse_catalog(self._fetch(CATALOG_URL, FETCH_TIMEOUT_S, MAX_CATALOG_BYTES))
            as_of = datetime.fromtimestamp(self._wall(), timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            models, as_of = None, ""
        with self._lock:
            try:
                now = self._clock()
                if models is not None:
                    self._models = models
                    self._loaded_at = now
                    self._as_of = as_of
                    self._next_attempt = 0.0
                else:
                    self._next_attempt = now + self._retry_after_s
            finally:
                self._inflight = False


CATALOG = PriceCatalog()


def _tier_suffix(entry: dict[str, float | None], total_input: int) -> str:
    """Tier key suffix by field PRESENCE (a present-but-invalid rate counts)."""
    for threshold, label in _TIER_THRESHOLDS:
        if total_input > threshold and any(
            f"{name}_above_{label}_tokens" in entry for name in _BUCKET_FIELDS.values()
        ):
            return f"_above_{label}_tokens"
    return ""


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def estimate_costs(
    bill: dict[str, Any], entry: dict[str, float | None]
) -> dict[str, float | None]:
    """STANDARD list-price USD per metrics-row bucket; ``None`` = unknown.

    Buckets mirror the task-card metrics row: ``miss`` is the ``↑`` cache-miss
    input (total input minus cache read, i.e. uncached input plus any cache
    writes), ``hit`` is the cache-read input (the ``| hit%`` share of ``◌``),
    and ``output`` is the ``↓`` billable output. ``bill["input"]`` is the TOTAL
    input incl. cache counts (it selects the tier). When the catalog prices
    cache writes separately (e.g. Anthropic), a known write count is charged
    at the write rate inside ``miss``; when it has no cache-write price at all
    (OpenAI/Codex style), writes are ordinary input, so ``miss`` needs no write
    count. Missing/incoherent counts, missing/invalid rates and non-finite
    (overflowing) products never become zero: they stay unknown.
    """
    total = _count(bill.get("input"))
    read = _count(bill.get("cached"))
    write = _count(bill.get("cache_write_tokens"))
    raw_one_hour = bill.get("cache_write_1h_tokens")
    one_hour = _count(raw_one_hour)
    ttl_malformed = raw_one_hour is not None and one_hour is None
    output = _count(bill.get("billable_output_tokens"))
    result: dict[str, float | None] = {"miss": None, "hit": None, "output": None}
    if output is not None:
        # Output does not depend on input counts; tier by total when known.
        suffix = _tier_suffix(entry, total) if total is not None else ""
        result["output"] = _charge(output, entry.get(_BUCKET_FIELDS["output"] + suffix))
    if total is None or read is None or read > total:
        return result
    suffix = _tier_suffix(entry, total)

    def rate(bucket: str) -> float | None:
        return entry.get(_BUCKET_FIELDS[bucket] + suffix)

    result["hit"] = _charge(read, rate("read"))
    writes_priced = any(key.startswith(_BUCKET_FIELDS["write"]) for key in entry)
    if not writes_priced:
        # No separate cache-write price: every non-cached input token bills
        # at the input rate, whether or not the wire reported writes.
        result["miss"] = _charge(total - read, rate("input"))
        return result
    if write is None or read + write > total:
        return result  # writes are priced separately but their count is unknown
    uncached = _charge(total - read - write, rate("input"))
    write_cost: float | None = None
    one_hour_rate = rate("write_1h")
    standard_write = rate("write")
    if ttl_malformed or (one_hour is not None and one_hour > write):
        pass  # malformed/incoherent TTL split (1h > total write): unknown
    elif write == 0:
        write_cost = 0.0
    elif one_hour is not None:
        five_min = _charge(write - one_hour, standard_write)
        long = _charge(one_hour, one_hour_rate)
        if five_min is not None and long is not None:
            write_cost = _finite(five_min + long)
    elif (
        not any(key.startswith(_BUCKET_FIELDS["write_1h"]) for key in entry)
        or (one_hour_rate is not None and one_hour_rate == standard_write)
    ):
        # No distinct 1h price exists, so the TTL split cannot change it.
        write_cost = _charge(write, standard_write)
    if uncached is not None and write_cost is not None:
        result["miss"] = _finite(uncached + write_cost)
    return result


def _charge(count: int | None, price: float | None) -> float | None:
    if count is None:
        return None
    if count == 0:
        return 0.0
    if price is None:
        return None
    try:
        return _finite(count * price)
    except OverflowError:
        return None


def _money(value: float) -> str:
    if value == 0:
        return "$0"
    if value < 0.0001:
        return "<$0.0001"
    return f"${value:.4f}" if value < 1 else f"${value:,.2f}"


def _avg_tps(api_delay_s: float | None, usage: dict[str, Any], bill: dict[str, Any]) -> str | None:
    if bill.get("estimated") is True:
        return None
    tokens = _count(bill.get("billable_output_tokens"))
    if tokens is None:
        tokens = _count(usage.get("output"))
    if (
        tokens is None
        or type(api_delay_s) not in (int, float)
        or not math.isfinite(api_delay_s)
        or api_delay_s <= 0
    ):
        return None
    try:
        speed = _finite(tokens / api_delay_s)
    except (OverflowError, ZeroDivisionError):
        return None
    return None if speed is None else f"{speed:.1f}"


def usage_line(
    api_delay_s: float | None,
    usage: dict[str, Any] | None,
    catalog: PriceCatalog | None = None,
) -> str:
    """One compact line: output tok/s plus a list-price estimate, or ``""``.

    ``6.2 tok/s · ≈$0.0310 · ↓$0.0006 ↑$0.0070 | $0.0234`` mirrors the metrics
    row (``↓`` output, ``↑`` cache-miss input, ``|`` cache hits). ``≈`` marks a
    STANDARD list-price estimate; a trailing ``+`` means some parts are
    unknown, so the figure is the known subtotal (every part is non-negative,
    so it is a lower bound).
    """
    if not isinstance(usage, dict) or not usage:
        return ""
    bill = usage.get("bill")
    bill = bill if isinstance(bill, dict) else {}
    tps = _avg_tps(api_delay_s, usage, bill)
    parts = []
    if tps is not None:
        parts.append(f"{tps} tok/s")
    if bill.get("estimated") is True:
        parts.append("cost n/a (estimated tokens)")
    elif not isinstance(bill.get("model"), str):
        parts.append("cost n/a (model unknown)")
    else:
        status, entry, _as_of = (catalog or CATALOG).lookup(bill["model"])
        if entry is None:
            parts.append({
                "loading": "cost loading",
                "unavailable": "cost n/a (prices unavailable)",
                "unlisted": "cost n/a (model not listed)",
            }[status])
        else:
            costs = estimate_costs(bill, entry)
            known = [value for value in costs.values() if value is not None]
            subtotal = _finite(sum(known)) if known else None
            if subtotal is None:
                head = "cost ?"
            elif len(known) == len(costs):
                head = f"≈{_money(subtotal)}"
            else:
                head = f"≈{_money(subtotal)}+"

            def cell(name: str) -> str:
                return _money(costs[name]) if costs[name] is not None else "?"

            # Mirrors the metrics row: ↓ output, ↑ cache-miss input, | cache hits.
            text = f"{head} · ↓{cell('output')} ↑{cell('miss')} | {cell('hit')}"
            if status == "stale":
                text += " stale prices"
            parts.append(text)
    return " · ".join(parts)
