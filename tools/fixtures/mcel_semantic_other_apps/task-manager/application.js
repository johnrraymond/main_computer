"use strict";
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"semantic-task-manager",title:"Semantic Task Manager"},dsl=>{
  const text=dsl.field.text(),num=dsl.field.integer();
  const rows=dsl.field.list(dsl.field.record({processKey:text,pid:num,name:text,status:text,memory:text,
    createdAt:text,commandPreview:text,memoryRss:num,memoryAvailable:dsl.field.boolean(),
    statusAvailable:dsl.field.boolean(),isMainComputer:dsl.field.boolean(),isCurrentProcess:dsl.field.boolean()}),{key:"processKey"});
  const processes=dsl.state.derived("processes",rows);
  const selected=dsl.state.rendererLocal("selected",text,{initial:""});
  const view=dsl.state.rendererLocal("view",text.oneOf(["server-processes","all-processes","connections","cpu-gpu"]),{initial:"server-processes"});
  const all=dsl.state.derived("all-processes",rows);
  const connectionRows=dsl.field.list(dsl.field.record({key:text,pid:text,name:text,status:text,local:text,remote:text}),{key:"key"});
  const connections=dsl.state.derived("connections",connectionRows);
  const cpuCore=dsl.field.record({id:text,index:num,label:text,percent:dsl.field.number()});
  const gpuDevice=dsl.field.record({id:text,index:num,name:text,utilization:dsl.field.number(),
    utilizationAvailable:dsl.field.boolean(),memoryUsed:dsl.field.number(),memoryUsedAvailable:dsl.field.boolean(),
    memoryTotal:dsl.field.number(),memoryTotalAvailable:dsl.field.boolean(),temperature:dsl.field.number(),temperatureAvailable:dsl.field.boolean()});
  const hardwareShape=dsl.field.record({summary:text,updatedAt:text,
    cpuAvailable:dsl.field.boolean(),cpuPercent:dsl.field.number(),cpuPercentAvailable:dsl.field.boolean(),
    cpuCores:dsl.field.list(cpuCore,{key:"id"}),cpuRaw:text,
    gpuAvailable:dsl.field.boolean(),gpuPercent:dsl.field.number(),gpuPercentAvailable:dsl.field.boolean(),
    gpuDevices:dsl.field.list(gpuDevice,{key:"id"}),gpuRaw:text});
  const hardware=dsl.state.derived("hardware",hardwareShape);
  const scheduleRows=dsl.field.list(dsl.field.record({id:text,action:text,runAt:text,note:text}),{key:"id"});
  const schedules=dsl.state.derived("schedules",scheduleRows);
  const aiResult=dsl.state.derived("ai-result",text);
  const operationResult=dsl.state.derived("operation-result",text);
  const reader=dsl.capability.external("monitor",{risk:"external-read",description:"Existing Task Manager overview endpoint, read only"});
  reader.operation("observe","observeProcesses",{inputs:{query:text},result:rows});
  reader.operation("all","observeAllProcesses",{inputs:{query:text},result:rows});
  reader.operation("connections","observeConnections",{inputs:{query:text},result:connectionRows});
  reader.operation("hardware","observeHardware",{inputs:{query:text},result:hardwareShape});
  reader.operation("schedules","listSchedules",{result:scheduleRows});
  reader.operation("ai","getAiBrief",{inputs:{instruction:text,query:text,view:text.oneOf(["server-processes","all-processes","connections","cpu-gpu"])},result:text});
  const control=dsl.capability.external("task-control",{risk:"consequential",description:"Confirmed, generation-checked process and server lifecycle actions"});
  control.operation("terminate","terminateObservedProcess",{inputs:{selected:text},result:text,risk:"consequential"});
  control.operation("kill","killObservedProcess",{inputs:{selected:text},result:text,risk:"consequential"});
  control.operation("start","startServer",{result:text,risk:"consequential"});
  control.operation("restart","restartServer",{result:text,risk:"consequential"});
  control.operation("shutdown","shutdownServer",{result:text,risk:"consequential"});
  const planner=dsl.capability.external("task-scheduling",{risk:"consequential",description:"Confirmed server lifecycle schedule creation, execution and deletion"});
  planner.operation("create","createSchedule",{inputs:{action:text,runAt:text,note:text},result:text,risk:"consequential"});
  planner.operation("delete","deleteSchedule",{inputs:{scheduleId:text},result:text,risk:"consequential"});
  planner.operation("run","runScheduledAction",{inputs:{scheduleId:text},result:text,risk:"consequential"});
  const refresh=dsl.intent.capabilityRequest("refresh",reader,{operation:"observe",inputs:{query:text},resultTo:processes});
  const switchView=dsl.intent.selectView("switch-view",view);
  const refreshAll=dsl.intent.capabilityRequest("refresh-all",reader,{operation:"all",inputs:{query:text},resultTo:all});
  const refreshConnections=dsl.intent.capabilityRequest("refresh-connections",reader,{operation:"connections",inputs:{query:text},resultTo:connections});
  const refreshHardware=dsl.intent.capabilityRequest("refresh-hardware",reader,{operation:"hardware",inputs:{query:text},resultTo:hardware});
  const listSchedules=dsl.intent.capabilityRequest("list-schedules",reader,{operation:"schedules",resultTo:schedules});
  const askAi=dsl.intent.capabilityRequest("task-ai",reader,{operation:"ai",inputs:{instruction:text,query:text},arguments:{view},resultTo:aiResult});
  const terminate=dsl.intent.capabilityRequest("terminate-process",control,{operation:"terminate",arguments:{selected},resultTo:operationResult});
  const kill=dsl.intent.capabilityRequest("kill-process",control,{operation:"kill",arguments:{selected},resultTo:operationResult});
  const startServer=dsl.intent.capabilityRequest("start-server",control,{operation:"start",resultTo:operationResult});
  const restartServer=dsl.intent.capabilityRequest("restart-server",control,{operation:"restart",resultTo:operationResult});
  const shutdownServer=dsl.intent.capabilityRequest("shutdown-server",control,{operation:"shutdown",resultTo:operationResult});
  const createSchedule=dsl.intent.capabilityRequest("create-schedule",planner,{operation:"create",inputs:{action:text,runAt:text,note:text},resultTo:operationResult});
  const deleteSchedule=dsl.intent.capabilityRequest("delete-schedule",planner,{operation:"delete",inputs:{scheduleId:text},resultTo:operationResult});
  const runSchedule=dsl.intent.capabilityRequest("run-schedule",planner,{operation:"run",inputs:{scheduleId:text},resultTo:operationResult});
  const choose=dsl.intent.selectFromCollection("choose-process",processes,selected);
  const surface=dsl.surface.bindHtml("application",{bindings:{
    query:[dsl.surface.action("select-notebook",{intent:switchView}),dsl.surface.form("monitor-query",{fields:{query:text},defaults:{query:""},intent:refresh})],
    processes:[dsl.surface.collection("process-list",{key:"processKey",fields:["name","pid","status","memory"],state:processes,select:choose}),dsl.surface.action("terminate-process",{intent:terminate}),dsl.surface.action("kill-process",{intent:kill})],
    all:[dsl.surface.form("all-query",{fields:{query:text},defaults:{query:""},intent:refreshAll}),dsl.surface.collection("all-process-list",{key:"processKey",fields:["name","pid","status","memory"],state:all})],
    connections:[dsl.surface.form("connection-query",{fields:{query:text},defaults:{query:""},intent:refreshConnections}),dsl.surface.collection("connection-list",{key:"key",fields:["pid","name","status","local","remote"],state:connections})],
    hardware:[dsl.surface.form("hardware-query",{fields:{query:text},defaults:{query:""},intent:refreshHardware}),dsl.surface.text("hardware-overview",{value:hardware})],
    schedules:[dsl.surface.action("list-schedules",{intent:listSchedules}),
       dsl.surface.collection("schedule-list",{key:"id",fields:["action","runAt","note"],state:schedules}),
       dsl.surface.form("create-schedule-form",{fields:{action:text,runAt:text,note:text},intent:createSchedule}),
       dsl.surface.form("delete-schedule-form",{fields:{scheduleId:text},intent:deleteSchedule}),
       dsl.surface.form("run-schedule-form",{fields:{scheduleId:text},intent:runSchedule})],
    assistant:[dsl.surface.form("ai-question",{fields:{instruction:text,query:text},defaults:{query:""},intent:askAi}),dsl.surface.text("ai-brief",{value:aiResult})],
    operations:[dsl.surface.action("start-server",{intent:startServer}),dsl.surface.action("restart-server",{intent:restartServer}),
      dsl.surface.action("shutdown-server",{intent:shutdownServer}),dsl.surface.text("operation-receipt",{value:operationResult})],
    selected:[dsl.surface.text("selected-process",{value:selected})],
    status:[dsl.surface.receipt("activity")]
  }});
  return {states:[processes,selected,view,all,connections,hardware,schedules,aiResult,operationResult],
    capabilities:[reader,control,planner],intents:[refresh,choose,switchView,refreshAll,refreshConnections,refreshHardware,
      listSchedules,askAi,terminate,kill,startServer,restartServer,shutdownServer,createSchedule,deleteSchedule,runSchedule],surfaces:[surface]};
});
