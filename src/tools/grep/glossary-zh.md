---
kind: tool-glossary
schema_version: 1
tool_package: tools.grep
language: zh
---
**术语对照**

- `grep`：在文件内容中搜索匹配正则表达式的行。返回匹配行及其文件路径和行号。对目录进行递归搜索。使用 glob 过滤器限定特定文件类型。
- `pattern`：要搜索的正则表达式模式
- `path`：要搜索的文件或目录
- `glob`：文件 glob 过滤器（例如 '*.py'）
- `max_matches`：最大返回匹配数
- `summary`：可选。默认 false。为 true 时，该 tool 照常执行，原始结果会完整保存到持久日志（可按 tool_call_id 取回），但在结果进入你的上下文之前，会被一段由你的 `reasoning` 字段驱动的 LLM 生成摘要替换——所以请在 `reasoning` 中明确说明要保留什么。仅当预期输出很大（>10k 字符）且你不需要精确原文时才设为 true。需要精确的行/文件/diff/stderr 原文时请保持 false。摘要非权威；若原始结果超过 500,000 字符则不生成摘要，你会收到一条指向已保存原始结果的拒绝信息。
