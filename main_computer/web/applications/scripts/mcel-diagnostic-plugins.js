/* Optional, independent diagnostic integrations for the Applications host.
 * No remote scripts or telemetry are loaded automatically.
 * APIs: window.MCELDiagnosticPlugins.list/enable/disable/run/findings/configure
 */
(() => {
  "use strict";
  if (window.MCELDiagnosticPlugins) return;
  const definitions = new Map();
  const running = new Map();
  const configs = new Map();
  const findings = [];
  const MAX_FINDINGS = 300;
  const startup = {armedAt: new Date().toISOString(), states: {}, ready: null};
  const normalizeError = (error) => String(error && (error.stack || error.message) || error).slice(0, 1200);
  function emit(id, code, severity, detail, extra = {}) {
    const item = {pluginId:id, code, severity, detail:String(detail).slice(0, 2000), timestamp:new Date().toISOString(), ...extra};
    findings.push(item);
    if (findings.length > MAX_FINDINGS) findings.splice(0, findings.length - MAX_FINDINGS);
    window.dispatchEvent(new CustomEvent("mcel-diagnostic-plugin-finding", {detail:item}));
    return item;
  }
  function register(spec) {
    if (!spec || !spec.id || definitions.has(spec.id)) throw new Error("Duplicate/invalid MCEL diagnostics plugin");
    definitions.set(spec.id, Object.freeze(spec));
  }
  function definition(id) {
    const item = definitions.get(id);
    if (!item) throw new Error("Unknown diagnostic plugin: " + id);
    return item;
  }
  function context(id) { return {id, config:configs.get(id) || {}, emit:(code, severity, detail, extra) => emit(id,code,severity,detail,extra)}; }
  const pendingStarts = new Map();
  async function enableInternal(id) {
    if (pendingStarts.has(id)) return pendingStarts.get(id);
    const operation = startPlugin(id);
    pendingStarts.set(id, operation);
    try { return await operation; }
    finally { pendingStarts.delete(id); }
  }
  async function startPlugin(id) {
    const spec = definition(id);
    if (running.has(id)) return {id,status:"enabled"};
    try {
      const state = spec.start ? await spec.start(context(id)) : null;
      running.set(id, state || {stop:() => {}});
      return {id,status:"enabled"};
    } catch (error) {
      emit(id,"plugin-start-failed","warning",normalizeError(error));
      return {id,status:"unavailable",reason:normalizeError(error)};
    }
  }
  function ensureStarted() {
    // Boot begins as this script loads, before Applications runtime.
    // All asynchronous public operations join the same boot promise.
    return startup.ready || Promise.resolve();
  }
  async function enable(id) {
    await ensureStarted();
    return enableInternal(id);
  }
  async function disable(id) {
    await ensureStarted();
    definition(id);
    if (!running.has(id)) return {id,status:"disabled"};
    const state = running.get(id);
    running.delete(id);
    try { if (state && typeof state.stop === "function") await state.stop(); }
    catch (error) { emit(id,"plugin-stop-failed","warning",normalizeError(error)); }
    return {id,status:"disabled"};
  }
  async function run(id) {
    await ensureStarted();
    const spec = definition(id);
    if (!spec.run) return {id,status:"not-an-audit"};
    try { return {id,status:"complete",result:await spec.run(context(id))}; }
    catch (error) {
      emit(id,"plugin-run-failed","warning",normalizeError(error));
      return {id,status:"unavailable",reason:normalizeError(error)};
    }
  }
  function configure(id, config) {
    definition(id);
    if (running.has(id)) throw new Error("Disable plugin before reconfiguring: " + id);
    if (!config || typeof config !== "object" || Array.isArray(config)) throw new TypeError("Config must be an object");
    configs.set(id, {...config});
    return {id,status:"configured"};
  }
  function list() {
    return [...definitions.values()].map(({id,kind,description,availability}) => ({
      id,kind,description,enabled:running.has(id),
      availability:typeof availability === "function" ? availability(context(id)) : "requires-configuration"
    }));
  }
  function collectFindings() {
    // Include errors generated after the early collector was initially drained.
    return findings.concat(window.__MCELDiagnosticEarly?.events() || []);
  }
  window.MCELDiagnosticPlugins = {register,enable,disable,run,configure,list,findings:collectFindings,
    status:() => ({armedAt:startup.armedAt, states:{...startup.states},
      earlyEvents:window.__MCELDiagnosticEarly?.events().length || 0}),
    whenReady:ensureStarted,
    clear:() => {findings.length=0;window.__MCELDiagnosticEarly?.drain();}};

  register({id:"axe-core",kind:"audit",description:"Accessibility audit of current document",availability:() => window.axe?.run ? "ready" : "requires-local-axe-core-script",
    async run(ctx) {
      if (!window.axe?.run) throw new Error("axe-core not loaded; bundle its local browser script before enabling");
      const result = await window.axe.run(document, ctx.config.options || {});
      for (const violation of result.violations) ctx.emit("axe:"+violation.id,"error",violation.help,{targetCount:violation.nodes.length,impact:violation.impact});
      return {violations:result.violations.length,incomplete:result.incomplete.length,passes:result.passes.length};
    }});

  // An opt-in, local-only recorder for MCEL hover/scroll ownership decisions.
  // Instrumentation is independent of rrweb, Sentry and hosted analytics. Nothing
  // is transmitted; applications may export the buffer explicitly as JSON.
  const hoverCapture = {events:[],running:false,scope:"#mcel-lifecycle-acid",limit:10000,startedAt:null,dropped:0};
  const hoverNow = () => Math.round(performance.now()*10)/10;
  function hoverExport() {
    // Summarize the recorded facts without interpreting why the browser moved.
    const counts={}, alerts=[];
    let maxRailGap=0,maxUnexpectedMotion=0,maxFrameGap=0;
    for(const e of hoverCapture.events){
      counts[e.kind]=(counts[e.kind]||0)+1;
      if(e.kind==='geometry-invariant-warning' || e.kind==='geometry-delta'){
        if(Number.isFinite(e.railGap))maxRailGap=Math.max(maxRailGap,Math.abs(e.railGap));
        if(e.kind==='geometry-invariant-warning')alerts.push({ms:e.ms,kind:e.kind,reason:e.reason,rowId:e.rowId});
        if(e.kind==='geometry-delta')maxUnexpectedMotion=Math.max(maxUnexpectedMotion,Math.abs(e.unexplainedRowDx||0),Math.abs(e.unexplainedRowDy||0));
      }
      if(e.kind==='frame-gap')maxFrameGap=Math.max(maxFrameGap,e.gapMs||0);
    }
    return {schema:"mcel.hover-pointer-trace.v2",localOnly:true,recording:hoverCapture.running,
      scope:hoverCapture.scope,startedAt:hoverCapture.startedAt,exportedAt:new Date().toISOString(),
      viewport:{width:innerWidth,height:innerHeight,devicePixelRatio:devicePixelRatio},
      summary:{eventCounts:counts,discardedOldEvents:hoverCapture.dropped,maxRailGap,maxUnexpectedMotion,maxFrameGap,
        invariantAlerts:alerts.slice(-100)},
      events:hoverCapture.events.slice(),
      performanceFindings:window.MCELDiagnosticPlugins.findings().filter(e=>e.pluginId==='chrome-performance').slice(-100),
      plugins:window.MCELDiagnosticPlugins.list().filter(x=>["mcel-hover-pointer","rrweb","chrome-performance","sentry"].includes(x.id))};
  }
  window.MCELDiagnosticPlugins.exportHoverTrace = hoverExport;
  window.MCELDiagnosticPlugins.clearHoverTrace = () => {hoverCapture.events.length=0;hoverCapture.dropped=0;hoverCapture.startedAt=null;};
  register({id:"mcel-hover-pointer",kind:"recorder",description:"Local cursor, hit-test, scroll owner and MCEL hover decision trace; opt-in only",
    availability:()=>"ready",
    start(ctx) {
      const selector=typeof ctx.config.scope==="string" && ctx.config.scope.startsWith("#mcel-")?
        ctx.config.scope:"#mcel-lifecycle-acid";
      const root=document.querySelector(selector);
      if(!root)throw new Error("Hover diagnostic scope not mounted: "+selector);
      hoverCapture.running=true;hoverCapture.scope=selector;
      hoverCapture.startedAt=new Date().toISOString();hoverCapture.dropped=0;
      hoverCapture.limit=Math.max(100,Math.min(30000,Number(ctx.config.maxEvents)||10000));
      const keep=(kind,data={})=>{
        const item={ms:hoverNow(),kind,...data};hoverCapture.events.push(item);
        if(hoverCapture.events.length>hoverCapture.limit){
          const removed=hoverCapture.events.length-hoverCapture.limit;
          hoverCapture.events.splice(0,removed);hoverCapture.dropped+=removed;
        }
      };
      const geom=element=>{
        if(!element?.isConnected)return null;
        const r=element.getBoundingClientRect();
        return {x:+r.x.toFixed(2),y:+r.y.toFixed(2),w:+r.width.toFixed(2),h:+r.height.toFixed(2)};
      };
      const elementName=el=>el?.closest?.('mcel-row')?.dataset.rowId||
        el?.closest?.('mcel-collection')?.localName||el?.localName||null;
      let lastPoint=null,lastSample=-Infinity,frame=0,lastFrameTime=0,lastPaint=0;
      let lastGeom=null,baseline=null,lastBoundary=null,lastRailWarning=0,lastHeartbeat=0,lastBadField=null,lastBadFieldAt=0;
      const scrollHost=()=>activeRow()?.closest('mcel-collection')||
        root.querySelector('[data-case-id="L18-01"] mcel-collection');
      const activeRow=()=>root.querySelector('mcel-row:popover-open');
      const activeRail=()=>document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
      const scrollFacts=()=>{
        const row=activeRow(),list=scrollHost(),page=document.scrollingElement;
        const rail=activeRail(),anchor=row?.mcelPopoverAnchor||null;
        const rr=row?.getBoundingClientRect(),tr=rail?.getBoundingClientRect();
        const gapRight=rr&&tr?tr.left-rr.right:null;
        const gapLeft=rr&&tr?rr.left-tr.right:null;
        const railSide=gapRight===null?null:Math.abs(gapRight)<=Math.abs(gapLeft)?'right':'left';
        return {rowId:row?.dataset.rowId||null,row:geom(row),rail:geom(rail),anchor:geom(anchor),list:geom(list),
          railHidden:!!rail?.hidden,rowScrollTop:row?.scrollTop??null,
          rowScrollHeight:row?.scrollHeight??null,rowClientHeight:row?.clientHeight??null,
          listScrollTop:list?.scrollTop??null,listScrollHeight:list?.scrollHeight??null,
          pageScrollTop:page?.scrollTop??null,
          railSide,railGap:row&&rail&&!rail.hidden?
            +((railSide==='right'?gapRight:gapLeft)).toFixed(2):null};
      };
      const inBox=(r,p)=>!!(r&&p&&p.x>=r.x&&p.x<=r.x+r.w&&p.y>=r.y&&p.y<=r.y+r.h);
      const recordPointer=ev=>{
        const hit=document.elementFromPoint(ev.clientX,ev.clientY);
        const row=activeRow();
        const inside=root.contains(hit)||hit?.closest?.('.mcel-unclip-row-scroll-track');
        if(!inside&&!row)return;
        const x=+ev.clientX.toFixed(1),y=+ev.clientY.toFixed(1);
        const id=elementName(hit),last=lastPoint;
        if(ev.type==='pointermove'&&last&&hoverNow()-lastSample<40 &&
           id===last.target && Math.abs(x-last.x)<6 && Math.abs(y-last.y)<6)return;
        lastSample=hoverNow();lastPoint={x,y,target:id};
        const f=scrollFacts();
        const boundary=inBox(f.row,lastPoint)?'row':inBox(f.rail,lastPoint)&&!f.railHidden?'rail':'outside';
        if(boundary!==lastBoundary){keep('pointer-hit-region-change',{from:lastBoundary,to:boundary,
          pointer:lastPoint,rowId:f.rowId,railGap:f.railGap,hit:id});lastBoundary=boundary;}
        keep('browser-'+ev.type,{x,y,isTrusted:ev.isTrusted,pointerType:ev.pointerType,
          hit:id,target:elementName(ev.target),activeRowId:f.rowId,
          row:f.row,rail:f.rail,anchor:f.anchor,list:f.list,
          region:boundary,rowScrollTop:f.rowScrollTop,listScrollTop:f.listScrollTop});
      };
      const recordScroll=ev=>{
        const el=ev.target;
        if(!root.contains(el)&&el!==document&&el!==document.scrollingElement)return;
        if(!['mcel-row','mcel-collection'].includes(el.localName) && el!==document && el!==document.scrollingElement)return;
        keep('browser-scroll',{owner:el.localName||'#document',rowId:el.dataset?.rowId||null,
          top:el.scrollTop??null,clientHeight:el.clientHeight??null,scrollHeight:el.scrollHeight??null,
          ...scrollFacts()});
      };
      let wheelSeq=0;
      const recordWheel=ev=>{
        const row=activeRow(),list=scrollHost(),rail=activeRail();
        if(!row&&!root.contains(ev.target))return;
        if(!row&&!list?.contains(ev.target))return;
        const seq=++wheelSeq;
        const before=scrollFacts();
        const pixels=ev.deltaY*(ev.deltaMode===1?16:ev.deltaMode===2?(row?.clientHeight||innerHeight):1);
        keep('wheel-before',{seq,deltaY:ev.deltaY,deltaX:ev.deltaX,deltaMode:ev.deltaMode,
          normalizedDeltaY:pixels,cancelable:ev.cancelable,isTrusted:ev.isTrusted,
          target:elementName(ev.target),point:{x:ev.clientX,y:ev.clientY},...before});
        // A post-default rAF reveals the real scroll offsets after host arbitration.
        requestAnimationFrame(()=>{if(!hoverCapture.running)return;
          const after=scrollFacts();keep('wheel-after',{seq,...after,
            rowDelta:(after.rowScrollTop??0)-(before.rowScrollTop??0),
            listDelta:(after.listScrollTop??0)-(before.listScrollTop??0),
            pageDelta:(after.pageScrollTop??0)-(before.pageScrollTop??0)});
        });
      };
      const recordNativeToggle=ev=>{
        const el=ev.target;
        if(el?.localName==='mcel-row'&&root.contains(el)){
          keep('browser-popover-toggle',{rowId:el.dataset.rowId||null,
            oldState:ev.oldState||null,newState:ev.newState||null,geometry:geom(el),...scrollFacts()});
        }
      };
      const recordHoverDecision=ev=>{
        const d=ev.detail||{};
        if(d.scope!=="mcel-row-hover")return;
        if(d.source && !root.contains(d.source))return;
        keep('host-'+(d.kind||'event'),{rowId:d.rowId||null,reason:d.reason||null,
          pointer:d.pointer||null,geometry:d.geometry||null,
          scrollOwner:d.scrollOwner||null,open:d.open??null,extra:d.extra||null,
          observed:scrollFacts()});
      };
      const near=(a,b)=>a!=null&&b!=null&&Math.abs(a-b)<.75;
      const frameStep=(time)=>{
        if(!hoverCapture.running)return;
        if(lastFrameTime && time-lastFrameTime>90){keep('frame-gap',{gapMs:+(time-lastFrameTime).toFixed(1),documentHidden:document.hidden});}
        lastFrameTime=time;
        // Sample at most ten times per second while the original row is open.
        // Emit meaningful deltas plus a 4 Hz heartbeat rather than 60 events/s.
        // This catches short jumps without flooding the bounded trace.
        if(time-lastPaint>=100){
          lastPaint=time;
          const f=scrollFacts(),row=activeRow();
          if(row){
            if(!baseline||baseline.rowId!==f.rowId){
              baseline={rowId:f.rowId,scrollTop:f.rowScrollTop,fields:[...row.children].filter(x=>x.localName==='mcel-field').slice(0,6)
                .map(x=>{const b=geom(x);return b?{el:x,x:b.x-f.row.x,y:b.y-f.row.y,w:b.w,h:b.h}:null;}).filter(Boolean)};
              keep('geometry-baseline',{rowId:f.rowId,fieldCount:baseline.fields.length,...f});
            }
            const fieldDrift=baseline.fields.map(b=>{
              const r=geom(b.el);return r?{dx:+(r.x-f.row.x-b.x).toFixed(2),dy:+(r.y-f.row.y-b.y).toFixed(2)}:null;
            });
            // Changing the row scrollTop legitimately moves normal fields and
            // may hold sticky fields in place. Only judge original-field layout
            // continuity at the exact baseline scroll offset.
            const comparableScroll=Math.abs((f.rowScrollTop||0)-(baseline.scrollTop||0))<.5;
            const badField=comparableScroll&&fieldDrift.some(v=>v&&(Math.abs(v.dx)>.75||Math.abs(v.dy)>.75));
            if(badField){
              const fingerprint=JSON.stringify(fieldDrift);
              if(lastBadField!==fingerprint||time-lastBadFieldAt>500){
                keep('original-fields-shifted',{rowId:f.rowId,fieldDrift,...f});
                lastBadField=fingerprint;lastBadFieldAt=time;
              }
            }else if(comparableScroll&&lastBadField){keep('original-fields-restored',{rowId:f.rowId,...f});lastBadField=null;}
            if(lastGeom&&lastGeom.rowId===f.rowId){
              const dx=f.row.x-lastGeom.row.x,dy=f.row.y-lastGeom.row.y;
              const adx=f.anchor&&lastGeom.anchor?f.anchor.x-lastGeom.anchor.x:0;
              const ady=f.anchor&&lastGeom.anchor?f.anchor.y-lastGeom.anchor.y:0;
              const unexplainedRowDx=dx-adx,unexplainedRowDy=dy-ady;
              if(!near(f.row.x,lastGeom.row.x)||!near(f.row.y,lastGeom.row.y)||
                 !near(f.row.h,lastGeom.row.h)||!near(f.rowScrollTop,lastGeom.rowScrollTop)||
                 !near(f.listScrollTop,lastGeom.listScrollTop)||
                 !near(f.rail?.x,lastGeom.rail?.x)){
                keep('geometry-delta',{rowId:f.rowId,...f,
                  delta:{rowDx:+dx.toFixed(2),rowDy:+dy.toFixed(2),
                    anchorDx:+adx.toFixed(2),anchorDy:+ady.toFixed(2),
                    rowHeight:f.row.h-lastGeom.row.h,
                    rowScroll:(f.rowScrollTop??0)-(lastGeom.rowScrollTop??0),
                    listScroll:(f.listScrollTop??0)-(lastGeom.listScrollTop??0)},
                  unexplainedRowDx:+unexplainedRowDx.toFixed(2),unexplainedRowDy:+unexplainedRowDy.toFixed(2)});
              }
            }
            if(f.rail&&!f.railHidden&&f.railGap!==null&&
               (Math.abs(f.railGap)>1||Math.abs(f.rail.y-f.row.y)>1||Math.abs(f.rail.h-f.row.h)>1)&&
               time-lastRailWarning>200){
              lastRailWarning=time;
              keep('geometry-invariant-warning',{rowId:f.rowId,reason:'rail-disconnected',...f});
            }
            if(time-lastHeartbeat>=250){lastHeartbeat=time;keep('paint-frame',{...f,
              hit:lastPoint?elementName(document.elementFromPoint(lastPoint.x,lastPoint.y)):null});}
            lastGeom={...f,ms:time};
          } else {lastGeom=null;baseline=null;lastBoundary=null;lastBadField=null;}
        }
        frame=requestAnimationFrame(frameStep);
      };
      for(const type of ['pointerenter','pointerleave','pointermove','pointerdown','pointerup'])
        document.addEventListener(type,recordPointer,true);
      document.addEventListener('scroll',recordScroll,true);
      document.addEventListener('wheel',recordWheel,{capture:true,passive:true});
      document.addEventListener('toggle',recordNativeToggle,true);
      window.addEventListener('mcel-hover-trace',recordHoverDecision);
      // A removedNodes notification means "removed from this parent", NOT
      // "removed from the document". Real MCEL layout updates can MOVE the
      // exact same row several times in a transaction. Check after the DOM
      // has settled and deduplicate by node identity before reporting loss.
      const detachObserver=new MutationObserver(records=>{
        const removed=new Set();
        for(const change of records)for(const node of change.removedNodes){
          if(node.nodeType!==1)continue;
          if(node.localName==='mcel-row')removed.add(node);
          for(const row of node.querySelectorAll?.('mcel-row')||[])removed.add(row);
        }
        if(!removed.size)return;
        queueMicrotask(()=>{
          let moved=0;
          for(const row of removed){
            if(row.isConnected && root.contains(row)){moved++;continue;}
            keep('dom-row-detached',{rowId:row.dataset.rowId||null,
              wasOpen:row.matches(':popover-open'),lastPointer:lastPoint,
              reason:'not-connected-after-mutation'});
          }
          if(moved)keep('dom-rows-moved',{count:moved,reason:'retained-dom-identity'});
        });
      });
      detachObserver.observe(root,{childList:true,subtree:true});
      frame=requestAnimationFrame(frameStep);
      keep('recording-started',{scope:selector});
      return {stop(){hoverCapture.running=false;cancelAnimationFrame(frame);
        for(const type of ['pointerenter','pointerleave','pointermove','pointerdown','pointerup'])
          document.removeEventListener(type,recordPointer,true);
        document.removeEventListener('scroll',recordScroll,true);
        document.removeEventListener('wheel',recordWheel,true);
        detachObserver.disconnect();
        document.removeEventListener('toggle',recordNativeToggle,true);
        window.removeEventListener('mcel-hover-trace',recordHoverDecision);
        keep('recording-stopped');}};
    }});

  register({id:"rrweb",kind:"recorder",description:"Local DOM/session event recording (not uploaded)",availability:() => (window.rrweb?.record || window.rrwebRecord?.record) ? "ready" : "requires-local-rrweb-script",
    start(ctx) {
      const record = window.rrweb?.record || window.rrwebRecord?.record;
      if (typeof record !== "function") throw new Error("rrweb recorder not loaded");
      const events = []; const limit = Math.max(10, Math.min(2000, Number(ctx.config.maxEvents) || 500));
      const stop = record({emit(event) {events.push(event); if (events.length > limit) events.shift();}, maskAllInputs:true, ...(ctx.config.options || {})});
      if (typeof stop !== "function") throw new Error("rrweb failed to start");
      // A separate export call is opt-in; avoid leaking recorded content in generic findings.
      const state = {stop,export:() => events.slice()};
      recordingStates.set("rrweb",state);
      return {stop() {stop(); recordingStates.delete("rrweb");}};
    }});
  const recordingStates = new Map();
  window.MCELDiagnosticPlugins.exportRecording = (id) => {
    if (id !== "rrweb" || !recordingStates.has(id)) throw new Error("rrweb recorder is not active");
    return recordingStates.get(id).export();
  };

  register({id:"sentry",kind:"runtime",description:"Browser exception capture using an already-initialized Sentry SDK",availability:() => window.Sentry?.captureException ? "ready" : "requires-configured-sentry-sdk",
    start(ctx) {
      if (!window.Sentry?.captureException) throw new Error("Sentry SDK not initialized; do not transmit data without explicit configuration");
      const onError = event => ctx.emit("browser-error","error",event.message || "Browser error",{source:event.filename,line:event.lineno});
      const onRejection = event => ctx.emit("unhandled-rejection","error",normalizeError(event.reason));
      window.addEventListener("error",onError); window.addEventListener("unhandledrejection",onRejection);
      return {stop() {window.removeEventListener("error",onError);window.removeEventListener("unhandledrejection",onRejection);}};
    }});

  register({id:"chrome-performance",kind:"runtime",description:"Long animation frames or long-task reporting",availability:() => typeof PerformanceObserver !== "undefined" ? "ready" : "unsupported-browser",
    start(ctx) {
      if (typeof PerformanceObserver === "undefined") throw new Error("PerformanceObserver unavailable");
      const supported = PerformanceObserver.supportedEntryTypes || [];
      const type = supported.includes("long-animation-frame") ? "long-animation-frame" : supported.includes("longtask") ? "longtask" : null;
      if (!type) throw new Error("Long animation frames and long tasks are unavailable in this browser");
      const threshold = Math.max(50, Number(ctx.config.thresholdMs) || 200);
      const finite = value => typeof value === "number" && Number.isFinite(value) ? value : null;
      const compactScript = script => ({
        sourceURL:String(script.sourceURL || "").slice(0, 600),
        sourceFunctionName:String(script.sourceFunctionName || "").slice(0, 160),
        sourceCharPosition:finite(script.sourceCharPosition),
        invoker:String(script.invoker || "").slice(0, 160),
        invokerType:String(script.invokerType || "").slice(0, 80),
        executionStart:finite(script.executionStart),
        duration:finite(script.duration),
        forcedStyleAndLayoutDuration:finite(script.forcedStyleAndLayoutDuration),
        pauseDuration:finite(script.pauseDuration)
      });
      const observer = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (entry.duration < threshold) continue;
          // Long Animation Frames supply attribution when the browser can expose it.
          // Preserve the native facts; lack of attribution must not imply no script ran.
          const scripts = entry.scripts ? Array.from(entry.scripts).slice(0, 24).map(compactScript) : [];
          const sources = [...new Set(scripts.map(script => script.sourceURL).filter(Boolean))].slice(0, 12);
          ctx.emit(type,"warning",`${entry.duration.toFixed(1)}ms`,{
            durationMs:entry.duration,
            startTime:entry.startTime,
            entryType:type,
            renderStart:finite(entry.renderStart),
            styleAndLayoutStart:finite(entry.styleAndLayoutStart),
            blockingDuration:finite(entry.blockingDuration),
            firstUIEventTimestamp:finite(entry.firstUIEventTimestamp),
            scripts,
            scriptCount:entry.scripts ? entry.scripts.length : null,
            attributionTruncated:!!(entry.scripts && entry.scripts.length > scripts.length),
            sources
          });
        }
      });
      observer.observe({type,buffered:true});
      return {stop() {observer.disconnect();}};
    }});

  register({id:"webhint",kind:"external-audit",description:"Node CLI site audit, imported as evidence",availability:() => "requires-external-node-runner",
    async run(ctx) {throw new Error("webhint is a Node CLI; run it outside the page and import a JSON report");}});
  register({id:"lighthouse",kind:"external-audit",description:"Chrome browser audit, imported as evidence",availability:() => "requires-external-chrome-runner",
    async run(ctx) {throw new Error("Lighthouse requires a Chrome/Node runner; run it outside the page and import a JSON report");}});
  register({id:"clarity",kind:"external-service",description:"Optional Microsoft Clarity hosted session analytics",availability:() => window.clarity ? "ready-needs-consent" : "requires-explicit-hosted-setup",
    start(ctx) {
      if (!ctx.config.consentGranted) throw new Error("Clarity requires explicit consentGranted=true and external service setup");
      if (typeof window.clarity !== "function") throw new Error("Clarity is not installed or configured");
      return {stop() { /* Clarity does not expose a complete reliable SDK shutdown; disable requires reload. */ }};
    }});

  // Import audit results generated outside the browser. No network connections required.
  window.MCELDiagnosticPlugins.importAudit = (id, report) => {
    if (id !== "webhint" && id !== "lighthouse") throw new Error("Only external audit reports can be imported");
    if (!report || typeof report !== "object") throw new TypeError("Expected parsed JSON report");
    const ctx = context(id);
    if (id === "lighthouse") {
      if (!report.categories || !report.audits) throw new Error("Not a Lighthouse JSON report");
      for (const [key, audit] of Object.entries(report.audits)) {
        if (audit.score !== null && audit.score !== undefined && audit.score < 0.9) ctx.emit("lighthouse:"+key,"warning",audit.title || key,{score:audit.score});
      }
    } else {
      const entries = Array.isArray(report) ? report : Array.isArray(report.messages) ? report.messages : null;
      if (!entries) throw new Error("Expected webhint JSON messages array");
      for (const message of entries) ctx.emit("webhint:"+(message.hintId || "hint"),"warning",message.message || "webhint finding",{resource:message.resource});
    }
    return {id,findings:findings.filter(f => f.pluginId===id).length};
  };
  // Start local, in-page runtime engines before any Applications runtime script.
  // Audit runners are deliberately not started here: they need a complete DOM
  // or an external process. Clarity never autostarts (remote telemetry).
  for (const event of window.__MCELDiagnosticEarly?.drain() || []) {
    findings.push(event);
  }
  const earlyPlugins = ["chrome-performance", "rrweb", "sentry"];
  const jobs = earlyPlugins.map(id => {
    const availability = definition(id).availability(context(id));
    if (availability !== "ready") {
      startup.states[id] = {status:"unavailable",availability};
      return Promise.resolve();
    }
    startup.states[id] = {status:"starting"};
    return enableInternal(id).then(result => {startup.states[id] = result;});
  });
  startup.ready = Promise.all(jobs).then(() => ({armedAt:startup.armedAt, states:{...startup.states}}));
  Object.freeze(window.MCELDiagnosticPlugins);
})();

/* MCEL console snapshot: copy(debug()) or copy(mcelDebug()).
 * Read-only; no listeners, probes, navigation, DOM writes, application operations,
 * access to input values, local storage, cookies, URLs with query strings, or networking.
 * Installed in both the Applications shell and the hosted application document.
 */
(function (host) {
  'use strict';
  const schema = 'mcel.console-debug.v1';
  const maxDepth = 4;
  const maxFrames = 24;
  const text = (value, limit = 200) => String(value ?? '').slice(0, limit);
  const exists = obj => obj !== null && obj !== undefined;
  const count = (doc, selector) => {
    try { return doc.querySelectorAll(selector).length; }
    catch (_) { return null; }
  };
  function pathOnly(loc) {
    try { return text(loc?.pathname || '', 180); }
    catch (_) { return '(unavailable)'; }
  }
  function frameMetadata(frame) {
    const sandbox = frame.getAttribute('sandbox');
    const sandboxTokens = sandbox === null ? [] : sandbox.toLowerCase().split(/\s+/).filter(Boolean);
    const srcdoc = frame.hasAttribute('srcdoc');
    const rawSrc = frame.getAttribute('src') || '';
    return {
      id: text(frame.id || '(anonymous)', 100),
      title: text(frame.getAttribute('title') || '', 100),
      sourceType: srcdoc ? 'srcdoc' : rawSrc.startsWith('about:') ? 'about' : rawSrc ? 'url' : 'unset',
      hidden: !!frame.hidden,
      sandboxed: sandbox !== null,
      allowsScripts: sandbox === null || sandboxTokens.includes('allow-scripts'),
      allowsSameOrigin: sandbox === null || sandboxTokens.includes('allow-same-origin'),
      // Some legacy rendered-site previews deliberately disallow scripts.
      sandboxBlocksScripts: sandbox !== null && !sandboxTokens.includes('allow-scripts')
    };
  }
  function safeStatus(doc, id) {
    const element = doc.getElementById(id);
    return element ? text(element.textContent.trim(), 320) : null;
  }
  function readTheme(win, doc) {
    const frame = doc.getElementById('mcel-semantic-theme-frame');
    const lab = doc.getElementById('mcel-lab-app');
    if (!frame && !lab) return null;
    const source = win.McelLabCanonicalPresentationSources?.calculator;
    const selected = doc.getElementById('mcel-theme-select');
    const runtime = win.mcelRuntimePreview || doc.querySelector('.mcel-runtime-preview');
    const requiredTokens = [
      '--mcel-theme-card', '--mcel-theme-ink', '--mcel-theme-muted',
      '--mcel-theme-accent', '--mcel-theme-accent-2', '--mcel-theme-line'
    ];
    let tokens = null;
    if (runtime && typeof win.getComputedStyle === 'function') {
      try {
        const style = win.getComputedStyle(runtime);
        tokens = Object.fromEntries(requiredTokens.map(key => [key, text(style.getPropertyValue(key).trim(), 100)]));
      } catch (_) { tokens = null; }
    }
    const f = frame ? frameMetadata(frame) : null;
    let calculatorFrame = null, mountedSource = null;
    if (frame) {
      try {
        const childDoc = frame.contentDocument;
        if (childDoc) {
          const expanded = childDoc.getElementById('mcel-expanded-ir');
          if (expanded) {
            try {
              const ir = JSON.parse(expanded.textContent);
              if (ir?.schema === 'mcel.application-ir.v1' &&
                  ir?.application?.appId === 'semantic-calculator-expanded') mountedSource = ir;
            } catch (_) { /* Invalid mounted IR must never be claimed as canonical. */ }
          }
          const modernRoot = childDoc.getElementById('mcel-expanded-root');
          const legacyRoot = childDoc.getElementById('calculator-app');
          const modern = Boolean(modernRoot && mountedSource);
          calculatorFrame = {
            accessible: true,
            readyState: text(childDoc.readyState, 40),
            mountModel: modern ? 'promoted-operational-runtime' :
              legacyRoot ? 'original-host' :
              childDoc.querySelector('#mcel-applications-shell,.mcel-applications-shell') ? 'applications-shell' : 'unresolved',
            mountedAppId: modern ? 'semantic-calculator-expanded' : legacyRoot ? 'calculator' : null,
            canonicalRootFound: modern || !!legacyRoot,
            canonicalControls: modern ? count(modernRoot,'[data-mcel-node][data-node-kind="control"], [data-mcel-node][data-node-kind="form"]') :
              count(childDoc, '#calculator-app [data-mcel-control]'),
            canonicalRegions: modern ? count(modernRoot,'[data-mcel-region]') :
              count(childDoc, '#calculator-app [data-mcel-region-id]'),
            themeStyleInstalled: !!childDoc.getElementById('mcel-lab-semantic-theme-color-only')
          };
        } else calculatorFrame = {accessible: false, reason: 'origin-or-sandbox-boundary'};
      } catch (_) { calculatorFrame = {accessible: false, reason: 'origin-or-sandbox-boundary'}; }
    }
    const previewEnabled = Boolean(win.mcelLabState?.semanticThemePreviewEnabled) ||
      (!!frame && !frame.hidden && frame.getAttribute('src') !== 'about:blank');
    const issues = [];
    if (previewEnabled) {
      if (!frame) issues.push('calculator-preview-frame-missing');
      else {
        if (f.sandboxBlocksScripts) issues.push('calculator-preview-sandbox-blocks-scripts');
        if (!calculatorFrame?.accessible) issues.push('calculator-preview-inaccessible');
        else if (calculatorFrame.mountModel === 'applications-shell')
          issues.push('calculator-preview-shell-instead-of-native-child');
        else if (!calculatorFrame.canonicalRootFound)
          issues.push('calculator-preview-canonical-root-missing');
        else if (!calculatorFrame.themeStyleInstalled)
          issues.push('calculator-theme-style-not-installed');
      }
      const selectedSource = mountedSource || source;
      if (selectedSource?.schema !== 'mcel.application-ir.v1' ||
          !['calculator','semantic-calculator-expanded'].includes(selectedSource?.application?.appId))
        issues.push('calculator-canonical-source-missing-or-invalid');
      if (!win.McelSemanticPresentationBridge) issues.push('semantic-presentation-bridge-missing');
      if (!win.McelSemanticThemePreview) issues.push('semantic-theme-engine-missing');
      if (!win.McelLabStyleLaw) issues.push('theme-law-missing');
      if (tokens && Object.values(tokens).some(v => !v)) issues.push('theme-tokens-incomplete');
    }
    const activeSource = mountedSource || source;
    return {
      previewEnabled,
      status: safeStatus(doc, 'mcel-semantic-theme-status'),
      computedColorEvidence: safeStatus(doc, 'mcel-semantic-theme-evidence-text'),
      selectedTheme: text(selected?.value || win.mcelLabState?.theme || '', 100),
      selectedChrome: text(doc.getElementById('mcel-chrome-select')?.value || '', 100),
      chromeProjection: 'observational-only-for-modern-mcel',
      canonicalSource: {
        present: !!activeSource,
        schema: text(activeSource?.schema || '', 90),
        appId: text(activeSource?.application?.appId || '', 90),
        rootSelector: text(activeSource?.surfaces?.[0]?.root ||
          (mountedSource ? '#mcel-expanded-root (runtime mount)' : ''), 90),
        declaredControls: mountedSource ?
          (mountedSource.surfaces?.[0]?.nodes || []).filter(n => ['control','form'].includes(n.nodeKind)).length :
          source?.surfaces?.[0]?.semanticSurface?.controls?.length ?? null,
        declaredRegions: mountedSource ? mountedSource.surfaces?.[0]?.interfaceIntent?.regions?.length ?? null :
          source?.surfaces?.[0]?.semanticSurface?.regions?.length ?? null
      },
      engines: {
        semanticBridge: !!win.McelSemanticPresentationBridge,
        semanticTheme: !!win.McelSemanticThemePreview,
        styleLaw: !!win.McelLabStyleLaw
      },
      frame: f,
      mountedCalculator: calculatorFrame,
      computedThemeTokens: tokens,
      issues
    };
  }
  function coreDebug(win) {
    const core = win.MCEL;
    if (!core) return {available: false};
    try {
      const history = typeof core.getDebugTimeline === 'function' ? core.getDebugTimeline() : [];
      const last = history[history.length - 1] || null;
      return {
        available: true,
        historyCount: history.length,
        lastOperation: text(last?.operation || '', 100),
        lastPhase: text(last?.phase || '', 50),
        lastOkay: last?.ok ?? null,
        lastIssues: Array.isArray(last?.issues) ? last.issues.length : null,
        mechanismCount: typeof core.listDebugMechanisms === 'function' ? core.listDebugMechanisms().length : null
      };
    } catch (_) { return {available: true, error: 'debug-timeline-unreadable'}; }
  }
  function collect(win, meta, depth, context) {
    if (context.seen.includes(win)) return {frame: meta, status: 'already-observed'};
    context.seen.push(win);
    let doc;
    try { doc = win.document; }
    catch (_) { return {frame: meta, status: 'inaccessible', reason: 'origin-or-sandbox-boundary'}; }
    if (!doc) return {frame: meta, status: 'inaccessible', reason: 'document-unavailable'};
    const theme = readTheme(win, doc);
    const result = {
      frame: meta,
      status: 'observed',
      path: pathOnly(win.location),
      readyState: text(doc.readyState || '', 50),
      mcel: {
        applicationsShell: !!doc.getElementById('mcel-applications-shell'),
        lab: !!doc.getElementById('mcel-lab-app'),
        calculatorRoot: !!doc.getElementById('calculator-app'),
        semanticRootCount: count(doc, '[data-mcel-node], [data-mcel-region-id]'),
        semanticControlCount: count(doc, '[data-mcel-control]'),
        semanticRegionCount: count(doc, '[data-mcel-region-id]')
      },
      theme,
      coreDebug: coreDebug(win),
      frames: []
    };
    if (depth >= maxDepth) { result.framesTruncated = 'maximum-depth'; return result; }
    const frames = Array.from(doc.querySelectorAll('iframe'));
    for (const iframe of frames) {
      if (context.seen.length >= maxFrames) {
        result.framesTruncated = 'maximum-frame-count';
        break;
      }
      const childMeta = frameMetadata(iframe);
      let child = null;
      try { child = iframe.contentWindow; }
      catch (_) { /* Inaccessible boundary recorded below. */ }
      result.frames.push(child
        ? collect(child, childMeta, depth + 1, context)
        : {frame: childMeta, status: 'inaccessible', reason: 'window-unavailable'});
    }
    return result;
  }
  function capture() {
    return {
      schema,
      generatedAt: new Date().toISOString(),
      scope: 'current-mcel-window-and-accessible-child-frames',
      safety: 'read-only; excludes input values, user content, URLs with queries and credentials',
      document: collect(host, {id: '(console-context)', sourceType: 'current-document'}, 0, {seen: []})
    };
  }
  host.mcelDebug = capture;
  // DevTools `copy(debug())` uses this short alias when no app owns it.
  // Never overwrite another application's existing `debug` function.
  if (typeof host.debug === 'undefined') host.debug = capture;
  host.MCELDebugCommand = Object.freeze({schema, capture});
})(window);
