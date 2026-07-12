"""POSIX filesystem mail transport adapter.

``PosixFilesystemMailAdapter`` implements the Core-owned
``lingtai.kernel.mail_transport.MailTransportPort`` by delivering messages as
files written directly into a recipient's inbox directory and by polling its own
inbox (plus subscribed pseudo-agent outboxes) on a fixed cadence. It is the only
production transport adapter; Core never constructs it.

The concrete addressing (working-directory basename addresses, peer/abs
resolution), storage layout (``mailbox/{inbox,outbox,sent}/<id>/message.json``
plus ``attachments/``), atomic writes, handshake-before-delivery, optimistic
pseudo-outbox claim/rollback, and 0.5-second polling all live here — they are
the POSIX filesystem mechanism, not part of the technology-neutral Port.
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
from typing import Callable

from lingtai.kernel.handshake import is_agent, is_alive, manifest, resolve_address
from lingtai.kernel.mail_transport import MailTransportPort
from lingtai.kernel.services.mail import _new_mailbox_id

logger = logging.getLogger(__name__)


class PosixFilesystemMailAdapter(MailTransportPort):
    """Filesystem-based mail delivery.

    Delivers messages by writing files directly to the recipient's inbox
    directory.  Monitors its own inbox via polling.

    Address = working directory name (relative basename).  Example::

        svc = PosixFilesystemMailAdapter(Path("/agents/abc123"))
        svc.listen(on_message=lambda msg: print(msg))  # poll own inbox
        svc.send("def456", {"message": "hello"})  # write to sibling agent
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
        *,
        mode: str = "peer",
    ) -> str | None:
        """Deliver *message* to the agent at *address*.

        Handshake:
        1. ``{address}/.agent.json`` must exist.
        2. ``{address}/.agent.heartbeat`` must be fresh (< 2 s).

        Then write ``message.json`` atomically into the recipient's inbox
        and copy any attachment files.

        Modes:
        - peer: resolve bare name against parent dir (default — sibling agents in same .lingtai/)
        - abs: use address as a literal absolute path (cross-network, same machine)
        """
        base_dir = self._working_dir.parent  # .lingtai/ directory
        if mode == "abs":
            recipient_dir = Path(address)
        else:
            recipient_dir = resolve_address(address, base_dir)

        # --- handshake ------------------------------------------------
        if not is_agent(recipient_dir):
            return f"No agent at {address}"

        if not is_alive(recipient_dir):
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

        # Handle attachments
        attachment_paths = message.get("attachments")
        if attachment_paths:
            att_dir = msg_dir / "attachments"
            att_dir.mkdir(parents=True, exist_ok=True)
            local_copies: list[str] = []
            for fpath in attachment_paths:
                src = Path(fpath)
                if not src.is_file():
                    return f"Attachment not found: {fpath}"
                dst = att_dir / src.name
                shutil.copy2(src, dst)
                local_copies.append(str(dst))
            # Replace original paths with recipient-local paths
            message = {**message, "attachments": local_copies}
        else:
            msg_dir.mkdir(parents=True, exist_ok=True)

        # Atomic write: tmp → rename
        tmp_path = msg_dir / "message.json.tmp"
        final_path = msg_dir / "message.json"
        try:
            tmp_path.write_text(
                json.dumps(message, indent=2, ensure_ascii=False, default=str)
            )
            os.replace(str(tmp_path), str(final_path))
        except OSError as e:
            return f"Failed to write message: {e}"

        return None

    # ------------------------------------------------------------------
    # listen / stop
    # ------------------------------------------------------------------

    def listen(self, on_message: Callable[[dict], None]) -> None:
        """Start polling the inbox for new messages.

        Existing messages are recorded in ``_seen`` so they are not
        re-delivered.  New directories that appear with a ``message.json``
        trigger *on_message*.
        """
        # Snapshot existing inbox entries so we don't re-notify
        if self._inbox_dir.is_dir():
            for entry in self._inbox_dir.iterdir():
                if entry.is_dir():
                    self._seen.add(entry.name)

        self._poll_stop.clear()

        def _poll_loop() -> None:
            while not self._poll_stop.is_set():
                # Phase 1 — subscribed pseudo-agent outboxes. Poll these
                # before historical own-inbox scans so urgent human/TUI
                # wake mail cannot starve behind old inbox entries. Isolated
                # from Phase 2 so a persistent OSError here cannot skip the
                # same tick's own-inbox scan.
                try:
                    for pseudo_dir in self._pseudo_agent_dirs:
                        self._poll_pseudo_outbox(pseudo_dir, on_message)
                except OSError:
                    pass

                # Phase 2 — own inbox.
                try:
                    if self._inbox_dir.is_dir():
                        for entry in self._inbox_dir.iterdir():
                            # _seen only holds handled directory names or
                            # pseudo-claim UUIDs, so skip before the stat.
                            if entry.name in self._seen:
                                continue
                            if not entry.is_dir():
                                continue
                            msg_file = entry / "message.json"
                            if msg_file.is_file():
                                try:
                                    payload = json.loads(msg_file.read_text(encoding="utf-8"))
                                    on_message(payload)
                                except (json.JSONDecodeError, OSError):
                                    pass
                                self._seen.add(entry.name)
                except OSError:
                    pass
                self._poll_stop.wait(0.5)

        self._poll_thread = threading.Thread(target=_poll_loop, daemon=True)
        self._poll_thread.start()

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
            except (json.JSONDecodeError, OSError):
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
                    json.dumps(payload, indent=2, ensure_ascii=False, default=str)
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
