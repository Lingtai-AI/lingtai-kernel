"""Behavior locks and conformance for the Agent Presence Store slice.

Covers three surfaces:

* Pure Core policy (``is_agent`` / ``is_human`` / ``is_alive``) over typed
  observations, including the human-always-alive rule, the strict freshness
  threshold, malformed/absent = dead, and the NaN/±inf/future characterization.
* The production ``PosixAgentPresenceStoreAdapter`` against real agent
  directories — the tri-state manifest/heartbeat observation, the exact
  ``str(wall_seconds)``-no-newline heartbeat bytes, best-effort idempotent
  withdrawal, and the foreign-observation end-to-end path.
* Substitutability of the shared in-memory ``FakeAgentPresenceStore``.

These pin the exact semantics moved out of ``kernel.handshake`` and
``base_agent.lifecycle`` so the migration preserves observable behavior.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import pytest

from lingtai.kernel.agent_presence import (
    AgentPresenceStorePort,
    DEFAULT_LIVENESS_THRESHOLD_SECONDS,
    HeartbeatKind,
    HeartbeatObservation,
    ManifestKind,
    ManifestObservation,
    is_agent,
    is_alive,
    is_human,
    observe_alive,
)
from lingtai.adapters.posix.agent_presence import PosixAgentPresenceStoreAdapter
from tests._agent_presence_helpers import (
    FakeAgentPresenceStore,
    make_test_presence_store,
)


# ---------------------------------------------------------------------------
# Typed observation values
# ---------------------------------------------------------------------------


def test_manifest_observation_constructors():
    absent = ManifestObservation.absent()
    assert absent.kind is ManifestKind.ABSENT
    assert absent.admin_absent_or_null is False
    assert absent.data == {}

    malformed = ManifestObservation.malformed()
    assert malformed.kind is ManifestKind.MALFORMED

    valid_admin_null = ManifestObservation.valid({"agent_name": "x", "admin": None})
    assert valid_admin_null.kind is ManifestKind.VALID
    assert valid_admin_null.admin_absent_or_null is True

    valid_admin_missing = ManifestObservation.valid({"agent_name": "x"})
    assert valid_admin_missing.admin_absent_or_null is True

    valid_admin_present = ManifestObservation.valid({"admin": {"karma": True}})
    assert valid_admin_present.admin_absent_or_null is False


def test_heartbeat_observation_constructors():
    assert HeartbeatObservation.absent().kind is HeartbeatKind.ABSENT
    assert HeartbeatObservation.absent().is_present is False
    assert HeartbeatObservation.malformed().kind is HeartbeatKind.MALFORMED
    present = HeartbeatObservation.present(123.5)
    assert present.kind is HeartbeatKind.PRESENT
    assert present.is_present is True
    assert present.wall_seconds == 123.5


# ---------------------------------------------------------------------------
# Pure Core policy — is_agent / is_human
# ---------------------------------------------------------------------------


def test_is_agent_present_including_malformed():
    # A present manifest — valid OR malformed — counts as an agent (file
    # existence, preserving the historical is_agent semantics).
    assert is_agent(ManifestObservation.valid({"admin": {}})) is True
    assert is_agent(ManifestObservation.malformed()) is True
    assert is_agent(ManifestObservation.absent()) is False


def test_is_human_only_valid_admin_missing_or_null():
    assert is_human(ManifestObservation.valid({"admin": None})) is True
    assert is_human(ManifestObservation.valid({"agent_name": "x"})) is True  # missing
    assert is_human(ManifestObservation.valid({"admin": {"karma": True}})) is False
    # Missing/malformed manifests are not human.
    assert is_human(ManifestObservation.absent()) is False
    assert is_human(ManifestObservation.malformed()) is False


# ---------------------------------------------------------------------------
# Pure Core policy — is_alive
# ---------------------------------------------------------------------------


def test_is_alive_human_always_alive_without_heartbeat():
    human = ManifestObservation.valid({"admin": None})
    # No heartbeat at all, yet a human is always alive.
    assert is_alive(HeartbeatObservation.absent(), human, wall_now=1000.0) is True


def test_observe_alive_human_never_observes_heartbeat():
    class HumanStore(FakeAgentPresenceStore):
        def observe_heartbeat(self):
            raise AssertionError("human liveness must short-circuit before heartbeat")

    store = HumanStore(
        manifest=ManifestObservation.valid({"admin": None})
    )
    assert observe_alive(store, wall_now=1000.0) is True


def test_is_alive_non_human_strict_freshness():
    agent = ManifestObservation.valid({"admin": {"karma": True}})
    now = 1000.0
    # Strictly fresher than the default 2.0s threshold → alive.
    fresh = HeartbeatObservation.present(now - 1.0)
    assert is_alive(fresh, agent, wall_now=now) is True
    # Exactly at the threshold is NOT strictly less-than → dead.
    at_threshold = HeartbeatObservation.present(now - 2.0)
    assert is_alive(at_threshold, agent, wall_now=now) is False
    # Older than threshold → dead.
    stale = HeartbeatObservation.present(now - 5.0)
    assert is_alive(stale, agent, wall_now=now) is False


def test_is_alive_absent_or_malformed_heartbeat_is_dead():
    agent = ManifestObservation.valid({"admin": {}})
    assert is_alive(HeartbeatObservation.absent(), agent, wall_now=1000.0) is False
    assert is_alive(HeartbeatObservation.malformed(), agent, wall_now=1000.0) is False


def test_is_alive_custom_threshold():
    agent = ManifestObservation.valid({"admin": {}})
    now = 1000.0
    hb = HeartbeatObservation.present(now - 3.0)
    assert is_alive(hb, agent, wall_now=now, threshold=5.0) is True
    assert is_alive(hb, agent, wall_now=now, threshold=2.0) is False


def test_default_threshold_is_two_seconds():
    assert DEFAULT_LIVENESS_THRESHOLD_SECONDS == 2.0


def test_is_alive_future_and_nonfinite_flow_through_raw_compare():
    """Future / NaN / ±inf timestamps are NOT normalized or rejected.

    They flow through the raw ``wall_now - wall_seconds < threshold`` compare
    exactly as the former ``handshake.is_alive`` float comparison did.
    """
    agent = ManifestObservation.valid({"admin": {}})
    now = 1000.0
    # A slightly-future heartbeat: now - future is negative < threshold → alive.
    future = HeartbeatObservation.present(now + 100.0)
    assert is_alive(future, agent, wall_now=now) is True
    # +inf: now - inf = -inf < threshold → alive.
    assert is_alive(HeartbeatObservation.present(math.inf), agent, wall_now=now) is True
    # -inf: now - (-inf) = +inf < threshold is False → dead.
    assert is_alive(HeartbeatObservation.present(-math.inf), agent, wall_now=now) is False
    # NaN: any comparison with NaN is False → dead.
    assert is_alive(HeartbeatObservation.present(math.nan), agent, wall_now=now) is False


# ---------------------------------------------------------------------------
# Production POSIX adapter — observation
# ---------------------------------------------------------------------------


def test_adapter_observe_manifest_absent(tmp_path):
    obs = PosixAgentPresenceStoreAdapter(tmp_path).observe_manifest()
    assert obs.kind is ManifestKind.ABSENT
    assert is_agent(obs) is False


def test_adapter_observe_manifest_valid(tmp_path, make_agent_dir):
    d = make_agent_dir(
        tmp_path, name="", heartbeat=False,
        manifest={"agent_name": "t", "admin": {"karma": True}},
    )
    obs = PosixAgentPresenceStoreAdapter(d).observe_manifest()
    assert obs.kind is ManifestKind.VALID
    assert obs.admin_absent_or_null is False
    assert obs.data["agent_name"] == "t"
    assert is_agent(obs) is True
    assert is_human(obs) is False


def test_adapter_observe_manifest_human(tmp_path, make_agent_dir):
    d = make_agent_dir(tmp_path, name="", human=True)
    obs = PosixAgentPresenceStoreAdapter(d).observe_manifest()
    assert obs.kind is ManifestKind.VALID
    assert obs.admin_absent_or_null is True
    assert is_human(obs) is True


def test_adapter_observe_manifest_malformed_still_agent(tmp_path):
    # A present-but-unparseable .agent.json is malformed, but still an agent.
    (tmp_path / ".agent.json").write_text("{not valid json", encoding="utf-8")
    obs = PosixAgentPresenceStoreAdapter(tmp_path).observe_manifest()
    assert obs.kind is ManifestKind.MALFORMED
    assert is_agent(obs) is True
    assert is_human(obs) is False


def test_adapter_observe_manifest_non_object_json_is_malformed(tmp_path):
    # Valid JSON that is not an object (a bare number): present but not a usable
    # manifest — is_agent True (file exists), is_human False, and no crash.
    (tmp_path / ".agent.json").write_text("42", encoding="utf-8")
    obs = PosixAgentPresenceStoreAdapter(tmp_path).observe_manifest()
    assert obs.kind is ManifestKind.MALFORMED
    assert is_agent(obs) is True
    assert is_human(obs) is False


def test_adapter_observe_heartbeat_absent(tmp_path):
    obs = PosixAgentPresenceStoreAdapter(tmp_path).observe_heartbeat()
    assert obs.kind is HeartbeatKind.ABSENT


def test_adapter_observe_heartbeat_present(tmp_path):
    (tmp_path / ".agent.heartbeat").write_text("1234567890.5", encoding="utf-8")
    obs = PosixAgentPresenceStoreAdapter(tmp_path).observe_heartbeat()
    assert obs.kind is HeartbeatKind.PRESENT
    assert obs.wall_seconds == 1234567890.5


def test_adapter_observe_heartbeat_malformed(tmp_path):
    (tmp_path / ".agent.heartbeat").write_text("not-a-float", encoding="utf-8")
    obs = PosixAgentPresenceStoreAdapter(tmp_path).observe_heartbeat()
    assert obs.kind is HeartbeatKind.MALFORMED


def test_adapter_observe_heartbeat_strips_whitespace(tmp_path):
    # The historical reader did float(text.strip()); a trailing newline parses.
    (tmp_path / ".agent.heartbeat").write_text("100.25\n", encoding="utf-8")
    obs = PosixAgentPresenceStoreAdapter(tmp_path).observe_heartbeat()
    assert obs.kind is HeartbeatKind.PRESENT
    assert obs.wall_seconds == 100.25


# ---------------------------------------------------------------------------
# Production POSIX adapter — own presence
# ---------------------------------------------------------------------------


def test_adapter_publish_heartbeat_exact_bytes(tmp_path):
    """Heartbeat bytes are exactly ``str(wall_seconds)`` with no newline."""
    value = 1719878400.123456
    PosixAgentPresenceStoreAdapter(tmp_path).publish_heartbeat(value)
    raw = (tmp_path / ".agent.heartbeat").read_bytes()
    assert raw == str(value).encode("utf-8")
    assert not raw.endswith(b"\n")


def test_adapter_publish_then_observe_roundtrip(tmp_path):
    store = PosixAgentPresenceStoreAdapter(tmp_path)
    value = 1719878400.5
    store.publish_heartbeat(value)
    obs = store.observe_heartbeat()
    assert obs.kind is HeartbeatKind.PRESENT
    assert obs.wall_seconds == value


def test_adapter_withdraw_heartbeat_removes_file(tmp_path):
    store = PosixAgentPresenceStoreAdapter(tmp_path)
    store.publish_heartbeat(100.0)
    assert (tmp_path / ".agent.heartbeat").is_file()
    store.withdraw_heartbeat()
    assert not (tmp_path / ".agent.heartbeat").exists()


def test_adapter_withdraw_heartbeat_idempotent(tmp_path):
    # Best-effort/idempotent: withdrawing when nothing is published is a no-op.
    store = PosixAgentPresenceStoreAdapter(tmp_path)
    store.withdraw_heartbeat()  # no file present
    store.withdraw_heartbeat()
    assert not (tmp_path / ".agent.heartbeat").exists()


def test_adapter_publish_heartbeat_swallows_write_error(tmp_path):
    # Publishing into a nonexistent parent directory raises OSError inside the
    # adapter, which is swallowed (best-effort), matching the historical tick.
    missing = tmp_path / "does_not_exist"
    store = PosixAgentPresenceStoreAdapter(missing)
    store.publish_heartbeat(100.0)  # must not raise
    assert not (missing / ".agent.heartbeat").exists()


# ---------------------------------------------------------------------------
# Foreign observation end-to-end (adapter + Core policy) — the migrated path
# ---------------------------------------------------------------------------


def test_foreign_observation_is_alive_fresh(tmp_path, make_agent_dir):
    d = make_agent_dir(
        tmp_path, name="", heartbeat=True,
        manifest={"agent_name": "t", "admin": {}},  # non-human
    )
    store = PosixAgentPresenceStoreAdapter(d)
    assert observe_alive(store, wall_now=time.time()) is True


def test_foreign_observation_is_alive_stale(tmp_path, make_agent_dir):
    d = make_agent_dir(
        tmp_path, name="", heartbeat=True, heartbeat_ts=time.time() - 5.0,
        manifest={"agent_name": "t", "admin": {}},
    )
    store = PosixAgentPresenceStoreAdapter(d)
    assert observe_alive(store, wall_now=time.time()) is False


def test_foreign_observation_human_always_alive(tmp_path, make_agent_dir):
    d = make_agent_dir(tmp_path, name="", human=True)  # admin=null, no heartbeat
    store = PosixAgentPresenceStoreAdapter(d)
    assert observe_alive(store, wall_now=time.time()) is True


# ---------------------------------------------------------------------------
# Substitutability — the shared in-memory fake conforms to the Port
# ---------------------------------------------------------------------------


def test_fake_is_a_port():
    assert isinstance(make_test_presence_store(), AgentPresenceStorePort)


def test_fake_publish_withdraw_roundtrip():
    fake = FakeAgentPresenceStore()
    assert fake.observe_heartbeat().kind is HeartbeatKind.ABSENT
    fake.publish_heartbeat(42.0)
    obs = fake.observe_heartbeat()
    assert obs.kind is HeartbeatKind.PRESENT
    assert obs.wall_seconds == 42.0
    assert fake.published_values == [42.0]
    fake.withdraw_heartbeat()
    assert fake.observe_heartbeat().kind is HeartbeatKind.ABSENT
    assert fake.withdraw_calls == 1


def test_fake_default_manifest_absent():
    assert make_test_presence_store().observe_manifest().kind is ManifestKind.ABSENT


def test_fake_configurable_manifest():
    fake = make_test_presence_store(
        manifest=ManifestObservation.valid({"admin": None})
    )
    assert is_human(fake.observe_manifest()) is True


def test_port_has_exactly_four_operations():
    # No fifth operation family: the abstract surface is exactly the four
    # capability operations.
    abstract = AgentPresenceStorePort.__abstractmethods__
    assert abstract == frozenset(
        {
            "observe_manifest",
            "observe_heartbeat",
            "publish_heartbeat",
            "withdraw_heartbeat",
        }
    )


def test_port_and_domain_are_technology_neutral():
    """The Port module exposes no Path/file/JSON/POSIX/time/threading vocabulary."""
    import lingtai.kernel.agent_presence as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    for banned in (
        "import os",
        "import json",
        "from pathlib",
        "import pathlib",
        "import time",
        "import threading",
        ".agent.json",
        ".agent.heartbeat",
        "lingtai.adapters",
    ):
        assert banned not in source, banned
