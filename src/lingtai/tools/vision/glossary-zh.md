---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.vision
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/vision/glossary-en.md
- src/lingtai/tools/vision/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `vision` tool package (lingtai.tools.vision); body must stay non-empty. Update in lockstep with glossary-en.md/glossary-wen.md whenever vision's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**术语对照**

- `vision`：使用 LLM 的视觉能力分析图像。支持 JPEG、PNG 和 WebP。可以对图像提出任何问题——描述内容、识别文字、解读图表、识别物体、评估风格或氛围。结合 draw 可以先生成图像再分析。
- `image_path`：图像文件路径
- `question`：关于图像的问题
