"""Tests for MailService and PosixFilesystemMailAdapter."""
import json
import threading
import time
from pathlib import Path

import pytest

from lingtai.adapters.posix.mail import PosixFilesystemMailAdapter


def _setup_agent_dir(path: Path) -> Path:
    """Create a minimal agent directory with manifest and heartbeat."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".agent.json").write_text(json.dumps({
        "agent_id": path.name,
        "agent_name": path.name,
        "admin": {},
    }))
    (path / ".agent.heartbeat").write_text(str(time.time()))
    return path


def _keep_heartbeat_alive(path: Path, stop_event: threading.Event) -> threading.Thread:
    """Start a background thread that keeps the heartbeat fresh."""
    hb = path / ".agent.heartbeat"
    def loop():
        while not stop_event.is_set():
            try:
                hb.write_text(str(time.time()))
            except OSError:
                pass
            stop_event.wait(0.5)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


class TestPosixFilesystemMailAdapter:
    def test_send_to_listener(self, tmp_path):
        """Test basic send/receive via filesystem."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")

        received = []
        event = threading.Event()
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        def on_message(msg):
            received.append(msg)
            event.set()

        listener = PosixFilesystemMailAdapter(working_dir=receiver_dir)
        listener.listen(on_message)

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            result = sender.send(
                str(receiver_dir),
                {"from": str(sender_dir), "to": str(receiver_dir), "message": "hello"},
            )
            assert result is None

            assert event.wait(timeout=5.0), "Message not received within timeout"
            assert len(received) == 1
            assert received[0]["message"] == "hello"
        finally:
            listener.stop()
            stop.set()

    def test_send_to_nonexistent_returns_error(self, tmp_path):
        """Sending to a non-existent agent directory should return an error string."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
        result = sender.send(str(tmp_path / "nonexistent"), {"message": "hello"})
        assert isinstance(result, str)
        assert "No agent" in result

    def test_send_to_stopped_agent_returns_error(self, tmp_path):
        """Sending to an agent with stale heartbeat should return an error."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")
        # Write a stale heartbeat (10 seconds old)
        (receiver_dir / ".agent.heartbeat").write_text(str(time.time() - 10))

        sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
        result = sender.send(str(receiver_dir), {"message": "hello"})
        assert isinstance(result, str)
        assert "not running" in result

    def test_address_property(self, tmp_path):
        """Address should be the working directory name (relative basename)."""
        agent_dir = _setup_agent_dir(tmp_path / "agent")
        svc = PosixFilesystemMailAdapter(working_dir=agent_dir)
        assert svc.address == agent_dir.name

    def test_stop_is_idempotent(self, tmp_path):
        """Calling stop multiple times should not raise."""
        agent_dir = _setup_agent_dir(tmp_path / "agent")
        svc = PosixFilesystemMailAdapter(working_dir=agent_dir)
        svc.stop()
        svc.stop()

    def test_multiple_messages(self, tmp_path):
        """Multiple messages should all be received."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")

        received = []
        all_done = threading.Event()
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        def on_message(msg):
            received.append(msg)
            if len(received) >= 3:
                all_done.set()

        listener = PosixFilesystemMailAdapter(working_dir=receiver_dir)
        listener.listen(on_message)

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            for i in range(3):
                sender.send(str(receiver_dir), {"message": f"msg-{i}"})
                time.sleep(0.05)

            assert all_done.wait(timeout=5.0), f"Only received {len(received)} of 3 messages"
            messages = [r["message"] for r in received]
            assert "msg-0" in messages
            assert "msg-1" in messages
            assert "msg-2" in messages
        finally:
            listener.stop()
            stop.set()


class TestMailAttachments:
    def test_send_with_attachment(self, tmp_path):
        """Sender copies attachment files into the recipient's inbox."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")

        received = []
        event = threading.Event()
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        def on_message(msg):
            received.append(msg)
            event.set()

        # Create a file to attach
        attachment = sender_dir / "image.png"
        attachment.write_bytes(b"\x89PNG_TEST_DATA")

        listener = PosixFilesystemMailAdapter(working_dir=receiver_dir)
        listener.listen(on_message)

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            result = sender.send(
                str(receiver_dir),
                {
                    "from": "sender",
                    "to": str(receiver_dir),
                    "message": "here is an image",
                    "attachments": [str(attachment)],
                },
            )
            assert result is None
            assert event.wait(timeout=5.0)
            msg = received[0]
            # Receiver should get local file paths
            assert "attachments" in msg
            assert len(msg["attachments"]) == 1
            recv_path = Path(msg["attachments"][0])
            assert recv_path.exists()
            assert recv_path.read_bytes() == b"\x89PNG_TEST_DATA"
            assert "mailbox" in str(recv_path)
        finally:
            listener.stop()
            stop.set()


    def test_attachment_file_not_found(self, tmp_path):
        """send() returns error when attachment file does not exist."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            result = sender.send(
                str(receiver_dir),
                {"from": "s", "to": "r", "message": "hi", "attachments": ["/nonexistent/file.png"]},
            )
            assert isinstance(result, str)
            assert "Attachment not found" in result
            # A failed send must not leave an orphaned partial message
            # directory in the recipient's inbox.
            inbox = receiver_dir / "mailbox" / "inbox"
            assert not inbox.is_dir() or not list(inbox.iterdir())
        finally:
            stop.set()

    def test_mailbox_directory_structure(self, tmp_path):
        """Received messages are saved in mailbox/<uuid>/message.json + attachments/."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")

        received = []
        event = threading.Event()
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        def on_message(msg):
            received.append(msg)
            event.set()

        attachment = sender_dir / "song.mp3"
        attachment.write_bytes(b"MP3_DATA")

        listener = PosixFilesystemMailAdapter(working_dir=receiver_dir)
        listener.listen(on_message)

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            sender.send(
                str(receiver_dir),
                {"from": "s", "to": "r", "message": "music", "attachments": [str(attachment)]},
            )
            assert event.wait(timeout=5.0)

            # Check mailbox structure
            mailbox = receiver_dir / "mailbox" / "inbox"
            assert mailbox.is_dir()
            msg_dirs = list(mailbox.iterdir())
            assert len(msg_dirs) == 1
            msg_dir = msg_dirs[0]
            assert (msg_dir / "message.json").is_file()
            assert (msg_dir / "attachments" / "song.mp3").is_file()
            assert (msg_dir / "attachments" / "song.mp3").read_bytes() == b"MP3_DATA"
        finally:
            listener.stop()
            stop.set()

    def test_message_without_attachments_also_persisted(self, tmp_path):
        """Even messages without attachments get persisted to mailbox."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")

        received = []
        event = threading.Event()
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        def on_message(msg):
            received.append(msg)
            event.set()

        listener = PosixFilesystemMailAdapter(working_dir=receiver_dir)
        listener.listen(on_message)

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            sender.send(str(receiver_dir), {"from": "s", "to": "r", "message": "plain"})
            assert event.wait(timeout=5.0)

            mailbox = receiver_dir / "mailbox" / "inbox"
            assert mailbox.is_dir()
            msg_dirs = list(mailbox.iterdir())
            assert len(msg_dirs) == 1
            assert (msg_dirs[0] / "message.json").is_file()
        finally:
            listener.stop()
            stop.set()


    def test_partial_attachment_failure_leaves_no_inbox_orphan(self, tmp_path):
        """When the second of two attachments is missing, nothing remains."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        good = sender_dir / "good.txt"
        good.write_text("hello")

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            result = sender.send(
                str(receiver_dir),
                {"message": "hi", "attachments": [str(good), "/nonexistent/bad.txt"]},
            )
            assert isinstance(result, str)
            assert "Attachment not found" in result
            # No partial copy of the first attachment may exist anywhere
            # under the recipient's mailbox.
            assert not list((receiver_dir / "mailbox").rglob("good.txt"))
            inbox = receiver_dir / "mailbox" / "inbox"
            assert not inbox.is_dir() or not list(inbox.iterdir())
        finally:
            stop.set()

    def test_attachment_copy_error_returns_error_not_raise(self, tmp_path, monkeypatch):
        """A copy2 OSError returns an error string and leaves the inbox clean."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        attachment = sender_dir / "a.txt"
        attachment.write_text("data")

        def _boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr("lingtai.adapters.posix.mail.shutil.copy2", _boom)

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            result = sender.send(
                str(receiver_dir),
                {"message": "hi", "attachments": [str(attachment)]},
            )
            assert isinstance(result, str)
            assert "Failed to write message" in result
            inbox = receiver_dir / "mailbox" / "inbox"
            assert not inbox.is_dir() or not list(inbox.iterdir())
        finally:
            stop.set()

    def test_delivered_attachment_paths_point_at_final_location(self, tmp_path):
        """Delivered message.json attachment paths must be final inbox paths."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        attachment = sender_dir / "a.txt"
        attachment.write_text("data")

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            result = sender.send(
                str(receiver_dir),
                {"message": "hi", "attachments": [str(attachment)]},
            )
            assert result is None

            inbox = receiver_dir / "mailbox" / "inbox"
            msg_dirs = list(inbox.iterdir())
            assert len(msg_dirs) == 1
            msg_dir = msg_dirs[0]
            payload = json.loads((msg_dir / "message.json").read_text(encoding="utf-8"))
            delivered = [Path(p) for p in payload["attachments"]]
            assert len(delivered) == 1
            final_path = msg_dir / "attachments" / "a.txt"
            assert delivered[0] == final_path
            assert delivered[0].is_file()
            assert delivered[0].read_text(encoding="utf-8") == "data"
            # No sender-owned staging leftovers.
            assert not any(e.name.startswith(".") for e in inbox.iterdir())
        finally:
            stop.set()

    def test_listener_never_dispatches_partial_message(self, tmp_path):
        """A failing send followed by a successful send dispatches exactly once."""
        sender_dir = _setup_agent_dir(tmp_path / "sender")
        receiver_dir = _setup_agent_dir(tmp_path / "receiver")

        received = []
        event = threading.Event()
        stop = threading.Event()
        _keep_heartbeat_alive(receiver_dir, stop)

        def on_message(msg):
            received.append(msg)
            event.set()

        listener = PosixFilesystemMailAdapter(working_dir=receiver_dir)
        listener.listen(on_message)

        try:
            sender = PosixFilesystemMailAdapter(working_dir=sender_dir)
            result = sender.send(
                str(receiver_dir),
                {"message": "bad", "attachments": ["/nonexistent/bad.png"]},
            )
            assert isinstance(result, str)
            result = sender.send(str(receiver_dir), {"message": "good"})
            assert result is None

            assert event.wait(timeout=5.0), "Successful message not received"
            time.sleep(1.0)  # give any wrong extra dispatch time to surface
            assert len(received) == 1
            assert received[0]["message"] == "good"

            # The failed send left nothing behind: exactly one delivered
            # message directory and no staging leftovers.
            inbox = receiver_dir / "mailbox" / "inbox"
            assert not any(e.name.startswith(".") for e in inbox.iterdir())
            assert len(list(inbox.iterdir())) == 1
        finally:
            listener.stop()
            stop.set()
