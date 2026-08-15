---
name: plugin-manual
description: >
  Router for the `plugin` capability — the per-agent Agent Plugins
  (agent-plugins.org, v1.0.0) catalog and registration snapshot. Covers what a
  plugin is, the difference between a registered and a merely discovered one,
  how installing and uninstalling actually work, and how to read a skipped
  component.

  Reach for this manual when:
    - The human asks what plugins this agent has, or asks to install, add, or
      remove an Agent Plugin.
    - You want the current snapshot
      (`plugin(action="info", input={}, reasoning="list plugins")`).
    - You are authoring a `plugin.json` or `mcp.json` and need the required
      fields, the `name` grammar, or the `./`-prefixed path containment rule.
    - A plugin you expect is missing from `<registered_plugin>`, a skill you
      expect is missing from your skills catalog, or a server you expect is
      missing from `mcp_registry.jsonl`.
    - A plugin is listed as `<mount>discovered</mount>` and you need to know why
      nothing of it is usable.

  Does NOT cover: the general MCP registration contract for hand-written servers
  (that is the `mcp-manual` skill), authoring Agent Skills (that is the `skills`
  capability's manual), or the Agent Plugins specification prose itself (fetch
  https://agent-plugins.org/specification.md with web when you need normative
  wording this router does not carry).
version: 2.1.0
last_changed_at: 2026-08-08T00:00:00Z
related_files:
- src/lingtai/tools/plugin/__init__.py
- src/lingtai/tools/plugin/ANATOMY.md
- src/lingtai/tools/plugin/CONTRACT.md
- src/lingtai/services/plugin_registry.py
- docs/examples/agent-plugins/hello-lingtai/plugin.json
maintenance: |
  Tracks the capability and service it summarizes; update when the plugin tool
  surface, the manifest validation rules, the registration/uninstall flow, or
  the Agent Plugins version this kernel understands change. Keep the two-tier
  mount contract (declared → registered, inherited → discovered) stated in this
  body, the tool description, and the prompt preamble in lockstep — it is the
  same contract in three places, and the boundary it draws is a security
  boundary, not a presentation choice.
---

# Plugin Capability — How To Use It

The `plugin` capability is your view of **Agent Plugins** (the open standard at
https://agent-plugins.org, version 1.0.0). A plugin is a directory bundling
Agent Skills and MCP server configuration; this capability is how one gets
mounted onto this agent, and how you see what did and did not mount.

## The one thing to internalize: declaration is what mounts

Every plugin in `<registered_plugin>` carries a `<mount>` stamp, and it is the
difference between "you can use this" and "this merely exists".

| `<mount>` | How it got there | Its skills | Its MCP servers |
|---|---|---|---|
| `registered` | Declared in `init.json` `manifest.plugins` | **In your skills catalog**, located inside the plugin | **In `mcp_registry.jsonl`** with `source="plugin:<name>"` — registered, *not running* |
| `discovered` | Only found on an inherited `manifest.capabilities.skills.paths` directory | Not in your catalog | Not in the registry |

The asymmetry is deliberate and it is a security boundary. Dropping a directory
somewhere the skills capability happens to scan must never silently register a
third party's MCP server. Only an explicit declaration does that.

Two things registration still does **not** do, and both matter:

| What you might assume | What is actually true |
|---|---|
| A registered server is running | **No.** Registration is registry-level, exactly like `addons:[]`. To actually run one, add a matching entry under the top-level `mcp` key in `init.json` and `system(action="refresh")`. |
| A registered skill was copied into `.library/` | **No.** Nothing is copied. Each validated skill directory inside the plugin is composed into the catalog scan, so the entry's `location` still points inside the plugin. That is why uninstall needs no file deletion. |
| A registered plugin is trustworthy | **Not implied.** Registration reports what parses and mounts, not what is safe. A plugin is third-party code, and declaring one is the human's decision to make, not yours to make for them. |

## Installing a plugin

There is no install action. Installing is a declaration edit plus a refresh:

1. Read the plugin first. `plugin.json`, every `SKILL.md`, and especially
   `mcp.json` — an `mcp.json` server is a command this agent will be configured
   to run. Third-party code: read before you trust.
2. Add its directory to `manifest.plugins` in `init.json`:

   ```json
   {
     "manifest": {
       "plugins": ["./plugins/hello-lingtai"]
     }
   }
   ```

   Entries may be absolute, tilde-prefixed, or relative to the agent working
   dir. An entry may be a single plugin directory (one carrying `plugin.json`)
   or a *collection* directory whose immediate children are plugin roots.
3. `system(action="refresh")`. Registration runs before capability setup, so the
   catalog and the registry are in their mounted state by the first turn.
4. `plugin(action="info", input={}, reasoning="confirm the mount")` and read
   `registered` — including `skipped`, which is where anything that did not
   mount says why.

### Uninstalling

The exact inverse, and equally mechanical:

1. Remove the entry from `manifest.plugins`.
2. `system(action="refresh")`.

Every `mcp_registry.jsonl` record stamped `source="plugin:<name>"` whose plugin
is no longer declared is pruned on that refresh, and the plugin's skills leave
the catalog because nothing scans its skill directories any more. Records the plugin
system does not own — hand-written ones, addon-decompressed ones — are never
touched. **Never delete the plugin directory as an uninstall step**; the
declaration is the installation, and the directory belongs to whoever put it
there.

The same pruning keeps a *still-declared* plugin honest: a server deleted from
its `mcp.json` loses its record, and a server whose spec changed has the stale
record replaced rather than duplicated.

### Canonical config key

`manifest.plugins` is the **canonical** declaration key.
`manifest.capabilities.plugin.paths` is a retained **alias** that means exactly
the same thing, for configs written before the canonical key existed. Prefer
`manifest.plugins` in anything you write. Declaring a directory under both is
harmless — it is de-duplicated and scanned once.

`manifest.capabilities.skills.paths` is neither: it is inherited for *discovery*
only, so a plugin dropped where skills live is at least visible.

## What a plugin is

A plugin is a directory:

```
my-plugin/
├── plugin.json          # required
├── skills/              # optional — one Agent Skill per subdir with SKILL.md
│   └── some-skill/
│       └── SKILL.md
├── mcp.json             # optional — MCP server configuration
├── com.example.client/  # optional — reverse-domain client extension namespace
└── LICENSE
```

A minimal working example ships with the kernel at
`docs/examples/agent-plugins/hello-lingtai/` — one skill, one stdio MCP server,
no dependencies. Read it when authoring your own.

### `plugin.json` — the required manifest

Two required fields, and this kernel rejects the plugin outright if either is
missing or malformed:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "my-plugin"
}
```

- **`$schema`** must be exactly the v1.0.0 URL above. It is compared as an
  opaque version identifier — the kernel never fetches it. Any other value is
  an unsupported version, not a warning.
- **`name`** is 1–64 characters, lowercase alphanumerics plus `.` and `-`, first
  and last characters alphanumeric, no `--` and no `..`. Valid: `my-plugin`,
  `acme.tools`, `lint3r`. Invalid: `My-Plugin`, `-leading`, `a--b`.

Optional and type-checked when present: `version`, `description`, `homepage`,
`repository`, `license` (strings), `keywords` (list of strings), `author` and
`extensions` (objects). `description` is what becomes `<summary>` in your
prompt, truncated at 200 characters.

### `skills/` — composed into your catalog

Each subdirectory of `skills/` containing a `SKILL.md` is one skill, per the
Agent Skills specification (https://agentskills.io/specification). For a
**registered** plugin these become ordinary entries in your skills catalog, with
their `location` pointing inside the plugin. For a **discovered** one they are
counted and nothing more; to use one, read it from `<source>` with `file`.

Two details worth knowing when you read a plugin's `skill_count`:

- **Grouping directories are walked through.** A directory under `skills/` with
  no `SKILL.md` of its own but only subdirectories is a grouping directory, so
  `skills/group/nested/SKILL.md` is the skill `group/nested`. Reported and
  mounted are the same set: the count is what the catalog actually holds, not
  just the top level. A directory with loose files and no `SKILL.md` is
  corrupted, and is named in `skipped`.
- **What is mounted is the validated skill list, not the `skills/` directory.**
  Each skill directory is contained-checked individually and composed into the
  catalog scan on its own. That is why a skipped skill is genuinely absent from
  your catalog rather than merely absent from the report.

### `mcp.json` — translated into registry records

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "my-server": {
      "type": "stdio",
      "command": "python3",
      "args": ["${PLUGIN_ROOT}/server.py"]
    }
  }
}
```

Transports: `stdio` (with `command`, optional `args`/`env`/`cwd`),
`streamable-http` and `sse` (with `url`, optional `headers`). `env` and `headers`
must map strings to strings or the server is skipped. The registry models two
transports, so `stdio` stays `stdio` and both URL-addressed transports become
`http`.

For a registered plugin each server becomes one `mcp_registry.jsonl` record,
carrying every field you declared — `env` and `cwd` for `stdio`, `headers` for
the URL-addressed transports — with `cwd` resolved to absolute:

```json
{"name": "my-server", "summary": "...", "transport": "stdio",
 "source": "plugin:my-plugin", "command": "python3",
 "args": ["/abs/path/to/plugin/server.py"],
 "cwd": "/abs/path/to/plugin", "env": {"TOKEN": "..."}}
```

Three things to know when authoring one:

- **The server name is the registry name.** It must satisfy the registry's own
  grammar (`^[a-z][a-z0-9_-]{0,30}$`) or the server is skipped with that reason
  in `skipped` — it is never silently renamed, because the name is what you will
  type.
- **Name collisions skip, never overwrite.** If a hand-written or
  addon-decompressed record already owns the name, the plugin's server is
  skipped and the existing record survives untouched. Same between two plugins:
  first declared wins.
- **MCP tool names must stay unique within a daemon run.** A plugin's `mcp.json`
  server is also injected into daemon tasks that select the plugin; if its
  exposed tool name collides with a task-level `mcp` registration (or another
  plugin/server), the daemon fails at dispatch with `duplicate MCP tool name` —
  rename or dedupe one registration. This is expected: both injection paths are
  live simultaneously.
- **`${PLUGIN_ROOT}` and `./` paths are resolved to absolute** in the record. The
  record is a registration, not a launch spec: activating the server needs an
  `init.json` top-level `mcp` entry, and the kernel spawns from *that* entry's
  own config, matching the registry only by `name`. The resolved paths here are
  informational — accurate, and not what gets executed.

## Path containment (§4.1) — why a component can be skipped

Every plugin-relative path a plugin declares **must start with `./`** (or the
equivalent `${PLUGIN_ROOT}/` form) and must still resolve inside the plugin
root. This covers `command`, `cwd`, and **every entry of `args`** — a path
smuggled through an argument is checked exactly like one in `command`. The
kernel enforces both halves, and containment is checked *after* symlinks are
followed, so `./link-to-elsewhere` is rejected exactly like `./../escape`.

Spelling is not a way around it. Any relative value carrying a `..` segment is
put through the identical gate whether or not it starts with `./`, so
`../../bin/sh` is rejected exactly like `./../../bin/sh`. A relative `..` that
stays inside (`bin/../bin/serve`) resolves and is rewritten absolute.

What genuinely is not a plugin-relative path passes through untouched, because
§4.1 has nothing to say about it, and the kernel cannot tell an executable name
from a relative file name:

- an absolute path (`/usr/bin/env`),
- an `${ENV_VAR}` the client is not asked to expand,
- a bare token with no `..` segment — `node`, `-c`, a literal argument.

So the gate constrains **where a declared path may point**, not what a server may
be. That is a boundary about the plugin directory, not a sandbox: registration is
registry-level only, nothing is spawned, and activation still requires an
operator-written `init.json` `mcp` entry.

Two failure boundaries, straight from the spec:

- **Whole-plugin:** an unreadable or invalid `plugin.json` rejects the plugin.
  It does not appear at all, registers nothing, and the reason is in `problems`.
- **Per-component:** a single skill directory or MCP server whose path escapes
  is skipped, and the rest of the plugin still mounts. The skipped component is
  named in `problems` and in that plugin's `skipped` list. Skipped means *not
  mounted*, on both halves: an escaping server never reaches
  `mcp_registry.jsonl`, and an escaping skill directory is never composed into
  the catalog — only the validated skill directories are, never their parent.

If a plugin you expect is missing, or a component of it did not mount, call
`plugin(action="info", ...)` and read `problems` and `registered[].skipped` —
every rejection carries the label, the offending path, and the reason.

## Tool surface

Two actions, called through the standard envelope
`plugin(action=..., input={}, reasoning="...")`. `action`, `input`, and
`reasoning` are all required; neither action takes any arguments, so `input` is
always the empty object `{}` — passing any field inside it is rejected before
the tool does anything. The optional root `summarize` boolean is presentation
only.

- `plugin(action="info", input={}, reasoning="...")` returns
  `{status, declared, registered_count, registered, discovered_count,
  discovered, mcp_appended, mcp_pruned, paths, problems}`.
  - `registered` is the boot registration snapshot: per plugin, `skills`,
    `skills_mounted`, `mcp_servers`, `mcp_registered`, and `skipped`
    (`{component, reason}` for everything that did not mount).
  - `discovered` is the discovery-only tier.
  - `paths` is a per-configured-path report (`resolved`, `exists`, `plugins`).
- `plugin(action="manual", input={}, reasoning="...")` returns this manual body
  on demand, without re-scanning.

**Both actions are read-only.** `info` re-scans and reports; it does not
register. Mounting happens at boot, so a plugin you just declared needs
`system(action="refresh")` before it appears as `registered`.

Every worked call in this manual is written in this full form; there is no
shorthand to expand.

## See also

- **The example plugin:** `docs/examples/agent-plugins/hello-lingtai/` — the
  smallest thing that registers: one skill, one stdio MCP server, stdlib only.
- **The standard:** https://agent-plugins.org/specification.md — normative
  wording, `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` placeholder expansion, client
  extension namespaces. Fetch it with `web` when you need spec text this router
  does not carry.
- **`mcp-manual` skill** — the activation contract for actually running a
  registered MCP server, including one a plugin registered.
- **`src/lingtai/tools/plugin/CONTRACT.md`** — the exact tool surface, result
  shapes, and error envelopes.

## Cleanup / Footprint

The capability itself owns no state and writes no file. Registration writes in
exactly one place: `mcp_registry.jsonl`, and only records stamped
`source="plugin:<name>"`. Those are cleaned up by undeclaring the plugin and
refreshing — never by hand-editing the registry, and never by deleting the
plugin directory, which belongs to whoever put it there.
