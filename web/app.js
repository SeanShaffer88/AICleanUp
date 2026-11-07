const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  items: [], locations: 0,
  category: 'all', risk: 'all', folder: null, query: '',
  sort: 'size', descending: true, busy: false,
  audits: [], overrideRecent: false,
  selectedId: null, duplicateGroups: null, view: 'files',
  zoom: 1, panX: 0, panY: 0,
  expanded: new Set(),
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
    if (!inFolder(x, state.folder)) return false;
    if (q && !`${x.name} ${x.path} ${x.category} ${x.source||''} ${x.runtime||''}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function filterDescription() {
  const bits = [];
  if (state.folder) bits.push(`folder ${baseName(state.folder)}`);
  if (state.category !== 'all') bits.push(state.category);
  if (state.risk !== 'all') bits.push(state.risk === 'recent' ? 'recent only' : `${state.risk} risk`);
  if (state.query.trim()) bits.push(`“${state.query.trim()}”`);
  return bits.length ? bits.join(' · ') : 'no filters applied';
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
  const rows = [
    ['Path', `<span class="info-path">${esc(x.path)}</span>`],
    ['Size', `${bytes(x.size)}${x.logical_size!==undefined?` <span class="info-muted">· ${bytes(x.logical_size)} logical</span>`:''}`],
    ['Modified', esc(new Date(x.modified).toLocaleString())],
    ['Category', esc(x.category)],
    ['Risk', `${esc((x.risk||'inspect').toUpperCase())}${x.age_blocked?' <span class="info-muted">· recent</span>':''}`],
    ['Source', `${esc(x.source||'—')}${x.runtime?` <span class="info-muted">(${esc(x.runtime)})</span>`:''}`],
    ['Action', x.cleanup_action==='none' ? 'Protected — manage in its runtime' : x.cleanup_action==='ollama-rm' ? 'Remove with ollama rm' : 'Move to Trash'],
  ];
  host.innerHTML = `<div class="info-title">${esc(x.name)} ${status}</div>
    <dl class="info-grid">${rows.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
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
  const eligible = inBranch.filter(i=>i.can_delete).reduce((n,i)=>n+i.size,0);
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
    ${largest.length?`<ul class="info-list">${largest.map(i=>`<li title="${esc(i.path)}"><span>${esc(i.name)}</span><b>${bytes(i.size)}</b></li>`).join('')}</ul>`:''}`;
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
  renderStats();
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

function clearFilters() {
  state.category = 'all'; state.risk = 'all'; state.folder = null; state.query = '';
  $('#search').value = '';
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
  const fields = ['path','name','size','modified','category','risk','runtime','source','cleanup_action','can_delete','age_blocked','blocked_reason','guidance'];
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
  $('#duplicatesBtn').disabled = on || !state.items.some(x=>x.model && x.cleanup_action==='none');
  $('#hfPreviewBtn').disabled = on || !state.items.some(x=>x.runtime==='huggingface');
  $('#restoreBtn').disabled = on || !state.audits.some(a=>a.restorable>0);
  $('#viewDuplicates').disabled = on || !duplicateGroups().length;
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
    state.category = 'all'; state.risk = 'all'; state.folder = null; state.query = '';
    state.selectedId = null; state.duplicateGroups = null; state.expanded = new Set(); state.view = 'files';
    $('#search').value = '';
    if (data.warnings?.length) { $('#warningLine').textContent = data.warnings.join(' '); $('#warningLine').hidden = false; }
    $('#statusText').textContent = `Scan complete · ${state.items.length.toLocaleString()} files found`;
    render();
    toast(state.items.length ? `Scan complete — ${state.items.length.toLocaleString()} candidate files found` : 'Scan complete — no candidate files found');
  } catch (err) {
    $('#statusText').textContent = 'Scan failed';
    toast(err.message);
  } finally {
    setBusy(false, $('#statusText').textContent);
  }
}

async function findDuplicates() {
  if (state.busy) return;
  setBusy(true, 'Hashing model files…');
  try {
    const response = await fetch('/api/duplicates', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Duplicate scan failed');
    const groups = data.groups || [];
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
$('#duplicatesBtn').addEventListener('click', findDuplicates);
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
$('#overrideRecent').addEventListener('change', e => {
  state.overrideRecent = e.target.checked;
  if (!state.overrideRecent) state.items.forEach(x => { if (!x.can_delete) x.selected = false; });
  render();
  if (state.overrideRecent) toast('Age override enabled — recent files can now be selected; model weights stay blocked');
});

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

render();
refreshAudits();
runScan();
