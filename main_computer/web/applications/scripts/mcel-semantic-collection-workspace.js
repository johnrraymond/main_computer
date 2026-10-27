/* MCEL semantic collection consumer: data -> declared semantic projection ->
 * registered elements -> shared layout authority. No Task Manager selectors,
 * process logic, network, mutations, capabilities, or operational effects.
 */
(function(global) {
  "use strict";
  const SCHEMA="mcel.semantic-collection-spec.v1";
  const LINK="mcel.representation-layout-link.experiment.v1";
  const kinds=new Set(["identifier","text","enum","quantity","long-text","timestamp"]);
  const tiers=new Set(["primary","secondary","detail"]);
  const has=(o,k)=>Object.prototype.hasOwnProperty.call(o,k);
  function requireIt(ok,reason) {if(!ok) throw Error("MCEL_SEMANTIC_COLLECTION_"+reason);}
  function validate(spec) {
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
  function mount(host,input,spec,opts={}) {
    requireIt(host && typeof host.replaceChildren==="function","HOST_INVALID");
    const registry=opts.registry||global.McelElementRegistry;
    const authority=opts.authority||global.McelSemanticLayoutInference;
    requireIt(registry && typeof authority?.mountSemanticCollectionLayout==="function", "AUTHORITY_UNAVAILABLE");
    const result=project(input,spec),doc=host.ownerDocument;
    const tag=id=>{const found=registry.get(id);requireIt(found?.renderer?.htmlTag,"UNREGISTERED_ELEMENT");return found.renderer.htmlTag;};
    const node=(tagName,css,value)=>{const x=doc.createElement(tagName);if(css)x.className=css;
      if(value!==undefined)x.textContent=String(value);return x;};
    const sourceById=new Map(result.source_rows.map(row=>[String(row.id),row]));
    const projected=new Map(result.projection.rows.map(row=>[String(row.id),row]));
    const container=node('section','mcel-semantic-collection');
    const collection=node(tag('element.core.collection'));
    collection.dataset.mcelElementId='element.core.collection';
    container.append(collection);
    const inspector=node('section','mcel-semantic-collection__inspector');
    inspector.setAttribute('aria-live','polite');
    inspector.append(node('p','', 'Select a row to inspect complete source values.'));
    function inspect(id) {
      const raw=result.rawRecords.get(id);
      if(!raw) return;
      inspector.replaceChildren(node('h3','', 'Exact record '+id));
      const dl=node('dl');
      // Inspect original keys, not merely visible/computed values. No shortening.
      for(const [key,value] of Object.entries(raw)) {
        dl.append(node('dt','',key),node('dd','',value===null?'null':
          typeof value==='object'?JSON.stringify(value):String(value)));
      }
      inspector.append(dl);
    }
    for(const semantic of result.source_rows) {
      const id=String(semantic.id),row=node(tag('element.core.collection-row'));
      row.dataset.mcelElementId='element.core.collection-row';row.dataset.rowId=id;
      row.tabIndex=0;row.setAttribute('role','button');row.setAttribute('aria-label','Inspect exact record '+id);
      for(const column of result.field_contracts) {
        const key=column.key,missing=!has(semantic,key),value=semantic[key];
        const field=node(tag('element.core.field'));
        field.dataset.mcelElementId='element.core.field';
        field.dataset.semanticField=key;field.dataset.sourceRowId=id;
        field.dataset.full=missing?'':String(value);
        field.dataset.sourceMissing=String(missing);
        field.dataset.sourceEmpty=String(!missing && value==='');
        field.append(node('span','field-label',column.label),
          node('span','field-value',missing?'Not provided':value===''?'Empty':projected.get(id).cells[key].display));
        row.append(field);
      }
      row.addEventListener('click',()=>inspect(id));
      row.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();inspect(id);}});
      collection.append(row);
    }
    container.append(inspector);
    // Shared authority validates every original/representation DOM binding.
    host.replaceChildren(container);
    const applied=authority.mountSemanticCollectionLayout(container,result,registry,{observeResize:opts.observeResize!==false});
    if(applied.status!=='pass') {host.replaceChildren();throw Error('MCEL_SEMANTIC_COLLECTION_LAYOUT_REFUSED: '+applied.reason);}
    let disposed=false;
    return Object.freeze({
      get layout(){return applied.refresh().plan;},
      get ids(){return result.source_rows.map(row=>String(row.id));},
      inspect(id){if(disposed) return false;if(!result.rawRecords.has(String(id)))return false;inspect(String(id));return true;},
      destroy(){if(disposed)return;disposed=true;applied.unmount();host.replaceChildren();},
      get source(){return result.source_rows.map(row=>({...row}));}
    });
  }
  global.McelSemanticCollectionWorkspace=Object.freeze({SCHEMA,validate,project,mount});
})(typeof window!=="undefined"?window:globalThis);
