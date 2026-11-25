"use strict";
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"document-surface-native-probe",title:"Document Surface Native Probe"},dsl=>{
  const text=dsl.field.text();
  const left=dsl.state.rendererLocal("left-html",text,{initial:"<p>First <strong>document</strong></p>"});
  const right=dsl.state.rendererLocal("right-html",text,{initial:"<p>Second document</p>"});
  const mode=dsl.state.rendererLocal("page-mode",text.oneOf(["paged","endless"]),{initial:"paged"});
  const result=dsl.state.derived("inspection",text);
  const inspector=dsl.capability.external("inspector",{risk:"external-read"});
  inspector.operation("inspect","inspectDraft",{inputs:{html:text},result:text});
  const inspect=dsl.intent.capabilityRequest("inspect",inspector,{operation:"inspect",arguments:{html:left},resultTo:result});
  const switchMode=dsl.intent.selectView("page-mode",mode);
  const surface=dsl.surface.bindHtml("workspace",{bindings:{
    modes:[dsl.surface.action("mode",{intent:switchMode})],
    left:[dsl.surface.documentSurface("left",{value:left,mode,label:"First document"})],
    right:[dsl.surface.documentSurface("right",{value:right,label:"Second document"})],
    status:[dsl.surface.action("inspect",{intent:inspect}),dsl.surface.text("inspection",{value:result})]
  }});
  return {states:[left,right,mode,result],capabilities:[inspector],intents:[inspect,switchMode],surfaces:[surface]};
});
