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
const fixtures = [
  { id: 'a', path: '/home/u/.cache/hf/hub/models--x/snapshots/1/model.safetensors', root: '/home/u/.cache/hf/hub', name: 'model.safetensors', size: 4096, modified: daysAgo(30), category: 'Model weights', risk: 're-download', runtime: 'huggingface', source: 'Hugging Face models', cleanup_action: 'none', can_delete: false, age_blocked: false, model: true, blocked_reason: 'Model files must be removed through their runtime, never as raw files.', guidance: 'Use `hf cache rm`.' },
  { id: 'b', path: '/home/u/.cache/hf/hub/models--x/snapshots/1/notes.json', root: '/home/u/.cache/hf/hub', name: 'notes.json', size: 2048, modified: daysAgo(10), category: 'Caches', risk: 'safe', runtime: 'huggingface', source: 'Hugging Face models', cleanup_action: 'trash', can_delete: true, age_blocked: false, selected: false },
  { id: 'c', path: '/home/u/.cache/hf/hub/blobs/fresh-partial', root: '/home/u/.cache/hf/hub', name: 'fresh-partial', size: 1024, modified: daysAgo(1), category: 'Ollama partial downloads', risk: 'safe', runtime: 'ollama', source: 'Ollama models', cleanup_action: 'trash', can_delete: false, age_blocked: true, blocked_reason: 'Modified within the last 7 days; blocked unless you explicitly override the age rule.' },
];

const calls = [];
const dom = new JSDOM(html, { url: 'http://127.0.0.1:8765/', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
window.fetch = async (url, options = {}) => {
  calls.push({ url, body: options.body });
  const reply = (value) => ({ ok: true, status: 200, json: async () => value });
  if (url === '/api/scan') return reply({ items: JSON.parse(JSON.stringify(fixtures)), warnings: [], scanned: 2 });
  if (url === '/api/audits') return reply({ audits: [{ name: 'cleanup_audit_x.json', created: new Date().toISOString(), items: 3, restorable: 2 }] });
  if (url === '/api/duplicates') return reply({ groups: [] });
  if (url === '/api/hf-prune-preview') return reply({ command: 'hf cache prune --dry-run', output: 'Would free 1G', dry_run: true, warnings: '' });
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
let failures = 0;
function check(label, condition, detail = '') {
  if (!condition) failures += 1;
  console.log(`${condition ? 'PASS' : 'FAIL'}  ${label}${!condition && detail ? ' :: ' + detail : ''}`);
}

setTimeout(async () => {
  check('scan request issued', calls.some((call) => call.url === '/api/scan'));
  check('table lists every finding', $$('#fileRows .file-row').length === fixtures.length, $$('#fileRows .file-row').length);
  check('folder tree lists nodes', $$('#folderTree .folder-row').length >= 3, $$('#folderTree .folder-row').length);
  check('tree root shows scan total', $('#folderTree .folder-scope .folder-size').textContent === '7.0 KB', $('#folderTree .folder-scope .folder-size').textContent);
  check('risk filter chips', $$('#riskFilters .filter-chip').length === 5, $$('#riskFilters .filter-chip').length);
  check('category filters include all + categories', $$('#treeChildren .tree-child').length === 4, $$('#treeChildren .tree-child').length);
  check('statistics panel populated', $('#statsPanel').textContent.includes('Findings'));
  check('space map blocks', $$('#treemapCanvas .map-block').length === fixtures.length, $$('#treemapCanvas .map-block').length);
  check('restore enabled by audits', $('#restoreBtn').disabled === false);
  check('HF preview enabled', $('#hfPreviewBtn').disabled === false);

  click($$('#fileRows .file-row')[0]);
  check('node info shows the selection', $('#nodeInfo').textContent.includes('model.safetensors'));
  check('node info marks runtime-protected weights', $('#nodeInfo').textContent.includes('Protected'));
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
    check('CSV escapes blocked reasons', csv.includes('"Model files must be removed'));
  }
  if (exported[1]) {
    const parsed = JSON.parse(await exported[1].text());
    check('JSON export records the filters', parsed.count === fixtures.length && 'filters' in parsed);
  }
  check('toast reports the export', $('#toast').textContent.includes('Exported'), $('#toast').textContent);
  process.exitCode = failures ? 1 : 0;
  if (failures) console.log(`${failures} UI check(s) failed`);
});
