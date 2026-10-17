"""Process-local metadata for UUID-named Telegram mirrors (never a read ledger).

Every scan still enumerates and stats the files. Only new/changed bodies are
parsed; check needs compact metadata, whereas read/ID lookup reopen selected
bodies and regex search retains the full scanner. Nothing is persisted. A new
manager therefore pays one full cold scan. External changes must be visible in
the filesystem signature; in-process writers invalidate explicitly, including
on filesystems with coarse timestamps. This is not a content-hash validator for
out-of-band rewrites preserving all signature fields.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import stat
import threading
from typing import Iterator


class MirrorIndex:
    """Small per-record metadata, with no attachments or raw Update envelopes."""

    def __init__(self) -> None:
        self._folders: dict[Path, dict[Path, tuple[tuple[int, ...], dict]]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _signature(path: Path) -> tuple[int, ...]:
        st = path.stat()
        if not stat.S_ISREG(st.st_mode):
            raise OSError("not a regular mirror file")
        return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

    @contextmanager
    def writing(self, path: Path) -> Iterator[None]:
        """Serialize cache invalidation with our own writes, even same-size edits."""
        with self._lock:
            folder = path.parent.parent
            self._folders.get(folder, {}).pop(path, None)
            try:
                yield
            finally:
                self._folders.get(folder, {}).pop(path, None)

    def records(self, folder: Path) -> list[dict]:
        """Return detached metadata in filesystem order (stable date-sort ties)."""
        with self._lock:
            if not folder.is_dir():
                self._folders.pop(folder, None)
                return []
            previous = self._folders.get(folder, {})
            current: dict[Path, tuple[tuple[int, ...], dict]] = {}
            result = []
            for directory in folder.iterdir():
                path = directory / "message.json"
                if not directory.is_dir():
                    continue
                try:
                    signature = self._signature(path)
                    cached = previous.get(path)
                    if cached is not None and cached[0] == signature:
                        summary = cached[1]
                    else:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        summary = {key: data[key] for key in (
                            "id", "date", "chat", "to", "from",
                        ) if key in data}
                        summary["text"] = (data.get("text") or "")[:100]
                        summary["_dir"] = str(directory)
                        # Don't cache bytes read across a concurrent external write.
                        if self._signature(path) != signature:
                            result.append(deepcopy(summary))
                            continue
                    current[path] = (signature, summary)
                    result.append(deepcopy(summary))
                except (json.JSONDecodeError, OSError):
                    # Retry invalid/unreadable files next call, including recovery
                    # under a coarse unchanged timestamp. Never retain stale data.
                    continue
            self._folders[folder] = current  # evict deleted, renamed, corrupt files
            return result
