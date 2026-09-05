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

- `avatar`：唯一公开工具，以 `action` 分派，每个动作各有严格独立的 `input`。详见 avatar-manual 技能。
- `action`：必填，无默认值。`spawn`（化出独立他我，继承 init.json，用默认预设启动）｜`settings`（只读，以五字段列出 Avatar 的固定默认值与策略）｜`manual`（只读，返回 avatar 手册全文及其 `manual_path`）。Avatar 已无 `rules` 动作；网络法则协议详见 psyche-manual。
- `input`：必填，为所选 `action` 独有之严格封闭对象。属于他动作分支之字段一律在任何写入前拒斥。
- `reasoning`：必填，根层字段，非动作输入。`action=spawn` 时即为任务书，成为他我第一道提示。
- `summarize`：可选，根层布尔，默认为否。仅作结果后处理之开关，永不传入动作实现。
- `input.name`：他我之真名（action=spawn 必填）。兼作 .lingtai/ 下目录名。单段：字母/数字/下划线/连字符，最长64字。
- `input.type`：'shallow'（默认，初生）：白纸，仅 init.json。'deep'（二重身）：完整复制灵台、简、典。
- `input.comment`：写入他我系统提示之持久注解（跨凝蜕/刷新/休眠不变）。不承自父。非必要勿填。
- `input.dry_run`：预览化出而不生进程。用于提交前审查。
- `input.confirm`：确认已审阅任务并决意化出。任务空白/过短/似试时必填。
