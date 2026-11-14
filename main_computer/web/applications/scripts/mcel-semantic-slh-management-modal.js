"use strict";
/* LMS-owned exclusive operation monitor. Presentation only: all authority,
 * stages, cancel admission and recovery come from the selected machine's LMS.
 * Dismissing this view never cancels an LMS operation or clears its receipt.
 */
(function(global){
  function create({host,request,onSettled=()=>{}}){
    if(!host||typeof request!=="function")throw Error("LMS_MODAL:INVALID_HOST");
    let running=false, active=false, inFlight=false, generation=0, timer=null;
    let tracked="", shown="", lastState=null, lastCompletion=null, dismissedId="", dismissedUntilIdle=false;
    let presentation="";
    let submission=null, lastObservedStage="", lastObservedResult="";
    let outputLines=[];
    const lastHarnessOutputSeq=new Map();
    // The semantic workspace is the visual boundary; target navigation stays live.
    const zone=host.querySelector('[data-mcel-zone="workspace"]') || host;
    zone.style.position="relative";
    const indicator=document.createElement("div");
    indicator.className="mcel-lms-operation-indicator";
    indicator.dataset.mcelLmsOperationIndicator="";
    indicator.setAttribute("role","status");
    indicator.hidden=true;
    const indicatorText=document.createElement("span");
    const view=document.createElement("button");view.type="button";view.textContent="View operation";
    indicator.append(indicatorText,view);
    // The SLH application supplies region semantics; MCEL owns mounting,
    // geometry allocation, viewport adaptation, focus and dismissal.
    const Engine=global.McelSemanticModalSubprogram;
    if(!Engine)throw Error("LMS_MODAL:MCEL_MODAL_RUNTIME_UNAVAILABLE");
    const definition=Engine.define({
      id:"slh-lms-operation-monitor",
      geometry:{minimumWidth:440,minimumHeight:460,
        preferredWidth:680,preferredHeight:590,
        maximumRequestedWidthPercent:88,maximumRequestedHeightPercent:86},
      regions:[
        {id:"header",budget:"fixed",height:116,overflow:"wrap"},
        {id:"details",budget:"fixed",height:142,overflow:"clip"},
        {id:"output",budget:"flexible",minimumHeight:132,overflow:"scroll"},
        {id:"actions",budget:"fixed",height:70,overflow:"clip"}
      ]
    });
    let internalDismiss=false;
    const modal=Engine.create({definition,document:global.document,root:global.document.body,
      viewport:zone,onDismiss:()=>{
        inertRest(false);
        presentation="";
        if(!internalDismiss){
          if(submission)submission.dismissed=true;
          dismissedId=shown;
          if(!shown)dismissedUntilIdle=true;
        }
      }});
    const shade=modal.element;
    shade.dataset.mcelLmsOperationModal="";
    const card=modal.panel;
    card.dataset.mcelLmsOperationPanel="";
    card.setAttribute("aria-label","LMS management operation");
    const header=modal.region("header");
    const title=document.createElement("h3");
    const description=document.createElement("p");
    header.append(title,description);
    const details=modal.region("details");
    const output=modal.region("output");
    const outputHeading=document.createElement("h4");outputHeading.textContent="Operation results";
    const outputText=document.createElement("pre");
    outputText.dataset.mcelLmsOperationResults="";
    outputText.setAttribute("role","log");
    outputText.setAttribute("aria-label","Operation results");
    outputText.setAttribute("aria-live","polite");
    output.append(outputHeading,outputText);
    const controls=modal.region("actions");
    const cancel=document.createElement("button");cancel.type="button";cancel.textContent="Cancel operation";
    const retry=document.createElement("button");retry.type="button";retry.textContent="Retry observation";
    const close=document.createElement("button");close.type="button";close.textContent="Dismiss";
    controls.append(cancel,retry,close);zone.append(indicator);
    // Preserve unrelated inert states; this overlay must never permanently lock MCEL.
    let oldInert=null;
    function inertRest(value){
      if(value){
        if(oldInert)return;
        oldInert=new Map();
        for(const child of Array.from(zone.children)){
          if(child!==indicator){oldInert.set(child,child.inert);child.inert=true;}
        }
      }else if(oldInert){
        for(const [child,inert] of oldInert)child.inert=inert;
        oldInert=null;
      }
    }
    function log(message){
      const line=String(message||"").slice(0,900);
      if(!line || outputLines.at(-1)===line)return;
      outputLines.push(line);
      if(outputLines.length>120)outputLines.shift();
      const follow=!modal.isOpen() || output.scrollHeight-output.clientHeight-output.scrollTop<=36;
      outputText.textContent=outputLines.join("\n");
      if(follow)output.scrollTop=output.scrollHeight;
    }
    function logHarnessOutput(op){
      if(!op || typeof op.operation_id!=="string" || !Array.isArray(op.output))return;
      const id=op.operation_id;
      let lastSeq=lastHarnessOutputSeq.get(id)||0;
      for(const entry of op.output){
        if(!entry || !Number.isSafeInteger(entry.seq) || entry.seq<=lastSeq ||
           !["stdout","stderr"].includes(entry.stream) || typeof entry.text!=="string")continue;
        if(entry.seq>lastSeq+1)
          log("LMS · Earlier harness output omitted from bounded receipt");
        log("Harness "+entry.stream+" · "+entry.text);
        lastSeq=entry.seq;
      }
      lastHarnessOutputSeq.set(id,lastSeq);
    }
    function logStage(op){
      if(!op)return;
      logHarnessOutput(op);
      const key=[op.operation_id,op.phase,op.stage].join(":");
      if(key===lastObservedStage)return;
      lastObservedStage=key;
      log("LMS · "+String(op.phase||"UNKNOWN")+" · "+String(op.stage||"stage unavailable")+
        (op.operation_id?" · "+op.operation_id:""));
    }
    function logResult(receipt){
      logHarnessOutput(receipt);
      const result=receipt?.result;
      if(!result || typeof result!=="object")return;
      const key=JSON.stringify([receipt.operation_id,receipt.phase,result.status,result.error_code]);
      if(lastObservedResult===key)return;
      lastObservedResult=key;
      log("LMS result · "+String(result.status||receipt.phase||"UNKNOWN"));
      // Explicit allowlist of status fields. No raw harness output, args or secrets.
      for(const field of ["error_code","error_type","application_uuid","remote_mutation"]){
        if(["string","boolean"].includes(typeof result[field]))
          log(field+": "+String(result[field]).slice(0,160));
      }
    }
    function hide(){
      if(!modal.isOpen())return;
      internalDismiss=true;
      try{modal.dismiss();}finally{internalDismiss=false;}
      inertRest(false);
      presentation="";
    }
    function display(name,explanation,fields={},opts={}){
      const opening=!modal.isOpen();
      presentation=opts.kind||"notice";
      title.textContent=name;description.textContent=explanation;
      details.replaceChildren();
      for(const [key,value] of Object.entries(fields)){
        const dt=document.createElement("dt"),dd=document.createElement("dd");
        dt.textContent=key;dd.textContent=String(value??"—");details.append(dt,dd);
      }
      cancel.hidden=!opts.cancelable;
      retry.hidden=!opts.retry;
      // The modal's presentation geometry belongs to the general MCEL engine.
      // Changing stages, messages or log output cannot resize its regions.
      modal.setBlocking(presentation==="busy");
      modal.show();
      inertRest(presentation==="busy");
      if(opening)close.focus({preventScroll:true});
    }
    function setIndicator(message,label="View operation"){
      indicatorText.textContent=message;
      view.textContent=label;
      indicator.hidden=false;
    }
    function detailsFor(op){
      return {"Operation":(op.action||"Management").replaceAll("-"," "),
        "Operation ID":op.operation_id,"Stage":op.stage||op.phase,
        "Status":op.phase,
        "Cancellation":op.cancelable===true?"Available before execution":"Unavailable during this stage"};
    }
    function showCurrent(force=false){
      const state=lastState;
      if(!state)return;
      const op=state.operation;
      if(state.state==="BUSY"&&op){
        shown=op.operation_id||"";
        logStage(op);
        if(force || (!submission?.dismissed && !dismissedUntilIdle && dismissedId!==shown)){
          display("LMS management operation in progress",
            "This machine is performing an exclusive management operation. Conflicting mutations are refused by LMS.",
            detailsFor(op),{kind:"busy",cancelable:op.cancelable===true});
        }
      }else if(state.state==="IDLE"&&state.last_operation?.result?.status==="INCOMPLETE"){
        const last=state.last_operation;
        shown=last.operation_id||"";
        if(force)display("Previous LMS operation incomplete",
          "The prior execution outcome was not confirmed. LMS is idle. Any new mutation must pass the SEH harness's existing safety checks.",
          {...detailsFor(last),"Recorded result":"INCOMPLETE",
            "Remote mutation":last.result?.remote_mutation||"UNKNOWN"},{kind:"notice"});
      }
    }
    // MCEL dispatch happens synchronously on the operator click, before an
    // authorization request, preflight, or LMS HTTP response can complete.
    function begin(intentId,action){
      submission={intentId,action,state:"submitting",dismissed:false};
      tracked="";shown="";lastCompletion=null;lastObservedStage="";lastObservedResult="";
      outputLines=[];lastHarnessOutputSeq.clear();outputText.textContent="";
      dismissedId="";dismissedUntilIdle=false;
      log("MCEL · "+action+" requested");
      log("MCEL · Submitting to LMS; waiting for operation acknowledgement");
      setIndicator(action+" · submitting to LMS", "View operation");
      display("Submitting "+action,
        "The request is being prepared. LMS has not yet accepted this operation.",
        {"Operation":action,"Stage":"SUBMITTING","Operation ID":"Not assigned by LMS"},
        {kind:"busy",cancelable:false});
    }
    function settled(intentId,disposition,reason=""){
      if(!submission || submission.intentId!==intentId)return;
      if(disposition!=="completed"){
        submission.state="failed";
        const code=String(reason||disposition||"REQUEST_REFUSED").slice(0,180);
        log("MCEL submission failed · "+code);
        setIndicator("LMS submission failed · "+code,"View result");
        // A timeout is not proof that the harness did not run. Preserve any
        // observed LMS operation while retaining the failed request evidence.
        if(!submission.dismissed && lastState?.state!=="BUSY")
          display("Deployment submission failed",
            "The request failed or its acknowledgement was lost. The LMS operation status is observed separately; do not blindly retry.",
            {"Operation":submission.action,"Error":code},
            {kind:"notice",retry:true});
      }else if(!tracked && submission.state==="submitting"){
        submission.state="unconfirmed";
        log("MCEL response completed · no LMS operation ID was returned");
        setIndicator("Submission returned without operation ID", "View result");
        if(!submission.dismissed)
          display("Submission response received",
            "The request returned, but no LMS operation ID was supplied. Check LMS status before retrying.",
            {"Operation":submission.action,"Stage":"ACKNOWLEDGEMENT_UNCONFIRMED"},
            {kind:"notice",retry:true});
      }
      void refresh();
    }
    function arm(){
      if(timer!==null)global.clearTimeout(timer);
      if(running)timer=global.setTimeout(()=>{void refresh();},1200);
    }
    async function refresh(){
      if(!running||inFlight)return;
      const epoch=generation;inFlight=true;
      try{
        const state=await request("operation-state",{});
        if(!running||epoch!==generation)return;
        if(!state||!["IDLE","BUSY"].includes(state.state))
          throw Error("INVALID_LMS_OPERATION_SNAPSHOT");
        lastState=state;
        const op=state.operation;
        const wasActive=active;
        if(state.busy&&op){
          lastCompletion=null;
          active=true;
          shown=op.operation_id||"";
          setIndicator("LMS busy · "+(op.action||"management")+" · "+(op.stage||op.phase||"running"));
          showCurrent();
        }else if(tracked){
          const receipt=await request("operation-status",{operationId:tracked});
          if(!running||epoch!==generation)return;
          active=false;shown="";
          if(receipt.phase==="RECORDED"||receipt.phase==="CANCELED"){
            logStage(receipt);logResult(receipt);
            lastCompletion={operationId:tracked,status:receipt.result?.status||receipt.phase};
            if(submission)submission.state="completed";
            const incomplete=receipt.result?.status==="INCOMPLETE";
            setIndicator(incomplete?"LMS operation incomplete":"LMS operation completed · "+(receipt.result?.status||receipt.phase),"View result");
            if(!submission?.dismissed && !dismissedUntilIdle && dismissedId!==tracked){
              display(incomplete?"LMS operation incomplete":"LMS management operation completed",
                incomplete?"The harness outcome was not confirmed. LMS is idle; inspect its receipt before a new operation.":
                  "The LMS recorded the outcome. Coolify submission is not proof of SEH readiness.",
                {"Operation ID":tracked,"Status":receipt.result?.status||receipt.phase},
                {kind:"completed"});
            }else hide();
            tracked="";
          }else{
            logStage(receipt);
            // A receipt can still be ACCEPTED while the latest snapshot is IDLE.
            // Keep watching rather than fabricating completion.
          }
        }else if(submission && ["submitting","failed","unconfirmed"].includes(submission.state)){
          active=false;
          // Do not erase the click-driven modal while preflight is still
          // awaiting an LMS answer, or hide its error on the next idle poll.
          if(submission.state==="submitting")
            setIndicator(submission.action+" · submitting to LMS","View operation");
        }else{
          active=false;shown="";lastState=state;
          dismissedId="";dismissedUntilIdle=false;
          // A terminal LMS receipt is not permission to erase the visible
          // results dialog. Keep it open until the operator dismisses it.
          if(!(lastCompletion && presentation==="completed" && !shade.hidden))hide();
          if(lastCompletion)setIndicator((lastCompletion.status==="INCOMPLETE"?"LMS operation incomplete":"LMS operation completed · "+lastCompletion.status),"View result");
          else if(state.last_operation?.result?.status==="INCOMPLETE"){
            setIndicator("Previous LMS operation incomplete · "+(state.last_operation.action||"management"),"View receipt");
          }else indicator.hidden=true;
        }
        if(wasActive&&!state.busy)onSettled();
      }catch(error){
        if(!running||epoch!==generation)return;
        active=false;lastState=null;
        // A failed observation must not dismiss a submission still in flight
        // or erase the failure returned by the operation request itself.
        if(tracked || submission?.state==="accepted"){
          log("LMS status temporarily unavailable · operation ID retained");
          setIndicator("LMS operation acknowledged · status unavailable","View operation");
          if(!submission?.dismissed)
            display("LMS operation status unavailable",
              "LMS accepted this operation, but the current status read failed. Monitoring will retry; do not resubmit deployment.",
              {"Operation ID":tracked||shown,"Stage":"OBSERVATION_UNAVAILABLE"},
              {kind:"notice",retry:true});
        }else if(!submission || !["submitting","failed","unconfirmed"].includes(submission.state)){
          shown="";hide();
          setIndicator("LMS operation state unavailable · status not verified","Check status");
        }else if(submission.state==="submitting"){
          log("LMS status observation unavailable · submission still pending");
        }
      }finally{inFlight=false;if(epoch===generation)arm();}
    }
    cancel.addEventListener("click",async()=>{
      if(!shown)return;
      cancel.disabled=true;
      try{await request("cancel-operation",{operationId:shown});}
      catch{/* LMS decides whether cancellation is safe; re-observe. */}
      finally{cancel.disabled=false;void refresh();}
    });
    retry.addEventListener("click",()=>{void refresh();});
    close.addEventListener("click",()=>modal.dismiss());
    function reopen(){
      // Reopening is an explicit presentation choice, superseding dismissal
      // while leaving the underlying operation untouched.
      if(submission)submission.dismissed=false;
      dismissedId="";dismissedUntilIdle=false;
      if(lastState?.state==="BUSY"){
        showCurrent(true);
      }else if(submission && ["submitting","failed","unconfirmed"].includes(submission.state)){
        const title=submission.state==="submitting"?"Submitting "+submission.action:
          submission.state==="failed"?"Deployment submission failed":"Submission response received";
        display(title,submission.state==="submitting"?
            "Waiting for LMS acknowledgement; this is not confirmation of deployment.":
            "Inspect the operation results and current LMS state before retrying.",
          {"Operation":submission.action,"Stage":submission.state.toUpperCase()},
          {kind:submission.state==="submitting"?"busy":"notice",retry:submission.state!=="submitting"});
      }else if(lastCompletion){
        display(lastCompletion.status==="INCOMPLETE"?"LMS operation incomplete":"LMS management operation completed",
          lastCompletion.status==="INCOMPLETE"?"The prior outcome was not confirmed. The LMS is idle; the SEH harness owns mutation safety.":
            "The LMS recorded the outcome. Coolify submission is not proof of SEH readiness.",
          {"Operation ID":lastCompletion.operationId,"Status":lastCompletion.status},
          {kind:"completed"});
      }else if(lastState?.last_operation?.result?.status==="INCOMPLETE"){
        showCurrent(true);
      }else{
        // A status probe is not a user dismissal: retry without locking the UI.
        void refresh();
      }
    }
    view.addEventListener("click",reopen);
    function start(){
      if(running)return refresh();
      running=true;generation++;
      setIndicator("Checking LMS operation state", "Check status");
      return refresh();
    }
    function stop(){
      running=false;generation++;
      if(timer!==null)global.clearTimeout(timer);
      timer=null;inFlight=false;active=false;tracked="";shown="";lastState=null;lastCompletion=null;
      dismissedId="";dismissedUntilIdle=false;
      submission=null;outputLines=[];lastHarnessOutputSeq.clear();outputText.textContent="";
      lastObservedStage="";lastObservedResult="";
      hide();indicator.hidden=true;
    }
    function track(operationId){
      if(typeof operationId==="string"&&/^[A-Za-z0-9][A-Za-z0-9_.-]{15,127}$/.test(operationId)){
        tracked=operationId;lastCompletion=null;
        shown=operationId;
        log("LMS acknowledged operation · "+operationId);
        if(submission){
          submission.state="accepted";
          setIndicator(submission.action+" · LMS accepted", "View operation");
          if(!submission.dismissed)
            display("LMS operation accepted","The LMS owns this operation. Monitoring its progress.",
              {"Operation":submission.action,"Operation ID":operationId,"Stage":"ACCEPTED"},
              {kind:"busy",cancelable:false});
        }
      }
    }
    return Object.freeze({start,stop,track,begin,settled,refresh,
      getState:()=>lastState,isActive:()=>active,modal});
  }
  global.McelSlhManagementModal=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
