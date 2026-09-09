---
name: email-manual-actions-and-storage
description: >
  Focused Email reference for action inputs, folders, check filters, delivery,
  self-send/time capsules, contacts, attachments, and mailbox persistence.
  Read when the schema and first-call map are not enough.
version: 1.0.0
tags: [lingtai, email, actions, mailbox, storage]
last_changed_at: "2026-09-09T10:17:00Z"
related_files:
- src/lingtai/tools/email/manual/SKILL.md
- src/lingtai/tools/email/_family_schema.py
- src/lingtai/tools/email/manager.py
- src/lingtai/tools/email/primitives.py
- src/lingtai/tools/email/settings.py
- src/lingtai/tools/email/CONTRACT.md
maintenance: |
  Tracks Email action behavior, input details, mailbox layout, and persistence guidance; update when action contracts or storage ownership changes.
---

# Email actions and storage

The root manual is the first-call router and the schema is authoritative. This
page covers action details that need care; action input objects remain closed.
Omitted optional values use defaults, and explicit `null` is omitted at the family
boundary.

## Send

```python
email(action="send", input={
    "address": "peer", "subject": "status", "message": "ready",
    "cc": ["human"], "bcc": [], "attachments": [], "delay": 0,
    "mode": "peer", "type": "normal",
}, reasoning="report status")
```

`address` is a bare peer name, or an explicitly authorized absolute path with
`mode="abs"`. CC is visible to recipients; BCC is stored only in the sender's
copy. `attachments` are source paths: for non-self POSIX delivery the adapter
validates every path before publishing that recipient's inbox entry, copies files
into recipient-local `attachments/`, and suffixes duplicate basenames. Source
paths are resolved/read at delivery time, so preserve their bytes until delivery;
there is no send-time frozen snapshot. No attachment-size or source-root-containment
limit is enforced: select only files you are authorized to share. A self-send bypasses this adapter
and does not validate or copy attachments; its inbox note can retain a missing
source path. Do not treat self-mail as an attachment backup. The message body is capped at 50,000 Unicode characters and is rejected,
not truncated, when oversized.

Each recipient's outbox is written before its thread starts; the unified sent
record follows all thread starts. `status="sent"` is scheduling, not acceptance.
Use a non-negative integer `delay` for one future attempt, not tool execution;
the schema does not enforce a non-negative bound. For partial failures and restart
limits read [Notifications and delivery](../notifications-and-delivery/SKILL.md).
The duplicate guard compares recipient/body, not the whole message; see
[Settings reference](../settings-reference/SKILL.md#duplicate-send-loop-guard).

## Check and search

`check` lists newest first from `inbox`; `folder` may be `inbox`, `sent`, or
`archive`, and `n` defaults to 10. Its only filter object supports:

- `sort`: `newest` (default) or `oldest`;
- `from`, `subject`, `contains`: case-insensitive substrings;
- `after`, `before`: ISO 8601 timestamps;
- `unread_only`, `has_attachments`: boolean selectors;
- `truncate`: preview characters, default 500; `0` requests the full body.

Email may trim a large `check` result to its token budget and reports that fact.
`search` takes a regex `query` and optional `folder`, searches sender/subject/body,
and rejects invalid regex; it accepts neither `filter` nor `n`. With no folder,
`search` covers inbox + sent. Folder-less `read` searches inbox, sent, then archive;
only inbox records are marked read.

## Read, mutate, and contacts

- `read` takes mailbox IDs, returns source records including attachments, and marks
  inbox IDs read. `dismiss` marks handled inbox IDs read without returning bodies.
- `reply`/`reply_all` take one ID and a message; routing and subject derivation are
  in [Addressing and replies](../addressing-and-replies/SKILL.md).
- `archive` moves inbox IDs to `mailbox/archive` and removes them from the read set.
  `delete` permanently removes inbox/archive IDs and refuses `sent`.
- `contacts` lists the private book; `add_contact` upserts; `remove_contact` deletes;
  `edit_contact` changes only supplied name/note fields. Contact writes use a
  temporary file and atomic replacement.

IDs must come from this agent's current notification or mailbox result. Stale or
foreign-working-directory IDs have no meaning and produce a not-found hint.

## Self-send and time capsules

An already-delivered self-note survives molt; `dismiss`/`read` clear unread status
but retain the message, `archive` moves it, and `delete` removes it. Before delivery,
a delayed self-note depends on the current mailman daemon thread staying alive;
outbox persistence does not make that timer survive process exit. Use it as a
one-shot time capsule, not delayed tool execution or a durable scheduler.
Recurring or restart-resilient work belongs to `shell-manual`.

## Mailbox layout and retention

Paths are relative to the agent working directory:

```text
mailbox/inbox/<id>/message.json       received mail
mailbox/sent/<id>/message.json        one sent record per call
mailbox/archive/<id>/message.json     archived inbox mail
mailbox/outbox/<id>/message.json      pending/delayed send
mailbox/read.json                     read-ID set
mailbox/contacts.json                 private contact book
.notification/email.json              producer-owned unread mirror
```

Message JSON is UTF-8. Non-self POSIX recipient entries use staging + atomic
publication; self-inbox, outbox and unified sent records use direct writes.
`read.json` and contacts use temporary-file replacement. BCC is not exposed in
recipient payloads. Do not infer crash durability from these different write paths.

## Cleanup / Footprint

Email leaves the paths above, recipient attachment snapshots, and ordinary Agent
logs/bounce events. Before mailbox retirement or when attachment growth matters,
inspect only this Agent's `mailbox/` and `.notification/email.json` with the
shared read-only count/byte recipe in `skills-manual` →
`reference/cleanup-footprint-contract.md#shared-footprint-check-recipe`.
Inspect metadata without broadcasting private bodies/addresses. Keep sole copies
of decisions, handoffs, attachments, contacts and pending/evidence records.

Show a dry-run report listing what would stay or go, obtain explicit human consent,
then prefer authorized `archive`/`delete` actions over filesystem removal. Do not
clean active outbox state. Cleanup is optional; without consent stop at the report.
If recording the audit, separately opt into the shared `logs/cleanup.jsonl` append
(timestamp, Email label, dry-run/apply, count, bytes, non-secret path summary,
approval). Inspection alone writes nothing; approved apply records actual results.
