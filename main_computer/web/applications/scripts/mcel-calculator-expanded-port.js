"use strict";
/* Explicit, least-authority bridge to the existing Calculator computational
 * core and optional same-origin service endpoints. No layout or DOM wiring.
 */
(function(global){
  const operations=Object.freeze({
    "intent:evaluate": {cap:"capability:calculator-core",op:"evaluate",method:"evaluateCalculatorExpression",keys:["expression"]},
    "intent:draw-graph": {cap:"capability:calculator-core",op:"graph",method:"sampleGraphExpression",keys:["expression","xMin","xMax","yMin","yMax"]},
    "intent:evaluate-symbolic": {cap:"capability:calculator-service",op:"mathics",method:"evaluateMathics",keys:["expression"]},
    "intent:ask-symbolic-model": {cap:"capability:calculator-service",op:"mathics-ask",method:"askMathicsModel",keys:["prompt"]},
    "intent:ask-arithmetic-model": {cap:"capability:calculator-service",op:"arithmetic-ask",method:"askArithmeticModel",keys:["prompt"]},
    "intent:ask-graph-model": {cap:"capability:calculator-service",op:"graph-ask",method:"askGraphModel",keys:["prompt"]},
    "intent:apply-arithmetic-suggestion": {cap:"capability:calculator-core",op:"evaluate",method:"evaluateCalculatorExpression",keys:["expression"]},
    "intent:apply-graph-suggestion": {cap:"capability:calculator-core",op:"graph",method:"sampleGraphExpression",keys:["expression","xMin","xMax","yMin","yMax"]},
    "intent:apply-symbolic-suggestion": {cap:"capability:calculator-core",op:"normalize-symbolic",method:"normalizeSymbolicSuggestion",keys:["expression"]},
    "intent:edit-graph-expression": {cap:"capability:calculator-core",op:"edit-graph",method:"editGraphExpression",keys:["expression","action","token","selectionStart","selectionEnd"]},
    "intent:clear-symbolic": {cap:"capability:calculator-core",op:"clear-symbolic",method:"clearSymbolic",keys:[]},
    "intent:calculator-chat": {cap:"capability:calculator-service",op:"chat",method:"calculatorEmbeddedChat",keys:["message"]},
    "intent:ask-result-question": {cap:"capability:calculator-service",op:"question",method:"askResultQuestion",keys:["question","arithmeticResult","symbolicResult","graphPoints","arithmeticExpression","graphExpression","xMin","xMax","yMin","yMax","symbolicExpression"]}
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
      if(spec.op==="question") {
        const a=req.arguments;
        return text(a.question) && [a.arithmeticResult,a.symbolicResult,a.arithmeticExpression,a.graphExpression,a.symbolicExpression].every(v=>
          typeof v==="string" && v.length<=4096) &&
          [a.xMin,a.xMax,a.yMin,a.yMax].every(Number.isFinite) &&
          a.xMin<a.xMax && a.yMin<a.yMax && Array.isArray(a.graphPoints) &&
          a.graphPoints.length<=513 && a.graphPoints.every(p=>p &&
            typeof p==="object" && !Array.isArray(p) &&
            Object.keys(p).sort().join(",")==="id,x,y" &&
            typeof p.id==="string" && p.id.length<=64 &&
            Number.isFinite(p.x) && Number.isFinite(p.y));
      }
      return spec.keys.every(k=>text(req.arguments[k]));
    }
    const boundOutput=v=>{
      if(typeof v!=="string" || v.length>100000)throw Error("MCEL_CALC_EXPANDED:invalid-service-result");
      return v;
    };
    const serviceOptions=()=>fetcher ? {fetcher} : {};
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
        case "normalize-symbolic": return boundOutput(a.expression);
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
          const out=await services.evaluateMathics(a.expression,serviceOptions());
          return boundOutput(out.output);
        }
        case "mathics-ask": {
          if(!services || typeof services.askMathicsModel!=="function")throw Error("MCEL_CALC_EXPANDED:symbolic-helper-unavailable");
          const out=await services.askMathicsModel(a.prompt,serviceOptions());
          return boundOutput(out.expression);
        }
        case "arithmetic-ask": {
          if(!services || typeof services.askArithmeticModel!=="function")throw Error("MCEL_CALC_EXPANDED:model-unavailable");
          const out=await services.askArithmeticModel(a.prompt,serviceOptions());
          return boundOutput(out.content);
        }
        case "graph-ask": {
          if(!services || typeof services.askGraphModel!=="function")throw Error("MCEL_CALC_EXPANDED:model-unavailable");
          const out=await services.askGraphModel(a.prompt,serviceOptions());
          return boundOutput(out.content);
        }
        case "question": {
          if(!services || typeof services.askResultQuestion!=="function")throw Error("MCEL_CALC_EXPANDED:qa-unavailable");
          // Share only verified, bounded computational evidence with the
          // optional explanation provider. No assistant computation is allowed
          // to overwrite the deterministic result states.
          const calculatorViewModel=global.MainComputerCalculatorViewModel;
          if(!calculatorViewModel?.buildCalculatorResultQaContext)
            throw Error("MCEL_CALC_EXPANDED:calculator-view-model-unavailable");
          const view=calculatorViewModel.buildCalculatorGraphRenderModel(a.graphExpression,
            {xMin:String(a.xMin),xMax:String(a.xMax),yMin:String(a.yMin),yMax:String(a.yMax)},
            {width:720,height:320});
          const context=calculatorViewModel.buildCalculatorResultQaContext({
            arithmeticExpression:a.arithmeticExpression,arithmeticResult:a.arithmeticResult,
            graphExpression:a.graphExpression,graphStatus:view.statusText,
            graphXMin:String(a.xMin),graphXMax:String(a.xMax),graphYMin:String(a.yMin),graphYMax:String(a.yMax),
            mathicsExpression:a.symbolicExpression,mathicsOutput:a.symbolicResult
          });
          const out=await services.askResultQuestion(a.question,JSON.stringify(context),serviceOptions());
          return boundOutput(out.answer);
        }
        default:throw Error("MCEL_CALC_EXPANDED:unknown-operation");
      }
    }
    function mountEmbeddedChat(host,node,read){
      if(!host || node?.targetKind!=="calculator-session" || node?.targetId!=="calculator" ||
         !global.MainComputerChatConsole?.mountEmbedded)
        throw Error("MCEL_CALC_EXPANDED:embedded-chat-controller-unavailable");
      const vm=global.MainComputerCalculatorViewModel;
      if(!vm?.buildCalculatorSessionContextSnapshot)
        throw Error("MCEL_CALC_EXPANDED:embedded-chat-context-unavailable");
      const getEmbeddedContext=()=>vm.buildCalculatorSessionContextSnapshot({
        activeMode:read("state:mode")==="graphing"?"scientific-graphing":"basic",
        arithmeticExpression:read("state:arithmetic-expression"),arithmeticResult:read("state:arithmetic-result"),
        graphExpression:read("state:graph-expression"),
        graphXMin:String(read("state:graph-x-min")),graphXMax:String(read("state:graph-x-max")),
        graphYMin:String(read("state:graph-y-min")),graphYMax:String(read("state:graph-y-max")),
        graphStatus:"",mathicsExpression:read("state:symbolic-expression"),
        qaStatus:read("state:qa-result")
      });
      const controller=global.MainComputerChatConsole.mountEmbedded(host,{
        embedId:"calculator",activeApp:"calculator",idPrefix:"calculator-chat",
        classPrefix:"calculator",title:node.title,notebookId:"calculator-chat-notebook",
        statusId:"calculator-chat-status",threadTitle:"Calculator Chat",
        targetKind:node.targetKind,targetId:node.targetId,layout:"compact",
        showThreadRail:false,showCurrentThreadBar:true,getEmbeddedContext,
        buildThreadMetadata(context){return {
          origin_app:"calculator",embedded_chat:true,
          linked_targets:[{app:"calculator",kind:"calculator-session",id:"calculator",path:"applications/calculator"}],
          calculator_active_mode:context?.active_mode||"basic"};}
      });
      if(!controller)throw Error("MCEL_CALC_EXPANDED:embedded-chat-mount-refused");
      return controller;
    }
    return Object.freeze({authorize:async req=>permitted(req),invoke,mountEmbeddedChat});
  }
  global.McelCalculatorExpandedPort=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
