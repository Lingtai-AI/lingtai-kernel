"""Preset library — atomic {llm, capabilities} bundles for agent runtime swap.

A preset lives as a single JSON or JSONC file anywhere on disk. The preset's
**name is its path** — there is no separate "stem" identity. Names accepted
by `load_preset` and stored in `manifest.preset.active` / `default` may be:

- absolute (`/Users/me/.lingtai-tui/presets/cheap.json`)
- home-relative (`~/.lingtai-tui/presets/cheap.json`)
- working-dir-relative (`./presets/cheap.json`)

The kernel `expanduser`s and resolves at read time only — what you wrote is
what's stored. There is **no canonicalization on write** and no implicit
search path: if `active` says `~/foo.json`, that exact file is loaded.

`manifest.preset.path` is a list of library *directories* used purely for
**listing** (the `system(action='presets')` enumeration and the TUI library
screen). It is not a resolver — two libraries can each contain a `cheap.json`
and they appear as two distinct entries in the listing, identified by their
full paths. No shadowing, no collisions, no ambiguity.

This module owns:
- `discover_presets`: enumerate preset *paths* across one or more directories
- `load_preset`: read + validate one preset by path
- `expand_inherit`: resolve `"provider": "inherit"` sentinels against main LLM
- `default_presets_path`: the per-machine library at ~/.lingtai-tui/presets/
- `resolve_presets_path`: resolve manifest.preset.path entries to list[Path]
- `home_shortened`: render an absolute path with `~/...` when under $HOME

The on-disk shape is `{name, description, manifest: {...}}` (with optional
`tags`). The `description` field may be a plain string or a structured
object — both are surfaced verbatim to the agent.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def default_presets_path() -> Path:
    """The per-machine preset library directory."""
    return Path.home() / ".lingtai-tui" / "presets"


def home_shortened(path: Path | str) -> str:
    """Render a path with `~/...` shorthand when it lives under $HOME.

    This is a *display* helper — the kernel's canonical form is whatever the
    operator wrote. Use this in listings and logs for readable output.

    Returns:
        ``~/.lingtai-tui/...`` style when the resolved path is under
        ``Path.home()``; otherwise the absolute string form. Never raises —
        falls back to ``str(path)`` if anything is unresolvable.
    """
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            return str(path)
        home = Path.home()
        try:
            rel = p.relative_to(home)
            return str(Path("~") / rel)
        except ValueError:
            return str(p)
    except (TypeError, ValueError, OSError):
        return str(path)


def resolve_preset_name(name: str, working_dir: Path) -> Path:
    """Resolve a preset name (path string) to an absolute Path.

    Accepts the three input forms — absolute, ~-prefixed, working-dir-relative —
    and returns an absolute Path with `~` expanded. Does NOT resolve symlinks
    or canonicalize: the returned path matches what the user wrote.

    Args:
        name: the preset path as a string. Must be non-empty.
        working_dir: directory to resolve relative paths against.

    Raises:
        ValueError: name is empty or not a string.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"preset name must be a non-empty string, got {name!r}")
    p = Path(name).expanduser()
    if p.is_absolute():
        return p
    return (working_dir / p).resolve()


def resolve_presets_path(manifest: dict, working_dir: Path) -> list[Path]:
    """Resolve manifest.preset.path entries to absolute Paths.

    Returns a list[Path] in declared order. The schema accepts either a
    single string or a list of strings; both are normalized to a list here.
    When the umbrella is absent or path is missing/empty (None, "", []),
    falls back to ``[default_presets_path()]``.

    Relative paths are resolved against working_dir (not the process CWD)
    so an agent's library reference remains valid regardless of where the
    process was launched.
    """
    preset_block = manifest.get("preset") or {}
    raw = preset_block.get("path") if isinstance(preset_block, dict) else None

    if isinstance(raw, str):
        entries: list[str] = [raw] if raw else []
    elif isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, str) and e]
    else:
        entries = []

    if not entries:
        return [default_presets_path()]

    resolved: list[Path] = []
    for entry in entries:
        p = Path(entry).expanduser()
        resolved.append(p if p.is_absolute() else (working_dir / p).resolve())
    return resolved


def _normalize_paths(presets_path: Path | str | list[Path | str]) -> list[Path]:
    """Coerce the discover argument to a list[Path]. Empty list returns []."""
    if isinstance(presets_path, (str, Path)):
        return [Path(presets_path)]
    return [Path(p) for p in presets_path]


def discover_presets(
    presets_path: Path | str | list[Path | str],
) -> dict[str, Path]:
    """Enumerate preset files across one or more library directories.

    Returns a mapping of **path-string → Path** for top-level *.json[c] files.
    The key is the absolute path string (with `~/...` shortening when under
    $HOME) — agents and UIs can pass it straight back to `load_preset`.

    Multiple libraries are scanned in declared order. Because the key is a
    full path, two libraries each containing `cheap.json` appear as two
    distinct entries — no collisions, no shadowing. Duplicate path entries
    (e.g. the same directory listed twice) collapse naturally because their
    files resolve to the same absolute path.

    Nonexistent directories are silently skipped — they're not an error.

    Triggers any pending kernel-side preset migrations against each path
    before listing — see lingtai_kernel.migrate. Migrations are idempotent
    and process-cached, so repeated calls share the work.
    """
    from lingtai_kernel.migrate import run_migrations
    from lingtai_kernel.migrate.migrate import meta_filename

    paths = _normalize_paths(presets_path)
    skip = meta_filename()
    out: dict[str, Path] = {}

    for p in paths:
        if not p.is_dir():
            continue
        run_migrations(p)
        for entry in p.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix not in (".json", ".jsonc"):
                continue
            if entry.name == skip:
                continue
            key = home_shortened(entry)
            # Same physical file from a duplicated path entry: harmless overwrite.
            out[key] = entry
    return out


def load_preset(
    name: str,
    working_dir: Path | None = None,
) -> dict:
    """Load and validate a preset by **path name**.

    Args:
        name: the preset's path. Accepts:
            - absolute: `/Users/me/.lingtai-tui/presets/cheap.json`
            - home-relative: `~/.lingtai-tui/presets/cheap.json`
            - working-dir-relative: `./presets/cheap.json` (requires working_dir)
            Both `.json` and `.jsonc` extensions are accepted; the name MUST
            include the extension — there is no implicit extension probing.
        working_dir: directory to resolve relative names against. Required
            iff `name` is relative. Pass `Path.cwd()` for one-off scripts.

    Returns:
        The parsed preset dict with shape {name, description, manifest: {...}}.

    Raises:
        KeyError: the file does not exist.
        ValueError: the name is malformed, the file is malformed, or
            required fields are missing.
    """
    from .config_resolve import load_jsonc
    from lingtai_kernel.migrate import run_migrations

    if not isinstance(name, str) or not name:
        raise ValueError(f"preset name must be a non-empty string, got {name!r}")

    p = Path(name).expanduser()
    if not p.is_absolute():
        if working_dir is None:
            raise ValueError(
                f"preset name {name!r} is relative but no working_dir provided"
            )
        p = (working_dir / p).resolve()

    if p.suffix not in (".json", ".jsonc"):
        raise ValueError(
            f"preset name {name!r}: must end in .json or .jsonc"
        )

    if not p.is_file():
        raise KeyError(f"preset not found: {name!r} (resolved to {p})")

    # Run kernel migrations on the containing directory so legacy on-disk
    # shapes are normalized before validation. Idempotent and process-cached.
    if p.parent.is_dir():
        run_migrations(p.parent)

    try:
        data = load_jsonc(p)
    except Exception as e:
        raise ValueError(f"failed to parse preset {name!r} ({p}): {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"preset {name!r} ({p}): expected object, got {type(data).__name__}")

    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError(f"preset {name!r} ({p}): missing or invalid 'manifest' object")

    llm = manifest.get("llm")
    if not isinstance(llm, dict):
        raise ValueError(f"preset {name!r} ({p}): missing or invalid 'manifest.llm' object")

    if not llm.get("provider") or not llm.get("model"):
        raise ValueError(f"preset {name!r} ({p}): manifest.llm requires non-empty 'provider' and 'model'")

    # context_limit lives inside manifest.llm. Migration m001 relocated any
    # legacy root-level placements; presets that still have it at the root
    # are ambiguous (migration explicitly skips both-locations) or hand-edited
    # regressions. Reject either case with a pointed error.
    if "context_limit" in manifest:
        raise ValueError(
            f"preset {name!r} ({p}): context_limit must live inside "
            f"manifest.llm, not at manifest root — move it under llm and retry"
        )
    ctx_limit = llm.get("context_limit")
    if ctx_limit is not None and not isinstance(ctx_limit, int):
        raise ValueError(
            f"preset {name!r} ({p}): context_limit must be an integer (got {type(ctx_limit).__name__})"
        )

    caps = manifest.get("capabilities", {})
    if not isinstance(caps, dict):
        raise ValueError(f"preset {name!r} ({p}): manifest.capabilities must be an object")

    # Optional top-level `tags` field — list of namespaced strings like
    # "tier:4" or "specialty:code". Used by agents (and the TUI) to pick
    # presets by category. The first namespace shipped is "tier:",
    # whose vocabulary is the numeric strings 1|2|3|4|5 (higher = better;
    # see TIER_VALUES below).
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        raise ValueError(
            f"preset {name!r} ({p}): 'tags' must be a list of strings"
        )
    for i, tag in enumerate(tags):
        if not isinstance(tag, str):
            raise ValueError(
                f"preset {name!r} ({p}): tags[{i}] must be a string (got {type(tag).__name__})"
            )

    return data


# ---------------------------------------------------------------------------
# Tag taxonomy
# ---------------------------------------------------------------------------
#
# Tags are namespaced strings stored as a top-level list on each preset:
#
#     "tags": ["tier:4", "specialty:code"]
#
# The first namespace introduced is `tier:`, a five-rung cost/quality ladder
# stored as plain numeric strings 1..5 — higher is better. The TUI renders
# these as star icons (★ through ★★★★★). Agents read tags via
# `system(action='presets')` and pick presets accordingly.
#
# Future namespaces (specialty, modality, context-class) follow the same
# `<namespace>:<value>` pattern so callers can filter by `t.startswith("X:")`.
TIER_NAMESPACE = "tier"
TIER_VALUES = ("1", "2", "3", "4", "5")
TIER_TAGS = tuple(f"{TIER_NAMESPACE}:{v}" for v in TIER_VALUES)


def preset_tags(preset: dict) -> list[str]:
    """Return the preset's tags list (or [] when unset)."""
    if not isinstance(preset, dict):
        return []
    tags = preset.get("tags")
    if not isinstance(tags, list):
        return []
    return [t for t in tags if isinstance(t, str)]


def preset_tier(preset: dict) -> str | None:
    """Return the preset's tier value (e.g. '4') or None.

    Reads the first `tier:*` tag — multiple tier tags on one preset is
    nonsensical, so the helper trusts the first.
    """
    for t in preset_tags(preset):
        if t.startswith(f"{TIER_NAMESPACE}:"):
            return t.split(":", 1)[1]
    return None


def preset_context_limit(preset_manifest: dict) -> int | None:
    """Return the preset's context_limit (lives inside manifest.llm).

    context_limit is a property of the model, so it's stored next to
    provider/model. Returns None when unset.

    For presets read via `load_preset`, the kernel migration system has
    already relocated any legacy root-level placements before validation —
    so by the time this helper is called, only the canonical location is
    populated.
    """
    if not isinstance(preset_manifest, dict):
        return None
    llm = preset_manifest.get("llm")
    if isinstance(llm, dict):
        return llm.get("context_limit")
    return None


def expand_inherit(capabilities: dict, main_llm: dict) -> dict:
    """Resolve `"provider": "inherit"` sentinels in capability configs.

    For each capability whose kwargs has `provider == "inherit"`, replace it
    with the main LLM's provider plus its credentials (api_key, api_key_env,
    base_url). The `model` field is NOT inherited — capabilities pick their
    own model independently.

    Mutates `capabilities` in place. Returns the same dict for convenience.
    """
    for cap_name, kwargs in capabilities.items():
        if not isinstance(kwargs, dict):
            continue
        if kwargs.get("provider") != "inherit":
            continue
        kwargs["provider"]    = main_llm.get("provider")
        kwargs["api_key"]     = main_llm.get("api_key")
        kwargs["api_key_env"] = main_llm.get("api_key_env")
        kwargs["base_url"]    = main_llm.get("base_url")
    return capabilities
