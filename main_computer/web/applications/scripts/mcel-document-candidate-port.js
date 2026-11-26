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
    'new-document':['draft-writer','create','createDocument','source-write',['context']],
    'lock-document':['document-ai','lock-document','lockDocument','external-read',[]],
    'lock-selection':['document-ai','lock','lockSelection','external-read',[]],
    'request-ai-proposal':['document-ai','propose','proposeReplacement','external-read',['html','instruction']],
    'apply-ai-proposal':['document-apply','apply','applyReplacement','consequential',[]],
    'export-pdf':['document-export','pdf','exportPdf','external-read',['html']]
  });
  function create(getApp, {fetcher=global.fetch.bind(global),confirm=global.confirm.bind(global)}={}){
    let active=null, saved=null, locked=null, proposal=null, busy=false, known=new Map(), closed=false;
    const fields=['name','date','type','author','description'];
    const today=()=>{const d=new Date();return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');};
    const defaults=()=>({name:'Untitled Document',date:today(),type:'',author:'',description:''});
    let metadata=defaults(), generation=0, creationStatus='', creationContext=null;
    const listeners=new Set();
    function publish(){for(const fn of listeners){try{fn();}catch(e){console.error(e);}}}
    function record(value, allowed){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!allowed.includes(k)))throw Error('INVALID_DOCUMENT_STATE_FIELDS');return value;}
    function checkedMetadata(value){record(value,fields);for(const [k,v] of Object.entries(value)){if(typeof v!=='string'||v.length>120000)throw Error('INVALID_METADATA_'+k);if(k==='date'&&v&&(!/^\d{4}-\d{2}-\d{2}$/.test(v)||!Number.isFinite(Date.parse(v))||new Date(v).toISOString().slice(0,10)!==v))throw Error('INVALID_DOCUMENT_DATE');}return value;}
    function checkedLayout(change, current){
      record(change,['layout','view']);const l=record(change.layout??{},['mode','preset','custom','margins']),v=record(change.view??{},['mode','zoom','showPageBreaks']);
      const next={layout:{...current.layout,...l,margins:{...current.layout.margins,...record(l.margins??{},['top','right','bottom','left'])}},view:{...current.view,...v}};
      if(l.mode==='custom'&&l.preset===undefined)next.layout.preset=null;
      if(l.mode==='preset'&&l.custom===undefined)next.layout.custom=null;
      const n=next.layout,w=next.view;
      if(!['preset','custom'].includes(n.mode)||!['paged','endless'].includes(w.mode)||typeof w.zoom!=='number'||!Number.isFinite(w.zoom)||w.zoom<.5||w.zoom>2||typeof w.showPageBreaks!=='boolean')throw Error('INVALID_DOCUMENT_LAYOUT');
      if(n.mode==='preset'){if(!['letter','a4','legal','screen'].includes(n.preset)||n.custom!==null)throw Error('INVALID_PAGE_PRESET');}
      else{record(n.custom,['name','widthPx','heightPx']);if(n.preset!==null||!Number.isInteger(n.custom.widthPx)||!Number.isInteger(n.custom.heightPx))throw Error('INVALID_CUSTOM_PAGE');}
      for(const val of Object.values(n.margins))if(!Number.isInteger(val)||val<0)throw Error('INVALID_PAGE_MARGIN');
      const normalized=McelDocumentEngine.normalizeDocumentLayoutState(next);
      if(n.mode==='custom'&&(normalized.layout.custom.widthPx!==n.custom.widthPx||normalized.layout.custom.heightPx!==n.custom.heightPx)||Object.keys(n.margins).some(k=>normalized.layout.margins[k]!==n.margins[k]))throw Error('DOCUMENT_LAYOUT_OUT_OF_RANGE');
      return normalized;
    }
    function surface(){if(closed)throw Error('DOCUMENT_APPLICATION_CLOSED');const s=getApp()?.getDocumentSurface(nodeId);if(!s)throw Error('DOCUMENT_SURFACE_UNAVAILABLE');return s;}
    function authorize(req){const name=req?.intentId?.replace(/^intent:/,'');const r=rules[name];return !closed && !!r && req.appId==='document' && req.intentId==='intent:'+name && req.effectId==='effect:'+name+'.request' && req.capabilityId==='capability:'+r[0] && req.operation===r[1] && req.runtimeMethod===r[2] && req.risk===r[3] && Object.keys(req.arguments||{}).sort().join('|')===r[4].slice().sort().join('|') && Object.values(req.arguments||{}).every(v=>typeof v==='string');}
    async function post(route,body){const response=await fetcher(base+route,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let data;try{data=await response.json();}catch(_){throw Error('HTTP '+response.status);}if(!response.ok||data.ok!==true)throw Error(data.error||'HTTP '+response.status);return data;}
    function escape(text){return String(text??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
    function plain(html){const t=document.createElement('template');t.innerHTML=html;return Array.from(t.content.childNodes,n=>n.textContent).join('\n');}
    // Match the incumbent Pretty Docs markdown/text renderer (HTML source is text, not executable).
    function render(content,kind){if(kind!=='markdown')return '<pre>'+escape(content)+'</pre>';const blocks=[];let bullets=[];const flush=()=>{if(bullets.length){blocks.push('<ul>'+bullets.map(x=>'<li>'+escape(x)+'</li>').join('')+'</ul>');bullets=[];}};for(const line of String(content||'').replace(/\r\n/g,'\n').split('\n')){const h=line.match(/^(#{1,3})\s+(.+)$/),b=line.match(/^\s*[-*]\s+(.+)$/);if(h){flush();blocks.push('<h'+h[1].length+'>'+escape(h[2])+'</h'+h[1].length+'>');}else if(b)bullets.push(b[1]);else{flush();if(line.trim())blocks.push('<p>'+escape(line)+'</p>');}}flush();return blocks.join('')||'<p></p>';}
    const key=s=>JSON.stringify([s.html,s.layout,metadata]);
    const initialMetadata=JSON.stringify(metadata);
    const anchorFor=s=>({revision:s.revision,key:key(s),path:active?.path??null});
    function current(anchor){const s=surface().getState();return anchor.revision===s.revision&&anchor.key===key(s)&&anchor.path===(active?.path??null);}
    function invalidate(){generation++;locked=null;proposal=null;creationStatus='';creationContext=null;publish();}
    function makeProposal(data,anchor){
      const suggestion=data.suggestion;
      if(suggestion?.operation!=='transform_document')throw Error('AI did not return a document-state transformation');
      const change=record(suggestion.changes,['html','metadata','layout']);if(!Object.keys(change).length)throw Error('EMPTY_DOCUMENT_TRANSFORMATION');
      const s=surface(),state=s.getState();
      if('html' in change&&(typeof change.html!=='string'||change.html.length>5*1024*1024))throw Error('INVALID_DOCUMENT_HTML');
      const html='html' in change?s.validateImport(change.html):state.html;
      const nextMeta={...metadata,...checkedMetadata(Object.hasOwn(change,'metadata')?change.metadata:{})};
      const layout='layout' in change?checkedLayout(change.layout,state.layout):state.layout;
      return {anchor,html,metadata:nextMeta,layout,summary:JSON.stringify(change,null,2),rationale:String(suggestion.rationale||'')};
    }
    async function requestState(instruction,state,anchor,context=null){
      const data=await post('ai',{instruction,preferred_operation:'transform_document',document:{path:active?.path||'',kind:active?.kind||'draft',html:state.html,text:plain(state.html),metadata:{...metadata},layout:state.layout,creation:context}});
      if(!current(anchor))throw Error('Document changed while AI was responding');
      return makeProposal(data,anchor);
    }
    function dirty(s){return saved===null?(plain(s.html).trim()!==''||JSON.stringify(metadata)!==initialMetadata||JSON.stringify(s.layout)!==JSON.stringify(McelDocumentEngine.defaultDocumentLayoutState())):key(s)!==saved;}
    function ensureCurrent(html){const s=surface().getState();if(s.html!==html)throw Error('DOCUMENT_DRAFT_CHANGED');return s;}
    async function open(path){
      const s=surface(),before=s.getState();
      if(dirty(before) && (active || !confirm('Discard the unbound initial canvas edits and open a stored document?')))throw Error('Unsaved changes — save the current draft before opening another document.');
      if(path && !known.has(path))throw Error('Choose a listed Pretty Docs document first');
      const disk=path&&!path.startsWith('new:')?await post('read',{path}):null;
      if(disk && disk.path!==path)throw Error('DOCUMENT_PATH_MISMATCH');
      const candidate=await post('candidate-draft/read',{path});
      if(candidate.path!==path||typeof candidate.token!=='string')throw Error('CANDIDATE_PATH_MISMATCH');
      const record=candidate.exists?candidate:null;
      const html=record?String(record.html??''):disk?render(disk.content,disk.kind):'<p></p>';
      if(closed||key(s.getState())!==key(before)||s.getState().revision!==before.revision)throw Error('DOCUMENT_CHANGED_DURING_OPEN');
      if(path && getApp().readState('state:selected-path')!==path)throw Error('DOCUMENT_SELECTION_CHANGED');
      if(path.startsWith('new:')&&!record)throw Error('DOCUMENT_NOT_FOUND');
      const nextMeta={...defaults(),name:disk?.title||(path?'Untitled Document':'Scratchpad'),...checkedMetadata(record?.metadata||{})};
      const next=s.loadDocument({expectedRevision:before.revision,html,layout:record?.layout||McelDocumentEngine.defaultDocumentLayoutState()});
      metadata=nextMeta;
      active={path,title:metadata.name,kind:disk?.kind||'draft',token:candidate.token,revision:record?.revision|| (disk?{content_hash:disk.content_hash,mtime:disk.mtime}:null)};
      saved=key(next);invalidate();
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
          case 'createDocument':{
            const s=surface(),before=s.getState();
            if(dirty(before)&&active)throw Error('Save the current document before creating another');
            if(dirty(before)&&!active&&!confirm('Create a new document and discard the unbound canvas?'))throw Error('Creation cancelled');
            const ctx=JSON.parse(a.context), baseline=checkedMetadata(ctx.defaults), supplied=checkedMetadata(ctx.values);
            if(fields.some(k=>!(k in baseline)||!(k in supplied))||!Array.isArray(ctx.explicit)||ctx.explicit.some(k=>!fields.includes(k))||typeof ctx.prompt!=='string'||ctx.prompt.length>120000||(ctx.sourceHtml!==null&&(typeof ctx.sourceHtml!=='string'||ctx.sourceHtml.length>120000)))throw Error('INVALID_CREATION_CONTEXT');
            const path='new:'+Array.from(global.crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
            const layout=McelDocumentEngine.defaultDocumentLayoutState(),html='<p></p>';
            const data=await post('candidate-draft/write',{path,expected_token:'',html,layout,metadata:supplied});
            if(data.path!==path||!data.token)throw Error('NEW_DOCUMENT_RECEIPT_INVALID');
            if(s.getState().revision!==before.revision||key(s.getState())!==key(before))throw Error('Document changed during creation; new file is available in Library');
            const next=s.loadDocument({expectedRevision:before.revision,html,layout});
            active={path,title:supplied.name||'Untitled Document',kind:'draft',token:data.token,revision:null};metadata={...supplied};saved=key(next);invalidate();
            creationContext={defaults:baseline,values:supplied,explicit:ctx.explicit,differences:Object.fromEntries(fields.filter(k=>baseline[k]!==supplied[k]).map(k=>[k,{default:baseline[k],user:supplied[k]}])),prompt:ctx.prompt,defaultHtml:html,defaultLayout:layout,sourceHtml:ctx.sourceHtml};
            if(ctx.explicit.length||ctx.prompt.trim()||ctx.sourceHtml){
              const serial=++generation, anchor=anchorFor(next),context=creationContext;creationStatus='AI creation proposal queued…';publish();
              // Deliberately independent of the operation gate: creation is durable before inference starts.
              void requestState('Create the intended document from the supplied creation context. Infer unspecified properties.',next,anchor,context).then(p=>{
                if(serial!==generation||closed)return;proposal=p;creationStatus='AI creation proposal ready:\n'+p.summary+'\n'+p.rationale;publish();
              }).catch(e=>{if(serial!==generation||closed)return;creationStatus='AI creation proposal unavailable: '+String(e.message||e);publish();});
            }
            return active.title+' — new document';
          }
          case 'saveDraft':{
            const s=surface(),state=ensureCurrent(a.html);s.validateImport(state.html);
            if(!active)throw Error('Open scratchpad or a listed document before saving');
            if(!confirm('Save draft for '+metadata.name+'? The original file will not be modified.'))throw Error('Save cancelled');
            const savedKey=key(state);const data=await post('candidate-draft/write',{path:active.path,expected_token:active.token,html:state.html,layout:state.layout,metadata,revision:active.revision});
            if(data.path!==active.path||!data.token)throw Error('CANDIDATE_SAVE_RECEIPT_INVALID');active.token=data.token;active.title=metadata.name;saved=savedKey;
            return key(s.getState())===saved?'Draft saved: '+active.title:'Draft saved; newer edits remain unsaved';
          }
          case 'lockDocument':{invalidate();locked={...anchorFor(surface().getState()),whole:true};return 'Locked: entire document (including metadata and layout)';}
          case 'lockSelection':{const s=surface();invalidate();locked=s.getSelection();if(!locked||!locked.text)throw Error('Select text within a single supported document block first');return 'Locked: '+locked.text;}
          case 'proposeReplacement':{
            const s=surface(),state=ensureCurrent(a.html);proposal=null;
            if(!locked||locked.revision!==state.revision)throw Error('Target stale — lock it again');
            if(!a.instruction.trim())throw Error('Instruction required');const anchor={...locked};
            if(anchor.whole){if(!current(anchor))throw Error('Document state stale — lock it again');proposal=await requestState(a.instruction,state,anchor,creationContext);return 'Proposed document changes:\n'+proposal.summary+'\n'+proposal.rationale;}
            const data=await post('ai',{instruction:a.instruction,preferred_operation:'replace_selection',document:{path:active?.path||'',kind:active?.kind||'draft',html:state.html,text:plain(state.html)},anchor:{range:{selected_text:anchor.text}}});
            if(s.getState().revision!==anchor.revision)throw Error('Document changed while AI was responding');
            if(data.suggestion?.operation!=='replace_selection'||typeof data.suggestion.replacement_text!=='string')return 'No supported selection replacement proposed: '+String(data.content||'');
            proposal={anchor,text:data.suggestion.replacement_text};return 'Original: '+anchor.text+'\n\nProposed: '+proposal.text+'\n\n'+String(data.suggestion.rationale||'');
          }
          case 'applyReplacement':{
            const s=surface();if(!proposal||proposal.anchor.revision!==s.getState().revision)throw Error('No current proposal — lock and request again');
            if('html' in proposal){
              if(!current(proposal.anchor))throw Error('Document state changed — request a new proposal');
              if(!confirm('Apply the displayed document-state changes?'))throw Error('Apply cancelled');
              const p=proposal;s.validateImport(p.html);
              if(!current(p.anchor))throw Error('Document state changed — request a new proposal');
              if(p.html!==s.getState().html)s.loadDocument({expectedRevision:p.anchor.revision,html:p.html,layout:p.layout});
              else s.setLayout(p.layout);
              metadata={...p.metadata};invalidate();return 'Document state applied; save explicitly.';
            }
            if(!confirm('Apply the displayed replacement to the locked selection?'))throw Error('Apply cancelled');
            s.mutate({operation:'replace-selection',expectedRevision:proposal.anchor.revision,anchor:proposal.anchor,text:proposal.text});proposal=null;locked=null;return 'Replacement applied; save the draft explicitly.';
          }
          case 'exportPdf':{
            const s=surface(),state=ensureCurrent(a.html);if(state.layout.view.mode!=='paged')throw Error('Switch to paged mode before PDF export');
            const pages=s.getExportPages();if(s.getState().revision!==state.revision||pages.length>100)throw Error('Export snapshot changed or exceeds 100 pages');
            const response=await fetcher(base+'export/pdf',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:metadata.name||'Untitled Document',layoutState:state.layout,contentHtml:state.html,pages})});
            if(!response.ok||!String(response.headers.get('Content-Type')).includes('application/pdf'))throw Error('PDF export failed: HTTP '+response.status);
            const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='document.pdf';link.click();setTimeout(()=>URL.revokeObjectURL(url),1500);return 'PDF exported (content-page renderer)';
          }
          default:throw Error('DOCUMENT_CAPABILITY_REFUSED');
        }
      }finally{busy=false;}
    }
    return Object.freeze({authorize,invoke,getMetadata:()=>({...metadata}),getCreationStatus:()=>creationStatus,getDefaults:defaults,subscribe(fn){listeners.add(fn);return ()=>listeners.delete(fn);},editMetadata(change){if(closed||busy)throw Error('DOCUMENT_OPERATION_IN_PROGRESS');metadata={...metadata,...checkedMetadata(change)};publish();},isDirty:()=>!closed&&dirty(surface().getState()),closeDocument(){closed=true;locked=null;proposal=null;}});
  }
  global.McelDocumentCandidatePort=Object.freeze({create});
})(window);
