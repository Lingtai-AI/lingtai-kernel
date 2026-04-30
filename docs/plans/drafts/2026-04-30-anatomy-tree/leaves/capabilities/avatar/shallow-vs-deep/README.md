# Shallow vs Deep Avatar Types

## What

Two avatar spawn modes with fundamentally different starting states. Shallow is a
blank slate inheriting only configuration. Deep is a doppelgänger inheriting
character, knowledge, and exports — but not runtime state.

## Contract

### Shallow (投胎) — `type="shallow"` (default)

**Created by:** bare `mkdir()` on the avatar working directory (line 203).

**Inherits from parent's init.json (via `_make_avatar_init`, lines 359-430):**
- LLM provider/model config (re-materialized from preset on first boot).
- Capabilities list (re-materialized from preset on first boot).
- `manifest.preset` (default, active, allowed — re-rooted to parent dir).
- `manifest.stamina`, `manifest.soul.*`, `manifest.language`.
- File references: `covenant_file`, `principle_file`, `procedures_file`,
  `soul_file`, `env_file` — re-resolved to absolute paths.

**Explicitly NOT inherited (blanked/stripped):**
- `prompt` — blanked to `""` (arrives via `.prompt` signal).
- `prompt_file` — removed.
- `admin` — set to `{}` (no privileges).
- `comment` / `comment_file` — not inherited (parent can set explicitly).
- `brief` / `brief_file` — stripped.
- `addons` — stripped (each agent must be explicitly configured).
- `llm`, `capabilities` (materialized forms) — stripped so avatar re-materializes.

**On disk:** only `init.json` and `.prompt` signal file. Empty working directory.

### Deep (二重身) — `type="deep"`

**Created by:** `_prepare_deep()` (lines 437-484).

**Copied from parent:**

| Source | Destination | Method |
|--------|-------------|--------|
| `system/` | `system/` | `shutil.copytree` (full recursive copy, symlinks followed) |
| `codex/` | `codex/` | `shutil.copytree` (symlinks followed) |
| `exports/` | `exports/` | `shutil.copytree` (symlinks followed) |
| `combo.json` | `combo.json` | `shutil.copy2` |

**Symlink behavior:** All `copytree` calls use default `symlinks=False`, so symbolic
links in the source are *followed* and copied as regular files in the destination.
This is intentional — symlinks in `codex/` or `exports/` that point outside the
working directory would break after copy anyway. File permissions (mode bits) are
preserved by `shutil.copytree` and `shutil.copy2`.

**Explicitly NOT copied (lines 483-484):**
- `history/` — conversation history (each avatar starts fresh).
- `mailbox/` — mail state (each agent has its own mailbox).
- `delegates/` — parent's avatar ledger.
- `.agent.json` — runtime manifest (written fresh by child on boot).
- `.agent.heartbeat` — liveness file (written fresh by child on boot).
- `logs/` — runtime logs.

**init.json modifications:** Same as shallow — `_make_avatar_init` is called
identically for both types (line 241). The only difference is the working
directory content, not the init.json transformation.

### Scope guard (`_prepare_deep`, lines 445-451)

Before any `rmtree()` or `copytree()`, verifies that `dst.parent == src.parent`.
Refuses to copy if the destination is not a direct sibling of the source. This
prevents accidental network-root escapes even if called from future, less-validated code.

## Source

| What | File | Line(s) |
|------|------|---------|
| Type check (`"shallow"` / `"deep"`) | `src/lingtai/core/avatar/__init__.py` | 131 |
| Shallow: bare `mkdir` | same | 203 |
| Deep: `_prepare_deep` call | same | 200-201 |
| `_prepare_deep` implementation | same | 437-484 |
| Scope guard in `_prepare_deep` | same | 445-451 |
| "Not copied" comment | same | 483-484 |
| `_make_avatar_init` (shared by both) | same | 359-430 |

## Related

- `spawn` — the entry point that chooses shallow vs deep.
- `handshake-files` — both types write identical handshake files on boot.
