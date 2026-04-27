"""Token ledger — append-only JSONL log of per-LLM-call token usage.

Single source of truth for lifetime token statistics.
Written alongside chat_history after every LLM call.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def append_token_entry(
    path: Path | str,
    *,
    input: int,
    output: int,
    thinking: int,
    cached: int,
    extra: dict | None = None,
) -> None:
    """Append one token usage entry to the ledger.

    Creates parent directories and the file if they don't exist.

    `extra` is an optional dict of additional fields merged into the entry.
    Required fields (ts/input/output/thinking/cached) take precedence — if a
    caller passes `extra={"input": 999}`, the explicit input value still wins.
    Used by the daemon capability to tag entries with source/em_id/run_id
    so the parent's ledger preserves per-daemon attribution.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry: dict = {}
    if extra:
        entry.update(extra)
    entry.update({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input": input,
        "output": output,
        "thinking": thinking,
        "cached": cached,
    })
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def sum_token_ledger(path: Path | str) -> dict:
    """Sum all entries in the token ledger.

    Returns dict with keys: input_tokens, output_tokens, thinking_tokens,
    cached_tokens, api_calls (= number of valid entries).

    Skips corrupt lines gracefully.
    """
    path = Path(path)
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cached_tokens": 0,
        "api_calls": 0,
    }
    if not path.is_file():
        return totals
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        totals["input_tokens"] += entry.get("input", 0)
        totals["output_tokens"] += entry.get("output", 0)
        totals["thinking_tokens"] += entry.get("thinking", 0)
        totals["cached_tokens"] += entry.get("cached", 0)
        totals["api_calls"] += 1
    return totals
