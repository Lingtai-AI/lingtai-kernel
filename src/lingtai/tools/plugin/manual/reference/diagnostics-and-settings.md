---
related_files:
- src/lingtai/tools/plugin/ANATOMY.md
- src/lingtai/tools/plugin/manual/SKILL.md
- src/lingtai/tools/plugin/__init__.py
- src/lingtai/tools/plugin/settings.py
- src/lingtai/tools/plugin/CONTRACT.md
- src/lingtai/services/plugin_registry.py
- tests/test_plugin_tool.py
maintenance: |
  Keep result fields, redaction, and generic envelope errors aligned with the
  plugin family. Diagnostics remain read-only and must not expose private paths,
  secrets, or mutation inputs.
---

# Plugin diagnostics and settings

Use this reference after the router's prerequisites. Actions are read-only;
registration and refresh stay outside this tool surface.

## `info`: one unsummarized audit

```text
plugin(action="info", input={}, reasoning="inspect plugin state", summarize=false)
```

Success returns:

```text
{status, declared, registered_count, registered, discovered_count, discovered,
 mcp_appended, mcp_pruned, paths, problems}
```

`registered` is the boot/refresh snapshot, not a revalidation of changed plugin
files. `info` can reconcile the protected prompt from live discovery, but new
registered content requires the authorized refresh path. Each entry has
`name`, `version`, `summary`, `source`, optional `homepage`, `skills`,
`skill_count`, `skills_mounted`, `mcp_servers`, `mcp_server_count`,
`mcp_registered`, and `skipped`. Each `skipped` item is `{component, reason}`;
use it for containment, invalid names, collisions, malformed components, and
disabled Skills capability. `discovered` has metadata/counts
(`name`, `version`, `summary`, `skill_count`, `mcp_server_count`, `source`,
optional `homepage`) and never implies registration.

`paths` is keyed by each raw configured/discovery path and reports
`{resolved, exists, plugins}`. `problems` contains `{plugin, path, error}` for
whole-plugin/component failures. `mcp_appended` and `mcp_pruned` describe the
last boot/refresh, not an `info` mutation. `summarize=true` is presentation only;
keep it absent or false for exact names, paths, and reasons.

Diagnosis: absent from both tiers → inspect `paths` and `problems`; discovered
only → an authorized owner must declare it and refresh; registered but incomplete
→ inspect that entry's `skipped`. Never infer execution from a record. Do not
include plugin `env`/headers, private errors, or local paths in public reports.

## `settings`: redacted inventory

```text
plugin(action="settings", input={}, reasoning="inspect plugin registration roots", summarize=false)
```

Success is exactly one row:

```json
{"settings":[{"key":"manifest.plugins","current":"<redacted>","default":"<redacted>","configurable":true,"comment":"plugin-manual#plugin-registration-roots"}]}
```

The comment points to the entry heading [Plugin registration roots](../SKILL.md#plugin-registration-roots).
It represents configured canonical-plus-alias roots from the detached snapshot.
The automatic root and inherited discovery paths are excluded. Redaction is
mandatory because roots reveal local layout/trust boundaries; `configurable`
does not grant this caller write authority. Settings does not scan, edit
`init.json`, set/reset, change the environment, or change registry records.

If the snapshot is unavailable, malformed, or unserializable, the whole action
returns the fixed failure with no row:

```json
{"status":"failed","error_code":"SETTINGS_UNAVAILABLE","message":"settings inventory is unavailable"}
```

The generic complete UTF-8 response limit is 65,536 bytes; an oversized complete
response uses `SETTINGS_RESPONSE_TOO_LARGE`.

## Envelope failures and verification

All actions require `action`, `input={}`, and `reasoning`; the root is closed and
`summarize`, when present, is boolean. Unknown actions fail before filesystem I/O:

```json
{"status":"error","message":"unknown action: 'install', only 'info' or 'settings' or 'manual' is supported"}
```

Non-object or extra input, unknown root fields, and non-boolean `summarize` use
the generic `{"status":"failed","error_code":"INVALID_ARGUMENT",...}` shape
before scanning, settings access, or manual loading. No action accepts install,
uninstall, refresh, launch, or file-editing input.

`manual` success is `{status: "ok", plugin_manual, manual_path}` and performs no
scan. If the installed body is missing it returns a degraded empty body and
error, never a substitute. After an authorized refresh, verify both:

```text
plugin(action="info", input={}, reasoning="verify refreshed plugin registration", summarize=false)
plugin(action="settings", input={}, reasoning="verify configured plugin roots", summarize=false)
```
