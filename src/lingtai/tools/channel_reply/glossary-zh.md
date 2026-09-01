---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.channel_reply
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/channel_reply/glossary-en.md
- src/lingtai/tools/channel_reply/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `channel_reply` tool package (lingtai.tools.channel_reply); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever channel_reply's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**术语对照**

- `channel_reply`：渠道回复。静态但默认关闭；只有持有渠道所有者签发、且绑定目标 Agent 的短时授权时，才可提交一条纯文本回复。
- `submit`：提交一次授权绑定的回复请求；目标不能提供账号、会话、消息锚点、渲染、媒体或重试参数。
- `grant_ref`：不透明授权引用。
- `request_id`：目标侧幂等请求标识；同一回复重试时保持不变。
- `proof`：与不透明授权配对的窄权限证明。
- `manual`：只读手册动作。
