---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.skills
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/skills/glossary-en.md
- src/lingtai/tools/skills/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `skills` tool package (lingtai.tools.skills); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever skills's public tool schema changes.
---
**术语对照**

- `skills`：【路标工具】此工具不会编写、固定、发布、安装或执行技能；`info` 只重新协调/扫描技能目录并返回健康状态，不带 manual 正文；`manual` 才返回 skills-manual 正文。你的器灵专属技能目录。系统提示词中的技能目录为 YAML 列表——每项技能为一 `- name:` 块，附 `location:` 与 `description:` 字段——涵盖当前所有可用的技能。用此工具前（编写、固定、发布或管理技能），必先读 `skills-manual` 技能——调用 `info` 即可获取其正文与运行时健康快照，无例外。
- `action`：info：刷新/协调技能目录并返回运行时健康快照（目录规模、解析后的路径、问题列表），不带 manual 正文。manual：只返回 skills-manual 技能正文。
