/* MCEL semantic collection consumer: data -> declared semantic projection ->
 * registered elements -> shared layout authority. No Task Manager selectors,
 * process logic, network, mutations, capabilities, or operational effects.
 */
(function(global) {
  "use strict";
  const SCHEMA="mcel.semantic-collection-spec.v1";
  const SCHEMA_V2="mcel.semantic-collection-spec.v2";
  const LINK="mcel.representation-layout-link.experiment.v1";
  const kinds=new Set(["identifier","text","enum","quantity","long-text","timestamp"]);
  const tiers=new Set(["primary","secondary","detail"]);
  const has=(o,k)=>Object.prototype.hasOwnProperty.call(o,k);
  function requireIt(ok,reason) {if(!ok) throw Error("MCEL_SEMANTIC_COLLECTION_"+reason);}
  function validate(spec) {
    if(spec?.schema===SCHEMA_V2) {
      requireIt(global.McelSemanticValueContract?.validate,"VALUE_CONTRACT_UNAVAILABLE");
      return global.McelSemanticValueContract.validate(spec);
    }
    requireIt(spec?.schema===SCHEMA && typeof spec.identity==="string" &&
      Array.isArray(spec.fields) && spec.fields.length>0 && spec.fields.length<=16,"SPEC_INVALID");
    const fields=spec.fields,keys=fields.map(f=>f.key);
    requireIt(keys.every(k=>typeof k==="string" && /^[a-zA-Z][\w-]*$/.test(k)) &&
      new Set(keys).size===keys.length && fields.some(f=>f.key===spec.identity && f.tier==="primary"),"IDENTITY_INVALID");
    for(const f of fields) {
      requireIt(typeof f.label==="string" && f.label.length>0 && kinds.has(f.kind) &&
        tiers.has(f.tier) && (f.from || f.derive) &&
        (!f.derive || f.derive.kind==="first-true" &&
          Array.isArray(f.derive.cases) && f.derive.cases.every(c=>typeof c.field==="string" &&
            typeof c.value==="string") && typeof f.derive.default==="string") &&
        (f.presentation?.maxChars===undefined || Number.isInteger(f.presentation.maxChars) &&
          f.presentation.maxChars>=16 && f.presentation.maxChars<=512),"FIELD_INVALID");
    }
    return spec;
  }
  function sourceValue(raw,field) {
    if (field.derive) {
      for(const item of field.derive.cases) if(raw[item.field]===true) return item.value;
      return field.derive.default;
    }
    return has(raw,field.from)?raw[field.from]:undefined;
  }
  function displayed(value,field) {
    if(value===undefined || value===null) return "Not provided";
    if(value==="") return "Empty";
    if(field.kind==="quantity" && typeof value==="number" && Number.isFinite(value)) {
      if(field.presentation?.units==="bytes") {
        const bands=["B","KB","MB","GB","TB"];
        let size=Math.max(0,value),index=0;
        while(size>=1024 && index<bands.length-1) {size/=1024;index++;}
        return (index===0?String(Math.round(size)):size.toFixed(1))+" "+bands[index];
      }
      return new Intl.NumberFormat().format(value);
    }
    const full=String(value);
    if(field.kind==="long-text" && full.length>(field.presentation?.maxChars||64)) {
      const limit=field.presentation?.maxChars||64;
      return full.slice(0,Math.max(1,limit-12))+"…"+full.slice(-11);
    }
    return full;
  }
  function project(input,spec) {
    validate(spec);
    if(spec.schema===SCHEMA_V2)return projectV2(input,spec);
    requireIt(Array.isArray(input) && input.length<=500,"ROWS_INVALID");
    const ids=new Set(),rawRecords=new Map(),originals=[];
    const rows=[];
    const visible=[];
    for(const raw of input) {
      requireIt(raw && typeof raw==="object" && !Array.isArray(raw),"ROW_INVALID");
      const identifier=sourceValue(raw,spec.fields.find(f=>f.key===spec.identity));
      requireIt(identifier!==undefined && identifier!==null && String(identifier)!=="", "ROW_ID_MISSING");
      const id=String(identifier);
      requireIt(!ids.has(id),"DUPLICATE_ID");ids.add(id);rawRecords.set(id,raw);
      const semantic={id};
      const cells={};
      for(const field of spec.fields) {
        const value=sourceValue(raw,field);
        if(value!==undefined) semantic[field.key]=value;
        cells[field.key]={display:displayed(value,field),strategy:field.kind,
          enabled:true};
      }
      originals.push(semantic);rows.push({id,cells});visible.push(id);
    }
    return {schema:SCHEMA,source_rows:originals,rawRecords,
      projection:{rows,collisions:[]},field_contracts:spec.fields.map(f=>({
        key:f.key,label:f.label,tier:f.tier,priority:f.priority||100,
        kind:f.kind==="identifier"?"identifier":f.kind,required:f.tier==="primary",
        presentation:{...f.presentation,compactDisclosure:f.presentation?.compactDisclosure===true}
      })),layout_link:{schema:LINK,required_keys:spec.fields.filter(f=>f.tier==="primary").map(f=>f.key),
        identifier_keys:[spec.identity],compact_breakpoint_px:500,min_visible_cell_px:48}};
  }
  function projectV2(input,spec) {
    requireIt(Array.isArray(input) && input.length<=500,"ROWS_INVALID");
    const engine=global.McelSemanticValueContract,ids=new Set(),rawRecords=new Map();
    const originals=[],rows=[],valueRecords=[];
    for(const row of input) {
      requireIt(row && typeof row==="object" && !Array.isArray(row),"ROW_INVALID");
      const interpreted=engine.rowValues(row,spec),id=interpreted.id;
      requireIt(!ids.has(id),"DUPLICATE_ID");ids.add(id);
      // Snapshot the authoritative source for later exact inspection without mutating it.
      const raw=structuredClone(row);rawRecords.set(id,raw);
      const semantic={id},cells={};
      for(const field of spec.fields) {
        const value=interpreted.fields[field.key];
        if(value.availability==="present") semantic[field.key]=value.rawValue;
        else if(value.availability==="null")semantic[field.key]=null;
        else if(value.availability==="empty")semantic[field.key]="";
        else if(value.availability==="unavailable")semantic[field.key]="Unavailable";
        else if(value.availability==="failed")semantic[field.key]="Failed";
        cells[field.key]={display:engine.display(value,field),strategy:field.kind,enabled:true};
      }
      originals.push(semantic);rows.push({id,cells});valueRecords.push({id,fields:interpreted.fields});
    }
    return {schema:SCHEMA_V2,source_rows:originals,rawRecords,value_records:valueRecords,
      projection:{rows,collisions:[]},field_contracts:spec.fields.map(f=>({
        key:f.key,label:f.label,tier:f.tier,priority:f.priority??100,kind:f.kind,
        required:f.tier==="primary",exact:f.exact===true,
        presentation:{...f.presentation,compactDisclosure:f.presentation?.compactDisclosure===true}
      })),layout_link:{schema:LINK,required_keys:spec.fields.filter(f=>f.tier==="primary").map(f=>f.key),
        identifier_keys:[spec.identity],compact_breakpoint_px:500,min_visible_cell_px:48}};
  }
  // A source identity and a presentation identity are distinct. An observer
  // missing a generation token has NOT observed the source disappear.
  function classifySourceTransition(previousPresent,nextPresent,previousGeneration,nextGeneration){
    if(!previousPresent)return nextPresent?'added':'absent';
    if(!nextPresent)return 'source-absent';
    if(previousGeneration===null||previousGeneration===undefined||
       nextGeneration===null||nextGeneration===undefined)return 'continuity-unverified';
    return previousGeneration===nextGeneration?'continuous':'identity-replaced';
  }
  function mount(host,input,spec,opts={}) {
    requireIt(host && typeof host.replaceChildren==="function","HOST_INVALID");
    const registry=opts.registry||global.McelElementRegistry;
    const authority=opts.authority||global.McelSemanticLayoutInference;
    requireIt(registry && typeof authority?.mountSemanticCollectionLayout==="function","AUTHORITY_UNAVAILABLE");
    let result=project(input,spec),disposed=false,selected=null;
    // The universal-profile laboratory opts in explicitly. Existing production
    // consumers keep the old inspector and collection presentation unchanged.
    const humanFirst=opts.humanFirst===true;
    const negotiated=opts.presentationNegotiation===true;
    // Explicit lab opt-in: source snapshots remain authoritative only while live.
    const retirementEnabled=negotiated && opts.sourceRetirement===true;
    if(retirementEnabled)requireIt(!!global.McelElementRetirement,'RETIREMENT_UNAVAILABLE');
    if(negotiated)requireIt(humanFirst && global.McelPresentationNegotiation &&
      global.McelPresentationHosts && global.McelSelectionContinuity,'NEGOTIATION_UNAVAILABLE');
    const doc=host.ownerDocument;
    const continuity=negotiated?global.McelSelectionContinuity.service(doc):null;
    const tag=id=>{const found=registry.get(id);requireIt(found?.renderer?.htmlTag,"UNREGISTERED_ELEMENT");return found.renderer.htmlTag;};
    const node=(tagName,css,value)=>{const x=doc.createElement(tagName);if(css)x.className=css;
      if(value!==undefined)x.textContent=String(value);return x;};
    const container=node('section',humanFirst?'mcel-semantic-collection mcel-semantic-collection--human-first':'mcel-semantic-collection');
    const collection=node(tag('element.core.collection'));
    collection.dataset.mcelElementId='element.core.collection';
    container.append(collection);
    const inspector=node('section','mcel-semantic-collection__inspector');
    if(negotiated){container.classList.add('mcel-semantic-collection--negotiated');inspector.hidden=true;}
    inspector.setAttribute('aria-live','polite');
    const prompt=()=>inspector.replaceChildren(node('p','',humanFirst?
      'Select a record for readable details. Exact source evidence is available on demand.':
      'Select a row to inspect complete source values.'));
    prompt();
    container.append(inspector);
    const rowCache=new Map();
    const retiredRecords=new Map();let retirementSerial=0;
    let layoutResult=result;
    let observationCursor=null;
    function recordForId(id){return result.rawRecords.get(id)||retiredRecords.get(id)?.raw||null;}
    function valueForId(id){return result.value_records?.find(r=>r.id===id)||retiredRecords.get(id)?.valueRecord||null;}
    function rebuildLayoutResult(){
      if(!retiredRecords.size){layoutResult=result;return;}
      const rawRecords=new Map(result.rawRecords),source_rows=[...result.source_rows],
        rows=[...result.projection.rows],value_records=[...(result.value_records||[])];
      for(const [id,r] of retiredRecords){
        source_rows.push({...r.semantic,id});rows.push({...r.projected,id});
        if(r.valueRecord)value_records.push({...r.valueRecord,id});
        rawRecords.set(id,r.raw);
      }
      layoutResult={...result,rawRecords,source_rows,value_records,
        projection:{...result.projection,rows}};
    }
    const negotiation=global.McelPresentationNegotiation;
    const policy=negotiated?(opts.presentationPolicy ||
      registry.get('element.core.collection-row')?.presentationPolicy || negotiation.defaultPolicy):null;
    if(negotiated)negotiation.validate(policy);
    const scrollHost=negotiated?global.McelPresentationHosts.createScrollHost(collection,opts.capabilities||{}):null;
    let suppressedHoverId=null,lastHoverId=null;
    const overlayHost=negotiated?global.McelPresentationHosts.createOverlayHost(doc,{
      capabilities:opts.capabilities||{},onClose:(reason)=>{
        if(!disposed)container.dataset.overlayOpen='false';
        if(reason==='escape'||reason==='user-scroll')suppressedHoverId=lastHoverId;
      }}):null;
    const unsuppressOnExit=ev=>{
      if(!suppressedHoverId)return;
      const row=rowCache.get(suppressedHoverId)?.node,rect=row?.getBoundingClientRect();
      if(!rect||ev.clientX<rect.left||ev.clientX>rect.right||ev.clientY<rect.top||ev.clientY>rect.bottom)
        suppressedHoverId=null;
    };
    if(negotiated)doc.addEventListener('pointermove',unsuppressOnExit,{passive:true});
    const receipts=[];
    let expandedId=null;
    let applied=null;
    function invalidateRetiredLayout(){
      if(disposed)return;
      const anchor=scrollHost?.captureAnchor();
      rebuildLayoutResult();
      if(applied){
        const committed=applied.getCommittedState?.()||null;
        applied.unmount();applied=null;
        committedLayoutState=committed;
        try{installLayout();}catch(error){container.dataset.retirementLayoutError=String(error);}
      }
      if(anchor)scrollHost.restoreAnchor(anchor);
    }
    const retirement=retirementEnabled?global.McelElementRetirement.create({doc,list:collection,
      visibility:row=>scrollHost.visibility(row),
      policy:opts.retirementPolicy||global.McelElementRetirement.DEFAULT,
      // Resolve every live presentation of the same logical element, not only
      // the source row. The retirement service owns the common interaction test.
      interactionRegions:row=>overlayHost.sourceId===row.dataset.rowId && overlayHost.active?
        [overlayHost.active]:[],
      onRemove:(id,entry,reason)=>{
        retiredRecords.delete(id);entry.node.remove();
        pendingRowFields.delete(id);pendingDetails.delete(id);
        if(selected===id)selected=null;
        if(expandedId===id)expandedId=null;
        if(overlayHost.sourceId===id)overlayHost.close('retired-removed');
        invalidateRetiredLayout();
      }
    }):null;
    function retirementSnapshot(id,entry,prior){
      const raw=prior.rawRecords.get(id);
      if(!raw)return false;
      // Presentation identity is distinct from live source identity. This also
      // handles PID/identifier reuse without selecting a different live record.
      const key='retired:'+ (++retirementSerial)+':'+id;
      const projected=prior.projection.rows.find(r=>r.id===id);
      const semantic=prior.source_rows.find(r=>String(r.id)===id);
      const valueRecord=prior.value_records?.find(r=>r.id===id)||null;
      if(!projected||!semantic)return false;
      entry.node.dataset.rowId=key;entry.node.dataset.mcelRetirementKey=key;
      entry.node.dataset.mcelOriginalRowId=id;
      entry.node.dataset.mcelSourceStatus='retired';
      entry.node.dataset.mcelSelectionScope=key;
      if(overlayHost.sourceId===key && overlayHost.active)
        overlayHost.active.dataset.mcelRetirementKey=key;
      for(const field of entry.node.querySelectorAll('mcel-field'))field.dataset.sourceRowId=key;
      const slot=entry.node.querySelector('[data-mcel-presentation-slot="inline-detail"]');
      if(slot)slot.dataset.sourceRowId=key;
      if(selected===id)selected=key;
      if(expandedId===id)expandedId=key;
      const frozen={entry,raw:structuredClone(raw),semantic,projected,valueRecord};
      retiredRecords.set(key,frozen);
      if(pendingRowFields.delete(id))pendingRowFields.add(key);
      if(pendingDetails.delete(id))pendingDetails.add(key);
      const perElement=opts.retirementPolicies?.[id] ||
        registry.get('element.core.collection-row')?.retirementPolicy ||
        opts.retirementPolicy || global.McelElementRetirement.DEFAULT;
      const response=retirement.retire(key,entry.node,{sourceId:id,generation:entry.gen,policy:perElement});
      if(!response.retained){retiredRecords.delete(key);entry.node.remove();
        if(selected===key)selected=null;if(expandedId===key)expandedId=null;
        pendingRowFields.delete(key);pendingDetails.delete(key);
        if(overlayHost.sourceId===key)overlayHost.close('retirement-immediate');
        return false;
      }
      return true;
    }
    function copyButton(textValue,label){
      const btn=node('button','mcel-semantic-collection__copy',label);
      btn.type='button';btn.setAttribute('aria-label',label);
      btn.addEventListener('click',async event=>{
        event.stopPropagation();
        try{
          if(!global.navigator?.clipboard?.writeText)throw Error('clipboard-unavailable');
          await global.navigator.clipboard.writeText(typeof textValue==='function'?textValue():textValue);
          btn.dataset.copyResult='success';
        }catch(_){btn.dataset.copyResult='unavailable';}
      });
      return btn;
    }

    const generation=raw=>{
      const field=spec.reconciliation?.generationFrom;
      const value=field && raw?.[field];
      return value===undefined||value===null||value===''?null:typeof value+':'+String(value);
    };
    // Information-bearing titles use registered field kinds and actual observed
    // discriminability, not app names, producer-path keywords or a hidden oracle.
    // The canonical key always remains the stable exact selection identity.
    function readableField() {
      const candidates=spec.fields.filter(f=>f.key!==spec.identity &&
        f.tier==='primary' && (f.kind==='text'||f.kind==='identifier'||f.kind==='enum'));
      if(!candidates.length)return null;
      const n=Math.max(1,result.source_rows.length);
      const scores=candidates.map(field=>{
        const values=result.source_rows.map(row=>row[field.key]).filter(v=>v!==undefined && v!==null && v!=='');
        const unique=new Set(values.map(String)).size;
        const meanLength=values.reduce((sum,v)=>sum+String(v).length,0)/Math.max(1,values.length);
        return {field,unique:unique/n,coverage:values.length/n,
          readable:field.kind==='text'?1:0,length:meanLength};
      });
      scores.sort((a,b)=>b.readable-a.readable||b.unique-a.unique||
        b.coverage-a.coverage||a.length-b.length||a.field.key.localeCompare(b.field.key));
      return scores[0].field;
    }
    function buildDetails(id,{withCopy=false}={}) {
      const raw=recordForId(id);
      if(!raw)return null;
      const valueRecord=valueForId(id);
      const surface=node('section','mcel-semantic-collection__detail-content');
      surface.dataset.mcelSelectionScope=id;
        const primary=readableField();
        const value=primary && valueRecord?.fields?.[primary.key];
        const readable=value?.availability==='present'?String(value.rawValue):'';
        const heading=node('h3','',readable?'Selected: '+readable:'Selected record');
        heading.dataset.mcelSelectionKey='heading';surface.append(heading);
        const facts=node('dl','mcel-semantic-collection__readable-facts');
        let added=0;
        for(const field of spec.fields){
          if(field.key===spec.identity || field.kind==='long-text' || field.tier==='detail' ||
             field.key===primary?.key || added>=8)continue;
          const fact=valueRecord?.fields?.[field.key];
          if(!fact)continue;
          const shown=global.McelSemanticValueContract.display(fact,field);
          const label=node('dt','',field.label),valueNode=node('dd','');
          label.dataset.mcelSelectionKey='label:'+field.key;
          const text=node('span','',shown);text.dataset.mcelSelectionKey='value:'+field.key;
          valueNode.append(text);facts.append(label,valueNode);
          added++;
        }
        if(added)surface.append(facts);
        // Collapsed is *not* removed: all authoritative source bytes and value
        // states remain in the DOM and retrievable via the existing surface.
        const disclosure=node('details','mcel-semantic-collection__exact-evidence');
        const button=node('summary','', 'Exact source record and semantic evidence');
        disclosure.append(button);
        const identity=node('p','mcel-semantic-collection__exact-identity');
        const identityCode=node('code','',id);identityCode.dataset.mcelSelectionKey='canonical-identity';
        identity.append(node('strong','', 'Canonical identity: '),identityCode);
        disclosure.append(identity);
        const exact=node('dl','mcel-semantic-collection__raw-values');
        for(const [key,item] of Object.entries(raw)){
          const rawLabel=node('dt','',key),rawValue=node('dd','',item===null?'null':
            typeof item==='object'?JSON.stringify(item):String(item));
          rawLabel.dataset.mcelSelectionKey='raw-label:'+key;
          rawValue.dataset.mcelSelectionKey='raw-value:'+key;exact.append(rawLabel,rawValue);
        }
        disclosure.append(exact);
        if(valueRecord){
          const semantics=node('details','mcel-semantic-collection__exact-semantics');
          semantics.append(node('summary','', 'Availability, provenance and conflicts'));
          const facts=node('dl');
          for(const [key,item] of Object.entries(valueRecord.fields)){
            const metadata=node('dd','',item.availability+' / '+item.freshness+
              (item.conflict.length?' / conflicting':'')+' / '+
              (item.source.path||item.dependencies.join(', ')||'derived'));
            metadata.dataset.mcelSelectionKey='provenance:'+key;
            facts.append(node('dt','',key),metadata);
          }
          semantics.append(facts);disclosure.append(semantics);
        }
        surface.append(disclosure);
      if(withCopy){
        const actions=node('div','mcel-semantic-collection__copy-actions');
        actions.append(copyButton(()=>{
          const pane=surface.closest('.mcel-negotiated-floating-card');
          const owner=surface.closest('[data-mcel-retirement-key]')||pane;
          const archive=owner?.dataset?.mcelRetirementKey;
          return JSON.stringify(recordForId(archive||id),null,2);
        },'Copy complete source record'));
        surface.append(actions);
        for(const dd of surface.querySelectorAll('.mcel-semantic-collection__readable-facts dd')){
          dd.append(copyButton(()=>dd.firstChild?.textContent||'','Copy field value'));
        }
      }
      return surface;
    }
    function presentationProviders(){
      const providers={};
      for(const cap of negotiation.CAPABILITIES){
        if(cap.startsWith('scroll.') || cap.startsWith('geometry.'))providers[cap]=scrollHost;
        else if(cap.startsWith('overlay.'))providers[cap]=overlayHost;
      }
      return providers;
    }
    function removeInline(except=null){
      for(const [key,entry] of [...rowCache,...[...retiredRecords].map(([id,r])=>[id,r.entry])]){
        const current=entry.node.querySelector('.mcel-semantic-collection__inline-detail');
        if(current!==except)current?.remove();
        const expanded=current===except && !!except;
        entry.node.setAttribute('aria-expanded',String(expanded));
        entry.node.setAttribute('aria-pressed',String(key===selected));
      }
      expandedId=except?.closest('[data-row-id]')?.dataset.rowId||null;
    }
    function markSelected(id){
      selected=id;
      for(const [key,entry] of [...rowCache,...[...retiredRecords].map(([key,r])=>[key,r.entry])])
        entry.node.setAttribute('aria-pressed',String(key===id));
    }
    function appendExpandInlineAction(detail,id){
      const actions=node('div','mcel-semantic-collection__copy-actions');
      const expand=node('button','mcel-semantic-collection__copy','Expand inline');
      expand.type='button';expand.setAttribute('aria-label','Expand inline');
      expand.addEventListener('click',ev=>{ev.stopPropagation();reveal(id,'onSelect',true);});
      actions.append(expand);detail.prepend(actions);
    }
    function reveal(id,event,forceExpand=false){
      const entry=rowCache.get(id)||retiredRecords.get(id)?.entry;
      if(!entry || !negotiated)return {ok:false,reason:'unknown-row'};
      if(event==='onSelect' && expandedId===id && !forceExpand){
        removeInline();selected=null;
        for(const item of rowCache.values())item.node.setAttribute('aria-pressed','false');
        const collapsed={ok:true,event,mode:'collapsed',attempts:[{mode:'collapsed',status:'fulfilled'}]};
        receipts.push({id,event,ok:true,mode:'collapsed',attempts:collapsed.attempts});
        return collapsed;
      }
      if(event==='onHover')lastHoverId=id;
      const decision=negotiation.negotiate(policy,event,presentationProviders(),
        {id,beginTransaction:()=>negotiation.createTransaction()},(choice,{transaction})=>{
        const detail=buildDetails(id,{withCopy:true});
        if(!detail)return {ok:false,reason:'missing-source'};
        if(choice.mode==='inline-expand'){
          const previousScroll=collection.scrollTop;
          const box=node('section','mcel-semantic-collection__inline-detail');
          box.dataset.mcelPresentationSlot='inline-detail';
          box.dataset.sourceRowId=id;
          box.dataset.presentation='inline-expand';
          let committed=false;
          transaction.addUndo(()=>{box.remove();
            entry.node.setAttribute('aria-expanded',String(expandedId===id));
            collection.scrollTop=previousScroll;});
          try{
            box.append(detail);entry.node.append(box);
            entry.node.setAttribute('aria-expanded','true');
            if(choice.requires.includes('scroll.reveal')){
              const moved=scrollHost.reveal(entry.node,{userInitiated:true});
              if(!moved.ok)return {ok:false,reason:moved.reason||'scroll-unfulfilled'};
            }
            markSelected(id);removeInline(box);overlayHost.close('inline-selection');
            committed=true;
            return {ok:true,visible:scrollHost.visibility(entry.node)};
          }catch(error){return {ok:false,reason:'presentation-execution-error'};}
          finally{
            if(!committed){box.remove();entry.node.setAttribute('aria-expanded',String(expandedId===id));
              collection.scrollTop=previousScroll;}
          }
        }
        if(choice.mode==='floating-over-source'){
          if(event==='onHover')appendExpandInlineAction(detail,id);
          const opened=overlayHost.open(entry.node,detail,choice.lifecycle,collection);
          if(opened.ok){
            transaction.addUndo(()=>{overlayHost.close('presentation-rollback');
              container.dataset.overlayOpen='false';});
            container.dataset.overlayOpen='true';
            if(event==='onSelect'){markSelected(id);removeInline();}
            return {ok:true,position:opened.position};
          }
          return opened;
        }
        return {ok:false,reason:'mode-unsupported'};
      });
      receipts.push({id,event,ok:decision.ok,mode:decision.mode||null,
        attempts:decision.attempts});
      if(receipts.length>200)receipts.shift();
      return decision;
    }
    // Universal selection-aware mutation: preserve semantic leaf nodes and active
    // evidence while applying unrelated updates immediately. Never replace a
    // selected value with different text merely to show the newest observation.
    const pendingDetails=new Set();
    const pendingRowFields=new Set();
    function updateDetailSurface(surface,id){
      const fresh=buildDetails(id,{withCopy:true});
      if(!fresh)return false;
      if(surface.closest('.mcel-negotiated-floating-card') &&
         surface.querySelector('[aria-label="Expand inline"]'))appendExpandInlineAction(fresh,id);
      const receipt=continuity.mutate(surface,fresh);
      if(receipt.deferred)pendingDetails.add(id);
      return receipt.ok && !receipt.deferred;
    }
    function refreshDetailsFor(id){
      let pending=false;
      const row=(rowCache.get(id)||retiredRecords.get(id)?.entry)?.node;
      const inline=row?.querySelector('.mcel-semantic-collection__inline-detail > .mcel-semantic-collection__detail-content');
      if(inline && !updateDetailSurface(inline,id))pending=true;
      const overlay=overlayHost?.active;
      if(overlay && overlayHost.sourceConnected && overlayHost.sourceId===id){
        const surface=overlay.querySelector('.mcel-semantic-collection__detail-content');
        if(surface && !updateDetailSurface(surface,id))pending=true;
      }
      if(pending)pendingDetails.add(id);else pendingDetails.delete(id);
    }
    function flushPendingDetails(){
      if(disposed || !negotiated)return;
      for(const id of [...pendingRowFields]){
        const archived=retiredRecords.get(id);
        const row=(rowCache.get(id)||archived?.entry)?.node;
        const semantic=result.source_rows.find(r=>String(r.id)===id)||
          (archived?{...archived.semantic,id}:null);
        if(!row||!semantic){pendingRowFields.delete(id);continue;}
        fillRow(row,id,semantic,archived?layoutResult:result);
      }
      for(const id of [...pendingDetails]){
        if(recordForId(id))refreshDetailsFor(id);
        else pendingDetails.delete(id);
      }
    }
    const unobserveSelection=negotiated?continuity.observe(flushPendingDetails):null;
    if(negotiated)doc.addEventListener('focusout',flushPendingDetails,true);

    function inspect(id) {
      const raw=result.rawRecords.get(id);
      if(!raw)return false;
      selected=id;
      for(const [key,entry] of rowCache)entry.node.setAttribute('aria-pressed',String(key===id));
      const valueRecord=result.value_records?.find(record=>record.id===id);
      if(humanFirst){inspector.replaceChildren(...buildDetails(id).childNodes);return true;}
      inspector.replaceChildren(node('h3','', 'Exact record '+id));
      const dl=node('dl');
      for(const [key,value] of Object.entries(raw)) {
        dl.append(node('dt','',key),node('dd','',value===null?'null':
          typeof value==='object'?JSON.stringify(value):String(value)));
      }
      inspector.append(dl);
      if(valueRecord) {
        const section=node('section','mcel-semantic-collection__semantics');
        section.append(node('h4','', 'Semantic evidence'));
        const evidence=node('dl');
        for(const [key,item] of Object.entries(valueRecord.fields)) {
          evidence.append(node('dt','',key),node('dd','',
            item.availability+' / '+item.freshness+
            (item.conflict.length?' / conflicting':'')+
            ' / '+(item.source.path||item.dependencies.join(', ')||'derived')));
        }
        section.append(evidence);inspector.append(section);
      }
      return true;
    }
    function makeRow(id,semantic,projection) {
      const row=node(tag('element.core.collection-row'));
      row.dataset.mcelElementId='element.core.collection-row';row.dataset.rowId=id;
      row.dataset.mcelSelectionScope=id;
      row.tabIndex=0;row.setAttribute('role','button');row.setAttribute('aria-label','Inspect exact record '+id);
      if(humanFirst)row.setAttribute('aria-pressed','false');
      for(const column of projection.field_contracts) {
        const key=column.key,field=node(tag('element.core.field'));
        field.dataset.mcelElementId='element.core.field';field.dataset.semanticField=key;
        field.dataset.sourceRowId=id;
        if(humanFirst && key===spec.identity)field.dataset.canonicalIdentity='true';
        const valueSpan=node('span','field-value');valueSpan.dataset.mcelSelectionKey='field:'+key;
        field.append(node('span','field-label',column.label),valueSpan);
        row.append(field);
      }
      if(negotiated){
        row.setAttribute('aria-expanded','false');
        row.addEventListener('pointerenter',()=>{if(!disposed && expandedId!==id && suppressedHoverId!==id)reveal(row.dataset.rowId,'onHover');});
        if(policy.events.onFocus)row.addEventListener('focus',()=>{if(!disposed)reveal(row.dataset.rowId,'onFocus');});
      }
      row.addEventListener('click',ev=>{if(ev.target.closest?.('.mcel-semantic-collection__detail-content'))return;
        if(negotiated)reveal(row.dataset.rowId,'onSelect');else inspect(id);});
      row.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){
        if(ev.target!==row)return;
        ev.preventDefault();if(negotiated)reveal(row.dataset.rowId,'onSelect');else inspect(id);
      }});
      return row;
    }
    function fillRow(row,id,semantic,projection) {
      const cells=new Map(projection.projection.rows.map(v=>[v.id,v.cells]));
      for(const column of projection.field_contracts) {
        const key=column.key,missing=!has(semantic,key),value=semantic[key];
        const field=[...row.children].find(el=>el.dataset.semanticField===key);
        requireIt(!!field,"FIELD_BINDING_MISSING");
        field.dataset.full=missing?'':String(value);
        field.dataset.sourceMissing=String(missing);
        field.dataset.sourceEmpty=String(!missing && value==='');
        const shown=missing?'Not provided':value===''?'Empty':cells.get(id)[key].display;
        const leaf=field.querySelector('.field-value');
        if(negotiated){
          const effect=continuity.mutateText(leaf,shown);
          if(effect.deferred){
            field.dataset.updatePending='true';
            leaf.dataset.retainedText=leaf.textContent;leaf.dataset.pendingCurrent=shown;
            pendingRowFields.add(id);
          }else{delete field.dataset.updatePending;
            delete leaf.dataset.retainedText;delete leaf.dataset.pendingCurrent;}
        }else leaf.textContent=shown;
      }
      if(negotiated && ![...row.querySelectorAll('mcel-field')].some(f=>f.dataset.updatePending==='true'))
        pendingRowFields.delete(id);
    }
    function reconcile(next,{initial=false}={}) {
      const previousProjection=result;
      const nextCache=new Map(),byId=new Map(next.source_rows.map(v=>[String(v.id),v]));
      const retained=[],changedIds=[];let added=0,replaced=0,updated=0,removed=0;
      let continuityUnverified=0,identityReplaced=0;
      // Validate all data before mutating the mounted DOM. Original records are
      // never edited; rows without generation evidence cannot claim continuity.
      for(const [id,semantic] of byId) {
        const raw=next.rawRecords.get(id),gen=generation(raw),before=rowCache.get(id);
        const transition=classifySourceTransition(!!before,true,before?.gen,gen);
        const continuous=transition==='continuous';
        if(transition==='continuity-unverified')continuityUnverified++;
        if(transition==='identity-replaced')identityReplaced++;
        const entry=continuous?before:{node:makeRow(id,semantic,next),gen};
        if(continuous)retained.push(id);
        else if(before)replaced++;
        else added++;
        const signature=JSON.stringify(raw);
        if(!continuous || before.signature!==signature) {
          fillRow(entry.node,id,semantic,next);updated++;changedIds.push(id);
        }
        entry.signature=signature;
        nextCache.set(id,entry);
      }
      const scroll=collection.scrollTop;
      const anchor=negotiated?scrollHost.captureAnchor():null;
      const focus=doc.activeElement?.closest?.('[data-row-id]');
      const focusId=focus && collection.contains(focus)?focus.dataset.rowId:null;
      const focusOld=focusId?rowCache.get(focusId):null;
      const selectedOld=selected && rowCache.get(selected);
      const selectedNew=selected && nextCache.get(selected);
      const selectionContinuous=!!(selectedOld && selectedNew && selectedOld===selectedNew);
      // Existing row nodes are appended in the new order, retaining listeners,
      // node identity and keyboard focus when their source generation is verified.
      // Avoid moving already correctly ordered nodes: even an append of the
      // same node can collapse the browser's native selection or lose focus.
      let cursor=collection.firstElementChild;
      for(const entry of nextCache.values()){
        if(entry.node===cursor){cursor=cursor.nextElementSibling;continue;}
        collection.insertBefore(entry.node,cursor);
      }
      for(const [id,entry] of rowCache)if(!nextCache.has(id)||nextCache.get(id)!==entry) {
        const sourceAbsent=!nextCache.has(id);
        if(sourceAbsent)removed++;
        // A remounted same-ID record is not an absent record, regardless of
        // whether generation verification succeeded. Never create a ghost
        // retirement from unverified continuity or an identity replacement.
        const retained=sourceAbsent && retirementEnabled && retirementSnapshot(id,entry,previousProjection);
        if(!retained)entry.node.remove();
      }
      if(!initial && selected && !selectionContinuous && !retiredRecords.has(selected)) {
        if(negotiated){overlayHost.close('record-identity-changed');expandedId=null;}
        selected=null;
        inspector.replaceChildren(node('p','', 'Previously inspected record is absent from this observation or changed identity. Select a current record.'));
      }
      rowCache.clear();for(const [id,entry] of nextCache)rowCache.set(id,entry);
      for(const id of [...pendingRowFields])if(!nextCache.has(id)&&!retiredRecords.has(id))pendingRowFields.delete(id);
      result=next;
      rebuildLayoutResult();
      if(retirement)retirement.tick();
      if(negotiated){
        for(const id of changedIds){
          if(retained.includes(id) && (expandedId===id || overlayHost.sourceId===id))
            refreshDetailsFor(id);
        }
      }
      if(selectionContinuous){
        if(negotiated){
          // Keep the original expanded DOM alive through telemetry churn;
          // do not destroy browser text selection or steal the viewport.
          if(expandedId && expandedId!==selected)removeInline();
        }else inspect(selected);
      }
      if(negotiated && !nextCache.has(expandedId) && !retiredRecords.has(expandedId))expandedId=null;
      if(negotiated && overlayHost.active && !overlayHost.sourceConnected)overlayHost.close('source-disappeared');
      if(focusId && rowCache.get(focusId)===focusOld && doc.activeElement!==focusOld.node)
        focusOld.node.focus({preventScroll:true});
      if(negotiated){if(!scrollHost.restoreAnchor(anchor).ok)collection.scrollTop=scroll;}
      else collection.scrollTop=scroll;
      return {added,replaced,updated,retained:retained.length,removed,
        continuityUnverified,identityReplaced,
        selected:selectionContinuous||retiredRecords.has(selected)?selected:null};
    }
    // Experimental continuity is explicit per consumer. Normal application
    // mounting, sorting, and renderer policy remain unchanged by default.
    const continuityMode=opts.layoutContinuity==="lab"?"lab":"off";
    let committedLayoutState=null;
    function installLayout() {
      const next=authority.mountSemanticCollectionLayout(container,layoutResult,registry,
        {observeResize:opts.observeResize!==false,continuity:continuityMode,
          previousState:committedLayoutState});
      if(next.status!=='pass'){
        const diagnostic=authority.evaluateSemanticCollectionLayout(container,layoutResult,registry);
        throw Error('MCEL_SEMANTIC_COLLECTION_LAYOUT_REFUSED: '+next.reason+
          ' / '+(diagnostic.reason||diagnostic.status));
      }
      applied=next;
    }
    reconcile(result,{initial:true});
    host.replaceChildren(container);
    try {installLayout();}
    catch(error){host.replaceChildren();throw error;}
    return Object.freeze({
      get layout(){if(negotiated)flushPendingDetails();return applied.refresh().plan;},
      get ids(){return result.source_rows.map(row=>String(row.id));},
      get inspectedId(){return selected;},
      get layoutCommittedState(){return applied?.getCommittedState?.()||null;},
      // Explicit user sort may reestablish source order. It must never be
      // blocked by implicit sticky row ordering in the lab layout.
      commitSourceRowOrder(){return applied?.commitSourceRowOrder?.()||{status:"unavailable"};},
      inspect(id){return !disposed && result.rawRecords.has(String(id))?
        (negotiated?reveal(String(id),'onSelect'):inspect(String(id))):false;},
      request(id,event){return !disposed && negotiated?reveal(String(id),event):{ok:false,reason:'not-opted-in'};},
      get negotiations(){return receipts.map(r=>structuredClone(r));},
      get expandedId(){return expandedId;},
      get floatingCard(){return overlayHost?.active||null;},
      update(rows,{observation}={}) {
        if(disposed)return {ok:false,code:'disposed'};
        // selectionchange is asynchronous: release stale presentation as soon as
        // the next synchronous lifecycle operation observes that selection ended.
        if(negotiated)flushPendingDetails();
        let next,decision;
        try {
          const incoming=project(rows,spec);
          const observations=global.McelObservationIntegrity;
          if(observation!==undefined && !observations)
            return {ok:false,code:'observation-service-unavailable'};
          if(observations){
            decision=observations.evaluate(result.rawRecords,incoming.rawRecords,observation,observationCursor);
            if(!decision.ok)return {ok:false,code:decision.reason,effects:[]};
            next=decision.complete?incoming:project([...decision.records.values()],spec);
          }else next=incoming;
        } catch(error) {return {ok:false,code:'projection-refused',message:String(error)};}
        const previous=result,previousRows=previous.source_rows.map(r=>previous.rawRecords.get(String(r.id)));
        const beforeSelection=selected;
        // A rejected layout must not leave newly generated retired snapshots.
        // Retirement is an effect of an accepted source-membership transition,
        // not a side effect that may outlive a failed update.
        const priorRetirements=new Set(retiredRecords.keys());
        try {
          committedLayoutState=applied.getCommittedState?.()||null;
          applied.unmount();applied=null;
          const receipt=reconcile(next);
          // Synchronous reconciliation and DOM order changes can end a native
          // selection before the asynchronous selectionchange event fires.
          // Never present a stale flag to layout authority without the actual
          // selection which authorized it.
          if(negotiated)flushPendingDetails();
          installLayout();
          if(decision)observationCursor=decision.cursor;
          return {ok:true,effects:[],...receipt,
            observation:decision?{complete:decision.complete,sequence:decision.sequence,
              authoritativeAbsences:decision.absent}:null};
        } catch(error) {
          // Release only retirements created by this failed transaction. Older
          // accepted retirements remain valid and retain their own clocks.
          for(const key of [...retiredRecords.keys()]){
            if(!priorRetirements.has(key))retirement?.end(key,'update-rollback');
          }
          try {reconcile(project(previousRows,spec));selected=beforeSelection;
            if(selected && result.rawRecords.has(selected)){
              if(negotiated)refreshDetailsFor(selected);else inspect(selected);
            }
            installLayout();}
          catch(_ignored) { /* a failed rollback is never reported as success */ }
          return {ok:false,code:'layout-refused',message:String(error),effects:[]};
        }
      },
      destroy(){if(disposed)return;disposed=true;retirement?.destroy();overlayHost?.destroy();scrollHost?.destroy();
        if(negotiated){doc.removeEventListener('pointermove',unsuppressOnExit);
          unobserveSelection?.();
          doc.removeEventListener('focusout',flushPendingDetails,true);}
        applied?.unmount();host.replaceChildren();},
      get retirement(){return retirement?{ids:retirement.ids,entries:retirement.ids.map(id=>retirement.get(id))}:null;},
      get source(){return result.source_rows.map(row=>({...row}));}
    });
  }
  global.McelSemanticCollectionWorkspace=Object.freeze({SCHEMA,SCHEMA_V2,validate,project,classifySourceTransition,mount});
})(typeof window!=="undefined"?window:globalThis);
