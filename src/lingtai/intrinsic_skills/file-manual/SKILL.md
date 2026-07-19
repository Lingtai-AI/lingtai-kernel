---
name: file-manual
description: "Operational guide for LingTai's built-in file tools: read, write, edit, glob, and grep. Use when working with local text files, deciding whether to use file tools versus bash, handling large files, avoiding binary/image misuse, or reading non-UTF-8 text via explicit bash/Python/iconv instead of complicating the core read tool. Covers UTF-8 policy, safe write/edit discipline, search workflows, and examples for GBK/Shift-JIS/Latin-1 conversion."
version: 0.1.0
tags: [files, read, write, edit, grep, glob, encoding, utf-8]
last_changed_at: "2026-07-19T00:00:00Z"
related_files:
- src/lingtai/tools/read/__init__.py
- src/lingtai/tools/write/__init__.py
- src/lingtai/tools/edit/__init__.py
- src/lingtai/intrinsic_skills/read-manual/SKILL.md
maintenance: |
  Tracks the tool/capability behavior it teaches; update when that tool's behavior changes.
---

# File Manual

Working guide for LingTai's built-in file tools. Use them for ordinary project
text: source code, Markdown, JSON/YAML/TOML, logs, prompts, skills, and notes.

Do **not** use them for binary/image/audio/video content. For images, use the
`vision` skill/tool. For arbitrary binary inspection or transcoding, use `bash`
with explicit commands.

## Choosing the right tool

| Need | Tool |
|---|---|
| Read a known text file (returns numbered lines) | `read` |
| Read a large file, paginate, or handle truncation | `read` with `offset`/`limit` — deeper semantics in `read-manual` |
| Create a new text file or replace a whole file | `write` |
| Make a small exact change | `edit` |
| Find files by name/path | `glob` |
| Search file contents by regex | `grep` |
| Decode non-UTF-8 text | `bash` + Python or `iconv` |
| Inspect binary format, archive, media | `bash` or a domain skill/tool |
| Analyze image content | `vision` |

## Encoding policy

LingTai's own text assets are UTF-8 — source code, prompts and system notes,
skills and knowledge entries, and JSON/YAML/TOML/Markdown config and docs. The
`read`/`write`/`edit` tools pin UTF-8 for exactly this reason.

Do not rely on the host locale. Windows Chinese/Japanese/Korean locales may
default Python text I/O to GBK/CP936/Shift-JIS-like encodings; internal LingTai
assets must never be decoded by guessing the locale.

For external or user-provided non-UTF-8 files, keep the core `read` tool simple
and use `bash` with an explicit encoding instead:

```bash
python - <<'PY'
from pathlib import Path
# encoding: 'gbk', 'shift_jis', 'latin-1', ... ; errors='replace' survives bad bytes
print(Path('file.txt').read_text(encoding='gbk', errors='replace'))
PY
```

Convert to UTF-8 with Python or `iconv`:

```bash
python - <<'PY'
from pathlib import Path
Path('legacy.utf8.txt').write_text(Path('legacy-gbk.txt').read_text(encoding='gbk'), encoding='utf-8')
PY

iconv -f gbk -t utf-8 legacy-gbk.txt > legacy-gbk.utf8.txt
iconv -f shift_jis -t utf-8 legacy-sjis.txt > legacy-sjis.utf8.txt
```

Rule: if a file will become part of the project, convert it to UTF-8 before
committing or storing it as a durable LingTai asset.

## Reading safely

Prefer `read` for known text files; its line numbers make later edits and
citations easier. If a file may be generated, minified, huge, or noisy, search
first and read only the relevant region:

```python
grep({"pattern": "class Agent|def handle", "path": "/abs/path/src", "glob": "*.py", "max_matches": 50})
read({"file_path": "/abs/path/src/module.py", "offset": 40, "limit": 80})
```

For the cap model, continuation via `next_offset`, and `line_truncated`
handling, read `read-manual` rather than improvising.

## Writing and editing safely

`write` is a full-file operation: use it to create a new file, replace a
generated artifact, or deliberately rewrite a small file you already understand.
Before overwriting an important existing file, read it first unless the human
explicitly asked for a blind overwrite. Do not use `write` for tiny
modifications to large files — use `edit`.

`edit` replaces an exact string and fails when the old string is absent or
ambiguous. That failure is a feature: it prevents accidental broad changes.

1. `read` the relevant lines.
2. Copy an exact old-string region with enough surrounding context to be unique.
3. Call `edit` once.
4. Re-read the changed region or run tests.

Use `replace_all=true` only when every occurrence is supposed to change and you
have checked the match set with `grep` first.

## Search workflow

Start broad with `glob` (file names), narrow with `grep` (file contents), then
inspect with `read`.

```python
glob({"pattern": "**/*.py", "path": "/abs/path/project"})
grep({"pattern": "read_text\\(", "path": "/abs/path/project/src", "glob": "*.py", "max_matches": 100})
```

## File paths and privacy

Use absolute paths with file tools. Paths inside your working directory may be
private to this agent. Do not send local private paths to other agents or
humans unless they are useful and safe for that recipient; another agent cannot
dereference your local path. When sharing file content, quote the relevant
content or attach/export a file through the appropriate communication channel.

## Manual versus ordinary calls

Normal file work is primary. Each file tool has two explicit modes:

- **Ordinary work:** for backward compatibility, omit `action` or set it to the
  tool name: `action="read"`, `"write"`, `"edit"`, `"glob"`, or `"grep"`.
  Supply the ordinary arguments shown in that tool's schema.
- **Manual lookup:** use `action="manual"` as a one-time entry when you need the
  installed workflow guide. It returns documentation and performs no file
  operation. `write`, `edit`, `glob`, and `grep` return this manual; `read`
  returns `read-manual`.

After a manual result, continue the original task with an ordinary call. Do not
request the same manual again. Repeating an identical manual call is an error loop,
not progress.
