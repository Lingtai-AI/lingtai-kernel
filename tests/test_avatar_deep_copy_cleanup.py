import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lingtai.tools.avatar import AvatarManager


class AvatarDeepCopyCleanupTests(unittest.TestCase):
    def test_replaces_each_existing_directory(self):
        for name in ("system", "knowledge", "exports"):
            with self.subTest(directory=name), tempfile.TemporaryDirectory() as tmp:
                src, dst = Path(tmp) / "parent", Path(tmp) / "child"
                source = src / name
                source.mkdir(parents=True)
                (source / "kept.bin").write_bytes(b"source bytes")
                old = dst / name
                old.mkdir(parents=True)
                (old / "removed.bin").write_bytes(b"old bytes")

                AvatarManager._prepare_deep(src, dst)

                self.assertEqual((dst / name / "kept.bin").read_bytes(), b"source bytes")
                self.assertFalse((dst / name / "removed.bin").exists())

    def test_preserves_destinations_when_sources_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "parent", Path(tmp) / "child"
            src.mkdir()
            for name in ("system", "knowledge", "exports"):
                target = dst / name
                target.mkdir(parents=True)
                (target / "existing.bin").write_bytes(name.encode())

            AvatarManager._prepare_deep(src, dst)

            for name in ("system", "knowledge", "exports"):
                self.assertEqual((dst / name / "existing.bin").read_bytes(), name.encode())

    def test_copy_order_combo_and_runtime_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "parent", Path(tmp) / "child"
            src.mkdir()
            for name in ("system", "knowledge", "exports"):
                (src / name).mkdir()
            for name in ("history", "mailbox", "delegates", "logs"):
                (src / name).mkdir()
                (src / name / "marker").write_bytes(b"runtime")
            for name in (".agent.json", ".agent.heartbeat"):
                (src / name).write_bytes(b"runtime")
            combo = b'{"identity": "durable"}'
            (src / "combo.json").write_bytes(combo)

            copied = []
            copytree = shutil.copytree

            def record_copytree(source, destination, *args, **kwargs):
                copied.append(Path(source).name)
                return copytree(source, destination, *args, **kwargs)

            with patch.object(shutil, "copytree", side_effect=record_copytree):
                AvatarManager._prepare_deep(src, dst)

            self.assertEqual(copied, ["system", "knowledge", "exports"])
            self.assertEqual((dst / "combo.json").read_bytes(), combo)
            self.assertFalse(any((dst / name).exists() for name in (
                "history", "mailbox", "delegates", "logs", ".agent.json", ".agent.heartbeat"
            )))
