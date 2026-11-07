import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import aicleanup


class ScannerTests(unittest.TestCase):
    def test_windows_target_registry_covers_msix_and_user_data_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "profile"
            roaming = home / "AppData/Roaming"
            local = home / "AppData/Local"
            expected = [
                local / "Packages/OpenAI.ChatGPT-Desktop_2p2nqsd0c76g0/LocalCache/Roaming/ChatGPT",
                local / "Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude",
                local / "Perplexity/Comet/User Data",
                local / "Microsoft/Copilot/User Data",
                roaming / "Microsoft/Windows/INetCache/Copilot",
                roaming / "gemini-desktop",
                roaming / "@deepseek-ai/dsh-desktop",
                roaming / "anythingllm-desktop/storage",
                roaming / "CherryStudio",
                roaming / "Jan/data",
            ]
            for directory in expected:
                directory.mkdir(parents=True)
            with patch("aicleanup.platform.system", return_value="Windows"), \
                    patch.object(Path, "home", return_value=home), \
                    patch.dict(os.environ, {"APPDATA": str(roaming), "LOCALAPPDATA": str(local)}):
                targets = aicleanup.known_targets()
            registered = {item["path"].resolve(): item for item in targets}
            self.assertTrue(set(p.resolve() for p in expected).issubset(registered))
            copilot_cache = registered[(roaming / "Microsoft/Windows/INetCache/Copilot").resolve()]
            self.assertEqual(copilot_cache["category"], "Caches")

    def test_macos_registry_covers_app_support_and_sandboxed_chatgpt(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            expected = [
                home / "Library/Application Support/ChatGPT",
                home / "Library/Application Support/Perplexity",
                home / "Library/Application Support/gemini-desktop",
                home / "Library/Application Support/DeepSeek",
                home / "Library/Containers/com.openai.chat/Data/Library/Application Support/ChatGPT",
            ]
            for directory in expected:
                directory.mkdir(parents=True)
            with patch("aicleanup.platform.system", return_value="Darwin"), \
                    patch.object(Path, "home", return_value=home):
                targets = aicleanup.known_targets()
            registered = {item["path"].resolve() for item in targets}
            self.assertTrue(set(p.resolve() for p in expected).issubset(registered))

    def test_runtime_overrides_and_lmstudio_pointer_are_discovered(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            ollama = home / "custom-ollama"
            localai = home / "custom-localai"
            lmstudio = home / "moved-lmstudio"
            for path in (ollama, localai, lmstudio / "models"):
                path.mkdir(parents=True)
            (home / ".lmstudio-home-pointer").write_text(str(lmstudio), encoding="utf-8")
            environment = {"OLLAMA_MODELS": str(ollama), "LOCALAI_MODELS_PATH": str(localai)}
            with patch("aicleanup.platform.system", return_value="Linux"), \
                    patch.object(Path, "home", return_value=home), patch.dict(os.environ, environment, clear=False):
                targets = aicleanup.known_targets()
            runtime_paths = {item.get("runtime"): item["path"].resolve() for item in targets}
            self.assertEqual(runtime_paths["ollama"], ollama.resolve())
            self.assertEqual(runtime_paths["localai"], localai.resolve())
            self.assertIn((lmstudio / "models").resolve(), {item["path"].resolve() for item in targets})

    def test_ollama_partial_cleanup_excludes_raw_blobs_and_uses_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "models"
            partial = root / "blobs" / "sha256-deadbeef-partial-2"
            raw_blob = root / "blobs" / "sha256-aabbccdd"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"partial download")
            raw_blob.write_bytes(b"shared model layer")
            old = time.time() - 8 * 86400
            os.utime(partial, (old, old))
            manifest_path = root / "manifests/registry.ollama.ai/library/llama3.1/8b"
            manifest_path.parent.mkdir(parents=True)
            shared_manifest = {"config":{"digest":"sha256:config","size":6},"layers":[{"digest":"sha256:layer","size":10}]}
            manifest_path.write_text(json.dumps(shared_manifest), encoding="utf-8")
            shared_tag = manifest_path.parent / "latest"
            shared_tag.write_text(json.dumps(shared_manifest), encoding="utf-8")
            target = {"label":"Ollama models", "category":"Model weights", "path":root, "models":True, "runtime":"ollama", "scan_partials":True}
            with patch.object(aicleanup, "running_ai_processes", return_value=[]):
                partials, _ = aicleanup.scan([target], include_models=False)
                all_models, _ = aicleanup.scan([target], include_models=True)
            self.assertEqual([item["name"] for item in partials], [partial.name])
            self.assertEqual(partials[0]["category"], "Ollama partial downloads")
            self.assertFalse(partials[0]["selected"])
            self.assertTrue(partials[0]["can_delete"])
            model_item = next(item for item in all_models if item.get("model_ref") == "llama3.1:8b")
            self.assertEqual(model_item["size"], 0)
            self.assertEqual(model_item["logical_size"], 16)
            self.assertEqual(model_item["cleanup_action"], "ollama-rm")
            self.assertFalse(any(item["name"] == raw_blob.name for item in all_models))

    def test_localcache_ancestor_does_not_mark_all_client_data_as_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "LocalCache" / "Roaming" / "ChatGPT"
            (root / "IndexedDB").mkdir(parents=True)
            data_file = root / "IndexedDB" / "chat.log"
            data_file.write_text("chat", encoding="utf-8")
            cache_root = root / "Cache"
            cache_root.mkdir()
            cache_file = cache_root / "cache.bin"
            cache_file.write_bytes(b"cache")
            old = time.time() - 8 * 86400
            os.utime(cache_file, (old, old))

            items, _ = aicleanup.scan([{"label": "ChatGPT Store", "category": "Application data", "path": root}])
            by_name = {item["name"]: item for item in items}
            self.assertEqual(by_name["chat.log"]["category"], "Application data")
            self.assertFalse(by_name["chat.log"]["selected"])
            self.assertEqual(by_name["cache.bin"]["category"], "Caches")
            self.assertTrue(by_name["cache.bin"]["selected"])

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
            old = time.time() - 8 * 86400
            os.utime(root / "cache.db", (old, old))
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
            self.assertEqual(items[0]["risk"], "re-download")
            self.assertEqual(items[0]["cleanup_action"], "none")
            self.assertFalse(items[0]["can_delete"])

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
