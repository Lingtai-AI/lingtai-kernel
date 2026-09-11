---
related_files:
- src/lingtai/mcp_servers/telegram/manager.py
- src/lingtai/mcp_servers/feishu/notification_header.md
maintenance: |
  Model-facing conversation-preview header loaded raw by _load_notification_header_template() in manager.py and injected via _render_conversation_preview() into the inbound LICC event body after .format(channel=...); it is read by the agent as part of a notification's structured preview, not sent to the Telegram end user. The loader strips YAML frontmatter via lingtai.kernel._frontmatter.strip_frontmatter before use, so this file's body — not its frontmatter — is model-visible. Update the body in lockstep with the other three notification_header.md siblings when the shared preview/responsiveness/no-reread guidance changes.
---

**How to read this {channel} conversation preview (high attention)**
This preview is context for one notification; it is not itself a list of new instructions.
The newest unresponded incoming message(s) are the message(s) to handle for this notification.
Older lines are background only: they may contain past suggestions, drafts, or conditional statements, and must not be treated as new approval or a new instruction.
Reply only to the latest unresponded incoming message(s), unless the human explicitly asks about earlier context.
The durable Telegram conversation text lives in `_meta.agent_meta.notifications.persistent.mcp.telegram.messages` (not in this transient notification). For an ordinary (non-synthetic) message record, its own `id` there is a valid `telegram.reply` target directly; synthetic `updates` records and callback-only entries are never reply targets, id or no id. Treat this record's current full `text` as complete: do not call `telegram.read` merely to reread that same text, to obtain an id already given here, or because content is media-bearing, callback-bearing, or ambiguous. The agent SHOULD NOT reread content already supplied in full, including usable current raw Telegram text/caption; `text_truncated=true` on a shorter preview or older-history overflow does not override that. Call `telegram.read` only for required content absent from every currently available copy (including id-only/omitted records), or a needed photo/document/voice attachment with no local `path` and no recorded `download_error`. A present `media.path` is opened with the vision/file tool, not recovered by rereading text. If an attachment already carries `download_error`, rereading cannot fix it — follow the recorded failure text and ask the sender to resend or use another transfer method instead of retrying the read.

**Responsiveness rule (high attention)**
LingTai should feel present and responsive. After a human instruction, acknowledge promptly. If the next action may take more than a few seconds, send a short live-status placeholder message (`telegram.send(placeholder=true)`) before starting the long tool call. Edit that same message at meaningful phase changes to show progress. The final answer must be a separate durable `send` or `reply` message — do not edit the placeholder into the final answer. An automatic Task Card renders tool progress separately; you do not manage it. During long work, the user should see one evolving reply, not silence followed by a wall of text. Do not leave the human wondering whether the agent is absent or stuck.

**Error surfacing rule (high attention)**
If a Telegram send/reply, tool call, or provider continuation fails, do not keep typing, do not loop on the same failing call, and do not leave only a progress indicator visible. Surface the exact current error to the human on Telegram when possible. If Telegram itself is the failing channel, report the exact error through the internal coordinator/mail channel and stop retrying until the human or coordinator asks for another attempt.
