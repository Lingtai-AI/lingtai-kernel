---
name: project-creation
contract_version: 7
root_contract: CONTRACT.md
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
  - src/lingtai/intrinsic_skills/lingtai-recipe/SKILL.md
  - src/lingtai/intrinsic_skills/lingtai-recipe/reference/recipe-format/SKILL.md
  - src/lingtai/intrinsic_skills/lingtai-recipe/reference/export-recipe/SKILL.md
  - src/lingtai/intrinsic_skills/lingtai-recipe/scripts/validate_recipe.py
  - src/lingtai/intrinsic_skills/lingtai-recipe/assets/gitignore.template
  - src/lingtai/kernel/project/bundle.py
  - src/lingtai/adapters/project_bundle.py
  - src/lingtai/adapters/project_bundle_exchange.py
  - src/lingtai/cli_project_bundle.py
  - tests/test_project_bundle.py
  - src/lingtai/kernel/project/recipe_assets/greeter/recipe/comment/comment.md
  - src/lingtai/kernel/project/recipe_assets/greeter/recipe/comment/wen/comment.md
  - src/lingtai/kernel/project/recipe_assets/greeter/recipe/comment/zh/comment.md
  - src/lingtai/kernel/project/recipe_assets/greeter/recipe/greet/greet.md
  - src/lingtai/kernel/project/recipe_assets/greeter/recipe/greet/wen/greet.md
  - src/lingtai/kernel/project/recipe_assets/greeter/recipe/greet/zh/greet.md
  - src/lingtai/kernel/project/recipe_assets/greeter/recipe/recipe.json
  - src/lingtai/kernel/project/recipe_assets/plain/recipe/recipe.json
  - src/lingtai/kernel/project/recipe_assets/tutorial/recipe/comment/comment.md
  - src/lingtai/kernel/project/recipe_assets/tutorial/recipe/comment/wen/comment.md
  - src/lingtai/kernel/project/recipe_assets/tutorial/recipe/comment/zh/comment.md
  - src/lingtai/kernel/project/recipe_assets/tutorial/recipe/greet/greet.md
  - src/lingtai/kernel/project/recipe_assets/tutorial/recipe/greet/wen/greet.md
  - src/lingtai/kernel/project/recipe_assets/tutorial/recipe/greet/zh/greet.md
  - src/lingtai/kernel/project/recipe_assets/tutorial/recipe/recipe.json
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
  - tests/test_init_ownership.py
  - tests/fixtures/project_configuration.json
  - src/lingtai/kernel/project/ANATOMY.md
  - src/lingtai/kernel/project/__init__.py
  - src/lingtai/adapters/project_workspace.py
  - src/lingtai/cli_project.py
  - src/lingtai/tools/psyche/settings.py
  - docs/references/project-create.md
  - tests/test_project_creation.py
  - src/lingtai/kernel/project/inspection.py
  - src/lingtai/adapters/project_inspection.py
  - tests/test_project_inspection.py
  - src/lingtai/kernel/project/BEHAVIORS.md
  - src/lingtai/kernel/agent_presence/CONTRACT.md
  - src/lingtai/kernel/project/interactive_creation.py
  - src/lingtai/adapters/project_interactive_workspace.py
  - src/lingtai/adapters/project_creation_recipe.py
  - src/lingtai/adapters/project_creation_exchange.py
  - tests/test_project_interactive_creation.py
  - src/lingtai/kernel/project/recipe_assets/adaptive/recipe/comment/comment.md
  - src/lingtai/kernel/project/recipe_assets/adaptive/recipe/comment/wen/comment.md
  - src/lingtai/kernel/project/recipe_assets/adaptive/recipe/comment/zh/comment.md
  - src/lingtai/kernel/project/recipe_assets/adaptive/recipe/greet/greet.md
  - src/lingtai/kernel/project/recipe_assets/adaptive/recipe/greet/wen/greet.md
  - src/lingtai/kernel/project/recipe_assets/adaptive/recipe/greet/zh/greet.md
  - src/lingtai/kernel/project/recipe_assets/adaptive/recipe/recipe.json
  - pyproject.toml
  - MANIFEST.in
  - src/lingtai/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/pad/CONTRACT.md
maintenance: |
  Keep this small governed Project boundary reciprocal with its Anatomy and root
  Contract. Update the Port, adapter, focused tests, and this contract together
  when its observable creation behavior changes.
---
# Project

This owner covers creation/configuration, Recipe policy, running-inventory
selection and external lifecycle decisions. The creation guarantees below stay
specific to creation; later sections define the additional Ports. Concrete Go
filesystem/process/JSON behavior remains in bound adapters, not in Core.

`lingtai-agent project create` creates one new local `.lingtai` seed below a
caller-selected existing directory. It writes a `human` pseudo-agent and one
named agent with mailboxes, manifests, an `init.json` based on the caller's
preset, and `settings/psyche.json` containing the caller covenant. It does not start an Agent or create
runtime, provider, MCP, registry, venv, or TUI state.

## Init/preset ownership

Guarded by [PRJ011](BEHAVIORS.md#behavior-prj011). New headless/interactive/
configure/rehydrate seeds use the canonical init owner sections and save only
`llm.preset` references, never preset LLM/capabilities copies. Tools default to
all shipped selectable builtins independently of the chosen model; Recipe library
paths are explicit init-owned skills overrides. Fresh Project agents retain the standard shared skill shelves (`../.library_shared` and `~/.lingtai-tui/utilities`) as creation-owned `tools.skills.paths`; Recipe paths append without replacing them, and existing explicit paths/off values are preserved. Existing canonical/legacy init
keeps its authored shape on configure; no implicit conversion is authorized.
Configure preserves target tool kwargs/skills, plugin roots and MCP; preset
capabilities never enter that ownership. Explicit config propagation retains its
existing distinct promise to copy SOURCE INIT-owned tools, not preset tools. Explicit name/language/
permission edits retain their existing requested meaning and blueprint history.

`ConfiguredMemberSeed.pad_seed` and `InteractiveProjectSeed.pad_seed` are additive
string fields (default empty); `ProjectConfigurationPort.save_member_init` adds
keyword `pad_seed: str = ""` for imported workers. The concrete publication
adapter calls the existing Pad `_seed_pad` only after init publication and before
reader validation (interactive: inside owned staging). It fills absent/empty
canonical Pad only, never nonempty memory. Pad text/pointers are not serialized
in new init. Old pad/pad_file/lingtai inputs and original legacy files remain
untouched; no new memory schema or ongoing binding exists. Seed failure reports
`pad_seed_failed` with truthful earlier-write receipts, no rollback. Existing
request `options.pad` carries typed creation data; no CLI/exchange field or
native callback frame was added. Headless create has no new seed flag.

Legacy options addons/runtime knobs/covenant fallback metadata remain accepted
for paired-caller compatibility, but never author those retired fields into a
new canonical init. Existing legacy declarations are preserved, not activated by
model selection. New explicit MCP desired selection comes solely from `options.mcp`. Compatibility-only missing addon launchers retain the original request venv seed via `ConfiguredMemberSeed.legacy_mcp_venv_path`; that input does not replace the authored host or existing MCP kwargs.
Psyche prompt edits still use its serializer; System policy is unchanged.

## Boundary

The Core owns request validation and a complete `ProjectSeed`. Its one
technology-neutral `ProjectWorkspacePort.create(seed)` operation is implemented
by the filesystem adapter. `cli_project` is the composition root: it reads the
caller covenant, serializes it through Psyche's public v1 owner-document
serializer, uses wrapper `load_preset`, and injects both the opaque serialized
content and the current init/Psyche readers. Project Core neither imports Psyche
nor names its schema version or value keys.

## Guarantees

- The root must already be a directory; an existing `.lingtai` is refused.
- The caller supplies a nonempty UTF-8 covenant and a loadable preset.
- Psyche is the only owner-document serialization authority; Project Core
  forwards the composition-root-provided content byte-for-byte into its seed.
- The adapter creates the `.lingtai` directory exclusively, writes the complete
  seed, then verifies that initial init and Psyche owner documents can be read
  by the current reconstruction seams.
  If creation or validation fails, it removes only the directory it just created.
- Results and errors contain stable small fields and do not start an Agent.

## Inspection

Guarded by [PRJ001](BEHAVIORS.md#behavior-prj001).

`ProjectInspectionUseCase.inspect()` consumes the construction-bound
`ProjectInspectionPort.observe_members()` directory evidence and returns typed
`ProjectInspectionResult`/`ProjectMemberObservation` values. The production
`FilesystemProjectInspectionAdapter` enumerates immediate real subdirectories;
symlink directories are excluded as in Go discovery. Presence remains the
manifest parser and absent/malformed/valid evidence owner. A non-file manifest
or stat/read/parse error is preserved as malformed; absence is not membership.
A read racing removal may observe absence. No operation writes state or claims a
consistent snapshot, runtime ownership, launchability or liveness.

Valid missing/null admin means human; literal true in an admin object means
orchestrator; other valid non-null admin means worker. Malformed members have
unknown role, not worker. Identity fields remain separate from directory name;
missing/non-string identity fields return empty strings. Results never include
raw manifests. Members are sorted by directory name. Inspection failure does
not produce a partial-success result.

`lingtai-agent project inspect` reads exactly `{"project_dir": "ROOT"}` from
stdin and emits one `status: inspected` JSON object on stdout. Missing `.lingtai`
under an existing root returns `initialized: false, members: []` with no writes;
an existing non-directory container fails. Errors are exit 1 with one JSON
object on stderr using `invalid_project_request`, `invalid_project_root`, or
`project_inspect_failed`. This operation adds no flags and changes neither the
existing create request/output nor run behavior. No secrets belong on argv.

Shared fake/filesystem cases and CLI fixtures live in
`tests/test_project_inspection.py`. The procedure is in the existing Project
manual. These tests require execution by the acceptance owner; authored source
alone is not proof of conformance.

### Startup readiness

Guarded by [PRJ005](BEHAVIORS.md#behavior-prj005). The same inspect request and
successful observation now add one closed `startup_readiness` object; no new
command, flag or lifecycle surface. `ProjectInspectionPort.observe_init_presence`
is a required specialized read boundary. Core classifies the existing startup
subset from observed members: valid worker/orchestrator, non-dot directory only.
Humans, malformed members and dot helpers remain in the general member report,
not in this subset; shared Presence membership is unchanged.

Readiness has exactly `reason`, `needs_rehydration`, `agents`, `orchestrators`,
`init_evidence`. Names use sorted immediate directory identities, not display
names. Each init item has exactly `directory_name`, `presence`, `failure`.
Presence is `present`, `absent`, or `stat_failed`. Successful stat means present
for ANY target, including directory/nonregular or symlink to an existing target;
it does not read, validate or repair init. Missing/dangling means absent. Other
stat failure stops at the first failing sorted member: evidence is only the
observed prefix including that failure, never guessed absence for later names.
Failure is null except for stat_failed, when exactly `path_b64`, `native_errno`,
`native_winerror` carry the adapter's requested native path bytes (canonical
padded base64) and integer-or-null native codes. No exception strings or raw
manifest/init contents cross this boundary. Native facts are opaque to Core;
they cannot reconstruct the original Go error operation/unwrap chain. Exact Go
presentation requires the callback boundary below, retaining the actual error
locally rather than translating Python codes or guessing a Windows operation.

Reasons are closed: `not_initialized`, `ready`, `cannot_stat_init`, `mixed_init`,
`no_agents`, `no_orchestrator`, `multiple_orchestrators`. Absent Project gives
not_initialized, false rehydration, empty arrays. For initialized Projects the
first stat failure beats mixed presence; mixed presence beats orchestrator
classification. Mixed retains the complete with/missing directory identities in
ordered init evidence. Otherwise all-absent with at least one eligible agent
sets needs_rehydration true, all-present/no-agents false. Then no_agents,
no_orchestrator and multiple_orchestrators are distinct refusals; exactly one
orchestrator yields ready. Rehydration stays factual even on orchestrator
refusal; only ready is usable by startup as an accepted existing network.

Domain refusals are successful read-only observations (exit 0), NOT CLI request
or enumeration failures. Existing missing/non-directory/read-failure inspect
behavior remains unchanged; the old standalone Go init helper's soft ReadDir
return does not weaken the real startup's preliminary fail-loud inspection.
Original long error prose/recovery advice belongs only in Go presentation.
This is sequential readiness evidence, not validation, a lease, authorization
to launch, repair or mutation, nor completion of the paired consumer migration.


### Synchronous native init observation exchange

Guarded by [PRJ006](BEHAVIORS.md#behavior-prj006). The SAME no-flag
`lingtai-agent project inspect` additionally accepts exactly
`{project_dir:string,init_observation_mode:"callback"}` as ONE newline-terminated
JSON object. Nonempty project_dir is required. Omitted selector keeps the old
EOF/multiline `json.load` request grammar, old error envelopes and EXACT old
terminal fields/serialization; optional explicit `"native"` uses that same
filesystem stat/payload, with closed duplicate/nonfinite-free new request syntax.
Callback syntax rejects duplicates, nonfinite values and extra fields; it cannot
be multiline/EOF-delimited. No new cap, dependency, thread, timeout or setting.

`JsonProjectInspectionAdapter` decorates the existing construction-bound Port;
observe_members delegates unchanged ONCE, observe_init_presence emits a frame
instead of invoking kernel native stat. Core still owns eligible identities,
roles, sorting, stat-failure stopping prefix and all readiness policy. Only when
Core asks, stdout emits and synchronously flushes exactly:
`{status:"observe_init_presence",sequence:integer,directory_name_b64:string}`.
Sequence starts 1, increases by one for each eligible member. Identity is
canonical RFC4648 standard padded base64 of `os.fsencode(directory_name)`;
no Unicode normalization/replacement or reordering. No arbitrary input path,
manifest/init content, credentials or native exception string is exchanged.

The Go Adapter validates the closed frame, derives ONLY that observed member's
original native `filepath.Join(lingtaiDir,name,"init.json")`, calls `os.Stat`
EXACTLY ONCE synchronously, and retains its original error object keyed by
sequence AND decoded native directory identity. Any stat success, including
nonregular targets, gives present; os.IsNotExist gives absent; all other errors
give stat_failed. No kernel stat first, second stat, fallback or retry occurs.
Stdin ack is exactly:
`{status:"init_presence_observed",sequence:integer,directory_name_b64:string,
  presence:"present"|"absent"|"stat_failed",failure:null|facts}`.
Integers exclude bool. Ack must echo the pending sequence and exact base64
identity. facts has exactly `path_b64:string,native_errno:integer|null,
native_winerror:integer|null`: canonical requested Go native path bytes and
factual nullable codes. Core never interprets the path/codes or names the Go
operation. Success/absence MUST carry null failure; stat_failed MUST carry facts.
Codes may both be null; the original Go error remains locally authoritative.

Callback terminal stdout is still ONE final `status:"inspected"` object after
zero or more intermediate frames. Top-level keys remain status, initialized,
members, startup_readiness. Closed differences from ordinary terminal shape:
each member adds `directory_name_b64`; startup_readiness adds `agents_b64` and
`orchestrators_b64` (parallel arrays to the existing sorted string arrays);
each init_evidence item adds `sequence` and `directory_name_b64`. All these
fields are REQUIRED in callback mode, even empty arrays. ASCII JSON escaping
preserves surrogate-bearing display labels; base64 native identities are
AUTHORITATIVE, not lossy JSON-decoded names. No normalization to Go sort order
is authorized: preserve existing Core directory identity order. Adapter verifies
each final evidence identity/observation equals its immutable accepted transcript
before projecting sequence. Ordinary mode gains NONE of these fields.

Go MUST validate full closed terminal shape, arrays/member identity consistency,
exact ordered ack transcript/evidence including presence/failure, no missing or
extra observations, sorted prefix on stat_failed, reason/evidence consistency,
child exit 0 and empty stderr before consuming readiness. On cannot_stat_init,
exactly the final failed evidence sequence+identity+facts MUST match its retained
failed observation. Only then display the ORIGINAL retained error once with the
original `sanity check: cannot stat %s: %w` wrapper and original path spelling.
Never fabricate PathError, operation or cause from serialized codes, scan again,
rederive readiness or substitute success. A mismatched final response is unknown
transport, not a native failure to display or permission to proceed.

EOF/no newline, wrong sequence/identity/type/enum, malformed/noncanonical base64,
duplicate/extra/private fields, missing required failure, wrong nullable code or
broken exchange stops with no partial inspected response and no fallback. CLI
exit 1 uses its existing structured stderr shape exactly
`{status:"error",code:"invalid_project_exchange",error:static string}`;
preceding flushed request frames alone may remain on stdout. Request errors use
invalid_project_request; unchanged discovery failures retain existing codes.
Only one terminal status is attempted, no raw exception/input text. Missing or
broken terminal output/abnormal child exit is unknown, never successful evidence.
This is pure read-only sequential observation with no rollback or disk state;
Go native Windows error-chain and filename encoding acceptance remain required.

## Non-goals

This first slice does not add agents to existing projects, recover partial
projects, manage presets, launch an agent, or provision a venv, addon, MCP, or
TUI project record. The user procedure is
[`docs/references/project-create.md`](../../../../docs/references/project-create.md).

## Interactive creation transaction

Guarded by [PRJ002](BEHAVIORS.md#behavior-prj002). This is fresh interactive
creation only, **not completion of the full Project-domain migration**.

`InteractiveCreationUseCase.create` owns the sequence, membership invariant and
complete specialized `InteractiveProjectSeed`; adapters serialize that seed
without selecting admin/preset authorization or prompt preference policy.
Its construction-injected `InteractiveWorkspacePort` owns preflight, beginning
an attempt, building, validating pending/persisted prerequisites, exclusive
publication, and proven-owned discard. `CreationRecipePort` supplies local
observations, wheel-owned adaptive resources and staged layers.
`CreationPrerequisitePort.persist_and_verify` is called only after actual staged
membership and init/Psyche readers pass. Its production JSON exchange lets the
existing Go credential/preset owner persist and verify those dependencies;
Core never acquires or writes credentials, preset profiles or runtime config.

### Request and exchange

The actual command is `lingtai-agent project create-interactive`, with no
operation flags. It consumes two newline-terminated UTF-8 JSON objects on one
live stdin pipe (maximum 1 MiB characters per line; duplicates and nonfinite
numbers rejected). Do not use EOF-delimited JSON for this interactive exchange.
First object has exactly the following required keys, plus optional `location`
and `commands` strings:

- `project_dir`, `approved_root`: independently supplied absolute roots, equal
  after filesystem resolution; existing directory required.
- `agent_name`: nonblank display identity; `directory_name`: separate safe,
  non-dot, non-human child segment.
- `preset_ref`: exact absolute or home-relative JSON/JSONC path reference;
  `preset_manifest`: reviewed **normalized** manifest snapshot with `llm` and
  optional `capabilities`. Snapshot is pending input, not evidence of existence.
- `options`: closed object with required `language` and nonempty `covenant`
  strings. Optional `karma` (default true), `nirvana` (default false) are literal
  booleans and at least one must be true. Optional `pad`, `base_prompt`,
  `env_file`, `venv_path` are strings; `addons`, `allowed_presets` are arrays of
  nonempty strings; `mcp` is an object of already-selected activation specs.
  When the current init owner requires `env_file` for `llm.api_key_env`, supply
  that explicit non-secret path; omission is not an authentication-free default.
- `recipe`: `"adaptive"` or `""` (no Recipe).
- `process_evidence`: `"clean"`, `"blocked"` or `"unknown"`. The trusted outer
  consumer MUST perform its fresh OS inventory scan immediately before sending
  the request. Matching phantom records are blocked; failed/incomplete scans
  are unknown. Absence of `.lingtai` alone is not a matching process. Core
  refuses blocked/unknown before any write. This field is an outer observation,
  not a kernel process scan, lease or authentication assertion.

No raw credential fields are accepted; existing secret-key classification
rejects them recursively, except public `*_env` identifiers naming environment
slots. Arbitrary text/specs are trusted operator input, not a secret detector;
Go MUST serialize an explicit non-secret projection, never the whole draft.
Allowed references retain exact caller spelling; selected ref is included once
and is both active and default. Retired init/runtime options are not accepted:
ordinary runtime policy remains with current System ownership, not new settings.

After validated hidden staging, stdout flushes one object:
`{"status":"ready_for_prerequisites","committed":false,"attempt_id":"...","preset_ref":"..."}`.
Go may now persist/verify the exact pending preset and credentials outside Core,
then send exactly:
`{"status":"prerequisites_acknowledged","attempt_id":"...","preset_ref":"...","accepted":true}`
(or false after prerequisite failure). Ack is attempt/ref-bound and secret-free.
EOF, malformed/mismatched ack, rejection or failed verification cannot publish.

### Writers, ordering and publication

1. Refuse any existing final `.lingtai` entry, including symlink/file/empty
   directory, before writes. Validate approved destination and pending shape
   with the supplied `options.env_file`, also preserved by the seed writer.
   Current init-owner `ValueError` becomes static `init_preflight_failed` with
   no ready object, staging/Recipe write, or prerequisite persistence.
2. Create sibling hidden `.lingtai.create-*` and write a private nonce marker;
   staging directory identity and matching regular marker prove cleanup ownership.
   Build human null-admin plus one privileged orchestrator, mailboxes/contacts,
   shared library, current init and Psyche owner document, optional greet,
   behavioral Recipe snapshot and existing Recipe UI selection file.
3. A valid project-local `.recipe/recipe.json` wins without rewriting its bytes.
   Otherwise materialize wheel-owned adaptive resources. Locale layers try the
   selected language then root. Root `.recipe` baseline writes can survive a
   later failure: **only final `.lingtai` publication is atomic**, not the whole
   project root or external dependency persistence.
4. Recipe comment seeds current Pad (`options.pad` if nonempty wins); no retired
   `comment_file` is written. Covenant selects its project-local file through
   Psyche's public serializer, otherwise caller covenant. `base_prompt` remains
   its distinct Psyche input. Kernel-owned procedures remain kernel-owned:
   Parsed local bundles are never replaced just because a declared library is
   unsupported: an escaping/non-segment library fails with local bytes retained.
   Recipe procedures are preserved in the snapshot, not emitted as inert init
   overrides. Library paths append to canonical default-on skills, unless explicitly false; legacy init retains its already-present-skills gate.
   Greet substitutes time, human address, language and outer location/commands;
   no location network lookup or runtime invocation occurs here.
5. Read actual staged membership and current init/Psyche seams with an explicit
   pending-snapshot loader, then ask outer prerequisites. After accepted ack,
   **load the real exact preset from disk**, compare its normalized manifest to
   the reviewed snapshot and repeat readers. The pending loader is never used
   to claim a persisted dependency exists.
6. Publish with native destination-must-not-exist rename (Darwin `RENAME_EXCL`,
   Linux `RENAME_NOREPLACE`, Windows non-replacing rename). Unsupported primitive
   fails closed; no `exists`-then-overwriting-rename fallback. This is atomic
   visibility, not a power-loss/fsync durability guarantee or process lease.
   Marker removal after known publication is best-effort and becomes a warning.

### Outcomes and caller behavior

Exit 0 terminal stdout is `status:created,committed:true,attempt_id,agent_name,
directory_name,preset_ref,warnings`. With a ready object preceding it, exactly
two stdout objects are expected. No-ready failures have no success stdout;
after-ready failures have only that ready object. Known failure is exit 1 with
one stderr object `status:error,committed:false,code,error`; arbitrary exception
text and input documents are never emitted. Errors distinguish invalid request,
root/preset/exchange, unresolved process evidence, rejected/mismatched ack,
staging/read/build/Recipe/persistence/publication failure and unresolved owned
cleanup. Unproved/failed cleanup retains staging and reports
`owned_staging_cleanup_unresolved`, not a claim of empty root.

Known uncommitted means this attempt did not publish; a concurrent foreign
final target may exist and MUST NOT be deleted.

Go MUST validate expected identities/ref/attempt, envelopes and child exit;
start failure, timeout/cancellation, signal, broken transport, unexpected output
or exit are **unknown**, not committed-false proof. Never auto-retry, delete a
final tree or sweep staging based on unknown transport. After known publication,
Go UI preferences/registration/launch run separately and may produce
committed-with-warnings, never rollback. No credentials or prerequisite writes
are rolled back by Core, even when later publication fails. Existing narrow
headless creation, run and inspection remain unchanged.

## Existing Project configuration (candidate slice)

Guarded by [PRJ003](BEHAVIORS.md#behavior-prj003). `configuration.py` owns
`ProjectConfigurationUseCase`, immutable requests/seeds/receipts, skeleton
identity/layout/early-return, merge, authorization, selected-member propagation
and all-or-none imported initialization. `ProjectConfigurationPort` is bound to
one Project; its filesystem adapter supplies sequential raw document evidence,
individual file writes and actual current init/Psyche readers. No runtime,
process inventory, credentials, registry or Recipe reconciliation is involved.
Core imports neither these owner implementations nor filesystem/OS APIs.

### JSON operations and accepted envelopes

These five implemented commands add no flags. Send one EOF-delimited UTF-8 JSON
object on stdin, at most 1 MiB characters, no duplicates/nonfinite numbers or
credential-shaped keys. The caller MUST project public metadata/reference
inputs, not serialize a whole draft, credential map or pending API key/OAuth.
Arbitrary user text is not a secret detector. `project_dir` is the **parent
root**, not `.lingtai`; existing root required. Subdirectories and nonregular
write targets are refused rather than followed.

- `project initialize`: exactly `{"project_dir":"ROOT"}`.
- `project configure` and `project rehydrate`: required `project_dir`,
  `directory_name`, `agent_name`, `preset_ref`, `keep_current`, `options`; optional
  `prompts`. Directory is one safe non-dot/non-human segment; display identity
  is independent. `keep_current` is a literal boolean. With false, `preset_ref`
  is an exact persisted absolute or `~/` JSON/JSONC reference, loaded through
  the real wrapper owner (no preset snapshot on stdin). With true, it MUST be
  empty and `options.preserve_active` true; existing init supplies public LLM,
  capabilities and real default/active refs. Missing real refs are refused;
  no `keep_current.json` synthetic path is manufactured.
- `project propagate-policy`: exactly `project_dir`, `directory_name` (the
  already-configured member to skip), `default_ref` (nonempty string),
  `allowed_presets` (nonempty-string array containing default). Go may read the
  selected saved init's policy to supply these authoritative values; it MUST NOT
  independently reconcile/re-authorize active/default/allowed.
- `project propagate-config`: exactly `project_dir`, `directory_name` (selected
  source member, not a path or fleet target).

`options` is closed. Required `env_file`, `venv_path` are explicit public path
strings (may be empty only when the current owner allows that shape); existing
nonempty values win. Optional strings `language` (default en), `pad`; literal
booleans `karma` (true), `nirvana` (false), `preserve_active` (false); arrays of
nonempty strings `addons`, `allowed_presets`; object `mcp` is an outer-generated
public activation-spec seed, **not secrets/config contents**. Existing MCP
entries win. Fresh canonical output uses explicit mcp selection, not addons;
Go may keep its curated module/config-reference presentation catalog. Optional integer legacy Go
writer inputs `context_limit` (500000), `max_rpm` (60), `max_aed_attempts` (5)
remain accepted compatibility request data but are not written into canonical
init or used as runtime settings. Existing legacy values are preserved in place.
No System policy or defaults change.

`prompts`, when present, is a closed string-only partial edit of `base_prompt`,
`base_prompt_file`, `covenant`, `covenant_file`. Composition merges the existing
strict Psyche owner values with the edit and calls its public serializer. Core
receives opaque bytes. When absent, existing owner bytes/settings routes stay
untouched. Explicit Pad is typed creation-seed data, not a saved init field. Go's
legacy CovenantFile/CommentFile choices map to `prompts.covenant_file` and Pad
content supplied after its selected public Recipe layer read. The inherited Go
writer's supported legacy `init.covenant_file` path also remains: optional public
option strings `covenant_file` (explicit nonempty choice) and
`default_covenant_file` (outer language-default path) restore its exact precedence,
explicit choice then existing nonempty path then default. Omitted fallbacks do
not invent a path. This metadata does not activate legacy init prompt loading or
rewrite Psyche; only an explicit `prompts` edit writes that owner document.
This operation itself does not apply a Recipe.

All terminal envelopes have exactly `status,outcome,updated,skipped,failed,code,
error,source`. `source` has `init_written,manifest_written,psyche_written,
reader_status`. Success exits 0, one stdout object, empty stderr; status is
`initialized`, `configured`, `policy_propagated`, `config_propagated` or
`rehydrated`; code/error empty; outcome `complete` or `unchanged`. Known failure
exits 1, no stdout, one stderr object, status `error`, static code/error and
outcome `rejected` (no operation write) or `partial`. Unexpected driver failure
is `unknown`. `updated` counts init writes (source included for rehydration),
not whole-document transactions or runtime readiness. Skeleton updated counts
human manifest writes. `failed` counts member failures, skipped counts no-ops.
Only actual source reader status is exposed: `not_read`, `READ_FAILED`,
`FULLY_EFFECTIVE`, `READ_OK_WITH_IGNORED_FIELDS`; never manufacture effectiveness.
Config propagation and rehydration readers are enforced per write but not
flattened into source status. Policy-only propagation does not reconstruct a
runtime or read unrelated owner settings; its receipts remain `not_read`.

### Compatibility, ordering and partial effects

Initialization preserves the old stat-success human-manifest early return,
including malformed content: no helper/mailbox repair on that branch. Otherwise
it writes inbox/sent/archive, null-admin human manifest, contacts `[]`, then
`.tui-asset` and `.library_shared`. This is idempotent skeleton initialization,
not exclusive fresh creation or imported initialization.

Configure preserves root/manifest unknown fields, exact integer values and
kernel-owned blueprint identity/history (agent_id, created_at, molt_count,
nickname, custom state and settings); only wizard name/address/admin overwrite
blueprint fields. Existing Pad/env/venv/addons/MCP win; typed Pad seeds fill absent/empty memory
only. The selected preset updates reference policy only; authored tools and
kwargs remain unchanged. Legacy addon dictionaries retain their inherited
identifier-filtered explicit conversion/selected-MCP-seed behavior. After Core's
conversion and explicit-request seeding, the filesystem adapter fills only missing
selected legacy curated entries using bundled launcher metadata and the inherited
public config references. Existing MCP kwargs win, then explicit request kwargs;
unknown addons remain unseeded unless explicitly supplied. List order and sorted
identifier-filtered dictionary order are unchanged. The original request venv supplies
the fallback interpreter path, without replacing the preserved init host venv.
Canonical/fresh empty selection never consults this
compatibility fallback, and unrelated bundled accounts remain absent. Guarded by
`tests/test_project_configuration.py::test_configure_legacy_addon_catalog_seeds_at_supported_entry`.
Current owner canonicalizes legacy capability aliases
only at existing explicit-write seams, and inherited retired top-field stripping
(principle_file, procedures_file) remains legacy-only. Unknown preset-block
fields retain the existing Go generator's replacement behavior on configure;
policy propagation instead edits only active/default/allowed in the existing
block. Active survives when still authorized. Nonempty explicit allowed revokes
an excluded active to default; empty/omitted allowed preserves prior setup
allowed and adds real default/active as before. Malformed selected documents are
refused rather than erased (intentional fail-loud adapter boundary).

Configured source ordering is actual owner preflight, init write, mailboxes,
blueprint write, optional opaque Psyche write, optional absent/empty Pad seed,
then actual readers. A missing
required env_file fails preflight without any operation write. Individual files
are replaced atomically, but this is **not a multi-document transaction**. Later
manifest/Psyche/reader failure retains earlier writes and receipts; there is no
rollback/retry. Owner-file validation or absent external references can fail
postwrite and must remain visible as partial. Neither globals, credentials,
mailbox contents nor unrelated owner documents are rewritten.

Policy propagation iterates immediate real directories, excluding human and
selected member, as old Go's init-based target semantics (no extra fleet/member
manifest gate). Missing init or missing preset block is skipped; malformed/read
or write failure is counted, traversal continues. Its specialized
`save_policy_init` Port operation atomically persists the Core-authored edit,
without whole-init preflight or init/Psyche readers: the old supported input is
any parsed init with a preset block, even without Pad, env, blueprint, loadable
preset references or valid unrelated owner settings. This is authorization
maintenance, not proof of runtime readiness; runtime validation is unchanged.
Config propagation copies nonempty env_file and source INIT-owned tools, strips
worker admin to false karma/nirvana and deletes legacy top-level addons. It MUST
NOT change an existing target model policy: active/default/allowed, revocations
and opaque fields remain authoritative. Canonical reference-bound targets retain
their entire `llm` owner exactly; reference-only sources never pop or replace a
legacy target model owner. Legacy inline sources retain the historical raw-LLM
copy beside a legacy target policy, which remains the effective model source.
Only targets without an existing policy may receive the source model source.
Use explicit `propagate-policy` for authorization changes; config copying never
smuggles a model switch through an existing policy. Guarded by
[PRJ011](BEHAVIORS.md#behavior-prj011) and supported-API propagation tests.
Target plugin roots, identity, Pad/venv/MCP/other settings remain unchanged; both
owner shapes are supported. Unreadable/absent or unclassifiable init is skipped,
write failures do not stop later targets, and partial receipts remain explicit.
No copied memory or System runtime policy expansion is implied.

Rehydrate MUST replace the old **configure-source then RehydrateNetwork** pair,
not run after Go has already created source init. It observes the full blueprint
membership first (non-dot real directories, non-null admin; human excluded).
All-present returns unchanged without writes; mixed is refused before writes;
all-absent configures the selected source and then initializes workers in sorted
order. Source display identity comes from its existing blueprint, fallback to
directory. Workers copy source init, use their own blueprint display identity,
admin `{}`, remove addons and retain existing blueprints/settings/Psyche routes.
Only init is copied to workers, as before; this is not a new owner-settings
propagation operation. First worker failure stops with exact preceding-write
count; initialized workers are never overwritten, including an absent-to-present
race (exclusive absent-only publication). No automatic mixed-state recovery or
resume is provided. Source prerequisites/preset persistence stay Go-owned and
must precede this operation; this is not the fresh-creation handshake.

Go MUST validate complete envelopes and exit status. Start failure, unsupported
command, cancellation/timeout/signal, broken output, extra objects or unexpected
exit are unknown, never evidence of rollback or safe retry. Do not auto-retry,
delete/repair state, fall back to Go mutation policy, install, register or launch
based on a failure. UI preferences/Recipe/lifecycle work remain separately owned.
Full Project migration still requires Recipe reconcile and lifecycle slices.

Static failure codes are `invalid_project_request`, `credentials_must_remain_outer`,
`invalid_project_root`, `invalid_member_directory`, `nonregular_project_document`,
`invalid_preset`, `existing_document_unreadable`, `keep_current_reference_missing`,
`source_document_unreadable`, `member_document_unreadable`,
`project_observation_failed`, `blueprint_unreadable`, `rehydration_source_missing`,
`mixed_initialization_state`, `skeleton_initialization_failed`,
`configuration_preflight_failed`, `init_write_failed`, `manifest_write_failed`,
`psyche_write_failed`, `pad_seed_failed`, `configuration_reader_failed`, `configuration_request_failed`.
Current-reader diagnostic logging is disabled only within this short-lived JSON
handler and restored afterward, preventing arbitrary unknown-field names from
escaping into its protocol. Newly allocated per-write temporary files alone are
removed best-effort; failed temp removal can retain them and never changes a
known written receipt into unwritten. No prefix sweep or later cleanup occurs.

Interactive creation's selected-preset loader locally suppresses only
`lingtai.kernel.presets.log` during the actual load and restores its prior
`disabled` value in `finally`. Ordinary legacy root-context normalization must
not corrupt the JSON exchange or disclose preset paths/values on stderr. Other
commands and Agent logging are unchanged; persisted mismatch still yields the
single static error envelope after ready, with no diagnostic prefix. Authored
actual-CLI regressions are in `tests/test_project_interactive_diagnostics.py`;
parent-owned execution remains required (PRJ002).

## Existing local Recipe observation/apply (candidate slice)

Guarded by [PRJ004](BEHAVIORS.md#behavior-prj004). `recipe.py` owns typed
`ProjectRecipePort`, observation policy and ordered apply; a standalone existing
filesystem adapter supplies raw evidence and specialized writes. No creation
attempt, bootstrap, fallback bundle or hidden fresh-creation workspace exists.

Proposed no-flag commands: `project recipe-observe` accepts exactly
`{"project_dir":"ROOT"}`; `project recipe-apply` accepts exactly `project_dir`,
`language` (string), `rendering` (null for identity or a closed string object
with `time,address,location,commands`). Go resolves these public observations
using the actual existing greet callback, including local timestamp format,
location resolution/unknown fallback and translated command list. Core performs
ordered literal replacements time, addr, lang, location, commands; unknown and
retired tokens remain literal. No network, clock or palette call lives in Core.

Observe returns exactly `status:recipe_observed,state,needs_apply,code,error`.
States are absent/equal/changed/read_failure. Empty/absent/non-directory selected
Recipe means no apply; absent/non-directory snapshot means changed; otherwise
compare relative file sets and bytes, ignoring directories/mode/mtime. Selected
stat failure retains read_failure with needs_apply false; comparison read failure
retains read_failure with needs_apply true (legacy booleans, not success proof).

Apply returns exactly `status,outcome,members,snapshot,greet_status,applied,
legacy_applied,failed,code,error`. Each member has exactly `directory_name,
prompt_written,init_written,reader_status,code,legacy_counted,prompt_code,init_code`; snapshot exactly
`attempted,removal_succeeded,copied,code`. `applied` counts actually error-free
processed members; `legacy_applied` explicitly exposes the old first-error-gated
count quirk, NOT successful application. First prompt/skills error skips remaining
work for that member; later write errors still permit following work/counting as
old Go did, but remain visible per member. All eligible immediate real nondot
nonhuman directories are processed, no manifest membership gate. Optional greet
missing/unreadable/empty is skipped and disclosed, not manufactured fallback.
Library path is additive only when existing skills exists; old paths, unknown
fields, exact integers and preset active/default/allowed remain unchanged.
Current public capability canonicalizer is outer-owned; only on an actual rewrite
are principle_file/procedures_file removed. Current init/Psyche readers report
actual postwrite status; reader failure is partial, not rollback.

**Snapshot ALWAYS attempted after member traversal, even partial failure.**
Best-effort removal then additive copy retains the existing Go meaning, NOT a
transaction or proof every member succeeded. Removal failure is independently
visible even if copy succeeds; the copied snapshot can make next observation
equal after partial application. Never auto-retry, rollback or cleanup. Invalid
Recipe or failed enumeration before traversal produces no snapshot attempt.

Existing Recipe preserves accepted declared library/language strings and Go
`filepath.Join` lexical cleaning, including non-segment/parent/absolute-looking
strings. The fresh interactive creation safe-sibling rule is unchanged and does
NOT govern this existing apply slice. Adapter is bound to the explicit caller
root; that authority is scoped Recipe application, not general runtime permission.
No new root/member/nonregular/symlink refusal is imposed. Prompt writes follow
existing targets; init reads follow links but fixed `.tmp` + rename replaces the
init entry; snapshot writes retain old ancestor/file-link behavior. Natural I/O
errors retain old first-error/count policy. Prompt failure may retain truncated/
partial bytes; snapshot copy failure may retain a partial tree. No stronger
full-success snapshot semantics or launch gate is authorized by this migration. Strict one-object
stdin and static envelopes follow existing five configuration operations; no raw
paths/documents/exception text or reader logs are emitted. Unknown transport
never proves no writes. Bundle copy/materialization/import, behavioral-layer
setup beyond greet, lifecycle/registry/readiness and Go callsites remain separate
bounded remainders; this is not FULL A or fullProject completion.

Final authored static failure vocabulary: `invalid_project_request`,
`credentials_must_remain_outer`, `invalid_recipe`,
`recipe_enumeration_failed`, `recipe_prompt_write_failed`,
`recipe_skills_read_failed`, `recipe_init_write_failed`, `recipe_reader_failed`,
`recipe_snapshot_copy_failed`, `recipe_request_failed`.
Top-level code/error and child exit preserve the old ApplyRecipe first-error
semantics. Exit 0, stdout `recipe_applied`, code/error empty can carry outcome
partial when current postwrite readers alone failed; this additional receipt is
NOT a new launch-blocking error. Exit 1, stderr `error`, retains the old actual
prompt/init/enumeration/metadata/copy failures, or rejected/unknown transport
request failure, with every receipt field present. Go callers use `legacy_applied`
for the old returned count and top-level code for existing error/warning behavior,
not truthful new applied/member evidence to silently strengthen policy. Snapshot
removal failure alone remains ignored for caller error/exit when copy succeeds,
though `removal_succeeded:false` remains explicit evidence. Observation
read_failure is exit 0 evidence with code/error empty and the old boolean; request/
driver failures instead exit 1 with status error, read_failure state, false boolean
and static code/error (never use that false boolean to claim safe retry). Both
commands reject duplicate/nonfinite/extra/credential-shaped requests and use the
existing 1 MiB character bound. Reader logging is suppressed only during this
short-lived command handler and restored afterward. No automatic cleanup/retry.
An init write preserves fixed `init.json.tmp` + rename semantics: existing temp
write targets are followed/truncated, no temp cleanup after write error, and only
old best-effort temp unlink on rename error. Caller changes racing sequential
read/write can be overwritten, as before. No snapshot/read consistency
or concurrency lease is claimed. Adapter supplies native relative path spelling
for exact legacy path equality on POSIX/Windows; Core owns the append decision.
Reader failure is real partial evidence but does not alter the legacy write count.


### Synchronous existing-member rendering extension

Guarded by [PRJ004](BEHAVIORS.md#behavior-prj004). The SAME no-flag
`project recipe-apply` additionally accepts one newline-terminated closed object
`{project_dir,lingtai_dir,language,rendering_mode}` with nonempty independent
bundle/destination strings and mode `callback` or `identity`. A complete first
line containing `rendering_mode` selects this new form; valid legacy requests
never contain that key and retain EOF/multiline parsing and the prior 1 MiB
character bound. New-form request, callback frames and terminal output use growing
line I/O without that cap. No new path refusal/canonicalization is introduced.

Core owns `RecipeGreetRenderingPort.render(RecipeMemberGreet) -> bytes`.
The immutable member value binds sequence (starting at 1), lossless directory
identity bytes and original template bytes. First metadata load, greet read ONCE,
conditional library helper SECOND metadata load (failure means empty library),
and directory enumeration ONCE precede traversal. Original nonempty readable
greet invokes the Port exactly once immediately before each prompt write;
returned EMPTY bytes still truncate/create. Identity stays in Core, no exchange.
Each preceding prompt/skills phase completes before the next rendering call.
No batching, template reread, enumeration reread, duplicate call or retry occurs.

Callback stdout emits and flushes exactly
`{status:render_recipe_member,sequence,directory_name_b64,template_b64,template_bytes}`;
stdin ack is exactly
`{status:recipe_member_rendered,sequence,rendered_b64,rendered_bytes}`.
Base64 is canonical RFC4648 padded standard alphabet; counts are decoded BYTES,
sequence/count integers exclude bool. Only the pending sequence is accepted.
Arbitrary template/result bytes (NUL, invalid UTF-8, empty, >1 MiB) are not text
converted. Name identity is adapter-supplied native filesystem bytes, not UTF-8
replacement. Interactive terminal member receipts supplement legacy fields with
`directory_name_b64`; escaped display names are nonauthoritative. The old static
terminal fields remain unchanged. Interactive normal receipts additionally carry
`legacy_failure` separately from reader diagnostics: factual phase/operation,
base64 native path, errno/winerror/JSON byte offset when known, never exception
text. This is NOT proof of exact Go error-string/type or mixed-OS decoder parity.

EOF, malformed/mismatched ack or broken exchange raises specialized
`RecipeRenderingStopped`; Core raises `RecipeExchangeStopped` with completed
receipts, first actual legacy failure, and pending member/sequence. NO pending
prompt/skills or snapshot is attempted. Prior writes remain. CLI exit 1 stderr
has `status:recipe_exchange_stopped`, `code:recipe_exchange_failed`,
`outcome:partial` when known preceding writes exist, otherwise `rejected`, and
`pending_sequence,pending_directory_name_b64,legacy_code,legacy_failure` plus
usual receipt fields. Rejected here describes this pending operation only, NOT a
zero-physical-write claim about callbacks or foreign mutations. Lost output is
unknown, never normal application, rollback or automatic retry. Go must preserve
original callback panic/Goexit, close input and reap the child while unwinding;
no kernel finally-snapshot, timeout, supervisor or fallback mutation is added.
Normal first-error/count and reader-only/removal-only semantics remain unchanged.

## Recipe bundle materialization

Guarded by [PRJ007](BEHAVIORS.md#behavior-prj007). `bundle.py` owns closed
`DiskBundleCopy`, `PackagedBundleCopy`, `WizardBundleStage`, `BundleMetadata`,
`RecipeBundlePort`, `RecipeBundleResourcesPort` and `RecipeBundleUseCase`.
Core owns empty/empty-name validity, local/global/packaged preference, metadata
before lexical-location noop, and recipe replacement then optional library then
optional imported network merge/stop. Native path, metadata decoding and physical
I/O mechanisms stay outside Core. No generic filesystem/RPC capability is exposed.

The actual no-flag `project recipe-materialize` uses one newline-terminated closed
request: disk `{kind:"disk",source_b64,destination_b64}`, packaged
`{kind:"packaged",recipe_name_b64,destination_b64}`, or wizard
`{kind:"wizard",recipe_name_b64,destination_b64,global_dir_b64}`. All base64 is
canonical padded standard alphabet of original native Go string bytes, including
empty; no Unicode/path normalization or new filename confinement. The adapter is
construction-bound to these roots. It never stats/reads/copies caller paths in
Python. The wizard's destination is the original Go `filepath.Dir(lingtaiDir)`.
No new line/output cap, deadline, retry, thread or process-control surface is added.

Each flushed stdout frame has exactly status `bundle_io`, binding (the exact
request), integer sequence starting1, operation, arguments. Ack has exactly status
`bundle_io_result`, matching sequence/operation, result. Duplicates, nonfinite,
extra fields, bool sequences, wrong enums/types/base64 and incomplete lines stop
the exchange. Operations are closed specialized phases, never caller commands:

| operation | arguments | normal result |
|---|---|---|
| observe_local_recipe | `{}` | `{kind:K}` |
| find_global | `{}` | `{found:bool}` |
| load_metadata | `{source:S}` | `{name_b64,library_b64:null|string}` |
| absolute_locations | `{source:S}` | `{source_b64:null|string,destination_b64:null|string}` |
| remove_recipe | `{}` | `{ok:true}` |
| copy_recipe | `{source:S}` | `{ok:true}` |
| observe_library / copy_library | `{source:S,library_b64}` | `{kind:K}` / `{ok:true}` |
| observe_network / copy_network | `{source:S}` | `{kind:K}` / `{ok:true}` |
| packaged_candidates | `{}` | `{roots_b64:[r1,r2,r3]}` |
| decode_packaged_metadata | `{root_b64,content_b64:null|string}` | `{name_b64,library_b64:null|string}` |
| prepare_packaged_file | `{relative_b64}` | `{ok:true}` |
| write_packaged_file | `{relative_b64,content_b64}` | `{ok:true}` |

K is directory/absent/not_directory/stat_failed. S is explicit/local/global,
per the bound request and previous evidence. Metadata operations and physical
remove/copy/prepare/write additionally allow exactly `{failed:true}`. The actual
original Go error is retained locally under sequence+operation+bound arguments;
Core receives only the opaque reference. No errno/text-based error reconstruction.
Full native RecipeInfo struct decoding preserves null/duplicates/casefold/type
errors and runtime tolerance. Core alone checks empty decoded name. Both Abs calls
run after valid metadata; null means failed Abs, not refusal. Only two successful
equal locations noop. Optional source Stat failure/notdir/absence is tolerated.

Wizard valid local `.recipe` wins even with empty requested name; invalid local
falls through to one original RecipeDir lookup. Found malformed global fails,
never packaged fallback. Disk RemoveAll(.recipe) precedes native copyTree, then
nonempty declared library Stat/optional additive copy, then .lingtai Stat/optional
additive copy. First failure stops; preceding destruction/truncation/writes stay.
Native copyTree/copyFile preserve original sorted depth-first order, source/target
symlink/nonregular behavior, modes, Open-before-parent/truncate/copy/close priority.
No stat duplication, prevalidation, safe-path filter or transaction is authorized.

Packaged candidates are native path.Join results for recommended/intrinsic/examples
in that order, not native resource lookups. Core picks first existing installed
catalog directory (including lexical parent/root cases). Native metadata decoding
receives exact kernel-owned recipe.json bytes and original io/fs source label;
null is statically absent catalog metadata, realized as native io/fs missing-file
error, not translated Python failure. After metadata validation, remove .recipe,
then each depth-first filename-sorted member: native parent MkdirAll0755 ACK,
installed resource read, native WriteFile0644 ACK. Existing file modes persist;
empty directories are not materialized. Adaptive/plain/greeter/tutorial ship cold
under non-dot package paths; Markdown developer frontmatter is stripped to exact
operational prompt bytes, not raw-source export. Fresh creation remains unchanged.
Unexpected installed resource read failure is unknown, never a fabricated Go error.

Every accepted domain outcome uses ONE final stdout object, exit0, empty stderr:
`{status:"bundle_materialized",binding,source,outcome,code,failure,transcript}`.
source is explicit/local/global/packaged; complete/unchanged have empty code and
null failure. rejected has empty_paths/packaged_not_found/source_empty_name and
null failure. failed has source_invalid/remove_recipe_failed/copy_recipe_failed/
copy_library_failed/copy_network_failed/packaged_create_failed/packaged_write_failed
and exact `{sequence,operation}` of the actual failed acknowledgement. Transcript
is every accepted ack projected as `{sequence,operation,result}` in order,
including tolerated local metadata failures. Exit0 alone is NOT successful copy.
Go must validate full closed binding/frames/arguments/transcript/terminal,
source/code/failure consistency, child exit0 and empty stderr BEFORE using it.
Only then wrap the ORIGINAL retained error with the original CopyBundle or
CopyEmbeddedBundle phase wording and `%w`; empty-name uses the original non-native
message/source label. Wizard wraps the chosen copy error once as copyRecipeBundle.

Malformed requests, stopped exchanges and driver failures attempt one static
stderr `{status:"error",code,error}` and exit1, no normal terminal. Codes are
invalid_bundle_request/invalid_bundle_exchange/bundle_driver_failed; error is
`Recipe bundle exchange did not complete`. Lost/extra/forged terminal, broken
transport, signal/cancellation/abnormal exit remain UNKNOWN partial effects.
No replay, fallback writer, rollback, repair or cleanup is permitted; only existing
direct-child close/reap/cancellation contracts apply. Native Go errors, installed
bytes and real wizard/headless callers require paired acceptance. Existing
readiness/config/apply/callback/snapshot/legacy terminal promises are unchanged.
Procedure: [Project manual](../../../../docs/references/project-create.md#materialize-a-recipe-bundle-native-driver).

## Running inventory and selection

`inventory.py` owns duplicate selection, role/enterability precedence, human
inclusion, stable ordering and grouping from typed `InventoryObservation`. Native
Go still observes processes, byte identities, manifests/status/heartbeat/lock and
collation keys; Core neither scans processes nor reparses owner documents. The
selected decision is bound to original row, directory bytes and PID. CLI list,
ProjectsModel and its embedded launcher consume the same query; Enter rescans
and revalidates `(AgentDir, PID)`. Broken/phantom/non-admin rows remain visible
and disabled, stale heartbeat never hides a process, and scan/query failure is
not empty success. `Snapshot.Err` is mandatory for Snapshot-only callers.

## Native Recipe owner documents

The Go-driven native variant retains exact Go metadata decoding and opaque init
owner documents locally. Core owns name acceptance, path append/no-op and member
ordering; strings/opaque entry identities cross the specialized Port, never
unknown/deep owner subtrees. Native writes preserve original numeric tokens,
canonicalization, temp/rename and first-error/partial-write semantics. Old raw
CLI/rendering callback ABI stays valid. Native written members explicitly use
`reader_status=not_read_native`: this is NOT a current Python init read,
FULLY_EFFECTIVE claim or readiness result. Standalone raw-mode diagnostics remain
unchanged. Comment/covenant selection is locale then root by native stat evidence;
first successful non-directory selection is irrevocable even if later unreadable.
Unknown transport must stop live save before configure, never imply absent/default.

## External Project lifecycle

`external_lifecycle.py` owns external sleep/suspend/all targeting, forceful
refresh, revive/CPR, temporary clear, CLI clean, nirvana and preset-candidate
selection over `ExternalLifecyclePort`. Go retains native handles, paths, exact
errors, locks/Puffo ownership checks, config codecs, process effects/watchdog,
UI generation guards and the existing writer-drain barrier. The CLI is only a
bound synchronous exchange: no concrete process/filesystem adapter is installed
inside Python. Native effects are once-only and action-bound; full terminal,
transcript, EOF and child status must validate before native failure restoration.
Unknown results never authorize replay, fallback, rollback, repair, re-init or
wizard continuation. Native best-effort errors remain best-effort.

Force refresh keeps suspend/lock wait/native termination/exact sweep/preset/force
launch/defensive suspend removal/watchdog order. CPR is normal duplicate-protected
launch. Temporary clear keeps after-revive capture/write/wait/suspend and error
precedence. CLI clean suspends then rediscover-unions and checks survivors; force
bypasses only the old discovery/survivor refusals, not confirmation/suspension.
Nirvana is DISTINCT: sequential native suspension, exact writer barrier, ignored
native removal errors, then UI-owned reset only after known completion. Its
unknown failure remains visible without false Done. Existing kernel self-refresh
and signal-consumer/watcher ownership is not duplicated or redefined.
