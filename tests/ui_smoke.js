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
  if (url === '/api/duplicates') return reply({ groups: [duplicateGroup] });
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
