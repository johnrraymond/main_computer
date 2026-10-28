/* MCEL semantic collection contract v2: pure, bounded, domain-neutral value
 * interpretation. This extends the existing semantic-collection contract family;
 * v1 consumers retain their exact legacy projection path. No IO, DOM or effects.
 */
(function(global) {
  "use strict";
  const SCHEMA="mcel.semantic-collection-spec.v2";
  const VALUE_SCHEMA="mcel.semantic-value.v1";
  const KINDS=new Set(["identifier","text","enum","quantity","long-text","timestamp","boolean"]);
  const TIERS=new Set(["primary","secondary","detail"]);
  const AVAIL=new Set(["present","missing","null","empty","unavailable","failed"]);
  const FRESH=new Set(["fresh","stale","unknown"]);
  const NAME=/^[a-zA-Z][\w-]*$/;
  const has=(obj,key)=>Object.prototype.hasOwnProperty.call(obj,key);
  const issue=(code,detail="")=>{const error=Error("MCEL_SEMANTIC_"+code+(detail?": "+detail:""));error.code="MCEL_SEMANTIC_"+code;throw error;};
  const name=(x)=>typeof x==="string" && NAME.test(x);
  const scalar=(x)=>x===null || ["string","boolean","number","bigint"].includes(typeof x);
  function validate(spec) {
    if(!spec || spec.schema!==SCHEMA || !Array.isArray(spec.sourceFields) ||
       !spec.sourceFields.length || spec.sourceFields.length>64 ||
       spec.sourceFields.some(x=>!name(x)) || new Set(spec.sourceFields).size!==spec.sourceFields.length ||
       !Array.isArray(spec.fields) || !spec.fields.length || spec.fields.length>16 ||
       !name(spec.identity)) issue("SPEC_INVALID");
    if(spec.reconciliation!==undefined &&
       (!spec.reconciliation || typeof spec.reconciliation!=="object" ||
        Array.isArray(spec.reconciliation) ||
        Object.keys(spec.reconciliation).some(k=>k!=="generationFrom") ||
        (spec.reconciliation.generationFrom!==undefined &&
         !spec.sourceFields.includes(spec.reconciliation.generationFrom)))) issue("RECONCILIATION_INVALID");
    const keys=new Set(),sources=new Set(spec.sourceFields);
    for(const field of spec.fields) {
      if(!field || !name(field.key) || keys.has(field.key) || !KINDS.has(field.kind) ||
         !TIERS.has(field.tier) || typeof field.label!=="string" || !field.label.trim() ||
         (has(field,"from")===has(field,"derive")) ||
         (field.priority!==undefined && (!Number.isFinite(field.priority)||field.priority<0)) ||
         (field.presentation?.maxChars!==undefined && (!Number.isInteger(field.presentation.maxChars)||field.presentation.maxChars<16||field.presentation.maxChars>512)) ||
         (field.presentation?.units!==undefined && field.presentation.units!=="bytes") ||
         (field.presentation?.compactDisclosure!==undefined && typeof field.presentation.compactDisclosure!=="boolean") ||
         (field.exact!==undefined && typeof field.exact!=="boolean")) issue("FIELD_INVALID",String(field?.key));
      keys.add(field.key);
      if(has(field,"from") && !sources.has(field.from)) issue("UNKNOWN_SOURCE",field.from);
      if(field.validity!==undefined) {
        if(!field.validity || typeof field.validity!=="object" || Array.isArray(field.validity)) issue("VALIDITY_INVALID",field.key);
        for(const key of Object.keys(field.validity)) {
          if(!["availabilityFrom","freshnessFrom","conflictFrom"].includes(key) || !sources.has(field.validity[key]))
            issue("UNKNOWN_SOURCE",field.key+"."+key);
        }
      }
    }
    const ident=spec.fields.find(f=>f.key===spec.identity);
    if(!ident || ident.tier!=="primary" || ident.kind!=="identifier") issue("IDENTITY_INVALID");
    const graph=new Map();
    for(const field of spec.fields) {
      const d=field.derive;
      if(!d) {graph.set(field.key,[]);continue;}
      const refs=[];
      if(d.kind==="first-true") {
        if(!Array.isArray(d.cases) || !d.cases.length || d.cases.length>16 ||
           d.cases.some(c=>!c || !scalar(c.value) || c.value===null || (has(c,"field")===has(c,"ref"))))
          issue("DERIVATION_INVALID",field.key);
        for(const c of d.cases) {
          if(has(c,"field") && !sources.has(c.field)) issue("UNKNOWN_SOURCE",c.field);
          if(has(c,"ref")) refs.push(c.ref);
        }
        if(has(d,"default") && (!scalar(d.default) || d.default===null)) issue("DERIVATION_INVALID",field.key);
      } else if(d.kind==="coalesce" || d.kind==="concat") {
        if(!Array.isArray(d.refs) || !d.refs.length || d.refs.length>16 ||
           d.refs.some(x=>!name(x))) issue("DERIVATION_INVALID",field.key);
        if(d.kind==="concat" && (field.kind!=="text" && field.kind!=="long-text" ||
           (d.separator!==undefined && (typeof d.separator!=="string" || d.separator.length>32))))
          issue("DERIVATION_TYPE",field.key);
        refs.push(...d.refs);
      } else issue("UNSUPPORTED_OPERATOR",String(d.kind));
      for(const ref of refs) if(!keys.has(ref)) issue("UNKNOWN_DEPENDENCY",field.key+" -> "+ref);
      if(d.kind==="coalesce" && refs.some(ref=>spec.fields.find(f=>f.key===ref)?.kind!==field.kind))
        issue("DERIVATION_TYPE",field.key);
      if(d.kind==="first-true" && refs.some(ref=>spec.fields.find(f=>f.key===ref)?.kind!=="boolean"))
        issue("DERIVATION_TYPE",field.key);
      if(d.kind==="concat" && refs.some(ref=>!["identifier","enum","text","long-text","timestamp"].includes(spec.fields.find(f=>f.key===ref)?.kind)))
        issue("DERIVATION_TYPE",field.key);
      graph.set(field.key,refs);
    }
    const visiting=new Set(),done=new Set();
    function visit(key) {
      if(visiting.has(key)) issue("DERIVATION_CYCLE",key);
      if(done.has(key))return;
      visiting.add(key);for(const ref of graph.get(key)||[]) visit(ref);
      visiting.delete(key);done.add(key);
    }
    for(const key of keys)visit(key);
    return spec;
  }
  function automatic(value,exists) {
    return !exists?"missing":value===null?"null":value===""?"empty":"present";
  }
  function typecheck(value,kind,key) {
    if(value===undefined || value===null || value==="")return;
    if(kind==="quantity" && (typeof value!=="number" || !Number.isFinite(value))) issue("TYPE_MISMATCH",key);
    if(kind==="boolean" && typeof value!=="boolean") issue("TYPE_MISMATCH",key);
    if(["text","long-text","enum","timestamp"].includes(kind) && typeof value!=="string") issue("TYPE_MISMATCH",key);
    if(kind==="identifier" && !(typeof value==="string" || typeof value==="number" && Number.isFinite(value) || typeof value==="bigint")) issue("TYPE_MISMATCH",key);
  }
  function rowValues(row,spec) {
    validate(spec);
    if(!row || typeof row!=="object" || Array.isArray(row))issue("ROW_INVALID");
    const fields=new Map(spec.fields.map(f=>[f.key,f]));
    const calculated=new Map();
    function fromRaw(from,kind,field) {
      const exists=has(row,from),value=exists?row[from]:undefined;
      let availability=automatic(value,exists),freshness="unknown",conflict=[];
      const meta=field?.validity || {};
      if(meta.availabilityFrom && has(row,meta.availabilityFrom)) {
        const raw=row[meta.availabilityFrom];
        if(!AVAIL.has(raw)) issue("INVALID_AVAILABILITY",String(raw));
        availability=raw;
      }
      if(meta.freshnessFrom && has(row,meta.freshnessFrom)) {
        freshness=row[meta.freshnessFrom];if(!FRESH.has(freshness))issue("INVALID_FRESHNESS",String(freshness));
      }
      if(meta.conflictFrom && has(row,meta.conflictFrom)) {
        const raw=row[meta.conflictFrom];
        if(raw===false || raw===null)conflict=[];
        else if(raw===true)conflict=[{source:meta.conflictFrom,reason:"contradictory evidence"}];
        else if(Array.isArray(raw) && raw.length<=16 && raw.every(x=>typeof x==="string" && x.length<=256))
          conflict=raw.map(reason=>({source:meta.conflictFrom,reason}));
        else issue("INVALID_CONFLICT",meta.conflictFrom);
      }
      if(availability==="present" && automatic(value,exists)!=="present")issue("INCONSISTENT_AVAILABILITY",from);
      if(availability==="present")typecheck(value,kind,from);
      return {schema:VALUE_SCHEMA,availability,freshness,conflict,rawValue:value,
        source:{path:from,exists},dependencies:[],type:kind};
    }
    function computed(key) {
      if(calculated.has(key))return calculated.get(key);
      const field=fields.get(key);
      let record;
      if(has(field,"from")) record=fromRaw(field.from,field.kind,field);
      else {
        const d=field.derive,dependency=[];
        const fromRef=(ref)=>{dependency.push(ref);return computed(ref);};
        let value,availability="missing",freshness="unknown",conflict=[];
        const inputs=[];
        if(d.kind==="first-true") {
          let undecidable=false,failed=false;
          for(const entry of d.cases) {
            const fact=has(entry,"ref")?fromRef(entry.ref):fromRaw(entry.field,"boolean");
            dependency.push(has(entry,"ref")?"ref:"+entry.ref:"source:"+entry.field);
            inputs.push(fact);
            if(fact.availability==="failed") {failed=true;break;}
            if(fact.availability!=="present") {undecidable=true;continue;}
            if(typeof fact.rawValue!=="boolean")issue("TYPE_MISMATCH",field.key);
            if(fact.rawValue===true){
              if(!undecidable){value=entry.value;availability="present";}
              break;
            }
          }
          if(failed)availability="failed";
          else if(availability!=="present" && !undecidable && has(d,"default")) {
            value=d.default;availability="present";
          } else if(undecidable && availability!=="present")availability="unavailable";
        } else if(d.kind==="coalesce") {
          for(const ref of d.refs){const v=fromRef(ref);inputs.push(v);
            if(v.availability==="failed"){availability="failed";break;}
            if(v.availability==="present") {value=v.rawValue;availability="present";break;}
            if(v.availability==="unavailable")availability="unavailable";
          }
        } else if(d.kind==="concat") {
          for(const ref of d.refs){const v=fromRef(ref);inputs.push(v);}
          if(inputs.some(v=>v.availability==="failed"))availability="failed";
          else if(inputs.some(v=>v.availability!=="present"))availability="unavailable";
          else {value=inputs.map(v=>String(v.rawValue)).join(d.separator??" ");availability="present";}
        }
        if(inputs.some(v=>v.freshness==="stale"))freshness="stale";
        else if(inputs.length && inputs.every(v=>v.freshness==="fresh"))freshness="fresh";
        conflict=inputs.flatMap(v=>v.conflict);
        if(availability==="present")typecheck(value,field.kind,field.key);
        record={schema:VALUE_SCHEMA,availability,freshness,conflict,rawValue:value,
          source:{path:null,exists:false},dependencies:dependency,type:field.kind};
      }
      calculated.set(key,record);return record;
    }
    for(const field of spec.fields)computed(field.key);
    const identity=calculated.get(spec.identity);
    if(identity.availability!=="present" || String(identity.rawValue)==="")issue("IDENTITY_MISSING");
    return {id:String(identity.rawValue),fields:Object.fromEntries(calculated)};
  }
  function display(record,field) {
    if(record.availability!=="present") {
      return ({missing:"Not provided",null:"Null",empty:"Empty",unavailable:"Unavailable",failed:"Failed"})[record.availability];
    }
    const value=record.rawValue;
    let result=String(value);
    if(field.kind==="quantity") {
      if(field.presentation?.units==="bytes") {
        const units=["B","KB","MB","GB","TB"],n=Math.abs(value);let v=n,i=0;
        while(v>=1024 && i<units.length-1){v/=1024;i++;}
        result=(value<0?"-":"")+(i===0?String(Math.round(v)):v.toFixed(1))+" "+units[i];
      } else result=new Intl.NumberFormat().format(value);
    }
    if(field.exact!==true && field.kind==="long-text" && result.length>(field.presentation?.maxChars??64)) {
      const limit=field.presentation?.maxChars??64;
      result=result.slice(0,Math.max(1,limit-12))+"…"+result.slice(-11);
    }
    if(record.freshness==="stale")result+=" · stale";
    if(record.conflict.length)result+=" · conflicting";
    return result;
  }
  global.McelSemanticValueContract=Object.freeze({SCHEMA,VALUE_SCHEMA,validate,rowValues,display});
})(typeof window!=="undefined"?window:globalThis);
