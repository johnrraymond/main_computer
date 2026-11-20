/* MCEL reactivity shadow oracle, coverage instrumentation v2.
 * Diagnostic-only: all incumbent consumers still execute; this observer cannot
 * write the DOM, mutate application state, dispatch operations, or schedule UI.
 * No DOM text, field values, credentials, receipt bodies or network URLs are exported.
 */
(function (global) {
  'use strict';
  const plugins=global.MCELDiagnosticPlugins;
  if(!plugins || global.MCELReactivityShadowOracle)return;
  const ID='mcel-reactivity-shadow', SCHEMA='mcel.reactivity.shadow.v1';
  const sessions=new Set(), completedSessions=[], byRoot=new WeakMap();
  let enabled=false,emit=null,eventLimit=10000,scopeLimit=128,layoutSampling=true;
  const MAX_STATE_BYTES=1000000;
  const safe=value=>String(value??'').replace(/[^A-Za-z0-9_.:-]/g,'_').slice(0,100);
  const keyOf=(kind,id)=>safe(kind)+'/'+safe(id);
  const now=()=>new Date().toISOString();
  const guarded=fn=>{try{return fn();}catch(_){return undefined;}};
  function serialize(value){
    try{
      const json=JSON.stringify(value);
      if(json===undefined)return {known:true,value:'[undefined]'};
      return json.length<=MAX_STATE_BYTES?{known:true,value:json}:{known:false};
    }catch(_){return {known:false};}
  }
  function snapshotState(map){
    if(Object.prototype.toString.call(map)!=='[object Map]'||map.size>10000)return null;
    const result=new Map();
    for(const [id,value] of map)result.set(String(id),serialize(value));
    return result;
  }
  function changes(a,b){
    if(!a||!b)return {known:false,ids:[]};
    const all=new Set([...a.keys(),...b.keys()]),ids=[];
    if(all.size>10000)return {known:false,ids:[]};
    for(const id of all){
      const prev=a.get(id),next=b.get(id);
      if((prev&&!prev.known)||(next&&!next.known))return {known:false,ids:[]};
      if(prev?.value!==next?.value || a.has(id)!==b.has(id))ids.push(id);
    }
    return {known:true,ids};
  }
  function sessionFor(root,appId){
    let s=byRoot.get(root);
    if(s)return s;
    s={root,appId:safe(appId),startedAt:now(),notifications:0,consumers:new Map(),
      states:null,receipts:null,current:null,events:[],dropped:0,externalEpochs:new Map(),
      mutationSupported:typeof global.MutationObserver==='function',observer:null,inNotify:false,
      stats:{executions:0,predictedSkips:0,observedMutations:0,contradictions:0,
        unverified:0,unknownBatches:0,propertyChanges:0,focusChanges:0,
        layoutChanges:0,unattributedMutations:0,asyncMutations:0,
        incompleteScopes:0,possibleStateOnlySkips:0},
      coverage:{trackedKinds:new Set(),externalReads:new Set(),invalidations:new Set(),
        totalScoped:0,completeScopes:0,externalMutations:0},errors:[]};
    if(s.mutationSupported){
      try{
        s.observer=new global.MutationObserver(records=>{
          if(!s.inNotify){s.stats.asyncMutations+=records.length;s.coverage.externalMutations+=records.length;}
        });
        s.observer.observe(root,{attributes:true,characterData:true,childList:true,subtree:true});
      }catch(_){s.observer=null;s.mutationSupported=false;}
    }
    byRoot.set(root,s);sessions.add(s);return s;
  }
  function remember(s,event){
    s.events.push(event);
    if(s.events.length>eventLimit){
      const excess=s.events.length-eventLimit;
      s.events.splice(0,excess);s.dropped+=excess;
    }
  }
  function bump(s,name){
    const key=safe(name);
    s.externalEpochs.set(key,(s.externalEpochs.get(key)||0)+1);
    s.coverage.invalidations.add(key);
  }
  // External invalidations are diagnostic facts only; they never call notify().
  function invalidate(root,name){
    if(!enabled)return;
    const s=byRoot.get(root);
    if(s)bump(s,name);
  }
  const isElement=el=>!!(el && el.nodeType===1);
  const anyTarget=value=>{
    if(!value)return [];
    if(Array.isArray(value))return value.filter(isElement);
    if(isElement(value))return [value];
    return [];
  };
  // Compare property values, including ones MutationObserver cannot see.
  // Snapshots are ephemeral and never enter the evidence report.
  function properties(el){
    const tag=String(el.tagName||'').toUpperCase();
    const field=(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT');
    return [el.hidden,el.disabled,el.checked,el.readOnly,
      field&&tag!=='INPUT'&&tag!=='TEXTAREA'?el.selectedIndex:null,
      // A property value is retained only until this consumer returns.
      field?el.value:null,el.scrollTop,el.scrollLeft,
      el.getAttribute?.('aria-pressed'),el.getAttribute?.('aria-selected'),
      el.getAttribute?.('aria-disabled'),el.getAttribute?.('aria-current')];
  }
  function scopeSnapshot(targets,root,kind){
    const entries=[],seen=new Set();let truncated=false;
    const add=el=>{
      if(!isElement(el)||seen.has(el))return;
      if(entries.length>=scopeLimit){truncated=true;return;}
      seen.add(el);
      entries.push({element:el,properties:properties(el)});
    };
    for(const target of anyTarget(targets)){
      if(root?.contains && target!==root&&!root.contains(target))continue;
      add(target);
      // Bound traversal at the source. querySelectorAll('*') would allocate a
      // potentially huge NodeList even when the evidence cap is small.
      const stack=Array.from(target.children||[]).reverse();
      while(stack.length){
        if(entries.length>=scopeLimit){truncated=true;break;}
        const child=stack.pop();add(child);
        if(child?.children?.length)stack.push(...Array.from(child.children).reverse());
      }
    }
    const doc=root?.ownerDocument;
    const active=doc?.activeElement;
    // Track focus separately without exposing element identity or input value.
    const geom=layoutSampling && kind==='generated-layout' && isElement(targets)
      ? guarded(()=>{const r=targets.getBoundingClientRect();return [r.x,r.y,r.width,r.height];}) : null;
    return {entries,truncated,scoped:entries.length>0,active,geom};
  }
  function compareScope(before,targets,root,kind){
    if(!before)return {changes:0,focus:false,layout:false,complete:false};
    const after=scopeSnapshot(targets,root,kind);
    let count=0;
    // Detect detached or replaced nodes as well as property-only changes.
    for(const item of before.entries){
      if(root?.contains && !root.contains(item.element) && item.element!==root){count++;continue;}
      const next=properties(item.element);
      if(item.properties.some((v,i)=>!Object.is(v,next[i])))count++;
    }
    const focus=before.active!==after.active;
    const layout=!!before.geom && !!after.geom &&
      before.geom.some((x,i)=>Math.abs(x-after.geom[i])>0.5);
    return {changes:count,focus,layout,complete:before.scoped&&!before.truncated&&!after.truncated};
  }
  function beforeNotify(root,appId,state,receiptSequence){
    if(!enabled)return;
    const s=sessionFor(root,appId);
    const next=snapshotState(state),diff=changes(s.states,next);
    if(s.receipts!==null && s.receipts!==receiptSequence)bump(s,'receipts');
    // Freshness predicates can expire without a state write: invalidate on each
    // incumbent notification, rather than treating a clock-dependent skip safe.
    bump(s,'clock');
    const currentEpochs=new Map(s.externalEpochs), changedExternal=new Set();
    for(const [name,version] of currentEpochs)
      if(s.previousEpochs?.get(name)!==version)changedExternal.add(name);
    s.current={known:diff.known,keys:new Set(diff.ids),changedExternal};
    s.previousEpochs=currentEpochs;s.states=next;s.receipts=receiptSequence;
    s.notifications++;s.inNotify=true;
    if(!diff.known)s.stats.unknownBatches++;
    const previous=s.observer?.takeRecords()||[];
    if(previous.length){s.stats.asyncMutations+=previous.length;s.coverage.externalMutations+=previous.length;}
  }
  function capture(root,kind,id,targets){
    if(!enabled)return null;
    const s=byRoot.get(root);
    if(!s?.current)return null;
    const key=keyOf(kind,id),prior=s.consumers.get(key);
    const stateOnly=kind==='display'||kind==='region';
    const deps=prior?.dependencies||[],external=prior?.externalDependencies||[];
    // A dependency without a declared invalidation producer is unknown, not
    // an unchanged input. Theme, local controls, layout, and host internals
    // are deliberately blocked until their producers are instrumented.
    const invalidationProducers=new Set(['clock','receipts','async-commit']);
    const unresolvedExternal=external.some(dep=>!invalidationProducers.has(dep));
    const known=!!prior && s.current.known && !unresolvedExternal;
    const changed=deps.some(dep=>s.current.keys.has(dep));
    const changedExternal=external.some(dep=>s.current.changedExternal.has(dep));
    const predicted=known ? changed||changedExternal?'run':'skip' : 'unverified';
    const reads=new Set(),externalReads=new Set();
    s.coverage.trackedKinds.add(safe(kind));
    const before=scopeSnapshot(targets,root,kind);
    s.coverage.totalScoped++;
    if(before.scoped&&!before.truncated)s.coverage.completeScopes++;
    s.observer?.takeRecords();
    return {
      read(id){reads.add(String(id));},
      readExternal(name){const key=safe(name);externalReads.add(key);s.coverage.externalReads.add(key);},
      finish(){
        const mutations=(s.observer?.takeRecords()||[]).length;
        const p=compareScope(before,targets,root,kind);
        const observed=mutations+p.changes+(p.focus?1:0)+(p.layout?1:0);
        s.stats.executions++;s.stats.observedMutations+=mutations;
        s.stats.propertyChanges+=p.changes;
        if(p.focus)s.stats.focusChanges++;
        if(p.layout)s.stats.layoutChanges++;
        if(!p.complete)s.stats.incompleteScopes++;
        if(predicted==='skip')s.stats.predictedSkips++;
        if(predicted==='unverified')s.stats.unverified++;
        const contradiction=predicted==='skip'&&observed>0;
        if(contradiction){
          s.stats.contradictions++;
          guarded(()=>emit?.('predicted-skip-mutated','error',
            'A predicted skip changed DOM, properties, focus, or geometry',
            {appId:s.appId,consumer:key,domMutations:mutations,propertyChanges:p.changes,
              focusChanged:p.focus,layoutChanged:p.layout}));
        }
        const safeCandidate=predicted==='skip'&&stateOnly&&p.complete&&
          external.length===0&&externalReads.size===0&&!contradiction;
        if(safeCandidate)s.stats.possibleStateOnlySkips++;
        const record={dependencies:[...reads].sort(),externalDependencies:[...externalReads].sort(),
          observations:(prior?.observations||0)+1,mutations:(prior?.mutations||0)+mutations,
          propertyChanges:(prior?.propertyChanges||0)+p.changes,
          contradictions:(prior?.contradictions||0)+(contradiction?1:0),
          predictions:(prior?.predictions||0)+(predicted==='skip'?1:0),
          incompleteScopes:(prior?.incompleteScopes||0)+(p.complete?0:1),
          possibleStateOnlySkips:(prior?.possibleStateOnlySkips||0)+(safeCandidate?1:0),
          lastPrediction:predicted};
        s.consumers.set(key,record);
        remember(s,{notification:s.notifications,consumer:key,prediction:predicted,
          changedStateCount:s.current?.keys.size??0,dependencyCount:reads.size,
          externalDependencyCount:externalReads.size,mutationCount:mutations,
          propertyChangeCount:p.changes,focusChanged:p.focus,layoutChanged:p.layout,
          scopeComplete:p.complete,dependencyModel:stateOnly?'state-only-candidate':'external-inputs-not-proven',
          classification:contradiction?'contradiction':safeCandidate?'state-only-candidate-unverified':
            predicted==='skip'?'potential-savings-unverified':'unverified'});
      }
    };
  }
  function afterNotify(root){
    if(!enabled)return;
    const s=byRoot.get(root);
    if(!s)return;
    const leftovers=(s.observer?.takeRecords()||[]).length;
    s.stats.unattributedMutations+=leftovers;
    s.inNotify=false;s.current=null;
  }
  function archive(s){
    s.observer?.disconnect();byRoot.delete(s.root);sessions.delete(s);
    completedSessions.push({...s,root:null,observer:null,states:null,current:null,
      consumers:new Map(s.consumers),events:s.events.slice()});
    if(completedSessions.length>12)completedSessions.shift();
  }
  function release(root){const s=byRoot.get(root);if(s)archive(s);}
  function report(){
    const apps=[...completedSessions,...sessions].map(s=>({
      appId:s.appId,startedAt:s.startedAt,notifications:s.notifications,
      consumers:s.consumers.size,mutationSupported:s.mutationSupported,
      stats:{...s.stats},dropped:s.dropped,eventsEvicted:s.dropped,
      coverage:{trackedKinds:[...s.coverage.trackedKinds].sort(),
        externalReads:[...s.coverage.externalReads].sort(),
        invalidations:[...s.coverage.invalidations].sort(),
        totalScoped:s.coverage.totalScoped,completeScopes:s.coverage.completeScopes,
        externalMutations:s.coverage.externalMutations,
        consumerSummaries:[...s.consumers].map(([consumer,v])=>({consumer,...v})),
        // These are explicit certification blockers, not inferred passes.
        unresolved:['implicit-host-dependencies','async-commit-parity','independent-candidate-renderer',
          'browser-visual-parity','lifecycle-coverage','external-css-and-layout-effects']},
      events:s.events.slice()}));
    return {schema:SCHEMA,oracleVersion:2,
      status:apps.length?'shadow-observations-incomplete':'no-sessions',
      enabled,localOnly:true,certified:false,productionSchedulerChanged:false,
      applicationOperationsInvoked:0,limitations:[
        'No consumer is suppressed: predictions are hypothetical and the incumbent executes all work',
        'Property and focus changes are checked on bounded consumer scopes, never guaranteed complete',
        'Unattributed asynchronous and external layout effects remain certification blockers',
        'A shadow oracle cannot certify rollout without an independent candidate and browser parity tests'
      ],apps};
  }
  plugins.register({id:ID,kind:'recorder',description:'Opt-in local read-only reactive coverage shadow oracle',
    availability:()=> 'ready-local-only',
    start(ctx){enabled=true;emit=ctx.emit;
      eventLimit=Math.max(100,Math.min(50000,Number(ctx.config.maxEvents)||10000));
      scopeLimit=Math.max(16,Math.min(512,Number(ctx.config.maxNodesPerConsumer)||128));
      layoutSampling=ctx.config.sampleLayout!==false;
      return {stop(){enabled=false;emit=null;for(const s of [...sessions])archive(s);}};},
    run(ctx){const result=report();guarded(()=>ctx.emit('shadow-audit','info',result.status,
      {certified:false,notifications:result.apps.reduce((n,a)=>n+a.notifications,0),
       contradictions:result.apps.reduce((n,a)=>n+a.stats.contradictions,0),
       propertyChanges:result.apps.reduce((n,a)=>n+a.stats.propertyChanges,0)}));
      return result;}});
  global.MCELReactivityShadowOracle=Object.freeze({beforeNotify,capture,afterNotify,invalidate,release,report});
})(typeof window!=='undefined'?window:globalThis);
