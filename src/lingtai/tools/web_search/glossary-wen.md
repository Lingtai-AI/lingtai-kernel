---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.web_search
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/web_search/glossary-en.md
- src/lingtai/tools/web_search/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `web_search` tool package (lingtai.tools.web_search); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever web_search's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**名相对照**

- `web_search`：游历之器——搜寻大千世界之最新信息。用于实时数据、近期事件、文档或超出训练所知之内容。返排序后之搜索结果，含题、URL 与摘要。用此器前，必先读 `web-browsing` 一技（含具体 URL 之取、PDF 之取、JS 动渲之页、隐身抓取、备用之径），无所例外。
- `query`：搜寻之问
