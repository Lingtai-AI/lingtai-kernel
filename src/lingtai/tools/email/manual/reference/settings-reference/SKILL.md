---
name: email-manual-settings-reference
description: >
  Focused Email settings reference for the five SHOW rows, installed limits,
  manifest subscription source, redaction, precedence, timing, and the
  read-only boundary. Read when interpreting settings rows.
version: 1.0.0
tags: [lingtai, email, settings, privacy, configuration]
last_changed_at: "2026-09-09T10:17:00Z"
related_files:
- src/lingtai/tools/email/manual/SKILL.md
- src/lingtai/tools/email/settings.py
- src/lingtai/tools/email/manager.py
- src/lingtai/tools/email/primitives.py
- src/lingtai/adapters/posix/mail.py
- src/lingtai/tools/email/CONTRACT.md
maintenance: |
  Tracks Email's SHOW-only settings rows, source truth, privacy redaction, and authorized configuration timing; update when settings ownership or constants change.
---

# Email settings reference

Call `email(action="settings", input={}, reasoning="inventory Email policy")` to
show effective Email policy. It is SHOW-only: non-empty input is refused, no writer
exists, and it performs no mailbox I/O. Success is `{"settings": [...]}` with
exactly `key`, `current`, `default`, `configurable`, and `comment` in every row.
The complete response is bounded at 65,536 UTF-8 bytes. If applied truth or a
provider row is unavailable, the whole action fails with fixed
`SETTINGS_UNAVAILABLE`, never partial rows or exception detail.

## Installed-code rows

These four values come from Email's installed `settings.py` constants. There is
no environment/owner-file override; changing them needs reviewed code/package
bytes plus full relaunch, then another SHOW. Their row order is the order below.

## Send body character limit

`send.body_char_limit`: current/default 50,000, configurable false. Counts Unicode
body characters for send/reply; oversize bodies are refused before scheduling.

## Duplicate send loop guard

`send.duplicate_free_passes`: current/default 2, configurable false. Two consecutive
same-body calls per recipient are allowed; the next is blocked. Only recipient
and body are compared: subject, attachment paths, and mode are not compared.
The counter advances after sender persistence/scheduling, not recipient acceptance;
a later bounce does not undo it. A different body or new EmailManager resets the
relevant history. `blocked` is not a delivery receipt; do not vary text to bypass it.

## Check result token limit

`check.result_token_limit`: current/default 10,000, configurable false. Summary
entries are removed to fit a `check` result; `truncated_by_budget` reports the
number omitted, so result size alone is not the mailbox total.

## Unread notification entry limit

`unread.max_entries`: current/default 10, configurable false. Projects newest
unread entries while preserving the total unread count. This is not a per-body
truncation or mailbox retention limit.

## Pseudo-agent subscriptions

`manifest.pseudo_agent_subscriptions` is a sensitive construction snapshot.
Current/default stay `<redacted>`; raw path lists never appear in SHOW. Accepted
configuration is a JSON list of paths (`[]` disables subscriptions); when absent,
the launcher default is `["../human"]`. Invalid path elements fail adapter
construction rather than being ignored.

For the normal CLI launcher the source is that exact `init.json` manifest field;
embedders can supply the POSIX constructor argument directly. There is no Email
environment or owner-file peer. After explicit authorization, edit the owning
source and fully relaunch so the adapter is reconstructed. Public System refresh
uses a process relaunch; the internal in-process `_setup_from_init()` only rebuilds
the EmailManager and keeps the existing mail adapter snapshot. SHOW proves
availability and redaction, never the paths. This distinction corrects the source
Contract's unqualified “ordinary refresh” wording without changing runtime.

## Privacy boundary

Mailbox/session paths, addresses, identities, contacts, messages, attachments, and
read/archive state are runtime data and are excluded, not merely redacted.
`LINGTAI_AGENT_ALIVE_THRESHOLD_SEC` remains kernel liveness policy and
`LINGTAI_NOTIFICATION_MAX_CHARS` remains Notification presentation policy; Email
does not claim either variable.
