"""Portable email and codex contracts for non-lingtai runtimes.

These constants define the filesystem-based protocols that any process
must follow to participate in a lingtai agent network.  They are
injected into CLAUDE.md files for Claude Code backends (daemon
emanations and avatar agents) and can be exported as standalone skills.

The contracts are the *only* requirement for network participation —
any runtime that can read/write files according to these specs is a
first-class lingtai network node.
"""
from __future__ import annotations

EMAIL_CONTRACT = """\
### Sending Email

To send an email to another agent:

1. Generate a message ID:
   ```bash
   python3 -c "from datetime import datetime,timezone; import random; t=datetime.now(timezone.utc); print(f'{t.strftime(\"%Y%m%dT%H%M%S\")}-{random.randbytes(2).hex()}')"
   ```
2. Generate a timestamp:
   ```bash
   python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))"
   ```
3. Create the message directory and write `message.json` to BOTH:
   - **Recipient inbox**: `<lingtai_root>/<recipient_address>/mailbox/inbox/<msg_id>/message.json`
   - **Your sent folder**: `./mailbox/sent/<msg_id>/message.json`

Message format (`message.json`):
```json
{
  "id": "<msg_id>",
  "_mailbox_id": "<msg_id>",
  "from": "<your_address>",
  "to": "<recipient_address>",
  "cc": [],
  "subject": "<subject line>",
  "message": "<body text>",
  "type": "normal",
  "received_at": "<timestamp>",
  "attachments": [],
  "identity": {
    "agent_id": "<your_agent_id>",
    "agent_name": "<your_name>",
    "address": "<your_address>",
    "admin": {},
    "via": "claude-code"
  }
}
```

### Reading Email

Your incoming mail is in `./mailbox/inbox/`. Each message is a
subdirectory containing `message.json`.

To list mail:
```bash
ls -lt ./mailbox/inbox/
```

To read a specific message:
```bash
cat ./mailbox/inbox/<msg_id>/message.json | python3 -m json.tool
```

### Key Rules

1. **Always** write to both recipient inbox AND your sent folder
2. The `identity` block must include your `agent_name` and `address`
3. Use `"via": "claude-code"` in identity to identify your runtime
4. Messages are plain JSON files — no special encoding needed
5. Recipient addresses are directory names under the lingtai root
6. Use `"type": "normal"` for regular messages
7. The `_mailbox_id` field must match the directory name
"""

CODEX_CONTRACT = """\
### Reading the Codex

Your codex is at `./codex/codex.json`. It contains durable knowledge entries:

```json
{
  "version": 1,
  "entries": [
    {
      "id": "7668d8c7",
      "title": "Entry Title",
      "summary": "1-3 sentence summary",
      "content": "Full body (~500 words)",
      "supplementary": "Backing material",
      "created_at": "2026-05-08T12:00:00Z"
    }
  ]
}
```

### Writing to the Codex

To add a new entry:

1. Read the existing `codex.json`
2. Generate an ID: first 8 chars of SHA-256(title + content + timestamp)
3. Append your entry to the `entries` array
4. Write back atomically (write to `.tmp`, then rename)

```python
import hashlib, json, os, time
from datetime import datetime, timezone

codex_path = "./codex/codex.json"
data = json.loads(open(codex_path).read())

new_entry = {
    "id": hashlib.sha256(f"{title}{content}{time.time()}".encode()).hexdigest()[:8],
    "title": "Your Title",
    "summary": "1-3 sentence summary",
    "content": "Full body text",
    "supplementary": "",
    "created_at": datetime.now(timezone.utc).isoformat(),
}
data["entries"].append(new_entry)

tmp = codex_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
os.replace(tmp, codex_path)
```

### Codex Limits

- Default max 20 entries
- If full, consolidate related entries before adding new ones
- Entries survive session restarts — use for durable knowledge only
"""

LIBRARY_CONTRACT = """\
### Skill Library

Your skill library is in `.library/`. Skills are organized as directories,
each containing a `SKILL.md` file with YAML frontmatter:

```
.library/
├── intrinsic/        # Built-in skills (read-only)
│   └── some-skill/
│       └── SKILL.md
└── custom/           # Your custom skills
    └── my-skill/
        ├── SKILL.md
        └── scripts/  # Optional supporting files
```

### SKILL.md Format

```markdown
---
name: skill-name
description: One-line description of what this skill does
version: 1.0.0
tags: [tag1, tag2]
---

# Skill Title

Instructions for using this skill...
```

### Reading Skills

To list available skills:
```bash
find .library/ -name "SKILL.md" -exec head -5 {} \\;
```

To read a specific skill:
```bash
cat .library/custom/my-skill/SKILL.md
```

### Key Rules

1. Skills are read-only instructions — they teach you HOW to do something
2. Each skill folder may contain supporting files (scripts, templates)
3. The SKILL.md references these files by relative path
4. Intrinsic skills are maintained by the framework; custom skills are per-agent
"""
