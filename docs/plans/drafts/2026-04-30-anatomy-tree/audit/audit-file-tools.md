# Audit Report: §Source References in file-tools Anatomy Leaves

**Auditor:** audit-file-tools  
**Date:** 2026-04-30T01:24:00-07:00  
**Scope:** 5 README.md files under `leaves/capabilities/file/`  
**Kernel source root:** `/Users/huangzesen/Documents/GitHub/lingtai-kernel/src/lingtai/`

---

## capabilities/file/read/

### §Source Table References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 1 | `core/read/__init__.py:39` | Handler: `handle_read` | ✅ Line 39: `def handle_read(args: dict) -> dict:` — exact match |
| 2 | `core/read/__init__.py:22` | Schema: `get_schema` | ✅ Line 22: `def get_schema(lang: str = "en") -> dict:` — exact match |
| 3 | `services/file_io.py:72` | I/O backend: `LocalFileIOService.read` | ✅ Line 72: `def read(self, path: str) -> str:` — exact match |
| 4 | `core/read/__init__.py:59` | Registration: `agent.add_tool` | ✅ Line 59: `agent.add_tool("read", schema=get_schema(lang), handler=handle_read, ...)` — exact match |

### §Behaviors Cross-References (inline line refs in Behaviors/Related)

The read README's §Behaviors section has no explicit `file.py:NN` line references — it describes behaviors in prose only ("Binary files that fail UTF-8 decode raise a generic read error", "offset clamps to 0 internally via `max(0, offset - 1)`", "Relative paths are joined to `agent._working_dir`"). These behavioral claims are all verified against the handler source:
- Binary → UTF-8 decode error: handler calls `agent._file_io.read(path)` (line 48) which calls `read_text(encoding="utf-8")` (file_io.py:73)
- offset clamp: handler line 54: `start = max(0, offset - 1)`
- Relative paths: handler lines 43-44: `if not Path(path).is_absolute(): path = str(agent._working_dir / path)`

### §Related Cross-References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 5 | `capabilities/__init__.py:37` (Related line 47) | Grouped capability: `file` bundles read + write + edit + glob + grep | ✅ Line 37: `_GROUPS: dict[str, list[str]] = {"file": ["read", "write", "edit", "glob", "grep"]}` — exact match |

**Summary: 5 ✅ / 0 ⚠️ / 0 ❌ out of 5 total Source references (4 table + 1 cross-ref)**

---

## capabilities/file/write/

### §Source Table References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 1 | `core/write/__init__.py:36` | Handler: `handle_write` | ✅ Line 36: `def handle_write(args: dict) -> dict:` — exact match |
| 2 | `core/write/__init__.py:20` | Schema: `get_schema` | ✅ Line 20: `def get_schema(lang: str = "en") -> dict:` — exact match |
| 3 | `services/file_io.py:75` | I/O backend: `LocalFileIOService.write` | ✅ Line 75: `def write(self, path: str, content: str) -> None:` — exact match |
| 4 | `services/file_io.py:77` | mkdir at :77 | ✅ Line 77: `p.parent.mkdir(parents=True, exist_ok=True)` — exact match |
| 5 | `services/file_io.py:78` | write_text at :78 | ✅ Line 78: `p.write_text(content, encoding="utf-8")` — exact match |
| 6 | `core/write/__init__.py:49` | Registration: `agent.add_tool` | ✅ Line 49: `agent.add_tool("write", schema=get_schema(lang), handler=handle_write, ...)` — exact match |

### §Behaviors Cross-References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 7 | `services/file_io.py:77` (Behaviors §1) | `parent.mkdir(parents=True, exist_ok=True)` — same line as Source #4 | ✅ consistent, exact |
| 8 | `services/file_io.py:78` (Behaviors §3) | Writes UTF-8 encoding | ✅ Line 78: `p.write_text(content, encoding="utf-8")` — exact match |

**Summary: 8 ✅ / 0 ⚠️ / 0 ❌ out of 8 total Source references (6 table + 2 cross-ref)**

---

## capabilities/file/edit/

### §Source Table References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 1 | `core/edit/__init__.py:38` | Handler: `handle_edit` | ✅ Line 38: `def handle_edit(args: dict) -> dict:` — exact match |
| 2 | `core/edit/__init__.py:20` | Schema: `get_schema` | ✅ Line 20: `def get_schema(lang: str = "en") -> dict:` — exact match |
| 3 | `core/edit/__init__.py:53-57` | Ambiguity check (count == 0, count > 1) | ✅ Lines 53-57: `count = content.count(old)` → `if count == 0: return error` → `if count > 1 and not replace_all: return error` — exact match |
| 4 | `services/file_io.py:80-93` | I/O backend: `LocalFileIOService.edit` | ⚠️ Line 80: `def edit(self, path: str, old_string: str, new_string: str) -> str:` — correct function, but the README's own Behaviors section says "the handler uses read+write directly, not the service's `edit` method" and "Counts occurrences before deciding (`content.count(old)` at `:53`)". The handler at :48 calls `agent._file_io.read(path)` and at :63 calls `agent._file_io.write(path, updated)`. So the service's edit method exists but is NOT used by the handler. The Source table correctly notes this: "(note: the handler uses read+write directly, not the service's `edit` method)". The reference is accurate but the service method is informational only. |
| 5 | `core/edit/__init__.py:68` | Registration: `agent.add_tool` | ✅ Line 68: `agent.add_tool("edit", schema=get_schema(lang), handler=handle_edit, ...)` — exact match |

### §Behaviors Cross-References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 6 | `core/edit/__init__.py:61` (Behaviors §2) | `content.replace(old, new, 1)` | ✅ Line 61: `updated = content.replace(old, new, 1)` — exact match |
| 7 | `core/edit/__init__.py:59` (Behaviors §3) | `content.replace(old, new)` | ✅ Line 59: `updated = content.replace(old, new)` — exact match |
| 8 | `core/edit/__init__.py:53` (Behaviors §4) | `content.count(old)` | ✅ Line 53: `count = content.count(old)` — exact match |

**Summary: 7 ✅ / 1 ⚠️ / 0 ❌ out of 8 total Source references (5 table + 3 cross-ref)**

The ⚠️ is on `services/file_io.py:80-93` — the line reference is accurate and the function exists, but the service's `edit` method is not actually called by the handler. The README correctly notes this as an informational reference ("the handler uses read+write directly"), so this is acceptable but worth flagging for clarity.

---

## capabilities/file/glob/

### §Source Table References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 1 | `core/glob/__init__.py:36` | Handler: `handle_glob` | ✅ Line 36: `def handle_glob(args: dict) -> dict:` — exact match |
| 2 | `core/glob/__init__.py:20` | Schema: `get_schema` | ✅ Line 20: `def get_schema(lang: str = "en") -> dict:` — exact match |
| 3 | `services/file_io.py:95` | I/O backend: `LocalFileIOService.glob` | ✅ Line 95: `def glob(self, pattern: str, root: str | None = None) -> list[str]:` — exact match |
| 4 | `core/glob/__init__.py:49` | Registration: `agent.add_tool` | ✅ Line 49: `agent.add_tool("glob", schema=get_schema(lang), handler=handle_glob, ...)` — exact match |

### §Behaviors Cross-References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 5 | `services/file_io.py:101` (Behaviors §1) | `os.walk` | ✅ Line 101: `for dirpath, _dirnames, filenames in os.walk(search_root):` — exact match |
| 6 | `services/file_io.py:106` (Behaviors §2) | `fnmatch.fnmatch` | ✅ Line 106: `if fnmatch.fnmatch(rel, pattern):` — exact match |
| 7 | `services/file_io.py:108` (Behaviors §5) | `sorted()` | ✅ Line 108: `return sorted(results)` — exact match |

**Summary: 7 ✅ / 0 ⚠️ / 0 ❌ out of 7 total Source references (4 table + 3 cross-ref)**

---

## capabilities/file/grep/

### §Source Table References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 1 | `core/grep/__init__.py:39` | Handler: `handle_grep` | ✅ Line 39: `def handle_grep(args: dict) -> dict:` — exact match |
| 2 | `core/grep/__init__.py:20` | Schema: `get_schema` | ✅ Line 20: `def get_schema(lang: str = "en") -> dict:` — exact match |
| 3 | `core/grep/__init__.py:27` | glob at :27 | ✅ Line 27: `"glob": {"type": "string", "description": t(lang, "grep.glob"), "default": "*"}` — exact match |
| 4 | `core/grep/__init__.py:47-58` | Glob filter: post-scan `fnmatch` | ✅ Lines 47-58: `glob_filter = args.get("glob", "*")` → raw scan → `fnmatch.fnmatch(Path(r.path).name, glob_filter)` filtering — exact match |
| 5 | `services/file_io.py:110` | I/O backend: `LocalFileIOService.grep` | ✅ Line 110: `def grep(self, pattern: str, path: str | None = None, max_results: int = 50) -> list[GrepMatch]:` — exact match |
| 6 | `core/grep/__init__.py:69` | Registration: `agent.add_tool` | ✅ Line 69: `agent.add_tool("grep", schema=get_schema(lang), handler=handle_grep, ...)` — exact match |

### §Behaviors Cross-References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 7 | `services/file_io.py:120` (Behaviors §1) | `sorted(search_path.rglob("*"))` | ✅ Line 120: `files = sorted(search_path.rglob("*"))` — exact match |
| 8 | `services/file_io.py:127` (Behaviors §2) | `UnicodeDecodeError` and `PermissionError` cause skip | ✅ Line 127: `except (UnicodeDecodeError, PermissionError):` + Line 128: `continue` — exact match |
| 9 | `services/file_io.py:113` (Behaviors §3) | `re.compile(pattern)` | ✅ Line 113: `regex = re.compile(pattern)` — exact match |
| 10 | `services/file_io.py:132-133` (Behaviors §4) | Capped at `max_matches` — returns immediately | ✅ Lines 132-133: `if len(results) >= max_matches:` / `return results` — exact match |

### §Note Block Cross-References

| # | Reference | Claim | Verdict |
|---|-----------|-------|---------|
| 11 | (behavioral, no line ref) | `max_matches` caps the raw scan, not the filtered output | ✅ Accurate — handler at :49 calls `agent._file_io.grep(pattern, path=search_path, max_results=max_matches)` which applies the cap at the service level (file_io.py:132), then the handler applies fnmatch filtering post-hoc (:54-58). The `truncated` flag is set from `raw_truncated = len(raw_results) >= max_matches` (:50) before filtering. |

**Summary: 11 ✅ / 0 ⚠️ / 0 ❌ out of 11 total Source references (6 table + 4 cross-ref + 1 behavioral)**

---

## Global Summary

| Leaf | ✅ | ⚠️ | ❌ | Total |
|------|---|---|---|-------|
| `capabilities/file/read/` | 5 | 0 | 0 | 5 |
| `capabilities/file/write/` | 8 | 0 | 0 | 8 |
| `capabilities/file/edit/` | 7 | 1 | 0 | 8 |
| `capabilities/file/glob/` | 7 | 0 | 0 | 7 |
| `capabilities/file/grep/` | 11 | 0 | 0 | 11 |
| **Total** | **38** | **1** | **0** | **39** |

### Notes

1. **All file paths exist** in the kernel source tree. No missing or renamed files.
2. **All line numbers are within ±0 lines** of the actual source — no drift detected. The documentation was clearly written against the current source.
3. **The single ⚠️** is on `services/file_io.py:80-93` in the edit README: the line reference is correct and the function exists, but the README itself notes the handler doesn't use this service method (it uses read+write directly). This is more of an informational/reference entry than a direct code path. Acceptable but flagged for transparency.
4. **No references were missing** — I checked for nearby code that should have been referenced but wasn't. The documentation covers all relevant functions and line ranges comprehensively.
5. **Cross-reference accuracy**: The `capabilities/__init__.py:37` reference in the Related section of read/write/edit/glob/grep is verified accurate.
