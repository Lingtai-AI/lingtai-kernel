---
name: telegram-behavior-tests
behavior_version: 1
labt_version: 2
contract:
  - ../../mcp_servers/telegram/task_card/CONTRACT.md
  - ../../mcp_servers/telegram/SKILL.md
anatomy: ../../mcp_servers/telegram/task_card/ANATOMY.md
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
  - src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/task_card/resident.py
  - src/lingtai/mcp_servers/telegram/SKILL.md
  - src/lingtai/mcp_servers/telegram/manager.py
  - tests/test_telegram_task_card_last_message.py
  - tests/test_telegram_reaction_states.py
maintenance: |
  LABT v2, migrated 2026-08 (CONVERT_BEHAVIOR) from
  tests/test_telegram_task_card_last_message.py (C001) and
  tests/test_telegram_reaction_states.py (C002). This file now covers only the
  Telegram task-card projection and Telegram reaction states. The former
  C003/C004 (web_search) and C006 (feishu LTP v2) LABTs were re-homed to
  src/lingtai/tools/web_search/BEHAVIORS.md and
  src/lingtai/tools/feishu/BEHAVIORS.md respectively (both listed in the root
  BEHAVIORS.md related_files); the former C005 (file read continuation) was
  retired with the removed `file` tool.
  There is no root CONTRACT.md beside this file; the task-card contract lives
  at src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md (frontmatter name
  `telegram-task-card-projection`) and Telegram reaction semantics are guarded
  by src/lingtai/mcp_servers/telegram/SKILL.md (`telegram-mcp-manual` § REPLY
  vs SEND) until a Telegram root contract exists. Keep guards pointed at real
  clauses when the contract or manual changes; update the paired ANATOMY.md
  entries in the same change.
---
# Telegram Behavior Tests

LABT v2. Self-contained agent-executable behavioral tests for the Telegram
channel: C001 proves the task-card projection's last-message semantics
(edit-in-place, rotation, failure paths) against the resident-core contract
(`src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md`), and C002 proves the
reaction state machine (received → seen → replied). The web_search, file
read, and feishu LTP v2 LABTs that previously lived here were re-homed to their
own module BEHAVIORS.md files (see related_files and the root BEHAVIORS.md).

## Behavior C001 — Telegram task card: edit-in-place vs supersede by last message

- **id**: C001
- **title**: Telegram task-card last-message semantics (edit-in-place, rotation, failure paths)
- **guards**: `telegram-task-card-projection` § Behavior (items 7–8: commit-after-success, edit-first delivery, conservative old-first rotation, failure-state projection) ([CONTRACT.md](../../mcp_servers/telegram/task_card/CONTRACT.md#behavior))
- **supersedes**: tests/test_telegram_task_card_last_message.py (CONVERT_BEHAVIOR)
- **runner**: any LingTai agent with the telegram capability (task-card projection against a recording transport)
- **prerequisites**: repo checkout with src/lingtai/mcp_servers/telegram/task_card; the task-card resident core exercised through a recording (fake) transport — all transport I/O is recorded locally, never sent to the network; start from a cold state with no resident task-card message for the tracked account+chat.
- **estimate**: 20 minutes

### Steps

1. Start from a cold state (no resident task-card message for the tracked account+chat).
2. Produce a task-card update with draft text `"old text"`; note the resulting resident message id (compound id `mybot:55:100`).
3. Produce a second update with draft text `"new text"` while the previous message is still the last message the bot sent.
4. Deliver a user message (user msg id `200`), then produce a third update.
5. Force the bot to send an unrelated message (bot reply id `101`), then produce a fourth update.
6. Rotate the resident card to a fresh message id (`mybot:55:201`), then produce a fifth update.
7. Exercise the rotation failure paths: old resident already gone; delete raises; send raises after rotation; persist of the new resident fails; suppressed updates; malformed send ids.

### Expected evidence

- [ ] **Edit-in-place**: when the resident task-card message is still the last message, the update result is `{status: ok, message_id: <same id>}` and exactly **1 send, 0 delete** transport calls occur.
- [ ] **Supersede by user message**: a user message after the resident card means the card is no longer "last"; the update rotates the card — the old resident message (`mybot:55:100`) is deleted and a new card is sent.
- [ ] **Rotation call order** (user msg id `200` case): transport sees exactly `edit(55, 100, <committed old text>)` → `delete(55, 100)` → `send(55, 201, <new text>)` with compound ids `mybot:55:100` / `mybot:55:201`; the rotation happens **before** the new send, so the card is never duplicated.
- [ ] **Supersede by bot message**: a bot reply (`mybot:55:101`) between the card and the update also rotates: `delete(55, 100)` → `send(55, 301, <new text>)`.
- [ ] **Rotation failure paths** (each returns `{status: ok, taskcard: false}` and a distinct marker): old resident already gone → `old_resident_deleted`; delete raises → `stale_delete_failed`; send raises after rotation → `indeterminate_send`; persist of the new resident fails → `resident_persist_failed`. Suppressed updates return `{status: ok, suppressed: true, taskcard: false}`.
- [ ] **Malformed send ids** (e.g. `no-colon-id`, `just:one`) are never adopted as the new resident; the update is suppressed.
- [ ] **Concurrency**: the task-card manager serializes rotation; only one in-flight rotation is allowed (`max_active == 1`).

### Pass / Fail

PASS when all evidence above holds; FAIL on any extra send/delete, any wrong id, or any missing failure marker.

## Behavior C002 — Telegram reaction states (received → seen → replied)

- **id**: C002
- **title**: Telegram reaction state transitions and reply-vs-send semantics
- **guards**: `telegram-mcp-manual` § REPLY vs SEND (a reply threads the response to a specific message and adds a ✅ reaction to it; reactions never hit the network in this LABT) ([SKILL.md](../../mcp_servers/telegram/SKILL.md#reply-vs-send))
- **supersedes**: tests/test_telegram_reaction_states.py (CONVERT_BEHAVIOR)
- **runner**: any LingTai agent with the telegram capability (reaction state machine against a recording transport)
- **prerequisites**: repo checkout with src/lingtai/mcp_servers/telegram; the reaction state machine exercised through a recording (fake) telegram transport — reactions are recorded locally, never sent to the network; the reply-vs-send rule is the manual clause `telegram-mcp-manual` § REPLY vs SEND (`src/lingtai/mcp_servers/telegram/SKILL.md`) — the guard for reply-vs-send until a telegram root contract exists.
- **estimate**: 15 minutes

### Steps

1. Deliver a user message with `message_id=12345` that mentions a tool call with `reply_to_message_id=8`.
2. Let the handler run the received → seen → replied state machine.

### Expected evidence

- [ ] `REACTION_RECEIVED` is `[{type: "emoji", emoji: "👍"}]`; `REACTION_SEEN` is `"👀"`; `REACTION_REPLIED` is `"✍️"`; `REACTION_DONE` equals `REACTION_REPLIED`.
- [ ] The transport receives exactly the reaction sequence `[(12345, 8, "👍"), (12345, 8, "👀")]` — received and seen both react to `message_id=12345`, `reply_to_message_id=8`.
- [ ] If seen-reaction delivery fails, the seen step is **skipped** (no retry, no crash) and the sequence stays `[(12345, 8, "👍")]`.
- [ ] On reply, the state machine reacts `(12345, 8, "✍️")` — it reacts to the message whose id is `_reply_to_message_id` (8), not to a separately sent message.

### Pass / Fail

PASS when the reaction sequence and skip-on-delivery-failure match exactly; FAIL on any missing reaction, extra reaction, or a reply that sends instead of reacting.
