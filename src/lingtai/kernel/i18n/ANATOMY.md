---
related_files:
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/i18n/__init__.py
  - src/lingtai/kernel/i18n/en.json
  - src/lingtai/kernel/i18n/wen.json
  - src/lingtai/kernel/i18n/zh.json
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# i18n

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues/mail/PR proposals; do not silently fix.

The kernel's message catalog — a flat key-value string table covering system notifications, molt instructions, and runtime manager prose in three locales (en / zh / wen). Model-facing tool schema/description text is no longer catalog-owned; it lives in canonical English tool source code and per-package glossary resources (`glossary-{en,zh,wen}.md`). The sole entry point is `t(lang, key, **kwargs)` which resolves a dotted key against the agent's configured language, falling back to English and then to the raw key itself.

## Components

- `__init__.py` — the entire module (77 lines). Three public symbols, one module-level cache, and one load-tracking set:
  - `_DIR` (`i18n/__init__.py:18`) — resolves to this folder; locates `{lang}.json` at runtime.
  - `_CACHE: dict[str, dict[str, str]]` (`i18n/__init__.py:19`) — in-memory cache, lazy-loaded per language on first access.
  - `_DISK_LOADED: set[str]` (`i18n/__init__.py:25`) — languages whose on-disk kernel catalog has been merged into `_CACHE`, tracked separately so a `register_strings`-only language (e.g. the tools bridge registering before any kernel `t()` call) doesn't suppress the kernel's own disk load.
  - `_load(lang)` (`i18n/__init__.py:28-45`) — loads and caches a locale file (registered strings win over on-disk defaults for the same key); returns `{}` if the JSON file is missing.
  - `register_strings(lang, strings)` (`i18n/__init__.py:48-58`) — additive merge of external strings into `_CACHE`; its docstring names the wrapper as the caller.
  - `t(lang, key, **kwargs)` (`i18n/__init__.py:61-77`) — loads the locale, looks up the key, falls back to English, then to the raw key string, and formats with `defaultdict(str, kwargs)`.
- `en.json` — English (baseline). **6 keys under 1 prefix**: `system.` (the `insight.` prefix left with the removed auto-insight path). Other prefixes referenced by callers below (`context.`, `system_tool.`, `email.`) are not in this file — they are registered into the shared `_CACHE` at runtime by the tools bridge (see "Inbound — tools bridge" below), not shipped as kernel-owned disk defaults.
- `zh.json` — 中文. Mirror of en.json; same key set.
- `wen.json` — 文言. Mirror of en.json in Classical Chinese register; same key set.

## Connections

**Inbound — kernel callers.** Callers pass the configured language into `t()`:

| Caller | Citation | Typical keys |
|---|---|---|
| `meta_block.py` | `meta_block.py:3243`, `meta_block.py:3264-3265` | `system.current_time`, `system.context_unknown`, context fragments |
| `lingtai/tools/system/` | `src/lingtai/tools/system/preset.py:202`, `src/lingtai/tools/system/karma.py:98`, `src/lingtai/tools/system/karma.py:117` | `system_tool.*` runtime manager prose |
| `lingtai/tools/context/` | `src/lingtai/tools/context/_molt.py:551`, `src/lingtai/tools/context/_molt.py:599-603`, `src/lingtai/tools/context/_molt.py:780` | `context.*` runtime manager prose (renamed from `psyche.*`) |
| `lingtai/tools/email/` | `src/lingtai/tools/email/primitives.py:290` | `email.*` runtime manager prose |

**Inbound — tools bridge.** The tools string catalog `src/lingtai/tools/i18n/__init__.py` loads every locale table (`src/lingtai/tools/i18n/__init__.py:39`) and pushes all keys into the kernel cache via `register_strings()` (`_register_all` at `src/lingtai/tools/i18n/__init__.py:46`, calling `register_strings` at `src/lingtai/tools/i18n/__init__.py:48`), triggered on import of `lingtai.tools.registry`. The kernel side is only the additive merge hook (`i18n/__init__.py:48-58`).

**Outbound — none.** This module has no imports beyond `json`, `pathlib`, and `collections.defaultdict` (`i18n/__init__.py:15-16`, `i18n/__init__.py:67`). It is a leaf dependency.

## Composition

- **Parent:** `src/lingtai/kernel/` (see `ANATOMY.md`).
- **Siblings:** `intrinsics/` is the main consumer; other kernel modules call `t()` where they render user-facing system text.
- **No subfolders.** This is a flat leaf.

## State

- **On-disk:** `en.json`, `zh.json`, `wen.json` — read-only at runtime. Source of truth for kernel-level string translations. Edited by developers; not mutated by the agent.
- **In-memory:** `_CACHE` (`i18n/__init__.py:19`) — a process-lifetime dict of `{lang: {key: value}}`. Populated lazily by `_load()`, extended additively by `register_strings()`. Never persisted back to disk. Lost on process restart or `system(refresh)`.

## Notes

- **Fallback chain:** `t("zh", "foo")` → check the merged `zh` table (registered strings, then on-disk `zh.json`) → check the merged `en` table → return `"foo"` (`i18n/__init__.py:69-74`). The key-as-fallback behavior makes missing translations visible rather than fatal.
- **Template vars:** `format_map(defaultdict(str, ...))` means missing placeholder values render as empty strings instead of raising (`i18n/__init__.py:75-76`).
- **No pluralisation or ICU.** The system is flat key-value with Python `str.format`. Complex linguistic features (plural forms, gendered agreement) are handled by having per-locale templates that embed the logic, not by the engine.
- **The tools bridge is one-directional.** The tools catalog calls `register_strings()` into the kernel cache; this folder never imports tool code (`src/lingtai/tools/i18n/__init__.py:46`, `i18n/__init__.py:48-58`).
