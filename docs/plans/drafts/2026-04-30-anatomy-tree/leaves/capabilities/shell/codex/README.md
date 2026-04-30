# Codex (Persistent Knowledge Store)

> **Capability:** codex
> **Module:** `lingtai/core/codex/__init__.py`
> **Name collision warning:** "codex" refers to TWO unrelated subsystems in the kernel. This leaf covers the **knowledge store** (`core/codex/`). The other is the **codex LLM provider** — an OAuth-authenticated ChatGPT backend adapter (`auth/codex.py` + `llm/_register.py` line 54–82). See `codex/oauth-originator/` for that one. They share a name because "codex" historically meant the ChatGPT product, and the knowledge store was built alongside it.

---

## What

Codex is a structured knowledge archive persisted in `codex/codex.json`. Agents submit, browse, read, consolidate, delete, and export entries. It is completely decoupled from psyche and memory — the agent decides what to do with retrieved knowledge.

Codex survives molt, restart, and suspend — the permanent layer of the durability hierarchy.

---

## Contract

### Storage

- **File:** `{agent_dir}/codex/codex.json` — format: `{"version": 1, "entries": [...]}`
- **Max entries:** 20 (default, configurable via `codex_limit` in `setup()`)
- **Persistence:** Atomic write via `tempfile.mkstemp()` → `os.replace()`

### Entry schema

```json
{
    "id": "<8-char sha256 prefix>",
    "title": "<string>", "summary": "<string>",
    "content": "<string>", "supplementary": "<string>",
    "created_at": "<ISO-8601 UTC>"
}
```

ID generation: `sha256(title + content + created_at)[:8]`. Identical content at different times → different IDs (no dedup).

### Actions

| Action | Required params | Returns |
|---|---|---|
| `submit` | `title`, `summary`, `content` | `{status, id, entries, max}` |
| `filter` | (none; optional `pattern`, `limit`) | `{status, entries: [{id, title, summary}]}` |
| `view` | `ids` | `{status, entries: [{id, title, summary, content, ?supplementary}]}` |
| `consolidate` | `ids`, `title`, `summary`, `content` | `{status, id, removed}` |
| `delete` | `ids` | `{status, removed}` |
| `export` | `ids` | `{status, files, count}` |

### Key behaviors

- **Filter**: regex (`re.IGNORECASE`) across title/summary/content. Returns id+title+summary only.
- **View**: `depth="content"` (default) or `depth="supplementary"`.
- **Consolidate**: removes old entries → creates merged entry → atomic save.
- **Export**: writes `{agent_dir}/exports/{id}.txt` with title, content, optional supplementary. Returns relative paths for `psyche(pad, edit, files=[...])`.
- **System prompt catalog**: on every mutation, `_inject_catalog()` rewrites the codex section with entry index + usage hints.

### Error cases

- Missing required fields → `{error: "..."}`
- Codex full on submit → `{error: "Codex is full (20 entries)...", entries, max}`
- Unknown IDs → `{error: "Unknown codex IDs: ..."}`
- Invalid regex → `{error: "Invalid regex pattern: ..."}`

---

## Source

All references to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| Schema + `CodexManager` class | `lingtai/core/codex/__init__.py` | 33-356 |
| `DEFAULT_MAX_ENTRIES = 20` | `lingtai/core/codex/__init__.py` | 85 |
| `_inject_catalog()` | `lingtai/core/codex/__init__.py` | 100-118 |
| `_load_entries()` / `_save_entries()` | `lingtai/core/codex/__init__.py` | 124-156 |
| `_make_id()` SHA-256 | `lingtai/core/codex/__init__.py` | 158-162 |
| `_submit()` / `_filter()` / `_view()` | `lingtai/core/codex/__init__.py` | 184-272 |
| `_consolidate()` / `_delete()` / `_export()` | `lingtai/core/codex/__init__.py` | 274-356 |
| `setup()` entry point | `lingtai/core/codex/__init__.py` | 359-372 |

---

## Related

| Leaf | Relationship |
|---|---|
| `codex/oauth-originator` | OAuth token manager for the "codex" LLM provider (separate from the knowledge store) |
| `bash` | Separate capability; bash is execution, codex is knowledge |

---

## Exploration notes

Anatomy leaves are meant to record what the system does. During this review, the leaves also surfaced what it does not:

1. **Provenance gap** — codex entries do not record source or identity.
2. **Name collision** — `"codex"` names both the knowledge store and the LLM provider.
3. **Migration risk** - renaming the LLM provider can break existing presets if backward compatibility is not preserved.

These findings are recorded alongside the leaves because a good anatomy is not only descriptive. It should help future reviewers distinguish what is current behavior from what is missing, risky, or overdue for cleanup.
