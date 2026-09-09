---
name: vision-settings-reference
tool: vision
related_files:
  - src/lingtai/tools/vision/manual/SKILL.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/settings.py
  - src/lingtai/tools/vision/CONTRACT.md
maintenance: |
  Keep the thirteen setting anchors in the router stable. Describe only the
  applied, read-only snapshot and existing owner procedures; never add a writer
  or imply provider/credential fallback.
---
# Vision settings reference

## Applied snapshot

Rows describe the values used when Vision binds at boot or after an authorized
refresh/relaunch. SHOW does not re-read files, environment, presets, or clients.
It returns no rows when route truth, required model, local settings, or an
opaque injected service has no attributable owner route; it never guesses or returns a partial snapshot.
`configurable: true` means an existing owner procedure can change a value, not
that SHOW can mutate it. Change through that owner, refresh/relaunch, then SHOW
again. Sensitive fields render both current and default as `<redacted>`, even when unused;
internal presence markers/nulls are not exposed.

## Setting: provider

Explicit Vision provider, otherwise a compatible active provider; no default or
automatic switch. Change the active capability/preset, refresh, and check.

## Setting: base-url

Explicit endpoint, same-provider endpoint, or local `settings/vision.json` then
`http://localhost:11434/v1`; route constructors own other defaults. Always redacted.

## Setting: model

Explicit capability, active route, local owner file, or constructed service.
Public identifiers remain visible; path-like values are redacted. Local requires
one; MLX owns its explicit-route default.

## Setting: api-key

Presence of applied credential only. It may come from explicit capability,
active route, or local owner file; local otherwise uses an SDK placeholder.
Codex/MLX do not consume this field; SHOW still redacts it.

## Setting: api-key-env

Presence of an explicit credential-variable pointer, resolved once before raw-key
fallback. Name and value are redacted; SHOW never reads or changes the environment.

## Setting: max-tokens

Positive response-token cap. Explicit capability wins, then local owner file;
service defaults are 1024 for API/local routes and 512 for MLX where applicable,
otherwise `null`. Gemini has no Vision-owned cap default.

## Setting: api-compat

Compatibility family from explicit Vision or active-provider defaults (`openai` or
`anthropic` where supported); irrelevant routes show `null`. It grants no access.

## Setting: wire-api

Effective wire: configured `auto`, `chat_completions`, or `responses` subject to
route support. Local/OpenAI-compatible routes normally use `chat_completions`,
Codex uses `responses`, and irrelevant routes show `null`.

## Setting: default-headers

Whether provider headers were applied. Explicit headers precede active defaults;
the mapping and names are always redacted, including when no headers apply.

## Setting: token-path

Whether a Codex OAuth identity path was applied from explicit value, active
`codex_auth_path`, or authorized pool selection. Non-Codex routes do not consume it;
SHOW still redacts path and token presence.

## Setting: instructions

Whether Codex Responses instructions were applied. Explicit text wins over the
Codex service default; non-Codex routes do not consume it, and SHOW still redacts it.

## Setting: max-output-tokens

Optional positive Codex Responses output cap; `null` omits it and is the default.
It is distinct from `max_tokens` and needs backend support.

## Setting: timeout

Positive finite Codex request timeout; explicit value wins and the default is
`120.0`. Non-Codex routes show `null`; a larger value grants no retry/network authority.

SHOW is read-only even when a row is configurable. Do not paste endpoints, keys,
OAuth paths, headers, or instructions into output; follow the owning capability,
preset, local-file, launcher, or Codex account procedure instead.

## Authorized change routes

- `provider`, `api_compat`, `wire_api`, `default_headers`, `instructions`,
  `max_output_tokens`, `timeout`: existing Vision capability/active-preset or
  same-provider configuration; verify backend support before editing.
- `base_url`, `model`, `max_tokens`: same owners; the local route additionally
  reads `settings/vision.json`, with capability values taking precedence.
- `api_key`, `api_key_env`: owner secret store/launcher and explicit capability
  pointer; `api_key_env` resolves before raw-key fallback. Never print either.
- `token_path`: existing Codex login/account-pool or explicit capability/preset
  procedure; never copy OAuth files into output.

After an authorized edit, refresh/relaunch and SHOW again. These routes grant no
new installation, credential, network, or retry authority.
