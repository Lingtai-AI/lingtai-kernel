"""JSON stdin driver/composition for existing Project operations (no flags)."""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import TextIO

from lingtai.kernel.project import ProjectCreationError
from lingtai.kernel.project.configuration import (
    ConfigurationOptions, ConfigurationResult, ConfigureMemberRequest,
    ProjectConfigurationUseCase, configuration_error, safe_member_name,
)

OPERATIONS = ("initialize", "configure", "propagate-policy", "propagate-config", "rehydrate")


def _closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate")
        value[key] = item
    return value


def read_request(stream: TextIO) -> dict[str, object]:
    try:
        text = stream.read(1024 * 1024 + 1)
        if len(text) > 1024 * 1024:
            raise ValueError("oversized")
        value = json.loads(text, object_pairs_hook=_closed_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if not isinstance(value, dict):
            raise ValueError("object required")
        # This rejects credential-shaped keys, not secrets hidden in arbitrary text.
        # The Go caller must send an explicit public projection, never its whole draft.
        from lingtai.kernel.workdir import _is_secret_key
        def public(item: object) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    if _is_secret_key(key) and not (
                        key.endswith("_env") and isinstance(child, str)
                        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", child)
                    ):
                        raise configuration_error("credentials_must_remain_outer")
                    public(child)
            elif isinstance(item, list):
                for child in item:
                    public(child)
        public(value)
        return value
    except ProjectCreationError:
        raise
    except Exception:
        raise configuration_error("invalid_project_request") from None


def _keys(value: dict, required: set[str], optional: set[str] = frozenset()) -> None:
    if not required <= value.keys() or value.keys() - required - optional:
        raise configuration_error("invalid_project_request")


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise configuration_error("invalid_project_request")
    return tuple(value)


def _normalize_manifest(manifest: dict) -> dict:
    from lingtai.tools.registry import normalize_capabilities
    result = copy.deepcopy(manifest)
    if "capabilities" in result:
        if not isinstance(result["capabilities"], dict):
            raise configuration_error("invalid_preset")
        try:
            result["capabilities"] = normalize_capabilities(result["capabilities"])
        except (TypeError, ValueError):
            raise configuration_error("invalid_preset") from None
    return result


def _configure_request(value: dict, workspace) -> ConfigureMemberRequest:
    _keys(value, {"project_dir", "directory_name", "agent_name", "preset_ref", "keep_current", "options"}, {"prompts"})
    for key in ("directory_name", "agent_name", "preset_ref"):
        if not isinstance(value[key], str):
            raise configuration_error("invalid_project_request")
    if not safe_member_name(value["directory_name"]) or type(value["keep_current"]) is not bool:
        raise configuration_error("invalid_project_request")
    options = value["options"]
    if not isinstance(options, dict):
        raise configuration_error("invalid_project_request")
    text_keys = {"language", "env_file", "venv_path", "pad", "covenant_file", "default_covenant_file"}
    bool_keys = {"karma", "nirvana", "preserve_active"}
    int_keys = {"context_limit", "max_rpm", "max_aed_attempts"}
    _keys(options, {"env_file", "venv_path"}, text_keys | bool_keys | int_keys | {"addons", "mcp", "allowed_presets"})
    for key in text_keys & options.keys():
        if not isinstance(options[key], str):
            raise configuration_error("invalid_project_request")
    for key in bool_keys & options.keys():
        if type(options[key]) is not bool:
            raise configuration_error("invalid_project_request")
    for key in int_keys & options.keys():
        if type(options[key]) is not int:
            raise configuration_error("invalid_project_request")
    if "mcp" in options and not isinstance(options["mcp"], dict):
        raise configuration_error("invalid_project_request")
    parsed = dict(options)
    for key in ("addons", "allowed_presets"):
        if key in parsed:
            parsed[key] = _strings(parsed[key])
    opts = ConfigurationOptions(**parsed)
    if value["keep_current"]:
        if value["preset_ref"] or not opts.preserve_active:
            raise configuration_error("invalid_project_request")
        prior = workspace.observe_member(value["directory_name"])
        from lingtai.init_reader import reader_callbacks
        from lingtai.agent import load_preset
        from lingtai.kernel.config_resolve import normalize_init
        candidate = normalize_init(prior.init_document or {})
        materialize, prepare = reader_callbacks(workspace._member(value["directory_name"]), load_preset=load_preset)
        materialize(candidate)
        prepare(candidate)
        manifest = candidate.get("manifest")
    else:
        from lingtai.agent import load_preset
        ref = Path(value["preset_ref"]).expanduser()
        if not ref.is_absolute() or ref.suffix not in {".json", ".jsonc"}:
            raise configuration_error("invalid_preset")
        manifest = load_preset(str(ref)).get("manifest")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("llm"), dict):
        raise configuration_error("invalid_preset")
    # Preset references retain caller spelling, including ~/ shorthand.
    manifest = _normalize_manifest(manifest)
    psyche = None
    if "prompts" in value:
        from lingtai.tools.psyche.settings import read_prompt_owner_values, serialize_prompt_owner_document
        prompts = value["prompts"]
        if not isinstance(prompts, dict):
            raise configuration_error("invalid_project_request")
        _keys(prompts, set(), {"base_prompt", "base_prompt_file", "covenant", "covenant_file"})
        if not all(isinstance(v, str) for v in prompts.values()):
            raise configuration_error("invalid_project_request")
        # Omission preserves existing owner bytes. Explicit edits merge with the
        # owner's public reader/writer, never a duplicate schema in Project Core.
        owner = read_prompt_owner_values(workspace._member(value["directory_name"]))
        owner.update(prompts)
        psyche = serialize_prompt_owner_document(**owner)
    return ConfigureMemberRequest(value["directory_name"], value["agent_name"], value["preset_ref"],
                                  value["keep_current"], manifest, opts, psyche)


def execute(operation: str, value: dict) -> ConfigurationResult:
    from lingtai.agent import load_preset
    from lingtai.adapters.project_configuration import FilesystemProjectConfigurationAdapter

    if not isinstance(value.get("project_dir"), str) or not value["project_dir"]:
        raise configuration_error("invalid_project_request")
    root = Path(value["project_dir"]).expanduser().resolve()
    workspace = FilesystemProjectConfigurationAdapter(root, load_preset=load_preset)
    usecase = ProjectConfigurationUseCase(workspace)
    if operation == "initialize":
        _keys(value, {"project_dir"})
        return usecase.initialize()
    if operation in {"configure", "rehydrate"}:
        request = _configure_request(value, workspace)
        return usecase.rehydrate(request) if operation == "rehydrate" else usecase.configure(request)
    if operation == "propagate-policy":
        _keys(value, {"project_dir", "directory_name", "default_ref", "allowed_presets"})
        if not isinstance(value["directory_name"], str) or not isinstance(value["default_ref"], str):
            raise configuration_error("invalid_project_request")
        return usecase.propagate_policy(value["directory_name"], value["default_ref"], _strings(value["allowed_presets"]))
    if operation == "propagate-config":
        _keys(value, {"project_dir", "directory_name"})
        if not isinstance(value["directory_name"], str):
            raise configuration_error("invalid_project_request")
        # The real source owner normalizer is outside Core. Only the selected
        # source is normalized; target documents keep unrelated fields intact.
        workspace.normalize_source = value["directory_name"]
        return usecase.propagate_config(value["directory_name"])
    raise configuration_error("invalid_project_request")


def handle_configuration_command(operation: str) -> None:
    import logging
    previous_disable = logging.root.manager.disable
    try:
        # Current readers may log arbitrary unknown field names. This machine
        # protocol emits only static errors/counts, never those diagnostic logs.
        logging.disable(logging.CRITICAL)
        result = execute(operation, read_request(sys.stdin))
    except ProjectCreationError as exc:
        payload = ConfigurationResult(operation, "rejected", code=exc.error.code).to_payload()
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        payload = ConfigurationResult(operation, "unknown", code="configuration_request_failed").to_payload()
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        logging.disable(previous_disable)
    print(json.dumps(result.to_payload(), sort_keys=True), file=sys.stderr if result.code else sys.stdout)
    if result.code:
        raise SystemExit(1)
