---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.bash
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/bash/glossary-en.md
- src/lingtai/tools/bash/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the canonical `shell` tool (retained implementation package lingtai.tools.bash); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever shell's public tool schema changes.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**名相对照**

- `shell`：执行指令，返 stdout/stderr。可运行系统上一切可用之程——脚本、git、curl、pip、数据管道等。返 exit_code、stdout、stderr，兼附 ok（真伪）与 command_status（'success'/'failed'）。须知：命令虽败，顶层 status 仍作 'ok'——此仅言 shell 已运，非言命令已成。必察 exit_code/ok，且阅 warning 一字（标非零之退、Python 之回溯、缺失之模块）；勿独凭 status 而断其成。忌大范围递归之扫（find … -name、rglob、os.walk、glob('**')）——易致超时；宜先用 `rg --files`。JSONL 当逐行而解，勿混作一 JSON。支持异步：设 async=true 取 job_id，后以 poll/cancel 查之。用此器前，必先读 `shell-manual` 一技（含定时之设、异步之规、进阶之用），无所例外。
- `action`：所行之事：'run'（默认）执行指令，'poll' 查异步任务之状，'cancel' 斩异步任务
- `command`：欲执行之指令
- `timeout`：超时秒数（默认：30，唯同步执行时生效）
- `working_dir`：指令之工作目录（可选）。留空或传空字符串即用 agent 工作目录。须在 agent 工作目录沙箱之内；沙箱外路径会被拒。若需操作外部仓库/路径，请令 working_dir 保持为 agent 目录，并在 command 中显式 cd，如 cd /absolute/path && ...
- `async`：后台运行指令，即返 job_id（默认：false，唯 action='run' 时生效）
- `reminder`：异步兜底唤醒之延迟秒数（默认 1800）。顶层 schema 要求此字段，故经 provider 校验之同步 run、poll、cancel 亦携之；运行时唯异步 run 用且校验之，余动作忽略。初期之限，惟崩溃兜底；有界且持久之 return-handoff，禁次 manager 于首 manager 尚未毕成功返转之际先发旧限。惟守尚有效而原子书 `returned_at + reminder`（或精确成败已于有效守内落盘），方报启成；守既逾期，后复之 owner 携 `job_id/pid` 明告“犹可 poll”之误，且存崩溃兜底。终限届而任务仍非终态，方发 system 通知促 poll。取消中之持久 suppressing 亦有界，逾期可复告。若已得精确终态，则抑“或仍在行”之旧告，改由 Bash completion 唤醒。
- `job_id`：异步任务之号，用于 poll/cancel（由异步 run 所返）
- `summary`：可选。默认 false。设 true 时，此 tool 照常运行，原始结果完存于持久日志（可凭 tool_call_id 取回）；然结果入尔上下文前，先以尔 `reasoning` 字段所驱之 LLM 摘要代之——故 `reasoning` 当明言所欲存者。唯料输出甚巨（逾一万字符）且无需精确原文时，方设 true。需精确之行/文件/diff/stderr 原文者，留 false。摘要非权威；原始结果逾五十万字符，则不生摘要，尔得一拒辞，指向所存之原始结果。
