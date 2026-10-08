---
related_files:
- src/lingtai/tools/plugin/ANATOMY.md
- src/lingtai/tools/plugin/manual/SKILL.md
- src/lingtai/services/plugin_registry.py
- src/lingtai/tools/plugin/CONTRACT.md
- docs/examples/agent-plugins/hello-lingtai/plugin.json
- docs/examples/agent-plugins/hello-lingtai/mcp.json
maintenance: |
  Keep this reference aligned with Agent Plugins v1.0.0 validation and §4.1
  containment. It owns format and path detail routed from plugin-manual.
---

# Plugin format and containment

Read `plugin-manual` first. This reference covers the directory shape,
component validation, and the §4.1 path gate. Declarations never launch a
process; activation is an explicit operator configuration step.

## Directory and manifest

```text
my-plugin/
├── plugin.json                 # required
├── skills/<name>/SKILL.md      # optional; nested groups are allowed
├── mcp.json                    # optional MCP declarations
└── com.example.client/         # optional extension namespace
```

`plugin.json` requires the exact local identifier and a valid name:

```json
{"$schema":"https://agent-plugins.org/schemas/1.0.0/plugin.schema.json","name":"my-plugin"}
```

The kernel compares `$schema` locally and never fetches it. A missing,
unreadable, malformed, or unsupported manifest rejects the whole plugin and
reports the reason in `info.problems`. A name is 1–64 characters, lowercase
letters/digits/`.`/`-`, starts and ends alphanumeric, and contains neither `--`
nor `..`. Optional `version`, `description`, `homepage`, `repository`, and
`license` are strings; `keywords` is a string list; `author` and `extensions`
are objects. `description` is the catalog summary (truncated to 200 chars).

## Skills and MCP

Every validated `skills/**/SKILL.md` is named as listed (for example,
`group/nested`); dot-directories, corrupt directories, and escaping entries are
skipped. The validated names and paths are one set: a rejected skill is neither
rendered in the protected Plugin field nor copied to `.library/` or the vanilla
Skills catalog.

A minimal `mcp.json` uses its own exact v1.0.0 identifier (also never fetched):

```json
{"$schema":"https://agent-plugins.org/schemas/1.0.0/mcp.schema.json","mcpServers":{"my-server":{"type":"stdio","command":"python3","args":["${PLUGIN_ROOT}/server.py"]}}}
```

Supply the referenced server implementation before activation. Supported
transports are `stdio` (`command`, optional `args`, `env`, `cwd`) and
`streamable-http`/`sse` (`url`, optional string-to-string `headers`). URL
transports become registry transport `http`; `stdio` stays `stdio`. Server names
must match `^[a-z][a-z0-9_-]{0,30}$`; invalid names are skipped, not renamed.
`env` and `headers` must map strings to strings; `env`/`cwd`/`headers` survive into a registered record. An existing
hand-written/addon registry name is never overwritten; among plugin declarations
first wins. Exposed MCP tool names must remain unique in a daemon run; a
collision requires deduplication or renaming. A record carries configuration but starts no process; use `mcp-manual` for
explicit top-level `mcp` activation.

## §4.1 path gate

For each plugin-relative `command`, `cwd`, and `args` entry:

1. Require `./` or `${PLUGIN_ROOT}/`.
2. Resolve the value and symlinks.
3. Reject it unless the resolved path stays inside the resolved plugin root.

Any relative value containing a `..` segment goes through the same gate even
without the prefix (`../x` cannot bypass it). A normalized path such as
`bin/../bin/serve` is accepted when it remains inside. Absolute paths,
unexpanded `${ENV_VAR}`, and bare tokens without `..` (such as `node` or `-c`)
pass through because they are not identified as plugin-relative paths. This is
a plugin-directory boundary, not a process sandbox.

## Failure boundary and check

An invalid manifest is a **whole-plugin** failure: no catalog entry and no
registration. An invalid/escaping skill or server is a **per-component**
failure: the plugin remains visible, while the component appears in
`registered[].skipped`/`problems` and is not used. After edits or surprises,
call `plugin(action="info", input={}, reasoning="inspect plugin validation", summarize=false)`.
