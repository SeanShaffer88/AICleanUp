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

    def test_runtime_inventory_groups_stores_and_reports_cli_availability(self):
        items = [
            {"runtime": "ollama", "model": True, "size": 800, "logical_size": 4096, "root": "/m/ollama"},
            {"runtime": "ollama", "model": True, "size": 200, "logical_size": 1024, "root": "/m/system"},
            {"runtime": "lmstudio", "category": "Model weights", "size": 500, "root": "/m/lmstudio"},
            {"runtime": "huggingface", "model": True, "size": 100, "root": "/m/hf"},
            {"runtime": "ollama", "category": "Caches", "size": 99999, "root": "/m/ollama"},
            {"category": "Caches", "size": 4242, "root": "/m/chatgpt"},
        ]
        with patch.object(runtime_support.shutil, "which",
                          side_effect=lambda name: "/usr/bin/ollama" if name == "ollama" else None):
            inventory = runtime_support.runtime_inventory(items)
        by_runtime = {entry["runtime"]: entry for entry in inventory}
        self.assertEqual([entry["runtime"] for entry in inventory], ["ollama", "lmstudio", "huggingface"])
        self.assertEqual(by_runtime["ollama"]["models"], 2)
        self.assertEqual(by_runtime["ollama"]["bytes"], 5120)
        self.assertEqual(by_runtime["ollama"]["stores"], ["/m/ollama", "/m/system"])
        self.assertEqual(by_runtime["ollama"]["cli"], "ollama")
        self.assertIsNone(by_runtime["lmstudio"]["cli"])
        self.assertIn("My Models", by_runtime["lmstudio"]["management"])
        self.assertIn("hf cache rm", by_runtime["huggingface"]["management"])
        self.assertNotIn("chatgpt", by_runtime)

    def test_hf_preview_is_dry_run_only_and_never_uses_a_shell(self):
        with patch.object(runtime_support.shutil, "which",
                          side_effect=lambda name: "/opt/bin/hf" if name == "hf" else None), \
                patch.object(runtime_support.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, "Would free 12.0G", "")) as run:
            result = runtime_support.huggingface_prune_preview()
        self.assertEqual(result["output"], "Would free 12.0G")
        self.assertTrue(result["dry_run"])
        run.assert_called_once_with(["/opt/bin/hf", "cache", "prune", "--dry-run"],
                                    capture_output=True, text=True, timeout=45, check=False)
        self.assertEqual(result["command"], "/opt/bin/hf cache prune --dry-run")

    def test_hf_preview_surfaces_cli_errors_and_timeouts(self):
        with patch.object(runtime_support.shutil, "which",
                          side_effect=lambda name: "/opt/bin/hf" if name == "hf" else None):
            with patch.object(runtime_support.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 1, "", "cache not found")):
                with self.assertRaisesRegex(RuntimeError, "cache not found"):
                    runtime_support.huggingface_prune_preview()
            with patch.object(runtime_support.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 45)):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    runtime_support.huggingface_prune_preview()

    def test_hf_preview_requires_the_cli(self):
        with patch.object(runtime_support.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Hugging Face CLI is not installed"):
                runtime_support.huggingface_prune_preview()

    def test_hf_preview_falls_back_to_the_legacy_cli_name(self):
        with patch.object(runtime_support.shutil, "which",
                          side_effect=lambda name: "/opt/bin/huggingface-cli" if name == "huggingface-cli" else None), \
                patch.object(runtime_support.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, "preview", "")) as run:
            runtime_support.huggingface_prune_preview()
        run.assert_called_once_with(["/opt/bin/huggingface-cli", "cache", "prune", "--dry-run"],
                                    capture_output=True, text=True, timeout=45, check=False)


if __name__ == "__main__":
    unittest.main()
