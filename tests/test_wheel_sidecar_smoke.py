"""Smoke-test that an installed lingtai wheel carries and uses the Rust sidecar.

This test is intended for cibuildwheel's ``CIBW_TEST_COMMAND``. It runs against
an installed wheel inside cibuildwheel's test virtualenv, not against the source
tree. When run from a source checkout, it skips rather than requiring a packaged
binary in ``src/lingtai/bin``.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import lingtai
from lingtai.services.file_io_sidecar import (
    RustFileIOBackend,
    default_file_io_service,
    resolve_sidecar_binary,
)


def _is_source_tree_import() -> bool:
    package_path = pathlib.Path(lingtai.__file__).resolve()
    project_root = pathlib.Path(__file__).resolve().parents[1]
    try:
        package_path.relative_to(project_root / "src")
    except ValueError:
        return False
    return True


def test_installed_wheel_carries_and_uses_rust_sidecar() -> None:
    if _is_source_tree_import():
        pytest.skip("wheel sidecar smoke test requires an installed wheel")

    binary = resolve_sidecar_binary(skip_env=True, skip_dev_tree=True)
    assert binary is not None, "packaged lingtai-search-sidecar was not found"
    binary_path = pathlib.Path(binary)
    assert binary_path.is_file(), binary

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        target = root / "a.txt"
        target.write_text("hello native sidecar\n", encoding="utf-8")

        service = default_file_io_service(str(root), backend="auto")
        backend = getattr(service, "_backend", service)
        assert isinstance(backend, RustFileIOBackend), type(backend)

        assert service.glob("*.txt") == [str(target.resolve())]
        matches = service.grep("native", str(root))
        got = [(pathlib.Path(m.path).resolve(), m.line_number, m.line) for m in matches]
        assert got == [(target.resolve(), 1, "hello native sidecar")], got
