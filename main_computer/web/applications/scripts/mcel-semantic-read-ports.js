"use strict";
/* Exact-scoped adapters to existing Main Computer endpoints.
 * File Manager and Task Manager remain read-only. Code Editor admits only an
 * explicitly confirmed, stale-source-checked save of a successfully opened file.
 * No generic fetch-by-name, source tree mutation or process control.
 * The semantic runtime has no ambient backend authority.
 */
(function(global){
  const allow=Object.freeze({
    "semantic-file-manager":Object.freeze({
      "intent:load-roots":["filesystem-read","roots","listRoots",[]],
      "intent:browse":["filesystem-read","list","listDirectory",["rootId"]],
      "intent:preview-entry":["filesystem-read","preview","readPreview",["rootId","relativePath"]],
      "intent:enter-directory":["filesystem-read","enter","openDirectory",["rootId","relativePath"]],
      "intent:search":["filesystem-read","search","searchEntries",["rootId","query"]],
      "intent:open-with":["application-navigation","open","openSuggestedApp",["rootId","relativePath"]]
    }),
    // P2-generated candidate: four bounded read-only monitor observations.
    "semantic-task-monitor-generated":Object.freeze({
      "intent:refresh":["monitor","observe","observeProcesses",["query"]],
      "intent:refresh-all":["monitor","all","observeAllProcesses",["query"]],
      "intent:refresh-connections":["monitor","connections","observeConnections",["query"]],
      "intent:refresh-hardware":["monitor","hardware","observeHardware",["query"]]
    }),
    "semantic-task-manager":Object.freeze({
      "intent:refresh":["monitor","observe","observeProcesses",["query"]],
      "intent:refresh-all":["monitor","all","observeAllProcesses",["query"]],
      "intent:refresh-connections":["monitor","connections","observeConnections",["query"]],
      "intent:refresh-hardware":["monitor","hardware","observeHardware",["query"]],
      "intent:list-schedules":["monitor","schedules","listSchedules",[]],
      "intent:task-ai":["monitor","ai","getAiBrief",["instruction","query","view"]],
      "intent:terminate-process":["task-control","terminate","terminateObservedProcess",["selected"]],
      "intent:kill-process":["task-control","kill","killObservedProcess",["selected"]],
      "intent:start-server":["task-control","start","startServer",[]],
      "intent:restart-server":["task-control","restart","restartServer",[]],
      "intent:shutdown-server":["task-control","shutdown","shutdownServer",[]],
      "intent:create-schedule":["task-scheduling","create","createSchedule",["action","runAt","note"]],
      "intent:delete-schedule":["task-scheduling","delete","deleteSchedule",["scheduleId"]],
      "intent:run-schedule":["task-scheduling","run","runScheduledAction",["scheduleId"]]
    }),
    "semantic-task-manager-generated-p3":Object.freeze({
      "intent:server-status":["monitor","server-status","readServerStatus",[]],
      "intent:refresh":["monitor","observe","observeProcesses",["query","limit","includeConnections"]],
      "intent:refresh-all":["monitor","all","observeAllProcesses",["query","limit","includeConnections"]],
      "intent:refresh-connections":["monitor","connections","observeConnections",["query","limit","includeConnections"]],
      "intent:refresh-hardware":["monitor","hardware","observeHardware",["query","limit","includeConnections"]],
      "intent:list-schedules":["monitor","schedules","listSchedules",[]],
      "intent:task-ai":["monitor","ai","getAiBrief",["instruction","query","view"]],
      "intent:terminate-process":["task-control","terminate","terminateObservedProcess",["selected"]],
      "intent:kill-process":["task-control","kill","killObservedProcess",["selected"]],
      "intent:start-server":["task-control","start","startServer",[]],
      "intent:restart-server":["task-control","restart","restartServer",[]],
      "intent:shutdown-server":["task-control","shutdown","shutdownServer",[]],
      "intent:create-schedule":["task-scheduling","create","createSchedule",["action","runAt","note"]],
      "intent:delete-schedule":["task-scheduling","delete","deleteSchedule",["scheduleId"]],
      "intent:run-schedule":["task-scheduling","run","runScheduledAction",["scheduleId"]]
    }),
    "semantic-code-editor":Object.freeze({
      "intent:list-files":["source-reader","list","listSourceFiles",[]],
      "intent:open-source":["source-reader","read","readSource",["path"]],
      "intent:browse-directory":["source-reader","directory","listDirectory",["path"]],
      "intent:save-source":["source-writer","save","saveSource",["path","text"]],
      "intent:prepare-reviewed-change":["reviewer","prepare","prepareReviewedChange",["path","text"]],
      "intent:apply-reviewed-change":["review-applier","apply","applyReviewedChange",["path","review","text"]],
      "intent:preview-aider-plan":["aider-preview","preview","previewAiderCommand",["path","instruction"]],
      "intent:aider-context":["aider-context","context","readContext",[]],
      "intent:aider-jobs":["aider-context","jobs","readJobs",[]],
      "intent:run-aider":["aider-control","run","runAider",["path","instruction","files"]],
      "intent:archive-aider-context":["aider-control","archive","archiveAiderContext",["path"]],
      "intent:reset-aider-context":["aider-control","reset","resetAiderContext",["path"]],
      "intent:load-aider-archive":["aider-control","load","loadAiderArchive",["archiveId"]]
    })
  });
  const err=s=>{throw Error("MCEL_READ_PORT:"+s);};
  const requiredText=(value,max=4096)=>typeof value==="string" && value.length<=max;
  const plain=x=>x && typeof x==="object" && !Array.isArray(x);
  function permitted(req){
    const consequential=new Set(["intent:terminate-process","intent:kill-process","intent:start-server",
      "intent:restart-server","intent:shutdown-server","intent:create-schedule","intent:delete-schedule","intent:run-schedule",
      "intent:run-aider","intent:archive-aider-context","intent:reset-aider-context","intent:load-aider-archive"]);
    const expectedRisk=(["semantic-task-manager","semantic-task-manager-generated-p3","semantic-code-editor"].includes(req.appId)) && consequential.has(req.intentId)?"consequential":
      req.appId==="semantic-code-editor" && ["intent:save-source","intent:apply-reviewed-change"].includes(req.intentId)?"source-write":"external-read";
    if(!plain(req) || req.risk!==expectedRisk)return false;
    const spec=allow[req.appId]?.[req.intentId];
    if(!spec || req.effectId!=="effect:"+req.intentId.slice(7)+".request" ||
       req.capabilityId!=="capability:"+spec[0] || req.operation!==spec[1] || req.runtimeMethod!==spec[2] ||
       !plain(req.arguments))return false;
    const keys=Object.keys(req.arguments).sort();
    return JSON.stringify(keys)===JSON.stringify([...spec[3]].sort()) &&
      keys.every(k=>k==="review" && req.intentId==="intent:apply-reviewed-change"
        ? plain(req.arguments[k]) && Object.keys(req.arguments[k]).sort().join(",")==="after,before,details,handle,path" &&
          Object.values(req.arguments[k]).every(v=>requiredText(v,100000))
        :requiredText(req.arguments[k],k==="text" && ["intent:save-source","intent:prepare-reviewed-change","intent:apply-reviewed-change"].includes(req.intentId) ? 20000000:4096));
  }
  function create(options={}){
    const appId=options.appId;
    if(!Object.prototype.hasOwnProperty.call(allow,appId))err("app-not-admitted");
    const fetcher=options.fetcher || global.fetch?.bind(global);
    if(typeof fetcher!=="function")err("missing-fetch");
    let knownRoots=new Set(), lastListing=null, knownDirectories=new Set([""]), knownEditorFiles=new Set(), knownEditorDirs=new Set([""]);
    let activeRoot=null, rootEpoch=0, readSequence=0;
    const directoryEvidence=new Map(), currentEntries=new Map();
    const routeableApps=new Set(["code-editor","document","spreadsheet","game-editor"]);
    const navigate=options.navigate || (route=>{
      if(typeof global.location?.assign!=="function")err("navigation-host-unavailable");
      global.location.assign(route);return true;
    });
    const openedEditors=new Map(), reviewedChanges=new Map(), editorReadEpochs=new Map();
    const busyEditors=new Set(), observedAiderArchives=new Map();
    let aiderContextRepo="";
    const selectedAiderFiles=(path,raw)=>{
      const files=raw.trim()?raw.split(/[\n,]+/).map(x=>x.trim()).filter(Boolean):[path];
      if(!files.length || files.length>32 || !files.includes(path) || new Set(files).size!==files.length)return null;
      if(!files.every(f=>validPath(f) && editorBaseline(f)?.hash && !editorBaseline(f).truncated &&
          !busyEditors.has(f)))return null;
      return files;
    };
    const observedProcesses=new Map(), observedSchedules=new Map();
    const p3Receipt=(outcome,message,requestId="")=>appId==="semantic-task-manager-generated-p3"
      ? {outcome,message:String(message),requestId:String(requestId)} : String(message);

    let monitorQuery="", monitoredLimit=100, monitorConnections=true;
    const validPath=path=>typeof path==="string" && !path.includes("\\") &&
      !path.startsWith("/") && !path.endsWith("/") && !path.includes("//") &&
      !path.split("/").some(part=>part==="." || part==="..");
    const isChild=(path,parent)=>validPath(path) && path!=="" &&
      (parent==="" ? !path.includes("/") : path.startsWith(parent+"/") && !path.slice(parent.length+1).includes("/"));
    function entriesFor(payload,parent,search=false){
      if(!Array.isArray(payload.entries))err("entries-missing");
      return unique(payload.entries.map(v=>{
        row(v,["relative_path","name","kind"]);
        if(!validPath(v.relative_path) || (!search && !isChild(v.relative_path,parent)))err("entry-outside-listed-directory");
        return fileEntry(v);
      }),"id");
    }
    function fileEntry(v){
      return {id:v.relative_path,name:v.name,kind:v.kind,
        pathDisplay:String(v.path_display??v.relative_path),bytes:Number.isFinite(v.bytes)&&v.bytes>=0?v.bytes:0,
        mtime:Number.isFinite(v.mtime)&&v.mtime>=0?v.mtime:0,
        category:String(v.category||"other"),suggestedApp:String(v.suggested_app||""),
        mainComputerPurview:v.main_computer_purview===true,mountedWindowsDrive:v.mounted_windows_drive===true};
    }
    function updateDirectoryEvidence(root,parent,values){
      if(activeRoot!==root)return;
      const children=new Set(values.filter(v=>v.kind==="directory").map(v=>v.id));
      for(const removed of directoryEvidence.get(parent)||[]){
        if(children.has(removed))continue;
        for(const key of [...directoryEvidence.keys()])
          if(key===removed || key.startsWith(removed+"/"))directoryEvidence.delete(key);
      }
      directoryEvidence.set(parent,children);
      knownDirectories=new Set(["",...directoryEvidence.keys(),...[...directoryEvidence.values()].flatMap(set=>[...set])]);
    }
    const editorBaseline=path=>openedEditors.get(path);
    const bumpEditorEpoch=path=>{const n=(editorReadEpochs.get(path)||0)+1;editorReadEpochs.set(path,n);return n;};
    const previouslyOpenedEditorFiles=new Set();
    async function digest(value){
      if(!global.crypto?.subtle)err("sha256-unavailable");
      const bytes=new TextEncoder().encode(value);
      const hash=await global.crypto.subtle.digest("SHA-256",bytes);
      return Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("");
    }
    function authorized(req){
      if(!permitted(req) || req.appId!==appId)return false;
      const a=req.arguments;
      if(req.intentId==="intent:browse")return knownRoots.has(a.rootId);
      if(req.intentId==="intent:preview-entry")return activeRoot===a.rootId && lastListing?.rootId===a.rootId && lastListing.entries.has(a.relativePath) && validPath(a.relativePath);
      if(req.intentId==="intent:open-with")return activeRoot===a.rootId && lastListing?.rootId===a.rootId &&
        lastListing.entries.has(a.relativePath) && routeableApps.has(currentEntries.get(a.relativePath)?.suggestedApp);
      if(req.intentId==="intent:enter-directory")return knownRoots.has(a.rootId) && activeRoot===a.rootId &&
        knownDirectories.has(a.relativePath) && validPath(a.relativePath);
      if(req.intentId==="intent:search")return knownRoots.has(a.rootId) && lastListing?.rootId===a.rootId && a.query.trim().length>0 && a.query.length<=256;
      if(req.intentId==="intent:open-source")return knownEditorFiles.has(a.path) || previouslyOpenedEditorFiles.has(a.path);
      if(req.intentId==="intent:browse-directory")return knownEditorDirs.has(a.path);
      if(req.intentId==="intent:preview-aider-plan")return !!editorBaseline(a.path)?.hash && !busyEditors.has(a.path) &&
        a.instruction.trim().length>0 && a.instruction.length<=4000;
      if(req.intentId==="intent:run-aider")return a.instruction.trim().length>0 && a.instruction.length<=4000 &&
        !!selectedAiderFiles(a.path,a.files);
      if(["intent:archive-aider-context","intent:reset-aider-context"].includes(req.intentId))return !!editorBaseline(a.path)?.hash && !busyEditors.has(a.path);
      if(req.intentId==="intent:load-aider-archive")return observedAiderArchives.has(a.archiveId) &&
        observedAiderArchives.get(a.archiveId)===aiderContextRepo && !!aiderContextRepo;
      if(req.intentId==="intent:prepare-reviewed-change")return !!editorBaseline(a.path)?.hash &&
        !editorBaseline(a.path).truncated && !busyEditors.has(a.path) && a.text.length<=2000000;
      if(req.intentId==="intent:apply-reviewed-change"){
        const prepared=reviewedChanges.get(a.path);
        return a.path===a.review.path && !!prepared && !busyEditors.has(prepared.path) &&
          prepared.hash===editorBaseline(prepared.path)?.hash && a.text===prepared.text &&
          Object.keys(a.review).every(k=>a.review[k]===prepared.review[k]);
      }
      if(req.intentId==="intent:save-source")return !!editorBaseline(a.path)?.hash &&
        requiredText(a.text,20000000) && !busyEditors.has(a.path) && !editorBaseline(a.path).truncated;
      if(["intent:terminate-process","intent:kill-process"].includes(req.intentId))return observedProcesses.has(a.selected) && !!observedProcesses.get(a.selected).createdAt &&
        !observedProcesses.get(a.selected).isCurrentProcess;
      if(req.intentId==="intent:create-schedule")return ["server_status","server_start","server_restart","server_shutdown"].includes(a.action) &&
        a.runAt.length<=80 && !Number.isNaN(Date.parse(a.runAt)) && a.note.length<=1024;
      if(["intent:delete-schedule","intent:run-schedule"].includes(req.intentId))return observedSchedules.has(a.scheduleId);
      if(req.intentId==="intent:task-ai")return ["server-processes","all-processes","connections","cpu-gpu"].includes(a.view) &&
        a.instruction.trim().length>0 && a.instruction.length<=2000 && a.query.length<=256;
      return true;
    }
    let parityRequestId=0;
    function parityTransport(detail){
      // The diagnostic plugin observes this event if enabled. No payload is copied.
      if (!global.document || typeof global.CustomEvent!=="function")return;
      global.document.dispatchEvent(new global.CustomEvent("mcel:parity-transport",
        {detail:{appId,...detail}}));
    }
    let taskCsrfPromise = null;
    function taskCsrf(){
      if(!taskCsrfPromise)taskCsrfPromise=fetcher("/api/applications/task/authority/session",
        {method:"GET",credentials:"same-origin"}).then(async response=>{
          if(!response?.ok)err("task-local-authority-unavailable");
          const result=await response.json();
          if(typeof result.csrf!=="string")err("invalid-task-session");
          return result.csrf;
        }).catch(error=>{taskCsrfPromise=null;throw error;});
      return taskCsrfPromise;
    }
    async function post(url,body){
      const requestId=++parityRequestId;
      const start=typeof performance!=="undefined" ? performance.now() : Date.now();
      let status=0;
      parityTransport({phase:"request",requestId,endpoint:url,method:"POST"});
      try {
        const taskMutation=url.startsWith("/api/applications/task/") &&
          ["action","schedule/create","schedule/delete","schedule/run","ai"].some(part=>url.endsWith("/"+part));
        const csrf=taskMutation?await taskCsrf():null;
        const sent=url.endsWith("/task/action") && body.confirm===true && body.action!=="server_status"
          ? {...body,request_id:body.request_id || "manual_"+(global.crypto?.randomUUID?.()?.replaceAll("-","") ||
              Date.now().toString(36)+Math.random().toString(36).slice(2,16))} : body;
        const response=await fetcher(url,{method:"POST",credentials:"same-origin",
          headers:{"Content-Type":"application/json",...(csrf?{"X-MCEL-Task-CSRF":csrf}:{})},body:JSON.stringify(sent)});
        status=Number(response?.status)||0;
        if(!response || !response.ok)err("backend-http-failed:"+url+":"+status);
        const payload=await response.json();
        if(!plain(payload) || payload.ok===false || typeof payload.error==="string")err("backend-refused:"+url);
        parityTransport({phase:"response",requestId,endpoint:url,status,outcome:"http-ok",
          durationMs:Math.round((typeof performance!=="undefined" ? performance.now() : Date.now())-start)});
        return payload;
      } catch (error) {
        parityTransport({phase:"response",requestId,endpoint:url,status,
          outcome:status>=400?"http-failed":"rejected",
          durationMs:Math.round((typeof performance!=="undefined" ? performance.now() : Date.now())-start)});
        throw error;
      }
    }
    async function get(url){
      const response=await fetcher(url,{method:"GET",credentials:"same-origin"});
      if(!response?.ok)err("backend-http-failed:"+url+":"+String(response?.status||0));
      const payload=await response.json();
      if(!plain(payload) || payload.ok===false || typeof payload.error==="string")err("backend-refused:"+url);
      return payload;
    }
    function readAiderStatus(payload){
      if(!plain(payload.active) || !Array.isArray(payload.archives) || !Array.isArray(payload.activities))
        err("aider-context-invalid");
      const repo=String(payload.active.repo_dir||".");
      if(repo!==aiderContextRepo){aiderContextRepo=repo;observedAiderArchives.clear();}
      for(const value of payload.archives){
        if(plain(value) && typeof value.id==="string" && typeof value.repo_dir==="string")
          observedAiderArchives.set(value.id,value.repo_dir);
      }
      return JSON.stringify({active:payload.active,archives:payload.archives,
        currentArchive:payload.current_archive,activities:payload.activities});
    }
    const row=(v,k)=>{if(!plain(v) || k.some(x=>typeof v[x]!=="string"))err("invalid-row");return v;};
    const unique=(rows,key)=>{const seen=new Set();for(const item of rows){const id=item[key];if(seen.has(id))err("duplicate-identity");seen.add(id);}return rows;};
    async function invoke(req){
      if(!authorized(req))err("request-denied");
      const a=req.arguments;
      switch(req.intentId){
        case "intent:load-roots":{
          const payload=await post("/api/applications/file-explorer/roots",{});
          if(!Array.isArray(payload.roots))err("roots-missing");
          const values=unique(payload.roots.map(v=>{row(v,["id","label"]);return {id:v.id,label:v.label};}),"id");
          knownRoots=new Set(values.map(v=>v.id));activeRoot=null;rootEpoch++;directoryEvidence.clear();currentEntries.clear();lastListing=null;knownDirectories=new Set([""]);return values;
        }
        case "intent:browse":{
          if(!a.rootId)err("root-not-selected");
          const epoch=++rootEpoch;
          if(activeRoot!==a.rootId){activeRoot=a.rootId;directoryEvidence.clear();currentEntries.clear();
            knownDirectories=new Set([""]);lastListing=null;}
          const payload=await post("/api/applications/file-explorer/list",{root_id:a.rootId,relative_path:""});
          if(payload.root_id!==undefined && payload.root_id!==a.rootId)err("root-response-mismatch");
          if(payload.relative_path!==undefined && payload.relative_path!=="")err("root-response-mismatch");
          const values=entriesFor(payload,"");
          if(epoch!==rootEpoch || activeRoot!==a.rootId)return values;
          lastListing={rootId:a.rootId,relativePath:"",entries:new Set(values.filter(v=>v.kind!=="directory").map(v=>v.id))};
          currentEntries.clear();for(const v of values)if(v.kind!=="directory")currentEntries.set(v.id,v);
          updateDirectoryEvidence(a.rootId,"",values);return values;
        }
        case "intent:enter-directory":{
          const epoch=rootEpoch, reqNumber=++readSequence;
          const payload=await post("/api/applications/file-explorer/list",{root_id:a.rootId,relative_path:a.relativePath});
          if(payload.root_id!==a.rootId || payload.relative_path!==a.relativePath || !Array.isArray(payload.entries))err("directory-response-mismatch");
          const values=entriesFor(payload,a.relativePath);
          if(epoch===rootEpoch && activeRoot===a.rootId){
            if(!lastListing || reqNumber>= (lastListing.sequence||0))
              lastListing={rootId:a.rootId,relativePath:a.relativePath,sequence:reqNumber,
                entries:new Set(values.filter(v=>v.kind!=="directory").map(v=>v.id))};
            currentEntries.clear();for(const v of values)if(v.kind!=="directory")currentEntries.set(v.id,v);
            updateDirectoryEvidence(a.rootId,a.relativePath,values);
          }
          return values;
        }
        case "intent:search":{
          const payload=await post("/api/applications/file-explorer/search",{root_id:a.rootId,relative_path:lastListing?.rootId===a.rootId?lastListing.relativePath:"",query:a.query,limit:200});
          if(payload.root_id!==a.rootId || !Array.isArray(payload.results))err("search-response-mismatch");
          const values=unique(payload.results.map(v=>{
            row(v,["relative_path","name","kind"]);
            if(!validPath(v.relative_path))err("invalid-search-path");return fileEntry(v);
          }),"id");
          if(activeRoot===a.rootId){
            lastListing={rootId:a.rootId,relativePath:lastListing?.relativePath||"",entries:new Set(values.filter(v=>v.kind!=="directory").map(v=>v.id))};
            currentEntries.clear();for(const v of values)if(v.kind!=="directory")currentEntries.set(v.id,v);
            for(const v of values)if(v.kind==="directory")knownDirectories.add(v.id);
          }
          return values;
        }
        case "intent:preview-entry":{
          if(!a.rootId || !a.relativePath)err("entry-not-selected");
          const payload=await post("/api/applications/file-explorer/read",{root_id:a.rootId,relative_path:a.relativePath});
          if(payload.readable!==true && payload.readable!==false)err("preview-unavailable");
          if(payload.entry && payload.entry.relative_path!==a.relativePath)err("preview-entry-mismatch");
          const entry=fileEntry(payload.entry || {relative_path:a.relativePath,name:a.relativePath.split("/").pop(),kind:"file"});
          if(payload.readable===true && !requiredText(payload.content,524288))err("invalid-preview");
          return {readable:payload.readable,reason:payload.readable?"":String(payload.reason||"metadata only"),
            content:payload.readable?payload.content:"",entry};
        }
        case "intent:open-with":{
          const entry=currentEntries.get(a.relativePath);
          if(!entry || !routeableApps.has(entry.suggestedApp))err("suggested-application-not-authorized");
          const route="/applications/"+entry.suggestedApp;
          const accepted=navigate(route,{rootId:a.rootId,relativePath:a.relativePath,appId:entry.suggestedApp});
          if(accepted!==true)err("navigation-not-accepted");
          return "Navigation requested: "+route;
        }
        case "intent:refresh":
        case "intent:refresh-all":
        case "intent:refresh-connections":
        case "intent:refresh-hardware":{
          const all=req.intentId==="intent:refresh-all";
          const needConnections=req.intentId==="intent:refresh-connections";
          // P3 operator controls are semantic inputs, not layout or implied authority.
          // P1/P2 and other installed ports retain their original request contract.
          let limit=100,includeConnections=needConnections;
          if(appId==="semantic-task-manager-generated-p3"){
            if(!/^(?:[5-9]|[1-9][0-9]|100)$/.test(a.limit) ||
                !["true","false"].includes(a.includeConnections))err("monitor-options-invalid");
            limit=Number(a.limit);includeConnections=a.includeConnections==="true";
            monitoredLimit=limit;monitorConnections=includeConnections;
          }
          const payload=await post("/api/applications/task/overview",{query:a.query,limit,include_all:all||needConnections,include_connections:includeConnections});
          if(a.query.trim() || req.intentId==="intent:refresh" || req.intentId==="intent:refresh-all")monitorQuery=a.query;
          if(needConnections){
            if(!Array.isArray(payload.connections))err("connections-missing");
            return unique(payload.connections.map((v,index)=>{
              if(!plain(v))err("invalid-connection");
              return {key:String(v.pid??"")+":"+String(v.local||"")+":"+String(v.remote||"")+":"+index,
                pid:String(v.pid??""),name:String(v.process_name||""),status:String(v.status||""),
                local:String(v.local||""),remote:String(v.remote||"")};
            }),"key");
          }
          if(req.intentId==="intent:refresh-hardware"){
            if(!plain(payload.hardware) || typeof payload.hardware.summary!=="string")err("hardware-missing");
            const hw=payload.hardware,cpu=plain(hw.cpu)?hw.cpu:{},gpu=plain(hw.gpu)?hw.gpu:{};
            const validNum=v=>typeof v==="number"&&Number.isFinite(v);
            const cpuCores=Array.isArray(cpu.per_core)?cpu.per_core:[];
            const devices=Array.isArray(gpu.devices)?gpu.devices:[];
            return {summary:hw.summary,updatedAt:String(hw.updated_at||""),
              cpuAvailable:cpu.available===true,cpuPercent:validNum(cpu.overall_percent)?cpu.overall_percent:0,
              cpuPercentAvailable:validNum(cpu.overall_percent),
              cpuCores:cpuCores.map((v,i)=>({id:String(v.index??i),index:v.index??i,
                label:String(v.label||""),percent:validNum(v.percent)?v.percent:0})),cpuRaw:JSON.stringify(cpu),
              gpuAvailable:gpu.available===true,gpuPercent:validNum(gpu.overall_percent)?gpu.overall_percent:0,
              gpuPercentAvailable:validNum(gpu.overall_percent),
              gpuDevices:devices.map((v,i)=>({id:String(v.index??i),index:v.index??i,name:String(v.name||""),
                utilization:validNum(v.utilization_percent)?v.utilization_percent:0,utilizationAvailable:validNum(v.utilization_percent),
                memoryUsed:validNum(v.memory_used_mb)?v.memory_used_mb:0,memoryUsedAvailable:validNum(v.memory_used_mb),
                memoryTotal:validNum(v.memory_total_mb)?v.memory_total_mb:0,memoryTotalAvailable:validNum(v.memory_total_mb),
                temperature:validNum(v.temperature_c)?v.temperature_c:0,temperatureAvailable:validNum(v.temperature_c)})),
              gpuRaw:JSON.stringify(gpu)};
          }
          if(!Array.isArray(payload.processes))err("processes-missing");
          const values=unique(payload.processes.map(v=>{
            if(!plain(v)||!Number.isSafeInteger(v.pid) || typeof v.name!=="string")err("invalid-process");
            const createdAt=String(v.created_at||"");
            return {processKey:String(v.pid)+":"+(createdAt||"unknown"),pid:v.pid,
              name:v.name,status:String(v.status||"unknown"),memory:String(v.memory_human||"n/a"),
              createdAt,commandPreview:String(v.command_preview||""),memoryRss:Number.isFinite(v.memory_rss)?v.memory_rss:0,
              memoryAvailable:v.memory_availability==="present" || Number.isFinite(v.memory_rss),
              statusAvailable:v.status_availability==="present" || !!v.status,
              isMainComputer:v.is_main_computer===true,isCurrentProcess:v.is_current_process===true};
          }),"processKey");
          // Both process collections can authorize the operator's selected
          // identity, but only after a real generation-bearing observation.
          // Stale identities are discarded on each observation, never inferred
          // from a PID alone. The server still verifies the process birth time.
          if(req.intentId==="intent:refresh" || req.intentId==="intent:refresh-all"){
            observedProcesses.clear();for(const v of values)if(v.createdAt)observedProcesses.set(v.processKey,v);
          }
          return values;
        }
        case "intent:server-status":{
          const payload=await post("/api/applications/task/overview",{limit:16,query:"",include_all:false,include_connections:false});
          if(!plain(payload.server))err("server-status-missing");
          return JSON.stringify(payload.server);
        }
        case "intent:list-schedules":{
          const payload=await post("/api/applications/task/schedules",{});
          if(!Array.isArray(payload.schedules))err("schedules-missing");
          const values=unique(payload.schedules.map(v=>{
            if(!plain(v) || typeof v.id!=="string" || typeof v.action!=="string" ||
              typeof v.revision!=="string")err("invalid-schedule");
            return {id:v.id,action:v.action,runAt:String(v.run_at||""),note:String(v.note||""),revision:String(v.revision||"")};
          }),"id");
          observedSchedules.clear();for(const value of values)observedSchedules.set(value.id,value);
          return values;
        }
        case "intent:task-ai":{
          if(!a.instruction.trim() || a.instruction.length>2000)err("ai-instruction-required");
          const query=a.query.trim()||monitorQuery;
          const payload=await post("/api/applications/task/ai",{instruction:a.instruction,query,limit:monitoredLimit,
            include_all:a.view==="all-processes",include_connections:appId==="semantic-task-manager-generated-p3"?monitorConnections:(a.view==="connections" || a.view==="server-processes")});
          if(typeof payload.content!=="string" || payload.content.length>100000)err("ai-response-invalid");
          return payload.content;
        }
        case "intent:terminate-process":
        case "intent:kill-process":{
          const observed=observedProcesses.get(a.selected);
          if(!observed?.createdAt)err("process-generation-not-observed");
          const action=req.intentId==="intent:kill-process"?"kill_pid":"terminate_pid";
          const payload=await post("/api/applications/task/action",{
            action,pid:observed.pid,confirm:true,expected_created_at:observed.createdAt,mcel_semantic_guarded:true
          });
          if(payload.ok!==true || payload.planned===true || payload.action!==action ||
            payload.pid!==observed.pid || payload.verified_created_at!==observed.createdAt)err("process-receipt-invalid");
          observedProcesses.delete(a.selected);
          return p3Receipt(payload.deferred?"accepted-deferred":"command-returned",
            "Process "+action+" PID "+observed.pid+" ("+observed.createdAt+"): "+String(payload.message||""));
        }
        case "intent:start-server":
        case "intent:restart-server":
        case "intent:shutdown-server":{
          const action={"intent:start-server":"server_start","intent:restart-server":"server_restart",
            "intent:shutdown-server":"server_shutdown"}[req.intentId];
          const payload=await post("/api/applications/task/action",{action,confirm:true});
          if(payload.ok!==true || payload.planned===true || payload.action!==action)err("server-action-receipt-invalid");
          return p3Receipt(payload.deferred?"accepted-deferred":"command-returned",
            action+": "+String(payload.message||""));
        }
        case "intent:create-schedule":{
          const payload=await post("/api/applications/task/schedule/create",{
            action:a.action,run_at:a.runAt,note:a.note,payload:{}});
          if(payload.ok!==true || payload.schedule?.action!==a.action ||
            typeof payload.schedule?.id!=="string")err("schedule-create-receipt-invalid");
          observedSchedules.set(payload.schedule.id,{id:payload.schedule.id,action:a.action,
            runAt:String(payload.schedule.run_at||""),note:a.note,revision:String(payload.schedule.revision||"")});
          return p3Receipt("recorded","Schedule created: "+payload.schedule.id+" / "+a.action);
        }
        case "intent:delete-schedule":{
          if(!observedSchedules.has(a.scheduleId))err("schedule-not-observed");
          const payload=await post("/api/applications/task/schedule/delete",{schedule_id:a.scheduleId,
            expected_revision:observedSchedules.get(a.scheduleId)?.revision||""});
          if(payload.ok!==true || !Array.isArray(payload.schedules) ||
            payload.schedules.some(v=>v.id===a.scheduleId))err("schedule-delete-receipt-invalid");
          observedSchedules.delete(a.scheduleId);
          return p3Receipt("recorded","Schedule deleted: "+a.scheduleId);
        }
        case "intent:run-schedule":{
          const selected=observedSchedules.get(a.scheduleId);
          if(!selected || !["server_status","server_start","server_restart","server_shutdown"].includes(selected.action))
            err("scheduled-action-not-admitted");
          const requestId="manual_"+Date.now().toString(36)+"_"+Math.random().toString(36).slice(2,14);
          const payload=await post("/api/applications/task/schedule/run",{
            schedule_id:a.scheduleId,expected_revision:selected.revision,
            expected_action:selected.action,request_id:requestId,confirm:true});
          if(payload.ok!==true || payload.action!==selected.action ||
             payload.schedule_revision!==selected.revision || payload.manual_invocation!==true)
            err("schedule-run-receipt-invalid");
          return p3Receipt(String(payload.outcome||"unknown"),"Recorded action "+selected.action+
            " manually invoked: "+a.scheduleId,requestId);
        }
        case "intent:list-files":{
          const payload=await post("/api/applications/editor/files",{repo_dir:".",path:"",limit:500});
          if(!Array.isArray(payload.entries))err("files-missing");
          const values=unique(payload.entries.map(v=>{row(v,["path","name","kind"]);return {path:v.path,name:v.name,kind:v.kind};}),"path");
          knownEditorFiles=new Set(values.filter(v=>v.kind==="file").map(v=>v.path));
          knownEditorDirs=new Set(["",...values.filter(v=>v.kind==="dir").map(v=>v.path)]);
          return values;
        }
        case "intent:browse-directory":{
          const payload=await post("/api/applications/editor/files",{repo_dir:".",path:a.path,limit:500});
          if(!Array.isArray(payload.entries)||payload.path!==a.path)err("directory-response-mismatch");
          const values=unique(payload.entries.map(v=>{row(v,["path","name","kind"]);return {path:v.path,name:v.name,kind:v.kind};}),"path");
          knownEditorFiles=new Set(values.filter(v=>v.kind==="file").map(v=>v.path));
          knownEditorDirs=new Set(["",a.path,...values.filter(v=>v.kind==="dir").map(v=>v.path)]);
          return values;
        }
        case "intent:save-source":{
          const baseline=editorBaseline(a.path);
          if(!baseline || busyEditors.has(a.path))err("source-not-opened");
          busyEditors.add(a.path);bumpEditorEpoch(a.path);
          let payload;
          try{payload=await post("/api/applications/editor/project/file/save",{
            repo_dir:".",project_root:".",path:a.path,replacement_text:a.text,
            expected_before_sha256:baseline.hash,explicit_save:true,
            stale_source_checked:true,write_policy:"author-owned-source",validation_profile:"none"
          });}finally{busyEditors.delete(a.path);}
          const receipt=payload.receipt, entry=receipt?.files?.length===1 ? receipt.files[0] : null;
          const expectedAfter=await digest(a.text);
          if(payload.schema!=="mcel-code-editor-file-save-v1" || payload.state!=="applied" ||
            payload.status!=="pass" || !/^[a-f0-9]{32}$/.test(String(payload.handle||"")) ||
            payload.savedPath!==a.path || !Array.isArray(payload.changedFiles) ||
            payload.changedFiles.length!==1 || payload.changedFiles[0]!==a.path ||
            receipt?.ok!==true || receipt.rollback_required!==false ||
            !/^[a-f0-9]{24}$/.test(String(receipt.transaction_id||"")) ||
            entry?.path!==a.path || entry.operation!=="modify" ||
            entry.before_sha256!==baseline.hash || entry.after_sha256!==expectedAfter ||
            entry.written_sha256!==expectedAfter)err("save-receipt-invalid");
          openedEditors.set(a.path,{path:a.path,hash:expectedAfter,truncated:false});
          reviewedChanges.delete(a.path);return a.path;
        }
        case "intent:open-source":{
          if(!a.path)err("path-not-selected");
          const epoch=bumpEditorEpoch(a.path);
          const payload=await post("/api/applications/editor/read",{repo_dir:".",files:[a.path]});
          if(!Array.isArray(payload.files) || payload.files.length!==1 || payload.files[0]?.path!==a.path ||
             !requiredText(payload.files[0]?.content,200100))err("invalid-source-response");
          const content=payload.files[0].content;
          const truncated=content.includes("[truncated at 200000 characters]") || content.includes("\uFFFD");
          const hash=truncated||!global.crypto?.subtle?null:await digest(content);
          // Only the newest read for this exact document can update its authority.
          if(editorReadEpochs.get(a.path)===epoch){
            openedEditors.set(a.path,{path:a.path,hash,truncated});reviewedChanges.delete(a.path);
          }
          previouslyOpenedEditorFiles.add(a.path);
          return content;
        }
        case "intent:aider-context":{
          const payload=await get("/api/applications/aider/context");
          return readAiderStatus(payload);
        }
        case "intent:aider-jobs":{
          const payload=await get("/api/applications/aider/jobs");
          if(!Array.isArray(payload.activities))err("aider-jobs-invalid");
          return JSON.stringify({activities:payload.activities});
        }
        case "intent:run-aider":{
          const files=selectedAiderFiles(a.path,a.files);
          if(!files || !a.instruction.trim())err("aider-fileset-not-authorized");
          const baseline=new Map(files.map(f=>[f,editorBaseline(f)]));
          for(const file of files)busyEditors.add(file);
          try{
            // Aider is a separate source-mutating workflow; recheck each opened
            // baseline immediately before submitting the explicit job.
            const current=await post("/api/applications/editor/read",{repo_dir:".",files});
            if(!Array.isArray(current.files) || current.files.length!==files.length)
              err("aider-source-revalidation-incomplete");
            for(const file of files){
              const entry=current.files.find(v=>v.path===file);
              if(!entry || !requiredText(entry.content,200100) ||
                entry.content.includes("[truncated at 200000 characters]") ||
                await digest(entry.content)!==baseline.get(file)?.hash)err("aider-source-stale");
            }
            const result=await post("/api/applications/aider/run",{
              repo_dir:".",instruction:a.instruction,files,dry_run:false,
              timeout_seconds:120,fallback:false
            });
            if(result.ok!==true || result.accepted!==true || !plain(result.job) ||
              typeof result.job.id!=="string" || !result.job.id)err("aider-job-receipt-invalid");
            // Accepted does not mean completed. Every touched document must be
            // re-read after the asynchronous job before further source writes.
            for(const file of files){bumpEditorEpoch(file);openedEditors.delete(file);reviewedChanges.delete(file);}
            return "Aider job accepted (not completed): "+result.job.id+"; files: "+files.join(", ");
          }finally{for(const file of files)busyEditors.delete(file);}
        }
        case "intent:archive-aider-context":
        case "intent:reset-aider-context":{
          if(!editorBaseline(a.path)?.hash)err("aider-context-source-not-authorized");
          const url=req.intentId==="intent:archive-aider-context"?
            "/api/applications/aider/context/archive":"/api/applications/aider/context/reset";
          const result=await post(url,{repo_dir:".",files:[a.path]});
          if(result.ok!==true || !plain(result.active) || !Array.isArray(result.archives))err("aider-context-receipt-invalid");
          observedAiderArchives.clear();
          return "Aider context "+(req.intentId==="intent:archive-aider-context"?"archived":"reset")+
            ": "+String(result.active.id||"");
        }
        case "intent:load-aider-archive":{
          if(!observedAiderArchives.has(a.archiveId) || observedAiderArchives.get(a.archiveId)!==aiderContextRepo)
            err("aider-archive-not-observed-in-workspace");
          const result=await post("/api/applications/aider/context/load",{archive_id:a.archiveId});
          if(result.ok!==true || !plain(result.active) || !Array.isArray(result.archives))err("aider-load-receipt-invalid");
          observedAiderArchives.clear();
          return "Aider context loaded: "+a.archiveId;
        }
        case "intent:preview-aider-plan":{
          if(!editorBaseline(a.path)?.hash || !a.instruction.trim())err("aider-preview-file-required");
          const payload=await post("/api/applications/aider/prepare",{
            repo_dir:".",files:[a.path],instruction:a.instruction,
            dry_run:true,timeout_seconds:120,fallback:false
          });
          if(payload.ok!==true || !Array.isArray(payload.command))err("aider-preview-invalid");
          return "Aider dry-run command: "+payload.command.map(v=>String(v)).join(" ");
        }
        case "intent:prepare-reviewed-change":{
          const baseline=editorBaseline(a.path);
          if(!baseline || baseline.truncated || baseline.hash===null)err("source-not-open-for-review");
          if(a.text.length>2000000)err("review-too-large");
          const after=await digest(a.text);
          if(after===baseline.hash)err("review-has-no-change");
          const payload=await post("/api/applications/editor/project/transaction/prepare",{
            repo_dir:".",project_root:".",changes:[{operation:"modify",path:a.path,
              expected_before_sha256:baseline.hash,replacement_text:a.text}],validation_profile:"none"
          });
          if(payload.schema!=="mcel-code-editor-project-transaction-v1" || payload.state!=="prepared" ||
             !/^[a-f0-9]{32}$/.test(String(payload.handle||"")) || !plain(payload.transaction))err("review-prepare-invalid");
          if(editorBaseline(a.path)!==baseline)err("review-source-superseded");
          const review={handle:payload.handle,path:a.path,before:baseline.hash,after,
            details:"Review proposed modification to "+a.path+". Before SHA-256: "+baseline.hash+
              "; after SHA-256: "+after+". Server transaction: "+String(payload.transaction.transaction_id||"")};
          reviewedChanges.set(a.path,{review,text:a.text,path:a.path,hash:baseline.hash});
          return review;
        }
        case "intent:apply-reviewed-change":{
          const prepared=reviewedChanges.get(a.path);
          if(a.path!==a.review.path || !prepared || busyEditors.has(prepared.path))err("review-not-prepared");
          busyEditors.add(prepared.path);bumpEditorEpoch(prepared.path);
          let payload;
          try{payload=await post("/api/applications/editor/project/transaction/apply",{
            repo_dir:".",handle:prepared.review.handle,reviewed:true,approved:true,
            require_project_manifest:false
          });}finally{busyEditors.delete(prepared.path);}
          const receipt=payload.receipt, entry=receipt?.files?.length===1?receipt.files[0]:null;
          if(payload.schema!=="mcel-code-editor-project-transaction-v1" || payload.state!=="applied" ||
             payload.handle!==prepared.review.handle || receipt?.ok!==true || receipt.rollback_required!==false ||
             !Array.isArray(payload.changedFiles) || payload.changedFiles.length!==1 ||
             payload.changedFiles[0]!==prepared.path ||
             entry?.path!==prepared.path || entry.operation!=="modify" ||
             entry.before_sha256!==prepared.review.before ||
             entry.after_sha256!==prepared.review.after || entry.written_sha256!==prepared.review.after)err("review-apply-receipt-invalid");
          openedEditors.set(prepared.path,{path:prepared.path,hash:prepared.review.after,truncated:false});
          reviewedChanges.delete(prepared.path);
          return "Applied reviewed change: "+prepared.path;
        }
        default:err("unknown-operation");
      }
    }
    return Object.freeze({authorize:async req=>authorized(req),invoke,
      closeDocument:path=>{
        if(typeof path==="string"){
          bumpEditorEpoch(path);openedEditors.delete(path);reviewedChanges.delete(path);
          previouslyOpenedEditorFiles.delete(path);
        }else{for(const path of openedEditors.keys())bumpEditorEpoch(path);
          openedEditors.clear();reviewedChanges.clear();previouslyOpenedEditorFiles.clear();}
      }});
  }
  global.McelSemanticReadPorts=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
