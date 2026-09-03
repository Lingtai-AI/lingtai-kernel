---
name: lingtai-tool-protocol-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/_catalog.py
  - src/lingtai/tools/_manual.py
  - src/lingtai/tools/registry.py
  - src/lingtai/tools/plugin/CONTRACT.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/tools/mcp/__init__.py
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/tools/context/__init__.py
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/tools/file/__init__.py
  - src/lingtai/tools/plugin/__init__.py
  - tests/test_tool_plugin_declaration.py
  - tests/test_tool_family_avatar_migration.py
  - tests/test_context_declared_tool_plugin.py
  - tests/test_email_official_tool_plugin.py
  - tests/test_file_tool_plugin_package.py
  - tests/test_plugin_tool.py
  - src/lingtai/mcp_servers/_plugin.py
  - src/lingtai/mcp_servers/telegram/plugin.py
  - src/lingtai/mcp_catalog.json
  - src/lingtai/services/plugin_registry.py
  - src/lingtai/kernel/base_agent/tools.py
  - scripts/check_docs_governance.py
  - tests/test_architecture_documents.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when an LTP
  envelope or settings rule changes, update the guarding LABT here in the same
  change. LP002 guards the `### Tool-to-MCP Plugin Contract` section: it
  verifies only what is true today (the section's scope-qualified status
  wording — current `mcp` base evidence and the separately landed `avatar`,
  `context`, `daemon`, `email`, `file`, `plugin`, `notification`, `shell`,
  `soul`, `system`, `task_card`, `vision`, and `web` vertical evidence; the
  exact twenty-name grantable-host inventory; and the empty former later-family
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
# LingTai Tool Protocol (LTP) Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/CONTRACT.md` (closed root envelope, strict per-action input,
manual action, two-level settings). Pinned pytest commands must run from the
repo root with the project's Python.

## Behavior LP001 — a migrated family's model-facing root is exactly action/input/reasoning/summarize and closed

- **id**: LP001
- **title**: a migrated family's model-facing root is exactly action/input/reasoning/summarize and closed
- **guards**: `lingtai-tool-protocol` § Behavior
- **runner**: any LingTai agent with `shell` and `file` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; one migrated family to probe (e.g. `file`, `knowledge`, or `mcp`)
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_tool_family_wire_parity.py -q` and capture the outcome.
2. Fetch a migrated family's advertised schema (`get_schema()` on the family) and verify the root properties are exactly `action`, `input`, `reasoning`, and `summarize` with `additionalProperties: false`, and that `reasoning`/`summarize` never appear nested under `input`.
3. Confirm `ToolExecutor` strips root `summarize` before handler dispatch and that the root boolean survives through result post-processing on both single and parallel call paths.

### Expected evidence
- [ ] Step 1: the wire-parity suite passes, pinning envelope correlation on both Chat Completions and Responses wires.
- [ ] Step 2: the schema's root is closed with exactly the four properties; no `parameters`/`payload` alias appears; action branches are closed per action.
- [ ] Step 3: `_reasoning` never appears in the model-facing schema or nested `input`; raw output is durably recorded before any visible summary replacement, and tool errors stay exact and unmodified.

### Pass / Fail
Pass when the suite passes and the closed-envelope observation holds for a real migrated family. Fail on an extra root property, on `reasoning`/`summarize` leaking into `input`, or on a summary replacing the recorded raw output; record the evidence trail in the task report.

## Behavior LP002 — the shared declared host-plugin contract matches the current fifteen-family and twenty-one-grant inventory

- **id**: LP002
- **title**: the shared declared host-plugin contract matches the current fifteen-family and twenty-one-grant inventory
- **guards**: `lingtai-tool-protocol` §
  [Tool-to-MCP Plugin Contract](CONTRACT.md#tool-to-mcp-plugin-contract)
- **runner**: any LingTai agent with `shell` and `file` access to a clean
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
   paragraph names exactly the fifteen static official families: `mcp`,
   `avatar`, `context`, `daemon`, `email`, `file`, `plugin`, `psyche`,
   `notification`, `shell`, `soul`, `system`, `task_card`, `vision`, and `web`, in that order;
   identifies `mcp` as the base reference; names the exact twenty-one grantable host
   names (`workdir`, `prompt_section`, `avatar_parent`, `context_runtime`,
   `daemon_runtime`, `email_runtime`, `file_io`, `plugin_catalog`,
   `psyche_settings`, `notification_state`, `notifications`, `configuration`, `soul_runtime`,
   `system_runtime`, `identity`, `shutdown`, `task_card_lifecycle`,
   `task_card_notifications`, `active_provider`, `web_runtime`, and
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

   Then prove all fifteen landed declarations — the current base `mcp`, Avatar,
   Context, Daemon, Email, File, Plugin, Notification, Shell, Soul, System,
   Task Card, Vision, and Web — none of which goes through packaging:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from lingtai.tools.mcp import DECLARATION as mcp; from lingtai.tools.avatar import DECLARATION as avatar; from lingtai.tools.context import DECLARATION as context; from lingtai.tools.daemon import DECLARATION as daemon; from lingtai.tools.email import DECLARATION as email; from lingtai.tools.file import DECLARATION as file; from lingtai.tools.plugin import DECLARATION as plugin; from lingtai.tools.notification import DECLARATION as notification; from lingtai.tools.bash._tool_family import DECLARATION as shell; from lingtai.tools.soul import DECLARATION as soul; from lingtai.tools.system import DECLARATION as system; from lingtai.tools.task_card import DECLARATION as task_card; from lingtai.tools.vision import DECLARATION as vision; from lingtai.tools.web_search import DECLARATION as web; from lingtai.kernel.tool_plugin import OFFICIAL_TOOL_PLUGIN_NAMES; declarations=(mcp, avatar, context, daemon, email, file, plugin, notification, shell, soul, system, task_card, vision, web); print(tuple((d.name, d.requires) for d in declarations)); print(OFFICIAL_TOOL_PLUGIN_NAMES); print(tuple(d.name for d in declarations) == OFFICIAL_TOOL_PLUGIN_NAMES)"
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_file_tool_plugin_package.py tests/test_file_tool_family.py \
     tests/test_plugin_tool.py tests/test_task_card_notifications.py \
     tests/test_tool_family_vision_migration.py \
     tests/test_web_official_plugin.py tests/test_web_composition_port.py
   ```

   Expect fifteen ordered pairs whose names are `mcp, avatar, context, daemon,
   email, file, plugin, psyche, notification, shell, soul, system, task_card,
   vision, web`,
   with requires respectively `workdir/prompt_section`, `workdir/avatar_parent`,
   `workdir/context_runtime`, `workdir/daemon_runtime`,
   `workdir/email_runtime`, `workdir/file_io`,
   `workdir/prompt_section/plugin_catalog`, `workdir/psyche_settings`,
   `workdir/notification_state`,
   `workdir/notifications/configuration`, `workdir/soul_runtime`,
   `workdir/system_runtime/identity`,
   `workdir/shutdown/task_card_lifecycle/task_card_notifications`,
   `workdir/active_provider/configuration`, and
   `workdir/web_runtime/provider_identity`; then expect
   exactly `('mcp', 'avatar', 'context', 'daemon', 'email', 'file', 'plugin',
   'notification', 'shell', 'soul', 'system', 'task_card', 'vision', 'web')`,
   then `True`. All fifteen declarations construct at import with no Agent,
   server, transport, or catalog record. The two File focused suites pass, proving its narrow
   adapter/grant, one mount, unchanged operations, sole package manual body at
   `file-manual`, and absent `capabilities/file`;
   `tests/test_tools_package_data.py::test_archives_ship_file_package_manual`
   proves the package-data source route;
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

   ```bash
   grep -n "Remove any existing schema with same name" src/lingtai/kernel/base_agent/tools.py
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_mcp_capability.py::test_direct_generic_mount_cannot_replace_official_mcp_and_nonreserved_replaces \
     tests/test_mcp_capability.py::test_external_mcp_cannot_replace_official_mcp_or_leave_routes \
     tests/test_tool_plugin_declaration.py::test_a_second_different_declaration_cannot_take_a_claimed_name \
     tests/test_tool_plugin_declaration.py::test_public_mount_bypass_cannot_publish_a_foreign_bound_plugin \
     tests/test_tool_plugin_declaration.py::test_a_constructed_transaction_cannot_replace_the_canonical_mcp_binding \
     tests/test_tool_plugin_declaration.py::test_clearing_the_backing_claim_cannot_admit_a_foreign_declaration \
     tests/test_tool_plugin_declaration.py::test_public_claim_view_cannot_clear_the_live_claim_or_admit_a_foreign_declaration \
     tests/test_mcp_capability.py::test_sealed_agent_post_preflight_failure_closes_client_and_rolls_back
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

- [ ] Step 1: the Status paragraph names the fifteen static official families
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
      fifteen declarations import without an Agent; their names and narrow
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
report — if the contract section does not name exactly `mcp`, `avatar`,
`context`, `daemon`, `email`, `file`, `plugin`, `notification`, `shell`,
`soul`, `system`, `task_card`, `vision`, and `web` as the static official
families, does not name the exact twenty-one grantable host names, or leaves the
former later-family target register nonempty; if File does not require exactly
`workdir`/`file_io`, exposes Agent/generic dispatch/mount authority, or installs
a second/non-`file-manual` body; if Plugin does not require exactly
`workdir`/`prompt_section`/`plugin_catalog`, gains registration/prune/launch/
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
