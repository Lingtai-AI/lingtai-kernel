---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.knowledge
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/knowledge/glossary-en.md
- src/lingtai/tools/knowledge/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `knowledge` tool package (lingtai.tools.knowledge); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever knowledge's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**术语对照**

- `knowledge`：私有长存知识域；无公开工具根。其手册经 `psyche(action='knowledge', input={}, reasoning='...')` 取之，唯还手册，不扫目录、不改盘。
- 条目以 `shell`（核实后的精确改写）直书 `knowledge/<名>/KNOWLEDGE.md`，再以 `context.rebuild` 使之现于提示；旧 `knowledge.info` 已废，无别名。
- `action`：psyche 之动作，取 `knowledge` 即还本域手册；旧 `info` 已废。
- `input`：严格空对象（strict-empty）：不接受任何字段，多余字段在动作执行前即被拒。
- `reasoning`：必填的根级调用理由，属宿主审计元数据，绝不下沉入 input。
- `summarize`：可选的根级结果后处理开关，默认为假；非动作入参。手册宜留假，以免略去确切步骤。
