from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lingtai.services.file_search_sidecar import (
    FileSearchSidecarError,
    RustFileSearchSidecar,
    SidecarGrepRequest,
)


def test_sidecar_adapter_requires_explicit_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LINGTAI_SEARCH_SIDECAR", raising=False)
    adapter = RustFileSearchSidecar()
    assert not adapter.available()
    with pytest.raises(FileSearchSidecarError, match="not configured"):
        adapter.grep(SidecarGrepRequest(root=str(tmp_path), path=str(tmp_path), pattern="needle"))


@pytest.mark.skipif(shutil.which("cargo") is None, reason="Rust toolchain is optional for this PoC")
def test_rust_sidecar_grep_poc_round_trip(tmp_path: Path) -> None:
    crate = Path(__file__).resolve().parents[1] / "experimental" / "lingtai-search-sidecar"
    subprocess.run(["cargo", "build", "--quiet"], cwd=crate, check=True, timeout=120)
    binary = crate / "target" / "debug" / ("lingtai-search-sidecar.exe" if os.name == "nt" else "lingtai-search-sidecar")

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("alpha\nneedle one\n", encoding="utf-8")
    (tmp_path / "src" / "b.txt").write_text("needle two\n", encoding="utf-8")
    (tmp_path / "src" / "bin.dat").write_bytes(b"needle\x00hidden")

    adapter = RustFileSearchSidecar(str(binary))
    matches = adapter.grep(
        SidecarGrepRequest(
            root=str(tmp_path),
            path=str(tmp_path / "src"),
            pattern="needle",
            max_results=10,
        )
    )

    assert [(match.path, match.line_number, match.line) for match in matches] == [
        ("src/a.py", 2, "needle one"),
        ("src/b.txt", 1, "needle two"),
    ]
