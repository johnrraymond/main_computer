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
  const graphEdited=dsl.state.derived("graph-edited",graphEditValue,{initial:{expression:"",selectionStart:0,selectionEnd:0}});
  const arithmeticExpression=dsl.state.rendererLocal("arithmetic-expression",text,{initial:""});
  const graphExpression=dsl.state.rendererLocal("graph-expression",text,{initial:""});
  const xMin=dsl.state.rendererLocal("graph-x-min",number,{initial:-10});
  const xMax=dsl.state.rendererLocal("graph-x-max",number,{initial:10});
  const yMin=dsl.state.rendererLocal("graph-y-min",number,{initial:-5});
  const yMax=dsl.state.rendererLocal("graph-y-max",number,{initial:5});
  const symbolicExpression=dsl.state.rendererLocal("symbolic-expression",text,{initial:""});
  const chatReceipt=dsl.state.derived("chat-receipt",text,{initial:""});
  const core=dsl.capability.external("calculator-core",{risk:"external-read",description:"Pure existing Calculator parser and graph evaluator"});
  core.operation("evaluate","evaluateCalculatorExpression",{inputs:{expression:text},result:text});
  core.operation("graph","sampleGraphExpression",{inputs:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},result:points});
  core.operation("edit-graph","editGraphExpression",{inputs:{expression:text,action:text,token:text,selectionStart:number,selectionEnd:number},result:graphEditValue});
  core.operation("clear-symbolic","clearSymbolic",{inputs:{},result:text});
  const service=dsl.capability.external("calculator-service",{risk:"external-read",description:"Existing optional model and symbolic calculator endpoints"});
  service.operation("chat","calculatorEmbeddedChat",{inputs:{message:text},result:text});
  service.operation("mathics","evaluateMathics",{inputs:{expression:text},result:text});
  const evaluate=dsl.intent.capabilityRequest("evaluate",core,{operation:"evaluate",inputs:{expression:text},resultTo:arithmeticResult});
  const graph=dsl.intent.capabilityRequest("draw-graph",core,{operation:"graph",inputs:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},resultTo:graphPoints});
  const mathics=dsl.intent.capabilityRequest("evaluate-symbolic",service,{operation:"mathics",inputs:{expression:text},resultTo:symbolicResult});
  const clearSymbolic=dsl.intent.capabilityRequest("clear-symbolic",core,{operation:"clear-symbolic",resultTo:symbolicResult});
  const graphEditor=dsl.intent.capabilityRequest("edit-graph-expression",core,{operation:"edit-graph",inputs:{expression:text,action:text,token:text,selectionStart:number,selectionEnd:number},resultTo:graphEdited});
  const chat=dsl.intent.capabilityRequest("calculator-chat",service,{operation:"chat",inputs:{message:text},resultTo:chatReceipt});
  const switchMode=dsl.intent.selectView("switch-mode",mode);
  // Source declares meaning only. The generic MCEL renderer owns descendants'
  // interaction states, and Theme Law alone chooses physical colors.
  const control={role:"action.evaluate",surface:"control",context:"form",consequence:"routine"};
  const navigation={role:"navigation.item",surface:"navigation",context:"navigation"};
  const value={role:"text.primary",surface:"text",context:"nested"};
  const graphSurface={role:"surface.panel",surface:"panel",context:"nested"};
  const surface=dsl.surface.bindHtml("application",{bindings:{
    mode:[dsl.surface.action("choose-mode",{intent:switchMode,color:navigation})],
    arithmetic:[dsl.surface.form("arithmetic-expression",{fields:{expression:text},intent:evaluate,fieldState:{expression:arithmeticExpression},color:control})],
    answer:[dsl.surface.text("calculated-value",{value:arithmeticResult,color:value})],
    "graph-input":[dsl.surface.form("graph-expression",{fields:{expression:text,xMin:number,xMax:number,yMin:number,yMax:number},defaults:{xMin:-10,xMax:10,yMin:-5,yMax:5},intent:graph,
      fieldState:{expression:graphExpression,xMin,xMax,yMin,yMax},acceptResult:{edited:{state:graphEdited,field:"expression",pick:"expression"}},
      editor:{intent:graphEditor,templates:["^","x",",","pi","e","sin()","cos()","tan()","asin()","acos()","atan()","sqrt()","abs()","exp()","log()","ln()","min(,)","max(,)","floor()","ceil()","round()"]},resetAndSubmit:true,color:control})],
    "graph-output":[dsl.surface.plot("graph-plot",{state:graphPoints,x:"x",y:"y",color:graphSurface})],
    "mathics-input":[dsl.surface.form("symbolic-expression",{fields:{expression:text},intent:mathics,fieldState:{expression:symbolicExpression},clearIntent:clearSymbolic,color:control})],
    "mathics-output":[dsl.surface.text("symbolic-value",{value:symbolicResult,color:value})],
    // Context is declarative, read-only and separate from the one AI terminus.
    // These are calculator-owned values, never assistant-generated results.
    "calculator-context":[
      dsl.surface.text("basic-expression-context",{label:"Basic expression",value:arithmeticExpression,color:value}),
      dsl.surface.text("basic-result-context",{label:"Basic result",value:arithmeticResult,color:value}),
      dsl.surface.text("graph-expression-context",{label:"Graph expression",value:graphExpression,color:value}),
      dsl.surface.text("symbolic-expression-context",{label:"Symbolic expression",value:symbolicExpression,color:value}),
      dsl.surface.text("symbolic-result-context",{label:"Symbolic result",value:symbolicResult,color:value})],
    "helper-chat":[dsl.surface.embeddedChat("calculator-chat",{intent:chat,title:"Calculator Help",targetKind:"calculator-session",targetId:"calculator",color:graphSurface})],
    status:[dsl.surface.receipt("receipts",{color:graphSurface})]
  }});
  return {states:[mode,arithmeticResult,graphPoints,symbolicResult,chatReceipt,graphEdited,
      arithmeticExpression,graphExpression,xMin,xMax,yMin,yMax,symbolicExpression],capabilities:[core,service],
    intents:[evaluate,graph,mathics,switchMode,clearSymbolic,graphEditor,chat],surfaces:[surface]};
});
