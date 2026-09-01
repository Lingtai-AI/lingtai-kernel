"""Telegram owner adapter for the channel_reply Core Port."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lingtai.adapters.channel_reply_state_lock import select_channel_reply_state_lock
from lingtai.kernel.channel_reply import (
    ChannelReplyFileStore,
    ChannelReplyOwnerFileTransport,
    OwnerChannelReplyController,
    OwnerReplyGrant,
    PreSendChannelReplyError,
)
from lingtai.kernel.channel_reply._mutation_lock import ChannelReplyExpectedRootMismatch


class TelegramChannelReplyAdapter(OwnerChannelReplyController):
    """Owner-bound Telegram adapter.

    The target supplies only a Core `ChannelReplySubmitRequest`. Account alias,
    chat id, and reply anchor are read from `OwnerReplyGrant.anchor`.
    """

    def __init__(
        self,
        *,
        state_root: str | Path,
        service,
        target_agent_id: str,
        now=None,
        target_agent_name: str | None = None,
        validate_target_eligibility: Callable[[], bool] | None = None,
    ) -> None:
        self._service = service
        self._validate_target_eligibility = validate_target_eligibility
        # Bounded target retention must preserve Core's record/surface cursors
        # across scheduled cycles.  The router keeps one adapter per exact
        # eligible-target pin, so this cache cannot cross a target identity
        # replacement.  Whole-root disappearance evicts the corresponding
        # transport below.
        self._target_cleanup_transports: dict[Path, ChannelReplyOwnerFileTransport] = {}
        super().__init__(
            store=ChannelReplyFileStore(
                Path(state_root),
                mutation_lock=select_channel_reply_state_lock(),
            ),
            target_agent_id=target_agent_id,
            target_agent_name=target_agent_name,
            validate_pre_send=self._validate_plain_text_reply,
            send_plain_text_reply=self._send_plain_text_reply,
            now=now,
        )

    def target_file_transport(
        self,
        target_workdir: str | Path,
        *,
        recover_on_init: bool = True,
    ) -> ChannelReplyOwnerFileTransport:
        """Compose PR1's owner drainer for one already-authorized target."""
        return ChannelReplyOwnerFileTransport(
            target_workdir,
            submit_port=self,
            mutation_lock=select_channel_reply_state_lock(),
            now=self._now,
            recover_on_init=recover_on_init,
        )

    def drain_target_outbox(
        self,
        target_workdir: str | Path,
        *,
        max_items: int = 100,
    ):
        """Drain an initialized target outbox without creating target state.

        A newly eligible target has no ``.channel_reply`` tree until it submits
        its first reply. That whole-root absence is ordinary no-work. Once the
        root exists, Core remains the sole validator: a symlink, wrong type,
        partial tree, wrong mode, or wrong owner must still fail closed.
        """
        reply_root = Path(target_workdir) / ".channel_reply"
        if self._reply_root_is_absent(reply_root):
            return []
        try:
            return self.target_file_transport(target_workdir).drain(
                max_items=max_items
            )
        except FileNotFoundError:
            # The root may disappear between the lstat and Core construction.
            # Suppress only that complete-root absence; a missing child under a
            # still-existing root remains a strict malformed-state failure.
            if self._reply_root_is_absent(reply_root):
                return []
            raise

    def cleanup_target_state(
        self,
        target_workdir: str | Path,
        *,
        now: str,
        retention_seconds: int,
        max_records: int | None = None,
    ) -> int:
        """Run Core target retention without creating an absent target root."""
        target_path = Path(target_workdir)
        reply_root = target_path / ".channel_reply"
        if self._reply_root_is_absent(reply_root):
            self._target_cleanup_transports.pop(target_path, None)
            return 0
        try:
            if max_records is None:
                transport = self.target_file_transport(target_path)
            else:
                transport = self._target_cleanup_transports.get(target_path)
                if transport is None:
                    transport = self.target_file_transport(
                        target_path, recover_on_init=False
                    )
                    self._target_cleanup_transports[target_path] = transport
            return transport.cleanup_retained(
                now=now,
                retention_seconds=retention_seconds,
                max_records=max_records,
            )
        except ChannelReplyExpectedRootMismatch:
            # Evict only after the pinned transport has positively rejected the
            # named root identity. The failed cycle never opens or trusts the
            # replacement through a newly constructed transport; a later cycle may.
            if max_records is not None:
                self._target_cleanup_transports.pop(target_path, None)
            raise
        except FileNotFoundError:
            if self._reply_root_is_absent(reply_root):
                self._target_cleanup_transports.pop(target_path, None)
                return 0
            raise

    @staticmethod
    def _reply_root_is_absent(reply_root: Path) -> bool:
        try:
            reply_root.lstat()
        except FileNotFoundError:
            return True
        return False

    def _validate_plain_text_reply(self, grant: OwnerReplyGrant, _text: str) -> None:
        """Reject malformed authority or an obsolete target before sending."""
        if grant.channel != "telegram":
            raise PreSendChannelReplyError("unsupported_channel")
        anchor = dict(grant.anchor)
        if set(anchor) != {"account_alias", "chat_id", "reply_to_message_id"}:
            raise PreSendChannelReplyError("bad_anchor")
        account_alias = anchor.get("account_alias")
        chat_id = anchor.get("chat_id")
        reply_to_message_id = anchor.get("reply_to_message_id")
        if (
            not isinstance(account_alias, str)
            or not account_alias
            or account_alias != account_alias.strip()
        ):
            raise PreSendChannelReplyError("bad_anchor")
        # Simple V1 accepts only private user chats. Telegram private chat ids are
        # positive integers; group/supergroup/channel ids occupy the negative
        # domain. Booleans are not identifiers despite being int subclasses.
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id <= 0:
            raise PreSendChannelReplyError("bad_anchor")
        if (
            isinstance(reply_to_message_id, bool)
            or not isinstance(reply_to_message_id, int)
            or reply_to_message_id <= 0
        ):
            raise PreSendChannelReplyError("bad_anchor")
        if self._validate_target_eligibility is not None:
            try:
                eligible = self._validate_target_eligibility()
            except Exception:
                eligible = False
            if eligible is not True:
                raise PreSendChannelReplyError("target_ineligible")

    def _send_plain_text_reply(self, grant: OwnerReplyGrant, text: str) -> str:
        # Repeat the pure check defensively at the concrete account boundary.
        self._validate_plain_text_reply(grant, text)
        anchor = dict(grant.anchor)
        account_alias = str(anchor["account_alias"])
        chat_id = int(anchor["chat_id"])
        reply_to_message_id = int(anchor["reply_to_message_id"])
        account = self._service.get_account(account_alias)
        result: dict[str, Any] = account.send_message(
            chat_id,
            f"[{grant.target_agent_name}] {text}",
            reply_to_message_id=reply_to_message_id,
        )
        message_id = result.get("message_id")
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            raise RuntimeError("bad_send_result")
        return f"owner-private-telegram-message:{message_id}"
