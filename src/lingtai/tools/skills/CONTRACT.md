---
name: skills-contract
tool: skills
contract_version: 2
related_files:
  - src/lingtai/tools/skills/__init__.py
  - src/lingtai/tools/skills/ANATOMY.md
  - src/lingtai/tools/_catalog.py
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/tool_family/__init__.py
  - src/lingtai/kernel/tool_result_summary.py
  - tests/test_skills.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Skills capability contract

`skills` is the per-agent, portable skill catalog. It scans the agent's
`.library/{intrinsic,custom}/` plus any declared Tier-1 paths, builds a compact
YAML catalog, and injects it into the protected `skills` system-prompt section.
It is pure presentation: it never writes to `.library/`. The implementation lives
in `src/lingtai/tools/skills/__init__.py`; the code is the source of truth.

## Routing Card
Guarded by: [SK001](BEHAVIORS.md#behavior-sk001)


**Use this when:**
- You are editing the catalog scanner, path resolution, prompt injection, or the
  `info` / `manual` action split.
- You need to verify the skills/knowledge boundary (portable procedures vs.
  private durable memory).

**Do not use this for:**
- Private durable memory: read `src/lingtai/tools/knowledge/CONTRACT.md` (the
  structurally isomorphic, physically separate sibling).
- Code navigation only: read `src/lingtai/tools/skills/ANATOMY.md`.
- Shared Markdown catalog mechanics: read `src/lingtai/tools/_catalog.py`.
- The generic envelope/dispatch/manual-child infrastructure itself: read
  `src/lingtai/tools/tool_family/CONTRACT.md`; the protocol it implements is
  `src/lingtai/tools/CONTRACT.md`.

**Fast paths:** tool schema -> §Tool surface; on-disk layout & path sources ->
§State & storage; skills vs knowledge -> §Scope.

## Scope

- Canonical capability / tool name: `skills`.
- Former names `library` and `codex` are intentionally NOT compatibility aliases;
  old configs are skipped and their tools are not registered.
- `skills` means the portable procedure catalog. Skills MUST NOT depend on
  private knowledge entry contents, agent-local paths, mail ids, or private
  memory state; the dependency direction is knowledge → skill, never the reverse.
- Non-goals: it does not create or populate `.library/` (the Agent initializer's
  `_install_intrinsic_manuals` does that), and it does not author skills — the
  agent writes `SKILL.md` files with `write`/`edit`.
- Path sources scanned: `.library/intrinsic/`, `.library/custom/`, and each entry
  of `manifest.capabilities.skills.paths` (absolute, workdir-relative, or
  tilde-prefixed).

## Tool surface

`skills` is a family migrated to the LingTai Tool Protocol v2 shape defined in
`src/lingtai/tools/CONTRACT.md`, built on the generic
`src/lingtai/tools/tool_family/` infrastructure. The public tool name and both
public action values are unchanged by that migration; only the envelope around
them is canonical now.

The final model-facing root is a closed object whose properties are exactly
`action`, `input`, `reasoning`, and `summarize`, with
`required: ["action", "input", "reasoning"]` and
`additionalProperties: false`. `action` is one of `info` or `manual`; each
action value equals its child name and its dispatch key, with no mapping layer.
Both actions declare the canonical **strict-empty** `input` object
(`{"type": "object", "properties": {}, "required": [], "additionalProperties":
false}`) — there is no field to pass on either action. Root `summarize` is an
optional boolean (absent or false by default) and `reasoning` is required Host
InvocationContext/audit metadata; neither is ever action input, and no `input`
branch admits `reasoning`, `_reasoning`, or `summarize`. The root also carries
one `allOf` `if`/`then` condition per action correlating the `action` const
with that exact action's `input` schema, on both the Chat Completions and
Responses wires.

The child registry is declared exactly once, in `_build_family`: both the
advertised schema and runtime dispatch are built from it, so action names,
input schemas, titles, and order cannot drift apart. The handler is
`handle_skills`, which delegates envelope validation and dispatch to an
agent-bound `ToolFamily` and owns only the post-dispatch presentation
adaptation of the reserved `manual` child. Exactly two actions.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `info` | `action="info"`, `input={}`, `reasoning` | root `summarize` | reconciles catalog, re-injects prompt, returns `{status, skills_dir, library_dir, catalog_size, paths, problems}` (manual body omitted) | see below |
| `manual` | `action="manual"`, `input={}`, `reasoning` | root `summarize` | `{status: "ok", skills_manual, library_manual, manual_path}` — manual body without refreshing catalog | degraded shape below |

Status is `"ok"` normally; `info` and `manual` return `status: "degraded"` (with
an `error` string and empty manual body) when the skills manual
(`.library/intrinsic/capabilities/skills/SKILL.md`) is missing. `library_manual`
and `library_dir` are back-compat keys mirroring the `skills_*` keys; the on-disk
directory remains `.library`.

Each action's own success/degraded result stays canonical and is returned
verbatim — there is no child-result envelope nested inside another action
result. `manual`'s reserved child (`tool_family.manual.build_manual_child`)
returns the canonical `content`/`structuredContent` MCP-compatible shape;
`handle_skills` flattens exactly that, strictly after dispatch, to the public
`skills_manual`/`library_manual`/`manual_path` keys above (no double wrap).

**Action separation.** `info` only refreshes/reconciles the catalogue and
reports health: it never authors, pins, publishes, installs, or executes a
skill. `manual` only reads the installed manual: it performs no catalogue
scan, no prompt injection, and no other `info`-side effect.

**Error shapes** (plain dicts). Envelope failures are raised by the generic
dispatcher before any handler I/O and are returned verbatim — `skills` has no
family-specific diagnostic block to stamp onto them:
- Unknown action (including a missing action key, and an unhashable `action`
  from invalid JSON): `{"status": "failed", "error_code": "ACTION_REQUIRED", "message": "action must be one of info, manual"}`.
- Any key inside `input` on either action:
  `{"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "unsupported skills input field"}`.
- Missing or non-object `input`:
  `{"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "input must be an object"}`.
- Unknown root field:
  `{"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "unsupported skills argument"}`.
- Non-boolean root `summarize`:
  `{"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "summarize must be a boolean"}`.

This replaces the pre-migration router envelope
(`{"status": "error", "message": "unknown action: ..."}`), which no longer
exists on this tool.

## Summarize profile

Per `src/lingtai/tools/CONTRACT.md` "Dispatch and actions", this family
assigns profiles per action: `info` is **short-result** (its health snapshot is
normally small — `summarize` is available but normally unnecessary, leave it
false), and `manual` is **bulky-result** (`summarize=true` is reasonable for a
gist, but calls meant to follow the exact procedure should keep the default
`false`). `skills` is listed in
`src/lingtai/kernel/tool_result_summary.py`'s `_LTP_V2_MIGRATED_FAMILIES`, so
its root `summarize` spelling is recognized as the canonical a-priori summary
control and its canonical `status: "failed"` envelope errors are never
summarized. The existing raw-first cap and centralized summarizer remain the
single source; this family adds no second summarizer.

## State & storage

The capability reads (never writes) the per-agent skill store:

```text
<agent>/.library/
  intrinsic/
    capabilities/<cap>/SKILL.md      # manuals installed by the initializer
    addons/<addon>/
  custom/                            # agent-authored skills (kernel never touches)
```

Plus each `manifest.capabilities.skills.paths` entry, scanned recursively. Each
skill is a directory containing a `SKILL.md` with `name` + `description`
frontmatter; entries missing either are surfaced in `problems`. Only
`name`/`description`/`path` are injected into the prompt — bodies stay on disk
until read. Scanning uses the shared `scan_markdown_catalog` /
`build_catalog_yaml` helpers in `src/lingtai/tools/_catalog.py`.

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **Path handling:** `_resolve_path` expands `~`, uses absolute paths as-is, and
  resolves relative declared paths against `agent._working_dir`. The on-disk root
  stays `.library` for compatibility even though the tool/keys are named `skills`.
- **Prompt injection:** the catalog is written to the protected `skills` section
  via `agent.update_system_prompt("skills", ..., protected=True)`; empty catalog
  clears the section.
- **Encoding:** `SKILL.md` bodies are read as UTF-8.

## Anchored claims

| Claim | Source `src/lingtai/tools/skills/...` | Test |
|---|---|---|
| Unknown actions return the typed `ACTION_REQUIRED` failure | `__init__.py` (`handle_skills`) | `tests/test_skills.py::test_unknown_action_returns_error` |
| Unknown actions do no handler I/O | `__init__.py` (`handle_skills`) | `tests/test_skills.py::test_unknown_action_fails_before_any_handler_io` |
| Root is the closed LTP v2 envelope with strict-empty inputs | `__init__.py` (`get_schema`) | `tests/test_skills.py::test_family_schema_is_the_canonical_ltp_v2_root` |
| Schema and dispatch come from one child registry | `__init__.py` (`_build_family`) | `tests/test_skills.py::test_schema_children_are_the_same_registry_dispatch_uses` |
| Both actions reject any `input` key / bad envelope fields | `__init__.py` (`handle_skills`) | `tests/test_skills.py::test_both_actions_reject_extra_input_before_dispatch` |
| `reasoning`/`_reasoning`/`summarize` never reach a handler | `__init__.py` (`handle_skills`) | `tests/test_skills.py::test_envelope_metadata_never_reaches_either_handler` |
| The composed schema survives both provider wires | `__init__.py` (`get_schema`) | `tests/test_skills.py::test_skills_family_reaches_both_provider_wires` |
| Root `summarize` is this family's canonical control | `src/lingtai/kernel/tool_result_summary.py` | `tests/test_skills.py::test_skills_is_a_migrated_ltp_v2_family_for_summarize` |
| `info` omits the manual body | `__init__.py` (`_skills_info`) | `tests/test_skills.py::test_info_omits_skills_manual_body` |
| `info` returns the exact health/problem report | `__init__.py` (`_reconcile`) | `tests/test_skills.py::test_info_result_keys_and_health_are_exactly_preserved` |
| `manual` returns the skills manual body | `__init__.py` (`_adapt_manual_result`) | `tests/test_skills.py::test_manual_returns_skills_manual_body` |
| `manual` returns exact body/path with no double wrap | `__init__.py` (`_adapt_manual_result`) | `tests/test_skills.py::test_manual_result_is_exact_body_and_path_without_double_wrap` |
| `manual` performs no `info`-side catalogue mutation | `__init__.py` (`handle_skills`) | `tests/test_skills.py::test_manual_has_no_info_side_effect` |
| `manual` degrades with the exact loader message | `__init__.py` (`_adapt_manual_result`) | `tests/test_skills.py::test_manual_degrades_with_exact_loader_message` |
| Missing intrinsic manual reports `degraded` | `__init__.py` (`_reconcile`) | `tests/test_skills.py::test_info_reports_degraded_when_intrinsic_missing` |
| Declared paths resolve (absolute / relative / `~`) | `__init__.py` (`_resolve_path`) | `tests/test_skills.py::test_skills_scans_absolute_path`, `::test_skills_resolves_relative_path_from_working_dir`, `::test_skills_expands_tilde` |
| Catalog is injected into the `skills` prompt section | `__init__.py` (`_reconcile`) | `tests/test_skills.py::test_catalog_injected_into_skills_section` |
| Former `library`/`codex` configs do not register legacy tools | `__init__.py` / registry | `tests/test_skills.py::test_former_library_config_does_not_register_library_tool` |
| Catalog scan/parse helpers behave per spec | `src/lingtai/tools/_catalog.py` | `tests/test_catalog_helpers.py::test_scan_recurses_and_sorts` |
| `SKILL.md` frontmatter validation | `src/lingtai/tools/_catalog.py` | `tests/test_validate_skill.py` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| No public `skills` tool is registered | `tests/test_skills.py::test_skills_registers_no_public_tool` | Inspect the built tool schemas for a `skills` root | Retired root silently returns |
| The manual loads only via `psyche` | `tests/test_psyche_family.py::test_each_action_returns_its_intended_manual` | Call `psyche(action="skills", input={}, reasoning="x")` | Domain manual unreachable |
| Loading the manual never mutates the catalogue | `tests/test_skills.py::test_psyche_skills_manual_has_no_catalog_side_effect` | Add a skill on disk, load the manual, inspect the `skills` prompt section | Signpost silently reconciles; hidden side effect |
| Catalog reaches the prompt | `tests/test_skills.py::test_catalog_injected_into_skills_section` | Boot with a custom skill, inspect `skills` prompt section | Skills invisible to the model |
| Body stays out of prompt | `tests/test_catalog_helpers.py::test_build_catalog_yaml_golden` | Author a long-body skill, inspect prompt | Prompt bloat |
| Missing manual is degraded not fatal | `tests/test_skills.py::test_manual_degrades_with_exact_loader_message` | Remove the intrinsic manual, load it via `psyche` | Boot failure vs. graceful degrade |
| Legacy names do not register | `tests/test_skills.py::test_former_library_config_does_not_register_library_tool` | Boot an old `library` manifest, inspect tools | Half-applied rename confuses model |

Run before merging:

```bash
python -m pytest tests/test_skills.py tests/test_validate_skill.py tests/test_catalog_helpers.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters resolve the top-level tool description
  through `wire_tool_description`: the global `WIRE_TOOL_DESCRIPTION` pointer
  while the resident `## tools` section is opted in via
  `LINGTAI_TOOL_PROSE_SECTION_ENABLED`, otherwise the full
  `FunctionSchema.description` prose (that section is off by default, so the
  wire is where the canonical prose lands). Nested parameter descriptions are
  unchanged either way.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
