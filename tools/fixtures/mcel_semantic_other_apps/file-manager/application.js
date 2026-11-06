"use strict";
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"semantic-file-manager",title:"Semantic File Manager"},dsl=>{
  const text=dsl.field.text();
  const rootsShape=dsl.field.list(dsl.field.record({id:text,label:text}),{key:"id"});
  const entryShape=dsl.field.record({id:text,name:text,kind:text,pathDisplay:text,
    bytes:dsl.field.number(),mtime:dsl.field.number(),category:text,suggestedApp:text,
    mainComputerPurview:dsl.field.boolean(),mountedWindowsDrive:dsl.field.boolean()});
  const filesShape=dsl.field.list(entryShape,{key:"id"});
  const previewShape=dsl.field.record({readable:dsl.field.boolean(),reason:text,content:text,entry:entryShape});
  const roots=dsl.state.derived("roots",rootsShape);
  const root=dsl.state.rendererLocal("selected-root",text,{initial:""});
  const files=dsl.state.derived("entries",filesShape);
  const chosen=dsl.state.rendererLocal("selected-entry",text,{initial:""});
  const detail=dsl.state.derived("preview",previewShape);
  const openedWith=dsl.state.derived("opened-with",text);
  const reader=dsl.capability.external("filesystem-read",{risk:"external-read",description:"Existing bounded read-only file explorer endpoints"});
  reader.operation("roots","listRoots",{result:rootsShape});
  reader.operation("list","listDirectory",{inputs:{rootId:text},result:filesShape});
  reader.operation("preview","readPreview",{inputs:{rootId:text,relativePath:text},result:previewShape});
  reader.operation("enter","openDirectory",{inputs:{rootId:text,relativePath:text},result:filesShape});
  reader.operation("search","searchEntries",{inputs:{rootId:text,query:text},result:filesShape});
  const opener=dsl.capability.external("application-navigation",{risk:"external-read",description:"Navigate to an application suggested by an observed entry"});
  opener.operation("open","openSuggestedApp",{inputs:{rootId:text,relativePath:text},result:text});
  const openWith=dsl.intent.capabilityRequest("open-with",opener,{operation:"open",arguments:{rootId:root,relativePath:chosen},resultTo:openedWith});
  const load=dsl.intent.capabilityRequest("load-roots",reader,{operation:"roots",resultTo:roots});
  const chooseRoot=dsl.intent.selectFromCollection("choose-root",roots,root);
  const browse=dsl.intent.capabilityRequest("browse",reader,{operation:"list",arguments:{rootId:root},resultTo:files});
  const enter=dsl.intent.capabilityRequest("enter-directory",reader,{operation:"enter",inputs:{relativePath:text},arguments:{rootId:root},resultTo:files});
  const search=dsl.intent.capabilityRequest("search",reader,{operation:"search",inputs:{query:text},arguments:{rootId:root},resultTo:files});
  const chooseFile=dsl.intent.selectFromCollection("choose-entry",files,chosen);
  const preview=dsl.intent.capabilityRequest("preview-entry",reader,{operation:"preview",arguments:{rootId:root,relativePath:chosen},resultTo:detail});
  const surface=dsl.surface.bindHtml("application",{bindings:{
    locations:[dsl.surface.action("load-roots",{intent:load}),dsl.surface.collection("roots",{key:"id",fields:["label"],state:roots,select:chooseRoot})],
    directory:[dsl.surface.action("browse",{intent:browse}),dsl.surface.form("open-directory",{fields:{relativePath:text},defaults:{relativePath:""},intent:enter}),dsl.surface.form("search",{fields:{query:text},intent:search}),dsl.surface.collection("entries",{key:"id",fields:["name","kind","bytes","category","pathDisplay","suggestedApp"],state:files,select:chooseFile})],
    inspection:[dsl.surface.text("selected-entry-details",{value:chosen}),dsl.surface.action("preview-file",{intent:preview}),dsl.surface.text("preview-content",{value:detail}),dsl.surface.action("open-with",{intent:openWith}),
      dsl.surface.text("open-with-result",{value:openedWith})],
    status:[dsl.surface.receipt("activity")]
  }});
  return {states:[roots,root,files,chosen,detail,openedWith],capabilities:[reader,opener],intents:[load,chooseRoot,browse,enter,search,chooseFile,preview,openWith],surfaces:[surface]};
});
