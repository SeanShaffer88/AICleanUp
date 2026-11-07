#!/usr/bin/env python3
"""AICleanUp: local AI-artifact scanner with a safe, browser-based interface."""
from __future__ import annotations

import argparse
import fnmatch
import http.server
import json
import os
import platform
import re
import shutil
import stat
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from runtime_support import (
    ScanCancelled, exact_model_duplicates, extend_runtime_targets, huggingface_prune_preview, huggingface_ref_revision,
    is_model_weight_path, is_ollama_partial, model_entry_for, ollama_model_items, runtime_inventory,
    open_file_index, process_block_reason, remove_ollama_model, running_ai_processes, runtime_for_source,
)

__version__ = "1.0.2"

# PyInstaller repoints __file__ at the unpacked bundle, so the bundled web/
# folder is found the same way whether the app runs from source or an installer.
APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
MAX_FILES = 150_000
MAX_EMPTY_DIRS = 500
# Runtime layout folders are never swept even when they look empty: the runtime
# recreates them and expects them to exist next to its content-addressed data.
KEEP_EMPTY_DIR_NAMES = {"blobs", "manifests", "snapshots", "refs", "hub", "models", "checkpoints"}
MAX_REQUEST_BYTES = 1_048_576
MIN_CLEANUP_AGE_SECONDS = 7 * 86400
MODEL_PATTERNS = ("*.gguf", "*.safetensors", "*.safetensor", "*.ckpt", "*.pth", "*.pt", "*.bin")
CACHE_DIR_NAMES = {".cache", "cache", "caches", "code cache", "codecache", "gpu cache", "gpucache", "shader cache", "shadercache", "http cache", "httpcache", "inetcache", "webcache", "application cache"}
SENSITIVE_NAMES = {".env", ".env.local", "credentials", "credentials.json", ".credentials.json", "token", "tokens", "auth.json", "secrets.json", "settings.json", "mcp.json", "config.json", "config.toml", "claude_desktop_config.json", "id_rsa", "id_ed25519"}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


def known_targets():
    """Return existing per-user data roots; application install binaries are deliberately excluded."""
    home = Path.home()
    system = platform.system()
    targets = []

    def add(label, category, path, models=False, runtime=None, scan_partials=False):
        p = Path(path).expanduser()
        if p.exists() and p.is_dir():
            targets.append({"label": label, "category": category, "path": p, "models": models,
                            "runtime": runtime, "scan_partials": scan_partials})

    if system == "Windows":
        roaming = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        packages = local / "Packages"
        # ChatGPT standalone and Store/MSIX data. Scan the user-data subtree,
        # not the package's binaries or install directory.
        add("ChatGPT", "Application data", roaming / "ChatGPT")
        add("ChatGPT (OpenAI)", "Application data", roaming / "OpenAI/ChatGPT")
        add("ChatGPT (Store)", "Application data", packages / "OpenAI.ChatGPT-Desktop_2p2nqsd0c76g0/LocalCache/Roaming/ChatGPT")
        add("Claude", "Application data", roaming / "Claude")
        add("Claude (Store)", "Application data", packages / "Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude")
        add("Claude 3P", "Application data", local / "Claude-3p")
        add("Replit", "Application data", roaming / "Replit")
        add("OpenAI / Codex", "Developer tools", local / "OpenAI/Codex")
        add("Perplexity AI", "Application data", roaming / "Perplexity AI")
        add("Comet", "Application data", local / "Perplexity/Comet/User Data")
        add("Microsoft Copilot", "Application data", local / "Microsoft/Copilot/User Data")
        add("Copilot cache", "Caches", roaming / "Microsoft/Windows/INetCache/Copilot")
        add("Gemini Desktop", "Application data", roaming / "gemini-desktop")
        add("DeepSeek Desktop", "Application data", roaming / "Deepseek-Harness-Desktop")
        add("DeepSeek Desktop", "Application data", roaming / "@deepseek-ai/dsh-desktop")
        add("DeepSeek user data", "Application data", home / ".dsh")
        add("Grok Desktop", "Application data", roaming / "grok-desktop")
        add("Grok user data", "Application data", home / ".grok-desktop")
        add("AnythingLLM", "Application data", roaming / "anythingllm-desktop/storage")
        add("Cherry Studio", "Application data", roaming / "CherryStudio")
        add("Jan", "Application data", roaming / "Jan/data")
    elif system == "Darwin":
        app_support = home / "Library/Application Support"
        caches = home / "Library/Caches"
        add("ChatGPT", "Application data", app_support / "ChatGPT")
        add("Claude", "Application data", app_support / "Claude")
        add("Replit", "Application data", app_support / "Replit")
        add("Perplexity", "Application data", app_support / "Perplexity")
        add("Comet", "Application data", app_support / "Comet")
        add("Microsoft Copilot", "Application data", app_support / "Microsoft/Copilot")
        add("Gemini Desktop", "Application data", app_support / "gemini-desktop")
        add("DeepSeek", "Application data", app_support / "DeepSeek")
        add("DeepSeek user data", "Application data", home / ".dsh")
        add("Grok user data", "Application data", home / ".grok-desktop")
        add("AnythingLLM", "Application data", app_support / "anythingllm-desktop/storage")
        add("Cherry Studio", "Application data", app_support / "CherryStudio")
        add("Jan", "Application data", app_support / "Jan/data")
        # App Store ChatGPT container IDs can differ across releases/installers.
        for container_data in home.glob("Library/Containers/*/Data/Library/Application Support/ChatGPT"):
            add("ChatGPT (sandboxed)", "Application data", container_data)
        for name, path in (("ChatGPT cache", caches / "com.openai.chat"),
                           ("Claude cache", caches / "com.anthropic.claudefordesktop")):
            add(name, "Caches", path)
    else:
        config = home / ".config"
        cache = home / ".cache"
        add("ChatGPT", "Application data", config / "ChatGPT")
        add("Claude", "Application data", config / "Claude")
        add("Replit", "Application data", config / "Replit")
        add("OpenAI / Codex", "Developer tools", home / ".openai")
        add("OpenAI cache", "Caches", cache / "openai")
    add("Claude Code", "Developer tools", home / ".claude")
    add("Claude Code legacy", "Developer tools", home / ".claude-code")
    add("Replit CLI", "Developer tools", home / ".replit")
    extend_runtime_targets(home, system, add)
    # Deduplicate paths while preserving category metadata.
    unique, seen = [], set()
    for item in targets:
        key = str(item["path"].resolve())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def load_ignore(root: Path):
    patterns = []
    ignore_file = root / ".aicleanignore"
    try:
        patterns = [line.strip() for line in ignore_file.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        pass
    return patterns


def ignored(path: Path, patterns, root: Path):
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    if any(part.casefold() in {".git", "node_modules", ".venv", "venv", "__pycache__"} for part in path.parts):
        return True
    name = path.name.casefold()
    if name in SENSITIVE_NAMES or name == ".aicleanignore" or name.endswith(SENSITIVE_SUFFIXES) or name.startswith(("id_rsa", "id_ed25519")):
        return True
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def category_for(path: Path, target_category: str, workspace=False):
    directory_parts = [part.casefold().replace("_", " ").strip() for part in path.parts[:-1]]
    if is_model_weight_path(path):
        return "Model weights"
    if workspace:
        if any(x in path.parts for x in (".ai_scratch", "tmp_gen")):
            return "Superseded outputs"
        if path.suffix.lower() in {".diff", ".patch", ".bak", ".old"} or path.name.endswith("~"):
            return "Superseded outputs"
        return "Project files"
    if target_category == "Model weights": return "Model weights"
    if target_category == "Caches" or any(part in CACHE_DIR_NAMES for part in directory_parts):
        return "Caches"
    if target_category == "Developer tools": return "Developer tools"
    return target_category


def version_key(path: Path):
    stem = path.stem.lower()
    stripped = re.sub(r"(?:[_ .-](?:v\d+|final|old|backup|copy\d*|\(\d+\)))+$", "", stem)
    return stripped, path.suffix.lower()


def scan(roots, include_models=False):
    findings, warnings, total_seen = [], [], 0
    processes = running_ai_processes()
    if processes is None:
        warnings.append("Process checks are unavailable; destructive actions will be disabled until dependencies are installed.")
    seen_paths = {}
    stop_scan = False
    # Give an explicitly selected workspace first claim on overlapping paths so
    # its scratch/backup findings retain the more specific category.
    ordered_roots = sorted(roots, key=lambda entry: not bool(entry.get("workspace", False)))
    for entry in ordered_roots:
        if stop_scan:
            break
        label = entry["label"]
        is_model = entry.get("models", False)
        partial_scan = entry.get("scan_partials", False)
        runtime = entry.get("runtime") or runtime_for_source(label)
        if is_model and not include_models and not partial_scan:
            continue
        try:
            root = Path(entry["path"]).resolve(strict=True)
        except OSError:
            continue
        if not root.is_dir():
            continue
        patterns = load_ignore(root)
        resolved_root = root
        refs_cache = {}

        def current_revision(link_path):
            """Hugging Face revision that refs/main points at for this snapshot link."""
            text = str(link_path)
            marker = f"{os.sep}snapshots{os.sep}"
            if marker not in text:
                return None
            repo = Path(text.split(marker)[0])
            key = str(repo)
            if key not in refs_cache:
                try:
                    refs_cache[key] = (repo / "refs/main").read_text(encoding="utf-8").strip() or None
                except OSError:
                    refs_cache[key] = None
            return refs_cache[key]
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            # Do not cross symlinked or excluded directories.
            dirs.sort(); files.sort()
            dirs[:] = [d for d in dirs if not (current_path / d).is_symlink() and not ignored(current_path / d, patterns, resolved_root)]
            if partial_scan and not include_models:
                dirs[:] = [d for d in dirs if d.casefold() == "blobs"] if current_path == root else []
            for filename in files:
                if partial_scan and not include_models and not re.search(r"-partial(?:-\d+)?$", filename, re.IGNORECASE):
                    continue
                if total_seen >= MAX_FILES:
                    warnings.append(f"Scan stopped at {MAX_FILES:,} files for safety. Narrow the selected folder.")
                    stop_scan = True
                    break
                total_seen += 1
                p = current_path / filename
                try:
                    if ignored(p, patterns, resolved_root): continue
                    link_target = None
                    if p.is_symlink():
                        # Hugging Face caches keep the real weights in blobs/ and expose
                        # them through per-revision symlinks, so a model folder would
                        # otherwise look empty. Follow links only inside a model root,
                        # only when the target stays inside that root, and count each
                        # physical file once so a shared blob is never double-counted.
                        if not is_model: continue
                        try:
                            link_target = p.resolve(strict=True)
                        except (OSError, RuntimeError):
                            # Broken links and link loops are skipped, never followed.
                            continue
                        if not link_target.is_file(): continue
                        if resolved_root != link_target and resolved_root not in link_target.parents: continue
                        file_stat = os.stat(link_target)
                        canonical_path = str(link_target)
                    else:
                        file_stat = os.stat(p, follow_symlinks=False)
                        if not stat.S_ISREG(file_stat.st_mode): continue
                        canonical_path = str(p.resolve(strict=True))
                    if canonical_path in seen_paths:
                        # A blob shared by several revisions is counted once. When one of
                        # those revisions is the current one (refs/main), report that path.
                        previous = seen_paths[canonical_path]
                        revision = current_revision(p) if link_target else None
                        if revision and f"{os.sep}{revision}{os.sep}" in str(p) and f"{os.sep}{revision}{os.sep}" not in previous["path"]:
                            previous.update({"path": str(p), "name": p.name, "link_target": str(link_target)})
                            seen_paths[str(canonical_path)] = previous
                        continue
                    partial = runtime == "ollama" and is_ollama_partial(p)
                    if runtime == "ollama" and not partial:
                        # Ollama blobs are shared content-addressed layers, never file-level candidates.
                        continue
                    cat = "Ollama partial downloads" if partial else category_for(p, entry["category"], entry.get("workspace", False))
                    is_weight = (is_model or cat == "Model weights") and not partial
                    if is_weight and (not include_models or not any(fnmatch.fnmatch(filename.lower(), pat) for pat in MODEL_PATTERNS)):
                        continue
                    # Workspace cleanup candidates are intentionally limited to known scratch / backup patterns.
                    if entry.get("workspace"):
                        scratch = any(part in {".ai_scratch", "tmp_gen"} for part in p.parts)
                        stale_scratch = scratch and time.time() - file_stat.st_mtime > 14 * 86400 and p.suffix.lower() in {".md", ".json", ".txt", ".log"}
                        backup = p.suffix.lower() in {".diff", ".patch", ".bak", ".old"} or p.name.endswith("~")
                        if not (stale_scratch or backup): continue
                    if file_stat.st_size == 0: continue
                    age_seconds = max(0, time.time() - file_stat.st_mtime)
                    risk = "re-download" if is_weight else "safe" if cat in {"Caches", "Ollama partial downloads"} else "inspect"
                    action = "none" if is_weight else "trash"
                    reason = "Model files must be removed through their runtime, never as raw files." if is_weight else None
                    age_blocked = not is_weight and age_seconds < MIN_CLEANUP_AGE_SECONDS
                    if age_blocked:
                        reason = "Modified within the last 7 days; blocked unless you explicitly override the age rule."
                    if not is_weight and not age_blocked:
                        reason = process_block_reason(runtime or label, processes)
                    can_delete = not is_weight and reason is None
                    guidance = None
                    if is_weight and runtime == "huggingface":
                        guidance = "Use `hf cache rm` or review `hf cache prune --dry-run`; raw model files are protected."
                    elif is_weight and runtime == "lmstudio":
                        guidance = "Remove models from LM Studio’s My Models manager."
                    elif is_weight and runtime == "ollama":
                        guidance = "Use `ollama rm model:tag`; shared blobs are protected."
                    elif partial:
                        guidance = "Orphaned partial download. Requires 7-day age and Ollama stopped."
                    finding = {"id": uuid.uuid4().hex, "path": str(p), "name": p.name, "root": str(root),
                                     "link_target": str(link_target) if link_target else None,
                                     "source": label, "runtime": runtime, "category": cat, "size": file_stat.st_size,
                                     "modified": datetime.fromtimestamp(file_stat.st_mtime).astimezone().isoformat(timespec="minutes"),
                                     "model": bool(is_weight), "risk": risk, "cleanup_action": action,
                                     "can_delete": can_delete, "blocked_reason": reason, "guidance": guidance,
                                     "age_blocked": age_blocked,
                                     "selected": cat == "Caches" and can_delete}
                    findings.append(finding)
                    seen_paths[canonical_path] = finding
                except (OSError, PermissionError, RuntimeError):
                    continue
            if stop_scan:
                break
    if include_models:
        for entry in ordered_roots:
            if entry.get("runtime") == "ollama":
                try:
                    findings.extend(ollama_model_items(Path(entry["path"]).resolve(strict=True)))
                except OSError:
                    continue
    annotate_model_entries(findings)
    # Flag versioned siblings as likely historical variants; never auto-select them.
    groups = {}
    for f in findings:
        key = (str(Path(f["path"]).parent), version_key(Path(f["path"])))
        groups.setdefault(key, []).append(f)
    for group in groups.values():
        if len(group) < 2: continue
        latest = max(group, key=lambda f: f["modified"])
        for f in group:
            if f is not latest and re.search(r"(?:v\d+|final|old|backup|copy\d*|\(\d+\))", f["name"].lower()):
                f["category"] = "Superseded outputs"
                f["selected"] = False
    findings.sort(key=lambda item: item["size"], reverse=True)
    return findings, warnings


def annotate_model_entries(findings):
    """Name each model entry and flag its current tag, revision, or newest variant.

    The strong signals are runtime-owned: an Ollama manifest tagged `latest`, or a
    Hugging Face `refs/main` pointer naming a revision folder. LM Studio has no
    pointer, so its newest file per publisher/repo is marked and labelled as such.
    """
    grouped = {}
    for item in findings:
        runtime = item.get("runtime")
        if not runtime:
            continue
        entry, variant = model_entry_for(Path(item["path"]), Path(item["root"]), runtime)
        if not entry:
            continue
        item["model_entry"] = entry
        if variant:
            item["variant"] = variant
        grouped.setdefault((runtime, entry, str(item["root"])), []).append(item)
    for (runtime, entry, root), group in grouped.items():
        entry_size = sum(int(item.get("logical_size") or item.get("size") or 0) for item in group)
        for item in group:
            item["entry_size"] = entry_size
            item["entry_count"] = len(group)
        latest, basis = None, None
        if runtime == "huggingface":
            revision = huggingface_ref_revision(root, entry)
            if revision:
                latest = next((item for item in group if f"{os.sep}snapshots{os.sep}{revision}{os.sep}" in item["path"]), None)
                basis = "ref" if latest else None
        elif runtime == "ollama":
            latest = next((item for item in group if item.get("variant") == "latest"), None)
            basis = "tag" if latest else None
        if latest is None:
            # No runtime pointer: the newest file wins, with size breaking ties
            # because coarse filesystem timestamps often match within one second.
            latest = max(group, key=lambda item: (item.get("modified") or "", int(item.get("size") or 0)))
            basis = "modified"
        latest["latest"] = True
        latest["latest_by"] = basis
    return findings


def human_size(value):
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(value)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def validate_trash_candidate(path: Path, root: Path):
    """Recheck containment and every path component immediately before trashing."""
    root = Path(root).resolve(strict=True)
    candidate = Path(os.path.abspath(os.fspath(path)))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Item is outside the scanned folder; skipped") from exc
    if not relative.parts:
        raise RuntimeError("A scan root itself cannot be moved to Trash")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("Symbolic links are not eligible for cleanup; skipped")
    resolved = candidate.resolve(strict=True)
    if resolved != candidate:
        raise RuntimeError("Item path changed since scanning; skipped")
    metadata = os.lstat(candidate)
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("Only regular files can be moved to Trash; skipped")
    return candidate


def validate_empty_directory(path: Path, root: Path):
    """Recheck containment, type, and emptiness immediately before removing a folder."""
    root_path = Path(root).resolve(strict=True)
    candidate = Path(os.path.abspath(os.fspath(path)))
    try:
        relative = candidate.relative_to(root_path)
    except ValueError as exc:
        raise RuntimeError("Folder is outside the scanned locations; skipped") from exc
    if not relative.parts:
        raise RuntimeError("A scan root itself is never removed")
    current = root_path
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("Symbolic links are not eligible for cleanup; skipped")
    if candidate.name.casefold() in KEEP_EMPTY_DIR_NAMES:
        raise RuntimeError("Runtime layout folders are kept even when empty; skipped")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Folder no longer exists; skipped") from exc
    if resolved != candidate:
        raise RuntimeError("Folder path changed since scanning; skipped")
    if not candidate.is_dir():
        raise RuntimeError("Only folders can use this action; skipped")
    try:
        next(candidate.iterdir())
    except StopIteration:
        return candidate
    except OSError as exc:
        raise RuntimeError(f"Cannot read that folder: {exc}") from exc
    raise RuntimeError("Folder is no longer empty; skipped")


def empty_directories(roots, limit=MAX_EMPTY_DIRS):
    """Folders inside the scanned roots that hold no files at all, deepest first.

    Symlinks, runtime layout folders, ignored paths, and anything holding a file
    (at any depth) are excluded, so a sweep can never strand data.
    """
    folders, truncated = [], False
    for root_value in roots:
        try:
            root = Path(root_value).resolve(strict=True)
        except OSError:
            continue
        if not root.is_dir():
            continue
        patterns = load_ignore(root)
        clean = set()
        for current, dirs, files in os.walk(root, topdown=False, followlinks=False):
            current_path = Path(current)
            if files or any((current_path / name).is_symlink() for name in dirs):
                continue
            if not all((current_path / name) in clean for name in dirs):
                continue
            if current_path != root:
                clean.add(current_path)
        for path in sorted(clean, key=lambda entry: len(entry.parts), reverse=True):
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if not relative.parts or path.name.casefold() in KEEP_EMPTY_DIR_NAMES:
                continue
            if ignored(path, patterns, root):
                continue
            folders.append({"path": str(path), "root": str(root), "depth": len(relative.parts),
                            "modified": datetime.fromtimestamp(path.lstat().st_mtime).astimezone().isoformat(timespec="minutes")})
            if len(folders) >= limit:
                return folders, True
    return folders, False


def sweep_empty_directories(paths, roots, processes=None):
    """Move the given empty folders to Trash, re-validating each one.

    Shared by the HTTP endpoint and the CLI so both paths enforce exactly the same
    containment, emptiness, symlink, layout-folder, and process rules.
    """
    if not paths:
        raise RuntimeError("Select at least one empty folder first.")
    root_paths = []
    for root_value in roots:
        try:
            root_paths.append(Path(root_value).resolve(strict=True))
        except OSError:
            continue
    if not root_paths:
        raise RuntimeError("Run a scan first; only folders inside scanned locations can be cleaned.")
    if processes is None:
        processes = running_ai_processes()
    if processes is None:
        raise RuntimeError("Cannot verify running processes; install requirements and retry.")
    moved, errors = [], []
    # Deepest first, so a swept folder is empty by the time its parent is checked.
    for value in sorted(set(paths), key=lambda entry: len(Path(entry).parts), reverse=True):
        try:
            candidate, failure = None, None
            for root in root_paths:
                try:
                    candidate = validate_empty_directory(Path(value), root)
                    runtime = runtime_for_source(str(root))
                    if runtime:
                        reason = process_block_reason(str(root), processes)
                        if reason:
                            raise RuntimeError(reason)
                    failure = None
                    break
                except RuntimeError as exc:
                    candidate, failure = None, exc
            if candidate is None:
                raise failure or RuntimeError("Folder is outside the scanned locations; skipped")
            result = trash_path(candidate)
            if isinstance(result, str):
                result = {"route": result, "destination": None}
            moved.append({"path": str(candidate), "kind": "directory", "size": 0, "category": "Empty folder",
                          "route": result.get("route"), "destination": result.get("destination"), "modified": None})
        except Exception as exc:
            errors.append({"path": value, "error": str(exc)})
    audit_path, audit_error = write_audit_manifest(moved)
    if audit_error:
        errors.append({"path": "audit manifest", "error": audit_error})
    return {"moved": moved, "errors": errors, "audit": audit_path}


def _trash_roots():
    """Candidate directories where the OS browser/desktop trash keeps files."""
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return [data_home / "Trash" / "files", Path.home() / ".Trash"]


def _locate_in_trash(name, size, directory=False):
    """Find the item we just trashed so the manifest can record a restore path."""
    for root in _trash_roots():
        candidate = root / name
        try:
            info = os.lstat(candidate)
        except OSError:
            continue
        if directory:
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                return candidate
            continue
        if stat.S_ISREG(info.st_mode) and (size is None or info.st_size == size):
            return candidate
    return None


def quarantine_move(path: Path):
    """Move into an app-managed quarantine folder when no OS trash route exists."""
    root = Path.home() / ".aicleanup" / "quarantine"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = root / f"{stamp}_{uuid.uuid4().hex[:6]}"
    folder.mkdir(parents=True, exist_ok=False)
    destination = folder / path.name
    shutil.move(str(path), str(destination))
    return {"route": "AICleanUp quarantine", "destination": str(destination)}


def trash_path(path: Path):
    """Move a file to the OS trash or app quarantine.

    Returns the route used plus the recorded destination, so the audit manifest
    can restore the file later. Never falls back to permanent deletion.
    """
    path = Path(path)
    try:
        original_info = os.lstat(path)
        original_size = original_info.st_size
        original_is_dir = stat.S_ISDIR(original_info.st_mode)
    except OSError:
        original_size, original_is_dir = None, False
    try:
        from send2trash import send2trash
    except ImportError:
        send2trash = None
    if send2trash is not None:
        try:
            send2trash(str(path))
        except Exception as exc:
            raise RuntimeError(f"Could not move item to system trash: {exc}") from exc
        located = _locate_in_trash(path.name, original_size, original_is_dir)
        return {"route": "system trash", "destination": str(located) if located else None}
    if platform.system() == "Linux":
        trash = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Trash"
        files, info = trash / "files", trash / "info"
        files.mkdir(parents=True, exist_ok=True); info.mkdir(parents=True, exist_ok=True)
        dest = files / path.name
        if dest.exists(): dest = files / f"{path.stem}-{uuid.uuid4().hex[:8]}{path.suffix}"
        original = str(path.resolve())
        shutil.move(str(path), str(dest))
        escaped = original.replace("%", "%25").replace("\n", "%0A")
        (info / f"{dest.name}.trashinfo").write_text(f"[Trash Info]\nPath={escaped}\nDeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
        return {"route": "system trash", "destination": str(dest)}
    return quarantine_move(path)


def audit_dir() -> Path:
    return Path.home() / ".aicleanup" / "audit"


def write_audit_manifest(items, prefix="cleanup_audit"):
    if not items:
        return None, None
    directory = audit_dir()
    audit_path = directory / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
    temp_path = audit_path.with_suffix(".json.tmp")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(json.dumps({"created": datetime.now(timezone.utc).isoformat(), "items": items}, indent=2), encoding="utf-8")
        os.replace(temp_path, audit_path)
        return str(audit_path), None
    except OSError as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        return None, f"Items were moved, but the audit manifest could not be saved: {exc}"


def list_audit_manifests(limit=20):
    """Summarize the most recent cleanup manifests so the GUI can offer an undo."""
    directory = audit_dir()
    if not directory.is_dir():
        return []
    summaries = []
    for path in sorted(directory.glob("cleanup_audit_*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = data.get("items") or []
        if not isinstance(items, list):
            continue
        summaries.append({"name": path.name, "created": data.get("created"), "items": len(items),
                          "restorable": sum(1 for item in items if isinstance(item, dict) and item.get("destination")
                                           and item.get("kind") != "directory"),
                          "route": sorted({str(item.get("route")) for item in items if isinstance(item, dict) and item.get("route")})})
    return summaries


def resolve_audit_manifest(value):
    """Accept a manifest name or path, but only inside AICleanUp's audit folder."""
    root = audit_dir().resolve(strict=False)
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate.name
    candidate = candidate.resolve(strict=False)
    if candidate.parent != root or not candidate.name.startswith("cleanup_audit_") or not candidate.name.endswith(".json"):
        raise RuntimeError("Unknown cleanup manifest; only manifests written by AICleanUp can be restored.")
    if not candidate.is_file():
        raise RuntimeError("That cleanup manifest no longer exists.")
    return candidate


def latest_audit_manifest():
    directory = audit_dir()
    manifests = sorted(directory.glob("cleanup_audit_*.json"), reverse=True) if directory.is_dir() else []
    if not manifests:
        raise RuntimeError("No cleanup manifests found; nothing to restore.")
    return manifests[0]


def restore_manifest(manifest, dry_run=False):
    """Move previously trashed files back to their original paths."""
    manifest = resolve_audit_manifest(manifest)
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read that cleanup manifest: {exc}") from exc
    entries = data.get("items") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("That manifest does not contain restorable items.")
    restored, skipped, errors = [], [], []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        original = Path(str(entry["path"]))
        destination = entry.get("destination")
        if entry.get("kind") == "directory":
            skipped.append({"path": str(original), "reason": "Empty folders are not restored automatically; recreate the folder or restore it from your system Trash."})
            continue
        if entry.get("route") == "ollama rm":
            skipped.append({"path": str(original), "reason": "Removed by Ollama; re-download the model with `ollama pull`."})
            continue
        if not destination:
            skipped.append({"path": str(original), "reason": "No recorded location; restore it from your operating system Trash or Recycle Bin."})
            continue
        destination_path = Path(str(destination))
        try:
            if destination_path.is_symlink():
                raise RuntimeError("The recorded location is a symbolic link; nothing was restored.")
            info = os.lstat(destination_path)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("The recorded location is not a regular file; nothing was restored.")
            if entry.get("size") is not None and info.st_size != entry["size"]:
                raise RuntimeError("The trashed file changed size since cleanup; nothing was restored.")
            if original.is_symlink() or original.exists():
                raise RuntimeError("A file already exists at the original path; nothing was overwritten.")
            if not original.parent.is_dir():
                raise RuntimeError("The original folder no longer exists; recreate it and retry.")
            if dry_run:
                restored.append({"path": str(original), "from": str(destination_path), "size": entry.get("size"),
                                 "category": entry.get("category"), "dry_run": True})
                continue
            shutil.move(str(destination_path), str(original))
            if destination_path.parent.name == "files" and destination_path.parent.parent.name == "Trash":
                # Clear the freedesktop .trashinfo entry so the restored file leaves no orphan.
                try:
                    (destination_path.parent.parent / "info" / f"{destination_path.name}.trashinfo").unlink()
                except OSError:
                    pass
            restored.append({"path": str(original), "from": str(destination_path), "size": entry.get("size"),
                             "category": entry.get("category"), "route": "restored"})
        except (OSError, RuntimeError) as exc:
            errors.append({"path": str(original), "error": str(exc)})
    return {"manifest": str(manifest), "restored": restored, "skipped": skipped, "errors": errors}


class State:
    def __init__(self): self.findings = {}; self.roots = []
state = State()


class DuplicateProgress:
    """Live counters for the read-only duplicate hash pass, readable while it runs.

    Hashing tens of gigabytes takes minutes, so the GUI polls this instead of
    staring at a frozen request. Nothing here touches files.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.cancelled = False
        self.total = self.done = self.bytes_total = self.bytes_done = 0
        self.started = self.finished = None
        self.groups = None
        self.error = None
        self.cancel_event = threading.Event()

    def begin(self):
        with self.lock:
            self.cancel_event.clear()
            self.running, self.cancelled = True, False
            self.total = self.done = self.bytes_total = self.bytes_done = 0
            self.started = datetime.now(timezone.utc).isoformat()
            self.finished, self.groups, self.error = None, None, None

    def update(self, done, total, bytes_done, bytes_total):
        with self.lock:
            self.done, self.total = done, total
            self.bytes_done, self.bytes_total = bytes_done, bytes_total

    def finish(self, groups, cancelled=False):
        with self.lock:
            self.running, self.cancelled = False, cancelled
            self.groups = groups
            self.finished = datetime.now(timezone.utc).isoformat()

    def fail(self, message):
        with self.lock:
            self.running = False
            self.error = message
            self.finished = datetime.now(timezone.utc).isoformat()

    def should_cancel(self):
        return self.cancel_event.is_set()

    def request_cancel(self):
        with self.lock:
            if not self.running:
                return False
            self.cancel_event.set()
            return True

    def snapshot(self):
        with self.lock:
            return {"running": self.running, "cancelled": self.cancelled, "done": self.done, "total": self.total,
                    "bytes_done": self.bytes_done, "bytes_total": self.bytes_total,
                    "groups": len(self.groups) if self.groups is not None else None,
                    "started": self.started, "finished": self.finished, "error": self.error}


duplicate_progress = DuplicateProgress()

MAX_IGNORE_RULES = 200
MAX_IGNORE_RULE_LENGTH = 300


def scanned_roots():
    """Every folder the current results came from, resolved and de-duplicated."""
    roots = set()
    for entry in state.roots:
        try:
            roots.add(str(Path(entry["path"]).resolve(strict=True)))
        except (OSError, KeyError, TypeError):
            continue
    for item in state.findings.values():
        root = item.get("root")
        if not root:
            continue
        try:
            roots.add(str(Path(root).resolve(strict=True)))
        except OSError:
            continue
    return sorted(roots)


def ignore_file_for(root_value):
    """Resolve a scanned folder plus its .aicleanignore, refusing anything else."""
    try:
        resolved = Path(str(root_value)).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise RuntimeError("That folder is not part of the current scan.")
    if not resolved.is_dir() or str(resolved) not in scanned_roots():
        raise RuntimeError("Ignore rules can only be edited for folders in the current scan.")
    ignore_file = resolved / ".aicleanignore"
    if ignore_file.is_symlink():
        raise RuntimeError("The .aicleanignore file is a symbolic link; refusing to edit it.")
    return resolved, ignore_file


def read_ignore_rules(ignore_file):
    try:
        return ignore_file.read_text(encoding="utf-8").splitlines()[:400]
    except OSError:
        return []


def normalize_ignore_rules(values):
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise RuntimeError("Ignore rules must be a list of text lines.")
    if len(values) > MAX_IGNORE_RULES:
        raise RuntimeError(f"Keep the ignore list to {MAX_IGNORE_RULES} lines or fewer.")
    cleaned = []
    for raw in values:
        line = raw.strip()
        if not line:
            continue
        if "\x00" in line:
            raise RuntimeError("Ignore rules cannot contain NUL characters.")
        if len(line) > MAX_IGNORE_RULE_LENGTH:
            raise RuntimeError(f"Each ignore rule must be {MAX_IGNORE_RULE_LENGTH} characters or fewer.")
        cleaned.append(line)
    return cleaned


def write_ignore_rules(root_value, values):
    """Atomically replace a scanned folder's .aicleanignore with reviewed rules."""
    root, ignore_file = ignore_file_for(root_value)
    rules = normalize_ignore_rules(values)
    if not rules:
        removed = ignore_file.exists()
        try:
            if removed:
                ignore_file.unlink()
        except OSError as exc:
            raise RuntimeError(f"Could not remove .aicleanignore: {exc}") from exc
        return {"root": str(root), "path": str(ignore_file), "rules": [], "removed": removed}
    temp = ignore_file.with_name(ignore_file.name + ".tmp")
    try:
        temp.write_text("\n".join(rules) + "\n", encoding="utf-8")
        os.replace(temp, ignore_file)
    except OSError as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        raise RuntimeError(f"Could not save .aicleanignore: {exc}") from exc
    return {"root": str(root), "path": str(ignore_file), "rules": rules, "removed": False}

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(WEB_DIR), **kwargs)
    def log_message(self, fmt, *args): print("[aicleanup] " + fmt % args)
    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/targets":
            targets = [{"label": x["label"], "category": x["category"], "path": str(x["path"]), "models": x.get("models", False)} for x in known_targets()]
            return self.send_json({"targets": targets})
        if parsed.path == "/api/meta":
            return self.send_json({"version": __version__, "platform": platform.system(),
                                   "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False))})
        if parsed.path == "/api/state":
            return self.send_json({"items": list(state.findings.values()), "warnings": []})
        if parsed.path == "/api/duplicates/progress":
            return self.send_json(duplicate_progress.snapshot())
        if parsed.path == "/api/runtimes":
            return self.send_json({"runtimes": runtime_inventory(list(state.findings.values()),
                                                                running_ai_processes(), open_file_index())})
        if parsed.path == "/api/audits":
            return self.send_json({"audits": list_audit_manifests()})
        if parsed.path == "/api/ignore":
            root_value = (parse_qs(parsed.query).get("root") or [""])[0]
            try:
                root, ignore_file = ignore_file_for(root_value)
            except RuntimeError as exc:
                return self.send_json({"error": str(exc)}, 400)
            return self.send_json({"root": str(root), "path": str(ignore_file),
                                   "exists": ignore_file.is_file(), "rules": read_ignore_rules(ignore_file)})
        return super().do_GET()
    def do_POST(self):
        parsed = urlparse(self.path)
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.lower() != self.headers.get("Host", "").lower():
            return self.send_json({"error": "Cross-origin requests are not allowed."}, 403)
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length < 0 or content_length > MAX_REQUEST_BYTES:
                self.close_connection = True
                return self.send_json({"error": "Request body is too large."}, 413)
            payload = json.loads(self.rfile.read(content_length) or b"{}")
        except (ValueError, json.JSONDecodeError): return self.send_json({"error": "Invalid request body"}, 400)
        if not isinstance(payload, dict):
            return self.send_json({"error": "Request body must be a JSON object."}, 400)
        if parsed.path == "/api/scan":
            include_models = payload.get("include_models", False)
            workspace = payload.get("workspace", "")
            if not isinstance(include_models, bool) or not isinstance(workspace, str):
                return self.send_json({"error": "Invalid scan options."}, 400)
            roots = []
            for target in known_targets():
                if target.get("models") and not include_models and not target.get("scan_partials"):
                    continue
                roots.append(target)
            workspace = workspace.strip()
            if workspace:
                p = Path(workspace).expanduser()
                if not p.exists() or not p.is_dir(): return self.send_json({"error": "That workspace folder does not exist or is not a directory."}, 400)
                roots.append({"label": "Selected workspace", "category": "Project files", "path": p, "workspace": True})
            items, warnings = scan(roots, include_models)
            state.findings = {item["id"]: item for item in items}
            state.roots = roots
            return self.send_json({"items": items, "warnings": warnings, "scanned": len(roots)})
        if parsed.path == "/api/restore":
            requested = payload.get("audit")
            if requested is not None and not isinstance(requested, str):
                return self.send_json({"error": "Invalid cleanup manifest."}, 400)
            try:
                manifest = resolve_audit_manifest(requested) if requested else latest_audit_manifest()
                result = restore_manifest(manifest)
            except RuntimeError as exc:
                return self.send_json({"error": str(exc)}, 400)
            log_path, log_error = write_audit_manifest(result["restored"], prefix="restore_log")
            if log_error:
                result["errors"].append({"path": "restore log", "error": log_error})
            result["restore_log"] = log_path
            return self.send_json(result)
        if parsed.path == "/api/ignore":
            root_value, rules = payload.get("root"), payload.get("rules")
            if not isinstance(root_value, str):
                return self.send_json({"error": "Choose a scanned folder to edit."}, 400)
            try:
                return self.send_json(write_ignore_rules(root_value, rules))
            except RuntimeError as exc:
                return self.send_json({"error": str(exc)}, 400)
        if parsed.path == "/api/hf-prune-preview":
            if not any(item.get("runtime") == "huggingface" for item in state.findings.values()):
                return self.send_json({"error": "Scan with model weights enabled first; no Hugging Face cache is in the current results."}, 400)
            try:
                return self.send_json(huggingface_prune_preview())
            except RuntimeError as exc:
                return self.send_json({"error": str(exc)}, 400)
        if parsed.path == "/api/empty-dirs":
            roots = scanned_roots()
            if not roots:
                return self.send_json({"error": "Run a scan first; empty folders are only reported inside scanned locations."}, 400)
            folders, truncated = empty_directories(roots)
            return self.send_json({"folders": folders, "count": len(folders), "truncated": truncated, "roots": roots})
        if parsed.path == "/api/trash-empty-dirs":
            paths = payload.get("paths", [])
            if not isinstance(paths, list) or any(not isinstance(value, str) for value in paths):
                return self.send_json({"error": "Folder paths must be provided as a list of strings."}, 400)
            if not paths:
                return self.send_json({"error": "Select at least one empty folder first."}, 400)
            try:
                result = sweep_empty_directories(paths, scanned_roots())
            except RuntimeError as exc:
                return self.send_json({"error": str(exc)}, 400)
            remaining, _ = empty_directories(scanned_roots())
            result["remaining"] = len(remaining)
            return self.send_json(result)
        if parsed.path == "/api/duplicates/cancel":
            cancelling = duplicate_progress.request_cancel()
            return self.send_json({"cancelling": cancelling, "progress": duplicate_progress.snapshot()})
        if parsed.path == "/api/duplicates":
            if duplicate_progress.snapshot()["running"]:
                return self.send_json({"error": "A duplicate check is already running; cancel it before starting another."}, 409)
            duplicate_progress.begin()
            try:
                groups = exact_model_duplicates(list(state.findings.values()),
                                                 progress=duplicate_progress.update,
                                                 should_cancel=duplicate_progress.should_cancel)
            except ScanCancelled:
                duplicate_progress.finish(None, cancelled=True)
                return self.send_json({"groups": [], "cancelled": True, "progress": duplicate_progress.snapshot()})
            except Exception as exc:
                duplicate_progress.fail(str(exc))
                return self.send_json({"error": str(exc)}, 500)
            duplicate_progress.finish(groups)
            return self.send_json({"groups": groups, "cancelled": False, "progress": duplicate_progress.snapshot()})
        if parsed.path == "/api/ollama-remove":
            ids = payload.get("ids", [])
            if not isinstance(ids, list) or any(not isinstance(item_id, str) for item_id in ids):
                return self.send_json({"error": "Item IDs must be provided as a list of strings."}, 400)
            if not ids: return self.send_json({"error": "Select at least one Ollama model first."}, 400)
            removed, errors = [], []
            for item_id in ids:
                item = state.findings.get(item_id)
                if not item or item.get("cleanup_action") != "ollama-rm" or not item.get("model_ref"):
                    errors.append({"path": item_id, "error": "Only manifest-backed Ollama model tags may use this action."})
                    continue
                try:
                    root = Path(item["root"]).resolve(strict=True)
                    manifest = validate_trash_candidate(Path(item["path"]), root)
                    if manifest.relative_to(root).parts[0] != "manifests":
                        raise RuntimeError("Only Ollama manifests are eligible for this action.")
                    output = remove_ollama_model(item["model_ref"])
                    removed.append({"path": str(manifest), "size": item["size"], "logical_size": item.get("logical_size", item["size"]),
                                    "category": item["category"], "route": "ollama rm", "model": item["model_ref"], "output": output})
                    state.findings.pop(item_id, None)
                except Exception as exc:
                    errors.append({"path": item.get("model_ref", item_id), "error": str(exc)})
            audit_path, audit_error = write_audit_manifest(removed)
            if audit_error: errors.append({"path": "audit manifest", "error": audit_error})
            return self.send_json({"moved": removed, "errors": errors, "audit": audit_path})
        if parsed.path == "/api/trash":
            ids = payload.get("ids", [])
            if not isinstance(ids, list) or any(not isinstance(item_id, str) for item_id in ids):
                return self.send_json({"error": "Item IDs must be provided as a list of strings."}, 400)
            if not ids: return self.send_json({"error": "Select at least one item first."}, 400)
            override_recent = payload.get("override_recent", False)
            if not isinstance(override_recent, bool):
                return self.send_json({"error": "Invalid override option."}, 400)
            processes, open_files = running_ai_processes(), open_file_index()
            audit, errors = [], []
            for item_id in ids:
                item = state.findings.get(item_id)
                if not item: continue
                p = Path(item["path"])
                try:
                    if item.get("cleanup_action") != "trash" or item.get("risk") in {"caution", "never-delete"}:
                        raise RuntimeError(item.get("blocked_reason") or "Model files must be removed through their runtime, not file-level cleanup.")
                    overridden = False
                    if not item.get("can_delete"):
                        # Only the 7-day rule may be overridden, and only for plain
                        # cleanup candidates; model weights and protected items stay blocked.
                        if override_recent and item.get("age_blocked") and not item.get("model") \
                                and item.get("cleanup_action") == "trash" and item.get("risk") not in {"caution", "never-delete"}:
                            overridden = True
                        else:
                            raise RuntimeError(item.get("blocked_reason") or "This item is not eligible for cleanup.")
                    reason = process_block_reason(item.get("runtime") or item.get("source", ""), processes)
                    if reason: raise RuntimeError(reason)
                    if open_files is None:
                        raise RuntimeError("Unable to verify open file handles; install requirements and retry.")
                    opened = open_files.get(os.path.normcase(os.path.realpath(p)))
                    if opened:
                        raise RuntimeError(f"File is open by {opened['name']} (PID {opened['pid']}); close it first.")
                    candidate = validate_trash_candidate(p, Path(item["root"]))
                    metadata = os.lstat(candidate)
                    if not overridden and time.time() - metadata.st_mtime < MIN_CLEANUP_AGE_SECONDS:
                        raise RuntimeError("File changed within the last 7 days; rescan after it becomes stale.")
                    if item.get("category") == "Ollama partial downloads" and not is_ollama_partial(candidate):
                        raise RuntimeError("The Ollama partial marker changed; rescan before cleanup.")
                    result = trash_path(candidate)
                    if isinstance(result, str):
                        result = {"route": result, "destination": None}
                    audit.append({"path": str(candidate), "size": item["size"], "category": item["category"],
                                  "route": result.get("route"), "destination": result.get("destination"),
                                  "modified": datetime.fromtimestamp(metadata.st_mtime).astimezone().isoformat(timespec="minutes"),
                                  "override": "recent-file-age" if overridden else None})
                    state.findings.pop(item_id, None)
                except Exception as exc:
                    errors.append({"path": item["path"], "error": str(exc)})
            audit_path, audit_error = write_audit_manifest(audit)
            if audit_error: errors.append({"path": "audit manifest", "error": audit_error})
            return self.send_json({"moved": audit, "errors": errors, "audit": audit_path})
        return self.send_json({"error": "Not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Scan AI client caches and local artifacts safely.")
    parser.add_argument("--dry-run", action="store_true", help="Print a scan summary without modifying files (default behavior).")
    parser.add_argument("--workspace", help="Also inspect a project folder for old scratch and patch files.")
    parser.add_argument("--include-models", action="store_true", help="Include model weights (not selected for cleanup by default).")
    parser.add_argument("--hf-prune-preview", action="store_true",
                        help="Print Hugging Face's read-only `hf cache prune --dry-run` preview and exit.")
    parser.add_argument("--restore", nargs="?", const="latest", metavar="MANIFEST",
                        help="Restore files listed in a cleanup manifest (newest by default). Preview only unless --confirm is given.")
    parser.add_argument("--confirm", action="store_true", help="Apply the --restore preview. Restores always need this flag.")
    parser.add_argument("--duplicates", action="store_true",
                        help="Hash model weights and report byte-identical SHA-256 duplicates (report only).")
    parser.add_argument("--empty-dirs", action="store_true",
                        help="List folders inside the scanned locations that hold no files at all.")
    parser.add_argument("--sweep-empty", action="store_true",
                        help="Move the listed empty folders to Trash. Preview only unless --confirm is given.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON for --duplicates or --empty-dirs.")
    parser.add_argument("--gui", action="store_true", help="Launch the local browser interface.")
    parser.add_argument("--port", type=int, default=8765, help="GUI port (default: 8765).")
    parser.add_argument("--host", default="127.0.0.1", help="GUI bind address (default: 127.0.0.1; use 0.0.0.0 only behind a trusted proxy).")
    parser.add_argument("--version", action="version", version=f"AICleanUp {__version__}")
    args = parser.parse_args()
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        # A packaged copy opened from Explorer or the Start Menu should show the
        # window, not print a scan summary into a console that closes at once.
        args.gui = True
    if args.hf_prune_preview:
        try:
            preview = huggingface_prune_preview()
        except RuntimeError as exc:
            parser.error(str(exc))
        print(f"Read-only preview: {preview['command']}")
        print(preview["output"])
        if preview.get("warnings"):
            print(f"Warning: {preview['warnings']}")
        print("AICleanUp only printed the preview; no cache files were changed.")
        return
    if args.restore:
        try:
            manifest = latest_audit_manifest() if args.restore in {"latest", ""} else resolve_audit_manifest(args.restore)
            result = restore_manifest(manifest, dry_run=not args.confirm)
        except RuntimeError as exc:
            parser.error(str(exc))
        label = "Would restore" if not args.confirm else "Restored"
        print(f"{label} {len(result['restored'])} item(s) from {result['manifest']}")
        for item in result["restored"]:
            print(f"  {human_size(item['size'] or 0):>10}  {item['path']}")
        for item in result["skipped"]:
            print(f"  skipped  {item['path']}: {item['reason']}")
        for error in result["errors"]:
            print(f"  blocked  {error['path']}: {error['error']}")
        if not args.confirm:
            print("Preview only. Re-run with --confirm to restore these files.")
        return
    if args.gui:
        server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
        print(f"AICleanUp is ready at http://127.0.0.1:{args.port} (bound to {args.host})")
        try:
            import webbrowser; webbrowser.open(f"http://127.0.0.1:{args.port}")
        except Exception: pass
        try: server.serve_forever()
        except KeyboardInterrupt: print("\\nStopping AICleanUp.")
        return
    targets = known_targets()
    roots = [x for x in targets if args.include_models or args.duplicates or not x.get("models")]
    if args.workspace:
        p = Path(args.workspace).expanduser()
        if not p.is_dir(): parser.error(f"Workspace is not a directory: {p}")
        roots.append({"label": "Selected workspace", "category": "Project files", "path": p, "workspace": True})

    if args.empty_dirs or args.sweep_empty:
        # Model stores are where empty folders pile up, so this mode ignores the
        # model-weights filter that only affects file findings.
        root_paths = [str(entry["path"]) for entry in targets]
        if args.workspace:
            root_paths.append(str(Path(args.workspace).expanduser()))
        folders, truncated = empty_directories(root_paths)
        report = {"kind": "empty-folders", "folders": folders, "count": len(folders), "truncated": truncated}
        if args.sweep_empty and args.confirm:
            if not folders:
                # Nothing to do is a success, not an error.
                report.update({"swept": True, "moved": [], "errors": [], "audit": None, "remaining": 0})
            else:
                try:
                    swept = sweep_empty_directories([folder["path"] for folder in folders], root_paths)
                except RuntimeError as exc:
                    parser.error(str(exc))
                remaining, _ = empty_directories(root_paths)
                report.update({"swept": True, "moved": swept["moved"], "errors": swept["errors"],
                               "audit": swept["audit"], "remaining": len(remaining)})
        elif args.sweep_empty:
            report["preview"] = True
        if args.json:
            print(json.dumps(report, indent=2))
            return
        if not folders:
            print("AICleanUp found no empty folders in the scanned locations.")
            return
        print(f"AICleanUp found {len(folders):,} empty folder(s)" + (" (first 500 shown)" if truncated else "") + ":")
        for folder in folders[:40]:
            print(f"  {folder['depth']} deep  {folder['path']}")
        if len(folders) > 40:
            print(f"  ...and {len(folders) - 40:,} more")
        if not args.sweep_empty:
            print("Listing only. Add --sweep-empty --confirm to move exactly these folders to Trash.")
            return
        if not args.confirm:
            print("Preview only. Re-run with --sweep-empty --confirm to move these folders to Trash.")
            return
        for error in report["errors"]:
            print(f"  skipped  {error['path']}: {error['error']}")
        print(f"Moved {len(report['moved'])} empty folder(s) to Trash; {report['remaining']} still empty.")
        if report["audit"]:
            print(f"Audit manifest: {report['audit']}")
        return

    items, warnings = scan(roots, args.include_models or args.duplicates)

    if args.duplicates:
        groups = exact_model_duplicates(list(items))
        wasted = sum(group["size"] * (len(group["items"]) - 1) for group in groups)
        if args.json:
            print(json.dumps({"kind": "exact-duplicates", "count": len(groups), "duplicated_bytes": wasted,
                              "groups": [{"sha256": group["sha256"], "size": group["size"],
                                          "copies": len(group["items"]), "duplicated_bytes": group["size"] * (len(group["items"]) - 1),
                                          "files": [{"path": item["path"], "name": item["name"], "source": item.get("source"),
                                                     "runtime": item.get("runtime")} for item in group["items"]]}
                                         for group in groups]}, indent=2))
            return
        if not groups:
            print("No byte-identical model files found; similar names are never treated as duplicates.")
            return
        print(f"AICleanUp found {len(groups)} exact SHA-256 duplicate group(s) · {human_size(wasted)} duplicated:")
        for group in groups[:20]:
            paths = "  <->  ".join(item["path"] for item in group["items"][:4])
            print(f"  {human_size(group['size']):>10} x{len(group['items'])}  {group['sha256'][:16]}...  {paths}")
        if len(groups) > 20:
            print(f"  ...and {len(groups) - 20} more group(s)")
        print("Report only: nothing was deleted, moved, or hardlinked. Remove copies through each model runtime.")
        return
    by_category = {}
    for item in items: by_category[item["category"]] = by_category.get(item["category"], 0) + item["size"]
    print(f"AICleanUp scan complete: {len(items):,} candidate files")
    for name, size in sorted(by_category.items(), key=lambda x: -x[1]): print(f"  {name:<24} {human_size(size):>10}")
    for warning in warnings: print(f"Warning: {warning}")
    print("Dry-run only. Launch --gui to review findings and move selected items to Trash.")

if __name__ == "__main__": main()
