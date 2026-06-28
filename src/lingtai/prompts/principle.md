Progressive disclosure principle: each resident prompt layer has one job and points to the next.

- `meta_guidance` is immediate runtime guidance and routing hooks: tell the agent where the canonical rule lives right now; do not duplicate long procedures here.
- `procedures` is how to act: operational triggers, checklists, routing steps, reporting discipline, and concrete tool-use rules.
- `substrate` is the working model and principles: why the agent has these bodies, memory layers, lifecycle states, and communication channels.
- Reference manuals are why, boundaries, examples, and troubleshooting: load them on demand when the concise resident layer is not enough.

Keep each layer concise. A rule should have one source of truth; other layers should point to that source instead of restating it.

Token efficiency principle: the current session's active context is carried into every provider request. When continuing, summarize consumed tool results whose raw text is no longer needed. At completed task boundaries, after reporting and durable stores are tended, do not molt automatically; default to a proactive task-boundary molt only once current-session API calls exceed 100, or when context pressure, explicit human request, or conversation confusion makes the molt worth its cost. Use daemons to keep bulky or noisy work out of the main context.
