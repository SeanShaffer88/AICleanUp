import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aicleanup


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(aicleanup, "running_ai_processes", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, argv):
        out = io.StringIO()
        with patch.object(sys, "argv", ["aicleanup.py"] + argv), contextlib.redirect_stdout(out):
            aicleanup.main()
        return out.getvalue()

    def model_root(self, base):
        root = base / "models"
        root.mkdir()
        payload = b"identical weights" * 128
        (root / "one.gguf").write_bytes(payload)
        (root / "two.gguf").write_bytes(payload)
        (root / "unique.gguf").write_bytes(b"different bytes")
        return root

    def target(self, root):
        return [{"label": "Test models", "category": "Model weights", "path": root, "models": True, "runtime": "huggingface"}]

    def test_duplicates_report_is_json_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = self.model_root(base)
            with patch.object(aicleanup, "known_targets", return_value=self.target(root)):
                output = self.run_cli(["--duplicates", "--json"])
            report = json.loads(output)
            self.assertEqual(report["kind"], "exact-duplicates")
            self.assertEqual(report["count"], 1)
            self.assertEqual(report["duplicated_bytes"], len(b"identical weights" * 128))
            group = report["groups"][0]
            self.assertEqual(group["copies"], 2)
            self.assertEqual({Path(entry["path"]).name for entry in group["files"]}, {"one.gguf", "two.gguf"})
            self.assertEqual(len(group["sha256"]), 64)
            # Nothing was removed, renamed, or linked.
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["one.gguf", "two.gguf", "unique.gguf"])
            self.assertFalse(any(path.is_symlink() for path in root.iterdir()))

    def test_duplicates_text_mode_explains_it_is_report_only(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = self.model_root(base)
            with patch.object(aicleanup, "known_targets", return_value=self.target(root)):
                output = self.run_cli(["--duplicates"])
            self.assertIn("exact SHA-256 duplicate group", output)
            self.assertIn("Report only", output)
            self.assertIn("one.gguf", output)

    def test_duplicates_without_matches_says_so(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "models"
            root.mkdir()
            (root / "one.gguf").write_bytes(b"alpha")
            (root / "two.gguf").write_bytes(b"beta!")
            with patch.object(aicleanup, "known_targets", return_value=self.target(root)):
                output = self.run_cli(["--duplicates"])
            self.assertIn("No byte-identical model files found", output)

    def empty_tree(self, base):
        root = base / "cache-root"
        (root / "leftover/deep/er").mkdir(parents=True)
        (root / "kept/logs").mkdir(parents=True)
        (root / "kept/logs/app.log").write_text("log", encoding="utf-8")
        return root

    def test_empty_dirs_lists_without_removing(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = self.empty_tree(base)
            with patch.object(aicleanup, "known_targets", return_value=self.target(root)):
                report = json.loads(self.run_cli(["--empty-dirs", "--json"]))
            self.assertEqual(report["kind"], "empty-folders")
            self.assertEqual(report["count"], 3)
            self.assertEqual({Path(entry["path"]).name for entry in report["folders"]}, {"leftover", "deep", "er"})
            self.assertTrue((root / "leftover/deep/er").is_dir())
            self.assertEqual([entry["depth"] for entry in report["folders"]], [3, 2, 1])

    def test_sweep_empty_previews_without_confirm(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = self.empty_tree(base)
            with patch.object(aicleanup, "known_targets", return_value=self.target(root)), \
                    patch.object(aicleanup, "trash_path") as trash:
                output = self.run_cli(["--sweep-empty"])
            trash.assert_not_called()
            self.assertIn("Preview only", output)
            self.assertTrue((root / "leftover/deep/er").is_dir())

    def test_sweep_empty_with_confirm_trashes_only_empty_folders(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = self.empty_tree(base)
            trashed = base / "trash"
            trashed.mkdir()

            def fake_trash(path):
                destination = trashed / str(Path(path)).replace(str(base), "").strip("/").replace("/", "_")
                shutil.move(str(path), str(destination))
                return {"route": "system trash", "destination": str(destination)}

            with patch.object(aicleanup, "known_targets", return_value=self.target(root)), \
                    patch.object(aicleanup, "trash_path", side_effect=fake_trash), \
                    patch.object(aicleanup.Path, "home", return_value=base / "home"):
                report = json.loads(self.run_cli(["--sweep-empty", "--confirm", "--json"]))
            self.assertTrue(report["swept"])
            self.assertEqual(report["errors"], [])
            self.assertEqual(len(report["moved"]), 3)
            self.assertEqual(report["remaining"], 0)
            self.assertFalse((root / "leftover").exists())
            self.assertTrue((root / "kept/logs/app.log").is_file())
            manifest = json.loads(Path(report["audit"]).read_text(encoding="utf-8"))
            self.assertEqual({entry["kind"] for entry in manifest["items"]}, {"directory"})

    def test_sweep_empty_confirm_reports_audit_in_text_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = self.empty_tree(base)
            trashed = base / "trash"
            trashed.mkdir()

            def fake_trash(path):
                destination = trashed / str(Path(path)).replace(str(base), "").strip("/").replace("/", "_")
                shutil.move(str(path), str(destination))
                return {"route": "system trash", "destination": str(destination)}

            with patch.object(aicleanup, "known_targets", return_value=self.target(root)), \
                    patch.object(aicleanup, "trash_path", side_effect=fake_trash), \
                    patch.object(aicleanup.Path, "home", return_value=base / "home"):
                output = self.run_cli(["--sweep-empty", "--confirm"])
            self.assertIn("Moved 3 empty folder(s) to Trash", output)
            self.assertIn("Audit manifest:", output)

    def test_sweep_empty_with_nothing_to_do_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "cache-root"
            (root / "kept").mkdir(parents=True)
            (root / "kept/app.log").write_text("log", encoding="utf-8")
            with patch.object(aicleanup, "known_targets", return_value=self.target(root)), \
                    patch.object(aicleanup, "trash_path") as trash:
                report = json.loads(self.run_cli(["--sweep-empty", "--confirm", "--json"]))
                output = self.run_cli(["--sweep-empty", "--confirm"])
            trash.assert_not_called()
            self.assertEqual(report["moved"], [])
            self.assertEqual(report["remaining"], 0)
            self.assertEqual(report["count"], 0)
            self.assertIn("no empty folders", output)

    def test_default_scan_still_summarises_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "cache"
            root.mkdir()
            (root / "cache.db").write_text("cache", encoding="utf-8")
            old = 30 * 86400
            os.utime(root / "cache.db", (os.path.getmtime(root / "cache.db") - old,) * 2)
            with patch.object(aicleanup, "known_targets", return_value=self.target(root)):
                output = self.run_cli(["--dry-run"])
            self.assertIn("AICleanUp scan complete", output)
            self.assertTrue((root / "cache.db").is_file())


if __name__ == "__main__":
    unittest.main()
