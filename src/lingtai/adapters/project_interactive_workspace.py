"""Concrete hidden Project workspace, current owner writers/readers and publication."""
from __future__ import annotations

import copy
import ctypes
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from lingtai.kernel.project.interactive_creation import (
    CreationAttempt, InteractiveCreateRequest, InteractiveWorkspacePort, InteractiveProjectSeed,
    creation_error, validate_staged_members,
)

_MARKER = ".lingtai-launcher-staging"


def _exclusive_rename(source: Path, target: Path) -> None:
    """Never replace an existing destination, including an empty directory."""
    if sys.platform == "win32":
        # Native Windows rename refuses any existing destination.
        os.rename(source, target)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(target), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                           ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)  # NOREPLACE
    else:
        raise creation_error("exclusive_publication_unsupported")
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, "exclusive Project publication failed")


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _preset_manifest(request: InteractiveCreateRequest) -> dict[str, object]:
    manifest = copy.deepcopy(request.preset_manifest)
    llm = manifest.get("llm")
    if not isinstance(llm, dict) or not isinstance(manifest.get("capabilities", {}), dict):
        raise creation_error("invalid_preset")
    llm.pop("context_limit", None)
    llm.pop("api_compat", None)
    return manifest


class FilesystemInteractiveWorkspaceAdapter(InteractiveWorkspacePort):
    def __init__(self, root: Path, *, load_preset: Callable[..., dict],
                 serialize_psyche: Callable[..., str],
                 publish_directory: Callable[[Path, Path], None] = _exclusive_rename) -> None:
        self.root = root
        self.load_preset = load_preset
        self.serialize_psyche = serialize_psyche
        self.publish_directory = publish_directory
        self._stage: Path | None = None
        self._attempt: CreationAttempt | None = None
        self._identity: tuple[int, int] | None = None
        self._published = False

    def preflight(self, request: InteractiveCreateRequest) -> None:
        from lingtai.init_schema import validate_init

        requested, approved = Path(request.project_dir), Path(request.approved_root)
        if (not requested.is_absolute() or not approved.is_absolute()
                or requested.resolve() != approved.resolve() or requested.resolve() != self.root
                or not self.root.is_dir()):
            raise creation_error("invalid_project_root")
        if os.path.lexists(self.root / ".lingtai"):
            raise creation_error("already_initialized")
        ref = Path(request.preset_ref).expanduser()
        if not ref.is_absolute() or ref.suffix not in {".json", ".jsonc"}:
            raise creation_error("invalid_preset")
        manifest = _preset_manifest(request)
        # Honor the same explicit env-file input as the current seed writer;
        # never synthesize one or bypass the owner's conditional requirement.
        from lingtai.kernel.config_resolve import normalize_init
        pending = normalize_init({"llm": manifest["llm"], "tools": {}, "mcp": request.options.mcp or {}})
        if request.options.env_file:
            pending["env_file"] = request.options.env_file
        # Validate pending shape without pretending the preset or credentials exist.
        try:
            validate_init(pending)
        except ValueError:
            raise creation_error("init_preflight_failed") from None
        self.serialize_psyche(covenant=request.options.covenant,
                              base_prompt=request.options.base_prompt)

    def begin(self) -> CreationAttempt:
        if self._stage is not None:
            raise creation_error("creation_attempt_already_used")
        self._stage = Path(tempfile.mkdtemp(prefix=".lingtai.create-", dir=self.root))
        try:
            info = self._stage.lstat()
            self._identity = (info.st_dev, info.st_ino)
            self._attempt = CreationAttempt(uuid.uuid4().hex)
            marker = self._stage / _MARKER
            with marker.open("x", encoding="utf-8") as stream:
                stream.write(self._attempt.attempt_id)
            marker.chmod(0o600)
        except Exception:
            # No marker proof: retain this directory, never blind-remove by prefix.
            raise creation_error("owned_staging_cleanup_unresolved") from None
        return self._attempt

    def owned_stage(self, attempt: CreationAttempt) -> Path:
        if self._published or attempt != self._attempt or self._stage is None:
            raise creation_error("staging_ownership_unresolved")
        info = self._stage.lstat()
        marker = self._stage / _MARKER
        if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._identity
                or not stat.S_ISREG(marker.lstat().st_mode)
                or marker.read_text(encoding="utf-8") != attempt.attempt_id):
            raise creation_error("staging_ownership_unresolved")
        return self._stage

    def build(self, attempt: CreationAttempt, seed: InteractiveProjectSeed) -> None:
        stage = self.owned_stage(attempt)
        for directory in (stage / "human", stage / seed.directory_name):
            for mailbox in ("inbox", "outbox", "sent", "archive", "schedules"):
                (directory / "mailbox" / mailbox).mkdir(parents=True)
        (stage / ".library_shared").mkdir()
        (stage / ".tui-asset").mkdir(exist_ok=True)
        _write(stage / "human" / ".agent.json", seed.human_manifest)
        _write(stage / "human" / "mailbox" / "contacts.json", [])
        agent = stage / seed.directory_name
        _write(agent / ".agent.json", seed.agent_manifest)
        _write(agent / "init.json", seed.init_document)
        from lingtai.tools.pad._pad import _seed_pad
        _seed_pad(agent / "system", seed.pad_seed)
        (agent / "settings").mkdir()
        psyche = self.serialize_psyche(base_prompt=seed.base_prompt,
                                      covenant=seed.covenant, covenant_file=seed.covenant_file)
        (agent / "settings" / "psyche.json").write_text(psyche, encoding="utf-8")
        if seed.greet is not None:
            greet = seed.greet
            for key, value in {"time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                               "addr": "human", "lang": seed.language,
                               "location": seed.location, "commands": seed.commands}.items():
                greet = greet.replace("{{" + key + "}}", value)
            (agent / ".prompt").write_text(greet, encoding="utf-8")

    def validate(self, attempt: CreationAttempt, request: InteractiveCreateRequest,
                 *, persisted: bool) -> None:
        from lingtai.init_reader import InitReadStatus, read_init, reader_callbacks
        from lingtai.tools.psyche.settings import read_resolved_prompt_inputs
        stage = self.owned_stage(attempt)
        # Observe actual staging through the existing presence parser and Core policy.
        from lingtai.adapters.posix.agent_presence import PosixAgentPresenceStoreAdapter
        from lingtai.kernel.project.inspection import ProjectMemberEvidence, observe_member
        members = [observe_member(ProjectMemberEvidence(child.name,
                   PosixAgentPresenceStoreAdapter(child).observe_manifest()))
                   for child in stage.iterdir() if child.is_dir() and not child.name.startswith(".")]
        validate_staged_members(tuple(members), request.directory_name)

        if persisted:
            observed_preset = self.load_preset(request.preset_ref)
            observed_manifest = {"llm": copy.deepcopy(observed_preset.get("manifest", {}).get("llm"))}
            requested_manifest = {"llm": copy.deepcopy(request.preset_manifest.get("llm"))}
            if observed_manifest != requested_manifest:
                raise creation_error("preset_dependency_mismatch")
            observed_preset["manifest"] = observed_manifest
        else:
            observed_preset = {"manifest": {"llm": copy.deepcopy(request.preset_manifest["llm"])}}
        # Bind reader materialization to the very preset observation compared
        # above, not a second potentially different disk read. Only persisted
        # validation gets this object from the real owner loader.
        def loader(name: str, *args: object, **kwargs: object) -> dict:
            if name != request.preset_ref:
                raise creation_error("invalid_preset")
            return copy.deepcopy(observed_preset)
        agent = stage / request.directory_name
        materialize, prepare = reader_callbacks(agent, load_preset=loader)
        result = read_init(agent, materialize=materialize, prepare=prepare, failure_behavior="STOP")
        if result.status is not InitReadStatus.FULLY_EFFECTIVE:
            raise creation_error("init_preflight_failed")
        read_resolved_prompt_inputs(agent)

    def publish(self, attempt: CreationAttempt) -> tuple[str, ...]:
        stage = self.owned_stage(attempt)
        self.publish_directory(stage, self.root / ".lingtai")
        self._published = True
        try:
            (self.root / ".lingtai" / _MARKER).unlink()
        except Exception:
            return ("staging_marker_retained",)
        return ()

    def discard_owned(self, attempt: CreationAttempt) -> bool:
        if self._published:
            return False
        try:
            stage = self.owned_stage(attempt)
            shutil.rmtree(stage)
            return True
        except Exception:
            return False
