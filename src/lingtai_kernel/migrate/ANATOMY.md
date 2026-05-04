# migrate

Versioned, append-only, forward-only migrations for kernel-managed on-disk preset library state. Each migration is a standalone `m<NNN>_<name>.py` file that rewrites preset `.json`/`.jsonc` files in place to normalize legacy shapes. The on-disk version counter lives in `<presets_dir>/_kernel_meta.json`; a process-level cache prevents re-running within a single process.

## Components

- `__init__.py` — exports `CURRENT_VERSION` and `run_migrations` (`migrate/__init__.py:25`).
- `migrate.py` — runner, registry, and version tracking.
  - `_META_FILENAME = "_kernel_meta.json"` — on-disk version file name (`migrate.py:45`).
  - `_migrated: set[str]` — process-level guard; resolved path keys already migrated this run (`migrate.py:49`).
  - `_MIGRATIONS` — append-only registry of `(version, name, function)` tuples (`migrate.py:55-58`).
  - `_validate_registry()` — import-time sanity check: contiguous, strictly-increasing, callable (`migrate.py:61-100`).
  - `CURRENT_VERSION` — derived from registry; no hand-maintained constant (`migrate.py:103`).
  - `meta_filename()` — exposes `_META_FILENAME` for callers that must skip it during directory listing (`migrate.py:106-108`).
  - `_load_version(presets_path)` — reads version from `_kernel_meta.json`, returns 0 on missing/malformed (`migrate.py:111-128`).
  - `_save_version(presets_path, version)` — atomic write via PID-suffixed tmp + `os.replace` (`migrate.py:131-150`).
  - `run_migrations(presets_path)` — main entry: reads version, runs all pending migrations, advances counter after each (`migrate.py:153-213`).
  - `reset_process_cache()` — test-only; clears `_migrated` (`migrate.py:216-223`).
- `m001_context_limit_relocation.py` — moves `manifest.context_limit` → `manifest.llm.context_limit` (`m001_context_limit_relocation.py:42`). Local `_load_jsonc()` avoids importing from `lingtai` (`m001_context_limit_relocation.py:25-39`).
- `m002_description_object.py` — promotes string `description` to `{summary, tier?}`; folds `tags:[tier:N]` into `description.tier`; deletes `tags` (`m002_description_object.py:64`). Local `_load_jsonc()` (`m002_description_object.py:37-44`); `_extract_tier()` (`m002_description_object.py:47-61`).

## Connections

- **Inbound (caller):** `lingtai.presets.discover_presets_in_dirs` calls `run_migrations(p)` before listing presets (`presets.py:144,157`). `lingtai.presets.load_preset` calls `run_migrations(p.parent)` before reading a file (`presets.py:200,224`). Both paths import `meta_filename()` to skip `_kernel_meta.json` during directory scans (`presets.py:145`).
- **Outbound (this folder):** Migrations rewrite preset files in the target directory — each uses atomic tmp + `os.replace`. No other kernel or lingtai modules are imported; migrations duplicate `_load_jsonc()` locally to keep the import surface minimal.
- **Boundary contract:** `__init__.py` exposes only `CURRENT_VERSION` and `run_migrations`. `meta_filename()` is a secondary export accessed via `lingtai_kernel.migrate.migrate.meta_filename`.

## Composition

- **Parent:** `src/lingtai_kernel/` (see `src/lingtai_kernel/ANATOMY.md`).
- **Subfolders:** none.

## State

- **On-disk:** `<presets_dir>/_kernel_meta.json` — `{"version": N}`, persisted after each successful migration step (`migrate.py:138-150`). Created only when at least one migration runs.
- **Process-level:** `_migrated: set[str]` — resolved absolute path strings already migrated this process. Checked at `migrate.py:178`; added at `migrate.py:181,192,196,213`.
- **Ephemeral:** each migration rewrites preset files in place (atomic tmp + `os.replace`). No rollback artifacts left on success.

## Notes

- **Forward-only:** a meta file with version > `CURRENT_VERSION` (e.g. from a newer kernel later downgraded) is honored as-is; no migrations run; a warning is logged (`migrate.py:186-193`).
- **Contiguity enforced at import:** `_validate_registry` raises `RuntimeError` if the `_MIGRATIONS` tuple has gaps, duplicates, or non-callable entries (`migrate.py:61-100`). This catches programmer errors before any user data is touched.
- **Concurrency safety:** PID-suffixed tmp files prevent parent + avatar processes sharing a presets dir from clobbering each other's in-flight writes (`migrate.py:139`).
- **No lingtai imports:** both `m001` and `m002` duplicate a local `_load_jsonc()` helper (`m001_context_limit_relocation.py:25-39`, `m002_description_object.py:37-44`). This keeps the kernel→lingtai dependency strictly one-directional.
- **Naming convention:** each migration file is `m<NNN>_<name>.py` and exports `migrate_<name>(presets_path: Path) -> None` (`__init__.py:18-19`).
