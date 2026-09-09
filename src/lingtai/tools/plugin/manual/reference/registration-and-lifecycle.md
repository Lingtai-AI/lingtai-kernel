---
related_files:
- src/lingtai/tools/plugin/ANATOMY.md
- src/lingtai/tools/plugin/manual/SKILL.md
- src/lingtai/services/plugin_registry.py
- src/lingtai/tools/plugin/__init__.py
- src/lingtai/tools/plugin/CONTRACT.md
- src/lingtai/adapters/tool_plugin_host.py
- docs/examples/agent-plugins/hello-lingtai/plugin.json
maintenance: |
  Keep this reference aligned with plugin_registry and the plugin tool. Preserve
  configured declarations, the automatic root, inherited discovery, and the
  protected Plugin prompt field as distinct concepts.
---

# Plugin registration and lifecycle

Read `plugin-manual` first. Registration is a boot/refresh operation, never a
model-facing action.

## Identity and tiers

`manifest.plugins` in `init.json` is canonical;
`manifest.capabilities.plugin.paths` is a retained alias with the same meaning.
Canonical entries are considered first and de-duplicated. An entry can name one
directory containing `plugin.json` or a collection of plugin directories; it may
be absolute, `~`-prefixed, or relative to the agent workdir. The derived
`<workdir>/plugin` root is also registered but is not part of the configurable
settings row. When not already present as that exact path string, the current
service prepends it to the operational roots; inspect `info.declared` for actual
scan order rather than assuming configured roots always win collisions.

`manifest.capabilities.skills.paths` is inherited for **discovery only**. It can
make a plugin visible in the protected Plugin field, but it never grants
registration. The distinction is the authorization boundary:

| Tier | Protected Plugin field | Registry |
|---|---|---|
| `registered` | validated skills and registration facts | valid MCP records stamped `source="plugin:<name>"` |
| `discovered` | metadata and counts only | no records and no mounted components |

Plugin skills remain a closed namespace even when registered: no `.library/`
copy and no vanilla Skills entry. A registry record is **not running**; only an
explicit operator-written top-level `mcp` entry and refresh activate a server.

## Boot, refresh, and ownership

Boot/refresh validates declarations, records the snapshot, writes only current
plugin-owned registry records, and supplies validated per-skill paths to the
protected prompt writer. The actions cannot invoke this path: `info` re-scans
without registering, `settings` reads a detached snapshot, and `manual` reads
the installed manual.

An authorized configuration owner changes `manifest.plugins` with the existing
file/shell procedure, then calls `system(action="refresh")`:

1. Install: add the plugin root.
2. Verify: call unsummarized `plugin(action="info", input={}, reasoning="confirm plugin registration")` and inspect `registered`, `skipped`, `mcp_registered`, and `problems`.
3. Uninstall: remove the authorized declaration, refresh, and verify. A plugin
   still reachable through the alias, another collection, or automatic root
   remains registered; an inherited Skills path may still show it as discovered.
   Removing one list entry does not guarantee disappearance. If an automatic-root
   move is required, ask its owner for that separate filesystem change; do not
   delete plugin files or alter other declarations as an implicit uninstall step.

Do not hand-edit `mcp_registry.jsonl` or delete the plugin directory. The
registry service prunes only `source="plugin:*"` records it owns; foreign,
blank, and unparseable lines survive. A removed server is pruned, a changed
specification replaces its old record, repeated refresh is idempotent, and
between plugin collisions the first declared name wins. An existing hand-written
or addon record is untouched. Duplicate plugin names are first-wins and appear
in `problems`; a skipped component is absent from the field/registry, not merely
labeled.
