"""CLI composition for one fresh local Project seed."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from lingtai.adapters.project_workspace import FilesystemProjectWorkspaceAdapter
from lingtai.kernel.project import (
    ProjectCreateRequest,
    ProjectCreationError,
    ProjectCreationUseCase,
    ProjectError,
)


def add_project_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    project = subparsers.add_parser("project", help="Create a fresh local Project seed")
    commands = project.add_subparsers(dest="project_command", required=True)
    create = commands.add_parser("create", help="Create one Project with one initial agent")
    create.add_argument("--dir", required=True, dest="project_dir", help="Existing project root directory")
    create.add_argument("--name", required=True, dest="agent_name", help="Initial agent name")
    create.add_argument("--preset", required=True, help="Preset JSON/JSONC reference")
    create.add_argument("--covenant-file", required=True, dest="covenant_file", help="UTF-8 caller covenant")
    create.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON result or error")

    rename = commands.add_parser(
        "rename",
        help="Rename one POSIX agent workdir/address after a proved stop and restart",
    )
    rename.add_argument("--agent-dir", required=True, help="Current canonical absolute agent workdir")
    rename.add_argument("--new-address", required=True, help="New safe one-segment workdir basename/address")
    rename.add_argument("--timeout", type=float, default=30.0, help="Positive seconds for stop and restart proof")
    rename.add_argument(
        "--no-known-external-writers",
        action="store_true",
        required=True,
        help="Confirm separately launched MCP/LICC or other old-path writers are stopped",
    )


def _error(code: str, message: str) -> ProjectCreationError:
    return ProjectCreationError(ProjectError(code, message))


def _read_covenant(path: str) -> str:
    try:
        value = Path(path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _error("covenant_file_unreadable", "covenant file must be readable UTF-8 text") from exc
    if not value:
        raise _error("covenant_file_empty", "covenant file must contain caller-supplied text")
    return value


def _request(args: argparse.Namespace) -> ProjectCreateRequest:
    from lingtai.agent import load_preset
    from lingtai.tools.psyche.settings import serialize_prompt_owner_document

    preset_ref = str(Path(args.preset).expanduser().resolve())
    try:
        preset = load_preset(preset_ref)
    except (KeyError, ValueError, OSError) as exc:
        raise _error("invalid_preset", "preset could not be loaded") from exc
    manifest = preset.get("manifest") if isinstance(preset, dict) else None
    if not isinstance(manifest, dict):
        raise _error("invalid_preset", "preset manifest is invalid")
    llm = manifest.get("llm")
    capabilities = manifest.get("capabilities", {})
    if not isinstance(llm, dict) or not isinstance(capabilities, dict):
        raise _error("invalid_preset", "preset LLM and capabilities must be objects")
    llm = dict(llm)
    # Preset-local context-fit metadata must not become a new init runtime input.
    llm.pop("context_limit", None)
    return ProjectCreateRequest(
        agent_name=args.agent_name,
        preset_ref=preset_ref,
        llm=llm,
        capabilities=dict(capabilities),
        psyche_settings_json=serialize_prompt_owner_document(
            covenant=_read_covenant(args.covenant_file)
        ),
    )


def _validate_agent(agent_dir: Path) -> None:
    from lingtai.agent import load_preset
    from lingtai.init_reader import InitReadStatus, read_init, reader_callbacks

    materialize, prepare = reader_callbacks(agent_dir, load_preset=load_preset)
    outcome = read_init(agent_dir, materialize=materialize, prepare=prepare, failure_behavior="STOP")
    if outcome.status is InitReadStatus.READ_FAILED:
        raise _error("init_preflight_failed", "generated init could not be read")
    try:
        from lingtai.tools.psyche.settings import read_resolved_prompt_inputs

        read_resolved_prompt_inputs(agent_dir)
    except Exception as exc:
        raise _error(
            "psyche_preflight_failed",
            "generated Psyche settings could not be read",
        ) from exc


def _emit_error(error: ProjectError, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"status": "error", "code": error.code, "error": error.message}, sort_keys=True), file=sys.stderr)
    else:
        print(f"error[{error.code}]: {error.message}", file=sys.stderr)


def _rename_helper_path() -> Path:
    return (
        Path(__file__).parent
        / "intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py"
    )


def _handle_rename_command(args: argparse.Namespace) -> None:
    if args.timeout <= 0:
        _emit_error(ProjectError("invalid_timeout", "timeout must be positive"), as_json=False)
        raise SystemExit(1)
    helper = _rename_helper_path()
    if not helper.is_file():
        _emit_error(
            ProjectError("rename_helper_unavailable", "address rename helper is not installed"),
            as_json=False,
        )
        raise SystemExit(1)
    command = [
        sys.executable,
        str(helper),
        str(Path(args.agent_dir).expanduser()),
        args.new_address,
        "--foreground",
        "--timeout",
        str(args.timeout),
        "--no-known-external-writers",
    ]
    try:
        completed = subprocess.run(command, check=False)
    except OSError:
        _emit_error(
            ProjectError(
                "agent_rename_outcome_unknown",
                "rename helper could not start; inspect both old and requested paths before any retry",
            ),
            as_json=False,
        )
        raise SystemExit(1) from None
    if completed.returncode != 0:
        _emit_error(
            ProjectError(
                "agent_rename_failed",
                "rename did not prove success; follow helper recovery guidance and inspect the retained path",
            ),
            as_json=False,
        )
        raise SystemExit(1)


def handle_project_command(args: argparse.Namespace) -> None:
    if args.project_command == "rename":
        _handle_rename_command(args)
        return
    if args.project_command != "create":
        raise SystemExit(2)
    try:
        root = Path(args.project_dir).expanduser().resolve()
        result = ProjectCreationUseCase(
            FilesystemProjectWorkspaceAdapter(root, validate_agent=_validate_agent)
        ).create(_request(args))
    except ProjectCreationError as exc:
        _emit_error(exc.error, as_json=args.as_json)
        raise SystemExit(1) from None
    except Exception:
        _emit_error(ProjectError("project_create_failed", "project creation could not be completed"), as_json=args.as_json)
        raise SystemExit(1) from None

    payload = result.to_payload()
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Project created for agent {payload['agent_name']}.")


__all__ = ["add_project_parser", "handle_project_command"]
