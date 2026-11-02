/* Opt-in Task Manager -> generic MCEL Tab Workspace bridge.
 * Read-only lab remains inert; candidate admits only a checked external-read refresh.
 * No direct fetches, machine mutations, schedule actions, writes or host tab handlers.
 * The IR witness is NOT the recovered original Phase 3 DSL source.
 */
(function (global) {
  "use strict";
  const VALID_VIEWS = ["server-processes", "all-processes", "connections", "hardware"];
  const VALID_SOURCES = ["processes", "all_processes", "connections", "hardware"];
  const instances = new WeakMap();
  let active = null;
  const BUILD="mcel-task-notebook-lifecycle-p10.v1";
  const journal=[];
  function record(receipt) {
    journal.push({at:new Date().toISOString(),...receipt});
    if(journal.length>32)journal.shift();
    return receipt;
  }
  function refuse(code) { return {ok: false, code, effects: []}; }
  function mode(search) {
    try {
      const value = new URLSearchParams(String(search || "")).get("mcel_task_notebook");
      return ["lab", "candidate", "application"].includes(value) ? value : null;
    } catch (_) { return null; }
  }
  function allow(search) { return mode(search) !== null; }
  function check(ir) {
    if (!ir || ir.application?.appId !== "task-manager-notebook") return false;
    if ((ir.effects || []).length && !global.McelIntentEffectRuntime?.check) return false;
    try {
      const validated = global.McelTabWorkspace?.check(ir);
      return Boolean(validated && JSON.stringify(validated.tabs.map(t => t.id)) === JSON.stringify(VALID_VIEWS) &&
        VALID_VIEWS.every((id, index) => validated.sources.get(id) === VALID_SOURCES[index]));
    } catch (_) { return false; }
  }
  function project(snapshot) {
    if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) return null;
    const rows = value => Array.isArray(value) ? value.slice(0, 500) : [];
    const hardware = snapshot.hardware && typeof snapshot.hardware === "object" &&
      !Array.isArray(snapshot.hardware) ? snapshot.hardware : {};
    // Only the four IR-declared data sources cross this boundary. No actions or authority.
    return {processes: rows(snapshot.processes), all_processes: rows(snapshot.all_processes),
      connections: rows(snapshot.connections), hardware};
  }
  // The generated notebook owns read-only views. This explicitly scoped host
  // operations panel reconnects the existing Task Manager guarded action API;
  // it does not pretend that the read-only notebook IR authorizes mutations.
  function installOperationsPanel(holder, root) {
    const doc=root.ownerDocument;
    const panel=doc.createElement("section");
    panel.dataset.mcelTaskOperationalBoundary="host-guarded-v1";
    panel.className="task-card task-mcel-operations";
    const title=doc.createElement("h2");title.textContent="Process, Server & Schedule Operations";
    const hint=doc.createElement("p");
    hint.textContent="Live Windows host actions. Destructive actions require a separate confirmation; displayed rows are snapshots, not authority.";
    const status=doc.createElement("output");status.setAttribute("role","status");
    status.setAttribute("aria-live","polite");status.dataset.mcelTaskOperationStatus="true";
    status.textContent="No operation requested.";
    const buttons=doc.createElement("div");buttons.className="task-actions";
    function button(label, action, requiresConfirm=false) {
      const node=doc.createElement("button");node.type="button";node.textContent=label;
      node.dataset.mcelTaskAction=action;
      node.dataset.mcelTaskConfirm=String(requiresConfirm);
      buttons.append(node);return node;
    }
    button("Refresh snapshot","refresh");
    button("Server status","server_status");
    button("Start server","server_start",true);
    button("Restart server","server_restart",true);
    button("Shutdown server","server_shutdown",true);
    const row=doc.createElement("div");row.className="task-actions";
    const select=doc.createElement("select");select.setAttribute("aria-label","Select observed process PID");
    select.dataset.mcelTaskSelectedPid="true";
    row.append(select);
    const terminate=button("Terminate selected PID","terminate_pid",true);
    const kill=button("Force-kill selected PID","kill_pid",true);
    // Keep the process controls next to the selected PID, not among server buttons.
    row.append(terminate,kill);
    const scheduleRow=doc.createElement("div");scheduleRow.className="task-actions";
    const scheduleAction=doc.createElement("select");
    scheduleAction.setAttribute("aria-label","Scheduled server action");
    for(const action of ["server_status","server_start","server_restart","server_shutdown"]){
      const option=doc.createElement("option");option.value=action;option.textContent=action.replaceAll("_"," ");
      scheduleAction.append(option);
    }
    const runAt=doc.createElement("input");runAt.type="datetime-local";runAt.setAttribute("aria-label","Scheduled local date and time");
    const note=doc.createElement("input");note.placeholder="Operator note";note.setAttribute("aria-label","Schedule note");
    const addSchedule=doc.createElement("button");addSchedule.type="button";addSchedule.textContent="Schedule action";
    const scheduleList=doc.createElement("div");scheduleList.dataset.mcelTaskSchedules="true";
    scheduleRow.append(scheduleAction,runAt,note,addSchedule);
    panel.append(title,hint,buttons,row,scheduleRow,scheduleList,status);
    holder.append(panel);
    let observed=new Map(),scheduleRevisions=new Map(),busy=false;
    const valid=new Set(["refresh","server_status","server_start","server_restart","server_shutdown","terminate_pid","kill_pid"]);
    async function execute(action,confirmed=false){
      if(busy||!valid.has(action))return {ok:false,code:"not-admitted-or-busy"};
      let pid=null,display="";
      if(action.endsWith("_pid")){
        pid=Number(select.value);
        if(!Number.isSafeInteger(pid)||pid<=0||!observed.has(pid)){
          status.textContent="Refused: select a PID from the current observed snapshot.";
          return {ok:false,code:"stale-or-missing-pid"};
        }
        display=observed.get(pid).name;
      }
      const dangerous=!["refresh","server_status"].includes(action);
      if(dangerous && !confirmed){
        const message=action.endsWith("_pid")?
          `Execute ${action} on observed PID ${pid} (${display})?`:
          `Execute ${action} on the Main Computer server?`;
        if(typeof global.confirm!=="function" || global.confirm(message)!==true){
          status.textContent="Operation refused: confirmation not granted.";
          return {ok:false,code:"confirmation-refused"};
        }
      }
      busy=true;status.textContent=`${action} pending host receipt…`;
      try{
        let result;
        if(action==="refresh"){
          if(typeof global.refreshTaskManager!=="function")throw new Error("Host snapshot refresh unavailable");
          await global.refreshTaskManager();
          result={ok:true,action,code:"snapshot-delivered"};
        }else{
          if(typeof global.runTaskAction!=="function")throw new Error("Host action boundary unavailable");
          result=await global.runTaskAction(action,pid?{pid,expected_created_at:observed.get(pid)?.createdAt||""}:{},dangerous);
        }
        if(!result || result.ok!==true)throw new Error(result?.message||"No successful host action receipt");
        status.textContent=`${action}: host receipt received; ${result.receipt?.message||result.code||"inspect refreshed snapshot for final state"}`;
        return result;
      }catch(error){
        status.textContent=`${action} failed: ${error.message||error}`;
        return {ok:false,code:"host-action-failed",message:String(error.message||error)};
      }finally{busy=false;}
    }
    panel.addEventListener("click",(event)=>{
      const btn=event.target?.closest?.("button[data-mcel-task-action]");
      if(btn)void execute(btn.dataset.mcelTaskAction);
    });
    async function scheduleRequest(endpoint,payload,label){
      if(busy)return;
      if(typeof global.taskManagerRequest!=="function"){
        status.textContent="Host schedule API unavailable.";return;
      }
      busy=true;status.textContent=label+" pending…";
      try{
        const receipt=await global.taskManagerRequest(endpoint,payload);
        if(receipt?.ok===false)throw new Error(receipt.message||"Schedule refused");
        status.textContent=receipt.message||label+" accepted";
        await global.refreshTaskManager?.();
      }catch(error){status.textContent=label+" failed: "+String(error.message||error);}
      finally{busy=false;}
    }
    addSchedule.addEventListener("click",()=>{
      if(!runAt.value){status.textContent="Schedule requires a date and time.";return;}
      const action=scheduleAction.value;
      if(action!=="server_status" &&
         (typeof global.confirm!=="function" || !global.confirm(`Schedule ${action} for ${runAt.value}?`)))return;
      void scheduleRequest("/api/applications/task/schedule/create",{
        action,run_at:new Date(runAt.value).toISOString(),note:note.value,payload:{}},"Schedule creation");
    });
    scheduleList.addEventListener("click",(event)=>{
      const del=event.target?.closest?.("button[data-mcel-task-schedule-delete]");
      if(!del)return;
      const id=del.dataset.mcelTaskScheduleDelete;
      if(typeof global.confirm!=="function"||!global.confirm(`Delete schedule ${id}?`))return;
      void scheduleRequest("/api/applications/task/schedule/delete",{schedule_id:id,
        expected_revision:scheduleRevisions.get(id)||""},"Schedule deletion");
    });
    function update(snapshot){
      if(!snapshot||typeof snapshot!=="object")return;
      const previous=select.value;
      observed=new Map();
      for(const process of [...(snapshot.processes||[]),...(snapshot.all_processes||[])]){
        const pid=Number(process?.pid);
        if(Number.isSafeInteger(pid)&&pid>0){
          observed.set(pid,{name:String(process.name||process.process_name||process.command||"process"),createdAt:String(process.created_at||"")});
        }
      }
      select.textContent="";
      const placeholder=doc.createElement("option");placeholder.value="";placeholder.textContent="Select observed process…";select.append(placeholder);
      for(const [pid,value] of observed){
        const option=doc.createElement("option");option.value=String(pid);option.textContent=`${pid} · ${value.name}`;select.append(option);
      }
      select.value=observed.has(Number(previous))?previous:"";
      scheduleList.textContent="";
      scheduleRevisions=new Map((snapshot.schedules||[]).map(item=>[item.id,item.revision||""]));
      for(const item of (snapshot.schedules||[])){
        const line=doc.createElement("div");line.className="task-actions";
        const detail=doc.createElement("span");detail.textContent=`${item.action} · ${item.run_at} · ${item.note||""}`;
        const del=doc.createElement("button");del.type="button";del.textContent="Delete";del.dataset.mcelTaskScheduleDelete=item.id;
        line.append(detail,del);scheduleList.append(line);
      }
    }
    return {update,execute,panel};
  }

  function mount({root, ir, search = "", snapshot = null} = {}) {
    const requestedMode = mode(search);
    if (!requestedMode) return refuse("not-opted-in");
    if (root && instances.has(root)) return refuse("already-mounted");
    if ((requestedMode === "candidate" || requestedMode === "application") &&
        (global.McelTaskManagerNotebookCandidate?.ir !== ir ||
        global.McelTaskManagerNotebookCandidate?.metadata?.schema !==
          "mcel.task-notebook-source-bound-candidate.v1" ||
        global.McelTaskManagerNotebookCandidate?.metadata?.status !==
          "new-candidate-not-historical-recovery")) return refuse("source-bound-candidate-missing");
    if (!root || root.id !== "task-manager-app" || !root.querySelector) return refuse("invalid-host");
    if (instances.has(root)) return refuse("already-mounted");
    if (!check(ir) || typeof global.McelViewTransition?.apply !== "function") return refuse("invalid-or-missing-ir");
    const grid = root.querySelector(".task-detail-grid");
    const legacy = grid?.querySelector(".task-notebook");
    if (!grid || !legacy) return refuse("missing-legacy-fallback");
    if (requestedMode === "lab" && ((ir.effects || []).length || (ir.capabilities || []).length))
      return refuse("lab-operational-authority-refused");
    const fullApp = requestedMode === "application";
    const originalShell = fullApp ? root.querySelector(":scope > .task-manager-shell") : null;
    if (fullApp && (!originalShell || !global.McelSemanticApplicationShell?.check))
      return refuse("full-application-shell-missing");
    if (fullApp) {
      try { global.McelSemanticApplicationShell.check(ir); }
      catch (_) { return refuse("full-application-shell-invalid"); }
    }
    const holder = root.ownerDocument.createElement("div");
    holder.className = "task-mcel-notebook-lab-host";
    holder.setAttribute("data-mcel-task-notebook-lab", requestedMode === "lab" ? "read-only" : "external-read");
    holder.setAttribute("aria-label", "MCEL notebook experimental read-only monitor");
    const header = root.ownerDocument.createElement("p");
    header.className = "task-mcel-notebook-lab-provenance";
    header.textContent = requestedMode !== "lab" ?
      "CANDIDATE · Authored DSL → compiled IR → source-bound read-only refresh · no machine mutations" :
      "LAB ONLY · Pinned IR witness · not the recovered Phase 3 notebook · no machine controls";
    const target = root.ownerDocument.createElement("div");
    target.className = "task-mcel-notebook-lab-render-target";
    if (!fullApp) holder.append(header, target);
    let workspace, operationRuntime, applicationShell, operationalControls;
    // Host form controls remain the actual existing backend-request inputs.
    // The generic shell owns generated field lifecycle; this adapter only maps
    // semantic values to the existing Task Manager capability interface.
    const legacyFields = fullApp ? {
      "query": root.querySelector("#task-query"),
      "limit": root.querySelector("#task-limit"),
      "include-connections": root.querySelector("#task-include-connections"),
      "auto-refresh": root.querySelector("#task-auto-refresh")
    } : null;
    const initialValues = {};
    if (legacyFields) for (const [id, el] of Object.entries(legacyFields))
      if (el) initialValues[id] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
    function setHostField(id, value) {
      const input = legacyFields?.[id];
      if (!input) return;
      if (input.type === "checkbox") input.checked = Boolean(value);
      else input.value = String(value);
      input.dispatchEvent(new Event("change", {bubbles:true}));
    }
    try {
      if (requestedMode !== "lab") {
        const adapters = {};
        // The host supplies only the existing safe read capability. The
        // generic executor owns the pending/effect/receipt lifecycle.
        const capId = "capability:task-manager-notebook.process-snapshot-refresh";
        if (typeof global.refreshTaskManager === "function") adapters[capId] = {
          refreshTaskManager: async () => {
            await global.refreshTaskManager();
            const delivery = active?.lastReceipt;
            return {ok:delivery?.ok === true,code:delivery?.code || "no-delivery-receipt",delivery};
          }
        };
        operationRuntime = global.McelIntentEffectRuntime.mount(ir, {
          adapters, observe: () => active?.version ?? 0
        });
      }
      if (fullApp) applicationShell = global.McelSemanticApplicationShell.mount(holder, ir, {
        // The URL selects only a general design profile. It grants no effect authority.
        presentation:(()=>{try {return new URLSearchParams(search).get("mcel_design")==="analytical"?
          "analytical":"operational";}catch(_){return "operational";}})(),
        planner:(()=>{try{return new URLSearchParams(search).get("mcel_planner")==="task-aware"?
          "task-aware":"legacy";}catch(_){return "legacy";}})(),
        slots:{notebook:target}, initialValues, onFieldChange:setHostField,
        effectRuntime:operationRuntime
      });
      // A single generated notebook is slotted into the generated application shell.
      workspace = global.McelTabWorkspace.mount(target, ir, {
        instanceId: "task-mcel-lab", status: "not-requested", syncRoute: false,
        renderActions:!fullApp, collections: global.McelTaskManagerProcessContract,
        transitions: global.McelViewTransition, effectRuntime: operationRuntime
      });
    } catch (_) {
      operationRuntime?.destroy(); applicationShell?.destroy();
      return refuse("renderer-refused");
    }
    const shellOriginal = fullApp ? {
      hidden:originalShell.hasAttribute("hidden"),inert:originalShell.hasAttribute("inert"),
      ariaHidden:originalShell.getAttribute("aria-hidden") } : null;
    const legacyOriginal = {
      hidden: legacy.hasAttribute("hidden"),
      inert: legacy.hasAttribute("inert"),
      ariaHidden: legacy.getAttribute("aria-hidden")
    };
    try {
      if (fullApp) root.insertBefore(holder, originalShell);
      else grid.insertBefore(holder, legacy);
      root.setAttribute("data-mcel-notebook-lab", "active");
      root.setAttribute("data-mcel-notebook-mode", requestedMode);
      if (fullApp) {
        originalShell.setAttribute("hidden", "");
        originalShell.setAttribute("inert", "");
        originalShell.setAttribute("aria-hidden", "true");
      } else if (requestedMode === "candidate") {
        // Only one accessible/presented notebook; retained legacy DOM is the
        // operational rollback surface and continues receiving host updates.
        legacy.setAttribute("hidden", "");
        legacy.setAttribute("inert", "");
        legacy.setAttribute("aria-hidden", "true");
      }
    } catch (_) {
      workspace.destroy(); operationRuntime?.destroy(); applicationShell?.destroy(); holder.remove();
      root.removeAttribute("data-mcel-notebook-lab");
      root.removeAttribute("data-mcel-notebook-mode");
      return refuse("host-attach-failed");
    }
    if (fullApp) operationalControls=installOperationsPanel(holder,root);
    const state = {root, holder, legacy, legacyOriginal, originalShell, shellOriginal, applicationShell,
      operationalControls, mode:requestedMode,
      candidateMetadata: requestedMode !== "lab" ?
        {...global.McelTaskManagerNotebookCandidate.metadata} : null,
      workspace, operationRuntime, version: 0, status: "not-requested",streams:{},lastReceipt:null};
    holder.dataset.mcelLabBuild=BUILD;
    instances.set(root, state);
    active = state;
    if (snapshot) publish(snapshot, "available", 1);
    return {ok: true, code: fullApp ? "mounted-semantic-application" :
      requestedMode === "candidate" ? "mounted-source-bound-candidate" : "mounted-lab-witness", effects: []};
  }
  function publish(snapshot,status="available",version,metadata={}) {
    const state=active;
    function fail(code,extra={}) {
      const response=record({ok:false,code,version:state?.version??0,build:BUILD,effects:[],...extra});
      if(state)state.lastReceipt=response;
      return response;
    }
    if(!state||!state.root.isConnected)return fail("not-mounted");
    if(!["loading","available","empty","failed"].includes(status))return fail("invalid-status");
    const next=version===undefined?state.version+1:version;
    if(!Number.isSafeInteger(next)||next<=state.version)return fail("stale-snapshot");
    if(!metadata||typeof metadata!=="object"||Array.isArray(metadata))return fail("invalid-metadata");
    const allowed=new Set(["available","loading","failed","empty","stale","not-requested"]);
    const streams=metadata.streams||{};
    if(!streams||typeof streams!=="object"||Array.isArray(streams)||
       Object.values(streams).some(v=>!allowed.has(v)))return fail("invalid-streams");
    const payload=status==="failed"||status==="loading" ?
      {processes:[],all_processes:[],connections:[],hardware:{}} :project(snapshot);
    if(!payload)return fail("invalid-snapshot");
    const outcome=state.workspace.updateSnapshot(payload,status,{streams});
    if(outcome.ok!==true||(outcome.effects||[]).length)
      return fail("renderer-update-refused",{cause:outcome.code||"unknown",message:outcome.message||""});
    if (state.applicationShell) {
      const aiBrief = state.root.querySelector("#task-ai-output")?.textContent || "No AI brief available";
      const shellPayload = status === "failed" ? {aiBrief} : {...snapshot, aiBrief};
      const shellReceipt = state.applicationShell.update(shellPayload, status);
      if (!shellReceipt.ok) return fail("shell-update-refused", {cause:shellReceipt.code});
    }
    state.operationalControls?.update(snapshot);
    state.version=next;state.status=status;state.streams={...streams};
    const partial=Object.values(streams).some(v=>v==="failed"||v==="stale");
    const response=record({ok:true,status:partial?"partial":status,version:next,build:BUILD,
      streams:state.streams,renderReceipts:outcome.streams||{},effects:[]});
    state.lastReceipt=response;
    state.holder.dataset.mcelLabSnapshotVersion=String(next);
    state.holder.dataset.mcelLabDelivery=partial?"partial":status;
    return response;
  }
  function unmount(root = active?.root) {
    const state = root && instances.get(root);
    if (!state) return refuse("not-mounted");
    state.operationRuntime?.destroy();
    state.workspace.destroy();
    state.applicationShell?.destroy();
    state.holder.remove();
    if (state.originalShell) {
      if (!state.shellOriginal.hidden)state.originalShell.removeAttribute("hidden");
      if (!state.shellOriginal.inert)state.originalShell.removeAttribute("inert");
      if (state.shellOriginal.ariaHidden===null)state.originalShell.removeAttribute("aria-hidden");
      else state.originalShell.setAttribute("aria-hidden",state.shellOriginal.ariaHidden);
    }
    if (state.mode === "candidate") {
      if (!state.legacyOriginal.hidden) state.legacy.removeAttribute("hidden");
      if (!state.legacyOriginal.inert) state.legacy.removeAttribute("inert");
      if (state.legacyOriginal.ariaHidden === null) state.legacy.removeAttribute("aria-hidden");
      else state.legacy.setAttribute("aria-hidden", state.legacyOriginal.ariaHidden);
    }
    state.root.removeAttribute("data-mcel-notebook-lab");
    state.root.removeAttribute("data-mcel-notebook-mode");
    instances.delete(root);
    if (active === state) active = null;
    return {ok: true, code: "legacy-restored", effects: []};
  }
  function status() {
    return active?{mounted:true,mode:active.mode === "application" ? "semantic-application" :
      active.mode === "candidate" ? "source-bound-candidate" : "lab-witness",
      source:active.candidateMetadata,build:BUILD,version:active.version,
      applicationShell:active.applicationShell?.getState() || null,
      dataStatus:active.status,streams:{...active.streams},selected:active.workspace.getState().selected,
      lastReceipt:active.lastReceipt, operation:active.operationRuntime?.status() || null,
      recentReceipts:journal.slice(-12),effects:[]}:
      {mounted:false,build:BUILD,recentReceipts:journal.slice(-12),effects:[]};
  }
  function optInHost(root, search = global.location?.search || "", snapshot = null) {
    const selected = mode(search);
    const ir = selected === "candidate" || selected === "application" ?
      global.McelTaskManagerNotebookCandidate?.ir : global.McelTaskManagerTabWitness;
    return mount({root, search, ir, snapshot});
  }
  global.McelTaskManagerNotebookLab = Object.freeze({allow, check, mount, publish, unmount, status, optInHost});
})(typeof window !== "undefined" ? window : globalThis);
