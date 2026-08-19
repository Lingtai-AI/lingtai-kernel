---
name: plugin-contract
tool: plugin
contract_version: 1
related_files:
  - src/lingtai/tools/plugin/__init__.py
  - src/lingtai/tools/plugin/ANATOMY.md
  - src/lingtai/services/plugin_registry.py
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/mcp/CONTRACT.md
  - src/lingtai/tools/skills/__init__.py
  - docs/examples/agent-plugins/hello-lingtai/plugin.json
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits. plugin's
  schema composition and envelope dispatch build on the generic tool_family
  package, and its surface is deliberately the twin of mcp's; keep both links
  current when either side's boundary changes. The Agent Plugins version this
  kernel understands is pinned in the service as a canonical identifier — bump
  it here and there together, never independently.
---

# Plugin capability contract

`plugin` is a read-only tool: it renders the per-agent **Agent Plugins**
(agent-plugins.org, v1.0.0) catalog and boot registration snapshot into the
protected `plugin` system-prompt section, and reports what mounted, what did
not, and why. **The tool itself mutates nothing.** Mounting happens once, at
boot/refresh, in `services.plugin_registry.register_plugins`, which `Agent` calls
before capability setup — no model-facing action can reach it. The tool slice
lives in `src/lingtai/tools/plugin/__init__.py`; the scanning, registration, and
pruning machinery lives in `src/lingtai/services/plugin_registry.py` (imported
lazily). The code is the source of truth.

## The two-tier mount contract
Guarded by: [PL001](BEHAVIORS.md#behavior-pl001)


This is the load-bearing distinction in this package; everything else follows.

| Tier | Declared where | Effect |
|---|---|---|
| **registered** | `init.json` `manifest.plugins` (canonical) or `manifest.capabilities.plugin.paths` (retained alias, same meaning) | Each of the plugin's containment-validated skill directories is composed into the skills-catalog scan; each `mcp.json` server becomes an `mcp_registry.jsonl` record stamped `source="plugin:<name>"` |
| **discovered** | Found on an inherited `manifest.capabilities.skills.paths` directory | Listed only. Nothing composed, nothing registered. |

The asymmetry is a security boundary, not a presentation choice: dropping a
directory where the skills capability happens to scan must never silently
register a third party's MCP server. Only an explicit declaration mounts.

Registration is registry-level, exactly as `addons:[]` is: a plugin's MCP server
becomes registered and visible, **never running**. Activation still requires an
explicit `init.json` top-level `mcp` entry. Nothing is executed and no subprocess
is spawned at registration time.

## Install / uninstall lifecycle

There is no install or uninstall action, by design — both are declaration edits
plus `system(action="refresh")`.

- **Install:** add the plugin directory to `manifest.plugins`, refresh.
- **Uninstall:** remove it from `manifest.plugins`, refresh. Every
  `source="plugin:<name>"` record whose owner is no longer declared is pruned by
  `prune_plugin_records`, and the plugin's skills leave the catalog because
  nothing scans its skill directories any more. **No file is deleted** — the plugin
  directory belongs to whoever put it there.

Pruning also converges a still-declared plugin: a server removed from its
`mcp.json` loses its record, and a changed spec has the stale record dropped and
the fresh one appended. Registration is therefore idempotent — running it twice
leaves the same registry as once.

Records the plugin system does not own are never touched, and neither are blank
or unparseable lines: pruning is a targeted removal, not a rewrite of the file
through a validator that would silently discard a human's broken line.

## Routing Card

**Use this when:**
- You are editing the plugin tool slice's action dispatch or the reconciliation
  that builds the prompt XML.
- You need to confirm which fields `info` surfaces, or that the tool itself
  writes no file and cannot mount or unmount anything.
- You need the canonical declaration key, or the install/uninstall lifecycle.

**Do not use this for:**
- Manifest validation, §4.1 path containment, skills/MCP component discovery,
  registration, pruning, or the XML render: those are the service at
  `src/lingtai/services/plugin_registry.py`.
- Code navigation only: read `src/lingtai/tools/plugin/ANATOMY.md`.
- Actually running a plugin's MCP server: that is `src/lingtai/tools/mcp/CONTRACT.md`
  plus editing `mcp_registry.jsonl` with `write`/`edit`, then
  `system(action="refresh")`.

**Fast paths:** tool schema and the LTP v2 envelope -> §Tool surface; where
plugins are searched for -> §State & storage; the generic
composition/dispatch infrastructure -> `src/lingtai/tools/tool_family/CONTRACT.md`.

## Scope

- Canonical tool name: `plugin`.
- Registered via `capabilities=["plugin"]` or via init.json. Default-on
  (`CORE_DEFAULTS`), which is safe precisely because the capability is pure
  presentation with zero side effects.
- Symmetric to `mcp`: a per-agent presentation capability with a protected
  prompt section and exactly the same two actions.
- Standard implemented: Agent Plugins v1.0.0. The two canonical schema
  identifiers
  (`https://agent-plugins.org/schemas/1.0.0/plugin.schema.json` and
  `.../mcp.schema.json`) are compared as opaque version strings. The kernel MUST
  NOT fetch them during loading; an unrecognized `$schema` is an unsupported
  version, reported as a problem, never a silent downgrade.
- Non-goals: this *tool* never writes any file. It never copies a plugin's
  skills into `.library/` (registration composes the plugin's own validated
  skill directories into the catalog scan instead — nothing is copied anywhere), never appends to
  `mcp_registry.jsonl` from an action, and never spawns a process. It is purely
  `info` (re-scan + report the boot snapshot) and `manual` (return the
  plugin-manual body).
- Ownership boundary: the module is the agent-callable tool slice only. The
  scanning service is imported lazily inside `_reconcile`, per the
  `lingtai.tools → lingtai` lazy-back-edge rule.

## Tool surface

`plugin` is an LTP v2 action-separated family (`src/lingtai/tools/CONTRACT.md`
"Envelope") built on the generic `src/lingtai/tools/tool_family/`
infrastructure. The public tool name is `plugin` and the public action values
are `info` / `manual`. Exactly two read-only actions; the handler is
`handle_plugin`, which delegates envelope validation and dispatch to a per-Agent
`ToolFamily`.

**Envelope.** The model-facing schema is `get_schema()` =
`ToolFamily.build_schema()` with a family-specific `action` description. Root
properties are exactly `action`, `input`, `reasoning`, and `summarize`;
`required` is `[action, input, reasoning]` and the root is closed
(`additionalProperties: false`). `reasoning` is required Host
InvocationContext/audit metadata declared by the family itself, never action
input. `summarize` is the optional root presentation control, validated as
boolean and stripped before dispatch. Both actions take **no arguments**, so
both share the one canonical strict-empty `input`
(`{type: object, properties: {}, additionalProperties: false}`) — the
`MANUAL_INPUT_SCHEMA` literal exported by `tool_family.manual` and reused here
as `_EMPTY_INPUT`, rather than hand-copied per action, so the schema-only and
dispatching families cannot advertise different shapes. The root
`allOf`/`if`/`then` correlates each `action` const with its own `input` branch,
and `input.oneOf` discloses both branches with titles
`info input` / `manual input`.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `info` | `action="info"`, `input={}`, `reasoning` | `summarize` | re-scans the configured paths, re-injects prompt XML, returns `{status: "ok", declared, registered_count, registered, discovered_count, discovered, mcp_appended, mcp_pruned, paths, problems}` | see below |
| `manual` | `action="manual"`, `input={}`, `reasoning` | `summarize` | `{status: "ok", plugin_manual, manual_path}` | degraded shape below |

`registered` is the boot registration snapshot, one entry per declared plugin:
`{name, version, summary, source, homepage?, skills, skill_count,
skills_mounted, mcp_servers, mcp_server_count, mcp_registered, skipped}`.
`skipped` entries are `{component, reason}` and are the single place a
non-mounting component explains itself — an escaping path, a server name outside
the registry grammar, a name collision, or the skills capability being disabled.
`discovered` is the discovery-only tier: `{name, version, summary, skill_count,
mcp_server_count, source, homepage?}`.

`paths` is a per-configured-path report keyed by the raw configured string, each
value `{resolved, exists, plugins}`; it covers the union of the declared paths
and the inherited skills paths, so a plugin declared through the canonical key
alone still reports its health here. `problems` entries are
`{plugin, path, error}`. `manual` returns `status: "degraded"` with an empty
`plugin_manual` and an `error` string when
`.library/intrinsic/capabilities/plugin/SKILL.md` is missing.

`manual` is the family-owned reserved child, registered directly from
`tool_family.manual.build_manual_child(agent, "plugin")`. `ToolFamily.handle()`
returns that child's canonical `content`/`structuredContent` result verbatim
(no double wrap); the flat public shape — body under the tool-specific key
`plugin_manual`, matching the `mcp_manual` precedent — is reconstructed by the
Host-owned `_flatten_manual_result` strictly *after* dispatch returns, never
inside a registered child. `manual` performs no scan.

**Error shapes** (plain dicts):
- Unknown action: `{"status": "error", "message": "unknown action: <action>, only 'info' or 'manual' is supported"}`. This envelope is Host-owned and rendered by `handle_plugin` *before* delegating, for the two reasons `mcp` established: a missing `action` key renders the empty-string default (not `None`), and an unhashable `action` (`[]` / `{}` from invalid JSON — issue #513) must not reach `ToolFamily.handle`'s dict lookup. Membership is tested against `child_names`, a tuple, whose `in` compares by `==` and never hashes. An unknown action is rejected before any input validation or filesystem I/O.
- Invalid envelope/input (from the generic dispatcher, canonical and unwrapped): `{"status": "failed", "error_code": "INVALID_ARGUMENT", "message": ...}` for a non-object `input` (`input must be an object`), an unknown root field (`unsupported plugin argument`), a non-boolean `summarize` (`summarize must be a boolean`), or any `input` key outside the selected action's strict-empty schema (`unsupported plugin input field`). Because both actions declare an empty `input`, any extra input field fails **before** the paths are re-scanned or the manual is loaded.

## State & storage

The **capability** owns no state: it creates no file, no directory, and no
cache. It only reads:

```text
<plugin path>/<plugin>/plugin.json         # required manifest
<plugin path>/<plugin>/skills/*/SKILL.md   # composed into the catalog scan, never copied
<plugin path>/<plugin>/mcp.json            # translated into registry records
```

**Registration** writes in exactly one place, and only at boot/refresh:
`<working_dir>/mcp_registry.jsonl`, and only lines stamped
`source="plugin:<name>"`. It never creates the file when there is nothing to
write, never touches a record it does not own, and never writes anything under
`.library/`.

Declaration paths come from `init.json` `manifest.plugins` (canonical) and
`manifest.capabilities.plugin.paths` (alias), canonical-first and de-duplicated.
The **discovery** scan additionally unions in `manifest.capabilities.skills.paths`,
which the capability inherits because plugins bundle Agent Skills and land in the
same directories operators already declare for skills — inherited paths are
scanned and listed, never registered. Each entry may be absolute,
tilde-prefixed, or relative to the agent working dir. A path is a collection
directory whose immediate children are plugin roots; a directory carrying
`plugin.json` itself is treated as one plugin, so a lone plugin needs no wrapper.

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **No model-facing side effects:** no action writes, copies, registers,
  unregisters, or executes anything. Mounting is boot-only and unreachable from
  the tool surface, which is what keeps the capability safe in `CORE_DEFAULTS`.
- **Declaration gates mounting:** an inherited skills path is scanned and listed
  but never registers anything. Only `manifest.plugins` and its alias mount.
- **Registration never executes:** a registered MCP server holds a registry
  record and nothing more; running it still requires an `init.json` top-level
  `mcp` entry.
- **Registration never overwrites what it does not own:** a name already held by
  a hand-written or addon-decompressed record skips the plugin's server, leaving
  the existing record untouched; between two plugins, first declared wins.
- **Idempotent and convergent:** running registration twice leaves the same
  registry as once, and the registry converges on the current declaration rather
  than accumulating stale records.
- **Prompt injection:** the catalog XML is written to the protected `plugin`
  section via `agent.update_system_prompt("plugin", xml, protected=True)`.
- **Lazy import:** `src/lingtai/services/plugin_registry.py` is imported lazily
  inside `_reconcile`, keeping the `lingtai.tools → lingtai` back-edge deferred.
- **Path containment (§4.1):** every plugin-relative path MUST start with `./`
  (or the equivalent `${PLUGIN_ROOT}/` form) and MUST still resolve inside the
  filesystem-resolved plugin root. This covers `command`, `cwd`, and every entry
  of `args`. Containment is checked *after* `Path.resolve()`, so a symlink
  escape is rejected exactly like a literal `../` escape, and it is enforced at
  registration, not only at display: an escaping server never reaches the
  registry. `resolve_server_spec` is the single gate both tiers pass through, so
  discovery and registration cannot disagree about what is acceptable. The `./`
  prefix is a spelling requirement, not a reachability one: any relative value
  carrying a `..` segment is normalized through the same gate
  (`_expand_plugin_path`), so `../x` is rejected exactly like `./../x`. Absolute
  paths, `${ENV_VAR}` placeholders, and bare tokens without a `..` segment pass
  through — they are not plugin-relative paths, and the manual's containment
  section states this rather than implying every value is checked.
- **Two failure boundaries:** an unreadable or invalid `plugin.json` rejects the
  whole plugin (absent from the catalog, reason in `problems`); an individual
  escaping skill directory or MCP server is skipped while the rest of the plugin
  is still listed. Skipped is enforced, not merely reported, on both halves: the
  escaping server never reaches `mcp_registry.jsonl`, and the escaping skill
  directory is never composed into the skills catalog because registration
  contributes the *per-skill validated directories* (`read_plugin`'s
  `skill_paths`), never the parent `skills/` — whose recursive, symlink-following
  scan would re-admit exactly what was rejected.
- **Reported skills are mounted skills:** `_scan_skills` walks `skills/` with the
  same traversal `tools._catalog._scan_recursive` mounts with (grouping
  directories descended, dot-directories and corrupt directories skipped), so a
  nested `skills/group/nested/SKILL.md` is reported as `group/nested` and counted
  rather than mounting invisibly.
- **Declared fields survive into the record:** `to_registry_record` carries
  `env`/`cwd` (stdio) and `headers` (http) through alongside `command`/`args`/
  `url`; the registry validator ignores keys it does not model. The record is a
  registration, not a launch spec — `Agent._load_mcp_from_workdir` matches by
  `name` and spawns from the `init.json` `mcp` entry's own config.
- **One registration per boot:** `Agent.__init__` calls
  `_register_declared_plugins` only when it was given `capabilities=` or
  `plugins=`. A minimal construction (the CLI path, `src/lingtai/cli.py`) declares
  neither and defers to `_setup_from_init`, which always registers. Without that
  gate the constructor's empty run pruned every plugin-owned record and
  `_setup_from_init` re-appended it, on every boot.
- **`$schema` is not fetched:** version selection is local identifier
  comparison only.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| The capability renders discovered plugins into the `plugin` prompt section | `src/lingtai/tools/plugin/__init__.py` (`_reconcile`) | `tests/test_plugin_tool.py::test_plugin_capability_renders_catalog_into_prompt` |
| `info` returns a health snapshot without the manual body | `src/lingtai/tools/plugin/__init__.py` (`_reconcile`) | `tests/test_plugin_tool.py::test_info_returns_catalog_snapshot` |
| Unknown actions return a `{status: error}` dict, including the unhashable case | `src/lingtai/tools/plugin/__init__.py` (`handle_plugin`) | `tests/test_plugin_tool.py::test_unknown_action_returns_error_envelope` |
| Public name/actions and the LTP v2 envelope are exact | `src/lingtai/tools/plugin/__init__.py` (`get_schema`) | `tests/test_plugin_tool.py::test_schema_exposes_exact_public_actions_and_envelope` |
| Both actions declare a strict-empty `input`; extra input fails before I/O | `src/lingtai/tools/plugin/__init__.py` (`_EMPTY_INPUT`, `_build_family`) | `tests/test_plugin_tool.py::test_extra_input_field_is_rejected`, `::test_schema_only_and_dispatching_families_declare_identical_children` |
| A `../` escape is rejected under §4.1 | `src/lingtai/services/plugin_registry.py` (`resolve_contained`) | `tests/test_plugin_tool.py::test_containment_rejects_dotdot_escape` |
| A symlink escape is rejected under §4.1 | `src/lingtai/services/plugin_registry.py` (`resolve_contained`) | `tests/test_plugin_tool.py::test_containment_rejects_symlink_escape` |
| A missing/invalid `plugin.json` rejects the whole plugin | `src/lingtai/services/plugin_registry.py` (`read_plugin`) | `tests/test_plugin_tool.py::test_invalid_manifest_rejects_whole_plugin` |
| An escaping component is skipped without rejecting the plugin | `src/lingtai/services/plugin_registry.py` (`_scan_skills`, `_scan_mcp_servers`) | `tests/test_plugin_tool.py::test_escaping_skill_is_skipped_but_plugin_survives` |
| A skipped skill is absent from the composed catalog, not merely from the report | `src/lingtai/services/plugin_registry.py` (`_scan_skills`), `src/lingtai/tools/skills/__init__.py` (`_compose_paths`, `_scan_one_skill`) | `tests/test_plugin_tool.py::test_escaping_skill_is_absent_from_the_composed_skills_catalog`, `::test_registration_records_each_validated_skill_dir_on_the_agent` |
| A nested skill is reported exactly as it mounts | `src/lingtai/services/plugin_registry.py` (`_walk_skills`) | `tests/test_plugin_tool.py::test_nested_skills_are_reported_exactly_as_they_mount` |
| Containment is not bypassed by omitting the `./` prefix | `src/lingtai/services/plugin_registry.py` (`_expand_plugin_path`) | `tests/test_plugin_tool.py::test_containment_is_not_bypassed_by_omitting_the_dot_slash`, `::test_values_that_are_not_plugin_relative_paths_still_pass_through` |
| `env`/`cwd`/`headers` are carried into the registry record | `src/lingtai/services/plugin_registry.py` (`to_registry_record`) | `tests/test_plugin_tool.py::test_env_and_cwd_are_carried_into_the_registry_record`, `::test_http_headers_are_validated_and_carried`, `::test_carried_fields_do_not_break_idempotence` |
| The shipped boot flow registers once and is idempotent | `src/lingtai/agent.py` (`__init__`, `_register_declared_plugins`) | `tests/test_plugin_tool.py::test_cli_shaped_boot_registers_once_and_is_idempotent`, `::test_minimal_construction_does_not_wipe_plugin_records`, `::test_declaring_capabilities_without_plugins_still_uninstalls` |
| An inherited (discovery-only) plugin mounts nothing | `src/lingtai/tools/plugin/__init__.py`, `src/lingtai/services/plugin_registry.py` | `tests/test_plugin_tool.py::test_discovery_only_plugin_installs_nothing` |
| `manifest.plugins` is canonical and the capability key is its alias | `src/lingtai/services/plugin_registry.py` (`declared_plugin_paths`) | `tests/test_plugin_tool.py::test_manifest_plugins_is_the_canonical_declaration_key`, `::test_capability_paths_alias_is_still_honored` |
| A declared plugin's skill joins the skills catalog, located inside the plugin | `src/lingtai/tools/skills/__init__.py` (`_compose_paths`) | `tests/test_plugin_tool.py::test_declared_plugin_skill_appears_in_the_skills_catalog` |
| A declared plugin's MCP server gets a `source="plugin:<name>"` registry record | `src/lingtai/services/plugin_registry.py` (`register_plugins`, `to_registry_record`) | `tests/test_plugin_tool.py::test_declared_plugin_mcp_server_lands_in_the_registry` |
| A path escape is rejected at registration, not merely displayed | `src/lingtai/services/plugin_registry.py` (`resolve_server_spec`) | `tests/test_plugin_tool.py::test_path_escape_is_rejected_at_registration_time`, `::test_containment_covers_args_not_just_command` |
| Undeclaring a plugin prunes its records and its skills | `src/lingtai/services/plugin_registry.py` (`prune_plugin_records`) | `tests/test_plugin_tool.py::test_undeclaring_a_plugin_prunes_its_registry_records`, `::test_uninstalling_removes_the_skill_from_the_catalog` |
| Registration never overwrites a record it does not own | `src/lingtai/services/plugin_registry.py` (`register_plugins`) | `tests/test_plugin_tool.py::test_registration_never_overwrites_a_record_it_does_not_own` |
| Registration is idempotent and convergent | `src/lingtai/services/plugin_registry.py` (`register_plugins`) | `tests/test_plugin_tool.py::test_registration_is_idempotent`, `::test_a_changed_spec_replaces_the_record_rather_than_duplicating_it` |
| The shipped example plugin registers end to end | `docs/examples/agent-plugins/hello-lingtai/plugin.json` | `tests/test_plugin_tool.py::test_the_shipped_example_plugin_registers_end_to_end` |
| All three glossary languages ship and validate | `src/lingtai/tools/plugin/glossary-{en,zh,wen}.md` | `tests/test_plugin_tool.py::test_glossary_resources_present_and_valid` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Catalog renders into the prompt | `tests/test_plugin_tool.py::test_plugin_capability_renders_catalog_into_prompt` | Drop a plugin on a configured path, inspect the `plugin` prompt section | Discovered plugins invisible to the model |
| Tool is read-only (mounting is boot-only) | `tests/test_plugin_tool.py::test_info_reports_registration_but_never_performs_it` | Add a plugin after boot, call `info`, confirm it reports as `discovered` and the registry is unchanged | A model-facing action could mount third-party code |
| An inherited path never mounts | `tests/test_plugin_tool.py::test_discovery_only_plugin_installs_nothing` | Drop a plugin on a skills path, confirm `mcp_registry.jsonl` is untouched | Silent third-party MCP registration from a directory drop |
| Uninstall prunes exactly what the plugin owns | `tests/test_plugin_tool.py::test_pruning_leaves_records_the_plugin_system_does_not_own` | Undeclare a plugin with a hand-written record alongside; confirm only the plugin's line goes | Data loss in a human-owned registry |
| Unknown actions handled | `tests/test_plugin_tool.py::test_unknown_action_returns_error_envelope` | Call `plugin(action="foo")` | Silent mis-dispatch |
| `../` and symlink escapes rejected | `tests/test_plugin_tool.py::test_containment_rejects_dotdot_escape`, `::test_containment_rejects_symlink_escape` | Point a plugin's `mcp.json` `command` outside its root | Plugin-declared path reaches outside its own root |
| Invalid input fails before any scan | `tests/test_plugin_tool.py::test_extra_input_field_is_rejected` | Call `plugin` with a bogus `input` field | Wasted or surprising I/O from a malformed call |

Run before merging:

```bash
python -m pytest tests/test_plugin_tool.py tests/test_signpost_tool_descriptions.py \
  tests/test_agent_capabilities.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored. Agent Plugins filenames (`plugin.json`, `mcp.json`,
  `skills/`, `SKILL.md`) are standard literals and are never localized.
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
