"""One auth + isolation policy for every ``claude`` CLI child LingTai launches.

Shared by the ``claude-code`` LLM adapter (``adapter.py``) and the daemon's
Claude CLI backends (``claude`` / ``claude-p`` / ``claude-code`` in
``lingtai.tools.daemon``) so the order is written once:

1. **Setup-token** — an explicit token (the preset's resolved ``api_key_env``,
   default ``CLAUDE_CODE_OAUTH_TOKEN``), else an operator overlay value, else
   the process env ``CLAUDE_CODE_OAUTH_TOKEN``. The child gets exactly that
   token plus a private, LingTai-owned ``CLAUDE_CONFIG_DIR`` and
   ``--setting-sources user`` (only that empty private dir's settings), so the
   machine's ``~/.claude`` settings, CLAUDE.md, hooks, plugins, credentials,
   and history are never loaded. The private dir's ``.claude.json`` is
   pre-seeded (``seed_private_config``) so an interactive CLI skips its
   first-run theme picker and trusts only LingTai-managed workspaces.
2. **Local login** — no token, and ``claude auth status --json`` reports the
   installed CLI logged in: the inherited config dir is kept (on macOS the
   login lives in the Keychain entry tied to it) and ``--setting-sources ""``
   drops user/project/local settings files.
3. **Neither** — ``ClaudeCodeAuthError`` with guidance naming both fixes.

Every mode strips ``CLAUDE_CODE_STRIPPED_ENV`` (API-key billing, a redirected
``ANTHROPIC_BASE_URL``, and the cloud-provider ``CLAUDE_CODE_USE_*`` switches)
and any inherited ``CLAUDE_CODE_OAUTH_TOKEN``; proxy variables pass through.
The token is only ever placed in the child's environment — never on argv and
never logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from lingtai.kernel import preset_connectivity as _connectivity
from lingtai.kernel.logging import get_logger
from lingtai.kernel.preset_connectivity import (
    CLAUDE_CODE_AUTH_GUIDANCE,
    CLAUDE_CODE_OAUTH_TOKEN_ENV,
    CLAUDE_CODE_STRIPPED_ENV,
    CLAUDE_LOGIN_CLI_MISSING,
    CLAUDE_LOGIN_NOT_LOGGED_IN,
)

logger = get_logger()

#: Claude Code's config-root env var. Setup-token mode points it at a private,
#: LingTai-owned directory; local-login mode leaves the inherited value alone.
CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"

AUTH_MODE_SETUP_TOKEN = "setup_token"
AUTH_MODE_LOCAL_LOGIN = "local_login"

# ``--setting-sources`` per auth mode. ``user`` in setup-token mode loads only
# the (empty) private config dir's settings — keeping Claude Code's default
# transcript retention cleanup — while dropping project/local settings and
# CLAUDE.md from the cwd's ancestors. ``""`` in local-login mode drops every
# settings-file source, so the machine's ~/.claude settings, hooks, and
# CLAUDE.md memory are not loaded (managed policy and ``--settings`` flag
# settings always apply). Verified on Claude Code 2.1.285.
SETTING_SOURCES = {
    AUTH_MODE_SETUP_TOKEN: "user",
    AUTH_MODE_LOCAL_LOGIN: "",
}

#: The global-state file Claude Code reads inside ``CLAUDE_CONFIG_DIR`` (it
#: lives at ``~/.claude.json`` only when the variable is unset). Verified on
#: Claude Code 2.1.285: ``auth status`` / ``-p`` create it inside the dir.
CLAUDE_GLOBAL_CONFIG_FILENAME = ".claude.json"

# Per-agent private config dirs live under ``<tempdir>/lingtai-claude-code/``,
# keyed by a hash of the agent anchor (never inside the agent's git-snapshotted
# working dir, and never shared with ``~/.claude``).
PRIVATE_CONFIG_ROOT = "lingtai-claude-code"


class ClaudeCodeError(RuntimeError):
    """A ``claude`` CLI invocation failed (non-zero exit, no output, etc.)."""


class ClaudeCodeAuthError(ClaudeCodeError):
    """No usable Claude Code credential (missing/rejected token, no local login,
    or no ``claude`` CLI)."""


def agent_config_anchor(working_dir: str | os.PathLike) -> str:
    """The per-agent anchor: the agent's resolved ``init.json`` path.

    The same value ``build_provider_defaults_from_manifest_llm`` injects as
    ``claude_code_config_anchor``, so an agent's claude-code brain and its
    daemon Claude backends share one private config dir.
    """
    return str((Path(working_dir) / "init.json").resolve())


def ensure_private_dir(path: Path) -> None:
    """Create *path* as a user-private (0700) directory, refusing a hijacked one.

    Re-run before every setup-token invocation so an OS temp cleaner that
    removed the directory is healed. A symlink, a non-directory, or a
    directory owned by another user raises ``OSError``.
    """
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"not a private directory: {path}")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        raise OSError(f"directory owned by another user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(path, 0o700)


def choose_private_config_dir(anchor: str | None, *, owner: object) -> Path:
    """Pick the private ``CLAUDE_CONFIG_DIR`` for setup-token mode.

    Per agent (stable across restarts, so the CLI's cached state and
    ``--resume`` transcripts persist) when an *anchor* is known; otherwise a
    fresh ``mkdtemp`` directory removed when *owner* is collected. Either way
    it is LingTai-created, user-private, and never ``~/.claude``.
    """
    if anchor:
        digest = hashlib.sha256(anchor.encode("utf-8")).hexdigest()[:16]
        root = Path(tempfile.gettempdir()) / PRIVATE_CONFIG_ROOT
        candidate = root / digest
        try:
            ensure_private_dir(root)
            ensure_private_dir(candidate)
            return candidate
        except OSError as exc:
            logger.warning(
                "[claude-code] per-agent config dir unusable (%s); "
                "using a per-owner private dir",
                type(exc).__name__,
            )
    path = Path(tempfile.mkdtemp(prefix=f"{PRIVATE_CONFIG_ROOT}-"))
    weakref.finalize(owner, shutil.rmtree, str(path), ignore_errors=True)
    return path


def seed_private_config(
    config_dir: Path, *, trusted_workspaces: Iterable[str | os.PathLike] = (),
) -> Path:
    """Merge LingTai's first-run state into a PRIVATE config dir's ``.claude.json``.

    Only ever called on the LingTai-owned setup-token dir, never ``~/.claude``
    and never in local-login mode. A fresh ``CLAUDE_CONFIG_DIR`` makes the
    interactive CLI open its first-run theme picker, gated by
    ``hasCompletedOnboarding`` (Claude Code 2.1.285); each workspace LingTai
    itself manages and trusts is recorded as
    ``projects[<realpath>].hasTrustDialogAccepted`` so its trust dialog does
    not block either. Print mode (``-p``) shows neither screen and is
    unaffected. Existing keys (the CLI's own state) are kept; the file is
    rewritten only when a key is missing, atomically and 0600, so steady-state
    calls are no-ops that never race the CLI's own writes.
    """
    path = config_dir / CLAUDE_GLOBAL_CONFIG_FILENAME
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        loaded = {}
    except (OSError, ValueError):
        logger.warning("[claude-code] private %s unreadable; reseeding it", path.name)
        loaded = {}
    data = dict(loaded) if isinstance(loaded, dict) else {}
    changed = not path.exists() or data != loaded
    if data.get("hasCompletedOnboarding") is not True:
        data["hasCompletedOnboarding"] = True
        changed = True
    for workspace in trusted_workspaces:
        key = os.path.realpath(os.fspath(workspace))
        projects = data.get("projects")
        if not isinstance(projects, dict):
            projects = {}
        entry = projects.get(key)
        entry = dict(entry) if isinstance(entry, dict) else {}
        if entry.get("hasTrustDialogAccepted") is not True:
            entry["hasTrustDialogAccepted"] = True
            projects = {**projects, key: entry}
            data["projects"] = projects
            changed = True
    if not changed:
        return path
    fd, tmp_name = tempfile.mkstemp(
        dir=config_dir, prefix=f"{CLAUDE_GLOBAL_CONFIG_FILENAME}.", suffix=".tmp"
    )
    try:
        # mkstemp creates the file 0600 in the private dir; os.replace keeps it.
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    return path


def resolve_setup_token(
    explicit: str | None = None,
    *,
    overlay: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """The setup-token for one invocation, or ``None``.

    Order: *explicit* (a preset's resolved ``api_key_env``), then an operator
    *overlay* value, then the process env ``CLAUDE_CODE_OAUTH_TOKEN``. Blank
    values count as absent.
    """
    source = os.environ if environ is None else environ
    for value in (
        explicit,
        (overlay or {}).get(CLAUDE_CODE_OAUTH_TOKEN_ENV),
        source.get(CLAUDE_CODE_OAUTH_TOKEN_ENV),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class LocalLoginCache:
    """Per-owner cache of the ``claude auth status`` verdict.

    Only a logged-in (or inconclusive) verdict is kept: after "not logged in"
    the next request re-checks, so a later ``claude auth login`` works without
    a refresh. ``forget()`` drops it when the CLI rejects the login later.
    """

    __slots__ = ("state",)

    def __init__(self) -> None:
        self.state: str | None = None

    def forget(self) -> None:
        self.state = None


def missing_credential_message(label: str) -> str:
    return (
        f"{label} has no Claude credential: no {CLAUDE_CODE_OAUTH_TOKEN_ENV} "
        "setup-token is configured and the local `claude` CLI is not logged "
        f"in. {CLAUDE_CODE_AUTH_GUIDANCE}."
    )


def require_local_login(
    env: Mapping[str, str],
    *,
    cli_path: str,
    cwd: str | None,
    cache: LocalLoginCache | None,
    label: str,
) -> None:
    """Fail closed unless the installed CLI reports a local login."""
    if cache is not None and cache.state is not None:
        return
    # Looked up on the module at call time: the one probe seam shared with the
    # connectivity check (and replaced by the test suite's guard).
    state = _connectivity.claude_cli_login_status(
        cli_path, env=dict(env), cwd=cwd
    )
    if state == CLAUDE_LOGIN_CLI_MISSING:
        raise ClaudeCodeAuthError(
            f"{label}: `{cli_path}` not found on PATH; install Claude Code. "
            f"{CLAUDE_CODE_AUTH_GUIDANCE}."
        )
    if state == CLAUDE_LOGIN_NOT_LOGGED_IN:
        raise ClaudeCodeAuthError(missing_credential_message(label))
    # logged_in, or unknown (e.g. a CLI without ``auth status``): proceed and
    # let the CLI report its own auth error if there is one.
    if cache is not None:
        cache.state = state


@dataclass(frozen=True)
class ClaudeChildAuth:
    """The resolved auth decision for one ``claude`` child process."""

    env: dict[str, str]
    mode: str

    @property
    def setting_sources_argv(self) -> list[str]:
        return ["--setting-sources", SETTING_SOURCES[self.mode]]

    @property
    def config_dir(self) -> Path:
        """Where this child keeps sessions (``--resume`` transcripts)."""
        configured = self.env.get(CLAUDE_CONFIG_DIR_ENV)
        return Path(configured) if configured else Path.home() / ".claude"


def prepare_claude_child(
    *,
    explicit_token: str | None,
    private_config_dir: Callable[[], Path],
    cli_path: str = "claude",
    probe_cwd: str | None = None,
    overlay: Mapping[str, str] | None = None,
    login_cache: LocalLoginCache | None = None,
    label: str = "claude-code",
    trusted_workspaces: Iterable[str | os.PathLike] = (),
) -> ClaudeChildAuth:
    """Resolve the auth mode and child env for one ``claude`` invocation.

    *private_config_dir* is called only in setup-token mode, where the dir is
    also seeded with first-run state (``seed_private_config``) including trust
    for *trusted_workspaces* (LingTai-managed workspaces only). *overlay* is an
    explicit operator ``backend_options.env`` map (daemon backends): it may
    carry the token, is visible to the login probe, and is applied last so a
    deliberate choice (for example a ``CLAUDE_CONFIG_DIR`` profile) wins.
    Raises ``ClaudeCodeAuthError`` when neither a token nor a local login is
    available.
    """
    overlay = dict(overlay or {})
    env = os.environ.copy()
    for key in CLAUDE_CODE_STRIPPED_ENV:
        env.pop(key, None)
    env.pop(CLAUDE_CODE_OAUTH_TOKEN_ENV, None)
    token = resolve_setup_token(explicit_token, overlay=overlay)
    if token:
        config_dir = private_config_dir()
        try:
            seed_private_config(config_dir, trusted_workspaces=trusted_workspaces)
        except OSError as exc:
            raise ClaudeCodeError(
                f"{label} could not seed its private Claude config: {exc}"
            ) from exc
        env[CLAUDE_CODE_OAUTH_TOKEN_ENV] = token
        env[CLAUDE_CONFIG_DIR_ENV] = str(config_dir)
        mode = AUTH_MODE_SETUP_TOKEN
    else:
        require_local_login(
            {**env, **overlay},
            cli_path=cli_path,
            cwd=probe_cwd,
            cache=login_cache,
            label=label,
        )
        mode = AUTH_MODE_LOCAL_LOGIN
    env.update(overlay)
    if mode == AUTH_MODE_SETUP_TOKEN:
        env[CLAUDE_CODE_OAUTH_TOKEN_ENV] = token
    return ClaudeChildAuth(env=env, mode=mode)
