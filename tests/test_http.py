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
                return "system trash"

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
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["items"][0]["path"], str(source))


if __name__ == "__main__":
    unittest.main()
