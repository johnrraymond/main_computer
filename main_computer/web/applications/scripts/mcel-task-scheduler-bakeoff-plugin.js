/* Opt-in scheduler bake-off inside the existing MCEL diagnostic plugin registry.
 * All work is synthetic and read-only. No application intents or service APIs.
 * Libraries are imported only when the operator clicks Run in the lab.
 */
(function (host) {
  'use strict';
  const plugins = host.MCELDiagnosticPlugins;
  if (!plugins) return;
  const ID = 'mcel-task-scheduler-bakeoff';
  const VERSION = 'mcel.task.scheduler-bakeoff.v1';
  const sources = Object.freeze({
    rxjs: 'https://esm.sh/rxjs@7.8.2?bundle',
    query: 'https://esm.sh/@tanstack/query-core@5.90.12?bundle'
  });
  const clock = () => host.performance && typeof host.performance.now === 'function' ? host.performance.now() : Date.now();
  const sleep = ms => new Promise(resolve => host.setTimeout(resolve, ms));
  const round = value => Math.round(value * 10) / 10;
  const clamp = (value, fallback, min, max) => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(min, Math.min(max, Math.round(number))) : fallback;
  };
  const cleanError = err => {
    const message=String(err?.message || '');
    return /^[A-Z][A-Z0-9_]{3,60}$/.test(message) ? message : String(err?.name || 'Error').slice(0,60);
  };
  let active = false;

  async function loadLibrary(name, config) {
    const injected = name === 'rxjs' ? host.rxjs : host.TanStackQueryCore;
    if (injected) return {lib:injected, source:'already-loaded', loadMs:0};
    // External scripts must be explicitly authorized in the debug control.
    if (config.allowCdn !== true) throw new Error('LOCAL_LIBRARY_NOT_INSTALLED');
    const loadStart = clock();
    try {
      const lib = await import(sources[name]);
      return {lib, source:'operator-opt-in-cdn', loadMs:round(clock() - loadStart)};
    } catch (_) { throw new Error('CDN_MODULE_IMPORT_FAILED'); }
  }

  function nativeScheduler(tick, ms) {
    let alive = true, handle = null;
    const cycle = async () => {
      if (!alive) return;
      try { await tick(); } catch (_) { /* task captures its own error */ }
      if (alive) handle = host.setTimeout(cycle, ms);
    };
    handle = host.setTimeout(cycle, 0);
    return () => { alive = false; host.clearTimeout(handle); };
  }

  function rxjsScheduler(rx, tick, ms) {
    if (!rx?.timer || !rx?.exhaustMap || !rx?.from || !rx?.catchError || !rx?.EMPTY)
      throw new Error('RXJS_SCHEDULER_API_MISSING');
    const subscription = rx.timer(0, ms).pipe(
      rx.exhaustMap(() => rx.from(tick()).pipe(rx.catchError(() => rx.EMPTY)))
    ).subscribe({error:()=>{}});
    return () => subscription.unsubscribe();
  }

  function queryScheduler(query, tick, ms) {
    if (!query?.QueryClient || !query?.QueryObserver)
      throw new Error('QUERY_CORE_SCHEDULER_API_MISSING');
    const client = new query.QueryClient({defaultOptions:{queries:{retry:false}}});
    const observer = new query.QueryObserver(client, {
      queryKey:['mcel-debug-only','scheduler-bakeoff',String(Date.now()),String(Math.random())],
      queryFn:({signal})=>tick(signal),
      refetchInterval:ms,
      refetchIntervalInBackground:true,
      refetchOnWindowFocus:false,
      refetchOnReconnect:false,
      retry:false,
      staleTime:0,
      gcTime:0
    });
    const unsubscribe = observer.subscribe(()=>{});
    return () => {
      unsubscribe();
      observer.destroy();
      // Cancel only this short-lived, isolated QueryClient.
      void client.cancelQueries().catch(()=>{});
      client.clear();
    };
  }

  async function trial(id, lib, settings) {
    const start = clock();
    const signalController = new AbortController();
    const stats = {started:0, successes:0, failures:0, canceled:0, suppressed:0,
      maxConcurrent:0, concurrent:0, lateWrites:0, recoveredAfterFailure:false,
      firstSuccessMs:null, lastSuccessMs:null, observationGapsMs:[], events:[]};
    let stopped = false, suspended = false, hadFailure = false;
    const remember = (kind, more={}) => {
      stats.events.push({atMs:round(clock()-start),kind,...more});
      if (stats.events.length > 60) stats.events.shift();
    };
    const observe = (externalSignal) => {
      if (stopped || suspended || signalController.signal.aborted) {
        stats.suppressed++;
        return Promise.resolve({suppressed:true});
      }
      const attempt = ++stats.started;
      stats.concurrent++;
      stats.maxConcurrent=Math.max(stats.maxConcurrent,stats.concurrent);
      remember('started',{attempt});
      return new Promise((resolve,reject)=>{
        let settled=false;
        const cleanup=()=>{
          signalController.signal.removeEventListener('abort',abort);
          externalSignal?.removeEventListener?.('abort',abort);
          stats.concurrent--;
        };
        const finish = (kind, error) => {
          if (settled) return;
          settled=true;
          host.clearTimeout(timer);
          cleanup();
          if (kind==='canceled') { stats.canceled++;remember(kind,{attempt});reject(error || new Error('Aborted'));return; }
          if (kind==='failed') { stats.failures++;hadFailure=true;remember(kind,{attempt});reject(error);return; }
          if (stopped) { stats.lateWrites++;remember('late-write',{attempt});reject(new Error('Stopped'));return; }
          stats.successes++;
          const at=round(clock()-start);
          if (stats.lastSuccessMs!==null) stats.observationGapsMs.push(round(at-stats.lastSuccessMs));
          if (stats.firstSuccessMs===null) stats.firstSuccessMs=at;
          stats.lastSuccessMs=at;
          if (hadFailure) stats.recoveredAfterFailure=true;
          remember('completed',{attempt});resolve({ok:true,attempt});
        };
        const abort = () => finish('canceled',new Error('Aborted'));
        const timer = host.setTimeout(()=> {
          if (attempt===3) finish('failed',new Error('SYNTHETIC_OBSERVATION_UNAVAILABLE'));
          else finish('success');
        },settings.workMs);
        signalController.signal.addEventListener('abort',abort,{once:true});
        externalSignal?.addEventListener?.('abort',abort,{once:true});
        if (signalController.signal.aborted || externalSignal?.aborted) abort();
      });
    };
    let stop = null;
    let suspendedTimer = null, resumedTimer = null;
    try {
      if (id==='native-timeout') stop=nativeScheduler(observe,settings.intervalMs);
      else if (id==='rxjs') stop=rxjsScheduler(lib,observe,settings.intervalMs);
      else if (id==='tanstack-query-core') stop=queryScheduler(lib,observe,settings.intervalMs);
      else throw new Error('Unknown candidate');
      // Pause/resume tests exercise the common MCEL task gate, not browser tab throttling.
      suspendedTimer=host.setTimeout(()=>{suspended=true;remember('task-paused');},Math.round(settings.durationMs*.48));
      resumedTimer=host.setTimeout(()=>{suspended=false;remember('task-resumed');},Math.round(settings.durationMs*.65));
      await sleep(settings.durationMs);
    } finally {
      stopped=true;
      host.clearTimeout(suspendedTimer);
      host.clearTimeout(resumedTimer);
      stop?.();
      signalController.abort();
    }
    await sleep(settings.workMs+20); // detect callbacks committing after unmount
    const gaps=stats.observationGapsMs.slice().sort((a,b)=>a-b);
    const p95=gaps.length ? gaps[Math.ceil(gaps.length*.95)-1] : null;
    const safe=stats.maxConcurrent<=1 && stats.lateWrites===0 && stats.concurrent===0;
    return {
      candidate:id,status:'measured',durationMs:settings.durationMs,
      started:stats.started,successes:stats.successes,failures:stats.failures,
      canceled:stats.canceled,suppressed:stats.suppressed,maxConcurrent:stats.maxConcurrent,
      lateWrites:stats.lateWrites,activeAtEnd:stats.concurrent,
      recoveredAfterFailure:stats.recoveredAfterFailure,firstSuccessMs:stats.firstSuccessMs,
      p95ObservationGapMs:p95,safetyPassed:safe,
      // Evidence is intentionally bounded and free of application data.
      events:stats.events
    };
  }

  async function runBakeoff(ctx) {
    if (active) return {schema:VERSION,status:'already-running'};
    active=true;
    const settings={
      intervalMs:clamp(ctx.config.intervalMs,65,30,2000),
      workMs:clamp(ctx.config.workMs,115,10,3000),
      durationMs:clamp(ctx.config.durationMs,1050,500,10000)
    };
    const summary={schema:VERSION,localOnly:true,synthetic:true,
      productionTasksChanged:false,serviceRequests:0,
      settings,startedAt:new Date().toISOString(),candidates:[],ranking:[],
      note:'Simulated repeated read-only observations. Focus/visibility behavior is not a real background-tab test.'};
    try {
      const variants=[['native-timeout',null],['rxjs','rxjs'],['tanstack-query-core','query']];
      for(const [id,dependency] of variants){
        let loaded={lib:null,source:'browser-native',loadMs:0};
        try {
          if(dependency) loaded=await loadLibrary(dependency,ctx.config);
          const outcome=await trial(id,loaded.lib,settings);
          summary.candidates.push({...outcome,source:loaded.source,libraryLoadMs:loaded.loadMs});
          if(!outcome.safetyPassed) ctx.emit('task-scheduler-unsafe','error',`${id} failed overlap/cleanup invariants`,{candidate:id});
        } catch(error) {
          summary.candidates.push({candidate:id,status:'unavailable',reason:cleanError(error),
            source:loaded.source});
        }
      }
      const measured=summary.candidates.filter(x=>x.status==='measured');
      summary.ranking=measured.map(x=>({candidate:x.candidate,safetyPassed:x.safetyPassed,
        recoveredAfterFailure:x.recoveredAfterFailure,p95ObservationGapMs:x.p95ObservationGapMs,
        successes:x.successes})).sort((a,b)=>Number(b.safetyPassed)-Number(a.safetyPassed) ||
          Number(b.recoveredAfterFailure)-Number(a.recoveredAfterFailure) ||
          (a.p95ObservationGapMs??Infinity)-(b.p95ObservationGapMs??Infinity));
      summary.status=measured.length===3?'comparison-complete':'comparison-incomplete';
      // The result is a short synthetic ranking, not production scheduler acceptance.
      summary.syntheticLeader=measured.length===3 && summary.ranking[0]?.safetyPassed &&
        summary.ranking[0]?.recoveredAfterFailure ? summary.ranking[0].candidate : null;
      summary.winner=null; // require actual Tasks-app integration and longer browser acceptance
      summary.completedAt=new Date().toISOString();
      if(summary.status!=='comparison-complete')ctx.emit('task-scheduler-comparison-incomplete','warning',
        'Not all candidate libraries were available; no winner established');
      return summary;
    } finally {active=false;}
  }

  plugins.register({id:ID,kind:'audit',
    description:'Opt-in native vs RxJS vs TanStack Query Core scheduler bake-off; synthetic observations only',
    availability:()=>active?'running':'ready-on-demand',
    run:runBakeoff
  });

  // Debug UI is present only in MCEL Lab; no app-specific scheduling on page mount.
  function mountControls(){
    const button=host.document.getElementById('mcel-task-bakeoff-run');
    const output=host.document.getElementById('mcel-task-bakeoff-report');
    if (!button || !output || button.dataset.mcelBound==='true')return;
    button.dataset.mcelBound='true';
    button.addEventListener('click',async()=>{
      button.disabled=true;
      output.textContent='Running synthetic scheduler comparison. Libraries are loaded only for this opt-in run.';
      try {
        plugins.configure(ID,{allowCdn:true,intervalMs:65,workMs:115,durationMs:1050});
        const result=await plugins.run(ID);
        const report=result.result||result;
      const lines=['Candidate | Outcome | Safe | Recovered | p95 gap (ms) | Completed'];
      for(const row of report.candidates||[]) lines.push([row.candidate,row.status,
        String(row.safetyPassed??'-'),String(row.recoveredAfterFailure??'-'),
        String(row.p95ObservationGapMs??'-'),String(row.successes??'-')].join(' | '));
      lines.push('', 'Synthetic leader: '+(report.syntheticLeader||'not established'),
        'Production selection: not made (requires Tasks-app integration)',
        '', 'Full evidence JSON:',JSON.stringify(report,null,2));
      output.textContent=lines.join('\n');
      } catch(error) {output.textContent='Bake-off unavailable: '+cleanError(error);}
      finally {button.disabled=false;}
    });
  }
  if(host.document.readyState==='loading') host.document.addEventListener('DOMContentLoaded',mountControls,{once:true});
  else mountControls();
})(window);
