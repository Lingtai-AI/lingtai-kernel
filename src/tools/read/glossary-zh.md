---
kind: tool-glossary
schema_version: 1
tool_package: tools.read
language: zh
---
**术语对照**

- `read`：读取文本文件的内容，返回带行号的文本。仅支持文本文件，不能读取二进制、图片或音频。使用 read 前，尤其是大文件、完整读取、截断或 line_truncated 处理，必须先读 read-manual skill。如遇非 UTF-8 或需要谨慎搜索/编辑，请先读 file-manual。用 offset/limit 控制行窗口，并可用可选 max_chars 控制单次字符预算。read 默认预算为 100 000 字符；max_chars 可在单次调用中放大/缩小，但会被不可配置的 runtime 硬上限 200 000 字符 clamp。读取成功仍可能截断：检查 truncated=true、cap_chars、returned_chars、next_offset、remaining_lines_estimate 和 line_truncated，并用 next_offset 续读至结束。若 line_truncated=true，所示物理行只是前缀；next_offset 会跳到下一行，不能恢复该行隐藏尾部。按 read-manual 推荐，先用 bash/Python 查看文件大小、总行数、最长行等 metadata/stats，再决定 offset/limit/max_chars；长行内容用 grep/sed/Python 等定向处理。
- `file_path`：文件的绝对路径
- `offset`：起始行号（从 1 开始）
- `limit`：最大读取行数
- `max_chars`：可选的单次读取字符预算。默认 100 000；超过 runtime 硬上限的值会被 clamp 到 200 000。大文件设置此参数前先读 read-manual。
- `summary`：可选。默认 false。为 true 时，该 tool 照常执行，原始结果会完整保存到持久日志（可按 tool_call_id 取回），但在结果进入你的上下文之前，会被一段由你的 `reasoning` 字段驱动的 LLM 生成摘要替换——所以请在 `reasoning` 中明确说明要保留什么。仅当预期输出很大（>10k 字符）且你不需要精确原文时才设为 true。需要精确的行/文件/diff/stderr 原文时请保持 false。摘要非权威；若原始结果超过 500,000 字符则不生成摘要，你会收到一条指向已保存原始结果的拒绝信息。
