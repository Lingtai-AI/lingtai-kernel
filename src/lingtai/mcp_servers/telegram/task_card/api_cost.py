"""Telegram-owned per-call API list-price estimate line for the Task Card.

Pure formatting over already-projected round facts (``usage["bill"]``) plus a
small process-local snapshot of LiteLLM's public price data. Rendering never performs synchronous network I/O: a missing/stale snapshot starts at most one bounded background
refresh and the line degrades honestly until it lands. Prices are public TOKEN
list-price ESTIMATES as of the catalog fetch: STANDARD rates, or the catalog's
``*_priority`` rates when the round REQUESTED the ``priority`` tier (a request,
not proof of the tier the provider applied; no multiplier is guessed and a
missing priority field stays unknown). Not an invoice, not subscription/pool
billing, and not batch/flex/routed-tier or discounted prices; a round that
requested any other tier is unknown, and search, grounding and image fixed fees
are not included. The SESSION
``Cost`` row sums the same per-round estimates over the since-molt responses
this process has observed, and says ``partial`` whenever any is missing.
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
from lingtai.mcp_servers.task_card.event_projection import TaskCardEventProjection

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


_PRIORITY = "_priority"


def _kept_fields() -> tuple[str, ...]:
    names = list(_BUCKET_FIELDS.values())
    for _, label in _TIER_THRESHOLDS:
        names.extend(f"{name}_above_{label}_tokens" for name in _BUCKET_FIELDS.values())
    # LiteLLM's requested-priority rates append ``_priority`` to the same names.
    return tuple(names + [name + _PRIORITY for name in names])


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
    """Reduce LiteLLM's JSON to the standard and ``_priority`` rate fields used.

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


def _requested_tier_entry(
    bill: dict[str, Any], entry: dict[str, float | None]
) -> dict[str, float | None] | None:
    """The rate fields for the round's REQUESTED service tier, or ``None``.

    No recorded tier (legacy round or none requested) and ``default`` use the
    standard fields. ``priority`` takes every VALUE from the ``*_priority``
    fields, renamed to the standard names so the one estimator prices them
    unchanged, but keeps the standard fields' STRUCTURAL presence (context
    tiers, cache-write pricing) with a ``None`` value where the priority rate is
    missing: a missing or malformed priority field stays unknown and never
    falls back to the standard rate, a cheaper base tier, or "no write price".
    Priority-only fields are kept. Any other tier (``auto``/``flex``/invalid)
    has no estimate. This is the tier REQUESTED, not necessarily the one the
    provider applied.
    """
    tier = bill.get("service_tier")
    if tier is None or tier == "default":
        return {k: v for k, v in entry.items() if not k.endswith(_PRIORITY)}
    if tier != "priority":
        return None
    view = {k: entry.get(k + _PRIORITY) for k in entry if not k.endswith(_PRIORITY)}
    view.update({k[: -len(_PRIORITY)]: v for k, v in entry.items() if k.endswith(_PRIORITY)})
    return view


def estimate_costs(
    bill: dict[str, Any], entry: dict[str, float | None]
) -> dict[str, float | None]:
    """Exact per-part costs (``None`` = unknown or only a lower bound known)."""
    costs, floors = estimate_parts(bill, entry)
    return {name: (None if name in floors else value) for name, value in costs.items()}


def estimate_parts(
    bill: dict[str, Any], entry: dict[str, float | None]
) -> tuple[dict[str, float | None], frozenset[str]]:
    """Requested-tier list-price USD per metrics-row bucket; ``None`` = unknown.

    Standard rates, or ``*_priority`` rates for a round that requested
    ``priority`` (see ``_requested_tier_entry``).

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

    Returns ``(costs, floors)``: ``floors`` names parts whose value is only a
    lower bound. That happens for ``miss`` when the catalog prices cache
    writes separately but the round did not record a write count (e.g. rounds
    logged before the adapter read it): every cache-miss token is then priced
    at the cheapest applicable rate (input or cache write), which can only
    under-state the real cost.
    """
    total = _count(bill.get("input"))
    read = _count(bill.get("cached"))
    write = _count(bill.get("cache_write_tokens"))
    raw_one_hour = bill.get("cache_write_1h_tokens")
    one_hour = _count(raw_one_hour)
    ttl_malformed = raw_one_hour is not None and one_hour is None
    output = _count(bill.get("billable_output_tokens"))
    result: dict[str, float | None] = {"miss": None, "hit": None, "output": None}
    floors: set[str] = set()
    tier_entry = _requested_tier_entry(bill, entry)
    if tier_entry is None:
        return result, frozenset(floors)  # requested tier has no estimate
    entry = tier_entry
    if output is not None:
        # Output does not depend on input counts; tier by total when known.
        suffix = _tier_suffix(entry, total) if total is not None else ""
        result["output"] = _charge(output, entry.get(_BUCKET_FIELDS["output"] + suffix))
    if total is None or read is None or read > total:
        return result, frozenset(floors)
    suffix = _tier_suffix(entry, total)

    def rate(bucket: str) -> float | None:
        return entry.get(_BUCKET_FIELDS[bucket] + suffix)

    result["hit"] = _charge(read, rate("read"))
    writes_priced = any(key.startswith(_BUCKET_FIELDS["write"]) for key in entry)
    if not writes_priced:
        # No separate cache-write price: every non-cached input token bills
        # at the input rate, whether or not the wire reported writes.
        result["miss"] = _charge(total - read, rate("input"))
        return result, frozenset(floors)
    if write is None:
        # Writes are priced separately but this round has no write count:
        # price every cache-miss token at the cheapest applicable rate.
        rates = [rate("input"), rate("write")]
        if any(key.startswith(_BUCKET_FIELDS["write_1h"]) for key in entry):
            rates.append(rate("write_1h"))
        if all(r is not None for r in rates):
            floor = _charge(total - read, min(rates))
            if floor is not None:
                result["miss"] = floor
                floors.add("miss")
        return result, frozenset(floors)
    if read + write > total:
        return result, frozenset(floors)  # incoherent counts
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
    return result, frozenset(floors)


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


def usage_line(
    api_delay_s: float | None,
    usage: dict[str, Any] | None,
    catalog: PriceCatalog | None = None,
) -> str:
    """One compact list-price estimate line, or ``""``.

    ``$0.0310 · ↓$0.0006 ↑$0.0070 | $0.0234`` mirrors the metrics
    row (``↓`` output, ``↑`` cache-miss input, ``|`` cache hits). The total is
    a requested-tier list-price estimate; a trailing ``+`` means some parts are
    unknown, so the figure is the known subtotal (every part is non-negative,
    so it is a lower bound). A round that requested the ``priority`` tier ends
    with ``priority est.``; other rounds keep the unlabelled standard form.
    """
    if not isinstance(usage, dict) or not usage:
        return ""
    bill = usage.get("bill")
    bill = bill if isinstance(bill, dict) else {}
    # ``api_delay_s`` is part of the shared hook signature but unused: a
    # tokens/second figure over the displayed API gap (which includes waiting,
    # prefill and streaming) would not be a real generation speed.
    parts = []
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
            costs, floors = estimate_parts(bill, entry)
            known = [value for value in costs.values() if value is not None]
            subtotal = _finite(sum(known)) if known else None
            if subtotal is None:
                head = "cost ?"
            elif len(known) == len(costs) and not floors:
                head = _money(subtotal)
            else:
                head = f"{_money(subtotal)}+"

            def cell(name: str) -> str:
                if costs[name] is None:
                    return "?"
                return _money(costs[name]) + ("+" if name in floors else "")

            # Mirrors the metrics row: ↓ output, ↑ cache-miss input, | cache hits.
            text = f"{head} · ↓{cell('output')} ↑{cell('miss')} | {cell('hit')}"
            if bill.get("service_tier") == "priority":
                text += " priority est."  # the REQUESTED tier, not an applied one
            if status == "stale":
                text += " stale prices"
            parts.append(text)
    return " · ".join(parts)


def fold_session_cost(
    cost: dict[str, Any] | None,
    session: dict[str, Any] | None,
    event: dict[str, Any],
) -> dict[str, Any] | None:
    """Fold one journal-ordered event into since-molt per-response bill facts.

    ``session`` is the shared SESSION reducer state AFTER ``event``. A main
    ``llm_response`` is recorded once, keyed by its v1 ``api_call_index``, and
    only when its own coherent v1 projection is exactly the snapshot that
    reducer accepted, so replays, multi-tool groups and rejected/older
    snapshots never double count or contribute a bad bill. A response the
    reducer had to invalidate on is ``unaccounted``. A new generation (molt)
    starts empty and a molt of unknown generation drops the facts. ``latest``
    follows the reducer's index, so an unseen response leaves a gap. Either
    keeps the total partial. Mutates and returns the caller-owned ``cost``.
    """
    session = session or {}
    generation = session.get("molt_count")
    if session.get("awaiting_new_generation") or type(generation) is not int:
        return None
    if not isinstance(cost, dict) or cost.get("generation") != generation:
        cost = {"generation": generation, "latest": 0, "bills": {}, "unaccounted": False}
    index = session.get("api_call_index")
    if type(index) is int:
        cost["latest"] = max(cost["latest"], index)
    if event.get("type") != "llm_response":
        return cost
    accepted = TaskCardEventProjection.project_llm_response_session_usage(event)
    if accepted and session.get("source") == "v1" and session.get("snapshot") == accepted:
        index = accepted["api_call_index"]
        if index not in cost["bills"]:
            usage = TaskCardEventProjection.project_llm_response_usage(event)
            bill = usage[1].get("bill") if usage is not None else None
            cost["bills"][index] = bill if isinstance(bill, dict) else None
    elif session.get("invalidated"):
        # The reducer could not accept a current/newer response, so its cost
        # is unknown and this generation's total can no longer be complete.
        cost["unaccounted"] = True
    return cost


def _split_miss(
    bill: dict[str, Any], entry: dict[str, float | None], miss: float | None,
) -> tuple[float | None, float | None]:
    """Split an exact ``estimate_parts`` ``miss`` into disjoint (input, write) USD.

    Ordinary input (total - read - write) is charged at the tier's input rate
    and the rest of ``miss`` is the cache write, so ``input + write == miss``
    and nothing is charged twice; a catalog without a separate write price thus
    books a known write count at the input rate under ``write``. A floor-only or
    unknown ``miss``, no recorded write count, or incoherent counts/TTL leave
    the split unknown — never ``write = 0``.
    """
    total = _count(bill.get("input"))
    read = _count(bill.get("cached"))
    write = _count(bill.get("cache_write_tokens"))
    raw_one_hour = bill.get("cache_write_1h_tokens")
    one_hour = _count(raw_one_hour)
    tier_entry = _requested_tier_entry(bill, entry)
    if tier_entry is None:
        return None, None
    entry = tier_entry
    if (
        miss is None
        or total is None
        or read is None
        or write is None
        or read + write > total
        or (raw_one_hour is not None and (one_hour is None or one_hour > write))
    ):
        return None, None
    rate = entry.get(_BUCKET_FIELDS["input"] + _tier_suffix(entry, total))
    ordinary = _charge(total - read - write, rate)
    if ordinary is None or ordinary > miss:
        return None, None
    return ordinary, miss - ordinary


def session_cost_text(
    cost: dict[str, Any] | None,
    catalog: PriceCatalog | None = None,
) -> str:
    """One since-molt line, e.g.
    ``total ~$0.0018 · in $0.0010 · write $0.0003 · read <$0.0001 · out $0.0005``

    Each recorded response is priced with ITS OWN recorded model: the total
    sums ``estimate_parts`` once per round (incl. its #1775 ``miss`` floor),
    and the disjoint ``in``/``write`` buckets come from ``_split_miss`` while
    ``read``/``out`` are its ``hit``/``output``. A known total may sit beside an
    unknown in/write split. A bucket is ``?`` when nothing is known, else its
    known sum with ``+`` when any round's part is unknown or a floor; the total
    shows ``≥`` likewise. An unseen, unaccounted or unpriceable response
    (missing usage/billing/model/price) appends ``partial`` and makes every
    figure a lower bound. ``requested-tier est.`` is appended when any recorded
    round requested ``priority``. Never blocks on the catalog; ``""`` without state.
    """
    if not isinstance(cost, dict):
        return ""
    bills = cost.get("bills") or {}
    partial = bool(cost.get("unaccounted")) or len(bills) != cost.get("latest")
    sums = dict.fromkeys(("total", "in", "write", "read", "out"), 0.0)
    known: set[str] = set()
    short: set[str] = set()  # some round's part is unknown or only a floor
    stale = False
    entries: dict[str, tuple[str, dict[str, float | None] | None]] = {}
    for bill in bills.values():
        model = bill.get("model") if isinstance(bill, dict) else None
        if isinstance(model, str) and model not in entries:
            status, entry, _as_of = (catalog or CATALOG).lookup(model)
            entries[model] = (status, entry)
        status, entry = entries.get(model, ("", None)) if isinstance(model, str) else ("", None)
        if entry is None:
            partial = True  # no usage/billing/model, estimated tokens, or no price
            continue
        stale = stale or status == "stale"
        costs, floors = estimate_parts(bill, entry)
        ordinary, write = _split_miss(bill, entry, None if floors else costs["miss"])
        parts = [value for value in costs.values() if value is not None]
        if floors or len(parts) != len(costs):
            short.add("total")
        values = {
            "total": sum(parts) if parts else None,
            "in": ordinary,
            "write": write,
            "read": costs["hit"],
            "out": costs["output"],
        }
        for name, value in values.items():
            if value is None:
                short.add(name)
            else:
                known.add(name)
                sums[name] += value

    def figure(name: str, mark: str = "") -> str:
        value = _finite(sums[name])
        lower = partial or name in short
        if value is None or (lower and name not in known):
            return "?"
        if name == "total":
            return ("≥" if lower else "~") + _money(value)
        return _money(value) + ("+" if lower else "")

    text = (
        f"total {figure('total')} · in {figure('in')} · write {figure('write')}"
        f" · read {figure('read')} · out {figure('out')}"
    )
    if partial or "total" in short:
        text += " · partial"
    if any(isinstance(b, dict) and b.get("service_tier") == "priority" for b in bills.values()):
        text += " · requested-tier est."  # some round REQUESTED priority; not proof it applied
    if stale:
        text += " · stale prices"
    return text
