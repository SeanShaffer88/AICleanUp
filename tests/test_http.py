import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import aicleanup


class HandlerRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), aicleanup.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, payload, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(
            self.url + "/api/scan",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=5)

    def test_rejects_cross_origin_write_requests(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request({}, "https://untrusted.example")
        self.assertEqual(error.exception.code, 403)

    def test_requires_a_json_object_and_accepts_same_origin(self):
        req = urllib.request.Request(
            self.url + "/api/scan", data=b"[]",
            headers={"Content-Type": "application/json", "Origin": self.url},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(error.exception.code, 400)

        with patch.object(aicleanup, "known_targets", return_value=[]):
            with self.request({"workspace": "", "include_models": False}, self.url) as response:
                data = json.load(response)
        self.assertEqual(data["scanned"], 0)
        self.assertEqual(data["items"], [])

    def test_rejects_oversized_request(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.putrequest("POST", "/api/scan")
        connection.putheader("Content-Length", str(aicleanup.MAX_REQUEST_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        connection.close()

    def test_duplicate_api_returns_only_byte_identical_models(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second, different = root / "one.gguf", root / "two.gguf", root / "lookalike.gguf"
            first.write_bytes(b"same exact bytes"); second.write_bytes(b"same exact bytes"); different.write_bytes(b"not the same data")
            items = {}
            for index, path in enumerate((first, second, different)):
                items[str(index)] = {"id":str(index),"path":str(path),"name":path.name,"model":True,"cleanup_action":"none"}
            with patch.dict(aicleanup.state.findings, items, clear=True):
                req = urllib.request.Request(self.url + "/api/duplicates", data=b"{}",
                    headers={"Content-Type":"application/json","Origin":self.url}, method="POST")
                with urllib.request.urlopen(req, timeout=5) as response:
                    data=json.load(response)
            self.assertEqual(len(data["groups"]),1)
            self.assertEqual({item["name"] for item in data["groups"][0]["items"]},{"one.gguf","two.gguf"})

    def test_raw_model_file_can_never_be_trashed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "models"; root.mkdir()
            model = root / "model.gguf"; model.write_bytes(b"weights")
            old = time.time() - 30 * 86400; os.utime(model, (old, old))
            item_id = "raw-model"
            item = {"id": item_id, "path": str(model), "root": str(root), "size": 7,
                    "category": "Model weights", "cleanup_action": "none", "risk": "re-download",
                    "can_delete": False, "source": "LM Studio"}
            with patch.dict(aicleanup.state.findings, {item_id: item}, clear=True), \
                    patch.object(aicleanup, "running_ai_processes", return_value=[]), \
                    patch.object(aicleanup, "open_file_index", return_value={}), \
                    patch.object(aicleanup, "trash_path") as trash:
                req = urllib.request.Request(self.url + "/api/trash", data=json.dumps({"ids": [item_id]}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST")
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.load(response)
            trash.assert_not_called()
            self.assertTrue(model.exists())
            self.assertIn("runtime", data["errors"][0]["error"])

    def test_open_handles_and_running_clients_block_trash(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "cache"; root.mkdir()
            cache_file = root / "cache.db"; cache_file.write_text("cache", encoding="utf-8")
            old = time.time() - 30 * 86400; os.utime(cache_file, (old, old))
            item_id = "open-cache"
            item = {"id": item_id, "path": str(cache_file), "root": str(root), "size": 5,
                    "category": "Caches", "cleanup_action": "trash", "risk": "safe",
                    "can_delete": True, "source": "ChatGPT"}
            with patch.dict(aicleanup.state.findings, {item_id: item}, clear=True), \
                    patch.object(aicleanup, "running_ai_processes", return_value=[{"runtime":"chatgpt", "name":"ChatGPT", "pid":9}]), \
                    patch.object(aicleanup, "open_file_index", return_value={}):
                req = urllib.request.Request(self.url + "/api/trash", data=json.dumps({"ids": [item_id]}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST")
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.load(response)
            self.assertEqual(data["moved"], [])
            self.assertIn("Close the running AI process", data["errors"][0]["error"])
            self.assertTrue(cache_file.exists())
            with patch.dict(aicleanup.state.findings, {item_id: item}, clear=True), \
                    patch.object(aicleanup, "running_ai_processes", return_value=[]), \
                    patch.object(aicleanup, "open_file_index", return_value={os.path.normcase(os.path.realpath(cache_file)):{"pid":33,"name":"Editor"}}):
                req = urllib.request.Request(self.url + "/api/trash", data=json.dumps({"ids": [item_id]}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST")
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.load(response)
            self.assertIn("File is open by Editor", data["errors"][0]["error"])
            self.assertTrue(cache_file.exists())

    def test_ollama_model_endpoint_invokes_tool_aware_removal_and_audits(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "models"
            manifest = root / "manifests/registry.ollama.ai/library/llama3.1/8b"
            manifest.parent.mkdir(parents=True); manifest.write_text("{}", encoding="utf-8")
            item_id = "ollama-tag"
            item = {"id": item_id, "path": str(manifest), "root": str(root), "size": 0, "logical_size": 12,
                    "category": "Model weights", "cleanup_action": "ollama-rm", "model_ref": "llama3.1:8b"}
            with patch.dict(aicleanup.state.findings, {item_id: item}, clear=True), \
                    patch.object(aicleanup.Path, "home", return_value=base / "home"), \
                    patch.object(aicleanup, "remove_ollama_model", return_value="removed llama3.1:8b") as remove:
                req = urllib.request.Request(self.url + "/api/ollama-remove", data=json.dumps({"ids": [item_id]}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST")
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.load(response)
            remove.assert_called_once_with("llama3.1:8b")
            self.assertEqual(data["moved"][0]["route"], "ollama rm")
            self.assertTrue(Path(data["audit"]).is_file())

    def test_trash_endpoint_moves_only_scanned_files_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "workspace"
            root.mkdir()
            source = root / "cache.db"
            source.write_text("cache", encoding="utf-8")
            old = time.time() - 8 * 86400
            os.utime(source, (old, old))
            moved_to = base / "trash" / source.name
            moved_to.parent.mkdir()
            item_id = "test-item"
            item = {"id": item_id, "path": str(source), "root": str(root), "size": 5, "category": "Caches", "cleanup_action": "trash", "risk": "safe", "can_delete": True, "source": "workspace"}

            def fake_trash(path):
                shutil.move(str(path), str(moved_to))
                return {"route": "system trash", "destination": str(moved_to)}

            with patch.dict(aicleanup.state.findings, {item_id: item}, clear=True), \
                    patch.object(aicleanup.Path, "home", return_value=base / "home"), \
                    patch.object(aicleanup, "trash_path", side_effect=fake_trash), \
                    patch.object(aicleanup, "running_ai_processes", return_value=[]), \
                    patch.object(aicleanup, "open_file_index", return_value={}):
                req = urllib.request.Request(
                    self.url + "/api/trash", data=json.dumps({"ids": [item_id]}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    result = json.load(response)

            self.assertFalse(source.exists())
            self.assertTrue(moved_to.exists())
            self.assertEqual(len(result["moved"]), 1)
            manifest = Path(result["audit"])
            self.assertTrue(manifest.is_file())
            entry = json.loads(manifest.read_text(encoding="utf-8"))["items"][0]
            self.assertEqual(entry["path"], str(source))
            self.assertEqual(entry["destination"], str(moved_to))

    def test_ignore_rules_can_be_read_and_saved_for_scanned_folders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "cache"
            root.mkdir()
            kept = root / "keep.log"
            kept.write_text("keep", encoding="utf-8")
            dropped = root / "drop.log"
            dropped.write_text("drop", encoding="utf-8")
            roots = [{"label": "workspace", "category": "Caches", "path": root}]

            def get_ignore(value):
                query = urllib.parse.urlencode({"root": str(value)})
                with urllib.request.urlopen(f"{self.url}/api/ignore?{query}", timeout=5) as response:
                    return json.load(response)

            def post_ignore(payload):
                req = urllib.request.Request(
                    self.url + "/api/ignore", data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.load(response)

            findings = {"probe": {"id": "probe", "path": str(kept), "root": str(root), "size": 4}}
            with patch.object(aicleanup.state, "roots", roots), \
                    patch.dict(aicleanup.state.findings, findings, clear=True):
                before = get_ignore(root)
                self.assertFalse(before["exists"])
                self.assertEqual(before["rules"], [])

                saved = post_ignore({"root": str(root), "rules": ["# generated by AICleanUp", " drop.log ", ""]})
                self.assertEqual(saved["rules"], ["# generated by AICleanUp", "drop.log"])
                self.assertTrue((root / ".aicleanignore").is_file())
                self.assertFalse((root / ".aicleanignore.tmp").exists())
                self.assertEqual((root / ".aicleanignore").read_text(encoding="utf-8"), "# generated by AICleanUp\ndrop.log\n")

                with patch.object(aicleanup, "running_ai_processes", return_value=[]):
                    rescanned, _ = aicleanup.scan(roots)
                self.assertEqual([item["name"] for item in rescanned], ["keep.log"])

                cleared = post_ignore({"root": str(root), "rules": []})
                self.assertTrue(cleared["removed"])
                self.assertFalse((root / ".aicleanignore").exists())

    def test_ignore_rules_reject_foreign_folders_and_unsafe_input(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            allowed, foreign = base / "allowed", base / "foreign"
            allowed.mkdir(); foreign.mkdir()

            def post_ignore(payload):
                req = urllib.request.Request(
                    self.url + "/api/ignore", data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req, timeout=5)
                return error.exception.code, json.load(error.exception)

            roots = [{"label": "workspace", "category": "Caches", "path": allowed}]
            with patch.object(aicleanup.state, "roots", roots), patch.dict(aicleanup.state.findings, {}, clear=True):
                code, body = post_ignore({"root": str(foreign), "rules": ["secret.log"]})
                self.assertEqual(code, 400)
                self.assertIn("current scan", body["error"])
                self.assertFalse((foreign / ".aicleanignore").exists())

                code, body = post_ignore({"root": str(allowed), "rules": "drop.log"})
                self.assertEqual(code, 400)
                self.assertIn("list of text lines", body["error"])

                code, body = post_ignore({"root": str(allowed), "rules": ["x" * 400]})
                self.assertEqual(code, 400)
                self.assertIn("characters or fewer", body["error"])

                code, body = post_ignore({"root": str(allowed), "rules": [f"rule-{index}" for index in range(250)]})
                self.assertEqual(code, 400)
                self.assertIn("lines or fewer", body["error"])

                (allowed / ".aicleanignore").symlink_to(foreign / "elsewhere")
                code, body = post_ignore({"root": str(allowed), "rules": ["drop.log"]})
                self.assertEqual(code, 400)
                self.assertIn("symbolic link", body["error"])

    def test_hf_preview_requires_a_huggingface_scan_and_returns_output(self):
        def post():
            req = urllib.request.Request(
                self.url + "/api/hf-prune-preview", data=b"{}",
                headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)

        with patch.dict(aicleanup.state.findings, {"x": {"id": "x", "runtime": "lmstudio"}}, clear=True):
            with self.assertRaises(urllib.error.HTTPError) as missing:
                post()
        self.assertEqual(missing.exception.code, 400)

        preview = {"command": "hf cache prune --dry-run", "output": "Would delete 3 revisions", "warnings": "", "dry_run": True}
        with patch.dict(aicleanup.state.findings, {"y": {"id": "y", "runtime": "huggingface"}}, clear=True), \
                patch.object(aicleanup, "huggingface_prune_preview", return_value=preview) as preview_call:
            data = post()
        preview_call.assert_called_once_with()
        self.assertEqual(data["output"], "Would delete 3 revisions")
        self.assertTrue(data["dry_run"])

        with patch.dict(aicleanup.state.findings, {"y": {"id": "y", "runtime": "huggingface"}}, clear=True), \
                patch.object(aicleanup, "huggingface_prune_preview", side_effect=RuntimeError("The Hugging Face CLI is not installed")):
            with self.assertRaises(urllib.error.HTTPError) as broken:
                post()
        self.assertEqual(broken.exception.code, 400)

    def test_recent_cache_needs_an_explicit_age_override(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "cache"
            root.mkdir()
            recent = root / "fresh.db"
            recent.write_text("fresh", encoding="utf-8")
            trashed = base / "trash" / recent.name
            item_id = "recent-cache"
            item = {"id": item_id, "path": str(recent), "root": str(root), "size": 5, "category": "Caches",
                    "cleanup_action": "trash", "risk": "safe", "can_delete": False, "age_blocked": True,
                    "blocked_reason": "Modified within the last 7 days; blocked unless you explicitly override the age rule.",
                    "source": "workspace"}

            def fake_trash(path):
                trashed.parent.mkdir(exist_ok=True)
                shutil.move(str(path), str(trashed))
                return {"route": "system trash", "destination": str(trashed)}

            def post(payload):
                req = urllib.request.Request(
                    self.url + "/api/trash", data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.load(response)

            with patch.dict(aicleanup.state.findings, {item_id: dict(item)}, clear=True), \
                    patch.object(aicleanup.Path, "home", return_value=base / "home"), \
                    patch.object(aicleanup, "trash_path", side_effect=fake_trash), \
                    patch.object(aicleanup, "running_ai_processes", return_value=[]), \
                    patch.object(aicleanup, "open_file_index", return_value={}):
                blocked = post({"ids": [item_id]})
                self.assertTrue(recent.exists())
                self.assertIn("7 days", blocked["errors"][0]["error"])

                with self.assertRaises(urllib.error.HTTPError) as bad:
                    post({"ids": [item_id], "override_recent": "yes"})
                self.assertEqual(bad.exception.code, 400)

                accepted = post({"ids": [item_id], "override_recent": True})
                manifest = json.loads(Path(accepted["audit"]).read_text(encoding="utf-8"))

            self.assertFalse(recent.exists())
            self.assertTrue(trashed.exists())
            self.assertEqual(len(accepted["moved"]), 1)
            self.assertEqual(manifest["items"][0]["override"], "recent-file-age")

    def test_age_override_never_unblocks_models_or_protected_items(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "models"
            root.mkdir()
            weight = root / "model.gguf"
            weight.write_bytes(b"weights")
            weight_id, block_id = "recent-weight", "recent-blocked"
            findings = {
                weight_id: {"id": weight_id, "path": str(weight), "root": str(root), "size": 7,
                            "category": "Model weights", "cleanup_action": "none", "risk": "re-download",
                            "can_delete": False, "age_blocked": False, "model": True, "source": "LM Studio"},
                block_id: {"id": block_id, "path": str(weight), "root": str(root), "size": 7,
                           "category": "Caches", "cleanup_action": "trash", "risk": "safe",
                           "can_delete": False, "age_blocked": False, "model": False,
                           "blocked_reason": "Close the running AI process (ChatGPT) before cleanup.",
                           "source": "ChatGPT"},
            }
            with patch.dict(aicleanup.state.findings, findings, clear=True), \
                    patch.object(aicleanup, "running_ai_processes", return_value=[]), \
                    patch.object(aicleanup, "open_file_index", return_value={}), \
                    patch.object(aicleanup, "trash_path") as trash:
                req = urllib.request.Request(
                    self.url + "/api/trash", data=json.dumps({"ids": [weight_id, block_id], "override_recent": True}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    result = json.load(response)
            trash.assert_not_called()
            self.assertEqual(result["moved"], [])
            self.assertEqual(len(result["errors"]), 2)
            self.assertTrue(weight.exists())

    def test_restore_endpoint_returns_files_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            home, root = base / "home", base / "workspace"
            root.mkdir()
            source = root / "cache.db"
            source.write_text("cache", encoding="utf-8")
            old = time.time() - 8 * 86400
            os.utime(source, (old, old))
            trashed = base / "trash" / source.name
            item_id = "restore-item"
            item = {"id": item_id, "path": str(source), "root": str(root), "size": 5, "category": "Caches",
                    "cleanup_action": "trash", "risk": "safe", "can_delete": True, "source": "workspace"}

            def fake_trash(path):
                trashed.parent.mkdir(exist_ok=True)
                shutil.move(str(path), str(trashed))
                return {"route": "system trash", "destination": str(trashed)}

            with patch.dict(aicleanup.state.findings, {item_id: item}, clear=True), \
                    patch.object(aicleanup.Path, "home", return_value=home), \
                    patch.object(aicleanup, "trash_path", side_effect=fake_trash), \
                    patch.object(aicleanup, "running_ai_processes", return_value=[]), \
                    patch.object(aicleanup, "open_file_index", return_value={}):
                req = urllib.request.Request(
                    self.url + "/api/trash", data=json.dumps({"ids": [item_id]}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    cleanup = json.load(response)
                manifest_name = Path(cleanup["audit"]).name

                with urllib.request.urlopen(self.url + "/api/audits", timeout=5) as response:
                    audits = json.load(response)["audits"]
                self.assertEqual(audits[0]["name"], manifest_name)
                self.assertEqual(audits[0]["restorable"], 1)

                req = urllib.request.Request(
                    self.url + "/api/restore", data=json.dumps({"audit": manifest_name}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    restored = json.load(response)
                self.assertTrue(source.exists())
                self.assertFalse(trashed.exists())
                self.assertEqual(restored["restored"][0]["path"], str(source))
                self.assertEqual(source.read_text(encoding="utf-8"), "cache")
                self.assertTrue(Path(restored["restore_log"]).is_file())

                # Trash it again, then block the restore because the path is occupied.
                shutil.move(str(source), str(trashed))
                source.write_text("new file", encoding="utf-8")
                req = urllib.request.Request(
                    self.url + "/api/restore", data=json.dumps({"audit": manifest_name}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    blocked = json.load(response)
            self.assertEqual(blocked["restored"], [])
            self.assertIn("already exists", blocked["errors"][0]["error"])
            self.assertEqual(source.read_text(encoding="utf-8"), "new file")
            self.assertTrue(trashed.exists())

    def test_restore_rejects_unknown_or_foreign_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / ".aicleanup" / "audit").mkdir(parents=True)
            (home / "outside").mkdir()
            foreign = home / "outside" / "cleanup_audit_20200101_000000_000000.json"
            foreign.write_text('{"items": []}', encoding="utf-8")
            for value in ("../outside/cleanup_audit_20200101_000000_000000.json", str(foreign), "missing.json"):
                req = urllib.request.Request(
                    self.url + "/api/restore", data=json.dumps({"audit": value}).encode(),
                    headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
                )
                with patch.object(aicleanup.Path, "home", return_value=home):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(req, timeout=5)
                self.assertEqual(error.exception.code, 400)

            req = urllib.request.Request(
                self.url + "/api/restore", data=json.dumps({"audit": 5}).encode(),
                headers={"Content-Type": "application/json", "Origin": self.url}, method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
