---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.edit
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/edit/glossary-en.md
- src/lingtai/tools/edit/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `edit` tool package (lingtai.tools.edit); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever edit's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**名相对照**

- `edit`：精确替换文中之字。若 old_string 未见或有歧义则不成。
- `file_path`：文卷之绝对路径
- `old_string`：欲查且替之精确文字
- `new_string`：替换后之文字
- `replace_all`：替换所有匹配
