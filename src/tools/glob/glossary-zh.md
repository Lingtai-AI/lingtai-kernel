---
kind: tool-glossary
schema_version: 1
tool_package: tools.glob
language: zh
---
**术语对照**

- `glob`：查找匹配 glob 模式的文件。使用 '**/' 进行递归搜索（例如 '**/*.py' 查找所有 Python 文件）。返回排序后的匹配文件路径列表。
- `pattern`：Glob 模式（例如 '**/*.py'）
- `path`：搜索目录
- `summary`：可选。默认 false。为 true 时，该 tool 照常执行，原始结果会完整保存到持久日志（可按 tool_call_id 取回），但在结果进入你的上下文之前，会被一段由你的 `reasoning` 字段驱动的 LLM 生成摘要替换——所以请在 `reasoning` 中明确说明要保留什么。仅当预期输出很大（>10k 字符）且你不需要精确原文时才设为 true。需要精确的行/文件/diff/stderr 原文时请保持 false。摘要非权威；若原始结果超过 500,000 字符则不生成摘要，你会收到一条指向已保存原始结果的拒绝信息。
