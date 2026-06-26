# lingtai_sdk Public Facade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a public, programmable `lingtai_sdk` facade package exposing typed contracts (options, tools, MCP config, sessions) and thin wrappers that build/construct a native `lingtai.agent.Agent`, inspired by the Anthropic Agent SDK, without changing runtime behavior.

**Architecture:** Purely additive package under `src/lingtai_sdk/`. Small single-purpose modules; dataclasses + protocols for contracts; a native runtime adapter translates `LingTaiOptions` into `lingtai.Agent` kwargs and an `LLMService`. No runtime internals move; `lingtai`/`lingtai_kernel` untouched.

**Tech Stack:** Python 3.11+, dataclasses, `from __future__ import annotations`, `unittest.mock` for tests, pytest.

## Global Constraints

- Python 3.11+, `from __future__ import annotations` in every module.
- Kernel/runtime must NOT import from `lingtai_sdk`; dependency is one-directional (`lingtai_sdk` → `lingtai` → `lingtai_kernel`).
- No secrets in `__repr__` or `to_dict()` output (api_key, MCP headers/env values).
- No deletion/rename of existing `lingtai`/`lingtai_kernel` APIs; no PyPI/script rename.
- Migrations clean: no back-compat shims beyond documented forward-compat placeholders.
- Tests use `MagicMock` for `LLMService` and `tmp_path` for `working_dir`.

---

### Task 1: Package skeleton + tools + mcp + session contracts

**Files:**
- Create: `src/lingtai_sdk/__init__.py`, `src/lingtai_sdk/tools.py`, `src/lingtai_sdk/mcp.py`, `src/lingtai_sdk/session.py`
- Test: `tests/test_sdk_imports.py`, `tests/test_sdk_tools.py`, `tests/test_sdk_mcp.py`

**Interfaces:**
- Produces: `PermissionMode`, `ToolSpec`, `ToolResult`, `builtin_tool_names()`, `BUILTIN_TOOLS`; `MCPServerConfig` + `MCPStdioServerConfig`/`MCPHttpServerConfig`/`MCPSSEServerConfig`/`MCPSdkServerConfig` each with `to_runtime_dict(redact=False)`; `SessionRef`, `SessionStore`, `InMemorySessionStore`.

- [ ] Step 1: Write `tools.py` (PermissionMode constants, ToolSpec/ToolResult dataclasses, `builtin_tool_names()` reading `lingtai.capabilities._BUILTIN`/`_GROUPS`, `BUILTIN_TOOLS` tuple).
- [ ] Step 2: Write `mcp.py` (config dataclasses + `to_runtime_dict`, redacting repr).
- [ ] Step 3: Write `session.py` (SessionRef dataclass, SessionStore Protocol, InMemorySessionStore).
- [ ] Step 4: Write `__init__.py` exporting all public symbols + `__version__`.
- [ ] Step 5: Write tests; run `pytest tests/test_sdk_imports.py tests/test_sdk_tools.py tests/test_sdk_mcp.py -q`; expect PASS.
- [ ] Step 6: Commit.

### Task 2: Options

**Files:**
- Create: `src/lingtai_sdk/options.py`; Modify: `src/lingtai_sdk/__init__.py`
- Test: `tests/test_sdk_options.py`

**Interfaces:**
- Consumes: `MCPServerConfig` from Task 1.
- Produces: `SystemPromptAssets` dataclass; `LingTaiOptions` dataclass with `to_dict(redact=True)`, redacting `__repr__`, `replace(**changes)`, and `cwd` alias handling.

- [ ] Step 1: Write failing test for defaults, cwd alias, redaction, replace.
- [ ] Step 2: Implement `options.py`.
- [ ] Step 3: Export from `__init__.py`.
- [ ] Step 4: Run `pytest tests/test_sdk_options.py -q`; expect PASS.
- [ ] Step 5: Commit.

### Task 3: Runtime adapter + client + query

**Files:**
- Create: `src/lingtai_sdk/runtime.py`, `src/lingtai_sdk/client.py`, `src/lingtai_sdk/query.py`; Modify: `src/lingtai_sdk/__init__.py`
- Test: `tests/test_sdk_runtime.py`, `tests/test_sdk_client.py`, `tests/test_sdk_query.py`

**Interfaces:**
- Consumes: `LingTaiOptions`, `MCPServerConfig`, `ToolSpec`.
- Produces: `build_llm_service(options)`, `options_to_agent_kwargs(options, *, service=None)`; `LingTaiClient(options)` with `build_agent_kwargs()`, `create_agent(*, service=None, connect_mcp=False)`, `tool_inventory()`; `async query(prompt, *, options, service=None)`.

- [ ] Step 1: Write `runtime.py` (pure translation; disable-list derivation from disallowed_tools).
- [ ] Step 2: Write `client.py`.
- [ ] Step 3: Write `query.py` (conservative lifecycle wrapper).
- [ ] Step 4: Export; write tests with MagicMock service + tmp_path; run `pytest tests/test_sdk_runtime.py tests/test_sdk_client.py tests/test_sdk_query.py -q`; expect PASS.
- [ ] Step 5: Commit.

### Task 4: Packaging + anatomy + docs + report

**Files:**
- Modify: `pyproject.toml` (add `lingtai_sdk*` to packages.find include)
- Create: `src/lingtai_sdk/ANATOMY.md`, `reports/sdk-public-facade-20260617/implementation.md`, docs note
- Verify: existing anatomy citations unaffected

- [ ] Step 1: Add `lingtai_sdk*` to `[tool.setuptools.packages.find] include`.
- [ ] Step 2: Verify discovery: `python -c "import lingtai_sdk; print(lingtai_sdk.__all__)"`.
- [ ] Step 3: Write `ANATOMY.md` (6-section template).
- [ ] Step 4: Write PR report under `reports/`.
- [ ] Step 5: Run full `pytest tests/ -q` (or scoped if too slow).
- [ ] Step 6: Commit.

## Self-Review

- Spec coverage: options/client/query/tools/mcp/session/runtime/packaging/anatomy/docs/tests all mapped to Tasks 1–4. ✓
- Placeholders: forward-compat placeholders (sse/sdk/permission_mode) are intentional and documented. ✓
- Type consistency: `to_runtime_dict`, `options_to_agent_kwargs`, `build_agent_kwargs`, `create_agent` names consistent across tasks. ✓
