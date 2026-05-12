# Knowledge capability contract

`knowledge` is the agent-private durable knowledge capability. It stores bounded,
curated entries that survive molts and are summarized into the agent's system
prompt. The implementation lives in `src/lingtai/core/knowledge/`; the code is
the source of truth.

## Routing Card

**Use this when:**
- You are editing the private durable knowledge capability.
- You are reviewing tool schema, persistence, prompt-injection, capacity, or rename changes.
- You need to verify the boundary between private knowledge and portable skills.

**Do not use this for:**
- Skill catalog behavior: read `src/lingtai/core/skills/`.
- Code navigation only: read `src/lingtai/core/knowledge/ANATOMY.md`.
- General procedure authoring: read the `skills-manual` skill.

**Fast paths:** tool schema -> §Tool surface; storage -> §Persistence; breaking rename -> §Scope; review -> §Verification matrix.

## Scope

- Canonical capability name: `knowledge`.
- Canonical tool name: `knowledge`.
- Former names `library` and `codex` are intentionally not compatibility aliases.

This is a breaking rename while the user base is still small. New manifests must
spell the private durable store as `knowledge`. Old `library`/`codex` capability
entries are skipped as unknown capabilities; old `library(...)`/`codex(...)` tool
calls are unavailable.

`knowledge` means private durable memory: what one agent has learned, decided,
and discovered. `skills` means portable procedure catalog. Knowledge entries may
point to public skills; skills must not depend on private knowledge entry ids,
agent-local paths, mail ids, or other private memory state.

## Knowledge / skill directionality

Knowledge entries MAY reference skills by public path/name when an agent has
learned that a skill is useful for a recurring situation.

Skills MUST NOT reference private knowledge entry ids, private agent paths, mail
ids, or agent-local memory state.

Reason: skills are portable shared procedures; knowledge is agent-local
accumulated memory. The dependency direction is knowledge -> skill, never skill
-> private knowledge.

## Tool surface

The schema requires `action` and accepts exactly four actions:

| Action | Required fields | Optional fields | Return on success |
|---|---|---|---|
| `submit` | `title`, `summary` | `content`, `supplementary` | `{status: "ok", id, entries, max}` |
| `view` | `ids` | `include_supplementary` | `{status: "ok", entries: [...]}` |
| `consolidate` | `ids`, `title`, `summary` | `content`, `supplementary` | `{status: "ok", id, removed}` |
| `delete` | `ids` | — | `{status: "ok", removed}` |

Unknown actions return an error and do not mutate state. Removed historical
actions such as `filter` and `export` are intentionally rejected.

Only `knowledge(...)` is registered. There is no `library(...)` or `codex(...)`
alias.

## Persistence

The store path is `<agent>/knowledge/knowledge.json`. File shape:

```json
{"version": 1, "entries": [ ... ]}
```

Writes are atomic within `knowledge/`: create a temporary file, write UTF-8 JSON
with `ensure_ascii=False`, close it, then `os.replace()` it over
`knowledge.json`. Reads are tolerant: missing/invalid/unreadable JSON means an
empty store; legacy entries without `title` are backfilled from old `content`.

No automatic storage migration from the old `<agent>/codex/codex.json` path is
performed in this change.

## Prompt injection

On setup and after every mutating action, the capability rewrites protected
prompt section `knowledge`:

- If there are entries, the section contains a compact catalog: total count/max
  count plus one line per entry with `[id] title: summary`, followed by a
  reminder to call `knowledge(view, ids=[...])`.
- If there are no entries, the section is cleared.

Only ids, titles, and summaries are always injected. Full `content` and
`supplementary` stay out of the prompt until loaded through `view`.

## Progressive disclosure

Knowledge is exposed in layers:

1. Prompt catalog: a compact index with `id`, `title`, and `summary` only.
2. `knowledge(view, ids=[...])`: full `content` for selected entries.
3. `include_supplementary=true`: backing material only when explicitly needed.

This keeps the always-on prompt small while preserving deep recall on demand.
The prompt catalog must never include full `content` or `supplementary`; tests
lock this behavior because leaking deep material into the system prompt defeats
the progressive-disclosure contract.

## Capacity configuration

`KnowledgeManager.DEFAULT_MAX_ENTRIES` is `50`. `knowledge_limit=N` overrides the
default. Old `library_limit` and `codex_limit` kwargs are not accepted.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `knowledge` is the only private durable memory capability in the builtin registry | `src/lingtai/capabilities/__init__.py` | `tests/test_check_caps.py::test_get_all_providers_includes_expected_capabilities` |
| `knowledge` setup registers only the `knowledge` tool | `src/lingtai/core/knowledge/__init__.py` | `tests/test_knowledge.py::test_knowledge_setup_registers_only_knowledge_tool` |
| Manager lookup is exact: `knowledge` resolves and former names do not | `src/lingtai/agent.py` | `tests/test_knowledge.py::test_knowledge_manager_accessible_by_exact_name` |
| Prompt catalog lives in protected `knowledge` section | `src/lingtai/core/knowledge/__init__.py` | `tests/test_knowledge.py::test_knowledge_tool_uses_knowledge_store` |
| Store path is `knowledge/knowledge.json` | `src/lingtai/core/knowledge/__init__.py` | `tests/test_knowledge.py::test_submit_creates_entry` |
| `knowledge_limit` controls capacity | `src/lingtai/core/knowledge/__init__.py` | `tests/test_knowledge.py::test_submit_enforces_limit` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| `knowledge(...)` is the only durable-memory tool | `tests/test_knowledge.py` | Boot with `capabilities={"knowledge": {}}` and inspect tools | Old names linger and confuse the model |
| Former `library`/`codex` names do not register | alias-removal tests in `tests/test_knowledge.py` / `tests/test_skills.py` | Boot old manifests and inspect capability skip logs | Breaking rename is only half-applied |
| Skills do not depend on private knowledge | documented invariant; enforce by review | Check shared skill docs for private ids/paths | Shared skills become non-portable |
| Full content stays out of prompt catalog | `test_view_returns_content` plus prompt inspection | Submit long content, inspect prompt section | Prompt bloat / private detail leakage |

Run before merging knowledge changes:

```bash
python -m pytest tests/test_knowledge.py tests/test_skills.py tests/test_check_caps.py tests/test_daemon_preset_capabilities.py -q
```
