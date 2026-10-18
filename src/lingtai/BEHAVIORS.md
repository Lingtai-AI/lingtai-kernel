---
name: init-reader-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - tests/test_agents_memory.py
  - src/lingtai/CONTRACT.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/init_reader.py
  - src/lingtai/init_schema.py
  - tests/test_init_ownership.py
  - src/lingtai/adapters/tool_plugin_host.py
  - tests/test_agent_capabilities.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  reader behavior or compatibility clause changes, update the guarding LABT
  here in the same change.
---
> Test-reference maintenance (2026-10-12): `retired-test:` identifiers below name removed pytest coverage, not runnable selectors or replacement conformance. Product promises are unchanged; manual acceptance remains necessary where automated coverage was surrendered.

# Init Reader Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/CONTRACT.md` (one real read path for boot and refresh, typed
outcomes, no auto-mutation of `<workdir>/init.json`). Pinned pytest commands
must run from the repo root with the project's Python.

## Behavior IR001 — the reader never modifies init.json and returns typed outcomes instead of fabricated success

- **id**: IR001
- **title**: the reader never modifies init.json and returns typed outcomes instead of fabricated success
- **guards**: `init-reader` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with an `init.json`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, **Automated step unavailable: pytest recipe retired (2026-10-12); no replacement conformance claimed** and capture the outcome.
2. In `<scratch>`, write an `init.json` containing a deprecated/ignored field and boot the reader path (boot CLI or refresh `Agent`); hash `<scratch>/init.json` before and after and compare.
3. Run `.venv/bin/python -m pytest -q tests/test_agents_memory.py -k legacy_character_seed`
   from `<repo>`; both old seed spellings must remain accepted and preserve
   their content once into Pad while leaving init bytes unchanged. Inspect the returned outcome: confirm it reports `FULLY_EFFECTIVE` / `READ_OK_WITH_IGNORED_FIELDS` / `READ_FAILED` with a typed shape decision and the ignored paths, and that the redacted `system/manifest.resolved.json` artifact was produced.

### Expected evidence
- [ ] Step 1: the init-reader/schema suites pass, pinning JSONC parsing,
      identical boot/refresh outcomes, ignored-path reporting, structured
      failure evidence, typed `manifest.disable` entries, and rejection of
      non-finite canonical numbers.
- [ ] Step 2: the bytes of `<scratch>/init.json` are unchanged after the read (no strip, no canonicalization, no rewrite).
- [ ] Step 3: the outcome status and shape decision are typed and truthful; failures carry stage, location when available, safe excerpt, behavior, and a next repair step.

### Pass / Fail
Pass when the suite passes, `init.json` is untouched, and the outcome is typed rather than fabricated. Fail on any auto-rewrite of `init.json`, on a claimed success for an ignored-field read, or on a missing redacted manifest artifact; record the evidence trail in the task report.


## Behavior IR002 — Model selection never owns tools or memory

- **id**: IR002
- **guards**: [init-reader § Authored ownership](CONTRACT.md#authored-ownership)
- **runner**: acceptance owner with candidate repository Python and authorized disposable fixture writes
- **prerequisites**: exact candidate installed; no credentials, network, providers, MCP subprocesses or existing Agent directories
- **steps**:
  1. Run `.venv/bin/python -m pytest -q tests/test_init_ownership.py` from the candidate repository root. Retain stdout/stderr and exit status. These tests call the real reader, activation, Project publication and Avatar seed helpers without provider/runtime startup.
  2. Require canonical and legacy switch cases to read A, persist only reference B, read B, and retain identical tool kwargs/skills/permissions. Inspect actual saved init: no expanded LLM copy. Require direct-caller widening separately from System's unauthorized-swap rejection.
  3. Require awareness/email/context owner fields to reach the historical internal ABI but not unsupported capability setup. Explicit MCP validation must create no registry or process; nonempty old servers must block canonical init without changing bytes. The plugin override proof must reject missing HTTP URL and ill-typed command/args through the real shared reader and actual reconstruction entry before old clients/tools/sealing change; metadata-only valid plugin activation must use the same shared merge with recording connectors only.
  4. Run the `inspect_init_subprocess` cases in the same thin suite. Require one JSON object, actual selected-source path/pointer for both source shapes and missing-active fallback, no secrets or writes, and typed READ_FAILED/exit 1 on invalid JSON. Require empty stderr for successful ignored legacy context-limit and missing-plugin warning fixtures, no sentinel across stdout plus stderr, and finally restoration of prior logging on success/exception. Run the Vision allowlist and recording-factory cases: list/borrow use only preset LLM plus current-init tool kwargs; unlisted references fail before resolution; borrowed inline/env credentials cannot cross endpoint/provider without an explicit own key.
  5. Run `tests/test_agent_capabilities.py::test_daemon_adapter_registered_tool_defaults`. Require actual Agent→production adapter→isolated Web/Vision setup, current-registration reads, defensive nested copies, explicit disables, and unchanged parent schemas/handlers. No standalone service or detached-launch claim follows from this proof. After the separately owned defaults-consumer patch is present, parent must additionally prove the existing explicit LLM-only preset child path and unchanged requested-tool filtering; until then that path is an integration gap.
  6. Parent acceptance additionally constructs a hermetic real Agent from canonical init with fake LLM transport and no explicit MCP, invokes actual setup, and builds the COMPLETE system prompt on this exact candidate; repeat after A→B and inspect full available tool schemas plus memory and System policy snapshots. Record unsupported host/installed-pair proof separately, not as passing.
- **expected_evidence**: all thin tests pass; raw legacy reads stay byte-identical; actual source/installed module provenance and complete prompt are retained; tools/MCP/authority/memory invariants hold with no provider/network effects.
- **pass_fail**: all evidence required. Authored tests are not execution proof. Any startup/prompt/installed-pair omission is unvalidated, not a passing receipt. No live config migration, automatic MCP launch, credential access or cleanup is authorized.
