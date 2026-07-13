from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lingtai.kernel.runtime_identity import (
    _source_kind,
    runtime_identity,
    runtime_identity_event_fields,
)
from tests._snapshot_helpers import FakeSourceRevisionPort


def _identity(revision: FakeSourceRevisionPort):
    runtime_identity.cache_clear()
    with (
        patch("lingtai.kernel.runtime_identity._source_root", return_value=Path("/source")),
        patch("lingtai.kernel.runtime_identity._distribution", return_value=None),
        patch("lingtai.kernel.runtime_identity._pyproject_version", return_value="1.2.3"),
    ):
        return runtime_identity(revision)


def test_runtime_identity_event_fields_are_json_serializable():
    revision = FakeSourceRevisionPort(revision="abcdef123456", dirty=False)
    runtime_identity.cache_clear()
    with (
        patch("lingtai.kernel.runtime_identity._source_root", return_value=Path("/source")),
        patch("lingtai.kernel.runtime_identity._distribution", return_value=None),
        patch("lingtai.kernel.runtime_identity._pyproject_version", return_value="1.2.3"),
    ):
        fields = runtime_identity_event_fields(revision)

    assert set(fields) == {"kernel_version", "kernel_runtime_stamp", "kernel_runtime"}
    assert fields["kernel_version"] == "1.2.3"
    assert fields["kernel_runtime_stamp"] == "1.2.3+git.abcdef123456"
    assert fields["kernel_runtime"]["version"] == fields["kernel_version"]
    assert fields["kernel_runtime"]["stamp"] == fields["kernel_runtime_stamp"]
    assert revision.revision_calls == [(12, 0.5)]
    assert revision.dirty_calls == [0.5]
    json.dumps(fields)


def test_runtime_identity_dirty_clean_and_unknown_tri_state():
    dirty = _identity(FakeSourceRevisionPort(revision="abcdef123456", dirty=True))
    assert dirty["git_dirty"] is True
    assert dirty["stamp"].endswith(".dirty")

    clean = _identity(FakeSourceRevisionPort(revision="abcdef123456", dirty=False))
    assert clean["git_dirty"] is False
    assert not clean["stamp"].endswith(".dirty")

    unknown = _identity(FakeSourceRevisionPort(revision="abcdef123456", dirty=None))
    assert "git_dirty" not in unknown
    assert unknown["stamp"] == "1.2.3+git.abcdef123456"


def test_runtime_identity_revision_failure_uses_source_fallback_without_dirty_query():
    revision = FakeSourceRevisionPort(revision=None, dirty=True)
    identity = _identity(revision)
    assert "git_commit" not in identity
    assert "git_dirty" not in identity
    assert identity["stamp"].startswith("1.2.3+source")
    assert revision.revision_calls == [(12, 0.5)]
    assert revision.dirty_calls == []


def test_runtime_identity_process_cache_accepts_unhashable_revision_capability():
    revision = FakeSourceRevisionPort(revision="abcdef123456", dirty=False)
    runtime_identity.cache_clear()
    with (
        patch("lingtai.kernel.runtime_identity._source_root", return_value=Path("/source")),
        patch("lingtai.kernel.runtime_identity._distribution", return_value=None),
        patch("lingtai.kernel.runtime_identity._pyproject_version", return_value="1.2.3"),
    ):
        first = runtime_identity(revision)
        second = runtime_identity(revision)
    assert first is second
    assert revision.revision_calls == [(12, 0.5)]


def test_source_kind_preserves_package_editable_and_source_classification():
    package_path = Path("/venv/lib/python/site-packages/lingtai/runtime_identity.py")
    package_dist = SimpleNamespace(read_text=lambda _name: None)
    assert _source_kind(package_dist, package_path, "1.2.3") == "package"

    editable_dist = SimpleNamespace(
        read_text=lambda _name: '{"dir_info": {"editable": true}}'
    )
    assert _source_kind(editable_dist, package_path, "1.2.3") == "editable"
    assert _source_kind(None, Path("/source/lingtai/runtime_identity.py"), "1.2.3") == "source-checkout"
