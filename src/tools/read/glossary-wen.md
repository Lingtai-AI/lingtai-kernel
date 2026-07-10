---
kind: tool-glossary
schema_version: 1
tool_package: tools.read
language: wen
---
**名相对照**

- `read`：阅文本卷，返带行号之文。唯文本可读，二进制、图像、音声不可。凡用 read，尤大卷、全卷续读、截断、line_truncated 之处置，必先读 read-manual 一技。若非 UTF-8 或须慎搜慎改，先读 file-manual。以 offset/limit 定行段；可以 max_chars 定此召字符之额。read 默额十万字；max_chars 可于一召增减，然不得逾 runtime 不可配置之硬限二十万字，逾则裁之。阅之虽成，犹或截断：当察 truncated=true、cap_chars、returned_chars、next_offset、remaining_lines_estimate、line_truncated；以 next_offset 续阅至尽。若 line_truncated=true，则所示物理行唯前缀；next_offset 越至下行，不复其隐尾。当循 read-manual，以 bash/Python 先察卷大、行数、最长行等 metadata/stats，再定 offset/limit/max_chars；长行则以 grep/sed/Python 等定向处之。
- `file_path`：文卷之绝对路径
- `offset`：起始行号（自一起算）
- `limit`：至多读取之行数
- `max_chars`：可选：此召 read 内容之字符额。默认十万；逾 runtime 硬限者裁至二十万。大卷设此参数前，先读 read-manual。
- `summary`：可选。默认 false。设 true 时，此 tool 照常运行，原始结果完存于持久日志（可凭 tool_call_id 取回）；然结果入尔上下文前，先以尔 `reasoning` 字段所驱之 LLM 摘要代之——故 `reasoning` 当明言所欲存者。唯料输出甚巨（逾一万字符）且无需精确原文时，方设 true。需精确之行/文件/diff/stderr 原文者，留 false。摘要非权威；原始结果逾五十万字符，则不生摘要，尔得一拒辞，指向所存之原始结果。
