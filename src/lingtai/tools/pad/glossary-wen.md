---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.pad
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/pad/glossary-en.md
- src/lingtai/tools/pad/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `pad` tool package (lingtai.tools.pad); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever pad's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---

**名相对照**

- `pad`：志板之域（`system/pad.md` 及所钉之簿 `system/pad_append.json`）；无公开之根。
- 其文以 `psyche(action='pad', input={}, reasoning='...')` 召之，唯还其文。二源皆以 `shell`（核而后易）易之，继以 `context.rebuild` 而行；旧 `pad.append` 已废，无所别名。
