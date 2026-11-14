/* MCEL Lab: deterministic, isolated modal-subprogram lifecycle specimen.
 * This fixture NEVER invokes LMS, ORG-SLH, the SEH harness, or Coolify.
 */
(function(global){
  "use strict";
  const contract={
    id:"mcel-lab-operation-fixture",
    geometry:{minimumWidth:440,minimumHeight:450,preferredWidth:690,preferredHeight:560,
      maximumRequestedWidthPercent:86,maximumRequestedHeightPercent:82},
    regions:[
      {id:"header",budget:"fixed",height:104,overflow:"wrap"},
      {id:"details",budget:"fixed",height:118,overflow:"clip"},
      {id:"output",budget:"flexible",minimumHeight:142,overflow:"scroll"},
      {id:"actions",budget:"fixed",height:74,overflow:"clip"}
    ]
  };
  function createMockOperation(onChange){
    let counter=0,current=null,timer=null;
    function publish(){onChange(snapshot());}
    function snapshot(){return current?{...current,lines:[...current.lines]}:null;}
    function stopTimer(){if(timer!==null){global.clearInterval(timer);timer=null;}}
    function start(mode){
      stopTimer();
      const id="lab-operation-"+String(++counter).padStart(3,"0");
      current={id,mode,phase:"SUBMITTING",stage:"REQUEST_DISPATCHED",cancelable:true,
        step:0,lines:[`MCEL · ${mode==="refusal"?"Mock refusal":"Mock deployment"} requested`,
          "MCEL · Waiting for simulated LMS acknowledgement"]};
      publish();
      const sequence=[
        ["ACCEPTED","OPERATION_ADMITTED","LMS · Accepted "+id],
        ["RUNNING","PREFLIGHT_EXECUTING","Harness stdout · Preflight started"],
        ["RUNNING","HARNESS_EXECUTING","Harness stdout · Processing simulated work"],
        ["RUNNING","HARNESS_EXECUTING","Harness stderr · Example diagnostic (simulated, nonfatal)"],
        ["RUNNING","VERIFYING","LMS · Verifying simulated result"],
        mode==="refusal"?["FAILED","PREFLIGHT_REFUSED","Harness stderr · REFUSED: mock prerequisite not satisfied"]:
          ["COMPLETED","RESULT_RECORDED","LMS result · COMPLETED (mock only)"]
      ];
      timer=global.setInterval(()=>{
        if(!current)return stopTimer();
        const item=sequence[current.step++];
        if(!item)return stopTimer();
        current.phase=item[0];current.stage=item[1];
        current.cancelable=current.phase==="SUBMITTING" || current.phase==="ACCEPTED";
        current.lines.push(item[2]);
        if(["COMPLETED","FAILED","CANCELLED"].includes(current.phase))stopTimer();
        publish();
      },520);
      return snapshot();
    }
    function cancel(){
      if(!current||!current.cancelable)return false;
      stopTimer();current.phase="CANCELLED";current.stage="CANCEL_ACKNOWLEDGED";
      current.cancelable=false;current.lines.push("LMS · Simulated operation canceled before execution");
      publish();return true;
    }
    function stress(count=140){
      if(!current)return;
      for(let i=0;i<count;i++)current.lines.push("Harness stdout · Diagnostic line "+String(i+1).padStart(3,"0")+" · mock streaming text");
      publish();
    }
    function reset(){stopTimer();current=null;publish();}
    return Object.freeze({start,cancel,stress,reset,snapshot,dispose:stopTimer});
  }
  function init(host=document){
    const root=host.getElementById("mcel-modal-subprogram-lab");
    const Engine=global.McelSemanticModalSubprogram;
    if(!root||!Engine)return null;
    if(root.dataset.mcelModalLabReady==="true")return global.McelModalSubprogramLab.controller;
    root.dataset.mcelModalLabReady="true";
    const definition=Engine.define(contract);
    const fixture=root.querySelector("[data-mcel-modal-lab-state]");
    root.querySelector("[data-mcel-modal-lab-contract]").textContent=JSON.stringify(contract,null,2);
    const geometryReport=root.querySelector("[data-mcel-modal-lab-geometry]");
    const open=()=>modal.show();
    const modal=Engine.create({definition,document:host,root:host.body,onGeometry:(g,reason)=>{
      geometryReport.textContent=`${reason}: ${g.width} × ${g.height}px; header ${g.regions.header}px, `+
        `details ${g.regions.details}px, output ${g.regions.output}px, actions ${g.regions.actions}px; `+
        `oversize presentation: ${g.exceedsViewport}`;
    }});
    const header=modal.region("header"),details=modal.region("details"),output=modal.region("output"),actions=modal.region("actions");
    const heading=host.createElement("h3"),summary=host.createElement("p");
    heading.textContent="MCEL modal subprogram · mock operation";
    header.append(heading,summary);
    const detailText=host.createElement("div");detailText.className="mcel-modal-lab-details";
    details.append(detailText);
    const outputTitle=host.createElement("strong"),log=host.createElement("pre");
    outputTitle.textContent="Mock stdout / stderr";log.setAttribute("role","log");
    log.setAttribute("aria-label","Mock operation output");
    output.append(outputTitle,log);
    const dismiss=host.createElement("button");dismiss.type="button";dismiss.textContent="Dismiss";
    const cancel=host.createElement("button");cancel.type="button";cancel.textContent="Cancel operation";
    const addOutput=host.createElement("button");addOutput.type="button";addOutput.textContent="Add 180 lines";
    actions.append(addOutput,cancel,dismiss);dismiss.addEventListener("click",()=>modal.dismiss());
    let lastSnapshot=null;
    function render(op){
      lastSnapshot=op;
      const follow=output.scrollHeight-output.clientHeight-output.scrollTop<30;
      summary.textContent=op?"LMS-like operation lifecycle, simulated entirely in MCEL Lab":"No operation running; start a mock scenario.";
      detailText.textContent=op?
        `ID: ${op.id}  |  Status: ${op.phase}  |  Stage: ${op.stage}  |  Cancelable: ${op.cancelable}`:
        "Status: IDLE · No operation ID · Geometry contract committed on open";
      log.textContent=op?op.lines.join("\n"):"Mock output will appear here.";
      cancel.disabled=!op?.cancelable;
      if(follow)output.scrollTop=output.scrollHeight;
      fixture.textContent=op?
        `${op.phase} · ${op.stage} · ${op.id} · ${op.lines.length} mock lines · modal ${modal.isOpen()?"open":"dismissed"}`:
        "IDLE · no mock operation";
    }
    const service=createMockOperation(render);
    cancel.addEventListener("click",()=>service.cancel());
    addOutput.addEventListener("click",()=>service.stress(180));
    root.querySelector("[data-mcel-modal-start]").addEventListener("click",()=>{
      modal.show();service.start("success");
    });
    root.querySelector("[data-mcel-modal-fail]").addEventListener("click",()=>{
      modal.show();service.start("refusal");
    });
    root.querySelector("[data-mcel-modal-reconnect]").addEventListener("click",()=>{
      modal.show();render(service.snapshot());
    });
    root.querySelector("[data-mcel-modal-stress]").addEventListener("click",()=>{
      if(!service.snapshot())service.start("success");
      modal.show();service.stress(180);
    });
    root.querySelector("[data-mcel-modal-reset]").addEventListener("click",()=>service.reset());
    render(null);
    const controller=Object.freeze({definition,modal,service,snapshot:()=>service.snapshot()});
    global.McelModalSubprogramLab.controller=controller;
    return controller;
  }
  global.McelModalSubprogramLab={init,contract,createMockOperation,controller:null};
})(window);
