---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.avatar
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/avatar/glossary-en.md
- src/lingtai/tools/avatar/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `avatar` tool package (lingtai.tools.avatar); body must stay non-empty (tool_glossary.py enforces this). Update in lockstep with glossary-en.md/glossary-wen.md whenever avatar's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**术语对照**

- `avatar_spawn`：化出独立他我。继承 init.json，用默认预设启动。详见 avatar-manual 技能。
- `avatar_rules`：设置网络法则并分发给所有后代（需 karma）。详见 avatar-manual 技能。
- `name`：他我之真名（必填）。兼作 .lingtai/ 下目录名。单段：字母/数字/下划线/连字符，最长64字。
- `type`：'shallow'（默认，初生）：白纸，仅 init.json。'deep'（二重身）：完整复制灵台、简、典。
- `comment`：写入他我系统提示之持久注解（跨凝蜕/刷新/休眠不变）。不承自父。非必要勿填。
- `dry_run`：预览化出而不生进程。用于提交前审查。
- `confirm`：确认已审阅任务并决意化出。任务空白/过短/似试时必填。
- `rules_content`：avatar_rules 的法则内容。纯文，每行一则。不可协商之约束，分发一切后代。
