/* Opt-in local parity recorder. Records operation shape and outcome, never content,
 * prompts, raw paths, request/response bodies, process identifiers or credentials.
 * Shares the MCELDiagnosticPlugins lifecycle with the onHover recorder.
 */
(function (global) {
  "use strict";
  const plugins = global.MCELDiagnosticPlugins;
  if (!plugins || global.MCELAppParityTrace) return;
  const schema = "mcel.app-parity-trace.v1";
  const events = [];
  let running = false, startedAt = null, dropped = 0, cap = 6000;
  const base = global.performance?.now?.() || 0;
  const now = () => Math.round(((global.performance?.now?.() || 0) - base) * 10) / 10;
  const limited = (x, max = 100) => String(x == null ? "" : x).slice(0, max);
  const safeId = x => limited(x, 100).replace(/[^a-zA-Z0-9_.:\-]/g, "_");
  function app() {
    const route = global.location?.pathname || "";
    if (route.includes("mcel-semantic-code-editor")) return {app:"code-editor",variant:"semantic"};
    if (route.includes("mcel-semantic-file-manager")) return {app:"file-manager",variant:"semantic"};
    if (route.includes("mcel-semantic-task-manager")) return {app:"task-manager",variant:"semantic"};
    const candidate = global.document?.querySelector?.("#mcel-workbench-ir");
    if (candidate) {
      try {
        const id = JSON.parse(candidate.textContent).application.appId;
        if (/^semantic-(code-editor|file-manager|task-manager)$/.test(id))
          return {app:id.substring(9),variant:"semantic"};
      } catch (_) { /* A failed candidate still produces startup errors. */ }
    }
    if (route.includes("code-editor")) return {app:"code-editor",variant:"production"};
    if (route.includes("file-explorer") || route.includes("file-manager")) return {app:"file-manager",variant:"production"};
    if (route.includes("task-manager")) return {app:"task-manager",variant:"production"};
    return {app:"unknown",variant:"production"};
  }
  function keep(kind, shape = {}) {
    if (!running) return null;
    const identity = app();
    const entry = {sequence: events.length + dropped + 1, ms:now(),kind,app:identity.app,
      variant:identity.variant,...shape};
    events.push(entry);
    if (events.length > cap) {const n=events.length-cap;events.splice(0,n);dropped+=n;}
    return entry;
  }
  function routeOf(input) {
    try {
      const target = typeof input === "string" || input instanceof URL ? input : input?.url;
      const origin = global.location?.origin;
      const url = new URL(target, origin && origin !== "null" ? origin : "http://127.0.0.1/");
      // API path only, never query string (which can contain search terms or tokens).
      const path = url.pathname;
      return (/^\/api\/applications\/(?:aider|editor|file-explorer|task)(?:\/|$)/.test(path) ||
        path==="/api/heartbeat/control") ? path : "";
    } catch (_) {return "";}
  }
  function localResourcePath(source) {
    try {
      const url=new URL(source,global.location?.href||"http://127.0.0.1/");
      if (url.origin!==global.location?.origin) return "external-resource";
      return /^[\/a-zA-Z0-9_.-]{1,150}$/.test(url.pathname) ? url.pathname : "redacted-resource";
    } catch (_) {return "redacted-resource";}
  }
  function boundedTarget(el) {
    const target = el?.closest?.("button,select,input,textarea,[role=tab],[data-record-key]") || el;
    if (!target?.getAttribute) return "unknown";
    const candidate = target.getAttribute("data-intent") || target.id ||
      target.getAttribute("data-mcel-document-save") && "document-save" ||
      target.getAttribute("data-mcel-document-close") && "document-close" ||
      target.getAttribute("data-mcel-document-discard") && "document-discard" ||
      target.getAttribute("data-code-editor-document-close") && "document-tab-close" ||
      target.getAttribute("data-code-editor-document-activate") && "document-tab-switch" ||
      target.getAttribute("data-mcel-node") || target.getAttribute("name") ||
      target.getAttribute("role") || target.tagName || "unknown";
    // Do not record arbitrary data-keys, data-paths, HTML content or field values.
    return safeId(candidate);
  }
  function counts(root, selector) {return root?.querySelectorAll?.(selector)?.length || 0;}
  function snapshot() {
    const d=global.document;
    if (!d) return null;
    const who=app();
    if (who.app==="code-editor" && who.variant==="production") {
      const root=d.querySelector("#code-editor-app");
      return {app:who.app,variant:who.variant,rootPresent:!!root,
        documentOpen:!!root?.querySelector?.('[data-code-editor-document-tabs] [aria-selected="true"]'),
        tabs:counts(root,'[data-code-editor-document-tabs] [role="tab"]'),
        dirtyTabs:counts(root,'[data-code-editor-document-tabs] button[title*="(unsaved)"]'),
        monacoPresent:!!root?.querySelector?.('.monaco-editor'),
        monacoHostPresent:!!root?.querySelector?.('#code-studio-runtime-monaco')};
    }
    if (who.app==="code-editor" && who.variant==="semantic") {
      const root=d.querySelector("#mcel-workbench");
      return {app:who.app,variant:who.variant,rootPresent:!!root,
        documentOpen:!!root?.querySelector?.("[data-mcel-document-label]") &&
          !["No document opened","Select a source file and open it"].includes(
            root.querySelector("[data-mcel-document-label]").textContent),
        tabs:counts(root,'[role="tab"]'),
        dirtyTabs:counts(root,'[data-mcel-tab-path]') ?
          [...root.querySelectorAll('[data-mcel-tab-path]')].filter(el=>el.textContent.endsWith(' *') || el.textContent.endsWith(' * !')).length : 0,
        documentSelected:!!global.__mcelWorkbench?.app?.readState?.("state:selected-path"),
        monacoPresent:!!root?.querySelector?.('.monaco-editor'),
        monacoHostPresent:!!root?.querySelector?.('[data-mcel-document-surface]'),
        dirty:counts(root,'[data-mcel-document-dirty="true"]')>0};
    }
    if (who.app==="file-manager") {
      const root=d.querySelector(who.variant==="semantic"?"#mcel-workbench":"#system-file-explorer-app, #file-explorer-app");
      return {app:who.app,variant:who.variant,rootPresent:!!root,
        visibleEntries:counts(root,'[data-record-key], [data-file-explorer-entry-path], [role="treeitem"]'),
        expandedNodes:counts(root,'[aria-expanded="true"]')};
    }
    if (who.app==="task-manager") {
      const root=d.querySelector(who.variant==="semantic"?"#mcel-workbench":"#task-manager-app");
      return {app:who.app,variant:who.variant,rootPresent:!!root,
        visibleRows:counts(root,'[data-record-key], tr[data-pid], [data-process-key]'),
        visibleTabs:counts(root,'[role="tab"]'),
        autoRefreshEnabled:who.variant==="semantic" ? root?.querySelector('[data-mcel-refresh-toggle]')?.getAttribute('aria-pressed')==="true" : null};
    }
    return {app:who.app,variant:who.variant};
  }
  function exportTrace() {
    const summary={};
    for (const event of events) summary[event.kind]=(summary[event.kind]||0)+1;
    return {schema,localOnly:true,recording:running,startedAt,exportedAt:new Date().toISOString(),
      identity:app(),dropped,eventCounts:summary,events:events.slice(),finalState:snapshot()};
  }
  function download() {
    const data=JSON.stringify(exportTrace(),null,2);
    const blob=new Blob([data],{type:"application/json"});
    const url=URL.createObjectURL(blob);
    const link=global.document.createElement("a");
    link.href=url;
    const identity=app();
    link.download="mcel-parity-"+identity.app+"-"+identity.variant+".json";
    global.document.body.appendChild(link);
    link.click();
    link.remove();
    global.setTimeout(() => URL.revokeObjectURL(url), 1000);
    return link.download;
  }
  const api = Object.freeze({schema,exportTrace,download,clear:()=>{events.length=0;dropped=0;},
    status:()=>({recording:running,app:app(),eventCount:events.length,dropped})});
  global.MCELAppParityTrace=api;
  plugins.register({id:"mcel-app-parity",kind:"recorder",
    description:"Opt-in local operation and UI parity traces for production and semantic MCEL apps; content-free",
    availability:()=>"ready",
    start(ctx) {
      running=true;startedAt=new Date().toISOString();
      cap=Math.max(100,Math.min(30000,Number(ctx.config.maxEvents)||6000));
      let request=0,lastState="",lastInput=null,lastInputAt=-Infinity;
      const recordState=()=>{
        const state=snapshot(),serialized=JSON.stringify(state);
        if(serialized && serialized!==lastState){lastState=serialized;keep("ui-state",{state});}
      };
      const onAction=(event)=>{
        const el=event.target;
        if (!el?.closest?.("#mcel-workbench,#code-editor-app,#system-file-explorer-app,#file-explorer-app,#task-manager-app"))return;
        const target=boundedTarget(el);
        if(event.type==="input"){
          const t=now();if(lastInput===target && t-lastInputAt<800)return;
          lastInput=target;lastInputAt=t;
        }
        keep("ui-action",{action:event.type,target});
        global.queueMicrotask?.(recordState);
      };
      const onOperation=(event)=>{
        const detail=event.detail || {};
        keep("intent",{intent:safeId(detail.intentId||detail.intentName||"unknown"),
          phase:safeId(detail.phase||"unknown"),disposition:safeId(detail.disposition||""),
          // Error reasons may embed source paths or command payloads; only a category is safe.
          error:detail.disposition==="failed"||detail.disposition==="refused-before-attempt"});
        recordState();
      };
      const onTransport=event=>{
        const d=event.detail||{};
        const endpoint=routeOf(d.endpoint);
        if(!endpoint)return;
        const shape={requestId:Number(d.requestId)||0,endpoint};
        if(d.phase==="request")keep("backend-request",{...shape,method:safeId(d.method||"POST")});
        if(d.phase==="response")keep("backend-response",{...shape,
          status:Number(d.status)||0,outcome:safeId(d.outcome||"unknown"),
          durationMs:Number(d.durationMs)||0});
        recordState();
      };
      const onError=event=>{
        if (event instanceof ErrorEvent) keep("browser-error",{name:safeId(event.error?.name||"Error"),
          source:localResourcePath(event.filename||"")});
        else keep("resource-error",{source:localResourcePath(event.target?.src||event.target?.href||"")});
      };
      const onRejection=event=>keep("browser-rejection",{name:safeId(event.reason?.name||"unknown")});
      const original=global.fetch;
      const wrapped=typeof original==="function"?function(...args){
        const endpoint=routeOf(args[0]);
        // Semantic capability ports emit their own request/response lifecycle.
        // Avoid duplicate observations if the port captured the wrapped fetch.
        if(!endpoint || app().variant==="semantic")return original.apply(this,args);
        const id=++request,method=safeId(args[1]?.method||args[0]?.method||"GET");
        const start=now();
        keep("backend-request",{requestId:id,endpoint,method});
        let result;
        try {result=original.apply(this,args);}catch(e){keep("backend-response",{requestId:id,endpoint,
          durationMs:now()-start,outcome:"threw",errorName:safeId(e?.name||"Error")});throw e;}
        return Promise.resolve(result).then(response=>{
          keep("backend-response",{requestId:id,endpoint,durationMs:now()-start,
            outcome:response?.ok===true?"http-ok":"http-failed",status:Number(response?.status)||0});
          recordState();return response;
        },e=>{keep("backend-response",{requestId:id,endpoint,durationMs:now()-start,
          outcome:"rejected",errorName:safeId(e?.name||"Error")});throw e;});
      }:null;
      if(wrapped)global.fetch=wrapped;
      global.document.addEventListener("click",onAction,true);
      global.document.addEventListener("change",onAction,true);
      global.document.addEventListener("input",onAction,true);
      global.document.addEventListener("mcel:parity-operation",onOperation,true);
      global.document.addEventListener("mcel:host-bound-intent",onOperation,true);
      global.document.addEventListener("mcel:parity-transport",onTransport,true);
      global.window?.addEventListener?.("error",onError,true);
      global.window?.addEventListener?.("unhandledrejection",onRejection);
      const timer=global.setInterval(recordState,1000);
      recordState();
      keep("trace-start",{feature:"local-parity-recorder"});
      return {stop(){
        if(wrapped && global.fetch===wrapped)global.fetch=original;
        global.clearInterval(timer);
        global.document.removeEventListener("click",onAction,true);
        global.document.removeEventListener("change",onAction,true);
        global.document.removeEventListener("input",onAction,true);
        global.document.removeEventListener("mcel:parity-operation",onOperation,true);
        global.document.removeEventListener("mcel:host-bound-intent",onOperation,true);
        global.document.removeEventListener("mcel:parity-transport",onTransport,true);
        global.window?.removeEventListener?.("error",onError,true);
        global.window?.removeEventListener?.("unhandledrejection",onRejection);
        keep("trace-stop",{});running=false;
      }};
    }});
  if(new URLSearchParams(global.location?.search||"").get("mcel_trace")==="1"){
    plugins.enable("mcel-app-parity").catch(()=>{});
  }
})(typeof window !== "undefined" ? window : globalThis);
