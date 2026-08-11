"""Missing-finish failures must carry truthful trace-inspection guidance.

Strict semantics are unchanged: a run that ends without calling finish()
while daemon_common MCP is loaded is still a failure. The guidance the
feature adds must (1) appear only for a physically missing completion file,
never for invalid/failed/incomplete completions, (2) name an evidence route
the missing-finish state actually provides — the run directory's physical
result.txt, not the null ``result_path`` field — with the full final text
discoverable there, and (3) exist on every documentation surface the change
touched: the failure message, the daemon oneshot context, the manual, and
the daemon Contract.
"""

import json
from pathlib import Path

import pytest

from lingtai.tools.daemon import DaemonManager
from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

_GUIDANCE = "does not necessarily mean the task failed"
_ROUTE = "the run directory's physical result.txt"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANUAL = _REPO_ROOT / "src/lingtai/tools/daemon/manual/SKILL.md"
_CONTRACT = _REPO_ROOT / "src/lingtai/tools/daemon/CONTRACT.md"


def _normalized(text: str) -> str:
    """Collapse whitespace and strip backticks so one canonical route
    fragment can be asserted across plain-string and Markdown surfaces."""
    return " ".join(text.replace("`", "").split())


def _fresh_run(agent, handle):
    return make_daemon_run_dir(
        agent,
        handle=handle,
        call_parameters={"mcp": [{"name": "daemon_common", "transport": "stdio"}]},
    )


def test_missing_finish_result_discoverable_at_the_advertised_physical_route(
    tmp_path,
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _fresh_run(agent, "em-missing-finish-guidance")
    # Longer than the message's 500-char preview: discovery must not depend
    # on the truncated "Final text:" excerpt.
    final_text = "head " * 60 + "FULL-RESULT-MARKER " + "tail " * 60
    assert len(final_text) > 500

    with pytest.raises(RuntimeError, match="missing completion") as excinfo:
        mgr._require_done_completion(run_dir, final_text)

    message = str(excinfo.value)
    assert _GUIDANCE in message
    assert "inspect the run's trace/result" in message
    assert _ROUTE in _normalized(message)
    # The guidance must name the physical file, not the result_path field —
    # daemon.json/artifacts.json/check all persist result_path=null here.
    assert "result_path" not in message

    state = json.loads(run_dir.daemon_json_path.read_text(encoding="utf-8"))
    assert state["state"] == "failed"
    assert state.get("result_path") is None

    # The full final text is discoverable at the advertised route.
    result_file = run_dir.path / "result.txt"
    assert result_file.is_file()
    assert result_file.read_text(encoding="utf-8") == final_text


_COMPLETION_MATRIX = [
    ("missing-completion", None, True),
    ("invalid-json", "not-json{", False),
    ("non-object-json", '"a bare string"', False),
    ("invalid-status", {"status": "victory"}, False),
    ("run-id-mismatch", {"status": "done", "run_id": "someone-else"}, False),
    ("invalid-field-type", {"status": "done", "summary": 123}, False),
    ("explicit-failed", {"status": "failed", "reason": "blocked"}, False),
    ("explicit-incomplete", {"status": "incomplete", "reason": "later"}, False),
]


@pytest.mark.parametrize(
    ("case", "completion", "expect_guidance"),
    _COMPLETION_MATRIX,
    ids=[case for case, _, _ in _COMPLETION_MATRIX],
)
def test_guidance_only_for_physically_missing_completion(
    tmp_path, case, completion, expect_guidance
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _fresh_run(agent, f"em-{case}")
    if completion is not None:
        payload = completion if isinstance(completion, str) else json.dumps(completion)
        (run_dir.path / "daemon_completion.json").write_text(
            payload, encoding="utf-8"
        )

    with pytest.raises(RuntimeError) as excinfo:
        mgr._require_done_completion(run_dir, "final text")

    message = str(excinfo.value)
    assert (_GUIDANCE in message) is expect_guidance
    assert (_ROUTE in _normalized(message)) is expect_guidance

    state = json.loads(run_dir.daemon_json_path.read_text(encoding="utf-8"))
    assert state["state"] == "failed"


def test_daemon_context_carries_guidance_with_truthful_modal_wording():
    context = DaemonManager._daemon_common_context()
    assert "missing-finish" in context
    assert "not proof of failure" in context
    assert _ROUTE in _normalized(context)
    # The runtime performs no automatic inspection: the context must say the
    # parent *should* inspect, never promise that it *will*.
    assert "should inspect" in context
    assert "will inspect" not in context


def test_manual_and_contract_name_the_physical_result_route():
    manual = _MANUAL.read_text(encoding="utf-8")
    assert "missing-finish failure" in manual
    assert "inspect the run's trace/result" in manual
    assert _ROUTE in _normalized(manual)
    # The old route claim (`check`, `result_path`) advertised a field that is
    # null in the exact missing-finish state; it must not come back.
    assert "(`check`, `result_path`)" not in manual

    contract = _CONTRACT.read_text(encoding="utf-8")
    assert "missing-finish failure is a contract failure" in _normalized(contract)
    assert _ROUTE in _normalized(contract)
