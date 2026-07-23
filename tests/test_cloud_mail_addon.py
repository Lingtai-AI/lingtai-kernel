"""Unit tests for the curated Cloud Mail MCP addon.

No real network: a small in-process router backs an ``httpx.MockTransport``
that speaks the Cloud Mail ``{code, message, data}`` envelope and the two
auth schemes (public-token header / user JWT header).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from lingtai.mcp_servers.cloud_mail.client import CloudMailClient, CloudMailError
from lingtai.mcp_servers.cloud_mail.manager import CloudMailManager
from lingtai.mcp_servers.cloud_mail import server as cm_server


# ---------------------------------------------------------------------------
# Fake Cloud Mail backend (httpx.MockTransport handler)
# ---------------------------------------------------------------------------

PUBLIC_TOKEN = "pub-token-uuid"
USER_JWT = "user.jwt.value"


def make_router(rows=None, *, captured=None):
    """Return an httpx handler emulating the Cloud Mail REST surface.

    ``rows`` is the list returned by /public/emailList. ``captured`` (if a
    dict) records the last request bodies/headers for assertions.
    """
    rows = rows if rows is not None else []
    captured = captured if captured is not None else {}

    def _ok(data):
        return httpx.Response(200, json={"code": 200, "message": "success", "data": data})

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except Exception:
                body = {}
        auth = request.headers.get("Authorization")

        if path == "/public/genToken":
            captured["genToken_body"] = body
            if body.get("email") == "admin@example.com" and body.get("password") == "adminpw":
                return _ok({"token": PUBLIC_TOKEN})
            return httpx.Response(200, json={"code": 401, "message": "bad admin creds"})

        if path == "/public/emailList":
            captured["emailList_auth"] = auth
            captured["emailList_body"] = body
            if auth != PUBLIC_TOKEN:
                return httpx.Response(200, json={"code": 401, "message": "missing public token"})
            return _ok(list(rows))

        if path == "/public/addUser":
            captured["addUser_auth"] = auth
            captured["addUser_body"] = body
            if auth != PUBLIC_TOKEN:
                return httpx.Response(200, json={"code": 401, "message": "missing public token"})
            if not isinstance(body.get("list"), list):
                return httpx.Response(200, json={"code": 400, "message": "missing user list"})
            return _ok(None)

        if path == "/login":
            captured["login_body"] = body
            if body.get("email") == "user@example.com" and body.get("password") == "userpw":
                return _ok({"token": USER_JWT})
            return httpx.Response(200, json={"code": 401, "message": "bad user creds"})

        if path == "/email/send":
            captured["send_auth"] = auth
            captured["send_body"] = body
            if auth != USER_JWT:
                return httpx.Response(200, json={"code": 401, "message": "missing jwt"})
            return _ok({"emailId": 999, "sent": True})

        return httpx.Response(404, json={"code": 404, "message": f"no route {path}"})

    return httpx.MockTransport(handler), captured


def make_manager(tmp_path, *, rows=None, captured=None, account_overrides=None):
    transport, captured = make_router(rows=rows, captured=captured)
    acct = {
        "alias": "cloudmail",
        "base_url": "https://mail.example.com",
        "admin_email": "admin@example.com",
        "admin_password": "adminpw",
        "poll_interval": 30,
    }
    if account_overrides:
        acct.update(account_overrides)
    mgr = CloudMailManager(
        accounts=[acct],
        working_dir=tmp_path,
        transport=transport,
    )
    return mgr, captured


def _row(email_id, *, sender="alice@x.com", subject="hi", to="agent@example.com",
         text="hello world", name="Alice"):
    return {
        "emailId": email_id,
        "sendEmail": sender,
        "sendName": name,
        "subject": subject,
        "toEmail": to,
        "toName": "Agent",
        "type": 1,
        "createTime": "2026-06-13T00:00:00Z",
        "content": f"<p>{text}</p>",
        "text": text,
        "isDel": 0,
    }


# ---------------------------------------------------------------------------
# Client: envelope + auth
# ---------------------------------------------------------------------------

def test_public_token_minted_and_header_is_raw_not_bearer(tmp_path):
    captured = {}
    transport, captured = make_router(rows=[_row(1)], captured=captured)
    client = CloudMailClient(
        base_url="https://mail.example.com",
        admin_email="admin@example.com",
        admin_password="adminpw",
        transport=transport,
    )
    rows = client.email_list(size=10)
    assert [r["emailId"] for r in rows] == [1]
    # Header is the raw token, NOT "Bearer <token>".
    assert captured["emailList_auth"] == PUBLIC_TOKEN
    client.close()


def test_non_200_envelope_raises_cloud_mail_error(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"code": 500, "message": "boom"})
    client = CloudMailClient(
        base_url="https://mail.example.com",
        admin_email="admin@example.com",
        admin_password="adminpw",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(CloudMailError) as ei:
        client.email_list()
    assert "code 500" in str(ei.value) and "boom" in str(ei.value)
    client.close()


def test_public_api_without_admin_creds_errors_clearly(tmp_path):
    transport, _ = make_router(rows=[])
    client = CloudMailClient(base_url="https://mail.example.com", transport=transport)
    with pytest.raises(CloudMailError) as ei:
        client.email_list()
    assert "admin_email" in str(ei.value)
    client.close()


# ---------------------------------------------------------------------------
# Manager: check / search id formatting
# ---------------------------------------------------------------------------

def test_check_returns_compound_ids_and_preview(tmp_path):
    mgr, _ = make_manager(tmp_path, rows=[_row(5, subject="newest"), _row(4)])
    out = mgr.handle({"action": "check", "limit": 10})
    assert out["status"] == "ok"
    assert out["count"] == 2
    ids = [e["compound_id"] for e in out["emails"]]
    assert ids == ["cloudmail:5", "cloudmail:4"]
    assert out["emails"][0]["from"] == "alice@x.com"
    assert "preview" in out["emails"][0]
    mgr.stop()


def test_search_passes_filters_to_public_emaillist(tmp_path):
    captured = {}
    mgr, captured = make_manager(tmp_path, rows=[_row(7)], captured=captured)
    out = mgr.handle({
        "action": "search",
        "subject": "invoice",
        "send_email": "boss@x.com",
        "time_sort": "asc",
        "num": 2,
        "size": 5,
    })
    assert out["status"] == "ok"
    body = captured["emailList_body"]
    assert body["subject"] == "invoice"
    assert body["sendEmail"] == "boss@x.com"
    assert body["timeSort"] == "asc"
    assert body["num"] == 2 and body["size"] == 5
    # None filters are dropped, not sent as null.
    assert "content" not in body
    mgr.stop()


def test_read_by_compound_id_returns_full_content(tmp_path):
    mgr, _ = make_manager(tmp_path, rows=[_row(11, text="full body here"), _row(10)])
    out = mgr.handle({"action": "read", "id": "cloudmail:11"})
    assert out["status"] == "ok"
    assert out["email"]["email_id"] == 11
    assert out["email"]["text"] == "full body here"
    assert out["email"]["content"] == "<p>full body here</p>"
    mgr.stop()


def test_read_missing_id_is_clear_error(tmp_path):
    mgr, _ = make_manager(tmp_path, rows=[_row(1)])
    out = mgr.handle({"action": "read", "id": "cloudmail:999"})
    assert out["status"] == "error"
    assert "not found" in out["error"]
    mgr.stop()


# ---------------------------------------------------------------------------
# Manager: accounts redaction
# ---------------------------------------------------------------------------

def test_accounts_status_is_redacted(tmp_path):
    mgr, _ = make_manager(tmp_path, account_overrides={
        "user_email": "user@example.com",
        "user_password": "userpw",
        "send_account_id": 3,
        "allowed_senders": ["A@X.com"],
    })
    out = mgr.handle({"action": "accounts"})
    assert out["status"] == "ok"
    blob = json.dumps(out)
    # No secrets anywhere in the status payload.
    assert "adminpw" not in blob
    assert "userpw" not in blob
    acct = out["accounts"][0]
    assert acct["alias"] == "cloudmail"
    assert acct["can_send"] is True
    assert acct["send_account_id"] == 3
    mgr.stop()


# ---------------------------------------------------------------------------
# Manager: send
# ---------------------------------------------------------------------------

def test_send_without_user_creds_errors_with_guidance(tmp_path):
    mgr, _ = make_manager(tmp_path)  # no user creds
    out = mgr.handle({"action": "send", "address": "x@y.com", "message": "hi"})
    assert out["status"] == "error"
    assert "user_email" in out["error"] and "user_password" in out["error"]
    mgr.stop()


def test_send_builds_expected_payload_and_uses_jwt(tmp_path):
    captured = {}
    mgr, captured = make_manager(
        tmp_path, captured=captured,
        account_overrides={
            "user_email": "user@example.com",
            "user_password": "userpw",
            "send_account_id": 1,
        },
    )
    out = mgr.handle({
        "action": "send",
        "address": ["to1@x.com", "to2@x.com"],
        "subject": "Subject",
        "message": "plain body",
        "name": "Agent",
    })
    assert out["status"] == "ok"
    assert out["sent_to"] == ["to1@x.com", "to2@x.com"]
    # JWT header (raw, not Bearer) used for /email/send.
    assert captured["send_auth"] == USER_JWT
    body = captured["send_body"]
    assert body["accountId"] == 1
    assert body["receiveEmail"] == ["to1@x.com", "to2@x.com"]
    assert body["subject"] == "Subject"
    assert body["text"] == "plain body"
    assert body["content"] == "plain body"  # falls back to text when no html
    assert body["attachments"] == []
    mgr.stop()


def test_send_rejects_attachments_first_pass(tmp_path):
    mgr, _ = make_manager(
        tmp_path,
        account_overrides={
            "user_email": "user@example.com",
            "user_password": "userpw",
            "send_account_id": 1,
        },
    )
    out = mgr.handle({
        "action": "send", "address": "x@y.com", "message": "hi",
        "attachments": [{"name": "a.pdf"}],
    })
    assert out["status"] == "error"
    assert "attachments are not supported" in out["error"]
    mgr.stop()


# ---------------------------------------------------------------------------
# Polling / LICC
# ---------------------------------------------------------------------------

def test_first_poll_seeds_without_licc_flood(tmp_path):
    events = []
    transport, _ = make_router(rows=[_row(3), _row(2), _row(1)])
    mgr = CloudMailManager(
        accounts=[{
            "alias": "cloudmail",
            "base_url": "https://mail.example.com",
            "admin_email": "admin@example.com",
            "admin_password": "adminpw",
        }],
        working_dir=tmp_path,
        on_inbound=lambda ev: events.append(ev),
        transport=transport,
    )
    acct = mgr.default_account
    pushed = mgr.poll_once(acct)
    assert pushed == 0  # first run seeds silently
    assert events == []
    # Watermark recorded at the highest emailId, marked seeded.
    assert acct.watermark.last_email_id == 3
    assert acct.watermark.seeded is True
    mgr.stop()


def test_second_poll_pushes_only_new_email(tmp_path):
    events = []
    captured = {}
    rows = [_row(3), _row(2), _row(1)]
    transport, captured = make_router(rows=rows, captured=captured)
    mgr = CloudMailManager(
        accounts=[{
            "alias": "cloudmail",
            "base_url": "https://mail.example.com",
            "admin_email": "admin@example.com",
            "admin_password": "adminpw",
        }],
        working_dir=tmp_path,
        on_inbound=lambda ev: events.append(ev),
        transport=transport,
    )
    acct = mgr.default_account
    mgr.poll_once(acct)  # seed at 3
    # A new email arrives.
    rows.insert(0, _row(4, sender="new@x.com", subject="fresh", text="brand new"))
    pushed = mgr.poll_once(acct)
    assert pushed == 1
    assert len(events) == 1
    ev = events[0]
    assert ev["from"] == "new@x.com"
    assert ev["subject"] == "fresh"
    md = ev["metadata"]
    assert md["source"] == "cloud_mail"
    assert md["event_type"] == "email"
    assert md["account"] == "cloudmail"
    assert md["email_id"] == 4
    assert md["compound_id"] == "cloudmail:4"
    assert acct.watermark.last_email_id == 4
    mgr.stop()


def test_notify_existing_pushes_on_first_poll(tmp_path):
    events = []
    transport, _ = make_router(rows=[_row(2), _row(1)])
    mgr = CloudMailManager(
        accounts=[{
            "alias": "cloudmail",
            "base_url": "https://mail.example.com",
            "admin_email": "admin@example.com",
            "admin_password": "adminpw",
            "notify_existing": True,
        }],
        working_dir=tmp_path,
        on_inbound=lambda ev: events.append(ev),
        transport=transport,
    )
    pushed = mgr.poll_once(mgr.default_account)
    assert pushed == 2
    # delivered oldest-first
    assert [e["metadata"]["email_id"] for e in events] == [1, 2]
    mgr.stop()


def test_allowed_senders_filter_case_insensitive(tmp_path):
    events = []
    rows = [_row(1)]
    transport, _ = make_router(rows=rows)
    mgr = CloudMailManager(
        accounts=[{
            "alias": "cloudmail",
            "base_url": "https://mail.example.com",
            "admin_email": "admin@example.com",
            "admin_password": "adminpw",
            "allowed_senders": ["Allowed@X.com"],
        }],
        working_dir=tmp_path,
        on_inbound=lambda ev: events.append(ev),
        transport=transport,
    )
    acct = mgr.default_account
    mgr.poll_once(acct)  # seed at 1
    # Disallowed sender — must be filtered, but watermark still advances.
    rows.insert(0, _row(2, sender="stranger@x.com"))
    assert mgr.poll_once(acct) == 0
    assert events == []
    assert acct.watermark.last_email_id == 2
    # Allowed sender (different case) — delivered.
    rows.insert(0, _row(3, sender="ALLOWED@x.COM"))
    assert mgr.poll_once(acct) == 1
    assert len(events) == 1
    mgr.stop()


def test_failed_allowed_delivery_blocks_higher_filtered_watermark(tmp_path):
    rows = [
        _row(3, sender="stranger@x.com"),
        _row(2, sender="allowed@x.com"),
        _row(1, sender="allowed@x.com"),
    ]
    transport, _ = make_router(rows=rows)
    attempts = []
    fail_email_id = 2

    def on_inbound(event):
        email_id = event["metadata"]["email_id"]
        attempts.append(email_id)
        if email_id == fail_email_id:
            raise RuntimeError("temporary LICC failure")

    mgr = CloudMailManager(
        accounts=[{
            "alias": "cloudmail",
            "base_url": "https://mail.example.com",
            "admin_email": "admin@example.com",
            "admin_password": "adminpw",
            "allowed_senders": ["allowed@x.com"],
            "notify_existing": True,
        }],
        working_dir=tmp_path,
        on_inbound=on_inbound,
        transport=transport,
    )
    acct = mgr.default_account

    assert mgr.poll_once(acct) == 1
    assert attempts == [1, 2]
    assert acct.watermark.last_email_id == 1

    fail_email_id = None
    assert mgr.poll_once(acct) == 1
    assert attempts == [1, 2, 2]
    assert acct.watermark.last_email_id == 3

    assert mgr.poll_once(acct) == 0
    assert attempts == [1, 2, 2]
    mgr.stop()


def test_add_user_uses_cloud_mail_batch_contract(tmp_path):
    mgr, captured = make_manager(tmp_path)
    out = mgr.handle({
        "action": "add_user",
        "email": "new@example.com",
        "password": "secretpw",
        "role_name": "member",
    })
    assert out["status"] == "ok"
    assert out["added"] == "new@example.com"
    assert captured["addUser_auth"] == PUBLIC_TOKEN
    assert captured["addUser_body"] == {
        "list": [{"email": "new@example.com", "password": "secretpw", "roleName": "member"}]
    }


# ---------------------------------------------------------------------------
# Config loading / path resolution
# ---------------------------------------------------------------------------

def test_load_config_resolves_relative_to_agent_dir(tmp_path, monkeypatch):
    cfg = {"accounts": [{"alias": "a", "base_url": "https://m.example.com"}]}
    (tmp_path / "cm.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("LINGTAI_CLOUD_MAIL_CONFIG", "cm.json")
    loaded = cm_server.load_config()
    assert loaded == cfg
    accts = cm_server.accounts_from_config(loaded)
    assert accts[0]["base_url"] == "https://m.example.com"


def test_load_config_missing_env_errors(monkeypatch):
    monkeypatch.delenv("LINGTAI_CLOUD_MAIL_CONFIG", raising=False)
    with pytest.raises(ValueError) as ei:
        cm_server.load_config()
    assert "LINGTAI_CLOUD_MAIL_CONFIG" in str(ei.value)


def test_accounts_from_config_flat_single_account():
    out = cm_server.accounts_from_config({"base_url": "https://m.example.com", "alias": "x"})
    assert out == [{"base_url": "https://m.example.com", "alias": "x"}]


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_catalog_contains_cloud_mail():
    catalog_path = Path(__file__).resolve().parents[1] / "src" / "lingtai" / "mcp_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert "cloud_mail" in catalog
    entry = catalog["cloud_mail"]
    assert entry["name"] == "cloud_mail"
    assert entry["args"] == ["-m", "lingtai.mcp_servers.cloud_mail"]
    assert entry["transport"] == "stdio"
    assert "cloud-mail" in entry["homepage"]
