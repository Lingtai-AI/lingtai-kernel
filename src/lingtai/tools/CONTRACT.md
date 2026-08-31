---
name: lingtai-tool-protocol
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/tools/bash/CONTRACT.md
  - src/lingtai/tools/BEHAVIORS.md
  - src/lingtai/tools/registry.py
  - src/lingtai/kernel/base_agent/tools.py
  - src/lingtai/kernel/tool_executor.py
  - src/lingtai/tools/web_search/CONTRACT.md
  - src/lingtai/tools/web_search/__init__.py
  - src/lingtai/tools/knowledge/CONTRACT.md
  - src/lingtai/tools/knowledge/__init__.py
  - src/lingtai/tools/file/CONTRACT.md
  - src/lingtai/tools/file/__init__.py
  - src/lingtai/tools/file/manual/SKILL.md
  - tests/test_file_tool_plugin_package.py
  - src/lingtai/tools/task_card/CONTRACT.md
  - src/lingtai/tools/task_card/__init__.py
  - src/lingtai/tools/avatar/CONTRACT.md
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/tools/context/__init__.py
  - src/lingtai/tools/context/manual/SKILL.md
  - src/lingtai/tools/soul/CONTRACT.md
  - src/lingtai/tools/soul/__init__.py
  - src/lingtai/tools/skills/CONTRACT.md
  - src/lingtai/tools/skills/__init__.py
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/vision/CONTRACT.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/kernel/tool_result_summary.py
  - src/lingtai/tools/notification/CONTRACT.md
  - src/lingtai/tools/system/CONTRACT.md
  - src/lingtai/tools/daemon/CONTRACT.md
  - src/lingtai/tools/email/CONTRACT.md
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/tools/pad/CONTRACT.md
  - src/lingtai/tools/lingtai/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/plugin/CONTRACT.md
  - src/lingtai/mcp_servers/_plugin.py
  - src/lingtai/mcp_servers/telegram/plugin.py
  - src/lingtai/mcp_catalog.json
  - src/lingtai/services/plugin_registry.py
  - tests/test_browser_capability.py
  - tests/test_wire_tool_description.py
maintenance: |
  This component contract is governed by the root CONTRACT.md and owns the
  LingTai Tool Protocol (LTP). Keep the paired tools Anatomy and cross-contract
  links reciprocal. Update Agent schema composition, ToolExecutor normalization,
  each migrated family, the `_LTP_V2_MIGRATED_FAMILIES` allowlist, and this
  contract together when the canonical call boundary changes. LTP alignment is documentary — this pair is the source of
  truth, not a central validator. Migrate one real family at a time; do not
  claim legacy tools already conform. `### Tool-to-MCP Plugin Contract` is
  guarded by LP002 in the paired BEHAVIORS.md: keep its status paragraph, its
  governed-surface classification, its selected form, its resolved collision
  decision, and its current-evidence list honest, and update it, the paired
  ANATOMY.md, and BEHAVIORS.md together when a family actually recuts.
  The selected form is the kernel-owned declared host-plugin contract
  (`src/lingtai/kernel/tool_plugin/CONTRACT.md`), a governed component contract
  in its own right — keep that link reciprocal, and mirror any change to the
  declaration shape, the host ports, or the reserved official-name rule in both
  files. The curated `CuratedMcpPlugin` descriptor plus
  `src/lingtai/mcp_catalog.json` is the retained external-transport/launcher
  route layered over a declaration, not the required form of every official
  tool; changing either choice is a normative change, so move
  `src/lingtai/mcp_servers/_plugin.py`, `telegram/plugin.py`, and
  `src/lingtai/mcp_catalog.json` in related_files with it. `mcp`, `avatar`,
  `context`, `daemon`, `email`, `file`, `plugin`, `psyche`, `notification`,
  `shell`, `soul`, `system`, `task_card`, `vision`, and `web` are the fifteen
  static declared families today, in that official order; do not widen that claim
  without another family's evidence.
---
# LingTai Tool Protocol (LTP)

## Purpose
Guarded by: [LP001](BEHAVIORS.md#behavior-lp001)


Define the **LingTai Tool Protocol (LTP)**: the future canonical public interface
for LingTai-owned model-facing tools. LTP covers public addressing, ownership,
boundaries, and the semantics of the envelope, actions, `manual`, and settings.

LTP is LingTai-owned. "Enhanced MCP-like" is a useful mental model, but LTP is
**not** an MCP extension: it does not rewrite arbitrary MCP schemas and reserves
nothing in them.

LTP standardizes the public interface only. It does **not** standardize
implementations, readers, schemas, or lifecycles. This file defines no family,
compiler, dispatcher, base class, port class, adapter class, handler, or result
type, and changes no runtime behavior by itself. It is a migration target applied
one family at a time.

## Behavior

A public LingTai tool is a **logical tool family**: one model-facing name that
groups cohesive capabilities sharing a domain, an authority, and a state scope.
Once a family is explicitly migrated, its final Agent-built model-facing argument
schema is a closed object whose root properties are exactly:

- `action` — selects one named action port within the family;
- `input` — the one strict input object for the selected action;
- `reasoning` — the top-level rationale for this tool call; and
- `summarize` — the root boolean opting this call's result into
  post-processing.

`action` and `input` are the family/action contract. `reasoning` and `summarize`
are cross-cutting envelope controls, not action arguments. The set is
provider-neutral and closed: there is no other public block, and `reasoning` and
`summarize` are never nested under `input`.

## Port

The provider-neutral boundary is the final `FunctionSchema` assembled by the
Agent. A migrated family owns `action` plus the strict per-action `input`
branches; Agent schema composition owns the standard top-level `reasoning`
field. Each action is one named port with one strict input schema.

"Action port" names a separation of concerns, not a class or module. The
contract standardizes the wire interface; how a family realizes its actions
behind that interface is not constrained here.

## Adapters

Provider adapters wrap the same schema in their protocol-native envelope.
OpenAI's outer `parameters`, Anthropic's `input_schema`, and the internal
`FunctionSchema.parameters` attribute are transport or implementation names and
remain unchanged; none creates a public LingTai block named `parameters`.
ToolExecutor removes public `reasoning` before handler dispatch and may preserve
it as internal `_reasoning` metadata. `_reasoning` must never appear in the
model-facing schema or nested `input`.

## Contract rules

### Envelope

- The final migrated root property set MUST be exactly `action`, `input`,
  `reasoning`, and `summarize`, with `additionalProperties: false`. The
  family-required set is `action` and `input`; standard Agent composition adds
  top-level `reasoning`.
- `input` MUST be one object selected by `action`. Action branches are closed;
  declared optional fields use the provider-compatible nullable representation.
- Nested `input` MUST NOT contain `reasoning`, `_reasoning`, or `summarize`.
- `reasoning` is root-only cross-cutting metadata and is never part of an
  action's independent implementation input.
- `summarize` is a root-only boolean, absent or false by default. It is universal
  cross-cutting result post-processing for every migrated family, not an action
  implementation argument. A family MUST NOT read `summarize` as action input.
- The envelope MUST retain the root boolean through result post-processing, on
  both the single and the parallel call path, and MUST strip it before action
  implementation dispatch. No action handler or use case receives `summarize` as
  implementation input. This is interface semantics: it constrains what crosses
  the boundary, and requires no compiler, dispatcher, base class, or shared
  implementation to satisfy.
- Raw output MUST be durably recorded before any visible summary replacement, and
  tool errors MUST remain exact and unmodified. Summarization replaces what the
  model sees; it never replaces what was recorded and never rewrites an error.
- The prohibition above is on the result-summarization **control**, identified by
  role and not by spelling. This contract reserves no name inside `input`: an
  action MAY declare a domain field named `summary` when that field is genuine
  action input rather than a post-processing control. The `context` molt
  retrospective (`input.summary`, a string the agent writes for the next
  session) is such a field and remains legitimate. Note that `context` also
  carries an ACTION named `summarize`; that too is a domain operation, distinct
  from the root control, and no `context` child declares a `summarize` field. The test is role: a
  boolean asking the runtime to post-process this call's result belongs at root
  as `summarize`; a value the action itself consumes belongs in `input` under
  whatever name the domain calls it.
- No public `parameters`, `parameter`, `arguments`, `payload`, or compatibility
  alias is admitted after migration. Provider envelope names are not aliases.
- Internal `_reasoning` is metadata only: handlers may admit it after
  ToolExecutor normalization but MUST NOT treat it as action input.

### Dispatch and actions

- A migrated family MUST validate action/input correspondence at dispatch and
  reject keys belonging to another action's branch; schema conformance alone is
  not the authorization or safety boundary. This PR does not implement that
  dispatcher.
- Every LingTai-owned family MUST offer a `manual` action returning exact
  guidance for that family. Agents SHOULD call it before complex or unfamiliar
  use. Manual content stays progressive-disclosure material; schemas MUST NOT be
  bloated to carry it.
- A migrated family's `manual` MUST explain root `summarize` honestly for that
  family, selecting one shared guidance profile rather than restating the whole
  rule. The profiles are:
  - **bulky-result** — the family or action has predictably large output. Its
    manual says when `summarize=true` helps, and when exact raw text, IDs, or
    paths mean it should stay false.
  - **short-result** — output is normally small. Its manual says `summarize` is
    available but normally unnecessary, and to leave it false.
  A family whose actions differ MAY assign profiles per action. Calls to `manual`
  itself normally use `summarize=false`, so exact procedure and critical
  constraints are not summarized away; each manual SHOULD say so.
- The profiles exist so this guidance is maintained once and referenced, not
  copied verbatim into every manual. This PR defines the obligation only; it
  writes no manual and implements no manual machinery.
- Family boundaries follow shared domain, authority, state, and cohesion — not
  superficial implementation similarity. A family exists because its actions
  belong to one thing, not because their code looks alike.

### Settings

LTP defines two optional settings levels under the Agent settings root. Both are
addresses and ownership rules, not a file format or a reader.

`<agent-dir>` is the filesystem working-directory root owned by the Agent
instance whose LingTai-owned family is invoked. Its LTP settings root is the
direct child `<agent-dir>/settings/`. This names an address only: it imposes no
reader, loader, lifecycle, or other runtime requirement.

- `<agent-dir>/settings/<family>.json` — **family-owned** generic settings.
- `<agent-dir>/settings/<family>.<action>.json` — **action-owned** settings.

Illustratively: `web.json`, `web.search.json`, `web.browse.json`.

- **Grammar.** The two addresses MUST stay unambiguous. Neither a family name nor
  an action name may contain `.`; the first `.` in the stem therefore separates
  family from action. A stem with no `.` is the family file; a stem with exactly
  one `.` is that action's file. No stem carries more than one `.`.
- **Orthogonal scopes.** A family file MUST NOT embed action blocks, and an
  action file MUST NOT embed family or generic blocks. There is no include,
  inherit, overlay, fallback, or override; there is no precedence and no merged
  settings object. One semantic setting has exactly one owner.
- **Reading boundary.** One call may be affected by both levels: the family
  envelope reads and consumes only `<family>.json`, and the selected action reads
  and consumes only `<family>.<action>.json`. Neither reads the other's file.
- **Optionality.** A scope that supports no settings has no file. A supported but
  absent file means the owner's documented defaults apply. A present but invalid
  file MUST fail loudly at that owner's boundary and MUST NOT be silently
  ignored.
- **Per-owner authority.** Every family and action owns its own settings schema,
  version, and migration; whether it reads hot, at boot, or cached; its cache
  invalidation; and its error vocabulary. Internal helpers are allowed, but LTP
  MUST NOT depend on a central reader.
- **Discovery via `manual`.** A migrated family's `manual` is the settings
  discovery surface: it states the exact supported files, their schema and
  defaults, their lifecycle, and what an invalid file does. Where no settings
  surface exists, the manual says so explicitly rather than staying silent.
- **Reading is not writing.** Owning the read of a settings file grants no
  authority to mutate configuration.

### Implementation independence

Action implementations stay maximally independent. This contract MUST NOT be
read as requiring any of the following, and a future migration MUST NOT
introduce them merely to satisfy this file:

- inheritance from a shared base or port class;
- a shared handler or shared business logic;
- a common module layout or file consolidation;
- common boot, state, or dependency wiring;
- common internal request or result types;
- a universal domain result shape.

Two actions in one family may share nothing but the family name and the wire
envelope. That is a conforming implementation.

### Scope

- Scope is LingTai-owned tool families only. Arbitrary and MCP-provided tool
  schemas are out of scope and untouched; this contract reserves no field name
  in them and MUST NOT overwrite MCP fields.
- Migration is one family at a time in later PRs, vertically: code, contract, and
  manual together, with the evidence that migration's reviewer asks for. Legacy
  tools are neither mass-renamed nor declared migrated without their own
  implementation and documented alignment.
- Until a family is migrated, its existing runtime and schema are unchanged.
  Adopting this contract by itself causes no wire or runtime behavior change.

### Non-goals

This contract does not introduce, and the PR that adopted it did not implement:
a central LTP validator, registry, schema compiler, or universal conformance
harness; the old result-summarization control nested as `input.summary`; a
shared settings or `_settings` foundation; runtime schema injection;
ToolExecutor changes; a provider adapter envelope; or MCP migration.

The `file` family was also a non-goal of that adopting PR. It has since been
migrated on its own, vertically and with its own evidence, exactly as the
"one family at a time" rule requires — see `### Relationship to current
runtime` below and `src/lingtai/tools/file/CONTRACT.md`. Migrating it did not
relax any rule in this file.

The `input.summary` non-goal bans one thing: carrying the result-summarization
*control* below root. It does not reserve the word `summary`, and it does not ban
an unrelated domain field that happens to be named `summary` — see
`### Envelope`.

### Tool-to-MCP Plugin Contract
Guarded by: [LP002](BEHAVIORS.md#behavior-lp002)

**Status.** This section fixes the declaration, activation, dispatch, manual,
host, identifier, and migration vocabulary that every official model-facing tool
family shares. The accepted declared evidence is exactly the fifteen static
official families `mcp`, `avatar`, `context`, `daemon`, `email`, `file`,
`plugin`, `notification`, `shell`, `soul`, `system`, `task_card`, `vision`, and
`web`, in that order. `mcp` is the base reference; the remaining thirteen are
accepted vertical slices with their narrow earned ports. The kernel's closed
`GRANTABLE_HOST_PORTS` inventory has twenty-one grantable names: `workdir`,
`prompt_section`, `avatar_parent`, `context_runtime`, `daemon_runtime`,
`email_runtime`, `file_io`, `plugin_catalog`, `notification_state`,
`notifications`, `configuration`, `soul_runtime`, `system_runtime`, `identity`,
`shutdown`, `task_card_lifecycle`, `task_card_notifications`,
`active_provider`, `web_runtime`, and `provider_identity`. Email retains its
call-time manager port,
File retains `workdir`/`file_io` plus a setup-selected immutable
`configuration` snapshot, Plugin retains its protected prompt section and
read-only `plugin_catalog` projection, and always-on Notification retains
`workdir`/`notification_state` with Core-bound callbacks, Soul retains
`workdir` plus its explicit `soul_runtime` live-self port, and System retains
`workdir` plus its `system_runtime` lifecycle vocabulary and durable naming
`identity` port, and Task Card retains `workdir` plus `shutdown`, its
current-Agent `task_card_lifecycle` manager slot, and the closed
operation-native `task_card_notifications` port (five scalar operations, no
generic publisher), keeping one manager across refresh and its channel-neutral
`taskcard/` artifact ownership, and Vision retains `workdir` plus its live
read-through `active_provider` and one setup-selected `configuration`
snapshot plus a bind-time settings projection, keeping its
`analyze | check | list | settings | manual` surface, active-provider default
routing, allowed-preset own-credential borrowing, and no automatic
provider/credential/MCP fallback, and Web retains `workdir` plus its Web-owned
typed `web_runtime` composition (browser transport, immutable engine specs,
and default provenance, granted by its own `setup` through `extra_ports_for`)
and the narrow read-only `provider_identity` label, keeping its
`search | browse | settings | manual` surface, Web's read-only SHOW owner,
`settings/web.search.json` ownership,
spill/settings behavior, canonical provider gates, and search-vs-browse
isolation, with a fail-closed bind on a missing or mistyped `web_runtime`. The
former later-family target register is now empty; the reserved list is never a
generic dispatch or admission path. Every other registry family remains an
explicit future migration unit and none ships as an MCP plugin package today.

The kernel-shipped curated MCP families under `src/lingtai/mcp_servers/` ship
the *external stdio transport* form described below — evidence about
packaging and launch, not conformance to this section's declaration clauses,
and no clause here may be read as certifying them. Nothing here says the
families listed under `### Relationship to current runtime` have become a
compatible universal runtime either — those are LTP *envelope* migrations, and
a declaration recut is a separate, per-family change with its own evidence.

**Governed surface.** Every LingTai-owned first-party model-facing tool family
shipped in this distribution. That is two classes today, and both are inside
this contract's classification:

- **Registry families** — the intrinsics and built-in capability rows
  registered through `src/lingtai/tools/registry.py`. `mcp`, `avatar`,
  `context`, `daemon`, `email`, `file`, `plugin`, `notification`, `shell`,
  `soul`, `system`, `task_card`, `vision`, and `web` are first-party families
  in scope; all fifteen are declared under the selected form. Every other
  family in this class is a future migration unit, and no family in this class
  is wrapped as an MCP plugin package today.
- **Kernel-shipped MCP families** — the model-facing families this
  distribution ships as MCP server packages under `src/lingtai/mcp_servers/`.
  The curated catalog families (`imap`, `telegram`, `feishu`, `wechat`,
  `whatsapp`, `cloud_mail`) each own a `CuratedMcpPlugin` descriptor and a
  matching `src/lingtai/mcp_catalog.json` record, and are therefore the current
  first-party precedent for the curated **external-transport** route the
  selected form layers over, not a separate standard and not exempt from this
  section. The built-in daemon MCP families
  (`lingtai.mcp_servers.daemon_common`, `lingtai.mcp_servers.daemon_email`) are
  also in this class but carry no descriptor and no packaged `SKILL.md` today.
  No family in this class is declared under the selected form yet, so adopting
  it is future work for all of them.

Classification is not conformance. A family in either class conforms to a
clause of this section only with its own evidence for that clause; this
contract makes no blanket conformance claim for the curated families or for
anything else.

Externally supplied MCP schemas — third-party servers reached through
`mcp_registry.jsonl`, external Agent Plugins directories — and legacy MCP
transport and catalog paths are **not** converted by this contract. Their wire
shape stays theirs, and adopting any individual one of them is separate,
explicitly authorized, later work.

**Selected wrapper form.** There is exactly **one** form a LingTai-owned
official model-facing tool family may take, and it is the kernel-owned
**declared host-plugin contract**:
[`src/lingtai/kernel/tool_plugin/CONTRACT.md`](../kernel/tool_plugin/CONTRACT.md).
An official family MUST:

- own exactly one static `ToolPluginDeclaration`, constructed at import in that
  family's own module under `src/lingtai/tools/<family>/`, before any `Agent`
  exists — naming the model-facing family name, its ordered operational
  actions, one strict `input` schema per action, its description, its manual,
  the host ports it requires, and how it binds. Discovery is prohibited: a
  declaration is reached because the hand-edited static capability table in
  `src/lingtai/tools/registry.py` names its module and that module hands the
  declaration to the registrar — never because a directory, entry point, or
  manifest was scanned;
- carry a name reserved in the kernel-owned static
  `OFFICIAL_TOOL_PLUGIN_NAMES` list. The kernel registers official
  implementations, so a name absent from that list is not official, and a
  second, different declaration of a reserved name is refused **before** any
  bind and before any `add_tool` (see the identifier clause below);
- receive only the host capabilities it declared. The kernel injects a
  least-privilege facade granting exactly the ports the declaration named; a
  port it did not name is not reachable, the mount port is never grantable, and
  no clause of this section authorizes handing a plugin the whole `Agent`;
- never declare the reserved `manual` action; the declaration appends it, and
  the family owns that child's handler and its manual source.

**Implementation and transport are not a separate product category.** Where an
official family's implementation runs — in this process, behind a stdio MCP
server package, in a spawned peer process (Avatar), or over a channel
(Telegram) — is an adapter decision at the boundary where that technology
actually varies (root `CONTRACT.md` rule 8), layered *over* one declaration. It
is not a second declaration form and not a second class of official tool.

That corrects a normative error in the form this section previously selected,
which required **every** first-party wrapper to live as a Python package under
`src/lingtai/mcp_servers/<family>/`, ship a stdio `__main__.py` entry point, own
a `CuratedMcpPlugin` descriptor, and express its declaration as that
descriptor's `mcp_declaration()` catalog record. Mandating an external stdio
process of every official tool was wrong: it made a transport choice the
definition of the product.

The curated route itself is **retained unchanged and reclassified**, not
removed. `lingtai.mcp_servers._plugin.CuratedMcpPlugin`,
`CuratedMcpPlugin.mcp_declaration()`, the six curated packages, and the shipped
`src/lingtai/mcp_catalog.json` remain the curated **external-transport and
launcher** concern for families published through the curated catalog, with
their descriptor↔catalog record equality still the objectively checkable
identity/action/manual agreement for that route. A curated family MUST still
keep that equality. What changed is only its status: one transport adapter
form, not the required form of every official tool.

External **Agent Plugins v1.0.0** filesystem packages (`plugin.json`,
`skills/`, `mcp.json`; `src/lingtai/services/plugin_registry.py`) and raw
third-party MCP schemas remain a **separate standard**, not an alternate
model-facing declaration or transport route. The `mcp` source package is the
narrow documented exception only for its owned manual: its static host-plugin
`DECLARATION` remains the sole model-facing mount, while Agent uses the existing
registry reader to validate and copy its one `skills/mcp-manual/` source. It
neither calls external-plugin registration nor permits `mcp.json`; no first-party
family is recut *into* the external standard. This section introduces no generic
manifest compiler, plugin-admission engine, or multi-form compatibility layer.

**One family slice, one declaration, retained form.** The unit of migration is one
current model-facing family gaining one declaration that *wraps* it. The shared
register is family-generic rather than MCP-only.

- The declaration MUST preserve that family's public tool name, action
  inventory and spelling, per-action strict `input` schemas, the closed root
  (`action`, `input`, `reasoning`, `summarize`), result shapes, error
  vocabulary, authorization gates, side effects, and public manual result
  shape. It is an adapter, not a rename, flattening, aggregation, split, or new
  public capability.
- A family MAY translate at its own private boundary — the division `shell`
  already uses to keep `ShellManager`'s flat call shape and `daemon` to keep
  `DaemonManager`'s — but public semantics MUST survive unchanged.
- Adopting this section makes no family declared. Blanket conformance claims
  are prohibited: a family is declared only once its own vertical slice lands.
  `mcp` is the current base reference; Avatar, Context, Daemon, Email, File,
  Plugin, Notification, Shell, Soul, System, Task Card, Vision, and Web are
  accepted vertical evidence here. The former later-family target register is
  empty; a reserved name is never by itself a claim that a candidate slice has
  merged, and the reserved list is not an admission path.

**Authority: manager, declaration, host stay separate.**

- The original family or domain **manager** remains the sole semantic authority
  for business actions, validation, state, side effects, and family errors.
- The **declaration and its bind** own only adaptation and the family's manual.
  They MUST NOT become a second tool registry, a hidden configuration owner, an
  alternate execution path, or a place domain decisions migrate into.
- The **host** owns registration, activation, process and connection
  lifecycle, mounting, audit, and the live model-facing namespace — and, for an
  official family, the reserved-name list that namespace is claimed from. A
  family MUST NOT self-register, self-spawn a host route, or leave a
  live-looking route behind after close. It cannot: the mount port is host-only
  and is never granted to a declaration.

**The package owns the canonical manual and its submanuals.** Per root Design
principles 3 and 4, a package-owned manual travels with the capability:

- One declaration names its own manual alongside its public action inventory,
  and the family ships that manual with every submanual, reference, or asset
  the skill routes to. Declared identity, action list, and manual MUST agree
  and MUST fail loudly at construction or import when they do not. Agreement is
  upheld by *derivation, then check*: a family MUST compose its tool name, its
  per-action `input` schemas, and its installed manual destination from its own
  declaration rather than restating any of them as a second literal, and
  `ToolPluginDeclaration.bind()` refuses, on every boot, a bound plugin
  advertising an action inventory other than the declared `public_actions`. A family
  additionally published over the curated external transport ships its server
  entry point and declarative launch/identity record in that same package, and
  its registered server name and launcher MUST agree with the rest.
- Submanuals stay progressive-disclosure skill files referenced by the parent
  `SKILL.md`. They MUST NOT be inlined into schemas or copied into a second
  host-side catalog.
- Packaging does not remove the Contract→manual and Anatomy→manual edges; both
  owner twins still carry them.

**Reserved `manual`.** The reserved-action rule under
`### Dispatch and actions` is unchanged and binds declarations. An operational
action MUST NOT declare, schema, or handle `manual`; the declaration appends
exactly one strict-empty `manual` child sourced from the family's own manual,
and a duplicate or reserved-name collision fails at construction — at import,
before any Agent exists and before any server is advertised. Where a family's
current public manual result differs from the canonical child result, the
family MUST preserve the current public shape through an explicit presentation
adapter applied after canonical dispatch, rather than double-wrapping it or
silently changing what the model sees.

**Registration is not activation.** Declaration or boot registration validates
a plugin and MAY compose its validated skills and register its MCP declaration;
it MUST NOT start a server. Starting a registered server requires explicit host
activation. For a declared official family the same split is structural:
`bind()` is pure composition, and the separately declared boot-presentation
step runs only after every reserved-name check has passed and immediately
before mounting. Discovery is read-only: a directory found on a skills search
path MUST NEVER be silently mounted or executed.

**One host lifecycle owner.** The host starts the selected transport client,
injects only bounded host metadata and only where the wire permits, mounts the
server's advertised schema unmodified, tracks client-to-tool ownership, retries
only through the documented refresh path, and on retry, refresh, or stop closes
clients and removes stale metadata so no false-live route survives. The host
MUST NOT widen an existing tool's public schema in order to transport plugin
metadata.

**Strict dispatch boundary.** Envelope mapping happens only at the family's own
adapter boundary. A strict LTP family receives and restores root `reasoning`;
a flat third-party MCP schema does not, and MUST NOT have it injected. The host
forwards only schema-permitted arguments. Malformed envelopes and wrong-branch
keys are rejected *before* the manager performs any I/O, exactly as
`### Dispatch and actions` already requires.

**Identifiers, provenance, and collisions — the decision, and its scope.**

- Three identifier scopes stay separate: plugin package name, registered server
  record name, and model-facing tool family name. Server records retain source
  provenance, and a registry conflict MUST NOT overwrite a record the plugin
  does not own; a same-plugin change converges by replacing only the
  plugin-owned record.
- **Decided: official names are reserved first and are not overwritable.** The
  maintainer decision this section previously reserved is made. The kernel owns
  a static, auditable list of official plugin names
  (`OFFICIAL_TOOL_PLUGIN_NAMES`, `src/lingtai/kernel/tool_plugin/__init__.py`).
  `register_official_tool_plugins` validates every declared name in a batch —
  reserved, unique in the batch, and not already claimed by a *different*
  declaration — **before** the first `bind()`, the first activation, and the
  first official mount. A conflict raises and leaves the live tool surface and the
  claim map untouched: there is no last-registration-wins path for an official
  name and no partially mounted batch. Re-registering the *same* declaration is
  idempotent, because refresh re-runs the whole boot.
- **Scope of that decision, stated exactly.** It governs official names at the
  common model-facing mount boundary, not only declarations. The kernel-owned
  `_add_tool` primitive rejects generic `Agent.add_tool` and external/legacy
  MCP attempts to publish a statically reserved name; only the production
  registrar route carries the opaque authorization needed to mount the bound
  official declaration. Nonreserved names retain the existing same-name
  replacement behavior. External MCP catalogs are preflighted and rejected
  before their client, metadata, or reverse route is retained, so an official
  handler/schema/claim cannot be replaced and no stale external route survives.

**Observable failures.** A malformed declaration — empty or duplicate actions,
an operational action claiming the reserved `manual`, a missing per-action
input schema, or a host port that is not grantable — fails at import, loudly,
before an Agent exists. An unreserved or conflicting official name fails at
registration, before any bind or mount, and a family that ships a public action
inventory other than the one it declared fails at `bind()`. None of these are
swallowed by the Composition Root's capability skip-guard: they descend from
`ToolPluginError`, which is deliberately not a `ValueError`, so they fail the
boot instead of leaving an agent up with the official tool silently missing (a
capability that genuinely cannot register still returns `CAPABILITY_UNAVAILABLE`
instead of raising). An invalid manifest or unsupported
version rejects the whole plugin. An invalid or path-escaping skill or server
component is skipped with a bounded, source-attributed reason instead of
failing the agent. A
malformed call or envelope is rejected before manager I/O. A missing packaged
manual degrades honestly and says so rather than fabricating content. A launch
or transport failure is observable and retries only through the lifecycle
policy above. Business-tool errors stay exactly as the family already returns
them: a declaration or its adapter MUST NOT rewrite a domain error as a plugin
error, and no failure path may leak secrets or resolved configuration.

**Compatibility.** Existing declaration surfaces are unchanged — the canonical
spelling and its read-compatible alias both keep working, and current init and
registry files are not rewritten. Legacy tool names, inputs, actions, manual
paths and results, direct external MCP schemas, and existing tool lifecycle
semantics MUST NOT be silently altered. Migration is additive at the
declaration, packaging, and host-composition boundaries only; a wire or schema
change requires that family's own explicitly authorized PR.

**Vertical migration.** Each family recuts vertically in its own later PR: its
declaration and the host ports it earns, its bind, host composition wiring, any
transport package data, its local Contract/Anatomy/Behaviors and manual, and
the evidence that migration's reviewer asks for. Until then the family's
runtime and schema are unchanged, exactly as `### Scope` already requires for
LTP itself.

**Non-goals of this section.** It introduces none of the following, and a later
migration MUST NOT add one merely to satisfy this file: a generic wrapper
runtime; a universal MCP server compiler; a generic manifest compiler or
plugin-admission engine; a multi-form manifest compatibility layer; a universal
validator or conformance suite; automatic discovery, activation, install, or
uninstall; a registry rewrite; a provider protocol rewrite; or conversion of
arbitrary third-party MCP schemas. It does not state that registration launches
a server, and it changes no wire or schema behavior for any family.

The one shared declared contract type (`ToolPluginDeclaration` and its host
ports) is deliberately **not** banned by this clause: it *is* the selected form
above, and one declared contract is what makes "every model-facing tool follows
one contract" checkable. What stays banned is the generic wrapper *runtime*
around it — a universal executor, a plugin-admission engine, a manifest
compiler, or a discovery mechanism. The distinction is the point: a declaration
is data plus one bind callable, validated at import; a runtime is a machine
that finds, admits, and executes plugins. The reserved-name list is likewise a
list, not a registry service.

The **implemented** collision mechanism is now in scope and shipped for
official declared names only, per the identifier clause above; it remains a
non-goal for third-party-versus-third-party mounts.

**Current evidence versus migration target.**

- The selected form is generic. `mcp` is the base reference; Avatar, Context,
  Daemon, Email, File, Plugin, Notification, Shell, Soul, System, Task Card,
  Vision, and Web are accepted vertical evidence. Soul's declaration binds only
  `workdir`/`soul_runtime`;
  its focused suites (`tests/test_tool_family_soul_migration.py`,
  `tests/test_soul_runtime_port_ab.py`, `tests/test_soul_settings.py`) prove the
  explicit runtime port, exact grant, preserved six-action operational surface,
  additive reserved SHOW child, one mount, and sole package manual body at
  `soul-manual`. Email's declaration
  binds only `workdir`/`email_runtime`; its focused suite proves the typed port,
  one mount/no capability row, canonical manual, and call-time replacement
  manager. File's declaration binds only
  `workdir`/`file_io`/`configuration`; its focused suites prove the typed
  adapters, exact grant, preserved operations, exact source/snapshot-backed
  13-row SHOW with no writer/file, one mount, sole package body at
  `file-manual`, and wheel/sdist package-data route. Task Card's
  declaration binds only
  `workdir`/`shutdown`/`task_card_lifecycle`/`task_card_notifications`; its
  focused suites (`tests/test_task_card_controller.py`,
  `tests/test_task_card_notifications.py`, plus the Task Card cases in
  `tests/test_tool_plugin_declaration.py`) prove one retained manager rebound
  across refresh, post-bind persisted-watch resume, the package-owned manual,
  exact error/recovered/limit and reminder wire parity through the production
  five-operation notification adapter, and foreign source/channel/field
  refusal. Vision's declaration binds only
  `workdir`/`active_provider`/`configuration`; its focused suites
  (`tests/test_tool_family_vision_migration.py`,
  `tests/test_vision_capability.py`, `tests/test_inherit_fallback.py`, the
  Vision case in `tests/test_tool_plugin_declaration.py`, and the strict
  controlled-host manual proof in `tests/test_intrinsic_manual_actions.py`)
  plus `tests/test_vision_settings.py` prove the five-action schema/dispatch
  and read-only settings surface, active-provider default
  routing, allowed-preset own-credential borrowing with no automatic
  provider/MCP fallback, `check`/`list`/`settings`/`manual` no-request
  boundaries, and the package-owned manual. Web's declaration binds only
  `workdir`/`web_runtime`/`provider_identity`; its focused suites
  (`tests/test_web_official_plugin.py`, `tests/test_web_composition_port.py`,
  the Web case in `tests/test_tool_plugin_declaration.py`, the strict
  controlled-host manual proof in `tests/test_intrinsic_manual_actions.py`,
  and the family-local `tests/test_web_canonical_provider_routing.py`,
  `tests/test_web_output_spill.py`, `tests/test_web_search_capability.py`,
  `tests/test_unified_web_capability.py`) prove the exact three-port grant,
  the fail-closed typed `web_runtime` bind, the standard-table
  `provider_identity` label, one mount with the manager published back
  exactly once, idempotent refresh re-claim, the package-owned manual with
  strict zero-input behavior, exact-match canonical provider gating, spill
  delivery, and search-vs-browse isolation. The former later-family target
  register is now empty.
  Avatar, Context, and Daemon retain
  their independently accepted declarations and focused coverage. The shared
  test seam is `tests/_tool_plugin_helpers.py`; each other family still needs its
  own vertical evidence. `src/lingtai/kernel/tool_plugin/__init__.py` owns the
  declaration type, host ports, exact ordered reserved-name list, and fail-fast
  registrar; `src/lingtai/adapters/tool_plugin_host.py` is the one production
  adapter set; `src/lingtai/tools/mcp/__init__.py` is the base slice; and
  `tests/test_tool_plugin_declaration.py` plus the generic helper are shared
  evidence. Plugin's declaration binds only
  `workdir`/`prompt_section`/`plugin_catalog`; its focused suite
  (`tests/test_plugin_tool.py`) proves the read-only action boundary, the
  protected-field skill projection, the closed vanilla-skills namespace, and the
  detached per-read catalog projection, while
  `tests/test_tool_plugin_declaration.py` proves its one mount, its real
  `info`/`settings`/`manual` dispatch, and that a standard-table port stays
  unreachable for a declaration that did not name it. MCP public behavior
  remains guarded by its
  parity/capability suites; Avatar's static declaration, restricted ports, local
  manual, behavior, and registrar path remain guarded by
  `tests/test_tool_family_avatar_migration.py`.
- The curated **external-transport** route ships for the curated MCP families
  only, and is cited as precedent for that route rather than as conformance to
  the declaration clauses: `src/lingtai/mcp_servers/_plugin.py` binds one
  package's server, bundled `SKILL.md`, launch record, and reserved `manual`
  child, and refuses a package that declares `manual` itself;
  `src/lingtai/mcp_servers/telegram/plugin.py` is the reference descriptor
  slice; `src/lingtai/mcp_catalog.json` carries the matching record for each of
  the six curated families. Its own docstring states it is deliberately not a
  plugin runtime. This is packaging and launch evidence about those six
  families — nothing more.
- External Agent Plugins v1.0.0 already separate declaration from activation
  (`src/lingtai/services/plugin_registry.py`, and
  `src/lingtai/tools/plugin/CONTRACT.md` for the tool that renders the result).
  That standard is excluded from conversion by `**Selected wrapper form**`; it
  is cited only for the registration-versus-activation rule.

Not evidenced, and therefore not claimed above: any other registry family
shipping as an MCP plugin package (`registry.py` imports no plugin packaging); a
`CuratedMcpPlugin` descriptor or packaged `SKILL.md` for the built-in daemon MCP
families; and the retention, dispatch, host-lifecycle, and manual clauses above
proven for every family, curated ones included.

### Relationship to current runtime

Nothing here describes shipped behavior beyond what each migrated family already
documents. `web` (`search | browse | settings | manual`) is the first family migrated to
this contract: its final model-facing root is exactly `action`, `input`,
`reasoning`, and `summarize`; its `search` action resolves
`LINGTAI_WEB_ENGINE` before the action-owned `settings/web.search.json` (see
`src/lingtai/tools/web_search/CONTRACT.md`).
`knowledge` (`info | manual`) is the third: the migration is envelope-only —
its public tool name and both public action values are unchanged, both children
take the canonical strict-empty `input`, and it supports no settings file (see
`src/lingtai/tools/knowledge/CONTRACT.md`). It remains a signpost capability
with no authoring, search, or edit action.

`mcp` (`info | settings | manual`) is the first family declared under
`### Tool-to-MCP Plugin Contract`: the reserved settings action is the sole
additive surface and is bound to the owner's five-field SHOW provider; the
public tool name and existing action result shapes including the tool-specific
`mcp_manual` body key are unchanged, while the family reaches the live Agent
body through two granted host ports instead of the whole `Agent` (see
`src/lingtai/tools/mcp/CONTRACT.md`).

`avatar` (`spawn | rules | manual`) is the second family declared under
`### Tool-to-MCP Plugin Contract`: its static declaration preserves the public
tool name, action values, strict action inputs, and result behavior while
binding `AvatarManager` only to `workdir` and `avatar_parent`, never the whole
`Agent` (see `src/lingtai/tools/avatar/CONTRACT.md`).

`context` (`molt | summarize | rebuild | manual`) is the current in-process lifecycle
vertical slice. It keeps Context's LTP shape, molt transport seam, and live
rebuild behavior while binding only `workdir` and `context_runtime`; its package
manual is the canonical source installed at the historical `context-manual` path
(see `src/lingtai/tools/context/CONTRACT.md`).

`daemon` (`emanate | list | ask | check | reclaim | manual`) is the fourth
actual declared vertical slice. It preserves Daemon's existing manager and
family dispatch semantics while binding only `workdir` and `daemon_runtime`; the
runtime notification operation resolves the host route at publication time so
terminal publication failure remains retryable (see
`src/lingtai/tools/daemon/CONTRACT.md`).

`email` is the fifth declared vertical slice. Its declaration binds exactly
`workdir` and its Email-owned `email_runtime`; mandatory boot creates/replaces
the manager before registration, and the narrow adapter reads that manager at
call time without intrinsic or official-handler dispatch (see
`src/lingtai/tools/email/CONTRACT.md`).

`file` is the sixth declared vertical slice. Its declaration binds exactly
`workdir`, kernel-owned `file_io`, and an immutable `configuration` snapshot;
`AgentFileIOAdapter` exposes only typed text/search operations plus traversal
and result-cap facts, and setup supplies it together with the factory-applied
bounded backend snapshot only through `extra_ports_for`; the sensitive sidecar
value is fully redacted before projection. The package manual
is the one operational body,
installed at `capabilities/file-manual` (see
`src/lingtai/tools/file/CONTRACT.md`).

`web` is the fourteenth declared vertical slice. Its declaration binds exactly
`workdir`, its Web-owned typed `web_runtime` composition (`WebComposition`:
browser transport plus immutable engine specs and default provenance, composed
by `setup` and granted to the `web` declaration alone through
`extra_ports_for`, with the bound `WebManager` published back through it
exactly once so `setup` still returns the manager), and the narrow read-only
`provider_identity` label built in the standard table only for `web`.
`_bind` MUST fail closed with the kernel's `HostPortError` on a missing,
legacy-carrier, or mistyped `web_runtime`; there is no generic runtime port, no
global runtime table, and no automatic provider/browser fallback beyond the
family's one documented OpenAI→DuckDuckGo runtime fallback (see
`src/lingtai/tools/web_search/CONTRACT.md`).

Separately, `file` (`read | write | edit | glob | grep | settings | manual`) is the fourth family
migrated to the LTP envelope contract, and the first aggregation of several former public
roots into one: its final model-facing root is exactly `action`, `input`,
`reasoning`, and `summarize`. The migration was a clean break rather than an
adapter layer — the five old model-facing roots, their implementation packages,
their per-operation contracts and glossaries, and their capability names were
all deleted, with the behavior folded into the single `lingtai.tools.file`
owner. Those five capability names are now unknown and fail loudly; `file`
surfaces no settings file at either level. Its `settings` action is strict,
SHOW-only, provider-bound, and immediately before `manual`; it exposes exactly
the File owner's 13 policy/selector rows without adding set/reset (see
`src/lingtai/tools/file/CONTRACT.md`).

`vision` (`analyze | check | list | settings | manual`) is the fifth: it keeps its public
tool name and action values while moving to the same root envelope, with
`analyze` owning the direct image request (default route: the active provider
only; an explicitly allowed `preset` borrows only that preset's own route for
the one call), `check` resolving the selected route without any image/provider
request, `list` enumerating only authorized preset declarations, `settings`
showing one read-only applied bind snapshot, and `manual` the family-owned
reserved child (see `src/lingtai/tools/vision/CONTRACT.md`). The fixed
workdir-relative `settings/vision.json` remains the only Vision-owned document
and configures only the generic `local` provider. SHOW adds no settings file,
parser, writer, or generic control plane.

`avatar` (`spawn | rules | manual`) is the sixth family migrated, keeping its
public name and action values unchanged (see
`src/lingtai/tools/avatar/CONTRACT.md`, contract_version 4). It owns no
settings file at either level, and its manual says so explicitly. Two
avatar-specific facts are worth naming here because they are envelope
consequences, not local details: its `spawn` mission brief is root `reasoning`
(never an `input` property, per "Envelope"), and its `rules` action is
karma-gated while `spawn` and `manual` are not — a family must not hide a
stronger child action behind a weaker family posture.

`shell` (`run | poll | cancel | settings | manual`) is the eighth: its final model-facing
root is likewise exactly `action`, `input`, `reasoning`, and `summarize`, its
run-only fields live only in `run`'s `input` and `job_id` only in
`poll`/`cancel`'s, and its unchanged `ShellManager` engine — sync execution,
the working-directory sandbox, the durable async lifecycle, cancellation, and
terminal receipts — keeps its historical flat shape as a purely internal
interface. Its declaration opts into the generic reserved, read-only SHOW child
immediately before `manual`; Shell owns no settings file at either LTP level
(see `src/lingtai/tools/bash/CONTRACT.md`).

`skills` (`info | manual`) is the ninth: it keeps its public tool name and both
public action values, adopts the same closed root, declares the canonical
strict-empty `input` object for both actions, and supports no settings file at
all — its manual says so explicitly (see
`src/lingtai/tools/skills/CONTRACT.md`). Family boundaries here follow the
shared-domain rule above: `info` and `manual` are two actions of one skill-
catalogue authority, not two related tools grouped for convenience.

`notification` (`check | dismiss_channel | dismiss_event | dismiss_ref | add |
drop | edit | list | delay | settings | manual`) is the tenth: its final model-facing root
is likewise exactly `action`, `input`, `reasoning`, and `summarize`, and each
action's arguments live only in that action's own strict `input` (so `channel`
belongs to `dismiss_channel`, `event_id` only to `dismiss_event`, and `ref_id`
only to `dismiss_ref`). It is the sixth accepted declared official family: its
static declaration binds an agent-hosted ToolFamily through only `workdir` and
`notification_state`, while Notification Core remains the sole authority for
stateful policy and Store mutation. Its declaration-bound read-only provider
uses that port for the two Notification-owned effective rows; no generic
configuration or mutation surface is added (see
`src/lingtai/tools/notification/CONTRACT.md`).

`system` (`refresh | sleep | lull | interrupt | suspend | cpr | clear |
nirvana | presets | name_set | name_nickname | settings | manual`) is the
eleventh, and
the third
migrated *intrinsic*: its final model-facing root is likewise exactly `action`,
`input`, `reasoning`, and `summarize`, and each action's arguments live only in
that action's own strict `input` — so `address` belongs to the six address
verbs, `preset`/`revert_preset` only to `refresh`, and `content` only to the
two name actions; there is no public `system(action='summarize')`, and
`items`/`rebuild` belong to no `system` action
(see `src/lingtai/tools/system/CONTRACT.md`). It is also the eleventh declared
official family: its static `DECLARATION` binds only `workdir`,
`system_runtime`, and `identity`, and `karma.sleep_use_case` is the single
self-sleep policy owner for both mounted and direct routes. Two facts are worth
naming here because they are envelope consequences rather than local details.
First, the family's three privilege classes (self, karma, karma+nirvana) are
*per action*, so the closed per-action `input` is load-bearing for safety, not
just for tidiness: `address` is undeclared on `sleep`, which means the
always-authoritative dispatch layer rejects a smuggled target before any signal
file is written — the same "a family must not hide a stronger child action
behind a weaker family posture" rule `avatar` established, applied to
rejection rather than gating. Second, `sleep.force` was live and read by the
handler before this migration but never advertised in the flat schema; a strict
child `input` must declare every key its handler accepts, so declaring it
surfaces existing behavior rather than adding a capability. System opts into
reserved read-only SHOW through `ToolPluginDeclaration(settings=True)` plus its
bound provider. The optional family owner document `settings/system.json` is closed and
versioned: v1 is exactly the cache-miss-budget source; v2 may carry the seven ordinary runtime-policy fields, the cache field, and Notification's file layer.
Its absence leaves SHOW available with fixed defaults, and file presence never
opts a family into SHOW. System declares no per-action settings and authorizes
no set/reset or mutation path.

`daemon` (`emanate | list | ask | check | reclaim | settings | manual`) is the twelfth
family migrated to this contract, and the one with the largest retained engine.
Its final model-facing root is exactly `action`, `input`, `reasoning`, and
`summarize`, and each action's arguments live only in that action's own strict
`input`: `tasks`/`backend`/`max_turns`/`timeout` belong to `emanate`,
`contains`/`status`/`include_done` to `list`, `message` to `ask`, `truncate` to
`check`, while `reclaim`, `settings`, and `manual` take the canonical strict-empty `input`
(`id` is shared by `ask`/`check` and `last` by `list`/`check`, each declared in
both branches). It follows `shell`'s division: a dedicated
`daemon/_tool_family.py` owns the public schema and a `DaemonFamilyDispatcher`
that translates the envelope into `DaemonManager`'s unchanged legacy flat call
shape, so the emanation engine, backend routing, detached supervisor,
completion signaling, cancellation, timeouts, and terminal notifications are
untouched by the migration. Its pre-migration flat `summary` boolean is
replaced by the canonical root `summarize`, joining the allowlist below in the
same change. See `src/lingtai/tools/daemon/CONTRACT.md`.
Daemon opts into reserved read-only SHOW through its declaration plus a bound
manager provider. The provider exposes exactly four Daemon-owned settings and
inserts `settings` immediately before `manual`; it adds no set/reset or other
writer. Meaning, precedence, and authorized change procedures remain in the
Daemon manual rather than this shared contract.
`email` (`send | check | read | dismiss | reply | reply_all | search |
archive | delete | contacts | add_contact | remove_contact | edit_contact |
manual`) is the thirteenth family migrated to this contract, and the widest
child registry so far. Its final model-facing root is exactly `action`,
`input`, `reasoning`, and `summarize`; the public tool name and all fifteen
action values are unchanged, and each action's arguments now live only in that
action's own strict `input` (so `query` belongs to `search`, `filter`/`n` to
`check`, and `attachments`/`delay`/`mode` to `send`) instead of the one open
flat bag every action previously shared. It is the fourth migrated *intrinsic*,
so it composes its dispatching family per call and strips the kernel-injected
`_tc_id` at its own boundary. Two facts are envelope consequences worth naming
here: its `unread` action is kernel-synthesized digest state and is
deliberately **not** a public child, keeping its own exact pre-migration
rejection rendered before dispatch; and `EmailManager`'s historical flat
argument shape is retained unchanged as a purely internal interface, exactly
as `shell` kept `ShellManager`'s. It owns no settings file at either level.
See `src/lingtai/tools/email/CONTRACT.md` (contract_version 2).

`psyche` (`lingtai_update | lingtai_load | pad_edit | pad_load | pad_append |
context_molt | name_set | name_nickname | manual`) is the fourteenth, and the
fifth migrated *intrinsic*. It is the first migration to fold a **two-key**
public surface into this envelope: psyche was addressed as an
`(object, action)` matrix, and each pair became exactly one flat action, the
same collapse `notification` made for its atomic dismiss verbs. The operation
inventory is preserved exactly — nothing added, dropped, renamed, or merged —
and every operation-level success payload and error, every log event, and every
persistence path is unchanged; only the argument shape and the envelope layer
around it moved. Envelope validation and its errors are necessarily new under
this contract, and psyche's former two-key unknown-object/invalid-action guards
became one unknown-action error (see
`src/lingtai/tools/context/CONTRACT.md`). It
owns no settings file at either level and its manual says so.

Three psyche facts are envelope consequences worth naming here. Its molt
retrospective is `input.summary` — the domain field this contract's "Envelope"
section explicitly permits, never the root `summarize` control. Its two
destructive full rewrites (`lingtai_update`, `pad_edit`) and its irreversible
`context_molt` make the "reject before dispatch" rule load-bearing rather than
merely tidy: a wrong-branch key must fail with nothing written and nothing
shed. And it is the first family that *consumes* the intrinsic-only `_tc_id`
rather than dropping it, so it strips that key at its own Host boundary and
threads it to the one action that needs it, instead of widening the shared
envelope.

**Current state (the paragraph above is migration history).** That family no
longer exists, and neither do the `pad`/`lingtai`/`knowledge`/`skills` public
roots that briefly succeeded it. The four durable domains are now taught by one
read-only root, `psyche` (`pad | lingtai | knowledge | skills | settings |
manual`,
`src/lingtai/tools/psyche/CONTRACT.md`, the equation
`pad + lingtai + knowledge + skills = psyche`): five actions return the domain
or routing manual; `settings` returns the eight fully redacted Psyche-owned Pad
and configurable-prompt rows. Every action mutates nothing. Those four packages remain as private lifecycle
owners — Pad/LingTai composers and the Skills/Knowledge catalogs plus the
Knowledge legacy migration — and register no tool. Generic durable mutation
belongs to `file.write`/`file.edit`, which never hot-load prompt state; the
retired `pad.append`, `skills.info`, and `knowledge.info` actions have no
aliases. The
context lifecycle is `context` (`molt | summarize | rebuild | manual`,
`src/lingtai/tools/context/CONTRACT.md`): `summarize` records only, while
`rebuild` is the one active operation that first recomposes every canonical
prompt source, then applies pending/new summaries, then requests provider
replay; bare `{}` remains valid with zero pending summaries. Refresh and molt
invoke that same internal reconstruction contract as passive scenarios. Name
actions moved to `system`. There is no alias for the dissolved Psyche actions
and no public `system(action='summarize')`. `context` alone consumes `_tc_id`; its action
named `summarize` remains unrelated to the root boolean control.

The legacy a-priori result-summarization flag under the literal key `summary`
(`src/lingtai/kernel/tool_result_summary.py:172`) remains honored for every
still-unmigrated caller; `src/lingtai/kernel/tool_result_summary.py` recognizes
the canonical `summarize` spelling only when the calling tool is a migrated LTP
v2 family (`_LTP_V2_MIGRATED_FAMILIES`, currently `web`, `mcp`, `knowledge`,
`file`, `vision`, `avatar`, `soul`, `shell`, `skills`, `notification`, `system`,
`daemon`, `email`, `pad`, `lingtai`, `context`, and `plugin`), so
an unmigrated tool's own field literally named `summarize` is never
reinterpreted as this control. A family adopting this envelope MUST join that
allowlist in the same change, or the root `summarize` it advertises to the
model would be silently ignored. Every other LingTai-owned family remains
unmigrated and keeps its existing schema and settings surface unchanged by
this file.

`mcp` is the second migrated family: public tool name `mcp`, actions `info |
settings | manual`, all taking the canonical strict-empty `input`. The bounded
settings owner slice is SHOW-only; existing action behavior stays read-only and
external MCP registration (direct insertion into `mcp_registry.jsonl`) is
untouched by it.
See `src/lingtai/tools/mcp/CONTRACT.md`.

`plugin` is `mcp`'s deliberate twin and the only family born on this envelope
rather than migrated onto it: public tool name `plugin`, actions `info |
settings | manual`, all taking the canonical strict-empty `input`. The generic
provider seam injects settings immediately before manual and offers no mutation
form. It renders the per-agent Agent Plugins (agent-plugins.org,
v1.0.0) catalog and boot registration snapshot into the protected `plugin`
prompt section. The *tool* owns no state and writes no file; mounting a declared
plugin — composing its `skills/` into the skills catalog and appending its
`mcp.json` servers to `mcp_registry.jsonl` with `source="plugin:<name>"` —
happens once at boot in `Agent._register_declared_plugins`, mirroring `mcp`'s
addon decompression and unreachable from any action. That is why it is safe in
`CORE_DEFAULTS`, and registration is registry-level only: registered, never
running. See `src/lingtai/tools/plugin/CONTRACT.md`.

`soul` (`inquiry | flow | config | voice | dismiss | settings | manual`) is the
seventh family migrated to this contract, and the first migrated *intrinsic*.
Its final model-facing root is exactly `action`, `input`, `reasoning`, and
`summarize`; each action owns one strict closed `input` object, and its
`summarize` guidance profile is **short-result** for every action (see
`src/lingtai/tools/soul/CONTRACT.md`). The reserved `settings` child is a
five-row SHOW over Soul's existing sources; `soul` still supports no settings
file at either level and its manual says so explicitly. Being an intrinsic, it also
proves one boundary `web` could not: `base_agent._dispatch_tool` injects the
transport-only `_tc_id` into every intrinsic's args, so a migrated intrinsic
drops that key at its own Host boundary before the closed-root check rather
than widening the shared envelope's admitted root fields.

`src/lingtai/tools/tool_family/` is optional, generic composition
infrastructure implementing this envelope (schema composition from a
`ChildTool` registry, dispatch-validation boilerplate, and a reusable
ManualTool builder) that a family MAY adopt instead of hand-writing the
equivalent code; `web` is its first consumer, using it for schema composition
and dispatch while retaining its own outer `handle()` for family-specific
diagnostics, `mcp` is its second, retaining its own outer `handle_mcp()`
for its exact pre-migration unknown-action envelope, `knowledge` is its
third, using it the same way with its own outer `handle()` preserving that
family's exact pre-migration unknown-action result, `file` is its fourth
(below), `vision` is its fifth, using it the same way while retaining
its own outer `handle()` for the family's flat manual/error result shapes,
`avatar` is its sixth, restoring its own pinned unknown-action error
envelope the same way, `soul` is its seventh, composing `get_schema()`
from a module-level schema-only family and building an agent-bound one per
`handle(agent, args)` call because an intrinsic module has no per-Agent
manager instance to hold one, `shell` is its eighth, using it the same
way while retaining a thin outer `handle()` that narrows the generic
unknown-action message to its own five actions, `skills` is its ninth,
using it the same way but returning its canonical envelope failures
verbatim, having no such diagnostics, `notification` is its tenth, binding an
agent-hosted family through its static declared Host ports; generic composition
injects its reserved `settings` child immediately before `manual`, and its outer
adapter flattens the reserved `manual` child's canonical result to the pinned
public shape and preserves Notification's own unknown-action result, while `context`
is its eleventh, using `soul`'s module-level composition shape while threading the
`_tc_id` it actually consumes to its `molt` child out-of-band rather
than widening the shared envelope. `avatar` reuses
`ToolFamily` but not `build_manual_child`, because its manual ships inside
its own package rather than the agent's installed `.library` catalog —
adopting part of the infrastructure is conforming. Using it is never
required — see its own
`src/lingtai/tools/tool_family/CONTRACT.md` "Implementation independence" is
binding on it exactly as it is on every family.

`file` is that illustration realized: one family with actions
`read | write | edit | glob | grep | settings | manual` whose five operation
implementations remain fully independent, sharing nothing but the family name
and the wire envelope —
co-located in one package as `_read.py`, `_write.py`, `_edit.py`, `_glob.py`,
and `_grep.py`, where none imports another. Single ownership is not shared
implementation.
The generic settings child and package manual remain separate reserved siblings;
neither couples the five operation modules.
It is also the worked example of the family-boundary rule above — the five
operations are one family because they act on one working tree through one
authority (the injected `FileIOService`) under one sandbox, not because their
code looks alike.

## Contract tests

**There is no universal LTP validator, registry, schema compiler, or machine-
enforced conformance suite, and this contract does not introduce one.** Alignment
to LTP is maintained through this contract and the paired `ANATOMY.md`, reviewed
per migration — not through a central programmatic gate.

Evidence for a migration is therefore documentary and reviewed: the migrating PR
shows its final model-facing schema, states which envelope and settings rules it
satisfies, and updates this contract's related documents where the promise
changes. A reviewer checks that against the rules above.

Individual families and actions MAY keep their own behavior tests as locally
chosen evidence, and are encouraged to where the risk warrants it — for example
around envelope root properties, closed input branches, wrong-branch rejection,
`summarize` retention and isolation on both the single and parallel call path,
raw output recorded before any visible replacement, exact error results,
`summarize` never reaching the action implementation, and loud failure on an
invalid settings file. LTP does not mandate one universal suite covering these,
and a family choosing a different local evidence set is not thereby
non-conforming.

Web's focused capability, wire, and executor tests are this contract's first
migration evidence: they cover the full closed root (`action` / `input` /
`reasoning` / `summarize`), closed input branches, wrong-branch and non-boolean
rejection, `summarize` retention and isolation on both the single and a
controlled-parallel call path, raw output recorded before any visible
replacement, exact `status: "failed"` results under `summarize=true`, and the
action-owned `settings/web.search.json` surface (see
`src/lingtai/tools/web_search/CONTRACT.md` Contract tests). They remain one
family's local evidence, not a conformance suite, and no such suite is required
to exist.

`file`'s focused suite (`tests/test_file_tool_family.py`) is the second
migration's evidence, chosen for its own risks: exactly one public root with no
surviving old roots, the closed envelope, action/input correlation on both
wires, every child's schema/dispatch/result/error, cross-action rejection
before handler I/O, the no-I/O family manual with read pagination as a nested
reference, read continuation and line truncation, verbatim write/edit receipts,
and the `summarize` control and truthful mixed read/write risk posture. The
retained operations' own suites (`tests/test_layers_file.py`,
`tests/test_read_continuation.py`) continue to cover per-operation depth. This
is a different evidence set from web's, which is exactly what the paragraph
above permits.

`tests/test_tool_family_avatar_migration.py` is `avatar`'s own local evidence
for the same rules, chosen for that family's risk: the closed root, per-action
child inputs, root `allOf` correlation surviving both wires, cross-action and
unknown-root-field rejection *before* any handler I/O, `summarize` never
reaching a child handler and `avatar` actually being on the kernel allowlist,
the preserved unknown-action envelope, spawn's dry-run/mission-guard/identity
and path validation, the karma gate and distribution for `rules`, and `manual`
performing no spawn or rules I/O. Every test there builds its own isolated
temporary network and fakes the launcher Port, so it neither creates a live
avatar nor writes a live `.rules` signal.

`context`'s evidence (`tests/test_tool_family_context_migration.py`,
plus the updated `tests/test_context.py`, `tests/test_pad.py`,
`tests/test_eigen.py`, `tests/test_session_journal_gate.py`, and
`tests/test_intrinsic_manual_actions.py`) is likewise one family's local
evidence, chosen for a risk profile no earlier migration had: the irreversible
molt plus the record/apply pair that rewrites what the provider actually sees.
It covers the exact four-action inventory (`molt | summarize | rebuild |
manual`), the record-only-versus-applying split that replaced the former
`rebuild` boolean, the proof that no `psyche` root survives anywhere, the
closed root on both wires with the `allOf` correlation intact, per-action input
isolation, envelope and cross-branch rejection before any file write or context
shed, `_tc_id` isolation on the consume-rather-than-drop path, the molt
journal gate refusing before any shed, a full successful molt lifecycle in a
disposable workdir, the synthesized system-forced pair carrying the current
envelope, and the reserved `manual` child's no-double-wrap result.

`soul`'s migration evidence (`tests/test_tool_family_soul_migration.py`, plus
the updated `tests/test_soul.py`, `tests/test_soul_consultation.py`,
`tests/test_system_dismiss.py`, `tests/test_intrinsic_manual_actions.py`, and
`tests/test_soul_settings.py`) is likewise one family's local evidence: it
covers the six existing children plus additive reserved `settings`, the closed
root on both provider wires, wrong-branch rejection
before any handler I/O, `reasoning`/`_reasoning`/`summarize`/`_tc_id`
isolation from child input, the reserved `manual` child's
full-body/`manual_path` result with no double wrap and no soul operation, and
— specific to this family — that the opt-in `flow` env gate stays the only
enable path and that a disabled `flow` is a stable status rather than an
error.

`task_card` is a migrated intrinsic family as well, but with a narrower
producer-first boundary than the channel adapters that may consume it. Its local
evidence (`tests/test_task_card_controller.py`,
`tests/test_telegram_toolfamily_ltpv2.py`,
`tests/test_telegram_task_card_programmable.py`) covers the closed family root,
the exact agent-local file contract, activation/deactivation ordering, and the
fact that transport-specific projection semantics belong to the consuming
adapter rather than the intrinsic producer.

## Maintenance

Keep this shared contract directional and concise. Add a family only after a real
scoped migration has code, contract/manual updates, and reviewed evidence that it
meets these rules. LTP alignment is maintained by keeping this contract and the
paired `ANATOMY.md` honest and current, not by a central validator; do not add
one here. Do not use this file to mass-normalize legacy schemas, to justify a
shared implementation framework, or to rename external provider protocol fields.
