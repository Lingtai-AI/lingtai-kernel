---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.write
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/write/glossary-en.md
- src/lingtai/tools/write/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `write` tool package (lingtai.tools.write); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever write's public tool schema changes.
---
**术语对照**

- `write`：创建或覆盖文件。父目录会自动创建。用于创建新文件或完整重写。对现有文件的小修改，优先使用 edit。
- `file_path`：文件的绝对路径
- `content`：要写入的内容
