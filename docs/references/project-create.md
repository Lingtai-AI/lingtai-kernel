---
related_files:
  - src/lingtai/kernel/project/inventory.py
  - src/lingtai/kernel/project/external_lifecycle.py
  - src/lingtai/adapters/project_recipe_native.py
  - src/lingtai/adapters/project_lifecycle_exchange.py
  - src/lingtai/cli_project_inventory.py
  - src/lingtai/cli_project_lifecycle.py
  - tests/test_project_inventory.py
  - tests/test_project_recipe_native.py
  - tests/test_project_external_lifecycle.py
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
  - src/lingtai/kernel/project/configuration.py
  - src/lingtai/adapters/project_configuration.py
  - src/lingtai/cli_project_configuration.py
  - src/lingtai/kernel/project/CONTRACT.md
  - src/lingtai/kernel/project/ANATOMY.md
  - src/lingtai/cli_project.py
  - src/lingtai/kernel/project/interactive_creation.py
  - src/lingtai/adapters/project_interactive_workspace.py
  - src/lingtai/adapters/project_creation_recipe.py
  - src/lingtai/adapters/project_creation_exchange.py
maintenance: |
  Keep this short public procedure aligned with the Project CLI contract. Do not
  add runtime, preset-management, or TUI workflow until separately authorized.
---
# Create a local Project

Create one fresh local LingTai seed without starting an agent:

```text
lingtai-agent project create --dir ROOT --name AGENT --preset PRESET --covenant-file FILE [--json]
```

`ROOT` must already be a directory without `.lingtai`. `FILE` is nonempty UTF-8
covenant text supplied by the caller. `PRESET` is loaded through the current
wrapper preset loader.

On success the command writes `ROOT/.lingtai/` with a `human` pseudo-agent and
one named agent, including their mailboxes, manifests, and the named agent's
`init.json` plus `settings/psyche.json` (which receives the caller covenant).
The CLI serializes that document through Psyche's owner primitive and validates
both generated owner documents before reporting success.

The command is data-only: it does not start an Agent, install a venv, contact a
provider, launch an MCP, or create TUI/global project state. Starting the agent
is a separate command:

```text
lingtai-agent run ROOT/.lingtai/AGENT
```

## Inspect local membership

Send the operation-specific request on stdin (not in argv):

```text
printf '%s\n' '{"project_dir":"/absolute/project/root"}' | lingtai-agent project inspect
```

Successful stdout is one JSON object with `status: "inspected"`, `initialized`
and `members`, plus `startup_readiness`. Each member has `directory_name`, `manifest_kind`, `role`,
`agent_name`, `address`, and `agent_id`. `directory_name` is the filesystem child;
`agent_name` is display identity and may differ. A present malformed manifest is
an `unknown` role repair target, not silently omitted. No raw manifest or
credential fields are returned. Missing `.lingtai` gives an empty observation,
not a request to initialize or start anything. Non-directory/unreadable
containers fail with exit 1 and a JSON error on stderr; do not reinterpret them
as empty projects. The caller must consume exactly one accepted stdout object
and a successful exit before using the observation.

Use `startup_readiness` as ordered stat-only evidence, not init validation: even a directory
counts present. Stat failure beats mixed init, then orchestrator refusal; exit-0 refusals do not
authorize launch. All-absent agents indicate rehydration, even on orchestrator refusal, without
wizard/writes. Go owns error prose; fields, eligible identities and mixed lists are defined in the
[startup readiness contract](../../src/lingtai/kernel/project/CONTRACT.md#startup-readiness).

This is read-only sequential evidence, not a lease, a process scan, or an
atomic snapshot. Do not authorize launch, cleanup or mutation solely from it.
Interactive creation-with-Recipe, skeleton initialization, setup, reconciliation,
import rehydration and Project lifecycle are not exposed by this operation;
the older create command remains a headless worker seed, not an interactive
orchestrator replacement.


### Preserve native Go init errors (inspection driver)

For the existing Go startup driver, keep the SAME command and live pipes. Send
ONE newline-terminated request:

```json
{"project_dir":"/absolute/project/root","init_observation_mode":"callback"}
```

On each flushed `observe_init_presence` frame validate only the closed fields
status, sequence and directory_name_b64 (canonical padded standard base64 native
filesystem bytes). Sequence starts 1; use the exact observed directory identity,
never a display label or arbitrary path. Call the original Go os.Stat ONCE on
its original joined init path. Success, even a directory, means present;
os.IsNotExist means absent; other errors mean stat_failed. Retain the ORIGINAL
error locally under sequence+directory bytes, not a reconstructed PathError.
Send one newline-terminated bound acknowledgement, for example:

```json
{"status":"init_presence_observed","sequence":1,"directory_name_b64":"YQ==","presence":"absent","failure":null}
```

Stat_failed alone sends closed factual path_b64/native_errno/native_winerror
instead of null. Never serialize exception strings, credentials or documents.
Core owns eligibility/order and readiness, stops at the first failed stat, and
never invokes the kernel native init stat in this mode. No second stat/fallback.

Accept readiness only after closed terminal/identity/reason validation, complete ordered transcript
correlation, exit 0 and empty stderr; native base64 identity, not surrogate-bearing display labels,
is authoritative. On matched cannot_stat_init show the retained ORIGINAL Go error once with the
old wrapper/path/unwrap chain; never infer a Windows operation from codes.
EOF/malformed/mismatched/private ack stops with one attempted static stderr terminal/exit 1,
no partial readiness or retry; intermediate frames are not success and lost terminal is unknown.
This is read-only, not a lease/launch permission/process scan/filesystem RPC. Exact validation and
separate native Windows/name/error-chain/cold-CLI acceptance: [inspection exchange contract](../../src/lingtai/kernel/project/CONTRACT.md#synchronous-native-init-observation-exchange).

## Approved interactive creation (Go driver)

Use `lingtai-agent project create-interactive` with live stdin/stdout pipes;
this is not the headless worker seed command. Send an explicit non-secret
projection of the reviewed draft as one JSON line. The exact fields, ordering,
and outcome rules are in the [Project transaction contract](../../src/lingtai/kernel/project/CONTRACT.md#interactive-creation-transaction).
A minimal initial line (replace paths with approved real paths) is:

```json
{"project_dir":"/approved/root","approved_root":"/approved/root","agent_name":"Display name","directory_name":"orchestrator","preset_ref":"/presets/selected.json","preset_manifest":{"llm":{"provider":"openai","model":"selected-model","api_key_env":"OPENAI_API_KEY"},"capabilities":{"shell":{"yolo":false}}},"options":{"language":"en","covenant":"Human-approved covenant","env_file":"/approved/task.env"},"recipe":"adaptive","process_evidence":"clean"}
```

The `env_file` value is an explicit non-secret path, not credential contents.
It is required when the selected LLM uses `api_key_env` without an inline key
under the current init validator. Omitting it fails before ready or writes; the
kernel does not synthesize an env file or resolve a provider key during creation.

1. Retain the wizard draft in memory through Review. Independently bind the
   approved root, normalize the selected preset without persisting it, and
   perform the real process inventory recheck. Do not turn unknown into clean.
2. Send the line, then read `ready_for_prerequisites`. Until that object arrives,
   **do not persist pending preset or credentials**. Do not log the input draft.
3. Check the returned exact `preset_ref` and nonempty `attempt_id`. Only then
   let Go persist and verify the exact preset reference and pending credentials
   using its existing owners. Send one newline-terminated acknowledgement:

   ```json
   {"status":"prerequisites_acknowledged","attempt_id":"COPY-READY-ID","preset_ref":"/presets/selected.json","accepted":true}
   ```

   Use false on persistence/verification failure. Never send credentials.
4. Accept created only after the terminal matching `created,committed:true`
   object and exit 0. Record warnings; do not regenerate init or reapply Recipe
   in Go. Persist UI preferences/registration and launch separately, without
   rolling back known publication on a UI/launch failure.
5. On documented exit-1 error, show its stable code and keep Review available.
   On unknown transport, stop and reconcile with the human before retrying;
   neither `project inspect` nor directory existence proves the lost outcome.

A valid root-local Recipe wins over packaged adaptive, with language then root
fallback. Packaged assets work in a wheel without Go extraction/global recipes.
Comment seeds Pad instead of retired init `comment_file`; covenant uses current
Psyche serialization. Procedures remain kernel-owned. External location and
command-list text may be supplied as non-secret strings; omission uses unknown
location and empty commands, with no network lookup. The kernel does not change
credentials, preset profiles, runtime settings, registration, or run an Agent.

This slice does not implement `/setup`, imported networks, existing-project
reconciliation, skeleton initialization or Project lifecycle. Full migration is
still pending; test/installed-wheel/startup acceptance is a separate gate.

## Existing Project configuration (JSON driver)

The kernel now offers the bounded `initialize`, `configure`, `propagate-policy`,
`propagate-config` and `rehydrate` operations. These add no flags: send one
EOF-delimited public JSON object on stdin. Exact closed requests, default option
values and result/error envelopes are in the
[configuration contract](../../src/lingtai/kernel/project/CONTRACT.md#existing-project-configuration-candidate-slice).
The root is the parent directory containing `.lingtai`, not `.lingtai` itself.
For example, data-only skeleton initialization is:

```text
printf '%s' '{"project_dir":"/absolute/existing/root"}' | lingtai-agent project initialize
```

Configure loads only an already-persisted selected preset reference; Go retains
credential/preset persistence. Keep current uses an empty preset_ref and explicit
keep_current/preserve_active, never a synthetic preset filename. Existing owner
bytes remain unchanged unless an explicit prompt edit is requested. New prompt
content uses Psyche's serializer; Pad replaces retired comment inputs.
Go also projects its existing language-default covenant path as public
`options.default_covenant_file` metadata, with an explicit nonempty legacy choice
in `options.covenant_file`. Core preserves the old init-path precedence (explicit,
existing nonempty, default). Fallback metadata alone never edits Psyche or reads
that legacy file; explicit prompt edits still use the current owner serializer.

Policy propagation maintains parsed preset blocks, not boot readiness. Missing
Pad/env/blueprint/preset files or malformed unrelated Psyche do not block this
policy-only write, and reader status stays `not_read`. Malformed init and write
failures remain explicit partial counts while later members continue. Configure,
config propagation and rehydrate keep their actual runtime-owner reader checks.
Config propagation copies init-owned tools/nonempty env references and worker
permissions, not model authorization. An existing target policy and its effective
model source stay authoritative (including revocations and opaque fields);
canonical reference-bound `llm` stays exact. Legacy inline inputs keep their old
raw-LLM copy beside legacy target policy, which still wins at read. Only targets
without a policy may receive the source model source. Use `propagate-policy`
for authorization changes rather than treating config copy as a model switch.

Imported all-absent rehydrate includes selected-source configuration. Do not
write source init first: a mixed project is refused, not automatically repaired.
A source/worker error can leave partial state, and previously written files are
not rolled back. Unknown transport does not prove either rejection or success:
no automatic retry/deletion/repair. These operations do not apply/reconcile a
Recipe, install, register or start/refresh/suspend an agent. Full Project
migration, including Recipe reconciliation and lifecycle, remains pending.

## Observe and apply an existing local Recipe

This bounded candidate uses only `ROOT/.recipe`; it never selects, imports,
replaces a bundle or creates a hidden fresh-creation attempt. For change evidence:

```text
printf '%s\n' '{"project_dir":"ROOT"}' | lingtai-agent project recipe-observe
```

Read `state` (absent/equal/changed/read_failure) AND `needs_apply`. Read failure
retains the legacy boolean, not success proof. Empty root explicitly means
absent/false, never the current directory. Nonempty root retains existing lexical path authority; missing/non-directory Recipe naturally observes absent.

To apply, send exactly one UTF-8 JSON object on stdin (no flags):

```json
{"project_dir":"ROOT","language":"en","rendering":{"time":"2030-01-02 03:04","address":"human","location":"unknown","commands":"  - /help — Help"}}
```

Outer Go resolves these **public** strings with the current greet callback:
local timestamp `YYYY-MM-DD HH:mm`, human address, cached/resolved location
joined `City, Region, Country` or `unknown`, and already-translated palette
commands. Kernel performs only ordered literal substitutions. Use `rendering:null`
for the old headless identity callback. Do not send credentials or a whole draft.

Validate the whole envelope and child exit. Existing callers use `legacy_applied` (the old count
quirk) and top-level code for old errors/warnings, not `applied` (error-free members) or reader
receipts/removal evidence as new launch/warning gates. No old error: stdout `recipe_applied`, exit 0, even with reader-only
partial evidence; old apply/request/driver failure: one static stderr error, exit 1. Neither count
promises launchability. Missing/unreadable/empty greet is skipped/reported; paths append only to
existing skills, with no new capability or propagated owner settings.
**Snapshot is always attempted after traversal, including partial apply.** Inspect its separate
attempted/removal_succeeded/copied/code evidence: removal is best-effort, copy nontransactional;
removal failure alone is ignored for caller error/exit if copy succeeds and may retain old files.
A copied snapshot can make observe equal after member failure. Prompt errors can truncate/leave
partial bytes; copy errors can leave a partial tree. Unknown/cancelled/lost output is NOT no-write
proof; no retry/rollback/cleanup. Existing library/language strings retain Go lexical Join/Clean,
including parent/non-segment/absolute-looking values, without fresh-creation safe-sibling or
five-OPS nonregular/symlink gates. Prompt/snapshot target following and fixed init.json.tmp + rename
remain unchanged; scoped caller-root Recipe authority is not general write permission.
UI selection, bundle/materialization/import, registration/lifecycle remain separate, not fullProject.
Schemas/phase/count policy and PRJ004: [Recipe contract](../../src/lingtai/kernel/project/CONTRACT.md#existing-local-recipe-observationapply-candidate-slice).


### Retain an arbitrary Go callback (synchronous driver)

Use the SAME `lingtai-agent project recipe-apply`, no flags. With live pipes,
send ONE newline-terminated closed request instead of the EOF static form:

```json
{"project_dir":"BUNDLE","lingtai_dir":"EXPLICIT-DESTINATION-OR-STAGING","language":"en","rendering_mode":"callback"}
```

Both paths are independent, nonempty lexical caller paths; they need not be
canonical/equal/parent-child. If child cwd differs, the caller preserves original
Go path meaning before sending. `identity` mode needs no callback/ack and keeps
original bytes within kernel. Static `rendering` requests still accept multiline
or EOF-delimited JSON and retain their prior 1 MiB character bound. The new
form, rendering frames and terminal receipts have no fixed line/output cap.
Memory grows with payload; base64 requires `4*ceil(decoded_bytes/3)` ASCII bytes
plus JSON/newline and copies. Allocation/broken transport is unknown, not empty.

On each flushed stdout `render_recipe_member` frame, validate exact fields
`status,sequence,directory_name_b64,template_b64,template_bytes`. Decode standard
canonical padded base64; count BYTES, not characters/runes. Sequence starts 1.
Execute the ORIGINAL Go function exactly once on the original calling goroutine,
with `string(decoded_template)` (not member name). Send and flush one bound ack:

```json
{"status":"recipe_member_rendered","sequence":1,"rendered_b64":"","rendered_bytes":0}
```

Empty/binary results are written verbatim, including empty truncation; each prompt/skills phase
finishes before the next callback, which can observe writes and alter future init/tree/Recipe.
No precomputation/batching/replay; absent/unreadable/empty original greet, no members or pretraversal failure
means no callback. Nil uses identity. Template/enumeration are captured once; the library helper
conditionally rereads metadata AFTER greet, failure nonfatal. Never log templates/results/exception/panic text.
Use authoritative native `directory_name_b64`, not lossy display labels; `legacy_failure` is factual
phase/path/code/parse-offset evidence, not proof of exact Go error formatting or path permission.
See the [rendering contract](../../src/lingtai/kernel/project/CONTRACT.md#synchronous-existing-member-rendering-extension) for closed schemas and paired native acceptance.

On panic/Goexit preserve original unwind: close child input and reap, without recovery/substitute ack.
EOF/malformed/broken ack stops before pending prompt/skills and WITHOUT snapshot. The observable
exit-1 `recipe_exchange_stopped`/`recipe_exchange_failed` terminal carries completed receipts,
pending sequence/native identity and separate `legacy_code,legacy_failure`.
`partial` means known preceding writes; `rejected` rejects only the pending member, not global effects.
Earlier failed writes/callback effects may survive unknown; lost terminal stays unknown even after ack,
which proves rendering, NOT prompt write. No retry/rollback/cleanup, Go-write fallback, timeout/thread
or supervisor; this is not full Project completion or PR activation.

## Materialize a Recipe bundle (native driver)

Use `lingtai-agent project recipe-materialize` with live pipes, not an Agent or
fresh-creation attempt. This is the existing disk copy, packaged copy and wizard
local-first staging boundary; apply/configure and TUI selection persistence are
separate later operations. Exact closed schemas live in the
[Project bundle contract](../../src/lingtai/kernel/project/CONTRACT.md#recipe-bundle-materialization).
The matching Go adapter must exist and be accepted before production use.

1. Keep the old exported CopyBundle/CopyEmbeddedBundle signatures. Bind original
   Go source/destination strings and cwd meaning once; wizard supplies original
   filepath.Dir(lingtaiDir), global root and name. Send only the selected closed
   disk/packaged/wizard request, canonical padded base64 of original string bytes.
   For example `src` and `dst` are:

   ```json
   {"kind":"disk","source_b64":"c3Jj","destination_b64":"ZHN0"}
   ```

2. Read each flushed bundle_io frame. Validate the exact request binding,
   sequence, operation and phase-specific arguments before I/O. Execute only
   that phase synchronously once, using original native filepath/Stat/ReadDir/
   Open/copy/JSON operations. Never invoke the whole old CopyBundle as a facade,
   repeat a stat/copy, select fallback in Go or run a Python equivalent first.
   Keep original native errors locally, keyed by sequence+operation+arguments.
   Metadata is full native RecipeInfo decoding; return decoded name and library,
   leaving empty-name validity to Core. An example first acknowledgement is:

   ```json
   {"status":"bundle_io_result","sequence":1,"operation":"load_metadata","result":{"name_b64":"Rml4dHVyZQ==","library_b64":null}}
   ```

3. Core validates before same-path detection or destruction, then replaces
   .recipe before additive library/network merges. Optional source Stat errors
   are tolerated observations, not failed operations. Do not strengthen legacy
   symlink, nonregular, unusual-library, mode/truncation or partial-write behavior.
   Valid local wins; invalid local falls through; malformed found global never
   falls back. Keep wizard setup/draft skips and imported preguard unchanged.
4. For packaged copy, Go supplies only native slash path.Join candidates. Kernel
   installed catalog owns adaptive/plain/greeter/tutorial without global bootstrap.
   Metadata bytes are decoded natively. For each delivered member, mkdir parent
   0755 first, ACK, then kernel reads resource, then native WriteFile0644. Do not
   prefetch/reorder/batch writes or create empty directories. Runtime Markdown is
   frontmatter-stripped operational body; raw source export is a different thing.
5. Validate exactly one bundle_materialized terminal, full acknowledgement
   transcript, binding/source/outcome/code/failure consistency, EOF, child exit0
   and empty stderr. Exit0 means accepted DOMAIN result, not successful copy.
   Only then restore the retained ORIGINAL error with old phase wording/%w;
   empty-name uses the original message/source label. Wizard adds its wrapper once.
   Keep headless copy-warning-then-still-apply/missing-disk-skip and required UI
   state ordering unchanged; this does not authorize embedded headless fallback.

On incomplete/broken/malformed ack, lost/forged terminal or abnormal child exit,
physical effects are unknown. Never replay, fall back, rollback, repair or clean
caller trees. An acknowledged failed remove/copy may already have changed bytes;
there is no transaction. Unexpected installed resource read failure is unknown,
not a reconstructed Go PathError. Use only existing direct-child close/reap
contracts. Parent-owned installed wheel/sdist, exact bytes/error chains, native
POSIX/Windows and actual Go caller proof remain necessary; authored source/tests
are not full Project completion or release readiness.

## Existing Project callers after creation

Use the kernel-discovered `lingtai-recipe` menu for format/export/validator work;
resolve its catalog location, not a TUI utilities directory. All four packaged
Recipe identities ship in kernel wheel/sdist; fresh creation choices are not
expanded merely because those resources exist.

`project inventory-query` consumes native observed rows only; no Agent/provider
is built and no scanning/writing permission is implied. Go list and ProjectsModel
bind the same exact managed-runtime query and validate original row/PID identity.
Native Recipe mode preserves Go JSON documents/unknown values opaquely;
`not_read_native` honestly reports that no Python owner reader ran. It cannot
be used as init readiness. The original raw apply mode remains distinct.

`project lifecycle` needs its bound native driver. The Core emits operations;
Go executes only original action-bound effects. Never treat stdout exit0 alone
as success: validate complete terminal/transcript/EOF/exit and original error
correlation. Unknown/lost output forbids replay or further effects. Force refresh,
normal CPR, temporary clear, force CLI clean and nirvana retain DIFFERENT old
orders/refusals; do not substitute one for another or bypass human confirmation.
No arbitrary host process action is authorized by reading this manual.
