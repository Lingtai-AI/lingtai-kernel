# Identity Card (身份牒)

> **Capability:** email (and mail intrinsic)

---

## What

Every outgoing email carries an **identity card** — a snapshot of the sender's manifest injected into the message payload at send time. On the receive side, the `_inject_identity()` method surfaces these fields in `check` and `read` results so the agent can identify who sent each message without loading the full payload.

---

## Contract

### Injection at send time

Both the mail intrinsic and the email capability wrapper call `agent._build_manifest()` to build the identity dict, then set it as `payload["identity"]`.

**Mail intrinsic** (intrinsics/mail.py:398):
```python
"identity": agent._build_manifest(),
```

**Email wrapper** (core/email/__init__.py:850):
```python
"identity": self._agent._build_manifest(),
```

### Manifest contents (base_agent.py:1477-1501)

`_build_manifest()` returns a dict with:

| Field | Source |
|---|---|
| `agent_id` | `self._agent_id` (permanent birth ID, survives renames) |
| `agent_name` | `self.agent_name` |
| `nickname` | `self.nickname` (or `None`) |
| `address` | `self._mail_service.address` or `self._working_dir.name` |
| `created_at` | ISO timestamp |
| `started_at` | ISO timestamp |
| `admin` | dict (karma/nirvana flags) or `null` (human) |
| `language` | language code |
| `stamina` | integer |
| `state` | agent state string |
| `soul_delay` | integer |
| `molt_count` | integer |

**Agent subclass** (agent.py:223-238) extends with:
- `capabilities` — list of (name, kwargs) tuples (sensitive keys stripped)
- `combo` — preset name if set

### Extraction at read time — `_inject_identity()` (core/email/__init__.py:369-385)

Called by `_email_summary()` and `_read()` for inbox/archive messages. Extracts from the `identity` dict:

| Summary field | Source in `identity` |
|---|---|
| `is_human` | `identity["admin"] is None` |
| `sender_name` | `identity["agent_name"]` |
| `sender_nickname` | `identity["nickname"]` |
| `sender_agent_id` | `identity["agent_id"]` |
| `sender_language` | `identity["language"]` |
| `sender_location` | `identity["location"]` dict (city, region, timezone) — only if present and has timezone |

### Fields NOT surfaced

The full manifest includes `admin`, `stamina`, `state`, `soul_delay`, `molt_count`, `capabilities`, `combo` — these are persisted in `message.json` but NOT extracted into the summary by `_inject_identity()`.

### Sender display in _message_summary (intrinsics/mail.py:206-226)

For the `from` field in check output, if `identity.agent_name` exists, the display becomes `"agent_name (address)"` instead of just the address.

---

## Source

All references: `lingtai-kernel/src/`

| What | File | Line(s) |
|---|---|---|
| `_build_manifest` (base) | `lingtai_kernel/base_agent.py` | 1477-1501 |
| `_build_manifest` (agent subclass, adds caps) | `lingtai/agent.py` | 223-238 |
| Identity injection in mail intrinsic | `lingtai_kernel/intrinsics/mail.py` | 398 |
| Identity injection in email wrapper | `lingtai/core/email/__init__.py` | 850 |
| `_inject_identity` (extraction) | `lingtai/core/email/__init__.py` | 369-385 |
| `_message_summary` sender display | `lingtai_kernel/intrinsics/mail.py` | 214-217 |
| `_email_summary` calls `_inject_identity` | `lingtai/core/email/__init__.py` | 344, 351, 1011 |

---

## Related

| Leaf | Relationship |
|---|---|
| `mail/peer-send` | Every peer-send carries the identity card in the payload |
| `mail/scheduling` | Scheduled sends also include identity in `send_payload` |
| `pilot-leaf/mail-protocol/send/self-send` | Self-send carries the same identity card (sender = self) |
