---
name: project-membership-behaviors
labt_version: 3
related_files:
  - src/lingtai/kernel/project/bundle.py
  - src/lingtai/adapters/project_bundle.py
  - src/lingtai/adapters/project_bundle_exchange.py
  - src/lingtai/cli_project_bundle.py
  - tests/test_project_bundle.py
  - src/lingtai/kernel/project/recipe.py
  - src/lingtai/adapters/project_recipe.py
  - src/lingtai/adapters/project_recipe_exchange.py
  - src/lingtai/adapters/project_inspection_exchange.py
  - tests/test_project_inspection_exchange.py
  - src/lingtai/cli_project_recipe.py
  - tests/test_project_recipe.py
  - tests/test_project_interactive_diagnostics.py
  - src/lingtai/kernel/project/configuration.py
  - src/lingtai/adapters/project_configuration.py
  - src/lingtai/cli_project_configuration.py
  - tests/test_project_configuration.py
  - tests/fixtures/project_configuration.json
  - src/lingtai/kernel/project/CONTRACT.md
  - src/lingtai/kernel/project/ANATOMY.md
  - src/lingtai/kernel/project/inspection.py
  - src/lingtai/adapters/project_inspection.py
  - src/lingtai/cli_project.py
  - tests/test_project_inspection.py
  - src/lingtai/kernel/project/interactive_creation.py
  - src/lingtai/adapters/project_interactive_workspace.py
  - src/lingtai/adapters/project_creation_recipe.py
  - src/lingtai/adapters/project_creation_exchange.py
  - tests/test_project_interactive_creation.py
maintenance: |
  Keep PRJ001 reciprocal with Project inspection promises and implementing code.
  Execution needs separate acceptance authorization; authored scenarios are not
  a passing runtime receipt.
---
# Project membership behavior

## Behavior PRJ001 — Inspection preserves malformed membership and separate identities

- **id**: PRJ001
- **guards**: `project-creation` § [Inspection](CONTRACT.md#inspection)
- **runner**: coding/LingTai agent with an authorized local shell and candidate `lingtai-agent` executable
- **prerequisites**: candidate kernel installed in a test environment; permission to create temporary fixture files only; no provider/network or existing Project access required
- **steps**:
  1. Create a fresh temporary directory `ROOT` with `mktemp -d`. Run `printf '%s\n' "{\"project_dir\":\"$ROOT\"}" | lingtai-agent project inspect`. Verify `.lingtai` is still absent.
  2. Run `mkdir -p "$ROOT/.lingtai/worker-dir" "$ROOT/.lingtai/orch" "$ROOT/.lingtai/broken" "$ROOT/.lingtai/human" "$ROOT/.lingtai/no-manifest"`.
  3. Run `printf '%s' '{"agent_name":"Display","address":"address","agent_id":"stable","admin":{},"secret":"DO-NOT-EMIT"}' > "$ROOT/.lingtai/worker-dir/.agent.json"`; run `printf '%s' '{"admin":{"create":true}}' > "$ROOT/.lingtai/orch/.agent.json"`; run `printf '%s' '[' > "$ROOT/.lingtai/broken/.agent.json"`; run `printf '%s' '{"admin":null}' > "$ROOT/.lingtai/human/.agent.json"`.
  4. Repeat the inspect stdin command. Read its one stdout JSON object and terminal status. Do not start any agent or issue mutations.
- **expected_evidence**: step 1 exits 0 with `status=inspected`, `initialized=false`, `members=[]`; step 4 exits 0 with `initialized=true` and exactly four members sorted as broken, human, orch, worker-dir. Broken is malformed/unknown, human is human, orch is orchestrator, worker-dir is worker with Display/address/stable identity. No-manifest is absent from members. `DO-NOT-EMIT` and the raw admin object are absent from output. All fixture bytes remain unchanged.
- **pass_fail**: pass only with every expected field and no filesystem mutation by inspection; no test/provider/Agent startup substitutes for these observations. A transport failure or unreadable result is unresolved, never empty-project success. Retain fixture evidence; cleanup requires separate authorization.

## Behavior PRJ002 — Hidden creation gates prerequisite persistence and exclusive publication

- **id**: PRJ002
- **guards**: `project-creation` § [Interactive creation transaction](CONTRACT.md#interactive-creation-transaction)
- **runner**: acceptance owner with authorized temporary fixture writes and installed candidate kernel, no provider/network
- **prerequisites**: exact candidate wheel installed; permission to create fixture root and selected preset; no real credentials, global config or runtime launch; two live pipes to `lingtai-agent project create-interactive`
- **steps**:
  1. Create a temporary approved existing root with no `.lingtai` or global Go recipe tree. Prepare the manual's explicit request with a pending non-secret selected manifest, a nonexistent exact preset ref, distinct display/directory, caller covenant, explicit non-secret `options.env_file` path when required by `api_key_env`, language zh and adaptive Recipe. Send one newline-terminated request.
  2. Before ready, observe only hidden owned staging, never final `.lingtai`. On ready verify actual staged human null-admin, one true-privileged orchestrator, init/owner documents, localized greet/Pad and Recipe snapshot. Verify pending preset is still absent. Do not persist prerequisites early.
  3. Write the exact selected preset fixture with required description summary, then send accepted attempt/ref-bound ack. Read terminal created and exit 0. Validate final current init and Psyche seams. Verify no runtime/credential/config/UI registration side effects and no staging remains.
  4. Repeat on separate fixture roots with blocked/unknown process evidence, invalid draft (including required env-file omission, which must return `init_preflight_failed` before ready or any writes), existing final file/symlink/empty directory, Recipe/build/staged-validation failure, rejected/malformed/EOF ack, missing or changed persisted preset, concurrent final creation and publication failure. The reduced `tests/test_project_interactive_creation.py` retains prerequisite, publication-race and owner-reader cases, not the exhaustive injected phase/draft matrices. Recipe/build/staged-validation phase substitutions remain manual acceptance work, not production flags.
  5. Repeat with a valid local Recipe and local root/zh layers plus a library declaration. Verify local bytes are unchanged, language then root fallback, additive skills paths, current Pad/covenant ownership and behavioral snapshot. Repeat no-Recipe with a postpublication marker-removal failure: created remains committed with a warning.
- **expected_evidence**: exact request/ready/ack/terminal envelopes and child exit status; actual reader outcomes; no final publication or prerequisite calls before staged validation; owned staging alone removed on prepublication failure; root Recipe/external prerequisite bytes may survive. Foreign or nonce-mismatched staging survives with cleanup-unresolved error, never swept. Concurrent final is never overwritten, including empty directory. No raw credential/input/exception text in errors/results.
- **pass_fail**: all invariants and installed-resource cases must pass. Authored tests alone are not execution evidence. A cancelled/killed child or lost/malformed output is unknown transport, not a failed commit: no automatic retry or deletion. Known committed projects are never rolled back for marker/UI/launch warnings. Retain fixtures unless cleanup is separately authorized. Full Project migration remains pending.

## Behavior PRJ003 — Existing configuration preserves data and honest partial outcomes

- **id**: PRJ003
- **guards**: `project-creation` § [Existing Project configuration](CONTRACT.md#existing-project-configuration-candidate-slice)
- **runner**: acceptance owner with installed exact candidate kernel and authorized temporary fixture writes
- **prerequisites**: public persisted preset and fixture root, no real auth/provider/network/runtime; `tests/fixtures/project_configuration.json` and `tests/test_project_configuration.py` available
- **steps**:
  1. On a fresh temporary root send initialize; verify null-admin human/mailboxes/contacts/helpers. Remove no assets. On a separate fixture with a pre-existing malformed human manifest send initialize; verify unchanged bytes and no helper repair.
  2. Seed selected init/blueprint using the shared preservation matrix. Send configure with real preset ref, explicit env/venv paths and preserve_active. Repeat explicit authorization revocation and Keep current with empty ref. Check exact large integers, IDs/history/nickname/pad/env/venv/custom MCP/unknown fields and current owner readers. Supply legacy covenant fallback metadata on fresh/existing/explicit fixtures: verify explicit path then existing nonempty path then language default, and no Psyche rewrite from fallback alone. Edit a prompt through optional prompts and verify actual Psyche serialization; omission preserves existing bytes.
  3. Seed worker, human, absent/malformed init and legacy no-preset fixtures. Run both propagation commands. Check active retention/revocation, stripped worker admin/addons, preserved worker settings and exact partial/skipped counts. Policy-only fixtures also include parsed preset blocks lacking Pad/env/blueprint or loadable references and malformed unrelated Psyche: they still update, leave other owner bytes untouched and claim no reader effectiveness. Write failure must not block later members; config propagation retains its actual reader checks. No runtime or fleet calls occur.
  4. On separate imported fixtures test all-absent, all-present and mixed membership. All-absent configures source then workers using blueprint names/admin {}; dot/human excluded. All-present bytes unchanged; mixed rejected before writes. Substitute a deterministic second-worker write failure using the authored test seam; verify fail-fast preceding-write count and retained first worker/source, with no automatic retry or cleanup.
  5. Exercise missing required env_file, duplicate/credential-shaped/malformed/oversize request, symlink/nonregular document, post-init manifest/Psyche/reader failures and unknown transport through the real Go consumer. Verify no raw input/exception/secret text appears in outputs and no existing creation/inspection ABI changes.
- **expected_evidence**: accepted JSON envelopes and exit statuses, exact before/after fixture semantics, current reader statuses (ignored fields not misreported FULLY_EFFECTIVE), partial receipts and retained unrelated owner bytes; Go callsite ownership and real startup evidence on exact candidates.
- **pass_fail**: every invariant plus actual installed owner/startup and consumer evidence is required. Authored tests are not a passing receipt; cancellation/lost response means unknown, not rollback. Retain fixtures; no deletion, repair, registration or launch based on unknown/partial results. Recipe reconciliation/lifecycle remain pending.

## Behavior PRJ004 — Existing local Recipe retains partial snapshot meaning

- **id**: PRJ004
- **guards**: `project-creation` § [Existing local Recipe observation/apply](CONTRACT.md#existing-local-recipe-observationapply-candidate-slice)
- **runner**: acceptance owner with installed exact candidate kernel and authorized temporary fixture writes
- **prerequisites**: `tests/test_project_recipe.py`, no provider/network/runtime/global bootstrap, only disposable fixture Projects; binding strict existing-behavior preservation decision
- **steps**:
  1. Follow the manual's recipe-observe stdin procedure for absent, non-directory, missing snapshot, equal and changed file-set/byte fixtures. Change only mode/mtime/empty directories; equal must remain equal. Inject stat/tree-read failures via source test seams and inspect distinct state plus old needs_apply boolean.
  2. On a fixture with valid local recipe.json and root/localized greet, human/dot/real member directories and existing owner-valid init, send recipe-apply with public resolved rendering strings. Verify selected locale then root; unreadable selected locale skips greet, does not retry root. Unknown/retired tokens remain literal; substitution order matches Go callback. Null rendering preserves bytes for headless identity.
  3. Verify no library/no skills/missing init are no-op, library path additive even if sibling absent, exact unknown integers/preset active/allowed and opaque Psyche owner preserved, canonical aliases rewritten only through current owner seam, current reader status honest. Malformed init remains unchanged and fails per member.
  4. Substitute deterministic prompt/init/reader failures and repeated later failures with source seams. Verify actual prompt/init/phase receipts, truthful applied count and separately disclosed legacy_counted/legacy_applied quirk. Snapshot is ALWAYS attempted after traversal, even partial; copied snapshot may make observation equal despite failed members. Test remove failure with continued copy and copy failure with retained partial tree.
  5. Exercise malformed/duplicate/extra/nonfinite/credential-shaped stdin. Separately verify declared library/lang parent/non-segment/absolute-looking strings use old filepath.Join cleaning, prompt/snapshot symlink targets retain old behavior, init link is read then replaced by fixed-temp rename, and nonregular targets fail only with natural old I/O errors. No fresh safe-path gate or fallback/replacement of local Recipe is introduced. Reader-only failure stays receipt evidence, not a new caller error; ignored removal failure stays ignored if copy succeeds. Lost/extra child output remains unknown transport with no retry/cleanup/launch decision.
- **expected_evidence**: exact request/result/exit envelopes, before/after fixture bytes, real current owner-reader statuses, independent snapshot attempted/removal/copied receipts, paired consumer validation and source ownership.
- **pass_fail**: every assertion and existing caller error/count/snapshot compatibility clause must pass. Authored source alone proves neither tests nor fullProject conformance. Snapshot equality never proves all members applied. Keep fixtures unless separately authorized to clean them; bundle materialization/import/lifecycle remain explicit remainders.


### PRJ004 synchronous-member extension acceptance

Additional prerequisites: exact candidate exposes no-flag `project recipe-apply`
line form; paired Go consumer retains original arbitrary callback and native
byte identity. Only fresh disposable fixtures are authorized; no provider or
existing Project writes. Existing PRJ004 steps above remain required.

1. Create separate temporary bundle `BUNDLE` and destination `DESTINATION`.
   Put valid `.recipe/recipe.json` with `name:fixture,library_name:library` and
   raw nonempty greet under BUNDLE; create a,b,c with existing skills init under
   DESTINATION. Add dot/human/file/symlink-dir exclusions and no-manifest member.
   Send `{"project_dir":"BUNDLE","lingtai_dir":"DESTINATION","language":"","rendering_mode":"callback"}` as a single JSON line, replacing fixture path
   placeholders. Verify each frame's sequence/native
   base64 identity/template decoded length, execute original Go function once.
2. Return empty, NUL/invalid-UTF8 and >1 MiB binary bytes respectively. In each
   next callback verify previous prompt and skills writes already visible and
   current prompt still old. On first call mutate future init/tree/selected greet;
   ensure original enumeration/template remain unchanged and future init is read
   when reached. Actual empty result truncates, no later new member is enumerated.
3. Repeat identity, original empty/unreadable/missing greet, no eligible members,
   metadata and enumeration failure: no rendering frames when absent/invalid;
   conditional second metadata failure after greet is nonfatal empty library.
   Verify first-error/count quirks and reader-only/removal-only non-gates unchanged.
4. On a fresh fixture accept member one, then close stdin on member two. Repeat
   malformed/duplicate/mismatched sequence/base64/byte count. Verify completed
   receipts/prior writes retained, pending prompt/skills untouched and snapshot
   NOT attempted. Panic/Goexit preserves original caller semantics with close+reap;
   no manufactured return error/success, retry, finally-snapshot or cleanup occurs.
5. Repeat legacy static EOF/multiline capped requests and actual installed new-form
   growing stream/terminal; test native malformed filename bytes with distinct
   base64 identities. Keep exact child statuses and receipt evidence. Native
   Windows and Go error formatting/decoder differences must be resolved before
   claiming all TUI compatibility; source tests do not grant acceptance.

Pass only with the seven design-case invariants plus original PRJ004 promises,
real paired caller behavior and no forbidden effects. Lost/abnormal output means
unknown physical effects, never zero-write/rollback/safe retry. Root LABT version
2 versus this pre-existing version 3 is an owner mismatch for parent review, not
silently changed by the bounded rendering seam.


## Behavior PRJ005 — Startup readiness is stat-only and preserves refusal order

- **id**: PRJ005
- **guards**: `project-creation` § [Startup readiness](CONTRACT.md#startup-readiness)
- **runner**: acceptance LingTai agent with installed candidate kernel and authorized local fixture shell
- **prerequisites**: fresh disposable fixture root only; no provider/network/global/runtime access; execute fixture writes only with explicit authorization
- **steps**:
  1. Create `ROOT=$(mktemp -d)`. Run `printf '%s\n' "{\"project_dir\":\"$ROOT\"}" | lingtai-agent project inspect`; require reason not_initialized, initialized false, empty arrays, no .lingtai creation.
  2. Run `mkdir -p "$ROOT/.lingtai/a" "$ROOT/.lingtai/b" "$ROOT/.lingtai/.helper" "$ROOT/.lingtai/human" "$ROOT/.lingtai/broken"`. Write `printf '%s' '{"admin":{"x":true}}' > "$ROOT/.lingtai/a/.agent.json"`; run `printf '%s' '{"admin":{"x":true}}' > "$ROOT/.lingtai/b/.agent.json"` and `printf '%s' '{"admin":{"x":true}}' > "$ROOT/.lingtai/.helper/.agent.json"`. Write `printf '%s' '{"admin":null}' > "$ROOT/.lingtai/human/.agent.json"`; write `printf '%s' '[' > "$ROOT/.lingtai/broken/.agent.json"`.
  3. Repeat inspect. Require multiple_orchestrators, needs_rehydration true, agents/orchestrators [a,b], two absent init items; all five members still reported. Run `mkdir "$ROOT/.lingtai/a/init.json"`, repeat: mixed_init must beat multiple_orchestrators, with a present and b absent. Run `mkdir "$ROOT/.lingtai/b/init.json"`, repeat: multiple_orchestrators and false rehydration (directories count as present).
  4. Write `printf '%s' '{"admin":[]}' > "$ROOT/.lingtai/b/.agent.json"`, repeat: ready, orchestrators [a], false rehydration. Write `printf '%s' '{"admin":{"x":1}}' > "$ROOT/.lingtai/a/.agent.json"`, repeat: no_orchestrator. Write `printf '%s' '{"admin":null}' > "$ROOT/.lingtai/a/.agent.json"` and same to b, repeat: no_agents with empty startup arrays; .helper remains only a general member.
  5. Where POSIX symlink creation is permitted, restore a's true-admin manifest, rename a/init.json to a/init.saved, then `ln -s init.json "$ROOT/.lingtai/a/init.json"`. Repeat inspect: cannot_stat_init with one stat_failed item and minimal path_b64/native_errno/native_winerror, never mixed/no-orchestrator; no exception text or manifest content. Record unsupported host coverage honestly, never claim it passed.
- **expected_evidence**: every inspection exits 0 with one closed result and empty stderr; domain refusal is evidence, not mutation. Fixture changes occur only in explicit executor setup, never inspection. Installed CLI plus paired Go startup display must consume this exact ABI before slice acceptance.
- **pass_fail**: all listed outcomes and unchanged fixture bytes across each inspect required; failures, unreadable response or unsupported host are unresolved. No cleanup, launch, fallback or repair is authorized. Source tests complement this scenario but authored tests are not execution proof.


## Behavior PRJ006 — Native init observation is single-call and correlated

- **id**: PRJ006
- **guards**: `project-creation` § [Synchronous native init observation exchange](CONTRACT.md#synchronous-native-init-observation-exchange)
- **runner**: acceptance owner with installed candidate CLI and paired native Go driver
- **prerequisites**: disposable read-only inspection fixture Project with a,b,c,d valid worker/orchestrator manifests and distinct display names; permission only for fixture setup; no provider/runtime/global effects
- **steps**:
  1. On fresh fixture set a init present, b absent, c to a deterministic native nonmissing stat failure and d absent. Send one line `{"project_dir":"ROOT","init_observation_mode":"callback"}` with ROOT replaced by the fixture path, through live pipes to `lingtai-agent project inspect`. Validate each exact observe_init_presence frame with sequences 1,2,3 and base64 a,b,c. Perform original Go os.Stat once per frame, retain original error objects, send exact init_presence_observed acknowledgements. No d frame may appear. Kernel Path.stat for init is forbidden; existing membership discovery remains once.
  2. Verify one terminal inspected, exit 0, empty stderr, cannot_stat_init, full sorted agents a,b,c,d and evidence only a,b,c with accepted ack fields and final sequence/identity correlation. Native Go wraps the retained c error once using the original path and `%w`; compare original operation, bytes and unwrap identity/chain on POSIX and native Windows. Do not reconstruct from codes or launch/register/repair.
  3. On separate pipe attempts send EOF, missing newline, wrong/replayed sequence, mismatched directory identity, duplicate/extra/private fields, bool code/sequence, unknown presence, noncanonical base64 or missing/wrong failure. Require only preceding request frames on stdout, one static structured stderr terminal and exit 1; no partial inspected, retry, fallback or additional stat. Substitute mismatched terminal evidence in a Go fixture: Go must reject as unknown, not display retained native error.
  4. Repeat absent Project/no eligible members (zero callback frames, empty required correlation arrays), all-present including directory init, all-absent, mixed-before-orchestrator and native-byte/surrogate identities. Require existing Core order/role/subset/refusal semantics unchanged. Compare ordinary EOF/multiline `{project_dir}` request/default final shape exactly; no callback-only fields may appear there.
- **expected_evidence**: exact installed frames/acks/terminal/child status and once-only native-stat ledger; unchanged fixture bytes; full terminal transcript validation; original native error/unwrap equality including Windows branches; no serialized raw exception/manifest/init/private values.
- **pass_fail**: source authoring alone is not acceptance. Unsupported native host or lost/malformed output remains unresolved, never fullProject/FULL A or safe startup. No process/runtime/cleanup effects are authorized. Root LABT v2 versus this existing v3 file remains an owner-held mismatch, not normalized by this slice.

## Behavior PRJ007 — Bundle Core validates before noop and correlates native phases

- **id**: PRJ007
- **guards**: `project-creation` § [Recipe bundle materialization](CONTRACT.md#recipe-bundle-materialization)
- **runner**: acceptance LingTai/coding agent with authorized local shell and installed candidate CLI
- **prerequisites**: installed candidate `lingtai-agent` on PATH and Python3; permission to launch only this short-lived data-only CLI. The following fixture supplies phase observations, performs NO caller-disk I/O, and is NOT native Go conformance proof. No provider/global/runtime/Project access is needed.
- **steps**:
  1. Run this complete kernel protocol exercise from any directory:

     ```python
     import json, subprocess
     binding = {"kind":"disk", "source_b64":"cw==", "destination_b64":"cw=="}
     def run(name):
         acks = [{"status":"bundle_io_result", "sequence":1,
                  "operation":"load_metadata",
                  "result":{"name_b64":name,"library_b64":None}}]
         if name:
             acks.append({"status":"bundle_io_result", "sequence":2,
                          "operation":"absolute_locations",
                          "result":{"source_b64":"L3M=","destination_b64":"L3M="}})
         text = "\n".join(json.dumps(v) for v in [binding] + acks) + "\n"
         p = subprocess.run(["lingtai-agent","project","recipe-materialize"],
                            input=text, text=True, capture_output=True, check=False)
         assert p.returncode == 0 and p.stderr == "", (p.returncode,p.stderr)
         frames = [json.loads(line) for line in p.stdout.splitlines()]
         terminal = frames.pop()
         assert [v["operation"] for v in frames] == [v["operation"] for v in acks]
         assert all(v["status"] == "bundle_io" and v["binding"] == binding for v in frames)
         assert terminal["status"] == "bundle_materialized" and terminal["binding"] == binding
         assert terminal["source"] == "explicit" and terminal["failure"] is None
         assert terminal["transcript"] == [{k:v for k,v in a.items() if k != "status"} for a in acks]
         assert terminal["outcome"] == ("unchanged" if name else "rejected")
         assert terminal["code"] == ("" if name else "source_empty_name")
     run("")
     run("Rml4dHVyZQ==")
     ```

  2. Record exact installed candidate identity, stdout/stderr/exit evidence. Do
     not report this scripted observation boundary as a Go copy or native error
     test. Separately require parent-owned paired POSIX/Windows once-only native
     I/O/error-chain and cold resource/wizard/headless acceptance before activation.
- **expected_evidence**: empty decoded name refuses after metadata alone; valid
  name then equal successful Abs observations noop without remove/copy; every
  phase and complete terminal transcript matches, no arbitrary path I/O is done
  by Python and no runtime/provider/global mutation occurs.
- **pass_fail**: pass this kernel protocol scenario only when all assertions hold.
  Any failure/unreadable/lost/extra terminal is unresolved, not no-write proof or
  replay permission. Native Go and installed resource materialization acceptance
  remain separate, as do full Project/FULL A and publication. Existing root-LABT
  v2 versus this pre-existing file's v3 mismatch stays owner-held, not normalized.

## Behavior PRJ008 — Inventory shares identity and failed-scan semantics

- **id**: PRJ008
- **guards**: `project-creation` § [Inventory shares identity and failed-scan semantics](CONTRACT.md#running-inventory-and-selection)
- **runner**: acceptance owner using installed candidate kernel and paired Go fixture adapters
- **prerequisites**: new task-only fixtures/owned child processes; no production agent, credential, shared directory, provider or arbitrary process actions
- **steps**: Run the installed candidate inventory query over process-only, malformed, human, duplicate-PID and phantom fixtures; then run the retained original Go list/ProjectsModel fixtures through that SAME exact candidate. Rescan a replaced PID before Enter and force a scan/query failure; neither may become empty/success.
- **expected_evidence**: exact candidate identity, stdout/stderr/exit, native original error/effect trace and unchanged old caller oracles; tests/test_project_inventory.py is complementary Core evidence, not native conformance.
- **pass_fail**: every stated old behavior and once-only correlation must agree. Unsupported host/unknown output is untested, not PASS or permission to replay. Existing LABT version mismatch remains separately held, not normalized.

## Behavior PRJ009 — Native Recipe decoding keeps opaque owner documents

- **id**: PRJ009
- **guards**: `project-creation` § [Native Recipe decoding keeps opaque owner documents](CONTRACT.md#native-recipe-owner-documents)
- **runner**: acceptance owner using installed candidate kernel and paired Go fixture adapters
- **prerequisites**: new task-only fixtures/owned child processes; no production agent, credential, shared directory, provider or arbitrary process actions
- **steps**: Compare original native Go decoder/AppendSkillsPath and candidate at depth2000/9999/10000, duplicate/case/null/type errors and exact large integers; compare rewritten bytes, callback empty/binary/order/snapshot and selected-unreadable-locale behavior. Unknown layer transport must stop the actual save caller before configure.
- **expected_evidence**: exact candidate identity, stdout/stderr/exit, native original error/effect trace and unchanged old caller oracles; tests/test_project_recipe_native.py is complementary Core evidence, not native conformance.
- **pass_fail**: every stated old behavior and once-only correlation must agree. Unsupported host/unknown output is untested, not PASS or permission to replay. Existing LABT version mismatch remains separately held, not normalized.

## Behavior PRJ010 — External lifecycle keeps distinct native sequences

- **id**: PRJ010
- **guards**: `project-creation` § [External lifecycle keeps distinct native sequences](CONTRACT.md#external-project-lifecycle)
- **runner**: acceptance owner using installed candidate kernel and paired Go fixture adapters
- **prerequisites**: new task-only fixtures/owned child processes; no production agent, credential, shared directory, provider or arbitrary process actions
- **steps**: Use only owned child/fixture adapters: assert direct/all signal eligibility, refresh default/explicit/Puffo race/failed launch, normal CPR, temporary-clear error precedence, clean rediscovery/new-member/force and DISTINCT nirvana barrier-before-delete. Reject repeated/forged/lost terminal without another effect or false Done.
- **expected_evidence**: exact candidate identity, stdout/stderr/exit, native original error/effect trace and unchanged old caller oracles; tests/test_project_external_lifecycle.py is complementary Core evidence, not native conformance.
- **pass_fail**: every stated old behavior and once-only correlation must agree. Unsupported host/unknown output is untested, not PASS or permission to replay. Existing LABT version mismatch remains separately held, not normalized.
