---
related_files:
  - src/lingtai/i18n/ANATOMY.md
  - src/lingtai/i18n/__init__.py
  - src/lingtai_kernel/ANATOMY.md
  - src/lingtai_kernel/i18n/__init__.py
  - src/lingtai_kernel/i18n/en.json
  - src/lingtai_kernel/i18n/wen.json
  - src/lingtai_kernel/i18n/zh.json
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# i18n

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues/mail/PR proposals; do not silently fix.

The kernel's message catalog — a flat key-value string table covering system notifications, soul prompts, molt instructions, and runtime manager prose in three locales (en / zh / wen). Model-facing tool schema/description text is no longer catalog-owned; it lives in canonical English tool source code and per-package glossary resources (`glossary-{en,zh,wen}.md`). The sole entry point is `t(lang, key, **kwargs)` which resolves a dotted key against the agent's configured language, falling back to English and then to the raw key itself.

## Components

- `__init__.py` — the entire module (60 lines). Three public symbols and one module-level cache:
  - `_DIR` (`i18n/__init__.py:18`) — resolves to this folder; locates `{lang}.json` at runtime.
  - `_CACHE: dict[str, dict[str, str]]` (`i18n/__init__.py:19`) — in-memory cache, lazy-loaded per language on first access.
  - `_load(lang)` (`i18n/__init__.py:22-30`) — loads and caches a locale file; returns `{}` if the JSON file is missing.
  - `register_strings(lang, strings)` (`i18n/__init__.py:33-41`) — additive merge of external strings into `_CACHE`; its docstring names the wrapper as the caller.
  - `t(lang, key, **kwargs)` (`i18n/__init__.py:44-60`) — loads the locale, looks up the key, falls back to English, then to the raw key string, and formats with `defaultdict(str, kwargs)`.
- `en.json` — English (baseline). ~80 keys across 7 prefixes: `system.`, `soul.`, `insight.`, `psyche.`, `system_tool.`, `tool.`, `email.`.
- `zh.json` — 中文. Mirror of en.json; same key set.
- `wen.json` — 文言. Mirror of en.json in Classical Chinese register; same key set.

## Connections

**Inbound — kernel callers.** Callers pass the configured language into `t()`:

| Caller | Citation | Typical keys |
|---|---|---|
| `meta_block.py` | `meta_block.py:151-176` | `system.current_time`, context fragments |
| `tools/system/` | `src/tools/system/preset.py:223`, `src/tools/system/karma.py:71`, `src/tools/system/karma.py:90` | `system_tool.*` runtime manager prose |
| `tools/psyche/` | `src/tools/psyche/_molt.py:490`, `src/tools/psyche/_molt.py:562-566`, `src/tools/psyche/_molt.py:708` | `psyche.*` runtime manager prose |
| `tools/soul/` | `src/tools/soul/config.py:375`, `src/tools/soul/config.py:385`, `src/tools/soul/consultation.py:372` | `soul.*` runtime manager prose |
| `tools/email/` | `src/tools/email/primitives.py:288` | `email.*` runtime manager prose |

**Inbound — tools bridge.** The tools string catalog `src/tools/i18n/__init__.py` loads every locale table (`src/tools/i18n/__init__.py:31`) and pushes all keys into the kernel cache via `register_strings()` (`_register_all` at `src/tools/i18n/__init__.py:38`, calling `register_strings` at `src/tools/i18n/__init__.py:45`), triggered on import of `tools.registry`. The kernel side is only the additive merge hook (`i18n/__init__.py:33-41`).

**Outbound — none.** This module has no imports beyond `json`, `pathlib`, and `collections.defaultdict` (`i18n/__init__.py:15-16`, `i18n/__init__.py:50`). It is a leaf dependency.

## Composition

- **Parent:** `src/lingtai_kernel/` (see `ANATOMY.md`).
- **Siblings:** `intrinsics/` is the main consumer; other kernel modules call `t()` where they render user-facing system text.
- **No subfolders.** This is a flat leaf.

## State

- **On-disk:** `en.json`, `zh.json`, `wen.json` — read-only at runtime. Source of truth for kernel-level string translations. Edited by developers; not mutated by the agent.
- **In-memory:** `_CACHE` (`i18n/__init__.py:19`) — a process-lifetime dict of `{lang: {key: value}}`. Populated lazily by `_load()`, extended additively by `register_strings()`. Never persisted back to disk. Lost on process restart or `system(refresh)`.

## Notes

- **Fallback chain:** `t("zh", "foo")` → check `zh.json["foo"]` → check `en.json["foo"]` → return `"foo"` (`i18n/__init__.py:52-57`). The key-as-fallback behavior makes missing translations visible rather than fatal.
- **Template vars:** `format_map(defaultdict(str, ...))` means missing placeholder values render as empty strings instead of raising (`i18n/__init__.py:47-59`).
- **No pluralisation or ICU.** The system is flat key-value with Python `str.format`. Complex linguistic features (plural forms, gendered agreement) are handled by having per-locale templates that embed the logic, not by the engine.
- **The tools bridge is one-directional.** The tools catalog calls `register_strings()` into the kernel cache; this folder never imports tool code (`src/tools/i18n/__init__.py:38`, `i18n/__init__.py:33-41`).
