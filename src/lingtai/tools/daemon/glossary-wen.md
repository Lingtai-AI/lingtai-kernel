---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.daemon
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/daemon/glossary-en.md
- src/lingtai/tools/daemon/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `daemon` tool package (lingtai.tools.daemon); body must stay non-empty and distinct from glossary-zh.md. Update in lockstep with glossary-en.md/glossary-zh.md whenever daemon's public tool schema changes.
---
**名相对照**

- `daemon`：神識——遣短暂之分神以隔上下文。分神者，一次性 LLM 会话，自具上下文窗，共享汝之工作目录然毕后不存记忆。以分神将嘈杂、耗上下文之事剥离己身：大规模文件扫描、探索之搜、多步之研、批量之转——凡所求唯结论者。默 4 并发（可于每灵之 init.json 中配；daemon(action="list") 报实之上限）。要：分神之果截至约 2000 字。如需详出，令分神书报告于文卷，毕后自取。操作：emanate（分），list（观），ask（问），check（察），reclaim（收）。凡入终境，系统皆自报，且止一回——成（done）、败（failed）、撤（cancelled）、逾时（timeout）一也——故遣后可安然入闲、候报即可，无须屡问「成否」。报中具分神之 id、终境、任务之要及果或谬之径；据之以 daemon(action="check", id=...) 处之。用此器前，必先读 `daemon-manual` 一技（含察法、节度与预设、能之承袭），无所例外。
- `action`：所执之操作：'emanate'（分，遣出分神），'list'（观，显诸状），'ask'（问，跟进），'check'（察，览某分神近事），'reclaim'（收，尽数终止）
- `tasks`：'emanate' 之任务列。每项：{task: 文（必填，含存放处之令），tools: 文列（必填，诸能名，如 ['file','bash']），skills: 文列（可选，skill 目录或 SKILL.md 路径，渲入 daemon 心令），mcp: 对象列（可选，完整之一务 MCP 注册，序入 daemon 上下文；LingTai 后端亦按之临时载入工具），preset: 文（可选，预设文件路径，取 system(action='presets') 所示 name）。省 preset 则承父灵常规工具面。父灵 MCP 工具不复恒承；须于 mcp 具列完整注册。预设承继与能力解析见 daemon-manual。
- `tasks.skills`：此 daemon 一务之可选 skill 上下文。字符串列；每项可为 skill 目录（内有 SKILL.md），亦可直指 SKILL.md。相对路径按父灵工作目录解之。daemon runtime 解析诸 skill frontmatter，渲为紧凑 YAML skill list 注入本次心令；以 system_prompt 明其何时/如何用之。
- `tasks.mcp`：此 daemon 一务之可选一次性 MCP 注册。对象列；每项为完整 MCP 注册：{name, transport/type: stdio|http；stdio 用 command+args+env，http 用 url+headers}。诸注册以 YAML 序入 daemon 心令；LingTai 后端亦临时启为本务 MCP client，显其 tools。CLI 后端得同一序列化注册为上下文，若其运行时可载 MCP 则自用之。心令中 env/header 密值皆脱敏。
- `tasks.preset`：可省之预设文件路径。必以 .json/.jsonc 结尾，用 system(action='presets') 所返之 name 字段。勿以简写名——当用 presets 列表中之完整 name。示例：'~/.lingtai-tui/presets/saved/cheap.json'。省则承父灵常规（非 MCP）工具面；MCP 当于 `mcp` 另具任务注册。
- `tasks.backend_options`：仅施于 'claude-code' / 'codex' / 'opencode' / 'mimocode' / 'qwen-code' / 'oh-my-pi' / 'kimicode' / 'cursor' 后端之自由 CLI 选项（lingtai 后端弗顾）。JSON 对象，键为 flag 之名，值为：true → 唯传 flag（如 {"search": true} → --search）；string/int/float → '--flag <值>'；标量数组 → '--flag <v1> --flag <v2>'；false/null 则省其 flag。键中之下划线易为短横；嵌套对象与不安之键皆见拒。仅于启分神之时生效，弗及于 `ask` 跟进。欲知所支之 flag，当于 bash 中行 'claude --help'、'codex exec --help'、'opencode run --help'、'mimo run --help'、'qwen --help'、'omp --help'、'kimi --help' 或 'agent --help'——CLI 之 flag 随版本而易，此字段乃直通而非定列。详见 daemon-manual。
- `tasks.system_prompt`：可选一次性心令，仅附此 daemon 一务：定其身、约其行、明工具用法、协作边界与安危姿态。留白或省之，即用 daemon 默认之身。可导其行事之法，不可越工具、取消/时限、执行/核准之门。
- `id`：'ask' 操作所用分神之 ID（如 'em-1'）
- `message`：'ask' 操作之跟进之言
- `last`：'check' 之操作：自 events.jsonl 返回几条近事。'list' 之操作：筛后至多显几条目录项，须为正整数。check 默二十；list 不传则不限。
- `truncate`：'check' 之操作：返事所含字符串字段之至长，过此则以省略缀之。默五百。设为零则不截。
- `contains`：'list' 之操作：于 daemon 任务、prompt 摘、result 摘、backend、status、group_id、run_id 与可见调用参数中作不分大小写之子串搜索。
- `status`：'list' 之操作：可选状态筛，如 running、done、failed、cancelled、timeout 或 all。
- `include_done`：'list' 之操作：除当下追踪之运行项外，亦含已竟历史 daemon run 目录。默认 true。
- `summary`：可选。默认 false。设 true 时，此 tool 照常运行，原始结果完存于持久日志（可凭 tool_call_id 取回）；然结果入尔上下文前，先以尔 `reasoning` 字段所驱之 LLM 摘要代之——故 `reasoning` 当明言所欲存者。唯料输出甚巨（逾一万字符）且无需精确原文时，方设 true。需精确之行/文件/diff/stderr 原文者，留 false。摘要非权威；原始结果逾五十万字符，则不生摘要，尔得一拒辞，指向所存之原始结果。
- `max_turns`：'emanate' 之操作：每分神工具之环最多几转。默承父灵之极（一千）。事简者可设少以约之。
- `timeout`：'emanate' 之操作：整批最长几秒。默承父灵之极（三千六百秒）。时至而仍未毕者，看门狗斩之。
