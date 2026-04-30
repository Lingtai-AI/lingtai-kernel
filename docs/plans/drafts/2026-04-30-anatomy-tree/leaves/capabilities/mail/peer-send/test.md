---
timeout: 180
---

# Test: mail / peer-send — Normal Delivery

> **Leaf:** [README.md](./README.md)
> **Expected tool calls:** ≤ 10

## Setup

1. Working directory is inside `.lingtai/` with at least two agent directories (self + a sibling or `human`).
2. Email tool is available.

## Steps

1. `email(action='send', address='human', subject='peer-send test', message='Verifying peer-send delivery.')` — send a message to the human agent (known to exist and be alive).
2. `bash(command='ls -la .lingtai/*/inbox/ | head -20')` — list inbox directories across the network.
3. `bash(command='find .lingtai -path "*/inbox/*/message.json" -newer /tmp/peer-send-marker -type f 2>/dev/null || find .lingtai -path "*/inbox/*/message.json" -type f | sort | tail -5')` — locate the newly delivered message.
4. `read` the newest `message.json` in the recipient's inbox.
5. `bash(command='grep -l "peer-send test" ./*/inbox/*/message.json 2>/dev/null')` — verify the subject matches.
6. `email(action='check', folder='sent')` — verify the sent copy exists in sender's sent folder.
7. `bash(command='find . -path "*/outbox/*/message.json" -type f 2>/dev/null | wc -l')` — verify outbox entry was cleaned up (should be 0 or same as before send).

## Pass criteria

- [ ] Recipient inbox contains a `message.json` with `"subject": "peer-send test"`
- [ ] That `message.json` contains `"message"` field with the sent body
- [ ] Sender's `sent/` folder contains a corresponding entry
- [ ] No `.tmp` files remain in recipient's inbox directory
- [ ] `message.json` contains identity card fields: `sender_name`, `sender_agent_id`, `received_at`
- [ ] Outbox entry cleaned up (no orphaned outbox directories)

## Output template

```markdown
# Test Result: mail / peer-send

**Status:** PASS | FAIL | INCONCLUSIVE
**Timestamp:** <ISO-8601>
**Tool calls used:** <N>

## Evidence
- Recipient inbox entry: <path>
- Sent folder entry: <path>
- Identity card fields present: YES/NO
- Outbox cleanup: YES/NO
- Atomic write (no .tmp): YES/NO

## Notes
<any observations>
```
