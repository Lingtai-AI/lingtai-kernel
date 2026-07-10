---
kind: tool-glossary
schema_version: 1
tool_package: tools.knowledge
language: zh
---
**术语对照**

- `knowledge`：【路标工具】此工具不会创建、编辑、搜索或加载知识条目；`info` 只重新扫描 knowledge 目录并返回健康状态；`manual` 才返回 knowledge-manual 正文。你的跨凝蜕长存私有知识目录——所学、所断、所悟之记。每条目皆为 knowledge/<名>/ 下之一夹，内含 KNOWLEDGE.md（YAML frontmatter 须有 name 与 description），可附脚本、素材、笔记、原始日志等支撑文件。系统提示中之知识目录为 YAML 列表——每条目为一 `- name:` 块，附 `location:` 与 `description:` 字段；正文按需以 read 工具取之，与 skills 同。知识为汝私有：条目可引本地路径、邮件 ID、日志——此皆为 skills 所不可依赖之物。新条目以 write/edit 直接写入 knowledge/<名>/KNOWLEDGE.md；修订同法。调 info 可刷新目录并查看健康状态。用此工具前，必先读 `knowledge-manual` 技能，无例外。
- `action`：info：重新扫描 knowledge/ 并返回运行时健康快照（目录大小、根路径、损坏条目），不带 manual 正文。manual：只返回 knowledge-manual 技能正文。
