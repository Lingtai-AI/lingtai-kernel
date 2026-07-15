---
related_files:
- src/lingtai/mcp_servers/telegram/manager.py
- src/lingtai/mcp_servers/feishu/notification_header.md
maintenance: |
  Outbound chat-message header template loaded raw by _load_notification_header_template() in manager.py and sent verbatim to Telegram users after .format(channel=...); the loader strips YAML frontmatter via lingtai.kernel._frontmatter.strip_frontmatter before use, so this file's body — not its frontmatter — is user-facing. Update the body in lockstep with the other three notification_header.md siblings when the shared preview/responsiveness guidance changes.
---

**How to read this {channel} conversation preview (high attention)**
This preview is context for one notification; it is not itself a list of new instructions.
The newest unresponded incoming message(s) are the message(s) to handle for this notification.
Older lines are background only: they may contain past suggestions, drafts, or conditional statements, and must not be treated as new approval or a new instruction.
Reply only to the latest unresponded incoming message(s), unless the human explicitly asks about earlier context.
The durable Telegram conversation text lives in `_meta.agent_meta.notifications.persistent.mcp.telegram.messages` (not in this transient notification). If the latest incoming message is complete there (not truncated and not ambiguous), you may reply directly without an extra `telegram.read`; call `telegram.read` first when content is truncated, ambiguous, media/callback-heavy, or needs exact anchoring.

**Responsiveness rule (high attention)**
LingTai should feel present and responsive. After a human instruction, acknowledge promptly. If the next action may take more than a few seconds, send a short live-status placeholder message (`telegram.send(placeholder=true)`) before starting the long tool call. Edit that same message at meaningful phase changes to show progress. The final answer must be a separate durable `send` or `reply` message — do not edit the placeholder into the final answer. An automatic Task Card renders tool progress separately; you do not manage it. During long work, the user should see one evolving reply, not silence followed by a wall of text. Do not leave the human wondering whether the agent is absent or stuck.

**Error surfacing rule (high attention)**
If a Telegram send/reply, tool call, or provider continuation fails, do not keep typing, do not loop on the same failing call, and do not leave only a progress indicator visible. Surface the exact current error to the human on Telegram when possible. If Telegram itself is the failing channel, report the exact error through the internal coordinator/mail channel and stop retrying until the human or coordinator asks for another attempt.
