# AICleanUp

A local-first disk analyzer for finding caches and temporary artifacts left by AI clients, developer tools, and local model runtimes. The GUI is inspired by the dense, practical layout of disk-usage analyzers such as WizTree: a location tree, sortable file list, at-a-glance category totals, and a space map.

## Launch the GUI

Requires Python 3.9 or newer. From this folder:

```bash
python3 aicleanup.py --gui
```

The interface opens at `http://127.0.0.1:8765`. The server binds to loopback by default so other devices cannot access the scanner. To use a different port:

```bash
python3 aicleanup.py --gui --port 8766
```

For a trusted reverse-proxy preview only, bind all interfaces explicitly with `--host 0.0.0.0`. Do not expose the port to an untrusted network. The API rejects cross-origin write requests and oversized request bodies. Scanning and cleanup run locally; no file listing is uploaded.

## Scan

- **Scan** checks known AI-client and runtime locations in the current user's home directory.
- Enter a project folder path to also look for stale files in `.ai_scratch/` and `tmp_gen/`, plus `.diff`, `.patch`, `.bak`, and similar backup files.
- `.aicleanignore` files are read from each scanned root. Add one glob pattern per line; blank lines and lines starting with `#` are ignored. The scanner also skips common dependency/version-control directories and credential-like filenames.
- Model weights are excluded by default. Enable **Include model weights** to inspect recognized weight files; they are always unselected initially.
- Only obvious cache files are preselected. App data, developer histories/configuration, superseded outputs, and model weights require explicit selection. All choices are visible in the file list and space totals; review paths before confirming cleanup.

## Supported client locations

The scanner derives Windows roaming/local roots from `%APPDATA%` and `%LOCALAPPDATA%`, including ChatGPT and Claude Store/MSIX user-data subtrees, Copilot data and its `INetCache` folder, Comet, Gemini Desktop, DeepSeek, Grok, AnythingLLM, Cherry Studio, and Jan. On macOS it checks the corresponding `~/Library/Application Support` folders and discovers ChatGPT sandbox data under `~/Library/Containers/*/Data/Library/Application Support/ChatGPT`. Existing ChatGPT, Claude, Replit, Codex, and local-model paths remain supported.

Installer binaries and `Program Files`/`AnthropicClaude/app-*` directories are intentionally not scanned. The name `LocalCache` by itself does not make a folder eligible for cleanup; only recognized cache subdirectories are categorized and preselected as caches. App data such as IndexedDB remains unselected for review.

## Cleanup safety

AICleanUp does not permanently delete files. Selected files are moved to the operating system Trash/Recycle Bin, and an audit manifest is written to `~/.aicleanup/audit/cleanup_audit_<timestamp>.json`. On Linux, the XDG Trash layout is used directly. On Windows and macOS, install the optional `send2trash` dependency to route files to the native bin:

```bash
python3 -m pip install -r requirements.txt
```

If the native trash operation is unavailable, the app reports an error and leaves the files in place. Review the selected paths before confirming cleanup. Avoid running as root/Administrator.

## CLI dry run

The command-line interface only reports candidate totals and never modifies files:

```bash
python3 aicleanup.py --dry-run
python3 aicleanup.py --workspace ~/src/my-project
python3 aicleanup.py --include-models
```
