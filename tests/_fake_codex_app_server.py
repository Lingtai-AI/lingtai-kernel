"""Tiny newline-delimited fake for the two quota helper tests."""
from __future__ import annotations

import json
import os
import sys


def _write(obj) -> None:
    print(json.dumps(obj), flush=True)


def main() -> int:
    used = 100 if os.environ.get("LINGTAI_FAKE_APP_SERVER_MODE") == "exhausted" else 10
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        method = request.get("method")
        if method == "initialize":
            _write({"id": request.get("id"), "result": {}})
        elif method == "account/rateLimits/read":
            _write({
                "id": request.get("id"),
                "result": {"rateLimits": {"primary": {"usedPercent": used}}},
            })
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
