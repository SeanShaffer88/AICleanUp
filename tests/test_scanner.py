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

    def test_model_weight_paths_are_opt_in_and_not_preselected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "model.gguf").write_bytes(b"weights")
            target = {"label": "models", "category": "Model weights", "path": root, "models": True}
            self.assertEqual(aicleanup.scan([target], include_models=False)[0], [])
            items, _ = aicleanup.scan([target], include_models=True)
            self.assertEqual(len(items), 1)
            self.assertFalse(items[0]["selected"])


if __name__ == "__main__":
    unittest.main()
