# tests/test_daemon_task_files.py
"""Tests for optional per-task ``task_files`` input to ``daemon`` emanate.

Covers the additive contract: ``task`` stays required and omitting
``task_files`` keeps old behavior; parent preflight resolves every path under
the agent working directory, validates UTF-8 text and practical limits, and
snapshots bytes content-addressed into the immutable ``daemons/_task_files/``
store; per-run durable metadata points at a compact manifest + snapshot rows;
the daemon prompt receives only the compact manifest (snapshot paths, never
contents); malformed/out-of-root/missing/oversize/non-UTF-8 input refuses the
whole batch before any dispatch; and ``list``/recovery never surface the
internal store as a run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lingtai.tools import daemon as daemon_pkg
from tests._daemon_helpers import (
    daemon_emanate_task_schema,
    install_fake_detached_owner,
    make_daemon_agent as _make_agent,
)


def _write_input(agent, rel: str, data: str | bytes) -> Path:
    p = agent._working_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8")
    return p


def _dispatch(mgr, tasks):
    return mgr.handle({"action": "emanate", "tasks": tasks})


def _capture_lingtai_spawn(monkeypatch, mgr):
    captured = []

    def fake_spawn(run_dir, **kwargs):
        captured.append((run_dir, kwargs))
        run_dir.mark_done("ok")

    monkeypatch.setattr(mgr, "_spawn_detached_lingtai_run", fake_spawn)
    return captured


def _durable_task_files(run_dir) -> dict:
    state = json.loads(run_dir.daemon_json_path.read_text(encoding="utf-8"))
    return state["call_parameters"]["task_files"]


# ---------------------------------------------------------------------------
# Schema: task_files is optional; task/tools requirements unchanged
# ---------------------------------------------------------------------------

def test_emanate_task_schema_declares_optional_task_files():
    task = daemon_emanate_task_schema()

    task_files = task["properties"]["task_files"]
    assert task_files["type"] == "array"
    item = task_files["items"]
    assert item["required"] == ["path"]
    assert set(item["properties"]) == {"path", "label", "role"}
    assert item["additionalProperties"] is False
    # task remains required and tools remains required; task_files is add-only.
    assert task["required"] == ["task", "tools"]


def test_omitting_task_files_keeps_old_behavior(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    captured = _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{"task": "clean the repo", "tools": ["file"]}])

    assert result["status"] == "dispatched"
    assert not (agent._working_dir / "daemons" / "_task_files").exists()
    prompt = captured[0][0].prompt_path.read_text(encoding="utf-8")
    assert "Parent-provided task files" not in prompt
    assert captured[0][1]["task"] == "clean the repo"


# ---------------------------------------------------------------------------
# Preflight refuses malformed / out-of-root / missing / oversize / non-UTF-8
# input loudly, before any run-dir creation or dispatch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "task_files,message_part",
    [
        ("not-a-list", "must be an array"),
        (["a string"], "must be an object with path"),
        ([{"label": "x"}], "path must be a non-empty string"),
        ([{"path": ""}], "path must be a non-empty string"),
        ([{"path": 7}], "path must be a non-empty string"),
    ],
)
def test_task_files_malformed_entries_refuse_whole_batch(
    tmp_path, monkeypatch, task_files, message_part
):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [
        {"task": "t", "tools": ["file"], "task_files": task_files},
    ])

    assert result["status"] == "error"
    assert "tasks[0].task_files" in result["message"]
    assert message_part in result["message"]
    assert not (agent._working_dir / "daemons").exists()
    assert not mgr._emanations


def test_task_files_out_of_root_path_refuses(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": str(outside)}],
    }])

    assert result["status"] == "error"
    assert "outside the agent working directory" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_task_files_relative_escape_refuses(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": "../outside.txt"}],
    }])

    assert result["status"] == "error"
    assert "outside the agent working directory" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_task_files_missing_path_refuses(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": "inputs/absent.txt"}],
    }])

    assert result["status"] == "error"
    assert "does not resolve to a file" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_task_files_oversize_refuses(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    monkeypatch.setattr(daemon_pkg, "TASK_FILE_MAX_BYTES", 8)
    _write_input(agent, "inputs/big.txt", "x" * 9)
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": "inputs/big.txt"}],
    }])

    assert result["status"] == "error"
    assert "exceeds the 8-byte limit" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_task_files_non_utf8_refuses(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    _write_input(agent, "inputs/binary.bin", b"\xff\xfe\x00\x01")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": "inputs/binary.bin"}],
    }])

    assert result["status"] == "error"
    assert "not valid UTF-8 text" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_task_files_too_many_files_refuses(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    monkeypatch.setattr(daemon_pkg, "TASK_FILES_MAX_PER_TASK", 2)
    for i in range(3):
        _write_input(agent, f"inputs/f{i}.txt", f"body {i}")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": f"inputs/f{i}.txt"} for i in range(3)],
    }])

    assert result["status"] == "error"
    assert "2-file limit" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_task_files_oversized_label_refuses(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    monkeypatch.setattr(daemon_pkg, "_TASK_FILES_ANNOTATION_MAX_CHARS", 5)
    _write_input(agent, "inputs/a.txt", "body")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": "inputs/a.txt", "label": "way too long"}],
    }])

    assert result["status"] == "error"
    assert "label must be a string of at most 5 characters" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_task_files_later_task_failure_refuses_whole_batch_without_store(tmp_path, monkeypatch):
    """A bad entry in a later task refuses the batch with zero store side effects."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    _write_input(agent, "inputs/ok.txt", "fine body")
    _write_input(agent, "inputs/bad.txt", b"\x00\x01\x02")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [
        {"task": "t1", "tools": ["file"], "task_files": [{"path": "inputs/ok.txt"}]},
        {"task": "t2", "tools": ["file"], "task_files": [{"path": "inputs/bad.txt"}]},
    ])

    assert result["status"] == "error"
    assert "tasks[1].task_files" in result["message"]
    assert not (agent._working_dir / "daemons").exists()
    assert not mgr._emanations


# ---------------------------------------------------------------------------
# Snapshot: content-addressed once per dispatch/group; durable rows; prompt
# carries only the compact manifest (snapshot paths), never file contents
# ---------------------------------------------------------------------------

def test_task_files_snapshot_once_per_group_and_durable_rows(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    content = "alpha input body\n"
    src = _write_input(agent, "inputs/alpha.txt", content)
    captured = _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [
        {
            "task": "t1", "tools": ["file"],
            "task_files": [{"path": "inputs/alpha.txt", "label": "spec", "role": "input"}],
        },
        {
            "task": "t2", "tools": ["file"],
            "task_files": [{"path": str(src), "label": "spec2"}],
        },
    ])

    assert result["status"] == "dispatched"
    store = agent._working_dir / "daemons" / "_task_files"
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    manifest_name = f"manifest-{result['group_id']}.json"
    assert sorted(p.name for p in store.iterdir()) == sorted([sha, manifest_name])
    blob = store / sha
    assert blob.read_bytes() == content.encode("utf-8")
    manifest = json.loads((store / manifest_name).read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["group_id"] == result["group_id"]
    assert len(manifest["files"]) == 2

    # Durable per-run metadata points at the manifest and this run's rows.
    assert len(captured) == 2
    first_row = _durable_task_files(captured[0][0])["files"][0]
    assert first_row["path"] == "inputs/alpha.txt"
    assert first_row["label"] == "spec"
    assert first_row["role"] == "input"
    assert first_row["sha256"] == sha
    assert first_row["size"] == len(content)
    assert first_row["snapshot"] == str(blob)
    assert _durable_task_files(captured[0][0])["manifest"] == str(store / manifest_name)
    second_row = _durable_task_files(captured[1][0])["files"][0]
    assert second_row["label"] == "spec2"
    assert second_row.get("role") is None
    assert second_row["snapshot"] == str(blob)

    # The prompt receives the compact manifest only: metadata + snapshot paths,
    # never the file contents.
    prompt = captured[0][0].prompt_path.read_text(encoding="utf-8")
    assert "## Parent-provided task files" in prompt
    assert "task_files:" in prompt
    assert "spec" in prompt
    assert "input" in prompt
    assert sha in prompt
    assert str(blob) in prompt
    assert content.strip() not in prompt


def test_task_files_preserves_distinct_attachment_rows_for_one_path(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    _write_input(agent, "inputs/spec.txt", "same bytes")
    captured = _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "compare inputs", "tools": ["file"],
        "task_files": [
            {"path": "inputs/spec.txt", "label": "before", "role": "baseline"},
            {"path": "inputs/spec.txt", "label": "after", "role": "target"},
        ],
    }])

    assert result["status"] == "dispatched"
    rows = _durable_task_files(captured[0][0])["files"]
    assert [(r["label"], r["role"]) for r in rows] == [
        ("before", "baseline"), ("after", "target"),
    ]
    # Attachment rows stay distinct while immutable blob storage is deduplicated.
    assert rows[0]["snapshot"] == rows[1]["snapshot"]


def test_task_files_relaunch_reads_snapshot_not_original(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    src = _write_input(agent, "inputs/alpha.txt", "original bytes v1")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": "inputs/alpha.txt"}],
    }])
    assert result["status"] == "dispatched"

    store = agent._working_dir / "daemons" / "_task_files"
    sha = hashlib.sha256(b"original bytes v1").hexdigest()
    blob = store / sha
    assert blob.read_bytes() == b"original bytes v1"

    # The mutable original changes; the immutable snapshot still serves the run.
    src.write_text("mutated after dispatch", encoding="utf-8")
    assert blob.read_bytes() == b"original bytes v1"

    # Durable metadata points at the manifest and the snapshot, never the source.
    run_dir = mgr._emanations[result["ids"][0]]["run_dir"]
    tf = _durable_task_files(run_dir)
    assert tf["manifest"].startswith(str(store))
    assert tf["files"][0]["snapshot"] == str(blob)
    assert tf["files"][0]["resolved"] == str(src.resolve())


def test_task_files_shared_blob_across_dispatches_deduplicates(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    _write_input(agent, "inputs/alpha.txt", "same bytes")
    _capture_lingtai_spawn(monkeypatch, mgr)

    first = _dispatch(mgr, [{
        "task": "t1", "tools": ["file"],
        "task_files": [{"path": "inputs/alpha.txt"}],
    }])
    second = _dispatch(mgr, [{
        "task": "t2", "tools": ["file"],
        "task_files": [{"path": "inputs/alpha.txt"}],
    }])
    assert first["status"] == second["status"] == "dispatched"

    store = agent._working_dir / "daemons" / "_task_files"
    sha = hashlib.sha256(b"same bytes").hexdigest()
    assert len([p for p in store.iterdir() if p.name == sha]) == 1
    manifests = [p for p in store.iterdir() if p.name.startswith("manifest-")]
    assert len(manifests) == 2  # one compact manifest per dispatch


# ---------------------------------------------------------------------------
# The internal input store is never surfaced as a run by list/recovery scans
# ---------------------------------------------------------------------------

def test_task_files_store_is_never_listed_as_a_run(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    _write_input(agent, "inputs/alpha.txt", "x")
    _capture_lingtai_spawn(monkeypatch, mgr)

    result = _dispatch(mgr, [{
        "task": "t", "tools": ["file"],
        "task_files": [{"path": "inputs/alpha.txt"}],
    }])
    assert result["status"] == "dispatched"

    store = agent._working_dir / "daemons" / "_task_files"
    assert store.is_dir()
    assert not mgr._looks_like_daemon_run_dir(store)
    listing = mgr._handle_list()["emanations"]
    assert all(item["id"] != "_task_files" for item in listing)


# ---------------------------------------------------------------------------
# CLI backends receive the same compact manifest and durable rows
# ---------------------------------------------------------------------------

def test_task_files_reach_cli_backend_prompt_and_durable_rows(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    # This test observes the classic detached CLI payload. Disable the manager
    # path explicitly rather than coupling its assertion to manager routing.
    monkeypatch.setattr(mgr, "_manager_pool_size", 0)
    content = "cli input body"
    _write_input(agent, "inputs/cli.txt", content)
    records = install_fake_detached_owner(monkeypatch)

    result = mgr.handle({
        "action": "emanate",
        "backend": "opencode",
        "tasks": [{
            "task": "run with input", "tools": ["file"],
            "task_files": [{"path": "inputs/cli.txt", "label": "spec", "role": "input"}],
        }],
    })

    assert result["status"] == "dispatched"
    assert len(records) == 1
    composed_task = records[0]["manifest"]["task"]
    assert "## Parent-provided task files" in composed_task
    assert "spec" in composed_task
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    store = agent._working_dir / "daemons" / "_task_files"
    assert str(store / sha) in composed_task
    assert content not in composed_task
    run_dir = records[0]["run_dir"]
    tf = _durable_task_files(run_dir)
    assert tf["files"][0]["sha256"] == sha
    assert tf["files"][0]["snapshot"] == str(store / sha)
