"""Schema and validator for the kernel release manifest (``lingtai.kernel.release/v1``).

One source of truth for the manifest shape: ``generate_release_manifest.py``
builds it, ``publish_release_assets.py`` and the TUI installer's Gitee/GitHub
consumer both read it. Keep this module free of network/subprocess calls so it
stays trivially unit-testable and importable from a throwaway venv.

Filename convention this schema assumes (wheel filenames per PEP 427):
``lingtai-{version}-{python_tag}-{abi_tag}-{platform_tag}.whl``. The sdist has
no python/abi/platform tag and is recorded with ``kind: "sdist"``.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "lingtai.kernel.release/v1"

# lingtai-0.16.4-cp312-cp312-macosx_11_0_arm64.whl
_WHEEL_RE = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)-(?P<python_tag>[^-]+)-(?P<abi_tag>[^-]+)-(?P<platform_tag>[^-]+)\.whl$"
)
# lingtai-0.16.4.tar.gz
_SDIST_RE = re.compile(r"^(?P<name>[^-]+)-(?P<version>.+)\.tar\.gz$")

REQUIRED_TOP_LEVEL_KEYS = {
    "schema",
    "kernel_version",
    "kernel_tag",
    "commit",
    "generated_at",
    "artifacts",
    "sdist_fallback",
}
REQUIRED_ARTIFACT_KEYS = {
    "filename",
    "sha256",
    "kind",
    "python_tag",
    "abi_tag",
    "platform_tag",
}
VALID_KINDS = {"wheel", "sdist"}


class ManifestError(ValueError):
    """Raised when a manifest dict violates the schema."""


@dataclass(frozen=True)
class Artifact:
    filename: str
    sha256: str
    kind: str  # "wheel" | "sdist"
    python_tag: str | None
    abi_tag: str | None
    platform_tag: str | None

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "kind": self.kind,
            "python_tag": self.python_tag,
            "abi_tag": self.abi_tag,
            "platform_tag": self.platform_tag,
        }


@dataclass(frozen=True)
class ReleaseManifest:
    kernel_version: str
    kernel_tag: str
    commit: str
    generated_at: str
    artifacts: tuple[Artifact, ...] = field(default_factory=tuple)
    sdist_fallback: str = ""
    schema: str = SCHEMA

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "kernel_version": self.kernel_version,
            "kernel_tag": self.kernel_tag,
            "commit": self.commit,
            "generated_at": self.generated_at,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "sdist_fallback": self.sdist_fallback,
        }


def sha256_file(path: Path) -> str:
    """Stream-hash a file; used by both the generator and its tests."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_wheel_filename(filename: str) -> dict | None:
    m = _WHEEL_RE.match(filename)
    if not m:
        return None
    return m.groupdict()


def parse_sdist_filename(filename: str) -> dict | None:
    m = _SDIST_RE.match(filename)
    if not m:
        return None
    return m.groupdict()


def classify_artifact(filename: str, sha256: str) -> Artifact:
    """Build an Artifact from a filename, inferring kind/tags. Raises ManifestError
    for a filename that is neither a recognized wheel nor a recognized sdist —
    this is the guard against publishing an unexpected/mislabeled file."""
    if filename.endswith(".whl"):
        parsed = parse_wheel_filename(filename)
        if parsed is None:
            raise ManifestError(f"filename does not match wheel naming convention: {filename}")
        if parsed["name"] != "lingtai":
            raise ManifestError(f"wheel is not a lingtai wheel: {filename}")
        return Artifact(
            filename=filename,
            sha256=sha256,
            kind="wheel",
            python_tag=parsed["python_tag"],
            abi_tag=parsed["abi_tag"],
            platform_tag=parsed["platform_tag"],
        )
    if filename.endswith(".tar.gz"):
        parsed = parse_sdist_filename(filename)
        if parsed is None:
            raise ManifestError(f"filename does not match sdist naming convention: {filename}")
        if parsed["name"] != "lingtai":
            raise ManifestError(f"sdist is not a lingtai sdist: {filename}")
        return Artifact(
            filename=filename,
            sha256=sha256,
            kind="sdist",
            python_tag=None,
            abi_tag=None,
            platform_tag=None,
        )
    raise ManifestError(f"unrecognized artifact extension (expected .whl or .tar.gz): {filename}")


def validate_manifest_dict(data: dict) -> None:
    """Raise ManifestError with a specific message on any schema violation.

    Deliberately strict: an unknown/missing key or an out-of-band kind fails
    loud rather than silently accepting a malformed manifest that a consumer
    (the TUI installer) would otherwise trust.
    """
    if not isinstance(data, dict):
        raise ManifestError(f"manifest must be an object, got {type(data).__name__}")

    missing = REQUIRED_TOP_LEVEL_KEYS - data.keys()
    if missing:
        raise ManifestError(f"manifest missing required keys: {sorted(missing)}")

    if data["schema"] != SCHEMA:
        raise ManifestError(f"unexpected schema {data['schema']!r}, expected {SCHEMA!r}")

    for key in ("kernel_version", "kernel_tag", "commit", "generated_at", "sdist_fallback"):
        if not isinstance(data[key], str) or not data[key]:
            raise ManifestError(f"manifest field {key!r} must be a non-empty string")

    artifacts = data["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ManifestError("manifest 'artifacts' must be a non-empty list")

    seen_filenames: set[str] = set()
    has_sdist = False
    for i, art in enumerate(artifacts):
        if not isinstance(art, dict):
            raise ManifestError(f"artifacts[{i}] must be an object")
        art_missing = REQUIRED_ARTIFACT_KEYS - art.keys()
        if art_missing:
            raise ManifestError(f"artifacts[{i}] missing keys: {sorted(art_missing)}")
        if not isinstance(art["filename"], str) or not art["filename"]:
            raise ManifestError(f"artifacts[{i}].filename must be a non-empty string")
        if art["filename"] in seen_filenames:
            raise ManifestError(f"duplicate artifact filename: {art['filename']}")
        seen_filenames.add(art["filename"])
        if not isinstance(art["sha256"], str) or not re.match(r"^[0-9a-f]{64}$", art["sha256"]):
            raise ManifestError(f"artifacts[{i}].sha256 must be a 64-char lowercase hex string")
        if art["kind"] not in VALID_KINDS:
            raise ManifestError(f"artifacts[{i}].kind must be one of {sorted(VALID_KINDS)}")
        if art["kind"] == "sdist":
            has_sdist = True
            if art["python_tag"] is not None or art["abi_tag"] is not None or art["platform_tag"] is not None:
                raise ManifestError(f"artifacts[{i}] is a sdist but has non-null python/abi/platform tag")
        else:  # wheel
            for tag_key in ("python_tag", "abi_tag", "platform_tag"):
                if not isinstance(art[tag_key], str) or not art[tag_key]:
                    raise ManifestError(f"artifacts[{i}].{tag_key} must be a non-empty string for a wheel")

    if data["sdist_fallback"] not in seen_filenames:
        raise ManifestError(
            f"sdist_fallback {data['sdist_fallback']!r} is not among the listed artifact filenames"
        )
    if not has_sdist:
        raise ManifestError("manifest has no artifact with kind='sdist'; sdist_fallback is unsatisfiable")


def manifest_from_dict(data: dict) -> ReleaseManifest:
    """Validate then construct a typed ReleaseManifest. Raises ManifestError."""
    validate_manifest_dict(data)
    artifacts = tuple(
        Artifact(
            filename=a["filename"],
            sha256=a["sha256"],
            kind=a["kind"],
            python_tag=a["python_tag"],
            abi_tag=a["abi_tag"],
            platform_tag=a["platform_tag"],
        )
        for a in data["artifacts"]
    )
    return ReleaseManifest(
        kernel_version=data["kernel_version"],
        kernel_tag=data["kernel_tag"],
        commit=data["commit"],
        generated_at=data["generated_at"],
        artifacts=artifacts,
        sdist_fallback=data["sdist_fallback"],
        schema=data["schema"],
    )
