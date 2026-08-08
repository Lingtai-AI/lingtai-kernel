"""Tests for the personal-account WhatsApp MCP (bridge protocol + manager)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from lingtai.mcp_servers.whatsapp.client import WhatsAppBridge
from lingtai.mcp_servers.whatsapp.manager import WhatsAppManager


def _fake_bridge_script(tmp_path: Path, script: str) -> Path:
    """Write a fake Node bridge that speaks the line protocol."""
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    (bridge_dir / "index.js").write_text(script, encoding="utf-8")
    return bridge_dir


PING_SCRIPT = """
const readline = require('readline');
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  const req = JSON.parse(line);
  if (req.method === 'ping') process.stdout.write(JSON.stringify({id: req.id, result: {pong: true}}) + '\\n');
  else process.stdout.write(JSON.stringify({id: req.id, error: 'unknown'}) + '\\n');
});
"""


class TestBridgeProtocol:
    def test_ping_roundtrip(self, tmp_path: Path):
        bridge_dir = _fake_bridge_script(tmp_path, PING_SCRIPT)
        bridge = WhatsAppBridge(node_path="node", bridge_dir=bridge_dir)
        result = bridge.request("ping")
        assert result == {"pong": True}
        bridge.stop()

    def test_event_dispatch(self, tmp_path: Path):
        script = """
const readline = require('readline');
const rl = readline.createInterface({ input: process.stdin });
process.stdout.write(JSON.stringify({type: 'qr', data: {qr_base64: 'data:image/png;base64,AAAA'}}) + '\\n');
process.stdout.write(JSON.stringify({type: 'ready', data: {me: '15551234567@c.us'}}) + '\\n');
rl.on('line', (line) => {
  const req = JSON.parse(line);
  if (req.method === 'status') process.stdout.write(JSON.stringify({id: req.id, result: {ready: true, me: '15551234567@c.us'}}) + '\\n');
});
"""
        bridge_dir = _fake_bridge_script(tmp_path, script)
        events = []
        bridge = WhatsAppBridge(node_path="node", bridge_dir=bridge_dir, on_event=lambda e: events.append(e))
        bridge.start()
        import time
        deadline = time.time() + 5
        while len(events) < 2 and time.time() < deadline:
            time.sleep(0.1)
        assert [e.get("type") for e in events] == ["qr", "ready"]
        bridge.stop()

    def test_timeout_on_silent_bridge(self, tmp_path: Path):
        script = """
const readline = require('readline');
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', () => {});
"""
        bridge_dir = _fake_bridge_script(tmp_path, script)
        bridge = WhatsAppBridge(node_path="node", bridge_dir=bridge_dir)
        with pytest.raises(Exception, match="timed out"):
            bridge.request("ping", timeout=1)
        bridge.stop()


    def test_pending_request_fails_fast_when_the_bridge_dies(self, tmp_path: Path):
        """A dead bridge must not make callers wait out the full timeout."""
        script = """
const readline = require('readline');
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', () => { process.exit(3); });
"""
        bridge_dir = _fake_bridge_script(tmp_path, script)
        bridge = WhatsAppBridge(node_path="node", bridge_dir=bridge_dir)
        import time
        started = time.time()
        with pytest.raises(Exception, match="bridge exited"):
            bridge.request("ping", timeout=30)
        assert time.time() - started < 20
        bridge.stop()

    def test_falsy_result_is_not_coerced_to_an_empty_dict(self, tmp_path: Path):
        script = """
const readline = require('readline');
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  const req = JSON.parse(line);
  process.stdout.write(JSON.stringify({id: req.id, result: []}) + '\\n');
});
"""
        bridge_dir = _fake_bridge_script(tmp_path, script)
        bridge = WhatsAppBridge(node_path="node", bridge_dir=bridge_dir)
        assert bridge.request("read", timeout=10) == []
        bridge.stop()


def _manager(tmp_path: Path, **config) -> WhatsAppManager:
    # autostart=False keeps the test hermetic: no Node bridge subprocess.
    cfg = {"store_dir": str(tmp_path / "store"), "autostart": False}
    cfg.update(config)
    return WhatsAppManager(cfg, working_dir=tmp_path)


class _FakeBridge:
    """Stand-in for WhatsAppBridge that records requests."""

    alive = True

    def __init__(self, result: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = result or {"id": "out-1", "wa_id": "15551234567@c.us"}

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self.calls.append((method, dict(params or {})))
        return self.result


class TestManager:
    def test_inbound_persists_and_context(self, tmp_path: Path):
        manager = _manager(tmp_path)
        manager._handle_incoming({
            "id": "msg-1", "from": "15551234567@c.us", "body": "hello",
            # whatsapp-web.js emits 'chat' for plain text, never 'text'.
            "type": "chat", "timestamp": 1700000000,
        })
        msgs = manager._iter_messages("15551234567@c.us", direction="inbox")
        assert len(msgs) == 1
        assert msgs[0]["body"] == "hello"
        ctx = manager._conversation_context("15551234567@c.us", msgs[0])
        assert ctx["platform"] == "whatsapp"
        assert ctx["conversation_ref"] == "whatsapp:15551234567@c.us"
        assert ctx["latest_incoming"]["text"] == "hello"

    def test_action_unknown_raises(self, tmp_path: Path):
        manager = _manager(tmp_path)
        with pytest.raises(ValueError):
            manager.action("nope")

    def test_handle_flattens_the_family_envelope(self, tmp_path: Path):
        manager = _manager(tmp_path)
        result = manager.handle({"action": "manual"})
        assert result["status"] == "ok"
        assert result["skill"] == "whatsapp-mcp-manual"

    def test_non_dict_event_data_is_dropped_cleanly(self, tmp_path: Path):
        manager = _manager(tmp_path)
        for data in ("not-a-dict", ["x"], 7):
            manager._on_bridge_event({"type": "message", "data": data})
        assert not list((tmp_path / "store").rglob("*.json"))

    def test_send_stores_the_outgoing_message(self, tmp_path: Path):
        manager = _manager(tmp_path)
        manager.bridge = _FakeBridge()
        manager.action("send", {"to": "15551234567", "text": "hi there"})
        assert manager.bridge.calls[0][0] == "send"
        stored = manager._iter_messages("15551234567@c.us", direction="sent")
        assert [m["body"] for m in stored] == ["hi there"]

    def test_reply_uses_an_explicit_recipient(self, tmp_path: Path):
        manager = _manager(tmp_path)
        manager.bridge = _FakeBridge()
        manager.action("reply", {
            "message_id": "msg-1", "to": "15551234567@c.us", "text": "answering",
        })
        method, params = manager.bridge.calls[0]
        assert method == "reply"
        assert params["to"] == "15551234567@c.us"
        assert params["message_id"] == "msg-1"

    def test_reply_recovers_the_recipient_from_the_store(self, tmp_path: Path):
        """B5: reply without `to` must find the quoted message in the store."""
        manager = _manager(tmp_path)
        manager._handle_incoming({
            "id": "msg-1", "from": "15551234567@c.us", "body": "ping",
            "type": "chat", "timestamp": 1700000000,
        })
        manager.bridge = _FakeBridge()
        manager.action("reply", {"message_id": "msg-1", "text": "pong"})
        _method, params = manager.bridge.calls[0]
        assert params["to"] == "15551234567@c.us"

    def test_reply_to_an_unknown_message_fails_loudly(self, tmp_path: Path):
        manager = _manager(tmp_path)
        manager.bridge = _FakeBridge()
        with pytest.raises(ValueError, match="reply requires to or wa_id"):
            manager.action("reply", {"message_id": "never-seen", "text": "hi"})
        assert manager.bridge.calls == []
