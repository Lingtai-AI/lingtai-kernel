---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.pad
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/pad/glossary-en.md
- src/lingtai/tools/pad/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `pad` tool package (lingtai.tools.pad); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever pad's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---

**术语对照**

- `pad`：提示板域（`system/pad.md` 与固定引用清单 `system/pad_append.json`）；无公开工具根。
- 其手册以 `psyche(action='pad', input={}, reasoning='...')` 取之，唯还手册。二源皆以 `shell`（核实后的精确改写）改之，再以 `context.rebuild` 生效；旧 `pad.append` 已废，无别名。
