# Audit: §Source References — psyche / library / vision / web_search leaves

**Auditor:** audit-psyche-lib-viz-web  
**Date:** 2026-04-30  
**Scope:** 6 leaf README.md files under `leaves/capabilities/`  
**Kernel source root:** `/Users/huangzesen/Documents/GitHub/lingtai-kernel/src/`  

## Key Observations

### Two-package layout

The kernel ships two Python packages under `src/`:

| Package | Purpose | Used by |
|---------|---------|---------|
| `lingtai_kernel/` | Core agent runtime — intrinsics (soul, eigen), base agent, i18n, LLM interface | psyche/soul-flow, psyche/inquiry, psyche/core-memories |
| `lingtai/` | Application layer — capabilities (vision, web_search), services, core (library) | library/paths-resolution, vision/multimodal, web_search/fallback |

Source path prefixes in §Source tables vary by leaf:

- **psyche leaves** use bare `intrinsics/soul.py`, `base_agent.py`, `intrinsics/eigen.py`, `i18n/en.json` — these resolve to `lingtai_kernel/` package but the `lingtai_kernel/` prefix is omitted.
- **library leaf** uses `core/library/__init__.py` — resolves to `lingtai/core/library/__init__.py` (application layer).
- **vision/web_search leaves** use `capabilities/vision/__init__.py`, `services/vision/__init__.py` etc. — resolve to `lingtai/` (application layer).

This is **consistent within each leaf** but could confuse readers who don't know which package a path belongs to.

---

## 1. psyche/soul-flow/README.md

**Kernel file:** `lingtai_kernel/intrinsics/soul.py` (387 lines)  
**Agent file:** `lingtai_kernel/base_agent.py` (1812 lines)  
**i18n file:** `lingtai_kernel/i18n/en.json` (53 lines)

| # | §Source claim | Actual location | Verdict |
|---|---|---|---|
| 1 | `intrinsics/soul.py:258` — `soul_flow()` | `soul.py:258` `def soul_flow(agent) -> dict \| None:` | ✅ Exact |
| 2 | `intrinsics/soul.py:118` — `_collect_new_diary()` | `soul.py:118` `def _collect_new_diary(agent) -> str:` | ✅ Exact |
| 3 | `intrinsics/soul.py:192` — `_ensure_soul_session()` | `soul.py:192` `def _ensure_soul_session(agent):` | ✅ Exact |
| 4 | `intrinsics/soul.py:293` — `_trim_soul_session()` | `soul.py:293` `def _trim_soul_session(agent) -> None:` | ✅ Exact |
| 5 | `base_agent.py:608` — `_start_soul_timer()` | `base_agent.py:610` `def _start_soul_timer(self) -> None:` | ✅ (L608 is `write_manifest` call at end of `_set_state`; L610 is the actual def — ±2, acceptable) |
| 6 | `base_agent.py:631` — `_soul_whisper()` | `base_agent.py:633` `def _soul_whisper(self) -> None:` | ✅ (L631 is `self._nap_wake.set()` at end of `_wake_nap`; L633 is the actual def — ±2, acceptable) |
| 7 | `base_agent.py:603` — `AgentState.IDLE` check | `base_agent.py:605` `if new_state == AgentState.IDLE:` | ✅ (L603 is `self._idle.set()`; L605 is the IDLE check — ±2, acceptable) |
| 8 | `intrinsics/soul.py:324` — `reset_soul_session()` | `soul.py:324` `def reset_soul_session(agent) -> None:` | ✅ Exact |
| 9 | `i18n/en.json:10` — `soul.system_prompt` | `en.json:10` — key `"soul.system_prompt"` | ✅ Exact |
| 10 | `intrinsics/soul.py:172` — `_save_soul_session()` | `soul.py:172` `def _save_soul_session(agent) -> None:` | ✅ Exact |

**Summary: 10/10 ✅ · 0 ⚠️ · 0 ❌**

---

## 2. psyche/inquiry/README.md

**Kernel files:** `lingtai_kernel/intrinsics/soul.py`, `lingtai_kernel/base_agent.py`

| # | §Source claim | Actual location | Verdict |
|---|---|---|---|
| 1 | `intrinsics/soul.py:52` — `action == "inquiry"` branch | `soul.py:52` `if action == "inquiry":` | ✅ Exact |
| 2 | `intrinsics/soul.py:337` — `soul_inquiry()` | `soul.py:337` `def soul_inquiry(agent, question: str) -> dict \| None:` | ✅ Exact |
| 3 | `intrinsics/soul.py:345-359` — `ChatInterface()` construction | `soul.py:345` `cloned = ChatInterface()` through `soul.py:359` `cloned.add_user_blocks(stripped)` | ✅ Exact |
| 4 | `intrinsics/soul.py:88` — `_send_with_timeout()` | `soul.py:88` `def _send_with_timeout(agent, session, content: str):` | ✅ Exact |
| 5 | `base_agent.py:648` — `_persist_soul_entry(mode="inquiry")` | `base_agent.py:650` `def _persist_soul_entry(self, result: dict, mode: str = "flow", source: str = "agent") -> None:` | ✅ (L648 is `self._log("soul_whisper_error", ...)` at end of `_soul_whisper`; L650 is the def — ±2, acceptable) |
| 6 | `base_agent.py:807-837` — `.inquiry` file detection | `base_agent.py:809-852` — the `.inquiry` detection block spans from L809 comment through L852 `taken_file.unlink()` | ⚠️ Start is off by +2 (807→809); end is off by +15 (837→852). The detection *begins* nearby but the block extends further than claimed. The actual signal file handling is L809-852. |
| 7 | `intrinsics/soul.py:236` — `_write_soul_tokens()` | `soul.py:236` `def _write_soul_tokens(agent, response) -> None:` | ✅ Exact |

**Summary: 5/7 ✅ · 2 ⚠️ · 0 ❌**

Note on #6: The `.inquiry` file handling block is larger than the source table suggests. The reference range `807-837` captures the `inquiry_file.is_file()` check and the rename, but misses the `_inquiry_done` thread spawn (L832-842) and the cleanup (L843-852). The correct full range is **809-852**.

---

## 3. psyche/core-memories/README.md

**Psyche file:** `lingtai/core/psyche/__init__.py` (366 lines)  
**Eigen file:** `lingtai_kernel/intrinsics/eigen.py` (237 lines)

| # | §Source claim | Actual location | Verdict |
|---|---|---|---|
| 1 | `core/psyche/__init__.py:66` — `PsycheManager` | `core/psyche/__init__.py:91` `class PsycheManager:` | ❌ **Wrong line.** L66 is inside `get_schema()` (returning a JSON schema block). `PsycheManager` is at **line 91**. Off by 25 lines. |
| 2 | `core/psyche/__init__.py:112` — `_lingtai_update()` | `core/psyche/__init__.py:137` `def _lingtai_update(self, args: dict) -> dict:` | ❌ **Wrong line.** L112 is inside `_VALID_ACTIONS` dict. `_lingtai_update()` is at **line 137**. Off by 25 lines. |
| 3 | `core/psyche/__init__.py:120` — `_lingtai_load()` | `core/psyche/__init__.py:145` `def _lingtai_load(self, _args: dict) -> dict:` | ❌ **Wrong line.** L120 is the `_VALID_ACTIONS` block. `_lingtai_load()` is at **line 145**. Off by 25 lines. |
| 4 | `core/psyche/__init__.py:150` — `_pad_edit()` | `core/psyche/__init__.py:175` `def _pad_edit(self, args: dict) -> dict:` | ❌ **Wrong line.** L150 is inside `_lingtai_load()` body. `_pad_edit()` is at **line 175**. Off by 25 lines. |
| 5 | `core/psyche/__init__.py:244` — `_pad_append()` | `core/psyche/__init__.py:269` `def _pad_append(self, args: dict) -> dict:` | ❌ **Wrong line.** L244 is inside `_read_append_content()`. `_pad_append()` is at **line 269**. Off by 25 lines. |
| 6 | `core/psyche/__init__.py:290` — `_pad_load()` | `core/psyche/__init__.py:315` `def _pad_load(self, args: dict) -> dict:` | ❌ **Wrong line.** L290 is inside `_pad_append()` body. `_pad_load()` is at **line 315**. Off by 25 lines. |
| 7 | `core/psyche/__init__.py:317` — delegates to eigen | `core/psyche/__init__.py:342` `def _context_molt(self, args: dict) -> dict:` | ❌ **Wrong line.** L317 is inside `_pad_load()` body. `_context_molt()` is at **line 342**. Off by 25 lines. |
| 8 | `intrinsics/eigen.py:124` — `_context_molt()` | `eigen.py:124` `def _context_molt(agent, args: dict) -> dict:` | ✅ Exact |
| 9 | `core/psyche/__init__.py:334` — registered in `setup()` | `core/psyche/__init__.py:359` — `agent._post_molt_hooks.append(...)` inside `setup()` | ❌ **Wrong line.** L334 is inside `_pad_load()` body. The post-molt hook registration is at **line 359**. Off by 25 lines. |
| 10 | `intrinsics/eigen.py:218` — `context_forget()` | `eigen.py:218` `def context_forget(agent, *, source: str = "warning_ladder", attempts: int = 0) -> dict:` | ✅ Exact |

**Summary: 2/10 ✅ · 0 ⚠️ · 8 ❌**

**Pattern:** Every reference to `core/psyche/__init__.py` is off by exactly 25 lines (each claims ~25 lines too early). This strongly suggests the §Source table was written against an **older version** of the file that was ~25 lines shorter (before the `_APPEND_LIST_PATH`, `_APPEND_TOKEN_LIMIT`, `_resolve_path`, `_read_append_content`, and `_is_text_file` members were added, or before the schema/action validation block grew). The two references to `eigen.py` are correct.

---

## 4. library/paths-resolution/README.md

**Library file:** `lingtai/core/library/__init__.py` (344 lines)

| # | §Source claim | Actual location | Verdict |
|---|---|---|---|
| 1 | `core/library/__init__.py:50` — `_FRONTMATTER_RE` | `__init__.py:50` `_FRONTMATTER_RE = re.compile(...)` | ✅ Exact |
| 2 | `core/library/__init__.py:72` — `_resolve_path()` | `__init__.py:72` `def _resolve_path(p: str, working_dir: Path) -> Path:` | ✅ Exact |
| 3 | `core/library/__init__.py:89` — `_parse_skill_file()` | `__init__.py:89` `def _parse_skill_file(skill_file: Path, label: str) -> tuple[dict \| None, dict \| None]:` | ✅ Exact |
| 4 | `core/library/__init__.py:111` — `_scan_recursive()` | `__init__.py:111` `def _scan_recursive(directory: Path, valid: list[dict], problems: list[dict], prefix: str = "") -> None:` | ✅ Exact |
| 5 | `core/library/__init__.py:182` — `_build_catalog_xml()` | `__init__.py:182` `def _build_catalog_xml(skills: list[dict], lang: str) -> str:` | ✅ Exact |
| 6 | `core/library/__init__.py:205` — `_reconcile()` | `__init__.py:205` `def _reconcile(agent: "BaseAgent", paths: list[str]) -> dict:` | ✅ Exact |
| 7 | `core/library/__init__.py:310` — `setup()` | `__init__.py:310` `def setup(agent: "BaseAgent", paths: list[str] \| None = None, **_ignored) -> None:` | ✅ Exact |
| 8 | `core/library/__init__.py:330` — `handle_library()` | `__init__.py:330` `def handle_library(args: dict) -> dict:` | ✅ Exact |

**Summary: 8/8 ✅ · 0 ⚠️ · 0 ❌**

---

## 5. vision/multimodal/README.md

**Capability file:** `lingtai/capabilities/vision/__init__.py` (135 lines)  
**Service file:** `lingtai/services/vision/__init__.py` (109 lines)

| # | §Source claim | Actual location | Verdict |
|---|---|---|---|
| 1 | `capabilities/vision/__init__.py:53` — `VisionManager` | `__init__.py:53` `class VisionManager:` | ✅ Exact |
| 2 | `capabilities/vision/__init__.py:64` — `handle()` | `__init__.py:64` `def handle(self, args: dict) -> dict:` | ✅ Exact |
| 3 | `capabilities/vision/__init__.py:90` — `setup()` | `__init__.py:90` `def setup(agent: "BaseAgent", ...):` | ✅ Exact |
| 4 | `services/vision/__init__.py:63` — `create_vision_service()` | `__init__.py:63` `def create_vision_service(provider: str, *, api_key: str \| None = None, **kwargs) -> VisionService:` | ✅ Exact |
| 5 | `services/vision/__init__.py:38` — `_MIME_BY_EXT` | `__init__.py:38` `_MIME_BY_EXT: dict[str, str] = {` | ✅ Exact |
| 6 | `services/vision/__init__.py:47` — `_read_image()` | `__init__.py:47` `def _read_image(image_path: str) -> tuple[bytes, str]:` | ✅ Exact |
| 7 | `capabilities/vision/__init__.py:27` — `PROVIDERS` | `__init__.py:27` `PROVIDERS = {` | ✅ Exact |

**Summary: 7/7 ✅ · 0 ⚠️ · 0 ❌**

---

## 6. web_search/fallback/README.md

**Capability file:** `lingtai/capabilities/web_search/__init__.py` (130 lines)  
**Service file:** `lingtai/services/websearch/__init__.py` (116 lines)

| # | §Source claim | Actual location | Verdict |
|---|---|---|---|
| 1 | `capabilities/web_search/__init__.py:41` — `WebSearchManager` | `__init__.py:41` `class WebSearchManager:` | ✅ Exact |
| 2 | `capabilities/web_search/__init__.py:52` — `handle()` | `__init__.py:52` `def handle(self, args: dict) -> dict:` | ✅ Exact |
| 3 | `capabilities/web_search/__init__.py:77` — `setup()` | `__init__.py:77` `def setup(agent: "BaseAgent", ...):` | ✅ Exact |
| 4 | `capabilities/web_search/__init__.py:94-105` — provider mismatch handling | `__init__.py:94-105` — the `if provider not in PROVIDERS["providers"]:` block through `api_key = None` | ✅ Exact |
| 5 | `capabilities/web_search/__init__.py:119-120` — no provider specified | `__init__.py:119-120` — `elif search_service is None and provider is None:` → `search_service = create_search_service("duckduckgo")` | ✅ Exact |
| 6 | `services/websearch/__init__.py:48` — `create_search_service()` | `__init__.py:48` `def create_search_service(provider: str, ...):` | ✅ Exact |
| 7 | `services/websearch/__init__.py:19` — `SearchResult` | `__init__.py:19-24` `@dataclass class SearchResult:` | ✅ Exact |
| 8 | `capabilities/web_search/__init__.py:20` — `PROVIDERS` | `__init__.py:20` `PROVIDERS = {` | ✅ Exact |

**Summary: 8/8 ✅ · 0 ⚠️ · 0 ❌**

---

## Grand Summary

| Leaf | ✅ | ⚠️ | ❌ | Notes |
|------|---|---|---|---|
| psyche/soul-flow | 10 | 0 | 0 | All refs accurate (3 minor ±2 drift in base_agent.py) |
| psyche/inquiry | 5 | 2 | 0 | `.inquiry` block range underestimated |
| psyche/core-memories | 2 | 0 | **8** | All psyche `__init__.py` refs off by +25 lines — stale table |
| library/paths-resolution | 8 | 0 | 0 | All refs exact |
| vision/multimodal | 7 | 0 | 0 | All refs exact |
| web_search/fallback | 8 | 0 | 0 | All refs exact |
| **Total** | **40** | **2** | **8** | |

### Action Items

1. **🔴 psyche/core-memories §Source table needs rewrite.** All 8 references to `core/psyche/__init__.py` are off by exactly 25 lines. The file has grown since the table was written. Corrected references:
   - `:66` → `:91` (PsycheManager)
   - `:112` → `:137` (_lingtai_update)
   - `:120` → `:145` (_lingtai_load)
   - `:150` → `:175` (_pad_edit)
   - `:244` → `:269` (_pad_append)
   - `:290` → `:315` (_pad_load)
   - `:317` → `:342` (_context_molt delegation)
   - `:334` → `:359` (post-molt hooks in setup)

2. **🟡 psyche/inquiry #6:** The `.inquiry` file detection range should be `809-852` (not `807-837`). The current range misses the thread spawn and cleanup logic.

3. **🟢 Optional:** The psyche leaves use bare paths (`intrinsics/soul.py`, `base_agent.py`) without the `lingtai_kernel/` prefix. This is internally consistent but differs from the library/vision/web_search leaves which use `core/...` and `capabilities/...` prefixes (relative to `lingtai/`). Consider adding a note about which package each path belongs to.
