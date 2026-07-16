---
related_files:
  - ANATOMY.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/registry.py
  - src/lingtai/tools/glossary_validator.py
  - src/lingtai/tools/i18n/__init__.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# src/lingtai/tools/

Top-level home for every concrete built-in agent tool. One directory per tool
package, flat — there is no `intrinsics/` / `core/` / `capabilities/` interior
ownership layer. The kernel (`lingtai.kernel`) owns the tool *machinery*
(protocol, schema build, dispatch, guard, executor, meta/notifications,
lifecycle); this package owns the *concrete tools* and the registry that
composes them.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents**
> update this file in the same commit as code changes. **LingTai agents** report
> drift as issues.

## Components

| File / dir | Role |
|---|---|
| `registry.py` | The composition seam: `INTRINSICS` (5 mandatory intrinsic modules injected into `BaseAgent`), `BUILTIN_TOOLS` (name → `tools.<pkg>` path), `_GROUPS`, `CORE_DEFAULTS`, `setup_capability`, `apply_core_defaults`, `normalize_capabilities`, `expand_groups`, `get_all_providers`, `CAPABILITY_UNAVAILABLE` |
| `i18n/` | `en/zh/wen` string catalogs for every tool; registers into the kernel i18n cache via `register_strings` on import |
| `_catalog.py` | Shared scan/manifest helpers for `knowledge` + `skills` |
| `_file_paths.py` | `resolve_workdir_path` — shared by the five file tools |
| `_media_host.py`, `_zhipu_mode.py` | Provider-host / z.ai-mode helpers for `vision` + `web_search` |

**Tool sub-packages:** `email/`, `system/`, `psyche/`, `soul/`, `notification/`
(the five mandatory intrinsics); canonical `shell` (retained `bash/` implementation), `knowledge/`, `skills/`, `avatar/`,
`daemon/`, `mcp/`, `read/`, `write/`, `edit/`, `glob/`, `grep/` (always-on
floor); `vision/`, `web_search/` (opt-in). `avatar/` registers one tool,
`avatar`, dispatched by `action` (`spawn`\|`rules`\|`manual`).

## Connections

- **→ `lingtai.kernel`** — tools import kernel machinery freely (static): schema
  types, dispatch helpers, notifications, i18n, services. This is the allowed
  downward edge.
- **← `lingtai.Agent`** — passes `lingtai.tools.registry.INTRINSICS` into
  `BaseAgent(intrinsics=...)` and calls `setup_capability` for the dynamic
  tools.
- **→ `lingtai` (lazy only)** — a handful of tools reach `lingtai` services
  (`daemon` → MCP clients / presets / llm.service; `mcp` → `mcp_registry`;
  `vision`/`web_search` → provider services) but **only** via imports inside
  `setup()`/handlers, never at module top. `import tools` must not import
  `lingtai`.

## Import DAG

    lingtai  →  lingtai.tools → lingtai.kernel

`lingtai.kernel` imports neither `lingtai` nor `tools`
(`tests/test_kernel_isolation.py`). The single back-edge `lingtai.tools → lingtai` is
lazy-only, keeping import-time acyclicity.

## State

No mutable runtime state lives in this package root. Per-tool persistent state
(mailbox, jobs, daemons, knowledge, `.library`, `.notification`) is documented in
each tool's own `ANATOMY.md` and `CONTRACT.md`.

## Notes

- Membership in `registry.INTRINSICS` is the mandatory-include mechanism for the
  five intrinsics — the `BaseAgent._wire_intrinsics` loop is unconditional.
- `CORE_DEFAULTS` is the always-on floor; `vision`/`web_search` stay opt-in.
- Each `tools/<name>/` carries `__init__.py` (+ submodules), `ANATOMY.md`,
  `CONTRACT.md`, and an optional `manual/`.
