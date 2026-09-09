---
name: vision-manual
description: >
  Use the Vision schema directly for ordinary image analysis; route setup,
  borrowing, settings, and recovery to focused references.
last_changed_at: 2026-09-09T00:00:00Z
related_files:
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/ANATOMY.md
  - src/lingtai/tools/vision/CONTRACT.md
  - src/lingtai/tools/vision/BEHAVIORS.md
  - src/lingtai/tools/vision/settings.py
  - src/lingtai/tools/vision/manual/reference/actions.md
  - src/lingtai/tools/vision/manual/reference/routing.md
  - src/lingtai/tools/vision/manual/reference/settings.md
  - src/lingtai/tools/vision/manual/reference/backends.md
maintenance: |
  Keep this page a short provider-neutral router and the settings action
  read-only. Keep every setting anchor stable and route depth to focused
  references. Do not add provider, credential, endpoint, CLI, or MCP fallback;
  never import, expose, or link a secret.
---

# Vision manual

Routine calls use the schema directly; load only the reference needed for setup
or recovery. This installed manual reads no configuration and invokes no provider.

## First action

```text
vision(action="analyze", input={"image_path": "...", "question": null}, reasoning="...")
```

One existing image per call; relative paths use the workdir and null selects the
default image prompt. PNG/JPEG/WebP/GIF have known MIME types, not a guarantee of
provider support. Success is `{"status": "ok", "analysis": text}`; failures are
structured errors. The schema owns all five actions and strict input fields.

## Choose depth

- [Actions and results](reference/actions.md): `check` resolves a route without
  an image (not live model acceptance); `list` reads declarations without service
  or credential construction; `settings` shows the applied bind snapshot.
- [Routing](reference/routing.md): allowed-preset borrowing, Claude CLI, recovery.
- [Backends](reference/backends.md): local server/model setup, MLX, troubleshooting.
- [Settings](reference/settings.md): exact source/precedence and owner procedures.

## Route and authority boundary

Without `preset`, use the configured service or active provider's own compatible
identity. A non-null `preset` must be in `manifest.preset.allowed`; it borrows the
allowed preset's own provider/model/endpoint/wire/credential for one call, without
switching the active preset. Missing or unsupported routes fail closed.

No provider, model, credential, preset, MCP, or CLI fallback is automatic. Vision
never auto-invokes MCP. Ask the
human before changing authorization/configuration, installing a server, or
pulling a model. Alternatives require an explicit later action. Never print keys,
tokens, environment values, headers, or private URLs.

## Setting anchors

SHOW is read-only: fresh rows from the applied snapshot, no file/environment
re-read or configuration validation/write. Rows contain only `key`, `current`,
`default`, `configurable`, `comment`; sensitive fields and path-like models are
redacted. Unavailable truth fails the entire inventory, never guessed rows.
Follow the exact owner section below, make only authorized changes, then
refresh/relaunch and SHOW again.

## Setting: provider
See [provider](reference/settings.md#setting-provider).

## Setting: base-url
See [base-url](reference/settings.md#setting-base-url).

## Setting: model
See [model](reference/settings.md#setting-model).

## Setting: api-key
See [api-key](reference/settings.md#setting-api-key).

## Setting: api-key-env
See [api-key-env](reference/settings.md#setting-api-key-env).

## Setting: max-tokens
See [max-tokens](reference/settings.md#setting-max-tokens).

## Setting: api-compat
See [api-compat](reference/settings.md#setting-api-compat).

## Setting: wire-api
See [wire-api](reference/settings.md#setting-wire-api).

## Setting: default-headers
See [default-headers](reference/settings.md#setting-default-headers).

## Setting: token-path
See [token-path](reference/settings.md#setting-token-path).

## Setting: instructions
See [instructions](reference/settings.md#setting-instructions).

## Setting: max-output-tokens
See [max-output-tokens](reference/settings.md#setting-max-output-tokens).

## Setting: timeout
See [timeout](reference/settings.md#setting-timeout).
