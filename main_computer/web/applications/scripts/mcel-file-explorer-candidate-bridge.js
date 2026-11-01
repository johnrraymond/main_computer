/* Patch 05 opt-in independent app candidate. Generic MCEL shell owns the UI;
 * existing FileExplorerSemanticAdapter owns all filesystem reads and refusals.
 * No API endpoint, path validation, or mutation authority is implemented here.
 */
(function(global){
  "use strict";
  let active=null;
  function refuse(code){return {ok:false,code};}
  function project(state){
    const raw=state||{}, searching=Boolean(raw.search?.query);
    const entries=searching ? raw.search?.results : raw.entries;
    const selected=raw.selectedEntry||raw.preview?.entry||{};
    return {roots:Array.isArray(raw.roots)?raw.roots:[],
      location:{root:raw.selectedRootId||"—",directory:raw.relativePath||"/",
        count:Array.isArray(entries)?entries.length:0},
      entries:Array.isArray(entries)?entries:[],
      preview:{name:selected.name||"—",kind:selected.kind||"—",
        status:raw.preview?(raw.preview.readable?"Readable":"Unavailable"):"Not requested",
        content:raw.preview?.readable?String(raw.preview.content||"").slice(0,2400):"—"}};
  }
  function mount(root,{presentation="operational",planner="legacy",load=true,adapter=global.FileExplorerSemanticAdapter}={}){
    if(active)return refuse('already-mounted');
    const source=global.McelFileExplorerCandidateSource;
    if(!root || root.id!=='file-explorer-app' || !adapter?.executeIntent ||
      !source?.ir || source.ir.application?.appId!=='file-explorer-candidate' ||
      source.metadata?.schema!=='mcel.file-explorer-source-bound-candidate.v1' ||
      !global.McelSemanticApplicationShell || !global.McelIntentEffectRuntime)
      return refuse('missing-authoritative-components');
    const legacy=root.querySelector('.file-explorer-shell');
    if(!legacy)return refuse('missing-legacy-rollback');
    const host=root.ownerDocument.createElement('div');
    host.className='mcel-file-explorer-candidate-host';host.dataset.mcelFileExplorerCandidate='true';
    let version=0,disposed=false,pickedRoot=null,pickedEntry=null,query='';
    // Source-backed directories only: no guessed descendants or synthesized
    // paths. An expansion is a governed FileExplorerSemanticAdapter read.
    const directoryCache=new Map(),expanded=new Set();
    let cachedRoot="",treeBusy=false;
    let application=null,effects=null;
    const last={phase:'idle',receipt:null};
    function publish(status='available'){
      if(disposed)return;
      const state=adapter.getState();
      if(state.selectedRootId && cachedRoot!==state.selectedRootId){
        cachedRoot=state.selectedRootId;directoryCache.clear();expanded.clear();
      }
      if(state.selectedRootId && Array.isArray(state.entries) &&
         ["selectRoot","listDirectory","navigateUp"].includes(state.lastIntentId)){
        directoryCache.set(state.relativePath||"",state.entries.map(entry=>({...entry})));
        expanded.add(state.relativePath||"");
      }
      version++;
      application?.update(project(state),status);
      renderHierarchy();
    }
    function normalizedPath(entry){return String(entry?.relative_path||"").replace(/\\/g,"/");}
    function renderHierarchy(){
      const region=host.querySelector('[data-mcel-region="region:entries"]');
      if(!region)return;
      let tree=region.querySelector('[data-mcel-file-hierarchy]');
      if(!tree){
        tree=root.ownerDocument.createElement("section");
        tree.dataset.mcelFileHierarchy="true";
        tree.setAttribute("aria-label","Expandable file hierarchy");
        tree.style.cssText="max-height:420px;overflow:auto;border-bottom:1px solid #667085;padding:4px 0 10px";
        const content=region.querySelector(".mcel-application-shell__body")||region;
        content.append(tree);
      }
      tree.textContent="";
      const heading=root.ownerDocument.createElement("strong");
      heading.textContent="Directory tree (live scoped reads)";
      tree.append(heading);
      const state=adapter.getState();
      if(!state.selectedRootId)return;
      const list=root.ownerDocument.createElement("ul");
      list.setAttribute("role","tree");
      list.style.cssText="list-style:none;padding-left:8px;margin:4px 0";
      tree.append(list);
      const visited=new Set();
      function nodes(parent,dir,depth){
        if(depth>32||visited.has(dir))return;
        visited.add(dir);
        const children=directoryCache.get(dir)||[];
        for(const entry of children){
          const path=normalizedPath(entry);
          if(!path||(!dir?false:!path.startsWith(dir+"/")))continue;
          const isDir=entry.kind==="directory";
          const li=root.ownerDocument.createElement("li");
          li.setAttribute("role","treeitem");
          li.setAttribute("aria-level",String(depth+1));
          li.style.cssText="margin:2px 0;padding-left:10px";
          const toggle=root.ownerDocument.createElement("button");
          toggle.type="button";
          toggle.textContent=(isDir?(expanded.has(path)?"▾ ":"▸ "):"  ")+String(entry.name||path);
          toggle.title=path;
          if(isDir)toggle.setAttribute("aria-expanded",String(expanded.has(path)));
          toggle.addEventListener("click",async()=>{
            if(!isDir){pickedEntry=entry;await execute("previewEntry",{rootId:cachedRoot,relativePath:path,entry});return;}
            if(expanded.has(path)){expanded.delete(path);renderHierarchy();return;}
            if(treeBusy)return;
            if(directoryCache.has(path)){expanded.add(path);renderHierarchy();return;}
            treeBusy=true;toggle.disabled=true;
            try{
              const receipt=await execute("listDirectory",{rootId:cachedRoot,relativePath:path});
              if(receipt.ok)expanded.add(path);
              else last.phase="failed-directory-expand";
            }finally{treeBusy=false;renderHierarchy();}
          });
          li.append(toggle);
          if(isDir&&expanded.has(path)){
            const nested=root.ownerDocument.createElement("ul");
            nested.setAttribute("role","group");
            nested.style.cssText="list-style:none;padding-left:9px";
            li.append(nested);
            nodes(nested,path,depth+1);
          }
          parent.append(li);
        }
      }
      nodes(list,"",0);
    }
    async function execute(intent,params={}){
      const result=await adapter.executeIntent(intent,params);
      if(disposed)return {ok:false,code:'destroyed'};
      last.receipt=result.receipt;
      last.phase=result.status;
      if(!result.ok)return {ok:false,code:result.status==='blocked'?'refused':'failed',delivery:{ok:false}};
      publish('available');
      return {ok:true,code:'completed',delivery:{ok:true,status:'available',version}};
    }
    async function run(id){
      const state=adapter.getState();
      const rootId=state.selectedRootId;
      if(id==='inspect-roots'){
        const result=await execute('inspectRoots');
        if(result.ok && adapter.getState().roots?.length && !adapter.getState().selectedRootId){
          const roots=adapter.getState().roots;
          const preferred=roots.find(r=>r.id==='workspace')||roots[0];
          if(preferred) {const selected=await execute('selectRoot',{rootId:preferred.id,relativePath:''});
            if(!selected.ok)return selected;}
        }
        return result.ok?{...result,delivery:{ok:true,status:"available",version}}:result;
      }
      if(id==='choose-root')return execute('selectRoot',{rootId:pickedRoot?.id,relativePath:''});
      if(id==='refresh-directory')return execute('listDirectory',{rootId,relativePath:state.relativePath});
      if(id==='navigate-up')return execute('navigateUp',{rootId,relativePath:state.relativePath});
      if(id==='search-directory')return query.trim()?execute('searchCurrentFolder',{rootId,relativePath:state.relativePath,query:query.trim(),limit:80}):
        execute('listDirectory',{rootId,relativePath:state.relativePath});
      if(id==='open-entry'){
        if(!pickedEntry)return {ok:false,code:'no-selected-entry'};
        if(pickedEntry.kind==='directory')return execute('listDirectory',{rootId,relativePath:pickedEntry.relative_path});
        return execute('previewEntry',{rootId,relativePath:pickedEntry.relative_path,entry:pickedEntry});
      }
      return {ok:false,code:'undeclared-intent'};
    }
    const adapters={};
    for(const action of global.McelIntentEffectRuntime.check(source.ir)){
      adapters[action.capabilityId]={executeRead:()=>run(action.id)};
    }
    try{
      effects=global.McelIntentEffectRuntime.mount(source.ir,{adapters,observe:()=>version});
      application=global.McelSemanticApplicationShell.mount(host,source.ir,{
        presentation,planner,effectRuntime:effects,initialValues:{query:''},
        onFieldChange:(id,value)=>{if(id==='query')query=String(value);},
        onCollectionSelect:(region,row)=>{
          if(region==='region:roots'){pickedRoot=row;return true;}
          if(region==='region:entries'){pickedEntry=row;return true;}
          return false;
        }});
      root.insertBefore(host,legacy);
    }catch(err){effects?.destroy();application?.destroy();host.remove();return refuse('candidate-mount-failed');}
    const original={hidden:legacy.hasAttribute('hidden'),inert:legacy.hasAttribute('inert'),aria:legacy.getAttribute('aria-hidden')};
    legacy.setAttribute('hidden','');legacy.setAttribute('inert','');legacy.setAttribute('aria-hidden','true');
    root.dataset.mcelFileExplorerMode='candidate';
    const rootProvenance={renderer:root.getAttribute('data-mcel-renderer'),
      projection:root.getAttribute('data-mcel-projection')};
    root.setAttribute('data-mcel-renderer','mcel.semantic-application-shell');
    root.setAttribute('data-mcel-projection','source-bound-candidate');
    active={root,host,legacy,original,rootProvenance,application,effects,adapter,getVersion:()=>version};
    publish('empty');
    if(load) void effects.dispatch('inspect-roots');
    return {ok:true,code:'mounted-file-explorer-semantic-candidate'};
  }
  function status(){return active?{mounted:true,version:active.getVersion(),
    operation:active.effects.status(),domain:active.adapter.getState(),presentation:active.application.getState().presentation}:
    {mounted:false};}
  function destroy(){
    if(!active)return refuse('not-mounted');
    const s=active;active=null;
    s.effects.destroy();s.application.destroy();s.host.remove();
    if(!s.original.hidden)s.legacy.removeAttribute('hidden');
    if(!s.original.inert)s.legacy.removeAttribute('inert');
    if(s.original.aria===null)s.legacy.removeAttribute('aria-hidden');else s.legacy.setAttribute('aria-hidden',s.original.aria);
    for(const [attr,value] of [['data-mcel-renderer',s.rootProvenance.renderer],
      ['data-mcel-projection',s.rootProvenance.projection]]){
      if(value===null)s.root.removeAttribute(attr);else s.root.setAttribute(attr,value);
    }
    delete s.root.dataset.mcelFileExplorerMode;
    return {ok:true,code:'legacy-restored'};
  }
  global.McelFileExplorerCandidate=Object.freeze({mount,status,destroy,project});
})(typeof window!=='undefined'?window:globalThis);
