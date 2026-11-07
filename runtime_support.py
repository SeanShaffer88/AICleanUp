"""Discovery and safe-operation helpers for local model runtimes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

MODEL_SUFFIXES = {".gguf", ".safetensors", ".safetensor", ".ckpt", ".pth", ".pt"}
MODEL_DIRS = {"model", "models", "hub", "checkpoints"}
PROCESS_MARKERS = {
    "chatgpt": ("chatgpt",), "claude": ("claude",), "replit": ("replit",),
    "codex": ("codex",), "perplexity": ("perplexity", "comet"), "copilot": ("copilot",),
    "gemini": ("gemini",), "deepseek": ("deepseek", "dsh-desktop"), "grok": ("grok",),
    "anythingllm": ("anythingllm",), "cherry": ("cherrystudio", "cherry studio"),
    "jan": ("jan",), "ollama": ("ollama",), "lmstudio": ("lmstudio", "lm studio"),
    "gpt4all": ("gpt4all",), "llama.cpp": ("llama-server", "llama.cpp", "llama_cpp"),
    "localai": ("local-ai", "localai"),
    "text-generation-webui": ("text-generation-webui", "oobabooga"),
}


def is_model_weight_path(path: Path):
    suffix = path.suffix.casefold()
    directories = {part.casefold().replace("_", " ").strip() for part in path.parts[:-1]}
    return suffix in MODEL_SUFFIXES or (suffix == ".bin" and bool(directories & MODEL_DIRS))


def is_ollama_partial(path: Path):
    return "blobs" in {part.casefold() for part in path.parts[:-1]} and bool(
        re.search(r"-partial(?:-\d+)?$", path.name, re.IGNORECASE)
    )


def _lmstudio_roots(home: Path, system: str):
    override = os.environ.get("LMSTUDIO_HOME")
    if override:
        return [Path(override).expanduser()]

    defaults = [home / ".lmstudio", home / ".cache/lm-studio", home / ".cache/lm-studio/models"]
    if system == "Windows":
        defaults.extend((Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "LMStudio",
                         Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "LMStudio"))
    elif system == "Darwin":
        defaults.append(home / "Library/Application Support/LMStudio")
    if any((root if root.name.casefold() == "models" else root / "models").is_dir() for root in defaults):
        return defaults

    # LM Studio can relocate its home; consult the pointer file only when the
    # conventional model locations are absent.
    try:
        pointer = (home / ".lmstudio-home-pointer").read_text(encoding="utf-8").strip().strip('"')
        try:
            value = json.loads(pointer)
            if isinstance(value, dict):
                pointer = next((str(value[key]) for key in ("path", "home", "models", "modelPath") if value.get(key)), "")
        except json.JSONDecodeError:
            pass
        if pointer:
            candidate = Path(pointer).expanduser()
            return [candidate if candidate.is_absolute() else home / candidate]
    except OSError:
        pass
    return defaults


def extend_runtime_targets(home: Path, system: str, add):
    """Append existing runtime data/model roots via the scanner's add callback."""
    def model(label, path, runtime, partials=False):
        add(label, "Model weights", path, models=True, runtime=runtime, scan_partials=partials)

    if system == "Windows":
        roaming = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        model("GPT4All models", local / "nomic.ai/GPT4All", "gpt4all")
        model("llama.cpp models", local / "llama_models", "llama.cpp")
        add("llama.cpp cache", "Caches", local / "llama.cpp")
        model("Jan models", roaming / "Jan/data/models", "jan")
    elif system == "Darwin":
        support = home / "Library/Application Support"
        model("GPT4All models", support / "nomic.ai/GPT4All", "gpt4all")
        model("llama.cpp models", home / ".local/share/llama_models", "llama.cpp")
        add("llama.cpp cache", "Caches", home / "Library/Caches/llama.cpp")
        model("Jan models", support / "Jan/data/models", "jan")
    else:
        model("GPT4All models", home / ".local/share/nomic.ai/GPT4All", "gpt4all")
        model("llama.cpp models", home / ".local/share/llama_models", "llama.cpp")
        add("llama.cpp cache", "Caches", home / ".cache/llama.cpp")
        model("Jan models", home / ".local/share/Jan/data/models", "jan")
        model("Jan models", home / ".config/jan/models", "jan")

    ollama_path = os.environ.get("OLLAMA_MODELS") or home / ".ollama/models"
    model("Ollama models" + (" (custom)" if os.environ.get("OLLAMA_MODELS") else ""), ollama_path, "ollama", partials=True)
    if system == "Linux" and not os.environ.get("OLLAMA_MODELS"):
        model("Ollama system models", Path("/usr/share/ollama/.ollama/models"), "ollama", partials=True)

    if os.environ.get("HF_HUB_CACHE"):
        model("Hugging Face Hub models", os.environ["HF_HUB_CACHE"], "huggingface")
    else:
        hf_home = Path(os.environ.get("HF_HOME", home / ".cache/huggingface")).expanduser()
        model("Hugging Face models", hf_home / "hub", "huggingface")
    model("Transformers models", os.environ.get("TRANSFORMERS_CACHE") or home / ".cache/huggingface/transformers", "huggingface")

    for root in _lmstudio_roots(home, system):
        path = root if root.name.casefold() == "models" else root / "models"
        model("LM Studio models", path, "lmstudio")

    if os.environ.get("LOCALAI_MODELS_PATH"):
        model("LocalAI models (custom)", os.environ["LOCALAI_MODELS_PATH"], "localai")
    else:
        model("LocalAI models", home / ".localai/models", "localai")
        model("LocalAI models", Path.cwd() / "models", "localai")
    model("Text-generation-webui models", Path.cwd() / "models", "text-generation-webui")
    model("Text-generation-webui models", Path.cwd() / "data/text-generation-webui/models", "text-generation-webui")


def runtime_for_source(source):
    text = str(source).casefold()
    for runtime, markers in PROCESS_MARKERS.items():
        if any(marker in text for marker in markers):
            return runtime
    return None


def running_ai_processes():
    """Return detected AI processes, or None if psutil is unavailable."""
    try:
        import psutil
    except ImportError:
        return None
    found = []
    try:
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = process.info
                if info.get("pid") == os.getpid():
                    # AICleanUp must never match its own command line (for example a
                    # --workspace path that contains a runtime name).
                    continue
                text = " ".join([info.get("name") or ""] + (info.get("cmdline") or [])).casefold()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                return None
            for runtime, markers in PROCESS_MARKERS.items():
                if any(re.search(r"(?<![a-z0-9])" + re.escape(marker) + r"(?:\.exe)?(?![a-z0-9])", text) for marker in markers):
                    found.append({"pid": info.get("pid"), "name": info.get("name") or "unknown", "runtime": runtime})
                    break
    except Exception:
        return None
    return found


def open_file_index():
    """Map open file paths to process owners; fail closed if psutil is missing."""
    try:
        import psutil
    except ImportError:
        return None
    opened = {}
    try:
        for process in psutil.process_iter(["pid", "name"]):
            try:
                info = process.info
                for file_info in process.open_files():
                    opened[os.path.normcase(os.path.realpath(file_info.path))] = {
                        "pid": info.get("pid"), "name": info.get("name") or "unknown"
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        return None
    return opened


def process_block_reason(source, processes):
    if processes is None:
        return "Cannot verify running processes; install requirements and retry."
    runtime = runtime_for_source(source)
    active = [process for process in processes if runtime is None or process["runtime"] == runtime]
    if active:
        names = ", ".join(sorted({process["name"] for process in active}))
        return f"Close the running AI process ({names}) before cleanup."
    return None


def ollama_model_items(root: Path):
    """Build virtual model rows from manifests; never expose content-addressed blobs as files."""
    manifest_root = root / "manifests"
    if not manifest_root.is_dir():
        return []
    records, refs = [], {}
    for manifest_path in manifest_root.rglob("*"):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            relative = manifest_path.relative_to(manifest_root).parts
            if len(relative) < 3:
                continue
            model_parts = list(relative[:-1])
            if model_parts and ("." in model_parts[0] or ":" in model_parts[0]):
                model_parts.pop(0)
            if model_parts and model_parts[0] == "library":
                model_parts.pop(0)
            if not model_parts:
                continue
            model_ref = "/".join(model_parts) + ":" + manifest_path.name
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            layers = list(data.get("layers", []))
            if isinstance(data.get("config"), dict):
                layers.append(data["config"])
            sizes = {}
            for layer in layers:
                digest = layer.get("digest")
                if digest:
                    sizes[digest] = max(sizes.get(digest, 0), int(layer.get("size", 0)))
            if not sizes:
                continue
            for digest in sizes:
                refs[digest] = refs.get(digest, 0) + 1
            records.append((manifest_path, model_ref, sizes))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    items = []
    for manifest_path, model_ref, sizes in records:
        logical_size = sum(sizes.values())
        reclaimable = sum(size for digest, size in sizes.items() if refs[digest] == 1)
        cli = shutil.which("ollama")
        items.append({"id": uuid.uuid4().hex, "path": str(manifest_path), "name": model_ref,
                      "root": str(root), "source": "Ollama", "runtime": "ollama",
                      "category": "Model weights", "size": reclaimable, "logical_size": logical_size,
                      "modified": datetime.fromtimestamp(manifest_path.stat().st_mtime).astimezone().isoformat(timespec="minutes"),
                      "model": True, "risk": "re-download", "cleanup_action": "ollama-rm",
                      "model_ref": model_ref, "can_delete": bool(cli),
                      "blocked_reason": None if cli else "Ollama CLI is not installed; use Ollama’s model manager.",
                      "selected": False, "guidance": "Uses `ollama rm`; shared content-addressed layers are retained."})
    return items


class ScanCancelled(Exception):
    """Raised when the user cancels a long-running read-only scan."""


def exact_model_duplicates(items, progress=None, should_cancel=None):
    """Find byte-identical standalone model files by SHA-256; never infer duplicates by name.

    Hashing large weight files can take minutes, so the caller may pass a
    ``progress(done, total, bytes_done, bytes_total)`` callback and a
    ``should_cancel()`` probe. Cancelling raises :class:`ScanCancelled` before the
    next file is read; nothing is modified either way.
    """
    by_size = {}
    for item in items:
        if not item.get("model") or item.get("cleanup_action") != "none":
            continue
        try:
            path = Path(item["path"])
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size == 0:
                continue
            by_size.setdefault(info.st_size, []).append((item, info.st_dev, info.st_ino, info.st_mtime_ns))
        except (OSError, KeyError):
            continue
    groups = []
    candidates_by_size = [(size, candidates) for size, candidates in by_size.items() if len(candidates) >= 2]
    total_files = sum(len(candidates) for _, candidates in candidates_by_size)
    total_bytes = sum(size * len(candidates) for size, candidates in candidates_by_size)
    hashed_files = hashed_bytes = 0
    if progress:
        progress(hashed_files, total_files, hashed_bytes, total_bytes)
    for size, candidates in candidates_by_size:
        by_hash = {}
        for item, device, inode, mtime_ns in candidates:
            if should_cancel and should_cancel():
                raise ScanCancelled("Duplicate check cancelled; nothing was changed.")
            path = Path(item["path"])
            digest = hashlib.sha256()
            try:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                after = os.stat(path, follow_symlinks=False)
                if after.st_size != size or after.st_mtime_ns != mtime_ns:
                    continue
                by_hash.setdefault(digest.hexdigest(), []).append((item, device, inode))
            except OSError:
                continue
            hashed_files += 1
            hashed_bytes += size
            if progress:
                progress(hashed_files, total_files, hashed_bytes, total_bytes)
        for sha256, matches in by_hash.items():
            unique_files = {}
            for item, device, inode in matches:
                unique_files.setdefault((device, inode), item)
            if len(unique_files) > 1:
                groups.append({"sha256": sha256, "size": size, "items": list(unique_files.values())})
    return sorted(groups, key=lambda group: group["size"], reverse=True)


def ollama_running_models():
    executable = shutil.which("ollama")
    if not executable:
        raise RuntimeError("Ollama CLI is not installed or not on PATH.")
    try:
        result = subprocess.run([executable, "ps"], capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not verify loaded Ollama models: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError("Could not verify loaded Ollama models; ensure the Ollama service is available.")
    models = set()
    for line in result.stdout.splitlines()[1:]:
        fields = line.strip().split()
        if fields:
            models.add(fields[0].casefold())
    return models


def remove_ollama_model(model_ref):
    if not isinstance(model_ref, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]+", model_ref):
        raise RuntimeError("Invalid Ollama model reference.")
    executable = shutil.which("ollama")
    if not executable:
        raise RuntimeError("Ollama CLI is not installed or not on PATH.")
    if model_ref.casefold() in ollama_running_models():
        raise RuntimeError("This model is currently loaded; stop its inference session before removing it.")
    try:
        result = subprocess.run([executable, "rm", model_ref], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Ollama could not remove the model: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Ollama returned an error").strip()[:400])
    return (result.stdout or f"Removed {model_ref}").strip()

def huggingface_cli():
    """Locate the Hugging Face CLI without assuming a single binary name."""
    for name in ("hf", "huggingface-cli"):
        executable = shutil.which(name)
        if executable:
            return executable
    return None


def huggingface_prune_preview(timeout=45):
    """Run Hugging Face's own read-only prune preview.

    AICleanUp never issues a pruning command that changes files, and never
    touches the Hugging Face cache as raw files: the CLI stays authoritative.
    """
    executable = huggingface_cli()
    if not executable:
        raise RuntimeError("The Hugging Face CLI is not installed; install `huggingface_hub[cli]` to preview cache pruning.")
    command = [executable, "cache", "prune", "--dry-run"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The Hugging Face prune preview timed out; run it from a terminal instead.") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run the Hugging Face CLI: {exc}") from exc
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError((error or output or "The Hugging Face CLI returned an error.")[:800])
    return {"command": " ".join(command), "output": output or "No unused revisions reported.",
            "warnings": error, "dry_run": True}

RUNTIME_MANAGEMENT = {
    "ollama": "Remove tags with `ollama rm <model:tag>`; shared blobs stay intact.",
    "lmstudio": "Remove models in LM Studio → My Models.",
    "huggingface": "Use `hf cache rm` or review `hf cache prune --dry-run`.",
    "gpt4all": "Manage models inside the GPT4All app.",
    "llama.cpp": "Keep only the .gguf you use; llama.cpp has no model registry.",
    "text-generation-webui": "Remove models from the text-generation-webui Models tab.",
    "localai": "Remove models from LocalAI's models folder or gallery.",
    "jan": "Remove models in Jan → Hub / My Models.",
}
RUNTIME_CLI_NAMES = {"ollama": ("ollama",), "huggingface": ("hf", "huggingface-cli")}


def runtime_cli(runtime):
    """Name of the management CLI on PATH for a runtime, or None."""
    for name in RUNTIME_CLI_NAMES.get(runtime, ()):
        if shutil.which(name):
            return name
    return None


def runtime_inventory(items):
    """Summarize model stores per runtime: size, count, paths, and how to manage them."""
    summary = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        runtime = item.get("runtime")
        is_model = item.get("model") or item.get("category") == "Model weights" or item.get("cleanup_action") == "ollama-rm"
        if not runtime or not is_model:
            continue
        entry = summary.setdefault(runtime, {"runtime": runtime, "models": 0, "bytes": 0, "stores": set()})
        entry["models"] += 1
        entry["bytes"] += int(item.get("logical_size") or item.get("size") or 0)
        if item.get("root"):
            entry["stores"].add(str(item["root"]))
    inventory = []
    for runtime, entry in summary.items():
        cli = runtime_cli(runtime)
        inventory.append({
            "runtime": runtime,
            "models": entry["models"],
            "bytes": entry["bytes"],
            "stores": sorted(entry["stores"]),
            "cli": cli,
            "management": RUNTIME_MANAGEMENT.get(runtime, "Manage models with the app that owns them."),
        })
    return sorted(inventory, key=lambda item: item["bytes"], reverse=True)
