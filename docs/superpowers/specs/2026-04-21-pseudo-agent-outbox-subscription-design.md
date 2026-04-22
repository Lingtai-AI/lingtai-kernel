# Pseudo-Agent Outbox Subscription — Kernel Side

**Status:** Amended 2026-04-22
**Date:** 2026-04-21 (original), 2026-04-22 (amendment)
**Scope:** `lingtai-kernel` repo. Pairs with the TUI-side change already merged in the `lingtai` repo.

## Amendment (2026-04-22)

The original design shipped with a missing step that broke the system's core mail-delivery invariant: a claimed pseudo-agent message was moved from `<sub>/mailbox/outbox/` to `<sub>/mailbox/sent/` and `on_message(payload)` was fired, but the message was **never written into the claiming agent's own `mailbox/inbox/`**. As a result, agents woke from `nap` with `reason: "mail_arrived"` while `email(action="check")` returned `total: 0` — the wake signal referred to a message that existed only in the sender's sent/ archive.

Root cause in the original doc: the "Poll loop extension" step list (original §"Poll loop extension") said "dispatch via `on_message(payload)` — same call path as inbox-delivered mail," equivocating between *same callback* and *same on-disk state*. It meant the former; a reader reasonably expected the latter, since for inbox-delivered mail the file is in the inbox *before* the callback fires. The original "Sent-folder semantics" and "Testing" sections reinforced the omission: they described the outbox→sent rename but never the inbox-write, and the proposed tests asserted only callback firing + sent/ population, so the buggy implementation passed them.

This amendment corrects the design: **claiming a pseudo-agent message means (a) writing `message.json` into the subscriber's own `mailbox/inbox/<uuid>/` AND (b) atomically renaming `<sub>/outbox/<uuid>/` → `<sub>/sent/<uuid>/`, in that order, with rollback if (b) fails.** The `on_message` callback fires only after both steps succeed. Sections "Poll loop extension," "Claim semantics" (renamed from "Sent-folder semantics"), "Failure modes," and "Testing" are updated below. Earlier wording that contradicts this amendment is superseded.

## Problem

The TUI/portal now writes human-sent mail to `human/mailbox/outbox/<uuid>/message.json` instead of directly delivering to the orchestrator's inbox. Unless the kernel polls that outbox, the message never reaches the agent.

## Goal

Teach the kernel's mail-receive loop to, in addition to scanning its own `mailbox/inbox/`, also scan the `mailbox/outbox/` of every folder listed in a new `pseudo_agent_subscriptions` field in `init.jsonc`. For each message in a subscribed outbox whose `To:` matches this agent's address, atomically rename the UUID folder from `outbox/<uuid>/` to `sent/<uuid>/` (claiming it) and dispatch it through the same `on_message` pipeline as inbox-delivered mail.

## Non-goals

- No change to send-side behavior in the kernel. Agents keep using `FilesystemMailService.send` exactly as today (write directly to recipient's inbox locally; postman handles remote).
- No change to the `MailMessage` wire format.
- No auto-discovery of pseudo-agents. Subscriptions are explicit paths in `init.jsonc`.
- No change to mail-intrinsic tool surface.

## Design

### Config surface

`init.jsonc` `manifest.pseudo_agent_subscriptions`:

- List of strings. Each string is a path, relative to the agent's working directory, to a folder whose `mailbox/outbox/` should be polled.
- Optional. Default (applied when the field is missing): `["../human"]`. The TUI's init.jsonc template ships this default explicitly so new agents pick up human mail.
- Validated in `init_schema.py` — added to `MANIFEST_OPTIONAL` (`list` type) and `MANIFEST_KNOWN`.

### Service surface

`FilesystemMailService.__init__` gains an optional keyword:

```python
def __init__(
    self,
    working_dir: str | Path,
    mailbox_rel: str = "mailbox",
    pseudo_agent_subscriptions: list[str] | None = None,
) -> None:
```

If omitted or empty, the service behaves exactly as today (polls own inbox only). If provided, paths are resolved relative to `working_dir` once at construction time and stored as a list of `Path` objects.

### Poll loop extension

`FilesystemMailService._poll_loop` gains a second scan phase. On each tick, after scanning own inbox, for each subscribed path:

1. Compute `<sub>/mailbox/outbox/`. If the directory doesn't exist, skip silently.
2. List its UUID subdirectories. For each:
   - Read `message.json`. If unreadable or malformed, skip.
   - Check if any address in the message's `To:` field matches this agent's address. If not, skip.
   - **Pre-mark the UUID in `_seen`.** This prevents Phase 1 (own-inbox scan) from re-dispatching the same message on the next tick, since we are about to place a file in the own inbox.
   - **Write `message.json` into `<self>/mailbox/inbox/<uuid>/` atomically** (tmp-write + rename), so the invariant "a wake signal implies the message is present in the claiming agent's own inbox" holds by the time the callback fires. Use the same tmp-write + `os.replace` pattern that `send()` uses (`mail.py:200-206`).
   - Attempt `os.replace(outbox_dir, sent_dir)` to claim the message atomically. If it fails (concurrent poller won the race), **roll back: remove the speculative own-inbox copy and the pre-mark in `_seen`**, then skip silently.
   - On success, dispatch via `on_message(payload)` — same call path as inbox-delivered mail. The message is now on disk in exactly two places: the pseudo-agent's `sent/<uuid>/` (archive) and the claiming agent's `inbox/<uuid>/` (receiver's authoritative copy).

### Address matching

`To:` matching reuses whatever rule the existing mail pipeline uses to decide "this message is for me" when mail lands in the inbox. Looking at the current code (`_poll_loop`), the inbox itself enforces addressing — once the sender called `send(address, message)`, the message was delivered *into this agent's inbox*, so inbox scanning doesn't need to re-check `To:`. Pseudo-agent pickup DOES need to re-check because one subscribed folder's outbox can contain mail for any number of agents.

Matching rule: the message's `To:` field is normalized to a list (it may be a string or a list on disk; current TUI and kernel writers use list, older writers used string). We then check whether `self.address` equals any entry in that list. Relative-name addressing is sufficient — the TUI writes `To:` as the orchestrator's relative name (e.g. `"本我"`), which equals `FilesystemMailService.address`.

### Wiring

In `src/lingtai/cli.py:build_agent`, when constructing `FilesystemMailService`, read `m.get("pseudo_agent_subscriptions", ["../human"])` and pass it as a keyword argument. No other wiring changes.

## Claim semantics

A successful claim mutates two places on disk and records one in-memory fact:

1. **Inbox write** (subscriber-side): `<self>/mailbox/inbox/<uuid>/message.json` is created via tmp-write + atomic rename. This mirrors what `send()` does for direct sends: the receiving agent's inbox is the authoritative location of every message it has ever received.
2. **Outbox → sent rename** (sender-side archive): `<sub>/mailbox/outbox/<uuid>/` is renamed to `<sub>/mailbox/sent/<uuid>/`. This populates the pseudo-agent's `sent/` folder, which is what the TUI's `MailCache` needs to see to flip the "delivering" indicator to "delivered".
3. **`_seen` pre-mark**: the UUID is added to `_seen` before the inbox write so that Phase 1's own-inbox scanner does not re-deliver the message when it notices the new directory on the next tick.

Invariants upheld by this ordering:

- **Wake-corresponds-to-file**: when `on_message` fires and the agent wakes, the message exists on disk in the agent's own `mailbox/inbox/`. `email(action="check")` will find it.
- **No duplicate delivery**: the pre-mark in `_seen` ensures Phase 1 does not re-dispatch. The atomic rename in step 2 ensures at most one subscriber claims each message (losers roll back).
- **No orphan inbox copies**: if the rename in step 2 fails, the inbox copy written in step 1 is deleted before returning, so a losing subscriber has no residue.

## Failure modes

- **Outbox dir missing:** skip silently. The human folder may not have an outbox until the first human message is sent.
- **Malformed `message.json`:** skip silently, same as existing inbox pipeline.
- **`To:` mismatch:** skip — the message isn't for us. Don't rename and don't write to own inbox.
- **Permission error on `sent/`:** attempt `mkdir` with `exist_ok=True` before the rename. If even that fails, log a warning and leave the message in outbox for manual recovery. Do not write to own inbox in this case — there is nothing to roll back.
- **Own-inbox write fails** (step 1 of claim): log a warning and skip. The outbox rename is not attempted, so the message stays in the pseudo-agent's outbox and will be retried next tick.
- **Two pollers race on the outbox rename:** each writes its own speculative inbox copy in step 1, then both attempt `os.replace` in step 2. Only one wins. The loser's `os.replace` raises `OSError` (source directory no longer exists); catch, **remove the speculative inbox copy and clear the UUID from `_seen`**, and skip. Net result: exactly one subscriber has an inbox copy of the message.

## Testing

Tests in `tests/test_filesystem_mail.py`:

1. **Happy path** — set up a pseudo-agent folder with a message in its outbox addressed to the subscribing agent. Start the service with subscriptions. Assert that, after the poller runs:
   - `on_message` fired exactly once.
   - The message was moved from `<sub>/outbox/` to `<sub>/sent/`.
   - **The message exists in `<self>/mailbox/inbox/<uuid>/message.json` with matching content** — this is the assertion whose absence in the original test suite let the inbox-write omission ship.
   - `on_message` does not fire a second time on subsequent ticks (no duplicate delivery from Phase 1 re-scanning the newly written inbox copy).
2. **Non-matching `To:`** — same setup, but the message is addressed to a different agent. The message stays in outbox; `on_message` is NOT called; the subscriber's inbox remains empty.
3. **Lost-race rollback** — two `FilesystemMailService` instances sharing the same pseudo-agent subscription, a single message addressed to both. Assert that exactly one subscriber ends up with an inbox copy of the message (not both, not neither), the other subscriber's inbox is empty (no residue from a rolled-back speculative write), and `on_message` is called exactly once per subscriber that actually claimed.

One new test in `tests/test_init_schema.py`: assert that `pseudo_agent_subscriptions: list` is accepted as a valid optional manifest field.

## Implementation split

1. `src/lingtai/init_schema.py` — add field to `MANIFEST_OPTIONAL` and `MANIFEST_KNOWN`.
2. `src/lingtai_kernel/services/mail.py` — extend `FilesystemMailService.__init__` with the new kwarg; extend `_poll_loop` with the subscription scan phase.
3. `src/lingtai/cli.py` — pass the field from init data into `FilesystemMailService`.
4. Tests as above.

## Backwards compatibility

- Absent `pseudo_agent_subscriptions` in init.jsonc → default `["../human"]`. New user-created init.jsonc files will ship the default explicitly (via the TUI template). Existing init.jsonc files without the field will also get the default via the code path's `.get(key, default)`.
- A user who explicitly sets `pseudo_agent_subscriptions: []` disables the feature for their agent. This is supported.
- A missing `../human` path is not an error — the poller skips silently. Agents spawned outside the TUI (e.g. raw `python -m lingtai run`) without a human folder continue to work unchanged.

## Open questions

None.
