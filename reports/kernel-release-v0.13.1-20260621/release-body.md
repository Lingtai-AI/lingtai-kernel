---
related_files:
- reports/kernel-release-v0.13.1-20260621/commits.md
- reports/kernel-release-v0.13.1-20260621/prs.md
maintenance: |
  Historical record of the v0.13.1 release; content is frozen except for factual corrections — do not rewrite retroactively to reflect later changes.
---

# LingTai kernel v0.13.1

Patch release for the Python runtime/kernel package after the tool-result metadata and summarize-guidance work.

## Highlights

- Refines tool-result runtime guidance metadata and removes the legacy metadata block path.
- Adds packaged summarize guidance/manual routing and mirrors active runtime guidance to `system/guidance.json` on boot/refresh.
- Clarifies Codex cache-affinity docs and Telegram contact wording.
- Strengthens resident procedures guidance so session-journal children use the canonical `<YYYY-MM-DD>-molt-<molt-count>-<slug>` naming form.

## Compatibility notes

- PyPI package version: `0.13.1`.
- No migration-version change.
- `system/guidance.json` is a runtime mirror of packaged guidance; agents may need refresh to see the new resident prompt wording.

## Validation

- `git diff --check` passed.
- `python -m pytest -q` passed: 2523 passed, 4 skipped.
- `python -m build` passed after removing test-generated `__pycache__` directories.
- `python -m twine check dist/*` passed.

## Artifact SHA-256

```text
3a0590cd0e1a5bedb50314f41e192db3ea3ecdcf3a81850e404fa408fd7d4fa1  lingtai-0.13.1-cp312-cp312-macosx_11_0_arm64.whl
052dac67b0eb4efb28910a3abfa5ab4f5026e6827c7d6476fefc8b21e030bdad  lingtai-0.13.1.tar.gz
```
