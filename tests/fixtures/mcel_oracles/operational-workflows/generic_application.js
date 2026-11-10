"use strict";
// A non-SLH, layout-free canary. Its phase comes from a capability response,
// not a renderer-owned view selector; UI applicability must follow that phase.
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id:"generic-activation-canary",title:"Generic Resource Activation",semanticVersion:"1"},
  dsl => {
    const text = dsl.field.text();
    const phaseSchema = text.oneOf(["inactive","active"]);
    const phase = dsl.state.derived("phase",phaseSchema);
    const entries = dsl.state.derived("entries",dsl.field.list(
      dsl.field.record({id:text,label:text}),{key:"id"}));
    const observe = dsl.capability.external("observer",{risk:"external-read"});
    observe.operation("phase","observePhase",{result:phaseSchema});
    observe.operation("entries","observeEntries",{result:dsl.field.list(
      dsl.field.record({id:text,label:text}),{key:"id"})});
    const enable = dsl.capability.external("activation",{risk:"consequential"});
    enable.operation("enable","enableResource",{result:text,risk:"consequential"});
    const result = dsl.state.derived("result",text);
    const inspect = dsl.intent.capabilityRequest("inspect",observe,{operation:"phase",resultTo:phase});
    const inspectEntries = dsl.intent.capabilityRequest("inspect-entries",observe,{operation:"entries",resultTo:entries});
    const activate = dsl.intent.capabilityRequest("activate",enable,{operation:"enable",resultTo:result});
    const activeWhen = {state:phase,equals:"active"};
    const inactiveWhen = {state:phase,equals:"inactive"};
    const surface = dsl.surface.define("generic",{purpose:"monitor",root:
      dsl.surface.region("main",{children:[
        dsl.surface.action("inspect",{intent:inspect}),
        dsl.surface.action("inspect-entries",{intent:inspectEntries}),
        dsl.surface.text("phase",{value:phase}),
        dsl.surface.collection("entries",{state:entries,key:"id",fields:["label"]}),
        dsl.surface.action("activate",{intent:activate,when:inactiveWhen}),
        dsl.surface.text("active",{value:phase,when:activeWhen}),
        dsl.surface.receipt("evidence")
      ]})
    });
    return {states:[phase,entries,result],capabilities:[observe,enable],
      intents:[inspect,inspectEntries,activate],surfaces:[surface]};
  }
);
