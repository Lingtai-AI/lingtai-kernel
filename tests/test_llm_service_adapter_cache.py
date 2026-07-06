"""Regression tests for LLMService adapter-cache concurrency.

Lingtai-AI/lingtai-kernel#739: ``LLMService.get_adapter`` has a lock-free
"fast path" that, when the caller passes no ``base_url``, scans the whole
``self._adapters`` dict looking for any adapter whose provider matches. That
scan runs without ``self._adapter_lock``, while the slow path inside the lock
does ``self._adapters[cache_key] = adapter``. In CPython, iterating a dict
while another thread inserts into it raises
``RuntimeError: dictionary changed size during iteration`` — surfacing as a
spurious, unexplained LLM failure right when several threads race to warm the
cache. The fix scans a snapshot so a concurrent insert can no longer interleave
with the iteration.
"""
from __future__ import annotations

import threading

from lingtai.llm.service import LLMService


def _register_opaque_adapter(provider: str) -> None:
    """Register a hermetic adapter factory returning a fresh opaque object."""
    LLMService.register_adapter(provider, lambda **kwargs: object())


def test_get_adapter_scan_race_with_concurrent_insert():
    """The base_url=None fast-path scan must not crash on a concurrent insert.

    Pre-fix, a reader iterating ``self._adapters.items()`` without the lock
    while a writer inserts a new key raises
    ``RuntimeError: dictionary changed size during iteration``. This test
    hammers that race: many readers scan for provider "a" (no base_url) while
    writers create adapters for fresh ("b", base_url) keys.
    """
    _register_opaque_adapter("z")
    _register_opaque_adapter("b")

    svc = LLMService(
        provider="z",
        model="m",
        api_key="sk-z",
        key_resolver=lambda p: "sk-key",
    )
    # Remove the boot ("z", None) entry: with a ("z", None) key present the
    # exact-key fast path (service.py:288) returns immediately and never
    # reaches the scan. The scan at service.py:291-294 only runs when there is
    # NO (provider, None) key but there IS a (provider, other_url) entry, so we
    # seed the lone match under an explicit base_url.
    del svc._adapters[("z", None)]
    for i in range(3000):
        svc._adapters[("b", f"http://filler/{i}")] = object()
    # Put the single matching entry LAST so the scan must traverse the whole
    # dict (past the churning region) before it can return.
    seeded_z = object()
    svc._adapters[("z", "http://only")] = seeded_z

    errors: list[BaseException] = []
    stop = threading.Event()
    barrier = threading.Barrier(4)

    def reader():
        barrier.wait()
        try:
            while not stop.is_set():
                svc.get_adapter("z")  # base_url=None -> full dict scan
        except BaseException as exc:  # noqa: BLE001 - capture RuntimeError race
            errors.append(exc)

    def writer():
        barrier.wait()
        try:
            i = 0
            while not stop.is_set():
                # Model the slow-path insert at service.py:320: a write into
                # the same dict the lock-free readers are scanning, under the
                # real lock the production writer holds. The reader does NOT
                # take that lock, so its scan is unprotected. Growing the dict
                # (net insert) changes its size mid-scan, which is exactly the
                # condition CPython raises "dictionary changed size during
                # iteration" for.
                with svc._adapter_lock:
                    svc._adapters[("b", f"http://churn/{i}")] = object()
                i += 1
                if i % 500 == 0:
                    # Periodically prune so memory stays bounded across the run.
                    with svc._adapter_lock:
                        for j in range(i - 500, i):
                            svc._adapters.pop(("b", f"http://churn/{j}"), None)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(3)] + [
        threading.Thread(target=writer) for _ in range(1)
    ]
    for t in threads:
        t.start()
    # Let the race run briefly, then stop.
    stop_timer = threading.Timer(1.5, stop.set)
    stop_timer.start()
    for t in threads:
        t.join(timeout=5)
    stop.set()
    stop_timer.cancel()

    assert not errors, f"get_adapter raced on concurrent insert: {errors!r}"


def test_get_adapter_base_url_none_still_matches_provider():
    """The snapshot scan still returns a cached adapter for the provider."""
    _register_opaque_adapter("a")
    svc = LLMService(
        provider="a",
        model="m",
        api_key="sk-a",
        key_resolver=lambda p: "sk-key",
    )
    seeded = svc._adapters[("a", None)]
    assert svc.get_adapter("a") is seeded
