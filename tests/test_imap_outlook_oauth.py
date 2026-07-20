from unittest.mock import MagicMock
import lingtai.mcp_servers.imap.account as account_mod


def test_outlook_oauth_uses_silent_token_and_imap_login(tmp_path, monkeypatch):
    cache_path = tmp_path / "outlook.cache"
    cache_path.write_text("serialized")
    cache = MagicMock(has_state_changed=True)
    cache.serialize.return_value = "updated"
    monkeypatch.setattr(account_mod.msal, "SerializableTokenCache", lambda: cache)
    app = MagicMock()
    account = {"username": "user@outlook.com"}
    app.get_accounts.return_value = [account]
    app.acquire_token_silent.return_value = {"access_token": "access-token"}
    factory = MagicMock(return_value=app)
    monkeypatch.setattr(account_mod.msal, "PublicClientApplication", factory)
    password_client = MagicMock()
    account_mod.IMAPAccount("password@example.com", "secret")._login(password_client)
    password_client.login.assert_called_once_with("password@example.com", "secret")
    client = MagicMock()
    imap = account_mod.IMAPAccount(
        email_address="user@outlook.com",
        working_dir=tmp_path,
        auth={"type": "microsoft_oauth2", "client_id": "client-id", "token_cache": "outlook.cache"},
    )
    imap._login(client)
    cache.deserialize.assert_called_once_with("serialized")
    app.get_accounts.assert_called_once_with(username="user@outlook.com")
    scope = "https://outlook.office.com/IMAP.AccessAsUser.All"
    app.acquire_token_silent.assert_called_once_with([scope], account=account)
    client.oauth2_login.assert_called_once_with("user@outlook.com", "access-token")
    assert cache_path.read_text() == "updated"
    assert cache_path.stat().st_mode & 0o777 == 0o600
