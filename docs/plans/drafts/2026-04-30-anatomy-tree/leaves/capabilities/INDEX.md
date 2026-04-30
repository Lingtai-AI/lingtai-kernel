# Capabilities — Anatomy Leaves Index

> Each leaf is a README + test pair. README documents the contract, source references,
> and known limitations. Test.md provides step-by-step verification instructions.

## psyche/ — Agent Identity & Mind

| Leaf | Description |
|------|-------------|
| [psyche/soul-flow](psyche/soul-flow/README.md) | Automatic subconscious: diary-triggered LLM reflection on idle, with `soul_flow.jsonl` persistence |
| [psyche/inquiry](psyche/inquiry/README.md) | On-demand synchronous self-question (deep-copy context clone) |
| [psyche/core-memories](psyche/core-memories/README.md) | Three persistent stores (lingtai, pad, context/molt) — what survives rebirth |

## library/ — Skill Catalog

| Leaf | Description |
|------|-------------|
| [library/paths-resolution](library/paths-resolution/README.md) | How `<available_skills>` XML is built from `.library/` path scanning |

## vision/ — Image Understanding

| Leaf | Description |
|------|-------------|
| [vision/multimodal](vision/multimodal/README.md) | Image → LLM routing: native multimodal, minimax-cli, or local VLM fallback |

## web_search/ — Web Search

| Leaf | Description |
|------|-------------|
| [web_search/fallback](web_search/fallback/README.md) | Provider fallback chain at setup time (DuckDuckGo as default) |

## Cross-Cutting

| Document | Description |
|----------|-------------|
| [DEPENDENCY-MAP.md](DEPENDENCY-MAP.md) | Capability interaction diagram — how psyche, library, vision, and web_search relate to each other and to kernel intrinsics |

## Other Categories (sibling directories)

| Directory | Leaves |
|-----------|--------|
| [avatar/](../core/) — spawn, shallow/deep, handshake, boot | 4 leaves |
| [daemon/](../core/) — dual-ledger, followup, rpm-gating, pre-send-health | 4 leaves + 1 convention |
| [file/](../core/) — read, write, edit, glob, grep | 5 leaves |
| [mail/](../core/) — atomic-write, dedup, identity-card, mailbox-core, peer-send, scheduling | 6 leaves + 1 index |
| [mcp/](../core/) — capability-discovery, inbox-listener, licc-roundtrip | 3 leaves + 1 index |
| [shell/bash/](../core/) — kill, sandbox, yolo | 3 leaves + 1 README |
| [shell/codex/](../core/) — oauth-originator | 1 leaf + 1 README |

See also: [core/](../core/), [init/](../init/), [llm/](../llm/) for non-capability leaves.
