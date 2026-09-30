"""Preset connectivity checks for the system(action='presets') listing.

Two-tier check:
1. Credential check (free): is the api_key_env set in the environment?
2. Endpoint reachability (network): TCP connect to the LLM's base_url host.

The CLI-backed ``claude-code`` provider has no base_url: its credential check
is the provider's own auth order — a ``claude setup-token`` OAuth token in the
environment, else the local ``claude`` CLI login as reported by
``claude auth status --json`` (a local status read, never a model request).

NO CACHING. Every call probes fresh. Caching connectivity status would
let an agent confidently swap into a preset that went down between
the cache write and the swap — exactly the failure mode this check
exists to prevent. The agent calls `presets` deliberately as a
planning step; a 0.2-2s round-trip is invisible at that cadence.

Concurrency: check_many() runs all checks in parallel via ThreadPoolExecutor.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

_PROBE_TIMEOUT_S = 2.0

# CLI-backed providers drive a locally installed CLI (no base_url), so a TCP
# probe would be a false negative. Their health is "is the backing provider
# module importable, and does the provider's own credential source resolve?".
# Maps the provider name to the module that backs it.
_CLI_BACKED_PROVIDERS = {
    "claude-code": "lingtai.llm.claude_code.adapter",
}

#: The env var that carries a long-lived Claude Code OAuth token (the output of
#: ``claude setup-token``). It is the default ``api_key_env`` of a
#: ``claude-code`` preset and the process-env fallback when a preset names none.
CLAUDE_CODE_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

#: Env vars never passed to a ``claude`` CLI child: they would switch the CLI
#: to API-key billing instead of the subscription OAuth path.
CLAUDE_CODE_API_KEY_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: ``claude_cli_login_status`` outcomes.
CLAUDE_LOGIN_LOGGED_IN = "logged_in"
CLAUDE_LOGIN_NOT_LOGGED_IN = "not_logged_in"
CLAUDE_LOGIN_CLI_MISSING = "cli_missing"
CLAUDE_LOGIN_UNKNOWN = "unknown"

_CLAUDE_AUTH_STATUS_TIMEOUT_S = 15.0

#: The one actionable instruction shared by the adapter's request-time auth
#: error and this module's ``no_credentials`` report.
CLAUDE_CODE_AUTH_GUIDANCE = (
    "To authenticate, run `claude setup-token` and put the printed token in the "
    "env var named by the preset's `api_key_env` (default "
    "CLAUDE_CODE_OAUTH_TOKEN, e.g. in the agent's .env file), or log the local "
    "`claude` CLI in once with `claude auth login`"
)


def claude_code_token_from_env(api_key_env: str | None = None) -> str | None:
    """Return the configured Claude Code setup-token from the environment.

    Looks up the preset's ``api_key_env`` first, then the default
    ``CLAUDE_CODE_OAUTH_TOKEN``. Blank values count as absent. The value is
    returned only so the caller can pass it on; it is never logged.
    """
    names = [api_key_env] if api_key_env else []
    if CLAUDE_CODE_OAUTH_TOKEN_ENV not in names:
        names.append(CLAUDE_CODE_OAUTH_TOKEN_ENV)
    for name in names:
        value = os.environ.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def claude_cli_login_status(
    cli_path: str = "claude",
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: float = _CLAUDE_AUTH_STATUS_TIMEOUT_S,
) -> str:
    """Ask the installed ``claude`` CLI whether it is logged in.

    Runs ``claude auth status --json`` — a local status read that makes no
    model request — and returns one of ``CLAUDE_LOGIN_*``. ``loggedIn: true``
    in the JSON (whatever the auth method) is ``logged_in``; ``false`` is
    ``not_logged_in``; a missing binary is ``cli_missing``; a timeout, an
    unparseable reply, or a CLI too old to know the subcommand is ``unknown``
    so callers can fall back to the CLI's own request-time error instead of
    blocking a working login.
    """
    try:
        proc = subprocess.run(
            [cli_path, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return CLAUDE_LOGIN_CLI_MISSING
    except (OSError, subprocess.SubprocessError):
        return CLAUDE_LOGIN_UNKNOWN
    try:
        payload = json.loads((proc.stdout or "").strip())
    except (json.JSONDecodeError, ValueError):
        return CLAUDE_LOGIN_UNKNOWN
    if not isinstance(payload, dict):
        return CLAUDE_LOGIN_UNKNOWN
    logged_in = payload.get("loggedIn")
    if logged_in is True:
        return CLAUDE_LOGIN_LOGGED_IN
    if logged_in is False:
        return CLAUDE_LOGIN_NOT_LOGGED_IN
    return CLAUDE_LOGIN_UNKNOWN


# Default base_url per provider for presets that omit base_url. The ``openai``
# and ``anthropic`` families reach any compatible vendor through an explicit
# base_url; these are only the official endpoints used when it is omitted.
_PROVIDER_DEFAULT_URLS = {
    "openai":     "https://api.openai.com",
    "anthropic":  "https://api.anthropic.com",
    "codex":      "https://chatgpt.com",
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
    """Parse a base_url into the (host, port) pair to TCP-probe.

    Schemeless URLs (``api.openai.com``, ``myhost:8080``) are treated as
    https. This matters because ``urlparse`` is hostile to them: it swallows
    a bare ``host:port`` string as a bogus scheme and returns
    ``hostname=None``, and ``socket.create_connection((None, ...))`` would
    silently resolve to loopback — probing localhost instead of the
    configured endpoint. Returns None when no hostname can be extracted
    (e.g. ``https://`` or a non-numeric port) so the caller can fail loudly
    rather than probing whatever ``getaddrinfo(None, ...)`` fancies.
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


def _check_claude_code(
    provider: str | None, module_name: str, api_key_env: str | None
) -> dict:
    """Credential check for ``claude-code``: token first, then local login."""
    checked_at = datetime.now(timezone.utc).isoformat()

    def _result(status: str, error: str | None) -> dict:
        return {
            "status": status,
            "checked_at": checked_at,
            "latency_ms": None,
            "error": error,
        }

    if not _module_available(module_name):
        return _result(
            "no_credentials",
            f"{provider} is a CLI-backed provider but its backing module "
            f"{module_name!r} is not importable — ensure the kernel is installed",
        )
    # 1. A configured setup-token is sufficient on its own (the adapter runs
    #    the CLI against a private config dir with just that token).
    if claude_code_token_from_env(api_key_env):
        return _result("ok", None)
    # 2. Otherwise the local CLI login, checked the same way the adapter
    #    checks it: API-key env stripped, local status read only.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in CLAUDE_CODE_API_KEY_ENV and k != CLAUDE_CODE_OAUTH_TOKEN_ENV
    }
    # A neutral cwd, like the adapter's: no project settings from wherever
    # the agent process happens to run.
    status = claude_cli_login_status(env=env, cwd=tempfile.gettempdir())
    if status == CLAUDE_LOGIN_CLI_MISSING:
        return _result(
            "no_credentials",
            f"no {CLAUDE_CODE_OAUTH_TOKEN_ENV} token is set and the `claude` "
            f"CLI is not on PATH; install Claude Code. "
            f"{CLAUDE_CODE_AUTH_GUIDANCE}",
        )
    if status == CLAUDE_LOGIN_NOT_LOGGED_IN:
        return _result(
            "no_credentials",
            f"no {CLAUDE_CODE_OAUTH_TOKEN_ENV} token is set and the local "
            f"`claude` CLI is not logged in. {CLAUDE_CODE_AUTH_GUIDANCE}",
        )
    # logged_in, or unknown (the adapter then lets the CLI report its own
    # auth error at request time rather than blocking a working login).
    return _result("ok", None)


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

    Schemeless base URLs (``api.openai.com``, ``myhost:8080``) are probed as
    https (port 443). A base URL with no extractable hostname yields
    ``"unreachable"`` with an ``invalid base_url`` error rather than a
    silent localhost probe.
    """
    # CLI-backed providers (claude-code) have no network endpoint to probe:
    # a base_url probe would be a false negative. Gauge health by the backing
    # module plus the provider's own auth order — never reach the base_url
    # resolution below for these, and never make a model request.
    module_name = _CLI_BACKED_PROVIDERS.get((provider or "").lower())
    if module_name is not None:
        return _check_claude_code(provider, module_name, api_key_env)

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
