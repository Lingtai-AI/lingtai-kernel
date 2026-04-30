# psyche/core-memories

## What

Core memories are the three persistent psyche stores that survive molt (context
reset), restart, and process death: **lingtai** (identity/character),
**pad** (workspace notes), and **context** (molt lifecycle). The psyche
capability wraps the eigen intrinsic with enhanced features (file imports,
pinned references) and manages the agent's long-term self.

**Survival semantics:** Eigen's `_context_molt()` does NOT touch
`system/lingtai.md` or `system/pad.md` — those files persist on disk untouched.
What molt destroys is the wire session (chat history → archived). The
`_post_molt_hooks` then re-inject the unchanged files into the new prompt
manager via `_lingtai_load()` + `_pad_load()`. So "survives molt" means:
*file untouched, prompt re-injected* — not *file rewritten by a hook*.

## Contract

### Lingtai (identity)

| Aspect | Value |
|--------|-------|
| File | `system/lingtai.md` |
| Actions | `update` (full rewrite + auto-load), `load` (re-read from disk into prompt) |
| Prompt section | `covenant` — combined with `system/covenant.md` if present |
| Load behavior | Reads `system/covenant.md` + `system/lingtai.md`, joins with `\n\n`, writes to `covenant` prompt section (protected). Deletes section if both empty. |
| Persistence | File on disk untouched by molt. Post-molt hook calls `_lingtai_load()` to re-inject into prompt. |

### Pad (workspace)

| Aspect | Value |
|--------|-------|
| File | `system/pad.md` |
| Actions | `edit` (write + auto-load), `load` (re-read + append pinned), `append` (set/clear pinned files) |
| Edit extras | `files` parameter: array of file paths appended as `[file-N]\n<content>` after the main content. |
| Append mechanism | `system/pad_append.json` persists the pinned file list. On `load`, pinned files are read and appended under `# 📎 Reference (read-only)` divider. |
| Append limit | 100,000 tokens total for pinned files. |
| Binary guard | `_is_text_file()` rejects files with null bytes in first 8KB. |
| Prompt section | `pad` — auto-loaded on edit/append/molt. |
| Persistence | File on disk untouched by molt. Post-molt hook calls `_pad_load()` to re-inject into prompt. |

### Context (molt)

| Aspect | Value |
|--------|-------|
| Action | `molt` — requires `summary` parameter (non-empty). |
| Mechanism | Archives `history/chat_history.jsonl` → `history/chat_history_archive.jsonl`, deletes current history, resets soul cursor, runs `_post_molt_hooks` (re-inject lingtai + pad into prompt), creates fresh session, injects summary as opening message. Note: eigen's `_context_molt()` does NOT touch `system/lingtai.md` or `system/pad.md`. |
| Warning ladder | 70% → first warning; 95% → unconditional force. System calls `context_forget()` if warnings ignored. |
| Post-molt | `_post_molt_hooks` reload lingtai + pad into the prompt manager before the new session starts. |
| Not cleared | `logs/soul_flow.jsonl` persists across molt (append-only, no rotation). The soul *session* (`history/soul_history.jsonl`) also persists — only the diary cursor resets. |

**Key invariant:** All three stores live on disk under `system/` and are
reloadable at any time. The prompt manager sections (`covenant`, `pad`) are
the in-memory projection — `load` re-syncs from disk.

## Source

- Psyche manager: `core/psyche/__init__.py:66` — `PsycheManager`
- Lingtai update: `core/psyche/__init__.py:112` — `_lingtai_update()`
- Lingtai load: `core/psyche/__init__.py:120` — `_lingtai_load()`
- Pad edit (with files): `core/psyche/__init__.py:150` — `_pad_edit()`
- Pad append: `core/psyche/__init__.py:244` — `_pad_append()`
- Pad load (with pinned): `core/psyche/__init__.py:290` — `_pad_load()`
- Context molt: `core/psyche/__init__.py:317` — delegates to eigen
- Eigen molt: `intrinsics/eigen.py:124` — `_context_molt()`
- Post-molt hooks: `core/psyche/__init__.py:334` — registered in `setup()`
- Forced molt: `intrinsics/eigen.py:218` — `context_forget()`

## Related

- **eigen intrinsic** — the underlying pad/molt implementation that psyche wraps.
- **codex** — separate permanent store for verifiable truths (not part of psyche).
- **library** — separate store for reusable skills (not part of psyche).
- **soul** — `reset_soul_session()` called during molt to reset the diary cursor.
