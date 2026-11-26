/* ONLYOFFICE application capability port. The spreadsheet engine runs only on the isolated docs origin. */
(function(global){
  'use strict';
  const rules=Object.freeze({
    'refresh-workbooks':['workbook-reader','list','listWorkbooks','external-read',[]],
    'refresh-server-status':['workbook-reader','status','serverStatus','external-read',[]],
    'open-workbook':['workbook-reader','open','openWorkbook','external-read',['path']],
    'reload-editor':['workbook-reader','reload','reloadWorkbook','external-read',[]],
    'create-workbook':['workbook-writer','create','createWorkbook','source-write',['name']],
    'upload-workbook':['workbook-writer','upload','uploadWorkbook','source-write',[]],
    'save-now':['workbook-writer','save','forceSave','source-write',[]],
    'attach-code':['workbook-comment','attach','attachComment','consequential',['language','code']]
  });
  function mountEditor(host,data,{timeout=120000}={}){
    if(data.hardened_editor_origin!==true)throw Error('Hardened isolated editor configuration required');
    const url=new URL(data.public_url||data.document_server_url);
    if(url.protocol!=='https:'||url.hostname!=='onlyoffice.main-computer.localhost'||url.username||url.password||url.pathname!=='/'||url.search||url.hash||url.origin===location.origin)throw Error('Invalid isolated Document Server origin');
    const origin=url.origin,channel=crypto.randomUUID(),frame=document.createElement('iframe');
    frame.title='ONLYOFFICE spreadsheet editor';frame.referrerPolicy='no-referrer';frame.style.cssText='width:100%;height:100%;border:0;display:block';
    const pending=new Map();let initialized=false,closed=false,ready=false,timer,resolveReady,rejectReady;
    const promise=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
    function destroy(){if(closed)return;closed=true;global.removeEventListener('message',receive);clearTimeout(timer);
      if(!ready)rejectReady(Error('Editor closed'));
      for(const item of pending.values()){clearTimeout(item.timer);item.reject(Error('Editor closed'));}pending.clear();frame.remove();}
    function receive(event){const msg=event.data;if(closed||event.origin!==origin||event.source!==frame.contentWindow||!msg||msg.channel!==channel)return;
      if(msg.type==='mc-oo-host-ready'&&!initialized){initialized=true;frame.contentWindow.postMessage({type:'mc-oo-config',channel,config:data.config},origin);}
      else if(msg.type==='mc-oo-document-ready'&&initialized){clearTimeout(timer);ready=true;resolveReady();}
      else if(msg.type==='mc-oo-error'){const error=Error(String(msg.message||'Isolated editor error'));if(!ready)rejectReady(error);else host.dispatchEvent(new CustomEvent('mcel:onlyoffice-error',{detail:error.message}));}
      else if(msg.type==='mc-oo-comment-result'&&ready){const item=pending.get(msg.requestId);if(item){pending.delete(msg.requestId);clearTimeout(item.timer);item.resolve(String(msg.message||'Command finished'));}}
    }
    function attachComment(text){if(closed||!ready)return Promise.reject(Error('Editor not ready'));
      return new Promise((resolve,reject)=>{const requestId=crypto.randomUUID();const timer=setTimeout(()=>{pending.delete(requestId);reject(Error('Automation response timed out'));},30000);pending.set(requestId,{resolve,reject,timer});frame.contentWindow.postMessage({type:'mc-oo-comment',channel,requestId,text},origin);});}
    global.addEventListener('message',receive);timer=setTimeout(()=>rejectReady(Error('Isolated Document Server readiness timed out')),timeout);
    frame.src=origin+'/mc-editor-host.html#'+new URLSearchParams({parent:location.origin,channel});host.append(frame);
    return {ready:promise,destroy,attachComment};
  }
  function create(getHost,getFile,{fetcher=global.fetch.bind(global),mount=mountEditor,onStatus=()=>{}}={}){
    let busy=false,closed=false,active='',editor=null,known=new Set(),writtenPath='';
    function authorize(req){const name=req?.intentId?.replace(/^intent:/,''),r=rules[name];return !closed&&!!r&&req.appId==='onlyoffice'&&req.intentId==='intent:'+name&&req.effectId==='effect:'+name+'.request'&&req.capabilityId==='capability:'+r[0]&&req.operation===r[1]&&req.runtimeMethod===r[2]&&req.risk===r[3]&&Object.keys(req.arguments||{}).sort().join('|')===r[4].slice().sort().join('|')&&Object.values(req.arguments||{}).every(v=>typeof v==='string');}
    async function post(route,body={}){const response=await fetcher('/api/applications/onlyoffice/'+route,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok||data.ok!==true)throw Error(data.error||'HTTP '+response.status);if(closed)throw Error('Application closed');return data;}
    async function list(){const data=await post('files');const rows=(data.files||[]).map(f=>({path:String(f.path),size:Number(f.bytes||0).toLocaleString()+' B'}));known=new Set(rows.map(f=>f.path));return rows;}
    function requireActive(){if(!active||!editor)throw Error('Open a workbook first');return active;}
    async function open(path,force=false){if(!force&&path&&path===active&&editor)return path;if(!path||!known.has(path)&&path!==active)throw Error('Choose a listed workbook first');
      const host=getHost();if(!host)throw Error('Editor surface unavailable');
      onStatus('Preparing editor: '+path+'…');const data=await post('config',{path});if(data.hardened_editor_origin!==true)throw Error('Hardened Document Server configuration required');
      // Validate the response before dropping the old editor. Never reparent a live iframe.
      const url=new URL(data.public_url||data.document_server_url);
      if(url.protocol!=='https:'||url.hostname!=='onlyoffice.main-computer.localhost'||url.username||url.password||url.pathname!=='/'||url.search||url.hash||url.origin===location.origin)throw Error('Invalid isolated Document Server origin');
      editor?.destroy();editor=null;active='';host.replaceChildren();onStatus('Opening '+path+'…');
      const next=mount(host,data);editor=next;
      try{await next.ready;if(closed)throw Error('Application closed');active=path;onStatus('Editor open: '+path);return path;}
      catch(e){next.destroy();if(editor===next)editor=null;onStatus('Editor unavailable: '+e.message);throw e;}
    }
    function base64(buffer){const bytes=new Uint8Array(buffer);let text='';for(let i=0;i<bytes.length;i+=32768)text+=String.fromCharCode.apply(null,bytes.subarray(i,i+32768));return btoa(text);}
    async function invoke(req){if(!authorize(req))throw Error('ONLYOFFICE_CAPABILITY_REFUSED');if(busy)throw Error('ONLYOFFICE_OPERATION_IN_PROGRESS');busy=true;
      try{const a=req.arguments;switch(req.runtimeMethod){
        case 'listWorkbooks':return await list();
        case 'serverStatus':{const d=await post('status',{probe:true,timeout_s:1.5});return (d.server_probe?.ok?'Document Server reachable':'Document Server unreachable')+' · '+String(d.public_url||d.document_server_url||'');}
        case 'openWorkbook':return await open(a.path);
        case 'reloadWorkbook':return await open(requireActive(),true);
        case 'createWorkbook':{
          // Re-read inventory before allocating a default name. Never overwrite.
          const rows=await list(),existing=new Set(rows.map(r=>r.path.toLowerCase()));
          let name=a.name.trim();if(!name){name='Book.xlsx';for(let n=2;existing.has(name.toLowerCase());n++)name='Book'+n+'.xlsx';}
          if(!/\.xlsx$/i.test(name))throw Error('Choose an XLSX filename');
          if(existing.has(name.toLowerCase()))throw Error('Workbook already exists: '+name+' — choose another name or leave it blank');
          const d=await post('create',{path:name});onStatus('Workbook created: '+d.path);const updated=await list();writtenPath=String(d.path);return updated;}
        case 'uploadWorkbook':{const f=getFile();if(!f||!/\.xlsx$/i.test(f.name))throw Error('Choose an XLSX file first');const d=await post('upload',{path:f.name,content_base64:base64(await f.arrayBuffer())});onStatus('Workbook uploaded: '+d.path);const updated=await list();writtenPath=String(d.path);return updated;}
        case 'forceSave':{const d=await post('force-save',{path:requireActive()});if(d.command_response?.error!==0)throw Error('Document Server did not accept force-save: '+String(d.command_response?.error??'unknown'));return 'Force-save requested (not a durable-save receipt)';}
        case 'attachComment':{const path=requireActive();if(!['javascript','python','basic'].includes(a.language)||!a.code.trim())throw Error('Select a language and enter code');return await editor.attachComment('Main Computer ONLYOFFICE code add-on\nLanguage: '+a.language+'\nWorkbook: '+path+'\n\n'+a.code);}
        default:throw Error('ONLYOFFICE_CAPABILITY_REFUSED');}}
      catch(error){onStatus(String(error.message||error));throw error;}finally{busy=false;}}
    function closeDocument(){closed=true;editor?.destroy();editor=null;active='';}
    return Object.freeze({authorize,invoke,closeDocument,isBusy:()=>busy,takeWrittenPath:()=>{const path=writtenPath;writtenPath='';return path;}});
  }
  global.McelOnlyOfficePort=Object.freeze({create,mountEditor});
})(window);
