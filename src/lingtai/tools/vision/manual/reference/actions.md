---
name: vision-actions-reference
tool: vision
related_files:
  - src/lingtai/tools/vision/manual/SKILL.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/CONTRACT.md
maintenance: |
  Keep examples aligned with declaration-owned action schemas and the installed
  router. Preserve strict input, first-call, and no-fallback semantics.
---
# Vision actions reference

## Call shape

The root requires `action`, `input`, and `reasoning`; optional `summarize` is host
presentation control. Inputs are strict and action-specific:

- **analyze** — `{"image_path": "...", "question": null, "preset": null}`.
  `image_path` and nullable `question` are required; null uses
  `Describe what you see in this image.`. `preset` is an optional explicit
  borrow of one `manifest.preset.allowed` route.
- **check** — `{"preset": null}`. Null checks the default; a string checks an
  authorized borrowed route. It may construct a service and resolve its own
  credential, but never sends an image; success is not a live server/model test.
- **list** — `{}`. Mechanically lists the active route and allowed vision-capable
  presets; it constructs no service or credential.
- **settings** — `{}`. Shows the applied bind snapshot as rows with exactly
  `key`, `current`, `default`, `configurable`, and `comment`; it never reads,
  validates, sets, resets, or writes configuration.
- **manual** — `{}`. Reads the installed Vision manual body/path only.

Unknown actions or root/input fields, non-object input, and cross-action fields
are rejected before provider, credential, image, or manual work.

## Result shapes

- `analyze` success: exactly `{"status": "ok", "analysis": text}`.
- `check` success: exactly `{"status": "ok", "route": route, "provider": provider, "model": model}`;
  it never sends an image.
- `list` success: `{"status": "ok", "default": default, "presets": presets, "count": count}`;
  entries contain route identity, not credentials.
- `settings` success: `{"settings": [...]}` with only the five row fields;
  unavailable truth returns the fixed no-row failure.
- `manual` success: exactly `{"status": "ok", "action": "manual", "manual": body, "manual_path": path}`;
  a missing installed manual is truthful `degraded` guidance.

Missing image, setup, authorization, provider, request, or empty-response
failures remain structured and sanitized. Alternatives are instructions for a
later explicit action; no provider/model/credential/preset/MCP fallback runs.
