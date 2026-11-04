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
    "intent:ask-result-question": {cap:"capability:calculator-service",op:"question",method:"askResultQuestion",keys:["question","arithmeticResult","symbolicResult","graphPoints"]}
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
      if(spec.op==="question") {
        const a=req.arguments;
        return text(a.question) && [a.arithmeticResult,a.symbolicResult].every(v=>
          typeof v==="string" && v.length<=4096) && Array.isArray(a.graphPoints) &&
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
          const context={};
          if(a.arithmeticResult)context.arithmetic_result=a.arithmeticResult;
          if(a.symbolicResult)context.symbolic_result=a.symbolicResult;
          if(a.graphPoints.length){
            const xs=a.graphPoints.map(p=>p.x),ys=a.graphPoints.map(p=>p.y);
            context.graph={sample_count:a.graphPoints.length,
              x_min:Math.min(...xs),x_max:Math.max(...xs),
              visible_y_min:Math.min(...ys),visible_y_max:Math.max(...ys)};
          }
          const out=await services.askResultQuestion(a.question,Object.keys(context).length?
            JSON.stringify(context):"",serviceOptions());
          return boundOutput(out.answer);
        }
        default:throw Error("MCEL_CALC_EXPANDED:unknown-operation");
      }
    }
    return Object.freeze({authorize:async req=>permitted(req),invoke});
  }
  global.McelCalculatorExpandedPort=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
