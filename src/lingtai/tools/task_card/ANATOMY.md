---
related_files:
  - src/lingtai/tools/task_card/CONTRACT.md
  - src/lingtai/tools/task_card/__init__.py
  - src/lingtai/tools/task_card/manual/SKILL.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/feishu/task_card.py
  - src/lingtai/mcp_servers/feishu/manager.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - tests/test_task_card_controller.py
  - tests/test_telegram_toolfamily_ltpv2.py
  - tests/test_telegram_task_card_programmable.py
  - tests/test_feishu_programmable_task_cards.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this Anatomy reciprocal with its paired CONTRACT.md and manual. Update
  this file in the same change as any ownership, file-path, lifecycle, or
  projection-boundary change.
---
# Intrinsic Task Card Anatomy

The intrinsic `task_card` capability owns one agent-local declarative artifact
plus its persisted agent-wide configuration, both under `<workdir>/taskcard/`,
and nothing else. It is producer-first and channel-neutral: it runs a
renderer, writes `taskcard/taskcard.md`, and writes `taskcard/status` as exact
`active` or `inactive`, reading `taskcard/taskcard.json` to resolve each new
watch's cadence/ceiling defaults. `stop` pauses a watch and preserves the last
body; `remove` is the terminal lifecycle action that also retires any active
watch and deletes the body, so a caller never needs to reach around this
capability with a filesystem delete. It does not own Telegram, Feishu, portals,
chat IDs, retry policy against a transport, or any resident message state.
Normative promises live in [`CONTRACT.md`](CONTRACT.md).

## Components

- `__init__.py` — the full capability owner: schema/description, one-watch
  lifecycle, renderer execution, atomic file writes, error/limit notifications,
  persisted config loading/validation (`TaskCardManager._load_config`), the
  one-way legacy-config migration (`TaskCardManager._migrate_legacy_config`),
  and `setup(agent)` registration.
- `manual/SKILL.md` — the progressive-disclosure manual for renderer authors
  and lifecycle use.

## Connections

- `setup(agent)` registers the public `task_card` tool through
  `lingtai.tools.registry`.
- `lifecycle._stop` calls `shutdown_for_agent_stop()` so a stopping agent
  writes `inactive` and joins the watch thread best-effort.
- Telegram and Feishu are only consumers: each manager reads
  `<workdir>/taskcard/status` and `<workdir>/taskcard/taskcard.md` and projects
  them separately. The intrinsic capability never calls back into either
  messaging adapter.
- One-way only, the reverse direction: if `<workdir>/taskcard/taskcard.json`
  has never been created, `start` reads `<workdir>/telegram/taskcard.json`
  (the retired Telegram-owned controller's persisted refresh ceiling) once,
  and migrates it only when that legacy value differs from its own untouched
  default (which ordinary `/taskcard` commands persist regardless of any real
  customization). Either way — migrated or built-in — this first resolution
  writes the new intrinsic config file immediately, so the legacy path is
  never read again for this agent, even if that Telegram file changes later.

## Composition

- Parent: [`src/lingtai/tools/ANATOMY.md`](../ANATOMY.md)
- Paired contract: [`CONTRACT.md`](CONTRACT.md)
- Consumer-specific projection rules: `src/lingtai/mcp_servers/telegram/` and
  `src/lingtai/mcp_servers/feishu/task_card.py`

## State

- `<workdir>/taskcard/status` — exact `active` or `inactive`
- `<workdir>/taskcard/taskcard.md` — the full rendered body
- `<workdir>/taskcard/taskcard.json` — persisted agent-wide config
  (`interval_s`/`timeout_s`/`max_refreshes`); read fresh on every `start`,
  written only by the one-way legacy migration (never by any model-facing
  action)
- In-memory only: one active watch, its thread, last valid body/timestamp, and
  deduped error/limit bookkeeping

## Notes

- Atomic ordering is the structural point of this unit: write the body fully
  before activation, update the body by atomic replace, write `inactive`
  before stopping, and — for `remove` — confirm the watch has quiesced before
  deleting the body, so the updater can never recreate a file `remove` just
  removed.
- Missing, invalid, or inactive producer state is a consumer concern. This
  intrinsic capability only writes the artifact truthfully.
- The legacy-config migration is a one-time bootstrap, not an integration:
  it is gated on `taskcard/taskcard.json` not yet existing (never on its
  content), so this capability never carries an ongoing runtime dependence on
  Telegram or any other consumer for its own policy.
