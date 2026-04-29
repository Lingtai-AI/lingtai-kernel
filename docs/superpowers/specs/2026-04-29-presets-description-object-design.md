---
date: 2026-04-29
status: Draft for review
scope: lingtai-kernel + lingtai-tui (migrations + preset library screen). Portal: no-op stub only.
supersedes: 2026-04-28-presets-design.md (the `tags`/`tier:N` shape)
---

# Presets — `description` as a structured object

## Why

The 2026-04-28 spec aimed for a single agent-facing channel — a top-level
`description` field, optionally a structured object — through which preset
authors document tradeoffs (summary, gains, loses, cost tier, etc.). What
shipped instead introduced a parallel `tags: ["tier:N"]` array with its
own namespace machinery (`TIER_NAMESPACE`, `TIER_VALUES`, `preset_tags`,
`preset_tier`, kernel-side helpers, TUI-side mirrors) so the TUI library
screen could render deterministic ★-style tier chips.

The drift cost:

- Two parallel commentary channels (`description` prose + `tags` taxonomy)
  where the design called for one.
- Schema bloat: a fixed `tier:1..5` vocabulary baked into the kernel.
- TUI-driven coupling: the TUI's UI need (deterministic chip rendering)
  forced a machine-readable taxonomy into the kernel's preset schema.
- "Tier" lives in `tags`; everything else (summary, gains, loses) would
  live in `description`. Authors have to remember which channel each
  field uses.

## Goal

Promote `description` to a **required structured object** at the top
level of every preset file. Move tier into it as a regular field. Drop
`tags` entirely. The agent reads `description` verbatim; the TUI's
library screen reads `description.tier` for chip rendering.

## Non-Goals

- No new commentary fields beyond `summary` (required) and `tier`
  (optional). Authors may add arbitrary extra keys (`gains`, `loses`,
  `recommended_for`, etc.); the kernel surfaces them verbatim and does
  not validate them. We're not designing a fixed schema for tradeoff
  documentation today.
- No backward-compat bridge for `tags: ["tier:N"]`. The current shape is
  unshipped (no public release contains the `tags` field), so the
  migration is a one-shot rewrite, not a permanent dual-read path.
- No pre-shipped tier values on the six built-in TUI presets — we'll
  leave `description.tier` empty on the built-ins until we have a clear
  story for what tier each one is. Migration only folds existing on-disk
  `tags:["tier:N"]` into `description.tier` if present.

## On-disk schema

```jsonc
{
  "name": "deepseek",
  "description": {
    "summary": "DeepSeek V4 — OpenAI-compatible, 1M context window, tool calls",
    "tier":    "4",                                   // optional
    "gains":   ["1M context", "low cost"],            // optional, free-form
    "loses":   ["vision", "multi-modal"]              // optional, free-form
  },
  "manifest": { /* llm, capabilities, etc. */ }
}
```

Validation rules in `load_preset`:

- `description` is required and **must** be an object (`dict`).
- `description.summary` is required and must be a non-empty string.
- `description.tier`, when present, must be a string in `{"1","2","3","4","5"}`
  (the existing tier vocabulary). Other values are an error.
- All other keys under `description` are accepted as-is. The kernel does
  not validate them.

What the agent sees from `system(action='presets')`: each entry's
`description` block in full, exactly as on disk. No projection, no
rewrapping. Tier rendering becomes a TUI presentation concern, not a
schema concern.

## Removed surface

- `presets.py`: `TIER_NAMESPACE`, `TIER_VALUES`, `TIER_TAGS`, `preset_tags`.
- `presets.py`: top-level `tags` parsing/validation.
- `intrinsics/system.py`: the `"tags"` field in `_presets` listing.
- TUI `Preset.Tags` field, `tierTagPrefix`, tag-array string manipulation
  helpers (`withTier` rewritten to operate on the description object).
- TUI library screen "Tags" detail section.
- TUI builtins: `Tags: []string{}` lines on each builtin preset.

## Renamed/repurposed surface

- `presets.py`: `preset_tier(preset)` keeps its signature but now reads
  `preset["description"]["tier"]` (returning `None` when absent or when
  description is malformed during a defensive read). The function is
  used only by the kernel's `_check_context_fits` helper today; nothing
  external depends on the old tag-array implementation.
- TUI `tui/internal/tui/preset_library.go`: `presetTier(p)` reads
  `p.Description.Tier`; `withTier(p, value)` writes to it. Both lose
  their tag-string parsing.

## Migrations

### Kernel — `m002_description_object`

New migration in `src/lingtai_kernel/migrate/`. Runs against any preset
library directory `discover_presets`/`load_preset` is invoked on
(currently `~/.lingtai-tui/presets/` plus any project-local libraries an
operator configures via `manifest.preset.path`). Mirrors the shape of
`m001_context_limit_relocation.py` for consistency.

For each `*.json[c]` file:

1. Parse. If unparseable, log + skip.
2. If `data["description"]` is a non-empty string, replace with
   `{"summary": "<old string>"}`.
3. If `data["description"]` is missing, set to `{"summary": ""}` (empty
   summary is acceptable on disk; the kernel only requires non-empty at
   `load_preset` time, but migration shouldn't synthesize content).
   *Decision point:* alternatively we could leave missing-description
   files alone and let `load_preset` reject them. Going with the
   wrap-empty default to keep the migration's "make the schema valid"
   posture. **Default behavior: synthesize `{summary: ""}` when missing.**
4. If `data["tags"]` is a list and contains a `tier:N` string, set
   `data["description"]["tier"] = "N"`.
5. Delete `data["tags"]` regardless of contents (only the tier mapping
   was meaningful; other namespace tags weren't shipped).
6. Atomic write (`tmp + os.replace`).

Idempotent: a preset whose `description` is already an object and has no
`tags` key is left untouched.

Registered in `_MIGRATIONS` as `(2, "description_object",
migrate_description_object)`. Bumps kernel `CURRENT_VERSION` to `2`.

### TUI — rewrite `m025` in place

The shipped `m025_preset_tags_field` is unreleased. Rather than ship an
inert `m025` and add an `m027` for the new work, rewrite `m025` (keeping
the version number and the file name to avoid a churny rename) to do
exactly the same per-file work as the kernel's `m002`. This serves
projects whose `meta.json` stamps version `>= 25` but where the kernel's
own per-library cache hasn't yet caught up — the TUI side ensures the
files are valid before any kernel call hits them.

Rename the migration: `m025_preset_tags_field` → `m025_preset_description_object`
(both the .go file and the function `migratePresetTagsField` →
`migratePresetDescriptionObject`). The portal-side stub at version 25
has its `Name` field updated to match.

The TUI registry's `Name` and the file/function name are cosmetic — the
*version number* 25 is what matters for cross-binary compatibility — but
keeping them aligned reduces future grep noise.

### Test mirroring

- `tests/test_presets.py`: drop tag-related cases, add description-object
  cases (string→object validation rejection, malformed tier rejection,
  extra keys preserved verbatim).
- `tests/test_kernel_migrate.py` (new or extended): cases for
  string-description wrap, missing-description synthesize-empty,
  tier-fold from tags, idempotent re-run.
- `tui/internal/migrate/m025_preset_description_object_test.go`: replace
  current tags-backfill tests with description-object cases.
- `tui/internal/preset/preset_test.go`: update assertions about
  `GenerateInitJSON` output to expect a structured description.

## TUI Preset struct

```go
type PresetDescription struct {
    Summary string `json:"summary"`
    Tier    string `json:"tier,omitempty"`
    // Extra fields the kernel passes through (gains/loses/etc.) live in
    // a generic map so the TUI never drops author-authored content during
    // a save round-trip.
    Extra   map[string]interface{} `json:"-"`
}

type Preset struct {
    Name        string                 `json:"name"`
    Description PresetDescription      `json:"description"`
    Manifest    map[string]interface{} `json:"manifest"`
}
```

JSON marshaling for `PresetDescription` needs custom logic so `Extra`
keys flatten into the same object on output. Use `json.RawMessage`
buffering or a custom `MarshalJSON`/`UnmarshalJSON` pair. The TUI
already uses similar patterns elsewhere; pick whichever is simplest.

If the round-trip-preserves-extra-keys requirement adds non-trivial code,
**fallback option**: drop `Extra`, accept that the TUI's "save" path
would strip unknown description keys when an operator edits a preset
through the library screen. This would only matter if operators
hand-author rich descriptions and then edit them through the TUI —
unlikely today. **Default: include `Extra` and round-trip preserve.**

## Files touched

Kernel:
- `src/lingtai/presets.py` — schema validation, helpers, `preset_tier`.
- `src/lingtai/agent.py` — no change (it calls `load_preset`, which now
  validates the new shape).
- `src/lingtai_kernel/intrinsics/system.py` — `_presets` listing drops
  `tags`, surfaces `description` verbatim.
- `src/lingtai_kernel/migrate/m002_description_object.py` (new).
- `src/lingtai_kernel/migrate/migrate.py` — register m002.
- `tests/test_presets.py` — rewrite tags cases as description cases.
- `tests/test_kernel_migrate.py` — extend with m002 cases.

TUI:
- `tui/internal/preset/preset.go` — Preset struct + 6 builtin builders.
- `tui/internal/preset/preset_test.go` — update assertions.
- `tui/internal/tui/preset_library.go` — tier read/write through
  description; drop tag plumbing; drop "Tags" detail section.
- `tui/internal/migrate/m025_preset_description_object.go` (renamed).
- `tui/internal/migrate/m025_preset_description_object_test.go` (renamed).
- `tui/internal/migrate/migrate.go` — update m025 entry name (cosmetic).

Portal:
- `portal/internal/migrate/migrate.go` — update m025 stub `Name` to match.

## Out of scope

- Pre-shipping a tier value for any built-in preset. Empty tier on all
  six. Operators tier their own presets via the library screen.
- Documenting recommended `gains`/`loses` keys. Author convention; not
  schema.
- A description-builder UI in the TUI (operators can edit `summary`
  inline, but `gains`/`loses`/etc. are JSON-edit territory).

## Risks

1. **Round-trip preservation in the TUI is the only finicky bit.** If
   the custom JSON marshaling proves fiddly we accept the fallback
   (drop `Extra`, lose unknown keys on TUI save). Low blast radius:
   nobody's authoring rich descriptions today.
2. **Existing libraries without `description`** (e.g. hand-edited
   presets from before the description field was popularized in the TUI
   builtins) will get a synthesized `{summary: ""}`. They'll fail
   `load_preset`'s "non-empty summary" check until the operator fixes
   them. This is intentional — better a clear validation error than a
   silent default that masks the data quality problem.
