"""Kernel-owned ``.lingtai`` project creation.

What makes a directory a valid LingTai project — the ``.lingtai/`` skeleton,
the human mailbox, an agent's ``init.json``/``.agent.json``, the covenant
mirror — is kernel semantics.  Until now it lived in the Go TUI, which meant
every other caller (``lingtai -p``, CI, benchmark harnesses) either shelled out
to the TUI or reimplemented it.  This module is the canonical Python
implementation; ``lingtai-agent project create`` is its CLI skin
(:mod:`lingtai.cli_project`), and the TUI becomes a thin caller.

Provenance
----------
Ported from the Go TUI (``Lingtai-AI/lingtai`` @ 7286da49):

===============================  ===================================
Go                               here
===============================  ===================================
``process.InitProject``          :func:`init_project`
``preset.GenerateInitJSONWithOpts``  :func:`generate_init_json`
``preset.AgentOpts``             :class:`AgentOpts`
``preset.DefaultAgentOpts``      :class:`AgentOpts` field defaults
``preset.ValidateSafeName``      :func:`validate_safe_name`
``preset.CanonicalizeCapabilities``  :func:`canonicalize_capabilities`
``preset.ClampAedAttempts``      :func:`clamp_aed_attempts`
``preset.SyncCapabilityAPIKeyEnv``   :func:`sync_capability_api_key_env`
``preset.stripObsoleteInitFields``   :func:`strip_obsolete_init_fields`
``preset.defaultMCPSpec``        :data:`DEFAULT_MCP_SPECS`
``config.SetEnvVar``             :func:`set_env_var`
===============================  ===================================

Behaviour, not syntax, is what is preserved: the read-modify-write merge over
an existing ``init.json``, the preset ``{active, default, allowed}``
reconciliation, capability canonicalization, and legacy-field stripping all
match the Go original case for case.  Deliberate deviations are marked
``DEVIATION`` in the code below and collected in the implementation report.

Explicitly NOT ported (dead code on the kernel side — see
``workspace-asset-provenance-20260820.md``): ``procedures``/``procedures_file``,
``principle``/``principle_file``, and ``soul_file``.  ``init_schema`` ignores
all of them.  Recipe/skills injection is likewise out of scope: it stays the
caller's (TUI's) responsibility.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from lingtai.kernel._fsutil import JSONNumber, atomic_write_json, atomic_write_text

__all__ = [
    "AgentOpts",
    "CapabilityConflictError",
    "ProjectCreateError",
    "DEFAULT_MCP_SPECS",
    "SOUL_FLOW_ENABLED_ENV_VAR",
    "SUPPORTED_LANGUAGES",
    "canonicalize_capabilities",
    "clamp_aed_attempts",
    "covenant_body",
    "create_project",
    "default_global_dir",
    "generate_init_json",
    "init_project",
    "preset_ref_for",
    "set_env_var",
    "strip_obsolete_init_fields",
    "sync_capability_api_key_env",
    "validate_safe_name",
    "write_covenant",
]


class ProjectCreateError(Exception):
    """A project-creation input or filesystem precondition was rejected."""


class CapabilityConflictError(ProjectCreateError):
    """Legacy ``bash`` and canonical ``shell`` capabilities disagree.

    Go: ``preset.ErrCapabilityConflict``.  Fail closed rather than silently
    picking a winner — the two objects configure the same capability.
    """


# --- Constants ported from the Go original -------------------------------

# preset.go: DefaultMaxAedAttempts / MinMaxAedAttempts / MaxMaxAedAttempts.
DEFAULT_MAX_AED_ATTEMPTS = 5
MIN_MAX_AED_ATTEMPTS = 1
MAX_MAX_AED_ATTEMPTS = 100

# preset.go: the per-wake loop budget stamped into every generated manifest.
# The kernel treats `max_turns` as recognized-and-ignored
# (init_schema.MANIFEST_LEGACY_IGNORED); it is written anyway so a
# kernel-created init.json stays byte-comparable with a TUI-created one during
# the mixed-fleet transition.
DEFAULT_MAX_TURNS = 500

# capability_alias.go: the legacy shell capability name and its canonical form.
LEGACY_SHELL_CAPABILITY = "bash"
CANONICAL_SHELL_CAPABILITY = "shell"

# config/global.go: the env var the kernel reads for the soul-flow opt-in.
SOUL_FLOW_ENABLED_ENV_VAR = "LINGTAI_SOUL_FLOW_ENABLED"

SUPPORTED_LANGUAGES = ("en", "zh", "wen")

# preset.go: defaultMCPSpec — the curated addon wiring table.  Keep in sync
# with the Go table (and with the m028 migration's addonSpec table) when a new
# curated addon is added.
DEFAULT_MCP_SPECS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "imap": ("lingtai.mcp_servers.imap", "LINGTAI_IMAP_CONFIG", (".secrets", "imap.json")),
    "telegram": ("lingtai.mcp_servers.telegram", "LINGTAI_TELEGRAM_CONFIG", (".secrets", "telegram.json")),
    "feishu": ("lingtai.mcp_servers.feishu", "LINGTAI_FEISHU_CONFIG", (".secrets", "feishu.json")),
    "wechat": ("lingtai.mcp_servers.wechat", "LINGTAI_WECHAT_CONFIG", (".secrets", "wechat", "config.json")),
    "whatsapp": ("lingtai.mcp_servers.whatsapp", "LINGTAI_WHATSAPP_CONFIG", (".secrets", "whatsapp.json")),
}

# config/addon_key.go: addonKeyRe.  A legacy `addons` *object* key is
# interpolated into generated Python import source, so only dotted identifier
# segments may survive into a regenerated init.json.
_ADDON_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

# The in-project path the covenant body is mirrored to.  Declared by
# prompts/covenant/covenant.yaml (`mirror_path: system/covenant.md`) and read
# back by BaseAgent/Agent on every boot.
COVENANT_MIRROR_RELPATH = "system/covenant.md"


def default_global_dir() -> Path:
    """The machine-global LingTai directory (``~/.lingtai-tui`` by default).

    Mirrors what the kernel already assumes elsewhere — ``auth/codex.py`` and
    ``auth/codex_pool.py`` read ``LINGTAI_TUI_DIR`` with the same fallback, and
    ``venv_resolve``/``kernel.presets`` hard-code the same location.  It is the
    source of ``env_file``, ``venv_path``, and the MCP interpreter path stamped
    into a generated ``init.json``.
    """
    return Path(os.environ.get("LINGTAI_TUI_DIR", "~/.lingtai-tui")).expanduser()


# --- Small pure helpers ---------------------------------------------------


def validate_safe_name(name: str) -> None:
    """Reject any name that is not a single, contained path segment.

    Go: ``preset.ValidateSafeName`` (issue #849).  Blank, ``.``, ``..``, and
    both separators (``/`` *and* ``\\``, regardless of platform) are refused, so
    a name that passes is guaranteed to stay a direct child of whatever
    directory it is joined to.
    """
    if not isinstance(name, str) or not name.strip():
        raise ProjectCreateError("must not be blank")
    if name in (".", ".."):
        raise ProjectCreateError(f'must not be "{name}"')
    if "/" in name or "\\" in name:
        raise ProjectCreateError("must not contain a path separator")


def clamp_aed_attempts(n: int) -> int:
    """Normalize a user-supplied AED max-attempts value into range.

    Go: ``preset.ClampAedAttempts``.  A zero value (caller never set it) must
    become the default rather than 0, which the kernel would read as
    "never retry".
    """
    if n < MIN_MAX_AED_ATTEMPTS:
        return DEFAULT_MAX_AED_ATTEMPTS
    if n > MAX_MAX_AED_ATTEMPTS:
        return MAX_MAX_AED_ATTEMPTS
    return n


def _json_structurally_equal(left: Any, right: Any) -> bool:
    """Compare JSON-compatible values without Python's cross-type coercions.

    This is the :mod:`json` equivalent of Go's ``reflect.DeepEqual`` for the
    values that can occur in a preset: maps and lists recurse, while every
    scalar must have the same concrete type.  In particular, ``True`` is not
    ``1``, ``1`` is not ``1.0``, and retained :class:`JSONNumber` lexemes such
    as ``1.0`` and ``1.00`` remain distinct.
    """
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (
            len(left) == len(right)
            and left.keys() == right.keys()
            and all(_json_structurally_equal(value, right[key]) for key, value in left.items())
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_structurally_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def canonicalize_capabilities(caps: Optional[dict]) -> bool:
    """Move a legacy ``bash`` capability onto canonical ``shell``.

    Go: ``preset.CanonicalizeCapabilities``.  Returns whether anything changed.
    A legacy value is never merged with a *different* canonical value — that is
    a fail-closed :class:`CapabilityConflictError` and the input is left
    untouched.  Identical values collapse deterministically onto ``shell``.
    """
    if not isinstance(caps, dict):
        return False
    if LEGACY_SHELL_CAPABILITY not in caps:
        return False
    legacy = caps[LEGACY_SHELL_CAPABILITY]
    has_canonical = CANONICAL_SHELL_CAPABILITY in caps
    if has_canonical and not _json_structurally_equal(
        caps[CANONICAL_SHELL_CAPABILITY], legacy
    ):
        raise CapabilityConflictError(
            f'conflicting capability configuration: "{LEGACY_SHELL_CAPABILITY}" '
            f'and "{CANONICAL_SHELL_CAPABILITY}" differ'
        )
    if not has_canonical:
        caps[CANONICAL_SHELL_CAPABILITY] = legacy
    del caps[LEGACY_SHELL_CAPABILITY]
    return True


def normalize_legacy_capabilities(preset_manifest: dict) -> None:
    """Apply :func:`canonicalize_capabilities` to a preset manifest in place.

    Go: ``(*Preset).NormalizeLegacyCapabilities``.  A non-object
    ``capabilities`` value is left alone for the preset validator to report.
    """
    if not isinstance(preset_manifest, dict):
        return
    caps = preset_manifest.get("capabilities")
    if isinstance(caps, dict):
        canonicalize_capabilities(caps)


def sync_capability_api_key_env(manifest: dict) -> None:
    """Propagate ``manifest.llm.api_key_env`` to same-provider capabilities.

    Go: ``preset.SyncCapabilityAPIKeyEnv``.  Preset templates carry a
    placeholder slot (``ZHIPU_API_KEY``) that the TUI's key stamper rewrites on
    the LLM block only (``ZHIPU_CN_1_API_KEY``); without this, ``web_search`` /
    ``vision`` keep pointing at a slot that does not exist and fail at boot.
    """
    llm = manifest.get("llm")
    if not isinstance(llm, dict):
        return
    llm_provider = llm.get("provider")
    llm_key_env = llm.get("api_key_env")
    if not isinstance(llm_provider, str) or not llm_provider:
        return
    if not isinstance(llm_key_env, str) or not llm_key_env:
        return
    caps = manifest.get("capabilities")
    if not isinstance(caps, dict):
        return
    for cfg in caps.values():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("provider") != llm_provider:
            continue
        cfg["api_key_env"] = llm_key_env


def strip_obsolete_init_fields(init_json: dict) -> None:
    """Drop top-level fields the kernel treats as ignored legacy input.

    Go: ``preset.stripObsoleteInitFields``.  Carrying them forward through a
    read-modify-write triggers a deterministic boot-time nudge before the agent
    can do any useful work.
    """
    init_json.pop("principle_file", None)
    init_json.pop("procedures_file", None)


def _valid_addon_key(name: Any) -> bool:
    """Go: ``config.ValidateAddonKey`` — reduced to a predicate."""
    return isinstance(name, str) and bool(_ADDON_KEY_RE.match(name))


def venv_python(venv_dir: Path) -> Path:
    """Go: ``config.VenvPython`` — the interpreter inside a venv directory."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def preset_ref_for(preset_path: Path | str) -> str:
    """The string a preset is recorded as in ``manifest.preset.*``.

    Go: ``preset.RefFor``, which derives ``~/.lingtai-tui/presets/{templates,
    saved}/<name>.json`` from the preset's *name* plus the directory it was
    loaded from.

    DEVIATION (shape, not behaviour): this CLI is handed a preset **path**, so
    the ref is just that path, home-shortened.  For every preset the TUI
    actually passes (a file under ``~/.lingtai-tui/presets/templates|saved/``)
    the two agree character for character; unlike ``RefFor`` this also works
    for presets kept outside the TUI's library.  The Go "synthetic preset"
    branch (the in-memory ``keep_current`` sentinel built by
    ``NewSetupModeModel``) has no analogue here and is deliberately not ported:
    a path-shaped interface cannot express a preset with no file behind it.
    """
    from lingtai.kernel.presets import home_shortened

    return home_shortened(Path(preset_path))


# --- Options --------------------------------------------------------------


@dataclass
class AgentOpts:
    """Go: ``preset.AgentOpts``; the defaults are ``preset.DefaultAgentOpts``.

    Fields deliberately absent: ``SoulFile`` — the ``soul``/``soul_file``
    mechanism was retired in kernel v0.7.6 in favour of the agent-authored
    ``manifest.soul.voice_prompt``, so the Go field has no live consumer.
    """

    language: str = "en"
    context_limit: int = 300000
    #: ``None`` omits ``manifest.soul.delay`` so the kernel default applies.
    soul_delay: Optional[float] = None
    #: Tri-state, unlike Go's ``bool``.  See :func:`apply_soul_flow_opt_in`.
    soul_flow_enabled: Optional[bool] = None
    max_rpm: int = 60
    max_aed_attempts: int = DEFAULT_MAX_AED_ATTEMPTS
    karma: bool = True
    nirvana: bool = False
    #: Overrides the packaged covenant mirror path in ``init.json``.
    covenant_file: str = ""
    #: Optional operator note file (``comment_file``).
    comment_file: str = ""
    #: Curated MCP addon names to seed into ``addons`` + ``mcp``.
    addons: list[str] = field(default_factory=list)
    #: Preset refs this agent may swap to.  The default ref is always added.
    allowed_presets: list[str] = field(default_factory=list)
    #: ``/setup`` semantics: update ``preset.default`` but leave ``active``.
    preserve_active_preset: bool = False


# --- InitProject ----------------------------------------------------------


def init_project(lingtai_dir: Path) -> None:
    """Scaffold the ``.lingtai/`` skeleton and the human mailbox.

    Go: ``process.InitProject``.  Idempotent by the same test the Go original
    uses — an existing ``human/.agent.json`` short-circuits the whole function,
    so re-running never rewrites a human manifest or contacts list someone has
    since edited.
    """
    lingtai_dir = Path(lingtai_dir)
    lingtai_dir.mkdir(parents=True, exist_ok=True)

    human_dir = lingtai_dir / "human"
    manifest_path = human_dir / ".agent.json"
    if manifest_path.exists():
        return

    for sub in ("mailbox/inbox", "mailbox/sent", "mailbox/archive"):
        (human_dir / sub).mkdir(parents=True, exist_ok=True)

    atomic_write_json(
        manifest_path,
        {"agent_name": "human", "address": "human", "admin": None},
        sort_keys=True,
    )
    # Go writes the two-byte literal "[]"; keep it byte-identical.
    atomic_write_text(human_dir / "mailbox" / "contacts.json", "[]")

    # TUI asset directory — viz data, topology snapshots, NOT agent state.
    # Created here (rather than left to the TUI) because InitProject is what
    # the TUI's own call sites relied on to make the directory exist.
    (lingtai_dir / ".tui-asset").mkdir(parents=True, exist_ok=True)

    # Network-shared library — the collective knowledge base agents reach
    # through the default library.paths entry "../.library_shared" (relative to
    # <agent>/).  Created empty so the library capability does not warn about a
    # missing Tier-1 path on first launch.
    (lingtai_dir / ".library_shared").mkdir(parents=True, exist_ok=True)


# --- Covenant -------------------------------------------------------------


def covenant_body(language: str = "en") -> str:
    """Return the packaged covenant body for *language*.

    The three bodies under :mod:`lingtai.project_assets` are byte-for-byte
    ports of the Go TUI's embedded ``covenant/<lang>/covenant.md``.  An
    unrecognized language falls back to ``en``, matching how the Go path
    behaves once ``Bootstrap`` has only extracted the languages it ships.
    """
    from importlib import resources

    lang = language if language in SUPPORTED_LANGUAGES else "en"
    ref = resources.files("lingtai.project_assets") / "covenant" / lang / "covenant.md"
    return ref.read_text(encoding="utf-8")


def write_covenant(agent_dir: Path, language: str = "en", *, overwrite: bool = False) -> Path:
    """Write the covenant body to ``<agent_dir>/system/covenant.md``.

    That path is the kernel's declared covenant ``mirror_path``: ``Agent``
    reads it back at every boot when ``init.json`` supplies no inline
    ``covenant``, then renders it as the protected ``covenant`` prompt section.

    Existing content is preserved unless *overwrite* is set — the covenant is
    operator-editable once the project exists, and re-running create must not
    silently revert someone's edits.
    """
    target = Path(agent_dir) / COVENANT_MIRROR_RELPATH
    if target.exists() and not overwrite:
        return target
    return atomic_write_text(target, covenant_body(language))


# --- Soul-flow opt-in (global .env) ---------------------------------------


def _read_env_lines(path: Path) -> list[str]:
    """Go: ``config.readEnvLines``.

    A missing file and an empty file are indistinguishable to callers, and the
    empty element a trailing newline would produce is dropped.
    """
    if not path.is_file():
        return []
    content = path.read_text(encoding="utf-8").rstrip("\n")
    if not content:
        return []
    return content.split("\n")


def _parse_env_key(line: str) -> Optional[str]:
    """Go: ``config.parseEnvKey`` — the key of a ``KEY=VALUE`` line, else None.

    Comments and blanks are reported as non-assignments so callers preserve
    them verbatim.  The ``=`` is located in the *raw* line (matching Go), so a
    line that starts with ``=`` has no key.
    """
    trimmed = line.strip()
    if not trimmed or trimmed.startswith("#"):
        return None
    eq = line.find("=")
    if eq <= 0:
        return None
    return line[:eq].strip()


def set_env_var(global_dir: Path, key: str, value: str) -> None:
    """Merge-preserving upsert of a single var in ``<global_dir>/.env``.

    Go: ``config.SetEnvVar``.  Touches exactly one key: comments, blank lines,
    unrelated keys, and file permissions survive.  An empty *value* removes the
    key; removing a key that was never present, from a file that does not
    exist, stays a pure no-op so a default-off agent never materializes a
    spurious empty ``.env``.
    """
    if not key:
        return
    path = Path(global_dir) / ".env"
    existing = _read_env_lines(path)

    out: list[str] = []
    replaced = False
    removed_existing = False
    for line in existing:
        k = _parse_env_key(line)
        if k != key:
            out.append(line)
            continue
        if value == "":
            removed_existing = True
            continue
        if not replaced:
            out.append(f"{key}={value}")
            replaced = True
        # Duplicate lines for the same key are dropped.
    if value != "" and not replaced:
        out.append(f"{key}={value}")

    if value == "" and not removed_existing and not path.exists():
        return

    text = "\n".join(out)
    if text:
        text += "\n"
    is_new = not path.exists()
    atomic_write_text(path, text, preserve_existing_mode=True)
    if is_new:
        # Go's writeEnvLines creates a new .env at 0o600 rather than inheriting
        # the umask — this file holds API keys.  An existing file keeps its own
        # mode (preserve_existing_mode above).
        try:
            path.chmod(0o600)
        except OSError:
            pass


def apply_soul_flow_opt_in(global_dir: Path, enabled: Optional[bool]) -> None:
    """Persist the soul-flow opt-in into the global ``.env``.

    Go: the tail of ``GenerateInitJSONWithOpts``, which writes ``1`` when the
    wizard's toggle is on and *removes* the key when it is off.

    DEVIATION: the flag is tri-state here.  ``None`` (the default) leaves the
    machine-global ``.env`` untouched, because a kernel-side "create a project
    in this directory" command should not silently mutate machine-wide state
    the caller never mentioned — and Go's ``false`` path is a real mutation
    (it deletes an existing key).  A caller that wants byte-identical Go
    behaviour passes an explicit ``True``/``False``.
    """
    if enabled is None:
        return
    set_env_var(Path(global_dir), SOUL_FLOW_ENABLED_ENV_VAR, "1" if enabled else "")


# --- GenerateInitJSONWithOpts --------------------------------------------


def _reject_nonstandard_json_constant(value: str) -> Any:
    """Reject Python's permissive ``NaN``/``Infinity`` JSON extensions."""
    raise ValueError(f"non-standard JSON constant: {value}")


def _read_json_object(path: Path) -> Optional[dict]:
    """Read one strict JSON object, or ``None`` on any read/decode failure.

    Go: ``os.ReadFile`` + ``DecodeJSONUseNumber``.  ``json.loads`` consumes one
    complete document (therefore rejects trailing data); number callbacks retain
    valid integer/exponent lexemes as :class:`JSONNumber`; and ``parse_constant``
    rejects Python's non-standard ``NaN``/``Infinity`` extensions.  The ``None``
    result deliberately drives Go's existing init/.agent parse-failure branches.
    """
    try:
        data = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_int=JSONNumber,
            parse_float=JSONNumber,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _retain_preset_capability_number_tokens(preset_path: Path, preset: dict) -> None:
    """Restore raw capability number lexemes after the normal preset validation.

    The general kernel preset loader intentionally exposes ordinary Python
    numbers to its existing validation/runtime surface.  Project creation needs
    Go ``json.Number`` semantics specifically when it compares the legacy
    ``bash`` and canonical ``shell`` capability objects and then writes them.
    Reparse only that already-validated capability subtree through the shared
    JSONC normalizer so ``1.0`` and ``1.00`` remain distinguishable here without
    widening the loader's public value types.
    """
    from lingtai.kernel.config_resolve import load_jsonc

    raw = load_jsonc(
        preset_path,
        parse_int=JSONNumber,
        parse_float=JSONNumber,
        parse_constant=_reject_nonstandard_json_constant,
    )
    raw_manifest = raw.get("manifest") if isinstance(raw, dict) else None
    raw_caps = raw_manifest.get("capabilities") if isinstance(raw_manifest, dict) else None
    manifest = preset.get("manifest")
    if isinstance(manifest, dict) and isinstance(raw_caps, dict):
        manifest["capabilities"] = raw_caps


def _append_unique(seq: list[str], seen: set[str], value: Any) -> None:
    """Go: the ``appendUnique`` closure — skip blanks and duplicates."""
    if not isinstance(value, str) or not value:
        return
    if value in seen:
        return
    seen.add(value)
    seq.append(value)


def _build_preset_block(
    preset_ref: str,
    existing_init: dict,
    opts: AgentOpts,
) -> dict:
    """Reconcile ``manifest.preset.{active, default, allowed}``.

    Go: the ``if p.Name != ""`` block of ``GenerateInitJSONWithOpts``.

    ``default`` is the chosen preset.  ``active`` is the same preset unless
    ``preserve_active_preset`` (``/setup``) keeps the running agent on what it
    already had.  ``allowed`` is seeded from the caller's explicit list, else
    the existing on-disk list, and always contains ``default``.

    The last rule is the subtle one: ``active`` must also be in ``allowed``
    (the kernel's ``validate_init`` enforces it).  But when the caller listed
    ``allowed`` explicitly and deselected the current ``active``, force-adding
    it back would silently re-authorize a preset the operator just revoked — so
    ``active`` is demoted to ``default`` instead.  With no caller-supplied
    list, ``active`` is simply included: nobody touched the authorization
    surface, so nothing about it changes.
    """
    active_ref = preset_ref
    existing_allowed: list[str] = []

    if opts.preserve_active_preset:
        manifest = existing_init.get("manifest")
        pre = manifest.get("preset") if isinstance(manifest, dict) else None
        if isinstance(pre, dict):
            cur = pre.get("active")
            if isinstance(cur, str) and cur:
                active_ref = cur
            allowed_raw = pre.get("allowed")
            if isinstance(allowed_raw, list):
                existing_allowed = [s for s in allowed_raw if isinstance(s, str) and s]

    allowed: list[str] = []
    seen: set[str] = set()

    user_supplied_allowed = bool(opts.allowed_presets)
    if user_supplied_allowed:
        seed: Iterable[str] = opts.allowed_presets
    elif existing_allowed:
        seed = existing_allowed
    else:
        seed = ()
    for s in seed:
        _append_unique(allowed, seen, s)
    _append_unique(allowed, seen, preset_ref)  # default is always allowed

    if active_ref not in allowed:
        if user_supplied_allowed:
            active_ref = preset_ref
        else:
            _append_unique(allowed, seen, active_ref)

    return {"active": active_ref, "default": preset_ref, "allowed": allowed}


def generate_init_json(
    preset_manifest: dict,
    preset_ref: str,
    agent_name: str,
    dir_name: str,
    lingtai_dir: Path,
    global_dir: Path,
    opts: Optional[AgentOpts] = None,
) -> Path:
    """Write ``<lingtai_dir>/<dir_name>/init.json`` (and ``.agent.json``).

    Go: ``preset.GenerateInitJSONWithOpts``.  This is a stateful
    read-modify-write, not a template stamp: an existing ``init.json`` is read
    first, the fields this function *generates* overwrite it, and every
    unrelated user- or kernel-owned field is carried through untouched.  Same
    for ``.agent.json``, where kernel-owned identity fields (``agent_id``,
    ``molt_count``, ``created_at``, …) must survive a regeneration — without
    that, ``molt_count`` would reset to 0 on every ``/setup`` and psyche would
    overwrite earlier snapshots.

    Args:
        preset_manifest: the chosen preset's ``manifest`` object.  Mutated in
            place by capability canonicalization, exactly as Go mutates its
            ``Preset``.
        preset_ref: what to record in ``manifest.preset.*`` — see
            :func:`preset_ref_for`.  Empty means "write no preset block",
            matching Go's ``if p.Name != ""`` guard.
        agent_name: display name (``manifest.agent_name``).
        dir_name: the agent's directory name under *lingtai_dir*.
        lingtai_dir: the project's ``.lingtai/`` directory.
        global_dir: the machine-global LingTai dir — source of ``env_file``,
            ``venv_path``, and the MCP interpreter path.
        opts: agent options; defaults to :class:`AgentOpts`'s defaults, which
            are Go's ``DefaultAgentOpts``.

    Returns:
        The path of the written ``init.json``.
    """
    opts = opts or AgentOpts()
    lingtai_dir = Path(lingtai_dir)
    global_dir = Path(global_dir)

    # The directory derived from dir_name must remain a single contained child
    # of lingtai_dir: reject absolute paths, parent segments, and either
    # platform's separators before any join or mkdir (issue #849).
    try:
        validate_safe_name(dir_name)
    except ProjectCreateError as exc:
        raise ProjectCreateError(f"invalid agent directory name {dir_name!r}: {exc}") from exc

    normalize_legacy_capabilities(preset_manifest)

    agent_dir = lingtai_dir / dir_name
    agent_dir.mkdir(parents=True, exist_ok=True)

    # Keep the existing root and manifest fields when /setup regenerates an
    # agent.  Known generated fields below overwrite these values; unrelated
    # user/kernel fields remain intact.
    existing_init = _read_json_object(agent_dir / "init.json") or {}

    # --- manifest ---
    manifest: dict[str, Any] = {}
    existing_manifest = existing_init.get("manifest")
    if isinstance(existing_manifest, dict):
        manifest.update(existing_manifest)

    manifest["agent_name"] = agent_name
    lang = opts.language or "en"
    manifest["language"] = lang
    if "llm" in preset_manifest:
        manifest["llm"] = preset_manifest["llm"]
    if "capabilities" in preset_manifest:
        manifest["capabilities"] = preset_manifest["capabilities"]
    sync_capability_api_key_env(manifest)
    manifest["admin"] = {"karma": opts.karma, "nirvana": opts.nirvana}
    if opts.soul_delay is not None:
        manifest["soul"] = {"delay": opts.soul_delay}
    manifest["context_limit"] = opts.context_limit
    # molt_pressure / molt_prompt are intentionally NOT written: the kernel no
    # longer accepts configurable context.molt thresholds or messages.  Stale
    # keys on an existing init.json are left untouched (no migration) — the
    # kernel ignores them.
    manifest["max_turns"] = DEFAULT_MAX_TURNS
    manifest["max_rpm"] = opts.max_rpm
    # Normalize through clamp_aed_attempts so a zero-value opts (caller never
    # set it) still writes a valid default rather than 0, which the kernel
    # would read as "never retry".
    manifest["max_aed_attempts"] = clamp_aed_attempts(opts.max_aed_attempts)
    manifest["streaming"] = False

    if preset_ref:
        manifest["preset"] = _build_preset_block(preset_ref, existing_init, opts)

    # --- top-level fields ---
    # Keep an existing covenant path during /setup; a fresh agent gets the
    # in-project mirror.  Obsolete principle/procedures paths are neither
    # resolved nor emitted.
    covenant_file = opts.covenant_file
    if not covenant_file:
        existing_covenant = existing_init.get("covenant_file")
        if isinstance(existing_covenant, str) and existing_covenant:
            covenant_file = existing_covenant
        else:
            # DEVIATION: Go points covenant_file at the machine-global
            # <globalDir>/covenant/<lang>/covenant.md that Bootstrap extracts.
            # The kernel now packages the covenant itself and writes it into
            # the project, so the pointer is the in-project mirror instead —
            # relative, hence portable across machines (resolve_paths re-roots
            # it against the agent working dir at boot) and identical to the
            # path Agent falls back to anyway.
            covenant_file = COVENANT_MIRROR_RELPATH

    # Load existing addons + mcp so they survive a regen.  Critical for
    # /setup: changing an unrelated setting must not drop addon registrations
    # or MCP activations.  User edits always win over opts.addons, which only
    # seeds the fields on first creation.
    #
    # Both shapes are read for back-compat: pre-v0.7.3 TUIs wrote a dict, new
    # ones write a list.  Either way the file is normalized to the list form.
    existing_addons_list: Optional[list] = None
    raw_addons = existing_init.get("addons")
    if isinstance(raw_addons, list):
        existing_addons_list = list(raw_addons)
    elif isinstance(raw_addons, dict):
        # Legacy dict shape — extract just the names, applying the same
        # identifier validation as the launch-time addon check so a malformed
        # or untrusted key can never be carried into a regenerated init.json.
        names = [name for name in raw_addons if _valid_addon_key(name)]
        if names:
            existing_addons_list = names

    existing_mcp = existing_init.get("mcp")
    if not isinstance(existing_mcp, dict) or not existing_mcp:
        existing_mcp = None

    env_file = str(global_dir / ".env")
    existing_env = existing_init.get("env_file")
    if isinstance(existing_env, str) and existing_env:
        env_file = existing_env

    venv_dir = global_dir / "runtime" / "venv"
    venv_path = str(venv_dir)
    existing_venv = existing_init.get("venv_path")
    if isinstance(existing_venv, str) and existing_venv:
        venv_path = existing_venv

    pad: Any = ""
    if "pad" in existing_init:
        pad = existing_init["pad"]

    init_json: dict[str, Any] = dict(existing_init)
    strip_obsolete_init_fields(init_json)
    init_json["manifest"] = manifest
    init_json["covenant_file"] = covenant_file
    init_json["env_file"] = env_file
    init_json["venv_path"] = venv_path
    init_json["pad"] = pad
    # No seed-character field is written.  灵台 (character) is durable state
    # owned by the agent and authored after creation via system/lingtai.md /
    # psyche — the kernel treats a missing seed as an empty seed.  The legacy
    # `prompt` field was an unknown key (boot warning, never honored); neither
    # `prompt` nor `lingtai` is emitted.

    # Which addons to wire.  Precedence: a pre-existing addons list in
    # init.json (preserved verbatim — user edits win), else opts.addons.
    addons_list: Optional[list] = None
    if existing_addons_list is not None:
        addons_list = existing_addons_list
    elif opts.addons:
        addons_list = list(opts.addons)
    if addons_list is not None:
        init_json["addons"] = addons_list

    # Build the mcp activation map for every addon name in the list.  Each
    # entry points at the local venv python (where `pip install lingtai` put
    # the MCP packages) running `python -m lingtai.mcp_servers.<name>` with the
    # canonical LINGTAI_<NAME>_CONFIG env var set to the .secrets/<name>.json
    # convention.  Pre-existing mcp.<name> entries win — someone who switched
    # to a different Python or added env vars keeps their settings.
    if addons_list:
        python_exe = str(venv_python(global_dir / "runtime" / "venv"))
        mcp_field: dict[str, Any] = dict(existing_mcp or {})
        for raw in addons_list:
            if not isinstance(raw, str) or not raw:
                continue
            if raw in mcp_field:
                continue  # user-set entry wins
            spec = DEFAULT_MCP_SPECS.get(raw)
            if spec is None:
                continue  # unknown name — let the kernel surface the warning
            module, env_var, config_rel = spec
            mcp_field[raw] = {
                "type": "stdio",
                "command": python_exe,
                "args": ["-m", module],
                "env": {env_var: os.path.join(*config_rel)},
            }
        if mcp_field:
            init_json["mcp"] = mcp_field

    # Comment file — only if the caller specified one.
    if opts.comment_file:
        init_json["comment_file"] = opts.comment_file

    init_path = agent_dir / "init.json"
    # sort_keys mirrors Go's encoding/json, which sorts map keys on marshal.
    # Keeping it means a kernel-created init.json is byte-comparable with a
    # TUI-created one for the same inputs during the mixed-fleet transition.
    atomic_write_json(
        init_path,
        init_json,
        sort_keys=True,
        preserve_existing_mode=True,
        preserve_number_tokens=True,
    )

    _write_agent_manifest(agent_dir, agent_name, opts)
    return init_path


def _write_agent_manifest(agent_dir: Path, agent_name: str, opts: AgentOpts) -> Path:
    """Write the wizard-controlled subset of ``<agent_dir>/.agent.json``.

    Go: the ``agentManifest`` tail of ``GenerateInitJSONWithOpts``.  Fields the
    kernel populates at runtime (``agent_id``, ``created_at``, ``molt_count``,
    ``language``, ``soul_delay``, ``soul_voice``, ``started_at``,
    ``capabilities``, ``nickname``, …) must NOT be touched: regenerating an
    existing agent has to preserve its identity and history.
    """
    agent_manifest = {
        "agent_name": agent_name,
        "address": agent_dir.name,
        "admin": {"karma": opts.karma, "nirvana": opts.nirvana},
    }

    for sub in ("mailbox/inbox", "mailbox/sent", "mailbox/archive"):
        (agent_dir / sub).mkdir(parents=True, exist_ok=True)

    agent_json_path = agent_dir / ".agent.json"
    merged: dict[str, Any] = dict(agent_manifest)
    if agent_json_path.exists():
        prev = _read_json_object(agent_json_path)
        if prev is not None:
            # Start from prev, then overwrite the keys this function owns.
            merged = prev
            merged.update(agent_manifest)
    else:
        # Fresh agent — initialize state to "" so the kernel sees a blank.
        # (Go keys this off the *read* failing, so an existing-but-unparseable
        # .agent.json gets no `state` either; matched here via .exists().)
        merged["state"] = ""

    return atomic_write_json(
        agent_json_path,
        merged,
        sort_keys=True,
        preserve_existing_mode=True,
        preserve_number_tokens=True,
    )


# --- Orchestrator ---------------------------------------------------------


def create_project(
    project_root: Path,
    agent_name: str,
    preset_path: Path | str,
    *,
    dir_name: Optional[str] = None,
    global_dir: Optional[Path] = None,
    opts: Optional[AgentOpts] = None,
) -> dict[str, Any]:
    """Create a LingTai project at *project_root* with one agent.

    Composes the three primitives this slice owns, in the order the Go headless
    spawn path uses them:

    1. :func:`init_project` — ``<project_root>/.lingtai/`` + human mailbox.
    2. :func:`generate_init_json` — the agent's ``init.json`` / ``.agent.json``.
    3. :func:`write_covenant` — the ``system/covenant.md`` mirror.

    Deliberately NOT done here (see the module docstring and the design doc):
    recipe/skills injection, ``procedures``/``principle``/``soul`` population,
    global asset bootstrap, project-registry registration, and the interactive
    wizard's staging-dir + atomic-rename commit policy.

    Returns a small result dict — ``project_root``, ``lingtai_dir``,
    ``agent_dir``, ``init_json``, ``covenant_file``, ``preset_ref`` — so the CLI
    can render it as JSON for scripted callers.
    """
    from lingtai.kernel.presets import load_preset

    opts = opts or AgentOpts()
    if opts.soul_delay is not None and not math.isfinite(opts.soul_delay):
        raise ProjectCreateError("soul_delay must be a finite number")

    project_root = Path(project_root).expanduser()
    global_dir = Path(global_dir) if global_dir is not None else default_global_dir()
    dir_name = dir_name or agent_name

    validate_safe_name(agent_name)
    validate_safe_name(dir_name)

    resolved_preset = Path(preset_path).expanduser()
    if not resolved_preset.is_absolute():
        resolved_preset = (Path.cwd() / resolved_preset).resolve()
    # Validate the preset through the kernel's own loader.  DEVIATION: Go does
    # no preset validation at this point; failing here instead turns a
    # guaranteed boot failure into an immediate, actionable creation error.
    try:
        preset = load_preset(str(resolved_preset), run_migrations=lambda _p: None)
        _retain_preset_capability_number_tokens(resolved_preset, preset)
    except (KeyError, ValueError) as exc:
        raise ProjectCreateError(f"preset: {exc}") from exc

    # Reject an ambiguous legacy/canonical capability collision before
    # init_project can create any output, matching the Go fail-closed path.
    normalize_legacy_capabilities(preset.get("manifest", {}))

    lingtai_dir = project_root / ".lingtai"
    init_project(lingtai_dir)

    init_path = generate_init_json(
        preset.get("manifest", {}),
        preset_ref_for(resolved_preset),
        agent_name,
        dir_name,
        lingtai_dir,
        global_dir,
        opts,
    )

    agent_dir = lingtai_dir / dir_name
    covenant_path = write_covenant(agent_dir, opts.language or "en")
    apply_soul_flow_opt_in(global_dir, opts.soul_flow_enabled)

    return {
        "project_root": str(project_root),
        "lingtai_dir": str(lingtai_dir),
        "agent_dir": str(agent_dir),
        "init_json": str(init_path),
        "covenant_file": str(covenant_path),
        "preset_ref": preset_ref_for(resolved_preset),
    }
