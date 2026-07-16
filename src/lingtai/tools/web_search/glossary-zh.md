---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.web_search
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/web_search/glossary-en.md
- src/lingtai/tools/web_search/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `web_search` tool package (lingtai.tools.web_search); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever web_search's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**术语对照**

- `web_search`：搜索网络获取最新信息。用于实时数据、近期事件、文档或超出训练知识范围的内容。返回排序后的搜索结果，包含标题、URL 和摘要。用此工具前，必先读 `web-browsing` 技能（涵盖特定 URL 抓取、PDF 下载、JS 渲染页、隐身抓取与回退 API），无例外。
- `query`：搜索查询
