"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"phase2-code-editor",title:"Phase 2 Code Editor Probe"},dsl=>{
  const draft = dsl.state.canonical("draft",dsl.field.text(),{initial:""});
  const loaded = dsl.state.derived("loaded",dsl.field.text());
  const fileId = dsl.state.rendererLocal("file-id",dsl.field.text(),{initial:""});
  const reader = dsl.capability.external("read-source",{risk:"external-read"});
  reader.operation("open","readSource",{inputs:{fileId:dsl.field.text()},result:dsl.field.text()});
  const writer = dsl.capability.external("write-source",{risk:"source-write"});
  writer.operation("save","writeSource",{inputs:{fileId:dsl.field.text(),content:dsl.field.text()}});
  const open = dsl.intent.capabilityRequest("open",reader,{operation:"open",arguments:{fileId},resultTo:loaded});
  const edit = dsl.intent.mutation("edit",{inputs:{content:dsl.field.text()},change:({input})=>[draft.set(input.content)]});
  const save = dsl.intent.capabilityRequest("save",writer,{operation:"save",arguments:{fileId,content:draft}});
  const surface = dsl.surface.bindHtml("workspace",{bindings:{
    files:[dsl.surface.action("open",{intent:open})],
    workspace:[dsl.surface.form("edit-form",{fields:{content:dsl.field.text()},intent:edit}),dsl.surface.text("draft",{value:draft})],
    commands:[dsl.surface.action("save",{intent:save})],
    status:[dsl.surface.receipt("receipt")]
  }});
  return {states:[draft,loaded,fileId],capabilities:[reader,writer],intents:[open,edit,save],surfaces:[surface]};
});
