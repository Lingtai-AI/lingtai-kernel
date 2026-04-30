# Avatar Spawn

## What

Spawns an independent peer agent as a fully detached process (`lingtai run <dir>`).
The avatar lives as a sibling directory under the same `.lingtai/` network root,
receives a modified copy of the parent's `init.json`, and boots into ASLEEP state
waiting for messages.

## Contract

### Name validation (`_spawn` → lines 138-152)

- Must match `^[\w-]+$` (Unicode `\w` — any script — plus hyphen).
- Must not be `"."` or `".."`, must not start with `"."`.
- Max length: 64 characters.
- Single-segment only — no slashes, dots, spaces.

### Workdir creation (lines 179-203)

- Created as `parent._working_dir.parent / peer_name` (sibling of parent).
- Defense-in-depth scope check: resolved parent must equal network root.
- If directory already exists → error (not overwrite).
- For `deep`: `_prepare_deep()` copies parent content; for `shallow`: bare `mkdir()`.

### init.json inheritance (lines 239-247, `_make_avatar_init` 359-430)

- Deep-copies parent's `init.json`.
- Sets `manifest.agent_name` to avatar name.
- Blanks `prompt` field (arrives via `.prompt` signal file instead).
- Sets `admin` to `{}` (no privileges).
- Strips: `comment_file`, `brief`, `brief_file`, `addons`.
- Re-roots relative preset paths against parent working dir.
- Forces `preset.active = preset.default` (avatars always use parent's DEFAULT preset).
- Strips materialized `llm` and `capabilities` (re-materialized on first boot).

### Process launch (lines 496-536, `_launch`)

- Resolves Python via `venv_resolve.resolve_venv(init_data)`.
- Command: `[python, "-m", "lingtai", "run", str(working_dir)]`.
- Fully detached (`start_new_session=True`, stdin=DEVNULL, stdout=DEVNULL).
- Stderr captured to `logs/spawn.stderr`.

### First prompt delivery (lines 223-251)

- Parent identity prompt + caller's `reasoning` written to `.prompt` signal file.
- Kernel's heartbeat loop consumes `.prompt` on first poll (one-shot, consumed via unlink).

### Ledger (lines 110-119, 267-278)

- Every spawn event appended to `delegates/ledger.jsonl` (JSONL, one record per line).
- Fields: `ts`, `event="avatar"`, `name`, `working_dir`, `mission`, `type`, `pid`, `boot_status`.

### Duplicate detection (lines 155-167)

- Before spawning, reads ledger for records matching `peer_name`.
- If a matching record exists AND `is_alive(working_dir)` returns true → `already_active` error.

## Source

| What | File | Line(s) |
|------|------|---------|
| AvatarManager class | `src/lingtai/core/avatar/__init__.py` | 85 |
| `_spawn` entry point | same | 125-316 |
| Name regex + max len | same | 41-42 |
| Name validation block | same | 138-152 |
| Workdir creation + scope check | same | 179-203 |
| `_make_avatar_init` | same | 359-430 |
| `_prepare_deep` | same | 437-484 |
| `_launch` (Popen) | same | 496-536 |
| `_wait_for_boot` | same | 318-352 |
| `.prompt` signal file write | same | 249-251 |
| Ledger append | same | 114-119 |
| Duplicate detection | same | 155-167 |

## Related

- `boot-verification` — how the parent confirms the avatar started.
- `shallow-vs-deep` — what content is copied.
- `handshake-files` — what files constitute "alive."
- `lingtai/cli.py:run()` (line 147) — the entry point the child process calls.
