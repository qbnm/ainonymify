import { resolveShortcut } from './shortcuts.mjs';

const $ = id => document.getElementById(id);
let session, currentDocument, currentPage = 1, interactionMode = null, selected, region;
const bulkSelection = new Set();
let pdfjs, pdfCache = new Map(), renderTask;
let nativeSelectionDraft = null;
let previewResizeTimer, renderedPreviewWidth = 0;
const types = ['PERSON','COMPANY','PROPERTY','ADDRESS','LOCATION','IBAN','BIC','EMAIL','PHONE','TAX_ID','VAT_ID','ACCOUNT','URL','OTHER'];
const option = (value, label) => { const e = document.createElement('option'); e.value = value; e.textContent = label; return e; };
for (const type of types) $('edit-type').append(option(type, type));
function message(text) { $('message').textContent = text; $('message').hidden = false; }
$('message').onclick = () => $('message').hidden = true;
async function work(text, action) {
  $('message').hidden = true; $('busy-text').textContent = text; $('busy').hidden = false;
  try { await action(); } catch (error) { message(error.message || 'Local processing failed'); }
  finally { $('busy').hidden = true; }
}
async function api(path, {method='GET', data, form}={}) {
  const headers = {};
  if (method !== 'GET') headers['X-Privacy-Gateway'] = '1';
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(new URL(path, location.origin).href, {method, headers, body: form || (data !== undefined ? JSON.stringify(data) : undefined), cache:'no-store'});
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail; } catch {}
    if (response.status === 404 && session) detail += '. Start a new session if this one expired.';
    throw new Error(detail);
  }
  return response;
}
const mutate = async (suffix, data) => (await api(`/api/sessions/${session.id}${suffix}`, {method:'POST', data})).json();
async function begin(files) {
  if (!files.length) return;
  await work(`Uploading ${files.length} PDF${files.length===1?'':'s'} locally…`, async () => {
    if (Array.from(files).some(f => !f.name.toLowerCase().endsWith('.pdf'))) throw new Error('Choose PDF files only.');
    session = await (await api('/api/sessions', {method:'POST'})).json();
    const form = new FormData(); for (const file of files) form.append('files', file);
    session = await (await api(`/api/sessions/${session.id}/documents`, {method:'POST', form})).json();
    $('busy-text').textContent = 'Analyzing text and page pixels locally…';
    session = await mutate('/analyze');
    bulkSelection.clear(); setInteractionMode(null);
    currentDocument = session.documents[0].id; currentPage = 1;
    $('landing').hidden = true; $('review').hidden = false;
    await update();
  });
}
$('pdf-files').onchange = event => begin(event.target.files);
const dropzone = $('dropzone');
for (const name of ['dragenter','dragover']) dropzone.addEventListener(name, event => {event.preventDefault(); dropzone.classList.add('drag');});
for (const name of ['dragleave','drop']) dropzone.addEventListener(name, event => {event.preventDefault(); dropzone.classList.remove('drag');});
dropzone.addEventListener('drop', event => begin(event.dataTransfer.files));
function entities() { return new Map(session.entities.map(e => [e.id,e])); }
function pageDetections() { return session.detections.filter(d => d.document_id===currentDocument && d.page===currentPage); }
async function update() {
  clearNativeSelection();
  const validIds = new Set(session.detections.map(d => d.id));
  for (const id of bulkSelection) if (!validIds.has(id)) bulkSelection.delete(id);
  const pending = session.detections.filter(d => d.status==='pending').length;
  const confirmed = session.detections.filter(d => d.status==='confirmed').length;
  $('session-summary').textContent = `${session.documents.length} documents · ${confirmed} selected regions · ${pending} unconfirmed suggestions will stay visible`;
  $('document-select').replaceChildren(...session.documents.map(d => option(d.id, d.name)));
  $('document-select').value = currentDocument;
  const doc = session.documents.find(d => d.id===currentDocument);
  $('page-label').textContent = `Page ${currentPage} / ${doc.pages.length}`;
  $('previous').disabled = currentPage===1; $('next').disabled = currentPage===doc.pages.length;
  $('generate-button').disabled = false;
  $('download').hidden = session.status!=='complete';
  $('mapping-notice').hidden = !session.includes_mapping;
  const single = session.documents.length===1;
  $('download-link').textContent = single ? 'Download anonymized PDF again' : 'Download ZIP again';
  $('download-link').download = single ? 'document-01.anonymized.pdf' : 'privacy-session.zip';
  $('mapping-download-link').hidden = !single || !session.includes_mapping;
  $('mapping-download-link').href = `/api/sessions/${session.id}/download/mapping`;
  $('download-link').href = `/api/sessions/${session.id}/download`;
  $('verification').hidden = !session.warnings.length;
  $('verification').replaceChildren();
  if (session.warnings.length) {
    const title = document.createElement('h2'); title.textContent = 'Verification warning — download withheld'; $('verification').append(title);
    const p = document.createElement('p'); p.textContent = 'A selected region may not have been fully redacted. Check or enlarge the listed region and generate again. Unselected content does not block downloads.'; $('verification').append(p);
    for (const warning of session.warnings) {
      const b = document.createElement('button');
      b.textContent = `${session.documents.find(d=>d.id===warning.document_id)?.name || 'Document'} · page ${warning.page}: ${warning.reason}`;
      b.onclick = () => work('Opening page…', async () => {currentDocument=warning.document_id; currentPage=warning.page; await update();});
      $('verification').append(b);
    }
  }
  const used = new Set(session.detections.map(d => d.entity_id));
  for (const name of ['merge-source','merge-target']) {
    $(name).replaceChildren(option('', 'Choose an entity'), ...session.entities.filter(e => used.has(e.id)).map(e => option(e.id, `${e.token} — ${e.value || '[visual only]'}`)));
  }
  await renderPage(doc);
  drawDetections();
}
async function renderPage(doc) {
  if (!pdfjs) {
    pdfjs = await import('/vendor/pdfjs/pdf.mjs');
    pdfjs.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/pdf.worker.mjs';
  }
  if (!pdfCache.has(doc.id)) {
    const response = await api(`/api/sessions/${session.id}/documents/${doc.id}/pdf`);
    const data = new Uint8Array(await response.arrayBuffer());
    pdfCache.set(doc.id, await pdfjs.getDocument({data, isEvalSupported:false, useSystemFonts:false,
      cMapUrl:'/vendor/pdfjs/cmaps/', cMapPacked:true, standardFontDataUrl:'/vendor/pdfjs/standard_fonts/',
      wasmUrl:'/vendor/pdfjs/wasm/'}).promise);
  }
  if (renderTask) { renderTask.cancel(); try {await renderTask.promise;} catch {} }
  const page = await pdfCache.get(doc.id).getPage(currentPage);
  const viewport = page.getViewport({scale:1});
  const scroller = document.querySelector('.preview-scroll');
  renderedPreviewWidth = scroller.clientWidth;
  const padding = getComputedStyle(scroller);
  const available = scroller.clientWidth - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight);
  const scale = Math.min(Math.max(available, 200) / viewport.width, 2);
  const visible = page.getViewport({scale});
  const dpr = window.devicePixelRatio || 1;
  const canvas = $('pdf-canvas'); canvas.width = Math.floor(visible.width*dpr); canvas.height = Math.floor(visible.height*dpr);
  $('page-stage').style.width = `${visible.width}px`; $('page-stage').style.height = `${visible.height}px`;
  renderTask = page.render({canvasContext:canvas.getContext('2d'), viewport:visible, transform:[dpr,0,0,dpr,0,0]});
  await renderTask.promise; renderTask = null;
  renderNativeText(doc.pages[currentPage-1],scale);
}
function drawDetections() {
  const items = pageDetections(), byId = entities();
  $('detection-count').textContent = `${items.length} on page`;
  $('detections').replaceChildren(); $('overlays').replaceChildren();
  const size = session.documents.find(d=>d.id===currentDocument).pages[currentPage-1];
  if (!items.length) {
    const p = document.createElement('p'); p.className='hint'; p.textContent='No sensitive information detected with certainty. Inspect the page and draw any missing regions.'; $('detections').append(p);
  }
  for (const d of items) {
    const entity = byId.get(d.entity_id);
    const status = d.status==='pending' ? '○ Not selected' : d.status==='ignored' ? '○ Ignored' : ['manual','native_text'].includes(d.source) ? '＋ Manual' : '✓ Confirmed';
    const row = document.createElement('div'); row.className='detection-row'; row.dataset.detectionId=d.id;
    const checkbox = document.createElement('input'); checkbox.type='checkbox'; checkbox.className='detection-select';
    checkbox.setAttribute('aria-label', `Select ${entity.token} occurrence ${d.id}`);
    checkbox.onchange=()=>toggleBulkSelection(d.id, checkbox.checked);
    const card = document.createElement('button'); card.className=`detection ${d.status}`;
    const strong = document.createElement('strong'); strong.textContent=`${status} · ${d.replacement || '[visual only]'}`;
    const value = document.createElement('span'); value.textContent=entity.value || '[visual-only region]';
    const source = document.createElement('small'); source.textContent = `${d.source} · ${d.reason || 'Validated pattern'}`;
    card.append(strong,value,source); card.onclick=()=>editDetection(d); row.append(checkbox,card); $('detections').append(row);
    for (const bbox of d.rects?.length ? d.rects : [d.bbox]) {
      const box = document.createElement('button'); box.className=`bbox ${d.status} ${['manual','native_text'].includes(d.source)?'manual':''}`;
      box.setAttribute('aria-label', `${status}: ${entity.type}`);
      box.dataset.detectionId=d.id;
      box.style.left=`${bbox[0]/size.width*100}%`; box.style.top=`${bbox[1]/size.height*100}%`;
      box.style.width=`${(bbox[2]-bbox[0])/size.width*100}%`; box.style.height=`${(bbox[3]-bbox[1])/size.height*100}%`;
      const label = document.createElement('span'); label.className='bbox-label'; label.textContent=`${status} ${d.replacement || '[visual only]'}`;
      box.append(label); box.onclick=event=>{if(event.ctrlKey||event.metaKey||event.shiftKey) toggleBulkSelection(d.id);else editDetection(d);}; $('overlays').append(box);
    }
  }
  syncBulkSelection();
}
function toggleBulkSelection(id, checked = !bulkSelection.has(id)) {
  if (checked) bulkSelection.add(id); else bulkSelection.delete(id);
  syncBulkSelection();
}
function syncBulkSelection() {
  const items = pageDetections(), count = items.filter(d=>bulkSelection.has(d.id)).length;
  $('select-page-detections').checked = items.length > 0 && count === items.length;
  $('select-page-detections').indeterminate = count > 0 && count < items.length;
  $('select-page-detections').disabled = items.length === 0;
  $('bulk-selection-count').textContent = `${bulkSelection.size} selected (${count} on page)`;
  for (const id of ['bulk-confirm','bulk-ignore','clear-selection']) $(id).disabled = bulkSelection.size === 0;
  for (const row of $('detections').querySelectorAll('.detection-row')) {
    const checked = bulkSelection.has(row.dataset.detectionId);
    row.classList.toggle('bulk-selected',checked);
    row.querySelector('.detection-select').checked = checked;
  }
  for (const box of $('overlays').querySelectorAll('.bbox')) box.classList.toggle('selected',bulkSelection.has(box.dataset.detectionId));
}
function selectPageDetections(checked) {
  for (const d of pageDetections()) {
    if (checked) bulkSelection.add(d.id); else bulkSelection.delete(d.id);
  }
  syncBulkSelection();
}
$('select-page-detections').onchange=event=>selectPageDetections(event.target.checked);
$('clear-selection').onclick=()=>{bulkSelection.clear();syncBulkSelection();};
async function applyBulkAction(status) {
  if (!bulkSelection.size) return;
  await work('Updating selected detections…',async()=>{
    session=await mutate('/detections/bulk',{detection_ids:[...bulkSelection],status});
    await update();
  });
}
$('bulk-confirm').onclick=()=>applyBulkAction('confirmed');
$('bulk-ignore').onclick=()=>applyBulkAction('ignored');
// Keep the review window in the viewport without changing form values or focus.
const editDialog = $('edit-dialog');
const dragHandle = $('edit-drag-handle');
let dialogDrag = null;
function positionDialog(left, top) {
  const rect = editDialog.getBoundingClientRect();
  const gap = 8;
  Object.assign(editDialog.style, {
    position: 'fixed', inset: 'auto', margin: '0',
    left: `${Math.max(gap, Math.min(left, window.innerWidth - rect.width - gap))}px`,
    top: `${Math.max(gap, Math.min(top, window.innerHeight - rect.height - gap))}px`,
  });
}
function keepDialogVisible() {
  if (!editDialog.open || !editDialog.style.left) return;
  const rect = editDialog.getBoundingClientRect();
  positionDialog(rect.left, rect.top);
}
function openEditDialog() {
  editDialog.showModal();
  keepDialogVisible();
}
function endDialogDrag() {
  dialogDrag = null;
  dragHandle.classList.remove('dragging');
}
dragHandle.addEventListener('pointerdown', event => {
  if (!event.isPrimary || event.button !== 0) return;
  event.preventDefault();
  const rect = editDialog.getBoundingClientRect();
  dialogDrag = {id:event.pointerId, x:event.clientX - rect.left, y:event.clientY - rect.top};
  positionDialog(rect.left, rect.top);
  dragHandle.setPointerCapture(event.pointerId);
  dragHandle.classList.add('dragging');
});
dragHandle.addEventListener('pointermove', event => {
  if (!dialogDrag || event.pointerId !== dialogDrag.id) return;
  positionDialog(event.clientX - dialogDrag.x, event.clientY - dialogDrag.y);
});
dragHandle.addEventListener('pointerup', event => {
  if (!dialogDrag || event.pointerId !== dialogDrag.id) return;
  endDialogDrag();
  if (dragHandle.hasPointerCapture(event.pointerId)) dragHandle.releasePointerCapture(event.pointerId);
});
dragHandle.addEventListener('pointercancel', endDialogDrag);
dragHandle.addEventListener('lostpointercapture', endDialogDrag);
editDialog.addEventListener('close', endDialogDrag);
window.addEventListener('resize', keepDialogVisible);
dragHandle.addEventListener('keydown', event => {
  const steps = {ArrowLeft:[-1,0], ArrowRight:[1,0], ArrowUp:[0,-1], ArrowDown:[0,1]};
  if (event.key === 'Home') {
    event.preventDefault();
    const rect = editDialog.getBoundingClientRect();
    positionDialog((window.innerWidth - rect.width)/2, (window.innerHeight - rect.height)/2);
  } else if (steps[event.key]) {
    event.preventDefault();
    const rect = editDialog.getBoundingClientRect(), step = event.shiftKey ? 40 : 10;
    positionDialog(rect.left + steps[event.key][0]*step, rect.top + steps[event.key][1]*step);
  }
});
function editDetection(d) {
  selected=d; region=null;
  const entity=entities().get(d.entity_id);
  $('edit-type').value=entity.type; $('edit-value').value=entity.value;
  $('edit-evidence').textContent = [d.reason, `Original extraction: ${d.original_text || '[none]'}`, d.secondary_ocr && `Secondary OCR:\n${d.secondary_ocr}`].filter(Boolean).join('\n\n');
  $('edit-separate').checked=false; $('split-label').hidden=false; $('visual-label').hidden=true; $('edit-ignore').hidden=false;
  openEditDialog();
}
$('edit-cancel').onclick = () => $('edit-dialog').close();
async function saveEdit(status) {
  const data = {type:$('edit-type').value, value:$('edit-value').value, status, separate:$('edit-separate').checked};
  await work('Updating selection and finding repeated text…', async () => {
    const previousRepeats = new Set(session.detections.filter(d=>d.status==='confirmed'&&d.source==='repeated_text').map(d=>d.id));
    if (region) session=await mutate(region.character_ids ? '/detections/from-text' : '/detections', {...region,type:data.type,value:data.value,visual_only:$('visual-only').checked});
    else session=await mutate(`/detections/${selected.id}`,data);
    $('edit-dialog').close(); await update();
    const addedRepeats = session.detections.filter(d=>d.status==='confirmed'&&d.source==='repeated_text'&&!previousRepeats.has(d.id));
    if (addedRepeats.length) message(`Automatically selected ${addedRepeats.length} additional occurrence${addedRepeats.length===1?'':'s'} in this PDF, across ${new Set(addedRepeats.map(d=>d.page)).size} page(s). You can ignore individual occurrences.`);
  });
}
$('edit-form').onsubmit=event=>{event.preventDefault();saveEdit('confirmed');};
$('edit-ignore').onclick=()=>saveEdit('ignored');
$('document-select').onchange=event=>work('Opening document…',async()=>{currentDocument=event.target.value;currentPage=1;await update();});
$('previous').onclick=()=>work('Opening page…',async()=>{currentPage--;await update();});
$('next').onclick=()=>work('Opening page…',async()=>{currentPage++;await update();});
$('merge-button').onclick=()=>work('Merging entities…',async()=>{session=await mutate('/entities/merge',{source:$('merge-source').value,target:$('merge-target').value});await update();});
$('generate-button').onclick=()=>work('Redacting and checking the selected regions…',async()=>{session=await mutate('/generate',{include_mapping:$('include-mapping').checked});await update();if(session.status==='complete'){$('download-link').click();$('download').scrollIntoView({behavior:'smooth'});}else $('verification').scrollIntoView({behavior:'smooth'});});
$('delete-session').onclick=()=>work('Deleting session files…',async()=>{await api(`/api/sessions/${session.id}`,{method:'DELETE'});for(const doc of pdfCache.values())await doc.destroy();pdfCache.clear();session=null;location.reload();});
let start, selection;
function clearSelectionDraft() {
  start=null; selection=null; $('draw-layer').replaceChildren();
}
function setInteractionMode(mode) {
  clearSelectionDraft(); clearNativeSelection(); interactionMode=mode;
  $('draw-layer').hidden=mode!=='select'&&mode!=='add';
  $('page-stage').classList.toggle('selecting-text',mode==='text');
  $('text-select-button').setAttribute('aria-pressed',String(mode==='text'));
  $('draw-button').textContent=mode==='add'?'Cancel drawing':'＋ Draw sensitive region';
  $('draw-button').setAttribute('aria-pressed',String(mode==='add'));
  $('select-regions-button').textContent=mode==='select'?'Finish selecting':'▧ Select regions';
  $('select-regions-button').setAttribute('aria-pressed',String(mode==='select'));
}
$('draw-button').onclick=()=>setInteractionMode(interactionMode==='add'?null:'add');
$('select-regions-button').onclick=()=>setInteractionMode(interactionMode==='select'?null:'select');
function point(event) {
  const box=$('draw-layer').getBoundingClientRect();
  return [Math.max(0,Math.min(box.width,event.clientX-box.left)),Math.max(0,Math.min(box.height,event.clientY-box.top))];
}
$('draw-layer').onpointerdown=event=>{
  if(!interactionMode||!event.isPrimary||event.button!==0)return;
  event.preventDefault();start={point:point(event),id:event.pointerId,additive:event.shiftKey};
  $('draw-layer').setPointerCapture(event.pointerId);
  selection=document.createElement('div');selection.className='selection';$('draw-layer').replaceChildren(selection);
};
$('draw-layer').onpointermove=event=>{
  if(!start||event.pointerId!==start.id)return;
  const p=point(event),a=start.point;
  selection.style.left=`${Math.min(p[0],a[0])}px`;selection.style.top=`${Math.min(p[1],a[1])}px`;
  selection.style.width=`${Math.abs(p[0]-a[0])}px`;selection.style.height=`${Math.abs(p[1]-a[1])}px`;
};
$('draw-layer').onpointerup=event=>{
  if(!start||event.pointerId!==start.id)return;
  const end=point(event),stage=$('draw-layer').getBoundingClientRect(),a=start.point,additive=start.additive;
  const size=session.documents.find(d=>d.id===currentDocument).pages[currentPage-1];
  const bbox=[Math.min(a[0],end[0])/stage.width*size.width,Math.min(a[1],end[1])/stage.height*size.height,
    Math.max(a[0],end[0])/stage.width*size.width,Math.max(a[1],end[1])/stage.height*size.height];
  clearSelectionDraft();
  if($('draw-layer').hasPointerCapture(event.pointerId))$('draw-layer').releasePointerCapture(event.pointerId);
  if(interactionMode==='select'){
    const items=pageDetections();
    if(!additive)for(const d of items)bulkSelection.delete(d.id);
    for(const d of items)if((d.rects?.length?d.rects:[d.bbox]).some(r=>r[0]<=bbox[2]&&r[2]>=bbox[0]&&r[1]<=bbox[3]&&r[3]>=bbox[1]))bulkSelection.add(d.id);
    syncBulkSelection();return;
  }
  setInteractionMode(null);
  if(bbox[2]-bbox[0]<3||bbox[3]-bbox[1]<3){message('Draw a slightly larger rectangle.');return;}
  region={document_id:currentDocument,page:currentPage,bbox};selected=null;
  $('edit-type').value='ADDRESS';$('edit-value').value='';$('edit-evidence').textContent='New manual region. Enter the original value, or leave it empty to try local text extraction/OCR when saving.';
  $('split-label').hidden=true;$('visual-label').hidden=false;$('visual-only').checked=false;$('edit-ignore').hidden=true;openEditDialog();
};
$('draw-layer').onpointercancel=clearSelectionDraft;
$('draw-layer').onlostpointercapture=clearSelectionDraft;


// The canvas remains a PDF.js rendering; this transparent layer uses exact
// source glyph boxes instead of OCR or a browser font's approximation of a word.
function clearNativeSelection() {
  nativeSelectionDraft=null;
  $('redact-selected-text').hidden=true;
  const selection=window.getSelection();
  if(selection?.anchorNode&&$('native-text-layer').contains(selection.anchorNode))selection.removeAllRanges();
}
function renderNativeText(info,scale) {
  const layer=$('native-text-layer');layer.replaceChildren();
  const chars=info.native_text||[];
  $('text-select-button').disabled=!chars.length;
  $('text-selection-help').textContent=chars.length?'Select text: highlight original characters, then choose Redact selected text.':'No visible native text on this page. Use Draw sensitive region for scans or OCR-only pages.';
  if(!chars.length&&interactionMode==='text')setInteractionMode(null);
  const context=document.createElement('canvas').getContext('2d'),fragment=document.createDocumentFragment();
  for(let index=0;index<chars.length;index++) {
    const char=chars[index],r=char.bbox,w=(r[2]-r[0])*scale,h=(r[3]-r[1])*scale;
    const span=document.createElement('span');span.dataset.characterId=String(index);span.textContent=char.text;
    context.font=`${h}px Arial`;
    const width=Math.max(context.measureText(char.text).width,.01);
    Object.assign(span.style,{left:`${r[0]*scale}px`,top:`${r[1]*scale}px`,fontSize:`${h}px`,lineHeight:`${h}px`,transform:`scaleX(${w/width})`});
    fragment.append(span);
  }
  layer.append(fragment);
}
$('text-select-button').onclick=()=>setInteractionMode(interactionMode==='text'?null:'text');
function readNativeSelection() {
  if(interactionMode!=='text'||editDialog.open)return;
  const selection=window.getSelection(),layer=$('native-text-layer');
  nativeSelectionDraft=null;$('redact-selected-text').hidden=true;
  if(!selection||selection.isCollapsed||!selection.rangeCount||!layer.contains(selection.anchorNode)||!layer.contains(selection.focusNode))return;
  const range=selection.getRangeAt(0),ids=[];
  for(const span of layer.children) {
    const node=span.firstChild;
    if(range.intersectsNode(node)&&!(range.startContainer===node&&range.startOffset===node.length)&&!(range.endContainer===node&&range.endOffset===0))ids.push(Number(span.dataset.characterId));
  }
  const info=session.documents.find(d=>d.id===currentDocument).pages[currentPage-1];
  let text='',lastLine=null;
  for(const id of ids) {
    const char=info.native_text[id];
    if(lastLine!==null&&lastLine!==char.line)text+='\n';
    text+=char.text;lastLine=char.line;
  }
  if(!text.trim())return;
  nativeSelectionDraft={document_id:currentDocument,page:currentPage,character_ids:ids,value:text};
  $('redact-selected-text').hidden=false;
}
let selectionFrame;
document.addEventListener('selectionchange',()=>{cancelAnimationFrame(selectionFrame);selectionFrame=requestAnimationFrame(readNativeSelection);});
$('redact-selected-text').onpointerdown=event=>event.preventDefault();
$('redact-selected-text').onclick=()=>{
  if(!nativeSelectionDraft)return;
  region={...nativeSelectionDraft};selected=null;
  $('edit-type').value='OTHER';$('edit-value').value=region.value;
  $('edit-evidence').textContent='Selected original PDF characters. Only these glyph regions will be removed, including separate fragments on different lines.';
  $('split-label').hidden=true;$('visual-label').hidden=true;$('visual-only').checked=false;$('edit-ignore').hidden=true;
  openEditDialog();
};
$('include-mapping').onchange=()=>{$('download').hidden=true;};

// Keep source-coordinate overlays aligned as the available viewport changes.
new ResizeObserver(() => {
  clearTimeout(previewResizeTimer);
  previewResizeTimer = setTimeout(() => {
    const scroller = document.querySelector('.preview-scroll');
    if (!session || $('review').hidden || !$('busy').hidden ||
        Math.abs(scroller.clientWidth - renderedPreviewWidth) < 2) return;
    work('Adjusting document view…', async () => {
      clearNativeSelection();
      clearSelectionDraft();
      const doc = session.documents.find(d => d.id === currentDocument);
      await renderPage(doc);
      drawDetections();
    });
  }, 180);
}).observe(document.querySelector('.preview-scroll'));


const shortcutsDialog = $('shortcuts-dialog');
const singleKeyToggle = $('single-key-shortcuts');
try { singleKeyToggle.checked = localStorage.getItem('ainonymify.singleKeyShortcuts') !== 'off'; } catch {}
singleKeyToggle.onchange = () => {
  try { localStorage.setItem('ainonymify.singleKeyShortcuts', singleKeyToggle.checked ? 'on' : 'off'); } catch {}
};
$('shortcuts-button').onclick = () => shortcutsDialog.showModal();
$('shortcuts-close').onclick = () => shortcutsDialog.close();
const commandLabel = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';
for (const element of document.querySelectorAll('[data-command-key]')) element.textContent = commandLabel;
const shortcutButtons = {
  upload: 'pdf-files',
  text: 'text-select-button', draw: 'draw-button', select: 'select-regions-button',
  reviewText: 'redact-selected-text', confirmBulk: 'bulk-confirm', ignoreBulk: 'bulk-ignore',
  clearBulk: 'clear-selection', previous: 'previous', next: 'next', generate: 'generate-button',
};
for (const [id, keys] of Object.entries({
  'text-select-button': ['Select text', 'T'], 'draw-button': ['Draw sensitive region', 'R'],
  'select-regions-button': ['Select regions', 'S'],
  'select-page-detections': ['Select all detections on this page', 'A'],
  'bulk-confirm': ['Confirm selected detections', 'C'], 'bulk-ignore': ['Ignore selected detections', 'I'],
  'clear-selection': ['Clear bulk selection', 'X'], 'previous': ['Previous page', '['], 'next': ['Next page', ']'],
  'shortcuts-button': ['Keyboard shortcuts', '?'],
})) {
  $(id).title = `${keys[0]} (${keys[1]})`;
  $(id).setAttribute('aria-keyshortcuts', keys[1]);
}
$('pdf-files').title = `Upload PDFs (${commandLabel}+U)`;
$('pdf-files').setAttribute('aria-keyshortcuts', 'Control+U Meta+U');
$('redact-selected-text').title = 'Redact selected text (Enter / E)';
$('redact-selected-text').setAttribute('aria-keyshortcuts', 'Enter E');
$('generate-button').title = `Generate and download (${commandLabel}+Enter)`;
$('generate-button').setAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter');
$('confirm-region').title = `Confirm region (${commandLabel}+Enter)`;
$('confirm-region').setAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter');

function isTyping(target) {
  if (!(target instanceof Element)) return false;
  return target.isContentEditable || !!target.closest('textarea, select, [role="textbox"], input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"])');
}
document.addEventListener('keydown', event => {
  if (['Enter', 'NumpadEnter'].includes(event.code) && !event.ctrlKey && !event.metaKey && !event.altKey) readNativeSelection();
  const action = resolveShortcut(event, {
    busy: !$('busy').hidden, reviewOpen: !!session && !$('review').hidden,
    dialog: editDialog.open ? 'review' : document.querySelector('dialog[open]') ? 'other' : null,
    editing: isTyping(event.target), singleKeys: singleKeyToggle.checked,
    uploadOpen: !$('landing').hidden, hasSelectedText: !!nativeSelectionDraft,
    controlFocused: event.target instanceof Element && !!event.target.closest('button, a, input, select, textarea, summary, [role="button"]'),
  });
  if (!action) return;
  if (action === 'reviewText') readNativeSelection();
  if (action === 'confirmRegion') {
    event.preventDefault();
    $('edit-form').requestSubmit();
  } else if (shortcutButtons[action]) {
    const button = $(shortcutButtons[action]);
    if (button.disabled || button.hidden) return;
    event.preventDefault();
    button.click();
  } else if (action === 'help') {
    event.preventDefault();
    $('shortcuts-button').click();
  } else if (action === 'selectPage') {
    event.preventDefault();
    selectPageDetections(true);
  } else if (action === 'cancel') {
    event.preventDefault();
    setInteractionMode(null);
    bulkSelection.clear(); syncBulkSelection();
    $('message').hidden = true;
  }
});
