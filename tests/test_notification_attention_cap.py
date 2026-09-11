"""Tests for the 10k character cap on the attention notification lane.

The attention lane (``_meta.agent_meta.notifications.attention``) is re-stamped
on every eligible tool batch and on every IDLE/ASLEEP synthesized pair, so a
busy hub agent (many unread emails plus several IM lanes) could grow context
fast and pay a large per-call cache miss.  The cap shares the
``LINGTAI_NOTIFICATION_MAX_CHARS`` env bar with the persistent lane (single
upper limit across all notification channels, Jason 2026-08-13); over the cap
it spills the full attention lane to ``logs/notification-attention-overflow-<digest>.json``
(content-addressed, so an unchanged oversized lane reuses the same file) and
returns a compacted copy with an ``overflow`` marker that points the agent at
the file (or the producer tool when the spill failed).  The terminal routing
stub is capped strictly and preserves ``message_ids`` (the stable IM routing
identifier) for as long as any routing remains.  Round 3 (P1-3): the
configured cap is clamped to [2048, 10,000] — values below 2048 clamp UP to
2048 so the terminal marker-only recovery envelope always fits, and a final
guard strips a pathologically long absolute spill path (``path_omitted``) so
the returned envelope ALWAYS satisfies the cap.
"""
from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import lingtai.kernel.meta_block as meta_block

MAX = meta_block.NOTIFICATION_ATTENTION_MAX_CHARS
ENV = meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_ENV


def _cap_agent(tmp_path):
    return SimpleNamespace(_working_dir=str(tmp_path))


def _attention_chars(attention: dict) -> int:
    return len(json.dumps(attention, ensure_ascii=False, sort_keys=True))


def _spill_files(tmp_path):
    return sorted(
        (tmp_path / "logs").glob("notification-attention-overflow-*.json")
    )


def _telegram_message(message_id: int, *, text: str) -> dict:
    return {
        "id": f"main:123:{message_id}",
        "direction": "incoming",
        "sender": "Jason",
        "text": text,
        "text_truncated": False,
    }


def _attention_payload(messages: list[dict]) -> dict:
    # Raw attention lane shape: producer source -> payload (as built by
    # ``build_notification_payload``).
    return {
        "mcp.telegram": {
            "data": {
                "count": len(messages),
                "previews": [
                    {
                        "from": "Jason",
                        "subject": "telegram message from Jason via main",
                        "message_ref": messages[-1]["id"],
                        "recent_messages": messages,
                    }
                ],
            }
        }
    }


def test_small_attention_unchanged_and_no_spill(tmp_path):
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text=f"message {i}") for i in range(1, 4)]
    attention = _attention_payload(messages)

    capped = meta_block._cap_notification_attention(agent, attention)

    assert "overflow" not in capped
    assert capped == attention
    assert _attention_chars(capped) <= MAX
    assert not (tmp_path / "logs").exists() or _spill_files(tmp_path) == []


def test_large_attention_spills_and_compacts(tmp_path):
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]
    attention = _attention_payload(messages)
    original = copy.deepcopy(attention)

    capped = meta_block._cap_notification_attention(agent, attention)

    assert _attention_chars(capped) <= MAX
    overflow = capped["overflow"]
    assert overflow["truncated"] is True
    assert overflow["full_chars"] > MAX
    spill_files = _spill_files(tmp_path)
    assert len(spill_files) == 1
    assert overflow["path"] == str(spill_files[0])

    # The spill file holds the FULL original attention lane, not the compacted one.
    spilled = json.loads(spill_files[0].read_text(encoding="utf-8"))
    spilled_messages = spilled["mcp.telegram"]["data"]["previews"][0]["recent_messages"]
    assert spilled_messages[0]["text"] == "T" * 3000

    # Structural/routing fields survive; heavy text is capped with the marker.
    preview = capped["mcp.telegram"]["data"]["previews"][0]
    assert preview["from"] == "Jason"
    assert preview["message_ref"] == "main:123:40"
    for message in preview["recent_messages"]:
        assert len(message["text"]) < 3000
        assert message["direction"] == "incoming"
        assert message["sender"] == "Jason"
    assert spill_files[0].name in capped["comment"]

    # The caller's attention dict is never mutated.
    assert attention == original


def test_compacted_attention_messages_get_truthful_truncated_flag(tmp_path):
    """The attention cap must never blank a heavy field while leaving a stale
    ``text_truncated: false`` — a shortened record has to say so."""
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]
    attention = _attention_payload(messages)

    capped = meta_block._cap_notification_attention(agent, attention)

    preview = capped["mcp.telegram"]["data"]["previews"][0]
    shortened = [m for m in preview["recent_messages"] if len(m["text"]) < 3000]
    assert shortened, "sanity: the cap must have actually shortened something"
    for message in shortened:
        assert message["text_truncated"] is True


def test_attention_overflow_comment_points_at_the_spill_file(tmp_path):
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]

    capped = meta_block._cap_notification_attention(
        agent, _attention_payload(messages)
    )

    spill_path = capped["overflow"]["path"]
    assert spill_path in capped["comment"]


def test_attention_cap_shares_persistent_env_bar(tmp_path, monkeypatch):
    """One env var enforces the upper limit across both notification lanes."""
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 500) for i in range(1, 41)]
    attention = _attention_payload(messages)
    # Tighten the shared env bar below the default; both lanes honor it (the
    # attention lane clamps to the 2048 floor, so assert against the effective
    # cap, not the raw configured value).
    monkeypatch.setenv(ENV, "1500")
    effective = meta_block._notification_attention_max_chars()
    assert effective == meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_MIN

    capped = meta_block._cap_notification_attention(agent, attention)
    assert _attention_chars(capped) <= effective
    assert capped.get("overflow", {}).get("truncated") is True

    # The persistent lane honors the same tightened bar.
    persistent_agent = SimpleNamespace(
        _working_dir=str(tmp_path),
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )
    raw_payload = {
        "notifications": {
            "email": {
                "data": {
                    "count": 1,
                    "email_ids": ["email-1"],
                    "emails": [
                        {
                            "id": "email-1",
                            "from": "human",
                            "subject": "Subject 1",
                            "message": "E" * 5000,
                        }
                    ],
                }
            }
        }
    }
    persistent = meta_block.build_notification_persistent_payload(
        persistent_agent, raw_payload
    )
    assert persistent["notification_persistent"]["overflow"]["truncated"] is True


def test_attention_spill_failure_uses_producer_guidance(tmp_path):
    """Without a workdir the compacted lane still carries a recovery hint."""
    agent = SimpleNamespace(_working_dir=None)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]

    capped = meta_block._cap_notification_attention(
        agent, _attention_payload(messages)
    )

    assert capped["overflow"]["truncated"] is True
    assert capped["overflow"].get("spill_failed") is True
    assert "producer tool" in capped["comment"]
    assert _attention_chars(capped) <= MAX


def test_attention_cap_honors_ceiling(tmp_path, monkeypatch):
    """Env values above the 10k ceiling are clamped, not silently disabled."""
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]
    monkeypatch.setenv(ENV, "999999")

    capped = meta_block._cap_notification_attention(
        agent, _attention_payload(messages)
    )
    # Over the clamped ceiling the payload is still capped.
    assert _attention_chars(capped) <= meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_CEILING
    assert capped.get("overflow", {}).get("truncated") is True


def test_build_synthetic_meta_envelope_caps_attention(tmp_path):
    """IDLE path: the synthesized pair's attention axis is capped."""
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]

    payload = meta_block.build_notification_payload(_attention_payload(messages))
    envelope = meta_block.build_synthetic_meta_envelope(
        agent, payload, call_id="call_attention_cap_test"
    )

    attention = envelope["agent_meta"]["notifications"]["attention"]
    assert attention.get("overflow", {}).get("truncated") is True
    assert _attention_chars(attention) <= MAX
    assert _spill_files(tmp_path)


# ---------------------------------------------------------------------------
# P1-1: the terminal routing stub is a STRICT cap and preserves message_ids
# ---------------------------------------------------------------------------


def _real_sanitized_telegram_attention() -> tuple[dict, str]:
    """Real Telegram attention after ``sanitize_telegram_notification_after_persistent``.

    Mirrors the persistent-cap test's producer payload; the sanitizer replaces
    the ephemeral ``mcp.telegram`` block with ``data.message_ids`` only, which
    is the stable IM routing identifier the routing stub must preserve.
    """
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]
    payload = {
        "notifications": {
            "mcp.telegram": {
                "data": {
                    "count": 1,
                    "previews": [
                        {
                            "from": "Jason",
                            "subject": "telegram message from Jason via main",
                            "platform": "telegram",
                            "conversation_ref": "main:123",
                            "message_ref": messages[-1]["id"],
                            "recent_messages": messages,
                            "latest_incoming": messages[-1],
                        }
                    ],
                }
            }
        }
    }
    meta_block.sanitize_telegram_notification_after_persistent(payload)
    return payload["notifications"], messages[-1]["id"]


def test_real_sanitized_telegram_low_cap_clamps_to_floor_with_long_workdir(
    tmp_path, monkeypatch
):
    """P1-3: a REPRESENTATIVE long workdir cannot break the cap under cap 200.

    The committed test faked a short workdir so the marker path stayed small
    under a 200-char cap.  With the 2048 floor, configured cap 200 is clamped
    UP to the floor, so the real sanitized Telegram lane
    (``message_ids=["main:123:..."]``, ~228 chars) fits WITHOUT compaction
    even with a realistic long absolute workdir (>= 120 chars) — the routing
    ids survive and the serialized envelope stays at or under the effective
    cap.
    """
    workdir = str(tmp_path / ("d" * 60) / ("e" * 60))
    assert len(workdir) >= 120
    agent = SimpleNamespace(_working_dir=workdir)
    attention, message_id = _real_sanitized_telegram_attention()
    monkeypatch.setenv(ENV, "200")

    effective = meta_block._notification_attention_max_chars()
    assert effective == meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_MIN

    capped = meta_block._cap_notification_attention(agent, copy.deepcopy(attention))

    serialized = json.dumps(capped, ensure_ascii=False, sort_keys=True, default=str)
    assert len(serialized) <= effective
    assert capped["mcp.telegram"]["data"]["message_ids"] == [message_id]


def test_real_sanitized_telegram_pathological_cap_degrades_to_marker_only(
    tmp_path, monkeypatch
):
    """P1-3: env cap 1 clamps UP to the 2048 floor, still a strict bound.

    The old test asserted a marker-only envelope under 250 chars against cap
    1; with the floor the effective cap is 2048, the sanitized lane fits under
    it without compaction, and the envelope is asserted against the EFFECTIVE
    cap (never ``< 250``).
    """
    agent = _cap_agent(tmp_path)
    attention, _ = _real_sanitized_telegram_attention()
    monkeypatch.setenv(ENV, "1")

    effective = meta_block._notification_attention_max_chars()
    assert effective == meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_MIN

    capped = meta_block._cap_notification_attention(agent, copy.deepcopy(attention))

    serialized = json.dumps(capped, ensure_ascii=False, sort_keys=True, default=str)
    assert len(serialized) <= effective
    assert capped["mcp.telegram"]["data"]["message_ids"]


@pytest.mark.parametrize("configured", [1, 2, 50, 200, 300, 500, 1024])
def test_low_configured_caps_clamp_to_floor_and_keep_sanitized_lane(
    tmp_path, monkeypatch, configured
):
    """P1-3: every low configured cap maps to ``max(value, 2048)``.

    For each low config the effective cap is the 2048 floor and the real
    sanitizer-shaped Telegram lane stays at or under it with ``message_ids``
    retained (2048 comfortably fits the ~228-char lane, so no compaction is
    needed and no routing id is ever dropped).
    """
    agent = _cap_agent(tmp_path)
    attention, message_id = _real_sanitized_telegram_attention()
    monkeypatch.setenv(ENV, str(configured))

    effective = meta_block._notification_attention_max_chars()
    assert effective == max(configured, meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_MIN)

    capped = meta_block._cap_notification_attention(agent, copy.deepcopy(attention))

    serialized = json.dumps(capped, ensure_ascii=False, sort_keys=True, default=str)
    assert len(serialized) <= effective
    assert capped["mcp.telegram"]["data"]["message_ids"] == [message_id]


def test_marker_only_envelope_strips_pathologically_long_spill_path(
    tmp_path, monkeypatch
):
    """P1-3: a pathologically long absolute spill path never breaks the cap.

    The marker-only envelope carries the absolute spill path; when even that
    envelope exceeds the cap (an absolute path long enough to dominate the
    2048 floor), the final guard strips the path (``path=None``,
    ``path_omitted=True``), records the exact spill basename, and returns a
    compact envelope that ALWAYS satisfies ``len(json.dumps(...)) <= max_chars``.  The
    spill file remains on disk under the deterministic content-addressed name.
    """
    attention, _ = _real_sanitized_telegram_attention()
    monkeypatch.setenv(ENV, "1")
    max_chars = meta_block._notification_attention_max_chars()
    assert max_chars == meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_MIN

    # White-box: a marker whose absolute path cannot fit the floor.  (Real
    # near-PATH_MAX workdirs are platform-limited; the guard is exercised
    # deterministically with a synthetic path.)
    long_path = "/" + "x" * 3000
    spill_name = f"{meta_block.NOTIFICATION_ATTENTION_OVERFLOW_FILE_PREFIX}{meta_block._attention_spill_digest8(copy.deepcopy(attention))}.json"
    marker = {"path": f"{long_path}/{spill_name}", "full_chars": 228, "truncated": True}
    result = meta_block._drop_notification_attention_records(
        copy.deepcopy(attention), marker, "recovery comment", max_chars
    )

    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    assert len(serialized) <= max_chars
    overflow = result["overflow"]
    assert overflow["path"] is None
    assert overflow["path_omitted"] is True
    # The exact spill basename (content-addressed, including any suffix) is
    # preserved so the recovery comment points at the real file on disk.
    assert overflow["spill_file"] == spill_name
    assert spill_name in result["comment"]


def test_path_omitted_recovery_points_at_suffixed_spill_file(tmp_path, monkeypatch):
    """P1-3b: path-omitted recovery names the ACTUAL ``-N`` spill file.

    When the unsuffixed content-addressed name is already occupied by a
    different payload, the allocator correctly writes ``<digest>-N.json``.  If
    the absolute path is then omitted by the terminal guard, the marker and
    comment must point at the SUFFIXED file, not the unsuffixed (wrong)
    payload.
    """
    # A large payload that exceeds the (floored) cap so the real spill path
    # runs and must allocate a suffix because the unsuffixed name is taken.
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]
    attention = _attention_payload(messages)
    digest = meta_block._attention_spill_digest8(copy.deepcopy(attention))
    base_name = f"{meta_block.NOTIFICATION_ATTENTION_OVERFLOW_FILE_PREFIX}{digest}.json"
    suffixed_name = f"{meta_block.NOTIFICATION_ATTENTION_OVERFLOW_FILE_PREFIX}{digest}-1.json"

    # Occupy the unsuffixed name with a DIFFERENT payload so a real spill of
    # *attention* must allocate the -1 suffix.
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    other = {"mcp.telegram": {"data": {"message_ids": ["wrong-other-payload"]}}}
    (logs_dir / base_name).write_text(
        json.dumps(other, ensure_ascii=False), encoding="utf-8"
    )

    agent = _cap_agent(tmp_path)
    monkeypatch.setenv(ENV, "1")
    capped = meta_block._cap_notification_attention(agent, copy.deepcopy(attention))

    # With a short workdir the absolute path fits and is kept; the spill must
    # have used the -1 suffix (unsuffixed occupied by different content).
    overflow = capped["overflow"]
    assert overflow["path"] is not None
    assert Path(overflow["path"]).name == suffixed_name

    # Now force the path-omitted guard on the exact same suffixed path and
    # assert the recovery comment points at the suffixed file, not unsuffixed.
    long_path = "/" + "x" * 3000
    marker = {
        "path": f"{long_path}/{suffixed_name}",
        "full_chars": 228,
        "truncated": True,
    }
    result = meta_block._drop_notification_attention_records(
        copy.deepcopy(attention), marker, "recovery comment",
        meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_MIN,
    )
    overflow2 = result["overflow"]
    assert overflow2["path"] is None
    assert overflow2["path_omitted"] is True
    assert overflow2["spill_file"] == suffixed_name
    assert suffixed_name in result["comment"]
    assert base_name not in result["comment"]
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    assert len(serialized) <= meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_MIN


def test_many_source_structural_overflow_stays_under_default_cap(tmp_path):
    """P1-1: 180+ structural sources never exceed the default 10k cap."""
    agent = _cap_agent(tmp_path)
    attention: dict = {}
    for source in range(180):
        attention[f"mcp.source{source}"] = {
            "data": {
                "count": 9999,
                "email_ids": [f"email-{source}-{i}" for i in range(50)],
                "message_ref": f"ref-{source}-99",
                "event_ids": [f"ev-{source}-{i}" for i in range(30)],
                "ref": f"r-{source}",
                "ref_id": f"rid-{source}",
                "message_ids": [f"msg-{source}-{i}" for i in range(20)],
            }
        }

    capped = meta_block._cap_notification_attention(agent, attention)

    serialized = json.dumps(capped, ensure_ascii=False, sort_keys=True, default=str)
    assert len(serialized) <= MAX
    assert capped.get("overflow", {}).get("truncated") is True


def test_adversarial_structural_values_stay_under_cap(tmp_path, monkeypatch):
    """P1-1: huge counts and huge id lists degrade to a bounded head."""
    agent = _cap_agent(tmp_path)
    attention = {
        "mcp.telegram": {
            "data": {
                "count": 10**30,
                "email_ids": [f"email-{i}" for i in range(10_000)],
                "message_ids": [f"main:123:{i}" for i in range(10_000)],
                "event_ids": [f"ev-{i}" for i in range(10_000)],
                "ref": "ref-" * 5000,
                "ref_id": "rid-" * 5000,
                "message_ref": "mref-" * 5000,
            }
        }
    }
    monkeypatch.setenv(ENV, "2000")
    effective = meta_block._notification_attention_max_chars()
    assert effective == meta_block.NOTIFICATION_ATTENTION_MAX_CHARS_MIN

    capped = meta_block._cap_notification_attention(agent, attention)

    serialized = json.dumps(capped, ensure_ascii=False, sort_keys=True, default=str)
    assert len(serialized) <= effective
    assert capped["overflow"]["truncated"] is True
    # The id lists degrade to the bounded head, keeping the routing identifier.
    assert (
        len(capped["mcp.telegram"]["data"]["message_ids"])
        == meta_block.NOTIFICATION_ATTENTION_ROUTING_ID_HEAD
    )


# ---------------------------------------------------------------------------
# P1-2: content-addressed, exclusive attention spill
# ---------------------------------------------------------------------------


def test_unchanged_attention_reuses_single_spill_file(tmp_path):
    """P1-2: an unchanged oversized lane spills ONCE with a stable marker path.

    The old code re-spilled on every batch (timestamp + suffix loop, -100
    overwritten); the content-addressed name reuses the same file and the two
    markers point at the identical recovery handle.
    """
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]
    attention = _attention_payload(messages)

    first = meta_block._cap_notification_attention(agent, copy.deepcopy(attention))
    second = meta_block._cap_notification_attention(agent, copy.deepcopy(attention))

    spill_files = _spill_files(tmp_path)
    assert len(spill_files) == 1
    assert first["overflow"]["path"] == second["overflow"]["path"]
    assert first["overflow"]["path"] == str(spill_files[0])
    spilled = json.loads(spill_files[0].read_text(encoding="utf-8"))
    spilled_messages = spilled["mcp.telegram"]["data"]["previews"][0]["recent_messages"]
    assert spilled_messages[0]["text"] == "T" * 3000


def _preoccupied_spill_name(digest: str, suffix: int | None) -> str:
    if suffix is None:
        return f"notification-attention-overflow-{digest}.json"
    return f"notification-attention-overflow-{digest}-{suffix}.json"


def test_spill_name_exhaustion_never_overwrites_existing_file(tmp_path, monkeypatch):
    """P1-2: when every exclusive name is taken, spill fails without overwriting.

    Pre-occupies the content-addressed name and all 100 suffix fallbacks with a
    DIFFERENT payload; the spill must fail (producer tool guidance) and every
    existing recovery handle must keep its original bytes.
    """
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]
    attention = _attention_payload(messages)
    canonical = json.dumps(attention, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    occupied: list[Path] = []
    names = [_preoccupied_spill_name(digest, None)] + [
        _preoccupied_spill_name(digest, suffix) for suffix in range(1, 101)
    ]
    for name in names:
        path = logs_dir / name
        path.write_text(json.dumps({"occupied": True}), encoding="utf-8")
        occupied.append(path)

    capped = meta_block._cap_notification_attention(agent, copy.deepcopy(attention))

    assert capped["overflow"].get("spill_failed") is True
    assert "producer tool" in capped["comment"]
    for path in occupied:
        assert json.loads(path.read_text(encoding="utf-8")) == {"occupied": True}


def test_concurrent_writers_same_payload_single_file(tmp_path):
    """P1-2: a two-writer race on the same payload yields ONE intact file."""
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 3000) for i in range(1, 41)]
    attention = _attention_payload(messages)

    def spill_once():
        out = meta_block._cap_notification_attention(agent, copy.deepcopy(attention))
        return out["overflow"]["path"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: spill_once(), range(8)))

    spill_files = _spill_files(tmp_path)
    assert len(spill_files) == 1
    assert len(set(paths)) == 1
    assert paths[0] == str(spill_files[0])
    spilled = json.loads(spill_files[0].read_text(encoding="utf-8"))
    assert spilled["mcp.telegram"]["data"]["previews"][0]["recent_messages"][0]["text"] == "T" * 3000


def test_concurrent_writers_different_payloads_never_lose(tmp_path):
    """P1-2: two different payloads never collide and never overwrite."""
    agent = _cap_agent(tmp_path)
    messages_a = [_telegram_message(i, text="A" * 3000) for i in range(1, 41)]
    messages_b = [_telegram_message(i, text="B" * 3000) for i in range(1, 41)]

    out_a = meta_block._cap_notification_attention(
        agent, copy.deepcopy(_attention_payload(messages_a))
    )
    out_b = meta_block._cap_notification_attention(
        agent, copy.deepcopy(_attention_payload(messages_b))
    )

    assert out_a["overflow"]["path"] != out_b["overflow"]["path"]
    spill_files = _spill_files(tmp_path)
    assert len(spill_files) == 2
    markers = {
        json.loads(path.read_text(encoding="utf-8"))["mcp.telegram"]["data"]["previews"][0][
            "recent_messages"
        ][0]["text"][0]
        for path in spill_files
    }
    assert markers == {"A", "B"}

def test_non_ascii_attention_measured_with_provider_escaping(tmp_path, monkeypatch):
    """P1-1: multilingual attention is measured like the provider serializes it.

    Anthropic/OpenAI Chat/OpenAI Responses/Gemini canonical converters
    re-serialize projected ToolResultBlock dictionaries with default ASCII
    escaping (``json.dumps(..., default=str)``), so a CJK body that is small
    under ``ensure_ascii=False`` is far larger on the wire.  The cap ruler must
    measure the provider-visible representation: a 2000-CJK-char attention
    body (2,035 chars unescaped) must spill instead of silently staying under
    the 10,000 cap at 12,035 escaped chars.
    """
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(1, text="中" * 2000)]
    attention = _attention_payload(messages)

    # The ruler must equal the provider-visible serialization (default ASCII
    # escaping), not the smaller UTF-8-preserving one.
    ruler_chars = meta_block._notification_attention_envelope_chars(attention)
    provider_chars = len(json.dumps(attention, default=str))
    assert ruler_chars == provider_chars
    assert ruler_chars > MAX  # 12,035 > 10,000: would previously NOT spill

    capped = meta_block._cap_notification_attention(agent, attention)
    overflow = capped["overflow"]
    assert overflow["truncated"] is True
    assert overflow["full_chars"] == ruler_chars
    # The returned compacted attention block itself satisfies the promised cap
    # under provider-visible serialization (no later wire expansion can push it
    # back over the ceiling).
    assert len(json.dumps(capped, default=str)) <= MAX
    assert len(_spill_files(tmp_path)) == 1
