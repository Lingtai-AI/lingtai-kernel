"""Lightweight prompt-cache/token audit JSONL writer.

This module is intentionally fail-open: cache auditing must never prevent the
agent's main LLM flow from starting or completing.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CacheAuditor:
    """Append normalized LLM usage observations to a JSONL file.

    Construction and writes are fail-open. If the parent directory cannot be
    created, the auditor disables itself; if a later write fails, the exception
    is swallowed. Observability must not become an availability dependency.
    """

    def __init__(self, log_path: str | Path) -> None:
        self._path = Path(log_path)
        self._lock = threading.Lock()
        self._enabled = True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._enabled = False

    @property
    def enabled(self) -> bool:
        """Whether this auditor will attempt writes."""
        return self._enabled

    def record(
        self,
        *,
        call_role: str,
        provider: str,
        model: str,
        endpoint: str = "",
        agent_name: str = "",
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int = 0,
        thinking_tokens: int = 0,
        system_tokens: int = 0,
        tools_tokens: int = 0,
        batch_hashes: list[str] | None = None,
        task_description: str = "",
        task_outcome: str = "",
        narrative_tag: str = "",
        **extra: Any,
    ) -> None:
        """Append one usage observation, swallowing all failures.

        ``uncached_input`` is computed from normalized provider-agnostic usage
        fields. For Anthropic this currently includes cache-write tokens because
        ``UsageMetadata`` preserves only total input and cache-read tokens; treat
        it as a coarse observation field, not exact billing cost.
        """
        if not self._enabled:
            return
        try:
            input_tokens = int(input_tokens or 0)
            cached_tokens = int(cached_tokens or 0)
            output_tokens = int(output_tokens or 0)
            thinking_tokens = int(thinking_tokens or 0)
            system_tokens = int(system_tokens or 0)
            tools_tokens = int(tools_tokens or 0)
            uncached = max(0, input_tokens - cached_tokens)
            cache_ratio = round(cached_tokens / input_tokens, 4) if input_tokens > 0 else 0.0
            entry: dict[str, Any] = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "call_role": call_role,
                "provider": provider,
                "model": model,
                "endpoint": endpoint or "",
                "agent_name": agent_name or "",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "thinking_tokens": thinking_tokens,
                "cached_tokens": cached_tokens,
                "uncached_input": uncached,
                "cache_ratio": cache_ratio,
                "system_tokens": system_tokens,
                "tools_tokens": tools_tokens,
                "batch_hashes": batch_hashes or [],
                "task_description": task_description,
                "task_outcome": task_outcome,
                "narrative_tag": narrative_tag,
            }
            if extra:
                entry.update(extra)
            with self._lock:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass


def batch_hash(text: str) -> str:
    """Return a short stable hash for one rendered prompt batch."""
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]
