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
    let submission=null, lastObservedStage="", lastObservedResult="", lastRecorded=null;
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
    const modal=Engine.create({definition,document:global.document,root:global.document.body,
      viewport:zone,onDismiss:()=>{
        inertRest(false);
        presentation="";
        if(submission)submission.dismissed=true;
        dismissedId=shown;
        if(!shown)dismissedUntilIdle=true;
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
    // LMS already persists a bounded, allowlisted diagnostic. Treat it as data,
    // not executable markup or a reason to inspect raw harness output. The
    // readback can appear either on the receipt or inside its result.
    function causalDiagnostic(receipt){
      const raw=receipt?.causal_diagnostic || receipt?.result?.causal_diagnostic;
      if(!raw || raw.schema!=="main-computer.seh-causal-diagnostic.v1" ||
         !/^[A-Z][A-Z0-9_]{2,95}$/.test(raw.error_code||"") ||
         !/^[A-Za-z][A-Za-z0-9_-]{2,95}$/.test(raw.failed_stage||""))return null;
      const diagnostic={schema:raw.schema,error_code:raw.error_code,
        failed_stage:raw.failed_stage};
      if(["lms-harness","resident-outbound-supervisor"].includes(raw.source))
        diagnostic.source=raw.source;
      if(/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(raw.exception_class||""))
        diagnostic.exception_class=raw.exception_class;
      for(const key of ["attempts","failures","failure_attempt","failure_unix_ns"]){
        if(Number.isSafeInteger(raw[key]) && raw[key]>=0)diagnostic[key]=raw[key];
      }
      if(Number.isInteger(raw.http_status)&&raw.http_status>=100&&raw.http_status<=599)
        diagnostic.http_status=raw.http_status;
      if(typeof raw.resident_child_observed==="boolean")
        diagnostic.resident_child_observed=raw.resident_child_observed;
      return diagnostic;
    }
    function receiptFields(receipt){
      const diag=causalDiagnostic(receipt);
      return {"Operation ID":receipt.operation_id||"Not confirmed",
        "Status":receipt.result?.status||receipt.phase||"UNKNOWN",
        ...(diag?{"Failure code":diag.error_code,"Failure stage":diag.failed_stage}:{})};
    }
    function logResult(receipt){
      logHarnessOutput(receipt);
      const result=receipt?.result;
      if(!result || typeof result!=="object")return;
      const diag=causalDiagnostic(receipt);
      if(typeof receipt.operation_id==="string" && receipt.operation_id)
        lastRecorded={schema:"mcel.slh.management-evidence.v1",
          operationId:receipt.operation_id,phase:receipt.phase||"UNKNOWN",
          status:result.status||receipt.phase||"UNKNOWN",causalDiagnostic:diag};
      const key=JSON.stringify([receipt.operation_id,receipt.phase,result.status,result.error_code,diag]);
      if(lastObservedResult===key)return;
      lastObservedResult=key;
      log("LMS result · "+String(result.status||receipt.phase||"UNKNOWN"));
      // Explicit allowlist only. No exception messages, raw request parameters,
      // credentials, or unrecognized diagnostic fields.
      for(const field of ["error_code","error_type","application_uuid","remote_mutation"]){
        if(["string","boolean"].includes(typeof result[field]))
          log(field+": "+String(result[field]).slice(0,160));
      }
      if(diag){
        log("LMS causal diagnostic · "+diag.error_code+" · "+diag.failed_stage);
        for(const field of ["source","exception_class","http_status","attempts",
          "failures","failure_attempt","resident_child_observed"]){
          if(diag[field]!==undefined)log("causal_"+field+": "+diag[field]);
        }
      }
    }
    function showReceipt(receipt,prior=false){
      const incomplete=receipt.result?.status==="INCOMPLETE";
      display(incomplete?(prior?"Previous LMS operation incomplete":"LMS operation incomplete"):
        "LMS management operation completed",
        incomplete?"LMS recorded an incomplete outcome. Inspect the failure code and stage before another operation.":
          "LMS recorded the outcome. Coolify submission is not proof of SEH readiness.",
        receiptFields(receipt),{kind:"completed"});
    }
    // Service observations have no authority to dismiss the operator's dialog.
    // Only the Dismiss control (or the engine's Escape key) closes it; leaving
    // the page naturally tears down the whole document.
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
        logStage(last);logResult(last);
        if(force)showReceipt(last,true);
      }
    }
    // MCEL dispatch happens synchronously on the operator click, before an
    // authorization request, preflight, or LMS HTTP response can complete.
    function begin(intentId,action,preview=false){
      submission={intentId,action,preview:preview===true,state:"submitting",dismissed:false};
      tracked="";shown="";lastCompletion=null;lastObservedStage="";lastObservedResult="";
      outputLines=[];lastHarnessOutputSeq.clear();outputText.textContent="";
      lastRecorded=null;
      dismissedId="";dismissedUntilIdle=false;
      log("MCEL · "+action+" requested");
      if(submission.preview){
        log("MCEL · Running read-only harness preflight; no LMS operation ID expected");
        setIndicator(action+" · checking", "View preflight");
        display(action+" running",
          "This is a read-only preflight. No SEH deployment change has been requested.",
          {"Operation":action,"Stage":"PREFLIGHT","Operation ID":"Not applicable"},
          {kind:"notice"});
        return;
      }
      log("MCEL · Submitting to LMS; waiting for operation acknowledgement");
      setIndicator(action+" · submitting to LMS", "View operation");
      display("Submitting "+action,
        "The request is being prepared. LMS has not yet accepted this operation.",
        {"Operation":action,"Stage":"SUBMITTING","Operation ID":"Not assigned by LMS"},
        {kind:"busy",cancelable:false});
    }
    function settled(intentId,disposition,reason="",result=null){
      if(!submission || submission.intentId!==intentId)return;
      if(submission.preview){
        // Preview is a synchronous, read-only harness operation, not a durable
        // LMS deployment job. Never wait for an operation ID or claim a mutation.
        const completed=disposition==="completed";
        const outcome=completed && typeof result?.outcome==="string"
          ? result.outcome.slice(0,100):"";
        const error=completed?"":String(reason||disposition||"PREFLIGHT_FAILED")
          .replace(/[^A-Za-z0-9_:.-]/g,"").slice(0,180);
        const refused=completed && outcome.toUpperCase()==="REFUSED";
        submission.state=completed?(refused?"preview-refused":"preview-completed"):"preview-failed";
        submission.outcome=outcome||null;submission.errorCode=error||null;
        const title=submission.action+(completed?(refused?" refused":" completed"):" failed");
        const explanation=completed
          ? "Read-only preflight returned. This did not deploy or undeploy the SEH."
          : "The preflight request failed before an outcome could be verified. No undeploy authorization was obtained.";
        const fields={"Operation":submission.action,"Stage":"PREFLIGHT",
          ...(completed?{"Result":outcome||"COMPLETED"}:{"Error":error})};
        submission.previewView={title,explanation,fields};
        log("MCEL · "+(completed?"Preflight result · "+(outcome||"COMPLETED"):
          "Preflight failed · "+error));
        setIndicator(submission.action+(completed?" · "+(outcome||"completed"):" · failed"),"View preflight");
        if(!submission.dismissed)display(title,explanation,fields,{kind:completed?"completed":"notice"});
        return;
      }
      if(disposition!=="completed" && tracked){
        // The HTTP/semantic acknowledgement may fail after LMS has accepted
        // this exact operation. A tracked ID is stronger evidence than the
        // transport disposition; do not report the accepted job as a failed
        // submission or stop monitoring its outcome.
        submission.state="accepted";
        log("MCEL acknowledgement interrupted · LMS operation accepted · "+tracked);
        setIndicator(submission.action+" · LMS accepted · monitoring", "View operation");
      }else if(disposition!=="completed"){
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
          // A different operation supersedes the previous completed diagnosis.
          // Never attribute stale receipt evidence to a newly observed action.
          if(lastRecorded && lastRecorded.operationId!==op.operation_id)lastRecorded=null;
          lastCompletion=null;
          active=true;
          shown=op.operation_id||"";
          setIndicator("LMS busy · "+(op.action||"management")+" · "+(op.stage||op.phase||"running"));
          showCurrent();
        }else if(tracked){
          const receipt=await request("operation-status",{operationId:tracked});
          if(!running||epoch!==generation)return;
          active=false;shown=tracked;
          if(receipt.phase==="RECORDED"||receipt.phase==="CANCELED"){
            logStage(receipt);logResult(receipt);
            lastCompletion={operationId:tracked,status:receipt.result?.status||receipt.phase};
            if(submission)submission.state="completed";
            // A terminal receipt is proof that the ID may be retired. Other
            // action attempts with the same arguments must get a new ID.
            onSettled({operationId:tracked,phase:receipt.phase});
            const incomplete=receipt.result?.status==="INCOMPLETE";
            setIndicator(incomplete?"LMS operation incomplete":"LMS operation completed · "+(receipt.result?.status||receipt.phase),"View result");
            if(!submission?.dismissed && !dismissedUntilIdle && dismissedId!==tracked){
              showReceipt(receipt);
            }
            // Dismissal is operator-owned. A receipt cannot close an already
            // visible dialog or undo a previous explicit dismissal.
            tracked="";
          }else{
            logStage(receipt);
            // A receipt can still be ACCEPTED while the latest snapshot is IDLE.
            // Keep watching rather than fabricating completion.
          }
        }else if(submission?.preview){
          // Polling the durable LMS operation journal cannot settle a read-only
          // preflight and must not replace its own visible result with IDLE.
          active=false;
          if(submission.state==="submitting")
            setIndicator(submission.action+" · checking", "View preflight");
        }else if(submission && ["submitting","failed","unconfirmed"].includes(submission.state)){
          active=false;
          // Do not erase the click-driven modal while preflight is still
          // awaiting an LMS answer, or hide its error on the next idle poll.
          if(submission.state==="submitting")
            setIndicator(submission.action+" · submitting to LMS","View operation");
        }else{
          active=false;lastState=state;
          // An LMS restart exposes its most recent incomplete receipt through
          // operation-state. Retain its diagnostic even if the modal is closed.
          if(state.last_operation?.result?.status==="INCOMPLETE"){
            logStage(state.last_operation);
            logResult(state.last_operation);
          }
          // A transient IDLE snapshot cannot dismiss a visible operation or
          // prove its outcome. Use a matching terminal receipt when available.
          // Otherwise leave the dialog open, show uncertainty, and allow retry.
          if(modal.isOpen() && (presentation==="busy" || presentation==="unverified")){
            const receipt=state.last_operation;
            const matching=shown && receipt?.operation_id===shown && receipt?.result?.status;
            if(matching){
              logStage(receipt);logResult(receipt);
              lastCompletion={operationId:shown,status:receipt.result.status};
              const incomplete=receipt.result.status==="INCOMPLETE";
              showReceipt(receipt,true);
            }else{
              log("LMS reports IDLE · previous operation outcome not verified");
              display("LMS operation outcome unverified",
                "LMS is currently idle, but no matching terminal receipt confirms the previous operation. Do not assume deployment succeeded or failed.",
                {"Operation ID":shown||"Not confirmed","Stage":"IDLE / OUTCOME_UNVERIFIED"},
                {kind:"unverified",retry:true});
            }
          }
          if(lastCompletion)setIndicator((lastCompletion.status==="INCOMPLETE"?"LMS operation incomplete":"LMS operation completed · "+lastCompletion.status),"View result");
          else if(state.last_operation?.result?.status==="INCOMPLETE"){
            setIndicator("Previous LMS operation incomplete · "+(state.last_operation.action||"management"),"View receipt");
          }else if(modal.isOpen())setIndicator("LMS idle · prior operation outcome unverified","View operation");
          else indicator.hidden=true;
          // Do not clear dismissedId/dismissedUntilIdle here. An intermittent
          // IDLE observation must not resurrect a manually closed operation.
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
          if(modal.isOpen() && !submission?.dismissed)
            display("LMS operation status unavailable",
              "LMS accepted this operation, but the current status read failed. Monitoring will retry; do not resubmit deployment.",
              {"Operation ID":tracked||shown,"Stage":"OBSERVATION_UNAVAILABLE"},
              {kind:"notice",retry:true});
        }else if(!submission || !["submitting","failed","unconfirmed"].includes(submission.state)){
          log("LMS status observation unavailable · no terminal outcome verified");
          setIndicator("LMS operation state unavailable · status not verified","Check status");
          if(modal.isOpen() && presentation!=="completed")
            display("LMS operation status unavailable",
              "The service or connection was interrupted. The operation outcome is unknown; monitoring will retry automatically.",
              {"Operation ID":shown||"Not confirmed","Stage":"OBSERVATION_UNAVAILABLE"},
              {kind:"unverified",retry:true});
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
      if(submission?.preview && lastState?.state!=="BUSY"){
        const view=submission.previewView;
        if(view)display(view.title,view.explanation,view.fields,
          {kind:submission.state==="preview-completed"?"completed":"notice"});
        else display(submission.action+" running",
          "Read-only preflight request pending. No deployment mutation has been requested.",
          {"Operation":submission.action,"Stage":"PREFLIGHT"},{kind:"notice"});
      }else if(lastState?.state==="BUSY"){
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
        // Preserve the same diagnosis after a deliberate dismiss-and-reopen.
        if(lastRecorded?.operationId===lastCompletion.operationId)
          display(lastCompletion.status==="INCOMPLETE"?"LMS operation incomplete":"LMS management operation completed",
            "LMS recorded the outcome. Review its causal diagnosis before another operation.",
            {"Operation ID":lastCompletion.operationId,"Status":lastCompletion.status,
              ...(lastRecorded.causalDiagnostic?{
                "Failure code":lastRecorded.causalDiagnostic.error_code,
                "Failure stage":lastRecorded.causalDiagnostic.failed_stage}:{})},
            {kind:"completed"});
        else display(lastCompletion.status==="INCOMPLETE"?"LMS operation incomplete":"LMS management operation completed",
          "The LMS recorded the outcome. Coolify submission is not proof of SEH readiness.",
          {"Operation ID":lastCompletion.operationId,"Status":lastCompletion.status},
          {kind:"completed"});
      }else if(lastState?.last_operation?.result?.status==="INCOMPLETE"){
        showCurrent(true);
      }else{
        // Reopening is explicit, even while the service cannot be observed.
        display("LMS operation status unavailable",
          "A verified operation snapshot is not available. Monitoring will retry; do not infer an outcome from the interruption.",
          {"Operation ID":tracked||shown||"Not confirmed","Stage":"OBSERVATION_UNAVAILABLE"},
          {kind:"unverified",retry:true});
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
    function pause(){
      // A network disconnect is not a navigation or a user dismissal. Stop
      // polling until reconnection, but retain the operation, log and dialog.
      running=false;generation++;
      if(timer!==null)global.clearTimeout(timer);
      timer=null;inFlight=false;
      log("LMS connection interrupted · awaiting reconnection");
      setIndicator("LMS connection unavailable · monitoring paused","View operation");
      if(modal.isOpen() && presentation!=="completed" && !submission?.preview)
        display("LMS connection interrupted",
          "The connection to LMS was lost. The operation outcome is not verified. Monitoring resumes on reconnection.",
          {"Operation ID":tracked||shown||"Not confirmed","Stage":"CONNECTION_UNAVAILABLE"},
          {kind:"unverified"});
    }
    function stop(){
      // Explicit controller teardown only; not a service/interruption path.
      // The host can abandon the monitor when it navigates away.
      pause();
      modal.dispose();
      indicator.remove();
    }
    function track(operationId){
      if(typeof operationId==="string"&&/^[A-Za-z0-9][A-Za-z0-9_.-]{15,127}$/.test(operationId)){
        if(lastRecorded?.operationId!==operationId)lastRecorded=null;
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
    function diagnostics(){
      // Purely observational: E/W/G must never initiate a network call.
      const receipt=lastRecorded;
      const preflight=submission?.preview?{
        action:submission.action,intentId:submission.intentId,
        state:submission.state,outcome:submission.outcome||null,
        errorCode:submission.errorCode||null}:null;
      return receipt?{...receipt,
        preflight,causalDiagnostic:receipt.causalDiagnostic?{...receipt.causalDiagnostic}:null}:
        {schema:"mcel.slh.management-evidence.v1",status:"not-observed",
          operationId:tracked||shown||null,preflight,causalDiagnostic:null};
    }
    return Object.freeze({start,pause,stop,track,begin,settled,refresh,diagnostics,
      getState:()=>lastState,isActive:()=>active,modal});
  }
  global.McelSlhManagementModal=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
