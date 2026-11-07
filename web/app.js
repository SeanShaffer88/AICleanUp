const $ = (s) => document.querySelector(s);
const state = { items: [], locations: 0, category: 'all', query: '', sort: 'size', descending: true, busy: false };
const colors = { 'Caches':'#3c78a9', 'Application data':'#527fad', 'Developer tools':'#47845f', 'Superseded outputs':'#a6783e', 'Model weights':'#795493', 'Project files':'#62758a' };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function bytes(n) { if (n < 1024) return `${n} B`; const units=['KB','MB','GB','TB']; let i=-1; do { n/=1024; i++; } while(n>=1024 && i<units.length-1); return `${n.toFixed(1)} ${units[i]}`; }
function dateText(s) { try { const d=new Date(s); return d.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'2-digit'}); } catch { return s; } }
function filtered() { return state.items.filter(x => (state.category==='all'||x.category===state.category) && (!state.query || `${x.name} ${x.path} ${x.category}`.toLowerCase().includes(state.query.toLowerCase()))); }
function selectedItems() { return state.items.filter(x=>x.selected); }
function render() {
  const all=state.items, shown=filtered(), selected=selectedItems(), selectedBytes=selected.reduce((n,x)=>n+x.size,0);
  $('#recoverable').innerHTML=all.length?`${(selectedBytes/1073741824).toFixed(selectedBytes/1073741824<10?2:1)} <small>GB</small>`:'— <small>GB</small>';
  $('#summaryNote').textContent=all.length?`${bytes(selectedBytes)} currently selected for cleanup`:'Scan to discover reclaimable space';
  $('#fileCount').textContent=all.length.toLocaleString();
  $('#locationCount').textContent=all.length||state.locations?state.locations.toString():'—';
  $('#selectedCount').textContent=selected.length;
  $('#cleanBtn').disabled=!selected.length||state.busy;
  $('#filterBadge').textContent=state.category==='all'?'ALL':state.category.toUpperCase();
  $('#resultSubtitle').textContent=all.length?`${shown.length.toLocaleString()} of ${all.length.toLocaleString()} candidate files · largest first`:'Largest items appear first';
  const cats={}; all.forEach(x=>cats[x.category]=(cats[x.category]||0)+x.size);
  const catList=Object.entries(cats).sort((a,b)=>b[1]-a[1]);
  const total=catList.reduce((n,x)=>n+x[1],0);
  $('#categoryBar').innerHTML=catList.map(([c,n])=>`<i title="${esc(c)}: ${bytes(n)}" style="width:${total?Math.max(n/total*100,1):0}%;background:${colors[c]||'#548eb8'}"></i>`).join('')||'<i></i>';
  $('#barLegend').textContent=catList.length?catList.slice(0,3).map(([c,n])=>`${c} ${bytes(n)}`).join('   ·   '):'Waiting for scan';
  renderTree(cats);
  let sorted=[...shown].sort((a,b)=>state.sort==='name'?a.name.localeCompare(b.name):state.sort==='date'?a.modified.localeCompare(b.modified):a.size-b.size);
  if(state.descending) sorted.reverse();
  if(!sorted.length) {
    $('#fileRows').innerHTML=`<tr class="empty-state"><td colspan="5"><div class="empty-icon">${all.length?'⌕':'⌕'}</div><strong>${all.length?'No matching files':'No findings yet'}</strong><p>${all.length?'Try another search or category.':'Scan known AI app locations, or add a project folder above.'}</p>${all.length?'':'<button class="text-button" id="emptyScan">Start a scan →</button>'}</td></tr>`;
    $('#shownCount').textContent='0 items';
    const empty=$('#emptyScan'); if(empty) empty.addEventListener('click',runScan);
  } else {
    $('#fileRows').innerHTML=sorted.map(x=>`<tr class="file-row ${x.selected?'selected':''}" data-id="${esc(x.id)}"><td><input class="file-check" type="checkbox" aria-label="Select ${esc(x.name)}" ${x.selected?'checked':''}></td><td><div class="file-name-cell"><span class="file-icon">▧</span><div style="min-width:0"><div class="file-name" title="${esc(x.path)}">${esc(x.name)}</div><div class="file-path" title="${esc(x.path)}">${esc(x.path)}</div></div></div></td><td class="size-cell">${bytes(x.size)}</td><td class="modified-cell">${dateText(x.modified)}</td><td><span class="category-pill ${x.category==='Superseded outputs'?'outputs':x.category==='Model weights'?'model':x.category==='Developer tools'?'developer':''}" title="${esc(x.category)}">${esc(x.category)}</span></td></tr>`).join('');
    $('#shownCount').textContent=`${shown.length.toLocaleString()} item${shown.length===1?'':'s'}${state.query?' matching':''}`;
    document.querySelectorAll('.file-row').forEach(row=>row.querySelector('.file-check').addEventListener('change',e=>{const x=state.items.find(i=>i.id===row.dataset.id); if(x)x.selected=e.target.checked;render();}));
  }
  const visibleIds=shown.map(x=>x.id), visibleSelected=shown.filter(x=>x.selected).length;
  $('#selectVisible').checked=visibleIds.length>0&&visibleSelected===visibleIds.length;
  $('#selectVisible').indeterminate=visibleSelected>0&&visibleSelected<visibleIds.length;
  renderMap(catList, total);
}
function renderTree(cats) {
  const children=$('#treeChildren');
  if(!Object.keys(cats).length){children.innerHTML='<div class="tree-empty">Your scan results will appear here.</div>';return;}
  children.innerHTML=Object.entries(cats).sort((a,b)=>b[1]-a[1]).map(([cat,size])=>`<button class="tree-child ${state.category===cat?'active':''}" data-category="${esc(cat)}"><i class="category-dot" style="background:${colors[cat]||'#5595c8'}"></i><span class="tree-name">${esc(cat)}</span><span class="tree-size">${bytes(size)}</span></button>`).join('');
  document.querySelectorAll('.tree-child').forEach(b=>b.addEventListener('click',()=>{state.category=b.dataset.category;render();}));
  const allBtn=document.querySelector('.tree-item'); allBtn.querySelector('.tree-size').textContent=bytes(Object.values(cats).reduce((a,b)=>a+b,0));
}
function renderMap(catList,total) {
  const eligible=filtered().slice(0,100);
  if(!eligible.length){$('#treemap').innerHTML='<div class="map-empty">Run a scan to visualize space usage</div>';return;}
  // Area is proportional to file size, while a modest minimum keeps tiny files discoverable.
  const adjusted=eligible.map(x=>Math.max(Math.sqrt(x.size),12)), sum=adjusted.reduce((a,b)=>a+b,0);
  $('#treemap').innerHTML=eligible.map((x,i)=>`<button class="map-block" title="${esc(x.path)} — ${bytes(x.size)}" data-category="${esc(x.category)}" style="flex:${adjusted[i]/sum*100} 1 0;background:${colors[x.category]||'#527fad'}"><span>${esc(x.name)}</span><small>${bytes(x.size)}</small></button>`).join('');
  document.querySelectorAll('.map-block').forEach(b=>b.addEventListener('click',()=>{state.category=b.dataset.category;render();}));
}
function setBusy(on, text) { state.busy=on; $('#scanBtn').disabled=on; $('#refreshBtn').disabled=on; $('#progress').classList.toggle('running',on); $('#statusText').textContent=text|| (on?'Scanning…':'Ready'); $('.status-light').classList.toggle('working',on); }
async function runScan() {
  if(state.busy)return;
  setBusy(true,'Scanning locations…'); $('#warningLine').hidden=true;
  try {
    const response=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:$('#workspace').value.trim(),include_models:$('#includeModels').checked})});
    const data=await response.json(); if(!response.ok)throw new Error(data.error||'Scan failed');
    state.items=data.items||[]; state.locations=data.scanned||0; state.category='all';
    if(data.warnings?.length){$('#warningLine').textContent=data.warnings.join(' ');$('#warningLine').hidden=false;}
    $('#statusText').textContent=`Scan complete · ${state.items.length.toLocaleString()} files found`;
    render(); toast(state.items.length?`Scan complete — ${state.items.length.toLocaleString()} candidate files found`:'Scan complete — no candidate files found');
  } catch(err) { $('#statusText').textContent='Scan failed'; toast(err.message); }
  finally { setBusy(false, $('#statusText').textContent); }
}
async function moveToTrash() {
  const selected=selectedItems(); if(!selected.length)return;
  $('#confirmText').textContent=`${selected.length.toLocaleString()} item${selected.length===1?'':'s'} (${bytes(selected.reduce((n,x)=>n+x.size,0))}) will be moved to your operating system’s Trash. You can restore them later.`;
  const dialog=$('#confirmDialog');
  if(await new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.showModal();})!==true)return;
  setBusy(true,'Moving files to Trash…');
  try {
    const response=await fetch('/api/trash',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:selected.map(x=>x.id)})});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Unable to move files');
    const moved=new Set((data.moved||[]).map(x=>x.path)); state.items=state.items.filter(x=>!moved.has(x.path));
    render();
    if(data.errors?.length){toast(`${data.moved.length} moved · ${data.errors.length} skipped. See details in the app status.`);$('#statusText').textContent=`${data.moved.length} moved · ${data.errors.length} skipped`;}
    else {toast(`${data.moved.length} item${data.moved.length===1?'':'s'} moved to Trash · audit log saved`);$('#statusText').textContent=`Cleanup complete · audit saved to ${data.audit}`;}
  }catch(err){toast(err.message);$('#statusText').textContent='Cleanup failed';}
  finally{setBusy(false,$('#statusText').textContent);}
}
let toastTimer;function toast(text){const el=$('#toast');el.textContent=text;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),3500);}
$('#scanBtn').addEventListener('click',runScan);$('#refreshBtn').addEventListener('click',runScan);$('#cleanBtn').addEventListener('click',moveToTrash);$('#search').addEventListener('input',e=>{state.query=e.target.value;render();});
$('#selectVisible').addEventListener('change',e=>{filtered().forEach(x=>x.selected=e.target.checked);render();});
$('#sortSize').addEventListener('click',()=>{state.sort='size';state.descending=!state.descending;render();});
$('#sortName').addEventListener('click',()=>{state.sort='name';state.descending=!state.descending;render();});
$('#clearPath').addEventListener('click',()=>$('#workspace').value='');
$('.tree-item').addEventListener('click',()=>{state.category='all';render();});
$('#locationInfo').addEventListener('click',()=>toast('Scans standard AI app folders in your user profile. Add a project folder to detect old scratch and patch files.'));
$('#confirmTrash').addEventListener('click',e=>{e.preventDefault();$('#confirmDialog').close('confirm');});
$('#confirmDialog .cancel-btn').addEventListener('click',e=>{e.preventDefault();$('#confirmDialog').close('cancel');});
render();
// Populate the dashboard on launch, so the first view is immediately useful.
runScan();
