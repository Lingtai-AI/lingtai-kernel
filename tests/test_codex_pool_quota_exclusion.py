"""Historical removal guard for the old Codex pool selector surface.

Pool parsing remains in ``codex_pool.py``; source arithmetic/exclusion lives in
``codex_account_source.py`` and per-request quota/terminal failure handling lives
in the one native ``CodexOpenAIAdapter``.  Current behavior is covered by
``test_codex_account_source.py`` and ``test_codex_native_multiaccount.py``.  This
small guard prevents the removed pool-owned selector/failover API from silently
returning.
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
