---
related_files:
- src/lingtai/intrinsic_skills/nokv-workbench/SKILL.md
maintenance: |
  Developer-facing TUI deployment preflight referenced by nokv-workbench/SKILL.md; update it whenever the 18-tool restore-capable workbench MCP surface, checkpoint/restore contract, capability gate, or runtime-version compatibility check changes.
---

# TUI runtime preflight (developer-facing)

This is deployment guidance for developers installing a workbench-enabled
LingTai branch into the TUI runtime. It is not agent-facing instruction and
is deliberately kept out of SKILL.md.

Check the runtime package version first:

```bash
~/.lingtai-tui/runtime/venv/bin/python - <<'PY'
import importlib.metadata as md
print(md.version("lingtai"))
PY
```

Do not install a source branch that is older than the runtime package already
used by TUI. Rebase or cherry-pick the workbench skill onto the matching or
newer upstream LingTai release, build/install that branch, then verify that
the runtime can see the skill:

```bash
~/.lingtai-tui/runtime/venv/bin/python - <<'PY'
from pathlib import Path
import lingtai.intrinsic_skills as skills
root = Path(skills.__file__).parent
print((root / "nokv-workbench" / "SKILL.md").exists())
PY
```

## NoKV contract gate

The v0.5 skill requires the complete 18-tool restore-capable workbench surface,
strict terminal checkpoint expiry, root-bound snapshot operations, stable v1
commit identity, bounded snapshot annotations, explicit snapshot retirement,
and the metadata capability `restore_to_fork_v1`. The base surface has 17 tools;
`workbench_restore` is the capability-gated eighteenth tool. Do not silently
downgrade to an older NoKV build or replace restore with a client-side read/copy
loop.

Run this gate against the exact NoKV command, metadata owner, object store, and
resolved per-agent workbench root that the deployment will use. `NOKV_MCP_ARGS`
is a JSON array containing the same arguments from the registry record. Keep
global NoKV flags before `mcp`.

```bash
export NOKV_BIN=/path/to/nokv
export NOKV_MCP_ARGS='["--server-bind","127.0.0.1:7777","--object-backend","rustfs","--s3-bucket","nokv-lingtai-workbench","mcp","--profile","workbench","--workbench-root","/agents/preflight/wb"]'

~/.lingtai-tui/runtime/venv/bin/python - <<'PY'
import json
import os

from lingtai.services.mcp import MCPClient

expected_tools = {
    "workbench_create", "workbench_put_file", "workbench_append",
    "workbench_edit", "workbench_stat", "workbench_list", "workbench_read",
    "workbench_grep", "workbench_search", "workbench_aggregate",
    "workbench_catalog", "workbench_find", "workbench_commit",
    "workbench_snapshot", "workbench_snapshot_renew",
    "workbench_snapshot_retire", "workbench_snapshot_list",
    "workbench_restore",
}
expected_commit_schema = {
    "type": "object",
    "required": ["id", "manifest", "content_digest_uri"],
    "properties": {
        "id": {"type": "string"},
        "manifest": {"type": "object"},
        "content_digest_uri": {
            "type": "string",
            "pattern": "^sha256:[0-9a-f]{64}$",
            "description": "Stable caller-computed digest of the committed content. It must be known before this call and exactly match sha256:<64 lowercase hex>.",
        },
        "replace": {
            "type": "boolean",
            "description": "Explicitly replace a different or legacy commit. Concurrent identity changes still fail closed. Defaults false.",
        },
    },
    "additionalProperties": False,
}
expected_snapshot_schema = {
    "type": "object",
    "required": ["id"],
    "properties": {
        "id": {"type": "string"},
        "name": {
            "type": ["string", "null"],
            "description": "Checkpoint alias matching [A-Za-z0-9_-]{1,64}. Resolves to this snapshot in workbench_snapshot_renew, workbench_snapshot_list, and at_snapshot reads.",
        },
        "ttl_days": {
            "type": "integer",
            "minimum": 1,
            "maximum": 90,
            "description": "Lease length in days. Defaults to 7; values above 90 are rejected.",
        },
        "reason": {
            "type": ["string", "null"],
            "minLength": 1,
            "maxLength": 256,
            "description": "Optional human-readable checkpoint reason. At most 256 Unicode characters and 1024 UTF-8 bytes.",
        },
        "metadata": {
            "type": ["object", "null"],
            "maxProperties": 64,
            "description": "Optional JSON annotation object. Canonical encoded size is at most 4096 bytes, with at most 8 container levels and 64 object keys across the complete value.",
        },
    },
    "additionalProperties": False,
}
expected_retire_schema = {
    "type": "object",
    "required": ["id"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "snapshot_id": {
            "type": "integer",
            "minimum": 0,
            "description": "Snapshot id to retire. Provide exactly one of snapshot_id or name.",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Checkpoint name to retire. Provide exactly one of snapshot_id or name.",
        },
        "reason": {
            "type": ["string", "null"],
            "minLength": 1,
            "maxLength": 256,
            "description": "Optional human-readable retirement reason. At most 256 Unicode characters and 1024 UTF-8 bytes.",
        },
    },
    "oneOf": [
        {"required": ["snapshot_id"]},
        {"required": ["name"]},
    ],
    "additionalProperties": False,
}
expected_restore_schema = {
    "type": "object",
    "required": ["id", "at_snapshot", "destination_id"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "at_snapshot": {
            "anyOf": [
                {"type": "integer", "minimum": 0},
                {"type": "string", "minLength": 1},
            ],
        },
        "destination_id": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}

client = MCPClient(os.environ["NOKV_BIN"], json.loads(os.environ["NOKV_MCP_ARGS"]))
try:
    tools = {tool["name"]: tool for tool in client.list_tools()}
    missing = sorted(expected_tools - tools.keys())
    if missing:
        raise SystemExit(f"NoKV workbench tools missing: {missing}")
    expected_schemas = {
        "workbench_commit": expected_commit_schema,
        "workbench_snapshot": expected_snapshot_schema,
        "workbench_snapshot_retire": expected_retire_schema,
        "workbench_restore": expected_restore_schema,
    }
    for name, expected in expected_schemas.items():
        actual = tools[name].get("schema")
        if actual != expected:
            raise SystemExit(
                f"{name} raw schema mismatch:\n"
                + json.dumps(actual, indent=2, sort_keys=True)
            )
finally:
    client.close()

print("NoKV workbench v0.5 raw contract: OK")
PY
```

Run the check before Agent registration. LingTai adapts MCP schemas for model
tool registration, including removing top-level `additionalProperties`; that
adapted schema is not evidence that the server enforces strict input. The raw
gate above must observe all three required fields, non-empty string ids,
non-negative integer or non-empty string `at_snapshot`, and
`additionalProperties=false`. The server must still reject unknown fields,
`null`, and equal source/destination ids at runtime.

Advertising `workbench_restore` is the deployment proof that the connected
metadata owner supports `restore_to_fork_v1`. If capability probing fails, the
tool must be absent or calls must return `CapabilityMismatch` with
`retryable=false` and `details.capability="restore_to_fork_v1"`. Either result
fails deployment; do not add a hard-coded NoKV gate to LingTai Agent startup.

## Live acceptance gate

Run the checked-in NoKV/LingTai live acceptance harness with
`--profile full --require-all` before deploying a new pair of builds. It must
exercise the real RustFS service, NoKV metadata server, workbench MCP
subprocess, LingTai Agent MCP registration/reconnect path, and two real MCP
clients created from the resolved per-agent launch configuration. Unit-only
schema checks are not a deployment substitute.

The acceptance run must cover exact numeric-`snapshot_id` redrive after
transport/server/Agent restart and response-ack ambiguity, first-visible
manifest validity, source checkpoint retirement/deletion, independent
destination writes, and final shared-object reclamation. A successful restore
must report `state="complete"` and `cleanup_pending=false`; validate
`metadata/restore_manifest.json` as documented in SKILL.md before accepting the
destination.
