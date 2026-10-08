"""Opt-in risky-action gate for kernel tool dispatch.

The gate is intentionally a policy/check module, not an executor: it never
sends notifications or dispatches a tool. When an opted-in proposal is risky it
writes an exact, durable pending request and returns a structured denial. A
separate approval/replay worker can consume those records later.

Opt-in is the presence of ``<working_dir>/.security/gate_config.json``. This
keeps existing agents unchanged while giving a deployment a mechanical,
fail-closed boundary for the first-class ``shell`` surface. The former
``file`` family is gone, so ``local_write_roots`` no longer constrains
anything: it is still parsed and merged for config compatibility, but no
classifier consults it.
"""
from __future__ import annotations

import contextlib
import json
import os
import shlex
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .tool_call_guard import GuardDecision, ToolProposal

MERGED_LIST_KEYS = ("local_write_roots", "remote_write_roots", "ssh_hosts", "trusted_scripts")
APPROVAL_CHANNELS = ("telegram", "wechat")
DEFAULT_APPROVAL_TTL_SECONDS = 24 * 60 * 60
GATE_CHECK_NAME = "risky_action_gate"

# Commands with a known write/destructive meaning. Unknown commands are also
# treated as risky below: shell is free-form, so the safe default is deny.
_DESTRUCTIVE_VERBS = {"rm", "rmdir", "unlink", "shred", "truncate"}
_DESTINATION_VERBS = {"mv", "cp", "install", "rsync"}
_READ_ONLY_VERBS = {
    "cat", "cut", "echo", "env", "head", "jq", "ls", "pwd",
    "printf", "rg", "sort", "tail", "true", "type", "uname", "wc", "which",
}
# Read-only verbs that ALSO accept a write-destination flag (e.g. sort -o,
# curl -o, wget -O, tee). A bare ``-o``/``--output``/``-O``/``-i``/``--in-place``
# would turn an otherwise read-only invocation into a write; reject them.
_READ_ONLY_WRITE_FLAGS = {"-o", "--output", "-O", "--outfile", "-i", "--in-place"}
# Option-aware per-verb allowlists for the read-only verbs. A read-only verb is
# only trusted when every option token is a known read-only form; unknown or
# risky options fail closed so free-form shell cannot smuggle helper execution
# (rg --pre), system mutation (date <new_date>), or config-remapped execution
# through an allowlisted verb name.
_READ_ONLY_VERB_LONG_OPTIONS: dict[str, set[str]] = {
    "cat": {"--number", "--number-nonblank", "--show-all", "--show-ends", "--show-tabs", "--show-nonprinting", "--squeeze-blank"},
    "cut": {"--bytes", "--characters", "--delimiter", "--fields", "--only-delimited", "--zero-terminated"},
    "echo": set(),
    "env": {"--chdir", "--unset", "--null", "--ignore-signal"},
    "head": {"--bytes", "--lines", "--quiet", "--verbose", "--zero-terminated"},
    "jq": {"--compact-output", "--raw-output", "--null-input", "--exit-status", "--slurp", "--raw-input", "--sort-keys", "--color-output", "--monochrome-output", "--join-output", "--ascii-output", "--from-file", "--arg", "--argjson", "--slurpfile", "--rawfile", "--args", "--jsonargs", "--seq", "--stream", "--indent", "--tab", "--unbuffered"},
    "ls": {"--all", "--almost-all", "--author", "--block-size", "--classify", "--color", "--directory", "--dereference-command-line", "--file-type", "--format", "--full-time", "--group-directories-first", "--hide", "--human-readable", "--ignore", "--inode", "--literal", "--no-group", "--no-owner", "--numeric-uid-gid", "--quote-name", "--recursive", "--reverse", "--size", "--sort", "--time", "--time-style", "--zero"},
    "pwd": {"--logical", "--physical"},
    "printf": set(),
    "rg": {"--text", "--ignore-case", "--line-number", "--files-with-matches", "--count", "--invert-match", "--word-regexp", "--line-regexp", "--regexp", "--file", "--glob", "--iglob", "--type", "--type-not", "--max-count", "--context", "--after-context", "--before-context", "--multiline", "--pcre2", "--smart-case", "--no-config", "--no-ignore", "--hidden", "--no-messages", "--column", "--heading", "--follow", "--no-line-number", "--only-matching", "--case-sensitive", "--files", "--no-filename", "--with-filename", "--json", "--null", "--max-columns", "--max-columns-preview", "--colors", "--color", "--encoding", "--path-separator", "--sort", "--type-add", "--type-clear"},
    "sort": {"--check", "--dictionary-order", "--field-separator", "--ignore-case", "--key", "--numeric-sort", "--reverse", "--stable", "--unique", "--version-sort", "--human-numeric-sort", "--general-numeric-sort", "--month-sort", "--random-sort", "--zero-terminated", "--ignore-leading-blanks", "--ignore-nonprinting", "--random-source", "--parallel", "--batch-size"},
    "tail": {"--bytes", "--follow", "--lines", "--quiet", "--retry", "--sleep-interval", "--verbose"},
    "true": set(),
    "type": set(),
    "uname": {"--all", "--kernel-name", "--nodename", "--kernel-release", "--kernel-version", "--machine", "--processor", "--hardware-platform", "--operating-system"},
    "wc": {"--bytes", "--chars", "--lines", "--max-line-length", "--words"},
    "which": {"--all", "--read-alias", "--skip-alias", "--skip-dot", "--skip-tilde"},
}
_READ_ONLY_VERB_SHORT_OPTIONS: dict[str, set[str]] = {
    "cat": {"A", "b", "e", "E", "n", "s", "t", "T", "u", "v"},
    "cut": {"b", "c", "d", "f", "s", "z", "n"},
    "echo": {"n", "e", "E"},
    "env": {"i", "0", "C"},
    "head": {"n", "c", "q", "v", "z"},
    "jq": {"c", "r", "n", "e", "s", "R", "S", "C", "M", "j", "a", "o", "u"},
    "ls": {"a", "A", "b", "c", "C", "d", "f", "F", "g", "G", "h", "H", "i", "k", "l", "L", "m", "n", "N", "p", "q", "Q", "r", "R", "s", "S", "t", "T", "u", "U", "v", "w", "x", "X", "Z", "1"},
    "pwd": {"L", "P"},
    "printf": set(),
    "rg": {"a", "i", "n", "l", "c", "v", "w", "x", "U", "P", "S", "H", "o", "N", "L", "M", "I", "s", "j", "0"},
    "sort": {"b", "c", "d", "f", "g", "h", "i", "m", "M", "n", "r", "R", "s", "u", "V", "z", "C"},
    "tail": {"n", "c", "f", "F", "q", "s", "v", "r", "z"},
    "true": set(),
    "type": {"a", "p", "P", "t"},
    "uname": {"a", "s", "n", "r", "v", "m", "p", "i", "o"},
    "wc": {"c", "l", "m", "w", "L"},
    "which": {"a", "s", "p"},
}
# Short options that take an attached value (e.g. ``sort -k2``, ``head -n5``,
# ``cut -d,``, ``rg -ePAT``). The first character may be a value-taking short;
# every other short-option cluster must be wholly allowlisted.
_READ_ONLY_VERB_VALUE_SHORTS: dict[str, set[str]] = {
    "cat": set(),
    "cut": {"b", "c", "d", "f"},
    "echo": set(),
    "env": {"u", "C"},
    "head": {"n", "c"},
    "jq": {"f", "L"},
    "ls": {"I", "T", "w"},
    "pwd": set(),
    "printf": set(),
    "rg": {"e", "f", "g", "t", "T", "m", "A", "B", "C"},
    "sort": {"k", "t"},
    "tail": {"n", "c", "s"},
    "true": set(),
    "type": set(),
    "uname": set(),
    "wc": set(),
    "which": set(),
}
# Git options that make a nominally read-only subcommand execute an external
# helper (git diff --ext-diff/--textconv, config overrides that remap
# diff.external/core.pager/alias etc.). Fail closed on all of them.
_GIT_EXTERNAL_EXEC_OPTIONS = {"--ext-diff", "--textconv", "--show-signature"}
# Subcommands whose diff machinery runs external textconv/diff filters by
# default (git-diff(1): textconv external filters enabled by default).
_GIT_TEXT_CONV_SUBCOMMANDS = {"diff", "log", "show"}
# Positive option grammar for diff/log/show: ONLY these long options are
# accepted on a read-only query. Anything else fails closed (Fable cross-check
# P0 #10 direction): unknown options must not default-open because git keeps
# gaining external-execution paths (--remerge-diff/--diff-merges=remerge runs
# custom merge drivers; --show-signature runs gpg; --ext-diff/--textconv run
# external filters). The merge/remap/exec families are simply NOT listed.
_GIT_QUERY_LONG_OPTIONS = {
    # required helper-disable flags
    "--no-textconv", "--no-ext-diff",
    # diff/log/show common read-only output selectors
    "--name-only", "--name-status", "--stat", "--numstat", "--shortstat",
    "--dirstat", "--dirstat-by-file", "--summary", "--patch", "--raw",
    "--patch-with-stat", "--patch-with-raw",
    "--no-color", "--color", "--color-moved", "--color-moved-ws", "--no-color-moved",
    "--exit-code", "--quiet",
    "--no-prefix", "--src-prefix", "--dst-prefix", "--line-prefix",
    "--relative", "--unified", "--inter-hunk-context", "--function-context",
    "--minimal", "--patience", "--histogram", "--anchored",
    "--indent-heuristic", "--no-indent-heuristic",
    "--ignore-space-at-eol", "--ignore-space-change", "--ignore-all-space",
    "--ignore-blank-lines", "--ignore-cr-at-eol", "--ignore-matching-lines",
    "--break-rewrites", "--irreversible-delete", "--find-renames",
    "--no-renames", "--find-copies", "--find-copies-harder",
    "--rename-empty", "--no-rename-empty", "--find-object",
    "--diff-filter", "--diff-algorithm", "--word-diff", "--word-diff-regex",
    "--color-words", "--ws-error-highlight", "--no-expand-tabs", "--expand-tabs",
    "--submodule", "--full-index", "--binary", "--abbrev-commit", "--no-abbrev-commit",
    "--output-indicator-new", "--output-indicator-old", "--output-indicator-context",
    # log/show pretty/format
    "--format", "--pretty", "--oneline", "--graph", "--decorate",
    "--no-decorate", "--abbrev", "--no-abbrev", "--date",
    # log revision/limit selectors (read-only queries)
    "--all", "--branches", "--tags", "--remotes", "--glob", "--exclude",
    "--reflog", "--not", "--first-parent", "--merges", "--no-merges",
    "--min-parents", "--max-parents", "--reverse", "--topo-order",
    "--date-order", "--author-date-order", "--since", "--until",
    "--after", "--before", "--author", "--committer", "--grep",
    "--all-match", "--invert-grep", "--regexp-ignore-case",
    "--extended-regexp", "--fixed-strings", "--pickaxe-all",
    "--pickaxe-regex", "--max-count", "--skip", "--count", "--follow",
    "--full-history", "--simplify-merges", "--simplify-by-decoration",
    "--dense", "--sparse", "--ancestry-path", "--left-right",
    "--left-only", "--right-only", "--cherry-pick", "--cherry", "--boundary",
    "--use-mailmap", "--no-mailmap",
    # explicitly NOT listed (external exec / merge machinery):
    # --ext-diff --textconv --show-signature --remerge-diff --diff-merges
    # -m/-r/-c/-cc combined forms, --output (write), --no-optional-locks
    # (global, consumed before subcommand only)
}
# Short options accepted on diff/log/show read-only queries (cluster of
# characters, plus value-taking shorts below). Merge/combined short forms
# (-m -r -c) are deliberately absent. Digits allow -1..-9 count limits.
_GIT_QUERY_SHORT_OPTIONS = {"p", "q", "b", "w", "i", "E", "F", "M", "C", "B",
                             "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
_GIT_QUERY_VALUE_SHORTS = {"n", "U", "G", "S", "L"}
_READ_ONLY_GIT_SUBCOMMANDS = {"branch", "diff", "log", "ls-files", "remote", "show", "status", "tag"}
# Git subcommands whose bare-name form is read-only but whose positional form
# mutates (branch <name> creates, tag <name> creates, remote add mutates).
_GIT_LIST_ONLY_SUBCOMMANDS = {"branch", "remote", "tag"}
_WRAPPERS = {"command", "env", "exec", "nohup", "sudo", "time", "doas"}
# Environment variables whose assignment can redirect execution or load hostile
# code; ``env PATH=/attacker ls`` must not be unwrapped into a read-only ``ls``.
_ENV_EXECUTION_REDIRECT_KEYS = {
    "PATH", "ENV", "BASH_ENV", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "PYTHONPATH", "PYTHONSTARTUP", "PERL5LIB", "RUBYLIB", "NODE_PATH", "GEM_HOME", "GEM_PATH",
    # Git env vars that redirect git to execute an external program.
    "GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR",
    "GIT_SSH_COMMAND", "GIT_SSH",
    # Git trace sinks: GIT_TRACE* / GIT_TRACE2* write to an arbitrary file
    # path or open a Unix domain socket (git(1) trace output options).
    "GIT_TRACE", "GIT_TRACE2",
    # Git config-path / config-pair env vars inject ambient config (e.g.
    # GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor
    # GIT_CONFIG_VALUE_0=/tmp/helper makes git status run an external hook).
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
}
# Env assignment keys matched by prefix (numbered pairs such as
# GIT_CONFIG_KEY_0 / GIT_CONFIG_VALUE_0).
_ENV_EXECUTION_REDIRECT_KEY_PREFIXES = {"GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "GIT_TRACE_", "GIT_TRACE2_"}
_INTERPRETERS = {"bash", "fish", "node", "perl", "python", "python2", "python3", "ruby", "sh", "zsh"}
_CHAIN_SEPARATORS = ("&&", "||", ";", "|", "\n", ">>", ">", "<<", "<", "|&")


def _is_write_destination_flag(token: str) -> bool:
    """True for any token that names a write destination on a read-only verb.

    Covers the standalone flags (``-o``/``--output``/``-O``/``-i``), their
    joined short forms (``sort -oFILE``, ``wget -OFILE``, ``sed -i.bak``), and
    the long ``--output=``/``--outfile=``/``--in-place=`` assignments.
    """
    if token in _READ_ONLY_WRITE_FLAGS:
        return True
    if token.startswith(("--output=", "--outfile=", "--in-place=")):
        return True
    if len(token) > 2 and token.startswith(("-o", "-O", "-i")):
        return True
    return False


# Read-only options for each list-only git subcommand. An option token is
# accepted only when its option name (before any ``=``) is in this set; every
# other option on branch/tag/remote fails closed so option-only mutations
# (``--unset-upstream``, ``--set-upstream-to=``, ``--edit-description``,
# ``-a``/``-s``/``-m`` for tags, ``remote set-url``) cannot be smuggled in.
_GIT_LIST_ONLY_READ_ONLY_OPTIONS: dict[str, set[str]] = {
    "branch": {
        "--list", "-l", "--all", "-a", "--remotes", "-r", "--verbose", "-v",
        "--no-color", "--color", "--format", "--contains", "--merged",
        "--no-merged", "--points-at", "--show-current", "--quiet", "-q",
        "--column", "--no-column", "--abbrev", "--no-abbrev",
    },
    "tag": {
        "--list", "-l", "--sort", "--format", "--color", "--no-color",
        "--contains", "--merged", "--no-merged", "--points-at", "--column",
        "--no-column", "--verify", "-v",
    },
    "remote": {"--verbose", "-v", "-n"},
}

# For ``git remote`` the first positional is the sub-subcommand; only these
# query forms are read-only. Any other sub-subcommand fails closed. ``show``
# must carry ``-n`` (no remote query): without it git first runs
# ``git ls-remote <name>`` and a repo-controlled remote URL (e.g. ext::<cmd>)
# can execute an external transport/helper.
_GIT_REMOTE_READ_ONLY_SUBCOMMANDS = {"get-url", "show"}


def _git_list_only_risk(subcommand: str, rest: list[str]) -> str | None:
    """Fail-closed check for git branch/tag/remote list-only forms.

    ``rest`` is the argument list after the subcommand (global options already
    consumed by ``_git_risk_reason``). Returns ``None`` for a read-only
    list/query form and a reason string for every creation, mutation, or
    unknown-option form. Only the allowed options
    (``_GIT_LIST_ONLY_READ_ONLY_OPTIONS``) and, for remote, the read-only
    sub-subcommands are accepted; everything else is denied.
    """
    positionals = [t for t in rest if not t.startswith("-")]
    options = [t for t in rest if t.startswith("-")]
    allowed_options = _GIT_LIST_ONLY_READ_ONLY_OPTIONS.get(subcommand, set())
    for option in options:
        if option.split("=", 1)[0] not in allowed_options:
            return f"git {subcommand} option {option} is not a read-only form"
    if subcommand == "remote":
        if positionals and positionals[0] not in _GIT_REMOTE_READ_ONLY_SUBCOMMANDS:
            return f"git remote subcommand {positionals[0]} is not read-only"
        if positionals and positionals[0] == "show" and "-n" not in options:
            return "git remote show may query a remote transport/helper; require -n"
        return None
    # branch / tag: a bare positional (no --list) is creation; only a single
    # ``--list <pattern>`` positional is read-only.
    if len(positionals) > 1:
        return f"git {subcommand} with multiple positional arguments is not read-only"
    if positionals and "--list" not in options:
        return f"git {subcommand} with a positional argument is not read-only"
    return None



def _resolve(path: str | os.PathLike[str]) -> str:
    return str(Path(path).expanduser().resolve())


def _canonical_target(target: str | None, base_cwd: str | None) -> str | None:
    """Resolve a shell script target against the executor's effective cwd.

    The gate and the executor must agree on where a relative target lands.
    Shell omits ``working_dir`` -> executor uses the agent workdir. Using a
    bare ``Path(...).resolve()`` here would resolve against the *process* cwd,
    which can differ from the executor's effective cwd and let an approved
    target point outside the approved root at execution time.
    """
    if not target or not base_cwd:
        return _resolve(target) if target else None
    path = Path(target).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    return str((Path(base_cwd) / path).resolve())


def _list_value(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key, [])
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


# Environment-variable opt-in switch. The gate is opt-in and DEFAULT CLOSED:
# without ``LINGTAI_RISKY_ACTION_GATE`` set (or a ``.security/gate_config.json``
# present) existing agents see zero behavior change. Set it to a truthy value
# (``1``/``true``/``yes``/``on``) to enable the gate even without a config file;
# an empty config then denies every unclassified shell command until the
# deployment adds allowlists.
_GATE_OPT_IN_ENV = "LINGTAI_RISKY_ACTION_GATE"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def _env_opt_in_enabled() -> bool:
    return os.environ.get(_GATE_OPT_IN_ENV, "").strip().lower() in _TRUTHY_ENV_VALUES


def load_gate_config(working_dir: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Load the opt-in config and union its sibling shared-network grants.

    Returns ``None`` when the gate is not opted in at all: no config file AND
    no ``LINGTAI_RISKY_ACTION_GATE`` environment switch. When the env switch is
    present without a config file, returns an empty config (gate enabled,
    strict default).
    """
    workdir = Path(working_dir).expanduser().resolve()
    own_path = workdir / ".security" / "gate_config.json"
    if not own_path.is_file():
        if not _env_opt_in_enabled():
            return None
        return {}
    own = json.loads(own_path.read_text(encoding="utf-8"))
    if not isinstance(own, dict):
        raise ValueError("gate_config.json must contain a JSON object")

    shared_path = workdir.parent / "shared" / ".security" / "gate_config.json"
    if not shared_path.is_file():
        return dict(own)
    shared = json.loads(shared_path.read_text(encoding="utf-8"))
    if not isinstance(shared, dict):
        raise ValueError("shared gate_config.json must contain a JSON object")
    merged = dict(own)
    for key in MERGED_LIST_KEYS:
        values: list[str] = []
        for source in (shared, own):
            values.extend(_list_value(source, key))
        if values:
            merged[key] = list(dict.fromkeys(values))
    return merged


def _pending_dir(working_dir: Path, config: dict[str, Any]) -> Path:
    raw = config.get("pending_dir", ".security/pending")
    if not isinstance(raw, str) or not raw.strip():
        raw = ".security/pending"
    path = Path(raw).expanduser()
    return (working_dir / path).resolve() if not path.is_absolute() else path.resolve()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def _request_lock(request_path: Path, *, timeout_seconds: float = 5.0, stale_after_seconds: float = 30.0) -> Iterator[None]:
    """Serialize writers on the same pending request via an exclusive lock file.

    The lock file is created with ``O_CREAT|O_EXCL`` so only one process can
    hold it at a time; a bounded retry loop waits for a concurrent writer.
    ``mark_approval``/``expire_pending`` re-read the request *inside* this
    lock, so a stale reader can never overwrite a newer terminal decision.

    A crashed holder leaves the lock file behind. After ``stale_after_seconds``
    the lock is treated as stale and reclaimed (unlinked and retried), so a
    crash cannot permanently wedge request mutation; the stale owner is
    recorded in the lock file for diagnosis.
    """
    lock_path = request_path.with_name(request_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    owner = f"pid={os.getpid()}"
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_after_seconds:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"could not acquire request lock: {lock_path}")
            time.sleep(0.02)
            continue
        try:
            os.write(fd, owner.encode("utf-8"))
        finally:
            os.close(fd)
        acquired = True
        break
    try:
        yield
    finally:
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass


def _token_is_ambiguous(token: str) -> bool:
    return any(char in token for char in ("*", "?", "$", "`")) or ("[" in token and "]" in token)


def _command_segments(command: str) -> list[list[str]]:
    # shlex does not interpret shell operators; splitting first gives a
    # conservative classifier while retaining the original command verbatim in
    # the pending record for exact replay.
    segments = [command]
    for separator in _CHAIN_SEPARATORS:
        segments = [part for segment in segments for part in segment.split(separator)]
    result: list[list[str]] = []
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return []
        if tokens:
            result.append(tokens)
    return result


def _unwrap(tokens: list[str]) -> list[str] | None:
    """Strip leading wrappers, returning ``None`` when a wrapper is unsafe.

    Returns the remaining tokens after benign wrappers (command/env/exec/etc.
    with plain flags and ordinary ``KEY=value`` assignments), or ``None`` when
    an ``env`` assignment redirects execution (PATH, LD_PRELOAD, PYTHONPATH,
    etc.) so the caller can fail closed instead of trusting the unwrapped verb.
    """
    index = 0
    while index < len(tokens) and tokens[index] in _WRAPPERS:
        wrapper = tokens[index]
        index += 1
        # ``env KEY=value command`` is common; skip assignments, but do not
        # attempt to model wrappers with option values (fail closed later).
        if wrapper == "env":
            while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("="):
                key = tokens[index].split("=", 1)[0]
                if key in _ENV_EXECUTION_REDIRECT_KEYS or any(
                    key.startswith(prefix) for prefix in _ENV_EXECUTION_REDIRECT_KEY_PREFIXES
                ):
                    return None
                index += 1
    return tokens[index:]


# Git global options that are proven read-only (no external exec, no config
# remap, no state mutation). Everything else before the subcommand fails
# closed, including -c/--config* (except the exact core.fsmonitor=false
# carve-out), --exec-path, --git-dir, --work-tree, --namespace.
_GIT_SAFE_GLOBAL_OPTIONS = {
    "--no-pager", "-P", "--paginate", "-p", "--no-optional-locks",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs", "--no-replace-objects",
}
_GIT_FSMONITOR_DISABLE = "core.fsmonitor=false"


def _git_consume_config_override(args: list[str], i: int) -> tuple[str | None, int]:
    """Consume one ``-c``/``--config``/``--config-env`` override.

    Returns ``(value, next_index)`` for the only allowed override
    (``core.fsmonitor=false``) or ``(reason, next_index)`` starting with a
    denial message for anything else. ``-c`` may be joined
    (``-ccore.fsmonitor=false``) or separate (``-c core.fsmonitor=false``);
    ``--config``/``--config-env`` are always denied.
    """
    token = args[i]
    if token == "--config" or token.startswith("--config=") or token == "--config-env" or token.startswith("--config-env="):
        return "git --config/--config-env override can execute an external helper", i + 1
    if token == "-c":
        if i + 1 >= len(args):
            return "git -c requires a value", i + 1
        value = args[i + 1]
        if value != _GIT_FSMONITOR_DISABLE:
            return f"git -c config override {value!r} can execute an external helper", i + 2
        return _GIT_FSMONITOR_DISABLE, i + 2
    if token.startswith("-c") and not token.startswith("-C") and not token.startswith("--"):
        value = token[2:]
        if value != _GIT_FSMONITOR_DISABLE:
            return f"git -c config override {value!r} can execute an external helper", i + 1
        return _GIT_FSMONITOR_DISABLE, i + 1
    return None, i + 1


def _git_format_has_gpg_atom(value: str) -> bool:
    """True when a pretty-format string contains a GPG/reflog-signature atom.

    ``%G*`` atoms ask git to run ``gpg --verify`` (configurable via
    ``gpg.program`` / ``gpg.<format>.program``) and ``%g*`` reflog-selector
    atoms are treated conservatively as non-read-only. ``%%`` is a literal
    percent and is skipped.
    """
    i = 0
    while i < len(value):
        if value[i] != "%":
            i += 1
            continue
        if i + 1 >= len(value):
            return False
        if value[i + 1] in ("G", "g"):
            return True
        i += 2  # skip %% and all other two-char atoms
    return False


def _git_risk_reason(tokens: list[str]) -> str | None:
    """Fail-closed read-only classifier for one git command.

    Parses git global options first (each ``-c``/``--config*`` independently
    consumed and validated, so one safe ``-c core.fsmonitor=false`` cannot
    let an unsafe ``-c`` pass), then the subcommand, then subcommand-specific
    checks: write-destination flags, external-exec options, ambient
    textconv/fsmonitor helpers, and branch/tag/remote list-only rules.
    """
    args = tokens[1:]
    fsmonitor_disabled = False
    no_optional_locks = False
    i = 0
    while i < len(args) and args[i].startswith("-"):
        token = args[i]
        if token.startswith("-c") or token.startswith("--config"):
            value, next_i = _git_consume_config_override(args, i)
            if value is None:
                return f"git option {token} is not a read-only form"
            if value != _GIT_FSMONITOR_DISABLE:
                return value
            fsmonitor_disabled = True
            i = next_i
            continue
        if token == "-C":
            if i + 1 >= len(args):
                return "git -C requires a directory"
            i += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            i += 1
            continue
        if token.startswith("--exec-path"):
            return "git --exec-path can execute external core programs"
        if token == "--git-dir" or token.startswith("--git-dir=") or token == "--work-tree" or token.startswith("--work-tree=") or token == "--namespace" or token.startswith("--namespace="):
            return f"git global option {token.split('=', 1)[0]} is not a read-only form"
        if token == "--no-optional-locks":
            no_optional_locks = True
            i += 1
            continue
        if token in _GIT_SAFE_GLOBAL_OPTIONS:
            i += 1
            continue
        if token == "--":
            i += 1
            break
        return f"git global option {token} is not a read-only form"
    if i >= len(args):
        return "git subcommand is missing"
    subcommand = args[i]
    rest = args[i + 1:]
    if subcommand not in _READ_ONLY_GIT_SUBCOMMANDS:
        return f"git subcommand is not classified read-only: {subcommand}"
    if any(_is_write_destination_flag(token) for token in rest):
        return f"git {subcommand} carries a write-destination flag"
    j = 0
    after_double_dash = False
    while j < len(rest):
        token = rest[j]
        if token == "--":
            after_double_dash = True
            j += 1
            continue
        if token in _GIT_EXTERNAL_EXEC_OPTIONS:
            return f"git option {token} can execute an external helper"
        if token == "--format" or token == "--pretty":
            if j + 1 >= len(rest):
                return f"git option {token} requires a value"
            if _git_format_has_gpg_atom(rest[j + 1]):
                return "git pretty format may invoke gpg.program via signature atoms; not read-only"
            j += 2
            continue
        if token.startswith("--format=") or token.startswith("--pretty="):
            if _git_format_has_gpg_atom(token.split("=", 1)[1]):
                return "git pretty format may invoke gpg.program via signature atoms; not read-only"
            j += 1
            continue
        if token.startswith("-c") or token.startswith("--config"):
            value, next_j = _git_consume_config_override(rest, j)
            if value != _GIT_FSMONITOR_DISABLE:
                return value if value is not None else f"git option {token} is not a read-only form"
            fsmonitor_disabled = True
            j = next_j
            continue
        if subcommand in _GIT_TEXT_CONV_SUBCOMMANDS and not after_double_dash:
            # Positive per-subcommand option grammar: any unknown option on
            # diff/log/show fails closed (Fable cross-check P0 #10 direction).
            if token.startswith("--"):
                name = token.split("=", 1)[0]
                if name not in _GIT_QUERY_LONG_OPTIONS:
                    return f"git {subcommand} option {name} is not an allowlisted read-only query option"
                j += 1
                continue
            if token.startswith("-") and token != "-":
                if len(token) == 2 and token[1] in _GIT_QUERY_SHORT_OPTIONS:
                    j += 1
                    continue
                if token[1] in _GIT_QUERY_VALUE_SHORTS:
                    j += 1
                    continue
                if all(ch in _GIT_QUERY_SHORT_OPTIONS for ch in token[1:]):
                    j += 1
                    continue
                return f"git {subcommand} option {token} is not an allowlisted read-only query option"
        j += 1
    if subcommand in _GIT_TEXT_CONV_SUBCOMMANDS:
        if "--no-textconv" not in rest or "--no-ext-diff" not in rest:
            return f"git {subcommand} may run external textconv/diff helpers; require --no-textconv --no-ext-diff"
    if subcommand == "status" and not (fsmonitor_disabled and no_optional_locks):
        return "git status may run a core.fsmonitor hook or write .git/index; require -c core.fsmonitor=false and --no-optional-locks"
    if subcommand in _GIT_LIST_ONLY_SUBCOMMANDS:
        return _git_list_only_risk(subcommand, rest)
    return None


def _read_only_option_reason(verb: str, tokens: list[str]) -> str | None:
    """Per-verb option allowlist for read-only verbs.

    Unknown or risky options fail closed: a verb name alone is not enough to
    call an invocation read-only (rg --pre executes a helper; date <new_date>
    sets the system clock). ``--`` ends option parsing; a lone ``-`` is a
    positional. A short cluster (``ls -la``) is accepted only when every
    character is an allowlisted short option; a short option with an attached
    value (``sort -k2``) is accepted only when the first character is an
    allowlisted value-taking short option.
    """
    longs = _READ_ONLY_VERB_LONG_OPTIONS.get(verb, set())
    shorts = _READ_ONLY_VERB_SHORT_OPTIONS.get(verb, set())
    value_shorts = _READ_ONLY_VERB_VALUE_SHORTS.get(verb, set())
    after_double_dash = False
    for token in tokens[1:]:
        if token == "--":
            after_double_dash = True
            continue
        if after_double_dash or token == "-" or not token.startswith("-"):
            continue
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            if name not in longs:
                return f"read-only verb {verb} carries an unknown option: {token}"
            continue
        if len(token) == 2 and token[1] in shorts:
            continue
        if token[1] in value_shorts:
            continue
        if all(ch in shorts for ch in token[1:]):
            continue
        return f"read-only verb {verb} carries an unknown option: {token}"
    return None


def _shell_risk_reason(command: str, config: dict[str, Any], *, cwd: str | None = None) -> str | None:
    segments = _command_segments(command)
    if not segments:
        return "shell command cannot be parsed safely"
    trusted_scripts = {_resolve(path) for path in _list_value(config, "trusted_scripts")}
    ssh_hosts = set(_list_value(config, "ssh_hosts"))
    for tokens in segments:
        unwrapped = _unwrap(tokens)
        if unwrapped is None:
            return "env assignment redirects execution (PATH/LD_*/PYTHONPATH/...)"
        tokens = unwrapped
        if not tokens:
            return "shell command has no resolvable executable"
        verb = Path(tokens[0]).name
        if "/" in tokens[0]:
            return "shell command uses a path-form executable"
        if any(_token_is_ambiguous(token) for token in tokens):
            return "shell command contains an ambiguous token"
        if verb in _DESTRUCTIVE_VERBS:
            return f"destructive shell command: {verb}"
        if verb in _DESTINATION_VERBS:
            return f"destination-writing shell command: {verb}"
        if verb == "git":
            reason = _git_risk_reason(tokens)
            if reason is not None:
                return reason
            continue
        if verb == "ssh":
            host = next((token for token in tokens[1:] if not token.startswith("-")), None)
            if host not in ssh_hosts:
                return "ssh target is not in ssh_hosts"
            return "remote shell execution is risky"
        if verb in _INTERPRETERS:
            if any(flag in tokens for flag in ("-c", "-e")):
                return "inline interpreter code is risky"
            script = next((token for token in tokens[1:] if not token.startswith("-")), None)
            script_path = _canonical_target(script, cwd) if script is not None else None
            if script_path is None or script_path not in trusted_scripts:
                return "interpreter script is not in trusted_scripts"
            continue
        if any(
            token in {"|", ">", ">>", "<", "<<", "|&"}
            or token.startswith((">", "<"))
            for token in tokens
        ):
            return "shell pipeline or redirection is risky"
        if verb == "curl" and any("bash" in token or "sh" == token for token in tokens):
            return "piped installer execution is risky"
        if verb not in _READ_ONLY_VERBS:
            return f"shell command is not classified read-only: {verb}"
        if any(_is_write_destination_flag(token) for token in tokens[1:]):
            return f"read-only verb {verb} carries a write-destination flag"
        reason = _read_only_option_reason(verb, tokens)
        if reason is not None:
            return reason
        if "/" in tokens[0]:
            # Any path-form executable (absolute, ../, bin/, ./) reduces to an
            # allowlisted basename via Path(...).name; only bare verbs are
            # classified read-only. Reject every path form so /tmp/ls, ../ls,
            # bin/ls, /usr/bin/printf and /usr/bin/env cannot masquerade as
            # their bare allowlisted names.
            return f"path-form executable {tokens[0]} is not a bare read-only verb"
    return None


def _operation(proposal: ToolProposal, reason: str) -> dict[str, Any]:
    args = dict(proposal.tool_args)
    operation: dict[str, Any] = {
        "kind": "shell_command" if proposal.tool_name == "shell" else "tool_call",
        "tool_name": proposal.tool_name,
        "args": args,
        "reason": reason,
    }
    if proposal.tool_name == "shell" and isinstance(args.get("input"), dict):
        operation["command"] = args["input"].get("command")
        operation["cwd"] = args["input"].get("working_dir") or None
        operation["effective_cwd"] = args["input"].get("working_dir") or None
    return operation


def _record_pending(working_dir: Path, config: dict[str, Any], proposal: ToolProposal, reason: str) -> tuple[str, Path]:
    request_id = uuid.uuid4().hex
    request = {
        "id": request_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_in_seconds": DEFAULT_APPROVAL_TTL_SECONDS,
        "status": "pending",
        "operation": _operation(proposal, reason),
        "approvals": {channel: None for channel in APPROVAL_CHANNELS},
    }
    directory = _pending_dir(working_dir, config)
    path = directory / f"{request_id}.json"
    _write_json_atomic(path, request)
    return request_id, path


def build_risky_action_check(working_dir: str | os.PathLike[str]):
    """Return the opt-in guard check for *working_dir*.

    Config errors deny rather than disabling the gate. Missing config is the
    explicit compatibility path and returns the existing default allow.
    """
    workdir = Path(working_dir).expanduser().resolve()

    def risky_action_check(proposal: ToolProposal) -> GuardDecision:
        from .execution_workspace import (
            current_execution_workspace,
            resolve_execution_path,
        )
        workspace = current_execution_workspace()
        execution_root = workspace.root if workspace is not None else workdir
        try:
            config = load_gate_config(workdir)
        except Exception as exc:
            return GuardDecision.deny(
                check_name=GATE_CHECK_NAME,
                reason=f"risky-action gate configuration failed: {type(exc).__name__}",
            )
        if config is None:
            return GuardDecision.allow()
        reason: str | None = None
        if proposal.tool_name in ("shell", "bash"):
            # ``bash`` is the legacy compatibility name that the registry maps
            # to the ``shell`` capability after guard evaluation; treating it
            # as shell here prevents a compat-named shell call from bypassing
            # the opted-in gate.
            action_input = proposal.tool_args.get("input")
            action = proposal.tool_args.get("action")
            if action in (None, "run") and isinstance(action_input, dict):
                # The executor uses the agent workdir when working_dir is
                # omitted, so the gate must resolve targets against the same
                # effective cwd rather than the gate process cwd.
                raw_cwd = action_input.get("working_dir") or str(execution_root)
                if workspace is None:
                    # Preserve the historical guard input for ordinary turns;
                    # the shell capability remains the canonical sandbox check.
                    effective_cwd = str(raw_cwd)
                else:
                    try:
                        effective_cwd = str(resolve_execution_path(
                            raw_cwd, fallback_root=execution_root
                        ))
                    except (ValueError, OSError):
                        return GuardDecision.deny(
                            check_name=GATE_CHECK_NAME,
                            reason="shell working_dir escapes execution workspace",
                        )
                reason = _shell_risk_reason(
                    str(action_input.get("command", "")),
                    config,
                    cwd=effective_cwd,
                )
        if not reason:
            return GuardDecision.allow()
        request_id, path = _record_pending(workdir, config, proposal, reason)
        return GuardDecision.deny(
            check_name=GATE_CHECK_NAME,
            reason=f"{reason}; pending dual-channel approval {request_id}",
            metadata={
                "pending_request_id": request_id,
                "pending_request_path": str(path),
                "approval_channels": list(APPROVAL_CHANNELS),
            },
        )

    risky_action_check.__name__ = GATE_CHECK_NAME
    return risky_action_check


def expire_pending(path: str | os.PathLike[str], *, now: datetime | None = None) -> dict[str, Any]:
    """Mark an unapproved request expired; expired requests are never replayable.

    Runs under the exclusive request lock and re-reads the file so a stale
    reader cannot resurrect a request that a concurrent writer already denied
    or expired. Terminal states (denied/expired/approved) are irreversible.
    """
    request_path = Path(path)
    with _request_lock(request_path):
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        if payload.get("status") != "pending":
            return payload
        created_raw = payload.get("created_at")
        try:
            created = datetime.fromisoformat(str(created_raw))
            ttl = int(payload.get("expires_in_seconds", DEFAULT_APPROVAL_TTL_SECONDS))
        except (TypeError, ValueError):
            # A malformed timestamp is not permission to execute; fail closed.
            payload["status"] = "expired"
        else:
            current = now or datetime.now(timezone.utc)
            if created + timedelta(seconds=max(ttl, 0)) <= current:
                payload["status"] = "expired"
        if payload.get("status") == "expired":
            _write_json_atomic(request_path, payload)
        return payload


def mark_approval(path: str | os.PathLike[str], channel: str, decision: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Record one human approval leg; replay remains a separate worker concern.

    An expired request is never replayable, and a denied request is
    irreversible: this function refuses to record an approval (or anything
    else) on a request whose TTL has already elapsed or whose status has
    already left ``pending``. Runs under the exclusive request lock and
    re-reads the file, so a concurrent stale approve can never overwrite a
    newer deny/expire.
    """
    if channel not in APPROVAL_CHANNELS:
        raise ValueError(f"unknown approval channel: {channel}")
    if decision not in {"approve", "deny"}:
        raise ValueError("decision must be approve or deny")
    request_path = Path(path)
    with _request_lock(request_path):
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        if payload.get("status") != "pending":
            return payload
        created_raw = payload.get("created_at")
        try:
            created = datetime.fromisoformat(str(created_raw))
            ttl = int(payload.get("expires_in_seconds", DEFAULT_APPROVAL_TTL_SECONDS))
        except (TypeError, ValueError):
            # A malformed timestamp is not permission to execute; fail closed.
            payload["status"] = "expired"
            _write_json_atomic(request_path, payload)
            return payload
        current = now or datetime.now(timezone.utc)
        if created + timedelta(seconds=max(ttl, 0)) <= current:
            payload["status"] = "expired"
            _write_json_atomic(request_path, payload)
            return payload
        payload.setdefault("approvals", {})[channel] = decision
        if decision == "deny":
            payload["status"] = "denied"
        elif all(payload["approvals"].get(item) == "approve" for item in APPROVAL_CHANNELS):
            payload["status"] = "approved"
        _write_json_atomic(request_path, payload)
        return payload
