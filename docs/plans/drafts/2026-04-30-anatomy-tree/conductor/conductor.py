#!/usr/bin/env python3
"""
conductor.py — Anatomy Tree Test Conductor (v0 skeleton)

Orchestrates agent-driven acceptance tests across an anatomy tree.
Discovers all test.md scenarios, spawns one shallow avatar per scenario,
waits for results, and writes a rollup INDEX.md.

This is a *design skeleton* showing the intended logic and LingTai API
usage. Helper functions are stubbed with docstrings explaining what
they do. The real implementation will live inside a LingTai conductor
agent (not a standalone Python script) — this file documents the
protocol and data flow so the agent can be built from it.

The conductor agent itself is a LingTai agent with these capabilities:
  - glob, read, write  (file operations)
  - avatar             (spawn shallow avatars)
  - email              (inter-agent comms for escalation)
  - system             (show, cleanup)

See init.json for the agent's configuration template.
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

ANATOMY_ROOT = Path(".")          # Root of the anatomy tree (set at runtime)
CONCURRENCY_CAP = 8               # Max parallel avatars
DEFAULT_TIMEOUT_SEC = 300          # 5 minutes per scenario
POLL_INTERVAL_SEC = 15             # How often to check for test-result.md
RESULTS_DIR = "test-results"       # Subdirectory under conductor workdir


# ---------------------------------------------------------------------------
# §1 Discovery — glob for all test.md files
# ---------------------------------------------------------------------------

def discover_scenarios(anatomy_root: Path) -> list[dict]:
    """
    Find all test.md files under the anatomy root.

    Returns a list of scenario descriptors:
        [
            {
                "path": Path("mail-protocol/send/self-send/test.md"),
                "leaf_dir": Path("mail-protocol/send/self-send"),
                "leaf_id": "mail-protocol/send/self-send",
                "timeout": 300,           # seconds, from frontmatter or default
                "reasoning": "...",       # the full test.md content as prompt
            },
            ...
        ]

    Implementation note (for the LingTai agent):
        Use glob({pattern: "**/test.md", path: "<anatomy_root>"})
        Then for each match, use read() to parse frontmatter and content.
    """
    scenarios = []
    # Agent calls: glob(pattern="**/test.md", path=str(anatomy_root))
    # For each returned path:
    #   agent calls: read(file_path=<path>)
    #   frontmatter = parse_test_frontmatter(content)  # ← picks up timeout from YAML
    #   The full markdown body becomes the avatar's reasoning prompt.
    return scenarios


def parse_test_frontmatter(content: str) -> dict:
    """
    Extract YAML frontmatter from test.md content.

    Expected frontmatter fields:
        timeout: 300          # seconds (optional, defaults to DEFAULT_TIMEOUT_SEC)
        concurrency_hint: 1   # how many avatars this scenario ideally needs (optional)
        dependencies: []      # leaf paths that must pass before this one runs (optional)

    Returns dict of parsed fields (empty dict if no frontmatter).
    Uses plain regex — no PyYAML dependency needed for simple key:value fields.
    """
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).strip().splitlines():
        if ':' in line:
            key, val = line.split(':', 1)
            key, val = key.strip(), val.strip()
            if val.isdigit():
                val = int(val)
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# §2 Spawn — fan out shallow avatars, one per scenario
# ---------------------------------------------------------------------------

def spawn_avatar_for_scenario(scenario: dict, conductor_addr: str) -> dict:
    """
    Spawn a shallow avatar to run one test scenario.

    The avatar receives:
        - name: derived from leaf path (e.g. "test-mail-send-selfsend")
        - type: "shallow" (white-paper state, inherits only init.json)
        - reasoning: the full test.md content as its initial prompt,
          prepended with a framing instruction:

              "You are an anatomy test runner. Your task is to execute
               the scenario below, verify the pass criteria, and write
               your result to test-result.md in your workdir.

               <full test.md content>

               When done, write test-result.md using the standard
               template, then go idle."

    LingTai API call (inside the conductor agent):
        avatar(
            action="spawn",
            name="test-mail-send-selfsend",
            type="shallow",
            reasoning="<framing + test.md body>"
        )

    Returns:
        {
            "avatar_name": "test-mail-send-selfsend",
            "avatar_addr": ".lingtai/test-mail-send-selfsend",  # workdir
            "scenario_leaf": "mail-protocol/send/self-send",
            "started_at": "2026-04-30T...",
            "timeout": 300,
        }
    """
    # Sanitize leaf path into a safe avatar name
    leaf_id = scenario["leaf_id"]
    avatar_name = "test-" + leaf_id.replace("/", "-")

    # Build the avatar's initial prompt
    framing = (
        "You are an anatomy test runner for the LingTai kernel. "
        "Your task is to execute the scenario below, verify the pass "
        "criteria against the real system behavior, and write your "
        "result to `test-result.md` in your working directory.\n\n"
        "When you have finished, write test-result.md using the "
        "standard template (see below) and then go idle.\n\n"
        "## Standard test-result.md template\n\n"
        "```\n"
        "# Scenario: <leaf path>\n"
        "**Status:** PASS | FAIL | INCONCLUSIVE\n"
        "**Anatomy ref:** <path to sibling README.md>\n"
        "**Run:** <ISO timestamp>\n"
        "**Avatar:** <your agent_id>\n\n"
        "## Steps taken\n"
        "1. ...\n\n"
        "## Expected (per anatomy)\n"
        "<quote from sibling README.md>\n\n"
        "## Observed\n"
        "<verbatim tool outputs>\n\n"
        "## Verdict reasoning\n"
        "<why PASS, FAIL, or INCONCLUSIVE>\n\n"
        "## Artifacts\n"
        "- <paths to relevant files>\n"
        "```\n\n"
        "---\n\n"
        f"## Scenario: {leaf_id}\n\n"
        f"{scenario['reasoning']}"
    )

    # --- LingTai API call ---
    # avatar(
    #     action="spawn",
    #     name=avatar_name,
    #     type="shallow",
    #     reasoning=framing
    # )
    #
    # The avatar is spawned as a new independent process.
    # Its working directory is: .lingtai/<avatar_name>/
    # The conductor tracks it by address.

    return {
        "avatar_name": avatar_name,
        "avatar_addr": avatar_name,  # .lingtai/<name>/ resolved at runtime
        "scenario_leaf": leaf_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "timeout": scenario.get("timeout", DEFAULT_TIMEOUT_SEC),
    }


def spawn_batch(scenarios: list[dict], conductor_addr: str) -> list[dict]:
    """
    Spawn avatars for a batch of scenarios, respecting concurrency cap.

    Returns the full list of spawned avatar descriptors (some may be
    queued and spawned later as earlier ones finish).

    Concurrency strategy:
        - Simple v0: spawn all at once up to CONCURRENCY_CAP.
        - If there are more scenarios than the cap, spawn the first
          batch, then spawn replacements as each finishes (tracked
          in the wait loop).

    For the LingTai agent:
        - Batch multiple avatar() calls in one turn (independent calls).
        - The agent's own concurrency is bounded by the kernel's
          avatar spawn limits.
    """
    spawned = []
    for scenario in scenarios[:CONCURRENCY_CAP]:
        avatar_info = spawn_avatar_for_scenario(scenario, conductor_addr)
        spawned.append(avatar_info)
    return spawned


# ---------------------------------------------------------------------------
# §3 Wait — poll for test-result.md, enforce timeout
# ---------------------------------------------------------------------------

def check_avatar_result(avatar_info: dict) -> Optional[dict]:
    """
    Check whether an avatar has produced its test-result.md.

    LingTai API calls:
        - read(file_path=".lingtai/<avatar_name>/test-result.md")

    Returns:
        - None if test-result.md doesn't exist yet (avatar still running)
        - dict with parsed result if it does:
            {
                "status": "PASS" | "FAIL" | "INCONCLUSIVE",
                "avatar_id": "...",
                "content": "...",           # full test-result.md
                "leaf_path": "...",
            }
    """
    # Agent calls:
    #   read(file_path=f".lingtai/{avatar_info['avatar_name']}/test-result.md")
    # If FileNotFoundError or file doesn't exist → return None
    # If exists → parse status line and return
    return None


def parse_result_status(content: str) -> str:
    """
    Extract the Status line from test-result.md.
    Expected format: **Status:** PASS | FAIL | INCONCLUSIVE
    """
    match = re.search(r"\*\*Status:\*\*\s*(PASS|FAIL|INCONCLUSIVE)", content)
    if match:
        return match.group(1)
    return "INCONCLUSIVE"  # Unparseable = inconclusive


def is_avatar_alive(avatar_info: dict) -> bool:
    """
    Check if the avatar process is still running.

    LingTau API: system(action="show") on the avatar's address
    would confirm, but the conductor agent can't call system() on
    another agent directly without karma.

    Simple v0 heuristic: check if the avatar's workdir exists and
    if any recent activity (file mtime) is within the timeout window.

    Better v1: use email — send a probe mail, check for bounce.
    """
    return True  # Stub


def wait_for_all(
    spawned: list[dict],
    poll_interval: float = POLL_INTERVAL_SEC,
) -> list[dict]:
    """
    Poll all spawned avatars until each produces test-result.md
    or times out.

    Returns list of results:
        [
            {
                "avatar_name": "...",
                "scenario_leaf": "...",
                "status": "PASS" | "FAIL" | "INCONCLUSIVE" | "TIMEOUT",
                "content": "...",           # test-result.md content, or None
                "wall_time": 12.5,          # seconds
            },
            ...
        ]

    Polling logic (for the conductor agent):
        1. Start a loop that iterates until all avatars have results.
        2. Each iteration:
           a. For each pending avatar, call check_avatar_result().
           b. If result found → mark done.
           c. If elapsed > timeout → mark TIMEOUT.
        3. Sleep poll_interval between iterations.
        4. Return when all are done or timed out.

    For the LingTai agent, this is implemented as a turn loop:
        - Check results → some found → record them
        - Still pending? → system(nap, seconds=poll_interval) or idle
        - Wake up → check again
        - Or: use email — avatars mail the conductor when done
    """
    results = []
    start_times = {a["avatar_name"]: time.time() for a in spawned}

    # Stub: in reality this is an async polling loop in the agent.
    # The agent uses system(nap) or idle+soul-flow between polls.
    for avatar_info in spawned:
        results.append({
            "avatar_name": avatar_info["avatar_name"],
            "scenario_leaf": avatar_info["scenario_leaf"],
            "status": "TIMEOUT",
            "content": None,
            "wall_time": 0,
        })

    return results


# ---------------------------------------------------------------------------
# §4 Aggregate — read all results, write rollup INDEX.md
# ---------------------------------------------------------------------------

def write_rollup(
    results: list[dict],
    conductor_workdir: Path,
    anatomy_version: str = "unknown",
    wall_time: float = 0,
) -> Path:
    """
    Write the rollup INDEX.md from all scenario results.

    Output path: <conductor_workdir>/test-results/<timestamp>/INDEX.md

    The INDEX.md shape follows the design doc spec:

        # Test run <timestamp>
        **Total:** N scenarios — X PASS, Y FAIL, Z INCONCLUSIVE
        **Wall time:** <seconds>
        **Anatomy version:** <SKILL.md version>

        ## Failures
        - [leaf-path](relative/path/to/test-result.md) — reason

        ## Inconclusive
        - [leaf-path](relative/path/to/test-result.md) — reason

        ## Passes
        - leaf-path-1
        - leaf-path-2

    For the LingTai agent:
        write(file_path=<path>, content=<generated markdown>)
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    results_dir = conductor_workdir / RESULTS_DIR / timestamp
    results_dir.mkdir(parents=True, exist_ok=True)

    # Tally
    passes = [r for r in results if r["status"] == "PASS"]
    fails = [r for r in results if r["status"] == "FAIL"]
    inconclusive = [r for r in results if r["status"] == "INCONCLUSIVE"]
    timeouts = [r for r in results if r["status"] == "TIMEOUT"]
    # Timeouts count as inconclusive in the rollup
    all_inconclusive = inconclusive + timeouts

    total = len(results)
    x = len(passes)
    y = len(fails)
    z = len(all_inconclusive)

    # Build INDEX.md content
    lines = [
        f"# Test run {timestamp}",
        "",
        f"**Total:** {total} scenarios — {x} PASS, {y} FAIL, {z} INCONCLUSIVE",
        f"**Wall time:** {wall_time:.1f}s",
        f"**Anatomy version:** {anatomy_version}",
        "",
    ]

    # Failures section
    if fails:
        lines.append("## Failures")
        for r in fails:
            reason = extract_failure_reason(r.get("content", ""))
            result_path = f"../{r['avatar_name']}/test-result.md"
            lines.append(f"- [{r['scenario_leaf']}]({result_path}) — {reason}")
        lines.append("")

    # Inconclusive section (includes timeouts)
    if all_inconclusive:
        lines.append("## Inconclusive")
        for r in all_inconclusive:
            if r["status"] == "TIMEOUT":
                reason = f"timed out after {r.get('wall_time', '?')}s"
            else:
                reason = extract_failure_reason(r.get("content", ""))
            result_path = f"../{r['avatar_name']}/test-result.md"
            lines.append(f"- [{r['scenario_leaf']}]({result_path}) — {reason}")
        lines.append("")

    # Passes section
    if passes:
        lines.append("## Passes")
        for r in passes:
            lines.append(f"- {r['scenario_leaf']}")
        lines.append("")

    index_content = "\n".join(lines)

    # Write INDEX.md
    index_path = results_dir / "INDEX.md"
    # LingTai agent: write(file_path=str(index_path), content=index_content)

    # Also write individual results into this directory for archival
    for r in results:
        if r.get("content"):
            result_file = results_dir / f"{r['avatar_name']}.md"
            # LingTai agent: write(file_path=str(result_file), content=r["content"])

    return index_path


def extract_failure_reason(content: str) -> str:
    """
    Extract a one-line failure reason from test-result.md content.

    Tries to pull from "## Verdict reasoning" section.
    Falls back to first non-empty line of content.
    Returns "no details" if nothing found.
    """
    # Look for Verdict reasoning section
    match = re.search(
        r"## Verdict reasoning\s*\n(.+?)(?:\n##|\Z)",
        content,
        re.DOTALL,
    )
    if match:
        # Take first sentence, truncate to 120 chars
        first_sentence = match.group(1).strip().split(".")[0]
        return first_sentence[:120] if first_sentence else "no details"
    return "no details"


# ---------------------------------------------------------------------------
# §5 Cleanup — optionally archive avatar workdirs
# ---------------------------------------------------------------------------

def cleanup_avatars(
    spawned: list[dict],
    archive: bool = False,
) -> None:
    """
    Clean up avatar workdirs after a test run.

    If archive=True:
        Move each avatar's workdir into the test-results run directory
        under avatars/<avatar_name>/.
    If archive=False (default):
        Leave workdirs intact for debugging.

    LingTai API: no special call needed — these are just filesystem ops.
    The conductor agent can use bash() or write() to move/copy dirs.
    """
    if not archive:
        return  # Leave workdirs for debugging

    # For each avatar:
    #   bash(command=f"mv .lingtai/{avatar_name} <results_dir>/avatars/{avatar_name}")
    pass


# ---------------------------------------------------------------------------
# Main orchestrator flow
# ---------------------------------------------------------------------------

def run_conductor(
    anatomy_root: str = ".",
    concurrency: int = CONCURRENCY_CAP,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    archive: bool = False,
    anatomy_version: str = "unknown",
) -> Path:
    """
    Full conductor run: discover → spawn → wait → aggregate → cleanup.

    This is the function the LingTai conductor agent implements as
    its main workflow. Each step maps to agent tool calls as documented
    in the function docstrings above.

    Returns: path to the generated INDEX.md

    For the LingTai agent, this flow is executed as a sequence of
    tool calls across multiple turns:

        Turn 1: discover (glob) + parse (read each test.md)
        Turn 2: spawn batch 1 (multiple avatar() calls)
        Turn 3+: poll loop (read for results, nap between polls)
        Final turn: aggregate (write INDEX.md) + cleanup
    """
    root = Path(anatomy_root)
    run_start = time.time()

    # Step 1: Discovery
    scenarios = discover_scenarios(root)
    print(f"Discovered {len(scenarios)} scenarios")

    if not scenarios:
        print("No scenarios found. Nothing to do.")
        return Path("")

    # Step 2: Spawn
    # For large numbers of scenarios, we spawn in batches.
    # This skeleton shows the simple "all at once" path.
    spawned = []
    remaining = list(scenarios)

    while remaining:
        batch = remaining[:concurrency]
        remaining = remaining[concurrency:]

        batch_spawned = spawn_batch(batch, conductor_addr="conductor")
        spawned.extend(batch_spawned)

        # Step 3: Wait for this batch
        batch_results = wait_for_all(batch_spawned)

        # If there are more batches, we could overlap here.
        # For v0, we wait for each batch before spawning the next.
        # (Simpler to reason about, still parallel within a batch.)

    # Re-wait for all (in the overlapping case, some may already be done)
    all_results = wait_for_all(spawned)

    run_wall_time = time.time() - run_start

    # Step 4: Aggregate
    conductor_workdir = Path(".")  # The conductor's own workdir
    index_path = write_rollup(
        results=all_results,
        conductor_workdir=conductor_workdir,
        anatomy_version=anatomy_version,
        wall_time=run_wall_time,
    )
    print(f"Rollup written to: {index_path}")

    # Step 5: Cleanup
    cleanup_avatars(spawned, archive=archive)

    return index_path


# ---------------------------------------------------------------------------
# CLI entry point (for development/testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Anatomy Tree Test Conductor"
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Path to the anatomy tree root (default: current dir)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=CONCURRENCY_CAP,
        help=f"Max parallel avatars (default: {CONCURRENCY_CAP})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SEC,
        help=f"Per-scenario timeout in seconds (default: {DEFAULT_TIMEOUT_SEC})",
    )
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Archive avatar workdirs after run (default: keep for debug)",
    )
    parser.add_argument(
        "--version",
        default="unknown",
        help="Anatomy version string to include in rollup",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover scenarios only, don't spawn avatars",
    )

    args = parser.parse_args()

    if args.dry_run:
        scenarios = discover_scenarios(Path(args.root))
        print(f"Would run {len(scenarios)} scenarios:")
        for s in scenarios:
            print(f"  - {s['leaf_id']} (timeout: {s.get('timeout', args.timeout)}s)")
    else:
        run_conductor(
            anatomy_root=args.root,
            concurrency=args.concurrency,
            timeout=args.timeout,
            archive=args.archive,
            anatomy_version=args.version,
        )
