/* Independent Node runtime probe of the *same browser public engine*. No DOM,
 * effects or adapter stubs. Pass fixture filenames as CLI arguments.
 */
"use strict";
const fs=require("fs"),path=require("path"),vm=require("vm"),crypto=require("crypto");
const root=path.resolve(__dirname,"..");
const enginePath=path.join(root,"main_computer/web/applications/scripts/mcel-semantic-value-contract.js");
const consumerPath=path.join(root,"main_computer/web/applications/scripts/mcel-semantic-collection-workspace.js");
const source=fs.readFileSync(enginePath,"utf8"),consumer=fs.readFileSync(consumerPath,"utf8");
const context=vm.createContext({Intl,structuredClone,console});
vm.runInContext(source,context,{filename:enginePath});
vm.runInContext(consumer,context,{filename:consumerPath});
const contract=context.McelSemanticValueContract,collection=context.McelSemanticCollectionWorkspace;
const fixtures=process.argv.slice(2).map(f=>JSON.parse(fs.readFileSync(f,"utf8")));
const checks=[];
function check(name,condition,detail={}) {checks.push({name,status:condition?"PASS":"FAIL",...detail});}
function refuse(name,fn,code) {let actual=null;try{fn();}catch(e){actual=e.code||String(e);}check(name,actual===code,{expected:code,actual});}
function clone(obj){return JSON.parse(JSON.stringify(obj));}
function sha(data){return crypto.createHash("sha256").update(data).digest("hex");}
const sourceBefore=fixtures.map(f=>sha(JSON.stringify(f.rows)));
const projected=fixtures.map(f=>collection.project(f.rows,f.contract));
for(let i=0;i<fixtures.length;i++) {
  const f=fixtures[i],result=projected[i],values=result.value_records;
  check(f.domain+" common engine",result.schema===contract.SCHEMA && values.length===3 && result.source_rows.length===3);
  check(f.domain+" measured zero",values[0].fields.measurement.availability==="present" && values[0].fields.measurement.rawValue===0 && result.projection.rows[0].cells.measurement.display.startsWith("0"));
  check(f.domain+" unavailable is NOT measured zero",values[1].fields.measurement.availability==="unavailable" && result.projection.rows[1].cells.measurement.display==="Unavailable" && values[1].fields.measurement.rawValue===0);
  check(f.domain+" stale and conflict independent",values[2].fields.measurement.availability==="present" && values[2].fields.measurement.freshness==="stale" && values[2].fields.measurement.conflict.length===1 && result.projection.rows[2].cells.measurement.display.includes("conflicting"));
  check(f.domain+" derived precedence",values[0].fields.category.availability==="present" && values[0].fields.category.rawValue===f.contract.fields.find(x=>x.key==="category").derive.cases[0].value);
  check(f.domain+" exact source preserved",sha(JSON.stringify(f.rows))===sourceBefore[i] && result.rawRecords.get(values[0].id).details===undefined && result.value_records[0].fields.details.rawValue===f.rows[0][f.contract.fields.find(x=>x.key==="details").from]);
  const renamed=clone(f),mapping=Object.fromEntries(f.contract.sourceFields.map((key,n)=>[key,"arbitrary_"+(n+1)]));
  renamed.contract.sourceFields=f.contract.sourceFields.map(x=>mapping[x]);
  renamed.contract.fields.forEach(field=>{
    if(field.from)field.from=mapping[field.from];
    if(field.validity)for(const key of Object.keys(field.validity))field.validity[key]=mapping[field.validity[key]];
    if(field.derive?.cases)for(const option of field.derive.cases)if(option.field)option.field=mapping[option.field];
  });
  renamed.rows=f.rows.map(row=>Object.fromEntries(Object.keys(row).reverse().map(k=>[mapping[k],row[k]])));
  const swapped=collection.project(renamed.rows,renamed.contract);
  check(f.domain+" arbitrary field rename and source reorder",JSON.stringify(swapped.source_rows)===JSON.stringify(result.source_rows) && JSON.stringify(swapped.projection.rows)===JSON.stringify(result.projection.rows));
  const alternative=clone(f.contract);
  alternative.fields.find(x=>x.key==="measurement").tier="detail";
  alternative.fields.find(x=>x.key==="category").priority=5;
  const alt=collection.project(f.rows,alternative);
  check(f.domain+" declarative tiers only",alt.field_contracts.find(x=>x.key==="measurement").tier==="detail" && alt.layout_link.required_keys.join()===result.layout_link.required_keys.join());
}
const sample=fixtures[0],spec=sample.contract,row=sample.rows[0];
const valueField=spec.fields.find(f=>f.key==="measurement"),src=valueField.from;
for(const [name,change,expected] of [
 ["missing",r=>{delete r[src];delete r.ram_quality;},"missing"],
 ["explicit null",r=>{r[src]=null;delete r.ram_quality;},"null"],
 ["explicit empty",r=>{r[src]="";delete r.ram_quality;},"empty"],
 ["failed",r=>{r.ram_quality="failed";},"failed"],
 ["stale known",r=>{r.ram_age="stale";},"present"],
 ["contradictory known",r=>{r.ram_conflicts=["source A disagrees with B"];},"present"]]) {
  const copy=clone(row);change(copy);const observed=contract.rowValues(copy,spec).fields.measurement;
  check("orthogonal state: "+name,observed.availability===expected && (name!=="stale known" || observed.freshness==="stale") && (name!=="contradictory known" || observed.conflict.length===1));
}
const uncertain=clone(row);delete uncertain.flag_self;
const unknown=contract.rowValues(uncertain,spec).fields.category;
check("first-true propagates unavailable higher-priority evidence",unknown.availability==="unavailable");
const both=clone(row);both.flag_self=false;both.flag_owned=false;
check("first-true lawful default",contract.rowValues(both,spec).fields.category.rawValue==="Other");
function altered(change) {const value=clone(spec);change(value);return value;}
refuse("reject undeclared source",()=>contract.validate(altered(s=>{s.fields[1].from="unknown_xyz";})),"MCEL_SEMANTIC_UNKNOWN_SOURCE");
refuse("reject invalid dependency",()=>contract.validate(altered(s=>{s.fields[4].derive={kind:"coalesce",refs:["unknown_xyz"]};})),"MCEL_SEMANTIC_UNKNOWN_DEPENDENCY");
refuse("reject cyclic derivation",()=>contract.validate(altered(s=>{s.fields[4].derive={kind:"coalesce",refs:["category"]};})),"MCEL_SEMANTIC_DERIVATION_CYCLE");
refuse("reject wrong-type derivation",()=>contract.validate(altered(s=>{s.fields[4].derive={kind:"coalesce",refs:["measurement"]};})),"MCEL_SEMANTIC_DERIVATION_TYPE");
refuse("reject executable operator",()=>contract.validate(altered(s=>{s.fields[4].derive={kind:"eval",code:"globalThis.evil=true"};})),"MCEL_SEMANTIC_UNSUPPORTED_OPERATOR");
refuse("reject inconsistent present/missing",()=>{const bad=clone(row);delete bad.ram_bytes; contract.rowValues(bad,spec);},"MCEL_SEMANTIC_INCONSISTENT_AVAILABILITY");
refuse("reject quantity type mismatch",()=>{const bad=clone(row);bad.ram_bytes="0"; contract.rowValues(bad,spec);},"MCEL_SEMANTIC_TYPE_MISMATCH");
refuse("reject missing identity",()=>{const bad=clone(row);delete bad.proc_uid; contract.rowValues(bad,spec);},"MCEL_SEMANTIC_IDENTITY_MISSING");
refuse("reject invalid freshness",()=>{const bad=clone(row);bad.ram_age="freshish";contract.rowValues(bad,spec);},"MCEL_SEMANTIC_INVALID_FRESHNESS");
refuse("reject invalid conflict evidence",()=>{const bad=clone(row);bad.ram_conflicts={inject:true};contract.rowValues(bad,spec);},"MCEL_SEMANTIC_INVALID_CONFLICT");
refuse("reject unknown status",()=>{const bad=clone(row);bad.ram_quality="magically-present";contract.rowValues(bad,spec);},"MCEL_SEMANTIC_INVALID_AVAILABILITY");
const v1={schema:"mcel.semantic-collection-spec.v1",identity:"id",fields:[{key:"id",from:"id",kind:"identifier",tier:"primary",label:"ID"},{key:"size",from:"size",kind:"quantity",tier:"secondary",label:"Size",presentation:{units:"bytes"}}]};
const old=collection.project([{id:"legacy",size:2048}],v1);
check("legacy v1 unaltered",old.schema===v1.schema && old.projection.rows[0].cells.size.display==="2.0 KB" && old.value_records===undefined);
// Adversarial identity and internationalized original-value preservation: do
// not collapse visually similar identifiers or destroy source text to shorten it.
const international=clone(sample.rows[0]);
international.proc_uid="P-001";
international.command="操作/naïve/📦/".repeat(18)+"/exact-ending";
const alternate=clone(sample.rows[0]);alternate.proc_uid="P-01";
const internationalResult=collection.project([international,alternate],spec);
check("near-collision identity remains distinct",
  internationalResult.source_rows.length===2 &&
  internationalResult.source_rows[0].id==="P-001" && internationalResult.source_rows[1].id==="P-01");
check("Unicode long text compressed without altering original",
  internationalResult.projection.rows[0].cells.details.display.length<international.command.length &&
  internationalResult.value_records[0].fields.details.rawValue===international.command &&
  internationalResult.rawRecords.get("P-001").command===international.command);
let duplicateRejected=false;
try{collection.project([alternate,clone(alternate)],spec);}catch(e){duplicateRejected=String(e).includes("DUPLICATE_ID");}
check("duplicate semantic identity rejected",duplicateRejected);
const numeric=clone(sample.rows[0]);numeric.proc_uid=0;
check("numeric zero identity is valid (not missing)",collection.project([numeric],spec).source_rows[0].id==="0");
const forbidden=/\b(?:memory_rss|is_main_computer|tracking|parcel|proc_uid|task-manager)\b/i;
check("shared source contains no tested domain field dispatch",!forbidden.test(source+consumer));
check("shared modules identical for both fixtures",fixtures.length===2 && fixtures[0].domain!==fixtures[1].domain);
check("source rows unchanged",fixtures.every((f,i)=>sha(JSON.stringify(f.rows))===sourceBefore[i]));
const payload={schema:"mcel.semantic-generality-runtime-probe.v1",status:checks.every(x=>x.status==="PASS")?"PASS":"FAIL",
  checks,engine_sha256:sha(source),consumer_sha256:sha(consumer),fixture_sha256:process.argv.slice(2).map(f=>sha(fs.readFileSync(f))),
  permissions:{network:false,dom:false,filesystemWrites:false,operationalEffects:false},
  originalSchema:collection.SCHEMA,extendedSchema:collection.SCHEMA_V2};
process.stdout.write(JSON.stringify(payload,null,2)+"\n");
process.exitCode=payload.status==="PASS"?0:1;
