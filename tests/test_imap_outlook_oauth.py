from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.mcp_servers.imap import account as account_module
from lingtai.mcp_servers.imap.oauth import (
    AUTHORITY,
    IMAP_SCOPE,
    SMTP_SCOPE,
    OAuth2AuthProvider,
    OAuthError,
    PasswordAuthProvider,
    TokenCacheStore,
    bootstrap_device_flow,
    normalize_accounts,
    provider_from_config,
)


class FakeCache:
    def __init__(self):
        self.state = ""
        self.has_state_changed = False

    def deserialize(self, state):
        self.state = state

    def serialize(self):
        self.has_state_changed = False
        return self.state or "synthetic-cache"


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.silent_scopes = []
        self.result = {"access_token": "synthetic-access-value"}
        self.flow = {"user_code": "SYNTHETIC-CODE", "message": "Microsoft message SYNTHETIC-CODE"}
        self.device_scopes = None

    def get_accounts(self, **kwargs):
        return [{"username": kwargs["username"]}]

    def acquire_token_silent(self, scopes, account):
        self.silent_scopes.append((scopes, account))
        return self.result

    def initiate_device_flow(self, scopes):
        self.device_scopes = scopes
        return self.flow

    def acquire_token_by_device_flow(self, flow):
        self.kwargs["token_cache"].state = "synthetic-cache"
        self.kwargs["token_cache"].has_state_changed = True
        return {"access_token": "synthetic-access-value", "refresh_token": "synthetic-refresh-value"}


@pytest.fixture
def fake_msal():
    clients = []

    def factory(**kwargs):
        client = FakeClient(**kwargs)
        clients.append(client)
        return client

    return SimpleNamespace(SerializableTokenCache=FakeCache), factory, clients


def test_legacy_password_provider_keeps_login_calls():
    class Client:
        def __init__(self): self.calls = []
        def login(self, *args): self.calls.append(args)
    client = Client()
    provider = PasswordAuthProvider("synthetic-password")
    provider.authenticate_imap(client, account="person@example.com")
    assert client.calls == [("person@example.com", "synthetic-password")]
    assert provider.status() == {"auth_type": "password", "auth_state": "configured"}


def test_oauth_config_is_explicit_and_rejects_password_or_secret():
    password = {"accounts": [{"email_address": "person@example.com", "email_password": "synthetic-password"}]}
    assert normalize_accounts(password) == password["accounts"]
    oauth = {"accounts": [{"email_address": "person@example.com", "auth": {
        "type": "microsoft_oauth2", "client_id": "synthetic-client",
        "token_cache": "oauth.cache",
    }}]}
    assert normalize_accounts(oauth) == oauth["accounts"]
    provider, smtp_enabled = provider_from_config(oauth["accounts"][0])
    assert isinstance(provider, OAuth2AuthProvider)
    assert smtp_enabled is False
    with pytest.raises(ValueError, match="config_invalid"):
        normalize_accounts({"accounts": [{**oauth["accounts"][0], "email_password": "synthetic-password"}]})
    with pytest.raises(ValueError, match="config_invalid"):
        normalize_accounts({"accounts": [{"email_address": "person@example.com", "auth": {
            "type": "microsoft_oauth2", "client_id": "synthetic-client",
            "token_cache": "oauth.cache", "client_secret": "synthetic-secret",
        }}]})


def test_oauth_imap_uses_silent_cache_and_native_login(tmp_path, fake_msal):
    module, factory, clients = fake_msal
    provider = OAuth2AuthProvider(
        "person@example.com", "synthetic-client", tmp_path / "cache",
        msal_module=module, client_factory=factory,
    )
    class Imap:
        def __init__(self): self.calls = []
        def oauth2_login(self, *args, **kwargs): self.calls.append((args, kwargs))
    client = Imap()
    provider.authenticate_imap(client, account="person@example.com")
    assert client.calls == [(
        ("person@example.com", "synthetic-access-value"), {"mech": "XOAUTH2"}
    )]
    assert clients[0].kwargs["authority"] == AUTHORITY
    assert clients[0].silent_scopes == [([IMAP_SCOPE], {"username": "person@example.com"})]
    assert provider.status()["auth_state"] == "ready"


def test_listener_reconnect_authenticates_each_new_connection(monkeypatch, tmp_path, fake_msal):
    module, factory, clients = fake_msal
    provider = OAuth2AuthProvider("person@example.com", "synthetic-client", tmp_path / "cache", msal_module=module, client_factory=factory)
    made = []
    class Imap:
        def __init__(self, *args, **kwargs): made.append(self)
        def logout(self): pass
        def oauth2_login(self, *args, **kwargs): self.oauth = args
        def select_folder(self, folder): self.folder = folder
    monkeypatch.setattr(account_module, "IMAPClient", Imap)
    acct = account_module.IMAPAccount("person@example.com", "unused", auth_provider=provider)
    acct._connect_listener("INBOX")
    acct._connect_listener("INBOX")
    assert len(made) == 2
    assert len(clients[0].silent_scopes) == 2
    assert all(c.oauth[1] == "synthetic-access-value" for c in made)


def test_smtp_xoauth2_callable_has_exact_payload(tmp_path, fake_msal):
    module, factory, _ = fake_msal
    provider = OAuth2AuthProvider(
        "person@example.com", "synthetic-client", tmp_path / "cache",
        smtp_enabled=True, msal_module=module, client_factory=factory,
    )
    class SMTP:
        def __init__(self): self.calls = []
        def ehlo_or_helo_if_needed(self): self.calls.append(("ehlo", {}))
        def auth(self, *args, **kwargs): self.calls.append((args, kwargs))
    server = SMTP()
    provider.authenticate_smtp(server, account="person@example.com")
    assert server.calls[0] == ("ehlo", {})
    args, kwargs = server.calls[1]
    assert args[0] == "XOAUTH2"
    assert args[1](b"challenge") == "user=person@example.com\x01auth=Bearer synthetic-access-value\x01\x01"
    assert kwargs == {"initial_response_ok": True}
    client = provider._client
    assert client.silent_scopes[-1][0] == [IMAP_SCOPE, SMTP_SCOPE]


def test_smtp_disabled_does_not_open_socket(monkeypatch, tmp_path, fake_msal):
    module, factory, _ = fake_msal
    provider = OAuth2AuthProvider("person@example.com", "synthetic-client", tmp_path / "cache", smtp_enabled=False, msal_module=module, client_factory=factory)
    acct = account_module.IMAPAccount("person@example.com", "unused", auth_provider=provider, smtp_enabled=False)
    monkeypatch.setattr(account_module.smtplib, "SMTP", lambda *a, **k: pytest.fail("SMTP opened"))
    assert acct.send_email(["recipient@example.com"], "subject", "body") == "smtp_disabled"


def test_reauth_error_is_redacted(tmp_path, fake_msal):
    module, factory, clients = fake_msal
    provider = OAuth2AuthProvider("person@example.com", "synthetic-client", tmp_path / "cache", msal_module=module, client_factory=factory)
    provider._client_for_cache()
    clients[0].result = {"error": "consent_required", "error_description": "synthetic-access-value"}
    with pytest.raises(OAuthError) as exc:
        provider._acquire(smtp=False)
    assert str(exc.value) == "oauth_reauthorization_required"
    assert "synthetic-access-value" not in str(exc.value)
    assert provider.status()["auth_state"] == "reauth_required"


def test_cache_is_owner_only_and_atomic(tmp_path):
    path = tmp_path / "private" / "cache"
    store = TokenCacheStore(path)
    store.save("synthetic-cache-state")
    assert store.load() == "synthetic-cache-state"
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0
    assert not list(path.parent.glob(".*.tmp"))


def test_bootstrap_fake_msal_prints_only_microsoft_message(tmp_path, fake_msal, capsys):
    module, factory, _ = fake_msal
    bootstrap_device_flow(
        "person@example.com", "synthetic-client", tmp_path / "cache",
        msal_module=module, client_factory=factory,
    )
    output = capsys.readouterr().out
    assert output == "Microsoft message SYNTHETIC-CODE\n"
    assert "synthetic-access-value" not in output
    assert "synthetic-refresh-value" not in output
    assert (tmp_path / "cache").read_text() == "synthetic-cache"


def test_bootstrap_smtp_scope_is_opt_in(tmp_path, fake_msal):
    module, factory, clients = fake_msal
    bootstrap_device_flow(
        "person@example.com", "synthetic-client", tmp_path / "cache",
        smtp_enabled=False, msal_module=module, client_factory=factory, output=lambda _: None,
    )
    # offline_access is reserved and added by MSAL; passing it explicitly fails.
    assert clients[0].device_scopes == [IMAP_SCOPE]


def test_status_contains_only_safe_auth_fields(tmp_path, fake_msal):
    module, factory, _ = fake_msal
    provider = OAuth2AuthProvider("person@example.com", "synthetic-client", tmp_path / "cache", smtp_enabled=False, msal_module=module, client_factory=factory)
    status = provider.status()
    assert status == {"auth_type": "microsoft_oauth2", "smtp_enabled": False, "auth_state": "bootstrap_required"}
    assert "token_cache" not in status and "access_token" not in status


def test_dependency_and_docs_contract():
    root = Path(__file__).parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    manual = (root / "src/lingtai/mcp_servers/imap/SKILL.md").read_text()
    assert '"msal>=1.37.0"' in pyproject
    assert "lingtai-imap-bootstrap" in pyproject
    assert "microsoft_oauth2" in manual
    assert "oauth_reauthorization_required" in manual


def test_account_tool_connection_uses_oauth2_login(monkeypatch, tmp_path, fake_msal):
    module, factory, _ = fake_msal
    provider = OAuth2AuthProvider("person@example.com", "synthetic-client", tmp_path / "cache", msal_module=module, client_factory=factory)
    made = []
    class Imap:
        def __init__(self, *args, **kwargs): made.append(self)
        def oauth2_login(self, *args, **kwargs): self.oauth = (args, kwargs)
        def capabilities(self): return [b"IDLE", b"UIDPLUS"]
        def list_folders(self): return [([], "/", "INBOX")]
    monkeypatch.setattr(account_module, "IMAPClient", Imap)
    acct = account_module.IMAPAccount("person@example.com", auth_provider=provider)
    acct.connect()
    assert made[0].oauth == (("person@example.com", "synthetic-access-value"), {"mech": "XOAUTH2"})
    assert acct.auth_status["auth_type"] == "microsoft_oauth2"
