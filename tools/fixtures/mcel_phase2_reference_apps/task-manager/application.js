"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"phase2-task-manager",title:"Phase 2 Task Manager Probe"},dsl=>{
  const processes = dsl.state.derived("processes",dsl.field.list(dsl.field.record({processKey:dsl.field.text(),name:dsl.field.text(),cpu:dsl.field.integer()}),{key:"processKey"}));
  const selected = dsl.state.rendererLocal("selected",dsl.field.text(),{initial:""});
  const tab = dsl.state.rendererLocal("tab",dsl.field.text().oneOf(["processes","resources","services","activity"]),{initial:"processes"});
  const reader = dsl.capability.external("monitor",{risk:"external-read"});
  reader.operation("list","observeProcesses",{result:processes.record.schema});
  const writer = dsl.capability.external("process-control",{risk:"consequential"});
  writer.operation("terminate","terminateProcess",{inputs:{processKey:dsl.field.text()}});
  const refresh = dsl.intent.capabilityRequest("refresh",reader,{operation:"list",resultTo:processes});
  const choose = dsl.intent.selectFromCollection("choose-process",processes,selected);
  const switchTab = dsl.intent.selectView("switch-tab",tab);
  const terminate = dsl.intent.capabilityRequest("terminate",writer,{operation:"terminate",arguments:{processKey:selected}});
  const surface = dsl.surface.bindHtml("workspace",{bindings:{
    modes:[dsl.surface.action("switch-tab",{intent:switchTab})],
    workspace:[dsl.surface.collection("process-list",{key:"processKey",fields:["name","cpu"],state:processes,select:choose}),dsl.surface.action("refresh",{intent:refresh})],
    commands:[dsl.surface.action("terminate",{intent:terminate})],
    status:[dsl.surface.receipt("receipt")]
  }});
  return {states:[processes,selected,tab],capabilities:[reader,writer],intents:[refresh,choose,switchTab,terminate],surfaces:[surface]};
});
