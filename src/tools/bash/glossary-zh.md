---
kind: tool-glossary
schema_version: 1
tool_package: tools.bash
language: zh
---
**术语对照**

- `bash`：执行指令，返 stdout/stderr。可运行系统上一切可用之程——脚本、git、curl、pip、数据管道等。返回 exit_code、stdout、stderr，并附 ok（布尔）与 command_status（'success'/'failed'）。要点：即便命令失败，顶层 status 仍为 'ok'——它仅表示 shell 已执行。务必检查 exit_code/ok 并阅读 warning 字段（标明非零退出、Python 回溯、缺失模块）；切勿仅凭 status 断定成功。避免大范围递归扫描（find … -name、rglob、os.walk、glob('**')）——易超时；优先用 `rg --files`。JSONL 须逐行解析，勿当作单个 JSON。支持异步：async=true 获取 job_id，再用 poll/cancel 查之。用此工具前，必先读 `bash-manual` 技能（涵盖定时任务、异步规范与进阶用法），无例外。
- `action`：执行动作：'run'（默认）执行命令，'poll' 查询异步任务状态，'cancel' 终止异步任务
- `command`：要执行的 shell 命令
- `timeout`：超时秒数（默认：30，仅同步执行时生效）
- `working_dir`：命令的工作目录（可选）。留空或传空字符串即使用 agent 工作目录。必须位于 agent 工作目录沙箱内；沙箱外路径会被拒绝。若要操作外部仓库/路径，请将 working_dir 保持为 agent 目录，并在 command 中显式 cd，例如 cd /absolute/path && ...
- `async`：后台运行命令并立即返回 job_id（默认：false，仅 action='run' 时生效）
- `job_id`：异步任务 ID，用于 poll/cancel（由异步 run 返回）
- `summary`：可选。默认 false。为 true 时，该 tool 照常执行，原始结果会完整保存到持久日志（可按 tool_call_id 取回），但在结果进入你的上下文之前，会被一段由你的 `reasoning` 字段驱动的 LLM 生成摘要替换——所以请在 `reasoning` 中明确说明要保留什么。仅当预期输出很大（>10k 字符）且你不需要精确原文时才设为 true。需要精确的行/文件/diff/stderr 原文时请保持 false。摘要非权威；若原始结果超过 500,000 字符则不生成摘要，你会收到一条指向已保存原始结果的拒绝信息。
