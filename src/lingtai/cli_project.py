"""``lingtai-agent project create`` — the CLI skin over kernel project creation.

The semantics live in :mod:`lingtai.kernel.project_create`; this module only
parses flags, maps them onto :class:`~lingtai.kernel.project_create.AgentOpts`,
and prints a machine-readable result.  Same split as ``cli_daemon.py``: a
``add_*_parser`` registered on the root ``lingtai-agent`` subparsers plus a
``handle_*_command`` dispatcher that turns a refusal into exit code 1.

Scope is Slice 1 of the kernel-owned project-creation migration: scaffold
``.lingtai/``, write ``init.json``/``.agent.json``, seed the covenant mirror.
Recipe/skills injection, global asset bootstrap, project-registry
registration, and the interactive wizard's staging + atomic-rename commit
policy are explicitly *not* here — they remain the caller's (the TUI's).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from lingtai.kernel.project_create import (
    AgentOpts,
    ProjectCreateError,
    SUPPORTED_LANGUAGES,
    create_project,
    default_global_dir,
)

__all__ = ["add_project_parser", "handle_project_command"]


def _finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be a finite number")
    return parsed


def add_project_parser(sub: "argparse._SubParsersAction") -> None:
    """Register the ``project`` subcommand tree on the root parser."""
    project_parser = sub.add_parser(
        "project",
        help="Create and inspect LingTai project directories",
    )
    project_sub = project_parser.add_subparsers(dest="project_command", required=True)

    create = project_sub.add_parser(
        "create",
        help="Scaffold a .lingtai/ project with one agent from a preset",
    )
    create.add_argument(
        "--name",
        required=True,
        help="Agent name; also the agent's directory name under .lingtai/",
    )
    create.add_argument(
        "--dir",
        dest="project_dir",
        type=Path,
        required=True,
        help="Project root — .lingtai/ is created inside it",
    )
    create.add_argument(
        "--preset",
        type=Path,
        required=True,
        help="Path to the preset .json/.jsonc the agent is created from",
    )
    create.add_argument(
        "--dir-name",
        default=None,
        help="Agent directory name when it must differ from --name",
    )
    create.add_argument(
        "--global-dir",
        type=Path,
        default=None,
        help="Machine-global LingTai dir (default: $LINGTAI_TUI_DIR or ~/.lingtai-tui); "
             "source of env_file, venv_path, and the MCP interpreter path",
    )
    create.add_argument(
        "--language",
        choices=list(SUPPORTED_LANGUAGES),
        default="en",
        help="Agent language; also selects the covenant body (default: en)",
    )
    create.add_argument(
        "--context-limit",
        type=int,
        default=AgentOpts.context_limit,
        help=f"manifest.context_limit token budget (default: {AgentOpts.context_limit})",
    )
    create.add_argument(
        "--max-rpm",
        type=int,
        default=AgentOpts.max_rpm,
        help=f"Cooperative requests-per-minute cap; 0 disables (default: {AgentOpts.max_rpm})",
    )
    create.add_argument(
        "--max-aed-attempts",
        type=int,
        default=AgentOpts.max_aed_attempts,
        help=f"AED retry attempts per message turn (default: {AgentOpts.max_aed_attempts})",
    )
    create.add_argument(
        "--soul-delay",
        type=_finite_float,
        default=None,
        help="manifest.soul.delay in seconds; omit to let the kernel default apply",
    )
    create.add_argument(
        "--no-karma",
        dest="karma",
        action="store_false",
        default=True,
        help="Withhold lifecycle control over other agents (admin.karma)",
    )
    create.add_argument(
        "--nirvana",
        action="store_true",
        default=False,
        help="Grant permanent agent destruction (admin.nirvana)",
    )
    create.add_argument(
        "--addon",
        dest="addons",
        action="append",
        default=[],
        metavar="NAME",
        help="Curated MCP addon to seed into addons + mcp (repeatable): "
             "imap, telegram, feishu, wechat, whatsapp",
    )
    create.add_argument(
        "--allowed-preset",
        dest="allowed_presets",
        action="append",
        default=[],
        metavar="REF",
        help="Preset path the agent may swap to at runtime (repeatable). "
             "The chosen preset is always included",
    )
    create.add_argument(
        "--preserve-active-preset",
        action="store_true",
        default=False,
        help="Update manifest.preset.default but leave .active alone "
             "(the /setup semantics — a running agent is not yanked mid-conversation)",
    )
    create.add_argument(
        "--covenant-file",
        default="",
        help="Override init.json's covenant_file instead of pointing at the "
             "in-project system/covenant.md mirror",
    )
    create.add_argument(
        "--comment-file",
        default="",
        help="Operator note file recorded as init.json's comment_file",
    )
    create.add_argument(
        "--soul-flow",
        choices=["on", "off"],
        default=None,
        help="Write or remove LINGTAI_SOUL_FLOW_ENABLED in <global-dir>/.env. "
             "Omitted (default), the machine-global .env is left untouched",
    )
    create.add_argument(
        "--json",
        action="store_true",
        help="Emit the created paths as JSON instead of a human summary",
    )


def _opts_from_args(args) -> AgentOpts:
    soul_flow = None
    if args.soul_flow is not None:
        soul_flow = args.soul_flow == "on"
    return AgentOpts(
        language=args.language,
        context_limit=args.context_limit,
        soul_delay=args.soul_delay,
        soul_flow_enabled=soul_flow,
        max_rpm=args.max_rpm,
        max_aed_attempts=args.max_aed_attempts,
        karma=args.karma,
        nirvana=args.nirvana,
        covenant_file=args.covenant_file,
        comment_file=args.comment_file,
        addons=list(args.addons or []),
        allowed_presets=list(args.allowed_presets or []),
        preserve_active_preset=args.preserve_active_preset,
    )


def _handle_create(args) -> int:
    result = create_project(
        args.project_dir,
        args.name,
        args.preset,
        dir_name=args.dir_name,
        global_dir=args.global_dir if args.global_dir is not None else default_global_dir(),
        opts=_opts_from_args(args),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    print(f"created agent {args.name!r}")
    print(f"  project root : {result['project_root']}")
    print(f"  agent dir    : {result['agent_dir']}")
    print(f"  init.json    : {result['init_json']}")
    print(f"  covenant     : {result['covenant_file']}")
    print(f"  preset ref   : {result['preset_ref']}")
    return 0


_HANDLERS = {"create": _handle_create}


def handle_project_command(args) -> None:
    """Run one ``project`` subcommand, exiting non-zero on refusal."""
    handler = _HANDLERS.get(getattr(args, "project_command", None))
    if handler is None:
        print("error: missing project subcommand", file=sys.stderr)
        sys.exit(1)
    try:
        code = handler(args)
    except ProjectCreateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if code:
        sys.exit(code)
