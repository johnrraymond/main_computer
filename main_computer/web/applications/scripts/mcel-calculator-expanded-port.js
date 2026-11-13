"use strict";
/* Explicit, least-authority bridge to the existing Calculator computational
 * core and optional same-origin service endpoints. No layout or DOM wiring.
 */
(function(global){
  const operations=Object.freeze({
    "intent:evaluate": {cap:"capability:calculator-core",op:"evaluate",method:"evaluateCalculatorExpression",keys:["expression"]},
    "intent:draw-graph": {cap:"capability:calculator-core",op:"graph",method:"sampleGraphExpression",keys:["expression","xMin","xMax","yMin","yMax"]},
    "intent:evaluate-symbolic": {cap:"capability:calculator-service",op:"mathics",method:"evaluateMathics",keys:["expression"]},
    "intent:edit-graph-expression": {cap:"capability:calculator-core",op:"edit-graph",method:"editGraphExpression",keys:["expression","action","token","selectionStart","selectionEnd"]},
    "intent:clear-symbolic": {cap:"capability:calculator-core",op:"clear-symbolic",method:"clearSymbolic",keys:[]},
    "intent:calculator-chat": {cap:"capability:calculator-service",op:"chat",method:"calculatorEmbeddedChat",keys:["message"]}
  });
  function create(options={}) {
    const core=options.core || global.MainComputerCalculatorCore;
    const services=options.services || global.MainComputerCalculatorCapabilities;
    if(!core || typeof core.evaluateCalculatorExpression!=="function" ||
       typeof core.sampleCalculatorGraphExpression!=="function") throw Error("MCEL_CALC_EXPANDED:core-unavailable");
    const fetcher=options.fetcher;
    const text=x=>typeof x==="string" && x.length>0 && x.length<=4096;
    function permitted(req){
      if(!req || req.appId!=="semantic-calculator-expanded" || req.risk!=="external-read")return false;
      const spec=operations[req.intentId];
      if(!spec || req.effectId!=="effect:"+req.intentId.slice(7)+".request" ||
         req.capabilityId!==spec.cap || req.operation!==spec.op || req.runtimeMethod!==spec.method ||
         !req.arguments || typeof req.arguments!=="object" || Array.isArray(req.arguments))return false;
      const keys=Object.keys(req.arguments).sort();
      if(JSON.stringify(keys)!==JSON.stringify([...spec.keys].sort()))return false;
      if(spec.op==="graph")return text(req.arguments.expression) &&
        ["xMin","xMax","yMin","yMax"].every(k=>Number.isFinite(req.arguments[k])) &&
        req.arguments.xMin<req.arguments.xMax && req.arguments.yMin<req.arguments.yMax;
      if(spec.op==="clear-symbolic")return true;
      if(spec.op==="edit-graph")return typeof req.arguments.expression==="string" &&
        req.arguments.expression.length<=4096 && ["insert","clear","backspace"].includes(req.arguments.action) &&
        typeof req.arguments.token==="string" && req.arguments.token.length<=48 &&
        [req.arguments.selectionStart,req.arguments.selectionEnd].every(Number.isSafeInteger) &&
        req.arguments.selectionStart>=0 && req.arguments.selectionEnd>=req.arguments.selectionStart &&
        req.arguments.selectionEnd<=req.arguments.expression.length;
      return spec.keys.every(k=>text(req.arguments[k]));
    }
    const boundOutput=v=>{
      if(typeof v!=="string" || v.length>100000)throw Error("MCEL_CALC_EXPANDED:invalid-service-result");
      return v;
    };
    const serviceOptions=()=>fetcher ? {fetcher} : {};
    let mathicsPreparation = null;
    function prepareMathics() {
      if (!services || typeof services.prepareMathics !== "function")
        return Promise.reject(Error("MCEL_CALC_EXPANDED:symbolic-preparation-unavailable"));
      if (!mathicsPreparation) {
        mathicsPreparation = services.prepareMathics(serviceOptions()).catch(error => {
          mathicsPreparation = null; // A later visit can retry a failed startup.
          throw error;
        });
      }
      return mathicsPreparation;
    }
    async function invoke(req){
      if(!permitted(req))throw Error("MCEL_CALC_EXPANDED:operation-denied");
      const a=req.arguments;
      switch(req.operation){
        case "evaluate": {
          const out=core.evaluateCalculatorExpression(a.expression);
          if(!out || out.ok!==true)throw Error("MCEL_CALC_EXPANDED:"+(out?.error||"expression-invalid"));
          return boundOutput(out.displayValue!==undefined ? String(out.displayValue) : String(out.value));
        }
        case "graph": {
          const out=core.sampleCalculatorGraphExpression(a.expression,
            {xMin:a.xMin,xMax:a.xMax,yMin:a.yMin,yMax:a.yMax},160);
          if(!out || out.ok!==true)throw Error("MCEL_CALC_EXPANDED:graph-invalid");
          const visible=out.samples.filter(p=>p.visible && Number.isFinite(p.y));
          if(visible.length<2)throw Error("MCEL_CALC_EXPANDED:no-plottable-points");
          return visible.map((p,i)=>({id:String(i),x:p.x,y:p.y}));
        }
        case "clear-symbolic": return "Mathics ready.";
        case "edit-graph": {
          const caretBack=a.token.endsWith("()")?1:a.token.endsWith("(,)")?2:0;
          const out=a.action==="clear"?core.clearCalculatorGraphExpression():
            a.action==="backspace"?core.backspaceCalculatorGraphExpression(a.expression,a.selectionStart,a.selectionEnd):
            core.insertCalculatorGraphText(a.expression,a.token,a.selectionStart,a.selectionEnd,caretBack);
          return {expression:boundOutput(out.expression),selectionStart:out.selectionStart,selectionEnd:out.selectionEnd};
        }
        case "mathics": {
          if(!services || typeof services.evaluateMathics!=="function")throw Error("MCEL_CALC_EXPANDED:symbolic-backend-unavailable");
          await prepareMathics(); // Never start evaluation against a cold session.
          const out=await services.evaluateMathics(a.expression,serviceOptions());
          return boundOutput(out.output);
        }
        default:throw Error("MCEL_CALC_EXPANDED:unknown-operation");
      }
    }
    let lastCalculatorMode="basic";
    function setCalculatorMode(mode){
      if(["basic","graphing","symbolic"].includes(mode))lastCalculatorMode=mode;
    }
    function mountEmbeddedChat(host,node,read){
      if(!host || node?.targetKind!=="calculator-session" || node?.targetId!=="calculator" ||
         !global.MainComputerChatConsole?.mountEmbedded)
        throw Error("MCEL_CALC_EXPANDED:embedded-chat-controller-unavailable");
      const vm=global.MainComputerCalculatorViewModel;
      if(!vm?.buildCalculatorSessionContextSnapshot)
        throw Error("MCEL_CALC_EXPANDED:embedded-chat-context-unavailable");
      const getEmbeddedContext=()=>vm.buildCalculatorSessionContextSnapshot({
        activeMode:lastCalculatorMode==="graphing"?"scientific-graphing":lastCalculatorMode,
        arithmeticExpression:read("state:arithmetic-expression"),arithmeticResult:read("state:arithmetic-result"),
        graphExpression:read("state:graph-expression"),
        graphXMin:String(read("state:graph-x-min")),graphXMax:String(read("state:graph-x-max")),
        graphYMin:String(read("state:graph-y-min")),graphYMax:String(read("state:graph-y-max")),
        graphStatus:read("state:graph-points")?.length?"graph sampled":"",
        mathicsExpression:read("state:symbolic-expression"),
        mathicsResult:read("state:symbolic-result")
      });
      const controller=global.MainComputerChatConsole.mountEmbedded(host,{
        embedId:"calculator",activeApp:"calculator",idPrefix:"calculator-chat",
        classPrefix:"calculator",title:node.title,notebookId:"calculator-chat-notebook",
        statusId:"calculator-chat-status",threadTitle:"Calculator Chat",
        targetKind:node.targetKind,targetId:node.targetId,layout:"conversation",
        showThreadRail:false,showCurrentThreadBar:true,getEmbeddedContext,
        buildThreadMetadata(context){return {
          origin_app:"calculator",embedded_chat:true,
          linked_targets:[{app:"calculator",kind:"calculator-session",id:"calculator",path:"applications/calculator"}],
          calculator_active_mode:context?.active_mode||"basic"};}
      });
      if(!controller)throw Error("MCEL_CALC_EXPANDED:embedded-chat-mount-refused");
      // The shared Chat Console's own canonical layout is authoritative
      // inside this semantic slot. A missing/invalid engine fails closed.
      global.MainComputerMCELChatConsole?.mountCanonical?.(host,options.chatLayoutIr);
      return controller;
    }
    return Object.freeze({authorize:async req=>permitted(req),invoke,mountEmbeddedChat,prepareMathics,setCalculatorMode});
  }
  global.McelCalculatorExpandedPort=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
