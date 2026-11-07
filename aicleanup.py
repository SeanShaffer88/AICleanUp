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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
MAX_FILES = 150_000
MODEL_PATTERNS = ("*.gguf", "*.safetensors", "*.safetensor", "*.ckpt", "*.pth", "*.pt", "*.bin")
SENSITIVE_NAMES = {".env", ".env.local", "credentials", "credentials.json", "token", "tokens", "auth.json", "secrets.json", "id_rsa", "id_ed25519"}


def known_targets():
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
        add("ChatGPT", "Application data", roaming / "ChatGPT")
        add("Claude", "Application data", roaming / "Claude")
        add("Replit", "Application data", roaming / "Replit")
        add("OpenAI / Codex", "Developer tools", local / "OpenAI/Codex")
    elif system == "Darwin":
        app_support = home / "Library/Application Support"
        caches = home / "Library/Caches"
        add("ChatGPT", "Application data", app_support / "ChatGPT")
        add("Claude", "Application data", app_support / "Claude")
        add("Replit", "Application data", app_support / "Replit")
        for name, p in (("ChatGPT cache", caches / "com.openai.chat"), ("Claude cache", caches / "com.anthropic.claudefordesktop")):
            add(name, "Caches", p)
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
            seen.add(key); unique.append(item)
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
    directory_parts = [part.lower() for part in path.parts[:-1]]
    if workspace:
        if any(x in path.parts for x in (".ai_scratch", "tmp_gen")):
            return "Superseded outputs"
        if path.suffix.lower() in {".diff", ".patch", ".bak", ".old"} or path.name.endswith("~"):
            return "Superseded outputs"
        return "Project files"
    if target_category == "Model weights": return "Model weights"
    if target_category == "Caches" or any(x in part for part in directory_parts for x in ("cache", "code cache", "gpu cache", "httpcache", "shadercache")):
        return "Caches"
    if target_category == "Developer tools": return "Developer tools"
    return target_category


def version_key(path: Path):
    stem = path.stem.lower()
    stripped = re.sub(r"(?:[_ .-](?:v\d+|final|old|backup|copy\d*|\(\d+\)))+$", "", stem)
    return stripped, path.suffix.lower()


def scan(roots, include_models=False):
    findings, warnings, total_seen = [], [], 0
    for entry in roots:
        root, label = entry["path"], entry["label"]
        is_model = entry.get("models", False)
        if is_model and not include_models:
            continue
        patterns = load_ignore(root)
        try:
            resolved_root = root.resolve(strict=True)
        except OSError:
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            # Do not cross symlinked or excluded directories.
            dirs[:] = [d for d in dirs if not (current_path / d).is_symlink() and not ignored(current_path / d, patterns, resolved_root)]
            for filename in files:
                total_seen += 1
                if total_seen > MAX_FILES:
                    warnings.append(f"Scan stopped at {MAX_FILES:,} files for safety. Narrow the selected folder.")
                    break
                p = current_path / filename
                try:
                    if p.is_symlink() or ignored(p, patterns, resolved_root): continue
                    stat = os.stat(p, follow_symlinks=False)
                    if not p.is_file(): continue
                    if is_model and not any(fnmatch.fnmatch(filename.lower(), pat) for pat in MODEL_PATTERNS):
                        continue
                    cat = category_for(p, entry["category"], entry.get("workspace", False))
                    # Workspace cleanup candidates are intentionally limited to known scratch / backup patterns.
                    if entry.get("workspace"):
                        scratch = any(part in {".ai_scratch", "tmp_gen"} for part in p.parts)
                        stale_scratch = scratch and time.time() - stat.st_mtime > 14 * 86400 and p.suffix.lower() in {".md", ".json", ".txt", ".log"}
                        backup = p.suffix.lower() in {".diff", ".patch", ".bak", ".old"} or p.name.endswith("~")
                        if not (stale_scratch or backup): continue
                    if stat.st_size == 0: continue
                    findings.append({"id": uuid.uuid4().hex, "path": str(p), "name": p.name, "root": str(root),
                                     "source": label, "category": cat, "size": stat.st_size,
                                     "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="minutes"),
                                     "model": bool(is_model), "selected": cat == "Caches" and not is_model})
                except (OSError, PermissionError):
                    continue
            if total_seen > MAX_FILES: break
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
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        except (ValueError, json.JSONDecodeError): return self.send_json({"error": "Invalid request body"}, 400)
        if parsed.path == "/api/scan":
            roots = []
            for target in known_targets():
                if target.get("models") and not payload.get("include_models"): continue
                roots.append(target)
            workspace = payload.get("workspace", "").strip()
            if workspace:
                p = Path(workspace).expanduser()
                if not p.exists() or not p.is_dir(): return self.send_json({"error": "That workspace folder does not exist or is not a directory."}, 400)
                roots.append({"label": "Selected workspace", "category": "Project files", "path": p, "workspace": True})
            items, warnings = scan(roots, payload.get("include_models", False))
            state.findings = {item["id"]: item for item in items}
            state.roots = roots
            return self.send_json({"items": items, "warnings": warnings, "scanned": len(roots)})
        if parsed.path == "/api/trash":
            ids = payload.get("ids", [])
            if not ids: return self.send_json({"error": "Select at least one item first."}, 400)
            audit = [] ; errors = []
            for item_id in ids:
                item = state.findings.get(item_id)
                if not item: continue
                p = Path(item["path"])
                try:
                    # Verify the current target still lives within the exact root we scanned.
                    root = Path(item["root"]).resolve(strict=True); resolved = p.resolve(strict=True)
                    if root != resolved and root not in resolved.parents: raise RuntimeError("Item is outside the scanned folder; skipped")
                    if not resolved.is_file() or resolved.is_symlink(): raise RuntimeError("Not a regular file; skipped")
                    route = trash_path(resolved)
                    audit.append({"path": str(resolved), "size": item["size"], "category": item["category"], "route": route})
                    state.findings.pop(item_id, None)
                except Exception as exc: errors.append({"path": item["path"], "error": str(exc)})
            audit_dir = Path.home() / ".aicleanup" / "audit"
            audit_dir.mkdir(parents=True, exist_ok=True)
            audit_path = audit_dir / f"cleanup_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            if audit: audit_path.write_text(json.dumps({"created": datetime.now(timezone.utc).isoformat(), "items": audit}, indent=2), encoding="utf-8")
            return self.send_json({"moved": audit, "errors": errors, "audit": str(audit_path) if audit else None})
        return self.send_json({"error": "Not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Scan AI client caches and local artifacts safely.")
    parser.add_argument("--dry-run", action="store_true", help="Print a scan summary without modifying files (default behavior).")
    parser.add_argument("--workspace", help="Also inspect a project folder for old scratch and patch files.")
    parser.add_argument("--include-models", action="store_true", help="Include model weights (not selected for cleanup by default).")
    parser.add_argument("--gui", action="store_true", help="Launch the local browser interface.")
    parser.add_argument("--port", type=int, default=8765, help="GUI port (default: 8765).")
    args = parser.parse_args()
    if args.gui:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
        print(f"AICleanUp is ready at http://127.0.0.1:{args.port}")
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
