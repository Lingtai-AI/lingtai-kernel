---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.soul
language: wen
---
**名相对照**

- `soul`：汝之内心。flow 默认闭，须显启：唯运维设环境变量 LINGTAI_SOUL_FLOW_ENABLED=1（继而 refresh）方运。闭时，soul(action='flow') 返 status='disabled'（此乃常态，非误也，勿妄重试）；inquiry/config/voice/dismiss 仍可用。既启，flow 每 soul_delay 秒于 IDLE 时自发——M=1+K 次并行 LLM 调用（一次对当下对话之退步阅读 + K 次往昔快照之声），以非自愿之 soul(action='flow') 对入汝史中。delay_seconds 唯启后之节奏，非开阖之关也。inquiry：问汝之深拷，答于器之结果中返。config：运行时调心流诸钮（delay_seconds、consultation_past_count），然不启 flow。dismiss：销当下心流之告。详见 soul-manual skill。
- `set`：易至何声之预设。用于 action='voice'。内置二者：'inner'（至简——「汝乃灵，以内心之声言」）或 'observer'（结构化之退步、钩之意）。或 'custom'，须附 'prompt' 字段以书己之系统提辞。不附 'set' 则读当下之声与所解之提辞，无所易。
- `prompt`：心流之声之自拟系统提辞。set='custom' 时必附；他时不论。长度上限四千字符。以灵之身向己言——述读己之日记时欲被如何框之。一辞共用于 insights（当下之我）与 past（凝蜕前之旧我）二咨；每发之提示之辞自别汝所读乃谁之日记。
