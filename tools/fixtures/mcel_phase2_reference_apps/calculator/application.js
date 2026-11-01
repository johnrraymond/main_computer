"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"phase2-calculator",title:"Phase 2 Calculator Probe"},dsl=>{
  const mode = dsl.state.rendererLocal("mode",dsl.field.text().oneOf(["basic","scientific","graphing"]),{initial:"basic"});
  const result = dsl.state.derived("result",dsl.field.integer());
  const engine = dsl.capability.external("arithmetic",{risk:"external-read"});
  engine.operation("compute","evaluateExpression",{inputs:{expression:dsl.field.text()},result:dsl.field.integer()});
  const compute = dsl.intent.capabilityRequest("compute",engine,{operation:"compute",inputs:{expression:dsl.field.text()},resultTo:result});
  const switchMode = dsl.intent.selectView("switch-mode",mode);
  const surface = dsl.surface.bindHtml("workspace",{bindings:{
    modes:[dsl.surface.action("mode",{intent:switchMode})],
    workspace:[dsl.surface.form("expression",{fields:{expression:dsl.field.text()},intent:compute})],
    results:[dsl.surface.text("result",{value:result})],
    status:[dsl.surface.receipt("receipt")]
  }});
  return {states:[mode,result],capabilities:[engine],intents:[compute,switchMode],surfaces:[surface]};
});
