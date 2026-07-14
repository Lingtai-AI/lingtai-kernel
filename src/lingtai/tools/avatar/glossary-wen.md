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
---
**名相对照**

- `avatar_spawn`：化出独立他我。承 init.json，以默认预设启。详见 avatar-manual 技。
- `avatar_rules`：设网法以布一切后嗣（需 karma）。详见 avatar-manual 技。
- `name`：他我真名（必填）。亦为 .lingtai/ 下目录之名。单段：字母/数/下划线/连字，至长六十四。
- `type`：'shallow'（默认，初生）：白纸，仅 init.json。'deep'（二重身）：全拷灵台、简、典。
- `comment`：他我提示之恒注（跨蜕/刷/眠不去）。不承自父。无事勿填。
- `dry_run`：预览而不化。用于提交前省察。
- `confirm`：确认已审任务且决意化。任务空/短/似试时必填。
- `rules_content`：avatar_rules 所需法则之文。纯文每行一则。不可议之约束，布一切后嗣。
