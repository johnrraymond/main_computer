"use strict";
// One official MCEL DSL, two authored files, no layout or host DOM wiring.
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"semantic-calculator-expanded",title:"Semantic Calculator"}, dsl=>{
  const text=dsl.field.text(), number=dsl.field.number();
  const point=dsl.field.record({id:dsl.field.text(),x:number,y:number});
  const points=dsl.field.list(point,{key:"id"});
  const mode=dsl.state.rendererLocal("mode",text.oneOf(["basic","graphing","symbolic","help"]),{initial:"basic"});
  const arithmeticResult=dsl.state.derived("arithmetic-result",text,{initial:""});
  const graphPoints=dsl.state.derived("graph-points",points,{initial:[]});
  const symbolicResult=dsl.state.derived("symbolic-result",text,{initial:""});
  const helperResult=dsl.state.derived("helper-result",text,{initial:""});
  const qaResult=dsl.state.derived("qa-result",text,{initial:""});
  const core=dsl.capability.external("calculator-core",{risk:"external-read",description:"Pure existing Calculator parser and graph evaluator"});
  core.operation("evaluate","evaluateCalculatorExpression",{inputs:{expression:text},result:text});
  core.operation("graph","sampleGraphExpression",{inputs:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},result:points});
  const service=dsl.capability.external("calculator-service",{risk:"external-read",description:"Existing optional model and symbolic calculator endpoints"});
  service.operation("mathics","evaluateMathics",{inputs:{expression:text},result:text});
  service.operation("mathics-ask","askMathicsModel",{inputs:{prompt:text},result:text});
  service.operation("arithmetic-ask","askArithmeticModel",{inputs:{prompt:text},result:text});
  service.operation("graph-ask","askGraphModel",{inputs:{prompt:text},result:text});
  // Q&A draws on typed, already-evaluated evidence, never invented model math.
  service.operation("question","askResultQuestion",{inputs:{question:text,arithmeticResult:text,symbolicResult:text,graphPoints:points},result:text});
  const evaluate=dsl.intent.capabilityRequest("evaluate",core,{operation:"evaluate",inputs:{expression:text},resultTo:arithmeticResult});
  const graph=dsl.intent.capabilityRequest("draw-graph",core,{operation:"graph",inputs:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},resultTo:graphPoints});
  const mathics=dsl.intent.capabilityRequest("evaluate-symbolic",service,{operation:"mathics",inputs:{expression:text},resultTo:symbolicResult});
  const askMathics=dsl.intent.capabilityRequest("ask-symbolic-model",service,{operation:"mathics-ask",inputs:{prompt:text},resultTo:helperResult});
  const askArithmetic=dsl.intent.capabilityRequest("ask-arithmetic-model",service,{operation:"arithmetic-ask",inputs:{prompt:text},resultTo:helperResult});
  const askGraph=dsl.intent.capabilityRequest("ask-graph-model",service,{operation:"graph-ask",inputs:{prompt:text},resultTo:helperResult});
  const question=dsl.intent.capabilityRequest("ask-result-question",service,{operation:"question",inputs:{question:text},arguments:{arithmeticResult:arithmeticResult,symbolicResult:symbolicResult,graphPoints:graphPoints},resultTo:qaResult});
  const switchMode=dsl.intent.selectView("switch-mode",mode);
  const surface=dsl.surface.bindHtml("application",{bindings:{
    mode:[dsl.surface.action("choose-mode",{intent:switchMode})],
    arithmetic:[dsl.surface.form("arithmetic-expression",{fields:{expression:text},intent:evaluate})],
    answer:[dsl.surface.text("calculated-value",{value:arithmeticResult})],
    "graph-input":[dsl.surface.form("graph-expression",{fields:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},defaults:{xMin:-10,xMax:10,yMin:-10,yMax:10},intent:graph})],
    "graph-output":[dsl.surface.plot("graph-plot",{state:graphPoints,x:"x",y:"y"})],
    "mathics-input":[dsl.surface.form("symbolic-expression",{fields:{expression:text},intent:mathics})],
    "mathics-output":[dsl.surface.text("symbolic-value",{value:symbolicResult})],
    helpers:[dsl.surface.form("ask-arithmetic",{fields:{prompt:text},intent:askArithmetic}),dsl.surface.form("ask-graph",{fields:{prompt:text},intent:askGraph}),dsl.surface.form("ask-symbolic",{fields:{prompt:text},intent:askMathics})],
    "helper-output":[dsl.surface.text("helper-result",{value:helperResult})],
    qa:[dsl.surface.form("question",{fields:{question:text},intent:question})],
    "qa-output":[dsl.surface.text("answer",{value:qaResult})],
    status:[dsl.surface.receipt("receipts")]
  }});
  return {states:[mode,arithmeticResult,graphPoints,symbolicResult,helperResult,qaResult],capabilities:[core,service],
    intents:[evaluate,graph,mathics,askMathics,askArithmetic,askGraph,question,switchMode],surfaces:[surface]};
});
