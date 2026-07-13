"""Tests for lingtai.kernel.handshake — now pure address resolution only.

Agent presence/liveness (``is_agent`` / ``is_alive`` / ``is_human`` / manifest
observation) moved to ``lingtai.kernel.agent_presence`` and its POSIX adapter;
those behavior locks live in ``tests/test_agent_presence.py``. ``handshake`` now
owns only ``resolve_address``.
"""
from __future__ import annotations


def test_resolve_address_relative(tmp_path):
    """Relative name resolves to base_dir / name."""
    from lingtai.kernel.handshake import resolve_address
    result = resolve_address("本我", tmp_path)
    assert result == tmp_path / "本我"


def test_resolve_address_absolute(tmp_path):
    """Absolute path is returned as-is."""
    from lingtai.kernel.handshake import resolve_address
    abs_path = tmp_path / "other" / ".lingtai" / "agent"
    result = resolve_address(str(abs_path), tmp_path)
    assert result == abs_path


def test_resolve_address_path_object(tmp_path):
    """Path objects work too."""
    from lingtai.kernel.handshake import resolve_address
    result = resolve_address(tmp_path / "human", tmp_path)
    assert result == tmp_path / "human"


def test_handshake_no_longer_exposes_presence_functions():
    """Presence/liveness moved to the agent_presence Port + adapter.

    The former ``is_agent`` / ``is_alive`` / ``is_human`` / ``manifest``
    functions are removed from ``handshake`` with no shim or dual route.
    """
    import lingtai.kernel.handshake as handshake

    for removed in ("is_agent", "is_alive", "is_human", "manifest"):
        assert not hasattr(handshake, removed), removed
