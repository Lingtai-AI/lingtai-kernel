---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.grep
language: wen
---
**名相对照**

- `grep`：以正则式搜寻文中之字。返匹配之行及其文卷路径与行号。对目录递归搜寻。以 glob 过滤器限定文卷类型。
- `pattern`：欲搜之正则式
- `path`：欲搜之文卷或目录
- `glob`：文卷 glob 过滤器（如'*.py'）
- `max_matches`：至多返回之匹配数
- `summary`：可选。默认 false。设 true 时，此 tool 照常运行，原始结果完存于持久日志（可凭 tool_call_id 取回）；然结果入尔上下文前，先以尔 `reasoning` 字段所驱之 LLM 摘要代之——故 `reasoning` 当明言所欲存者。唯料输出甚巨（逾一万字符）且无需精确原文时，方设 true。需精确之行/文件/diff/stderr 原文者，留 false。摘要非权威；原始结果逾五十万字符，则不生摘要，尔得一拒辞，指向所存之原始结果。
