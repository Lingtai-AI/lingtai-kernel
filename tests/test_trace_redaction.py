import json
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS
from unittest.mock import MagicMock

from lingtai.kernel.base_agent import BaseAgent
from lingtai.kernel.services.logging import CompositeLoggingService, JSONLLoggingService
from lingtai.kernel.trace_redaction import redact_for_trajectory, redact_text
from tests._workdir_lease_helpers import make_test_lease


def test_redact_text_common_secret_shapes():
    telegram_like = "123456789" + ":" + "A" * 35
    openai_like = "sk" + "-proj-" + "B" * 60
    bearer_like = "C" * 12 + "." + "D" * 12 + "_" + "E" * 12
    text = (
        f"token={telegram_like} "
        f"api_key='{openai_like}' "
        f"Authorization: Bearer {bearer_like}"
    )
    redacted = redact_text(text)
    assert telegram_like not in redacted
    assert openai_like not in redacted
    assert f"Bearer {bearer_like}" not in redacted
    assert "<REDACTED:" in redacted


def test_redact_for_trajectory_redacts_secret_mapping_values_without_mutation():
    event = {
        "type": "tool_result",
        "tool_args": {
            "token": "plain-app-password-value",
            "safe": "keep me",
        },
    }
    redacted = redact_for_trajectory(event)
    assert event["tool_args"]["token"] == "plain-app-password-value"
    assert redacted["tool_args"]["token"] == "<REDACTED:secret>"
    assert redacted["tool_args"]["safe"] == "keep me"


def test_composite_logging_redacts_before_jsonl_write_and_sqlite_index(tmp_path):
    jsonl = JSONLLoggingService(tmp_path / "events.jsonl")
    service = CompositeLoggingService(jsonl)
    service.log({
        "type": "tool_result",
        "ts": 1.0,
        "tool_args": {"password": "correct-horse-battery-staple"},
        "result": "token=" + "123456789" + ":" + "A" * 35,
    })
    service.close()

    line = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "correct-horse-battery-staple" not in line
    assert "123456789" + ":" not in line
    record = json.loads(line)
    assert record["tool_args"]["password"] == "<REDACTED:secret>"


def test_bearer_redaction_avoids_plain_prose_false_positive():
    prose = "Bearer responsibility-for-this-is-yours and continue."
    assert redact_text(prose) == prose
    token = "Bearer abc.def_ghi~jkl/mno+pqrstu"
    assert redact_text(token) == "Bearer <REDACTED:bearer_token>"


def test_redact_text_json_style_quoted_secret_assignment():
    raw = '{"password":"supersecret12345","safe":"ordinary"}'
    redacted = redact_text(raw)
    assert "supersecret12345" not in redacted
    assert '"password":"<REDACTED:secret>"' in redacted
    assert '"safe":"ordinary"' in redacted


def test_redact_for_trajectory_redacts_credential_key_names():
    event = {
        "type": "tool_result",
        "tool_args": {
            "private_key": "MIIEvQIBADANBgkq",
            "signing_key": "whsec_abcdef123456",
            "pem": "-----BEGIN PRIVATE KEY-----",
            "certificate": "MIIDdzCCAl-gAwIB",
            "cert": "MIIDdzCCAl-gAwIB",
            "connection_string": "Server=db;User=sa;Pwd=hunter2;",
            "dsn": "https://abc123@o0.ingest.sentry.io/1",
            "database_url": "postgres://user:hunter2@host/db",
            "database-url": "postgres://user:hunter2@host/db",
        },
    }
    redacted = redact_for_trajectory(event)
    for key in event["tool_args"]:
        assert redacted["tool_args"][key] == "<REDACTED:secret>", key


def test_redact_text_credential_key_assignments():
    secret = "postgres://user:hunter2@host/db"
    keys = (
        "private_key", "private-key", "signing_key", "signing-key",
        "pem", "certificate", "cert",
        "connection_string", "connection-string",
        "dsn", "database_url", "database-url",
    )
    for key in keys:
        for raw in (f"{key}={secret}", f"{key}='{secret}'", f'{{"{key}":"{secret}"}}'):
            redacted = redact_text(raw)
            assert secret not in redacted, raw
            assert "<REDACTED:secret>" in redacted, raw
    # Key and formatting survive; only the value is removed.
    assert redact_text(f'{{"database_url":"{secret}"}}') == '{"database_url":"<REDACTED:secret>"}'


def test_credential_key_near_words_are_not_redacted():
    # Substring near-words must not trigger key-name redaction.
    prose = "concert=ticket-code-42 dsname=analytics-source certainty=extremely-high"
    assert redact_text(prose) == prose
    # Prose mentions without an assignment stay untouched.
    prose2 = "the certificate expired and the pem file was rotated"
    assert redact_text(prose2) == prose2
    # client_id is a public identifier (OAuth2), deliberately not redacted.
    event = {"tool_args": {"client_id": "public-client-identifier"}}
    assert redact_for_trajectory(event)["tool_args"]["client_id"] == "public-client-identifier"


def test_save_chat_history_redacts_persisted_copy_without_mutation(tmp_path):
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "test"
    svc.model = "test-model"
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=svc, agent_name="redactor", working_dir=tmp_path / "redactor", workdir_lease=make_test_lease())

    raw_secret = "plain-app-password-value"
    state = {
        "messages": [
            {
                "role": "user",
                "content": '{"password":"%s","safe":"ordinary"}' % raw_secret,
            },
            {
                "role": "tool",
                "content": {"token": raw_secret, "safe": "keep me"},
            },
        ]
    }
    agent.get_chat_state = lambda: state  # type: ignore[method-assign]
    agent._write_status_snapshot = lambda: None  # type: ignore[method-assign]
    agent._workdir.write_manifest = lambda manifest: None  # type: ignore[method-assign]

    agent._save_chat_history()

    text = (tmp_path / "redactor" / "history" / "chat_history.jsonl").read_text(encoding="utf-8")
    assert raw_secret not in text
    records = [json.loads(line) for line in text.splitlines()]
    assert records[0]["content"] == '{"password":"<REDACTED:secret>","safe":"ordinary"}'
    assert records[1]["content"]["token"] == "<REDACTED:secret>"
    assert records[1]["content"]["safe"] == "keep me"
    assert state["messages"][0]["content"] == '{"password":"%s","safe":"ordinary"}' % raw_secret
    assert state["messages"][1]["content"]["token"] == raw_secret
