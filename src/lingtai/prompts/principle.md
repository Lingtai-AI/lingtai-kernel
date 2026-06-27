Progressive disclosure principle: each resident prompt layer has one job and points to the next.

- `meta_guidance` is immediate runtime guidance and routing hooks: tell the agent where the canonical rule lives right now; do not duplicate long procedures here.
- `procedures` is how to act: operational triggers, checklists, routing steps, reporting discipline, and concrete tool-use rules.
- `substrate` is the working model and principles: why the agent has these bodies, memory layers, lifecycle states, and communication channels.
- Reference manuals are why, boundaries, examples, and troubleshooting: load them on demand when the concise resident layer is not enough.

Keep each layer concise. A rule should have one source of truth; other layers should point to that source instead of restating it.
