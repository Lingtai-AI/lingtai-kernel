"""Manager for the LingTai WhatsApp MCP (personal-account mode).

The manager owns the bridge lifecycle, message persistence, LICC inbound
notification, and the validated tool dispatch. The Meta Cloud API surface
(accounts/webhook/templates) is gone; this module talks to the local Node
bridge (whatsapp-web.js) over the newline-JSON protocol.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import client as bridge_client
from . import redaction
from .licc import push_inbox_event


log = logging.getLogger(__name__)

from lingtai.mcp_servers import _skill  # noqa: E402
from ._family import WHATSAPP_SCHEMA  # noqa: E402

_SKILL_NAME = "whatsapp-mcp-manual"
_SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH = _skill.load_skill(__package__)

SCHEMA = WHATSAPP_SCHEMA

DESCRIPTION = (
    "WhatsApp MCP: connect a personal WhatsApp account via a local "
    "whatsapp-web.js bridge (QR-code pairing). Send, read, search, "
    "reply, react, manage contacts, and receive inbound messages "
    "pushed into the agent inbox."
)

DEFAULT_STORE = "whatsapp"


class WhatsAppManager:
    """Personal-account WhatsApp manager.

    ``config`` is a dict with optional keys:
      - node_path: Node executable (default: node on PATH)
      - bridge_dir: path to the Node bridge directory (default: bundled)
      - session_dir: path to store the whatsapp-web.js session (default: <workdir>/.wwebjs_auth)
      - store_dir: path to store message/contact metadata (default: <workdir>/whatsapp)
      - allowed_users: optional allowlist of wa_ids / @c.us ids for inbound push
    """

    def __init__(self, config: dict[str, Any] | None = None, working_dir: str | Path | None = None) -> None:
        self.config = dict(config or {})
        self.working_dir = Path(working_dir or os.environ.get("LINGTAI_AGENT_DIR", os.getcwd()))
        self.store_dir = Path(self.config.get("store_dir") or self.working_dir / DEFAULT_STORE)
        self.session_dir = Path(self.config.get("session_dir") or self.working_dir / ".wwebjs_auth")
        self.allowed_users: set[str] = set(self.config.get("allowed_users") or [])
        self.contacts_path = self.store_dir / "contacts.json"
        self._contacts: dict[str, dict[str, Any]] = {}
        self._load_contacts()
        self.bridge = bridge_client.WhatsAppBridge(
            node_path=self.config.get("node_path"),
            bridge_dir=self.config.get("bridge_dir"),
            session_dir=self.session_dir,
            on_event=self._on_bridge_event,
        )
        self._identity_written = False

    # ------------------------------------------------------------------ helpers

    def _load_contacts(self) -> None:
        if self.contacts_path.is_file():
            try:
                self._contacts = json.loads(self.contacts_path.read_text(encoding="utf-8"))
            except Exception:
                self._contacts = {}

    def _save_contacts(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.contacts_path.write_text(json.dumps(self._contacts, ensure_ascii=False, indent=2), encoding="utf-8")

    def _message_path(self, wa_id: str, direction: str, msg_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", wa_id)
        return self.store_dir / safe / direction / f"{msg_id}.json"

    def _store_message(self, wa_id: str, direction: str, msg: dict[str, Any]) -> str:
        msg_id = msg.get("id") or uuid.uuid4().hex
        path = self._message_path(wa_id, direction, msg_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(msg)
        payload["id"] = msg_id
        payload["direction"] = direction
        payload["stored_at"] = time.time()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return msg_id

    def _iter_messages(self, wa_id: str, direction: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        base = self._message_path(wa_id, "", "").parent
        if not base.is_dir():
            return []
        files: list[Path] = []
        if direction:
            files = sorted((base / direction).glob("*.json"), key=lambda p: p.stat().st_mtime)
        else:
            for d in ("inbox", "sent"):
                files += sorted((base / d).glob("*.json"), key=lambda p: p.stat().st_mtime)
        files.sort(key=lambda p: p.stat().st_mtime)
        out: list[dict[str, Any]] = []
        for p in files[-limit:]:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        return out

    def _conversation_context(self, wa_id: str, latest: dict[str, Any]) -> dict[str, Any]:
        history = self._iter_messages(wa_id, limit=10)
        recent = []
        for m in history:
            text = m.get("body")
            if m.get("type") not in ("text",):
                text = f"[{m.get('type') or 'media'}]"
            recent.append({
                "id": m.get("id"),
                "fromMe": m.get("direction") == "sent",
                "text": (text or "")[:500],
                "timestamp": m.get("timestamp"),
            })
        latest_text = latest.get("body")
        if latest.get("type") not in ("text",):
            latest_text = f"[{latest.get('type') or 'media'}]"
        return {
            "platform": "whatsapp",
            "conversation_ref": f"whatsapp:{wa_id}",
            "recent_messages": recent[-10:],
            "latest_incoming": {
                "id": latest.get("id"),
                "from": latest.get("from"),
                "text": (latest_text or "")[:500],
                "timestamp": latest.get("timestamp"),
                "type": latest.get("type"),
            },
        }

    # ------------------------------------------------------------------ bridge events

    def _on_bridge_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "message":
            self._handle_incoming(data)
        elif etype == "qr":
            log.info("whatsapp bridge: QR available (type=%s)", "data-url" if data.get("qr_base64") else "ascii")
        elif etype == "ready":
            log.info("whatsapp bridge: ready me=%s", data.get("me"))
        elif etype == "disconnected":
            log.warning("whatsapp bridge disconnected: %s", data.get("reason"))
        elif etype == "error":
            log.error("whatsapp bridge error: %s", data.get("error"))
        else:
            log.debug("whatsapp bridge event: %s", etype)

    def _handle_incoming(self, msg: dict[str, Any]) -> None:
        from_id = msg.get("from")
        if not from_id:
            return
        if self.allowed_users and from_id not in self.allowed_users:
            log.info("whatsapp: ignored inbound from non-allowed %s", from_id)
            return
        msg_id = self._store_message(from_id, "inbox", msg)
        ctx = self._conversation_context(from_id, msg)
        push_inbox_event(
            sender="whatsapp",
            subject=f"whatsapp message from {from_id}",
            body=(msg.get("body") or f"[{msg.get('type')}]")[:2000],
            event_id=f"wa:{from_id}:{msg_id}",
            metadata={
                "conversation_ref": ctx["conversation_ref"],
                "message_id": msg_id,
                "from": from_id,
                "timestamp": msg.get("timestamp"),
                "type": msg.get("type"),
                "recent_messages": ctx["recent_messages"],
                "latest_incoming": ctx["latest_incoming"],
            },
        )

    # ------------------------------------------------------------------ actions

    def action(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        method = getattr(self, f"_{name}", None)
        if method is None:
            raise ValueError(f"unknown whatsapp action: {name}")
        return method(args or {})

    def _manual(self, args: dict[str, Any]) -> dict[str, Any]:
        return _skill.manual_payload(
            _SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH, _SKILL_NAME
        )

    def _status(self, args: dict[str, Any]) -> dict[str, Any]:
        alive = self.bridge.alive
        status = {"bridge_alive": alive, "session_dir": str(self.session_dir)}
        if alive:
            try:
                st = self.bridge.request("status")
                status.update({"ready": st.get("ready", False), "me": st.get("me"), "qr_available": st.get("qr_available", False)})
            except Exception as e:
                status["error"] = str(e)
        else:
            status["ready"] = False
        return redaction.safe_status(status)

    def _get_qr(self, args: dict[str, Any]) -> dict[str, Any]:
        self.bridge.start()
        try:
            st = self.bridge.request("status")
            if st.get("ready"):
                return {"ready": True, "me": st.get("me")}
        except Exception:
            pass
        try:
            result = self.bridge.request("get_qr")
        except Exception as e:
            return {"ready": False, "error": str(e), "hint": "If no QR yet, wait for the bridge to emit a qr event, then retry."}
        return {"ready": False, "qr_base64": result.get("qr_base64")}

    def _send(self, args: dict[str, Any]) -> dict[str, Any]:
        to = args.get("to") or args.get("wa_id")
        if not to:
            raise ValueError("send requires to or wa_id")
        result = self.bridge.request("send", {"to": to, "text": args.get("text"), "media": args.get("media")})
        msg = {"id": result.get("id"), "to": result.get("wa_id"), "body": args.get("text"), "type": "text" if args.get("text") else "media", "timestamp": time.time(), "fromMe": True}
        self._store_message(result.get("wa_id") or to, "sent", msg)
        return result

    def _check(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit") or 10)
        result = self.bridge.request("read", {"limit": limit})
        return result

    def _read(self, args: dict[str, Any]) -> dict[str, Any]:
        wa_id = args.get("wa_id") or args.get("to")
        if wa_id:
            msgs = self._iter_messages(wa_id, limit=int(args.get("limit") or 50))
            return {"messages": [{"id": m.get("id"), "fromMe": m.get("direction") == "sent", "body": m.get("body"), "type": m.get("type"), "timestamp": m.get("timestamp")} for m in msgs]}
        limit = int(args.get("limit") or 20)
        return self.bridge.request("read", {"limit": limit})

    def _reply(self, args: dict[str, Any]) -> dict[str, Any]:
        message_id = args.get("message_id")
        to = args.get("to") or args.get("wa_id")
        text = args.get("text")
        if not message_id or not text:
            raise ValueError("reply requires message_id and text")
        if not to:
            # Recover the conversation from the stored message if possible.
            for msg in self._iter_messages("", limit=200):
                if msg.get("id") == message_id:
                    to = msg.get("from") or msg.get("to")
                    break
        if not to:
            raise ValueError("reply requires to or wa_id (message not found in store)")
        result = self.bridge.request("reply", {"to": to, "message_id": message_id, "text": text})
        self._store_message(to, "sent", {"id": result.get("id"), "to": to, "body": text, "type": "text", "timestamp": time.time(), "fromMe": True})
        return result

    def _react(self, args: dict[str, Any]) -> dict[str, Any]:
        message_id = args.get("message_id")
        emoji = args.get("emoji")
        if not message_id or not emoji:
            raise ValueError("react requires message_id and emoji")
        return self.bridge.request("react", {"message_id": message_id, "emoji": emoji})

    def _search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query")
        if not query:
            raise ValueError("search requires query")
        limit = int(args.get("limit") or 20)
        return self.bridge.request("search", {"query": query, "limit": limit})

    def _contacts(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self.bridge.request("contacts")
        contacts = result.get("contacts") or []
        self._contacts = {c.get("id"): c for c in contacts if c.get("id")}
        self._save_contacts()
        return {"contacts": contacts[: int(args.get("limit") or 500)]}

    def _add_contact(self, args: dict[str, Any]) -> dict[str, Any]:
        wa_id = args.get("wa_id") or args.get("to")
        name = args.get("name")
        if not wa_id:
            raise ValueError("add_contact requires wa_id")
        self._contacts[wa_id] = {"id": wa_id, "name": name}
        self._save_contacts()
        return {"ok": True, "wa_id": wa_id}

    def _remove_contact(self, args: dict[str, Any]) -> dict[str, Any]:
        wa_id = args.get("wa_id") or args.get("to")
        if not wa_id:
            raise ValueError("remove_contact requires wa_id")
        self._contacts.pop(wa_id, None)
        self._save_contacts()
        return {"ok": True, "wa_id": wa_id}

    def _logout(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.bridge.alive:
            try:
                self.bridge.request("logout", timeout=10)
            except Exception:
                pass
        return {"ok": True}

    def close(self) -> None:
        self.bridge.stop()


# --------------------------------------------------------------------------- config

def load_config() -> tuple[dict[str, Any], Path | None]:
    """Load LINGTAI_WHATSAPP_CONFIG (JSON file) into a dict."""
    raw = os.environ.get("LINGTAI_WHATSAPP_CONFIG")
    if not raw:
        return {}, None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"LINGTAI_WHATSAPP_CONFIG not found: {path}")
    return json.loads(path.read_text(encoding="utf-8")), path


# local shim so server.py keeps its import shape without the _config dependency
def _config_load(config: dict[str, Any], path: Path | None) -> dict[str, Any]:
    return config
