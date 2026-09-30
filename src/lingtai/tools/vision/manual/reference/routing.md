---
name: vision-routing-reference
tool: vision
related_files:
  - src/lingtai/tools/vision/manual/SKILL.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/CONTRACT.md
  - src/lingtai/tools/vision/BEHAVIORS.md
maintenance: |
  Keep routing and authorization aligned with the Vision resolver. Alternatives
  remain explicit instructions: never add provider, model, credential, preset,
  MCP, or CLI fallback.
---
# Vision routing reference

## Default route

With no `preset`, Vision uses the configured Vision service or the active
provider's own family:

- `openai` — OpenAI-compatible vision on the active service's *effective*
  endpoint (the configured `base_url`, else the official
  `https://api.openai.com/v1`), with the same key, model, headers, and wire
  (`wire_api: responses` selects Responses; otherwise Chat Completions).
- `anthropic` — Anthropic Messages vision on the effective endpoint with the
  same key, model, and headers.
- `codex` — Codex Responses vision with the active OAuth account (below).
- `claude-code` — manual-only `claude -p` guidance (below).

An explicit `capabilities.vision` may instead select `openai` or `anthropic`
(with `base_url`, `api_key`/`api_key_env`, `model`, and for `openai`
`wire_api`), `codex` (with `model`, `token_path`), or the local `local`/`mlx`
routes. The active credential is only ever sent to the active effective
endpoint: a vision `base_url` that differs from it needs its own
`api_key`/`api_key_env`. Any other provider name — for example a vendor that is
now reached through `openai`/`anthropic` with a `base_url` — is manual-only.
If the endpoint or model cannot do vision, the request simply fails. An
unsupported or incomplete identity fails closed with sanitized guidance. There
is no hidden model, legacy credential, provider switch, or automatic
MCP/provider fallback.

The Codex provider (`codex`) uses one OAuth identity: an explicit `token_path`,
else the active `codex_auth_path`, else (active Codex service only) the default
`codex-auth.json`. An unrelated active provider cannot lend its model, endpoint,
or credential.
Unsupported wires remain manual-only.

## Borrow one authorized route

A non-null preset is explicit one-call borrowing, never fallback:

1. `vision(action="list", input={}, reasoning="...")` shows active and allowed
   route declarations without constructing a service or reading credentials.
2. `vision(action="check", input={"preset": "<allowed reference>"}, reasoning="...")`
   resolves the borrowed provider/model without sending an image. Construction
   may resolve that preset's own credential.
3. `vision(action="analyze", input={"image_path": "...", "question": null,
   "preset": "<allowed reference>"}, reasoning="...")` sends one image through
   that selected service.

The reference must be present in `manifest.preset.allowed`; Vision loads it
read-only and uses its own `manifest.llm` plus `manifest.capabilities.vision`
identity. It cannot switch the active preset, lend active credentials, or invoke
another provider after failure. An unlisted, unreadable, or incomplete preset
fails closed. Ask the human before changing authorization or configuration.

## Claude route

Claude-family providers (`claude-code` and the vision-only `claude-p` alias) are
manual-only for Vision. Run the operator's explicit CLI action, for example:

```text
claude -p "Analyze this image: /path/to/image.png"
```

The CLI reads the referenced file and uses its own authentication and cost model;
Vision never proxies that authentication or invokes the command. JPEG, PNG, and
GIF (first frame) are documented CLI inputs. Consult the official
[CLI reference](https://code.claude.com/docs/en/cli-reference) and
[image workflows](https://code.claude.com/docs/en/common-workflows) for current
limits; an optional MCP or other skill is likewise an explicit action.

## Provider-specific method and safety

Use the `skills` catalog to find the current preset/provider manual; if none is
installed, report that no discoverable method is available. A route failure is
not permission to edit files, install a backend, or retry a non-idempotent action.
Never request or print API keys, OAuth tokens, environment values, headers, or
unsanitized URLs. Missing fields remain unknown; do not guess them.
