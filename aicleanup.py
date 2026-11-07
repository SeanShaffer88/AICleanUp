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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
MAX_FILES = 150_000
MAX_REQUEST_BYTES = 1_048_576
MODEL_PATTERNS = ("*.gguf", "*.safetensors", "*.safetensor", "*.ckpt", "*.pth", "*.pt", "*.bin")
CACHE_DIR_NAMES = {".cache", "cache", "caches", "code cache", "codecache", "gpu cache", "gpucache", "shader cache", "shadercache", "http cache", "httpcache", "inetcache", "webcache", "application cache"}
SENSITIVE_NAMES = {".env", ".env.local", "credentials", "credentials.json", "token", "tokens", "auth.json", "secrets.json", "id_rsa", "id_ed25519"}


def known_targets():
    """Return existing per-user data roots; application install binaries are deliberately excluded."""
    home = Path.home()
    system = platform.system()
    targets = []

    def add(label, category, path, models=False):
        p = Path(path).expanduser()
        if p.exists() and p.is_dir():
            targets.append({"label": label, "category": category, "path": p, "models": models})

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
    add("Ollama models", "Model weights", home / ".ollama/models", True)
    add("Hugging Face models", "Model weights", home / ".cache/huggingface/hub", True)
    add("LM Studio models", "Model weights", home / ".lmstudio/models", True)
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
    parts = set(path.parts)
    if any(part in {".git", "node_modules", ".venv", "venv", "__pycache__"} for part in parts):
        return True
    if path.name.lower() in SENSITIVE_NAMES or path.name.lower() == ".aicleanignore" or path.name.lower().startswith(("id_rsa", "id_ed25519")):
        return True
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def category_for(path: Path, target_category: str, workspace=False):
    directory_parts = [part.casefold().replace("_", " ").strip() for part in path.parts[:-1]]
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
    seen_paths = set()
    stop_scan = False
    # Give an explicitly selected workspace first claim on overlapping paths so
    # its scratch/backup findings retain the more specific category.
    ordered_roots = sorted(roots, key=lambda entry: not bool(entry.get("workspace", False)))
    for entry in ordered_roots:
        if stop_scan:
            break
        label = entry["label"]
        is_model = entry.get("models", False)
        if is_model and not include_models:
            continue
        try:
            root = Path(entry["path"]).resolve(strict=True)
        except OSError:
            continue
        if not root.is_dir():
            continue
        patterns = load_ignore(root)
        resolved_root = root
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            # Do not cross symlinked or excluded directories.
            dirs[:] = [d for d in dirs if not (current_path / d).is_symlink() and not ignored(current_path / d, patterns, resolved_root)]
            for filename in files:
                if total_seen >= MAX_FILES:
                    warnings.append(f"Scan stopped at {MAX_FILES:,} files for safety. Narrow the selected folder.")
                    stop_scan = True
                    break
                total_seen += 1
                p = current_path / filename
                try:
                    if p.is_symlink() or ignored(p, patterns, resolved_root): continue
                    file_stat = os.stat(p, follow_symlinks=False)
                    if not stat.S_ISREG(file_stat.st_mode): continue
                    canonical_path = str(p.resolve(strict=True))
                    if canonical_path in seen_paths: continue
                    if is_model and not any(fnmatch.fnmatch(filename.lower(), pat) for pat in MODEL_PATTERNS):
                        continue
                    cat = category_for(p, entry["category"], entry.get("workspace", False))
                    # Workspace cleanup candidates are intentionally limited to known scratch / backup patterns.
                    if entry.get("workspace"):
                        scratch = any(part in {".ai_scratch", "tmp_gen"} for part in p.parts)
                        stale_scratch = scratch and time.time() - file_stat.st_mtime > 14 * 86400 and p.suffix.lower() in {".md", ".json", ".txt", ".log"}
                        backup = p.suffix.lower() in {".diff", ".patch", ".bak", ".old"} or p.name.endswith("~")
                        if not (stale_scratch or backup): continue
                    if file_stat.st_size == 0: continue
                    findings.append({"id": uuid.uuid4().hex, "path": str(p), "name": p.name, "root": str(root),
                                     "source": label, "category": cat, "size": file_stat.st_size,
                                     "modified": datetime.fromtimestamp(file_stat.st_mtime).astimezone().isoformat(timespec="minutes"),
                                     "model": bool(is_model), "selected": cat == "Caches" and not is_model})
                    seen_paths.add(canonical_path)
                except (OSError, PermissionError):
                    continue
            if stop_scan:
                break
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


def trash_path(path: Path):
    """Move to the OS trash; refuse a permanent-delete fallback."""
    try:
        from send2trash import send2trash
        send2trash(str(path)); return "system trash"
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Could not move item to system trash: {exc}") from exc
    system = platform.system()
    if system == "Linux":
        trash = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Trash"
        files, info = trash / "files", trash / "info"
        files.mkdir(parents=True, exist_ok=True); info.mkdir(parents=True, exist_ok=True)
        dest = files / path.name
        if dest.exists(): dest = files / f"{path.stem}-{uuid.uuid4().hex[:8]}{path.suffix}"
        original = str(path.resolve())
        shutil.move(str(path), str(dest))
        escaped = original.replace("%", "%25").replace("\n", "%0A")
        (info / f"{dest.name}.trashinfo").write_text(f"[Trash Info]\nPath={escaped}\nDeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
        return "system trash"
    raise RuntimeError("Install the optional 'send2trash' package to use the system trash on this platform; nothing was deleted.")


class State:
    def __init__(self): self.findings = {}; self.roots = []
state = State()

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
        if parsed.path == "/api/state":
            return self.send_json({"items": list(state.findings.values()), "warnings": []})
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
                if target.get("models") and not include_models: continue
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
        if parsed.path == "/api/trash":
            ids = payload.get("ids", [])
            if not isinstance(ids, list) or any(not isinstance(item_id, str) for item_id in ids):
                return self.send_json({"error": "Item IDs must be provided as a list of strings."}, 400)
            if not ids: return self.send_json({"error": "Select at least one item first."}, 400)
            audit = [] ; errors = []
            for item_id in ids:
                item = state.findings.get(item_id)
                if not item: continue
                p = Path(item["path"])
                try:
                    root = Path(item["root"])
                    candidate = validate_trash_candidate(p, root)
                    route = trash_path(candidate)
                    audit.append({"path": str(candidate), "size": item["size"], "category": item["category"], "route": route})
                    state.findings.pop(item_id, None)
                except Exception as exc: errors.append({"path": item["path"], "error": str(exc)})
            audit_path = None
            if audit:
                audit_dir = Path.home() / ".aicleanup" / "audit"
                audit_path = audit_dir / f"cleanup_audit_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
                temp_path = audit_path.with_suffix(".json.tmp")
                try:
                    audit_dir.mkdir(parents=True, exist_ok=True)
                    temp_path.write_text(json.dumps({"created": datetime.now(timezone.utc).isoformat(), "items": audit}, indent=2), encoding="utf-8")
                    os.replace(temp_path, audit_path)
                except OSError as exc:
                    errors.append({"path": str(audit_path), "error": f"Items moved, but audit manifest could not be saved: {exc}"})
                    audit_path = None
            return self.send_json({"moved": audit, "errors": errors, "audit": str(audit_path) if audit_path else None})
        return self.send_json({"error": "Not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Scan AI client caches and local artifacts safely.")
    parser.add_argument("--dry-run", action="store_true", help="Print a scan summary without modifying files (default behavior).")
    parser.add_argument("--workspace", help="Also inspect a project folder for old scratch and patch files.")
    parser.add_argument("--include-models", action="store_true", help="Include model weights (not selected for cleanup by default).")
    parser.add_argument("--gui", action="store_true", help="Launch the local browser interface.")
    parser.add_argument("--port", type=int, default=8765, help="GUI port (default: 8765).")
    parser.add_argument("--host", default="127.0.0.1", help="GUI bind address (default: 127.0.0.1; use 0.0.0.0 only behind a trusted proxy).")
    args = parser.parse_args()
    if args.gui:
        server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
        print(f"AICleanUp is ready at http://127.0.0.1:{args.port} (bound to {args.host})")
        try:
            import webbrowser; webbrowser.open(f"http://127.0.0.1:{args.port}")
        except Exception: pass
        try: server.serve_forever()
        except KeyboardInterrupt: print("\\nStopping AICleanUp.")
        return
    roots = [x for x in known_targets() if args.include_models or not x.get("models")]
    if args.workspace:
        p = Path(args.workspace).expanduser()
        if not p.is_dir(): parser.error(f"Workspace is not a directory: {p}")
        roots.append({"label": "Selected workspace", "category": "Project files", "path": p, "workspace": True})
    items, warnings = scan(roots, args.include_models)
    by_category = {}
    for item in items: by_category[item["category"]] = by_category.get(item["category"], 0) + item["size"]
    print(f"AICleanUp scan complete: {len(items):,} candidate files")
    for name, size in sorted(by_category.items(), key=lambda x: -x[1]): print(f"  {name:<24} {human_size(size):>10}")
    for warning in warnings: print(f"Warning: {warning}")
    print("Dry-run only. Launch --gui to review findings and move selected items to Trash.")

if __name__ == "__main__": main()
