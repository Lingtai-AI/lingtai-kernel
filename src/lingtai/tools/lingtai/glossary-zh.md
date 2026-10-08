---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.lingtai
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/lingtai/glossary-en.md
- src/lingtai/tools/lingtai/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `lingtai` tool package (lingtai.tools.lingtai); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever lingtai's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---

**术语对照**

- `lingtai`：灵台身份域（`system/lingtai.md`）；无公开工具根。
- 其手册以 `psyche(action='lingtai', input={}, reasoning='...')` 取之，唯还手册，不改盘、不热载；durable 之改以 `shell`（核实后的精确改写）加 `context.rebuild`。
