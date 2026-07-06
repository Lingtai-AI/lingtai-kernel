"""Preset connectivity checks for the system(action='presets') listing.

Two-tier check:
1. Credential check (free): is the api_key_env set in the environment?
2. Endpoint reachability (network): TCP connect to the LLM's base_url host.

NO CACHING. Every call probes fresh. Caching connectivity status would
let an agent confidently swap into a preset that went down between
the cache write and the swap — exactly the failure mode this check
exists to prevent. The agent calls `presets` deliberately as a
planning step; a 0.2-2s round-trip is invisible at that cadence.

Concurrency: check_many() runs all checks in parallel via ThreadPoolExecutor.
"""
from __future__ import annotations

import importlib.util
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

_PROBE_TIMEOUT_S = 2.0

# Local CLI-login providers authenticate through a locally installed CLI/login
# session (no per-request API key, no base_url) — so a TCP probe would be a
# false negative. Health for these is "is the backing provider module importable?".
# Maps the provider name (and its aliases) to the module that backs it.
_LOCAL_CLI_LOGIN_PROVIDERS = {
    "claude-code": "lingtai.llm.claude_code.adapter",
    "claude_code": "lingtai.llm.claude_code.adapter",
}

# Default base_url per provider for presets that omit base_url.
_PROVIDER_DEFAULT_URLS = {
    "openai":     "https://api.openai.com",
    "anthropic":  "https://api.anthropic.com",
    "gemini":     "https://generativelanguage.googleapis.com",
    "deepseek":   "https://api.deepseek.com",
    "minimax":    "https://api.minimax.io",
    "zhipu":      "https://open.bigmodel.cn",
    "openrouter": "https://openrouter.ai",
    "codex":      "https://chatgpt.com",
    "mimo":       "https://api.xiaomimimo.com",
    "kimi":       "https://api.kimi.com",
}


def _probe_host(host: str, port: int, timeout: float) -> int:
    """Open a TCP connection to (host, port). Returns latency in ms on success.

    Raises OSError on any connect failure (DNS, refused, timeout, etc.).
    """
    start = time.monotonic()
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        elapsed_ms = int((time.monotonic() - start) * 1000)
    finally:
        sock.close()
    return elapsed_ms


def _module_available(module_name: str) -> bool:
    """Return True if ``module_name`` can be imported without importing it.

    Used to gauge a local CLI-login provider's health: the optional package
    being installed is the in-process, network-free signal that the provider
    is usable.
    """
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _resolve_url(provider: str | None, base_url: str | None) -> str | None:
    """Pick the URL to probe: explicit base_url > provider default > None."""
    if base_url:
        return base_url
    if provider and provider.lower() in _PROVIDER_DEFAULT_URLS:
        return _PROVIDER_DEFAULT_URLS[provider.lower()]
    return None


def _parse_probe_target(url: str) -> tuple[str, int] | None:
    """Parse ``url`` into a ``(host, port)`` tuple for a TCP probe.

    Guards against two surprising ``urlparse`` behaviors that otherwise make
    the probe silently resolve to localhost (``getaddrinfo(None, ...)``):

    - ``urlparse("api.openai.com")`` -> ``scheme=''``, ``hostname=None`` (the
      whole string lands in ``path``).
    - ``urlparse("myhost:8080")`` -> ``scheme='myhost'``, ``hostname=None`` (the
      host is swallowed as a scheme).

    A schemeless URL is normalized to https before parsing — matching every
    ``_PROVIDER_DEFAULT_URLS`` entry and the realistic intent of an API base
    URL — so ``api.openai.com`` probes ``(api.openai.com, 443)`` and
    ``myhost:8080`` probes ``(myhost, 8080)``. The scheme test must be
    ``"://" not in url`` rather than ``parsed.scheme == ""`` precisely because
    the ``myhost:8080`` case yields a non-empty bogus scheme.

    Returns ``None`` (never a localhost fallback) when no hostname can be
    extracted or the port is non-numeric, so the caller can fail loud.
    """
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    try:
        port = parsed.port  # raises ValueError on e.g. "host:notaport"
    except ValueError:
        return None
    return parsed.hostname, port or (443 if parsed.scheme == "https" else 80)


def check_connectivity(
    provider: str | None,
    base_url: str | None,
    api_key_env: str | None,
) -> dict:
    """Check whether a preset's LLM is reachable RIGHT NOW.

    No caching. Every call is a fresh check.

    Returns a dict with shape:
        {"status": "ok" | "no_credentials" | "unreachable",
         "checked_at": "<ISO timestamp>",
         "latency_ms": int (only on ok),
         "error": str | None}

    A schemeless base_url (e.g. ``api.openai.com`` or ``myhost:8080``) is
    probed as https on its real host — never silently against localhost. A
    base_url with no extractable host yields ``"unreachable"`` with an
    ``invalid base_url`` error rather than a misleading loopback probe.
    """
    # Local CLI-login providers (e.g. claude-code) have no network
    # endpoint and no API key — they authenticate through a local CLI/login
    # session. Probing a base_url would be a false negative, so gauge health
    # by whether the backing provider module is importable. Never reach the
    # base_url resolution below for these.
    module_name = _LOCAL_CLI_LOGIN_PROVIDERS.get((provider or "").lower())
    if module_name is not None:
        if _module_available(module_name):
            return {
                "status": "ok",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": None,
                "error": None,
            }
        return {
            "status": "no_credentials",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "error": (
                f"{provider} is a local CLI-login provider but its backing "
                f"module {module_name!r} is not importable — ensure the kernel "
                f"is installed and run `claude` (or `claude setup-token`) to log in"
            ),
        }

    # Credential check (free) — never makes a network call.
    if api_key_env and not os.environ.get(api_key_env):
        return {
            "status": "no_credentials",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "error": f"{api_key_env} not set in environment",
        }

    # Resolve URL to probe.
    url = _resolve_url(provider, base_url)
    if not url:
        return {
            "status": "unreachable",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "error": f"no base_url and no default URL for provider {provider!r}",
        }

    target = _parse_probe_target(url)
    if target is None:
        return {
            "status": "unreachable",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "error": f"invalid base_url {url!r}: cannot determine host to probe",
        }
    host, port = target

    try:
        latency_ms = _probe_host(host, port, _PROBE_TIMEOUT_S)
        return {
            "status": "ok",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": latency_ms,
            "error": None,
        }
    except (OSError, socket.timeout) as e:
        return {
            "status": "unreachable",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "error": str(e),
        }


def check_many(specs: list[dict]) -> list[dict]:
    """Run check_connectivity in parallel for a list of {provider, base_url,
    api_key_env} dicts. Returns the results in the same order as specs.
    """
    if not specs:
        return []
    results: list[dict | None] = [None] * len(specs)
    with ThreadPoolExecutor(max_workers=min(len(specs), 16)) as pool:
        futures = {
            pool.submit(check_connectivity, **spec): i
            for i, spec in enumerate(specs)
        }
        for fut in as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()
    return results
