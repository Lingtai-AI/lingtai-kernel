---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.channel_reply
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/channel_reply/glossary-en.md
- src/lingtai/tools/channel_reply/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `channel_reply` tool package (lingtai.tools.channel_reply); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever channel_reply's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**名相对照**

- `channel_reply`：渠道之复。其器常设而常闭；惟执渠道主所授、系于所选 Agent 之短契者，乃可请发一纯文。
- `submit`：以契请复一次。受者不得自定账号、会话、消息之锚、渲染、媒体与重试之政。
- `grant_ref`：不显其义之契引。
- `request_id`：受者所立之幂等请求识；复请同一回复，不改其识。
- `proof`：与契引相配之狭权凭证。
- `manual`：惟读之手册动作。
