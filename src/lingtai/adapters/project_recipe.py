"""Existing-project Recipe filesystem evidence and current public owner seams."""
from __future__ import annotations

import base64
import json
import os
import shutil
import stat
from pathlib import Path
from collections.abc import Callable

from lingtai.kernel.project.recipe import (
    GreetLayer, ProjectRecipePort, RecipeInfo, RecipeSkills, RecipeSnapshotReceipt,
    RecipeTree, RecipeWrite, RecipeFailure, RecipeOperationError,
)


def decode_native_recipe_path(value: object) -> str:
    """Decode only the specialized Go Recipe request at the filesystem edge."""
    from lingtai.kernel.project.recipe import recipe_error
    try:
        if not isinstance(value, str):
            raise ValueError("path bytes required")
        raw = base64.b64decode(value, validate=True)
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("canonical path bytes required")
        return os.fsdecode(raw)
    except (ValueError, UnicodeError):
        raise recipe_error("invalid_project_request") from None


def _failure(phase: str, operation: str, path: Path, exc: Exception,
             source: bytes | None = None) -> RecipeFailure:
    native_path = getattr(exc, "filename", None) or path
    offset = None
    if isinstance(exc, json.JSONDecodeError) and source is not None:
        # Report only a proved UTF-8 source-byte offset. Python also accepts
        # UTF-16/32 JSON, unlike Go; do not invent offsets for those decoders.
        try:
            decoded = source.decode("utf-8")
            prefix = 3 if decoded.startswith("\ufeff") else 0
            if (decoded[1:] if prefix else decoded) == exc.doc:
                offset = prefix + len(exc.doc[:exc.pos].encode("utf-8"))
        except UnicodeError:
            pass
    return RecipeFailure(phase, operation,
                         base64.b64encode(os.fsencode(native_path)).decode("ascii"),
                         exc.errno if isinstance(exc, OSError) else None,
                         getattr(exc, "winerror", None), offset)


class _RecipeNumber(str):
    """Opaque JSON number lexeme, matching Go DecodeJSONUseNumber floats."""


class _RecipeFields(list):
    """Ordered JSON object members, distinct from JSON arrays."""


def _read_recipe_metadata(data: bytes) -> dict[str, str | None]:
    """Decode only the legacy Go RecipeInfo fields, not init/request JSON."""
    # Go consumes UTF-8 only and substitutes one RuneError per invalid byte.
    # surrogateescape retains each bad byte, unlike decode(errors="replace")
    # which can collapse a truncated multibyte sequence into one replacement.
    text = data.decode("utf-8", errors="surrogateescape")
    text = "".join("\ufffd" if 0xDC80 <= ord(char) <= 0xDCFF else char for char in text)

    def invalid_constant(_: str) -> None:
        raise ValueError("invalid")

    value = json.loads(text, object_pairs_hook=_RecipeFields,
                       # Ignored numbers need no Python integer/float conversion;
                       # known fields still see a non-string and reject them.
                       parse_int=lambda _: 0, parse_float=lambda _: 0,
                       parse_constant=invalid_constant)
    if not isinstance(value, _RecipeFields):
        raise ValueError("invalid")
    fields: dict[str, str | None] = dict.fromkeys(("id", "name", "description", "version"), "")
    fields["library_name"] = None
    # These ASCII tags have no case-fold collisions. Go's Unicode SimpleFold
    # also equates long s with ASCII S; full Python casefold would overmatch.
    fold = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ\u017f", "abcdefghijklmnopqrstuvwxyzs")
    for key, item in value:
        key = key.translate(fold)
        if key not in fields:
            continue
        if item is None:
            # null leaves Go string values alone; *string is cleared instead.
            if key == "library_name":
                fields[key] = None
        elif isinstance(item, str):
            # json already combines valid escaped surrogate pairs. Go replaces
            # every remaining unpaired UTF-16 surrogate with RuneError.
            fields[key] = "".join("\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char for char in item)
        else:
            # A wrong-typed occurrence remains an Unmarshal error even when a
            # later duplicate is valid; never validate only the final mapping.
            raise ValueError("invalid")
    if not fields["name"]:
        raise ValueError("invalid")
    if not fields["version"]:
        fields["version"] = "1.0.0"
    return fields


def _read_init_bytes(data: bytes | None) -> object:
    return json.loads(data, parse_float=_RecipeNumber,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))


def _init_bytes(value: object) -> bytes:
    # Only raw init serialization at this adapter seam. No effective-owner
    # serialization or public generic mutation endpoint. Preserve arbitrary
    # finite number lexemes rather than round unknown fields through float.
    def encode(item: object) -> str:
        if isinstance(item, _RecipeNumber):
            return str(item)
        if isinstance(item, dict):
            return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + encode(child)
                                  for key, child in item.items()) + "}"
        if isinstance(item, (tuple, list)):
            return "[" + ",".join(encode(child) for child in item) + "]"
        return json.dumps(item, ensure_ascii=False, allow_nan=False)
    return encode(value).encode("utf-8")


def _legacy_join(*parts: str) -> Path:
    # Go filepath.Join joins first, then cleans; a later absolute-looking
    # library/lang does not reset earlier components as pathlib joining would.
    cleaned = os.path.normpath(os.sep.join(part for part in parts if part))
    if os.name != "nt" and cleaned.startswith("//"):
        cleaned = "/" + cleaned.lstrip("/")  # Go Clean does not retain POSIX //
    return Path(cleaned)


def _legacy_write_bytes(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o644)
    try:
        view = memoryview(content)
        while view:
            count = os.write(fd, view)
            if not count:
                raise OSError("file write failed")
            view = view[count:]
    finally:
        os.close(fd)


def _legacy_mkdir_all(path: Path) -> None:
    if path.is_dir():
        return
    if path.parent != path:
        _legacy_mkdir_all(path.parent)
    path.mkdir(mode=0o755, exist_ok=True)


def _legacy_init_write(path: Path, document: dict) -> None:
    # Exact old AppendSkillsPath fixed .tmp + rename behavior, including a
    # followed pre-existing temporary symlink and no cleanup on write failure.
    temporary = Path(str(path) + ".tmp")
    _legacy_write_bytes(temporary, _init_bytes(document))
    try:
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


class FilesystemProjectRecipeAdapter(ProjectRecipePort):
    def __init__(self, root: Path, *, load_preset: Callable[..., dict],
                 lingtai_dir: Path | None = None) -> None:
        root = _legacy_join(str(root))
        self.root = root
        self.base = _legacy_join(str(lingtai_dir)) if lingtai_dir is not None else root / ".lingtai"
        self.recipe = root / ".recipe"
        self.snapshot = self.base / ".tui-asset" / ".recipe"
        self.load_preset = load_preset
        # Bound root is explicit caller authority; do not import fresh-create
        # or configuration refusal policy into existing Recipe operations.

    @staticmethod
    def _tree(path: Path) -> RecipeTree:
        try:
            if not path.is_dir():
                # Path.is_dir suppresses only absence/non-directory on Python 3.11.
                return RecipeTree(False)
        except OSError:
            return RecipeTree(False, read_failed=True)
        files: list[tuple[str, bytes]] = []
        try:
            def visit(directory: Path) -> None:
                for child in sorted(directory.iterdir()):
                    if child.is_dir() and not child.is_symlink():
                        visit(child)
                    else:
                        files.append((child.relative_to(path).as_posix(), child.read_bytes()))
            # Go Walk does not recurse a symlink root, treating it as a file.
            if path.is_symlink():
                return RecipeTree(True, read_failed=True)
            visit(path)
            return RecipeTree(True, tuple(files))
        except (OSError, ValueError):
            return RecipeTree(True, read_failed=True)

    def observe_trees(self) -> tuple[RecipeTree, RecipeTree]:
        selected = self._tree(self.recipe)
        # Don't read snapshot when selected is absent/stat-failed.
        return selected, self._tree(self.snapshot) if selected.present else RecipeTree(False)

    def load_info(self) -> RecipeInfo:
        path = self.recipe / "recipe.json"
        operation = "read"
        data = None
        try:
            data = path.read_bytes()
            operation = "decode"
            value = _read_recipe_metadata(data)
            library = value["library_name"]
            entry = str(_legacy_join("..", "..", library)) if library else ""
            return RecipeInfo(value["name"], library, entry)
        except Exception as exc:
            raise RecipeOperationError("invalid_recipe", _failure("metadata", operation, path, exc, data)) from None

    def observe_greet(self, language: str) -> GreetLayer:
        root = self.recipe / "greet"
        candidates = (_legacy_join(str(root), language, "greet.md"), root / "greet.md") if language else (root / "greet.md",)
        for path in candidates:
            try:
                # Go resolves the first stat-success non-directory, then one
                # read. Unreadable selected locale does NOT fall back to root.
                info = path.stat()
                if stat.S_ISDIR(info.st_mode):
                    continue
            except (OSError, ValueError):
                continue
            try:
                return GreetLayer(True, path.read_bytes())
            except OSError:
                return GreetLayer(True, read_failed=True)
        return GreetLayer()

    def observe_directories(self) -> tuple[str, ...]:
        try:
            # One enumeration, immediate eligible real directories; no manifest
            # membership filter. Entry identity is captured before callbacks.
            with os.scandir(self.base) as entries:
                return tuple(sorted((e.name for e in entries if e.is_dir(follow_symlinks=False)),
                                    key=os.fsencode))
        except OSError as exc:
            raise RecipeOperationError("recipe_enumeration_failed",
                                       _failure("enumeration", "readdir", self.base, exc)) from None

    def directory_identity(self, directory_name: str) -> bytes:
        return os.fsencode(directory_name)

    def _member(self, name: str) -> Path:
        return self.base / name

    def write_prompt(self, directory_name: str, content: bytes) -> RecipeWrite:
        path = self._member(directory_name) / ".prompt"
        try:
            # Existing direct write semantics, not a new per-member transaction.
            _legacy_write_bytes(path, content)
            return RecipeWrite(True)
        except Exception as exc:
            return RecipeWrite(code="recipe_prompt_write_failed",
                               failure=_failure("prompt", "write", path, exc))

    def observe_skills(self, directory_name: str) -> RecipeSkills:
        path = self._member(directory_name) / "init.json"
        operation = "read"
        data = None
        try:
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                return RecipeSkills()
            operation = "decode"
            document = _read_init_bytes(data)
            if document is None:
                return RecipeSkills()
            if not isinstance(document, dict):
                raise ValueError("invalid init")
            canonical = "manifest" not in document
            if canonical:
                from lingtai.kernel.config_resolve import normalize_init
                normalize_init(document)  # conflict/type gate, never resolve a model
                capabilities = document.setdefault("tools", {})
                skills = capabilities.get("skills", {})
                if skills is False:
                    return RecipeSkills()
                if not isinstance(skills, dict):
                    raise ValueError("invalid skills config")
                changed = False
                capabilities.setdefault("skills", skills)
            else:
                manifest = document.get("manifest")
                if not isinstance(manifest, dict):
                    return RecipeSkills()
                capabilities = manifest.get("capabilities")
                if not isinstance(capabilities, dict):
                    return RecipeSkills()
                from lingtai.tools.registry import normalize_capabilities
                operation = "normalize"
                normalized = normalize_capabilities(capabilities)
                changed = normalized != capabilities
                skills = normalized.get("skills")
                if not isinstance(skills, dict):
                    return RecipeSkills()
                manifest["capabilities"] = normalized
            # Exact old strip only carried to disk when Core requests a write.
            document.pop("principle_file", None)
            document.pop("procedures_file", None)
            paths = skills.get("paths")
            content = _init_bytes(document)
            return RecipeSkills(content, True, tuple(paths) if isinstance(paths, list) else (), changed)
        except Exception as exc:
            return RecipeSkills(code="recipe_skills_read_failed",
                                failure=_failure("skills", operation, path, exc, data))

    def _reader_status(self, path: Path) -> str:
        from lingtai.init_reader import read_init, reader_callbacks
        from lingtai.tools.psyche.settings import read_resolved_prompt_inputs
        materialize, prepare = reader_callbacks(path, load_preset=self.load_preset)
        outcome = read_init(path, materialize=materialize, prepare=prepare, failure_behavior="STOP")
        if outcome.status.value == "READ_FAILED":
            return "READ_FAILED"
        read_resolved_prompt_inputs(path)
        return outcome.status.value

    def write_skills(self, directory_name: str, evidence: RecipeSkills,
                     paths: tuple[object, ...]) -> RecipeWrite:
        written = False
        status = "not_read"
        code = "recipe_init_write_failed"
        path = self._member(directory_name)
        try:
            # Only the specialized skills-path field is written; raw owner
            # evidence preserves unmaterialized preset references and integers.
            document = _read_init_bytes(evidence.owner_content)
            capabilities = (document["manifest"]["capabilities"] if "manifest" in document
                            else document["tools"])
            capabilities["skills"]["paths"] = list(paths)
            _legacy_init_write(path / "init.json", document)
            written = True
            code = "recipe_reader_failed"
            status = self._reader_status(path)
            if status == "READ_FAILED":
                return RecipeWrite(True, status, code)
            return RecipeWrite(True, status)
        except Exception as exc:
            return RecipeWrite(written, "READ_FAILED" if written else status, code,
                               _failure("reader" if written else "skills", "read" if written else "write",
                                        path / "init.json", exc))

    @staticmethod
    def _copy_file(source: Path, target: Path, mode: int) -> None:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            _legacy_mkdir_all(target.parent)
            # Go open follows links and preserves existing target mode.
            target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), mode & 0o777)
            try:
                while data := os.read(source_fd, 64 * 1024):
                    view = memoryview(data)
                    while view:
                        count = os.write(target_fd, view)
                        if not count:
                            raise OSError("snapshot write failed")
                        view = view[count:]
            finally:
                os.close(target_fd)
        finally:
            os.close(source_fd)

    @staticmethod
    def _copy_tree(source: Path, destination: Path) -> None:
        info = source.stat()
        if not stat.S_ISDIR(info.st_mode):
            FilesystemProjectRecipeAdapter._copy_file(source, destination, info.st_mode)
            return
        _legacy_mkdir_all(destination)
        for child in sorted(source.iterdir()):
            target = destination / child.name
            info = child.lstat()  # e.Info, not followed target metadata
            if stat.S_ISDIR(info.st_mode):
                FilesystemProjectRecipeAdapter._copy_tree(child, target)
            else:
                FilesystemProjectRecipeAdapter._copy_file(child, target, info.st_mode)

    def replace_snapshot(self) -> RecipeSnapshotReceipt:
        removed = False
        try:
            try:
                if self.snapshot.is_symlink() or (self.snapshot.exists() and not self.snapshot.is_dir()):
                    self.snapshot.unlink()
                elif self.snapshot.exists():
                    shutil.rmtree(self.snapshot)
                removed = True
            except OSError:
                pass  # exact legacy best-effort removal; still attempt copy
            self._copy_tree(self.recipe, self.snapshot)
            # RemoveAll errors were ignored by old Go; independent removal
            # evidence is NOT a new top-level error when copy succeeds.
            return RecipeSnapshotReceipt(True, removed, True)
        except Exception as exc:
            return RecipeSnapshotReceipt(True, removed, False, "recipe_snapshot_copy_failed",
                                         _failure("snapshot", "copy", self.snapshot, exc))
