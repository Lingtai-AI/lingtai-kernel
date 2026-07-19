"""Deterministic fake `codex app-server` for Codex quota-read tests.

Speaks the same newline-delimited JSON-RPC-over-stdio shape as the real
``codex app-server`` for exactly the handshake
``lingtai.llm.openai.codex_quota`` drives: ``initialize`` -> response,
``initialized`` notification (ignored), ``account/rateLimits/read`` ->
response. Behavior is switched by the ``LINGTAI_FAKE_APP_SERVER_MODE`` env var
so a single script covers every fixture scenario without argv plumbing through
``subprocess.Popen``:

  * ``ok``            -> normal handshake + a realistic rate-limits payload.
  * ``sparse``         -> a minimal/sparse rate-limits payload (nulls, no
                          ``rateLimitsByLimitId``, no credits).
  * ``exhausted``       -> ``usedPercent: 100`` on the main window (remaining
                          percent == 0), for the quota-aware pool-exclusion
                          tests.
  * ``error_response`` -> the ``account/rateLimits/read`` request gets a
                          JSON-RPC error instead of a result.
  * ``malformed``       -> the read response's ``result`` is not a dict.
  * ``hang``            -> never writes anything to stdout (drives the
                          caller's timeout path).
  * ``init_error``      -> the ``initialize`` request itself gets an error.

Never reads real auth files; ``$CODEX_HOME`` is accepted but ignored.
"""
from __future__ import annotations

import json
import os
import sys
import time


def _write(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    mode = os.environ.get("LINGTAI_FAKE_APP_SERVER_MODE", "ok")

    if mode == "hang":
        time.sleep(60)
        return 0

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        method = obj.get("method")
        req_id = obj.get("id")

        if method == "initialize":
            if mode == "init_error":
                _write({"id": req_id, "error": {"code": -1, "message": "boom"}})
                return 0
            _write({
                "id": req_id,
                "result": {
                    "codexHome": os.environ.get("CODEX_HOME", ""),
                    "platformFamily": "unix",
                    "platformOs": "macos",
                    "userAgent": "fake/0.0.0",
                },
            })
        elif method == "initialized":
            continue  # notification, no response
        elif method == "account/rateLimits/read":
            if mode == "error_response":
                _write({"id": req_id, "error": {"code": 42, "message": "nope"}})
            elif mode == "malformed":
                _write({"id": req_id, "result": "not-a-dict"})
            elif mode == "exhausted":
                _write({
                    "id": req_id,
                    "result": {
                        "rateLimits": {
                            "limitId": "codex",
                            "limitName": None,
                            "primary": {
                                "usedPercent": 100,
                                "windowDurationMins": 10080,
                                "resetsAt": 1784955319,
                            },
                            "secondary": None,
                            "credits": {
                                "hasCredits": False,
                                "unlimited": False,
                                "balance": "0",
                            },
                            "individualLimit": None,
                            "planType": "pro",
                            "rateLimitReachedType": "rate_limit_reached",
                        },
                        "rateLimitsByLimitId": None,
                        "rateLimitResetCredits": None,
                    },
                })
            elif mode == "sparse":
                _write({
                    "id": req_id,
                    "result": {
                        "rateLimits": {
                            "limitId": None,
                            "limitName": None,
                            "primary": None,
                            "secondary": None,
                            "credits": None,
                            "individualLimit": None,
                            "planType": None,
                            "rateLimitReachedType": None,
                        },
                        "rateLimitsByLimitId": None,
                        "rateLimitResetCredits": None,
                    },
                })
            else:
                _write({
                    "id": req_id,
                    "result": {
                        "rateLimits": {
                            "limitId": "codex",
                            "limitName": None,
                            "primary": {
                                "usedPercent": 10,
                                "windowDurationMins": 10080,
                                "resetsAt": 1784955319,
                            },
                            "secondary": None,
                            "credits": {
                                "hasCredits": False,
                                "unlimited": False,
                                "balance": "0",
                            },
                            "individualLimit": None,
                            "planType": "pro",
                            "rateLimitReachedType": None,
                        },
                        "rateLimitsByLimitId": {
                            "codex": {
                                "limitId": "codex",
                                "limitName": None,
                                "primary": {
                                    "usedPercent": 10,
                                    "windowDurationMins": 10080,
                                    "resetsAt": 1784955319,
                                },
                                "secondary": None,
                                "credits": {
                                    "hasCredits": False,
                                    "unlimited": False,
                                    "balance": "0",
                                },
                                "individualLimit": None,
                                "planType": "pro",
                                "rateLimitReachedType": None,
                            },
                            "codex_bengalfox": {
                                "limitId": "codex_bengalfox",
                                "limitName": "GPT-5.3-Codex-Spark",
                                "primary": {
                                    "usedPercent": 0,
                                    "windowDurationMins": 10080,
                                    "resetsAt": 1785064700,
                                },
                                "secondary": None,
                                "credits": None,
                                "individualLimit": None,
                                "planType": "pro",
                                "rateLimitReachedType": None,
                            },
                        },
                        "rateLimitResetCredits": {
                            "availableCount": 1,
                            "credits": None,
                        },
                    },
                })
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
