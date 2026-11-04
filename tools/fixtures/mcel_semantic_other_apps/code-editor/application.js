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
  const docShape=dsl.field.list(dsl.field.record({id:text,title:text}),{key:"id"});
  const docTargets=dsl.state.derived("component-doc-targets",docShape);
  const docSelected=dsl.state.rendererLocal("selected-component-doc",text,{initial:""});
  const docContent=dsl.state.derived("component-doc-content",text,{initial:""});
  const docViewportConfig=dsl.state.derived("component-doc-viewport-config",text,{initial:""});
  const docScriptResult=dsl.state.derived("component-doc-script-result",text,{initial:""});
  const scmComponentContract=dsl.state.derived("scm-component-contract",text,{initial:""});
  const scmRouteContract=dsl.state.derived("scm-route-contract",text,{initial:""});
  const studioSourceChecks=dsl.state.derived("studio-source-checks",text,{initial:""});
  const studioSerializedPreview=dsl.state.derived("studio-serialized-preview",text,{initial:""});
  const scmRuntimeResult=dsl.state.derived("scm-runtime-result",text,{initial:""});
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
  // The same generated-component documentation endpoints as the incumbent
  // viewport, exposed as bounded reads. Rendering stays owned by MCEL.
  const docs=dsl.capability.external("component-docs",{risk:"external-read",description:"Manifest-scoped generated component documentation reads"});
  docs.operation("manifest","listComponentDocs",{result:docShape});
  docs.operation("read","readComponentDoc",{inputs:{id:text},result:text});
  docs.operation("viewport-config","readDocViewportConfig",{result:text});
  // Definition inspection uses the production SCM manifest as its single source.
  // This does not run the legacy Studio's DOM-owned workbench or accept its
  // runtime state as evidence of a validated/committed semantic document.
  const viewport=dsl.capability.external("sandboxed-viewport",{risk:"external-read",description:"Execute an explicitly submitted pixel script in an opaque-origin, no-network sandbox; return only a bounded PNG and log"});
  viewport.operation("run","runSandboxedViewportScript",{inputs:{script:text},result:text});
  const scm=dsl.capability.external("scm-definition",{risk:"external-read",description:"Read-only incumbent Code Studio SCM component and route definitions"});
  scm.operation("component","readScmComponentDefinition",{result:text});
  scm.operation("route","readScmRouteDefinition",{result:text});
  const studio=dsl.capability.external("studio-source-tools",{risk:"external-read",description:"Read-only Code Studio source validation and clean-serialization preview of an opened draft"});
  studio.operation("check","checkStudioDraftSource",{inputs:{path:text,text:text},result:text});
  studio.operation("serialize-preview","previewStudioDraftSerialization",{inputs:{path:text,text:text},result:text});
  // Governed in-memory SCM lifecycle for the opened document. This is not a
  // server write, or permission to adopt the old Studio's authored layout.
  const scmRuntime=dsl.capability.external("scm-runtime",{risk:"external-read",description:"Document-bound SCM kernel mount, validation, serialization, repair, evidence and replay; no source writes"});
  for(const [name,method] of [["mount","mountStudioRuntime"],["validate","validateStudioRuntime"],
      ["serialize","serializeStudioRuntime"],["repair","repairStudioRuntime"],
      ["stage","stageStudioDraft"],["commit","commitStudioDraftInMemory"],
      ["route-enter","enterStudioRoute"],["route-leave","leaveStudioRoute"],
      ["evidence","readStudioRuntimeEvidence"],["replay","replayStudioRuntime"]])
    scmRuntime.operation(name,method,{inputs:{path:text,text:text},result:text});
  const listDocs=dsl.intent.capabilityRequest("list-component-docs",docs,{operation:"manifest",resultTo:docTargets});
  const chooseDoc=dsl.intent.selectFromCollection("choose-component-doc",docTargets,docSelected);
  const readDoc=dsl.intent.capabilityRequest("read-component-doc",docs,{operation:"read",arguments:{id:docSelected},resultTo:docContent});
  const readViewportConfig=dsl.intent.capabilityRequest("read-component-doc-viewport-config",docs,{operation:"viewport-config",resultTo:docViewportConfig});
  const runDocScript=dsl.intent.capabilityRequest("run-component-doc-script",viewport,{operation:"run",inputs:{script:text},resultTo:docScriptResult});
  const inspectScmComponent=dsl.intent.capabilityRequest("inspect-scm-component-contract",scm,{operation:"component",resultTo:scmComponentContract});
  const inspectScmRoute=dsl.intent.capabilityRequest("inspect-scm-route-contract",scm,{operation:"route",resultTo:scmRouteContract});
  const checkStudioSource=dsl.intent.capabilityRequest("check-studio-draft",studio,{operation:"check",arguments:{path:selected,text:draft},resultTo:studioSourceChecks});
  const previewStudioSource=dsl.intent.capabilityRequest("preview-studio-serialization",studio,{operation:"serialize-preview",arguments:{path:selected,text:draft},resultTo:studioSerializedPreview});
  const runtimeIntents={};
  for(const name of ["mount","validate","serialize","repair","stage","commit","route-enter","route-leave","evidence","replay"])
    runtimeIntents[name]=dsl.intent.capabilityRequest("studio-runtime-"+name,scmRuntime,
      {operation:name,arguments:{path:selected,text:draft},resultTo:scmRuntimeResult});
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
    documentation:[dsl.surface.action("list-component-docs",{intent:listDocs}),
      dsl.surface.collection("component-doc-targets",{key:"id",fields:["title"],state:docTargets,select:chooseDoc}),
      dsl.surface.action("read-component-doc",{intent:readDoc}),
      dsl.surface.text("component-doc-content",{value:docContent}),
      dsl.surface.action("read-component-doc-viewport-config",{intent:readViewportConfig}),
      dsl.surface.text("component-doc-viewport-config",{value:docViewportConfig}),
      dsl.surface.form("run-component-doc-script",{fields:{script:text},intent:runDocScript}),
      dsl.surface.text("component-doc-script-result",{value:docScriptResult})],
    studio:[dsl.surface.action("inspect-scm-component-contract",{intent:inspectScmComponent}),
      dsl.surface.text("scm-component-contract",{value:scmComponentContract}),
      dsl.surface.action("inspect-scm-route-contract",{intent:inspectScmRoute}),
      dsl.surface.text("scm-route-contract",{value:scmRouteContract}),
      dsl.surface.action("check-studio-draft",{intent:checkStudioSource}),
      dsl.surface.text("studio-source-checks",{value:studioSourceChecks}),
      dsl.surface.action("preview-studio-serialization",{intent:previewStudioSource}),
      dsl.surface.text("studio-serialized-preview",{value:studioSerializedPreview}),
      dsl.surface.action("studio-runtime-mount",{intent:runtimeIntents.mount}),
      dsl.surface.action("studio-runtime-validate",{intent:runtimeIntents.validate}),
      dsl.surface.action("studio-runtime-serialize",{intent:runtimeIntents.serialize}),
      dsl.surface.action("studio-runtime-repair",{intent:runtimeIntents.repair}),
      dsl.surface.action("studio-runtime-route-enter",{intent:runtimeIntents["route-enter"]}),
      dsl.surface.action("studio-runtime-stage",{intent:runtimeIntents.stage}),
      dsl.surface.action("studio-runtime-commit",{intent:runtimeIntents.commit}),
      dsl.surface.action("studio-runtime-route-leave",{intent:runtimeIntents["route-leave"]}),
      dsl.surface.action("studio-runtime-evidence",{intent:runtimeIntents.evidence}),
      dsl.surface.action("studio-runtime-replay",{intent:runtimeIntents.replay}),
      dsl.surface.text("scm-runtime-result",{value:scmRuntimeResult})],
    status:[dsl.surface.receipt("activity")]
  }});
  return {states:[files,selected,source,draft,reviewed,applied,aiderPreview,aiderContext,aiderJobs,aiderResult,docTargets,docSelected,docContent,docViewportConfig,docScriptResult,scmComponentContract,scmRouteContract,studioSourceChecks,studioSerializedPreview,scmRuntimeResult],
    capabilities:[reader,writer,reviewer,applier,aider,aiderReader,aiderController,docs,viewport,scm,studio,scmRuntime],
    intents:[listing,choose,read,directory,save,prepare,apply,previewAider,readAiderContext,readAiderJobs,
      listDocs,chooseDoc,readDoc,readViewportConfig,runDocScript,inspectScmComponent,inspectScmRoute,checkStudioSource,previewStudioSource,
      ...Object.values(runtimeIntents),runAider,archiveAider,resetAider,loadAider],surfaces:[surface]};
});
