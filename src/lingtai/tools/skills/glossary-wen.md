---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.skills
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/skills/glossary-en.md
- src/lingtai/tools/skills/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `skills` tool package (lingtai.tools.skills); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever skills's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---

**名相对照**

- `skills`：诸技之目域；无公开之根。其文以 `psyche(action='skills', input={}, reasoning='...')` 召之，唯还其文，不扫其目，不注于提示。
- 技以 `shell`（核而后易）书于 `.library/custom/<名>/SKILL.md`，继以 `context.rebuild` 重扫其目；旧 `skills.info` 已废，无所别名。
