"""Pure unit tests for daemon peer-core primitives (checkpoint A).

These exercise only the pure, clock-free helpers and dataclasses in
``lingtai.core.daemon.peer``. No DaemonManager wiring, no threads, no LLM.
Group lifecycle / router / delivery are later checkpoints.
"""
import re
from datetime import datetime, timezone

import pytest

from lingtai.core.daemon import peer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 6, 13, 15, 22, 33, tzinfo=timezone.utc)


def _member(
    *,
    em_id="em-1",
    run_id="em-1-20260613-152200-aaa111",
    handle="codex",
    backend="codex",
    role=None,
    can_author=True,
    can_receive=True,
) -> peer.GroupMember:
    return peer.GroupMember(
        em_id=em_id,
        run_id=run_id,
        handle=handle,
        backend=backend,
        role=role,
        can_author_peer_send=can_author,
        can_receive_peer_message=can_receive,
    )


def _group(
    *,
    members=None,
    state="active",
    policy=None,
    message_count=0,
    group_id="dg-20260613-152233-a1b2c3",
) -> peer.DaemonGroup:
    if members is None:
        members = [
            _member(em_id="em-1", run_id="run-codex", handle="codex", backend="codex"),
            _member(em_id="em-2", run_id="run-claude", handle="claude",
                    backend="claude-code"),
        ]
    if policy is None:
        policy = peer.GroupPolicy(
            allow_pairs={("codex", "claude"), ("claude", "codex")}
        )
    by_handle = {m.handle: m for m in members}
    by_run_id = {m.run_id: m for m in members}
    return peer.DaemonGroup(
        group_id=group_id,
        state=state,
        roster_by_handle=by_handle,
        roster_by_run_id=by_run_id,
        policy=policy,
        message_count=message_count,
    )


def _envelope(group, *, from_handle="codex", to_handle="claude", body="hi",
              hop_budget=1, source_adapter="inproc", in_reply_to=None,
              message_id="pm-1", created_at="2026-06-13T15:22:33Z"):
    src = group.roster_by_handle[from_handle]
    tgt = group.roster_by_handle.get(to_handle)
    return peer.PeerMessageEnvelope(
        message_id=message_id,
        group_id=group.group_id,
        from_run_id=src.run_id,
        from_handle=from_handle,
        to_run_id=tgt.run_id if tgt else "unknown-run",
        to_handle=to_handle,
        body=body,
        hop_budget=hop_budget,
        source_adapter=source_adapter,
        created_at=created_at,
        in_reply_to=in_reply_to,
    )


# ---------------------------------------------------------------------------
# Handle validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("handle", ["codex", "claude", "a", "A1", "x_y-z", "H" + "a" * 31])
def test_validate_handle_accepts_safe_handles(handle):
    assert peer.validate_handle(handle) is True


@pytest.mark.parametrize("handle", [
    "",            # empty
    "1abc",        # leading digit
    "_abc",        # leading underscore
    "-abc",        # leading dash
    "ab c",        # space
    "ab.c",        # dot
    "ab/c",        # slash
    "a" * 33,      # too long (>32)
    "café",        # non-ascii
])
def test_validate_handle_rejects_unsafe_handles(handle):
    assert peer.validate_handle(handle) is False


def test_validate_handle_non_string_is_false():
    assert peer.validate_handle(None) is False
    assert peer.validate_handle(123) is False


# ---------------------------------------------------------------------------
# ID generation (deterministic prefix/date, random suffix)
# ---------------------------------------------------------------------------

def test_new_group_id_format():
    gid = peer.new_group_id(FIXED_NOW)
    assert re.fullmatch(r"dg-20260613-152233-[0-9a-f]{6}", gid), gid


def test_new_message_id_format():
    mid = peer.new_message_id(FIXED_NOW)
    assert re.fullmatch(r"pm-20260613-152233-[0-9a-f]{6}", mid), mid


def test_new_group_id_suffix_varies():
    a = peer.new_group_id(FIXED_NOW)
    b = peer.new_group_id(FIXED_NOW)
    # Same clock, but the random suffix should (almost surely) differ.
    assert a != b


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------

def test_group_policy_defaults():
    p = peer.GroupPolicy()
    assert p.max_message_bytes == 8192
    assert p.default_hop_budget == 1
    assert p.max_messages_per_group == 32
    assert p.allow_pairs is None


def test_group_member_defaults():
    m = peer.GroupMember(em_id="em-1", run_id="r", handle="h", backend="codex")
    assert m.role is None
    assert m.can_author_peer_send is False
    assert m.can_receive_peer_message is True


def test_peer_send_intent_defaults_to_cli_stdout():
    intent = peer.PeerSendIntent(to="claude", body="hi")
    assert intent.in_reply_to is None
    assert intent.source_adapter == "cli-stdout"


# ---------------------------------------------------------------------------
# CLI sentinel parser — fail-closed, exactly one strict block
# ---------------------------------------------------------------------------

def _block(payload: str) -> str:
    return f"<<<PEER_SEND>>>\n{payload}\n<<<END>>>"


def test_parse_peer_send_contract_accepts_exact_block():
    text = "some reasoning\n" + _block('{"to":"claude","body":"hello there"}')
    intent, errors = peer.parse_peer_send_contract(text)
    assert errors == []
    assert intent is not None
    assert intent.to == "claude"
    assert intent.body == "hello there"
    assert intent.in_reply_to is None
    assert intent.source_adapter == "cli-stdout"


def test_parse_peer_send_contract_accepts_optional_in_reply_to():
    text = _block('{"to":"claude","body":"hi","in_reply_to":"pm-9"}')
    intent, errors = peer.parse_peer_send_contract(text)
    assert errors == []
    assert intent is not None
    assert intent.in_reply_to == "pm-9"


def test_parse_peer_send_contract_no_block_returns_none_no_error():
    intent, errors = peer.parse_peer_send_contract("just normal output, no sentinel")
    assert intent is None
    assert errors == []


def test_parse_peer_send_contract_rejects_no_body():
    intent, errors = peer.parse_peer_send_contract(_block('{"to":"claude"}'))
    assert intent is None
    assert errors


def test_parse_peer_send_contract_rejects_empty_body():
    intent, errors = peer.parse_peer_send_contract(
        _block('{"to":"claude","body":""}'))
    assert intent is None
    assert errors


def test_parse_peer_send_contract_rejects_missing_to():
    intent, errors = peer.parse_peer_send_contract(_block('{"body":"hi"}'))
    assert intent is None
    assert errors


def test_parse_peer_send_contract_rejects_malformed_json():
    intent, errors = peer.parse_peer_send_contract(
        _block('{"to":"claude","body":'))
    assert intent is None
    assert errors


def test_parse_peer_send_contract_rejects_non_object_json():
    intent, errors = peer.parse_peer_send_contract(_block('["claude","hi"]'))
    assert intent is None
    assert errors


def test_parse_peer_send_contract_rejects_non_string_fields():
    intent, errors = peer.parse_peer_send_contract(
        _block('{"to":"claude","body":123}'))
    assert intent is None
    assert errors


def test_parse_peer_send_contract_rejects_multiple_blocks():
    text = (
        _block('{"to":"claude","body":"first"}')
        + "\nmiddle\n"
        + _block('{"to":"claude","body":"second"}')
    )
    intent, errors = peer.parse_peer_send_contract(text)
    # Fail-closed: must NOT first-valid-wins.
    assert intent is None
    assert errors


@pytest.mark.parametrize("bad_key", [
    "from", "from_run_id", "run_id", "group_id", "to_run_id",
    "source_adapter", "message_id", "hop_budget",
])
def test_parse_peer_send_contract_rejects_daemon_claimed_routing_keys(bad_key):
    payload = '{"to":"claude","body":"hi","%s":"x"}' % bad_key
    intent, errors = peer.parse_peer_send_contract(_block(payload))
    assert intent is None
    assert errors


def test_parse_peer_send_contract_rejects_unterminated_block():
    text = "<<<PEER_SEND>>>\n" + '{"to":"claude","body":"hi"}'
    intent, errors = peer.parse_peer_send_contract(text)
    assert intent is None
    # No END sentinel -> fail-closed with an explicit unterminated_block error.
    assert errors
    assert errors[0]["reason"] == "unterminated_block"


def test_parse_peer_send_contract_rejects_valid_block_plus_unterminated_second():
    # A valid, fully-delimited block followed by a dangling open sentinel.
    # Any unmatched <<<PEER_SEND>>> in complete terminal text must fail closed,
    # even though the first block on its own is valid.
    text = (
        _block('{"to":"claude","body":"first"}')
        + "\n"
        + '<<<PEER_SEND>>>\n{"to":"claude","body":"dangling"}'
    )
    intent, errors = peer.parse_peer_send_contract(text)
    assert intent is None
    assert errors
    assert errors[0]["reason"] == "unterminated_block"


# ---------------------------------------------------------------------------
# Pure authorization
# ---------------------------------------------------------------------------

def test_authorize_accepts_valid_message():
    g = _group()
    env = _envelope(g)
    result = peer.authorize_peer_message(g, env)
    assert result.status == "sent"


def test_authorize_rejects_reclaimed_group():
    g = _group(state="reclaimed")
    env = _envelope(g)
    result = peer.authorize_peer_message(g, env)
    assert result.status == "group_reclaimed"


def test_authorize_rejects_source_not_in_roster():
    g = _group()
    env = _envelope(g)
    env.from_run_id = "ghost-run"
    result = peer.authorize_peer_message(g, env)
    assert result.status == "denied"


def test_authorize_rejects_handle_run_id_mismatch():
    g = _group()
    env = _envelope(g)
    # from_run_id belongs to codex, but claims claude's handle.
    env.from_handle = "claude"
    result = peer.authorize_peer_message(g, env)
    assert result.status == "denied"


def test_authorize_rejects_non_author_source():
    members = [
        _member(em_id="em-1", run_id="run-codex", handle="codex",
                backend="codex", can_author=False),
        _member(em_id="em-2", run_id="run-claude", handle="claude",
                backend="claude-code"),
    ]
    g = _group(members=members)
    env = _envelope(g)
    result = peer.authorize_peer_message(g, env)
    assert result.status == "denied"


def test_authorize_unknown_target_handle():
    g = _group()
    env = _envelope(g, to_handle="claude")
    env.to_handle = "nobody"
    result = peer.authorize_peer_message(g, env)
    assert result.status == "unknown_peer"


def test_authorize_target_cannot_receive():
    members = [
        _member(em_id="em-1", run_id="run-codex", handle="codex", backend="codex"),
        _member(em_id="em-2", run_id="run-claude", handle="claude",
                backend="claude-code", can_receive=False),
    ]
    g = _group(members=members)
    env = _envelope(g)
    result = peer.authorize_peer_message(g, env)
    assert result.status == "denied"


def test_authorize_rejects_disallowed_pair():
    g = _group(policy=peer.GroupPolicy(allow_pairs={("claude", "codex")}))
    env = _envelope(g, from_handle="codex", to_handle="claude")
    result = peer.authorize_peer_message(g, env)
    assert result.status == "denied"


def test_authorize_allows_when_allow_pairs_none():
    g = _group(policy=peer.GroupPolicy(allow_pairs=None))
    env = _envelope(g)
    result = peer.authorize_peer_message(g, env)
    assert result.status == "sent"


def test_authorize_rejects_oversized_body():
    g = _group(policy=peer.GroupPolicy(
        max_message_bytes=4, allow_pairs=None))
    env = _envelope(g, body="too long body")
    result = peer.authorize_peer_message(g, env)
    assert result.status == "message_too_large"


def test_authorize_oversized_uses_byte_length_not_char_length():
    # 3 multibyte chars = >3 bytes; cap at 3 bytes should reject.
    g = _group(policy=peer.GroupPolicy(max_message_bytes=3, allow_pairs=None))
    env = _envelope(g, body="é€")  # >3 bytes encoded
    result = peer.authorize_peer_message(g, env)
    assert result.status == "message_too_large"


def test_authorize_rejects_hop_budget_zero():
    g = _group(policy=peer.GroupPolicy(allow_pairs=None))
    env = _envelope(g, hop_budget=0)
    result = peer.authorize_peer_message(g, env)
    assert result.status == "hop_exhausted"


def test_authorize_rejects_at_message_cap():
    g = _group(
        policy=peer.GroupPolicy(max_messages_per_group=2, allow_pairs=None),
        message_count=2,
    )
    env = _envelope(g)
    result = peer.authorize_peer_message(g, env)
    assert result.status == "rate_capped"


def test_authorize_order_group_active_checked_before_membership():
    # Reclaimed group with a bogus source: group state wins.
    g = _group(state="reclaimed")
    env = _envelope(g)
    env.from_run_id = "ghost"
    result = peer.authorize_peer_message(g, env)
    assert result.status == "group_reclaimed"


# ---------------------------------------------------------------------------
# Provenance banner
# ---------------------------------------------------------------------------

def test_provenance_banner_contains_core_fields():
    g = _group()
    env = _envelope(g, from_handle="codex", body="the body text",
                    message_id="pm-42")
    banner = peer.build_provenance_banner(env)
    assert "[peer message]" in banner
    assert "from: @codex" in banner
    assert g.group_id in banner
    assert "pm-42" in banner
    assert "the body text" in banner
    # Untrusted-collaboration warning must be present.
    assert "untrusted" in banner.lower()


def test_provenance_banner_includes_reply_to_only_when_present():
    g = _group()
    env_no_reply = _envelope(g, message_id="pm-1")
    assert "reply_to" not in peer.build_provenance_banner(env_no_reply)

    env_reply = _envelope(g, message_id="pm-2", in_reply_to="pm-1")
    banner = peer.build_provenance_banner(env_reply)
    assert "reply_to: pm-1" in banner


# ---------------------------------------------------------------------------
# Roster notice
# ---------------------------------------------------------------------------

def test_roster_notice_contains_own_and_peer_handles_and_provenance_rule():
    g = _group()
    me = g.roster_by_handle["codex"]
    notice = peer.build_roster_notice(g, me)
    assert g.group_id in notice
    assert "codex" in notice   # own handle
    assert "claude" in notice  # peer handle
    # Should communicate that peer messages are untrusted collaboration input.
    assert "untrusted" in notice.lower()


# ---------------------------------------------------------------------------
# Schema + author-contract rendering
# ---------------------------------------------------------------------------

def test_build_peer_send_schema_shape():
    schema = peer.build_peer_send_schema()
    assert schema.name == "peer_send"
    params = schema.parameters
    props = params["properties"]
    assert "to_handle" in props
    assert "body" in props
    assert "in_reply_to" in props
    assert set(params["required"]) == {"to_handle", "body"}


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
def test_build_peer_author_contract_includes_sentinels(backend):
    contract = peer.build_peer_author_contract(backend=backend)
    assert "<<<PEER_SEND>>>" in contract
    assert "<<<END>>>" in contract
    # Must instruct exactly-one-block discipline.
    assert "one" in contract.lower()
