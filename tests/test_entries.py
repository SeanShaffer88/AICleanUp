"""Model entries: what a user calls "a model" and which variant is current.

Each runtime names things differently: Hugging Face has repos and revisions with a
``refs/main`` pointer, Ollama has tags where ``latest`` wins, and LM Studio has
publisher/repo folders with no pointer at all.
"""
import tempfile
import unittest
from pathlib import Path

import aicleanup

ROOT = Path("C:/models")


def finding(path, runtime, size=1024, modified="2026-01-01T00:00:00", root=ROOT, **extra):
    item = {
        "path": str(path),
        "root": str(root),
        "name": Path(path).name,
        "size": size,
        "modified": modified,
        "runtime": runtime,
        "category": "Model weights",
        "source": "Local models",
    }
    item.update(extra)
    return item


class HuggingFaceEntryTests(unittest.TestCase):
    def test_refs_main_names_the_current_revision(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "hub"
        entry = root / "models--acme--demo"
        (entry / "refs").mkdir(parents=True, exist_ok=True)
        (entry / "refs" / "main").write_text("bbbrev", encoding="utf-8")
        items = [
            finding(entry / "snapshots" / "aaarev" / "model.safetensors", "huggingface", size=100,
                    modified="2026-02-02T00:00:00", root=root),
            finding(entry / "snapshots" / "bbbrev" / "model.safetensors", "huggingface", size=100,
                    modified="2026-01-01T00:00:00", root=root),
        ]
        aicleanup.annotate_model_entries(items)
        current = [item for item in items if item.get("latest")]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["latest_by"], "ref")
        self.assertIn("/snapshots/bbbrev/", current[0]["path"].replace("\\", "/"))
        self.assertEqual({item["model_entry"] for item in items}, {"acme/demo"})
        self.assertEqual({item["entry_count"] for item in items}, {2})
        self.assertEqual({item["entry_size"] for item in items}, {200})

    def test_missing_ref_falls_back_to_the_newest_file(self):
        root = Path("/home/u/.cache/huggingface/hub")
        entry = root / "models--acme--demo"
        items = [
            finding(entry / "snapshots" / "old" / "model.safetensors", "huggingface", modified="2026-01-01T00:00:00", root=root),
            finding(entry / "snapshots" / "new" / "model.safetensors", "huggingface", modified="2026-05-05T00:00:00", root=root),
        ]
        aicleanup.annotate_model_entries(items)
        current = [item for item in items if item.get("latest")]
        self.assertEqual(current[0]["latest_by"], "modified")
        self.assertIn("/snapshots/new/", current[0]["path"].replace("\\", "/"))


class OllamaEntryTests(unittest.TestCase):
    def test_latest_tag_is_the_current_variant(self):
        root = Path("/usr/share/ollama/.ollama/models")
        items = [
            finding(root / "manifests" / "registry.ollama.ai" / "library" / "llama3" / "1b", "ollama",
                    size=400, modified="2026-06-01T00:00:00", root=root, logical_size=4_000),
            finding(root / "manifests" / "registry.ollama.ai" / "library" / "llama3" / "latest", "ollama",
                    size=450, modified="2026-01-01T00:00:00", root=root, logical_size=5_000),
        ]
        aicleanup.annotate_model_entries(items)
        self.assertEqual({item["model_entry"] for item in items}, {"llama3"})
        self.assertEqual({item["variant"] for item in items}, {"1b", "latest"})
        current = [item for item in items if item.get("latest")]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["variant"], "latest")
        self.assertEqual(current[0]["latest_by"], "tag")
        self.assertEqual(current[0]["entry_size"], 9_000)

    def test_untagged_entries_use_the_newest_manifest(self):
        root = Path("/usr/share/ollama/.ollama/models")
        items = [
            finding(root / "manifests" / "example.com" / "team" / "qwen3" / "8b", "ollama",
                    modified="2026-03-03T00:00:00", root=root),
            finding(root / "manifests" / "example.com" / "team" / "qwen3" / "14b", "ollama",
                    modified="2026-04-04T00:00:00", root=root),
        ]
        aicleanup.annotate_model_entries(items)
        current = [item for item in items if item.get("latest")]
        self.assertEqual(current[0]["variant"], "14b")
        self.assertEqual(current[0]["latest_by"], "modified")


class LmStudioEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/home/u/.lmstudio/models")

    def _items(self):
        repo = self.root / "lmstudio-community" / "Llama-3.2-1B-Instruct-GGUF"
        return [
            finding(repo / "Llama-3.2-1B-Instruct-Q4_K_M.gguf", "lmstudio", size=800,
                    modified="2026-03-01T00:00:00", root=self.root),
            finding(repo / "Llama-3.2-1B-Instruct-Q8_0.gguf", "lmstudio", size=1_300,
                    modified="2026-05-01T00:00:00", root=self.root),
        ]

    def test_quantizations_are_variants_of_one_entry(self):
        items = self._items()
        aicleanup.annotate_model_entries(items)
        self.assertEqual({item["model_entry"] for item in items}, {"lmstudio-community/Llama-3.2-1B-Instruct-GGUF"})
        self.assertEqual({item["variant"] for item in items}, {"Q4_K_M", "Q8_0"})
        self.assertEqual({item["entry_count"] for item in items}, {2})
        self.assertEqual({item["entry_size"] for item in items}, {2_100})

    def test_newest_variant_is_flagged_as_current(self):
        items = self._items()
        aicleanup.annotate_model_entries(items)
        current = [item for item in items if item.get("latest")]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["variant"], "Q8_0")
        self.assertEqual(current[0]["latest_by"], "modified")

    def test_separate_repos_are_separate_entries(self):
        items = self._items()
        other = self.root / "bartowski" / "Mistral-7B-GGUF" / "Mistral-7B-Q4_K_M.gguf"
        items.append(finding(other, "lmstudio", size=4_000, root=self.root))
        aicleanup.annotate_model_entries(items)
        groups = {}
        for item in items:
            groups.setdefault(item["model_entry"], []).append(item)
        self.assertEqual(set(groups), {"lmstudio-community/Llama-3.2-1B-Instruct-GGUF", "bartowski/Mistral-7B-GGUF"})
        self.assertEqual([item["entry_count"] for item in groups["bartowski/Mistral-7B-GGUF"]], [1])
        self.assertTrue(groups["bartowski/Mistral-7B-GGUF"][0]["latest"])

    def test_files_outside_the_store_are_left_unnamed(self):
        item = finding(Path("/home/u/Downloads/loose.gguf"), "lmstudio", root=self.root)
        aicleanup.annotate_model_entries([item])
        self.assertNotIn("model_entry", item)
        self.assertNotIn("latest", item)


class PlainRuntimeEntryTests(unittest.TestCase):
    def test_other_runtimes_use_the_parent_folder_as_the_entry(self):
        root = Path("/home/u/models")
        item = finding(root / "ggml" / "whisper-large-v3-q5_0.bin", "whisper", root=root)
        aicleanup.annotate_model_entries([item])
        self.assertEqual(item["model_entry"], "ggml")
        self.assertEqual(item["variant"], "Q5_0")
        self.assertTrue(item["latest"])


if __name__ == "__main__":
    unittest.main()
