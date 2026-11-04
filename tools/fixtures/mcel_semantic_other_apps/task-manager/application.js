"use strict";
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"semantic-task-manager",title:"Semantic Task Manager"},dsl=>{
  const text=dsl.field.text(),num=dsl.field.integer();
  const rows=dsl.field.list(dsl.field.record({processKey:text,pid:num,name:text,status:text,memory:text,
    createdAt:text,commandPreview:text,memoryRss:num,memoryAvailable:dsl.field.boolean(),
    statusAvailable:dsl.field.boolean(),isMainComputer:dsl.field.boolean(),isCurrentProcess:dsl.field.boolean()}),{key:"processKey"});
  const processes=dsl.state.derived("processes",rows);
  const selected=dsl.state.rendererLocal("selected",text,{initial:""});
  const selectedSchedule=dsl.state.rendererLocal("selected-schedule",text,{initial:""});
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
  const scheduleRows=dsl.field.list(dsl.field.record({id:text,action:text,runAt:text,note:text,revision:text}),{key:"id"});
  const schedules=dsl.state.derived("schedules",scheduleRows);
  const aiResult=dsl.state.derived("ai-result",text);
  const serverResult=dsl.state.derived("server-result",text);
  const operationReceipt=dsl.field.record({outcome:text,message:text,requestId:text});
  const operationResult=dsl.state.derived("operation-result",operationReceipt);
  const reader=dsl.capability.external("monitor",{risk:"external-read",description:"Existing Task Manager overview endpoint, read only"});
  reader.operation("observe","observeProcesses",{inputs:{query:text,limit:text,includeConnections:text},result:rows});
  reader.operation("all","observeAllProcesses",{inputs:{query:text,limit:text,includeConnections:text},result:rows});
  reader.operation("connections","observeConnections",{inputs:{query:text,limit:text,includeConnections:text},result:connectionRows});
  reader.operation("hardware","observeHardware",{inputs:{query:text,limit:text,includeConnections:text},result:hardwareShape});
  reader.operation("schedules","listSchedules",{result:scheduleRows});
  reader.operation("server-status","readServerStatus",{result:text});
  reader.operation("ai","getAiBrief",{inputs:{instruction:text,query:text,view:text.oneOf(["server-processes","all-processes","connections","cpu-gpu"])},result:text});
  const control=dsl.capability.external("task-control",{risk:"consequential",description:"Confirmed, generation-checked process and server lifecycle actions"});
  control.operation("terminate","terminateObservedProcess",{inputs:{selected:text},result:operationReceipt,risk:"consequential"});
  control.operation("kill","killObservedProcess",{inputs:{selected:text},result:operationReceipt,risk:"consequential"});
  control.operation("start","startServer",{result:operationReceipt,risk:"consequential"});
  control.operation("restart","restartServer",{result:operationReceipt,risk:"consequential"});
  control.operation("shutdown","shutdownServer",{result:operationReceipt,risk:"consequential"});
  const planner=dsl.capability.external("task-scheduling",{risk:"consequential",description:"Confirmed server lifecycle schedule creation, execution and deletion"});
  planner.operation("create","createSchedule",{inputs:{action:text,runAt:text,note:text},result:operationReceipt,risk:"consequential"});
  planner.operation("delete","deleteSchedule",{inputs:{scheduleId:text},result:operationReceipt,risk:"consequential"});
  planner.operation("run","runScheduledAction",{inputs:{scheduleId:text},result:operationReceipt,risk:"consequential"});
  const refresh=dsl.intent.capabilityRequest("refresh",reader,{operation:"observe",inputs:{query:text,limit:text,includeConnections:text},resultTo:processes});
  const switchView=dsl.intent.selectView("switch-view",view);
  const refreshAll=dsl.intent.capabilityRequest("refresh-all",reader,{operation:"all",inputs:{query:text,limit:text,includeConnections:text},resultTo:all});
  const refreshConnections=dsl.intent.capabilityRequest("refresh-connections",reader,{operation:"connections",inputs:{query:text,limit:text,includeConnections:text},resultTo:connections});
  const refreshHardware=dsl.intent.capabilityRequest("refresh-hardware",reader,{operation:"hardware",inputs:{query:text,limit:text,includeConnections:text},resultTo:hardware});
  const listSchedules=dsl.intent.capabilityRequest("list-schedules",reader,{operation:"schedules",resultTo:schedules});
  const serverStatus=dsl.intent.capabilityRequest("server-status",reader,{operation:"server-status",resultTo:serverResult});
  const askAi=dsl.intent.capabilityRequest("task-ai",reader,{operation:"ai",inputs:{instruction:text,query:text},arguments:{view},resultTo:aiResult});
  const terminate=dsl.intent.capabilityRequest("terminate-process",control,{operation:"terminate",arguments:{selected},resultTo:operationResult});
  const kill=dsl.intent.capabilityRequest("kill-process",control,{operation:"kill",arguments:{selected},resultTo:operationResult});
  const startServer=dsl.intent.capabilityRequest("start-server",control,{operation:"start",resultTo:operationResult});
  const restartServer=dsl.intent.capabilityRequest("restart-server",control,{operation:"restart",resultTo:operationResult});
  const shutdownServer=dsl.intent.capabilityRequest("shutdown-server",control,{operation:"shutdown",resultTo:operationResult});
  const createSchedule=dsl.intent.capabilityRequest("create-schedule",planner,{operation:"create",inputs:{action:text,runAt:text,note:text},resultTo:operationResult});
  const deleteSchedule=dsl.intent.capabilityRequest("delete-schedule",planner,{operation:"delete",arguments:{scheduleId:selectedSchedule},resultTo:operationResult});
  const runSchedule=dsl.intent.capabilityRequest("run-schedule",planner,{operation:"run",arguments:{scheduleId:selectedSchedule},resultTo:operationResult});
  const choose=dsl.intent.selectFromCollection("choose-process",processes,selected);
  const chooseAll=dsl.intent.selectFromCollection("choose-all-process",all,selected);
  const chooseSchedule=dsl.intent.selectFromCollection("choose-schedule",schedules,selectedSchedule);
  const surface=dsl.surface.bindHtml("application",{bindings:{
    query:[dsl.surface.action("select-notebook",{intent:switchView}),dsl.surface.form("monitor-query",{fields:{query:text,limit:text,includeConnections:text},defaults:{query:"",limit:"100",includeConnections:"true"},intent:refresh})],
    processes:[dsl.surface.collection("process-list",{key:"processKey",fields:["name","pid","status","memory"],state:processes,select:choose})],
    all:[dsl.surface.form("all-query",{fields:{query:text,limit:text,includeConnections:text},defaults:{query:"",limit:"100",includeConnections:"true"},intent:refreshAll}),dsl.surface.collection("all-process-list",{key:"processKey",fields:["name","pid","status","memory"],state:all,select:chooseAll})],
    connections:[dsl.surface.form("connection-query",{fields:{query:text,limit:text,includeConnections:text},defaults:{query:"",limit:"100",includeConnections:"true"},intent:refreshConnections}),dsl.surface.collection("connection-list",{key:"key",fields:["pid","name","status","local","remote"],state:connections})],
    hardware:[dsl.surface.form("hardware-query",{fields:{query:text,limit:text,includeConnections:text},defaults:{query:"",limit:"100",includeConnections:"true"},intent:refreshHardware}),dsl.surface.text("hardware-overview",{value:hardware})],
    schedules:[dsl.surface.action("list-schedules",{intent:listSchedules}),
       dsl.surface.collection("schedule-list",{key:"id",fields:["action","runAt","note"],state:schedules,select:chooseSchedule}),
       dsl.surface.form("create-schedule-form",{fields:{action:text,runAt:text,note:text},intent:createSchedule}),
       dsl.surface.text("selected-schedule",{value:selectedSchedule}),
       dsl.surface.action("delete-schedule",{intent:deleteSchedule}),
       dsl.surface.action("run-schedule",{intent:runSchedule})],
    assistant:[dsl.surface.form("ai-question",{fields:{instruction:text,query:text},defaults:{query:""},intent:askAi}),dsl.surface.text("ai-brief",{value:aiResult})],
    operations:[dsl.surface.action("terminate-process",{intent:terminate}),dsl.surface.action("kill-process",{intent:kill}),
      dsl.surface.action("start-server",{intent:startServer}),dsl.surface.action("restart-server",{intent:restartServer}),
      dsl.surface.action("shutdown-server",{intent:shutdownServer}),
      dsl.surface.action("server-status",{intent:serverStatus}),
      dsl.surface.text("server-status-result",{value:serverResult}),
      dsl.surface.text("operation-receipt",{value:operationResult})],
    selected:[dsl.surface.text("selected-process",{value:selected})],
    status:[dsl.surface.receipt("activity")]
  }});
  return {states:[processes,selected,selectedSchedule,view,all,connections,hardware,schedules,aiResult,operationResult,serverResult],
    capabilities:[reader,control,planner],intents:[refresh,choose,chooseAll,chooseSchedule,switchView,refreshAll,refreshConnections,refreshHardware,
      listSchedules,serverStatus,askAi,terminate,kill,startServer,restartServer,shutdownServer,createSchedule,deleteSchedule,runSchedule],surfaces:[surface]};
});
