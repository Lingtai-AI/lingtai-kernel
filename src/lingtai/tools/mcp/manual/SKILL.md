---
name: mcp-manual
description: >
  Router for the `mcp` capability — register, activate, update, deregister,
  and troubleshoot MCP (Model Context Protocol) servers. Covers both generic
  third-party MCPs and the six kernel-curated LingTai addons (`imap`,
  `telegram`, `feishu`, `wechat`, `whatsapp`, `cloud_mail`).

  Reach for this manual when:
    - The human asks to install, set up, configure, or remove an MCP server.
      Kernel-curated → the `addons:` + `init.json mcp.<name>` workflow;
      third-party → the registry route or the legacy `mcp/servers.json` route.
    - The human asks to set up an `imap` / `telegram` / `feishu` / `wechat` /
      `whatsapp` / `cloud_mail` addon, or any LingTai email/chat integration.
      **Step 1 is always** `reference/curated-addons.md`; exact config field
      names come from the addon docs — do NOT guess them.
    - You want to know what MCPs you currently have
      (`mcp(action="info", input={}, reasoning="list registered MCPs")`).
    - An MCP isn't behaving: registry validation, the `problems` list,
      refresh-after-edit verification, common boot errors.
    - You're exploring an unfamiliar third-party MCP and need its docs.

  Covers (progressively, via reference/): the three states (catalog →
  registry → active); curated vs third-party install paths; the legacy
  `mcp/servers.json` direct mount route (still functional, ungated); stdio
  and HTTP server config; where `mcp_registry.jsonl` lives and how to mutate
  it (`write`/`edit`/`bash` — the `mcp` capability itself is read-only); the
  `<homepage>` fallback doc field; and how `init.json`'s `addons:` list and
  `mcp:` activation entries relate to the registry. Replaces the deprecated
  `lingtai-mcp` skill.

  Does NOT cover the protocol spec. `lingtai-kernel-anatomy
  reference/mcp-protocol.md` owns the supported SDK/protocol range, the
  SDK-versus-LingTai ownership split, and the stdio env-injection boundary
  (`LINGTAI_AGENT_DIR` / `LINGTAI_MCP_NAME`); it routes onward to
  `src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md` for the LICC v1 inbox
  callback contract and to `src/lingtai/services/mcp_registry.py` for
  validator internals. Read this for *what to do*, anatomy for *how it
  works*.
version: 3.4.1
last_changed_at: 2026-08-11T00:00:00Z
related_files:
- src/lingtai/tools/mcp/__init__.py
- src/lingtai/tools/mcp/ANATOMY.md
- src/lingtai/tools/mcp/CONTRACT.md
maintenance: |
  Tracks the routed source/resources it summarizes; update when the underlying capability or its sub-references change.
---

# MCP Capability — How To Use It

The `mcp` capability is your interface to Model Context Protocol (MCP) servers — both generic third-party servers and the six kernel-curated LingTai addons (`imap`, `telegram`, `feishu`, `wechat`, `whatsapp`, `cloud_mail`). Like the `skills` capability, it is **pure presentation**: registered MCPs are listed in your system prompt under `<registered_mcp>`, and the registry itself is a JSONL file you edit directly with `write` / `edit` / `bash`.

This is the router. Detail lives in `reference/`. Load only what you need.

## TUI command boundary

`/addon` is retired; never recommend it. `/mcp` is the only current TUI command for this surface, and it is read-only config/status inspection. It is **not** a guided setup or configuration screen; never describe it as one or redirect a human there for addon setup.

For curated addon setup, load `reference/curated-addons.md` (the curated-addon setup contract) and the relevant provider docs before editing. Make exact configuration changes only within explicit human authorization, using that contract's four-step mechanism; do not redirect the human to a nonexistent or setup-like TUI screen.

## Three states of an MCP

For any MCP server, relative to this agent:

1. **In the kernel catalog** — LingTai blesses it. Reference template ships with the kernel. The six curated addons live here: `imap`, `telegram`, `feishu`, `wechat`, `whatsapp`, `cloud_mail`.
2. **Officially registered** — appears as a line in `mcp_registry.jsonl` (sibling to `init.json`). The system prompt's `<registered_mcp>` lists it.
3. **Active** — the MCP server subprocess is running, its tools are mounted in your tool surface.

Promotion path: catalog → registry → active. You move things along by editing files and calling `system(action="refresh")`.

## Pick a sub-skill

| Task | Read |
|---|---|
| Set up an `imap` / `telegram` / `feishu` / `wechat` / `whatsapp` / `cloud_mail` addon | `reference/curated-addons.md` |
| Add a third-party MCP (`npx`/`uvx`/HTTP) | `reference/third-party-and-legacy.md` |
| Wire up a server quickly via `mcp/servers.json` (legacy/ungated) | `reference/third-party-and-legacy.md` |
| MCP not behaving / cryptic boot errors / `KeyError: 'foo'` | `reference/troubleshooting.md` |
| Update or deregister an MCP | `reference/troubleshooting.md` |
| Spec-level questions (schema, env injection, LICC) | `lingtai-kernel-anatomy reference/mcp-protocol.md` |
| Which MCP protocol/SDK version LingTai supports, and what it owns vs. delegates | `lingtai-kernel-anatomy reference/mcp-protocol.md` |

**Before curated addon setup**, start with `reference/curated-addons.md`; it owns the setup contract and the registry-name → module-name table. Those first-party servers now ship inside the `lingtai` wheel under `lingtai.mcp_servers.*`; historical `lingtai_*` packages remain as thin compatibility wrappers.

**Before third-party setup or troubleshooting**, read the server's own docs — see below.

## Reading an MCP's README

Every MCP server's README is the canonical install + config + troubleshooting doc — config field names, env vars, error meanings, the lot. **Always read the relevant docs before guessing at config.** For kernel-curated addons, begin with `reference/curated-addons.md` and use the catalog homepage when provider-specific detail exceeds the bundled note. For third-party servers, read the README.

### 1. Local README (preferred for third-party Python MCPs)

If the MCP is installed as its own Python package, run the bundled script with the **runtime venv's Python** — the same interpreter where the server package is actually installed:

```bash
~/.lingtai-tui/runtime/venv/bin/python3 \
  .library/intrinsic/capabilities/mcp/scripts/find_readme.py <pkg-name>
```

`<pkg-name>` is the installed distribution name. (`python3` from your `$PATH` may resolve to a system or conda interpreter that doesn't see the venv's installed packages — always use the venv's Python explicitly.)

The script tries the editable repo on disk first, then falls back to the README embedded in the wheel's `METADATA` file (PEP 566). Works for editable installs and normal PyPI wheels alike. Pass `--module <modname>` if you only know the importable module name instead of the distribution name.

### 2. Homepage URL (fallback)

If the script prints `ERROR: no README found locally` (or the MCP isn't a Python package — e.g. an `npx`-launched server), fetch the registry's `<homepage>` field with `web_read`. Each registered MCP exposes this when known.

### 3. Runtime self-description (last resort)

If neither path yields docs, fall back to the MCP's own runtime self-description: once activated, its tool descriptions appear in your tool surface, and many servers also publish a server-level `instructions` string at connection time.

## Tool surface

Two actions, called through the standard envelope
`mcp(action=..., input={}, reasoning="...")`. `action`, `input`, and `reasoning`
are all required; neither action takes any arguments, so `input` is always the
empty object `{}` — passing any field inside it is rejected before the tool does
anything. The optional root `summarize` boolean is presentation only.

- `mcp(action="info", input={}, reasoning="...")` returns current registry contents and a runtime health snapshot (registry path, count, problems) without the manual body.
- `mcp(action="manual", input={}, reasoning="...")` returns this manual body on demand, without re-reading the registry.

Every worked call in this manual and its `reference/` docs is written in this
full form; there is no shorthand to expand.

Each `registered` entry may also carry a non-secret **`identity`** block, so you can tell *which* configured account/bot/channel an MCP surface represents without reading private config:

```json
{
  "name": "telegram",
  "summary": "...",
  "identity": {
    "mcp": "telegram",
    "account_count": 1,
    "last_verified_at": "2026-06-24T09:59:00+00:00",
    "accounts": [
      {"alias": "main", "bot_username": "my_agent_bot", "bot_id": 123456789,
       "bot_display_name": "My Agent", "is_bot": true}
    ]
  }
}
```

Identity comes from the addon-written, non-secret document at `system/mcp_identities/<name>.json` (schema `lingtai.mcp.identity.v1`), surfaced both here and as an `<identity>` block under the server in your `<registered_mcp>` prompt section. It is a **strict allowlist projection** — only non-secret identity fields (alias, provider username/id/display name, non-secret routing counts) are ever shown; tokens, passwords, app secrets, refresh/access tokens, headers, and any unrecognized field are dropped. The block appears only for servers that have published an identity file (currently the curated messaging addons: `telegram`, `feishu`, `wechat`, `whatsapp`); it is absent otherwise and reflects each account's last-cached state (no live network call). For richer per-account detail, the addon's own `accounts` action remains authoritative.

All registry mutations happen via `write` / `edit` / `bash`. The `mcp` capability never writes to the registry.

## Runtime venv swap (latest-main owner rollout)

When the runtime venv moves (e.g. a latest-main owner rollout), curated registry records that still carry the old interpreter's `command` must be reconciled **explicitly and manually** after the authorized venv swap. There is no kernel helper for this: `decompress_addons` is append-only/non-destructive to existing records, and the `mcp` capability never writes the registry. The rollout procedure performs the reconcile itself as a manual step using ordinary `write`/`edit`/`bash`, e.g. run this self-contained command with the **new** runtime interpreter:

```bash
NEW_PYTHON=/absolute/path/to/new/venv/bin/python
AGENT_DIR=/absolute/path/to/agent/dir

"$NEW_PYTHON" - "$AGENT_DIR" <<'PY'
import json, os, sys, tempfile
from pathlib import Path

agent_dir = Path(sys.argv[1])
if not agent_dir.is_absolute():
    raise SystemExit("AGENT_DIR must be an absolute path")
registry = agent_dir / "mcp_registry.jsonl"
print(f"target registry: {registry}")
print(f"interpreter: {sys.executable}")
if not registry.is_file():
    raise SystemExit(f"no registry at {registry}")  # fail loud, never "empty"

# Fail-closed preflight: only rewrite a registry with zero problems.
from lingtai.services.mcp_registry import read_registry
_valid, problems = read_registry(agent_dir)
if problems:
    for p in problems:
        print(f"problem line {p['line']}: {p['error']}")
    raise SystemExit("registry has problems; aborting without writing")

lines = registry.read_text(encoding="utf-8").splitlines(keepends=True)
seen: set[str] = set()
changed: list[str] = []
for i, raw in enumerate(lines):
    if not raw.strip():
        continue
    try:
        rec = json.loads(raw)
    except json.JSONDecodeError:
        continue  # defensive; preflight already rejected invalid lines
    name = rec.get("name")
    if not name or name in seen:
        continue  # only the canonical first-valid row per name is touched
    seen.add(name)
    if (
        rec.get("source") == "lingtai-curated"
        and rec.get("command") != sys.executable
    ):
        rec["command"] = sys.executable
        ending = "\n" if raw.endswith("\n") else ""
        lines[i] = json.dumps(rec, ensure_ascii=False) + ending
        changed.append(name)
if changed:
    fd, tmp = tempfile.mkstemp(dir=str(registry.parent), prefix=".mcp_registry.jsonl.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:  # atomic replace
            f.write("".join(lines)); f.flush(); os.fsync(f.fileno())
        if registry.exists():
            os.chmod(tmp, registry.stat().st_mode & 0o777)
        os.replace(tmp, registry)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    print(f"reconciled {len(changed)} curated command(s): {', '.join(changed)} -> {sys.executable}")
else:
    print("no curated command needed reconciling")
PY
```

**Contract (explicit manual step, not automatic):** after an **authorized** venv swap this command switches **every canonical curated record** to `NEW_PYTHON` (a `lingtai-curated` record whose `command` differs from the current interpreter gets its `command` rewritten **in place** — atomic, order-preserving, no duplicate record appended — so the canonical `read_registry()` surface (`mcp info`, `<registered_mcp>`, manual copies) observes the new command with `problems == []`). It does **not** preserve a command-only intentional override of a curated record: if you deliberately pointed a curated record at an independent interpreter, exclude that record or do not run this recipe. The command is fail-closed: if the registry has any invalid/duplicate/problem line, it aborts **before** writing and the file bytes are unchanged. This reconcile is **not** the MCP spawn source: live children spawn from `init.json`'s `mcp.<name>.command` (main agent) or the caller's `sys.executable` (daemon), never from the registry record, so a registry rewrite never auto-swaps a live child's runtime.

`system(action="refresh")` only retries **failed** MCPs and does **not** restart healthy children — a live curated child only moves to the new venv on a **full agent relaunch**. After any venv swap, verify live children with a **fail-closed provenance gate**: one-shot probe with the child's activation command/env printing `sys.executable`, `lingtai.__file__`, and `lingtai.mcp_servers.<name>.__file__`. If they still resolve to the old venv, the gate FAILS and reports 'requires relaunch' — do not claim PASS.

## See also

- **Canonical spec**: `lingtai-kernel-anatomy reference/mcp-protocol.md` — supported SDK range (`mcp>=2,<3`), protocol `2026-07-28` with legacy fallback, the split between what the official SDK owns and what LingTai owns, the tool-metadata sidecar, and the stdio env-injection/registry boundary. It routes onward for the details it does not own.
- **LICC v1 inbox callback contract**: `src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md` — the normative event envelope and two-lane projection.
- **File formats**: `lingtai-kernel-anatomy reference/file-formats.md` §2.7 (init.json `addons` + `mcp` fields), §6 (`mcp/servers.json` legacy direct mounts), §6.5 (`mcp_registry.jsonl`), §6.6 (`.mcp_inbox/<name>/<id>.json` LICC events).

## Cleanup / Footprint

MCP itself owns registry/configuration state (`mcp_registry.jsonl`, optional
`mcp/servers.json`, and `.mcp_inbox/<name>/...` LICC event files). Curated addon
packages such as Telegram/Feishu/WeChat/IMAP also maintain their own data
stores; their README/manual is responsible for declaring addon-specific cleanup
such as downloaded voice/audio attachments. Do not delete credentials or active
registry entries as a cleanup shortcut.

Footprint check (read-only, records the audit):

```bash
python3 - <<'PY'
import json, time
from pathlib import Path
agent = Path.cwd()
roots = [p for p in [agent / "mcp_registry.jsonl", agent / "mcp", agent / ".mcp_inbox"] if p.exists()]
def size(p): return p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
rows = [(p, size(p)) for p in roots]
total = sum(s for _, s in rows)
print(f"mcp roots: {len(rows)}; bytes: {total}")
for p, s in rows: print(f"{s:>12}  {p}")
log = agent / "logs" / "cleanup.jsonl"; log.parent.mkdir(parents=True, exist_ok=True)
log.open("a", encoding="utf-8").write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": "mcp", "dry_run": True, "candidates": len(rows), "bytes": total, "human_approved": False, "summary": "mcp footprint audit"}) + "\n")
PY
```

Recommended cadence: after adding/removing MCP servers, when `.mcp_inbox` grows,
and before sharing a project. Cleanup requires explicit user consent after the
dry-run report, and the audit/apply step must be recorded in `logs/cleanup.jsonl`. Prefer deregistering/updating registry files followed by
`system(action="refresh")` over deleting registry state by hand.
