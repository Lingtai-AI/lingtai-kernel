"""Tests for scripts/lib/release_manifest.py (the lingtai.kernel.release/v1
schema + validator) and scripts/generate_release_manifest.py (the aggregator
CLI). No network, no subprocess against a real wheel build — fixtures only.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from release_manifest import (  # noqa: E402
    Artifact,
    ManifestError,
    ReleaseManifest,
    classify_artifact,
    manifest_from_dict,
    parse_sdist_filename,
    parse_wheel_filename,
    sha256_file,
    validate_manifest_dict,
)

GENERATE_SCRIPT = REPO_ROOT / "scripts" / "generate_release_manifest.py"


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------


def test_parse_wheel_filename_extracts_tags():
    parsed = parse_wheel_filename("lingtai-0.16.4-cp312-cp312-macosx_11_0_arm64.whl")
    assert parsed == {
        "name": "lingtai",
        "version": "0.16.4",
        "python_tag": "cp312",
        "abi_tag": "cp312",
        "platform_tag": "macosx_11_0_arm64",
    }


def test_parse_wheel_filename_rejects_malformed():
    assert parse_wheel_filename("not-a-wheel.whl") is None
    assert parse_wheel_filename("lingtai-0.16.4.whl") is None


def test_parse_sdist_filename():
    parsed = parse_sdist_filename("lingtai-0.16.4.tar.gz")
    assert parsed == {"name": "lingtai", "version": "0.16.4"}


# ---------------------------------------------------------------------------
# classify_artifact
# ---------------------------------------------------------------------------


def test_classify_artifact_wheel():
    art = classify_artifact("lingtai-0.16.4-cp313-cp313-macosx_11_0_arm64.whl", "a" * 64)
    assert art.kind == "wheel"
    assert art.python_tag == "cp313"
    assert art.abi_tag == "cp313"
    assert art.platform_tag == "macosx_11_0_arm64"


def test_classify_artifact_sdist():
    art = classify_artifact("lingtai-0.16.4.tar.gz", "b" * 64)
    assert art.kind == "sdist"
    assert art.python_tag is None


def test_classify_artifact_rejects_plain_pure_wheel_name_mismatch():
    # Not lingtai's own name -> reject regardless of extension.
    with pytest.raises(ManifestError):
        classify_artifact("other-pkg-1.0-py3-none-any.whl", "c" * 64)


def test_classify_artifact_rejects_unrecognized_extension():
    with pytest.raises(ManifestError):
        classify_artifact("lingtai-0.16.4.zip", "d" * 64)


def test_classify_artifact_rejects_malformed_wheel_name():
    with pytest.raises(ManifestError):
        classify_artifact("lingtai.whl", "e" * 64)


# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world" * 1000)
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    assert sha256_file(p) == expected


# ---------------------------------------------------------------------------
# validate_manifest_dict — the strict schema gate
# ---------------------------------------------------------------------------


def _valid_manifest_dict() -> dict:
    return {
        "schema": "lingtai.kernel.release/v1",
        "kernel_version": "0.16.4",
        "kernel_tag": "v0.16.4",
        "commit": "a" * 40,
        "generated_at": "2026-07-15T00:00:00Z",
        "sdist_fallback": "lingtai-0.16.4.tar.gz",
        "artifacts": [
            {
                "filename": "lingtai-0.16.4-cp312-cp312-macosx_11_0_arm64.whl",
                "sha256": "b" * 64,
                "kind": "wheel",
                "python_tag": "cp312",
                "abi_tag": "cp312",
                "platform_tag": "macosx_11_0_arm64",
            },
            {
                "filename": "lingtai-0.16.4.tar.gz",
                "sha256": "c" * 64,
                "kind": "sdist",
                "python_tag": None,
                "abi_tag": None,
                "platform_tag": None,
            },
        ],
    }


def test_validate_manifest_dict_accepts_valid():
    validate_manifest_dict(_valid_manifest_dict())  # no raise


def test_validate_manifest_dict_rejects_unknown_keys():
    top_level = _valid_manifest_dict()
    top_level["unexpected"] = True
    with pytest.raises(ManifestError, match="unknown top-level keys"):
        validate_manifest_dict(top_level)

    artifact = _valid_manifest_dict()
    artifact["artifacts"][0]["unexpected"] = True
    with pytest.raises(ManifestError, match="unknown keys"):
        validate_manifest_dict(artifact)


def test_validate_manifest_dict_requires_sdist_fallback_kind():
    data = _valid_manifest_dict()
    data["sdist_fallback"] = data["artifacts"][0]["filename"]
    with pytest.raises(ManifestError, match="kind='sdist'"):
        validate_manifest_dict(data)


@pytest.mark.parametrize("missing_key", sorted({
    "schema", "kernel_version", "kernel_tag", "commit", "generated_at", "artifacts", "sdist_fallback",
}))
def test_validate_manifest_dict_rejects_missing_top_level_key(missing_key):
    data = _valid_manifest_dict()
    del data[missing_key]
    with pytest.raises(ManifestError, match="missing required keys"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_wrong_schema():
    data = _valid_manifest_dict()
    data["schema"] = "lingtai.kernel.release/v2"
    with pytest.raises(ManifestError, match="unexpected schema"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_empty_artifacts():
    data = _valid_manifest_dict()
    data["artifacts"] = []
    with pytest.raises(ManifestError, match="non-empty list"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_bad_sha256_shape():
    data = _valid_manifest_dict()
    data["artifacts"][0]["sha256"] = "not-hex"
    with pytest.raises(ManifestError, match="sha256"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_duplicate_filenames():
    data = _valid_manifest_dict()
    data["artifacts"].append(dict(data["artifacts"][0]))
    with pytest.raises(ManifestError, match="duplicate artifact filename"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_bad_kind():
    data = _valid_manifest_dict()
    data["artifacts"][0]["kind"] = "egg"
    with pytest.raises(ManifestError, match="kind"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_wheel_missing_tags():
    data = _valid_manifest_dict()
    data["artifacts"][0]["python_tag"] = None
    with pytest.raises(ManifestError, match="python_tag"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_sdist_with_tags():
    data = _valid_manifest_dict()
    data["artifacts"][1]["python_tag"] = "cp312"
    with pytest.raises(ManifestError, match="non-null"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_sdist_fallback_not_listed():
    data = _valid_manifest_dict()
    data["sdist_fallback"] = "does-not-exist.tar.gz"
    with pytest.raises(ManifestError, match="sdist_fallback"):
        validate_manifest_dict(data)


def test_validate_manifest_dict_rejects_no_sdist_artifact():
    data = _valid_manifest_dict()
    data["artifacts"] = [data["artifacts"][0]]  # only the wheel
    data["sdist_fallback"] = data["artifacts"][0]["filename"]
    with pytest.raises(ManifestError, match="no artifact with kind='sdist'"):
        validate_manifest_dict(data)


def test_manifest_from_dict_round_trips():
    data = _valid_manifest_dict()
    manifest = manifest_from_dict(data)
    assert isinstance(manifest, ReleaseManifest)
    assert manifest.to_dict() == data


# ---------------------------------------------------------------------------
# generate_release_manifest.py — end-to-end against fixture files
# ---------------------------------------------------------------------------


def _write_fixture_wheel(path: Path, sidecar: bool) -> None:
    """A minimal real zip so classify/sha256 logic runs against real bytes.
    Sidecar presence is irrelevant here because these tests pass
    --skip-sidecar-check; the sidecar contract itself is covered by the
    existing tests/test_wheel_sidecar_smoke.py suite.
    """
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("lingtai/__init__.py", "")
        if sidecar:
            zf.writestr("lingtai/bin/lingtai-search-sidecar", b"fake-binary")


def test_generate_release_manifest_cli_end_to_end(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    _write_fixture_wheel(assets / "lingtai-0.16.4-cp312-cp312-macosx_11_0_arm64.whl", sidecar=True)
    _write_fixture_wheel(assets / "lingtai-0.16.4-cp313-cp313-manylinux_2_28_x86_64.whl", sidecar=True)
    (assets / "lingtai-0.16.4.tar.gz").write_bytes(b"fake-sdist-bytes")

    out_manifest = tmp_path / "manifest.json"
    out_sums = tmp_path / "SHA256SUMS"

    result = subprocess.run(
        [
            sys.executable, str(GENERATE_SCRIPT),
            "--assets-dir", str(assets),
            "--kernel-version", "0.16.4",
            "--kernel-tag", "v0.16.4",
            "--commit", "a" * 40,
            "--generated-at", "2026-07-15T00:00:00Z",
            "--out-manifest", str(out_manifest),
            "--out-sha256sums", str(out_sums),
            "--skip-sidecar-check",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    data = json.loads(out_manifest.read_text())
    validate_manifest_dict(data)  # re-validate the emitted file with the shared validator
    assert data["kernel_tag"] == "v0.16.4"
    assert len(data["artifacts"]) == 3
    kinds = {a["kind"] for a in data["artifacts"]}
    assert kinds == {"wheel", "sdist"}

    sums_text = out_sums.read_text()
    assert "lingtai-0.16.4.tar.gz" in sums_text
    assert sums_text.count("\n") == 3  # 3 artifacts, trailing newline


def test_generate_release_manifest_rejects_plain_fallback_wheel(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    _write_fixture_wheel(assets / "lingtai-0.16.4-py3-none-any.whl", sidecar=False)
    (assets / "lingtai-0.16.4.tar.gz").write_bytes(b"fake-sdist-bytes")

    result = subprocess.run(
        [
            sys.executable, str(GENERATE_SCRIPT),
            "--assets-dir", str(assets),
            "--kernel-version", "0.16.4",
            "--kernel-tag", "v0.16.4",
            "--commit", "a" * 40,
            "--generated-at", "2026-07-15T00:00:00Z",
            "--out-manifest", str(tmp_path / "manifest.json"),
            "--out-sha256sums", str(tmp_path / "SHA256SUMS"),
            "--skip-sidecar-check",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "py3-none-any" in result.stderr or "py3-none-any" in result.stdout


def test_generate_release_manifest_rejects_no_wheels(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "lingtai-0.16.4.tar.gz").write_bytes(b"fake-sdist-bytes")

    result = subprocess.run(
        [
            sys.executable, str(GENERATE_SCRIPT),
            "--assets-dir", str(assets),
            "--kernel-version", "0.16.4",
            "--kernel-tag", "v0.16.4",
            "--commit", "a" * 40,
            "--generated-at", "2026-07-15T00:00:00Z",
            "--out-manifest", str(tmp_path / "manifest.json"),
            "--out-sha256sums", str(tmp_path / "SHA256SUMS"),
            "--skip-sidecar-check",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "no *.whl" in result.stderr


def test_generate_release_manifest_rejects_multiple_sdists(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    _write_fixture_wheel(assets / "lingtai-0.16.4-cp312-cp312-macosx_11_0_arm64.whl", sidecar=True)
    (assets / "lingtai-0.16.4.tar.gz").write_bytes(b"one")
    (assets / "lingtai-0.16.4-take2.tar.gz").write_bytes(b"two")

    result = subprocess.run(
        [
            sys.executable, str(GENERATE_SCRIPT),
            "--assets-dir", str(assets),
            "--kernel-version", "0.16.4",
            "--kernel-tag", "v0.16.4",
            "--commit", "a" * 40,
            "--generated-at", "2026-07-15T00:00:00Z",
            "--out-manifest", str(tmp_path / "manifest.json"),
            "--out-sha256sums", str(tmp_path / "SHA256SUMS"),
            "--skip-sidecar-check",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "exactly one sdist" in result.stderr


@pytest.mark.parametrize("existing_output", ["manifest.json", "SHA256SUMS"])
def test_generate_release_manifest_refuses_existing_outputs(tmp_path: Path, existing_output: str):
    assets = tmp_path / "assets"
    assets.mkdir()
    _write_fixture_wheel(assets / "lingtai-0.16.4-cp312-cp312-macosx_11_0_arm64.whl", sidecar=True)
    (assets / "lingtai-0.16.4.tar.gz").write_bytes(b"fake-sdist-bytes")
    existing = tmp_path / existing_output
    existing.write_text("immutable receipt", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, str(GENERATE_SCRIPT),
            "--assets-dir", str(assets),
            "--kernel-version", "0.16.4",
            "--kernel-tag", "v0.16.4",
            "--commit", "a" * 40,
            "--generated-at", "2026-07-15T00:00:00Z",
            "--out-manifest", str(tmp_path / "manifest.json"),
            "--out-sha256sums", str(tmp_path / "SHA256SUMS"),
            "--skip-sidecar-check",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "refusing to replace existing release output" in result.stderr
    assert existing.read_text(encoding="utf-8") == "immutable receipt"
    other = tmp_path / ("SHA256SUMS" if existing_output == "manifest.json" else "manifest.json")
    assert not other.exists()
