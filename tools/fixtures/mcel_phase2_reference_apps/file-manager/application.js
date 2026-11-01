"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"phase2-file-manager", title:"Phase 2 File Manager Probe"}, dsl => {
  const schema = dsl.field.list(dsl.field.record({id:dsl.field.text(),name:dsl.field.text()}), {key:"id"});
  const entries = dsl.state.derived("entries", schema);
  const selected = dsl.state.rendererLocal("selected", dsl.field.text(), {initial:""});
  const preview = dsl.state.derived("preview", dsl.field.text());
  const io = dsl.capability.external("file-reader", {risk:"external-read"});
  io.operation("list", "listFiles", {result:schema});
  io.operation("preview", "previewFile", {inputs:{fileId:dsl.field.text()},result:dsl.field.text()});
  const list = dsl.intent.capabilityRequest("list-files", io, {operation:"list",resultTo:entries});
  const select = dsl.intent.selectFromCollection("select-file", entries, selected);
  const read = dsl.intent.capabilityRequest("read-file", io, {operation:"preview",arguments:{fileId:selected},resultTo:preview});
  const surface = dsl.surface.bindHtml("workspace", {bindings:{
    locations:[dsl.surface.action("read-directory",{intent:list})],
    workspace:[dsl.surface.collection("file-list",{key:"id",fields:["name"],state:entries,select})],
    details:[dsl.surface.action("preview-action",{intent:read}),dsl.surface.text("preview-text",{value:preview})],
    "operation-status":[dsl.surface.receipt("receipt")]
  }});
  return {states:[entries,selected,preview],capabilities:[io],intents:[list,select,read],surfaces:[surface]};
});
