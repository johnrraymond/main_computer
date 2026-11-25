/* Document application adapter: no canvas or competing document state writer. */
(function(global){
  "use strict";
  const nodeId='surface-node:document.draft';
  const base='/api/applications/docs/';
  const rules=Object.freeze({
    'list-documents':['document-reader','list','listDocuments','external-read',[]],
    'open-document':['document-reader','open','openDocument','external-read',['path']],
    'open-scratchpad':['document-reader','scratchpad','openScratchpad','external-read',[]],
    'save-candidate-draft':['draft-writer','save','saveDraft','source-write',['html']],
    'lock-selection':['document-ai','lock','lockSelection','external-read',[]],
    'request-ai-proposal':['document-ai','propose','proposeReplacement','external-read',['html','instruction']],
    'apply-ai-proposal':['document-apply','apply','applyReplacement','consequential',[]],
    'export-pdf':['document-export','pdf','exportPdf','external-read',['html']]
  });
  function create(getApp, {fetcher=global.fetch.bind(global),confirm=global.confirm.bind(global)}={}){
    let active=null, saved=null, locked=null, proposal=null, busy=false, known=new Map(), closed=false;
    function surface(){if(closed)throw Error('DOCUMENT_APPLICATION_CLOSED');const s=getApp()?.getDocumentSurface(nodeId);if(!s)throw Error('DOCUMENT_SURFACE_UNAVAILABLE');return s;}
    function authorize(req){const name=req?.intentId?.replace(/^intent:/,'');const r=rules[name];return !closed && !!r && req.appId==='document' && req.intentId==='intent:'+name && req.effectId==='effect:'+name+'.request' && req.capabilityId==='capability:'+r[0] && req.operation===r[1] && req.runtimeMethod===r[2] && req.risk===r[3] && Object.keys(req.arguments||{}).sort().join('|')===r[4].slice().sort().join('|') && Object.values(req.arguments||{}).every(v=>typeof v==='string');}
    async function post(route,body){const response=await fetcher(base+route,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let data;try{data=await response.json();}catch(_){throw Error('HTTP '+response.status);}if(!response.ok||data.ok!==true)throw Error(data.error||'HTTP '+response.status);return data;}
    function escape(text){return String(text??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
    function plain(html){const t=document.createElement('template');t.innerHTML=html;return Array.from(t.content.childNodes,n=>n.textContent).join('\n');}
    // Match the incumbent Pretty Docs markdown/text renderer (HTML source is text, not executable).
    function render(content,kind){if(kind!=='markdown')return '<pre>'+escape(content)+'</pre>';const blocks=[];let bullets=[];const flush=()=>{if(bullets.length){blocks.push('<ul>'+bullets.map(x=>'<li>'+escape(x)+'</li>').join('')+'</ul>');bullets=[];}};for(const line of String(content||'').replace(/\r\n/g,'\n').split('\n')){const h=line.match(/^(#{1,3})\s+(.+)$/),b=line.match(/^\s*[-*]\s+(.+)$/);if(h){flush();blocks.push('<h'+h[1].length+'>'+escape(h[2])+'</h'+h[1].length+'>');}else if(b)bullets.push(b[1]);else{flush();if(line.trim())blocks.push('<p>'+escape(line)+'</p>');}}flush();return blocks.join('')||'<p></p>';}
    const key=s=>JSON.stringify([s.html,s.layout]);
    function dirty(s){return saved===null?plain(s.html).trim()!=='':key(s)!==saved;}
    function ensureCurrent(html){const s=surface().getState();if(s.html!==html)throw Error('DOCUMENT_DRAFT_CHANGED');return s;}
    async function open(path){
      const s=surface(),before=s.getState();
      if(dirty(before) && (active || !confirm('Discard the unbound initial canvas edits and open a stored document?')))throw Error('Unsaved changes — save the current draft before opening another document.');
      if(path && !known.has(path))throw Error('Choose a listed Pretty Docs document first');
      const disk=path?await post('read',{path}):null;
      if(disk && disk.path!==path)throw Error('DOCUMENT_PATH_MISMATCH');
      const candidate=await post('candidate-draft/read',{path});
      if(candidate.path!==path||typeof candidate.token!=='string')throw Error('CANDIDATE_PATH_MISMATCH');
      const record=candidate.exists?candidate:null;
      const html=record?String(record.html??''):disk?render(disk.content,disk.kind):'<p></p>';
      if(closed||key(s.getState())!==key(before)||s.getState().revision!==before.revision)throw Error('DOCUMENT_CHANGED_DURING_OPEN');
      if(path && getApp().readState('state:selected-path')!==path)throw Error('DOCUMENT_SELECTION_CHANGED');
      const next=s.loadDocument({expectedRevision:before.revision,html,layout:record?.layout||McelDocumentEngine.defaultDocumentLayoutState()});
      active={path,title:disk?.title||'Scratchpad',kind:disk?.kind||'draft',token:candidate.token,revision:record?.revision|| (disk?{content_hash:disk.content_hash,mtime:disk.mtime}:null)};
      saved=key(next);locked=null;proposal=null;
      return active.title+' — '+(candidate.exists?'draft':disk?'original source (read-only)':'scratchpad');
    }
    async function invoke(req){
      if(!authorize(req))throw Error('DOCUMENT_CAPABILITY_REFUSED');
      if(busy)throw Error('DOCUMENT_OPERATION_IN_PROGRESS');busy=true;
      try{
        const a=req.arguments;
        switch(req.runtimeMethod){
          case 'listDocuments':{const data=await post('files',{});const rows=(data.documents||[]).map(x=>({path:String(x.path),title:String(x.title||x.path),kind:String(x.kind||'text')}));known=new Map(rows.map(x=>[x.path,x]));return rows;}
          case 'openDocument':return await open(a.path);
          case 'openScratchpad':return await open('');
          case 'saveDraft':{
            const s=surface(),state=ensureCurrent(a.html);s.validateImport(state.html);
            if(!active)throw Error('Open scratchpad or a listed document before saving');
            if(!confirm('Save draft for '+active.title+'? The original file will not be modified.'))throw Error('Save cancelled');
            const data=await post('candidate-draft/write',{path:active.path,expected_token:active.token,html:state.html,layout:state.layout,revision:active.revision});
            if(data.path!==active.path||!data.token)throw Error('CANDIDATE_SAVE_RECEIPT_INVALID');active.token=data.token;saved=key(state);
            return key(s.getState())===saved?'Draft saved: '+active.title:'Draft saved; newer edits remain unsaved';
          }
          case 'lockSelection':{const s=surface();proposal=null;locked=s.getSelection();if(!locked||!locked.text)throw Error('Select text within a single supported document block first');return 'Locked: '+locked.text;}
          case 'proposeReplacement':{
            const s=surface(),state=ensureCurrent(a.html);proposal=null;
            if(!locked||locked.revision!==state.revision)throw Error('Selection stale — lock it again');
            if(!a.instruction.trim())throw Error('Instruction required');const anchor={...locked};
            const data=await post('ai',{instruction:a.instruction,preferred_operation:'replace_selection',document:{path:active?.path||'',kind:active?.kind||'draft',html:state.html,text:plain(state.html)},anchor:{range:{selected_text:anchor.text}}});
            if(s.getState().revision!==anchor.revision)throw Error('Document changed while AI was responding');
            if(data.suggestion?.operation!=='replace_selection'||typeof data.suggestion.replacement_text!=='string')return 'No supported selection replacement proposed: '+String(data.content||'');
            proposal={anchor,text:data.suggestion.replacement_text};return 'Original: '+anchor.text+'\n\nProposed: '+proposal.text+'\n\n'+String(data.suggestion.rationale||'');
          }
          case 'applyReplacement':{
            const s=surface();if(!proposal||proposal.anchor.revision!==s.getState().revision)throw Error('No current proposal — lock and request again');
            if(!confirm('Apply the displayed replacement to the locked selection?'))throw Error('Apply cancelled');
            s.mutate({operation:'replace-selection',expectedRevision:proposal.anchor.revision,anchor:proposal.anchor,text:proposal.text});proposal=null;locked=null;return 'Replacement applied; save the draft explicitly.';
          }
          case 'exportPdf':{
            const s=surface(),state=ensureCurrent(a.html);if(state.layout.view.mode!=='paged')throw Error('Switch to paged mode before PDF export');
            const pages=s.getExportPages();if(s.getState().revision!==state.revision||pages.length>100)throw Error('Export snapshot changed or exceeds 100 pages');
            const response=await fetcher(base+'export/pdf',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:active?.title||'Scratchpad',layoutState:state.layout,contentHtml:state.html,pages})});
            if(!response.ok||!String(response.headers.get('Content-Type')).includes('application/pdf'))throw Error('PDF export failed: HTTP '+response.status);
            const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='document.pdf';link.click();setTimeout(()=>URL.revokeObjectURL(url),1500);return 'PDF exported (content-page renderer)';
          }
          default:throw Error('DOCUMENT_CAPABILITY_REFUSED');
        }
      }finally{busy=false;}
    }
    return Object.freeze({authorize,invoke,isDirty:()=>!closed&&dirty(surface().getState()),closeDocument(){closed=true;locked=null;proposal=null;}});
  }
  global.McelDocumentCandidatePort=Object.freeze({create});
})(window);
