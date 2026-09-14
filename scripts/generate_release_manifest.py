#!/usr/bin/env python3
"""Aggregate built wheel/sdist artifacts into a release manifest + SHA256SUMS.

Usage (CI, after downloading the `wheels-*` and `sdist` Actions artifacts into
one flat directory):

    python scripts/generate_release_manifest.py \\
        --assets-dir ./release-assets \\
        --kernel-version 0.19.5 \\
        --kernel-tag v0.19.5 \\
        --commit "$GITHUB_SHA" \\
        --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \\
        --out-manifest ./release-assets/lingtai-kernel-release-manifest.json \\
        --out-sha256sums ./release-assets/SHA256SUMS

The kernel is a pure-Python distribution, so the release contract is one
universal ``py3-none-any`` wheel plus one sdist. Every ``*.whl`` in
--assets-dir is verified against that contract before it is trusted into the
manifest: the filename must carry the universal tag, the archive must place
``lingtai/`` at its root, and it must carry no native payload or
``*.data/{purelib,platlib}`` scheme entries. A platform-specific or
mis-laid-out wheel fails loud here rather than being published.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from release_manifest import (  # noqa: E402
    ReleaseManifest,
    classify_artifact,
    sha256_file,
    validate_manifest_dict,
)

UNIVERSAL_WHEEL_SUFFIX = "-py3-none-any.whl"


def verify_wheel_is_universal(wheel: Path) -> None:
    """Fail loud unless *wheel* is the pure-Python universal release artifact.

    Dependency-free by design (stdlib ``zipfile`` only) so the release job
    needs no lingtai runtime dependencies to validate the artifact.
    """
    if not wheel.name.endswith(UNIVERSAL_WHEEL_SUFFIX):
        raise SystemExit(
            f"error: {wheel.name} is not a py3-none-any universal wheel. The "
            "kernel is pure Python: a platform-specific or interpreter-specific "
            "wheel means a native payload or build hook crept back in, and it "
            "must never be published."
        )
    try:
        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"error: {wheel.name} is not a readable wheel archive: {exc}")
    if "lingtai/__init__.py" not in names:
        raise SystemExit(
            f"error: {wheel.name} does not place lingtai/ at the archive root"
        )
    misplaced = [n for n in names if ".data/" in n or n.startswith("lingtai/bin/")]
    if misplaced:
        raise SystemExit(
            f"error: {wheel.name} carries native or install-scheme entries that "
            f"a pure wheel must not have: {misplaced[:5]}"
        )
    print(f"  universal OK: {wheel.name}")


def discover_artifacts(assets_dir: Path) -> list[Path]:
    wheels = sorted(assets_dir.glob("*.whl"))
    sdists = sorted(assets_dir.glob("*.tar.gz"))
    if not wheels:
        raise SystemExit(f"error: no *.whl files found in {assets_dir}")
    if not sdists:
        raise SystemExit(f"error: no *.tar.gz sdist found in {assets_dir}")
    if len(sdists) > 1:
        raise SystemExit(
            f"error: expected exactly one sdist in {assets_dir}, found {len(sdists)}: "
            f"{[p.name for p in sdists]}"
        )
    return wheels + sdists


def build_manifest(
    assets_dir: Path,
    kernel_version: str,
    kernel_tag: str,
    commit: str,
    generated_at: str,
) -> ReleaseManifest:
    files = discover_artifacts(assets_dir)
    artifacts = []
    sdist_filename = None

    for path in files:
        if path.suffix == ".whl":
            verify_wheel_is_universal(path)
        digest = sha256_file(path)
        artifact = classify_artifact(path.name, digest)
        if artifact.kind == "sdist":
            sdist_filename = artifact.filename
        artifacts.append(artifact)
        print(f"  {artifact.kind:5s} {artifact.filename}  sha256={digest}")

    assert sdist_filename is not None  # discover_artifacts guarantees exactly one sdist

    manifest = ReleaseManifest(
        kernel_version=kernel_version,
        kernel_tag=kernel_tag,
        commit=commit,
        generated_at=generated_at,
        artifacts=tuple(artifacts),
        sdist_fallback=sdist_filename,
    )
    validate_manifest_dict(manifest.to_dict())
    return manifest


def write_sha256sums(manifest: ReleaseManifest, out_path: Path) -> None:
    lines = [f"{a.sha256}  {a.filename}" for a in manifest.artifacts]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets-dir", type=Path, required=True, help="directory containing built .whl/.tar.gz files")
    parser.add_argument("--kernel-version", required=True, help="PEP 440 version, e.g. 0.19.5")
    parser.add_argument("--kernel-tag", required=True, help="release tag, e.g. v0.19.5")
    parser.add_argument("--commit", required=True, help="full git commit SHA the release was built from")
    parser.add_argument("--generated-at", required=True, help="UTC ISO8601 timestamp, injected by the caller")
    parser.add_argument("--out-manifest", type=Path, required=True)
    parser.add_argument("--out-sha256sums", type=Path, required=True)
    args = parser.parse_args(argv)

    if not args.assets_dir.is_dir():
        raise SystemExit(f"error: --assets-dir is not a directory: {args.assets_dir}")

    print(f"Aggregating release assets from {args.assets_dir} ...")
    manifest = build_manifest(
        args.assets_dir,
        args.kernel_version,
        args.kernel_tag,
        args.commit,
        args.generated_at,
    )

    args.out_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.out_manifest.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"Wrote manifest: {args.out_manifest}")

    args.out_sha256sums.parent.mkdir(parents=True, exist_ok=True)
    write_sha256sums(manifest, args.out_sha256sums)
    print(f"Wrote checksums: {args.out_sha256sums}")

    print(f"\n{len(manifest.artifacts)} artifact(s) for {manifest.kernel_tag} ({manifest.commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
