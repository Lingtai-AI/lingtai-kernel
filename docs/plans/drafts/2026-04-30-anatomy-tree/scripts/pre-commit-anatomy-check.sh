#!/bin/bash
# Pre-commit hook: verify anatomy file:line references are not stale.
# Install: cp this to .git/hooks/pre-commit (or symlink)
#          chmod +x .git/hooks/pre-commit
#
# Only runs when .py files under src/ are staged (the lines that may shift).
# Fast: skips entirely if no kernel source changed.

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
ANATOMY_ROOT="$REPO_ROOT/docs/plans/drafts/2026-04-30-anatomy-tree"
SCRIPT="$ANATOMY_ROOT/scripts/verify_references.py"

# Only run if kernel .py source files are staged
if ! git diff --cached --name-only --diff-filter=ACMR | grep -q '^src/.*\.py$'; then
    exit 0
fi

echo "Anatomy ref check: kernel source changed, verifying..."
python3 "$SCRIPT" --kernel-root "$REPO_ROOT" --anatomy-root "$ANATOMY_ROOT/leaves"
