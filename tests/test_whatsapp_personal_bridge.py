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


class TestManager:
    def test_inbound_persists_and_context(self, tmp_path: Path):
        manager = WhatsAppManager({"store_dir": str(tmp_path / "store")}, working_dir=tmp_path)
        manager._handle_incoming({
            "id": "msg-1", "from": "15551234567@c.us", "body": "hello",
            "type": "text", "timestamp": 1700000000,
        })
        msgs = manager._iter_messages("15551234567@c.us", direction="inbox")
        assert len(msgs) == 1
        assert msgs[0]["body"] == "hello"
        ctx = manager._conversation_context("15551234567@c.us", msgs[0])
        assert ctx["platform"] == "whatsapp"
        assert ctx["conversation_ref"] == "whatsapp:15551234567@c.us"
        assert ctx["latest_incoming"]["text"] == "hello"

    def test_action_unknown_raises(self, tmp_path: Path):
        manager = WhatsAppManager({}, working_dir=tmp_path)
        with pytest.raises(ValueError):
            manager.action("nope")
