---
related_files:
  - ANATOMY.md
  - src/lingtai/intrinsic_skills/ANATOMY.md
  - src/lingtai/tools/BEHAVIORS.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - src/lingtai/tools/channel_reply/ANATOMY.md
  - src/lingtai/tools/channel_reply/CONTRACT.md
  - src/lingtai/tools/feishu/BEHAVIORS.md
  - src/lingtai/tools/psyche/ANATOMY.md
  - src/lingtai/tools/plugin/ANATOMY.md
  - src/lingtai/tools/plugin/CONTRACT.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/telegram/BEHAVIORS.md
  - src/lingtai/tools/web_search/ANATOMY.md
  - src/lingtai/tools/web_search/CONTRACT.md
  - src/lingtai/tools/task_card/ANATOMY.md
  - src/lingtai/tools/task_card/CONTRACT.md
  - src/lingtai/tools/file/ANATOMY.md
  - src/lingtai/tools/file/CONTRACT.md
  - src/lingtai/tools/file/manual/SKILL.md
  - src/lingtai/tools/vision/ANATOMY.md
  - src/lingtai/tools/vision/CONTRACT.md
  - src/lingtai/tools/soul/ANATOMY.md
  - src/lingtai/tools/soul/CONTRACT.md
  - src/lingtai/tools/browser/ANATOMY.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/system/ANATOMY.md
  - src/lingtai/tools/system/CONTRACT.md
  - src/lingtai/tools/context/ANATOMY.md
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/tools/knowledge/ANATOMY.md
  - src/lingtai/tools/knowledge/CONTRACT.md
  - src/lingtai/tools/pad/ANATOMY.md
  - src/lingtai/tools/pad/CONTRACT.md
  - src/lingtai/tools/lingtai/ANATOMY.md
  - src/lingtai/tools/lingtai/CONTRACT.md
  - src/lingtai/tools/avatar/ANATOMY.md
  - src/lingtai/tools/avatar/CONTRACT.md
  - src/lingtai/tools/bash/ANATOMY.md
  - src/lingtai/tools/bash/CONTRACT.md
  - src/lingtai/tools/bash/_tool_family.py
  - src/lingtai/adapters/browser_transport.py
  - src/lingtai/mcp_servers/_plugin.py
  - src/lingtai/mcp_servers/telegram/plugin.py
  - src/lingtai/mcp_catalog.json
  - src/lingtai/services/plugin_registry.py
  - src/lingtai/kernel/base_agent/tools.py
  - src/lingtai/tools/registry.py
  - src/lingtai/tools/glossary_validator.py
  - ENVIRONMENT_VARIABLES.md
  - src/lingtai/tools/__init__.py
  - src/lingtai/tools/_manual.py
  - src/lingtai/tools/email/ANATOMY.md
  - src/lingtai/tools/i18n/__init__.py
  - src/lingtai/tools/i18n/en.json
  - src/lingtai/tools/i18n/wen.json
  - src/lingtai/tools/i18n/zh.json
  - src/lingtai/tools/mcp/ANATOMY.md
  - src/lingtai/tools/skills/ANATOMY.md
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/interactive_terminal/ANATOMY.md
maintenance: |
  Keep this registry Anatomy connected to its parent and the unified web owner.
  Browser is an internal browse child, not a second public capability. The
  generic tool_family package is optional composition infrastructure any
  future family migration may adopt, not a second registry. context is its
  thirteenth consumer and the fifth migrated intrinsic. Update
  structural claims with code and keep reciprocal graph edges valid.
  The curated-MCP packaging, catalog, Agent Plugins, and host tool-registration
  entries in related_files are navigation for the paired Contract's
  `### Tool-to-MCP Plugin Contract`. The form that Contract selects is now the
  kernel-owned declared host-plugin contract
  (`src/lingtai/kernel/tool_plugin/ANATOMY.md`, linked here); the curated
  descriptor/catalog route is the retained external-transport/launcher adapter
  over a declaration, and the Agent Plugins entry is the excluded external
  standard kept only as the registration-versus-activation precedent. Those
  navigation entries do not themselves prove a declaration: `mcp` is the base reference,
  while the separately landed `avatar`, Context, Daemon, Email, File, Plugin,
  Notification, Shell, Soul, System, Task Card, Vision, and Web vertical slices
  are actual declared evidence; the former later-family target register is empty.
  The normative rules — including the selected form, the reserved official-name
  rule, and the governed-surface classification — stay in the Contract and in
  the kernel component's own Contract.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# src/lingtai/tools/

This package owns concrete built-in tools and the registry that composes them
onto an Agent. The kernel owns generic tool machinery; this layer owns public
capability names and lazy adapters.

## Components

- `CONTRACT.md` — the LingTai Tool Protocol (LTP): the future canonical
  model-facing tool call contract, the two-level family/action settings
  addressing and ownership rules, the explicit per-tool migration boundary, and
  `### Tool-to-MCP Plugin Contract`, the per-family migration target selecting
  the kernel-owned declared host-plugin form and package-owned manual, with the
  curated descriptor route retained only as an external-transport adapter.
- `BEHAVIORS.md` — the paired LABT file: LP001 guards the closed LTP envelope,
  LP002 guards the Tool-to-MCP Plugin Contract's status, its two-class governed
  surface, its single selected wrapper form, the document graph, and the
  current inventory: fourteen static official families (`mcp`, `avatar`,
  `context`, `daemon`, `email`, `file`, `plugin`, `notification`, `shell`,
  `soul`, `system`, `task_card`, `vision`, `web`), twenty grantable host names,
  and an empty former later-family target register.
- `channel_reply/` — mandatory static, normally unauthorized reply intrinsic.
  Active target-file composition for an ordinary Agent is macOS only;
  unsupported platforms keep a reason-bearing local closed Port and no route
  marker. An owner capsule enables only tuple-safe local queue/receipt transport.
  The Core Port accepts
  opaque grant, request id, timestamp, bounded plain text, and proof; owner
  adapters derive concrete destinations from grant state
  (`src/lingtai/tools/channel_reply/ANATOMY.md`).
- `registry.py` — intrinsic mapping, public `BUILTIN_TOOLS`, input aliases,
  defaults, normalization, setup, and check-caps metadata
  (`src/lingtai/tools/registry.py:39-344`).
- `web_search/` — public `web` composition owner for search, browse, settings,
  and manual, declared as the fourteenth official host-plugin slice against
  `workdir`/`web_runtime`/`provider_identity`
  (`src/lingtai/tools/web_search/ANATOMY.md`).
- `task_card/` — intrinsic channel-neutral declarative Task Card producer: one
  public `task_card` family, one agent-local artifact under `taskcard/`, and
  no transport ownership (`src/lingtai/tools/task_card/ANATOMY.md`).
- `file/` — sole owner of the public `file` capability: its static sixth
  official `DECLARATION`, composed schema/envelope dispatch, all five operation
  implementations in `_read.py`/`_write.py`/`_edit.py`/`_glob.py`/`_grep.py`,
  its exact source/snapshot-backed 13-row SHOW provider, and the one package
  manual body installed as `file-manual`
  (`src/lingtai/tools/file/ANATOMY.md`).
- `vision/` — public `vision` composition owner: one action-separated family
  with canonical `analyze`/`check`/`list`/`settings`/`manual` children over the
  existing direct provider routing and one bind-time settings snapshot,
  declared as the thirteenth official host-plugin
  slice against `workdir`/`active_provider`/`configuration`
  (`src/lingtai/tools/vision/ANATOMY.md`).
- `browser/` — internal static browse Core/Port used by `web`
  (`src/lingtai/tools/browser/ANATOMY.md`).
- `tool_family/` — generic, optional ToolFamily/ChildTool schema-composition
  and dispatch infrastructure implementing the LTP v2 envelope, and the
  reusable ManualTool builder; `web` is its first real consumer, `mcp` its
  second, `knowledge` its third, `file` its fourth, `vision` its fifth,
  `avatar` its sixth, `soul` its seventh, `shell` its eighth, `skills` its
  ninth, `notification` its tenth, `system` its eleventh, `daemon` its
  twelfth, `context` its thirteenth, and `plugin` its fourteenth
  (`src/lingtai/tools/tool_family/ANATOMY.md`).
  <!-- LTP_V2_CURRENT_FAMILIES: web,mcp,plugin,file,vision,avatar,soul,shell,notification,system,daemon,email,task_card,channel_reply,context,psyche -->
- `plugin/` — the `plugin` capability: the per-agent Agent Plugins
  (agent-plugins.org, v1.0.0) catalog and registration snapshot, structural twin
  of `mcp` with the same tool/service split; both independently opt into the
  reserved read-only `settings` child (`info`/`settings`/`manual`)
  (`src/lingtai/tools/plugin/ANATOMY.md`, `src/lingtai/tools/plugin/CONTRACT.md`).
  A plugin *declared* in `init.json` `manifest.plugins` is registered at boot —
  its validated `skills/` are named in the protected `plugins` prompt field and
  its `mcp.json` servers are registered in `mcp_registry.jsonl` with
  `source="plugin:<name>"` — while one merely *discovered* on an inherited
  skills path is listed and nothing more. Plugin skills deliberately do **not**
  enter the vanilla skills catalog: `src/lingtai/tools/skills/__init__.py`
  `_compose_paths` unions only explicitly configured vanilla paths, so the two
  namespaces stay parallel and closed and the protected Plugin field is the sole
  Plugin projection. The tool itself stays read-only: registration happens in
  `Agent._register_declared_plugins` before capability setup, unreachable from
  any action, which is what makes it safe in `CORE_DEFAULTS`. Registration is
  registry-level like `addons:[]`: registered, never running.
- `system/` — the official declared `system` family (a mandatory intrinsic
  whose model-facing surface mounts only through the kernel registrar):
  runtime, lifecycle, preset, and identity-naming actions behind one
  model-facing root (`src/lingtai/tools/system/ANATOMY.md`). Its static
  `DECLARATION` binds only `workdir`, `system_runtime`, and `identity`;
  `karma.sleep_use_case` owns the one self-sleep policy. It owns no public
  context-hygiene action — `summarize.py` stays here as the private engine
  `context` drives. It composes its schema from a module-level schema-only
  family, retains one port-bridge family per mount, and builds a direct
  dispatching one per compatibility `handle(agent, args)` call.
- `knowledge/` — private durable knowledge catalog, migrated to the LTP v2
  family envelope with the unchanged public actions `info`/`manual`
  (`src/lingtai/tools/knowledge/ANATOMY.md`).
- `soul/` — the official declared `soul` family (its injected module remains
  only for kernel hooks): seven action-separated children (`inquiry`, `flow`,
  `config`, `voice`, `dismiss`, read-only `settings`, and `manual`) behind one
  model-facing root (`src/lingtai/tools/soul/ANATOMY.md`).
- `bash/` — public `shell` composition owner for run/poll/cancel/settings/manual
  (`src/lingtai/tools/bash/ANATOMY.md`); the public model-facing schema is
  the ToolFamily-composed LTP v2 envelope (`bash/_tool_family.py`) and is the
  package's only schema/description pair, while `ShellManager` remains the
  unchanged execution engine behind an internal-only flat call shape.
- `notification/` — always-on declared official family owning the public
  `notification` action set (`check`, three atomic dismiss actions, hook-registry
  actions, `delay`, read-only `settings`, and `manual`;
  `src/lingtai/tools/notification/ANATOMY.md`).
  Its static `DECLARATION` is registered through the official host-plugin route,
  binds only `workdir` and `notification_state`, and preserves the ToolFamily
  LTP v2 surface without receiving the Agent or a direct intrinsic dispatcher.
  Its settings provider reads two effective scalars fresh through that same
  narrow port and owns no configuration object or writer.
  Its Host adapters retain presentation-only rules while Notification Core owns
  all producer guards, Store mutation, and delay/timer policy.
- `context/` — mandatory intrinsic owning the public `context` family: the
  agent's context lifecycle and hygiene — `molt`, `summarize`, `rebuild`, and
  `manual` — behind one root (`src/lingtai/tools/context/ANATOMY.md`). It
  replaces the former `psyche` root, which no longer exists at any
  model-visible or registry level: the two name actions moved to `system` and
  the public `system` summarize action moved in. `summarize` records only;
  `rebuild` is the sole active full reconstruction: canonical prompt composition,
  summary application, then provider replay. Refresh/molt invoke the same
  internal contract passively. Like `soul` it builds its family per call and
  alone consumes kernel `_tc_id`.
- `pad/` — mandatory `append | manual` family. `append` validates/persists pinned
  references without hot-loading; private `_pad_load` participates only in the
  Agent's canonical reconstruction path (`src/lingtai/tools/pad/ANATOMY.md`).
- `lingtai/` — mandatory manual-only identity signpost. Private
  `_lingtai_load` composes character during canonical reconstruction; generic
  durable mutation is owned by `file` (`src/lingtai/tools/lingtai/ANATOMY.md`).
- `email/` — the filesystem-based `email` intrinsic: mailbox I/O, composition,
  search, contacts, and delivery, migrated to the LTP v2 family envelope
  (`src/lingtai/tools/email/ANATOMY.md`).
- `_manual.py` — bounded installed-manual loader
  (`src/lingtai/tools/_manual.py:1-61`). `load_installed_manual(source,
  skill_name)` resolves the agent working directory from either shape a family
  can hold: the live `Agent` (private `_working_dir`) for an unmigrated family,
  or a `lingtai.kernel.tool_plugin.WorkdirPort` (`path`) for one recut onto the
  declared host-plugin contract, so one loader still serves every family. A
  source that is neither raises an `AttributeError` naming that fact.
- `__init__.py` — the package docstring that fixes the flat one-directory-per-tool
  layout and the `lingtai → lingtai.tools → lingtai.kernel` import DAG enforced by
  `tests/test_kernel_isolation.py` (`src/lingtai/tools/__init__.py:1-12`).
- `i18n/` — the tool locale catalog (`en`/`zh`/`wen`) holding the *human-facing
  manager prose* concrete tools resolve through `lingtai.kernel.i18n.t(lang, key)`
  (`soul.system_prompt`, `knowledge.preamble`, `email.unread_digest`, …). It owns
  no model-facing schema or description text: that lives in canonical English tool
  source plus the per-package `glossary-{en,zh,wen}.md` resources
  (`src/lingtai/tools/i18n/__init__.py:1-14`).

## Connections

`Agent` calls registry setup. The public `web` row imports
`lingtai.tools.web_search` lazily. That owner imports the browser Core and
provider factory only at composition or action boundaries, and imports
`tool_family` to compose its schema and (optionally) dispatch. The public
`vision` row imports `lingtai.tools.vision`, which imports `tool_family` the
same way and reaches `lingtai.services.vision` only on the selected direct
route. The pinned browser transport remains an outer adapter. `web_search` is
accepted only as a one-way configuration input alias and is never emitted as a
public name. `soul` is a mandatory intrinsic (`INTRINSICS`, not
`BUILTIN_TOOLS`) and imports `tool_family` statically; because it is a module
rather than a per-Agent manager object, it composes its schema from a
module-level schema-only `ToolFamily` and builds an agent-bound one per
`handle(agent, args)` call. The public `shell` row imports `lingtai.tools.bash`
lazily; `bash/__init__.py` imports `tool_family` (via `bash/_tool_family.py`)
to compose the public action-separated schema (re-exported as the package's
canonical `get_schema`/`get_description`) and to translate `action`/`input`
calls into the internal flat shape `ShellManager.handle` consumes. `bash` is
the one-way legacy input alias for `shell` (`registry.py`) and is never
emitted as a public name or a second schema.

The public `file` row imports `lingtai.tools.file` lazily; that owner registers
its static declaration, binds the five operation modules once per host grant,
and reaches the working tree only through `WorkdirPort` plus the
capability-native `FileIOPort` implemented by `AgentFileIOAdapter`. Its
SHOW-only provider receives the canonical service factory's immutable,
bounded backend snapshot through `ConfigurationPort`; the sidecar value is
fully redacted, and no settings file or writer exists. Unlike
`bash`/`web_search`, the
file migration kept no configuration aliases: `read`, `write`, `edit`, `glob`, and
`grep` are unknown capability names that fail loudly. Capability groups no
longer exist at all — `file` was `_GROUPS`' only entry, so the map,
`expand_groups`, and every consumer were deleted rather than left empty.

The public `task_card` row imports `lingtai.tools.task_card` lazily and owns
the artifact writer entirely within `lingtai.tools`. It writes only
`taskcard/status` and `taskcard/taskcard.md`; consumer-specific reading,
polling, and projection stay outside this package.

The form the paired Contract's `### Tool-to-MCP Plugin Contract` selects is the
kernel-owned declared host-plugin contract. `mcp` is the base reference; Avatar,
Context, Daemon, Email, File, Plugin, Notification, Shell, Soul, System,
Task Card, Vision, and Web are accepted vertical
evidence. They bind respectively their narrow earned ports: `avatar_parent`,
`context_runtime`, `daemon_runtime`, `email_runtime`, `file_io`,
`prompt_section`/`plugin_catalog`, `notification_state`,
`notifications`/`configuration`, `soul_runtime`,
`system_runtime`/`identity`,
`shutdown`/`task_card_lifecycle`/`task_card_notifications`,
`active_provider`/`configuration`, and `web_runtime`/`provider_identity` (all
also retain `workdir`). The former candidate target register is now empty; the
list is not a generic dispatch or admission mechanism.
These are the roles it separates, and where each one lives. `src/lingtai/kernel/tool_plugin/ANATOMY.md` is the
selected form's own component: the static `ToolPluginDeclaration`, the
least-privilege host ports, the reserved `OFFICIAL_TOOL_PLUGIN_NAMES` list, and
the fail-fast registrar. `src/lingtai/tools/mcp/__init__.py` `DECLARATION` is
the current base reference slice — `mcp` binds against `workdir` and
`prompt_section` instead of the whole `Agent`, with its public tool name,
actions, inputs, and result shapes unchanged. `src/lingtai/tools/avatar/__init__.py`
`DECLARATION` is the landed detached-peer slice — `avatar` binds against
`workdir` and `avatar_parent` instead of the whole `Agent`.
`src/lingtai/tools/context/__init__.py` is the current in-process lifecycle
slice: it binds `workdir` plus `context_runtime`, keeps the established live
engines behind that narrow port, and owns the canonical package manual installed
as `context-manual`. `src/lingtai/tools/email/__init__.py` is the fifth slice:
it owns `EmailRuntimeRequest`/`EmailRuntimePort`, creates or replaces the real
EmailManager before using `extra_ports_for` to grant a call-time
`AgentEmailRuntimeAdapter`, and remains a mandatory injected official family
with no capability/manifest row. `src/lingtai/tools/file/__init__.py` is the
sixth slice: its declaration composes the unchanged operations plus generic
SHOW against `workdir`/`file_io`/`configuration`, and `setup` grants a typed
`AgentFileIOAdapter` plus immutable `StaticConfigurationAdapter` only through
`extra_ports_for`; the adapters have no whole Agent, generic dispatch, or mount
operation. The Agent installer maps the package-owned File manual body to the
established `capabilities/file-manual` destination. `src/lingtai/tools/plugin/__init__.py`
is the seventh slice: its
`DECLARATION` binds `plugin` against `workdir`, its own protected prompt
section, and the read-only `plugin_catalog` projection built by
`AgentPluginCatalogAdapter`, so registration/discovery presentation is preserved
without a whole `Agent`, without generic dispatch, and without any registration,
prune, launch, config-write, or mount authority. Its validated skills are named
only in the protected Plugin prompt field and never enter the vanilla skills
catalog. `src/lingtai/tools/notification/__init__.py` is the eighth slice: its
`DECLARATION` binds only `workdir`/`notification_state` and delegates Core policy
through a callback-only adapter without exposing the Agent, Store,
configuration object, or writer, including its two-row settings projection.
`src/lingtai/tools/soul/__init__.py` is the tenth slice: its `DECLARATION`
preserves the public `inquiry | flow | config | voice | dismiss | manual`
family, adds the generic read-only `settings` child immediately before
`manual`, and binds the five operational children only to `workdir` plus the
explicit `SoulRuntimePort`; Soul stays an injected intrinsic for kernel
lifecycle hooks while its model-facing root mounts only through the registrar,
and its package manual is the sole operational body installed at the historical
`soul-manual` destination. `src/lingtai/tools/task_card/__init__.py` is the
twelfth slice: its `DECLARATION` preserves the public
`start | inspect | retry | stop | remove | manual` family and binds the one
retained `TaskCardManager` against `workdir`, `shutdown`,
`task_card_lifecycle`, and the closed operation-native
`task_card_notifications` port; one manager per current Agent survives and is
rebound across refresh, the persisted watch resumes only after a successful
bind, the family-local `TaskCardNotificationsAdapter` consumes only the five
native notification operations, and the producer stays channel-neutral (it
writes the `taskcard/` artifact and never calls Telegram/Feishu).
`src/lingtai/tools/vision/__init__.py` is the thirteenth slice: its
`DECLARATION` owns the public `analyze | check | list | settings | manual`
family and binds `VisionManager` against `workdir`, the live read-through
`active_provider`, and one `configuration` snapshot (`VisionConfiguration`,
carried as the same `StaticConfigurationAdapter` mapping Shell uses and granted
to `vision` alone through `extra_ports_for`); default routing uses only the
active provider, an explicitly allowed `preset` resolves only that preset's own
credential for the one requested call, `check`/`list`/`settings`/`manual` make
no image/provider request, and no provider/credential/MCP fallback is automatic.
`src/lingtai/tools/web_search/__init__.py` is the fourteenth slice: its
`DECLARATION` owns the public `search | browse | settings | manual` family and binds
`WebManager` against `workdir`, the Web-owned typed `web_runtime` composition
(`WebComposition` — browser transport plus immutable engine specs and default
provenance, composed by `setup` and granted to `web` alone through
`extra_ports_for`, with the bound manager published back through it exactly
once), and the narrow read-only `provider_identity` label
(`AgentProviderIdentityAdapter`, built in the standard table only for `web`);
`_bind` fails closed with `HostPortError` on a missing or mistyped
`web_runtime`, the explicit Anthropic/Gemini opt-in is gated by exact match on
that label, and no automatic provider/browser fallback exists beyond the
family's one documented OpenAI→DuckDuckGo runtime fallback.
Every landed
family retains its public name, actions, inputs, and result shapes. The
legacy whole-`Agent` `setup(agent)` boot path is no longer used by any
official family; that compatibility fact was never a generic
registrar/bridge dispatch model.

`registry.py` remains the current first-party composition point and stays a
hand-edited static table: it imports no `lingtai.mcp_servers` packaging and no
plugin discovery, so no family *in this package* is wrapped as an MCP plugin
package; the Contract's governed surface is wider than this directory and also
classifies the kernel-shipped MCP families under `src/lingtai/mcp_servers/`.
`src/lingtai/mcp_servers/_plugin.py` is the retained curated
*external-transport/launcher* route — one curated package binding its server,
bundled `SKILL.md`, launch record, and reserved `manual` child, explicitly not
a plugin runtime — reclassified by the Contract as one adapter form over a
declaration rather than the required form of every official tool.
`src/lingtai/mcp_servers/telegram/plugin.py` is its reference descriptor slice
and `src/lingtai/mcp_catalog.json` the shipped catalog record each descriptor
must agree with.
`src/lingtai/services/plugin_registry.py` is the external Agent Plugins v1.0.0
*declaration and boot-registration* path, with activation kept separate (the
rendering tool is `src/lingtai/tools/plugin/ANATOMY.md`). The Contract excludes
that standard from conversion; it is navigation to the precedent for
registration versus activation, not an alternative declaration form.
`src/lingtai/kernel/base_agent/tools.py` is the final common model-facing mount
point. `_add_tool` retains same-name replacement for nonreserved tools, but its static reserved-name guard rejects generic `add_tool` and external stdio/HTTP catalogs before publication.

The registrar reaches the boundary only through a registrar-issued one-use
transaction created after the declaration's successful bind. The Agent mount
seam verifies the persistent declaration anchor and exact canonical bound result
before publication; a caller-supplied foreign plugin or constructed transaction
is refused. A live official claim is read-only through its public view and can
be recorded only from that transaction after mount. This is trusted-in-process
Python provenance, not an absolute defense against deliberate private-state
mutation. The Contract's collision policy is scoped to official declared names,
which the registrar refuses before any bind or mount. Read the Contract for the
rules; this file only names the route.

## Composition

The parent [`src/lingtai/ANATOMY.md`](../ANATOMY.md) owns Agent composition.
The paired tools Contract owns LTP: the future canonical `action` / `input` /
`reasoning` / `summarize` public call shape, family/action settings ownership,
the migration boundary, and the Tool-to-MCP Plugin Contract — whose selected
form is owned by `src/lingtai/kernel/tool_plugin/CONTRACT.md`. Read them there;
this Anatomy does not restate those promises, and `BEHAVIORS.md` LP002 (with
the kernel component's TP001/TP002) owns their verification. The web Contract specializes
that promise for the first real implementation; its Anatomy and the internal
browser Anatomy provide progressive disclosure. Other tool packages retain their
existing public shapes until explicitly migrated.

## State

No mutable state lives at package root. `WebManager` owns per-Agent engine
specs, lazy provider cache, BrowserEngine refs/snapshots/cursors, and settings
observations. No process-global environment mutation or cross-Agent state is
owned here.

## Notes

Retained physical legacy directories (`bash/`, `web_search/`) and
provider-native wire strings remain for compatibility. They must not become
registry, schema, prompt, check-caps, manual, or catalog entries under those old
public names. The five pre-migration file packages are not among them: they were
deleted outright into `file/`, so there is no legacy directory, contract,
glossary, or alias left for that surface.
