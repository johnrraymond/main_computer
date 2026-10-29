/* MCEL lifecycle acid lab: actual read-only universal renderer, orthogonal screening,
 * and element-controlled drill-in. Never mounts operational Task Manager actions.
 */
(function(global){
'use strict';
const SCHEMA='mcel.lifecycle-acid-lab.v1';
// L18 mixed-level orthogonal screening design, with a verified replacement
// for the eighth column (derived by constrained assignment, not copied).
// Every factor pair is independently checked for exact balance at startup.
const L18=[
[1,1,1,1,1,1,1,2],
[1,1,2,2,2,2,2,1],
[1,1,3,3,3,3,3,3],
[1,2,1,1,2,2,3,3],
[1,2,2,2,3,3,1,2],
[1,2,3,3,1,1,2,1],
[1,3,1,2,1,3,2,3],
[1,3,2,3,2,1,3,2],
[1,3,3,1,3,2,1,1],
[2,1,1,3,3,2,2,2],
[2,1,2,1,1,3,3,1],
[2,1,3,2,2,1,1,3],
[2,2,1,2,3,1,3,1],
[2,2,2,3,1,2,1,3],
[2,2,3,1,2,3,2,2],
[2,3,1,3,2,3,1,1],
[2,3,2,1,3,1,2,3],
[2,3,3,2,1,2,3,2]
].map(row=>row.map(x=>x-1));
const FACTORS=Object.freeze([
  {key:'data',name:'Data density',levels:['normal','stressed']},
  {key:'trigger',name:'Reveal request',levels:['select','hover','select near edge']},
  {key:'source',name:'Source observation',levels:['unchanged','partial update','authoritative removal']},
  {key:'interaction',name:'Interaction',levels:['idle','native selection','floating inspection']},
  {key:'host',name:'Host capabilities',levels:['all allowed','scroll denied','overlay denied']},
  {key:'retirement',name:'Retirement clock',levels:['resume','reset','continue']},
  {key:'width',name:'Presentation width',levels:['360px','620px','900px']},
  {key:'identity',name:'Record identity',levels:['verified','unverified','new generation']}
]);
const clone=value=>JSON.parse(JSON.stringify(value));
const choices=row=>Object.fromEntries(FACTORS.map((factor,i)=>[factor.key,factor.levels[row[i]]]));
function validateArray(){
  if(L18.length!==18 || L18.some(row=>row.length!==FACTORS.length))throw Error('L18_MATRIX_INVALID');
  for(let a=0;a<FACTORS.length;a++)for(let b=a+1;b<FACTORS.length;b++){
    const counts=new Map();
    for(const row of L18){
      if(row[a]<0 || row[a]>=FACTORS[a].levels.length || row[b]<0 || row[b]>=FACTORS[b].levels.length)
        throw Error('L18_LEVEL_INVALID');
      const key=row[a]+':'+row[b];counts.set(key,(counts.get(key)||0)+1);
    }
    const expected=18/(FACTORS[a].levels.length*FACTORS[b].levels.length);
    if(counts.size!==FACTORS[a].levels.length*FACTORS[b].levels.length ||
       [...counts.values()].some(x=>x!==expected))throw Error('L18_NOT_ORTHOGONAL_'+a+'_'+b);
  }
  return true;
}
validateArray();
const EXPERIMENTS=L18.map((row,i)=>({id:'L18-'+String(i+1).padStart(2,'0'),index:i,settings:choices(row)}));
function randomOrder(n,seed){let x=seed>>>0||1;const arr=Array.from({length:n},(_,i)=>i);
  for(let i=arr.length-1;i>0;i--){x^=x<<13;x^=x>>>17;x^=x<<5;const j=(x>>>0)%(i+1);[arr[i],arr[j]]=[arr[j],arr[i]];}
  return arr;
}
function makePlan(data,identity){
  const plan=clone(global.McelLifecycleAcidFixtures[data]);
  if(!plan)throw Error('ACID_FIXTURE_UNAVAILABLE');
  if(identity!=='unverified'){
    if(!plan.collection.spec.sourceFields.includes('acidGeneration'))plan.collection.spec.sourceFields.push('acidGeneration');
    plan.collection.spec.reconciliation={generationFrom:'acidGeneration'};
    plan.collection.rows.forEach((row,i)=>{row.acidGeneration='g'+i;});
  }
  return plan;
}
function policyFor(settings){return {
  schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'remove',
  delayMs:2000,pauseWhen:['text-selected','focused','hovered','expanded'],onPause:settings.retirement,maxRetained:40
};}
function environment(){
  if(!global.McelUniversalSemanticLayoutLab?.mount||!global.McelLifecycleAcidFixtures||
     !global.McelPresentationNegotiation?.negotiate)throw Error('MCEL_14_7_LIFECYCLE_RUNTIME_UNAVAILABLE');
}
function invariants(instance,root,expected,trace){
  const ids=instance.collection.ids;
  const assertions={
    sourceIds:JSON.stringify([...ids].sort())===JSON.stringify([...expected].sort()),
    uniqueSourceIds:new Set(ids).size===ids.length,
    noInventedRows:root.querySelectorAll('mcel-row').length===ids.length+instance.collection.retirement.ids.length,
    layout:!!instance.collection.layout,
    canonicalLayout:instance.tree.refresh().status==='pass',
    noOperationalControls:root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create').length===0
  };
  trace.push({event:'invariants',assertions});
  return assertions;
}
function applyEvent(session,event,trace){
  const {instance,plan,root}=session;
  const active=instance.collection.ids;
  const row=root.querySelector('mcel-row');
  const id=row?.dataset.rowId;
  if(!row||!id)return {ok:false,reason:'no-mounted-row'};
  if(event==='select' || event==='hover'){
    const result=instance.collection.request(id,event==='select'?'onSelect':'onHover');
    trace.push({event,ok:result.ok,mode:result.mode||null,attempts:result.attempts});
    return result;
  }
  if(event==='select near edge'){
    const list=root.querySelector('mcel-collection');
    list.scrollTop=list.scrollHeight;
    const result=instance.collection.request(id,'onSelect');
    trace.push({event,scrollTop:list.scrollTop,ok:result.ok,mode:result.mode||null,attempts:result.attempts});
    return result;
  }
  if(event==='native selection'){
    const leaf=row.querySelector('mcel-field .field-value');
    if(!leaf)return {ok:false,reason:'missing-selection-leaf'};
    const range=document.createRange();range.selectNodeContents(leaf);
    const selection=document.getSelection();selection.removeAllRanges();selection.addRange(range);
    const result={ok:!selection.isCollapsed,text:selection.toString()};trace.push({event,...result});return result;
  }
  if(event==='clear selection'){
    document.getSelection()?.removeAllRanges();const result={ok:true};trace.push({event,...result});return result;
  }
  if(event==='scroll'){
    const list=root.querySelector('mcel-collection');
    list.scrollTop+=70;list.dispatchEvent(new WheelEvent('wheel',{bubbles:true}));
    const result={ok:true,scrollTop:list.scrollTop};trace.push({event,...result});return result;
  }
  if(event==='source unchanged' || event==='source update' || event==='source remove'){
    const next=clone(session.currentPlan);session.sequence++;
    if(event==='source update'){
      const target=next.collection.rows[0];if(!target)return {ok:false,reason:'no-live-row-to-update'};target.name='acid-updated-'+session.sequence;
      if(target.mcelCanonicalRecord?.properties?.name)target.mcelCanonicalRecord.properties.name.raw=target.name;
      if(session.settings.identity==='new generation')target.acidGeneration='replacement-'+session.sequence;
      if(session.settings.source==='partial update')next.collection.rows=[target];
    }else if(event==='source remove')next.collection.rows=next.collection.rows.slice(1);
    const observation={schema:'mcel.source-observation.v1',complete:event==='source unchanged'||event==='source remove'||session.settings.source!=='partial update',sequence:session.sequence};
    const receipt=instance.update(next,{observation});
    if(receipt.ok){if(observation.complete)session.expected=next.collection.rows.map(r=>r.mcelIdentity);
      if(observation.complete)session.currentPlan=next;else {const updated=next.collection.rows[0];
        session.currentPlan.collection.rows=session.currentPlan.collection.rows.map(row=>row.mcelIdentity===updated.mcelIdentity?updated:row);}}
    trace.push({event,ok:receipt.ok,reason:receipt.reason||null,observation,live:instance.collection.ids.length,
      retired:instance.collection.retirement.ids.length});
    return receipt;
  }
  if(event==='stale observation'){
    const next=clone(session.currentPlan);
    const result=instance.update(next,{observation:{schema:'mcel.source-observation.v1',complete:true,sequence:session.sequence}});
    const valid=result.ok===false;
    trace.push({event,ok:valid,rejected:!result.ok,reason:result.reason||result.code||null});
    return {ok:valid,rejected:!result.ok};
  }
  if(event==='fault injection'){
    const engine=global.McelPresentationNegotiation;
    let marker=false,unwound=0,calls=0;
    const provider={assess:()=>({ok:true})};
    const pol={schema:engine.SCHEMA,events:{onSelect:[
      {mode:'inline-expand',requires:['geometry.child-resize']},
      {mode:'floating-over-source',requires:['overlay.anchor-source']}]}};
    const response=engine.negotiate(pol,'onSelect',{'geometry.child-resize':provider,
      'overlay.anchor-source':provider},{beginTransaction:()=>engine.createTransaction()},(_choice,{transaction})=>{
        calls++;if(calls===1){transaction.addUndo(()=>{marker=false;unwound++;});marker=true;
          throw Error('ACID_INJECTED_POST_MUTATION_FAILURE');}
        if(marker)throw Error('ACID_FALLBACK_ENTERED_WITH_DIRTY_STATE');
        return {ok:true};
      });
    const result={ok:response.ok && response.mode==='floating-over-source' && !marker && unwound===1,
      mode:response.mode,unwound,marker,attempts:response.attempts};
    trace.push({event,...result});return result;
  }
  return {ok:false,reason:'unknown-event'};
}
function start(settings,stage){
  environment();
  const plan=makePlan(settings.data,settings.identity);
  const frame=document.createElement('section');frame.className='mcel-lifecycle-acid__specimen';
  frame.style.width=settings.width;frame.style.maxWidth='100%';frame.style.boxSizing='border-box';
  stage.append(frame);
  const capabilities={allowScroll:settings.host!=='scroll denied',allowOverlay:settings.host!=='overlay denied'};
  const instance=global.McelUniversalSemanticLayoutLab.mount(frame,plan,{lab:true,presentationNegotiation:true,
    sourceRetirement:true,capabilities,retirementPolicy:policyFor(settings)});
  const list=frame.querySelector('mcel-collection');
  if(list){list.style.maxHeight='200px';list.style.overflowY='auto';}
  const session={settings,plan,currentPlan:plan,frame,root:frame,instance,sequence:0,expected:plan.collection.rows.map(r=>r.mcelIdentity),
    destroy(){const selection=document.getSelection();
      if(selection && !selection.isCollapsed && selection.containsNode(frame,true))selection.removeAllRanges();
      instance.destroy();frame.remove();}};
  return session;
}
async function runOne(settings,{stage,retain=false}={}){
  let session=null;const trace=[];let verdict='pass';let failure=null;const t0=performance.now();
  try{
    session=start(settings,stage);
    // Establish an ordered authoritative baseline, including an unchanged
    // unverified-identity snapshot (must never invent retired ghosts).
    const baseline=session.instance.update(clone(session.plan),{observation:{
      schema:'mcel.source-observation.v1',complete:true,sequence:++session.sequence}});
    trace.push({event:'ordered-baseline',ok:baseline.ok,reason:baseline.code||null});
    if(!baseline.ok)throw Error('ORDERED_BASELINE_REFUSED:'+String(baseline.code));
    if(settings.identity==='new generation'){
      const replacement=applyEvent(session,'source update',trace);
      if(!replacement.ok)throw Error('IDENTITY_REPLACEMENT_REFUSED:'+String(replacement.code||replacement.reason));
    }
    if(settings.interaction==='native selection')applyEvent(session,'native selection',trace);
    if(settings.interaction==='floating inspection')applyEvent(session,'hover',trace);
    const revelation=applyEvent(session,settings.trigger,trace);
    // Refusals are observations, not automatically invariant failures.
    if(revelation.ok!==true && !revelation.reason)throw Error('REVEAL_REFUSAL_MISSING_REASON');
    if(settings.source==='partial update')applyEvent(session,'source update',trace);
    if(settings.source==='authoritative removal')applyEvent(session,'source remove',trace);
    const checks=invariants(session.instance,session.root,session.expected,trace);
    const shouldFloat=settings.trigger==='hover'?settings.host!=='overlay denied':
      settings.host==='scroll denied';
    const expectedMode=settings.trigger==='hover'?(shouldFloat?'floating-over-source':null):
      (shouldFloat?'floating-over-source':'inline-expand');
    checks.declaredFallback=(!expectedMode ? revelation.ok===false : revelation.mode===expectedMode);
    // Test the real retirement state, rather than merely that the policy parses.
    const retained=session.instance.collection.retirement;
    const entries=retained?.entries||[];
    checks.retirementPolicy=entries.every(entry=>entry && ['retiring','protected'].includes(entry.state));
    trace.push({event:'policy-outcomes',expectedMode,actualMode:revelation.mode||null,
      retirement:entries,checks:{declaredFallback:checks.declaredFallback,retirementPolicy:checks.retirementPolicy}});
    const fault=applyEvent(session,'fault injection',trace);
    checks.transactionRollback=fault.ok===true;
    // An authoritative source update may not be followed by a duplicate sequence.
    if(true){
      const stale=applyEvent(session,'stale observation',trace);
      checks.staleObservationRejected=stale.ok===true;
    }

    if(Object.values(checks).some(ok=>!ok))throw Error('INVARIANT_FAILED:'+Object.entries(checks).filter(([,ok])=>!ok).map(([k])=>k).join(','));
    return {verdict,settings,checks,trace,elapsedMs:Math.round((performance.now()-t0)*10)/10,
      live:session.instance.collection.ids.length,retired:session.instance.collection.retirement.ids.length,
      reveal:revelation.mode||revelation.reason||null,session:retain?session:undefined};
  }catch(error){verdict='fail';failure=String(error);trace.push({event:'error',failure});
    return {verdict,settings,failure,trace,elapsedMs:Math.round((performance.now()-t0)*10)/10,session:retain?session:undefined};
  }finally{if(!retain)session?.destroy();}
}
function build(root){
  if(!root||root.dataset.initialized==='true')return;
  root.dataset.initialized='true';
  const matrix=root.querySelector('#mcel-lifecycle-acid-matrix');
  const overview=root.querySelector('#mcel-lifecycle-acid-overview');
  const factors=root.querySelector('#mcel-lifecycle-acid-factors');
  const drill=root.querySelector('#mcel-lifecycle-acid-drill');
  const report=root.querySelector('#mcel-lifecycle-acid-trace');
  const preview=root.querySelector('#mcel-lifecycle-acid-preview');
  const follow=root.querySelector('#mcel-lifecycle-acid-followups');
  const seedInput=root.querySelector('#mcel-lifecycle-acid-seed');
  const repetitionsInput=root.querySelector('#mcel-lifecycle-acid-repetitions');
  const create=(tag,text,cls)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;if(cls)el.className=cls;return el;};
  const state={runs:new Map(),current:{...EXPERIMENTS[0].settings},session:null,busy:false,last:null,seed:1475,
    activeIndex:0,custom:false,reviews:new Map(),events:new Map(),lastPointerRow:null,stepIndex:0};
  const casePicker=root.querySelector('#mcel-lifecycle-acid-case-picker');
  const caseInfo=root.querySelector('#mcel-lifecycle-acid-case-info');
  const feedback=root.querySelector('#mcel-lifecycle-acid-feedback');
  const notes=root.querySelector('#mcel-lifecycle-acid-notes');
  const reviewProgress=root.querySelector('#mcel-lifecycle-acid-review-progress');
  const workbench=root.querySelector('#mcel-lifecycle-acid-workbench');
  const stepStatus=root.querySelector('#mcel-lifecycle-acid-step-status');
  const stepButton=root.querySelector('#mcel-lifecycle-acid-step');
  function scenarioSteps(settings){
    const steps=[];
    if(settings.interaction==='native selection')steps.push({label:'Start a native selection',event:'native selection'});
    if(settings.interaction==='floating inspection')steps.push({label:'Open a floating inspection',event:'hover'});
    steps.push({label:'Request '+settings.trigger,event:settings.trigger});
    steps.push(settings.source==='partial update'?{label:'Deliver a partial source observation',event:'source update'}:
      settings.source==='authoritative removal'?{label:'Authoritatively remove a record',event:'source remove'}:
      {label:'Replay an unchanged complete observation',event:'source unchanged'});
    return steps;
  }
  function updateStep(){
    const steps=scenarioSteps(state.current),step=steps[state.stepIndex];
    stepStatus.textContent=step?`Step ${state.stepIndex+1}/${steps.length}: ${step.label} (then inspect the specimen)`:'All planned conditions applied. Explore further, or restart the experiment.';
    stepButton.disabled=!step;
  }
  const currentId=()=>EXPERIMENTS[state.activeIndex].id;
  const snapshot=()=>state.session?{
    live:state.session.instance.collection.ids.length,
    retired:state.session.instance.collection.retirement.ids.length,
    expanded:!!preview.querySelector('mcel-row .mcel-inline-details, mcel-row [data-mcel-inline-detail]'),
    floating:!!preview.querySelector('[data-mcel-floating-card],.mcel-floating-card,.mcel-floating-detail'),
    scrollTop:Math.round(preview.querySelector('mcel-collection')?.scrollTop||0),
    selectedText:String(document.getSelection()?.toString()||'').slice(0,160)
  }:null;
  function recordEvent(event,detail){
    const id=currentId(),list=state.events.get(id)||[];
    list.push({event,detail:detail||null,state:snapshot(),at:new Date().toISOString(),settings:{...state.current}});
    if(list.length>150)list.shift();state.events.set(id,list);
  }
  function showProgress(){
    const reviewed=[...state.reviews.values()].filter(r=>r.verdict).length;
    reviewProgress.textContent=`${reviewed} of 18 reviewed · ${[...state.reviews.values()].filter(r=>r.verdict==='broken').length} flagged`;
  }
  function showReview(){
    const review=state.reviews.get(currentId())||{};
    notes.value=review.notes||'';
    root.querySelectorAll('[data-mcel-acid-verdict]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mcelAcidVerdict===review.verdict)));
    showProgress();
  }
  function showCase(){
    const exp=EXPERIMENTS[state.activeIndex];casePicker.value=exp.id;
    caseInfo.replaceChildren();
    const heading=create('strong',`${exp.id} · Experiment ${state.activeIndex+1} of 18${state.custom?' · edited variant':''}`);
    caseInfo.append(heading);
    const description=create('p',FACTORS.map(f=>`${f.name}: ${state.current[f.key]}`).join('  ·  '));caseInfo.append(description);
    const guidance=create('p',`Try ${state.current.trigger==='hover'?'hovering a row':state.current.trigger==='select near edge'?'selecting a row near the lower edge':'selecting a row'}, then ${state.current.source==='authoritative removal'?'removing the source record':state.current.source==='partial update'?'sending a partial update':'checking an unchanged source snapshot'}. Host: ${state.current.host}; retirement clock: ${state.current.retirement}.`);
    guidance.className='mcel-lifecycle-acid__guidance';caseInfo.append(guidance);
    renderMatrix();showReview();updateStep();
  }
  function mountInteractive(){
    if(state.busy)return false;
    safe();state.stepIndex=0;updateStep();
    try{
      state.session=start({...state.current},preview);
      state.last={settings:{...state.current},verdict:'manual-interaction',caseId:currentId()};
      feedback.textContent=`${currentId()} live. Try mouse, keyboard, copy and the test buttons. Then record your judgment.`;
      recordEvent('specimen-mounted');
      return true;
    }catch(e){
      feedback.textContent=`Could not mount ${currentId()}: ${String(e)}`;
      recordEvent('mount-failed',String(e));return false;
    }
  }
  function activateCase(index,{scroll=true}={}){
    if(state.busy)return;
    state.activeIndex=(index+EXPERIMENTS.length)%EXPERIMENTS.length;
    state.current={...EXPERIMENTS[state.activeIndex].settings};state.custom=false;
    renderFactorControls(state.current);showCase();mountInteractive();
    if(scroll)workbench.scrollIntoView({block:'start'});
  }

  const safe=()=>{state.session?.destroy();state.session=null;preview.replaceChildren();};
  const selector=(factor,current)=>{
    const label=create('label',factor.name),select=create('select');select.dataset.factor=factor.key;
    select.setAttribute('aria-label',factor.name);
    for(const option of factor.levels){const el=create('option',option);el.value=option;select.append(el);}
    select.value=current[factor.key];select.addEventListener('change',()=>{state.current[factor.key]=select.value;state.custom=true;showCase();feedback.textContent='Settings edited. Click Try edited configuration to mount this variant; the L18 case remains unchanged.';});
    label.append(select);return label;
  };
  function renderFactorControls(settings){factors.replaceChildren(...FACTORS.map(f=>selector(f,settings)));}
  function renderOverview(){
    const counts={pass:0,fail:0};for(const r of state.runs.values())if(r?.verdict in counts)counts[r.verdict]++;
    overview.textContent=`L18 orthogonal screening · ${state.runs.size}/18 configurations · ${counts.pass} passed · ${counts.fail} invariant failures · seed ${state.seed}`;
  }
  function analyze(){
    const entries=[...state.runs.values()];
    const lines=FACTORS.map(f=>({factor:f.name,levels:f.levels.map(level=>{
      const sub=entries.filter(r=>r.settings[f.key]===level);
      return {level,n:sub.length,fail:sub.filter(r=>r.verdict==='fail').length,
        averageMs:sub.length?Math.round(sub.reduce((sum,r)=>sum+r.averageMs,0)/sub.length):null};
    })}));
    follow.replaceChildren();
    const title=create('p','Main-effect screening (failures / executions by factor level). Small samples; interactions are confounded.');follow.append(title);
    const table=create('table');const tr=create('tr');for(const x of ['Factor','Level 1','Level 2','Level 3'])tr.append(create('th',x));table.append(tr);
    for(const line of lines){const tr=create('tr');tr.append(create('td',line.factor));
      for(let i=0;i<3;i++){const v=line.levels[i];tr.append(create('td',v?`${v.level}: ${v.fail}/${v.n} · ${v.averageMs===null?'—':v.averageMs+'ms'}`:'—'));}table.append(tr);}
    follow.append(table);
  }
  function renderMatrix(){matrix.replaceChildren();
    const table=create('table');const heading=create('tr');['Run',...FACTORS.map(f=>f.name),'Your verdict','Automated','Mean / worst','Interact'].forEach(h=>heading.append(create('th',h)));table.append(heading);
    for(const exp of EXPERIMENTS){const tr=create('tr');tr.dataset.run=exp.id;tr.dataset.active=String(state.activeIndex===exp.index);
      tr.append(create('td',exp.id));FACTORS.forEach(f=>tr.append(create('td',exp.settings[f.key])));
      const review=state.reviews.get(exp.id);const human=create('td',review?.verdict||'not reviewed');human.dataset.verdict=review?.verdict||'pending';tr.append(human);const result=state.runs.get(exp.id);const status=create('td',result?.verdict||'not run');status.dataset.status=result?.verdict||'pending';tr.append(status);
      tr.append(create('td',result?`${result.averageMs} / ${result.worstMs} ms`:'—'));
      const cell=create('td'),btn=create('button','Try live');btn.type='button';btn.setAttribute('aria-label','Open interactive experiment '+exp.id);btn.addEventListener('click',()=>activateCase(exp.index));cell.append(btn);tr.append(cell);table.append(tr);
    }matrix.append(table);
  }
  async function screen(){if(state.busy)return;state.busy=true;safe();state.runs.clear();state.seed=Math.max(1,Math.min(2147483647,Number(seedInput.value)||1475));
    const repetitions=Math.max(1,Math.min(5,Math.floor(Number(repetitionsInput.value)||2)));
    renderMatrix();renderOverview();const stage=document.createElement('div');stage.className='mcel-lifecycle-acid__offscreen-stage';stage.setAttribute('aria-hidden','true');root.append(stage);
    const order=randomOrder(18,state.seed);
    try{for(let i=0;i<order.length;i++){
      const exp=EXPERIMENTS[order[i]],replicas=[];
      for(let repeat=0;repeat<repetitions;repeat++)replicas.push(await runOne(exp.settings,{stage}));
      const averageMs=Math.round(replicas.reduce((sum,r)=>sum+r.elapsedMs,0)/repetitions*10)/10;
      state.runs.set(exp.id,{id:exp.id,settings:exp.settings,
        verdict:replicas.every(r=>r.verdict==='pass')?'pass':'fail',
        averageMs,worstMs:Math.max(...replicas.map(r=>r.elapsedMs)),replicas});
      renderMatrix();renderOverview();
      if(i%3===0)await new Promise(resolve=>setTimeout(resolve,0));
    }
    analyze();report.textContent=JSON.stringify({schema:SCHEMA,seed:state.seed,repetitions,orthogonal:true,runs:[...state.runs.values()]},null,2);
    }finally{stage.remove();state.busy=false;mountInteractive();}
  }
  function runFocused(){if(state.busy)return;mountInteractive();showCase();recordEvent('edited-configuration-mounted');}
  async function pairwise(){if(state.busy)return;state.busy=true;safe();const a=root.querySelector('#mcel-lifecycle-acid-pair-a').value;
    const b=root.querySelector('#mcel-lifecycle-acid-pair-b').value;
    if(a===b){report.textContent='Choose two different factors.';state.busy=false;return;}
    const stage=document.createElement('div');stage.className='mcel-lifecycle-acid__offscreen-stage';stage.setAttribute('aria-hidden','true');root.append(stage);
    const results=[];try{
      for(const av of FACTORS.find(f=>f.key===a).levels)for(const bv of FACTORS.find(f=>f.key===b).levels){
        const settings={...state.current,[a]:av,[b]:bv};results.push(await runOne(settings,{stage}));
      }
      report.textContent=JSON.stringify({kind:'focused-pairwise',factorA:a,factorB:b,
        warning:'Conditional local drill-in; not an orthogonal design or an interaction estimate without repetitions.',results},null,2);
    }finally{stage.remove();state.busy=false;mountInteractive();}
  }
  async function liveAction(event){if(!state.session){report.textContent='Mount a focused specimen first.';return;}
    const trace=[];try{let result;
      if(event==='retirement expiry'){await new Promise(resolve=>setTimeout(resolve,2200));
        result={ok:true,retired:state.session.instance.collection.retirement.ids.length};
        trace.push({event,retirement:state.session.instance.collection.retirement});
      }else result=applyEvent(state.session,event,trace);
      const checks=invariants(state.session.instance,state.session.root,state.session.expected,trace);
      report.textContent=JSON.stringify({event,result,checks,trace},null,2);recordEvent('action-'+event,{result,checks});feedback.textContent=`${currentId()}: ${event} → ${result?.ok?'completed':'refused or failed'} · ${Object.values(checks).every(Boolean)?'invariants hold':'check trace for invariant failure'}`;
    }catch(e){report.textContent=JSON.stringify({event,error:String(e),trace},null,2);feedback.textContent=`${currentId()}: ${event} failed: ${String(e)}`;recordEvent('action-error-'+event,String(e));}
  }
  EXPERIMENTS.forEach(exp=>{const option=create('option',`${exp.id} · ${exp.settings.trigger} / ${exp.settings.source}`);option.value=exp.id;casePicker.append(option);});
  casePicker.addEventListener('change',()=>activateCase(EXPERIMENTS.findIndex(exp=>exp.id===casePicker.value)));
  stepButton.addEventListener('click',async()=>{if(state.busy)return;const steps=scenarioSteps(state.current);const step=steps[state.stepIndex];if(!step)return;state.stepIndex++;updateStep();await liveAction(step.event);recordEvent('planned-scenario-step',{index:state.stepIndex,total:steps.length,label:step.label});});
  root.querySelector('#mcel-lifecycle-acid-prev').addEventListener('click',()=>activateCase(state.activeIndex-1));
  root.querySelector('#mcel-lifecycle-acid-next').addEventListener('click',()=>activateCase(state.activeIndex+1));
  root.querySelector('#mcel-lifecycle-acid-restart').addEventListener('click',()=>{recordEvent('restart');activateCase(state.activeIndex,{scroll:false});});
  root.querySelectorAll('[data-mcel-acid-verdict]').forEach(button=>button.addEventListener('click',()=>{
    const id=currentId(),record=state.reviews.get(id)||{};
    state.reviews.set(id,{...record,verdict:button.dataset.mcelAcidVerdict,notes:notes.value});
    recordEvent('human-verdict',button.dataset.mcelAcidVerdict);showReview();renderMatrix();
  }));
  notes.addEventListener('input',()=>{const id=currentId(),record=state.reviews.get(id)||{};state.reviews.set(id,{...record,notes:notes.value});showProgress();});
  preview.addEventListener('pointerover',e=>{
    const id=e.target.closest('mcel-row')?.dataset.rowId;if(!id||state.lastPointerRow===id)return;
    state.lastPointerRow=id;recordEvent('native-pointer-over-row',id);
  });
  preview.addEventListener('click',e=>{const id=e.target.closest('mcel-row')?.dataset.rowId;if(id){recordEvent('native-click-row',id);feedback.textContent=`${currentId()}: clicked row ${id}; inspect its presentation.`;}});
  preview.addEventListener('scroll',e=>{if(e.target.matches?.('mcel-collection'))recordEvent('native-scroll',Math.round(e.target.scrollTop));},true);
  document.addEventListener('selectionchange',()=>{
    if(!state.session)return;
    const selected=document.getSelection();if(!selected||selected.isCollapsed)return;
    const within=selected.anchorNode&&preview.contains(selected.anchorNode);
    if(within)feedback.textContent=`${currentId()}: text selected — try Ctrl+C, update the source, or release selection.`;
  });
  root.querySelector('#mcel-lifecycle-acid-screen').addEventListener('click',()=>void screen());
  root.querySelector('#mcel-lifecycle-acid-focused').addEventListener('click',()=>void runFocused());
  root.querySelector('#mcel-lifecycle-acid-pair-run').addEventListener('click',()=>void pairwise());
  root.querySelector('#mcel-lifecycle-acid-clear').addEventListener('click',()=>{recordEvent('specimen-released');safe();report.textContent='Specimen released.';feedback.textContent='Specimen released. Click Restart this experiment to try again.';});
  root.querySelector('#mcel-lifecycle-acid-export').addEventListener('click',()=>{
    const data={schema:SCHEMA,seed:state.seed,experiments:[...state.runs.values()],focused:state.last?{...state.last,session:undefined}:null,manualReviews:EXPERIMENTS.map(exp=>({id:exp.id,settings:exp.settings,...(state.reviews.get(exp.id)||{verdict:null,notes:''}),events:state.events.get(exp.id)||[]})),humanReviewed:[...state.reviews.values()].filter(r=>r.verdict).length};
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
    const a=create('a');a.href=url;a.download='mcel-lifecycle-acid-'+state.seed+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  root.querySelectorAll('[data-mcel-acid-action]').forEach(button=>button.addEventListener('click',()=>void liveAction(button.dataset.mcelAcidAction)));
  for(const name of ['a','b']){
    const select=root.querySelector('#mcel-lifecycle-acid-pair-'+name);
    FACTORS.forEach(f=>{const el=create('option',f.name);el.value=f.key;select.append(el);});
    select.value=name==='a'?'source':'interaction';
  }
  renderFactorControls(state.current);renderOverview();activateCase(0,{scroll:false});
  root.dataset.ready='true';
}
function init(){
  const root=document.querySelector('#mcel-lifecycle-acid');
  if(!root)return;
  if(root.dataset.ready!=='true')build(root);
  const lab=root.closest('#mcel-lab-app');
  if(!lab || lab.dataset.mcelWorkspaceSwitchReady==='true')return;
  const lifecycle=lab.querySelector('#mcel-lab-workspace-lifecycle');
  const blueprint=lab.querySelector('#mcel-lab-workspace-blueprint');
  if(!lifecycle || !blueprint)return;
  const setView=view=>{
    lab.dataset.mcelWorkspaceView=view;
    lifecycle.setAttribute('aria-pressed',String(view==='lifecycle'));
    blueprint.setAttribute('aria-pressed',String(view==='blueprint'));
  };
  lifecycle.addEventListener('click',()=>setView('lifecycle'));
  blueprint.addEventListener('click',()=>setView('blueprint'));
  setView(lab.dataset.mcelWorkspaceView==='lifecycle'?'lifecycle':'blueprint');
  lab.dataset.mcelWorkspaceSwitchReady='true';
}
if(global.document){if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();}
global.McelLifecycleAcidLab=Object.freeze({SCHEMA,FACTORS,L18,EXPERIMENTS,validateArray,runOne,makePlan,randomOrder,init});
})(typeof window!=='undefined'?window:globalThis);
