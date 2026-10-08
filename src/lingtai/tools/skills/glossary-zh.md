---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.skills
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/skills/glossary-en.md
- src/lingtai/tools/skills/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `skills` tool package (lingtai.tools.skills); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever skills's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---

**术语对照**

- `skills`：技能目录域；无公开工具根。其手册以 `psyche(action='skills', input={}, reasoning='...')` 取之，唯还手册，不扫目录、不注提示。
- 技能以 `shell`（核实后的精确改写）写入 `.library/custom/<名>/SKILL.md`，再以 `context.rebuild` 重扫目录；旧 `skills.info` 已废，无别名。
