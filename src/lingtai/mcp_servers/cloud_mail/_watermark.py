"""Per-account Cloud Mail polling watermark store.

Cloud Mail rows carry a monotonically increasing integer ``emailId``. The
watermark records the highest ``emailId`` we have already delivered to the
host agent so a restart never re-notifies old mail.

State file shape::

    {
      "last_email_id": <int>,
      "seeded": true
    }

``seeded`` distinguishes a fresh first run (where we record the current
high-water mark WITHOUT flooding the agent with historical mail) from a
genuine empty mailbox.

Atomic on POSIX and Windows via tmp-file + os.replace. Corrupt or missing
files are treated as empty — the addon re-seeds on next poll.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class WatermarkStore:
    """Tiny JSON-on-disk persistence for the per-account emailId watermark."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self) -> dict:
        """Return the persisted dict, or {} if missing/corrupt."""
        if not self._path.is_file():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, state: dict) -> None:
        """Atomically replace the state file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            os.write(fd, json.dumps(state, indent=2).encode("utf-8"))
            os.close(fd)
            os.replace(tmp, str(self._path))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise

    # -- convenience helpers --

    @property
    def last_email_id(self) -> int:
        state = self.load()
        try:
            return int(state.get("last_email_id", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def seeded(self) -> bool:
        return bool(self.load().get("seeded", False))

    def set_last_email_id(self, email_id: int, *, seeded: bool = True) -> None:
        self.save({"last_email_id": int(email_id), "seeded": bool(seeded)})
