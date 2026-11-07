import http.client
import json
import shutil
import tempfile
import threading
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

    def test_trash_endpoint_moves_only_scanned_files_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "workspace"
            root.mkdir()
            source = root / "cache.db"
            source.write_text("cache", encoding="utf-8")
            moved_to = base / "trash" / source.name
            moved_to.parent.mkdir()
            item_id = "test-item"
            item = {"id": item_id, "path": str(source), "root": str(root), "size": 5, "category": "Caches"}

            def fake_trash(path):
                shutil.move(str(path), str(moved_to))
                return "system trash"

            with patch.dict(aicleanup.state.findings, {item_id: item}, clear=True), \
                    patch.object(aicleanup.Path, "home", return_value=base / "home"), \
                    patch.object(aicleanup, "trash_path", side_effect=fake_trash):
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
