---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.knowledge
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/knowledge/glossary-en.md
- src/lingtai/tools/knowledge/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `knowledge` tool package (lingtai.tools.knowledge); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever knowledge's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**名相对照**

- `knowledge`：私藏长存之知域；无公开之根。其手册以 `psyche(action='knowledge', input={}, reasoning='...')` 召之，唯还其文，不扫其目，不易其盘。
- 经卷以 `shell`（核而后易）直书 `knowledge/<名>/KNOWLEDGE.md`，继以 `context.rebuild` 而显于提示；旧 `knowledge.info` 已废，无所别名。
- `action`：psyche 之所召，取 `knowledge` 则还本域之文；旧 `info` 已废。
- `input`：严空之器：不纳一字，逾者未行而先拒。
- `reasoning`：必具之根由，宿主稽核之属，终不入 input。
- `summarize`：根上之约，可有可无，默为否；非动作之入。取文宜留否，恐略其确要。
