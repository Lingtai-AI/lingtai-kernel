---
name: vision-manual
description: >
  Use this manual when the vision capability has no usable provider route or
  reports a direct setup/request failure and needs safe, provider-neutral
  troubleshooting guidance.
last_changed_at: "2026-07-16T21:00:00-07:00"
related_files:
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/ANATOMY.md
  - src/lingtai/tools/vision/CONTRACT.md
maintenance: |
  Keep this manual provider-neutral and read-only. It must not import, name, or
  link to a TUI package, credential, endpoint secret, or automatic MCP action.
---
# Vision manual

This is the provider-neutral fallback for `vision`. It contains guidance only;
it does not discover, install, start, or invoke a backend.

## OpenRouter and custom try first

For OpenRouter and custom OpenAI-compatible presets, `vision(action="analyze")`
first tries the current endpoint, model, and credential. It does not reject the
route merely because downstream image support cannot be known in advance. If
the real request fails, the sanitized vision tool result reports the failure
type and points here for explicit alternatives; it does not expose exception
contents or silently switch provider, model, credential, or MCP.

## Direct route

When `vision` reports a direct setup or request failure, inspect the identity
already shown in the prompt: the current provider, model, and sanitized
endpoint. Do not substitute another provider, model, credential, endpoint, or
wire protocol. Retry only after the operator has corrected the active preset.

## Find the current preset's method

Use the `skills` capability's catalog to search your own installed skills for a
manual matching that provider/model or preset. Read the matching manual before
trying its documented method or official-page pointer. If no matching manual is
present, report that no discoverable vision method is available.

An optional MCP or other skill may be described by that preset manual, but it
is always an explicit operator/agent action. This manual never auto-loads or
auto-invokes MCP.

## Safety

Never request or print API keys, OAuth tokens, environment values, headers, or
full unsanitized URLs. Missing provider, model, or endpoint fields are simply
unknown; do not fill them with guesses.
