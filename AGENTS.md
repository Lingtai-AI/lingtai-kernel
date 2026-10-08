---
related_files:
- CONTRIBUTING.md
- dev-guide-skill/SKILL.md
- docs/references/claude-code-guide.md
- CLAUDE.md
- ANATOMY.md
- CONTRACT.md
- docs.yaml
maintenance: |
  Tracks the agent-facing definition-of-done checklist for lingtai-kernel (quality research P0-3). Update it whenever the repository's validation gates change: pytest/ruff/mypy configuration in pyproject.toml, the validation layers in dev-guide-skill/SKILL.md, the .github/PULL_REQUEST_TEMPLATE.md checklist, or the CI workflows under .github/workflows.
---

# AGENTS.md

Agent-facing definition of done for the LingTai kernel repository. For humans,
see [`CONTRIBUTING.md`](CONTRIBUTING.md). The mandatory development workflow
lives in the repository-local dev guide skill — read it first:
[`dev-guide-skill/SKILL.md`](dev-guide-skill/SKILL.md). The full coding-agent
reference with deeper test commands and conventions is
[`docs/references/claude-code-guide.md`](docs/references/claude-code-guide.md).
Routing entry points: [`CLAUDE.md`](CLAUDE.md).

## Repository overview

- **What this is**: the LingTai agent kernel — a Python runtime and SDK
  (distribution name `lingtai`) containing the minimal `lingtai.kernel`
  runtime and the batteries-included `lingtai` package (tools, LLM adapters,
  curated MCP servers), shipped as a pure-Python universal wheel plus sdist
  from `pyproject.toml`.
- **Layout**: `src/` (packages), `tests/` (pytest suite), `docs/` (long-form
  references), plus root entry points (`ANATOMY.md`, `CONTRACT.md`).
- **Python**: requires Python >= 3.11; classifiers cover 3.11/3.12/3.13.
- **Docs governance**: every Markdown document must carry `related_files` and
  `maintenance` frontmatter per [`docs.yaml`](docs.yaml), validated by
  `scripts/check_docs_governance.py` and `tests/test_docs_governance.py`.

## Build and test commands

Use the repository virtual environment (README quick start):

```bash
uv venv --python 3.11
uv pip install -e . pytest
.venv/bin/python -m pytest                                  # full suite
.venv/bin/python -m pytest -q -x <touched test files>       # targeted, fail fast
.venv/bin/python -m pytest -q tests/test_architecture_documents.py  # Anatomy/Contract changes
python scripts/check_docs_governance.py --check             # any Markdown change
```

- pytest is configured in `pyproject.toml` (`[tool.pytest.ini_options]`:
  `testpaths = ["tests"]`, `pythonpath = ["src"]`). Always invoke it as
  `python -m pytest` from the repository venv, and inspect every non-zero
  exit code.
- Linting/typing: ruff and mypy are **not configured** in this repository
  today — no `[tool.ruff]`/`[tool.mypy]` in `pyproject.toml`, no standalone
  config, no CI gate. Do not claim or require their output; the enforced
  hygiene gates are `git diff --check` plus the pytest checks above.

## Definition of Done

Before a PR is called done — by the author and by reviewers — all of the
following must hold:

1. **Diff hygiene**: `git diff --check` reports no whitespace errors (repo PR
   gate; also listed in `.github/PULL_REQUEST_TEMPLATE.md`).
2. **Touched tests pass**: `.venv/bin/python -m pytest -q -x <every touched
   test file>` passes, run from the repository venv; never report a
   timed-out or interrupted run as passing.
3. **Documentation/system tests** when the diff touches those systems:
   - Anatomy/Contract graphs: `python -m pytest -q
     tests/test_architecture_documents.py`.
   - Any Markdown change: frontmatter satisfies `docs.yaml`; run
     `python scripts/check_docs_governance.py --check` (and
     `tests/test_docs_governance.py`).
4. **Conventions**:
   - New code uses `pathlib.Path` (or `importlib.resources`) rather than
     `os.path` string manipulation.
   - No debug prints or leftover debugging code (`print()`, `pdb`,
     `breakpoint()`) in the diff.
   - No secrets in logs, screenshots, or examples (PR template note).
5. **Scope**: the staged diff contains only the reviewed change; re-read the
   human scope and name any untested risk (dev guide PR gate).

## Platform notes

- Development happens on **Linux** and **Native Windows** with Python >= 3.11.
- PR CI is Native Windows: `kernel-windows-pr.yml` (windows-latest, Python
  3.12) gates kernel/daemon/avatar paths and `shell-windows-pr.yml` gates
  shell paths. Before calling a PR done, make sure the change is
  Native-Windows-safe (case sensitivity, path separators, line endings;
  CI sets `PYTHONUTF8=1`).
- Release-time packaging (`wheels.yml`) builds the pure-Python universal
  wheel (`py3-none-any`) and sdist; there is no per-platform native build.
- A dedicated macOS CI job is being added separately; do not assume PR-level
  macOS coverage yet.
