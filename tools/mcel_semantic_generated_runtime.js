/* MCEL generic generated fixture application runtime. No fetch, live endpoints or effects. */
(() => {
  "use strict";
  const m=window.MCEL_GENERATED_MANIFEST;
  const root=document.getElementById('generated-root');
  if(!m||!root||!window.McelSemanticLayoutInference)throw Error('MCEL_GENERATOR_PREREQUISITE_MISSING');
  const def=m.definitions, regions=m.regions;
  const presentation=m.presentationPlan||null;
  const planned=!!presentation;
  const stateAware=!!m.statePresets;
  const composition=m.compositionPlan||null;
  const labels=composition?.labels||null;
  function fieldLabel(s){return labels?.fields?.[s]||String(s).replace(/[_-]/g, " ");}
  function rowLabel(s){return labels?.rows?.[s]||String(s).replace(/[_-]/g, " ");}
  if(planned&&!presentation.semantic_preservation)throw Error('MCEL_PRESENTATION_SEMANTIC_LOSS');
  const roleById=Object.fromEntries(regions.map(r=>[r.id,r.role]));
  const viewRegion=regions.find(r=>r.role==='primary-content');
  const views=viewRegion.views||[];
  const memberships={};
  for(const view of views) {
    for(const section of [...(view.sections||[]), ...(view.children||[])]) {
      if(section.row && !memberships[section.row]) memberships[section.row]=view.id;
    }
  }
  const readOnly=new Set(['inspect','select-view','refresh_snapshot','search_process','inspect_process','inspect_connection','inspect_operation']);
  let activeView=views.find(v=>v.id==='processes')?.id||views[0]?.id||null;
  let selected=null;
  let syntheticPreset='unavailable';
  let lastStateKey=null;
  let workspaceGroup=null;
  function activeTask(){
    if(!composition)return 'browse';
    const operation=window.__MCEL_STATE_PROOF?.operation?.state;
    if(operation==='failed')return 'recover';
    if(['unknown','running','submitted','acknowledged'].includes(operation))return 'monitor';
    if((document.getElementById('global-search')?.value||'').trim())return 'search';
    if(selected)return 'inspect';
    return 'browse';
  }

  // Mount exactly the serialized MCEL registry tree, retaining element identity
  // and registration evidence in each DOM node. DOM content is fixture-only.
  function elementFor(node,rowType=null,regionRole=null) {
    const contract=def[node.elementId];
    if(!contract)throw Error('Unknown registered MCEL element '+node.elementId);
    const tag=contract.renderer?.htmlTag;
    if(!tag||!tag.includes('-'))throw Error('MCEL element lacks web-component tag');
    const el=document.createElement(tag);
    el.dataset.mcelElementId=node.elementId;
    el.dataset.semanticInstanceId=node.id;
    if(node.elementId==='element.core.region') {
      el.id=node.id;
      el.className='region';
      el.dataset.role=roleById[node.id]||node.props?.semanticRole||'';
      if(planned)el.dataset.recipe=presentation.regions[el.dataset.role]?.recipe||'';
      regionRole=el.dataset.role;
      const h=document.createElement('h2');h.textContent=planned?presentation.regions[regionRole]?.title||node.label:node.label;el.append(h);
    }
    if(node.elementId==='element.core.panel') {
      const rt=node.props?.rowType;
      if(rt) {
        const row=m.fixture[rt];
        if(!row)throw Error('Missing fixture row '+rt);
        el.className='generated-panel record';
        const compression=planned?presentation.rows[rt]:null;
        if(planned&&!compression)throw Error('Missing compression row contract: '+rt);
        if(planned)el.dataset.pattern=innerWidth<=600?'compact-list':'comparison-grid';
        el.dataset.rowType=rt;
        el.dataset.view=memberships[rt]||views[0]?.id||'';
        el.dataset.semanticEntity=row.entity;
        el.setAttribute('role','button');el.tabIndex=0;el.setAttribute('aria-label','Inspect '+node.label);
        const heading=document.createElement('span');heading.className='title';
        heading.textContent=planned
          ? compression.identity.map(field=>String(row.values[field]??'unknown')).join(' · ')
          : node.label;
        el.append(heading);
        if(planned){const kind=document.createElement('span');kind.className='record-entity';
          kind.textContent=rowLabel(rt);el.append(kind);}
        const facts=document.createElement('div');facts.className='facts';
        for(const ch of node.children||[]){
          const child=elementFor(ch,rt,regionRole);
          if(planned){
            if(ch.elementId==='element.core.field'){
              const name=ch.props?.semanticField;
              child.dataset.identityField=String(compression.identity.includes(name));
              child.dataset.deferredField=String(compression.deferred.includes(name));
              child.dataset.summaryField=String(compression.summary.includes(name));
            }
            if(ch.elementId==='element.core.action')child.dataset.inspectAction='true';
          }
          facts.append(child);
        }
        el.append(facts);
        const view=()=>selectRow(rt,el);
        el.addEventListener('click',view);
        el.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' ') {ev.preventDefault();view();}});
      } else {
        el.className=planned&&regionRole==='status-and-attention'?'generated-panel status-cell':'generated-panel';
        const h=document.createElement('h3');h.textContent=node.label;el.append(h);
        if(planned&&regionRole==='status-and-attention'){
          const evidence=document.createElement('small');evidence.className='status-evidence';
          evidence.textContent='Not verified from live source';el.append(evidence);
        }
      }
    }
    if(node.elementId==='element.core.toolbar') {
      el.className='tabs';el.setAttribute('role','tablist');el.setAttribute('aria-label','Application views');
      el.addEventListener('keydown',event=>{
        if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;
        const tabs=[...el.querySelectorAll('[role="tab"]')];
        let index=tabs.indexOf(document.activeElement);
        if(event.key==='Home')index=0;
        else if(event.key==='End')index=tabs.length-1;
        else index=(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
        event.preventDefault();tabs[index].focus();tabs[index].click();
      });
    }
    if(node.elementId==='element.core.action') {
      const intent=node.props?.intent;
      const consequential=node.props?.riskPolicy?.blocked||node.props?.riskPolicy?.risk==='destructive';
      if(intent==='select-view') {
        el.setAttribute('role','tab');el.dataset.view=node.props.viewId;
        el.setAttribute('aria-selected','false');el.tabIndex=-1;el.textContent=node.label;
        el.addEventListener('click',()=>setView(node.props.viewId));
      } else if(consequential) {
        const button=document.createElement('button');button.type='button';button.dataset.consequential=intent||'unknown';
        button.disabled=true;button.title='Disabled in fixture: operation not authorized';button.textContent=node.label;
        el.append(button);
      } else {
        // An inspect-only action cannot dispatch any remote or destructive effects.
        const span=document.createElement('span');span.className='tag';span.textContent=node.label+' · read-only';el.append(span);
      }
    }
    if(node.elementId==='element.core.field') {
      el.className='muted';el.style.display='block';
      if(rowType){
        el.className='semantic-record-field';
        const raw=m.fixture[rowType].values[node.props?.semanticField];
        const shown=composition&&(raw==='not observed'||raw===null||raw===undefined)?'Unavailable':String(raw??'Unavailable');
        el.textContent=fieldLabel(node.props?.semanticField)+': '+shown;
      } else if(!node.id.endsWith('universal-search')){
        el.textContent=node.label;
      } else {
        const input=document.createElement('input');input.id='global-search';input.className='search';
        input.type='search';input.placeholder=composition?'Search this collection…':'Filter semantic observations…';input.setAttribute('aria-label',composition?'Search this collection':'Search semantic observations');
        input.addEventListener('input',filterRows);el.append(input);
      }
    }
    // Row children have already mounted their actual registry field components.
    if(node.props?.rowType)return el;
    if(node.elementId==='element.core.region'&&planned&&regionRole==='status-and-attention'){
      const grid=document.createElement('div');grid.className='status-grid';
      for(const ch of node.children||[])grid.append(elementFor(ch,rowType,regionRole));
      el.append(grid);
    } else if(node.elementId==='element.core.region'&&planned&&regionRole==='primary-content'){
      const list=document.createElement('div');list.className='record-list';
      for(const ch of node.children||[]){
        const rendered=elementFor(ch,rowType,regionRole);
        if(ch.props?.rowType)list.append(rendered);
        else el.append(rendered);
      }
      el.append(list);
    } else if(node.elementId==='element.core.region'&&planned&&regionRole==='persistent-nonblocking-feedback'){
      for(const ch of node.children||[])el.append(elementFor(ch,rowType,regionRole));
      const notice=document.createElement('div');notice.className='feedback-summary';
      notice.textContent='No verified operation activity. Live operation feed is not connected.';
      el.append(notice);
    } else if(node.elementId==='element.core.region'&&el.dataset.role==='supporting-tools'){
      const details=document.createElement('details');details.className='advanced-tools';
      const summary=document.createElement('summary');summary.textContent='Optional controls · consequential actions disabled';
      details.append(summary);
      for(const ch of node.children||[])details.append(elementFor(ch,rowType,regionRole));
      el.append(details);
    } else for(const ch of node.children||[])el.append(elementFor(ch,rowType,regionRole));
    return el;
  }
  function selectRow(rowId,element) {
    selected=rowId;
    for(const row of root.querySelectorAll('.record'))row.setAttribute('aria-current',row===element?'true':'false');
    const inspector=root.querySelector('[data-role="selection-details"]');
    if(!inspector)return;
    let box=inspector.querySelector('#selected-entity');
    if(!box){box=document.createElement('div');box.id='selected-entity';box.className='inspector-details';inspector.append(box);}
    box.replaceChildren();
    const record=m.fixture[rowId];
    const title=document.createElement('h3');title.textContent='Selected: '+(labels?.entities?.[record.entity]||record.entity);box.append(title);
    const dl=document.createElement('dl');
    for(const [name,value] of Object.entries(record.values)){
      const dt=document.createElement('dt');dt.dataset.semanticField=name;dt.textContent=fieldLabel(name);
      const dd=document.createElement('dd');dd.textContent=String(value);dl.append(dt,dd);
    }
    box.append(dl);
    const footer=document.createElement('p');footer.className='safe-note';
    footer.textContent='Fixture record · not observed from a live source';box.append(footer);
    if(stateAware)applyState();
  }
  function filterRows(){
    const query=(document.querySelector('#global-search')?.value||'').toLowerCase();
    for(const row of root.querySelectorAll('.record')){
      row.hidden=row.dataset.view!==activeView || !!query&&!row.textContent.toLowerCase().includes(query);
    }
    if(composition){root.dataset.activeTask=activeTask();
      if(window.__MCEL_COMPOSITION_PROOF)window.__MCEL_COMPOSITION_PROOF.task=activeTask();}
  }
  function setView(view){
    if(!views.some(x=>x.id===view))return;
    activeView=view;
    if(composition){
      const main=root.querySelector('[data-role="primary-content"]');
      const heading=main?.querySelector('h2');
      if(heading)heading.textContent=composition.labels.views[view]||view;
    }
    for(const tab of root.querySelectorAll('[role="tab"]')){
      const selected=tab.dataset.view===view;
      tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;
    }
    // A new view invalidates the old contextual selection.
    if(selected && ![...root.querySelectorAll('.record')].some(r=>r.dataset.rowType===selected && r.dataset.view===view)) {
      selected=null;
      const inspector=root.querySelector('#selected-entity');
      if(inspector)inspector.replaceChildren();
    }
    filterRows();
    if(stateAware)applyState();
  }
  const criticStrategy=m.designCriticStrategy||'A';
  const tree=m.componentTree;
  if(tree.elementId!=='element.core.app')throw Error('MCEL app root expected');
  root.dataset.mcelElementId=tree.elementId;
  root.dataset.semanticInstanceId=tree.id;
  for(const child of tree.children)root.append(elementFor(child));
  if(composition){
    const needed=composition.composites['searchable-collection'].roles;
    workspaceGroup=document.createElement('section');
    workspaceGroup.id='mcel-workspace-composite';
    workspaceGroup.className='workspace-composite';
    workspaceGroup.setAttribute('aria-label','Application workspace');
    const nodes=needed.map(role=>root.querySelector('[data-role="'+role+'"]'));
    if(nodes.some(node=>!node))throw Error('MCEL_COMPOSITION_REQUIRED_REGION_MISSING');
    const last=nodes[nodes.length-1];
    root.insertBefore(workspaceGroup,last.nextSibling);
    for(const node of nodes)workspaceGroup.append(node);
  }

  // The same production MCEL canonical layout engine resolves state-specific
  // visible regions. Hidden semantic regions stay in the registered tree, but
  // relinquish their grid areas. No application-specific role IDs or CSS rules.
  let lastLayoutKey=null;
  function stateVariant(width,policy){
    const byRole=Object.fromEntries(regions.map(r=>[r.role,r.id]));
    const isVisible=role=>policy.regions[role].mode!=='absent';
    if(composition){
      // The root sees *composition units*. The recursive canonical tree owns
      // the nested searchable collection. No CSS grid positions are authored
      // per application, and source semantic regions remain intact.
      const order=['status-and-attention','persistent-nonblocking-feedback',
                   'workspace','supporting-tools'].filter(role=>
                      role==='workspace'||isVisible(role));
      const areas=order.map(role=>role==='workspace'?'mcel-workspace-composite':byRole[role]);
      if(width>=1024 && criticStrategy==='B'){
        const side=['status-and-attention','supporting-tools','persistent-nonblocking-feedback']
          .filter(role=>isVisible(role));
        // Rail and workspace are composed by the canonical MCEL layout grammar.
        // No CSS override may substitute for a tested canonical candidate.
        return {tracks:['minmax(0,0.9fr)','minmax(0,3.1fr)'],
          areas:side.map(role=>byRole[role]+' mcel-workspace-composite')};
      }
      if(width>=1024 && criticStrategy==='C'){
        const tools=isVisible('supporting-tools')?byRole['supporting-tools']:null;
        const feedback=isVisible('persistent-nonblocking-feedback')?byRole['persistent-nonblocking-feedback']:null;
        const side=[tools,feedback].filter(Boolean);
        return {tracks:['minmax(0,3fr)','minmax(0,1fr)'],
          areas:[byRole['status-and-attention']+' '+byRole['status-and-attention'],
            ...(side.length?side.map(id=>'mcel-workspace-composite '+id):['mcel-workspace-composite mcel-workspace-composite'])]};
      }
      return {tracks:['minmax(0, 1fr)'],areas};
    }
    const ordered=['status-and-attention','global-context-and-search',
       'primary-content','selection-details','persistent-nonblocking-feedback','supporting-tools']
       .filter(isVisible);
    const active=policy.regions['persistent-nonblocking-feedback'].mode==='prominent';
    if(active){const i=ordered.indexOf('persistent-nonblocking-feedback');
      ordered.splice(i,1);ordered.splice(1,0,'persistent-nonblocking-feedback');}
    if(width<=600){return {tracks:['minmax(0, 1fr)'],areas:ordered.map(role=>byRole[role])};}
    const rows=[];
    function full(role){if(isVisible(role))rows.push([byRole[role],byRole[role]]);}
    full('status-and-attention');
    full('global-context-and-search');
    if(isVisible('selection-details'))rows.push([byRole['primary-content'],byRole['selection-details']]);
    else full('primary-content');
    full('persistent-nonblocking-feedback');full('supporting-tools');
    return {tracks:['minmax(0, 1.65fr)','minmax(0, 1fr)'],areas:rows.map(r=>r.join(' '))};
  }
  function workspaceVariant(width,policy){
    const byRole=Object.fromEntries(regions.map(r=>[r.role,r.id]));
    const search=byRole['global-context-and-search'],main=byRole['primary-content'];
    const inspector=byRole['selection-details'];
    const showInspector=policy.regions['selection-details'].mode!=='absent';
    if(width<1024||!showInspector)
      return {tracks:['minmax(0, 1fr)'],areas:[search,main,...(showInspector?[inspector]:[])]};
    return {tracks:['minmax(0, 2.3fr)','minmax(0, 1fr)'],
      areas:[`${search} ${search}`,`${main} ${inspector}`]};
  }
  function applyLayout(force=false){
    const width=innerWidth;
    const key=width<=600?'390':width<1024?'768':'1440';
    const policy=stateAware?window.__MCEL_STATE_PROOF:null;
    const signature=key+'|'+(policy?Object.entries(policy.regions).map(([k,v])=>k+v.mode).join(';'):'baseline')+
                    (composition?'|'+activeTask()+'|'+activeView:'');
    if(!force&&signature===lastLayoutKey)return;
    if(lastLayoutKey!==null)McelSemanticLayoutInference.clearCanonicalLayoutTree(root);
    const variant=stateAware?stateVariant(width,policy):m.plans[key]?.variant;
    if(!variant)throw Error('Missing generated layout variant '+key);
    const layoutChildren=composition?[
      ...regions.filter(r=>!['global-context-and-search','primary-content','selection-details'].includes(r.role))
             .map(r=>({id:r.id,selector:'#'+r.id,role:'region',verifyControls:false})),
      {id:'mcel-workspace-composite',selector:'#mcel-workspace-composite',role:'group',gap:10,
       variants:[workspaceVariant(width,policy)],
       children:regions.filter(r=>['global-context-and-search','primary-content','selection-details'].includes(r.role))
                    .map(r=>({id:r.id,selector:'#'+r.id,role:'region',verifyControls:false}))}
    ]:regions.map(r=>({id:r.id,selector:'#'+r.id,role:'region',verifyControls:false}));
    const layoutTree={schema:'mcel.layout-tree.v1',root:{id:'generated-root',selector:'#generated-root',role:'group',gap:12,
      variants:[variant],children:layoutChildren}};
    const candidate=McelSemanticLayoutInference.evaluateCanonicalLayoutTree(root,layoutTree,{maxCandidates:24});
    const result=McelSemanticLayoutInference.applyCanonicalLayoutTree(root,candidate);
    window.__MCEL_LAYOUT_PROOF={status:result.status,reason:result.reason||null,selected:key,
      hintDecisions:m.plans[key].decisions,stateVariant:stateAware?variant:null};
    if(result.status!=='pass')throw Error('MCEL_CANONICAL_LAYOUT_REFUSED: '+result.reason);
    lastLayoutKey=signature;
    if(composition) {
      const search=document.getElementById(composition.role_ids['global-context-and-search']);
      const main=document.getElementById(composition.role_ids['primary-content']);
      const inspector=document.getElementById(composition.role_ids['selection-details']);
      const composite=workspaceGroup.getBoundingClientRect();
      window.__MCEL_COMPOSITION_PROOF={status:'pass',task:activeTask(),view:activeView,
        searchWithinWorkspace:workspaceGroup.contains(search),
        collectionWithinWorkspace:workspaceGroup.contains(main),
        inspectorWithinWorkspace:workspaceGroup.contains(inspector),
        inspectorVisible:!inspector.hidden,workspaceWidth:composite.width,
        nestedGrid:getComputedStyle(workspaceGroup).gridTemplateAreas,
        explanation:composition.explanation,allFieldsPreserved:composition.preservation.all_fields_inspectable};
      root.dataset.activeTask=activeTask();
    }
    if(planned)for(const row of root.querySelectorAll('.record'))
      row.dataset.pattern=width<=600?'compact-list':'comparison-grid';
    fitRecordColumns();
  }
  // Content-aware record tracks: the usable inline size belongs to the list,
  // not to the viewport. The same rule applies to every row/entity type.
  // The C candidate caps density at two columns but never squeezes below the
  // experiment's 240px readable record floor (scaled with the record font).
  function fitRecordColumns(){
    const list=root.querySelector('[data-role="primary-content"] .record-list');
    if(!list || !planned || m.designCriticStrategy!=='C')return;
    const width=list.getBoundingClientRect().width;
    if(width<1)return;
    const rows=[...list.querySelectorAll('.record')].filter(r=>!r.hidden);
    const fonts=rows.map(r=>parseFloat(getComputedStyle(r).fontSize)).filter(Number.isFinite);
    const font=Math.max(13,...fonts);
    const floor=Math.max(240,240*font/13);
    const gap=6;
    const columns=(width >= 2*floor+gap)?2:1;
    list.style.gridTemplateColumns=`repeat(${columns},minmax(0,1fr))`;
    list.dataset.contentAwareColumns=String(columns);
    list.dataset.minimumReadableWidth=String(Math.round(floor));
  }
  let recordColumnFrame=null;
  function scheduleRecordColumns(){
    if(recordColumnFrame!==null)return;
    recordColumnFrame=requestAnimationFrame(()=>{recordColumnFrame=null;fitRecordColumns();});
  }
  if(m.designCriticStrategy==='C'){
    // Text scaling in the Frob changes record height without changing viewport.
    // ResizeObserver supplies a second trigger beyond normal resize/layout.
    const recordList=root.querySelector('[data-role="primary-content"] .record-list');
    if(recordList && typeof ResizeObserver!=='undefined'){
      const observer=new ResizeObserver(scheduleRecordColumns);
      for(const row of recordList.querySelectorAll('.record'))observer.observe(row);
    }
  }
  function applyState(){
    if(!stateAware)return;
    const presets=m.statePresets;
    if(!presets||!presets[syntheticPreset])throw Error('MCEL_STATE_PRESET_MISSING');
    const policy=presets[syntheticPreset][selected?'selected':'unselected'];
    if(!policy||policy.schema!=='mcel.state-presentation-plan.v1')throw Error('MCEL_STATE_POLICY_INVALID');
    for(const [role,rule] of Object.entries(policy.regions)){
      const el=root.querySelector('[data-role="'+role+'"]');
      if(!el)throw Error('MCEL_STATE_ROLE_MISSING: '+role);
      el.hidden=rule.mode==='absent';
      el.dataset.stateMode=rule.mode;
    }
    const attention=root.querySelector('[data-role="status-and-attention"]');
    let summary=attention.querySelector('.state-summary');
    if(!summary){summary=document.createElement('p');summary.className='state-summary';attention.insertBefore(summary,attention.querySelector('.status-grid'));}
    summary.textContent=policy.summary.message;
    let evidence=attention.querySelector('.state-evidence');
    if(!evidence){evidence=document.createElement('p');evidence.className='state-evidence';summary.after(evidence);}
    const counts=policy.summary.availability_counts;
    const affected=Object.entries(counts).filter(([k])=>k!=='ready').reduce((a,[k,n])=>a+n,0);
    evidence.textContent=affected?affected+' observations need verification':'Fixture data only · not verified live';
    let details=attention.querySelector('.state-details');
    if(!details){details=document.createElement('details');details.className='state-details';
      const label=document.createElement('summary');label.textContent='Observation sources and details';details.append(label);attention.append(details);}
    const previous=details.open;
    details.querySelectorAll('ul').forEach(e=>e.remove());
    const list=document.createElement('ul');
    for(const obs of policy.facts.observations){const li=document.createElement('li');
      li.textContent=obs.id+': '+obs.availability+' · '+obs.source;list.append(li);}
    details.append(list);details.open=previous;
    const feedback=root.querySelector('[data-role="persistent-nonblocking-feedback"] .feedback-summary');
    if(feedback){const op=policy.facts.operation;
      feedback.textContent=op.state==='running'?'Fixture operation running · completion not verified':
      op.state==='failed'?'Synthetic verified failure · review outcome':
      op.state==='unknown'?'Operation outcome unknown · reconcile source before retry':
      op.state==='submitted'||op.state==='acknowledged'?'Operation pending · completion unknown':
      'No active operation';}
    window.__MCEL_STATE_PROOF={status:'pass',synthetic:policy.facts.synthetic,
       regions:policy.regions,summary:policy.summary,operation:policy.facts.operation,
       sourceState:syntheticPreset};
    applyLayout();
  }
  // Deterministic fixture-only visual stress states. No network/effect calls.
  // Browser Frob invokes this API to inspect actual generated MCEL components
  // under non-default data/operation states. These are NOT live observations.
  const baseLabels = new Map();
  // Experimental visual fixtures must be reversible and must affect a visible
  // record. Record fields are separate from the registry-level field labels.
  const baseRecordValues = new Map([...root.querySelectorAll('.record .semantic-record-field')]
    .map(field => [field, field.textContent]));
  for(const field of root.querySelectorAll('[data-mcel-element-id="element.core.field"]')) {
    // Never flatten a field containing real interactive descendants (search).
    if(field.querySelector('input,button,select,textarea,[role="tab"]'))continue;
    baseLabels.set(field, field.textContent);
  }
  const fixtureTexts = {
    'long_text': 'Synthetic long command: C:/Users/example/projects/a_very_long_nested_directory_name/worker_with_many_options.py --task-id=012345678901234567890123456789abcdefghijklmnopqrstuvwxyz --remote=2001:db8:abcd:1234::17',
    'operation_pending': 'SYNTHETIC / NOT LIVE · Operation submitted and acknowledged; running, final outcome not verified',
    'operation_failure': 'SYNTHETIC / NOT LIVE · Operation failed; no completion receipt, outcome unknown'
  };
  function visualFixture(state) {
    const supported=['default','long_text','crowded','idle','unavailable','empty',
      'unknown','operation_pending','operation_failure','operation_verified_failure'];
    if(!supported.includes(state))throw Error('Unsupported synthetic state: '+state);
    for(const [field, original] of baseLabels)field.textContent=original;
    for(const [field, original] of baseRecordValues){
      field.textContent=original;
      delete field.dataset.stressFixture;
    }
    root.dataset.visualFixtureState=state;
    const stateMap={default:'unavailable',long_text:'unavailable',crowded:'unavailable',
      idle:'idle',unavailable:'unavailable',empty:'empty',unknown:'unknown',
      operation_pending:'running',operation_failure:'unknown',operation_verified_failure:'failure'};
    syntheticPreset=stateMap[state];
    const workspace=root.querySelector('[data-role="primary-content"]');
    if(state==='crowded'){
      for(const row of workspace?.querySelectorAll('.record') || [])row.hidden=false;
    } else filterRows();
    if(stateAware)applyState();
    if(state==='long_text'){
      // Mutate only after filtering and state-dependent visibility are resolved.
      // The old querySelector matched hidden/deferred fields and changed no
      // painted record. Fail explicitly instead of accepting an inert fixture.
      const isPainted = el => {
        if(!el || !el.getClientRects().length || el.getBoundingClientRect().width<1)return false;
        for(let cur=el;cur;cur=cur.parentElement){
          if(cur.hidden)return false;
          const style=getComputedStyle(cur);
          if(style.display==='none'||style.visibility==='hidden')return false;
        }
        return true;
      };
      const field=[...(workspace?.querySelectorAll('.record .semantic-record-field')||[])]
        .find(isPainted);
      if(!field)throw Error('MCEL_LONG_TEXT_FIXTURE_NO_VISIBLE_RECORD_FIELD');
      field.textContent=fixtureTexts.long_text;
      field.dataset.stressFixture='long-text';
    }
    return {state,synthetic:true,normalized:syntheticPreset};
  }
  window.__MCEL_SET_VISUAL_FIXTURE=visualFixture;
  setView(activeView);
  // A default selected item is not assumed: selection requires a user action.
  if(stateAware)applyState();
  else {const firstRow=root.querySelector('.record:not([hidden])');
    if(firstRow)selectRow(firstRow.dataset.rowType,firstRow);applyLayout();}
  // Screenshot/full-page measurement may temporarily resize the Chromium
  // backing viewport to near zero. Ignore transient measurements and debounce
  // width-driven layout rebuilds until the browser returns to a usable width.
  let pendingResize=null;
  window.addEventListener('resize',()=>{
    if(pendingResize!==null)clearTimeout(pendingResize);
    pendingResize=setTimeout(()=>{
      pendingResize=null;
      if(innerWidth<240 || document.documentElement.clientWidth<240)return;
      applyLayout();
    },120);
  });
  window.__MCEL_PRESENTATION_PROOF=planned?{
    status:'pass', rows:Object.keys(presentation.rows).length, summaryFields:presentation.stats.visible_row_fields,
    deferredFields:presentation.stats.deferred_row_fields,
    preserved:presentation.semantic_preservation,
    recipes:Object.values(presentation.regions).map(value=>value.recipe)
  }:null;
  window.__MCEL_GENERATED_READY=true;
})();
