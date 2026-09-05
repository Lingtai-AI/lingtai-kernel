---
related_files:
  - src/lingtai/tools/bash/ANATOMY.md
  - src/lingtai/tools/email/ANATOMY.md
  - src/lingtai/tools/file/ANATOMY.md
  - src/lingtai/tools/tool_family/BEHAVIORS.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/tool_family/__init__.py
  - src/lingtai/tools/tool_family/settings.py
  - src/lingtai/intrinsic_skills/system-manual/reference/tool-plugin-settings/SKILL.md
  - src/lingtai/tools/tool_family/manual.py
  - src/lingtai/tools/vision/ANATOMY.md
  - src/lingtai/tools/web_search/ANATOMY.md
  - src/lingtai/tools/mcp/ANATOMY.md
  - src/lingtai/tools/plugin/ANATOMY.md
  - src/lingtai/tools/knowledge/ANATOMY.md
  - src/lingtai/tools/avatar/ANATOMY.md
  - src/lingtai/tools/avatar/settings.py
  - src/lingtai/tools/soul/ANATOMY.md
  - src/lingtai/tools/skills/ANATOMY.md
  - src/lingtai/tools/psyche/ANATOMY.md
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/system/ANATOMY.md
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/_tool_family.py
  - src/lingtai/tools/context/ANATOMY.md
  - src/lingtai/tools/pad/ANATOMY.md
  - src/lingtai/tools/lingtai/ANATOMY.md
  - src/lingtai/tools/tool_family/glossary-en.md
  - src/lingtai/tools/tool_family/glossary-wen.md
  - src/lingtai/tools/tool_family/glossary-zh.md
  - tests/test_tool_settings_contract.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# src/lingtai/tools/tool_family/ Anatomy

This package owns the generic ToolFamily/ChildTool composition and dispatch
boilerplate a family MAY opt into when implementing the LingTai Tool Protocol
v2 shape defined in `../CONTRACT.md`. It standardizes the wire envelope
(`action`/`input`/`reasoning`/`summarize`) and the schema-composition and
dispatch-validation boilerplate that would otherwise be duplicated by every
hand-migrated family; it does not standardize implementations, handlers, or
result types (`../CONTRACT.md` "Implementation independence" is binding on
this package too — using it is optional, not mandatory). Official declared
families use a runtime-bound registrar/host bridge and narrow ports; any direct
per-call Agent family construction described below is legacy compatibility, not
that generic dispatch route.

## Components

- `ChildTool` — a frozen descriptor pairing one child's canonical name,
  `input_schema`, `handler`, and an optional `diagnostics` sidecar; name
  doubles as the model `action` constant and dispatch key
  (`__init__.py:143-170`, preceded by the `DiagnosticDescriptor` dataclass at
  `__init__.py:125-140`). `diagnostics` maps a structural trigger name
  (today: only
  `TRIGGER_UNSUPPORTED_INPUT_FIELD`) to the static `DiagnosticDescriptor`
  (`code`/`expected_form`/`reason`/`fix`) that action owns for it — see
  "Diagnostics sidecar" below.
- `ToolFamily` — validates a fixed child registry (duplicate names and a
  `manual` reserved-name collision fail loudly at construction), composes a
  model-facing schema from each child's own `input_schema` plus a REQUIRED
  root `reasoning` string property (declared by the family schema itself, not
  left to Agent schema composition's property-only re-injection), and
  provides an optional `handle()` dispatcher: validates `action`, type-checks
  and strips root `summarize`, rejects unknown root fields, and rejects
  `input` keys outside the selected child's own declared schema properties
  before calling that child's handler with only its `input`
  (`__init__.py:173-472`). Two enforcement layers correlate `action` with
  `input`, generated purely from the child registry with no name/schema
  mapping table: (1) schema-level — a root `allOf` with one `if`/`then`
  condition per child, each `if` testing `action` via `const` against that
  child's own registry name, each `then` constraining `input` to that exact
  child's canonical schema; (2) dispatch-level — `handle()`'s own `input`-key
  check against the selected child's declared properties, which remains
  always-authoritative and fail-closed regardless of whether a given
  provider enforces `allOf`/`if`/`then` schema-side. Root-level `allOf`
  correlation was adopted after a live non-strict Codex Responses probe on
  2026-07-27 accepted a raw root `allOf`/`if`/`then` schema without error on
  the current route (see `CONTRACT.md` "Contract rules").
- `settings.py` — owns the public `SettingRow`/`SettingsProvider` seam and the
  injected five-field SHOW projection with private redaction and incremental
  response bounding (`settings.py:1-137`, guarded by T011).

### Diagnostics sidecar

`DiagnosticDescriptor` (`__init__.py`, next to `ChildTool`) is a frozen,
fully static value an action author writes once, adjacent to that action's
own `input_schema` — never computed, parsed, or guessed. When `handle()`
rejects a selected action's `input` for a key outside its declared
properties, `_build_diagnostics` checks whether that child declared a
`TRIGGER_UNSUPPORTED_INPUT_FIELD` entry; if so, it additively attaches a
`diagnostics` array to the otherwise-unchanged legacy failure result, one
entry per foreign field, each pairing a mechanically computed
`<family>/<action>/input.<field>` location with the descriptor's own text
verbatim. A field label that is not conventional-identifier-shaped, or
contains a secret-shaped substring, is dropped by the generic
`_is_safe_field_label` check rather than surfaced. This sidecar is read only
by `handle()` — `build_schema()` never touches it, so it cannot reach any
provider wire. See `CONTRACT.md` "Diagnostics sidecar" for the full rules and
`../context/ANATOMY.md`/below for `molt`'s concrete declaration.
- `manual.py` — owns `MANUAL_INPUT_SCHEMA`, the single strict-empty `manual`
  input schema every family reuses, and `build_manual_child()`, which wraps
  `../_manual.py`'s
  `load_installed_manual()` into the ManualTool stable contract: strict empty
  input, and a handler whose actual return value (what `ToolFamily.handle()`
  dispatches back verbatim, once the returned `ChildTool` is registered
  directly and unwrapped in a family's own `ToolFamily`) is the canonical
  `content[0].text` (full body) / `structuredContent.manual_path` (host-local
  path) shape, with `status`/`error` loader facts preserved truthfully. The
  strict-empty input literal it registers is exported as `MANUAL_INPUT_SCHEMA`
  so a family composing a schema-only `ToolFamily` alongside its dispatching
  one reuses the same object instead of hand-copying it and drifting (`mcp`,
  `knowledge`, `file`, `vision`, and `soul` all do; `manual.py:1-89`) — and a
  family supplying its own `manual` child entirely, like `avatar`, can
  reference it the same way instead of restating the literal. `web` predates
  the export and still declares its own local `_MANUAL_INPUT_SCHEMA`, which
  its own owner may collapse onto this export separately. Each `ChildTool`
  deep-copies `MANUAL_INPUT_SCHEMA` rather than sharing the literal, so one
  family's schema can never be mutated through another's.

## Connections

`web_search/__init__.py` is the first real consumer: `get_schema()` composes
the model-facing schema from a module-level schema-only `ToolFamily`, and each
`WebManager` instance builds its own per-instance `ToolFamily` with handlers
bound to that instance — search/browse close over instance state;
`manual.build_manual_child(agent, "web")`'s returned `ChildTool` is
registered *directly*, unwrapped, as the family's `manual` child, so
`ToolFamily.handle()` returns that child's canonical
`content`/`structuredContent` result verbatim for `action="manual"` (no
double wrap). `WebManager.handle()` calls `self._family.handle(args)` and,
strictly *after* that call returns, adapts a successfully dispatched manual
result back to Web's pre-migration public flat shape (`status`, `manual`,
`manual_path`, `action`, `current_setting`) via
`WebManager._adapt_manual_result` — this adaptation is Web's own
Host/presentation-layer responsibility, applied post-dispatch, never inside
a registered child. `handle()` also stamps its own `current_setting`
diagnostic onto any envelope-level failure result, since a generic
`ToolFamily` has no knowledge of a specific family's settings diagnostics.
This division follows `../CONTRACT.md` "Implementation independence": using
`ToolFamily.handle()` is `web`'s choice, not an inherited requirement.

`mcp/__init__.py` ([`../mcp/ANATOMY.md`](../mcp/ANATOMY.md)) is the second
consumer and the minimal shape of one: a two-child family (`info`, `manual`)
whose public tool name and action values are unchanged by the migration, where
both children take the canonical strict-empty `input`. It follows the same
division — the `manual` child from
`build_manual_child(host.workdir, DECLARATION.manual)` is registered directly
and unwrapped, and `mcp`'s own flat `mcp_manual` public shape is reconstructed
post-dispatch by a Host-owned adapter. `mcp` is the current base reference slice
recut onto the kernel-owned family-generic declared host-plugin contract
(`src/lingtai/kernel/tool_plugin/ANATOMY.md`), so it never receives the `Agent`
at all: the builder is handed the granted `WorkdirPort`, and the installed
manual's destination name is read back out of the family's own declaration
rather than restated here. It also shows
what a family, not this package, must own when a pre-migration public error
envelope predates the generic dispatcher: `mcp` renders its exact
unknown-action envelope in its own `handle_mcp` *before* delegating, including
the missing-action empty-string default and unhashable `action` values that
`ToolFamily.handle`'s dict lookup would otherwise raise `TypeError` on. The
generic dispatcher's canonical error shape is never changed to accommodate a
consumer.

`knowledge/__init__.py` is the third real consumer
(`src/lingtai/tools/knowledge/ANATOMY.md`): one `_build_family(agent | None)`
is the single builder — `_FAMILY = _build_family(None)` backs `get_schema()`
with non-dispatching handlers, and `_build_family(agent)` binds the
`info`/`manual` operations named in `_CHILD_SPECS` per agent. Both children declare the canonical strict-empty
`input_schema`, so every `input` key is a cross-branch/unknown key rejected
before handler I/O. It registers its own `manual` child rather than
`build_manual_child`, because knowledge's public manual result is keyed
`knowledge_manual` — the child's canonical result is returned verbatim, so no
Host-layer flattening is needed. Its outer `handle()` normalizes only the
generic `ACTION_REQUIRED` envelope failure back to knowledge's exact
pre-migration unknown-action result.

`avatar/__init__.py` ([`../avatar/ANATOMY.md`](../avatar/ANATOMY.md)) is the
sixth real consumer, and shows partial adoption is conforming: it reuses
`ChildTool`/`ToolFamily` and the exported `MANUAL_INPUT_SCHEMA` for
`spawn`/`rules`/`settings`/`manual` schema composition and dispatch. Its
declaration opts into the generic settings child with the no-I/O provider from
`avatar/settings.py`, while its operational actions remain one
`_DECLARED_CHILD_SPECS` source and its public listing derives from the
declaration. It supplies its **own** `manual` handler rather than
`manual.build_manual_child`, because its manual ships inside its own package
instead of the agent's installed `.library` catalog. `ToolFamily.handle()`
returns that child's own canonical flat result verbatim — no double wrap, and
no post-dispatch adaptation of a manual result at all. `AvatarManager.handle()`
does two things after dispatch returns that this package deliberately cannot:
it restores avatar's pinned unknown-action error string in place of the generic
`ACTION_REQUIRED` envelope failure, and it threads root `_reasoning` (the spawn
mission brief) to the `spawn` handler out-of-band, since `ToolFamily` correctly
passes no envelope field to any child.

`soul/__init__.py` is a declared-host-plugin consumer and the first
*intrinsic* one in this composition account. Production binding is through
`_bind(host)`: the five operational children receive only the granted
`host.soul_runtime` (`SoulRuntimePort`), while the reserved `manual` child gets
the granted `host.workdir` through `build_manual_child(host.workdir,
DECLARATION.manual)`. The declaration-owned action registry and schemas are the
single source for the schema-only and bound families; duplicate or reserved
child names fail loudly rather than being resolved by order.

Whole-Agent `handle(agent, args)` and `_coerce_runtime()` remain compatibility
bridges at Soul's package root for kernel lifecycle and legacy callers only;
they are not the production composition model. After dispatch, Soul's
`_adapt_manual_result` intentionally restores the historical flat
`status`/`manual`/`manual_path` result, while the bound operational
implementation continues to consume only `SoulRuntimePort`. Soul also drops
the kernel-injected `_tc_id` at this root compatibility boundary; it must not
widen the shared envelope or leak transport metadata into a child.

`skills/__init__.py` ([`../skills/ANATOMY.md`](../skills/ANATOMY.md)) is the
ninth consumer and uses the same division with no shared code beyond this
package, and differs from `web` in declaring its child registry exactly once:
a single `_build_family(agent, paths)` builder registers the `info` child and
`manual.build_manual_child(agent, "skills")` directly, and both `get_schema()`
(via an import-time `agent=None` instance whose handlers are unreachable) and
`setup()` obtain their `ToolFamily` from it — so the advertised input schemas
are by construction the ones dispatch registers. Both of its children declare
the canonical strict-empty `input` schema, so `handle()`'s allowed-key check
rejects every `input` key for either action. Its
`handle_skills` wrapper adapts only the dispatched `manual` child result to
the capability's public `skills_manual`/`library_manual`/`manual_path` shape
and, unlike `web`, keeps this package's canonical envelope-failure result
verbatim — it has no family-specific diagnostic block to stamp on.

`system/__init__.py` ([`../system/ANATOMY.md`](../system/ANATOMY.md)) is the
eleventh consumer and the third *intrinsic* one, reusing `soul`'s module-level
division verbatim: a schema-only family built at import (which is also the
registry's collision check) behind `get_schema()`, an agent-bound family per
`handle(agent, args)` call, `build_manual_child(agent, "system-manual")`
registered directly with a post-dispatch flattening adapter, `_tc_id` dropped
at its own Host boundary, and the generic `ACTION_REQUIRED` failure normalized
to its own pinned unknown-action string. At twelve action children it is this
package's largest consumer, and the one where the allowed-key rejection does
the most safety work — `system`'s privilege classes are per action, so an
`input` key outside the selected child's schema is refused before any
lifecycle handler runs.

`daemon/_tool_family.py` ([`../daemon/ANATOMY.md`](../daemon/ANATOMY.md)) is the
twelfth consumer and repeats `shell`'s structural division rather than `web`'s:
the family module is a separate file from the engine, owning the package's one
public `get_schema`/`get_description` pair and a `DaemonFamilyDispatcher` whose
six child handlers each flatten their own validated `input` (injecting the
matching `action` key) and call the unchanged `DaemonManager.handle`. A single
`_child_specs(backend_enum)` builder is the one registry source for both the
schema-only family behind `get_schema()` and the dispatcher's handler-bound
family, so composition and dispatch cannot drift; the engine's
`_BACKEND_SCHEMA_ENUM` is passed *into* that builder rather than imported,
because `daemon/__init__.py` imports this module and the reverse import would
be circular. Two engine ceilings (`DEFAULT_MAX_TURNS`, `_CHECK_LAST_MAX`) are
restated as module literals for the same reason and pinned by import-time
assertions in `daemon/__init__.py`. It registers
`build_manual_child(agent, "daemon")` directly and returns its canonical result
verbatim; its only Host normalization is narrowing this package's generic
`ACTION_REQUIRED` message to daemon's exact six actions.

`context/__init__.py` ([`../context/ANATOMY.md`](../context/ANATOMY.md)) uses
the module-level schema-only plus legacy per-call agent-bound compatibility shape from one
`_CHILD_SPECS` registry. It is the intrinsic family that genuinely **consumes**
the kernel-injected `_tc_id` rather than dropping it: `molt` needs that wire id
to locate and replay its own ToolCallBlock. `handle()` strips it from the closed
root and threads it to that child out-of-band, via the same Host-owned seam
`avatar` uses for its spawn mission brief. The generic package is not widened:
`_ROOT_FIELDS` is unchanged and no envelope field reaches any child. The sibling
`pad` and `lingtai` families are independent consumers with their own final
action inventories. `context` is also the first concrete "Diagnostics
sidecar" declaration: a `_CHILD_DIAGNOSTICS` mapping next to `_MOLT_INPUT_SCHEMA`
gives `molt`'s `ChildTool` a `TRIGGER_UNSUPPORTED_INPUT_FIELD`
`DiagnosticDescriptor` stating its own allowed-field set and refusal reason;
the sibling `summarize`/`rebuild`/`manual` children declare none, so a
foreign `input` key on those still renders the plain legacy failure.

## Composition

The parent [`../ANATOMY.md`](../ANATOMY.md) owns capability registry
composition and lists this package. The shared
[`../CONTRACT.md`](../CONTRACT.md) owns the LingTai Tool Protocol (LTP) the
schema this package composes must satisfy. The paired [`CONTRACT.md`](CONTRACT.md)
specializes that promise into this package's own Port/Adapters/rules. No
external MCP transport, endpoint, or registry is owned or touched here —
"MCP-compatible" describes only the `name`/`description`/`inputSchema`-shaped
internal descriptor convention `ChildTool` follows for clean internal
boundaries.

## State

No mutable state lives at package root. `ToolFamily` instances are immutable
after construction (a frozen child registry); `build_schema()` recomputes the
model-facing schema on every call rather than caching one at construction.
Any per-Agent state (engine specs, settings diagnostics, service caches)
belongs to the consuming family, as `WebManager` demonstrates.

## Notes

A fake `widget` family in `tests/test_tool_family_generic.py` and
`tests/test_tool_family_wire_parity.py` proves this package is generic, not
Web-specific; `soul`'s migration
(`tests/test_tool_family_soul_migration.py`) proves it a second time against a
real intrinsic with a different composition shape. Building a family on
`ToolFamily` is optional: a family may hand-write an equivalent
`handle()`/schema composition instead, exactly as `web` did before adopting
this package.
