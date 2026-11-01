"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"semantic-file-probe", title:"Semantic File Probe"}, dsl => {
  const root = dsl.field.record({id:dsl.field.text(),name:dsl.field.text()});
  const entry = dsl.field.record({id:dsl.field.text(),name:dsl.field.text(),kind:dsl.field.text()});
  const roots = dsl.state.canonical("roots", dsl.field.list(root,{key:"id"}), {initial:[]});
  const entries = dsl.state.canonical("entries",dsl.field.list(entry,{key:"id"}),{initial:[]});
  const preview = dsl.state.canonical("preview",dsl.field.text(),{initial:""});
  const rootList = dsl.surface.collection("roots",{state:roots,key:"id",fields:["name"]});
  const entryList = dsl.surface.collection("entries",{state:entries,key:"id",fields:["name","kind"]});
  const previewText = dsl.surface.text("preview",{value:preview});
  const receipt = dsl.surface.receipt("operation-receipts");
  const surface = dsl.surface.bindHtml("file-manager", {
    bindings: {
      locations:[rootList], workspace:[entryList], details:[previewText],
      "operation-status":[receipt],
    },
    relations: [
      dsl.surface.relate("locations","supports","workspace"),
      dsl.surface.relate("details","contextual-to","workspace"),
    ],
  });
  return {states:[roots,entries,preview],surfaces:[surface]};
});
