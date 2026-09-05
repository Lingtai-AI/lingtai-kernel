---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.avatar
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/avatar/glossary-en.md
- src/lingtai/tools/avatar/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `avatar` tool package (lingtai.tools.avatar); body must stay non-empty and distinct from glossary-zh.md (tool_glossary.py enforces both). Update in lockstep with glossary-en.md/glossary-zh.md whenever avatar's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**名相对照**

- `avatar`：唯一公开之器，以 `action` 分遣，每遣各有严整自专之 `input`。详见 avatar-manual 技。
- `action`：必填，无默认。`spawn`（化出独立他我，承 init.json，以默认预设启）｜`settings`（唯读，以五栏列 Avatar 之定默认与策）｜`manual`（只读，还 avatar 手册全文及其 `manual_path`）。Avatar 已无 `rules` 之遣；网法之协见于 psyche-manual。
- `input`：必填，乃所遣一动作自专之严封之器。他遣分支之名，未及动手之先即斥。
- `reasoning`：必填，居根，非动作之入。`action=spawn` 时即任务之书，为他我第一言。
- `summarize`：可选，居根之布尔，默然为否。惟司果之后治，终不入动作之实。
- `input.name`：他我真名（action=spawn 必填）。亦为 .lingtai/ 下目录之名。单段：字母/数/下划线/连字，至长六十四。
- `input.type`：'shallow'（默认，初生）：白纸，仅 init.json。'deep'（二重身）：全拷灵台、简、典。
- `input.comment`：他我提示之恒注（跨蜕/刷/眠不去）。不承自父。无事勿填。
- `input.dry_run`：预览而不化。用于提交前省察。
- `input.confirm`：确认已审任务且决意化。任务空/短/似试时必填。
