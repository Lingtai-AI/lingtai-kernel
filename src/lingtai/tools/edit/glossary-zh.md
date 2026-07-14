---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.edit
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/edit/glossary-en.md
- src/lingtai/tools/edit/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `edit` tool package (lingtai.tools.edit); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever edit's public tool schema changes.
---
**术语对照**

- `edit`：精确替换文件中的字符串。如果 old_string 未找到或存在歧义则失败。
- `file_path`：文件的绝对路径
- `old_string`：要查找并替换的精确文本
- `new_string`：替换后的文本
- `replace_all`：替换所有匹配项
