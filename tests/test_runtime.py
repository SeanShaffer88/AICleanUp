import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_support


class OllamaCommandTests(unittest.TestCase):
    def test_model_removal_uses_cli_with_manifest_reference_and_no_shell(self):
        with patch.object(runtime_support.shutil, "which", return_value="/usr/bin/ollama"), \
                patch.object(runtime_support, "ollama_running_models", return_value=set()), \
                patch.object(runtime_support.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "removed", "")) as run:
            output = runtime_support.remove_ollama_model("llama3.1:8b")
        self.assertEqual(output, "removed")
        run.assert_called_once_with(["/usr/bin/ollama", "rm", "llama3.1:8b"], capture_output=True, text=True, timeout=60, check=False)

    def test_loaded_model_is_refused_before_invoking_remove(self):
        with patch.object(runtime_support.shutil, "which", return_value="/usr/bin/ollama"), \
                patch.object(runtime_support, "ollama_running_models", return_value={"llama3.1:8b"}), \
                patch.object(runtime_support.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "currently loaded"):
                runtime_support.remove_ollama_model("llama3.1:8b")
        run.assert_not_called()

    def test_model_reference_is_validated_before_process_invocation(self):
        with patch.object(runtime_support.shutil, "which", return_value="/usr/bin/ollama"), \
                patch.object(runtime_support.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Invalid Ollama model"):
                runtime_support.remove_ollama_model("../../something; rm -rf /")
        run.assert_not_called()

    def test_duplicate_scan_requires_exact_hash_and_skips_existing_hardlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, exact, similar = root / "a.gguf", root / "b.gguf", root / "similar.gguf"
            first.write_bytes(b"identical model bytes")
            exact.write_bytes(b"identical model bytes")
            similar.write_bytes(b"different model bytes")
            hardlink_a, hardlink_b = root / "hard-a.safetensors", root / "hard-b.safetensors"
            hardlink_a.write_bytes(b"already linked")
            try:
                os.link(hardlink_a, hardlink_b)
            except OSError:
                self.skipTest("Hard links are unavailable")
            items = [{"path":str(path),"name":path.name,"model":True,"cleanup_action":"none"}
                     for path in (first, exact, similar, hardlink_a, hardlink_b)]
            groups = runtime_support.exact_model_duplicates(items)
            self.assertEqual(len(groups), 1)
            self.assertEqual({Path(item["path"]) for item in groups[0]["items"]}, {first, exact})

    def test_scanner_never_flags_its_own_process(self):
        class FakeProcess:
            def __init__(self, info):
                self.info = info

        fake = FakeProcess({"pid": os.getpid(), "name": "ChatGPT", "cmdline": ["chatgpt", "--serve"]})
        with patch("psutil.process_iter", return_value=[fake]), \
                patch.object(runtime_support.os, "getpid", return_value=os.getpid()):
            self.assertEqual(runtime_support.running_ai_processes(), [])

        other = FakeProcess({"pid": os.getpid() + 1, "name": "ChatGPT", "cmdline": ["chatgpt", "--serve"]})
        with patch("psutil.process_iter", return_value=[other]):
            detected = runtime_support.running_ai_processes()
        self.assertEqual([process["runtime"] for process in detected], ["chatgpt"])


if __name__ == "__main__":
    unittest.main()
