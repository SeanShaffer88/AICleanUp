# AICleanUp

A local-first disk analyzer for finding caches and temporary artifacts left by AI clients, developer tools, and local model runtimes. The GUI is inspired by the dense, practical layout of disk-usage analyzers such as WizTree: a location tree, sortable file list, at-a-glance category totals, and a space map.

## Launch the GUI

Requires Python 3.9 or newer. Install the runtime safety/trash helpers, then launch from this folder:

```bash
python3 -m pip install -r requirements.txt
python3 aicleanup.py --gui
```

The interface opens at `http://127.0.0.1:8765`. The server binds to loopback by default so other devices cannot access the scanner. To use a different port:

```bash
python3 aicleanup.py --gui --port 8766
```

For a trusted reverse-proxy preview only, bind all interfaces explicitly with `--host 0.0.0.0`. Do not expose the port to an untrusted network. The API rejects cross-origin write requests and oversized request bodies. Scanning and cleanup run locally; no file listing is uploaded.

## Workspace layout

The GUI follows the dense, panel-driven layout of a disk-usage analyzer such as WizTree:

- **Tree view** (left): a real folder hierarchy built from the scan, sized and sorted by weight, with expand/collapse. Selecting a node scopes the whole workspace to that folder; the active path stays unfolded and highlighted.
- **Filter panel** (left): risk chips (safe, inspect, re-download, recent) with live counts, plus the category list. Filters combine with the tree scope and the search box.
- **Results and space map** (center): sortable file list where a row click loads the inspector, double-clicking a space-map block zooms the tree to that block's folder, and the map supports wheel zoom, drag-pan, and zoom/reset controls.
- **Duplicate review** (center): after **Find duplicates**, the results switch to a grouped view built from SHA-256 matches — one header per group with the hash prefix, copy count, per-copy size and total duplicated bytes, then each copy with its owner. Group rows jump back to the file list, and CSV/JSON export write the whole report. Nothing is deleted or linked from this view: weights stay under their runtime manager.
- **Model runtimes** (right): each runtime that owns model files, with its model count, total size, store paths, whether its management CLI is on `PATH` (`ollama`, `hf`), and the exact way to manage it — `ollama rm`, LM Studio's *My Models*, `hf cache rm` / `hf cache prune --dry-run`, and so on. Clicking a runtime scopes the list to it; clicking again clears the scope.
- **Inspector** (right): **Selected item** (node info: size, modified, category, risk, owner/runtime, eligibility, guidance, and quick actions), **Folder** (branch info: findings, total size, share of scan, cleanup-eligible bytes, largest children), and **Statistics** for the current view.
- **Ignore rules**: the `.ignore` button in the tree pane edits a scanned folder's own `.aicleanignore` — pick the folder, review or change the patterns, and save. Saving is atomic, replaces only that file, refuses symlinked ignore files, caps the list at 200 lines, and rescans automatically. Clearing the list deletes the file so default scanning resumes.
- **Export**: the CSV and JSON buttons download the current filtered view, including category, risk, runtime, eligibility, and any blocked reason.

Press `/` to jump to the search box. Moved files remain restorable, so the workspace never becomes a one-way door.

## Scan

- **Scan** checks known AI-client and runtime locations in the current user's home directory.
- Enter a project folder path to also look for stale files in `.ai_scratch/` and `tmp_gen/`, plus `.diff`, `.patch`, `.bak`, and similar backup files.
- `.aicleanignore` files are read from each scanned root. Add one glob pattern per line; blank lines and lines starting with `#` are ignored. The scanner also skips common dependency/version-control directories and credential-like filenames. Use the `.ignore` button in the tree pane to edit these rules from the GUI.
- Model weights are excluded by default. Enable **Include model weights** to inspect recognized weight files; they are always unselected initially.
- Only obvious cache files are preselected. App data, developer histories/configuration, superseded outputs, and model weights require explicit selection. All choices are visible in the file list and space totals; review paths before confirming cleanup.

## Supported client locations

The scanner derives Windows roaming/local roots from `%APPDATA%` and `%LOCALAPPDATA%`, including ChatGPT and Claude Store/MSIX user-data subtrees, Copilot data and its `INetCache` folder, Comet, Gemini Desktop, DeepSeek, Grok, AnythingLLM, Cherry Studio, and Jan. On macOS it checks the corresponding `~/Library/Application Support` folders and discovers ChatGPT sandbox data under `~/Library/Containers/*/Data/Library/Application Support/ChatGPT`. Existing ChatGPT, Claude, Replit, Codex, and local-model paths remain supported.

Installer binaries and `Program Files`/`AnthropicClaude/app-*` directories are intentionally not scanned. The name `LocalCache` by itself does not make a folder eligible for cleanup; only recognized cache subdirectories are categorized and preselected as caches. App data such as IndexedDB remains unselected for review.

## Local model runtimes and risk rules

The scanner treats model weights as a different data layer from client caches. It checks Ollama (`OLLAMA_MODELS` or `.ollama/models` and the Linux system store), LM Studio (`LMSTUDIO_HOME`, `.lmstudio-home-pointer`, `.lmstudio/models`, and legacy LM Studio cache), GPT4All, llama.cpp, Hugging Face/Transformers cache overrides, LocalAI (`LOCALAI_MODELS_PATH`), Jan, and the current install folder's text-generation-webui/LocalAI `models` directories.

- Model weights are opt-in for viewing, marked **re-download risk**, and never eligible for raw file-to-Trash cleanup. LM Studio models should be removed in **My Models**; Hugging Face cache repos should use `hf cache rm` or a reviewed `hf cache prune --dry-run`.
- **HF prune preview** (toolbar button, or `python3 aicleanup.py --hf-prune-preview`) runs the Hugging Face CLI's own `hf cache prune --dry-run` and shows its report. AICleanUp only displays that output and never prunes, deletes, or rewrites the Hugging Face cache itself; run the same command without `--dry-run` yourself when you are ready.
- **Find duplicates** hashes model files with SHA-256 and reports only byte-identical copies in a grouped review table. Similar filenames are not treated as duplicates; no hardlinks are created automatically and nothing is deleted from that view.
- Ollama model tags are read from manifests and removed only through `ollama rm <model:tag>`, preserving shared content-addressed blobs. A model reported by `ollama ps` cannot be removed.
- Only Ollama `blobs/*-partial` and `*-partial-<index>` files are considered file-level partial-download candidates. They are never preselected, must be older than seven days, and the Ollama runtime must be stopped.
- Config/credential filenames are protected. Cleanup candidates open in another process or associated with a running AI client/runtime are blocked; process and open-handle checks use `psutil`, and if checks are unavailable destructive file actions fail closed.
- Files modified within the last seven days are blocked by default and shown with a **RECENT** marker. Turning on **Allow recent files** in the toolbar makes only those plain cleanup candidates selectable; the confirmation dialog then says the age override is in effect, and every affected manifest entry records `"override": "recent-file-age"`. Model weights, protected config, and process-blocked items stay blocked either way.

## Cleanup safety

AICleanUp does not permanently delete files. Selected files are moved to the operating system Trash/Recycle Bin, and an audit manifest is written to `~/.aicleanup/audit/cleanup_audit_<timestamp>.json`. On Linux, the XDG Trash layout is used directly. On Windows and macOS, install the optional `send2trash` dependency to route files to the native bin:

```bash
python3 -m pip install -r requirements.txt
```

If no native trash route is available, the file is moved into an app-managed quarantine folder (`~/.aicleanup/quarantine/<timestamp>_<id>/`) instead of being deleted or refused. Every manifest entry records the original path, size, route, and the location the file was moved to, so cleanup stays reversible.

## Restore a cleanup

**Undo last cleanup** in the toolbar (or the CLI below) moves files back to their original folders from the newest manifest. Restores are previewed first, never overwrite an existing file, refuse when the recorded file changed size, and write a `restore_log_*.json` manifest:

```bash
python3 aicleanup.py --restore                      # preview the newest cleanup
python3 aicleanup.py --restore --confirm            # apply it
python3 aicleanup.py --restore cleanup_audit_20260101_120000_000000.json --confirm
```

Entries without a recorded location (for example files the OS Recycle Bin does not expose) are reported so you can restore them through the system Trash, and model tags removed with `ollama rm` are re-downloaded with `ollama pull` rather than restored.

## Optional UI checks

The behavior of the workspace (tree, filters, inspector, zoom, export) has a browser-less smoke test powered by jsdom:

```bash
npm install
npm run test:ui
```

## CLI dry run

The command-line interface reports candidate totals and never modifies files; restores need an explicit `--confirm`:

```bash
python3 aicleanup.py --dry-run
python3 aicleanup.py --workspace ~/src/my-project
python3 aicleanup.py --include-models
python3 aicleanup.py --restore            # preview the newest cleanup
python3 aicleanup.py --hf-prune-preview   # Hugging Face read-only prune report
```
