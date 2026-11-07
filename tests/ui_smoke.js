/*
 * Optional UI smoke test for the WizTree-style workspace.
 *
 * Requires jsdom:  npm install   (then)  npm run test:ui
 * The Python suite covers the server; this covers the panels, filters,
 * inspector, zoom controls, and export paths in web/app.js.
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch {
  console.log('SKIP  jsdom is not installed — run `npm install` in the repo root first.');
  process.exit(0);
}

const root = path.join(__dirname, '..', 'web');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const appJs = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

const now = Date.now();
const daysAgo = (n) => new Date(now - n * 86400000).toISOString().slice(0, 16);
const duplicateGroup = {
  sha256: 'c0ffee1234567890abcdef1234567890abcdef1234567890abcdef1234567890',
  size: 2048,
  items: [
    { id: 'b', path: '/home/u/.cache/hf/hub/models--x/snapshots/1/notes.json', name: 'notes.json', size: 2048, source: 'Hugging Face models', runtime: 'huggingface' },
    { id: 'd', path: '/home/u/.cache/hf/hub/models--y/snapshots/2/notes.json', name: 'notes.json', size: 2048, source: 'Hugging Face models', runtime: 'huggingface' },
  ],
};
const fixtures = [
  { id: 'a', path: '/home/u/.cache/hf/hub/models--x/snapshots/1/model.safetensors', root: '/home/u/.cache/hf/hub', name: 'model.safetensors', size: 4096, link_target: '/home/u/.cache/hf/hub/models--x/blobs/abc123', modified: daysAgo(30), category: 'Model weights', risk: 're-download', runtime: 'huggingface', source: 'Hugging Face models', cleanup_action: 'none', can_delete: false, age_blocked: false, model: true, model_entry: 'acme/demo', entry_size: 4096, entry_count: 2, latest: true, latest_by: 'ref', blocked_reason: 'Model files must be removed through their runtime, never as raw files.', guidance: 'Use `hf cache rm`.' },
  { id: 'b', path: '/home/u/.cache/hf/hub/models--x/snapshots/1/notes.json', root: '/home/u/.cache/hf/hub', name: 'notes.json', size: 2048, modified: daysAgo(10), category: 'Caches', risk: 'safe', runtime: 'huggingface', source: 'Hugging Face models', cleanup_action: 'trash', can_delete: true, age_blocked: false, selected: false },
  { id: 'c', path: '/home/u/.cache/hf/hub/blobs/fresh-partial', root: '/home/u/.cache/hf/hub', name: 'fresh-partial', size: 1024, modified: daysAgo(1), category: 'Ollama partial downloads', risk: 'safe', runtime: 'ollama', source: 'Ollama models', cleanup_action: 'trash', can_delete: false, age_blocked: true, blocked_reason: 'Modified within the last 7 days; blocked unless you explicitly override the age rule.' },
  { id: 'e', path: '/home/u/.ollama/models/blobs/old-partial', root: '/home/u/.ollama/models', name: 'old-partial', size: 512, modified: daysAgo(20), category: 'Ollama partial downloads', risk: 'safe', runtime: 'ollama', source: 'Ollama models', cleanup_action: 'trash', can_delete: true, age_blocked: false, selected: false },
  { id: 'f', path: '/home/u/.ollama/models/manifests/registry.ollama.ai/library/qwen2/7b', root: '/home/u/.ollama/models', name: 'qwen2:7b', size: 1536, modified: daysAgo(40), category: 'Ollama models', risk: 're-download', runtime: 'ollama', source: 'Ollama models', cleanup_action: 'ollama-rm', can_delete: true, age_blocked: false, selected: false, model: true, logical_size: 4096, model_entry: 'qwen2', variant: '7b', entry_size: 4096, entry_count: 1, latest: true, latest_by: 'tag' },
];

const calls = [];
let duplicateDelay = 10;
let duplicateCancelled = false;
const dom = new JSDOM(html, { url: 'http://127.0.0.1:8765/', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
window.fetch = async (url, options = {}) => {
  calls.push({ url, body: options.body });
  const reply = (value) => ({ ok: true, status: 200, json: async () => value });
  if (url === '/api/scan') return reply({ items: JSON.parse(JSON.stringify(fixtures)), warnings: [], scanned: 2 });
  if (url === '/api/audits') return reply({ audits: [{ name: 'cleanup_audit_x.json', created: new Date().toISOString(), items: 3, restorable: 2 }] });
  if (url === '/api/duplicates') {
    await new Promise((resolve) => setTimeout(resolve, duplicateDelay));
    return reply(duplicateCancelled ? { groups: [], cancelled: true } : { groups: [duplicateGroup], cancelled: false });
  }
  if (url === '/api/duplicates/progress') return reply({ running: true, done: 2, total: 4, bytes_done: 2048, bytes_total: 8192, cancelled: false });
  if (url === '/api/duplicates/cancel') return reply({ cancelling: true, progress: { running: true } });
  if (url === '/api/runtimes') return reply({ runtimes: [
    { runtime: 'huggingface', models: 1, entries: 1, bytes: 4096, stores: ['/home/u/.cache/hf/hub'], cli: 'hf', management: 'Use `hf cache rm` or review `hf cache prune --dry-run`.', in_use: null, handles: [] },
    { runtime: 'ollama', models: 1, entries: 1, bytes: 1024, stores: ['/home/u/.ollama/models'], cli: null, management: 'Remove tags with `ollama rm <model:tag>`; shared blobs stay intact.',
      in_use: [{ pid: 4242, name: 'ollama' }], handles: [{ path: '/home/u/.ollama/models/blobs/old-partial', pid: 4242, name: 'ollama' }] },
  ] });
  if (url === '/api/empty-dirs') return reply({ count: 2, truncated: false, roots: ['/home/u/.cache/hf/hub'], folders: [
    { path: '/home/u/.cache/hf/hub/old-run/logs', root: '/home/u/.cache/hf/hub', depth: 3 },
    { path: '/home/u/proj/tmp_gen', root: '/home/u/proj', depth: 1 },
  ] });
  if (url === '/api/trash-empty-dirs') {
    const paths = JSON.parse(options.body || '{}').paths || [];
    return reply({ moved: paths.map((path) => ({ path, kind: 'directory' })), errors: [], audit: '/home/u/.aicleanup/audit/cleanup_audit_sweep.json', remaining: 0 });
  }
  if (url === '/api/hf-prune-preview') return reply({ command: 'hf cache prune --dry-run', output: 'Would free 1G', dry_run: true, warnings: '' });
  if (typeof url === 'string' && url.startsWith('/api/ignore')) {
    if (url.startsWith('/api/ignore?')) {
      return reply({ root: decodeURIComponent(url.split('root=')[1]), path: '/home/u/.cache/hf/hub/.aicleanignore', exists: false, rules: [] });
    }
    return reply({ root: JSON.parse(options.body).root, path: '/home/u/.cache/hf/hub/.aicleanignore', rules: JSON.parse(options.body).rules.filter((line) => line.trim()), removed: false });
  }
  return reply({});
};
const exported = [];
window.URL.createObjectURL = (blob) => { exported.push(blob); return 'blob:stub'; };
window.URL.revokeObjectURL = () => {};
window.HTMLAnchorElement.prototype.click = function () { /* downloads are asserted through createObjectURL */ };
window.eval(appJs);

const $ = (selector) => window.document.querySelector(selector);
const $$ = (selector) => [...window.document.querySelectorAll(selector)];
const click = (element) => element.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
function clearSelection() {
  const boxes = () => $$('#fileRows .file-check');
  for (let guard = 0; guard < 20 && boxes().some((box) => box.checked); guard += 1) {
    const box = boxes().find((entry) => entry.checked);
    box.checked = false;
    box.dispatchEvent(new window.Event('change', { bubbles: true }));
  }
}

let failures = 0;
function check(label, condition, detail = '') {
  if (!condition) failures += 1;
  console.log(`${condition ? 'PASS' : 'FAIL'}  ${label}${!condition && detail ? ' :: ' + detail : ''}`);
}

setTimeout(async () => {
  check('scan request issued', calls.some((call) => call.url === '/api/scan'));
  check('table lists every finding', $$('#fileRows .file-row').length === fixtures.length, $$('#fileRows .file-row').length);
  check('folder tree lists nodes', $$('#folderTree .folder-row').length >= 3, $$('#folderTree .folder-row').length);
  check('tree root shows scan total', $('#folderTree .folder-scope .folder-size').textContent === '9.0 KB', $('#folderTree .folder-scope .folder-size').textContent);
  check('risk filter chips', $$('#riskFilters .filter-chip').length === 5, $$('#riskFilters .filter-chip').length);
  check('category filters include all + categories', $$('#treeChildren .tree-child').length === 5, $$('#treeChildren .tree-child').length);
  check('statistics panel populated', $('#statsPanel').textContent.includes('Findings'));
  check('space map blocks', $$('#treemapCanvas .map-block').length === fixtures.length, $$('#treemapCanvas .map-block').length);
  check('restore enabled by audits', $('#restoreBtn').disabled === false);
  check('HF preview enabled', $('#hfPreviewBtn').disabled === false);

  click($$('#fileRows .file-row')[0]);
  check('node info shows the selection', $('#nodeInfo').textContent.includes('model.safetensors'));
  check('node info marks runtime-protected weights', $('#nodeInfo').textContent.includes('Protected'));
  check('node info shows the shared blob', $('#nodeInfo').textContent.includes('blobs/abc123'), $('#nodeInfo').textContent.slice(0, 120));
  check('node info explains revision links', $('#nodeInfo').textContent.includes('counted once'), $('#nodeInfo').textContent.slice(0, 200));
  check('node info names the model entry', $('#nodeInfo').textContent.includes('acme/demo'), $('#nodeInfo').textContent.slice(0, 200));
  check('node info flags the current revision', ($('#nodeInfo .info-grid .status-pill') || {}).textContent === 'current', $('#nodeInfo .info-grid').textContent.slice(0, 200));
  check('node info counts the entry variants', $('#nodeInfo').textContent.includes('2 variants'), $('#nodeInfo').textContent.slice(0, 200));
  check('node info explains the refs/main pointer', $('#nodeInfo').textContent.includes('refs/main'), $('#nodeInfo').textContent.slice(0, 240));
  check('inspect select disabled for weights', $('#inspectSelect').disabled === true);
  check('branch info resolves the folder', $('#branchInfo').textContent.includes('.cache'));

  click($('#inspectFolder'));
  check('folder filter narrows the table', $$('#fileRows .file-row').length === 2, $$('#fileRows .file-row').length);
  check('folder filter hides other folders', !$$('#fileRows .file-row').some((row) => row.dataset.id === 'c'));
  check('tree highlights the active folder', $$('#folderTree .folder-row.active').length === 1, $$('#folderTree .folder-row.active').length);
  click($('#clearFilters'));

  click($$('#fileRows .file-row').find((row) => row.dataset.id === 'b'));
  click($('#inspectSelect'));
  check('selection count updates', $('#selectedCount').textContent === '1', $('#selectedCount').textContent);
  check('cleanup button enabled', $('#cleanBtn').disabled === false);

  click($$('#riskFilters .filter-chip').find((chip) => chip.dataset.risk === 'recent'));
  check('recent filter narrows rows', $$('#fileRows .file-row').length === 1, $$('#fileRows .file-row').length);
  check('recent filter keeps the fresh partial', $$('#fileRows .file-row')[0].dataset.id === 'c');
  click($('#clearFilters'));

  const override = $('#overrideRecent');
  override.checked = true;
  override.dispatchEvent(new window.Event('change', { bubbles: true }));
  const recentCheck = $$('#fileRows .file-row').find((row) => row.dataset.id === 'c').querySelector('.file-check');
  check('age override makes recent rows selectable', recentCheck.disabled === false);

  click($('#zoomIn'));
  check('zoom in updates the label', $('#zoomLabel').textContent === '125%', $('#zoomLabel').textContent);
  click($('#zoomReset'));
  check('zoom reset restores 100%', $('#zoomLabel').textContent === '100%');

  click($('#exportCsv'));
  check('CSV export creates a download blob', exported.length === 1);
  click($('#exportJson'));
  check('JSON export creates a download blob', exported.length === 2);

  if (exported[0]) {
    const csv = await exported[0].text();
    check('CSV has header plus rows', csv.split('\r\n').length === fixtures.length + 1, csv.split('\r\n').length);
    check('CSV exports the blob target', csv.split('\r\n')[0].endsWith('link_target'), csv.split('\r\n')[0]);
    check('CSV escapes blocked reasons', csv.includes('"Model files must be removed'));
    check('CSV exports the model entry columns', ['model_entry', 'variant', 'entry_count', 'latest_by'].every((field) => csv.split('\r\n')[0].includes(field)), csv.split('\r\n')[0]);
    check('CSV exports the entry row values', csv.includes('acme/demo') && csv.includes('qwen2'), csv.split('\r\n')[1] && csv.split('\r\n')[1].slice(0, 120));
  }
  if (exported[1]) {
    const parsed = JSON.parse(await exported[1].text());
    check('JSON export records the filters', parsed.count === fixtures.length && 'filters' in parsed);
    check('JSON export keeps the blob target', parsed.items.some((item) => item.link_target === '/home/u/.cache/hf/hub/models--x/blobs/abc123'));
    check('JSON export keeps the model entry', parsed.items.some((item) => item.model_entry === 'acme/demo' && item.latest_by === 'ref'));
    check('JSON export keeps the quantization variant', parsed.items.some((item) => item.variant === '7b' && item.latest_by === 'tag'), JSON.stringify(parsed.items.map((item) => [item.variant, item.latest_by])));
  }
  check('toast reports the export', $('#toast').textContent.includes('Exported'), $('#toast').textContent);

  // runtime inventory
  await new Promise((resolve) => setTimeout(resolve, 20));
  check('runtime panel lists each runtime', $$('#runtimePanel .runtime-row').length === 2, $$('#runtimePanel .runtime-row').length);
  check('runtime panel marks the detected CLI', $('#runtimePanel .runtime-badge.ok').textContent.includes('hf found'));
  check('runtime panel shows a missing CLI', $$('#runtimePanel .runtime-badge').some(b => b.textContent === 'no CLI'));
  check('runtime panel shows store size', $('#runtimePanel .runtime-row .runtime-size').textContent === '4.0 KB', $('#runtimePanel .runtime-row .runtime-size').textContent);
  check('runtime panel explains management', $('#runtimePanel').textContent.includes('ollama rm'));
  check('runtime panel counts model entries', $('#runtimePanel').textContent.includes('1 entry'), $('#runtimePanel').textContent.slice(0, 200));
  check('runtime panel names the holding process', $('#runtimePanel').textContent.includes('In use by ollama (PID 4242)'), $('#runtimePanel').textContent.slice(0, 200));
  check('runtime panel lists an open store file', $('#runtimePanel').textContent.includes('Open: old-partial'), $('#runtimePanel').textContent.slice(0, 240));
  check('runtime panel admits a missing process check', $('#runtimePanel').textContent.includes('Process check unavailable'), $('#runtimePanel').textContent.slice(0, 240));
  const hfRow = $$('#runtimePanel .runtime-row').find(row => row.dataset.runtime === 'huggingface');
  click(hfRow);
  check('runtime row filters the file list', $$('#fileRows .file-row').length === 2, $$('#fileRows .file-row').length);
  check('runtime filter hides other runtimes', !$$('#fileRows .file-row').some(row => row.dataset.id === 'c'));
  check('runtime filter is described', $('#resultSubtitle').textContent.includes('huggingface models'), $('#resultSubtitle').textContent);
  check('runtime row shows as active', $$('#runtimePanel .runtime-row.active').length === 1);
  click($$('#runtimePanel .runtime-row').find(row => row.dataset.runtime === 'huggingface'));
  check('clicking the active runtime clears the filter', $$('#fileRows .file-row').length === fixtures.length, $$('#fileRows .file-row').length);

  // duplicate review view
  check('duplicate view starts disabled', $$('#viewDuplicates').length === 1);
  click($('#duplicatesBtn'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  check('duplicate check enables the review view', $('#viewDuplicates').disabled === false);
  check('duplicate badge shows group count', $('#dupCount').textContent === '1', $('#dupCount').textContent);
  check('duplicate view renders the group header', $$('#fileRows .dup-group').length === 1, $$('#fileRows .dup-group').length);
  check('duplicate view lists every copy', $$('#fileRows .dup-member').length === 2, $$('#fileRows .dup-member').length);
  check('group header shows a hash', $('#fileRows .dup-hash').textContent.includes('c0ffee'));
  check('group header shows duplicated bytes', $('#fileRows .dup-group').textContent.includes('2.0 KB'), $('#fileRows .dup-group').textContent.replace(/\s+/g,' ').slice(0, 160));
  check('duplicate header row visible', $('#duplicatesHead').hidden === false && $('#filesHead').hidden === true);
  check('selection disabled while reviewing duplicates', $('#selectVisible').disabled === true);
  check('subtitle explains review-only', $('#resultSubtitle').textContent.includes('review only'), $('#resultSubtitle').textContent);

  click($$('#fileRows .dup-member')[0]);
  check('duplicate copy loads the inspector', $('#nodeInfo').textContent.includes('notes.json'));

  const exportsBefore = exported.length;
  click($('#exportCsv'));
  check('duplicate CSV export creates a blob', exported.length === exportsBefore + 1, exported.length);
  if (exported[exportsBefore]) {
    const dupCsv = await exported[exportsBefore].text();
    check('duplicate CSV starts with the group columns', dupCsv.startsWith('group,sha256,size,copies,duplicated_bytes'), dupCsv.split('\r\n')[0]);
    check('duplicate CSV lists both copies', dupCsv.split('\r\n').length === 3, dupCsv.split('\r\n').length);
  }

  click($$('#fileRows .dup-show')[0]);
  check('show-in-list returns to the file view', $('#filesHead').hidden === false && $('#duplicatesHead').hidden === true);
  check('show-in-list filters to model weights', $('#filterBadge').textContent === 'MODEL WEIGHTS', $('#filterBadge').textContent);

  click($('#viewDuplicates'));
  check('duplicate view can be reopened', $$('#fileRows .dup-group').length === 1);
  click($('#viewFiles'));

  // duplicate hashing progress and cancel
  duplicateDelay = 400;
  const dupStart = calls.length;
  click($('#duplicatesBtn'));
  await new Promise((resolve) => setTimeout(resolve, 320));
  check('hashing swaps the button for a cancel', $('#duplicatesBtn span').textContent === 'Cancel hashing', $('#duplicatesBtn').textContent.trim());
  check('hashing keeps the cancel clickable', $('#duplicatesBtn').disabled === false);
  check('hashing polls the progress endpoint', calls.slice(dupStart).some((call) => call.url === '/api/duplicates/progress'));
  check('hashing reports file and byte counts', $('#statusText').textContent.includes('Hashing 2 of 4 files'), $('#statusText').textContent);
  check('hashing fills the progress bar', $('#progress div').style.width === '50%', $('#progress div').style.width);
  check('hashing marks the button as running', $('#duplicatesBtn').classList.contains('running'));
  click($('#duplicatesBtn'));
  check('cancel posts to the cancel endpoint', calls.slice(dupStart).some((call) => call.url === '/api/duplicates/cancel'));
  await new Promise((resolve) => setTimeout(resolve, 260));
  check('hashing restores the button label', $('#duplicatesBtn span').textContent === 'Find duplicates', $('#duplicatesBtn').textContent.trim());
  check('hashing clears the progress bar', $('#progress div').style.width === '', $('#progress div').style.width);
  duplicateDelay = 10;

  // a cancelled check reports nothing changed and leaves the file view
  duplicateCancelled = true;
  click($('#duplicatesBtn'));
  await new Promise((resolve) => setTimeout(resolve, 80));
  check('cancelled check reports nothing changed', $('#statusText').textContent.includes('cancelled'), $('#statusText').textContent);
  check('cancelled check leaves no duplicate groups', $('#dupCount').textContent === '0', $('#dupCount').textContent);
  check('cancelled check returns to the file view', $('#filesHead').hidden === false, $('#filesHead').hidden);
  duplicateCancelled = false;

  // bulk selection
  click($('#clearFilters'));
  clearSelection();
  check('selection starts empty for the bulk pass', $('#selectedCount').textContent === '0', $('#selectedCount').textContent);
  check('bulk button names the current scope', $('#selectEligible').textContent === 'Select eligible in view', $('#selectEligible').textContent);
  check('bulk button enabled with eligible rows', $('#selectEligible').disabled === false);

  click($('#selectEligible'));
  check('bulk select picks every eligible trash file', $('#selectedCount').textContent === '2', $('#selectedCount').textContent);
  check('bulk select skips model weights', $$('#fileRows .file-row').find((row) => row.dataset.id === 'a').querySelector('.file-check').checked === false);
  check('bulk select leaves the ollama pass alone', $$('#fileRows .file-row').find((row) => row.dataset.id === 'f').querySelector('.file-check').checked === false);
  check('bulk select reports the other action', $('#selectNote').textContent.includes('1 ollama rm left for a separate pass'), $('#selectNote').textContent);
  check('bulk select reports the age rule', $('#selectNote').textContent.includes('1 recent skipped (7-day rule)'), $('#selectNote').textContent);
  check('bulk select reports protected weights', $('#selectNote').textContent.includes('1 runtime-managed or blocked'), $('#selectNote').textContent);
  check('bulk select keeps one action usable', $('#cleanBtn').disabled === false && $('#cleanBtn').textContent.includes('Trash'), $('#cleanBtn').textContent);

  click($('#clearFilters'));
  check('clearing filters drops the bulk note', $('#selectNote').textContent === '', $('#selectNote').textContent);
  click($$('#fileRows .file-row').find((row) => row.dataset.id === 'f'));
  check('node info shows the tag variant', $('#nodeInfo').textContent.includes('7b'), $('#nodeInfo').textContent.slice(0, 200));
  check('node info marks the latest tag', $('#nodeInfo').textContent.includes('Tagged latest'), $('#nodeInfo').textContent.slice(0, 240));
  clearSelection();
  click($$('#folderTree .folder-row').find((row) => row.dataset.path === '/home/u/.ollama/models'));
  check('bulk button follows the folder scope', $('#selectEligible').textContent === 'Select eligible in this folder', $('#selectEligible').textContent);
  check('branch info offers a bulk select', $$('#branchSelect').length === 1);
  click($('#branchSelect'));
  check('folder bulk selects only that branch', $('#selectedCount').textContent === '1', $('#selectedCount').textContent);
  check('folder bulk takes the dominant action', $$('#fileRows .file-row').find((row) => row.dataset.id === 'f').querySelector('.file-check').checked === true);
  check('folder bulk reports the trash leftovers', $('#selectNote').textContent.includes('1 Trash left for a separate pass'), $('#selectNote').textContent);
  check('folder bulk switches the cleanup action', $('#cleanBtn').textContent.includes('Ollama'), $('#cleanBtn').textContent);

  // space map export
  click($('#clearFilters'));
  const canvasCalls = [];
  window.HTMLCanvasElement.prototype.getContext = function () {
    return {
      fillStyle: '#000', strokeStyle: '#000', lineWidth: 1, font: '', textBaseline: 'alphabetic',
      fillRect: (...args) => canvasCalls.push(['fillRect', ...args]),
      strokeRect: (...args) => canvasCalls.push(['strokeRect', ...args]),
      fillText: (...args) => canvasCalls.push(['fillText', ...args]),
      measureText: (text) => ({ width: String(text).length * 7 }),
    };
  };
  window.HTMLCanvasElement.prototype.toBlob = function (callback, type) {
    callback(new window.Blob(['fake-png'], { type: type || 'image/png' }));
  };
  const mapBlobs = exported.length;
  click($('#exportMapPng'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  check('PNG export creates one blob', exported.length === mapBlobs + 1, exported.length);
  check('PNG export is an image blob', exported[mapBlobs] && exported[mapBlobs].type === 'image/png', exported[mapBlobs] && exported[mapBlobs].type);
  check('PNG export draws a block per file', canvasCalls.filter((call) => call[0] === 'fillRect').length > fixtures.length, canvasCalls.filter((call) => call[0] === 'fillRect').length);
  check('PNG export titles the image', canvasCalls.some((call) => call[0] === 'fillText' && String(call[1]).includes('space map')));
  check('PNG export labels the largest file', canvasCalls.some((call) => call[0] === 'fillText' && String(call[1]).includes('model.safetensors')));

  const pdfBlobs = exported.length;
  click($('#exportMapPdf'));
  check('PDF export creates one blob', exported.length === pdfBlobs + 1, exported.length);
  if (exported[pdfBlobs]) {
    const pdf = await exported[pdfBlobs].text();
    check('PDF blob is a PDF', exported[pdfBlobs].type === 'application/pdf', exported[pdfBlobs].type);
    check('PDF opens and closes correctly', pdf.startsWith('%PDF-1.4') && pdf.endsWith('%%EOF\n'), pdf.slice(0, 10));
    check('PDF declares a landscape page', pdf.includes('/MediaBox [0 0 842 595]'));
    check('PDF draws a rectangle per block', (pdf.match(/ re f/g) || []).length >= fixtures.length, (pdf.match(/ re f/g) || []).length);
    check('PDF labels the largest file', pdf.includes('model.safetensors'));
    check('PDF writes a resolving xref', /startxref\n\d+\n%%EOF\n$/.test(pdf));
    check('PDF notes that nothing changed', pdf.includes('nothing was changed'));
  }

  // whole-scan map export, grouped per folder
  check('map export offers the whole scan', $$('#mapScope option').length === 2, $$('#mapScope option').length);
  const groupedBlobs = exported.length;
  $('#mapScope').value = 'scan';
  $('#mapScope').dispatchEvent(new window.Event('change', { bubbles: true }));
  click($('#exportMapPdf'));
  check('grouped map export creates one blob', exported.length === groupedBlobs + 1, exported.length);
  if (exported[groupedBlobs]) {
    const pdf = await exported[groupedBlobs].text();
    check('grouped map titles the whole scan', pdf.includes('whole scan map by folder'), pdf.slice(0, 260));
    check('grouped map labels a folder group', pdf.includes('hub/models--x'), (pdf.match(/\(([^)]*)\) Tj/g) || []).slice(0, 6).join(' | '));
    check('grouped map labels a runtime folder group', pdf.includes('models/manifests'), (pdf.match(/\(([^)]*)\) Tj/g) || []).slice(0, 6).join(' | '));
    check('grouped map draws groups and their files', (pdf.match(/ re f/g) || []).length > fixtures.length, (pdf.match(/ re f/g) || []).length);
    check('grouped map keeps the safety note', pdf.includes('nothing was changed'));
    check('grouped map totals per group', pdf.includes('6.0 KB'), (pdf.match(/\(([^)]*)\) Tj/g) || []).join(' | '));
    check('grouped map counts the files per group', pdf.includes('2 files'), (pdf.match(/\(([^)]*)\) Tj/g) || []).slice(0, 4).join(' | '));
  }
  $('#mapScope').value = 'view';
  $('#mapScope').dispatchEvent(new window.Event('change', { bubbles: true }));

  // scan scopes
  check('scope panel starts empty', $$('#scopeSelect option').length === 1, $$('#scopeSelect option').length);
  check('scope note invites a first save', $('#scopeNote').textContent.includes('Save a scope'), $('#scopeNote').textContent);
  check('scope delete disabled while empty', $('#scopeDelete').disabled === true);
  $('#workspace').value = '/home/u/projects/old-app';
  $('#includeModels').checked = true;
  click($('#scopeSave'));
  check('scope dialog opens', $('#scopeDialog').hasAttribute('open'));
  check('scope dialog suggests a folder name', $('#scopeName').value === 'old-app', $('#scopeName').value);
  check('scope dialog summarises the options', $('#scopeSummary').textContent.includes('model weights on'), $('#scopeSummary').textContent);
  $('#scopeName').value = 'Old app folders';
  click($('#scopeConfirm'));
  check('scope dialog closes after saving', !$('#scopeDialog').hasAttribute('open'));
  const storedScopes = JSON.parse(window.localStorage.getItem('aicleanup.scopes') || '[]');
  check('scope saved to browser storage', storedScopes.length === 1 && storedScopes[0].name === 'Old app folders', window.localStorage.getItem('aicleanup.scopes'));
  check('stored scope keeps the folder', storedScopes[0] && storedScopes[0].workspace === '/home/u/projects/old-app', storedScopes[0] && storedScopes[0].workspace);
  check('scope select lists the saved scope', $$('#scopeSelect option').length === 2, $$('#scopeSelect option').length);
  check('scope note describes the saved scope', $('#scopeNote').textContent.includes('old-app'), $('#scopeNote').textContent);
  check('scope delete enabled after saving', $('#scopeDelete').disabled === false);

  const scansBefore = calls.filter((call) => call.url === '/api/scan').length;
  $('#workspace').value = '';
  $('#includeModels').checked = false;
  const scopeSelect = $('#scopeSelect');
  scopeSelect.value = 'Old app folders';
  scopeSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 30));
  check('applying a scope restores the folder', $('#workspace').value === '/home/u/projects/old-app', $('#workspace').value);
  check('applying a scope restores the options', $('#includeModels').checked === true);
  check('applying a scope rescans', calls.filter((call) => call.url === '/api/scan').length === scansBefore + 1, calls.filter((call) => call.url === '/api/scan').length);
  const scopeScan = JSON.parse(calls.filter((call) => call.url === '/api/scan').pop().body);
  check('scope rescan posts the saved folder', scopeScan.workspace === '/home/u/projects/old-app', scopeScan.workspace);
  check('scope rescan posts the saved options', scopeScan.include_models === true, scopeScan.include_models);

  click($('#scopeDelete'));
  check('deleting a scope clears storage', JSON.parse(window.localStorage.getItem('aicleanup.scopes') || '[]').length === 0, window.localStorage.getItem('aicleanup.scopes'));
  check('scope panel returns to the empty state', $$('#scopeSelect option').length === 1, $$('#scopeSelect option').length);
  check('scope delete disabled after deleting', $('#scopeDelete').disabled === true);

  // empty-folder sweep
  check('empty folder button enabled after scan', $('#emptyDirsBtn').disabled === false);
  click($('#emptyDirsBtn'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  check('empty folder dialog lists every folder', $$('#emptyList li').length === 2, $$('#emptyList li').length);
  check('empty folder dialog explains the audit', $('#emptyNote').textContent.includes('audit manifest'), $('#emptyNote').textContent);
  check('empty folder list shows the depth', $('#emptyList small').textContent.includes('3 levels deep'), $('#emptyList small').textContent);
  check('empty folder confirm names the count', $('#emptyConfirm').textContent === 'Move 2 folders to Trash', $('#emptyConfirm').textContent);
  check('status says nothing was removed yet', $('#statusText').textContent.includes('nothing removed yet'), $('#statusText').textContent);
  click($('#emptyConfirm'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  const sweep = calls.find((call) => call.url === '/api/trash-empty-dirs');
  check('sweep posts every listed folder', sweep && JSON.parse(sweep.body).paths.length === 2, sweep && sweep.body);
  check('sweep closes the dialog', !$('#emptyDialog').hasAttribute('open'));
  check('sweep reports the result', $('#toast').textContent.includes('2 empty folders moved'), $('#toast').textContent);
  check('sweep records the audit path', $('#statusText').title.includes('cleanup_audit_sweep'), $('#statusText').title);

  // ignore-rule editor
  check('ignore button enabled after scan', $('#ignoreBtn').disabled === false);
  click($('#ignoreBtn'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  check('ignore dialog offers scan roots', $$('#ignoreRoot option').length >= 1, $$('#ignoreRoot option').length);
  check('ignore dialog notes the target file', $('#ignoreNote').textContent.includes('.aicleanignore'));
  const textarea = $('#ignoreRules');
  textarea.value = '*.tmp\n# keep this comment\ncache/**';
  click($('#ignoreSave'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  const posted = calls.find((call) => call.url === '/api/ignore' && call.body);
  check('ignore save posts the rules', posted && JSON.parse(posted.body).rules.length === 3, posted && posted.body);
  check('ignore save reports success', $('#toast').textContent.includes('ignore rule'), $('#toast').textContent);
  check('ignore dialog closed after save', !$('#ignoreDialog').hasAttribute('open'));
  process.exitCode = failures ? 1 : 0;
  if (failures) console.log(`${failures} UI check(s) failed`);
});
