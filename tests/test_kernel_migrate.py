"""Tests for the kernel-side preset library migration system.

The runner is in `lingtai.kernel.migrate.migrate`; the first migration
(m001) relocates `manifest.context_limit` into `manifest.llm.context_limit`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lingtai.kernel.migrate import (
    AGENT_CURRENT_VERSION,
    CURRENT_VERSION,
    INIT_DOCUMENT_REF,
    MCP_REGISTRY_REF,
    MigrationArchiveKind,
    MigrationDomain,
    MigrationEntryKind,
    MigrationWorkspaceError,
)
from lingtai.kernel.migrate.migrate import meta_filename, reset_process_cache
from tests._migration_workspace_helpers import (
    FakeMigrationWorkspace,
    agent_meta_relative_path,
    agent_workspace,
    preset_workspace,
    run_agent_migrations,
    run_migrations,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with a clean per-process migration cache."""
    reset_process_cache()
    yield
    reset_process_cache()


def _write_preset(plib: Path, name: str, body: dict) -> Path:
    p = plib / f"{name}.json"
    p.write_text(json.dumps(body, indent=2))
    return p


def _read(p: Path) -> dict:
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# Runner behavior
# ---------------------------------------------------------------------------

def test_run_migrations_creates_meta_when_directory_has_presets(tmp_path):
    """First successful run writes _kernel_meta.json with the current version."""
    plib = tmp_path / "presets"
    plib.mkdir()
    _write_preset(plib, "anything", {
        "name": "anything",
        "manifest": {"llm": {"provider": "p", "model": "m"}, "capabilities": {}},
    })

    run_migrations(plib)

    meta = _read(plib / meta_filename())
    assert meta == {"version": CURRENT_VERSION}


def test_run_migrations_no_op_when_directory_missing(tmp_path):
    """Nonexistent presets dir is silently ignored — no meta file created."""
    plib = tmp_path / "does_not_exist"
    run_migrations(plib)
    assert not plib.exists()


def test_run_migrations_no_op_when_already_at_current_version(tmp_path):
    """Existing meta.json at current version means migrations are skipped."""
    plib = tmp_path / "presets"
    plib.mkdir()
    (plib / meta_filename()).write_text(json.dumps({"version": CURRENT_VERSION}))

    # Old-layout preset — would normally be migrated, but version says we're done.
    p = _write_preset(plib, "stale", {
        "name": "stale",
        "manifest": {
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
            "context_limit": 16384,
        },
    })

    run_migrations(plib)

    after = _read(p)
    assert "context_limit" in after["manifest"]  # untouched
    assert "context_limit" not in after["manifest"]["llm"]


def test_run_migrations_idempotent_within_same_process(tmp_path):
    """Calling twice in the same process for the same path is a no-op the second time."""
    plib = tmp_path / "presets"
    plib.mkdir()
    p = _write_preset(plib, "p", {
        "name": "p",
        "manifest": {
            "llm": {"provider": "x", "model": "y"},
            "capabilities": {},
            "context_limit": 8192,
        },
    })

    run_migrations(plib)
    first_mtime = (plib / meta_filename()).stat().st_mtime_ns
    after_first = _read(p)

    run_migrations(plib)
    second_mtime = (plib / meta_filename()).stat().st_mtime_ns
    after_second = _read(p)

    assert after_first == after_second
    # process-cache short-circuit means second run never touches the meta file
    assert first_mtime == second_mtime


def test_run_migrations_advances_version_on_success(tmp_path):
    """After a successful migration, the on-disk version equals CURRENT_VERSION."""
    plib = tmp_path / "presets"
    plib.mkdir()
    _write_preset(plib, "p", {
        "name": "p",
        "manifest": {
            "llm": {"provider": "x", "model": "y"},
            "capabilities": {},
            "context_limit": 8192,
        },
    })

    run_migrations(plib)

    assert _read(plib / meta_filename())["version"] == CURRENT_VERSION


def test_run_migrations_skips_version_already_done_on_legacy_preset(tmp_path):
    """Forward-only invariant: with version=N already on disk, migrations
    ≤N never re-run, even if the legacy on-disk shape is restored.

    This proves the version gate is the source of truth, not the data shape.
    """
    plib = tmp_path / "presets"
    plib.mkdir()
    # Mark the dir as already at CURRENT_VERSION
    (plib / meta_filename()).write_text(
        json.dumps({"version": CURRENT_VERSION})
    )
    # Plant a preset in the LEGACY shape that m001 would normally migrate
    p = _write_preset(plib, "legacy", {
        "name": "legacy",
        "manifest": {
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
            "context_limit": 99999,
        },
    })

    run_migrations(plib)

    # Untouched — gate prevented re-run
    after = _read(p)
    assert after["manifest"]["context_limit"] == 99999
    assert "context_limit" not in after["manifest"]["llm"]


def test_run_migrations_honors_future_version_without_downgrading(tmp_path, caplog):
    """Forward-only: a meta file from a future kernel is honored as-is.

    If a user installs kernel v5, runs migrations, then downgrades to v3,
    we must not roll back the version counter. We log a warning so the
    operator notices.
    """
    import logging
    plib = tmp_path / "presets"
    plib.mkdir()
    future_version = CURRENT_VERSION + 10
    (plib / meta_filename()).write_text(
        json.dumps({"version": future_version})
    )
    # Plant a legacy preset — must NOT be migrated by this older kernel
    p = _write_preset(plib, "legacy", {
        "name": "legacy",
        "manifest": {
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
            "context_limit": 12345,
        },
    })

    caplog.set_level(logging.WARNING, logger="lingtai.kernel.migrate.migrate")
    run_migrations(plib)

    # Meta version preserved, not downgraded
    assert _read(plib / meta_filename())["version"] == future_version
    # Preset untouched
    assert _read(p)["manifest"]["context_limit"] == 12345
    # Warning surfaced
    assert any("downgrade" in r.message.lower() for r in caplog.records)


def test_run_migrations_persists_across_simulated_process_restart(tmp_path):
    """The version gate survives across processes (simulated by clearing
    the in-memory cache and re-invoking).

    First call runs all pending migrations → writes version to disk.
    Second call (after cache reset) reads version from disk → no-op.
    Crucially, this proves the version persistence is on-disk, not just
    in-process.
    """
    plib = tmp_path / "presets"
    plib.mkdir()
    p = _write_preset(plib, "p", {
        "name": "p",
        "manifest": {
            "llm": {"provider": "x", "model": "y"},
            "capabilities": {},
            "context_limit": 7777,
        },
    })

    # First "process": cache empty, fixture is legacy-shape
    run_migrations(plib)
    assert _read(p)["manifest"]["llm"]["context_limit"] == 7777
    first_meta_mtime = (plib / meta_filename()).stat().st_mtime_ns

    # Plant a fresh legacy-shape preset (e.g. user added a new file)
    p2 = _write_preset(plib, "p2", {
        "name": "p2",
        "manifest": {
            "llm": {"provider": "x", "model": "y"},
            "capabilities": {},
            "context_limit": 8888,
        },
    })

    # Simulate process restart
    from lingtai.kernel.migrate.migrate import reset_process_cache
    reset_process_cache()

    # Second "process": cache empty, but on-disk version says we're done
    run_migrations(plib)

    # m001 did NOT run on p2 — version gate held
    after_p2 = _read(p2)
    assert after_p2["manifest"]["context_limit"] == 8888  # legacy shape preserved
    assert "context_limit" not in after_p2["manifest"]["llm"]
    # Meta file untouched (mtime unchanged)
    assert (plib / meta_filename()).stat().st_mtime_ns == first_meta_mtime


# ---------------------------------------------------------------------------
# Agent workdir migration domain
# ---------------------------------------------------------------------------

def _write_init(workdir: Path, body: dict) -> Path:
    p = workdir / "init.json"
    p.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return p


def _minimal_init() -> dict:
    return {
        "manifest": {
            "agent_name": "alice",
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
        },
        "principle": "",
        "covenant": "",
        "pad": "",
        "lingtai": "",
    }


def test_run_agent_migrations_archives_and_removes_procedures_fields(tmp_path):
    init = _minimal_init()
    init["procedures"] = "legacy procedures text"
    init["procedures_file"] = "legacy/procedures.md"
    init_path = _write_init(tmp_path, init)

    run_agent_migrations(tmp_path)

    data = _read(init_path)
    assert "procedures" not in data
    assert "procedures_file" not in data

    import hashlib
    digest = hashlib.sha256(b"legacy procedures text").hexdigest()
    archive = tmp_path / "system" / "migrations" / f"init-procedures-{digest}.md"
    assert archive.read_text(encoding="utf-8") == "legacy procedures text"

    meta = _read(tmp_path / agent_meta_relative_path())
    assert meta == {"version": AGENT_CURRENT_VERSION, "domain": "agent"}


def test_run_agent_migrations_version_gate_prevents_rerun_after_restart(tmp_path):
    init = _minimal_init()
    init["procedures"] = "first legacy"
    init_path = _write_init(tmp_path, init)

    run_agent_migrations(tmp_path)
    assert "procedures" not in _read(init_path)

    restored = _read(init_path)
    restored["procedures"] = "restored after migration"
    _write_init(tmp_path, restored)
    reset_process_cache()

    run_agent_migrations(tmp_path)

    # Version gate is the source of truth: migrations do not rerun for a
    # workdir already marked current, even if a stale shape is later restored.
    assert _read(init_path)["procedures"] == "restored after migration"


def test_run_agent_migrations_no_op_when_workdir_missing(tmp_path):
    workdir = tmp_path / "missing"
    run_agent_migrations(workdir)
    assert not workdir.exists()


def test_run_agent_migrations_no_op_when_init_missing(tmp_path):
    run_agent_migrations(tmp_path)
    assert not (tmp_path / agent_meta_relative_path()).exists()


def test_run_agent_migrations_treats_non_object_meta_as_zero(tmp_path, caplog):
    import logging

    init = _minimal_init()
    init["procedures_file"] = "legacy/procedures.md"
    init_path = _write_init(tmp_path, init)
    meta_path = tmp_path / agent_meta_relative_path()
    meta_path.parent.mkdir(parents=True)
    meta_path.write_text("null", encoding="utf-8")

    # The malformed-meta → version 0 read is adapter mechanism now, so the
    # "expected object" warning is logged by the POSIX adapter.
    caplog.set_level(logging.WARNING, logger="lingtai.adapters.posix.migration_workspace")
    run_agent_migrations(tmp_path)

    assert "procedures_file" not in _read(init_path)
    assert _read(meta_path) == {"version": AGENT_CURRENT_VERSION, "domain": "agent"}
    assert any("expected object" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Registry validation (best-practice invariants)
# ---------------------------------------------------------------------------

def test_registry_rejects_duplicate_versions():
    """Two migrations claiming the same version → import-time RuntimeError."""
    from lingtai.kernel.migrate.migrate import _validate_registry
    import lingtai.kernel.migrate.migrate as mod

    original = mod._MIGRATIONS
    try:
        mod._MIGRATIONS = (
            (1, "first", lambda p: None),
            (1, "second", lambda p: None),  # collision
        )
        with pytest.raises(RuntimeError, match="duplicate"):
            _validate_registry()
    finally:
        mod._MIGRATIONS = original


def test_registry_rejects_non_contiguous_versions():
    """Skipping a version (1, 3) → RuntimeError."""
    from lingtai.kernel.migrate.migrate import _validate_registry
    import lingtai.kernel.migrate.migrate as mod

    original = mod._MIGRATIONS
    try:
        mod._MIGRATIONS = (
            (1, "first", lambda p: None),
            (3, "third", lambda p: None),  # skipped 2
        )
        with pytest.raises(RuntimeError, match="contiguous"):
            _validate_registry()
    finally:
        mod._MIGRATIONS = original


def test_registry_rejects_out_of_order_versions():
    """Decreasing version → RuntimeError."""
    from lingtai.kernel.migrate.migrate import _validate_registry
    import lingtai.kernel.migrate.migrate as mod

    original = mod._MIGRATIONS
    try:
        mod._MIGRATIONS = (
            (2, "second", lambda p: None),
            (1, "first", lambda p: None),
        )
        with pytest.raises(RuntimeError, match="contiguous|increasing"):
            _validate_registry()
    finally:
        mod._MIGRATIONS = original


def test_registry_rejects_zero_or_negative_versions():
    """Version must be ≥ 1."""
    from lingtai.kernel.migrate.migrate import _validate_registry
    import lingtai.kernel.migrate.migrate as mod

    original = mod._MIGRATIONS
    try:
        mod._MIGRATIONS = ((0, "zero", lambda p: None),)
        with pytest.raises(RuntimeError, match="positive"):
            _validate_registry()
    finally:
        mod._MIGRATIONS = original


def test_registry_rejects_non_callable_function():
    """The third tuple element must be callable."""
    from lingtai.kernel.migrate.migrate import _validate_registry
    import lingtai.kernel.migrate.migrate as mod

    original = mod._MIGRATIONS
    try:
        mod._MIGRATIONS = ((1, "broken", "not_a_function"),)
        with pytest.raises(RuntimeError, match="callable"):
            _validate_registry()
    finally:
        mod._MIGRATIONS = original


def test_current_version_derived_from_registry_max():
    """CURRENT_VERSION is the highest registered migration's version, not
    a hand-maintained constant that can drift."""
    from lingtai.kernel.migrate.migrate import _MIGRATIONS, CURRENT_VERSION
    assert CURRENT_VERSION == max(v for v, _, _ in _MIGRATIONS)


def test_store_version_uses_pid_suffixed_tmp_file(tmp_path):
    """Concurrent processes writing the same meta file would otherwise race
    on `_kernel_meta.json.tmp`. The adapter's tmp filename includes the PID."""
    plib = tmp_path / "presets"
    plib.mkdir()

    preset_workspace(plib).store_version(1)

    # No leftover tmp file with this process's PID
    pid_tmp = plib / f"{meta_filename()}.{os.getpid()}.tmp"
    assert not pid_tmp.exists()
    # And the version landed
    assert _read(plib / meta_filename())["version"] == 1


# ---------------------------------------------------------------------------
# m001 — context_limit relocation
# ---------------------------------------------------------------------------

def test_m001_moves_context_limit_into_llm_block(tmp_path):
    """The canonical case: old layout → new layout."""
    plib = tmp_path / "presets"
    plib.mkdir()
    p = _write_preset(plib, "old", {
        "name": "old",
        "manifest": {
            "llm": {"provider": "px", "model": "mx", "api_key_env": "X"},
            "capabilities": {"file": {}},
            "context_limit": 32768,
        },
    })

    run_migrations(plib)

    after = _read(p)
    assert "context_limit" not in after["manifest"]
    assert after["manifest"]["llm"]["context_limit"] == 32768
    # other llm fields preserved
    assert after["manifest"]["llm"]["provider"] == "px"
    assert after["manifest"]["llm"]["api_key_env"] == "X"


def test_m001_leaves_already_migrated_presets_alone(tmp_path):
    """A preset where context_limit is already inside llm is unchanged
    by m001. m002 (description_object) still runs and synthesizes a
    description block when one is missing — that's expected.
    """
    plib = tmp_path / "presets"
    plib.mkdir()
    body = {
        "name": "new",
        "manifest": {
            "llm": {"provider": "p", "model": "m", "context_limit": 65536},
            "capabilities": {},
        },
    }
    p = _write_preset(plib, "new", body)

    run_migrations(plib)

    expected = {**body, "description": {"summary": ""}}
    assert _read(p) == expected


def test_m001_leaves_presets_without_context_limit_alone(tmp_path):
    """No context_limit anywhere → m001 doesn't rewrite. m002 still
    synthesizes a description block when one is missing.
    """
    plib = tmp_path / "presets"
    plib.mkdir()
    body = {
        "name": "noctx",
        "manifest": {
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
        },
    }
    p = _write_preset(plib, "noctx", body)

    run_migrations(plib)

    expected = {**body, "description": {"summary": ""}}
    assert _read(p) == expected


def test_m001_warns_and_skips_when_both_locations_set(tmp_path, caplog):
    """Ambiguous preset (both locations) is left untouched and warned about."""
    import logging
    plib = tmp_path / "presets"
    plib.mkdir()
    body = {
        "name": "dup",
        "manifest": {
            "llm": {"provider": "p", "model": "m", "context_limit": 8000},
            "capabilities": {},
            "context_limit": 16000,
        },
    }
    p = _write_preset(plib, "dup", body)

    caplog.set_level(logging.WARNING,
                     logger="lingtai.kernel.migrate.m001_context_limit_relocation")
    run_migrations(plib)

    # m001 leaves the ambiguous preset untouched (just warns); m002 still
    # synthesizes a description block for the missing field.
    expected = {**body, "description": {"summary": ""}}
    assert _read(p) == expected
    assert any("both" in r.message.lower() for r in caplog.records)


def test_m001_skips_internal_meta_file(tmp_path):
    """The migration walker must skip _kernel_meta.json itself."""
    plib = tmp_path / "presets"
    plib.mkdir()
    # Pre-create a bogus meta file (simulating a partial earlier run)
    (plib / meta_filename()).write_text(json.dumps({"version": 0}))
    p = _write_preset(plib, "ok", {
        "name": "ok",
        "manifest": {
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
            "context_limit": 4096,
        },
    })

    run_migrations(plib)

    # The preset got migrated normally
    assert _read(p)["manifest"]["llm"]["context_limit"] == 4096
    # The meta file is now at version 1 (overwritten — not parsed as a preset)
    assert _read(plib / meta_filename())["version"] == CURRENT_VERSION


def test_m001_handles_jsonc_files(tmp_path):
    """JSONC presets with comments + trailing commas are migrated correctly."""
    plib = tmp_path / "presets"
    plib.mkdir()
    body = '''{
      "name": "jc",   // a comment
      "manifest": {
        "llm": {"provider": "p", "model": "m"},
        "capabilities": {},
        "context_limit": 12345,
      },
    }'''
    p = plib / "jc.jsonc"
    p.write_text(body)

    run_migrations(plib)

    after = _read(p)  # rewrites land as plain JSON regardless of input suffix
    assert after["manifest"]["llm"]["context_limit"] == 12345
    assert "context_limit" not in after["manifest"]


def test_jsonc_migrations_preserve_double_slash_inside_strings(tmp_path):
    """The shared JSONC reader preserves URLs while both preset migrations run."""
    plib = tmp_path / "presets"
    plib.mkdir()
    body = '''{
      "name": "urlish", // outside-string comment still strips
      "description": "Docs at https://example.test/path?from=kernel",
      "manifest": {
        "llm": {"provider": "p", "model": "m"},
        "capabilities": {
          "docs": {"homepage": "https://example.test/docs//nested"},
        },
        "context_limit": 45678,
      },
      "tags": ["tier:4"],
    }'''
    p = plib / "urlish.jsonc"
    p.write_text(body, encoding="utf-8")

    run_migrations(plib)

    after = _read(p)
    assert after["description"] == {
        "summary": "Docs at https://example.test/path?from=kernel",
        "tier": "4",
    }
    assert after["manifest"]["capabilities"]["docs"]["homepage"] == (
        "https://example.test/docs//nested"
    )
    assert after["manifest"]["llm"]["context_limit"] == 45678
    assert "context_limit" not in after["manifest"]
    assert "tags" not in after


def test_m001_skips_subdirectories_and_non_json(tmp_path):
    """Non-preset entries are ignored without errors."""
    plib = tmp_path / "presets"
    plib.mkdir()
    (plib / "README.md").write_text("# library docs")
    sub = plib / "subdir"
    sub.mkdir()
    (sub / "nested.json").write_text(json.dumps({
        "name": "nested",
        "manifest": {"llm": {"provider": "x", "model": "y"},
                     "capabilities": {}, "context_limit": 999},
    }))

    run_migrations(plib)

    # subdirectory preset NOT migrated (out of scope)
    nested = _read(sub / "nested.json")
    assert nested["manifest"]["context_limit"] == 999  # original layout intact


def test_m001_continues_past_unreadable_preset(tmp_path, caplog):
    """A malformed JSON file warns but doesn't abort the whole migration run."""
    import logging
    plib = tmp_path / "presets"
    plib.mkdir()
    (plib / "broken.json").write_text("{ not json")
    good = _write_preset(plib, "good", {
        "name": "good",
        "manifest": {
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
            "context_limit": 7777,
        },
    })

    caplog.set_level(logging.WARNING,
                     logger="lingtai.kernel.migrate.m001_context_limit_relocation")
    run_migrations(plib)

    # good preset was still migrated
    assert _read(good)["manifest"]["llm"]["context_limit"] == 7777
    # broken one warned about
    assert any("unreadable" in r.message.lower() for r in caplog.records)



def test_run_agent_migrations_rewrites_legacy_curated_mcp_launch_args(tmp_path):
    """agent m002 rewrites old curated MCP module launch args to canonical modules."""
    workdir = tmp_path / "agent"
    workdir.mkdir()
    (workdir / "init.json").write_text(json.dumps({
        "manifest": {"agent_name": "agent"},
        "mcp": {
            "telegram": {"type": "stdio", "command": "python", "args": ["-m", "lingtai_telegram"]},
            "remote": {"type": "http", "url": "http://example.test/mcp"},
            "imap": {"type": "stdio", "command": "python", "args": ["-m", "lingtai.mcp_servers.imap"]},
        },
    }))
    registry_records = [
        {"name": "imap", "transport": "stdio", "command": "python", "args": ["-m", "lingtai_imap"]},
        {"name": "telegram", "transport": "stdio", "command": "python", "args": ["-m", "lingtai.mcp_servers.telegram"]},
        {"name": "other", "transport": "stdio", "command": "python", "args": ["-m", "other_module"]},
        {"name": "remote", "transport": "http", "url": "http://example.test/mcp"},
    ]
    (workdir / "mcp_registry.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in registry_records),
        encoding="utf-8",
    )

    run_agent_migrations(workdir)

    init_after = _read(workdir / "init.json")
    assert init_after["mcp"]["telegram"]["args"] == ["-m", "lingtai.mcp_servers.telegram"]
    assert init_after["mcp"]["imap"]["args"] == ["-m", "lingtai.mcp_servers.imap"]
    lines = (workdir / "mcp_registry.jsonl").read_text(encoding="utf-8").splitlines()
    registry_after = [json.loads(line) for line in lines]
    by_name = {r["name"]: r for r in registry_after}
    assert by_name["imap"]["args"] == ["-m", "lingtai.mcp_servers.imap"]
    assert by_name["telegram"]["args"] == ["-m", "lingtai.mcp_servers.telegram"]
    assert by_name["other"]["args"] == ["-m", "other_module"]
    assert by_name["remote"]["transport"] == "http"
    assert _read(workdir / agent_meta_relative_path()) == {"version": AGENT_CURRENT_VERSION, "domain": "agent"}

    before = (workdir / "mcp_registry.jsonl").read_text(encoding="utf-8")
    run_agent_migrations(workdir)
    assert (workdir / "mcp_registry.jsonl").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Integration with discover_presets
# ---------------------------------------------------------------------------

def test_discover_presets_triggers_migration(tmp_path):
    """discover_presets() with old-layout files migrates them automatically."""
    from tests._migration_workspace_helpers import discover_presets
    plib = tmp_path / "presets"
    plib.mkdir()
    p = _write_preset(plib, "x", {
        "name": "x",
        "manifest": {
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
            "context_limit": 4321,
        },
    })

    found = discover_presets(plib)

    # Listing keys are full path strings now; the file's path is the identity.
    assert any(k.endswith("x.json") for k in found)
    after = _read(p)
    assert after["manifest"]["llm"]["context_limit"] == 4321
    assert "context_limit" not in after["manifest"]


def test_discover_presets_excludes_kernel_meta_file(tmp_path):
    """The internal _kernel_meta.json is not surfaced as a preset."""
    from tests._migration_workspace_helpers import discover_presets
    plib = tmp_path / "presets"
    plib.mkdir()
    _write_preset(plib, "real", {
        "name": "real",
        "manifest": {"llm": {"provider": "p", "model": "m"},
                     "capabilities": {}},
    })

    discover_presets(plib)  # creates _kernel_meta.json
    assert (plib / meta_filename()).exists()

    found = discover_presets(plib)
    # Exactly one preset surfaced — `real.json` — and the meta file is hidden.
    assert len(found) == 1
    assert next(iter(found.keys())).endswith("real.json")
    assert all("_kernel_meta" not in k for k in found)


# ---------------------------------------------------------------------------
# Seven-family Port conformance — the production adapter and the in-memory fake
# are proven against the same MigrationWorkspacePort contract.
# ---------------------------------------------------------------------------


def _fake_preset(tmp_path, entries):
    return FakeMigrationWorkspace(MigrationDomain.PRESET_LIBRARY, entries=entries)


def _posix_preset(tmp_path, entries):
    root = tmp_path / "lib"
    root.mkdir()
    for name, text in entries.items():
        (root / name).write_text(text, encoding="utf-8")
    return preset_workspace(root)


def _fake_agent(tmp_path, entries):
    return FakeMigrationWorkspace(MigrationDomain.AGENT_WORKDIR, entries=entries)


def _posix_agent(tmp_path, entries):
    root = tmp_path / "wd"
    root.mkdir()
    for name, text in entries.items():
        (root / name).write_text(text, encoding="utf-8")
    return agent_workspace(root)


@pytest.mark.parametrize("factory", [_fake_preset, _posix_preset])
def test_conformance_preset_families(factory, tmp_path):
    ws = factory(tmp_path, {"a.json": '{"m": 1}', "b.jsonc": '{"m": 2}'})

    state = ws.inspect()
    assert state.available is True
    assert isinstance(state.cache_key, str) and state.cache_key
    assert state.current_version == 0

    refs = ws.enumerate_entries()
    assert sorted(r.name for r in refs) == ["a.json", "b.jsonc"]
    assert all(r.kind is MigrationEntryKind.PRESET_DOCUMENT for r in refs)

    a = next(r for r in refs if r.name == "a.json")
    assert ws.read_entry(a) == '{"m": 1}'
    ws.atomic_replace_entry(a, '{"m": 9}')
    assert ws.read_entry(a) == '{"m": 9}'

    ws.store_version(2)
    assert ws.inspect().current_version == 2


@pytest.mark.parametrize("factory", [_fake_agent, _posix_agent])
def test_conformance_agent_families(factory, tmp_path):
    import hashlib

    ws = factory(tmp_path, {"init.json": '{"manifest": {"agent_name": "x"}}'})

    state = ws.inspect()
    assert state.available is True
    assert state.current_version == 0
    # Agent domain has no enumerable candidate documents.
    assert ws.enumerate_entries() == ()

    assert ws.read_entry(INIT_DOCUMENT_REF) is not None
    assert ws.read_entry(MCP_REGISTRY_REF) is None  # not seeded
    ws.atomic_replace_entry(INIT_DOCUMENT_REF, '{"x": 1}')
    assert ws.read_entry(INIT_DOCUMENT_REF) == '{"x": 1}'

    result = ws.archive(MigrationArchiveKind.INIT_PROCEDURES, "legacy text")
    digest = hashlib.sha256(b"legacy text").hexdigest()
    assert result.relative_path == f"system/migrations/init-procedures-{digest}.md"
    assert result.content_hash == digest
    assert result.byte_length == len(b"legacy text")
    assert result.char_length == len("legacy text")

    # Best-effort audit append never raises.
    ws.append_audit("conformance_probe", {"k": "v"})

    ws.store_version(1)
    assert ws.inspect().current_version == 1


def test_run_migrations_drives_fake_workspace():
    """The Core runner cooperates with any conforming workspace, not just POSIX."""
    ws = FakeMigrationWorkspace(
        MigrationDomain.PRESET_LIBRARY,
        entries={
            "legacy.json": json.dumps({
                "name": "legacy",
                "manifest": {
                    "llm": {"provider": "p", "model": "m"},
                    "capabilities": {},
                    "context_limit": 4096,
                },
            }),
        },
    )

    from lingtai.kernel.migrate import run_migrations as core_run_migrations
    core_run_migrations(ws)

    [ref] = ws.enumerate_entries()
    after = json.loads(ws.read_entry(ref))
    assert after["manifest"]["llm"]["context_limit"] == 4096
    assert "context_limit" not in after["manifest"]
    # Version advanced to the preset registry head after both transforms run.
    assert ws.inspect().current_version == CURRENT_VERSION


# ---------------------------------------------------------------------------
# Store-version durability — success-by-success persistence across the Port
#
# A failed version write must not be swallowed: the mechanism raises
# MigrationWorkspaceError, the Core does not advance the persisted version, and
# it does not process-cache the workspace, so the next launch retries the step.
# ---------------------------------------------------------------------------


class _FailOnceStoreWorkspace(FakeMigrationWorkspace):
    """Fake that raises `MigrationWorkspaceError` on its first `store_version`
    call, then persists normally — a transient version-write failure."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.store_attempts = 0

    def store_version(self, version: int) -> None:
        self.store_attempts += 1
        if self.store_attempts == 1:
            raise MigrationWorkspaceError("simulated one-time version-write failure")
        super().store_version(version)


def test_posix_store_version_failure_raises_and_cleans_temp(tmp_path):
    """A failed version write surfaces as `MigrationWorkspaceError` (never a raw
    OSError), persists no version advance, and removes the PID-suffixed temp file.
    """
    plib = tmp_path / "presets"
    plib.mkdir()
    # Force os.replace to fail deterministically: the version-meta target is an
    # (un-replaceable) directory, so the tmp file is written and must then be
    # cleaned up on the error path.
    meta_target = plib / meta_filename()
    meta_target.mkdir()

    ws = preset_workspace(plib)
    with pytest.raises(MigrationWorkspaceError):
        ws.store_version(1)

    # The PID-suffixed temp file was cleaned up, not orphaned.
    pid_tmp = plib / f"{meta_filename()}.{os.getpid()}.tmp"
    assert not pid_tmp.exists()
    # No version was persisted: the meta target is still the untouched directory
    # and inspect() reports version 0 across the Port.
    assert meta_target.is_dir()
    assert ws.inspect().current_version == 0


def test_one_time_store_version_failure_is_not_process_cached():
    """Success-by-success durability: a transient `store_version` failure aborts
    the run at version 0 without process-caching the workspace, so a retry in the
    same process resumes and reaches CURRENT_VERSION.
    """
    from lingtai.kernel.migrate import run_migrations as core_run_migrations

    ws = _FailOnceStoreWorkspace(
        MigrationDomain.PRESET_LIBRARY,
        entries={
            "legacy.json": json.dumps({
                "name": "legacy",
                "manifest": {
                    "llm": {"provider": "p", "model": "m"},
                    "capabilities": {},
                    "context_limit": 4096,
                },
            }),
        },
    )

    # First run: the very first version write fails, so the run aborts with the
    # version unadvanced and the workspace is NOT added to the process cache.
    core_run_migrations(ws)
    assert ws.store_attempts == 1
    assert ws.inspect().current_version == 0

    # Second run in the same process: nothing was cached, so the runner retries
    # and now persists all the way to the registry head.
    core_run_migrations(ws)
    assert ws.inspect().current_version == CURRENT_VERSION
