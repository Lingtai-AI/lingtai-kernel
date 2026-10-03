import json

from lingtai.adapters.posix import daemon_manager


def test_manager_pid_info_normalizes_missing_malformed_and_non_dict(monkeypatch, tmp_path):
    assert daemon_manager._read_manager_pid_info(tmp_path) == {}

    def malformed(*args, **kwargs):
        raise TypeError("manager pid data must be an object")

    monkeypatch.setattr(daemon_manager, "read_json", malformed)
    assert daemon_manager._read_manager_pid_info(tmp_path) == {}

    monkeypatch.setattr(daemon_manager, "read_json", lambda *args, **kwargs: [])
    assert daemon_manager._read_manager_pid_info(tmp_path) == {}

    info = {"pid": 123, "manager_runtime_identity": "runtime"}
    monkeypatch.setattr(daemon_manager, "read_json", lambda *args, **kwargs: info)
    assert daemon_manager._read_manager_pid_info(tmp_path) == info


def test_ensure_manager_uses_normalized_pid_info(monkeypatch, tmp_path):
    root = tmp_path / "manager"
    info = {
        "pid": 123,
        "started_at": 1,
        "manager_start_identity": "start",
        "manager_runtime_identity": "runtime",
    }
    reads = []

    def read_info(actual_root):
        reads.append(actual_root)
        return info

    monkeypatch.setattr(daemon_manager, "_read_manager_pid_info", read_info)
    monkeypatch.setattr(
        daemon_manager, "_manager_runtime_identity", lambda: "runtime"
    )
    monkeypatch.setattr(daemon_manager, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        daemon_manager, "_process_identity_matches", lambda pid, identity: True
    )

    daemon_manager._ensure_manager_locked(tmp_path, root, pool_size=1)

    assert reads == [root]


def test_queue_sort_orders_timestamp_ties_and_invalid_values(tmp_path):
    jobs = {
        "later.json": {"enqueued_at": 5},
        "tie-b.json": {"enqueued_at": 2},
        "tie-a.json": {"enqueued_at": 2},
        "missing.json": {},
    }
    paths = []
    for name, payload in jobs.items():
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)
    malformed = tmp_path / "bad.json"
    malformed.write_text("{", encoding="utf-8")
    paths.append(malformed)

    manager = object.__new__(daemon_manager._DaemonManagerProcess)
    ordered = sorted(paths, key=manager._queue_sort_key)

    assert [path.name for path in ordered] == [
        "tie-a.json",
        "tie-b.json",
        "later.json",
        "bad.json",
        "missing.json",
    ]
