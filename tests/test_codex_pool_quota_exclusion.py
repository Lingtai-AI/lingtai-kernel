"""Tests for quota-aware Codex account exclusion — Codex-core-owned domain.

Thin-wrapper refactor (spec v3): quota reading and exclusion decisions moved
from ``codex_pool.py`` (``select_codex_pool_auth`` /
``CodexPoolAllAccountsExhaustedError`` / ``_is_proven_exhausted``, all
removed) to Codex core in ``_register.py`` (``_build_quota_snapshot`` /
``_pool_create_chat``) plus the exclude-aware ``WeightedAccountSource`` in
``codex_account_source.py``.  The invariants this file used to test —
weights preserved among survivors, ``source_index`` anchored to the full
list, all-accounts-exhausted raises before any provider request, and no
caching (a since-reset/added account is selectable on the very next call) —
now live in ``tests/test_codex_account_source.py``:
``test_weights_preserved_among_survivors_after_exclusion``,
``test_source_index_anchors_to_full_list_not_filtered_position``,
``test_preflight_all_zero_raises_no_candidate``, and
``test_select_rereads_pool_file_fresh_every_call``/
``test_no_candidate_after_pool_fully_excluded_falls_back_to_legacy``
respectively.  This file asserts the removal is real rather than merely
claimed, so the redirect above stays honest.
"""

from lingtai.auth import codex_pool


def test_old_quota_exclusion_symbols_are_removed():
    """The pre-refactor public surface this file tested no longer exists —
    if any of these symbols reappear, the redirect claim above is stale and
    real test coverage belongs back in this file, not just a pointer."""
    assert not hasattr(codex_pool, "select_codex_pool_auth")
    assert not hasattr(codex_pool, "CodexPoolAllAccountsExhaustedError")
    assert not hasattr(codex_pool, "_is_proven_exhausted")
    assert not hasattr(codex_pool, "_codex_pool_failover_candidates")
