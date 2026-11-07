const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  items: [], locations: 0,
  category: 'all', risk: 'all', folder: null, runtime: null, query: '',
  sort: 'size', descending: true, busy: false,
  audits: [], overrideRecent: false,
  selectedId: null, duplicateGroups: null, view: 'files', runtimes: [],
  zoom: 1, panX: 0, panY: 0,
  expanded: new Set(), scopes: [], emptyDirs: [], hashing: false, mapScope: 'view',
};

const colors = { 'Caches':'#3c78a9', 'Ollama partial downloads':'#3c78a9', 'Ollama models':'#795493', 'Application data':'#527fad', 'Developer tools':'#47845f', 'Superseded outputs':'#a6783e', 'Model weights':'#795493', 'Project files':'#62758a' };
const RISK_FILTERS = [['all','All risks'],['safe','Safe'],['inspect','Inspect'],['re-download','Re-download'],['recent','Recent']];

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function bytes(n) { if (n === undefined || n === null) return '—'; if (n < 1024) return `${n} B`; const units=['KB','MB','GB','TB']; let i=-1; do { n/=1024; i++; } while(n>=1024 && i<units.length-1); return `${n.toFixed(1)} ${units[i]}`; }
function dateText(s) { try { const d=new Date(s); return d.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'2-digit'}); } catch { return s; } }
function baseName(p) { const parts=String(p||'').split(/[\\/]+/).filter(Boolean); return parts[parts.length-1] || String(p||''); }
function dirName(p) { const s=String(p||''); const i=Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\')); return i > 0 ? s.slice(0,i) : s; }
function sepFor(p) { return String(p||'').includes('\\') && !String(p||'').includes('/') ? '\\' : '/'; }

// ---------------------------------------------------------------- view model
const overridable = (x) => state.overrideRecent && !!x.age_blocked && !x.model && x.cleanup_action==='trash' && x.risk!=='caution' && x.risk!=='never-delete';
const selectable = (x) => x.can_delete || overridable(x);
function selectionTitle(x) { return x.can_delete ? '' : overridable(x) ? 'Modified in the last 7 days — selected under the age override' : (x.blocked_reason||x.guidance||''); }
function selectedItems() { return state.items.filter(x=>x.selected); }
function itemById(id) { return state.items.find(i=>i.id===id) || null; }

function inFolder(item, folder) {
  if (!folder) return true;
  return item.path === folder || item.path.startsWith(folder + '/') || item.path.startsWith(folder + '\\');
}

function filtered() {
  const q = state.query.trim().toLowerCase();
  return state.items.filter(x => {
    if (state.category !== 'all' && x.category !== state.category) return false;
    if (state.risk === 'recent') { if (!x.age_blocked) return false; }
    else if (state.risk !== 'all' && x.risk !== state.risk) return false;
    if (state.runtime && x.runtime !== state.runtime) return false;
    if (!inFolder(x, state.folder)) return false;
    if (q && !`${x.name} ${x.path} ${x.category} ${x.source||''} ${x.runtime||''}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function filterDescription() {
  const bits = [];
  if (state.folder) bits.push(`folder ${baseName(state.folder)}`);
  if (state.runtime) bits.push(`${state.runtime} models`);
  if (state.category !== 'all') bits.push(state.category);
  if (state.risk !== 'all') bits.push(state.risk === 'recent' ? 'recent only' : `${state.risk} risk`);
  if (state.query.trim()) bits.push(`“${state.query.trim()}”`);
  return bits.length ? bits.join(' · ') : 'no filters applied';
}

// ---------------------------------------------------------------- scan scopes
const SCOPE_KEY = 'aicleanup.scopes';
const SCOPE_LIMIT = 12;

function scopeStore() {
  try { return window.localStorage || null; } catch { return null; }
}

function readScopes() {
  const store = scopeStore();
  if (!store) return [];
  try {
    const parsed = JSON.parse(store.getItem(SCOPE_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(entry => entry && typeof entry.name === 'string' && entry.name.trim())
      .slice(0, SCOPE_LIMIT)
      .map(entry => ({
        name: String(entry.name).trim().slice(0, 60),
        workspace: typeof entry.workspace === 'string' ? entry.workspace : '',
        includeModels: !!entry.includeModels,
        overrideRecent: !!entry.overrideRecent,
      }));
  } catch { return []; }
}

function writeScopes() {
  const store = scopeStore();
  if (!store) return false;
  try { store.setItem(SCOPE_KEY, JSON.stringify(state.scopes)); return true; } catch { return false; }
}

function scanOptions() {
  return { workspace: $('#workspace').value.trim(), includeModels: $('#includeModels').checked, overrideRecent: $('#overrideRecent').checked };
}

function scopeSummary(scope) {
  const bits = [scope.workspace ? (baseName(scope.workspace) || scope.workspace) : 'default AI locations'];
  bits.push(scope.includeModels ? 'model weights on' : 'model weights off');
  if (scope.overrideRecent) bits.push('recent files allowed');
  return bits.join(' · ');
}

function renderScopes(preferred) {
  const select = $('#scopeSelect');
  if (!select) return;
  const keep = preferred === undefined ? select.value : preferred;
  const options = state.scopes.map(scope => `<option value="${esc(scope.name)}">${esc(scope.name)}</option>`).join('');
  select.innerHTML = state.scopes.length ? `<option value="">Choose a saved scope…</option>${options}` : '<option value="">No saved scopes yet</option>';
  if (state.scopes.some(scope => scope.name === keep)) select.value = keep;
  const active = state.scopes.find(scope => scope.name === select.value) || null;
  $('#scopeDelete').disabled = !active;
  $('#scopeNote').textContent = active
    ? `${scopeSummary(active)} — apply to rescan`
    : state.scopes.length ? 'Pick a scope to rescan with its folders and options.' : 'Save a scope to reuse folders and scan options.';
}

function applyScope(name) {
  const scope = state.scopes.find(entry => entry.name === name);
  if (!scope) { renderScopes(); return; }
  $('#workspace').value = scope.workspace;
  $('#includeModels').checked = scope.includeModels;
  setOverrideRecent(scope.overrideRecent);
  renderScopes();
  toast(`Scope “${scope.name}” applied — scanning`);
  runScan();
}

function openScopeEditor() {
  const options = scanOptions();
  const suggestion = options.workspace ? baseName(options.workspace) : 'All AI locations';
  $('#scopeName').value = (suggestion || 'All AI locations').slice(0, 60);
  $('#scopeSummary').textContent = `${scopeSummary(options)} — saved in this browser only.`;
  openDialog($('#scopeDialog'));
}

function commitScope(event) {
  if (event) event.preventDefault();
  const name = $('#scopeName').value.trim().slice(0, 60);
  if (!name) { toast('Give the scope a name first'); return; }
  const entry = { name, ...scanOptions() };
  const index = state.scopes.findIndex(scope => scope.name.toLowerCase() === name.toLowerCase());
  if (index >= 0) state.scopes[index] = entry; else state.scopes.push(entry);
  if (state.scopes.length > SCOPE_LIMIT) state.scopes = state.scopes.slice(-SCOPE_LIMIT);
  const persisted = writeScopes();
  closeDialog($('#scopeDialog'));
  renderScopes(entry.name);
  toast(persisted ? `Saved scope “${entry.name}”` : `Saved scope “${entry.name}” for this session only`);
}

function deleteScope() {
  const name = $('#scopeSelect').value;
  if (!name) return;
  state.scopes = state.scopes.filter(scope => scope.name !== name);
  writeScopes();
  renderScopes();
  toast(`Deleted scope “${name}”`);
}

// ---------------------------------------------------------------- folder tree
function buildFolderTree(items) {
  const nodes = new Map();
  const ensure = (path) => {
    if (!nodes.has(path)) nodes.set(path, { path, name: baseName(path), size: 0, count: 0, children: new Map() });
    return nodes.get(path);
  };
  const roots = new Set();
  for (const item of items) {
    const rootPath = item.root && String(item.path).startsWith(item.root) ? item.root : dirName(item.path);
    let node = ensure(rootPath);
    roots.add(rootPath);
    const sep = sepFor(rootPath);
    let path = String(rootPath).replace(/[\\/]+$/, '');
    const rel = String(item.path).slice(String(rootPath).length).replace(/^[\\/]+/, '');
    const parts = rel ? rel.split(/[\\/]+/) : [];
    parts.pop();
    const chain = [node];
    for (const part of parts) {
      path = path + sep + part;
      const child = ensure(path);
      child.parent = node.path;
      node.children.set(child.path, child);
      node = child;
      chain.push(node);
    }
    for (const n of chain) { n.size += item.size || 0; n.count += 1; }
  }
  return { nodes, roots: [...roots] };
}

function renderFolderTree() {
  const host = $('#folderTree');
  const scroller = host.parentElement;
  const scrollTop = scroller ? scroller.scrollTop : 0;
  if (!state.items.length) {
    host.innerHTML = '<div class="tree-empty">Run a scan to build the folder tree.</div>';
    return;
  }
  const { nodes, roots } = buildFolderTree(state.items);
  // Keep the active folder visible: expand every ancestor and unfold the path.
  let cursor = state.folder ? nodes.get(state.folder) : null;
  while (cursor && cursor.parent) {
    state.expanded.add(cursor.parent);
    cursor = nodes.get(cursor.parent);
  }
  const total = state.items.reduce((n,x)=>n+x.size,0) || 1;
  const renderNode = (node, depth) => {
    const expanded = depth === 0 || state.expanded.has(node.path);
    const kids = [...node.children.values()].sort((a,b)=>b.size-a.size);
    const active = state.folder === node.path;
    const row = `<div class="folder-row ${active?'active':''}" style="padding-left:${6+depth*13}px" data-path="${esc(node.path)}" title="${esc(node.path)} — ${(node.size/total*100).toFixed(1)}% of scan">
        <button class="folder-toggle ${kids.length?'':'invisible'}" data-toggle="${esc(node.path)}" title="${expanded?'Collapse':'Expand'}" aria-label="${expanded?'Collapse':'Expand'} ${esc(node.name)}">${expanded?'▾':'▸'}</button>
        <span class="folder-icon">${depth===0?'▣':'▢'}</span>
        <span class="folder-name">${esc(node.name)}</span>
        <span class="folder-size">${bytes(node.size)}</span>
      </div>`;
    return row + (expanded && kids.length ? kids.map(child=>renderNode(child, depth+1)).join('') : '');
  };
  const body = roots.sort((a,b)=>nodes.get(b).size - nodes.get(a).size).map(p=>renderNode(nodes.get(p), 0)).join('');
  host.innerHTML = `<div class="folder-row folder-scope ${state.folder===null?'active':''}" data-path="" title="Show findings from every scanned location">
      <span class="folder-toggle invisible">▾</span><span class="folder-icon">▤</span><span class="folder-name">All scanned folders</span><span class="folder-size">${bytes(total)}</span>
    </div>` + body;
  if (scroller) scroller.scrollTop = scrollTop;
  const activeRow = host.querySelector('.folder-row.active');
  if (activeRow && typeof activeRow.scrollIntoView === 'function') activeRow.scrollIntoView({ block: 'nearest' });
}

function onTreeClick(event) {
  const toggle = event.target.closest('.folder-toggle');
  const row = event.target.closest('.folder-row');
  if (toggle && row && !toggle.classList.contains('invisible')) {
    const path = toggle.dataset.toggle;
    if (state.expanded.has(path)) state.expanded.delete(path); else state.expanded.add(path);
    render();
    return;
  }
  if (!row) return;
  state.folder = row.dataset.path || null;
  render();
}

// ---------------------------------------------------------------- filters
function renderRiskFilters() {
  const counts = { all: state.items.length, safe: 0, inspect: 0, 're-download': 0, recent: 0 };
  for (const x of state.items) {
    if (counts[x.risk] !== undefined) counts[x.risk] += 1;
    if (x.age_blocked) counts.recent += 1;
  }
  $('#riskFilters').innerHTML = RISK_FILTERS.map(([key,label]) =>
    `<button class="filter-chip ${state.risk===key?'active':''}" data-risk="${key}">${label}<b>${counts[key]||0}</b></button>`).join('');
}

function renderCategories() {
  const host = $('#treeChildren');
  const cats = {};
  for (const x of state.items) cats[x.category] = (cats[x.category]||0) + x.size;
  const list = Object.entries(cats).sort((a,b)=>b[1]-a[1]);
  if (!list.length) { host.innerHTML = '<div class="tree-empty">Categories appear after a scan.</div>'; return; }
  const total = list.reduce((n,x)=>n+x[1],0) || 1;
  host.innerHTML = `<button class="tree-child ${state.category==='all'?'active':''}" data-category="all"><i class="category-dot" style="background:#5f7892"></i><span class="tree-name">All categories</span><span class="tree-size">${bytes(total)}</span></button>` +
    list.map(([cat,size]) => `<button class="tree-child ${state.category===cat?'active':''}" data-category="${esc(cat)}" title="${esc(cat)} — ${(size/total*100).toFixed(1)}%"><i class="category-dot" style="background:${colors[cat]||'#5595c8'}"></i><span class="tree-name">${esc(cat)}</span><span class="tree-size">${bytes(size)}</span></button>`).join('');
  $$('#treeChildren .tree-child').forEach(button => button.addEventListener('click', () => {
    state.category = button.dataset.category;
    render();
  }));
}

// ---------------------------------------------------------------- duplicates
function duplicateGroups() { return state.duplicateGroups || []; }

function duplicateWaste(groups) {
  return groups.reduce((total, group) => total + group.size * Math.max(0, group.items.length - 1), 0);
}

function showDuplicatesInList(index) {
  const group = duplicateGroups()[index];
  if (!group) return;
  state.view = 'files';
  state.category = 'Model weights';
  state.folder = null;
  state.selectedId = group.items[0]?.id || null;
  render();
  toast('Showing these copies in the file list');
}

// ---------------------------------------------------------------- table
function renderDuplicatesTable() {
  const groups = duplicateGroups();
  const wrap = $('.table-wrap');
  const scrollTop = wrap.scrollTop;
  if (!groups.length) {
    $('#fileRows').innerHTML = `<tr class="empty-state"><td colspan="5"><div class="empty-icon">⧉</div><strong>No duplicate scan yet</strong><p>Hash the scanned model files to look for byte-identical copies. Names, variants, and quantizations are never treated as duplicates.</p><button class="text-button" id="emptyDuplicates">Find duplicates</button></td></tr>`;
    const start = $('#emptyDuplicates');
    if (start) start.addEventListener('click', findDuplicates);
    wrap.scrollTop = scrollTop;
    return;
  }
  const rows = [];
  groups.forEach((group, index) => {
    const copies = group.items.length;
    const wasted = group.size * (copies - 1);
    rows.push(`<tr class="dup-group">
        <td class="dup-count">${copies}×</td>
        <td colspan="2"><div class="dup-title">Group ${index + 1} <span class="dup-hash" title="SHA-256 ${esc(group.sha256)}">${esc(String(group.sha256 || '').slice(0, 20))}…</span></div><div class="file-path">${copies} byte-identical copies · ${bytes(group.size)} each</div></td>
        <td class="size-cell">${bytes(wasted)}<div class="dup-wasted">duplicated bytes</div></td>
        <td><button class="info-btn dup-show" data-group="${index}">Show in list</button></td>
      </tr>`);
    group.items.forEach(item => {
      rows.push(`<tr class="dup-member ${state.selectedId === item.id ? 'active' : ''}" data-id="${esc(item.id || item.path)}">
          <td></td>
          <td><div class="file-name-cell"><span class="file-icon">▧</span><div style="min-width:0"><div class="file-name" title="${esc(item.path)}">${esc(item.name || baseName(item.path))}</div><div class="file-path" title="${esc(item.path)}">${esc(item.path)}</div></div></div></td>
          <td class="size-cell">${bytes(item.size)}</td>
          <td class="modified-cell">${esc(item.source || item.runtime || '—')}</td>
          <td><span class="category-pill model">review only</span></td>
        </tr>`);
    });
  });
  $('#fileRows').innerHTML = rows.join('');
  wrap.scrollTop = scrollTop;
}

function renderTable() {
  if (state.view === 'duplicates') {
    renderDuplicatesTable();
    return;
  }
  const wrap = $('.table-wrap');
  const scrollTop = wrap.scrollTop;
  const shown = filtered();
  const all = state.items;
  let sorted = [...shown].sort((a,b) => state.sort==='name' ? a.name.localeCompare(b.name) : state.sort==='date' ? a.modified.localeCompare(b.modified) : a.size-b.size);
  if (state.descending) sorted.reverse();
  if (!sorted.length) {
    $('#fileRows').innerHTML = `<tr class="empty-state"><td colspan="5"><div class="empty-icon">⌕</div><strong>${all.length?'No files match the current view':'No findings yet'}</strong><p>${all.length?'Clear a filter, change the folder, or search again.':'Scan known AI app locations, or add a project folder above.'}</p>${all.length?'<button class="text-button" id="emptyReset">Clear filters</button>':'<button class="text-button" id="emptyScan">Start a scan →</button>'}</td></tr>`;
    const reset = $('#emptyReset'); if (reset) reset.addEventListener('click', clearFilters);
    const start = $('#emptyScan'); if (start) start.addEventListener('click', runScan);
  } else {
    $('#fileRows').innerHTML = sorted.map(x => `<tr class="file-row ${x.selected?'selected':''} ${state.selectedId===x.id?'active':''}" data-id="${esc(x.id)}">
        <td><input class="file-check" type="checkbox" aria-label="Select ${esc(x.name)}" ${x.selected?'checked':''} ${selectable(x)?'':'disabled'} title="${esc(selectionTitle(x))}"></td>
        <td><div class="file-name-cell"><span class="file-icon">▧</span><div style="min-width:0"><div class="file-name" title="${esc(x.path)}">${esc(x.name)}</div><div class="file-path" title="${esc(x.guidance||x.path)}">${esc(dirName(x.path))}</div></div></div></td>
        <td class="size-cell" title="${x.logical_size!==undefined?`Potentially reclaimable ${bytes(x.size)}; logical model size ${bytes(x.logical_size)}`:''}">${bytes(x.size)}</td>
        <td class="modified-cell">${dateText(x.modified)}</td>
        <td><span class="category-pill ${x.category==='Superseded outputs'?'outputs':x.category==='Model weights'?'model':x.category==='Ollama partial downloads'?'partial':x.category==='Developer tools'?'developer':''}" title="${esc(x.category)}">${esc(x.category)}</span><span class="risk-chip ${esc(x.risk||'inspect')}" title="${esc(x.blocked_reason||x.guidance||x.risk||'')}">${esc((x.risk||'inspect').toUpperCase())}${x.age_blocked&&!x.model?' · RECENT':''}</span></td>
      </tr>`).join('');
  }
  wrap.scrollTop = scrollTop;

  const selected = selectedItems();
  const activeAction = selected.find(x=>x.cleanup_action)?.cleanup_action;
  const selectableRows = shown.filter(x=>selectable(x) && (!activeAction || x.cleanup_action===activeAction));
  const visibleSelected = selectableRows.filter(x=>x.selected).length;
  $('#selectVisible').disabled = selectableRows.length === 0;
  $('#selectVisible').checked = selectableRows.length > 0 && visibleSelected === selectableRows.length;
  $('#selectVisible').indeterminate = visibleSelected > 0 && visibleSelected < selectableRows.length;
  $('#shownCount').textContent = `${shown.length.toLocaleString()} item${shown.length===1?'':'s'} shown · ${selectableRows.length.toLocaleString()} selectable`;
}

// ---------------------------------------------------------------- bulk selection
function actionLabel(action) { return action === 'ollama-rm' ? 'ollama rm' : 'Trash'; }

function bulkEligible(folder) {
  if (folder !== undefined && (folder || null) !== state.folder) state.folder = folder || null;
  const rows = filtered();
  const where = state.folder ? `“${baseName(state.folder)}”` : 'the current view';
  const eligible = rows.filter(x => x.can_delete);
  if (!eligible.length) {
    $('#selectNote').textContent = `nothing eligible in ${where} — model weights and recent files stay protected`;
    render();
    toast('No cleanup-eligible files here — model weights and recent files stay protected');
    return;
  }
  // Never widen a selection to a second cleanup action: the Trash button only acts on one.
  const groups = new Map();
  for (const x of eligible) {
    const entry = groups.get(x.cleanup_action) || { action: x.cleanup_action, rows: [], bytes: 0 };
    entry.rows.push(x); entry.bytes += x.size;
    groups.set(x.cleanup_action, entry);
  }
  const locked = selectedItems().map(x => x.cleanup_action).find(action => action && action !== 'none');
  const chosen = (locked && groups.get(locked))
    || [...groups.values()].sort((a, b) => b.bytes - a.bytes || a.action.localeCompare(b.action))[0];
  chosen.rows.forEach(x => { x.selected = true; });

  const notes = [`${chosen.rows.length} selected for ${actionLabel(chosen.action)} · ${bytes(chosen.bytes)}`];
  for (const other of groups.values()) {
    if (other === chosen) continue;
    notes.push(`${other.rows.length} ${actionLabel(other.action)} left for a separate pass`);
  }
  const recent = rows.filter(x => !x.can_delete && x.age_blocked && !x.model).length;
  const protectedRows = rows.filter(x => !x.can_delete && !(x.age_blocked && !x.model)).length;
  if (recent) notes.push(`${recent} recent skipped (7-day rule)`);
  if (protectedRows) notes.push(`${protectedRows} runtime-managed or blocked`);
  $('#selectNote').textContent = notes.join(' · ');
  render();
  toast(`Selected ${chosen.rows.length} eligible file${chosen.rows.length === 1 ? '' : 's'} in ${where} — ${actionLabel(chosen.action)} · nothing moved yet`);
}

function clearSelectNote() {
  const note = $('#selectNote');
  if (note) note.textContent = '';
}

// ---------------------------------------------------------------- space map
let suppressMapClick = false;
function applyTransform() {
  const canvas = $('#treemapCanvas');
  if (!canvas) return;
  canvas.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.zoom})`;
  $('#zoomLabel').textContent = `${Math.round(state.zoom*100)}%`;
}

function renderMap() {
  const canvas = $('#treemapCanvas');
  const shown = filtered().slice(0, 100);
  if (!shown.length) {
    canvas.innerHTML = '<div class="map-empty">Run a scan to visualize space usage</div>';
    applyTransform();
    return;
  }
  // Area is proportional to file size; a modest minimum keeps tiny files discoverable.
  const adjusted = shown.map(x=>Math.max(Math.sqrt(x.size),12));
  const sum = adjusted.reduce((a,b)=>a+b,0);
  canvas.innerHTML = shown.map((x,i) => `<button class="map-block ${state.selectedId===x.id?'active':''}" title="${esc(x.path)} — ${bytes(x.size)}${x.logical_size!==undefined?` · ${bytes(x.logical_size)} logical`:''}" data-id="${esc(x.id)}" style="flex:${adjusted[i]/sum*100} 1 0;background:${colors[x.category]||'#527fad'}"><span>${esc(x.name)}</span><small>${bytes(x.size)}</small></button>`).join('');
  applyTransform();
}

function setZoom(next) {
  state.zoom = Math.min(8, Math.max(0.4, next));
  applyTransform();
}

// ---------------------------------------------------------------- empty folders
const EMPTY_KEEP_NOTE = 'Runtime layout folders (blobs, models, manifests, snapshots, refs, hub, checkpoints) are always kept.';

function renderEmptyDirs(folders) {
  const shown = folders.slice(0, 60);
  $('#emptyList').innerHTML = shown.map(folder =>
    `<li><span class="empty-path" title="${esc(folder.path)}">${esc(folder.path)}</span><small>${folder.depth} level${folder.depth === 1 ? '' : 's'} deep</small></li>`).join('')
    + (folders.length > shown.length ? `<li class="empty-more">…and ${(folders.length - shown.length).toLocaleString()} more</li>` : '');
}

async function findEmptyDirs() {
  if (state.busy) return;
  setBusy(true, 'Looking for empty folders…');
  try {
    const response = await fetch('/api/empty-dirs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not list empty folders');
    state.emptyDirs = data.folders || [];
    if (!state.emptyDirs.length) {
      $('#statusText').textContent = 'No empty folders to sweep';
      toast('No empty folders left in the scanned locations');
      return;
    }
    $('#emptyNote').textContent = `${state.emptyDirs.length.toLocaleString()} folder${state.emptyDirs.length === 1 ? '' : 's'} hold no files at all — no files, no hidden files, nothing but empty folders beneath. They move to your system Trash and the sweep is recorded in the audit manifest. ${EMPTY_KEEP_NOTE}${data.truncated ? ' Showing the first 500.' : ''}`;
    renderEmptyDirs(state.emptyDirs);
    $('#emptyConfirm').textContent = `Move ${state.emptyDirs.length.toLocaleString()} folder${state.emptyDirs.length === 1 ? '' : 's'} to Trash`;
    openDialog($('#emptyDialog'));
    $('#statusText').textContent = `${state.emptyDirs.length.toLocaleString()} empty folders found · nothing removed yet`;
  } catch (err) {
    toast(err.message);
    $('#statusText').textContent = 'Empty folder check failed';
  } finally {
    setBusy(false, $('#statusText').textContent);
  }
}

async function sweepEmptyDirs(event) {
  if (event) event.preventDefault();
  const paths = state.emptyDirs.map(folder => folder.path);
  if (!paths.length) { closeDialog($('#emptyDialog')); return; }
  closeDialog($('#emptyDialog'));
  setBusy(true, 'Moving empty folders to Trash…');
  try {
    const response = await fetch('/api/trash-empty-dirs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paths }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not move the empty folders');
    const moved = (data.moved || []).length;
    state.emptyDirs = [];
    if (data.errors?.length) {
      $('#warningLine').textContent = `${moved} empty folder${moved === 1 ? '' : 's'} moved · ${data.errors.length} skipped — ${data.errors[0].path}: ${data.errors[0].error}`;
      $('#warningLine').hidden = false;
    }
    toast(`${moved} empty folder${moved === 1 ? '' : 's'} moved to Trash · audit saved${data.remaining ? ` · ${data.remaining} still empty` : ''}`);
    $('#statusText').textContent = `Empty folders swept · ${moved} moved${data.remaining ? ` · ${data.remaining} still empty` : ''}`;
    $('#statusText').title = data.audit || '';
  } catch (err) {
    toast(err.message);
    $('#statusText').textContent = 'Empty folder sweep failed';
  } finally {
    setBusy(false, $('#statusText').textContent);
  }
}

// ---------------------------------------------------------------- space map export
const MAP_EXPORT_LIMIT = 150;

function stamp() { return new Date().toISOString().slice(0, 19).replace(/[:T]/g, ''); }

function downloadFile(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function folderGroupKey(item) {
  const root = item.root || dirName(item.path);
  const relative = String(item.path).slice(String(root).length).replace(/^[\\/]+/, '');
  const parts = relative.split(/[\\/]+/).filter(Boolean);
  const head = parts.length > 1 ? parts[0] : '';
  const rootName = baseName(root) || root;
  return head ? `${rootName}/${head}` : rootName;
}

function mapExportModel() {
  const grouped = state.mapScope === 'scan';
  const source = grouped ? state.items : filtered();
  const limit = grouped ? MAP_EXPORT_LIMIT * 3 : MAP_EXPORT_LIMIT;
  const items = [...source].sort((a, b) => b.size - a.size).slice(0, limit);
  let groups = null;
  if (grouped) {
    const byFolder = new Map();
    for (const item of items) {
      const key = folderGroupKey(item);
      const entry = byFolder.get(key) || { key, size: 0, items: [] };
      entry.size += item.size || 0;
      entry.items.push(item);
      byFolder.set(key, entry);
    }
    groups = [...byFolder.values()].sort((a, b) => b.size - a.size);
  }
  const categories = new Map();
  for (const item of items) {
    const entry = categories.get(item.category) || { category: item.category, size: 0, count: 0 };
    entry.size += item.size; entry.count += 1;
    categories.set(item.category, entry);
  }
  return {
    items, groups, shown: source.length,
    total: items.reduce((n, x) => n + x.size, 0),
    categories: [...categories.values()].sort((a, b) => b.size - a.size),
    filters: grouped ? 'whole scan · grouped by folder' : filterDescription(),
    generated: new Date(),
  };
}

/* Groups first, then the files inside each group — a folder-level treemap. */
function drawGroupedBlocks(ctx, model, box) {
  const groupRects = squarify(model.groups.map(group => Math.max(group.size, 1)), box);
  model.groups.forEach((group, index) => {
    const rect = groupRects[index];
    if (!rect || rect.w < 2 || rect.h < 2) return;
    ctx.fillStyle = '#191c21';
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
    ctx.strokeStyle = '#2b3038';
    ctx.lineWidth = 1;
    ctx.strokeRect(rect.x + 0.5, rect.y + 0.5, Math.max(rect.w - 1, 0), Math.max(rect.h - 1, 0));
    const header = rect.w > 110 && rect.h > 46 ? 17 : 0;
    if (header) {
      ctx.fillStyle = '#aab3bf';
      ctx.font = '600 11px "Segoe UI", system-ui, sans-serif';
      const label = shorten(ctx, `${group.key} · ${group.items.length} file${group.items.length === 1 ? '' : 's'} · ${bytes(group.size)}`, rect.w - 12);
      if (label) ctx.fillText(label, rect.x + 6, rect.y + 12);
    }
    const inner = { x: rect.x + 2, y: rect.y + header + 2, w: Math.max(rect.w - 4, 0), h: Math.max(rect.h - header - 4, 0) };
    if (inner.w <= 0 || inner.h <= 0) return;
    const fileRects = squarify(group.items.map(item => Math.max(item.size || 0, 1)), inner);
    group.items.forEach((item, fileIndex) => {
      const cell = fileRects[fileIndex];
      if (!cell || cell.w <= 0 || cell.h <= 0) return;
      ctx.fillStyle = colors[item.category] || '#527fad';
      ctx.fillRect(cell.x, cell.y, cell.w, cell.h);
      if (cell.w > 78 && cell.h > 24) {
        ctx.fillStyle = '#f6f9ff';
        ctx.font = '600 11px "Segoe UI", system-ui, sans-serif';
        const name = shorten(ctx, item.name, cell.w - 10);
        if (name) ctx.fillText(name, cell.x + 4, cell.y + 15);
      }
    });
  });
}

function worstRatio(row, sum, side) {
  const sideSq = side * side, sumSq = sum * sum;
  let worst = 0;
  for (const node of row) {
    const ratio = Math.max((node.area * sideSq) / sumSq, sumSq / (node.area * sideSq));
    if (ratio > worst) worst = ratio;
  }
  return worst;
}

/* Squarified treemap (Bruls et al.): area is proportional to size, aspect ratios stay near 1. */
function squarify(values, box) {
  const rects = values.map(() => ({ x: box.x, y: box.y, w: 0, h: 0 }));
  const total = values.reduce((a, b) => a + b, 0);
  if (!values.length || !(total > 0) || !(box.w > 0) || !(box.h > 0)) return rects;
  const scale = (box.w * box.h) / total;
  const nodes = values
    .map((value, index) => ({ index, area: Math.max(value * scale, 1) }))
    .sort((a, b) => b.area - a.area);
  let x = box.x, y = box.y, w = box.w, h = box.h, cursor = 0;
  while (cursor < nodes.length) {
    if (w <= 0.01 || h <= 0.01) break;
    const side = Math.min(w, h);
    const row = [];
    let rowArea = 0, worst = Infinity;
    while (cursor < nodes.length) {
      const node = nodes[cursor];
      const nextArea = rowArea + node.area;
      const nextWorst = worstRatio([...row, node], nextArea, side);
      if (row.length && nextWorst > worst) break;
      row.push(node); rowArea = nextArea; worst = nextWorst; cursor += 1;
    }
    if (w <= h) {
      const bandHeight = rowArea / w;
      let offset = x;
      for (const node of row) {
        const nodeWidth = node.area / bandHeight;
        rects[node.index] = { x: offset, y, w: nodeWidth, h: bandHeight };
        offset += nodeWidth;
      }
      y += bandHeight; h -= bandHeight;
    } else {
      const columnWidth = rowArea / h;
      let offset = y;
      for (const node of row) {
        const nodeHeight = node.area / columnWidth;
        rects[node.index] = { x, y: offset, w: columnWidth, h: nodeHeight };
        offset += nodeHeight;
      }
      x += columnWidth; w -= columnWidth;
    }
  }
  return rects;
}

function shorten(ctx, text, maxWidth) {
  const label = String(text ?? '');
  if (maxWidth <= 10) return '';
  if (ctx.measureText(label).width <= maxWidth) return label;
  let cut = label;
  while (cut.length > 1 && ctx.measureText(`${cut}…`).width > maxWidth) cut = cut.slice(0, -1);
  return cut.length > 1 ? `${cut}…` : '';
}

function drawMapImage(canvas, model) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return false;
  const W = canvas.width, H = canvas.height, pad = 44;
  ctx.fillStyle = '#12151a';
  ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = '#f1f4f8';
  ctx.font = '700 34px "Segoe UI", system-ui, sans-serif';
  ctx.fillText(model.groups ? 'AICleanUp — whole scan map by folder' : 'AICleanUp — space map', pad, 62);
  ctx.fillStyle = '#98a1ad';
  ctx.font = '500 16px "Segoe UI", system-ui, sans-serif';
  const summary = model.groups
    ? `${model.groups.length} folders · ${model.items.length} of ${model.shown} files · ${bytes(model.total)}`
    : `${model.items.length} of ${model.shown} files · ${bytes(model.total)} · ${model.filters}`;
  ctx.fillText(summary, pad, 94);

  let legendX = pad;
  const legendY = 130;
  ctx.font = '500 15px "Segoe UI", system-ui, sans-serif';
  for (const entry of model.categories.slice(0, 6)) {
    const label = `${entry.category} ${bytes(entry.size)}`;
    ctx.fillStyle = colors[entry.category] || '#527fad';
    ctx.fillRect(legendX, legendY - 12, 14, 14);
    ctx.fillStyle = '#c6ccd4';
    ctx.fillText(label, legendX + 21, legendY);
    legendX += 21 + ctx.measureText(label).width + 26;
  }

  const box = { x: pad, y: 158, w: W - pad * 2, h: H - 158 - 76 };
  if (model.groups) {
    drawGroupedBlocks(ctx, model, box);
  } else {
  const rects = squarify(model.items.map(item => Math.max(item.size || 0, 1)), box);
  model.items.forEach((item, index) => {
    const r = rects[index];
    if (!r || r.w <= 0 || r.h <= 0) return;
    ctx.fillStyle = colors[item.category] || '#527fad';
    ctx.fillRect(r.x, r.y, r.w, r.h);
    ctx.strokeStyle = 'rgba(10,12,16,0.55)';
    ctx.lineWidth = 1;
    ctx.strokeRect(r.x + 0.5, r.y + 0.5, Math.max(r.w - 1, 0), Math.max(r.h - 1, 0));
    if (r.w > 84 && r.h > 30) {
      ctx.fillStyle = '#f6f9ff';
      ctx.font = '600 14px "Segoe UI", system-ui, sans-serif';
      const name = shorten(ctx, item.name, r.w - 14);
      if (name) ctx.fillText(name, r.x + 7, r.y + 20);
      if (r.h > 46) {
        ctx.fillStyle = 'rgba(246,249,255,0.78)';
        ctx.font = '500 12px monospace';
        ctx.fillText(bytes(item.size), r.x + 7, r.y + 36);
      }
    }
  });
  }

  ctx.fillStyle = '#78818d';
  ctx.font = '500 13px "Segoe UI", system-ui, sans-serif';
  ctx.fillText(`Generated ${model.generated.toLocaleString()} · review only — nothing was changed`, pad, H - 26);
  return true;
}

function pdfRgb(hex) {
  const raw = String(hex || '').replace('#', '');
  const full = raw.length === 3 ? raw.split('').map(c => c + c).join('') : raw;
  const value = parseInt(full, 16);
  if (!full.match(/^[0-9a-f]{6}$/i) || Number.isNaN(value)) return [0.32, 0.5, 0.68];
  return [((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255];
}

function pdfString(text) {
  return String(text ?? '').replace(/[^\x20-\x7e]/g, '?').replace(/[\\()]/g, c => `\\${c}`);
}

function assemblePdf(objects) {
  let out = '%PDF-1.4\n';
  const offsets = [];
  objects.forEach((body, index) => {
    offsets.push(out.length);
    out += `${index + 1} 0 obj\n${body}\nendobj\n`;
  });
  const startxref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets) out += `${String(offset).padStart(10, '0')} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${startxref}\n%%EOF\n`;
  return out;
}

/* Vector PDF: the same squarified geometry as the PNG, written without any dependency. */
function buildMapPdf(model) {
  const W = 842, H = 595, pad = 34;   // A4 landscape in points
  const parts = [];
  const fill = (rgb) => `${rgb[0].toFixed(3)} ${rgb[1].toFixed(3)} ${rgb[2].toFixed(3)} rg`;
  parts.push(fill([0.071, 0.082, 0.102]), `0 0 ${W} ${H} re f`);
  parts.push('BT /F1 21 Tf 0.94 0.96 0.98 rg',
    `${pad} ${H - 46} Td (AICleanUp - ${model.groups ? 'whole scan map by folder' : 'space map'}) Tj ET`);
  parts.push('BT /F1 9 Tf 0.60 0.64 0.69 rg',
    `${pad} ${H - 62} Td (${pdfString(`${model.items.length} of ${model.shown} files | ${bytes(model.total)} | ${model.filters}`)}) Tj ET`);

  let legendX = pad;
  const legendY = H - 84;
  for (const entry of model.categories.slice(0, 6)) {
    const label = `${entry.category} ${bytes(entry.size)}`;
    parts.push(fill(pdfRgb(colors[entry.category] || '#527fad')), `${legendX} ${legendY - 8} 9 9 re f`);
    parts.push('BT /F1 8 Tf 0.78 0.81 0.85 rg', `${legendX + 13} ${legendY - 7} Td (${pdfString(label)}) Tj ET`);
    legendX += 13 + label.length * 4.4 + 18;
  }

  const box = { x: pad, y: 112, w: W - pad * 2, h: H - 112 - 66 };
  if (model.groups) {
    const groupRects = squarify(model.groups.map(group => Math.max(group.size, 1)), box);
    model.groups.forEach((group, index) => {
      const r = groupRects[index];
      if (!r || r.w <= 0 || r.h <= 0) return;
      const y = H - (r.y + r.h);
      parts.push(fill([0.098, 0.11, 0.129]), `${r.x.toFixed(2)} ${y.toFixed(2)} ${r.w.toFixed(2)} ${r.h.toFixed(2)} re f`);
      parts.push('0.169 0.188 0.220 RG 0.7 w', `${r.x.toFixed(2)} ${y.toFixed(2)} ${r.w.toFixed(2)} ${r.h.toFixed(2)} re S`);
      const header = r.w > 90 && r.h > 34 ? 12 : 0;
      if (header) {
        const room = Math.max(1, Math.floor((r.w - 8) / 4.4));
        const label = `${group.key} | ${group.items.length} file${group.items.length === 1 ? '' : 's'} | ${bytes(group.size)}`;
        const text = label.length > room ? `${label.slice(0, Math.max(room - 3, 1))}...` : label;
        parts.push('BT /F1 7 Tf 0.67 0.71 0.76 rg', `${(r.x + 4).toFixed(2)} ${(H - r.y - 9).toFixed(2)} Td (${pdfString(text)}) Tj ET`);
      }
      const inner = { x: r.x + 2, y: r.y + header + 2, w: Math.max(r.w - 4, 0), h: Math.max(r.h - header - 4, 0) };
      if (inner.w <= 0 || inner.h <= 0) return;
      const fileRects = squarify(group.items.map(item => Math.max(item.size || 0, 1)), inner);
      group.items.forEach((item, fileIndex) => {
        const cell = fileRects[fileIndex];
        if (!cell || cell.w <= 0 || cell.h <= 0) return;
        const cellY = H - (cell.y + cell.h);
        parts.push(fill(pdfRgb(colors[item.category] || '#527fad')), `${cell.x.toFixed(2)} ${cellY.toFixed(2)} ${cell.w.toFixed(2)} ${cell.h.toFixed(2)} re f`);
        if (cell.w > 56 && cell.h > 16 && !header) {
          const room = Math.max(1, Math.floor((cell.w - 8) / 3.9));
          const name = item.name.length > room ? `${item.name.slice(0, Math.max(room - 3, 1))}...` : item.name;
          parts.push('BT /F1 6.5 Tf 0.97 0.98 1 rg', `${(cell.x + 4).toFixed(2)} ${(H - cell.y - 8).toFixed(2)} Td (${pdfString(name)}) Tj ET`);
        }
      });
    });
  } else {
  const rects = squarify(model.items.map(item => Math.max(item.size || 0, 1)), box);
  model.items.forEach((item, index) => {
    const r = rects[index];
    if (!r || r.w <= 0 || r.h <= 0) return;
    const y = H - (r.y + r.h);
    parts.push(fill(pdfRgb(colors[item.category] || '#527fad')), `${r.x.toFixed(2)} ${y.toFixed(2)} ${r.w.toFixed(2)} ${r.h.toFixed(2)} re f`);
    parts.push('0.078 0.094 0.118 RG 0.5 w', `${r.x.toFixed(2)} ${y.toFixed(2)} ${r.w.toFixed(2)} ${r.h.toFixed(2)} re S`);
    if (r.w > 62 && r.h > 18) {
      const room = Math.max(1, Math.floor((r.w - 8) / 3.9));
      const name = item.name.length > room ? `${item.name.slice(0, Math.max(room - 3, 1))}...` : item.name;
      parts.push('BT /F1 6.5 Tf 0.97 0.98 1 rg', `${(r.x + 4).toFixed(2)} ${(H - r.y - 8).toFixed(2)} Td (${pdfString(name)}) Tj ET`);
    }
  });
  }

  parts.push('BT /F1 8 Tf 0.47 0.5 0.55 rg',
    `${pad} 34 Td (${pdfString(`Generated ${model.generated.toLocaleString()} | review only - nothing was changed`)}) Tj ET`);

  const stream = parts.join('\n');
  return assemblePdf([
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${W} ${H}] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>`,
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ]);
}

function exportMapPng() {
  const model = mapExportModel();
  if (!model.items.length) { toast(state.mapScope === 'scan' ? 'Scan a folder before exporting the whole-scan map' : 'Nothing in the current view to map'); return; }
  let canvas = null, drawn = false;
  try {
    canvas = document.createElement('canvas');
    canvas.width = 1600; canvas.height = 900;
    drawn = drawMapImage(canvas, model);
  } catch { drawn = false; }
  if (!drawn || !canvas || typeof canvas.toBlob !== 'function') { toast('PNG export needs a browser canvas — the PDF export still works'); return; }
  canvas.toBlob((blob) => {
    if (!blob) { toast('PNG export failed'); return; }
    downloadFile(blob, `aicleanup-map-${stamp()}.png`);
    toast('Space map exported as PNG — nothing was changed');
  }, 'image/png');
}

function exportMapPdf() {
  const model = mapExportModel();
  if (!model.items.length) { toast(state.mapScope === 'scan' ? 'Scan a folder before exporting the whole-scan map' : 'Nothing in the current view to map'); return; }
  downloadFile(new Blob([buildMapPdf(model)], { type: 'application/pdf' }), `aicleanup-map-${stamp()}.pdf`);
  $('#statusText').textContent = `Space map exported as PDF · ${model.items.length} blocks`;
  toast('Space map exported as PDF — nothing was changed');
}

// ---------------------------------------------------------------- inspector
function renderNodeInfo() {
  const host = $('#nodeInfo');
  const x = itemById(state.selectedId);
  if (!x) {
    host.innerHTML = '<p class="info-empty">Select a row in the file list or a block in the space map to inspect it.</p>';
    return;
  }
  const status = x.can_delete
    ? '<span class="status-pill ok">Eligible</span>'
    : overridable(x) ? '<span class="status-pill warn">Override available</span>'
    : '<span class="status-pill block">Blocked</span>';
  const variantNote = x.latest ? {
    ref: 'Current revision according to the runtime’s refs/main pointer.',
    tag: 'Tagged latest by the runtime.',
    modified: 'Newest file in this entry — this runtime keeps no pointer, so newer variants may exist upstream.',
  }[x.latest_by] : null;
  const rows = [
    ['Path', `<span class="info-path">${esc(x.path)}</span>`],
    ...(x.link_target ? [['Blob', `<span class="info-path">${esc(x.link_target)}</span>`]] : []),
    ...(x.model_entry ? [['Entry', `${esc(x.model_entry)}${x.latest ? ' <span class="status-pill ok">current</span>' : ''}${x.entry_count > 1 ? ` <span class="info-muted">· ${x.entry_count} variants · ${bytes(x.entry_size)}</span>` : ''}`]] : []),
    ...(x.variant ? [['Variant', esc(x.variant)]] : []),
    ['Size', `${bytes(x.size)}${x.logical_size!==undefined?` <span class="info-muted">· ${bytes(x.logical_size)} logical</span>`:''}`],
    ['Modified', esc(new Date(x.modified).toLocaleString())],
    ['Category', esc(x.category)],
    ['Risk', `${esc((x.risk||'inspect').toUpperCase())}${x.age_blocked?' <span class="info-muted">· recent</span>':''}`],
    ['Source', `${esc(x.source||'—')}${x.runtime?` <span class="info-muted">(${esc(x.runtime)})</span>`:''}`],
    ['Action', x.cleanup_action==='none' ? 'Protected — manage in its runtime' : x.cleanup_action==='ollama-rm' ? 'Remove with ollama rm' : 'Move to Trash'],
  ];
  host.innerHTML = `<div class="info-title">${esc(x.name)} ${status}</div>
    <dl class="info-grid">${rows.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    ${x.link_target?'<p class="info-note">Revision link to one shared blob — counted once across every revision.</p>':''}
    ${variantNote?`<p class="info-note">${esc(variantNote)}</p>`:''}
    ${x.blocked_reason?`<p class="info-note warn">${esc(x.blocked_reason)}</p>`:''}
    ${x.guidance?`<p class="info-note">${esc(x.guidance)}</p>`:''}
    <div class="info-actions">
      <button class="info-btn" id="inspectSelect" ${selectable(x)?'':'disabled'}>${x.selected?'Deselect':'Select for cleanup'}</button>
      <button class="info-btn" id="inspectFolder">Filter to folder</button>
      <button class="info-btn" id="inspectCopy">Copy path</button>
    </div>`;
  $('#inspectSelect').addEventListener('click', () => { x.selected = !x.selected; render(); });
  $('#inspectFolder').addEventListener('click', () => { state.folder = dirName(x.path); render(); });
  $('#inspectCopy').addEventListener('click', () => copyText(x.path));
}

function renderBranchInfo() {
  const host = $('#branchInfo');
  const x = itemById(state.selectedId);
  const folder = state.folder || (x ? dirName(x.path) : null);
  if (!folder) {
    host.innerHTML = '<p class="info-empty">Select a file, or a folder in the tree, to see its branch details.</p>';
    return;
  }
  const inBranch = state.items.filter(i => inFolder(i, folder));
  const total = inBranch.reduce((n,i)=>n+i.size,0);
  const allTotal = state.items.reduce((n,i)=>n+i.size,0) || 1;
  const eligibleRows = inBranch.filter(i=>i.can_delete);
  const eligible = eligibleRows.reduce((n,i)=>n+i.size,0);
  const owners = [...new Set(inBranch.map(i=>i.source).filter(Boolean))].slice(0,3).join(', ');
  const largest = [...inBranch].sort((a,b)=>b.size-a.size).slice(0,4);
  host.innerHTML = `<div class="info-title">${esc(baseName(folder))}</div>
    <div class="info-path">${esc(folder)}</div>
    <dl class="info-grid">
      <dt>Findings</dt><dd>${inBranch.length.toLocaleString()}</dd>
      <dt>Total size</dt><dd>${bytes(total)}</dd>
      <dt>Share of scan</dt><dd>${(total/allTotal*100).toFixed(1)}%</dd>
      <dt>Cleanup-eligible</dt><dd>${bytes(eligible)}</dd>
      <dt>Owner</dt><dd>${esc(owners||'—')}</dd>
    </dl>
    ${largest.length?`<ul class="info-list">${largest.map(i=>`<li title="${esc(i.path)}"><span>${esc(i.name)}</span><b>${bytes(i.size)}</b></li>`).join('')}</ul>`:''}
    <div class="info-actions"><button class="info-btn" id="branchSelect" ${eligibleRows.length?'':'disabled'} title="Select every cleanup-eligible file under this folder in one pass">Select eligible here<small>${eligibleRows.length}</small></button></div>`;
  const branchSelect = $('#branchSelect');
  if (branchSelect) branchSelect.addEventListener('click', () => bulkEligible(folder));
}

function renderRuntimes() {
  const host = $('#runtimePanel');
  const runtimes = state.runtimes;
  if (!runtimes.length) {
    host.innerHTML = '<p class="info-empty">Enable <b>Include model weights</b> and scan to inventory local model runtimes and their management commands.</p>';
    return;
  }
  host.innerHTML = runtimes.map(entry => {
    const active = state.runtime === entry.runtime;
    const cli = entry.cli
      ? `<span class="runtime-badge ok">${esc(entry.cli)} found</span>`
      : '<span class="runtime-badge">no CLI</span>';
    const store = entry.stores[0] || '';
    const extra = entry.stores.length > 1 ? ` <span class="info-muted">+${entry.stores.length - 1} more</span>` : '';
    const counts = [`${entry.models.toLocaleString()} model file${entry.models === 1 ? '' : 's'}`];
    if (entry.entries !== undefined && entry.entries !== null) counts.push(`${entry.entries.toLocaleString()} entr${entry.entries === 1 ? 'y' : 'ies'}`);
    counts.push(`${entry.stores.length} store${entry.stores.length === 1 ? '' : 's'}`);
    const flags = [];
    if (entry.in_use === null || entry.in_use === undefined) {
      flags.push('<div class="runtime-flag unknown">Process check unavailable</div>');
    } else if (entry.in_use.length) {
      const names = entry.in_use.map(process => `${esc(process.name)} (PID ${esc(process.pid)})`).join(', ');
      flags.push(`<div class="runtime-flag busy">In use by ${names}</div>`);
    }
    for (const handle of (entry.handles || []).slice(0, 2)) {
      flags.push(`<div class="runtime-flag">Open: ${esc(baseName(handle.path))} · ${esc(handle.name)} (PID ${esc(handle.pid)})</div>`);
    }
    return `<div class="runtime-row ${active ? 'active' : ''}" data-runtime="${esc(entry.runtime)}" title="${esc(entry.stores.join(' '))}">
        <div class="runtime-head"><span class="runtime-name">${esc(entry.runtime)}</span>${cli}<span class="runtime-size">${bytes(entry.bytes)}</span></div>
        <div class="runtime-meta">${counts.join(' · ')}</div>
        ${flags.join('')}
        ${store ? `<div class="info-path">${esc(store)}${extra}</div>` : ''}
        <div class="runtime-note">${esc(entry.management)}</div>
      </div>`;
  }).join('');
  $$('#runtimePanel .runtime-row').forEach(row => row.addEventListener('click', () => {
    state.runtime = state.runtime === row.dataset.runtime ? null : row.dataset.runtime;
    state.view = 'files';
    render();
  }));
}

function renderStats() {
  const host = $('#statsPanel');
  const all = state.items;
  if (!all.length) {
    host.innerHTML = '<p class="info-empty">Statistics appear after a scan.</p>';
    return;
  }
  const shown = filtered();
  const sum = all.reduce((n,x)=>n+x.size,0);
  const shownSum = shown.reduce((n,x)=>n+x.size,0);
  const largest = [...all].sort((a,b)=>b.size-a.size)[0];
  const eligible = all.filter(x=>x.can_delete).reduce((n,x)=>n+x.size,0);
  const categories = new Set(all.map(x=>x.category)).size;
  const restorable = state.audits.reduce((n,a)=>n+(a.restorable||0),0);
  const dup = state.duplicateGroups;
  host.innerHTML = `<dl class="info-grid">
      <dt>Findings</dt><dd>${all.length.toLocaleString()}</dd>
      <dt>Total size</dt><dd>${bytes(sum)}</dd>
      <dt>Current view</dt><dd>${shown.length.toLocaleString()} · ${bytes(shownSum)}</dd>
      <dt>Eligible now</dt><dd>${bytes(eligible)}</dd>
      <dt>Largest file</dt><dd title="${esc(largest.path)}">${esc(largest.name)} <span class="info-muted">· ${bytes(largest.size)}</span></dd>
      <dt>Average file</dt><dd>${bytes(Math.round(sum/all.length))}</dd>
      <dt>Categories</dt><dd>${categories}</dd>
      <dt>Restorable</dt><dd>${restorable?`${restorable} file${restorable===1?'':'s'}`:'none'}</dd>
      <dt>Duplicate groups</dt><dd>${dup===null?'not checked':`${dup.length} exact`}</dd>
    </dl>`;
}

// ---------------------------------------------------------------- summary
function renderSummary(all, shown) {
  const selected = selectedItems();
  const selectedBytes = selected.reduce((n,x)=>n+x.size,0);
  $('#recoverable').innerHTML = all.length ? `${(selectedBytes/1073741824).toFixed(selectedBytes/1073741824<10?2:1)} <small>GB</small>` : '— <small>GB</small>';
  $('#summaryNote').textContent = all.length ? `${bytes(selectedBytes)} selected · ${bytes(shown.reduce((n,x)=>n+x.size,0))} in view` : 'Scan to discover reclaimable space';
  $('#fileCount').textContent = all.length.toLocaleString();
  $('#locationCount').textContent = (all.length||state.locations) ? state.locations.toString() : '—';
  $('#selectedCount').textContent = selected.length;

  const actions = new Set(selected.map(x=>x.cleanup_action));
  const action = actions.size === 1 ? [...actions][0] : null;
  $('#cleanBtn span').textContent = action==='ollama-rm' ? 'Remove with Ollama' : actions.size>1 ? 'Choose one action' : 'Move to Trash';
  $('#cleanBtn').classList.toggle('ollama-action', action==='ollama-rm');
  $('#cleanBtn').disabled = !selected.length || !action || state.busy;

  const cats = {};
  all.forEach(x => cats[x.category] = (cats[x.category]||0) + x.size);
  const catList = Object.entries(cats).sort((a,b)=>b[1]-a[1]);
  const total = catList.reduce((n,x)=>n+x[1],0);
  $('#categoryBar').innerHTML = catList.map(([c,n]) => `<i title="${esc(c)}: ${bytes(n)}" style="width:${total?Math.max(n/total*100,1):0}%;background:${colors[c]||'#548eb8'}"></i>`).join('') || '<i></i>';
  $('#barLegend').textContent = catList.length ? catList.slice(0,3).map(([c,n])=>`${c} ${bytes(n)}`).join('   ·   ') : 'Waiting for scan';

  $('#duplicatesBtn').disabled = state.busy || !all.some(x=>x.model && x.cleanup_action==='none');
  $('#hfPreviewBtn').disabled = state.busy || !all.some(x=>x.runtime==='huggingface');
  const restorable = state.audits.reduce((n,a)=>n+(a.restorable||0),0);
  $('#restoreBtn').disabled = state.busy || restorable === 0;
  $('#restoreBtn').title = restorable ? `Restore ${restorable} file${restorable===1?'':'s'} from the last cleanup manifest` : 'No restorable cleanup manifest yet';

  $('#ignoreBtn').disabled = state.busy || !all.length;
  $('#emptyDirsBtn').disabled = state.busy || !all.length;
  const groups = duplicateGroups();
  const duplicatesView = state.view === 'duplicates';
  $('#viewFiles').classList.toggle('active', !duplicatesView);
  $('#viewDuplicates').classList.toggle('active', duplicatesView);
  $('#viewDuplicates').disabled = state.busy || !groups.length;
  $('#dupCount').textContent = groups.length;
  $('#filesHead').hidden = duplicatesView;
  $('#duplicatesHead').hidden = !duplicatesView;
  if (duplicatesView) $('#selectVisible').disabled = true;
  $('#filterBadge').textContent = state.category==='all' ? 'ALL' : state.category.toUpperCase();
  $('#resultSubtitle').textContent = duplicatesView
    ? `${groups.length} exact SHA-256 group${groups.length===1?'':'s'} · ${bytes(duplicateWaste(groups))} duplicated · review only`
    : all.length ? filterDescription() : 'Largest items appear first';
  $('#resultTitle').innerHTML = duplicatesView ? 'Duplicate review <span id="filterBadge">MODELS</span>' : 'File findings <span id="filterBadge">' + (state.category==='all' ? 'ALL' : esc(state.category.toUpperCase())) + '</span>';
  $('#shownCount').textContent = duplicatesView
    ? `${groups.length} group${groups.length===1?'':'s'} · ${groups.reduce((n,g)=>n+g.items.length,0)} files · nothing changes on disk`
    : $('#shownCount').textContent;
  $('#filterNote').textContent = `${shown.length.toLocaleString()} of ${all.length.toLocaleString()} files · ${filterDescription()}`;
  const eligibleInView = shown.filter(x => x.can_delete);
  $('#selectEligible').disabled = state.busy || duplicatesView || !eligibleInView.length;
  $('#selectEligible').textContent = state.folder ? 'Select eligible in this folder' : 'Select eligible in view';
  $('#selectEligible').title = state.folder
    ? `Select every cleanup-eligible file under ${state.folder} in one pass`
    : 'Select every cleanup-eligible file in the current view in one pass';
}

function render() {
  const shown = filtered();
  renderSummary(state.items, shown);
  renderFolderTree();
  renderRiskFilters();
  renderCategories();
  renderTable();
  renderMap();
  renderNodeInfo();
  renderBranchInfo();
  renderRuntimes();
  renderStats();
  renderScopes();
}

// ---------------------------------------------------------------- helpers
function copyText(text) {
  const done = () => toast('Path copied to clipboard');
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const area = document.createElement('textarea');
  area.value = text; area.setAttribute('readonly',''); area.style.position='fixed'; area.style.opacity='0';
  document.body.appendChild(area); area.select();
  try { document.execCommand('copy'); done(); } catch { toast('Copy is unavailable in this browser'); }
  area.remove();
}

function openDialog(dialog) {
  try { dialog.showModal(); } catch { dialog.setAttribute('open', ''); }
}
function closeDialog(dialog) {
  try { dialog.close(); } catch { dialog.removeAttribute('open'); }
}

function scanRoots() {
  const roots = new Map();
  for (const item of state.items) {
    const root = item.root || dirName(item.path);
    if (!root) continue;
    if (!roots.has(root)) roots.set(root, { root, label: item.source || baseName(root), size: 0 });
    roots.get(root).size += item.size || 0;
  }
  return [...roots.values()].sort((a,b)=>b.size-a.size);
}

function setOverrideRecent(value) {
  const next = !!value;
  state.overrideRecent = next;
  $('#overrideRecent').checked = next;
  if (!next) state.items.forEach(x => { if (!x.can_delete) x.selected = false; });
  render();
  if (next) toast('Age override enabled — recent files can now be selected; model weights stay blocked');
}

function clearFilters() {
  state.category = 'all'; state.risk = 'all'; state.folder = null; state.runtime = null; state.query = '';
  $('#search').value = '';
  clearSelectNote();
  render();
}

function exportDuplicateRows(kind) {
  const groups = duplicateGroups();
  if (!groups.length) { toast('Run a duplicate check first'); return; }
  const stamp = new Date().toISOString().slice(0,19).replace(/[:T]/g,'');
  let text, mime, ext;
  if (kind === 'json') {
    text = JSON.stringify({
      exported: new Date().toISOString(),
      kind: 'exact-duplicates',
      groups: groups.map((group, index) => ({
        group: index + 1, sha256: group.sha256, size: group.size, copies: group.items.length,
        duplicated_bytes: group.size * (group.items.length - 1),
        files: group.items.map(item => ({ path: item.path, name: item.name, size: item.size, source: item.source, runtime: item.runtime })),
      })),
    }, null, 2);
    mime = 'application/json'; ext = 'json';
  } else {
    const cell = (value) => { const s = value === undefined || value === null ? '' : String(value); return /[",\n]/.test(s) ? `"${s.replace(/"/g,'""')}"` : s; };
    const lines = ['group,sha256,size,copies,duplicated_bytes,path,source,runtime'];
    groups.forEach((group, index) => group.items.forEach(item => lines.push([
      index + 1, group.sha256, group.size, group.items.length, group.size * (group.items.length - 1),
      item.path, item.source || '', item.runtime || '',
    ].map(cell).join(','))));
    text = lines.join('\r\n');
    mime = 'text/csv'; ext = 'csv';
  }
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement('a');
  link.href = url; link.download = `aicleanup-duplicates-${stamp}.${ext}`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  toast(`Exported ${groups.length} duplicate group${groups.length===1?'':'s'} as ${ext.toUpperCase()} — nothing was changed`);
}

function exportRows(kind) {
  if (state.view === 'duplicates') { exportDuplicateRows(kind); return; }
  const rows = filtered();
  if (!rows.length) { toast('Nothing to export in the current view'); return; }
  const fields = ['path','name','size','modified','category','risk','runtime','source','model_entry','variant','entry_size','entry_count','latest','latest_by','cleanup_action','can_delete','age_blocked','blocked_reason','guidance','link_target'];
  const stamp = new Date().toISOString().slice(0,19).replace(/[:T]/g,'');
  let text, mime, ext;
  if (kind === 'json') {
    const items = rows.map(row => Object.fromEntries(fields.map(f => [f, row[f] === undefined ? null : row[f]])));
    text = JSON.stringify({ exported: new Date().toISOString(), filters: { folder: state.folder, category: state.category, risk: state.risk, query: state.query }, count: rows.length, items }, null, 2);
    mime = 'application/json'; ext = 'json';
  } else {
    const cell = (value) => { const s = value === undefined || value === null ? '' : String(value); return /[",\n]/.test(s) ? `"${s.replace(/"/g,'""')}"` : s; };
    text = [fields.join(','), ...rows.map(row => fields.map(f => cell(row[f])).join(','))].join('\r\n');
    mime = 'text/csv'; ext = 'csv';
  }
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement('a');
  link.href = url; link.download = `aicleanup-findings-${stamp}.${ext}`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  toast(`Exported ${rows.length.toLocaleString()} row${rows.length===1?'':'s'} as ${ext.toUpperCase()}`);
}

async function openIgnoreEditor() {
  if (state.busy) return;
  const roots = scanRoots();
  if (!roots.length) { toast('Scan first so the ignore editor knows which folders to use'); return; }
  const selector = $('#ignoreRoot');
  selector.innerHTML = roots.map(entry => `<option value="${esc(entry.root)}">${esc(entry.label)} — ${esc(entry.root)}</option>`).join('');
  const preferred = state.folder && roots.some(entry => entry.root === state.folder) ? state.folder : roots[0].root;
  selector.value = preferred;
  $('#ignoreNote').textContent = 'Patterns are saved to the scanned folder’s own .aicleanignore file; nothing else is written.';
  await loadIgnoreRules(preferred);
  openDialog($('#ignoreDialog'));
}

async function loadIgnoreRules(root) {
  const area = $('#ignoreRules');
  area.value = '';
  area.placeholder = 'Loading…';
  try {
    const response = await fetch(`/api/ignore?root=${encodeURIComponent(root)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not read ignore rules');
    area.value = (data.rules || []).join('\n');
    area.placeholder = '*.tmp\ncache/**\n# comments are kept';
    $('#ignoreNote').textContent = data.exists
      ? `${data.path} — ${(data.rules||[]).length} line(s) currently ignored.`
      : `No .aicleanignore yet in ${data.root}; saving creates one.`;
  } catch (err) {
    area.placeholder = 'Rules unavailable';
    toast(err.message);
  }
}

async function saveIgnoreRules(event) {
  if (event) event.preventDefault();
  if (state.busy) return;
  const root = $('#ignoreRoot').value;
  const rules = $('#ignoreRules').value.split('\n');
  setBusy(true, 'Saving ignore rules…');
  try {
    const response = await fetch('/api/ignore', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ root, rules }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not save ignore rules');
    closeDialog($('#ignoreDialog'));
    const count = (data.rules || []).length;
    toast(data.removed ? 'Ignore file removed — default scanning restored' : `Saved ${count} ignore rule${count===1?'':'s'}`);
    $('#statusText').textContent = data.removed ? 'Ignore file removed · rescanning' : `Ignore rules saved · ${count} pattern${count===1?'':'s'}`;
    await runScan();
  } catch (err) {
    toast(err.message);
    $('#statusText').textContent = 'Saving ignore rules failed';
  } finally {
    setBusy(false, $('#statusText').textContent);
  }
}

function setBusy(on, text) {
  state.busy = on;
  $('#scanBtn').disabled = on;
  $('#refreshBtn').disabled = on;
  $('#duplicatesBtn').disabled = !state.hashing && (on || !state.items.some(x=>x.model && x.cleanup_action==='none'));
  $('#hfPreviewBtn').disabled = on || !state.items.some(x=>x.runtime==='huggingface');
  $('#restoreBtn').disabled = on || !state.audits.some(a=>a.restorable>0);
  $('#viewDuplicates').disabled = on || !duplicateGroups().length;
  $('#ignoreBtn').disabled = on || !state.items.length;
  $('#emptyDirsBtn').disabled = on || !state.items.length;
  $('#selectEligible').disabled = on || state.view === 'duplicates' || !filtered().some(x=>x.can_delete);
  $('#progress').classList.toggle('running', on);
  $('#statusText').textContent = text || (on ? 'Scanning…' : 'Ready');
  $('.status-light').classList.toggle('working', on);
}

// ---------------------------------------------------------------- actions
async function runScan() {
  if (state.busy) return;
  setBusy(true, 'Scanning locations…');
  $('#warningLine').hidden = true;
  try {
    const response = await fetch('/api/scan', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ workspace: $('#workspace').value.trim(), include_models: $('#includeModels').checked }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Scan failed');
    state.items = data.items || [];
    state.locations = data.scanned || 0;
    state.category = 'all'; state.risk = 'all'; state.folder = null; state.runtime = null; state.query = '';
    state.selectedId = null; state.duplicateGroups = null; state.expanded = new Set(); state.view = 'files';
    $('#search').value = '';
    clearSelectNote();
    if (data.warnings?.length) { $('#warningLine').textContent = data.warnings.join(' '); $('#warningLine').hidden = false; }
    $('#statusText').textContent = `Scan complete · ${state.items.length.toLocaleString()} files found`;
    await refreshRuntimes();
    render();
    toast(state.items.length ? `Scan complete — ${state.items.length.toLocaleString()} candidate files found` : 'Scan complete — no candidate files found');
  } catch (err) {
    $('#statusText').textContent = 'Scan failed';
    toast(err.message);
  } finally {
    setBusy(false, $('#statusText').textContent);
  }
}

async function pollDuplicateProgress() {
  try {
    const response = await fetch('/api/duplicates/progress');
    if (!response.ok || !state.hashing) return;
    const progress = await response.json();
    if (!progress.running || !state.hashing) return;
    const pct = progress.total ? Math.min(100, Math.round((progress.done / progress.total) * 100)) : 0;
    $('#statusText').textContent = `Hashing ${progress.done.toLocaleString()} of ${progress.total.toLocaleString()} files · ${pct}% · ${bytes(progress.bytes_done)} of ${bytes(progress.bytes_total)}`;
    const bar = $('#progress');
    bar.classList.add('running');
    bar.firstElementChild.style.width = `${pct}%`;
  } catch { /* progress is best-effort; the request itself still reports the result */ }
}

async function cancelDuplicateScan() {
  if (!state.hashing) return;
  $('#statusText').textContent = 'Cancelling the duplicate check…';
  try {
    await fetch('/api/duplicates/cancel', { method:'POST', headers:{'Content-Type':'application/json'}, body: '{}' });
  } catch { /* the running request reports the outcome */ }
}

async function findDuplicates() {
  if (state.busy) return;
  state.hashing = true;
  setBusy(true, 'Hashing model files…');
  $('#duplicatesBtn').classList.add('running');
  $('#duplicatesBtn span').textContent = 'Cancel hashing';
  const poll = setInterval(pollDuplicateProgress, 250);
  try {
    const response = await fetch('/api/duplicates', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Duplicate scan failed');
    const groups = data.groups || [];
    if (data.cancelled) {
      state.duplicateGroups = [];
      state.view = 'files';
      $('#statusText').textContent = 'Duplicate check cancelled · nothing was changed';
      toast('Duplicate check cancelled — nothing was changed');
      render();
      return;
    }
    state.duplicateGroups = groups;
    if (!groups.length) {
      $('#warningLine').textContent = 'No byte-identical model files found. Similar names are never treated as duplicates.';
      $('#warningLine').hidden = false;
      toast('No exact duplicates found');
    } else {
      const savings = groups.reduce((sum,g) => sum + g.size*Math.max(0,g.items.length-1), 0);
      const detail = groups.slice(0,4).map(g => `${bytes(g.size)} each · ${g.items.slice(0,3).map(i=>i.path).join('  ↔  ')}${g.items.length>3?`  (+${g.items.length-3} more)`:''}`).join('   |   ');
      $('#warningLine').textContent = `${groups.length} exact SHA-256 duplicate group${groups.length===1?'':'s'} · about ${bytes(savings)} duplicated bytes. Review only; no files were changed. ${detail}${groups.length>4?'  |  More groups omitted.':''}`;
      $('#warningLine').hidden = false;
      state.view = 'duplicates';
      toast(`Found ${groups.length} exact duplicate groups; nothing was changed`);
    }
    $('#statusText').textContent = `Duplicate check complete · ${groups.length} exact groups`;
    render();
  } catch (err) {
    toast(err.message);
    $('#statusText').textContent = 'Duplicate check failed';
  } finally {
    clearInterval(poll);
    state.hashing = false;
    $('#duplicatesBtn').classList.remove('running');
    $('#duplicatesBtn span').textContent = 'Find duplicates';
    $('#progress').firstElementChild.style.width = '';
    setBusy(false, $('#statusText').textContent);
  }
}

async function previewHuggingFacePrune() {
  if (state.busy) return;
  setBusy(true, 'Running Hugging Face preview…');
  try {
    const response = await fetch('/api/hf-prune-preview', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Preview failed');
    $('#outputTitle').textContent = 'Hugging Face prune preview (read-only)';
    $('#outputNote').textContent = `${data.command} — nothing was removed. Run the same command without --dry-run yourself to prune.`;
    $('#outputText').textContent = data.output || 'No unused revisions reported.';
    $('#outputDialog').showModal();
    $('#statusText').textContent = 'Hugging Face preview complete — no files changed';
  } catch (err) {
    toast(err.message);
    $('#statusText').textContent = 'Preview failed';
  } finally {
    setBusy(false, $('#statusText').textContent);
  }
}

async function refreshRuntimes() {
  try {
    const response = await fetch('/api/runtimes');
    if (!response.ok) return;
    const data = await response.json();
    state.runtimes = data.runtimes || [];
  } catch { state.runtimes = []; }
}

async function refreshAudits() {
  try {
    const response = await fetch('/api/audits');
    if (!response.ok) return;
    const data = await response.json();
    state.audits = data.audits || [];
    render();
  } catch { /* restore stays disabled when the manifest list is unavailable */ }
}

async function restoreLastCleanup() {
  if (state.busy) return;
  const audit = state.audits.find(a=>a.restorable>0);
  if (!audit) { toast('No restorable cleanup manifest yet'); return; }
  $('#confirmDialog h2').textContent = 'Restore the last cleanup?';
  $('#confirmDialog .dialog-trash').textContent = 'Restore files';
  $('#confirmText').textContent = `${audit.restorable} of ${audit.items} item${audit.items===1?'':'s'} from ${new Date(audit.created).toLocaleString()} can be moved back to their original folders. Restores never overwrite an existing file. Items removed with ollama rm are re-downloaded instead.`;
  const dialog = $('#confirmDialog');
  if (await new Promise(resolve => { dialog.addEventListener('close', () => resolve(dialog.returnValue==='confirm'), {once:true}); dialog.showModal(); }) !== true) return;
  setBusy(true, 'Restoring files…');
  try {
    const response = await fetch('/api/restore', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ audit: audit.name }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Restore failed');
    const restored = (data.restored||[]).length, skipped = (data.skipped||[]).length, errors = (data.errors||[]).length;
    const note = [`${restored} restored`, skipped?`${skipped} need the system Trash`:null, errors?`${errors} blocked`:null].filter(Boolean).join(' · ');
    toast(`Restore complete — ${note}`);
    $('#statusText').textContent = `Restore complete · ${note}`;
    $('#statusText').title = data.restore_log || '';
    if (errors || skipped) {
      const details = [...(data.errors||[]).map(e=>`${e.path}: ${e.error}`), ...(data.skipped||[]).map(s=>`${s.path}: ${s.reason}`)].slice(0,3).join('  |  ');
      $('#warningLine').textContent = details;
      $('#warningLine').hidden = false;
    }
    await refreshAudits();
    await runScan();
  } catch (err) {
    toast(err.message);
    $('#statusText').textContent = 'Restore failed';
  } finally {
    setBusy(false, $('#statusText').textContent);
  }
}

async function moveToTrash() {
  const selected = selectedItems();
  if (!selected.length) return;
  const actions = new Set(selected.map(x=>x.cleanup_action));
  if (actions.size !== 1) return;
  const ollama = actions.has('ollama-rm');
  const bytesTotal = selected.reduce((n,x)=>n+x.size,0);
  const logicalTotal = selected.reduce((n,x)=>n+(x.logical_size||x.size),0);
  const overriddenCount = selected.filter(x=>!x.can_delete).length;
  $('#confirmDialog h2').textContent = ollama ? 'Remove selected models with Ollama?' : overriddenCount ? 'Override the 7-day rule for these files?' : 'Move selected items to Trash?';
  $('#confirmDialog .dialog-trash').textContent = ollama ? 'Run ollama rm' : overriddenCount ? 'Move anyway' : 'Move to Trash';
  $('#confirmText').textContent = ollama
    ? `The Ollama CLI will remove ${selected.length} selected model tag${selected.length===1?'':'s'} (${bytes(logicalTotal)} logical size). Estimated space freed: ${bytes(bytesTotal)}; shared blobs remain. Loaded models are blocked.`
    : `${selected.length.toLocaleString()} item${selected.length===1?'':'s'} (${bytes(bytesTotal)}) will be moved to your system Trash.${overriddenCount?` ${overriddenCount} of them changed within the last 7 days; the age override is recorded in the audit manifest.`:''} Open handles and running related clients are still blocked.`;
  const dialog = $('#confirmDialog');
  if (await new Promise(resolve => { dialog.addEventListener('close', () => resolve(dialog.returnValue==='confirm'), {once:true}); dialog.showModal(); }) !== true) return;
  setBusy(true, ollama ? 'Removing through Ollama…' : 'Moving files to Trash…');
  try {
    const endpoint = ollama ? '/api/ollama-remove' : '/api/trash';
    const response = await fetch(endpoint, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ ids: selected.map(x=>x.id), override_recent: overriddenCount > 0 }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Cleanup failed');
    const moved = new Set((data.moved||[]).map(x=>x.path));
    const removedIds = new Set(selected.filter(x=>moved.has(x.path)).map(x=>x.id));
    state.items = state.items.filter(x => !removedIds.has(x.id));
    if (state.selectedId && removedIds.has(state.selectedId)) state.selectedId = null;
    render();
    if (data.errors?.length) {
      const detail = `${data.errors[0].path}: ${data.errors[0].error}`;
      toast(`${data.moved.length} completed · ${data.errors.length} blocked · ${data.errors[0].error}`);
      $('#statusText').textContent = `${data.moved.length} completed · ${data.errors.length} blocked`;
      $('#statusText').title = detail;
    } else {
      toast(ollama ? `${data.moved.length} model tag${data.moved.length===1?'':'s'} removed by Ollama · audit saved` : `${data.moved.length} item${data.moved.length===1?'':'s'} moved to Trash · audit saved`);
      $('#statusText').textContent = `Cleanup complete · audit saved to ${data.audit}`;
      $('#statusText').title = data.audit || '';
    }
    if (!ollama) await refreshAudits();
  } catch (err) {
    toast(err.message);
    $('#statusText').textContent = 'Cleanup failed';
  } finally {
    setBusy(false, $('#statusText').textContent);
  }
}

let toastTimer;
function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('visible'), 3500);
}

// ---------------------------------------------------------------- events
$('#scanBtn').addEventListener('click', runScan);
$('#refreshBtn').addEventListener('click', runScan);
$('#cleanBtn').addEventListener('click', moveToTrash);
$('#duplicatesBtn').addEventListener('click', () => (state.hashing ? cancelDuplicateScan() : findDuplicates()));
$('#viewFiles').addEventListener('click', () => { state.view = 'files'; render(); });
$('#viewDuplicates').addEventListener('click', () => { state.view = 'duplicates'; render(); });
$('#hfPreviewBtn').addEventListener('click', previewHuggingFacePrune);
$('#restoreBtn').addEventListener('click', restoreLastCleanup);
$('#exportCsv').addEventListener('click', () => exportRows('csv'));
$('#exportJson').addEventListener('click', () => exportRows('json'));
$('#search').addEventListener('input', e => { state.query = e.target.value; render(); });
$('#clearFilters').addEventListener('click', clearFilters);
$('#clearPath').addEventListener('click', () => { $('#workspace').value = ''; });
$('#locationInfo').addEventListener('click', () => toast('Scans standard AI app folders in your profile. Add a project folder to detect old scratch and patch files.'));
$('#collapseTree').addEventListener('click', () => { state.expanded.clear(); render(); });
$('#ignoreBtn').addEventListener('click', openIgnoreEditor);
$('#ignoreRoot').addEventListener('change', e => { loadIgnoreRules(e.target.value); });
$('#ignoreSave').addEventListener('click', saveIgnoreRules);
$('#folderTree').addEventListener('click', onTreeClick);
$('#riskFilters').addEventListener('click', e => {
  const chip = e.target.closest('.filter-chip');
  if (!chip) return;
  state.risk = chip.dataset.risk;
  render();
});
$('#sortSize').addEventListener('click', () => { state.sort = 'size'; state.descending = !state.descending; render(); });
$('#sortName').addEventListener('click', () => { state.sort = 'name'; state.descending = !state.descending; render(); });
$('#fileRows').addEventListener('change', e => {
  const check = e.target.closest('.file-check');
  if (!check) return;
  const row = check.closest('.file-row');
  const item = state.items.find(i=>i.id === row.dataset.id);
  if (item) item.selected = check.checked;
  render();
});
$('#fileRows').addEventListener('click', e => {
  const show = e.target.closest('.dup-show');
  if (show) {
    showDuplicatesInList(Number(show.dataset.group));
    return;
  }
  if (e.target.closest('.file-check')) return;
  const row = e.target.closest('.file-row') || e.target.closest('.dup-member');
  if (!row) return;
  state.selectedId = row.dataset.id;
  render();
});
$('#selectVisible').addEventListener('change', e => {
  const activeAction = selectedItems().find(x=>x.cleanup_action)?.cleanup_action;
  filtered().filter(x=>selectable(x) && (!activeAction || x.cleanup_action===activeAction)).forEach(x => { x.selected = e.target.checked; });
  render();
});
$('#overrideRecent').addEventListener('change', e => setOverrideRecent(e.target.checked));
$('#scopeSave').addEventListener('click', openScopeEditor);
$('#scopeDelete').addEventListener('click', deleteScope);
$('#scopeSelect').addEventListener('change', e => applyScope(e.target.value));
$('#scopeConfirm').addEventListener('click', commitScope);
$('#selectEligible').addEventListener('click', () => bulkEligible());
$('#mapScope').addEventListener('change', e => { state.mapScope = e.target.value === 'scan' ? 'scan' : 'view'; });
$('#exportMapPng').addEventListener('click', exportMapPng);
$('#emptyDirsBtn').addEventListener('click', findEmptyDirs);
$('#emptyConfirm').addEventListener('click', sweepEmptyDirs);
$('#exportMapPdf').addEventListener('click', exportMapPdf);

$('#treemapCanvas').addEventListener('click', e => {
  if (suppressMapClick) { suppressMapClick = false; return; }
  const block = e.target.closest('.map-block');
  if (!block) return;
  state.selectedId = block.dataset.id;
  render();
});
$('#treemapCanvas').addEventListener('dblclick', e => {
  const block = e.target.closest('.map-block');
  if (!block) return;
  const item = itemById(block.dataset.id);
  if (!item) return;
  state.folder = dirName(item.path);
  state.category = 'all';
  render();
});
$('#zoomIn').addEventListener('click', () => setZoom(state.zoom * 1.25));
$('#zoomOut').addEventListener('click', () => setZoom(state.zoom / 1.25));
$('#zoomReset').addEventListener('click', () => { state.zoom = 1; state.panX = 0; state.panY = 0; applyTransform(); });
$('#treemap').addEventListener('wheel', e => {
  e.preventDefault();
  setZoom(state.zoom * (e.deltaY < 0 ? 1.12 : 0.89));
}, { passive: false });

let drag = null;
$('#treemap').addEventListener('pointerdown', e => {
  if (e.button !== 0) return;
  drag = { x: e.clientX, y: e.clientY, panX: state.panX, panY: state.panY, moved: false };
  $('#treemap').setPointerCapture(e.pointerId);
  $('#treemap').classList.add('dragging');
});
$('#treemap').addEventListener('pointermove', e => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
  state.panX = drag.panX + dx;
  state.panY = drag.panY + dy;
  applyTransform();
});
function endDrag() {
  if (!drag) return;
  suppressMapClick = drag.moved;
  drag = null;
  $('#treemap').classList.remove('dragging');
}
$('#treemap').addEventListener('pointerup', endDrag);
$('#treemap').addEventListener('pointercancel', endDrag);

$('#confirmTrash').addEventListener('click', e => { e.preventDefault(); $('#confirmDialog').close('confirm'); });
$('#confirmDialog .cancel-btn').addEventListener('click', e => { e.preventDefault(); $('#confirmDialog').close('cancel'); });
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== $('#search') && !e.metaKey && !e.ctrlKey) {
    e.preventDefault();
    $('#search').focus();
  }
});

state.scopes = readScopes();
render();
refreshAudits();
runScan();
