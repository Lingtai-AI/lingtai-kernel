---
related_files:
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/mcp_servers/local_commands/__init__.py
  - src/lingtai/mcp_servers/local_commands/core.py
  - src/lingtai/mcp_servers/telegram/account.py
  - src/lingtai/mcp_servers/telegram/service.py
  - src/lingtai/mcp_servers/feishu/control_cards.py
  - src/lingtai/mcp_servers/feishu/manager.py
  - src/lingtai/mcp_servers/feishu/service.py
  - tests/test_local_command_core.py
  - tests/test_telegram_slash_commands.py
  - tests/test_feishu_control_cards.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Update this Anatomy with changes to command ownership, workdir reads, signal
  semantics, or the boundary between shared command results and channel UI.
---
# Local Messaging Command Core Anatomy

Channel-neutral read/control logic for local messaging commands. The core reads
Agent status, briefing, system Markdown, command catalogs, and Task Card
preferences; it writes only the established `.refresh`, `.sleep`, and `.clear`
signals. It never admits a chat actor, chooses a recipient, builds Telegram
keyboards/Markdown or Feishu cards, or sends a provider request.

## Components

- `core.py` — immutable result records plus `LocalCommandCore` for command
  catalogs, kanban/status data collection, brief/system reads, signal writes,
  and Task Card preference parsing through injected callbacks.
- `__init__.py` — the stable import surface used by messaging adapters.

## Connections

- `telegram/account.py` keeps slash/callback admission and byte-compatible
  Telegram rendering, delegating only reads and controls to this package.
- `telegram/service.py` injects one workdir-bound core into every configured
  account so local commands share the same Agent scope.
- `feishu/control_cards.py` consumes the same semantic results while owning
  Feishu schema-2.0 rendering, `zh`/`en`/`wen` localization, layered navigation,
  and internal callback values. `feishu/manager.py` retains admission,
  callback dispatch, recipient/thread routing, and transport.

## Composition

- Parent: `src/lingtai/mcp_servers/`
- Consumers: messaging adapters only
- No dependency on a provider SDK or kernel-private Agent object

## State

The core owns no durable state. It reads current workdir artifacts and writes
only existing signal files. Task Card preferences remain owned/persisted by the
injected adapter service: Telegram under `telegram/taskcard.json` and Feishu
under `feishu/taskcard.json`.

## Notes

Shared outcomes are semantic (`sent`, `pending`, `not_found`, etc.); channels
own localization, presentation, navigation, and provider error transport.
