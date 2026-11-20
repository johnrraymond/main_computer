/* MCEL reactivity shadow oracle, revision and deferred semantic-producer provenance v2.3.
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
      nextRevision:0,activeOperations:new Map(),operationHistory:[],operationsEvicted:0,invalidationHistory:[],
      asyncCorrelation:{singlePending:0,ambiguousPending:0,noPending:0,notificationRevisions:0,
        unknownNotificationOwners:0,receiptMatches:0},
      nextSourceSpan:0,producerStack:[],producerSummaries:new Map(),receiptRevisions:new Map(),
      sourceCoverage:{identified:0,scheduled:0,direct:0,unclassified:0,
        operationLinked:0,independentUi:0,unexplained:0,unclassifiedAttributeNames:{},
        unclassifiedNoPending:0,unclassifiedSinglePending:0,unclassifiedAmbiguousPending:0,
        unclassifiedStructure:{}},
      mutationSupported:typeof global.MutationObserver==='function',observer:null,inNotify:false,
      stats:{executions:0,predictedSkips:0,observedMutations:0,contradictions:0,
        unverified:0,unknownBatches:0,bootstrapBatches:0,propertyChanges:0,focusChanges:0,
        layoutChanges:0,unattributedMutations:0,asyncMutations:0,
        incompleteScopes:0,possibleStateOnlySkips:0},
      coverage:{trackedKinds:new Set(),externalReads:new Set(),invalidations:new Set(),
        totalScoped:0,completeScopes:0,externalMutations:0},errors:[]};
    if(s.mutationSupported){
      try{
        s.observer=new global.MutationObserver(records=>{
          if(!s.inNotify){recordOutsideNotify(s,records);}
        });
        s.observer.observe(root,{attributes:true,characterData:true,childList:true,subtree:true});
      }catch(_){s.observer=null;s.mutationSupported=false;}
    }
    byRoot.set(root,s);sessions.add(s);return s;
  }
  // Only single *pending* operations yield a temporal candidate correlation.
  // Even a unique pending operation is not proof that it caused a DOM change:
  // other host callbacks and CSS effects can run in the same interval.
  function solePending(s){
    return s.activeOperations.size===1?[...s.activeOperations.values()][0]:null;
  }
  function exactKnownRevision(s,revision){
    return Number.isInteger(revision)&&revision>0&&
      (s.activeOperations.has(revision)||s.operationHistory.some(op=>op.revision===revision));
  }
  function recordOutsideNotify(s,records,producer=null){
    const count=typeof records==='number'?records:records?.length||0;
    if(!count)return;
    s.stats.asyncMutations+=count;s.coverage.externalMutations+=count;
    const pending=solePending(s),number=s.activeOperations.size;
    const kind=number===1?'singlePending':number>1?'ambiguousPending':'noPending';
    // Keep the original temporal counters as a partition of ALL external
    // mutations. Producer ownership is orthogonal to in-flight operations.
    s.asyncCorrelation[kind]+=count;
    const source=producer?.source||null;
    const exactRevision=source&&exactKnownRevision(s,producer?.revision)?producer.revision:null;
    const classification=!source?'unexplained':exactRevision!==null?'operation-linked':'identified-independent-ui';
    if(classification==='operation-linked')s.sourceCoverage.operationLinked+=count;
    else if(classification==='identified-independent-ui')s.sourceCoverage.independentUi+=count;
    else s.sourceCoverage.unexplained+=count;
    if(source){
      const scope=producer.scheduled?'scheduled':'direct';
      s.sourceCoverage.identified+=count;s.sourceCoverage[scope]+=count;
      const row=s.producerSummaries.get(source)||{source,records:0,spans:0,
        direct:0,scheduled:0,revisionLinked:0,notificationLinked:0};
      row.records+=count;row[scope]+=count;
      if(exactRevision!==null)row.revisionLinked+=count;
      if(producer.notification!==null)row.notificationLinked+=count;
      s.producerSummaries.set(source,row);
    }else{
      s.sourceCoverage.unclassified+=count;
      s.sourceCoverage['unclassified'+kind[0].toUpperCase()+kind.slice(1)]+=count;
    }
    // Mutation descriptions are structural only: never export target nodes,
    // attributes' values, DOM text, selectors or user-controlled identifiers.
    const kinds={attributes:0,childList:0,characterData:0,other:0};
    const attributeGroups={style:0,class:0,aria:0,semanticMarker:0,visibility:0,other:0};
    if(Array.isArray(records))for(const record of records){
      if(Object.prototype.hasOwnProperty.call(kinds,record?.type))kinds[record.type]++;
      else kinds.other++;
      if(record?.type==='attributes'){
        const name=record.attributeName;
        const category=name==='style'?'style':name==='class'?'class':
          typeof name==='string'&&name.startsWith('aria-')?'aria':
          typeof name==='string'&&name.startsWith('data-mcel-')?'semanticMarker':
          name==='hidden'?'visibility':'other';
        attributeGroups[category]++;
        if(!source && category==='semanticMarker' && typeof name==='string' &&
           /^data-mcel-[a-z0-9-]{1,70}$/.test(name)){
          const names=s.sourceCoverage.unclassifiedAttributeNames;
          if(Object.prototype.hasOwnProperty.call(names,name)||Object.keys(names).length<48)
            names[name]=(names[name]||0)+1;
        }
      }
    }
    if(!source){
      const breakdown=s.sourceCoverage.unclassifiedStructure;
      for(const key of Object.keys(kinds))breakdown[key]=(breakdown[key]||0)+kinds[key];
      for(const key of Object.keys(attributeGroups))
        breakdown['attribute_'+key]=(breakdown['attribute_'+key]||0)+attributeGroups[key];
    }
    remember(s,{type:'async-dom-batch',count,mutationKinds:kinds,attributeGroups,source,
      provenance:source?'instrumented-dom-producer':'not-instrumented',
      classification,
      sourceExecution:producer?.id??null,
      scheduledByNotification:producer?.notification??null,
      scheduledRevisionCandidate:producer?.scheduledRevision??null,
      exactOperationRevision:exactRevision,
      revisionCandidate:pending?.revision??null,
      correlation:kind==='singlePending'?'temporal-only':kind==='ambiguousPending'?'ambiguous':'unassociated'});
  }
  // A synchronous instrumentation span identifies the function that performed
  // DOM writes, not the request that logically caused that function to run.
  // Flush pre-existing records BEFORE entering the span, then drain only new
  // records on exit. The incumbent renderer and scheduling remain untouched.
  function beginMutationSource(root,name,revision=null,scheduled=null){
    if(!enabled)return null;
    const s=byRoot.get(root);
    if(!s||s.inNotify)return null;
    const before=s.observer?.takeRecords()||[];
    if(before.length)recordOutsideNotify(s,before);
    const source=safe(name);
    const token={id:++s.nextSourceSpan,source,
      revision:exactKnownRevision(s,revision)?revision:
        exactKnownRevision(s,scheduled?.exactRevision)?scheduled.exactRevision:
        s.producerStack.length?s.producerStack[s.producerStack.length-1].revision:null,
      scheduled:!!scheduled,notification:Number.isInteger(scheduled?.notification)?scheduled.notification:null,
      scheduledRevision:Number.isInteger(scheduled?.revisionCandidate)?scheduled.revisionCandidate:null};
    s.producerStack.push(token);
    const row=s.producerSummaries.get(source)||{source,records:0,spans:0,
      direct:0,scheduled:0,revisionLinked:0,notificationLinked:0};
    row.spans++;s.producerSummaries.set(source,row);
    return token;
  }
  function endMutationSource(root,token){
    if(!token)return;
    const s=byRoot.get(root);
    if(!s)return;
    const top=s.producerStack[s.producerStack.length-1];
    if(top!==token)return; // Do not misattribute a mismatched nesting context.
    const records=s.observer?.takeRecords()||[];
    if(records.length)recordOutsideNotify(s,records,token);
    s.producerStack.pop();
  }
  // Capture the scheduling relationship at scheduling time. A notification's
  // revision is a temporal candidate unless separately passed as an exact
  // operation revision; never upgrade it to causal proof.
  function captureSchedule(root,source){
    if(!enabled)return null;
    const s=byRoot.get(root);
    if(!s)return null;
    return {source:safe(source),notification:s.inNotify?s.notifications:null,
      revisionCandidate:s.inNotify?s.current?.revisionCandidate??null:null,
      exactRevision:s.inNotify && exactKnownRevision(s,s.current?.exactRevision)
        ?s.current.exactRevision:null};
  }
  // Captures only structural operation metadata, never inputs, response data,
  // network URLs, credentials, receipts, or element content.
  function beginOperation(root,appId,intentId,kind,risk,origin='dispatch'){
    if(!enabled)return null;
    const s=sessionFor(root,appId);
    const revision=++s.nextRevision;
    s.activeOperations.set(revision,{revision,intentId:safe(intentId),kind:safe(kind),
      risk:safe(risk),origin:safe(origin),phase:'dispatched',receipts:0,notifications:0});
    return revision;
  }
  function operationPhase(root,revision,phase){
    if(!enabled||!revision)return;
    const op=byRoot.get(root)?.activeOperations.get(revision);
    if(op)op.phase=safe(phase);
  }
  function operationReceipt(root,intentId,disposition,receiptSequence=null){
    if(!enabled)return;
    const s=byRoot.get(root);if(!s)return;
    const matches=[...s.activeOperations.values()].filter(op=>op.intentId===safe(intentId));
    if(matches.length!==1)return;
    const op=matches[0];op.receipts++;op.phase=safe(disposition);s.asyncCorrelation.receiptMatches++;
    if(Number.isInteger(receiptSequence)&&receiptSequence>0){
      op.lastReceiptSequence=receiptSequence;
      s.receiptRevisions.set(receiptSequence,op.revision);
      if(s.receiptRevisions.size>300)s.receiptRevisions.delete(s.receiptRevisions.keys().next().value);
    }
    return op.revision;
  }
  function revisionForReceipt(root,receiptSequence){
    if(!enabled||!Number.isInteger(receiptSequence))return null;
    return byRoot.get(root)?.receiptRevisions.get(receiptSequence)||null;
  }
  function finishOperation(root,revision){
    if(!enabled||!revision)return;
    const s=byRoot.get(root);const op=s?.activeOperations.get(revision);
    if(!op)return;
    s.activeOperations.delete(revision);
    s.operationHistory.push({...op});
    if(s.operationHistory.length>300){s.operationHistory.shift();s.operationsEvicted++;}
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
  function invalidate(root,name,revision=null){
    if(!enabled)return;
    const s=byRoot.get(root);
    if(!s)return;
    bump(s,name);
    const op=Number.isInteger(revision)?s.activeOperations.get(revision):null;
    s.invalidationHistory.push({source:safe(name),revision:op?.revision??null,
      provenance:op?'instrumented-operation':'unresolved-external-source'});
    if(s.invalidationHistory.length>300)s.invalidationHistory.shift();
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
  function beforeNotify(root,appId,state,receiptSequence,exactRevision=null){
    if(!enabled)return;
    const s=sessionFor(root,appId);
    const next=snapshotState(state),diff=changes(s.states,next);
    const bootstrap=s.states===null&&next!==null;
    if(s.receipts!==null && s.receipts!==receiptSequence)bump(s,'receipts');
    // Freshness predicates can expire without a state write: invalidate on each
    // incumbent notification, rather than treating a clock-dependent skip safe.
    bump(s,'clock');
    const currentEpochs=new Map(s.externalEpochs), changedExternal=new Set();
    for(const [name,version] of currentEpochs)
      if(s.previousEpochs?.get(name)!==version)changedExternal.add(name);
    const pending=solePending(s);
    if(pending){pending.notifications++;s.asyncCorrelation.notificationRevisions++;}
    else s.asyncCorrelation.unknownNotificationOwners++;
    s.current={known:diff.known,keys:new Set(diff.ids),changedExternal,
      exactRevision:exactKnownRevision(s,exactRevision)?exactRevision:null,
      revisionCandidate:pending?.revision??null,
      correlation:pending?'temporal-only':s.activeOperations.size>1?'ambiguous':'unassociated'};
    s.previousEpochs=currentEpochs;s.states=next;s.receipts=receiptSequence;
    s.notifications++;s.inNotify=true;
    if(bootstrap)s.stats.bootstrapBatches++;
    else if(!diff.known)s.stats.unknownBatches++;
    const previous=s.observer?.takeRecords()||[];
    if(previous.length)recordOutsideNotify(s,previous);
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
        remember(s,{type:'consumer',notification:s.notifications,consumer:key,prediction:predicted,
          revisionCandidate:s.current?.revisionCandidate??null,
          exactOperationRevision:s.current?.exactRevision??null,
          correlation:s.current?.correlation||'unassociated',
          changedStateIds:[...s.current.keys].sort().slice(0,64),
          changedStateIdsTruncated:s.current.keys.size>64,
          changedExternal:[...s.current.changedExternal].sort(),
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
      correlation:{schema:'mcel.reactivity.revision-correlation.v1',
        nextRevision:s.nextRevision,operationsEvicted:s.operationsEvicted,
        operations:[...s.operationHistory,...s.activeOperations.values()].map(op=>({...op})),
        producers:{...s.sourceCoverage,
          partitionInvariant:s.sourceCoverage.identified+s.sourceCoverage.unclassified===s.stats.asyncMutations,
          classificationInvariant:s.sourceCoverage.operationLinked+
            s.sourceCoverage.independentUi+s.sourceCoverage.unexplained===s.stats.asyncMutations,
          classificationRules:{operationLinked:'exact instrumented operation revision only',
            independentUi:'identified DOM producer without a proven operation revision',
            unexplained:'no instrumented DOM producer'},
          unclassifiedStructure:{...s.sourceCoverage.unclassifiedStructure},
          sources:[...s.producerSummaries.values()].map(row=>({...row})).sort((a,b)=>b.records-a.records)},
        invalidations:s.invalidationHistory.map(event=>({...event})),
        async:{...s.asyncCorrelation},
        guarantee:'temporal-candidates-only-no-causal-certification'},
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
        'Instrumented producer spans prove synchronous DOM-write ownership only',
        'Identified independent UI means no exact operation link, not proof of independence from all requests',
        'Asynchronous correlation identifies pending revisions but does not establish causal ownership',
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
  global.MCELReactivityShadowOracle=Object.freeze({beforeNotify,capture,afterNotify,invalidate,
    beginOperation,operationPhase,operationReceipt,revisionForReceipt,finishOperation,
    beginMutationSource,endMutationSource,captureSchedule,release,report});
})(typeof window!=='undefined'?window:globalThis);
