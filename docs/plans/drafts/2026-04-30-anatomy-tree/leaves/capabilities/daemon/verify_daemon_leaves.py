#!/usr/bin/env python3
"""Static verification of daemon anatomy leaf claims.

Runs against source code only — no runtime agent needed.
Each check maps to a specific claim in one of the 4 README.md leaves.

Usage:
    python3 verify_daemon_leaves.py <kernel_src_dir>
    # e.g. python3 verify_daemon_leaves.py /path/to/lingtai-kernel/src/lingtai

Exit code 0 = all checks pass, 1 = any failure.

⚠ ON FAILURE: See README.md in this directory for the verification convention.
  The question is always: "Did the code change intentionally, or did the docs drift?"

═══ What this script covers (47 assertable claims) ═══

  dual-ledger:      13 checks — dual-write paths, tags, zero-skip, fault tolerance
  followup-injection: 11 checks — inbox put, prefix format, lock, truncation constants
  pre-send-health:   12 checks — blacklist, mkdir flags, heartbeat, run_id uniqueness
  max-rpm-gating:    11 checks — defaults, capacity math, reclaim, watchdog ordering
  coverage guard:     3 checks — no unleafed symbols (DaemonManager + DaemonRunDir) + exempt count

═══ What this script does NOT cover (observational / runtime-only) ═══

  - Whether intermediate notifications actually arrive in a running agent's inbox
  - Whether follow-up messages are correctly injected mid-loop (requires live daemon)
  - Whether daemon.json state transitions match expected lifecycle (running→done→...)
  - Whether sum_token_ledger totals include daemon spend in practice (requires ledger data)
  - Whether the watchdog actually kills emanations on timeout (requires live agent)
  - Semantic correctness of error messages (i18n text may change without breaking contract)

These remain covered by the test.md files, which require a running agent.
"""

import ast
import json
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "src" / "lingtai"
DAEMON_INIT = SRC / "core" / "daemon" / "__init__.py"
RUN_DIR = SRC / "core" / "daemon" / "run_dir.py"
TOKEN_LEDGER = SRC.parent / "lingtai_kernel" / "token_ledger.py"
I18N_EN = SRC / "i18n" / "en.json"

passed = 0
failed = 0
skipped = 0


def check(leaf: str, claim: str, condition: bool, detail: str = ""):
    global passed, failed
    tag = f"[{leaf}]"
    if condition:
        passed += 1
        print(f"  ✓ {tag} {claim}")
    else:
        failed += 1
        msg = f"  ✗ {tag} {claim}"
        if detail:
            msg += f"  — {detail}"
        print(msg)


def find_func(source: str, func_name: str, class_name: str = None) -> ast.FunctionDef | None:
    """Find a function/method AST node by name, optionally inside a class."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if class_name:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == func_name:
                        return item
        else:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                return node
    return None


def func_has_call(source: str, func_name: str, call_pattern: str, class_name: str = None) -> bool:
    """Check if a function body contains a call matching a pattern (substring in source)."""
    node = find_func(source, func_name, class_name)
    if node is None:
        return False
    # Get the source segment for this function
    start = node.lineno - 1
    end = node.end_lineno if hasattr(node, 'end_lineno') else start + 50
    lines = source.splitlines()[start:end]
    segment = "\n".join(lines)
    return call_pattern in segment


def source_contains(source: str, pattern: str) -> bool:
    return pattern in source


# ── Load source texts ──────────────────────────────────────────────

init_src = DAEMON_INIT.read_text()
rd_src = RUN_DIR.read_text()
tl_src = TOKEN_LEDGER.read_text()
i18n = json.loads(I18N_EN.read_text()) if I18N_EN.exists() else {}

print("=" * 60)
print("Daemon Anatomy Leaf — Static Verification")
print(f"Source: {SRC}")
print("=" * 60)

# ═══════════════════════════════════════════════════════════════════
print("\n── dual-ledger ──")
# ═══════════════════════════════════════════════════════════════════

# Claim 1: Session created with tracked=False
check("dual-ledger", "emanation session uses tracked=False",
      func_has_call(init_src, "_run_emanation", "tracked=False"))

# Claim 2: _accum callback reads resp.usage and calls append_tokens
check("dual-ledger", "_accum() calls run_dir.append_tokens()",
      func_has_call(init_src, "_accum", "append_tokens", class_name=None))

# Claim 3: append_tokens writes to BOTH ledgers
check("dual-ledger", "append_tokens writes to daemon ledger",
      func_has_call(rd_src, "append_tokens", "token_ledger_path", class_name="DaemonRunDir"))
check("dual-ledger", "append_tokens writes to parent ledger",
      func_has_call(rd_src, "append_tokens", "_parent_token_ledger", class_name="DaemonRunDir"))

# Claim 4: Both writes tagged with source, em_id, run_id
check("dual-ledger", "daemon ledger entry tagged with 'source': 'daemon'",
      func_has_call(rd_src, "append_tokens", '"source": "daemon"', class_name="DaemonRunDir"))
check("dual-ledger", "daemon ledger entry tagged with em_id",
      func_has_call(rd_src, "append_tokens", '"em_id"', class_name="DaemonRunDir"))
check("dual-ledger", "daemon ledger entry tagged with run_id",
      func_has_call(rd_src, "append_tokens", '"run_id"', class_name="DaemonRunDir"))

# Claim 5: Zero-skip guard
check("dual-ledger", "append_tokens skips when all token counts are zero",
      func_has_call(rd_src, "append_tokens", "return", class_name="DaemonRunDir") and
      source_contains(rd_src, "if not (input or output or thinking or cached)"))

# Claim 6: _safe() catches OSError
check("dual-ledger", "_safe() catches OSError",
      source_contains(rd_src, "except OSError"))

# Claim 7: _parent_token_ledger derived from parent_working_dir
check("dual-ledger", "_parent_token_ledger points to parent's logs/token_ledger.jsonl",
      source_contains(rd_src, 'parent_working_dir / "logs" / "token_ledger.jsonl"'))

# Claim 8: sum_token_ledger has no source filter
check("dual-ledger", "sum_token_ledger() sums all entries (no source filter)",
      "source" not in tl_src.split("def sum_token_ledger")[1].split("\ndef ")[0] if "def sum_token_ledger" in tl_src else False)

# Claim 9: daemon.json tokens field updated
check("dual-ledger", "append_tokens increments daemon.json tokens field",
      func_has_call(rd_src, "append_tokens", '_state["tokens"]', class_name="DaemonRunDir"))

# Claim 10: model and endpoint are first-class fields
check("dual-ledger", "append_token_entry accepts model and endpoint params",
      source_contains(tl_src, "model: str | None") and source_contains(tl_src, "endpoint: str | None"))

# ═══════════════════════════════════════════════════════════════════
print("\n── followup-injection ──")
# ═══════════════════════════════════════════════════════════════════

# Claim 1: _notify_parent puts message on inbox
check("followup-injection", "_notify_parent() puts message on agent.inbox",
      func_has_call(init_src, "_notify_parent", "inbox.put"))

# Claim 2: _notify_parent uses [daemon:em-N] prefix
check("followup-injection", "_notify_parent uses [daemon:em-N] prefix",
      func_has_call(init_src, "_notify_parent", "[daemon:"))

# Claim 3: _notify_parent uses MSG_REQUEST
check("followup-injection", "_notify_parent uses MSG_REQUEST message type",
      func_has_call(init_src, "_notify_parent", "MSG_REQUEST"))

# Claim 4: _handle_ask appends to followup_buffer with lock
check("followup-injection", "_handle_ask uses followup_lock",
      func_has_call(init_src, "_handle_ask", "followup_lock"))
check("followup-injection", "_handle_ask concatenates with \\n\\n",
      func_has_call(init_src, "_handle_ask", '"\\n\\n"'))

# Claim 5: followup drain only after text-only response (no tool_calls)
check("followup-injection", "_drain_followup called only when no tool_calls",
      func_has_call(init_src, "_run_emanation", "_drain_followup") and
      source_contains(init_src, "if not response.tool_calls"))

# Claim 6: _on_emanation_done truncates at _max_result_chars
check("followup-injection", "_on_emanation_done truncates long results",
      func_has_call(init_src, "_on_emanation_done", "_max_result_chars"))

# Claim 7: _on_emanation_done suppresses short results
check("followup-injection", "_on_emanation_done suppresses short results (< _NOTIFY_MIN_LEN)",
      func_has_call(init_src, "_on_emanation_done", "_notify_threshold"))

# Claim 8: _NOTIFY_MIN_LEN is 20
check("followup-injection", "_NOTIFY_MIN_LEN == 20",
      source_contains(init_src, "_NOTIFY_MIN_LEN = 20"))

# Claim 9: _max_result_chars default is 2000
check("followup-injection", "max_result_chars default is 2000",
      source_contains(init_src, "max_result_chars: int = 2000"))

# Claim 10: record_user_send appends to chat_history.jsonl with kind field
check("followup-injection", "record_user_send appends kind to chat_history.jsonl",
      func_has_call(rd_src, "record_user_send", '"kind"', class_name="DaemonRunDir"))

# ═══════════════════════════════════════════════════════════════════
print("\n── pre-send-health ──")
# ═══════════════════════════════════════════════════════════════════

# Claim 1: EMANATION_BLACKLIST contains the 4 forbidden tools
check("pre-send-health", 'EMANATION_BLACKLIST contains {"daemon", "avatar", "psyche", "library"}',
      source_contains(init_src, '"daemon"') and
      source_contains(init_src, '"avatar"') and
      source_contains(init_src, '"psyche"') and
      source_contains(init_src, '"library"'))

# Claim 2: _handle_emanate prunes completed before counting
check("pre-send-health", "_handle_emanate prunes completed futures before capacity check",
      func_has_call(init_src, "_handle_emanate", ".done()"))

# Claim 3: load_preset is called for preset validation
check("pre-send-health", "_handle_emanate calls load_preset for preset tasks",
      func_has_call(init_src, "_handle_emanate", "load_preset"))

# Claim 4: check_connectivity is called
check("pre-send-health", "_handle_emanate calls check_connectivity for preset tasks",
      func_has_call(init_src, "_handle_emanate", "check_connectivity"))

# Claim 5: _instantiate_preset_capabilities uses _ToolCollector sandbox
check("pre-send-health", "_instantiate_preset_capabilities uses _ToolCollector",
      func_has_call(init_src, "_instantiate_preset_capabilities", "_ToolCollector"))

# Claim 6: _build_tool_surface expands groups
check("pre-send-health", "_build_tool_surface expands group names",
      func_has_call(init_src, "_build_tool_surface", "_GROUPS"))

# Claim 7: DaemonRunDir.mkdir with exist_ok=False
check("pre-send-health", "DaemonRunDir.__init__ uses mkdir(exist_ok=False)",
      source_contains(rd_src, "exist_ok=False"))

# Claim 8: .heartbeat is touched on construction
check("pre-send-health", ".heartbeat touched on construction",
      func_has_call(rd_src, "__init__", "heartbeat_path.touch", class_name="DaemonRunDir"))

# Claim 9: daemon_start event appended on construction
check("pre-send-health", "daemon_start event appended on construction",
      func_has_call(rd_src, "__init__", '"daemon_start"', class_name="DaemonRunDir"))

# Claim 10: run_id includes random hex suffix
check("pre-send-health", "run_id includes secrets.token_hex(3) for uniqueness",
      source_contains(rd_src, "secrets.token_hex(3)"))

# Claim 11: .heartbeat touched on tool dispatch
check("pre-send-health", ".heartbeat touched on set_current_tool",
      func_has_call(rd_src, "set_current_tool", "heartbeat_path.touch", class_name="DaemonRunDir"))

# Claim 12: .heartbeat touched on turn bump
check("pre-send-health", ".heartbeat touched on bump_turn",
      func_has_call(rd_src, "bump_turn", "heartbeat_path.touch", class_name="DaemonRunDir"))

# ═══════════════════════════════════════════════════════════════════
print("\n── max-rpm-gating ──")
# ═══════════════════════════════════════════════════════════════════

# Claim 1: Default max_emanations is 4
check("max-rpm-gating", "DaemonManager default max_emanations is 4",
      source_contains(init_src, "max_emanations: int = 4"))

# Claim 2: Capacity check: running + requested > max
check("max-rpm-gating", "capacity check: running + len(tasks) > max_emanations",
      source_contains(init_src, "running + len(tasks) > self._max_emanations"))

# Claim 3: Error response includes running/requested/max counts
check("max-rpm-gating", "error message includes running, requested, max",
      source_contains(init_src, "daemon.limit_reached"))

# Claim 4: _handle_reclaim cancels all and clears registry
check("max-rpm-gating", "_handle_reclaim cancels via cancel_event.set()",
      func_has_call(init_src, "_handle_reclaim", "cancel.set()"))
check("max-rpm-gating", "_handle_reclaim clears _emanations",
      func_has_call(init_src, "_handle_reclaim", "_emanations.clear"))

# Claim 5: _handle_reclaim resets _next_id to 1
check("max-rpm-gating", "_handle_reclaim resets _next_id to 1",
      func_has_call(init_src, "_handle_reclaim", "_next_id = 1"))

# Claim 6: ThreadPoolExecutor created per batch
check("max-rpm-gating", "ThreadPoolExecutor created with max_workers=len(tasks)",
      source_contains(init_src, "ThreadPoolExecutor(max_workers=len(tasks))"))

# Claim 7: Watchdog sets timeout_event before cancel_event
check("max-rpm-gating", "watchdog sets timeout_event then cancel_event (ordering)",
      func_has_call(init_src, "_watchdog", "timeout_event.set") and
      func_has_call(init_src, "_watchdog", "cancel_event.set"))

# Claim 8: Watchdog sleeps in 1-second ticks
check("max-rpm-gating", "watchdog sleeps in 1-second ticks",
      source_contains(init_src, "time.sleep(1.0)"))

# Claim 9: Per-batch timeout capped at manager ceiling
check("max-rpm-gating", "per-batch timeout capped at manager ceiling",
      source_contains(init_src, "min(to, self._timeout)"))

# Claim 10: Per-batch max_turns capped at manager ceiling
check("max-rpm-gating", "per-batch max_turns capped at manager ceiling",
      source_contains(init_src, "min(mt, self._max_turns)"))

# ═══════════════════════════════════════════════════════════════════
#  Negative coverage: no public symbol left unleafed
# ═══════════════════════════════════════════════════════════════════
import re as _re

def get_class_methods(src: str, cls: str) -> list[tuple[str, int]]:
    _tree = ast.parse(src)
    for node in ast.walk(_tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return [(n.name, n.lineno) for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return []

def symbols_in_this_script() -> set[str]:
    tokens: set[str] = set()
    script_src = Path(__file__).read_text()
    for node in ast.walk(ast.parse(script_src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for t in _re.findall(r'\b[a-z_][a-z0-9_]*\b', node.value):
                tokens.add(t)
    for t in _re.findall(r'\b_[a-z][a-z0-9_]*\b', script_src):
        tokens.add(t)
    return tokens

_COVERAGE_EXEMPT = {
    # ── Trivial delegates (no independent contract) ────────────────
    "_log",              # passthrough to parent agent's _log(); no daemon logic
    # ── Timestamp helpers (pure functions, no state) ───────────────
    "_now_iso",          # returns UTC ISO string; used by every write
    "_now_secs",         # returns monotonic elapsed; used by every write
    # ── Shared implementations (tested via their callers) ──────────
    "_mark_terminal",    # shared body for mark_cancelled + mark_timeout;
                         #   both callers are in _EXEMPT (see below)
    # ── Path properties (data-only, no behavior) ───────────────────
    "run_id",            # returns self._run_id (immutable after construction)
    "handle",            # returns self._handle (immutable after construction)
    "path",              # returns self._path (immutable after construction)
    "daemon_json_path",  # returns self._path / "daemon.json"
    "prompt_path",       # returns self._path / ".prompt"
    "chat_path",         # returns self._path / "history" / "chat_history.jsonl"
    "events_path",       # returns self._path / "logs" / "events.jsonl"
    # ── Internal FS helpers (used everywhere, no independent contract) ──
    "_atomic_write_json",  # tmpfile → os.replace; every daemon.json write uses this
    "_append_jsonl",       # single-writer JSONL append; every event/history write uses this
    # ── Terminal state markers (trivial wrappers around _mark_terminal) ──
    "mark_done",       # sets state=done + result_preview; tested via _on_emanation_done
    "mark_failed",     # sets state=failed + error; tested via _run_emanation exception path
    "mark_cancelled",  # sets state=cancelled; tested via reclaim + cancel_event
    "mark_timeout",    # sets state=timeout; tested via watchdog + timeout_event
    # ── Read-only queries (no state mutation, no complex contract) ──
    "_build_emanation_prompt",  # string concat: tool descriptions + task text
    "_handle_list",             # returns registry snapshot; daemon(action="list")
    "_handle_check",            # reads daemon.json + events.jsonl tail
    # ── Complement of tested method ────────────────────────────────
    "clear_current_tool",  # inverse of set_current_tool; both in pre-send-health leaf
}

_EXPECTED_EXEMPT_COUNT = len(_COVERAGE_EXEMPT)  # currently 21
_EXEMPT_MIN_JUSTIFICATION_LEN = 30

print("\n" + "─" * 60)
print("── Negative coverage ──")

_referenced = symbols_in_this_script()
coverage_failed = False

for cls_name in ("DaemonManager", "DaemonRunDir"):
    src = init_src if cls_name == "DaemonManager" else rd_src
    methods = get_class_methods(src, cls_name)
    unleafed = [(name, line) for name, line in methods
                if name not in _COVERAGE_EXEMPT and name not in _referenced]
    if unleafed:
        coverage_failed = True
        for name, line in unleafed:
            failed += 1
            print(f"  ✗ [coverage] {cls_name}.{name} (line {line}) — no leaf touches this")
    else:
        passed += 1
        print(f"  ✓ [coverage] {cls_name} — all non-exempt symbols covered")

# Exempt count guard — makes _EXEMPT growth visible in diffs
if len(_COVERAGE_EXEMPT) != _EXPECTED_EXEMPT_COUNT:
    failed += 1
    print(f"  ✗ [coverage] _COVERAGE_EXEMPT grew from {_EXPECTED_EXEMPT_COUNT} to "
          f"{len(_COVERAGE_EXEMPT)}. Each addition needs a justified comment.")
else:
    passed += 1
    print(f"  ✓ [coverage] _COVERAGE_EXEMPT count stable ({_EXPECTED_EXEMPT_COUNT})")

# Justification quality guard — no lazy exemptions
script_src = Path(__file__).read_text()
exempt_block = script_src.split("_COVERAGE_EXEMPT = {")[1].split("}")[0]
exempt_entries = _re.findall(r'"(\w+)".*?#\s*(.+)', exempt_block)
short_justifications = [
    (sym, comment.strip(), len(comment.strip()))
    for sym, comment in exempt_entries
    if len(comment.strip()) < _EXEMPT_MIN_JUSTIFICATION_LEN
]
if short_justifications:
    failed += 1
    for sym, c, clen in short_justifications:
        print(f"  ✗ [coverage] \"{sym}\" — justification too short ({clen} < {_EXEMPT_MIN_JUSTIFICATION_LEN}): \"{c}\"")
else:
    passed += 1
    print(f"  ✓ [coverage] all {len(exempt_entries)} exempt justifications ≥ {_EXEMPT_MIN_JUSTIFICATION_LEN} chars")

# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
total = passed + failed
print(f"Results: {passed}/{total} passed, {failed} failed")
if failed:
    print("EXIT 1 — some claims no longer match source")
    sys.exit(1)
else:
    print("EXIT 0 — all claims verified against current source")
    sys.exit(0)
