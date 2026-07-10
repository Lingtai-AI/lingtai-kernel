---
kind: tool-glossary
schema_version: 1
tool_package: tools.glob
language: wen
---
**名相对照**

- `glob`：以式寻卷。用'**/'递归搜寻（如'**/*.py'寻尽 Python 文卷）。返排序后之匹配路径。
- `pattern`：Glob 式（如'**/*.py'）
- `path`：搜寻之目录
- `summary`：可选。默认 false。设 true 时，此 tool 照常运行，原始结果完存于持久日志（可凭 tool_call_id 取回）；然结果入尔上下文前，先以尔 `reasoning` 字段所驱之 LLM 摘要代之——故 `reasoning` 当明言所欲存者。唯料输出甚巨（逾一万字符）且无需精确原文时，方设 true。需精确之行/文件/diff/stderr 原文者，留 false。摘要非权威；原始结果逾五十万字符，则不生摘要，尔得一拒辞，指向所存之原始结果。
