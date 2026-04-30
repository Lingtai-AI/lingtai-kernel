# Audit: §Source References — Shell / Codex Leaves

**Auditor:** audit-shell-codex  
**Date:** 2026-04-30  
**Kernel source root:** `lingtai-kernel/src/lingtai/`  
**Scope:** 6 leaves under `leaves/capabilities/shell/`

---

## capabilities/shell/bash/README.md

Source table has 12 rows. All references to `lingtai/core/bash/__init__.py` (255 lines) and `lingtai/core/bash/bash_policy.json`.

| # | What | Claimed Lines | Actual Lines | Verdict | Notes |
|---|------|---------------|--------------|---------|-------|
| 1 | Module docstring + usage | 1-10 | 1-10 | ✅ | Docstring opens L1 `"""`, closes L10 `"""`. Usage examples on L8-9. |
| 2 | Default policy file constant | 26 | 26 | ✅ | `_DEFAULT_POLICY_FILE = Path(__file__).parent / "bash_policy.json"` |
| 3 | Schema definition | 32-51 | 32-51 | ✅ | `def get_schema()` at L32, closing `}` of return dict at L51. |
| 4 | `BashPolicy` class | 55-143 | 55-143 | ✅ | `class BashPolicy:` at L55, `_extract_commands` returns at L143. |
| 5 | `BashPolicy.yolo()` | 81-83 | 81-83 | ✅ | `def yolo(cls)` at L81, `return cls()` at L83. |
| 6 | `is_allowed()` + `_extract_commands()` | 99-143 | 99-143 | ✅ | `is_allowed` at L99, `_extract_commands` returns at L143. |
| 7 | `BashManager` class + `handle()` | 146-214 | 146-214 | ✅ | `class BashManager:` at L146, generic exception catch at L214. |
| 8 | Working dir sandbox validation | 176-187 | 176-187 | ✅ | Comment `# Validate working_dir...` at L176, `Invalid working_dir path` return at L187. |
| 9 | `subprocess.run` call | 190-197 | 190-197 | ✅ | `result = subprocess.run(` at L190, closing `)` at L197. |
| 10 | Output truncation | 200-203 | 200-203 | ✅ | `if len(stdout) > self._max_output:` at L200, stderr truncation at L202-203. |
| 11 | `setup()` entry point | 217-255 | 217-255 | ✅ | `def setup(` at L217, `return mgr` at L255. File ends at L255. |
| 12 | Default policy JSON | full file | full file | ✅ | `bash_policy.json` exists at expected path. |

**Summary: 12 ✅ / 0 ⚠️ / 0 ❌**

---

## capabilities/shell/bash/yolo/README.md

Source table has 6 rows. References to `lingtai/core/bash/__init__.py` and `bash_policy.json`.

| # | What | Claimed Lines | Actual Lines | Verdict | Notes |
|---|------|---------------|--------------|---------|-------|
| 1 | `BashPolicy.__init__` (allow=None, deny=None) | 66-69 | 66-69 | ✅ | `def __init__` at L66, `self._deny` assignment at L69. |
| 2 | `BashPolicy.yolo()` factory | 81-83 | 81-83 | ✅ | Same as parent leaf. |
| 3 | `is_allowed()` early return when both None | 104-105 | 104-105 | ✅ | `if self._allow is None and self._deny is None:` at L104, `return True` at L105. |
| 4 | `describe()` empty return | 86-88 | 85-88 | ⚠️ | `def describe(self)` declared at L85, docstring at L86, condition at L87, `return ""` at L88. Claimed start is L86; the `def` is at L85. Off by 1 — the range misses the method signature. Functional content (condition + return) is at L87-88, so the core logic is captured. |
| 5 | `setup()` yolo branch | 235-236 | 235-236 | ✅ | `if yolo:` at L235, `policy = BashPolicy.yolo()` at L236. |
| 6 | Default policy JSON | full file | full file | ✅ | File exists. |

**Summary: 5 ✅ / 1 ⚠️ / 0 ❌**

*Note:* The ⚠️ on `describe()` is cosmetic — L86-88 contains the condition and return statement, which is the behavior the leaf documents. The missing L85 is just the method signature line.

---

## capabilities/shell/bash/sandbox/README.md

Source table has 5 rows. All references to `lingtai/core/bash/__init__.py`.

| # | What | Claimed Lines | Actual Lines | Verdict | Notes |
|---|------|---------------|--------------|---------|-------|
| 1 | Default cwd assignment | 174 | 174 | ✅ | `cwd = args.get("working_dir", self._working_dir)` |
| 2 | Path resolution + sandbox check | 177-185 | 177-185 | ✅ | `try:` at L177, `resolved`/`sandbox` computation through L180, error return through L185. |
| 3 | Invalid path error handling | 186-187 | 186-187 | ✅ | `except (ValueError, OSError):` at L186, `return {"status": "error", "message": "Invalid working_dir path"}` at L187. |
| 4 | `BashManager.__init__` stores working_dir | 149-157 | 149-157 | ✅ | `def __init__` at L149, `self._max_output = max_output` at L157. |
| 5 | `setup()` passes agent dir to manager | 244-247 | 244-247 | ✅ | `mgr = BashManager(` at L244, closing `)` at L247. |

**Summary: 5 ✅ / 0 ⚠️ / 0 ❌**

---

## capabilities/shell/bash/kill/README.md

Source table has 5 rows. All references to `lingtai/core/bash/__init__.py`.

| # | What | Claimed Lines | Actual Lines | Verdict | Notes |
|---|------|---------------|--------------|---------|-------|
| 1 | Default timeout in schema | 43 | 43 | ✅ | `"default": 30,` — the timeout property's default in the JSON schema. |
| 2 | Default timeout in handle() | 173 | 173 | ✅ | `timeout = args.get("timeout", 30)` |
| 3 | `subprocess.run()` with timeout | 190-197 | 190-197 | ✅ | Same as parent leaf. |
| 4 | `TimeoutExpired` catch | 211-212 | 211-212 | ✅ | `except subprocess.TimeoutExpired:` at L211, error return at L212. |
| 5 | Generic exception catch | 213-214 | 213-214 | ✅ | `except Exception as e:` at L213, error return at L214. |

**Summary: 5 ✅ / 0 ⚠️ / 0 ❌**

---

## capabilities/shell/codex/README.md

Source table has 8 rows. All references to `lingtai/core/codex/__init__.py` (372 lines).

| # | What | Claimed Lines | Actual Lines | Verdict | Notes |
|---|------|---------------|--------------|---------|-------|
| 1 | Schema + `CodexManager` class | 33-356 | 33-356 | ✅ | `def get_schema()` at L33, `_export` method's final return at L356. |
| 2 | `DEFAULT_MAX_ENTRIES = 20` | 85 | 85 | ✅ | Exact match. |
| 3 | `_inject_catalog()` | 100-118 | 100-118 | ✅ | `def _inject_catalog(self)` at L100, final `update_system_prompt` call at L118. |
| 4 | `_load_entries()` / `_save_entries()` | 124-156 | 124-156 | ✅ | `_load_entries` at L124, `_save_entries` ends with `raise` at L156. |
| 5 | `_make_id()` SHA-256 | 158-162 | 158-162 | ✅ | `@staticmethod` at L158, `hexdigest()[:8]` at L162. |
| 6 | `_submit()` / `_filter()` / `_view()` | 184-272 | 184-272 | ✅ | `_submit` at L184, `_view` returns at L272. |
| 7 | `_consolidate()` / `_delete()` / `_export()` | 274-356 | 274-356 | ✅ | `_consolidate` at L274, `_export` returns at L356. |
| 8 | `setup()` entry point | 359-372 | 359-372 | ✅ | `def setup(` at L359, `return mgr` at L372. File ends at L372. |

**Summary: 8 ✅ / 0 ⚠️ / 0 ❌**

---

## capabilities/shell/codex/oauth-originator/README.md

Source table has 13 rows spanning 3 files:
- `lingtai/auth/codex.py` (145 lines)
- `lingtai/llm/_register.py` (93 lines)
- `lingtai/core/codex/__init__.py` (372 lines)

| # | What | File | Claimed Lines | Actual Lines | Verdict | Notes |
|---|------|------|---------------|--------------|---------|-------|
| 1 | Module docstring | `auth/codex.py` | 1-4 | 1-5 | ⚠️ | Docstring opens at L1 `"""`, closes at L5 `"""`. Lines 1-4 contain all prose text but miss the closing triple-quote delimiter on L5. Minor — the content is complete; only the syntactic terminator is excluded. |
| 2 | Token URL constant | `auth/codex.py` | 17 | 17 | ✅ | `TOKEN_URL = "https://auth.openai.com/oauth/token"` |
| 3 | Client ID constant | `auth/codex.py` | 18 | 18 | ✅ | `CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"` |
| 4 | Refresh buffer (300s) | `auth/codex.py` | 19 | 19 | ✅ | `REFRESH_BUFFER_SECONDS = 300` |
| 5 | `CodexTokenManager` class | `auth/codex.py` | 30-145 | 30-145 | ✅ | `class CodexTokenManager:` at L30, cache invalidation at L145. File ends at L145. |
| 6 | `is_authenticated()` | `auth/codex.py` | 46-52 | 46-52 | ✅ | Method at L46, `return False` at L52. |
| 7 | `get_access_token()` | `auth/codex.py` | 54-66 | 54-66 | ✅ | Method at L54, `return data["access_token"]` at L66. |
| 8 | `_read()` with mtime cache | `auth/codex.py` | 72-90 | 72-90 | ✅ | Method at L72, `return data` at L90. |
| 9 | `_refresh()` with file lock | `auth/codex.py` | 92-145 | 92-145 | ✅ | Method at L92, `self._cache_mtime = 0.0` at L145. |
| 10 | `_codex` LLM factory in `_register.py` | `llm/_register.py` | 54-82 | 54-82 | ✅ | `def _codex` at L54, `LLMService.register_adapter("codex", _codex)` at L82. |
| 11 | Monkey-patched `create_chat` | `llm/_register.py` | 71-74 | 70-74 | ⚠️ | `_orig_create_chat = adapter.create_chat` at L70, wrapper at L71-73, assignment `adapter.create_chat = ...` at L74. Claimed start is L71; the backup assignment (`_orig_create_chat = ...`) is at L70. Off by 1 — misses the backup line. |
| 12 | Monkey-patched `generate` | `llm/_register.py` | 75-79 | 75-79 | ✅ | `_orig_generate = adapter.generate` at L75, wrapper at L76-78, assignment at L79. |
| 13 | Codex entry `_make_id` (no originator) | `core/codex/__init__.py` | 158-162 | 158-162 | ✅ | `@staticmethod` at L158, `hexdigest()[:8]` at L162. |

**Summary: 11 ✅ / 2 ⚠️ / 0 ❌**

*Notes on ⚠️ items:*
- **#1 (docstring):** Lines 1-4 capture all human-readable content of the docstring. Line 5 is the closing `"""` delimiter — a syntactic boundary, not semantic content.
- **#11 (create_chat):** Lines 71-74 capture the wrapper function and assignment. The missing L70 (`_orig_create_chat = adapter.create_chat`) is the backup of the original method — important for understanding the monkey-patch pattern but not the wrapper itself.

---

## Global Summary

| Leaf | ✅ | ⚠️ | ❌ | Notes |
|------|-----|-----|-----|-------|
| `bash/` | 12 | 0 | 0 | All 12 references exact |
| `bash/yolo/` | 5 | 1 | 0 | `describe()` off-by-1 (L86→L85) |
| `bash/sandbox/` | 5 | 0 | 0 | All 5 references exact |
| `bash/kill/` | 5 | 0 | 0 | All 5 references exact |
| `codex/` | 8 | 0 | 0 | All 8 references exact |
| `codex/oauth-originator/` | 11 | 2 | 0 | Docstring range off-by-1, create_chat backup off-by-1 |
| **TOTAL** | **46** | **3** | **0** | |

### Assessment

All 49 source references across 6 leaves point to files that exist. No references point to deleted, renamed, or restructured code. No line numbers are off by more than 1 line.

The 3 ⚠️ items are all **off-by-1** in range boundaries — including one missing a method signature line, one missing a closing `"""` delimiter, and one missing a backup-assignment line. None represent broken references or stale documentation. The content described in each "What" column is verifiably present at the referenced lines.

**No ❌ findings. No corrections required.**

### Files checked

| File | Lines | Exists |
|------|-------|--------|
| `lingtai/core/bash/__init__.py` | 255 | ✅ |
| `lingtai/core/bash/bash_policy.json` | — | ✅ |
| `lingtai/core/codex/__init__.py` | 372 | ✅ |
| `lingtai/auth/codex.py` | 145 | ✅ |
| `lingtai/llm/_register.py` | 93 | ✅ |
