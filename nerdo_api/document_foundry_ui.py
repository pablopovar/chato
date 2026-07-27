from __future__ import annotations

FOUNDRY_PANEL = r'''
<section id="foundryPanel" class="panel hidden">
  <div class="foundry-heading">
    <div>
      <h2>Nerdo's Document Foundry</h2>
      <p>Inspect the domain data as Markdown, edit the active corpus, or convert and add new source documents.</p>
    </div>
    <div id="foundryService" class="foundry-service">Checking conversion service…</div>
  </div>

  <form id="foundryUpload" class="foundry-upload">
    <label>Upload source documents
      <input id="foundryFiles" type="file" multiple accept=".pdf,.docx,.html,.htm,.md,.markdown,.txt">
    </label>
    <button id="foundryImport" class="primary" type="submit">Process and add to domain</button>
  </form>
  <p class="help">PDF, DOCX, HTML, Markdown, and plain text are processed by the standalone Document Foundry. The resulting Markdown becomes part of this domain immediately.</p>
  <p id="foundryNotice" class="notice"></p>

  <div class="foundry-layout">
    <aside class="foundry-documents">
      <div class="foundry-list-head">
        <strong>Domain Markdown</strong>
        <button id="foundryRefresh" class="secondary" type="button">Refresh</button>
      </div>
      <div id="foundryDocumentList" class="foundry-document-list">
        <div class="empty">Open this tab to load documents.</div>
      </div>
    </aside>

    <section class="foundry-editor">
      <div class="foundry-editor-head">
        <div>
          <strong id="foundryDocumentTitle">Select a document</strong>
          <div id="foundryDocumentMeta" class="help"></div>
        </div>
        <a id="foundryDownload" class="foundry-download hidden" href="#">Download</a>
      </div>
      <textarea id="foundryMarkdown" class="foundry-markdown" spellcheck="false" disabled placeholder="Select a Markdown document to inspect its data."></textarea>
      <div class="actions">
        <button id="foundrySave" class="primary" type="button" disabled>Save Markdown</button>
        <button id="foundryReload" class="secondary" type="button" disabled>Discard changes</button>
      </div>
    </section>
  </div>
</section>
'''


FOUNDRY_CSS = r'''
.foundry-heading{display:flex;justify-content:space-between;gap:18px;align-items:start;margin-bottom:16px}
.foundry-heading h2{margin:0 0 6px}.foundry-heading p{margin:0;color:var(--muted)}
.foundry-service{padding:7px 10px;border-radius:999px;background:var(--soft);font-size:12px;font-weight:800;white-space:nowrap}
.foundry-service.online{background:#e7f5eb;color:#195a2c}.foundry-service.offline{background:#f8e5e5;color:#8d1c1c}
.foundry-upload{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:end;padding:16px;border:1px solid var(--line);border-radius:10px;background:var(--soft)}
.foundry-upload label{margin:0}.foundry-layout{display:grid;grid-template-columns:minmax(260px,360px) minmax(0,1fr);gap:16px;margin-top:18px}
.foundry-documents,.foundry-editor{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}
.foundry-list-head,.foundry-editor-head{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:12px 14px;border-bottom:1px solid var(--line)}
.foundry-document-list{max-height:680px;overflow:auto}.foundry-document{display:block;width:100%;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:12px 14px;text-align:left;background:#fff;color:var(--navy)}
.foundry-document:hover,.foundry-document.active{background:var(--soft)}.foundry-document:last-child{border-bottom:0}
.foundry-document strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.foundry-document span{display:block;margin-top:4px;color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.foundry-markdown{display:block;width:100%;min-height:560px;border:0;border-radius:0;padding:16px;resize:vertical;font:13px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}
.foundry-editor .actions{padding:0 14px 14px}.foundry-download{color:var(--navy);font-weight:800;text-decoration:none}
@media(max-width:900px){.foundry-layout{grid-template-columns:1fr}.foundry-upload{grid-template-columns:1fr}.foundry-heading{display:block}.foundry-service{display:inline-block;margin-top:10px}.foundry-document-list{max-height:320px}}
'''


FOUNDRY_JS = r'''
let foundryLoaded=false;
let foundryDocuments=[];
let foundryCurrent=null;

const foundryNotice=$('#foundryNotice');
const foundryEndpoint=`/dashboard/api/domains/${encodeURIComponent(domain)}/foundry`;

function foundryMessage(text='',error=false){
  foundryNotice.textContent=text;
  foundryNotice.style.color=error?'#8d1c1c':'';
}
function foundryFormatBytes(value){
  const units=['B','KB','MB','GB'];let n=Number(value||0),i=0;
  while(n>=1024&&i<units.length-1){n/=1024;i++}
  return `${n.toFixed(i?1:0)} ${units[i]}`;
}
function renderFoundryDocuments(){
  const list=$('#foundryDocumentList');
  if(!foundryDocuments.length){
    list.innerHTML='<div class="empty">No Markdown documents were found for this domain.</div>';
    return;
  }
  list.innerHTML=foundryDocuments.map(doc=>`<button type="button" class="foundry-document ${foundryCurrent?.path===doc.path?'active':''}" data-foundry-path="${esc(doc.path)}"><strong>${esc(doc.filename)}</strong><span>${esc(doc.category)} · ${esc(doc.path)}</span></button>`).join('');
}
async function loadFoundry(selectPath=null){
  foundryMessage('Loading domain documents…');
  const r=await fetch(foundryEndpoint,{credentials:'same-origin'});
  const p=await r.json();
  if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);
  foundryLoaded=true;
  foundryDocuments=p.documents||[];
  const status=$('#foundryService');
  status.textContent=p.foundry?.available?'Document Foundry online':'Document Foundry offline';
  status.className=`foundry-service ${p.foundry?.available?'online':'offline'}`;
  if(!p.foundry?.available&&p.foundry?.error)status.title=p.foundry.error;
  renderFoundryDocuments();
  foundryMessage(`${p.document_count||0} Markdown document${p.document_count===1?'':'s'} in the active domain corpus.`);
  if(selectPath)await openFoundryDocument(selectPath);
}
async function openFoundryDocument(path){
  foundryMessage('Loading Markdown…');
  const url=`${foundryEndpoint}/document?path=${encodeURIComponent(path)}`;
  const r=await fetch(url,{credentials:'same-origin'});
  const p=await r.json();
  if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);
  foundryCurrent=p;
  $('#foundryDocumentTitle').textContent=p.path;
  $('#foundryDocumentMeta').textContent=`${p.category} · ${foundryFormatBytes(p.bytes)} · updated ${new Date(p.updated_at).toLocaleString()}${p.source_url?` · ${p.source_url}`:''}`;
  $('#foundryMarkdown').value=p.content;
  $('#foundryMarkdown').disabled=false;
  $('#foundrySave').disabled=false;
  $('#foundryReload').disabled=false;
  const download=$('#foundryDownload');
  download.href=`${url}&download=true`;
  download.classList.remove('hidden');
  renderFoundryDocuments();
  foundryMessage('');
}
$('#foundryDocumentList').addEventListener('click',e=>{
  const button=e.target.closest('[data-foundry-path]');
  if(!button)return;
  openFoundryDocument(button.dataset.foundryPath).catch(error=>foundryMessage(error.message,true));
});
$('#foundryRefresh').addEventListener('click',()=>loadFoundry(foundryCurrent?.path||null).catch(error=>foundryMessage(error.message,true)));
$('#foundryReload').addEventListener('click',()=>{
  if(!foundryCurrent)return;
  openFoundryDocument(foundryCurrent.path).catch(error=>foundryMessage(error.message,true));
});
$('#foundrySave').addEventListener('click',async()=>{
  if(!foundryCurrent)return;
  $('#foundrySave').disabled=true;
  foundryMessage('Saving Markdown…');
  try{
    const r=await fetch(`${foundryEndpoint}/document`,{
      method:'PUT',
      credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        path:foundryCurrent.path,
        content:$('#foundryMarkdown').value,
        expected_sha256:foundryCurrent.sha256
      })
    });
    const p=await r.json();
    if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);
    foundryMessage(`Saved. Backup: ${p.backup}`);
    await loadFoundry(p.document.path);
  }catch(error){foundryMessage(error.message,true)}
  finally{$('#foundrySave').disabled=false}
});
$('#foundryUpload').addEventListener('submit',async e=>{
  e.preventDefault();
  const files=Array.from($('#foundryFiles').files||[]);
  if(!files.length)return;
  $('#foundryImport').disabled=true;
  foundryMessage(`Processing ${files.length} source document${files.length===1?'':'s'}…`);
  try{
    const body=new FormData();
    files.forEach(file=>body.append('files',file));
    const r=await fetch(`${foundryEndpoint}/import`,{
      method:'POST',
      credentials:'same-origin',
      body
    });
    const p=await r.json();
    if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);
    $('#foundryFiles').value='';
    const suffix=p.failed_count?` ${p.failed_count} failed.`:'';
    foundryMessage(`Imported ${p.imported_count} Markdown document${p.imported_count===1?'':'s'}.${suffix}`,Boolean(p.failed_count));
    await loadFoundry(p.documents?.[0]?.path||null);
  }catch(error){foundryMessage(error.message,true)}
  finally{$('#foundryImport').disabled=false}
});
'''


def enhance_dashboard_page(source: str) -> str:
    if "foundryPanel" in source:
        return source

    tab_needle = '<button data-tab="chat">Test chat</button>'
    history_needle = '<section id="historyPanel"'
    toggle_needle = "$('#chatPanel').classList.toggle('hidden',tab!=='chat');"
    history_load_needle = "if(tab==='history'&&!historyLoaded)loadHistory()"
    script_end = "</script></body></html>"

    required = (
        tab_needle,
        history_needle,
        toggle_needle,
        history_load_needle,
        script_end,
        "</style></head>",
    )
    if any(needle not in source for needle in required):
        raise RuntimeError(
            "Could not install Nerdo's Document Foundry into the domain dashboard."
        )

    source = source.replace(
        "</style></head>",
        FOUNDRY_CSS + "</style></head>",
        1,
    )
    source = source.replace(
        tab_needle,
        tab_needle + '<button data-tab="foundry">Nerdo\'s Document Foundry</button>',
        1,
    )
    source = source.replace(
        history_needle,
        FOUNDRY_PANEL + history_needle,
        1,
    )
    source = source.replace(
        toggle_needle,
        toggle_needle
        + "$('#foundryPanel').classList.toggle('hidden',tab!=='foundry');",
        1,
    )
    source = source.replace(
        history_load_needle,
        "if(tab==='foundry'&&!foundryLoaded)loadFoundry().catch(e=>foundryMessage(e.message,true));"
        + history_load_needle,
        1,
    )
    source = source.replace(
        script_end,
        FOUNDRY_JS + script_end,
        1,
    )
    return source
