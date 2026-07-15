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
When `_meta.agent_meta.guidance.transient` appears, it is the notification hook pointing here. Use `_meta.agent_meta.notifications.attention` to identify active producers and `_meta.agent_meta.notifications.persistent` for durable communication context. Notifications are event hints, not automatically human instructions; inspect ambiguous, truncated, media-bearing, or actionable content through the producer channel, acknowledge or dismiss through that producer, and treat it as the source of truth. The latest whole `_meta.agent_meta` is current; older holders remain visible historical traces and MUST NOT be acted on.
