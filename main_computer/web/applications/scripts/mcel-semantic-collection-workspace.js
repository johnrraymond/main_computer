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
  function mount(host,input,spec,opts={}) {
    requireIt(host && typeof host.replaceChildren==="function","HOST_INVALID");
    const registry=opts.registry||global.McelElementRegistry;
    const authority=opts.authority||global.McelSemanticLayoutInference;
    requireIt(registry && typeof authority?.mountSemanticCollectionLayout==="function","AUTHORITY_UNAVAILABLE");
    let result=project(input,spec),disposed=false,selected=null;
    const doc=host.ownerDocument;
    const tag=id=>{const found=registry.get(id);requireIt(found?.renderer?.htmlTag,"UNREGISTERED_ELEMENT");return found.renderer.htmlTag;};
    const node=(tagName,css,value)=>{const x=doc.createElement(tagName);if(css)x.className=css;
      if(value!==undefined)x.textContent=String(value);return x;};
    const container=node('section','mcel-semantic-collection');
    const collection=node(tag('element.core.collection'));
    collection.dataset.mcelElementId='element.core.collection';
    container.append(collection);
    const inspector=node('section','mcel-semantic-collection__inspector');
    inspector.setAttribute('aria-live','polite');
    const prompt=()=>inspector.replaceChildren(node('p','', 'Select a row to inspect complete source values.'));
    prompt();
    container.append(inspector);
    const rowCache=new Map();
    const generation=raw=>{
      const field=spec.reconciliation?.generationFrom;
      const value=field && raw?.[field];
      return value===undefined||value===null||value===''?null:typeof value+':'+String(value);
    };
    function inspect(id) {
      const raw=result.rawRecords.get(id);
      if(!raw)return false;
      selected=id;
      inspector.replaceChildren(node('h3','', 'Exact record '+id));
      const dl=node('dl');
      for(const [key,value] of Object.entries(raw)) {
        dl.append(node('dt','',key),node('dd','',value===null?'null':
          typeof value==='object'?JSON.stringify(value):String(value)));
      }
      inspector.append(dl);
      const valueRecord=result.value_records?.find(record=>record.id===id);
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
      row.tabIndex=0;row.setAttribute('role','button');row.setAttribute('aria-label','Inspect exact record '+id);
      for(const column of projection.field_contracts) {
        const key=column.key,field=node(tag('element.core.field'));
        field.dataset.mcelElementId='element.core.field';field.dataset.semanticField=key;
        field.dataset.sourceRowId=id;
        field.append(node('span','field-label',column.label),node('span','field-value'));
        row.append(field);
      }
      row.addEventListener('click',()=>inspect(id));
      row.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();inspect(id);}});
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
        field.querySelector('.field-value').textContent=missing?'Not provided':
          value===''?'Empty':cells.get(id)[key].display;
      }
    }
    function reconcile(next,{initial=false}={}) {
      const nextCache=new Map(),byId=new Map(next.source_rows.map(v=>[String(v.id),v]));
      const retained=[];let added=0,replaced=0,updated=0,removed=0;
      // Validate all data before mutating the mounted DOM. Original records are
      // never edited; rows without generation evidence cannot claim continuity.
      for(const [id,semantic] of byId) {
        const raw=next.rawRecords.get(id),gen=generation(raw),before=rowCache.get(id);
        const continuous=!!(before && gen!==null && before.gen===gen);
        const entry=continuous?before:{node:makeRow(id,semantic,next),gen};
        if(continuous)retained.push(id);
        else if(before)replaced++;
        else added++;
        const signature=JSON.stringify(raw);
        if(!continuous || before.signature!==signature) {
          fillRow(entry.node,id,semantic,next);updated++;
        }
        entry.signature=signature;
        nextCache.set(id,entry);
      }
      const scroll=collection.scrollTop;
      const focus=doc.activeElement?.closest?.('[data-row-id]');
      const focusId=focus && collection.contains(focus)?focus.dataset.rowId:null;
      const focusOld=focusId?rowCache.get(focusId):null;
      const selectedOld=selected && rowCache.get(selected);
      const selectedNew=selected && nextCache.get(selected);
      const selectionContinuous=!!(selectedOld && selectedNew && selectedOld===selectedNew);
      // Existing row nodes are appended in the new order, retaining listeners,
      // node identity and keyboard focus when their source generation is verified.
      for(const entry of nextCache.values())collection.append(entry.node);
      for(const [id,entry] of rowCache)if(!nextCache.has(id)||nextCache.get(id)!==entry) {
        if(!nextCache.has(id))removed++;
        entry.node.remove();
      }
      if(!initial && selected && !selectionContinuous) {
        selected=null;
        inspector.replaceChildren(node('p','', 'Previously inspected record is absent from this observation or changed identity. Select a current record.'));
      }
      rowCache.clear();for(const [id,entry] of nextCache)rowCache.set(id,entry);
      result=next;
      if(selectionContinuous)inspect(selected);
      if(focusId && rowCache.get(focusId)===focusOld && doc.activeElement!==focusOld.node)
        focusOld.node.focus({preventScroll:true});
      collection.scrollTop=scroll;
      return {added,replaced,updated,retained:retained.length,removed,
        selected:selectionContinuous?selected:null};
    }
    let applied=null;
    function installLayout() {
      const next=authority.mountSemanticCollectionLayout(container,result,registry,
        {observeResize:opts.observeResize!==false});
      if(next.status!=='pass')throw Error('MCEL_SEMANTIC_COLLECTION_LAYOUT_REFUSED: '+next.reason);
      applied=next;
    }
    reconcile(result,{initial:true});
    host.replaceChildren(container);
    try {installLayout();}
    catch(error){host.replaceChildren();throw error;}
    return Object.freeze({
      get layout(){return applied.refresh().plan;},
      get ids(){return result.source_rows.map(row=>String(row.id));},
      get inspectedId(){return selected;},
      inspect(id){return !disposed && result.rawRecords.has(String(id))?inspect(String(id)):false;},
      update(rows) {
        if(disposed)return {ok:false,code:'disposed'};
        let next;
        try {next=project(rows,spec);} catch(error) {return {ok:false,code:'projection-refused',message:String(error)};}
        const previous=result,previousRows=previous.source_rows.map(r=>previous.rawRecords.get(String(r.id)));
        const beforeSelection=selected;
        try {
          applied.unmount();applied=null;
          const receipt=reconcile(next);
          installLayout();
          return {ok:true,effects:[],...receipt};
        } catch(error) {
          try {reconcile(project(previousRows,spec));selected=beforeSelection;
            if(selected && result.rawRecords.has(selected))inspect(selected);installLayout();}
          catch(_ignored) { /* a failed rollback is never reported as success */ }
          return {ok:false,code:'layout-refused',message:String(error),effects:[]};
        }
      },
      destroy(){if(disposed)return;disposed=true;applied?.unmount();host.replaceChildren();},
      get source(){return result.source_rows.map(row=>({...row}));}
    });
  }
  global.McelSemanticCollectionWorkspace=Object.freeze({SCHEMA,SCHEMA_V2,validate,project,mount});
})(typeof window!=="undefined"?window:globalThis);
