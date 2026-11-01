"use strict";
// Four-notebook monitoring source; view membership is *semantic*
// mode state, not a DOM hierarchy, CSS rule, placement hint, or authorization.
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"semantic-task-monitor-generated",title:"Operational Process Monitor"},dsl=>{
  const text=dsl.field.text(), num=dsl.field.integer(), real=dsl.field.number();
  const processShape=dsl.field.record({processKey:text,pid:num,name:text,status:text,memory:text,
    createdAt:text,commandPreview:text,memoryRss:num,memoryAvailable:dsl.field.boolean(),
    statusAvailable:dsl.field.boolean(),isMainComputer:dsl.field.boolean(),isCurrentProcess:dsl.field.boolean()});
  const processRows=dsl.field.list(processShape,{key:"processKey"});
  const processes=dsl.state.derived("processes",processRows);
  const all=dsl.state.derived("all-processes",processRows);
  const selected=dsl.state.rendererLocal("selected",text,{initial:""});
  const view=dsl.state.rendererLocal("view",text.oneOf(["server-processes","all-processes","connections","cpu-gpu"]),{initial:"server-processes"});
  const connectionRows=dsl.field.list(dsl.field.record({key:text,pid:text,name:text,status:text,local:text,remote:text}),{key:"key"});
  const connections=dsl.state.derived("connections",connectionRows);
  const cpuCore=dsl.field.record({id:text,index:num,label:text,percent:real});
  const gpuDevice=dsl.field.record({id:text,index:num,name:text,utilization:real,
    utilizationAvailable:dsl.field.boolean(),memoryUsed:real,memoryUsedAvailable:dsl.field.boolean(),
    memoryTotal:real,memoryTotalAvailable:dsl.field.boolean(),temperature:real,temperatureAvailable:dsl.field.boolean()});
  const hardwareShape=dsl.field.record({summary:text,updatedAt:text,
    cpuAvailable:dsl.field.boolean(),cpuPercent:real,cpuPercentAvailable:dsl.field.boolean(),
    cpuCores:dsl.field.list(cpuCore,{key:"id"}),cpuRaw:text,
    gpuAvailable:dsl.field.boolean(),gpuPercent:real,gpuPercentAvailable:dsl.field.boolean(),
    gpuDevices:dsl.field.list(gpuDevice,{key:"id"}),gpuRaw:text});
  const hardware=dsl.state.derived("hardware",hardwareShape);
  const reader=dsl.capability.external("monitor",{risk:"external-read",description:"Read-only, bounded Task Manager overview"});
  reader.operation("observe","observeProcesses",{inputs:{query:text},result:processRows});
  reader.operation("all","observeAllProcesses",{inputs:{query:text},result:processRows});
  reader.operation("connections","observeConnections",{inputs:{query:text},result:connectionRows});
  reader.operation("hardware","observeHardware",{inputs:{query:text},result:hardwareShape});
  const refresh=dsl.intent.capabilityRequest("refresh",reader,{operation:"observe",inputs:{query:text},resultTo:processes});
  const refreshAll=dsl.intent.capabilityRequest("refresh-all",reader,{operation:"all",inputs:{query:text},resultTo:all});
  const refreshConnections=dsl.intent.capabilityRequest("refresh-connections",reader,{operation:"connections",inputs:{query:text},resultTo:connections});
  const refreshHardware=dsl.intent.capabilityRequest("refresh-hardware",reader,{operation:"hardware",inputs:{query:text},resultTo:hardware});
  const choose=dsl.intent.selectFromCollection("choose-process",processes,selected);
  const switchView=dsl.intent.selectView("switch-view",view);
  const inView=equals=>({state:view,equals});
  const surface=dsl.surface.define("monitor",{purpose:"monitor",root:dsl.surface.region("semantic-source",{children:[
    dsl.surface.action("select-notebook",{intent:switchView}),
    dsl.surface.form("monitor-query",{fields:{query:text},defaults:{query:""},intent:refresh,when:inView("server-processes")}),
    dsl.surface.form("all-query",{fields:{query:text},defaults:{query:""},intent:refreshAll,when:inView("all-processes")}),
    dsl.surface.form("connection-query",{fields:{query:text},defaults:{query:""},intent:refreshConnections,when:inView("connections")}),
    dsl.surface.form("hardware-query",{fields:{query:text},defaults:{query:""},intent:refreshHardware,when:inView("cpu-gpu")}),
    dsl.surface.collection("process-list",{key:"processKey",fields:["name","pid","status","memory"],state:processes,select:choose,when:inView("server-processes")}),
    dsl.surface.collection("all-process-list",{key:"processKey",fields:["name","pid","status","memory"],state:all,when:inView("all-processes")}),
    dsl.surface.collection("connection-list",{key:"key",fields:["pid","name","status","local","remote"],state:connections,when:inView("connections")}),
    dsl.surface.text("hardware-overview",{value:hardware,when:inView("cpu-gpu")}),
    dsl.surface.text("selection-echo",{value:selected,when:inView("server-processes")}),
    dsl.surface.receipt("activity")
  ]})});
  return {states:[processes,all,selected,view,connections,hardware],capabilities:[reader],
    intents:[refresh,refreshAll,refreshConnections,refreshHardware,choose,switchView],surfaces:[surface]};
});
