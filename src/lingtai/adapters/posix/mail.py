"""POSIX filesystem mail transport adapter.

``PosixFilesystemMailAdapter`` implements the Core-owned
``lingtai.kernel.mail_transport.MailTransportPort`` by delivering messages as
files written directly into a recipient's inbox directory and by polling its own
inbox (plus subscribed pseudo-agent outboxes) on a fixed cadence. It is the only
production transport adapter; Core never constructs it.

The concrete addressing (absolute agent-workdir paths), storage layout
(``mailbox/{inbox,outbox,sent}/<id>/message.json`` plus ``attachments/``),
atomic writes, handshake-before-delivery, optimistic pseudo-outbox
claim/rollback, and 0.5-second polling all live here — they are the POSIX
filesystem mechanism, not part of the technology-neutral Port.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator

from lingtai.kernel.agent_presence import (
    is_agent as _presence_is_agent,
    observe_alive as _presence_observe_alive,
)
from lingtai.kernel.mail_transport import MailTransportPort
from lingtai.kernel.services.mail import _new_mailbox_id

logger = logging.getLogger(__name__)

# A ``message.json`` that fails to read (transient OSError: permissions blip,
# network-filesystem hiccup) is retried for this many poll cycles before being
# dead-lettered. At the 0.5 s poll cadence this is ~5 s of retries.
_MAX_READ_RETRIES = 10
# Dead-letter store for permanently bad inbox entries, mirroring the MCP inbox
# poller's ``.dead/`` convention.
_DEAD_DIRNAME = ".dead"


def _presence_store_for(recipient_dir: Path):
    """Build a target-bound POSIX presence adapter for a recipient directory."""
    from lingtai.adapters.posix.agent_presence import PosixAgentPresenceStoreAdapter

    return PosixAgentPresenceStoreAdapter(recipient_dir)


class PosixFilesystemMailAdapter(MailTransportPort):
    """Filesystem-based mail delivery.

    Delivers messages by writing files directly to the recipient's inbox
    directory.  Monitors its own inbox via polling.

    Send addresses are absolute agent-workdir paths.  Example::

        svc = PosixFilesystemMailAdapter(Path("/agents/abc123"))
        svc.listen(on_message=lambda msg: print(msg))  # poll own inbox
        svc.send("/agents/def456", {"message": "hello"})  # write to another agent
    """

    def __init__(
        self,
        working_dir: str | Path,
        mailbox_rel: str = "mailbox",
        pseudo_agent_subscriptions: list[str] | None = None,
    ) -> None:
        self._working_dir = Path(working_dir)
        self._mailbox_rel = mailbox_rel
        self._mailbox_dir = self._working_dir / mailbox_rel
        self._inbox_dir = self._mailbox_dir / "inbox"
        self._inbox_dir.mkdir(parents=True, exist_ok=True)

        # Resolve subscribed pseudo-agent folders once at construction time.
        # Each subscription is a path relative to working_dir; we keep them as
        # absolute Paths so a later cwd change doesn't break lookups.
        self._pseudo_agent_dirs: list[Path] = []
        for sub in (pseudo_agent_subscriptions or []):
            self._pseudo_agent_dirs.append((self._working_dir / sub).resolve())

        # Polling state
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()
        self._seen: set[str] = set()
        # Consecutive read-failure counts per inbox entry name, for bounded
        # retry of transiently unreadable ``message.json`` files.
        self._read_failures: dict[str, int] = {}
        # Own-inbox scans can be expensive on large/slow external-volume
        # mailboxes. Keep iterator progress across poll ticks so Phase 2 can
        # be sliced instead of restarting from the first historical entry.
        self._own_inbox_iter: Iterator[Path] | None = None
        # Directory names observed in the current own-inbox pass. When a pass
        # completes, ids in ``_seen`` that were not observed and whose inbox
        # directories no longer exist are pruned so ``_seen`` tracks the live
        # inbox instead of all history (see ``_prune_seen_to_live_inbox``).
        self._own_inbox_observed: set[str] = set()
        self._own_inbox_slice_seconds = 0.05
        self._own_inbox_slice_entries = 200
        self._mail_poll_slow_seconds = 1.0

    @property
    def pseudo_agent_subscriptions(self) -> tuple[str, ...]:
        """Return the effective constructor-time subscription snapshot."""
        return tuple(str(path) for path in self._pseudo_agent_dirs)

    # ------------------------------------------------------------------
    # address
    # ------------------------------------------------------------------

    @property
    def address(self) -> str:
        """Return the working directory name as this agent's mail address."""
        return self._working_dir.name

    # ------------------------------------------------------------------
    # send
    # ------------------------------------------------------------------

    def send(
        self,
        address: str,
        message: dict,
    ) -> str | None:
        """Deliver *message* to the agent at *address*.

        Handshake:
        1. ``{address}/.agent.json`` must exist.
        2. ``{address}/.agent.heartbeat`` must be fresh under the shared
           ``HEARTBEAT_LIVENESS_SECONDS`` policy (10 seconds by default; a
           valid ``LINGTAI_AGENT_ALIVE_THRESHOLD_SEC`` override is resolved
           when ``lingtai.kernel.config`` loads).

        Then stage the whole message (attachments + ``message.json``) in a
        hidden ``.<id>.staging`` directory inside the recipient's inbox and
        publish it with a single atomic ``os.replace``, so the recipient can
        never observe a partial inbox entry. Every failure path removes the
        sender-owned staging directory and leaves the recipient's inbox
        untouched.

        Addressing: *address* is a literal absolute path to the recipient's
        agent workdir (cross-network, same machine).
        """
        recipient_dir = Path(address)
        if not recipient_dir.is_absolute():
            return f"Address must be an absolute agent-workdir path: {address}"

        # --- handshake ------------------------------------------------
        # Observe the recipient's presence through a target-bound presence store
        # and apply Core policy (a malformed manifest still counts as an agent;
        # a fresh non-human heartbeat under the shared liveness window
        # (HEARTBEAT_LIVENESS_SECONDS, resolved from
        # LINGTAI_AGENT_ALIVE_THRESHOLD_SEC when lingtai.kernel.config loads) counts
        # as alive).
        recipient_presence = _presence_store_for(recipient_dir)
        if not _presence_is_agent(recipient_presence.observe_manifest()):
            return f"No agent at {address}"

        if not _presence_observe_alive(
            recipient_presence,
            wall_now=time.time(),
        ):
            return f"Agent at {address} is not running"

        # --- create inbox entry ---------------------------------------
        msg_id = _new_mailbox_id()
        inbox_dir = recipient_dir / self._mailbox_rel / "inbox"
        msg_dir = inbox_dir / msg_id

        # Inject mailbox metadata (required by mail intrinsic for
        # message tracking, read/unread, reply, archive, delete).
        from datetime import datetime, timezone
        message = {
            **message,
            "_mailbox_id": msg_id,
            "received_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }

        # Validate every attachment path up front: a missing file is a
        # zero-side-effect early return instead of a partial inbox entry.
        attachment_paths = message.get("attachments")
        srcs: list[Path] = []
        if attachment_paths:
            for fpath in attachment_paths:
                src = Path(fpath)
                if not src.is_file():
                    return f"Attachment not found: {fpath}"
                srcs.append(src)

        # Assemble the whole message (attachments + ``message.json``) in a
        # hidden staging dir inside the recipient's inbox, then publish with a
        # single atomic ``os.replace``. Staging under the inbox (not the system
        # temp dir) keeps the final rename on one filesystem, and the
        # dot-prefixed name keeps the listener from ever dispatching a partial
        # entry. Every failure path removes the sender-owned staging dir, so a
        # failed send never leaves an orphan in another agent's mailbox.
        inbox_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = inbox_dir / f".{msg_id}.staging"
        try:
            staging_dir.mkdir(parents=True, exist_ok=True)
            if srcs:
                staging_att_dir = staging_dir / "attachments"
                staging_att_dir.mkdir(parents=True, exist_ok=True)
                # Recipient-local paths recorded in the payload must point at
                # the final location (``msg_dir``), not the staging path.
                # Duplicate basenames are disambiguated with a ``-N`` suffix
                # (``a.txt`` -> ``a-1.txt``) so no attachment silently
                # overwrites an earlier one (#1354): every accepted source is
                # delivered as its own physical file with its own payload path.
                local_names = _unique_attachment_names(srcs)
                local_copies = [
                    str(msg_dir / "attachments" / name) for name in local_names
                ]
                for src, name in zip(srcs, local_names):
                    shutil.copy2(src, staging_att_dir / name)
                message = {**message, "attachments": local_copies}

            (staging_dir / "message.json").write_text(
                json.dumps(message, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(str(staging_dir), str(msg_dir))
        except OSError as e:
            _remove_tree_retry(staging_dir)
            return f"Failed to write message: {e}"

        return None

    # ------------------------------------------------------------------
    # listen / stop
    # ------------------------------------------------------------------

    def listen(self, on_message: Callable[[dict], None]) -> None:
        """Start polling the inbox for new messages.

        Existing messages are recorded in ``_seen`` so they are not
        re-delivered.  New directories that appear with a ``message.json``
        trigger *on_message*. After each complete own-inbox pass, ``_seen``
        is pruned to ids whose inbox directories still exist (archived or
        deleted messages drop out of the dedupe set).

        Re-entry guard: raises ``RuntimeError`` when a poll thread is already
        running, so a second ``listen()`` cannot spawn a second poll thread
        that double-dispatches the same messages.  The agent lifecycle
        (``base_agent/lifecycle.py``) relies on this exception to treat a
        repeated ``listen()`` as "already listening".
        """
        if self._poll_thread is not None and self._poll_thread.is_alive():
            raise RuntimeError("MailService already listening")
        # Snapshot existing inbox entries so we don't re-notify. Dotted
        # directories (e.g. the ``.dead/`` dead-letter store) are not inbox
        # entries and are excluded from the snapshot.
        if self._inbox_dir.is_dir():
            for entry in self._inbox_dir.iterdir():
                if entry.is_dir() and not entry.name.startswith("."):
                    self._seen.add(entry.name)

        self._poll_stop.clear()

        def _poll_loop() -> None:
            while not self._poll_stop.is_set():
                # Phase 1 — subscribed pseudo-agent outboxes. Poll these
                # before historical own-inbox scans so urgent human/TUI
                # wake mail cannot starve behind old inbox entries.
                self._poll_pseudo_outboxes(on_message)

                # Phase 2 — own inbox, sliced. A large historical inbox on a
                # slow external volume must not monopolize the poll thread for
                # minutes. Recheck pseudo outboxes only after this slice
                # examined entries; an idle slice must not double the baseline
                # external-volume scan.
                if self._poll_own_inbox_slice(on_message):
                    self._poll_pseudo_outboxes(on_message)

                self._poll_stop.wait(0.5)

        self._poll_thread = threading.Thread(target=_poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_pseudo_outboxes(self, on_message: Callable[[dict], None]) -> None:
        """Poll subscribed pseudo-agent outboxes, isolating per-dir errors."""
        start = time.monotonic()
        dirs = 0
        for pseudo_dir in self._pseudo_agent_dirs:
            dirs += 1
            try:
                self._poll_pseudo_outbox(pseudo_dir, on_message)
            except OSError:
                logger.debug(
                    "pseudo-agent outbox poll failed for %s",
                    pseudo_dir,
                    exc_info=True,
                )
        self._log_slow_mail_phase(
            "pseudo_outboxes",
            start,
            dirs=dirs,
        )

    def _poll_own_inbox_slice(
        self,
        on_message: Callable[[dict], None],
        *,
        budget_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> bool:
        """Poll a bounded slice of own inbox entries.

        Returns True when this slice examined at least one directory entry.
        Keeping iterator progress turns the historical own-inbox scan into
        small slices, letting pseudo-agent outboxes be checked between chunks
        without scanning them twice on an idle tick.
        """
        budget = self._own_inbox_slice_seconds if budget_seconds is None else budget_seconds
        limit = self._own_inbox_slice_entries if max_entries is None else max_entries
        if budget <= 0:
            budget = self._own_inbox_slice_seconds
        if limit <= 0:
            limit = self._own_inbox_slice_entries

        start = time.monotonic()
        deadline = start + budget
        visited = 0
        skipped_seen = 0
        dispatched = 0

        try:
            if not self._inbox_dir.is_dir():
                self._reset_own_inbox_iter()
                return False
            if self._own_inbox_iter is None:
                self._own_inbox_iter = iter(self._inbox_dir.iterdir())
                self._own_inbox_observed = set()

            while not self._poll_stop.is_set() and visited < limit:
                # Every branch below—including cheap `_seen` skips—must pay
                # the time budget before another directory entry is fetched.
                # One entry is always allowed so the cursor can make progress.
                if visited and time.monotonic() >= deadline:
                    break
                try:
                    entry = next(self._own_inbox_iter)
                except StopIteration:
                    # Complete pass: prune _seen to ids whose inbox dirs still
                    # exist (archived/deleted messages drop out). Pruning only
                    # here, on a full scan, means a partial pass can never
                    # evict live entries.
                    self._prune_seen_to_live_inbox()
                    self._reset_own_inbox_iter()
                    return visited > 0

                visited += 1
                # Sender-owned staging dirs (``.{id}.staging``) contain a
                # ``message.json`` while the message is being assembled; only
                # the final atomic publish is deliverable, so never dispatch or
                # record dot-prefixed entries.
                if entry.name.startswith("."):
                    continue
                self._own_inbox_observed.add(entry.name)
                # _seen only holds handled directory names or pseudo-claim
                # UUIDs, so skip before the stat. Dotted directories (e.g. the
                # ``.dead/`` dead-letter store) are never inbox entries.
                if entry.name in self._seen:
                    skipped_seen += 1
                    continue
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                msg_file = entry / "message.json"
                if msg_file.is_file():
                    # Transient read failures are retried (NOT marked seen),
                    # so a one-cycle OSError on a network filesystem cannot
                    # permanently drop an intact message. Permanent failures
                    # are dead-lettered with a logged error report.
                    try:
                        raw = msg_file.read_text(encoding="utf-8")
                    except UnicodeDecodeError as e:
                        # #1063: corrupt UTF-8 content must not crash the poll
                        # thread; dead-letter it like other permanently bad
                        # inbox entries.
                        self._dead_letter_inbox_entry(entry, f"invalid UTF-8: {e}")
                        continue
                    except OSError as e:
                        n = self._read_failures.get(entry.name, 0) + 1
                        self._read_failures[entry.name] = n
                        if n >= _MAX_READ_RETRIES:
                            self._dead_letter_inbox_entry(
                                entry, f"read failed after {n} attempts: {e}"
                            )
                        else:
                            logger.warning(
                                "mail inbox: transient read failure on %s (attempt %d/%d): %s",
                                entry.name,
                                n,
                                _MAX_READ_RETRIES,
                                e,
                            )
                        continue

                    if not raw.strip():
                        # An empty read means a ``message.json`` caught
                        # mid-write by a non-atomic writer; retry next cycle
                        # instead of dead-lettering a half-written file.
                        continue

                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError as e:
                        # `send()` writes message.json atomically (tmp -> rename),
                        # so a parse error means genuinely malformed content;
                        # dead-letter immediately.
                        self._dead_letter_inbox_entry(entry, f"invalid JSON: {e}")
                        continue

                    self._read_failures.pop(entry.name, None)
                    # Mark seen BEFORE dispatch: a consumer error must not
                    # redeliver (at-most-once), and must not be misdiagnosed
                    # as a transport failure.
                    self._seen.add(entry.name)
                    try:
                        on_message(payload)
                        dispatched += 1
                    except Exception:
                        logger.exception(
                            "mail inbox: on_message handler failed for %s", entry.name
                        )

        except OSError:
            self._reset_own_inbox_iter()
            return visited > 0
        finally:
            self._log_slow_mail_phase(
                "own_inbox_slice",
                start,
                visited=visited,
                skipped_seen=skipped_seen,
                dispatched=dispatched,
                has_more=self._own_inbox_iter is not None,
            )

        return visited > 0

    def _dead_letter_inbox_entry(self, entry: Path, error: str) -> None:
        """Move a bad inbox entry directory into ``inbox/.dead/`` with a report.

        Mail inbox entries are directories (``<uuid>/message.json`` plus
        optional ``attachments/``), so unlike the single-file MCP inbox
        dead-letter path the whole entry directory is moved. An ``error.json``
        report and a warning log line are left behind for operators.
        """
        from datetime import datetime, timezone

        dead_dir = self._inbox_dir / _DEAD_DIRNAME
        try:
            dead_dir.mkdir(parents=True, exist_ok=True)
            target = dead_dir / entry.name
            shutil.move(str(entry), str(target))
            (target / "error.json").write_text(
                json.dumps(
                    {
                        "error": error,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.warning("mail inbox: dead-lettered %s: %s", target, error)
        except OSError as e:
            logger.error(
                "mail inbox: failed to dead-letter %s: %s (original error: %s)",
                entry,
                e,
                error,
            )
        # Either way, stop re-processing this entry.
        self._seen.add(entry.name)
        self._read_failures.pop(entry.name, None)

    def _reset_own_inbox_iter(self) -> None:
        iterator = self._own_inbox_iter
        self._own_inbox_iter = None
        close = getattr(iterator, "close", None)
        if callable(close):
            close()

    def _prune_seen_to_live_inbox(self) -> None:
        """Evict ``_seen`` ids whose inbox directories no longer exist.

        Called only after a complete own-inbox pass (iterator exhausted), so a
        partial scan can never evict live entries. Candidates are ``_seen`` ids
        that were not observed in this pass; each is verified on disk before
        eviction. The existence check matters because the own-inbox pass spans
        many poll ticks: a pseudo-agent claim pre-marks an id in ``_seen``
        before placing the inbox copy, so an id created mid-pass may not be
        yielded by this pass's iterator even though its directory exists —
        evicting it would re-dispatch an already-delivered message.

        Pruning is safe: a missing directory can never be re-dispatched by the
        poll loop, and mailbox ids are timestamp-prefixed so an old id does not
        recur as a new message.
        """
        if not self._seen:
            return
        for name in self._seen - self._own_inbox_observed:
            if not (self._inbox_dir / name).exists():
                self._seen.discard(name)

    def _log_slow_mail_phase(self, phase: str, start: float, **fields: object) -> None:
        elapsed = time.monotonic() - start
        if elapsed < self._mail_poll_slow_seconds:
            return
        redacted_keys = {"message", "body", "content", "subject", "attachments"}
        safe_fields = {key: value for key, value in fields.items() if key not in redacted_keys}
        details = " ".join(f"{key}={value}" for key, value in sorted(safe_fields.items()))
        logger.warning(
            "filesystem mail poll phase slow phase=%s elapsed_ms=%.1f agent=%s %s",
            phase,
            elapsed * 1000,
            self.address,
            details,
        )

    def _poll_pseudo_outbox(
        self,
        pseudo_dir: Path,
        on_message: Callable[[dict], None],
    ) -> None:
        """Claim addressed-to-self messages from a pseudo-agent's outbox.

        For each UUID folder in ``<pseudo_dir>/mailbox/outbox/``, read the
        message, check whether this service's address appears in its ``to``
        field, and if so:

          1. Atomically rename ``<pseudo_dir>/mailbox/outbox/<uuid>/`` to a
             temporary claim directory under ``sent``. Only one poller can win
             this claim.
          2. Pre-mark the UUID in ``_seen`` so the own-inbox scan in this
             tick, and later ticks, won't re-dispatch it after we place a copy
             in own inbox.
          3. Write ``message.json`` atomically into
             ``<self>/mailbox/inbox/<uuid>/``. This makes the wake signal
             truthful: by the time ``on_message`` fires, the message really is
             in this agent's inbox.
          4. Atomically rename the temporary claim directory to
             ``<pseudo_dir>/mailbox/sent/<uuid>/``.
          5. Dispatch via ``on_message(payload)`` unless this is a runtime
             probe handled by the runtime management layer.

        Concurrent pollers racing on the same message: only one can claim the
        outbox directory. Losers skip without writing their own inbox. If the
        winner cannot persist the inbox copy, it restores the claim back to
        outbox for retry.
        """
        outbox_dir = pseudo_dir / self._mailbox_rel / "outbox"
        if not outbox_dir.is_dir():
            return
        sent_parent = pseudo_dir / self._mailbox_rel / "sent"
        sent_parent.mkdir(parents=True, exist_ok=True)

        for entry in outbox_dir.iterdir():
            if not entry.is_dir():
                continue
            msg_file = entry / "message.json"
            if not msg_file.is_file():
                continue
            try:
                payload = json.loads(msg_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue

            # Normalize `to` to a list of strings.
            to_field = payload.get("to")
            if isinstance(to_field, str):
                recipients = [to_field]
            elif isinstance(to_field, list):
                recipients = [str(x) for x in to_field]
            else:
                recipients = []

            if self.address not in recipients:
                continue

            uuid_name = entry.name
            own_inbox_dir = self._inbox_dir / uuid_name
            own_msg_file = own_inbox_dir / "message.json"
            own_tmp_file = own_inbox_dir / "message.json.tmp"
            claim_dir = sent_parent / f".{uuid_name}.claim-{uuid.uuid4()}"
            sent_dir = sent_parent / uuid_name

            try:
                os.replace(str(entry), str(claim_dir))
            except OSError:
                continue

            # Pre-mark BEFORE placing the file so the same poll iteration's
            # own-inbox scan, and later ticks, do not double-dispatch it.
            # Removed on rollback.
            self._seen.add(uuid_name)

            # Step 1: write the payload to own inbox (tmp -> rename).
            try:
                own_inbox_dir.mkdir(parents=True, exist_ok=True)
                own_tmp_file.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                os.replace(str(own_tmp_file), str(own_msg_file))
            except OSError:
                logger.warning(
                    "failed to write claimed pseudo-agent message %s to own inbox",
                    uuid_name,
                    exc_info=True,
                )
                # Leave the message in the pseudo-agent outbox for retry;
                # do not attempt the sent-rename.
                self._seen.discard(uuid_name)
                # Best-effort cleanup of the partial inbox dir.
                _remove_tree_retry(own_inbox_dir)
                _restore_claim_to_outbox(claim_dir, entry)
                continue

            # Step 2: publish the claim by renaming claim -> sent. If this
            # fails, roll back our inbox copy and restore the claim for retry.
            try:
                os.replace(str(claim_dir), str(sent_dir))
            except OSError:
                _remove_tree_retry(own_inbox_dir)
                _restore_claim_to_outbox(claim_dir, entry)
                self._seen.discard(uuid_name)
                continue

            if self._write_runtime_probe_reply(pseudo_dir, payload, uuid_name):
                continue

            # Step 3: best-effort dispatch. If on_message raises, the
            # message is fully persisted (own inbox + sender sent/) and
            # nothing needs to unwind; just log so silent loss of the
            # handler-side effect is observable.
            try:
                on_message(payload)
            except Exception:
                logger.exception(
                    "on_message raised for claimed pseudo-agent message %s from %s",
                    uuid_name,
                    pseudo_dir,
                )

    def _write_runtime_probe_reply(
        self,
        pseudo_dir: Path,
        payload: dict,
        mailbox_id: str,
    ) -> bool:
        """Write a runtime-managed structured ack for explicit probe mail."""
        probe = _runtime_probe_payload(payload)
        if probe is None:
            return False

        correlation_id = _first_nonempty(
            payload.get("correlationId"),
            payload.get("correlation_id"),
            probe.get("correlationId"),
            probe.get("correlation_id"),
        )
        task_id = _first_nonempty(
            payload.get("taskId"),
            payload.get("task_id"),
            probe.get("taskId"),
            probe.get("task_id"),
        )
        if not correlation_id or not task_id:
            logger.warning(
                "runtime probe %s from %s missing correlationId/taskId",
                mailbox_id,
                pseudo_dir,
            )
            return True

        from datetime import datetime, timezone

        ack_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        in_reply_to = payload.get("_mailbox_id") or payload.get("id") or mailbox_id
        structured = {
            "type": "runtime_probe_ack",
            "status": "ok",
            "correlationId": correlation_id,
            "taskId": task_id,
            "inReplyTo": in_reply_to,
            "agent": self.address,
            "runtime": "filesystem-mail-service",
        }
        ack = {
            "id": ack_id,
            "_mailbox_id": ack_id,
            "from": self.address,
            "to": [str(payload.get("from") or pseudo_dir.name)],
            "subject": "Runtime probe ack",
            "message": json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
            "type": "runtime_probe_ack",
            "correlationId": correlation_id,
            "taskId": task_id,
            "in_reply_to": in_reply_to,
            "received_at": now,
            "attachments": [],
            "identity": _safe_manifest(self._working_dir),
            "structured": structured,
        }

        inbox_dir = pseudo_dir / self._mailbox_rel / "inbox" / ack_id
        tmp_file = inbox_dir / "message.json.tmp"
        final_file = inbox_dir / "message.json"
        try:
            inbox_dir.mkdir(parents=True, exist_ok=True)
            tmp_file.write_text(
                json.dumps(ack, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(str(tmp_file), str(final_file))
        except OSError:
            logger.exception(
                "failed to write runtime probe ack %s to %s",
                ack_id,
                pseudo_dir,
            )
        return True

    def stop(self) -> None:
        """Stop the polling thread."""
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=3.0)
        self._poll_thread = None
        self._reset_own_inbox_iter()


def _unique_attachment_names(srcs: list[Path]) -> list[str]:
    """Map each source attachment to a unique recipient-local file name.

    Basenames are preserved when unique. On a collision, a ``-1``, ``-2``,
    ... counter is inserted before the suffix (``a.txt`` -> ``a-1.txt``) so
    every accepted attachment keeps its own physical file and payload path
    instead of silently overwriting an earlier one (#1354).
    """
    used: set[str] = set()
    names: list[str] = []
    for src in srcs:
        candidate = src.name
        if candidate not in used:
            used.add(candidate)
            names.append(candidate)
            continue
        stem, suffix = src.stem, src.suffix
        i = 1
        while True:
            candidate = f"{stem}-{i}{suffix}"
            if candidate not in used:
                break
            i += 1
        used.add(candidate)
        names.append(candidate)
    return names


def _runtime_probe_payload(payload: dict) -> dict | None:
    if payload.get("type") in {"runtime_probe", "runtime.probe"}:
        return payload

    message = payload.get("message")
    if not isinstance(message, str):
        return None
    try:
        decoded = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    if decoded.get("type") in {"runtime_probe", "runtime.probe"}:
        return decoded
    return None


def _first_nonempty(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _safe_manifest(agent_dir: Path) -> dict:
    try:
        data = json.loads((agent_dir / ".agent.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"address": agent_dir.name}
    return {
        key: data.get(key)
        for key in ("agent_id", "agent_name", "nickname", "address", "state", "admin")
        if key in data
    }


def _remove_tree_retry(path: Path) -> None:
    for _ in range(5):
        shutil.rmtree(str(path), ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.05)


def _restore_claim_to_outbox(claim_dir: Path, outbox_dir: Path) -> None:
    if not claim_dir.exists() or outbox_dir.exists():
        return
    try:
        os.replace(str(claim_dir), str(outbox_dir))
    except OSError:
        logger.exception("failed to restore claimed pseudo-agent message %s", claim_dir)


__all__ = ["PosixFilesystemMailAdapter"]
