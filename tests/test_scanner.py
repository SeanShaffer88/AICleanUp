import os
import tempfile
import time
import unittest
from pathlib import Path

import aicleanup


class ScannerTests(unittest.TestCase):
    def test_workspace_only_returns_scratch_and_backup_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".ai_scratch").mkdir()
            (root / ".ai_scratch" / "notes.md").write_text("generated", encoding="utf-8")
            (root / "change.patch").write_text("diff", encoding="utf-8")
            (root / "normal.py").write_text("source", encoding="utf-8")
            old = time.time() - 15 * 86400
            os.utime(root / ".ai_scratch" / "notes.md", (old, old))

            items, warnings = aicleanup.scan([{
                "label": "workspace", "category": "Project files", "path": root, "workspace": True
            }])

            self.assertEqual({item["name"] for item in items}, {"notes.md", "change.patch"})
            self.assertTrue(all(not item["selected"] for item in items))
            self.assertEqual(warnings, [])

    def test_ignore_patterns_and_sensitive_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".aicleanignore").write_text("*.keep\n", encoding="utf-8")
            for name in ("cache.db", "safe.keep", ".env"):
                (root / name).write_text("data", encoding="utf-8")
            items, _ = aicleanup.scan([{"label": "test", "category": "Caches", "path": root}])
            self.assertEqual([item["name"] for item in items], ["cache.db"])
            self.assertTrue(items[0]["selected"])

            app_data, _ = aicleanup.scan([{"label": "app", "category": "Application data", "path": root}])
            self.assertFalse(app_data[0]["selected"])

    def test_overlapping_roots_do_not_duplicate_workspace_findings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "orphan.patch").write_text("diff", encoding="utf-8")
            roots = [
                {"label": "known app", "category": "Application data", "path": root},
                {"label": "workspace", "category": "Project files", "path": root, "workspace": True},
            ]
            items, _ = aicleanup.scan(roots)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["category"], "Superseded outputs")
            self.assertFalse(items[0]["selected"])

    def test_model_weight_paths_are_opt_in_and_not_preselected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "model.gguf").write_bytes(b"weights")
            target = {"label": "models", "category": "Model weights", "path": root, "models": True}
            self.assertEqual(aicleanup.scan([target], include_models=False)[0], [])
            items, _ = aicleanup.scan([target], include_models=True)
            self.assertEqual(len(items), 1)
            self.assertFalse(items[0]["selected"])

    def test_trash_validation_requires_a_regular_file_inside_scanned_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "scan"
            root.mkdir()
            candidate = root / "cache.db"
            candidate.write_text("candidate", encoding="utf-8")
            self.assertEqual(aicleanup.validate_trash_candidate(candidate, root), candidate)
            with self.assertRaisesRegex(RuntimeError, "outside the scanned folder"):
                aicleanup.validate_trash_candidate(Path(temp) / "elsewhere", root)
            with self.assertRaisesRegex(RuntimeError, "scan root"):
                aicleanup.validate_trash_candidate(root, root)

    def test_trash_validation_rejects_symlinked_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "scan"
            root.mkdir()
            target = Path(temp) / "real.db"
            target.write_text("do not move", encoding="utf-8")
            link = root / "link.db"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable")
            with self.assertRaisesRegex(RuntimeError, "Symbolic links"):
                aicleanup.validate_trash_candidate(link, root)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
