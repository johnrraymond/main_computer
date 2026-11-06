"use strict";
// One official MCEL DSL, two authored files, no layout or host DOM wiring.
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"semantic-calculator-expanded",title:"Semantic Calculator"}, dsl=>{
  const text=dsl.field.text(), number=dsl.field.number();
  const point=dsl.field.record({id:dsl.field.text(),x:number,y:number});
  const points=dsl.field.list(point,{key:"id"});
  const graphEditValue=dsl.field.record({expression:text,selectionStart:number,selectionEnd:number});
  const mode=dsl.state.rendererLocal("mode",text.oneOf(["basic","graphing","symbolic","help"]),{initial:"basic"});
  const arithmeticResult=dsl.state.derived("arithmetic-result",text,{initial:""});
  const graphPoints=dsl.state.derived("graph-points",points,{initial:[]});
  const symbolicResult=dsl.state.derived("symbolic-result",text,{initial:""});
  const helperResult=dsl.state.derived("helper-result",text,{initial:""});
  const arithmeticSuggestion=dsl.state.derived("arithmetic-suggestion",text,{initial:""});
  const graphSuggestion=dsl.state.derived("graph-suggestion",text,{initial:""});
  const symbolicSuggestion=dsl.state.derived("symbolic-suggestion",text,{initial:""});
  const symbolicApplied=dsl.state.derived("symbolic-applied",text,{initial:""});
  const graphEdited=dsl.state.derived("graph-edited",graphEditValue,{initial:{expression:"",selectionStart:0,selectionEnd:0}});
  const arithmeticExpression=dsl.state.rendererLocal("arithmetic-expression",text,{initial:""});
  const graphExpression=dsl.state.rendererLocal("graph-expression",text,{initial:""});
  const xMin=dsl.state.rendererLocal("graph-x-min",number,{initial:-10});
  const xMax=dsl.state.rendererLocal("graph-x-max",number,{initial:10});
  const yMin=dsl.state.rendererLocal("graph-y-min",number,{initial:-5});
  const yMax=dsl.state.rendererLocal("graph-y-max",number,{initial:5});
  const symbolicExpression=dsl.state.rendererLocal("symbolic-expression",text,{initial:""});
  const qaResult=dsl.state.derived("qa-result",text,{initial:""});
  const chatReceipt=dsl.state.derived("chat-receipt",text,{initial:""});
  const core=dsl.capability.external("calculator-core",{risk:"external-read",description:"Pure existing Calculator parser and graph evaluator"});
  core.operation("evaluate","evaluateCalculatorExpression",{inputs:{expression:text},result:text});
  core.operation("graph","sampleGraphExpression",{inputs:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},result:points});
  core.operation("normalize-symbolic","normalizeSymbolicSuggestion",{inputs:{expression:text},result:text});
  core.operation("edit-graph","editGraphExpression",{inputs:{expression:text,action:text,token:text,selectionStart:number,selectionEnd:number},result:graphEditValue});
  core.operation("clear-symbolic","clearSymbolic",{inputs:{},result:text});
  const service=dsl.capability.external("calculator-service",{risk:"external-read",description:"Existing optional model and symbolic calculator endpoints"});
  service.operation("chat","calculatorEmbeddedChat",{inputs:{message:text},result:text});
  service.operation("mathics","evaluateMathics",{inputs:{expression:text},result:text});
  service.operation("mathics-ask","askMathicsModel",{inputs:{prompt:text},result:text});
  service.operation("arithmetic-ask","askArithmeticModel",{inputs:{prompt:text},result:text});
  service.operation("graph-ask","askGraphModel",{inputs:{prompt:text},result:text});
  // Q&A draws on typed, already-evaluated evidence, never invented model math.
  service.operation("question","askResultQuestion",{inputs:{question:text,arithmeticResult:text,symbolicResult:text,graphPoints:points,
    arithmeticExpression:text,graphExpression:text,xMin:number,xMax:number,yMin:number,yMax:number,symbolicExpression:text},result:text});
  const evaluate=dsl.intent.capabilityRequest("evaluate",core,{operation:"evaluate",inputs:{expression:text},resultTo:arithmeticResult});
  const graph=dsl.intent.capabilityRequest("draw-graph",core,{operation:"graph",inputs:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},resultTo:graphPoints});
  const mathics=dsl.intent.capabilityRequest("evaluate-symbolic",service,{operation:"mathics",inputs:{expression:text},resultTo:symbolicResult});
  const clearSymbolic=dsl.intent.capabilityRequest("clear-symbolic",core,{operation:"clear-symbolic",resultTo:symbolicResult});
  const graphEditor=dsl.intent.capabilityRequest("edit-graph-expression",core,{operation:"edit-graph",inputs:{expression:text,action:text,token:text,selectionStart:number,selectionEnd:number},resultTo:graphEdited});
  const applyArithmetic=dsl.intent.capabilityRequest("apply-arithmetic-suggestion",core,{operation:"evaluate",arguments:{expression:arithmeticSuggestion},resultTo:arithmeticResult});
  const applyGraph=dsl.intent.capabilityRequest("apply-graph-suggestion",core,{operation:"graph",arguments:{expression:graphSuggestion,xMin,xMax,yMin,yMax},resultTo:graphPoints});
  const applySymbolic=dsl.intent.capabilityRequest("apply-symbolic-suggestion",core,{operation:"normalize-symbolic",arguments:{expression:symbolicSuggestion},resultTo:symbolicApplied});
  const askMathics=dsl.intent.capabilityRequest("ask-symbolic-model",service,{operation:"mathics-ask",inputs:{prompt:text},resultTo:symbolicSuggestion});
  const askArithmetic=dsl.intent.capabilityRequest("ask-arithmetic-model",service,{operation:"arithmetic-ask",inputs:{prompt:text},resultTo:arithmeticSuggestion});
  const askGraph=dsl.intent.capabilityRequest("ask-graph-model",service,{operation:"graph-ask",inputs:{prompt:text},resultTo:graphSuggestion});
  const question=dsl.intent.capabilityRequest("ask-result-question",service,{operation:"question",inputs:{question:text},arguments:{arithmeticResult,symbolicResult,graphPoints,arithmeticExpression,graphExpression,xMin,xMax,yMin,yMax,symbolicExpression},resultTo:qaResult});
  const chat=dsl.intent.capabilityRequest("calculator-chat",service,{operation:"chat",inputs:{message:text},resultTo:chatReceipt});
  const switchMode=dsl.intent.selectView("switch-mode",mode);
  const surface=dsl.surface.bindHtml("application",{bindings:{
    mode:[dsl.surface.action("choose-mode",{intent:switchMode,resetOutputs:[{state:arithmeticResult,value:"ready"}]})],
    arithmetic:[dsl.surface.form("arithmetic-expression",{fields:{expression:text},intent:evaluate,fieldState:{expression:arithmeticExpression},acceptResult:{expression:{state:arithmeticSuggestion,invoke:applyArithmetic},computed:{state:arithmeticResult,field:"expression",whenIntent:applyArithmetic}}})],
    answer:[dsl.surface.text("calculated-value",{value:arithmeticResult})],
    "graph-input":[dsl.surface.form("graph-expression",{fields:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},defaults:{xMin:-10,xMax:10,yMin:-5,yMax:5},intent:graph,
      fieldState:{expression:graphExpression,xMin,xMax,yMin,yMax},acceptResult:{expression:{state:graphSuggestion,invoke:applyGraph},edited:{state:graphEdited,field:"expression",pick:"expression"}},
      editor:{intent:graphEditor,templates:["^","x",",","pi","e","sin()","cos()","tan()","asin()","acos()","atan()","sqrt()","abs()","exp()","log()","ln()","min(,)","max(,)","floor()","ceil()","round()"]},resetAndSubmit:true})],
    "graph-output":[dsl.surface.plot("graph-plot",{state:graphPoints,x:"x",y:"y"})],
    "mathics-input":[dsl.surface.form("symbolic-expression",{fields:{expression:text},intent:mathics,fieldState:{expression:symbolicExpression},acceptResult:{expression:{state:symbolicSuggestion,invoke:applySymbolic},applied:{state:symbolicApplied,field:"expression"}},clearIntent:clearSymbolic})],
    "mathics-output":[dsl.surface.text("symbolic-value",{value:symbolicResult})],
    helpers:[dsl.surface.form("ask-arithmetic",{fields:{prompt:text},intent:askArithmetic}),dsl.surface.form("ask-graph",{fields:{prompt:text},intent:askGraph}),dsl.surface.form("ask-symbolic",{fields:{prompt:text},intent:askMathics}),
      dsl.surface.embeddedChat("calculator-chat",{intent:chat,title:"Calculator Chat",targetKind:"calculator-session",targetId:"calculator"})],
    "helper-output":[dsl.surface.text("arithmetic-suggestion",{value:arithmeticSuggestion}),dsl.surface.text("graph-suggestion",{value:graphSuggestion}),dsl.surface.text("symbolic-suggestion",{value:symbolicSuggestion})],
    qa:[dsl.surface.form("question",{fields:{question:text},intent:question})],
    "qa-output":[dsl.surface.text("answer",{value:qaResult})],
    status:[dsl.surface.receipt("receipts")]
  }});
  return {states:[mode,arithmeticResult,graphPoints,symbolicResult,helperResult,qaResult,chatReceipt,
      arithmeticSuggestion,graphSuggestion,symbolicSuggestion,symbolicApplied,graphEdited,
      arithmeticExpression,graphExpression,xMin,xMax,yMin,yMax,symbolicExpression],capabilities:[core,service],
    intents:[evaluate,graph,mathics,askMathics,askArithmetic,askGraph,question,switchMode,
      clearSymbolic,graphEditor,applyArithmetic,applyGraph,applySymbolic,chat],surfaces:[surface]};
});
