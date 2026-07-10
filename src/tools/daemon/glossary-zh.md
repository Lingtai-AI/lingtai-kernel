---
kind: tool-glossary
schema_version: 1
tool_package: tools.daemon
language: zh
---
**术语对照**

- `daemon`：神識——派遣短暂子智能体（分神）以隔离上下文。每个分神是一次性 LLM 会话，拥有独立的上下文窗口；共享你的工作目录但完成后不保留任何记忆。用分神将嘈杂、占上下文的工作从自身剥离：大规模文件扫描、探索性搜索、多步研究、批量转换——凡是你只需结论的事。重要：分神结果截断至约 2000 字符。如需详细输出，指示分神将报告写入文件，之后自行读取。操作：emanate（分，派遣一批），list（观，查看状态），ask（问，后续跟进），check（察，查看近期事件），reclaim（收，终止所有）。每个终态都会被推送通知且仅一次——完成（done）、失败（failed）、取消（cancelled）或超时（timeout）皆然——故派遣后你可放心进入空闲、等待通知即可，无需轮询「是否完成」。通知含分神 id、终态、任务摘要及结果/错误路径；据此以 daemon(action="check", id=...) 处置。用此工具前，必先读 `daemon-manual` 技能（涵盖检查模式、轮询节奏与预设/能力继承），无例外。
- `action`：执行的操作：'emanate'（派遣分神），'list'（显示状态），'ask'（跟进消息），'check'（查看某分神最近的事件），'reclaim'（终止所有）
- `tasks`：'emanate' 的任务列表。每项：{task: str（必填——包含保存位置的指令），tools: list[str]（必填——能力名称，如 ['file', 'bash']），skills: list[str]（可选——skill 目录或 SKILL.md 路径，会渲染进 daemon prompt），mcp: list[object]（可选——完整的一次性 MCP 注册对象，会序列化进 daemon 上下文；LingTai backend 还会将其作为本次任务的 MCP tools 加载），preset: str（可选——预设文件路径，使用 system(action='presets') 输出中的 name）。省略 preset 则继承父 agent 的常规工具面。父 agent 的 MCP 工具不会自动继承；需要时请在 mcp 中提供完整注册对象。预设继承与能力解析详见 daemon-manual。
- `tasks.skills`：此 daemon 任务的可选 skill 上下文。字符串数组；每项可以是 skill 目录（内含 SKILL.md），也可以是直接的 SKILL.md 文件路径。相对路径按父 agent 工作目录解析。daemon runtime 会解析每个 skill 的 frontmatter，并把紧凑 YAML skill list 注入本次 prompt；用 system_prompt 说明何时/如何应用这些 skills。
- `tasks.mcp`：此 daemon 任务的可选一次性 MCP 注册。对象数组；每项为完整 MCP 注册对象：{name, transport/type: stdio|http，stdio 使用 command+args+env，http 使用 url+headers}。这些注册会以 YAML 序列化进 daemon prompt；LingTai backend 还会将其启动为本次任务专属 MCP client 并暴露其 tools。CLI backends 会收到同一份序列化注册作为上下文，如其运行时支持 MCP 可自行加载。prompt 中的 env/header secret 会被脱敏。
- `tasks.preset`：可选预设文件路径。必须是 .json/.jsonc 路径，使用 system(action='presets') 返回的 name 字段。不要用简写名——用 presets 列表里的完整 name。示例：'~/.lingtai-tui/presets/saved/cheap.json'。省略则继承父灵的常规（非 MCP）工具面；MCP 请另在 `mcp` 中提供任务级注册。
- `tasks.backend_options`：仅适用于 'claude-code' / 'codex' / 'opencode' / 'mimocode' / 'qwen-code' / 'oh-my-pi' / 'kimicode' / 'cursor' 后端的自由 CLI 选项（lingtai 后端会忽略）。JSON 对象，键为 flag 名，值为：true → 仅传 flag（如 {"search": true} → --search）；string/int/float → '--flag <值>'；标量数组 → '--flag <v1> --flag <v2>'；false/null 则省略。键里的下划线会转成短横线；嵌套对象与不安全键会被拒绝。只在启动分神时生效，不会带到 `ask` 跟进里。要查看当前支持的 flag，请在 bash 里跑 'claude --help'、'codex exec --help'、'opencode run --help'、'mimo run --help'、'qwen --help'、'omp --help'、'kimi --help' 或 'agent --help'——CLI 的 flag 会随版本变化，这里是直通字段而非固定列表。详见 daemon-manual。
- `tasks.system_prompt`：可选的一次性行为契约，只附加到此 daemon 任务：角色、约束、工具使用策略、协作边界与安全姿态。留空或省略表示使用默认 daemon 身份。它可以引导 daemon 的工作方式，但不能覆盖工具可用性、取消/超时限制或工具执行/审批 gate。
- `id`：'ask' 操作的分神 ID（如 'em-1'）
- `message`：'ask' 操作的跟进消息
- `last`：'check' 操作：从 events.jsonl 返回最近多少条事件。'list' 操作：筛选后最多显示多少条列表项，必须为正整数。check 默认 20；list 不传则不限制。
- `truncate`：'check' 操作：返回事件中任意字符串字段的最大长度，超出部分以省略号截断。默认 500。设为 0 以关闭截断。
- `contains`：'list' 操作：在 daemon 任务、prompt 预览、result 预览、backend、status、group_id、run_id 与可见调用参数中进行不区分大小写的子串搜索。
- `status`：'list' 操作：可选状态筛选，例如 running、done、failed、cancelled、timeout 或 all。
- `include_done`：'list' 操作：除当前跟踪的运行项外，也包含已完成的历史 daemon run 目录。默认 true。
- `summary`：可选。默认 false。为 true 时，该 tool 照常执行，原始结果会完整保存到持久日志（可按 tool_call_id 取回），但在结果进入你的上下文之前，会被一段由你的 `reasoning` 字段驱动的 LLM 生成摘要替换——所以请在 `reasoning` 中明确说明要保留什么。仅当预期输出很大（>10k 字符）且你不需要精确原文时才设为 true。需要精确的行/文件/diff/stderr 原文时请保持 false。摘要非权威；若原始结果超过 500,000 字符则不生成摘要，你会收到一条指向已保存原始结果的拒绝信息。
- `max_turns`：'emanate' 操作：每个分神的工具循环最大轮数。默认：父灵上限（1000）。简单任务可设较小之值以加约束。
- `timeout`：'emanate' 操作：整批的最大壁钟秒数。默认：父灵上限（3600 秒）。超时仍在运行的分神由看门狗强制终止。
