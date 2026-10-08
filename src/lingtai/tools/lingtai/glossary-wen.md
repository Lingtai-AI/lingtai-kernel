---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.lingtai
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/lingtai/glossary-en.md
- src/lingtai/tools/lingtai/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `lingtai` tool package (lingtai.tools.lingtai); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever lingtai's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---

**名相对照**

- `lingtai`：灵台之域（`system/lingtai.md`）；无公开之根。
- 其文以 `psyche(action='lingtai', input={}, reasoning='...')` 召之，唯还其文，不易其盘，不即更今提示；欲改长存者，以 `shell`（核而后易）继以 `context.rebuild`。
