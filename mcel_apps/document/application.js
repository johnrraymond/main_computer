"use strict";
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"document",title:"Document Editor"},dsl=>{
  const text=dsl.field.text();
  const listing=dsl.field.list(dsl.field.record({path:text,title:text,kind:text}),{key:"path"});
  const docs=dsl.state.derived("documents",listing);
  const path=dsl.state.rendererLocal("selected-path",text,{initial:""});
  const draft=dsl.state.rendererLocal("draft",text,{initial:"<p></p>"});
  const opened=dsl.state.derived("opened-document",text,{initial:"Scratchpad"});
  const status=dsl.state.derived("activity",text,{initial:"Ready — open a document or scratchpad"});
  const proposal=dsl.state.derived("proposal",text,{initial:"No proposal"});
  const reader=dsl.capability.external("document-reader",{risk:"external-read",description:"Pretty Docs and native document drafts"});
  reader.operation("list","listDocuments",{result:listing});
  reader.operation("open","openDocument",{inputs:{path:text},result:text});
  reader.operation("scratchpad","openScratchpad",{result:text});
  const writer=dsl.capability.external("draft-writer",{risk:"source-write",description:"Explicit save to native document draft storage"});
  writer.operation("create","createDocument",{inputs:{context:text},result:text});
  writer.operation("save","saveDraft",{inputs:{html:text},result:text});
  const ai=dsl.capability.external("document-ai",{risk:"external-read",description:"Read selection or complete document state and request an AI proposal"});
  ai.operation("lock-document","lockDocument",{result:text});
  ai.operation("lock","lockSelection",{result:text});
  ai.operation("propose","proposeReplacement",{inputs:{instruction:text,html:text},result:text});
  const apply=dsl.capability.external("document-apply",{risk:"consequential",description:"Explicitly confirmed local revision-checked replacement"});
  apply.operation("apply","applyReplacement",{result:text});
  const exporter=dsl.capability.external("document-export",{risk:"external-read",description:"Export current supported document pages through incumbent PDF backend"});
  exporter.operation("pdf","exportPdf",{inputs:{html:text},result:text});
  const list=dsl.intent.capabilityRequest("list-documents",reader,{operation:"list",resultTo:docs});
  const choose=dsl.intent.selectFromCollection("select-document",docs,path);
  const open=dsl.intent.capabilityRequest("open-document",reader,{operation:"open",arguments:{path},resultTo:opened});
  const scratch=dsl.intent.capabilityRequest("open-scratchpad",reader,{operation:"scratchpad",resultTo:opened});
  const create=dsl.intent.capabilityRequest("new-document",writer,{operation:"create",inputs:{context:text},resultTo:opened,confirmationRequired:false});
  const whole=dsl.intent.capabilityRequest("lock-document",ai,{operation:"lock-document",resultTo:proposal});
  const save=dsl.intent.capabilityRequest("save-candidate-draft",writer,{operation:"save",arguments:{html:draft},resultTo:status,confirmationRequired:false});
  const lock=dsl.intent.capabilityRequest("lock-selection",ai,{operation:"lock",resultTo:proposal});
  const suggest=dsl.intent.capabilityRequest("request-ai-proposal",ai,{operation:"propose",inputs:{instruction:text},arguments:{html:draft},resultTo:proposal});
  const accept=dsl.intent.capabilityRequest("apply-ai-proposal",apply,{operation:"apply",resultTo:proposal,confirmationRequired:false});
  const pdf=dsl.intent.capabilityRequest("export-pdf",exporter,{operation:"pdf",arguments:{html:draft},resultTo:status});
  const surface=dsl.surface.bindHtml("workspace",{bindings:{
    library:[dsl.surface.action("list-documents",{intent:list}),dsl.surface.collection("documents",{key:"path",fields:["title","path"],state:docs,select:choose}),dsl.surface.action("open-document",{intent:open}),dsl.surface.action("open-scratchpad",{intent:scratch})],
    header:[dsl.surface.text("opened-document",{value:opened}),dsl.surface.action("save-candidate-draft",{intent:save}),dsl.surface.action("export-pdf",{intent:pdf})],
    document:[dsl.surface.documentSurface("draft",{value:draft,label:"Document canvas",toolbar:true})],
    companion:[dsl.surface.action("lock-document",{intent:whole}),dsl.surface.action("lock-selection",{intent:lock}),dsl.surface.form("ai-instruction",{fields:{instruction:text},intent:suggest}),dsl.surface.text("proposal",{value:proposal}),dsl.surface.action("apply-ai-proposal",{intent:accept})],
    activity:[dsl.surface.text("activity",{value:status}),dsl.surface.receipt("receipts")]
  }});
  return {states:[docs,path,draft,opened,status,proposal],capabilities:[reader,writer,ai,apply,exporter],intents:[list,choose,open,scratch,create,save,whole,lock,suggest,accept,pdf],surfaces:[surface]};
});
