import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import aicleanup


class ScannerTests(unittest.TestCase):
    def setUp(self):
        # Scan tests assert cache selection, so keep ambient AI processes out of
        # the picture; process blocking has its own dedicated tests.
        patcher = patch.object(aicleanup, "running_ai_processes", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_recent_cache_is_flagged_for_an_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Cache").mkdir()
            fresh = root / "Cache" / "fresh.bin"
            fresh.write_bytes(b"cache")
            stale = root / "Cache" / "stale.bin"
            stale.write_bytes(b"cache")
            old = time.time() - 8 * 86400
            os.utime(stale, (old, old))
            items, _ = aicleanup.scan([{"label": "ChatGPT Store", "category": "Caches", "path": root}])
            by_name = {item["name"]: item for item in items}
            self.assertTrue(by_name["fresh.bin"]["age_blocked"])
            self.assertFalse(by_name["fresh.bin"]["can_delete"])
            self.assertFalse(by_name["fresh.bin"]["selected"])
            self.assertIn("override", by_name["fresh.bin"]["blocked_reason"])
            self.assertFalse(by_name["stale.bin"]["age_blocked"])
            self.assertTrue(by_name["stale.bin"]["can_delete"])

    def test_running_client_blocks_candidate_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Cache").mkdir()
            cache_file = root / "Cache" / "cache.bin"
            cache_file.write_bytes(b"cache")
            old = time.time() - 8 * 86400
            os.utime(cache_file, (old, old))
            with patch.object(aicleanup, "running_ai_processes",
                              return_value=[{"runtime": "chatgpt", "name": "ChatGPT", "pid": 7}]):
                items, _ = aicleanup.scan([{"label": "ChatGPT Store", "category": "Caches", "path": root}])
            self.assertEqual(len(items), 1)
            self.assertFalse(items[0]["can_delete"])
            self.assertFalse(items[0]["selected"])
            self.assertIn("Close the running AI process", items[0]["blocked_reason"])

    def test_quarantine_fallback_is_restorable_from_its_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            source_root = home / "cache"
            source_root.mkdir()
            source = source_root / "blob.bin"
            source.write_bytes(b"cache bytes")
            with patch.object(aicleanup.Path, "home", return_value=home):
                moved = aicleanup.quarantine_move(source)
                self.assertEqual(moved["route"], "AICleanUp quarantine")
                self.assertFalse(source.exists())
                self.assertTrue(Path(moved["destination"]).is_file())
                manifest, error = aicleanup.write_audit_manifest([
                    {"path": str(source), "size": 11, "category": "Caches",
                     "route": moved["route"], "destination": moved["destination"]},
                ])
                self.assertIsNone(error)
                preview = aicleanup.restore_manifest(Path(manifest).name, dry_run=True)
                self.assertEqual(len(preview["restored"]), 1)
                self.assertFalse(source.exists())
                report = aicleanup.restore_manifest(Path(manifest).name)
            self.assertEqual(len(report["restored"]), 1)
            self.assertEqual(source.read_bytes(), b"cache bytes")

    def test_restore_skips_ollama_removals_and_unknown_locations(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with patch.object(aicleanup.Path, "home", return_value=home):
                manifest, error = aicleanup.write_audit_manifest([
                    {"path": str(home / "llama3.1-8b"), "size": 4, "category": "Model weights", "route": "ollama rm"},
                    {"path": str(home / "legacy-cache.db"), "size": 4, "category": "Caches", "route": "system trash"},
                ])
                self.assertIsNone(error)
                report = aicleanup.restore_manifest(Path(manifest).name)
            self.assertEqual(report["restored"], [])
            self.assertEqual(len(report["skipped"]), 2)
            reasons = " ".join(item["reason"] for item in report["skipped"])
            self.assertIn("ollama pull", reasons)
            self.assertIn("operating system Trash", reasons)


class ModelEntryScanTests(unittest.TestCase):
    """A scan should name the model behind a folder and flag its current variant."""

    def test_lmstudio_variants_share_one_entry_and_newest_is_current(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "models"
            repo = root / "lmstudio-community" / "Demo-GGUF"
            repo.mkdir(parents=True)
            older = repo / "Demo-Q4_K_M.gguf"
            newer = repo / "Demo-Q8_0.gguf"
            older.write_bytes(b"a" * 512)
            newer.write_bytes(b"b" * 900)
            now = time.time()
            os.utime(older, (now - 86400 * 40, now - 86400 * 40))
            os.utime(newer, (now - 86400 * 20, now - 86400 * 20))
            roots = [{"label": "LM Studio models", "category": "Model weights", "path": root,
                      "models": True, "runtime": "lmstudio", "workspace": False}]
            with patch.object(aicleanup, "running_ai_processes", return_value=[]):
                items, warnings = aicleanup.scan(roots, include_models=True)

        self.assertEqual(warnings, [])
        self.assertEqual(len(items), 2)
        self.assertEqual({item["model_entry"] for item in items}, {"lmstudio-community/Demo-GGUF"})
        self.assertEqual({item["variant"] for item in items}, {"Q4_K_M", "Q8_0"})
        self.assertEqual({item["entry_count"] for item in items}, {2})
        self.assertEqual({item["entry_size"] for item in items}, {512 + 900})
        current = [item for item in items if item.get("latest")]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["variant"], "Q8_0")
        self.assertEqual(current[0]["latest_by"], "modified")
        self.assertFalse(any(item["can_delete"] for item in items))

    def test_weights_stay_protected_with_their_entry_named(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "models"
            repo = root / "acme" / "tiny"
            repo.mkdir(parents=True)
            (repo / "tiny-F16.gguf").write_bytes(b"c" * 256)
            roots = [{"label": "LM Studio models", "category": "Model weights", "path": root,
                      "models": True, "runtime": "lmstudio", "workspace": False}]
            with patch.object(aicleanup, "running_ai_processes", return_value=[]):
                items, _ = aicleanup.scan(roots, include_models=True)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["model_entry"], "acme/tiny")
        self.assertEqual(items[0]["variant"], "F16")
        self.assertFalse(items[0]["can_delete"])
        self.assertEqual(items[0]["cleanup_action"], "none")


if __name__ == "__main__":
    unittest.main()


class SymlinkedModelStoreTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(aicleanup, "running_ai_processes", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_hub(self, base):
        hub = base / "hub"
        blobs = hub / "models--acme--demo/blobs"
        blobs.mkdir(parents=True)
        weights = blobs / ("a" * 64)
        weights.write_bytes(b"w" * 4096)
        config = blobs / ("b" * 64)
        config.write_text('{"arch":"demo"}', encoding="utf-8")
        metadata = blobs / ("c" * 64)
        metadata.write_bytes(b"header")
        for revision in ("rev1", "rev2"):
            snapshot = hub / "models--acme--demo/snapshots" / revision
            snapshot.mkdir(parents=True)
            for name, target in (("model.safetensors", weights), ("config.json", config), ("model.safetensors.metadata", metadata)):
                (snapshot / name).symlink_to(os.path.relpath(target, snapshot))
        return hub, weights

    def scan_hub(self, hub):
        roots = [{"label": "Hugging Face models", "category": "Model weights", "path": hub,
                  "models": True, "runtime": "huggingface"}]
        return aicleanup.scan(roots, include_models=True)[0]

    def test_revision_symlinks_are_followed_and_counted_once(self):
        with tempfile.TemporaryDirectory() as temp:
            hub, weights = self.build_hub(Path(temp))
            items = self.scan_hub(hub)
            self.assertEqual(len(items), 1, [item["path"] for item in items])
            finding = items[0]
            self.assertEqual(Path(finding["path"]).relative_to(hub).as_posix(), "models--acme--demo/snapshots/rev1/model.safetensors")
            self.assertEqual(finding["size"], 4096)
            self.assertEqual(finding["link_target"], str(weights))
            self.assertEqual(finding["category"], "Model weights")
            self.assertEqual(finding["cleanup_action"], "none")
            self.assertFalse(finding["can_delete"])
            self.assertTrue(Path(finding["path"]).is_symlink())

    def test_symlinks_that_escape_the_model_root_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            hub, _ = self.build_hub(base)
            outside = base / "outside.gguf"
            outside.write_bytes(b"x" * 1024)
            snapshot = hub / "models--acme--demo/snapshots/rev1"
            (snapshot / "escape.gguf").symlink_to(outside)
            items = self.scan_hub(hub)
            self.assertNotIn("escape.gguf", {Path(item["path"]).name for item in items})
            self.assertTrue(outside.is_file())

    def test_symlink_loops_and_broken_links_do_not_break_the_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            hub, _ = self.build_hub(base)
            snapshot = hub / "models--acme--demo/snapshots/rev2"
            (snapshot / "loop.gguf").symlink_to(snapshot / "loop.gguf")
            (snapshot / "broken.gguf").symlink_to(snapshot / "missing.gguf")
            items = self.scan_hub(hub)
            self.assertEqual(len(items), 1)
            self.assertEqual(Path(items[0]["path"]).parent.name, "rev1")

    def test_cache_roots_still_ignore_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            cache = base / "cache"
            cache.mkdir()
            real = base / "real.db"
            real.write_bytes(b"cache data")
            (cache / "linked.db").symlink_to(real)
            roots = [{"label": "ChatGPT cache", "category": "Caches", "path": cache}]
            items, _ = aicleanup.scan(roots, include_models=False)
            self.assertEqual(items, [])
            self.assertTrue(real.is_file())


class EmptyDirectoryTests(unittest.TestCase):
    def test_empty_directories_reports_file_free_trees_deepest_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "cache-root"
            (root / "cache/v/cache").mkdir(parents=True)
            (root / "empty-only").mkdir()
            (root / "models").mkdir()          # runtime layout names are kept
            (root / "blobs").mkdir()
            (root / "logs").mkdir()
            (root / "logs/app.log").write_text("log", encoding="utf-8")

            folders, truncated = aicleanup.empty_directories([str(root)])
            paths = [entry["path"] for entry in folders]

            self.assertFalse(truncated)
            self.assertEqual(set(paths), {str(root / "cache/v/cache"), str(root / "cache/v"), str(root / "cache"),
                                          str(root / "empty-only")})
            self.assertEqual(folders[0]["depth"], 3)
            self.assertEqual(paths[0], str(root / "cache/v/cache"))
            self.assertNotIn(str(root / "models"), paths)
            self.assertNotIn(str(root / "blobs"), paths)
            self.assertNotIn(str(root / "logs"), paths)
            self.assertNotIn(str(root), paths)

    def test_empty_directories_skips_symlinks_and_ignored_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "root"
            (root / "keepme").mkdir(parents=True)
            (root / "empty").mkdir()
            (root / ".aicleanignore").write_text("keepme\n", encoding="utf-8")
            outside = base / "outside"
            outside.mkdir()
            (root / "linked").symlink_to(outside)

            folders, _ = aicleanup.empty_directories([str(root)])
            paths = [entry["path"] for entry in folders]

            self.assertIn(str(root / "empty"), paths)
            self.assertNotIn(str(root / "keepme"), paths)
            self.assertNotIn(str(root / "linked"), paths)
            self.assertTrue(outside.is_dir())
            self.assertTrue((root / "linked").is_symlink())

    def test_validate_empty_directory_refuses_unsafe_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "root"
            empty = root / "a/empty"
            empty.mkdir(parents=True)
            filled = root / "filled"
            filled.mkdir()
            (filled / "data.bin").write_bytes(b"x")
            layout = root / "blobs"
            layout.mkdir()
            (root / "file.txt").write_text("x", encoding="utf-8")
            outside = base / "outside"
            outside.mkdir()
            (root / "linked").symlink_to(outside)

            self.assertEqual(aicleanup.validate_empty_directory(empty, root), empty)
            for path, fragment in ((root, "scan root"), (filled, "no longer empty"), (root / "file.txt", "Only folders"),
                                   (outside, "outside the scanned"), (layout, "layout folders"),
                                   (root / "linked", "Symbolic links")):
                with self.assertRaises(RuntimeError) as error:
                    aicleanup.validate_empty_directory(path, root)
                self.assertIn(fragment, str(error.exception))
            self.assertTrue(filled.is_dir() and layout.is_dir())
