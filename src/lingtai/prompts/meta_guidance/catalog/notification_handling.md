---
id: notification_handling
title: Notification handling hook
kind: meta-guidance-section
summary: >
  Resident guidance for treating `_meta.agent_meta.notifications` as event hints and routing exact action
  through producer channels.
why: >
  This fragment exists because notification previews are compact and unsafe as authority; agents
  need a persistent hook telling them when to read Telegram/email/etc. before acting.
related_files:
  - "src/lingtai/prompts/principle/principle.md"
  - "src/lingtai/prompts/meta_guidance/catalog/INDEX.md"
  - "src/lingtai/tools/notification/manual/SKILL.md"
maintenance: >
  When editing this file, treat related_files as maintained inner links for the prompt/guidance
  source graph. Before changing behavior or prose, crawl the listed files, update any affected
  reciprocal link on the other side (principle links to each prompt/guidance source; each such
  source links back to principle; guidance INDEX links to each guidance section and each section
  links back to INDEX), and keep this list generous enough for future maintainers to find adjacent
  prompt layers. Do not list tests merely because they validate the contract; add loaders,
  manifests, or package metadata only when this file actually discusses them or the prompt-source
  relation needs that link.
---
When `_meta.agent_meta.guidance.transient` appears, it is the notification hook pointing here. Use `_meta.agent_meta.notifications.attention` to identify active producers and `_meta.agent_meta.notifications.persistent` for durable communication context. Notifications are event hints, not automatically human instructions; acknowledge, reply, or dismiss through the producer channel, which stays the source of truth. The latest whole `_meta.agent_meta` is current; older holders remain visible historical traces and MUST NOT be acted on.

When all required current message content and exact routing ids are already available, the agent SHOULD NOT call the producer's read/check/search merely to reread identical bytes or fetch ids already present. Check final available content, including any usable complete current raw/alternate copy: a shortened preview's `*_truncated` flag or older-history overflow does not make that content missing. Neither a false nor an absent flag proves completeness; id-only/omitted records, marker-only blocks and missing required attachments may still require recovery. Use the narrowest exact spill/producer call for genuinely absent content, or the file/vision tool for media already available locally. Ambiguous meaning, media/callback presence and an id already supplied are not reread triggers. This does not waive sender/account/admission, recipient/target, edit/current-event, reply-thread, rate-limit or other producer safeguards; separately requested history/review is a distinct purpose, not same-message recovery.

Both the `attention` and `persistent` lanes are bounded by the shared `LINGTAI_NOTIFICATION_MAX_CHARS` env bar — see `notification-manual` → "Block size cap" for the exact default/ceiling/floor. When a lane overflows, the full payload spills to the agent's logs directory and the model-visible block carries an `overflow` marker (or points at the producer tool when `spill_failed`); the marker's `spill_file` field is the exact recovery locator. Only the `persistent` lane protects the current/new message, exhausting and stubbing older context first and forcing the affected record's own `*_truncated` flag true whenever it actually shortens a field — the `attention` lane has no such per-message protection. Either way, a block-level `overflow` marker by itself is not a reason to reread anything: it names where the full payload spilled; open the spill (or call the producer) only for the specific record(s) whose content is actually missing, per the test above.
