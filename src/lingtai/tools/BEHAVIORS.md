---
name: lingtai-tool-protocol-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/workflow/BEHAVIORS.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/_catalog.py
  - src/lingtai/tools/_manual.py
  - src/lingtai/tools/registry.py
  - src/lingtai/tools/plugin/CONTRACT.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/tools/plugin/_mcp.py
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/tools/context/__init__.py
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/tools/plugin/__init__.py
  - src/lingtai/mcp_servers/_plugin.py
  - src/lingtai/mcp_servers/telegram/plugin.py
  - src/lingtai/mcp_catalog.json
  - src/lingtai/services/plugin_registry.py
  - src/lingtai/kernel/base_agent/tools.py
  - src/lingtai/kernel/tool_executor.py
  - src/lingtai/kernel/tool_result_summary.py
  - tests/test_tool_executor.py
  - scripts/check_docs_governance.py
  - tests/test_architecture_documents.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when an LTP
  envelope or settings rule changes, update the guarding LABT here in the same
  change. LP002 guards the `### Tool-to-MCP Plugin Contract` section: it
  verifies only what is true today (the section's scope-qualified status
  wording — current `mcp` base evidence and the separately landed `avatar`,
  `context`, `daemon`, `email`, `plugin`, `psyche`, `notification`, `shell`,
  `system`, `task_card`, `vision`, and `web` vertical evidence; the
  exact twenty-one-name grantable-host inventory; and the empty former later-family
  target register — its two-class governed surface, its single selected form as
  the kernel-owned declared host-plugin contract, the retained and reclassified
  curated transport route, the resolved official-name collision decision and
  its exact scope, the document graph, and the cited current evidence). Its
  steps inspect the
  full governed boundary — the registry surface *and* the kernel-shipped MCP
  packages — so a registry-only grep is never treated as proof about every
  family. Keep every command copy-paste executable (one line, or a fenced block
  with explicit `\` continuations). When a family actually recuts onto a plugin
  wrapper, or when authoring-time line numbers drift, replace the affected
  evidence with that family's own proof in the same change rather than leaving
  a stale pass.
---
> Test-reference maintenance (2026-10-12): `retired-test:` identifiers below name removed pytest coverage, not runnable selectors or replacement conformance. Product promises are unchanged; manual acceptance remains necessary where automated coverage was surrendered.

# LingTai Tool Protocol (LTP) Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/CONTRACT.md` (closed root envelope, strict per-action input,
manual action, two-level settings). Pinned pytest commands must run from the
repo root with the project's Python.

## Behavior LP001 — a migrated family's model-facing root is exactly action/input/reasoning/summarize and closed

- **id**: LP001
- **title**: a migrated family's model-facing root is exactly action/input/reasoning/summarize and closed
- **guards**: `lingtai-tool-protocol` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; one migrated family to probe (e.g. `shell`, `knowledge`, or `mcp`)
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, **Automated step unavailable: pytest recipe retired (2026-10-12); no replacement conformance claimed** and capture the outcome.
2. Fetch a migrated family's advertised schema (`get_schema()` on the family) and verify the root properties are exactly `action`, `input`, `reasoning`, and `summarize` with `additionalProperties: false`, and that `reasoning`/`summarize` never appear nested under `input`.
3. Confirm family dispatch validates and strips root `summarize` before child dispatch, while `ToolExecutor` retains the root control through result post-processing on both single and parallel call paths.
4. From `<repo>`, run `python -m pytest tests/test_tool_executor.py -q -k 'obsolete_secondary or secondary_named_handler'`. These hermetic executor probes pass the retired `secondary` key to strict Python functions on single-call and concurrent sibling-batch paths, compare it with another unexpected key, and check an explicitly accepting custom handler.

### Expected evidence
- [ ] Step 1: the wire-parity suite passes, pinning envelope correlation on both Chat Completions and Responses wires.
- [ ] Step 2: the schema's root is closed with exactly the four properties; no `parameters`/`payload` alias appears; action branches are closed per action.
- [ ] Step 3: `_reasoning` never appears in the model-facing schema or nested `input`; raw output is durably recorded before any visible summary replacement, and tool errors stay exact and unmodified.
- [ ] Step 4: unexpected `secondary` produces the ordinary dispatch `TypeError`, including null/false values, without invoking the strict function body or a nested tool. A valid sibling still succeeds; a custom handler explicitly accepting the name receives it unchanged as data. No deprecated-ignore event or normalization-log field remains.

### Pass / Fail
Pass when the suite passes and the closed-envelope observation holds for a real migrated family. Fail on an extra root property, on `reasoning`/`summarize` leaking into `input`, or on a summary replacing the recorded raw output; record the evidence trail in the task report.

## Behavior LP002 — the shared declared host-plugin contract matches the current thirteen-family and twenty-one-grant inventory

- **id**: LP002
- **title**: the shared declared host-plugin contract matches the current thirteen-family and twenty-one-grant inventory
- **guards**: `lingtai-tool-protocol` §
  [Tool-to-MCP Plugin Contract](CONTRACT.md#tool-to-mcp-plugin-contract)
- **runner**: any LingTai agent with `shell` access to a clean
  checkout of the `lingtai-kernel` repository
- **prerequisites**: a clean checkout of the repository; a working repository
  virtual environment at `.venv/` (`uv venv --python 3.11 && uv pip install -e
  . pytest`, per `AGENTS.md`). Run every command below from the repository root
  (`git rev-parse --show-toplevel`). Every command block is copy-paste
  executable as written. No network, no agent runtime, and no MCP server is
  needed: this task inspects documents and source text only.
- **estimate**: ≈ 15 minutes

### Steps

1. Read `src/lingtai/tools/CONTRACT.md`, section `### Tool-to-MCP Plugin
   Contract` (it sits under `## Contract rules`, between `### Non-goals` and
   `### Relationship to current runtime`). Confirm its opening **Status**
   paragraph names exactly the thirteen static official families: `avatar`, `context`, `daemon`, `email`, `plugin`, `psyche`,
   `notification`, `shell`, `system`, `task_card`, `vision`, `web`, and `workflow`, in that order;
   identifies Plugin's private MCP mode as the reference; names the exact twenty-one grantable host
   names (`workdir`, `prompt_section`, `mcp_prompt_section`, `avatar_parent`, `context_runtime`,
   `daemon_runtime`, `email_runtime`, `plugin_catalog`,
   `psyche_settings`, `notification_state`, `notifications`, `configuration`,
   `system_runtime`, `identity`, `shutdown`, `task_card_lifecycle`,
   `workflow_lifecycle`, `task_card_notifications`, `active_provider`, `web_runtime`, and
   `provider_identity`); and says the former later-family target register is
   empty. Confirm each claim is scoped to declaration clauses; other registry
   families remain future migration units, kernel-shipped curated MCP families
   are external-transport evidence rather than conformance, and families under
   `### Relationship to current runtime` are LTP envelope migrations rather than
   a compatible universal runtime.
2. Prove the old unqualified global negative is gone and that every surviving
   use of the phrase is scope-qualified:

   ```bash
   grep -n "No LingTai-owned family ships as an MCP plugin" src/lingtai/tools/CONTRACT.md
   grep -n "MCP plugin" src/lingtai/tools/CONTRACT.md
   ```

   The first command must print nothing and exit 1. Every surviving `MCP plugin`
   statement must be scope-qualified; none may be a bare global negative.
3. Confirm the section classifies the **whole** first-party boundary, not just
   the registry, and that exactly one form is selected:

   ```bash
   grep -n "Registry families\|Kernel-shipped MCP families" src/lingtai/tools/CONTRACT.md
   grep -n "Selected wrapper form" src/lingtai/tools/CONTRACT.md
   grep -n "remain a \*\*separate standard\*\*" src/lingtai/tools/CONTRACT.md
   grep -n "plugin-admission engine" src/lingtai/tools/CONTRACT.md
   ```

   The output must identify both governed-surface classes, the selected form,
   the excluded external standard, and the non-goal of a generic manifest
   compiler, admission engine, or wrapper runtime.

   Read the `**Selected wrapper form.**` paragraph and confirm the selected
   form is the kernel-owned declared host-plugin contract
   (`src/lingtai/kernel/tool_plugin/CONTRACT.md`): one static
   `ToolPluginDeclaration` per official family, a name reserved in the
   kernel-owned `OFFICIAL_TOOL_PLUGIN_NAMES` list, and least-privilege host
   ports instead of the whole `Agent`. Confirm the paragraph then states
   explicitly that the previously selected mandatory external-stdio package
   form was **wrong** and is corrected, and that the curated
   `CuratedMcpPlugin` + `src/lingtai/mcp_catalog.json` route is **retained
   unchanged and reclassified** as one external-transport/launcher adapter over
   a declaration — not the required form of every official tool. Confirm the
   `**Non-goals**` paragraph bans a generic wrapper *runtime* while stating
   that the one shared declared contract type is deliberately not banned.
4. Inspect the **registry class** of the governed surface — no family there is
   wrapped as an MCP plugin package, and the registry is still a hand-edited
   static table with no plugin packaging and no discovery:

   ```bash
   grep -n "lingtai\.mcp_servers\|CuratedMcpPlugin" src/lingtai/tools/registry.py
   ```

   Expect no output and shell exit status 1.

   Then inspect all twelve landed declarations — Avatar,
   Context, Daemon, Email, Plugin, Psyche, Notification, Shell, System,
   Task Card, Vision, and Web — none of which goes through packaging:

   ```text
Automated recipe retired by test pruning (2026-10-12).
No replacement conformance claimed for: test_plugin_tool, test_task_card_notifications, test_tool_family_vision_migration, test_web_composition_port, test_web_official_plugin.
```

   Expect twelve ordered pairs whose names are `avatar, context, daemon,
   email, plugin, psyche, notification, shell, system, task_card,
   vision, web`,
   with requires respectively `workdir/avatar_parent`,
   `workdir/context_runtime`, `workdir/daemon_runtime`,
   `workdir/email_runtime`,
   `workdir/prompt_section/mcp_prompt_section/plugin_catalog`, `workdir/psyche_settings`,
   `workdir/notification_state`,
   `workdir/notifications/configuration`,
   `workdir/system_runtime/identity`,
   `workdir/shutdown/task_card_lifecycle/task_card_notifications`,
   `workdir/active_provider/configuration`, and
   `workdir/web_runtime/provider_identity`; then expect
   exactly `('avatar', 'context', 'daemon', 'email', 'plugin', 'psyche',
   'notification', 'shell', 'system', 'task_card', 'vision', 'web')`,
   then `True`. All twelve declarations construct at import with no Agent,
   server, transport, or catalog record. No `file` declaration, `file_io`
   port, or `lingtai.tools.file` module exists (`import lingtai.tools.file`
   must raise `ModuleNotFoundError`);
   Plugin's focused suite passes, proving its read-only action boundary, its
   protected-field skill projection with the vanilla skills catalog left closed,
   and its detached per-read catalog projection; Task Card's typed notification
   suite passes, proving exact error/recovered/limit and reminder wire parity
   through the production five-operation port adapter and foreign
   source/channel/field refusal; Vision's focused suite passes, proving its
   four-action schema/dispatch, active-provider default routing,
   allowed-preset own-credential borrowing with no automatic fallback, and
   `check`/`list`/`manual` no-request boundaries.
5. Inspect the **kernel-shipped MCP class** of the governed surface. A
   registry-only grep proves nothing about these families, so check them
   directly:

   ```bash
   grep -rln "CuratedMcpPlugin" src/lingtai/mcp_servers/*/plugin.py | sort
   grep -c "lingtai-curated" src/lingtai/mcp_catalog.json
   grep -rn "CuratedMcpPlugin" src/lingtai/mcp_servers/daemon_common src/lingtai/mcp_servers/daemon_email
   ```

   Expect curated descriptor paths and catalog records to agree, then no output
   and exit status 1 for the built-in daemon families. That is the split the
   Contract's `**Governed surface.**` bullets state: curated families carry the
   retained external-transport descriptor/catalog form, while built-in daemon
   MCP families are in that governed external-transport class without a
   descriptor; neither fact is evidence that those packages conform to the
   selected declared host-plugin form.
6. Prove the packaging precedent the section selects exists and is not a
   runtime:

   ```bash
   grep -n "plugin runtime" src/lingtai/mcp_servers/_plugin.py
   grep -n "must not declare the reserved" src/lingtai/mcp_servers/_plugin.py
   ```

   Confirm the implementation says it is **not** a plugin runtime and raises
   `CuratedMcpPluginError` for a package that declares the reserved action.
7. Prove the live-collision clause is enforced at the final common model-facing
   boundary, while nonreserved replacement remains intact:

   ```text
Automated recipe retired by test pruning (2026-10-12).
No replacement conformance claimed for: test_mcp_capability, test_tool_plugin_declaration.
```

   Expect the selected tests to pass. Confirm `_add_tool` rebuilds
   `agent._tool_schemas` with `s.name != name`, so last registration still
   replaces an existing tool of the same name for nonreserved mounts.
   Confirm the contract's identifier clause states the decision (official names
   reserved first, not overwritable, refused before any bind or generic
   `add_tool`) and its exact scope: `_add_tool` remains the common boundary,
   external stdio/HTTP catalogs are rejected before client metadata/routes are
   published, post-preflight mount failures restore the connection snapshot and
   close/remove the new client, and the registrar issues a canonical one-use
   transaction whose mounted result alone can claim the official name. Private
   Python state remains trusted in-process rather than an absolute security
   boundary.

8. Prove the tridirectional graph edges exist exactly once each:

   ```bash
   grep -c "src/lingtai/tools/BEHAVIORS.md" BEHAVIORS.md src/lingtai/tools/CONTRACT.md src/lingtai/tools/ANATOMY.md
   grep -n "behavior-lp002" src/lingtai/tools/CONTRACT.md
   grep -n "^  \[Tool-to-MCP Plugin Contract\](CONTRACT\.md#tool-to-mcp-plugin-contract)$" src/lingtai/tools/BEHAVIORS.md
   ```

   Each of the three files must contain one graph edge; the contract section
   must carry one `Guarded by: [LP002](BEHAVIORS.md#behavior-lp002)` line, and
   this LABT must carry one anchored reverse clause link in its `guards`
   annotation, satisfying root `BEHAVIORS.md`'s relative-link-back rule. The
   anchor pattern deliberately excludes this step's own indented command text.

   Then confirm the newly selected form is a governed component in its own
   right, reciprocally linked:

   ```bash
   grep -n "^  - src/lingtai/kernel/tool_plugin/CONTRACT.md$" CONTRACT.md src/lingtai/tools/CONTRACT.md
   grep -n "^  - src/lingtai/tools/CONTRACT.md$" src/lingtai/kernel/tool_plugin/CONTRACT.md
   ```

   Expect one `related_files` entry in each direction: the root contract indexes
   the governed child, and the two child contracts list each other. Prose
   references to those paths elsewhere in the same files are deliberately
   excluded by the anchored pattern.
9. Run the narrow governed-pairing validation with the repository venv:

   ```bash
   .venv/bin/python -m pytest -q \
     tests/test_architecture_documents.py::test_root_architecture_documents_are_reciprocal_and_well_formed \
     tests/test_architecture_documents.py::test_governed_child_contracts_have_reciprocal_anatomy_pairs
   ```

   The selected root and governed-child pairing selectors must pass. Do not
   claim a result for `test_governed_cross_document_links_are_reciprocal` here:
   its current unrelated `src/lingtai/tools/bash/ANATOMY.md` baseline failure
   belongs to Bash ownership and must be reported separately.
10. Run the documentation governance checker:

    ```bash
    .venv/bin/python scripts/check_docs_governance.py --check
    ```

    At authoring time it has pre-existing violations owned elsewhere:
    `IMPLEMENTATION_REPORT.md` (no frontmatter),
    `src/lingtai/kernel/llm/ANATOMY.md` (duplicate `related_files` entries), and
    Feishu reference files without frontmatter. Any violation naming a path under
    `src/lingtai/tools/` — or the root `BEHAVIORS.md` — is a failure of this
    task, not a pre-existing one. The same kernel LLM duplicate also causes
    `tests/test_architecture_documents.py::test_every_tracked_file_climbs_the_anatomy_graph`
    to fail at authoring time; treat a `src/lingtai/tools/` path in that failure
    as a failure of this task.

### Expected evidence

- [ ] Step 1: the Status paragraph names the thirteen static official families
      in official order, the exact twenty-one grantable host names, and the empty
      former later-family target register; its remaining claims stay scoped to
      their current declaration, registry, transport, or LTP-envelope owner.
- [ ] Step 2: no unqualified "No LingTai-owned family ships as an MCP plugin"
      sentence survives, and every remaining statement is scope-qualified.
- [ ] Step 3: the governed surface names registry families and kernel-shipped
      MCP families, selects the kernel-owned declared host-plugin contract,
      retains and reclassifies the curated route, excludes external Agent
      Plugins and raw third-party schemas, and introduces no generic manifest
      compiler, admission engine, or wrapper runtime.
- [ ] Step 4: `registry.py` contains no MCP-server packaging reference; all
      twelve declarations import without an Agent; their names and narrow
      requirements match the exact `OFFICIAL_TOOL_PLUGIN_NAMES` tuple; and the
      selected focused suites pass.
- [ ] Step 5: the curated descriptor/catalog route agrees with the governed
      external-transport class, while built-in daemon MCP families carry no
      descriptor and are not claimed to conform to the selected form.
- [ ] Step 6: `src/lingtai/mcp_servers/_plugin.py` states it is not a plugin
      runtime and refuses a package that declares the reserved `manual` action.
- [ ] Step 7: the selected collision selectors pass, `_add_tool` retains
      nonreserved same-name replacement, and the contract states the narrower
      official-name policy and scope.
- [ ] Step 8: the Tools behavior, contract, and anatomy edges are reciprocal,
      and the kernel component contract is reciprocally linked with the Tools
      contract.
- [ ] Step 9: the selected root and governed-child pairing selectors pass. The
      broader cross-document-link selector's Bash-owned baseline is reported
      separately, not attributed to this triad.
- [ ] Step 10: the governance checker reports no violation under
      `src/lingtai/tools/` or in the root `BEHAVIORS.md`.

### Pass / Fail

Pass when every box above is observed. **Fail loudly** — do not soften the
report — if the contract section does not name exactly `avatar`,
`context`, `daemon`, `email`, `plugin`, `psyche`, `notification`, `shell`,
`system`, `task_card`, `vision`, and `web` as the static official
families, does not name the exact twenty-one grantable host names, or leaves the
former later-family target register nonempty; if any `file` declaration,
`file_io` grant, or `lingtai.tools.file` module reappears; if Plugin does not require exactly
`workdir`/`prompt_section`/`mcp_prompt_section`/`plugin_catalog`, gains registration/prune/launch/
config-write/mount authority, or claims its skills enter the vanilla skills
catalog; if it treats a
curated or built-in MCP package as already conforming to the selected declared
host-plugin form merely because an external-transport descriptor, catalog
record, or package exists; if it makes an unqualified global claim that no
LingTai-owned family is packaged as an MCP plugin; if it leaves the first-party form
unselected or admits more than one form, if it re-mandates an external stdio
package for every official tool, if it removes or weakens the curated
`CuratedMcpPlugin`/`mcp_catalog.json` route rather than reclassifying it, if it
claims a wrapper runtime, universal compiler, manifest compiler,
plugin-admission engine, discovery mechanism, or conformance suite exists, if
it claims collisions between a *third-party* MCP tool and a reserved official
name are not fail-closed on the normal public stdio/HTTP external-catalog paths
before publication, or if it mislabels a generic unrelated failure as a
reserved-name collision result instead of requiring focused collision evidence,
or if it claims external/third-party MCP schemas and legacy transport paths have
been converted; if step 4 finds plugin
packaging wired into `src/lingtai/tools/registry.py`; if step 5's
curated/daemon split disagrees with the Contract's governed-surface bullets; if
step 7 shows an official name conflict detected only after a bind or a mount;
if any graph edge in step 8 is missing or duplicated; or if steps 9-10 report a
failure naming a path under `src/lingtai/tools/` or the root `BEHAVIORS.md`.
Record the evidence trail, including the exact grep output and test summary
lines, in the task report. This task performs no writes: creating a plugin
package or editing a contract to make an assertion pass are forbidden side
effects; the pytest node IDs named in steps 4, 7, and 9 are read-only
verification and are the only code the task runs.

## Behavior LP003 — default-on root summaries preserve proactive exact-raw opt-out

- **id**: LP003
- **title**: migrated roots default to a-priori summary; explicit false preserves raw evidence
- **guards**: `lingtai-tool-protocol` § [Envelope](CONTRACT.md#envelope),
  `tool-family` § [Behavior](tool_family/CONTRACT.md#behavior), and
  `daemon-contract` § [A-priori summary composition](daemon/CONTRACT.md#a-priori-summary-composition)
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: candidate checkout at `<repo>` with its repository virtual
  environment and installed development dependencies; no live provider, MCP
  server, channel credentials, or running agent is required. Run commands from
  `<repo>`. Read this task and mandatory manuals with root `summarize=false`.
- **estimate**: ≈ 5 minutes

### Steps

1. From `<repo>`, run `.venv/bin/python -m pytest -q tests/test_tool_executor.py -k apriori_default`.
2. Inspect the successful probe assertions: exact migrated roots are `web`,
   `mcp`, `plugin`, `vision`, `avatar`, `shell`, `notification`, `system`,
   `daemon`, `email`, `task_card`, `context`, `psyche`, `telegram`, `feishu`,
   `imap`, `wechat`, `whatsapp`, and `cloud_mail`. No arbitrary external root
   becomes a presentation control merely by exposing a field named `summarize`.
3. Inspect `src/lingtai/tools/tool_family/__init__.py` and
   `src/lingtai/mcp_servers/cloud_mail/_family.py`: both root schemas advertise
   boolean `summarize` with `default: true`, an exact-raw opt-out, and a retention
   specification in `reasoning`. The shared schema recommends summaries for
   anticipated long, bulky output and explicit false for expected short results
   or required exact text. Retention targets name status, key fields, counts
   and evidence; the summarizer compresses faithfully without extra conclusions.

### Expected evidence

- [ ] Step 1 exits zero; omission invokes the summarizer for tiny receipts on
  actual single/concurrent consumers and daemon-local recovery locators.
- [ ] Explicit canonical false wins even alongside legacy `summary=true`;
  raw durable records precede summary generation and are not rewritten.
- [ ] Null/malformed values retain literal-True control semantics and the
  existing parser error contract. Parser/tool failures remain exact.
- [ ] All six curated real schemas and manual dispatchers agree with the
  executor default, including Cloud Mail's independently composed schema.
- [ ] Context's `summarize` action and agent-authored `input.items[].summary`
  remain domain data. Unrelated `Context.summarize` is not reinterpreted.
- [ ] Missing summary gateway remains fail-closed with preserved raw recovery,
  not a hidden size bypass or fail-open raw fallback.

### Pass / Fail

Pass only with the zero-exit focused probes and the advertised schema checks.
Fail if omission returns raw on a migrated root, canonical false is overridden,
raw history is replaced, required manuals lose their explicit opt-out guidance,
or an unrelated domain field changes meaning. These are hermetic presentation
probes, not proof of live-provider startup, pricing, or full prompt construction;
those acceptance gates belong to the integrating parent/reviewer.

Workflow is the independent opt-in official slice added after these earlier
family recipes; its executable ordering, strict envelope, stop-race and real
host startup evidence lives in `src/lingtai/tools/workflow/BEHAVIORS.md`
(WF001–WF003). The reserved inventory includes `workflow` and grants its
`workflow_lifecycle` current/retain-manager slot only to that declaration.
