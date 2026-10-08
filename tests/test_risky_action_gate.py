"""Tests for the opt-in kernel risky-action gate."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from lingtai.kernel.llm.base import ToolCall
from lingtai.kernel.loop_guard import LoopGuard
from lingtai.kernel.risky_action_gate import (
    build_risky_action_check,
    expire_pending,
    load_gate_config,
    mark_approval,
)
from lingtai.kernel.tool_call_guard import ToolCallGuard, ToolProposal
from lingtai.kernel.tool_executor import ToolExecutor


def _proposal(tool_name: str, args: dict) -> ToolProposal:
    return ToolProposal(tool_name=tool_name, tool_args=args, tool_call_id="tc-1")


def _gate_config(root, **extra):
    """Opt the gate in with an (optionally extended) empty config."""
    security = root / ".security"
    security.mkdir(parents=True, exist_ok=True)
    (security / "gate_config.json").write_text(
        json.dumps(dict(extra)),
        encoding="utf-8",
    )


def _shell(command: str, working_dir=None) -> ToolProposal:
    return _proposal("shell", {"action": "run", "input": {
        "command": command, "working_dir": working_dir,
        "timeout": None, "async": False, "reminder": None,
    }})


def test_gate_is_zero_behavior_change_without_opt_in(tmp_path):
    check = build_risky_action_check(tmp_path)

    decision = check(_shell("rm -f outside.txt"))

    assert decision.allowed
    assert not (tmp_path / ".security").exists()


def test_executor_blocks_before_dispatch_when_gate_denies(tmp_path):
    _gate_config(tmp_path)
    dispatched = []
    executor = ToolExecutor(
        dispatch_fn=lambda tool_call: dispatched.append(tool_call) or {"status": "ok"},
        make_tool_result_fn=lambda name, result, **kwargs: {"name": name, "result": result},
        guard=LoopGuard(max_total_calls=10),
        known_tools={"shell"},
        working_dir=tmp_path,
        tool_call_guard=ToolCallGuard([build_risky_action_check(tmp_path)]),
    )
    results, intercepted, _ = executor.execute([ToolCall(
        name="shell",
        args={"action": "run", "input": {
            "command": "rm -f outside.txt", "working_dir": str(tmp_path),
        }},
        id="blocked-file",
    )])

    assert not intercepted
    assert dispatched == []
    assert results[0]["result"]["error_type"] == "ToolCallGuardDenied"
    assert results[0]["result"]["guard_decision"]["metadata"]["pending_request_id"]


def test_shell_write_is_denied_and_recorded_as_shell_command(tmp_path):
    _gate_config(tmp_path)
    check = build_risky_action_check(tmp_path)

    proposal = _shell("cp exact.txt outside.txt", str(tmp_path))
    decision = check(proposal)

    assert not decision.allowed
    assert decision.check_name == "risky_action_gate"
    request_id = decision.metadata["pending_request_id"]
    request_path = tmp_path / ".security" / "pending" / f"{request_id}.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["status"] == "pending"
    assert payload["approvals"] == {"telegram": None, "wechat": None}
    assert payload["operation"]["tool_name"] == "shell"
    assert payload["operation"]["kind"] == "shell_command"
    assert payload["operation"]["args"] == proposal.tool_args
    assert not (tmp_path / "outside.txt").exists()


def test_local_write_roots_no_longer_grants_anything(tmp_path):
    """``local_write_roots`` is parsed for compatibility but constrains nothing.

    The File family that consulted it is gone. A Shell write under a listed
    root is still classified by the unchanged Shell command classifier, so it
    is denied exactly as it would be without the key.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _gate_config(tmp_path, local_write_roots=[str(allowed)])
    check = build_risky_action_check(tmp_path)

    assert load_gate_config(tmp_path)["local_write_roots"] == [str(allowed)]
    decision = check(_shell(f"cp {allowed / 'a.txt'} {allowed / 'b.txt'}"))
    assert not decision.allowed
    assert "destination-writing" in decision.reason


def test_read_only_shell_passes_without_pending_record(tmp_path):
    _gate_config(tmp_path)
    check = build_risky_action_check(tmp_path)

    shell_decision = check(_shell("printf hello"))

    assert shell_decision.allowed
    assert not list((tmp_path / ".security" / "pending").glob("*.json"))


def test_shell_unknown_command_is_denied_and_exact_command_is_recorded(tmp_path):
    _gate_config(tmp_path)
    check = build_risky_action_check(tmp_path)
    command = "python3 -c 'open(\"outside.txt\", \"w\").write(\"x\")'"
    proposal = _proposal("shell", {"action": "run", "input": {
        "command": command, "working_dir": str(tmp_path),
        "timeout": None, "async": False, "reminder": None,
    }})

    decision = check(proposal)

    assert not decision.allowed
    request_path = tmp_path / ".security" / "pending" / f"{decision.metadata['pending_request_id']}.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["operation"]["command"] == command
    assert payload["operation"]["args"] == proposal.tool_args
    assert not (tmp_path / "outside.txt").exists()


def test_expired_request_is_denied_even_if_approval_arrives_later(tmp_path):
    _gate_config(tmp_path)
    decision = build_risky_action_check(tmp_path)(_proposal("shell", {
        "action": "run", "input": {"command": "rm -f x"}
    }))
    request_path = tmp_path / ".security" / "pending" / f"{decision.metadata['pending_request_id']}.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["created_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    assert expire_pending(request_path)["status"] == "expired"
    assert mark_approval(request_path, "telegram", "approve")["status"] == "expired"


def test_shared_config_grants_are_unioned_and_two_approvals_transition_status(tmp_path):
    agent = tmp_path / "agent"
    shared = tmp_path / "shared"
    (agent / ".security").mkdir(parents=True)
    (shared / ".security").mkdir(parents=True)
    (agent / ".security" / "gate_config.json").write_text("{}", encoding="utf-8")
    (shared / ".security" / "gate_config.json").write_text(
        json.dumps({"local_write_roots": [str(agent / "shared-ok")], "ssh_hosts": ["cluster"]}),
        encoding="utf-8",
    )
    config = load_gate_config(agent)
    assert config["local_write_roots"] == [str(agent / "shared-ok")]
    assert config["ssh_hosts"] == ["cluster"]

    check = build_risky_action_check(agent)
    proposal = _shell("rm -f not-ok.txt", str(agent))
    decision = check(proposal)
    request_path = agent / ".security" / "pending" / f"{decision.metadata['pending_request_id']}.json"
    assert mark_approval(request_path, "telegram", "approve")["status"] == "pending"
    assert mark_approval(request_path, "wechat", "approve")["status"] == "approved"


def test_glued_redirection_is_denied(tmp_path):
    """`printf pwn>/tmp/x` must not be unwrapped into read-only printf."""
    _gate_config(tmp_path)
    decision = build_risky_action_check(tmp_path)(_proposal("shell", {
        "action": "run", "input": {"command": "printf pwn>/tmp/x"}
    }))
    assert not decision.allowed


def test_glued_append_redirection_is_denied(tmp_path):
    _gate_config(tmp_path)
    decision = build_risky_action_check(tmp_path)(_proposal("shell", {
        "action": "run", "input": {"command": "echo hi >>/tmp/x"}
    }))
    assert not decision.allowed


def test_path_form_executable_is_denied(tmp_path):
    """`./ls` must not be trusted as the read-only system ``ls``."""
    _gate_config(tmp_path)
    decision = build_risky_action_check(tmp_path)(_proposal("shell", {
        "action": "run", "input": {"command": "./ls"}
    }))
    assert not decision.allowed


def test_any_path_form_executable_is_denied(tmp_path):
    """Path(tokens[0]).name must not reduce an arbitrary path to a bare verb.

    Regression for Fable batch-A cross-check P0: /tmp/ls, ../ls, bin/ls and
    /usr/bin/printf all reduce to allowlisted basenames and were allowed.
    """
    _gate_config(tmp_path)
    for command in (
        "/tmp/ls",
        "../ls",
        "bin/ls",
        "/usr/bin/printf hello",
        "command /tmp/ls",
        "sudo /tmp/ls",
        "/usr/bin/env PATH=/attacker ls",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Bare allowlisted verbs still pass.
    for command in ("ls", "printf hello", "ls -la"):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_env_path_override_is_denied(tmp_path):
    """`env PATH=/attacker ls` must not unwrap into read-only ls."""
    _gate_config(tmp_path)
    for command in (
        "env PATH=/attacker ls",
        "env LD_PRELOAD=/attacker.so ls",
        "env PYTHONPATH=/attacker python3 -c pass",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command


def test_git_list_only_subcommands_reject_positional_mutation(tmp_path):
    _gate_config(tmp_path)
    for command in ("git branch pwn", "git tag v1", "git remote add x y"):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Bare list/query forms stay allowed (diff/log/show need explicit
    # --no-textconv --no-ext-diff; status needs global --no-optional-locks
    # and -c core.fsmonitor=false to stay read-only).
    for command in ("git branch", "git tag", "git remote -v", "git --no-optional-locks -c core.fsmonitor=false status", "git log --no-textconv --no-ext-diff -1"):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_list_only_option_mutations_are_denied(tmp_path):
    """Option-only git mutations must fail closed.

    Regression for Fable batch-A cross-check P0 #2: `git branch
    --unset-upstream` / `--set-upstream-to=` / `--edit-description` mutate
    branch config yet previously passed because only positional tokens were
    counted. Unknown options on list-only subcommands fail closed; read-only
    query forms stay allowed.
    """
    _gate_config(tmp_path)
    for command in (
        "git branch --unset-upstream",
        "git branch --set-upstream-to=origin/main",
        "git branch --edit-description",
        "git branch -m new-name",
        "git tag -a v1 -m msg",
        "git remote set-url origin http://evil",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    for command in (
        "git branch",
        "git branch --list",
        "git branch --list feature",
        "git branch --show-current",
        "git tag",
        "git tag --list",
        "git remote",
        "git remote -v",
        "git remote get-url origin",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_option_only_mutations_denied_but_git_write_flag_is_denied(tmp_path):
    """Git fast-path must still reject --output/--outfile write destinations.

    Regression for Fable batch-A cross-check P0 #3-A: the git fast-path
    ``continue`` skipped the generic write-flag check, so ``git diff
    --output=/tmp/x`` etc. were allowed despite writing a file.
    """
    _gate_config(tmp_path)
    for command in (
        "git diff --output=/tmp/fable-never-created",
        "git diff --output /tmp/fable-never-created",
        "git show --output=/tmp/fable-never-created HEAD",
        "git log --output=/tmp/fable-never-created -1",
        "git diff --outfile=/tmp/fable-never-created",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Git read-only with explicit helper-disable flags still passes.
    for command in (
        "git diff --no-textconv --no-ext-diff",
        "git show --no-textconv --no-ext-diff HEAD",
        "git log --no-textconv --no-ext-diff -1",
        "git --no-optional-locks -c core.fsmonitor=false status",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_mark_approval_cannot_overwrite_denied_or_expired(tmp_path, monkeypatch):
    """A stale approve must never resurrect a denied/expired request.

    Regression for Fable batch-A cross-check P0 #3-B: mark_approval was a
    lock-free read-modify-atomic-replace, so a concurrent stale ``approved``
    write after ``denied`` could flip the terminal state. Both writers now
    serialize on the request lock and re-read before transition; deny and
    expire are irreversible.
    """
    from lingtai.kernel.risky_action_gate import expire_pending, mark_approval
    import json, shutil, threading

    _gate_config(tmp_path)
    pending_dir = tmp_path / ".security" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    req = pending_dir / "race.json"

    def fresh_payload(created_at: str) -> dict:
        return {
            "status": "pending",
            "created_at": created_at,
            "expires_in_seconds": 3600,
            "operation": "NEVER EXECUTED",
        }

    def deny_and_approve():
        mark_approval(req, "telegram", "deny")
        mark_approval(req, "telegram", "approve")

    def expire_and_approve():
        expire_pending(req)
        mark_approval(req, "telegram", "approve")

    scenarios = (
        # deny must be irreversible: approve afterwards must not flip it.
        ("denied", fresh_payload("2099-01-01T00:00:00+00:00"), deny_and_approve),
        # expired must be irreversible: approve afterwards must not resurrect.
        ("expired", fresh_payload("2020-01-01T00:00:00+00:00"), expire_and_approve),
    )
    for scenario, payload, writer in scenarios:
        req.write_text(json.dumps(payload), encoding="utf-8")
        threads = [threading.Thread(target=writer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        final = json.loads(req.read_text(encoding="utf-8"))
        assert final["status"] == scenario, (scenario, final["status"])
        assert final["operation"] == "NEVER EXECUTED"


def test_read_only_verb_with_write_flag_is_denied(tmp_path):
    _gate_config(tmp_path)
    for command in ("sort -o /tmp/x input", "curl -o /tmp/x https://example.com", "wget -O /tmp/x https://example.com"):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command


def test_read_only_verb_with_joined_write_flag_is_denied(tmp_path):
    """Joined short-option write flags (-oFILE) must be denied too.

    Regression for Fable batch-A cross-check P0: `sort -o/tmp/x input`
    was allowed because only the standalone ``-o`` token was matched.
    """
    _gate_config(tmp_path)
    for command in (
        "sort -o/tmp/fable-never-executed input",
        "sort -o/tmp/x input",
        "wget -O/tmp/x https://example.com",
        "sort --output=/tmp/x input",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command


def test_read_only_verb_option_allowlist_denies_helper_execution(tmp_path):
    """Read-only verbs must be option-aware; unknown/exec options fail closed.

    Regression for Fable batch-A cross-check P0 #4: rg --pre executes an
    arbitrary preprocessor, date <new_date> sets the system clock, and both
    were previously allowed because only the verb name was classified.
    """
    _gate_config(tmp_path)
    for command in (
        "rg --pre /tmp/fable-never-executed needle .",
        "rg --pre-glob '*.rs' /tmp/fable-never-executed needle .",
        "rg --search-zip needle archive.zip",
        "rg -z needle archive.zip",
        "sudo rg --pre /tmp/fable-never-executed needle .",
        "date 010101012030",
        "sudo date 010101012030",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Option-aware read-only forms stay allowed.
    for command in ("rg -n needle .", "rg --ignore-case needle .", "ls -la", "head -n5 file", "sort -k2 file", "cut -d, -f1 file"):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_external_exec_options_are_denied(tmp_path):
    """Git read-only fast-path must not run external diff/textconv helpers.

    Regression for Fable batch-A cross-check P0 #4: --ext-diff, --textconv and
    config overrides (git -c diff.external=..., env GIT_EXTERNAL_DIFF=...) all
    execute external programs yet were allowed because only output flags and
    list-only subcommands were checked.
    """
    _gate_config(tmp_path)
    for command in (
        "git diff --ext-diff",
        "git diff --textconv",
        "git show --ext-diff HEAD",
        "git -c diff.external=/tmp/fable-never-executed diff --ext-diff",
        "git --config diff.external=/tmp/fable-never-executed diff --ext-diff",
        "env GIT_EXTERNAL_DIFF=/tmp/fable-never-executed git diff --ext-diff",
        "env GIT_PAGER=/tmp/fable-never-executed git log -1",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Ordinary read-only git queries stay allowed (with explicit helper-disable flags).
    for command in (
        "git diff --no-textconv --no-ext-diff",
        "git show --no-textconv --no-ext-diff HEAD",
        "git log --no-textconv --no-ext-diff -1",
        "git --no-optional-locks -c core.fsmonitor=false status",
        "git log --no-textconv --no-ext-diff --oneline -5",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_ambient_helper_execution_requires_explicit_disable(tmp_path):
    """Bare git queries must not run ambient textconv/fsmonitor helpers.

    Regression for Fable batch-A cross-check P0 #6: git diff/log run external
    textconv filters by default, and git status consults a core.fsmonitor hook
    from ambient config; GIT_CONFIG_* env could inject core.fsmonitor. Only
    explicit --no-textconv --no-ext-diff / -c core.fsmonitor=false prove no
    external helper can run.
    """
    _gate_config(tmp_path)
    for command in (
        "git diff",
        "git log -1",
        "git show HEAD",
        "git status",
        "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=/tmp/fable-never-executed git status",
        "env GIT_CONFIG_GLOBAL=/tmp/fable-config-never-read git status",
        "env GIT_CONFIG_KEY_0=diff.external git diff",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Explicit helper-disable forms stay allowed.
    for command in (
        "git diff --no-textconv --no-ext-diff",
        "git log --no-textconv --no-ext-diff -1",
        "git --no-optional-locks -c core.fsmonitor=false status",
        "git --no-optional-locks -ccore.fsmonitor=false status",
        "env FOO=bar git --no-optional-locks -c core.fsmonitor=false status",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_multi_c_and_remote_show_helpers_denied(tmp_path):
    """Each git -c must be validated independently; remote show needs -n.

    Regression for Fable batch-A cross-check P0 #7: one safe
    -ccore.fsmonitor=false used to let an unsafe -ccore.fsmonitor=/tmp/helper
    pass (whole-args fsmonitor check); git remote show without -n queries a
    remote transport/helper; --exec-path global was unparsed.
    """
    _gate_config(tmp_path)
    for command in (
        "git -ccore.fsmonitor=false -ccore.fsmonitor=/tmp/fable-never-executed status",
        "git -ccore.fsmonitor=/tmp/fable-never-executed -ccore.fsmonitor=false status",
        "git -c core.fsmonitor=false -c core.fsmonitor=/tmp/x status",
        "git -c diff.external=/tmp/x diff --no-textconv --no-ext-diff",
        "git remote show origin",
        "git --exec-path=/tmp/fable-exec-never-used remote show origin",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Each -c independently safe; remote show -n (cached, no query) allowed.
    for command in (
        "git --no-optional-locks -c core.fsmonitor=false status",
        "git remote show -n origin",
        "git remote get-url origin",
        "git remote -v",
        "git -C /tmp diff --no-textconv --no-ext-diff",
        "git --no-pager log --no-textconv --no-ext-diff -1",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_status_requires_no_optional_locks(tmp_path):
    """git status refreshes/writes .git/index unless optional locks are off.

    Regression for Fable batch-A cross-check P0 #8: git-status(1) by default
    refreshes the index, updating cached stat information and writing out the
    result (taking an index lock) even with core.fsmonitor=false. So status is
    read-only ONLY with an effective global --no-optional-locks in addition to
    core.fsmonitor=false; subcommand-after spellings and any form without the
    global flag fail closed.
    """
    _gate_config(tmp_path)
    for command in (
        "git status",
        "git status -c core.fsmonitor=false",
        "git status -ccore.fsmonitor=false",
        "git status --no-optional-locks",
        "git -c core.fsmonitor=false status",
        "git -ccore.fsmonitor=false status",
        "env FOO=bar git -c core.fsmonitor=false status",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Canonical global form: --no-optional-locks + core.fsmonitor=false before
    # the subcommand. No subcommand-after spelling is relied upon.
    for command in (
        "git --no-optional-locks -c core.fsmonitor=false status",
        "git --no-optional-locks -ccore.fsmonitor=false status",
        "git -c core.fsmonitor=false --no-optional-locks status",
        "env FOO=bar git --no-optional-locks -c core.fsmonitor=false status",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_signature_pretty_atoms_denied(tmp_path):
    """log/show must not run gpg.program via --show-signature or %G*/%g* atoms.

    Regression for Fable batch-A cross-check P0 #9: git-log/show's
    --show-signature hands the signature to gpg --verify and pretty-format
    %GG/%G?/%GS/%GK/%GF/%GP/%GT (and reflog %gD/%gN) ask for the same,
    executing the configurable gpg.program / gpg.<format>.program from ambient
    config. Those forms fail closed; safe pretty atoms (%% %h %s ...) stay
    allowed.
    """
    _gate_config(tmp_path)
    for command in (
        "git log --no-textconv --no-ext-diff --show-signature -1",
        "git show --no-textconv --no-ext-diff --show-signature HEAD",
        "git log --no-textconv --no-ext-diff --format=%GG -1",
        "git log --no-textconv --no-ext-diff --pretty=%G? -1",
        "git log --no-textconv --no-ext-diff --format=%GS -1",
        "git log --no-textconv --no-ext-diff --format=%gD -1",
        "git log --no-textconv --no-ext-diff --pretty=%gN -1",
        "git log --no-textconv --no-ext-diff --format=%h --show-signature -1",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Safe pretty atoms and plain log/show stay allowed.
    for command in (
        "git log --no-textconv --no-ext-diff -1",
        "git show --no-textconv --no-ext-diff HEAD",
        "git log --no-textconv --no-ext-diff --format=%h %s -1",
        "git log --no-textconv --no-ext-diff --format=oneline -1",
        "git log --no-textconv --no-ext-diff --pretty=%% -1",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_positive_query_grammar_denies_unknown_and_remerge(tmp_path):
    """diff/log/show use a positive option grammar; unknown options fail closed.

    Regression for Fable batch-A cross-check P0 #10: --remerge-diff /
    --diff-merges=remerge / --diff-merges=r rerun the merge machinery on
    two-parent merges and can execute repo/user-configured custom merge
    drivers (merge.<driver>.driver). Blacklisting each new option class is not
    audit-proof, so diff/log/show now accept ONLY the allowlisted read-only
    query options; any unknown long/short option fails closed.
    """
    _gate_config(tmp_path)
    for command in (
        "git log --no-textconv --no-ext-diff --remerge-diff -1",
        "git show --no-textconv --no-ext-diff --remerge-diff HEAD",
        "git log --no-textconv --no-ext-diff --diff-merges=remerge -1",
        "git log --no-textconv --no-ext-diff --diff-merges=r -1",
        "git log --no-textconv --no-ext-diff --diff-merges remerge -1",
        "git log --no-textconv --no-ext-diff --output=/tmp/fable-never-written -1",
        "git diff --no-textconv --no-ext-diff --relative-path=x",
        "git log --no-textconv --no-ext-diff -m -1",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Canonical fixed query shapes remain allowed.
    for command in (
        "git log --no-textconv --no-ext-diff -1",
        "git show --no-textconv --no-ext-diff HEAD",
        "git diff --no-textconv --no-ext-diff",
        "git log --no-textconv --no-ext-diff --oneline --stat -5",
        "git log --no-textconv --no-ext-diff --name-only --format=%h -1",
        "git log --no-textconv --no-ext-diff --all --grep=foo -1",
        "git log --no-textconv --no-ext-diff -n 3",
        "git log --no-textconv --no-ext-diff --max-count=2 --diff-filter=AM",
        "git show --no-textconv --no-ext-diff --stat --format=fuller HEAD",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_git_trace_env_sinks_denied(tmp_path):
    """GIT_TRACE* env vars must not write files or open Unix sockets.

    Regression for Fable batch-A cross-check P0 #11: git(1) trace output
    options (GIT_TRACE / GIT_TRACE_* / GIT_TRACE2 / GIT_TRACE2_*) append to
    an arbitrary file path or open a Unix domain socket (af_unix:), so a
    nominally read-only git query could write files or connect to a local
    socket without dual approval. Fail closed by exact key and prefix.
    """
    _gate_config(tmp_path)
    for command in (
        "env GIT_TRACE=/tmp/fable-never-written git log --no-textconv --no-ext-diff -1",
        "env GIT_TRACE_PACKET=/tmp/fable-never-written git log --no-textconv --no-ext-diff -1",
        "env GIT_TRACE2=/tmp/fable-never-written git show --no-textconv --no-ext-diff HEAD",
        "env GIT_TRACE2_EVENT=af_unix:/tmp/fable-never-connected git diff --no-textconv --no-ext-diff",
        "env GIT_TRACE2_PERF=/tmp/fable-never-written git log --no-textconv --no-ext-diff -1",
        "env GIT_TRACE_SHALLOW=/tmp/fable-never-written git log --no-textconv --no-ext-diff -1",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Unrelated env assignments on git queries stay allowed.
    for command in (
        "env FOO=bar git log --no-textconv --no-ext-diff -1",
        "git log --no-textconv --no-ext-diff -1",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_effective_cwd_binds_relative_targets_to_agent_workdir(tmp_path):
    """Omitted/empty cwd and relative script targets resolve to the agent workdir.

    Regression for Fable final P1: the shell executor runs with the agent
    workdir when cwd is omitted, so the gate must resolve trusted scripts
    against the same base instead of the gate process cwd. A relative
    trusted script must match the workdir-relative canonical path.
    """
    approved = tmp_path / "approved"
    approved.mkdir()
    _gate_config(tmp_path, trusted_scripts=[str(approved / "run.py")])
    # Relative trusted script resolves against the workdir, not process cwd.
    decision = build_risky_action_check(tmp_path)(_proposal("shell", {
        "action": "run", "input": {"command": "python approved/run.py", "working_dir": None}
    }))
    assert decision.allowed, "relative trusted script under workdir must be allowed"
    decision = build_risky_action_check(tmp_path)(_proposal("shell", {
        "action": "run", "input": {"command": "python ../run.py", "working_dir": None}
    }))
    assert not decision.allowed


def test_stale_request_lock_is_reclaimed(tmp_path):
    """A crashed holder's lock file must not permanently wedge mutation.

    Regression for Fable final P1 #2: _request_lock now records an owner and
    reclaims a lock older than stale_after_seconds, so a crashed process
    cannot wedge mark_approval/expire_pending forever.
    """
    from lingtai.kernel.risky_action_gate import expire_pending, mark_approval, _request_lock
    import os, time
    request = tmp_path / "req.json"
    request.write_text(json.dumps({
        "id": "x", "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_in_seconds": 3600, "status": "pending",
        "operation": {}, "approvals": {"telegram": None, "wechat": None},
    }), encoding="utf-8")
    lock = tmp_path / "req.json.lock"
    lock.write_text("pid=999999", encoding="utf-8")
    # Make it look stale (older than the default 30s reclaim threshold).
    old = time.time() - 60
    os.utime(lock, (old, old))
    with _request_lock(request, stale_after_seconds=30.0):
        pass
    assert not lock.exists(), "stale lock should have been reclaimed"
    result = mark_approval(request, "telegram", "approve")
    assert result["status"] == "pending"  # second channel still needed
    result = mark_approval(request, "wechat", "approve")
    assert result["status"] == "approved"


def test_path_form_executable_denied_across_all_fast_paths(tmp_path):
    """Path-form executables must be denied before any basename fast-path.

    Regression for Fable batch-A cross-check P0 #5: git/interpreter branches
    previously continued before the generic read-only path-form check, so
    /tmp/git status, ../python <trusted>, and wrapper-wrapped variants all
    passed while /tmp/ls was denied.
    """
    _gate_config(tmp_path)
    for command in (
        "/tmp/git status",
        "../git status",
        "command /tmp/git status",
        "sudo /tmp/git status",
        "/tmp/python -c pass",
        "../python -c pass",
        "command /tmp/python -c pass",
        "sudo /tmp/python -c pass",
        "./git status",
        "usr/bin/git status",
    ):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert not decision.allowed, command
    # Bare basenames stay allowed (interpreter inline code is separately risky).
    for command in ("git --no-optional-locks -c core.fsmonitor=false status", "ls -la"):
        decision = build_risky_action_check(tmp_path)(_proposal("shell", {
            "action": "run", "input": {"command": command}
        }))
        assert decision.allowed, command


def test_bash_compat_name_is_treated_as_shell(tmp_path):
    """A compat ``bash`` tool call must hit the same shell gate as ``shell``."""
    _gate_config(tmp_path)
    decision = build_risky_action_check(tmp_path)(_proposal("bash", {
        "action": "run", "input": {"command": "rm -f x"}
    }))
    assert not decision.allowed


def test_daemon_stub_wires_risky_action_gate(tmp_path):
    """Detached daemon stub must not bypass an opted-in gate."""
    _gate_config(tmp_path)
    from lingtai.kernel.daemon_supervisor.agent_stub import DaemonSupervisorAgentStub
    stub = DaemonSupervisorAgentStub(tmp_path)
    assert stub._tool_call_guard is not None
    decision = stub._tool_call_guard.evaluate(_proposal("shell", {
        "action": "run", "input": {"command": "rm -f x"}
    }))
    assert not decision.allowed


def test_mark_approval_refuses_expired_inline(tmp_path):
    """mark_approval itself must refuse an already-expired pending request."""
    _gate_config(tmp_path)
    decision = build_risky_action_check(tmp_path)(_proposal("shell", {
        "action": "run", "input": {"command": "rm -f x"}
    }))
    request_path = tmp_path / ".security" / "pending" / f"{decision.metadata['pending_request_id']}.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["created_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    result = mark_approval(request_path, "telegram", "approve")
    assert result["status"] == "expired"
    assert request_path.read_text(encoding="utf-8") != ""


def test_env_opt_in_default_closed(tmp_path, monkeypatch):
    """Without config file AND without env switch the gate stays off."""
    monkeypatch.delenv("LINGTAI_RISKY_ACTION_GATE", raising=False)
    check = build_risky_action_check(tmp_path)
    assert check(_proposal("shell", {
        "action": "run", "input": {"command": "rm -f x"}
    })).allowed


def test_env_opt_in_enables_gate_without_config(tmp_path, monkeypatch):
    """LINGTAI_RISKY_ACTION_GATE=1 enables the gate with an empty strict config."""
    monkeypatch.setenv("LINGTAI_RISKY_ACTION_GATE", "1")
    check = build_risky_action_check(tmp_path)
    assert not check(_proposal("shell", {
        "action": "run", "input": {"command": "rm -f x"}
    })).allowed
    # A bare read-only shell command stays allowed under the empty strict config.
    assert check(_proposal("shell", {
        "action": "run", "input": {"command": "ls"}
    })).allowed


def test_env_opt_in_truthy_values(tmp_path, monkeypatch):
    monkeypatch.setenv("LINGTAI_RISKY_ACTION_GATE", "true")
    check = build_risky_action_check(tmp_path)
    assert not check(_proposal("shell", {
        "action": "run", "input": {"command": "rm -f x"}
    })).allowed
    monkeypatch.setenv("LINGTAI_RISKY_ACTION_GATE", "off")
    assert check(_proposal("shell", {
        "action": "run", "input": {"command": "rm -f x"}
    })).allowed
