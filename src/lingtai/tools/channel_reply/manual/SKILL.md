---
name: channel_reply
description: |
  Manual for the static channel_reply intrinsic. Use it when a routed message
  supplies an opaque reply grant and the target needs to answer through the
  owning channel without receiving channel credentials or destination fields.
version: 1.0.0
last_changed_at: 2026-08-09T00:00:00Z
related_files:
- src/lingtai/tools/channel_reply/ANATOMY.md
- src/lingtai/tools/channel_reply/CONTRACT.md
- src/lingtai/tools/channel_reply/__init__.py
- src/lingtai/kernel/channel_reply/__init__.py
- src/lingtai/tools/channel_reply/schema.py
maintenance: |
  Update this manual with the channel_reply Contract and intrinsic schema
  whenever request fields, receipts, duplicate handling, or authority changes.
---

# channel_reply

`channel_reply` lets an Agent answer one routed channel message when, and only
when, the channel owner supplied an opaque active reply grant and target capsule.
Without that owner authority the static tool is closed and performs no channel
call. V1 file-backed submission is available on macOS only. On Linux, Windows,
and every other unsupported platform the tool remains statically visible, but a
submit returns a local terminal `dead` receipt and performs no file, provider, or
channel side effect.

Use `action="submit"` with `version`, `grant_ref`, `request_id`, `created_at`,
`text`, and `proof`. Generate `created_at` as the current UTC timestamp at the
moment you make this concrete submit attempt. It is target-authored request time,
not an exact owner authority field and not the grant's earlier issuance/route
time; Core still rejects stale or future request timestamps. The reply is plain
text. Keep the exact `request_id` stable for the same logical reply. A local
`pending` result means the request is queued or already owned by a dispatcher; it
does **not** authorize a second request id or a changed reply. Submit the same
tuple only to look up its eventual committed receipt.

Do not include account, chat, user, message, path, destination, parse/render,
media, entity, retry, backoff, or attempt fields. Those are owner authority. The
owning channel adapter derives them from the grant and rejects target attempts to
provide them.

A `sent`, `failed`, `dead`, or `ambiguous` receipt is terminal for that tuple.
`ambiguous` means an external send may have committed, so it must never be
resent. Do not rotate `request_id` to work around any terminal result. Only a new
owner-routed event with a new grant can authorize another reply.

Receipts are local and opaque. They do not reveal account aliases, chat ids,
message ids, paths, proof material, routed input, or your reply body. The target
transport may retain a proof-free terminal marker after receipt retention; that
state also fails closed rather than requeueing.

## Result summaries

`channel_reply` is a short-result family. Normally leave the root
`summarize=false` and inspect the exact submit receipt or manual text. Set
`summarize=true` only when you deliberately want the executor's canonical
LTP-v2 result-summary handling; it never changes the raw recorded result. In
particular, keep it false when exact terminal status, receipt wording, grant
reference, request id, or manual wording matters.

## Settings

The `channel_reply` family has no settings file. Neither `submit` nor `manual`
has an action-level settings file. Authority comes only from the owner-created
capsule and opaque grant; it is never enabled through tool settings.
