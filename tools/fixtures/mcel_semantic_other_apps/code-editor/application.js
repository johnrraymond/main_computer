"use strict";
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"semantic-code-editor",title:"Semantic Code Editor"},dsl=>{
  const text=dsl.field.text();
  const filesShape=dsl.field.list(dsl.field.record({path:text,name:text,kind:text}),{key:"path"});
  const files=dsl.state.derived("files",filesShape);
  const selected=dsl.state.rendererLocal("selected-path",text,{initial:""});
  const source=dsl.state.derived("source",text,{initial:""});
  const draft=dsl.state.rendererLocal("draft",text,{initial:""});
  const reviewShape=dsl.field.record({handle:text,path:text,before:text,after:text,details:text});
  const reviewed=dsl.state.derived("reviewed-change",reviewShape);
  const applied=dsl.state.derived("applied-change",text);
  const aiderPreview=dsl.state.derived("aider-preview",text);
  const aiderContext=dsl.state.derived("aider-context",text);
  const aiderJobs=dsl.state.derived("aider-jobs",text);
  const aiderResult=dsl.state.derived("aider-result",text);
  const reader=dsl.capability.external("source-reader",{risk:"external-read",description:"Existing bounded Code Editor file listing and read API"});
  reader.operation("list","listSourceFiles",{result:filesShape});
  reader.operation("read","readSource",{inputs:{path:text},result:text});
  reader.operation("directory","listDirectory",{inputs:{path:text},result:filesShape});
  const writer=dsl.capability.external("source-writer",{risk:"source-write",description:"Explicit project-contained source save with stale-source verification"});
  writer.operation("save","saveSource",{inputs:{path:text,text:text},result:text,risk:"source-write"});
  const reviewer=dsl.capability.external("reviewer",{risk:"external-read",description:"Existing project transaction prepare; no source writes"});
  reviewer.operation("prepare","prepareReviewedChange",{inputs:{path:text,text:text},result:reviewShape});
  const applier=dsl.capability.external("review-applier",{risk:"source-write",description:"Only an explicitly confirmed, server-issued reviewed transaction may apply"});
  applier.operation("apply","applyReviewedChange",{inputs:{path:text,review:reviewShape,text:text},result:text,risk:"source-write"});
  const aider=dsl.capability.external("aider-preview",{risk:"external-read",description:"Explicit dry-run command preview from the existing Aider service"});
  aider.operation("preview","previewAiderCommand",{inputs:{path:text,instruction:text},result:text});
  const aiderReader=dsl.capability.external("aider-context",{risk:"external-read",description:"Observed Aider session history, archives, and job status"});
  aiderReader.operation("context","readContext",{result:text});
  aiderReader.operation("jobs","readJobs",{result:text});
  const aiderController=dsl.capability.external("aider-control",{risk:"consequential",description:"Explicit confirmation for scoped Aider run and context changes"});
  aiderController.operation("run","runAider",{inputs:{path:text,instruction:text,files:text},result:text,risk:"consequential"});
  aiderController.operation("archive","archiveAiderContext",{inputs:{path:text},result:text,risk:"consequential"});
  aiderController.operation("reset","resetAiderContext",{inputs:{path:text},result:text,risk:"consequential"});
  aiderController.operation("load","loadAiderArchive",{inputs:{archiveId:text},result:text,risk:"consequential"});
  const readAiderContext=dsl.intent.capabilityRequest("aider-context",aiderReader,{operation:"context",resultTo:aiderContext});
  const readAiderJobs=dsl.intent.capabilityRequest("aider-jobs",aiderReader,{operation:"jobs",resultTo:aiderJobs});
  const runAider=dsl.intent.capabilityRequest("run-aider",aiderController,{operation:"run",inputs:{instruction:text,files:text},arguments:{path:selected},resultTo:aiderResult});
  const archiveAider=dsl.intent.capabilityRequest("archive-aider-context",aiderController,{operation:"archive",arguments:{path:selected},resultTo:aiderResult});
  const resetAider=dsl.intent.capabilityRequest("reset-aider-context",aiderController,{operation:"reset",arguments:{path:selected},resultTo:aiderResult});
  const loadAider=dsl.intent.capabilityRequest("load-aider-archive",aiderController,{operation:"load",inputs:{archiveId:text},resultTo:aiderResult});
  const previewAider=dsl.intent.capabilityRequest("preview-aider-plan",aider,{operation:"preview",inputs:{instruction:text},arguments:{path:selected},resultTo:aiderPreview});
  const prepare=dsl.intent.capabilityRequest("prepare-reviewed-change",reviewer,{operation:"prepare",arguments:{path:selected,text:draft},resultTo:reviewed});
  const apply=dsl.intent.capabilityRequest("apply-reviewed-change",applier,{operation:"apply",arguments:{path:selected,review:reviewed,text:draft},resultTo:applied});
  const listing=dsl.intent.capabilityRequest("list-files",reader,{operation:"list",resultTo:files});
  const choose=dsl.intent.selectFromCollection("select-file",files,selected);
  const read=dsl.intent.capabilityRequest("open-source",reader,{operation:"read",arguments:{path:selected},resultTo:source});
  const directory=dsl.intent.capabilityRequest("browse-directory",reader,{operation:"directory",inputs:{path:text},resultTo:files});
  const save=dsl.intent.capabilityRequest("save-source",writer,{operation:"save",arguments:{path:selected,text:draft}});
  const surface=dsl.surface.bindHtml("application",{bindings:{
    navigation:[dsl.surface.action("list-files",{intent:listing}),dsl.surface.form("browse-directory",{fields:{path:text},defaults:{path:""},intent:directory}),dsl.surface.collection("files",{key:"path",fields:["name","kind"],state:files,select:choose})],
    document:[dsl.surface.action("open-source",{intent:read}),dsl.surface.documentEditor("source-editor",{value:source,path:selected,draft,loadIntent:read,saveIntent:save})],
    aider:[dsl.surface.form("aider-preview-form",{fields:{instruction:text},intent:previewAider}),
      dsl.surface.text("aider-preview-result",{value:aiderPreview}),
      dsl.surface.form("aider-run-form",{fields:{instruction:text,files:text},defaults:{files:""},intent:runAider}),
      dsl.surface.action("aider-context",{intent:readAiderContext}),
      dsl.surface.action("aider-jobs",{intent:readAiderJobs}),
      dsl.surface.action("archive-aider-context",{intent:archiveAider}),
      dsl.surface.action("reset-aider-context",{intent:resetAider}),
      dsl.surface.form("aider-load-archive",{fields:{archiveId:text},intent:loadAider}),
      dsl.surface.text("aider-result",{value:aiderResult}),
      dsl.surface.text("aider-session-history",{value:aiderContext}),
      dsl.surface.text("aider-job-status",{value:aiderJobs})],
    review:[dsl.surface.action("prepare-reviewed-change",{intent:prepare}),dsl.surface.text("review-details",{value:reviewed}),
      dsl.surface.action("apply-reviewed-change",{intent:apply}),dsl.surface.text("review-result",{value:applied})],
    status:[dsl.surface.receipt("activity")]
  }});
  return {states:[files,selected,source,draft,reviewed,applied,aiderPreview,aiderContext,aiderJobs,aiderResult],
    capabilities:[reader,writer,reviewer,applier,aider,aiderReader,aiderController],
    intents:[listing,choose,read,directory,save,prepare,apply,previewAider,readAiderContext,readAiderJobs,
      runAider,archiveAider,resetAider,loadAider],surfaces:[surface]};
});
