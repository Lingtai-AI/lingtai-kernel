---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.write
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/write/glossary-en.md
- src/lingtai/tools/write/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `write` tool package (lingtai.tools.write); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever write's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**名相对照**

- `write`：创卷或覆写之器。父目录自动创建。用于新建文卷或完整重写。小改现有文卷，当用改（edit）。
- `file_path`：文卷之绝对路径
- `content`：欲写入之内容
