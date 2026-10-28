/* Lab-only, read-only Task Manager -> generic MCEL Tab Workspace bridge.
 * No fetches, timers, process actions, schedule actions, writes or host tab handlers.
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
  function allow(search) {
    try { return new URLSearchParams(String(search || "")).get("mcel_task_notebook") === "lab"; }
    catch (_) { return false; }
  }
  function check(ir) {
    if (!ir || ir.application?.appId !== "task-manager-notebook" ||
        (ir.effects || []).length || (ir.capabilities || []).length) return false;
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
  function mount({root, ir, search = "", snapshot = null} = {}) {
    if (!allow(search)) return refuse("not-opted-in");
    if (!root || root.id !== "task-manager-app" || !root.querySelector) return refuse("invalid-host");
    if (instances.has(root)) return refuse("already-mounted");
    if (!check(ir) || typeof global.McelViewTransition?.apply !== "function") return refuse("invalid-or-missing-ir");
    const grid = root.querySelector(".task-detail-grid");
    const legacy = grid?.querySelector(".task-notebook");
    if (!grid || !legacy) return refuse("missing-legacy-fallback");
    const holder = root.ownerDocument.createElement("div");
    holder.className = "task-mcel-notebook-lab-host";
    holder.setAttribute("data-mcel-task-notebook-lab", "read-only");
    holder.setAttribute("aria-label", "MCEL notebook experimental read-only monitor");
    const header = root.ownerDocument.createElement("p");
    header.className = "task-mcel-notebook-lab-provenance";
    header.textContent = "LAB ONLY · Pinned IR witness · not the recovered Phase 3 notebook · no machine controls";
    const target = root.ownerDocument.createElement("div");
    target.className = "task-mcel-notebook-lab-render-target";
    holder.append(header, target);
    let workspace;
    try {
      // Pure local selection; route synchronization OFF, no host events or action forwarding.
      workspace = global.McelTabWorkspace.mount(target, ir, {
        instanceId: "task-mcel-lab", status: "not-requested", syncRoute: false,
        collections: global.McelTaskManagerProcessContract,
        transitions: global.McelViewTransition
      });
    } catch (_) {
      return refuse("renderer-refused");
    }
    try { grid.insertBefore(holder, legacy); root.setAttribute("data-mcel-notebook-lab", "active"); }
    catch (_) { workspace.destroy(); holder.remove(); return refuse("host-attach-failed"); }
    const state = {root, holder, workspace, version: 0, status: "not-requested",streams:{},lastReceipt:null};
    holder.dataset.mcelLabBuild=BUILD;
    instances.set(root, state);
    active = state;
    if (snapshot) publish(snapshot, "available", 1);
    return {ok: true, code: "mounted-lab-witness", effects: []};
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
    state.workspace.destroy();
    state.holder.remove();
    state.root.removeAttribute("data-mcel-notebook-lab");
    instances.delete(root);
    if (active === state) active = null;
    return {ok: true, code: "legacy-restored", effects: []};
  }
  function status() {
    return active?{mounted:true,mode:"lab-witness",build:BUILD,version:active.version,
      dataStatus:active.status,streams:{...active.streams},selected:active.workspace.getState().selected,
      lastReceipt:active.lastReceipt, recentReceipts:journal.slice(-12),effects:[]}:
      {mounted:false,build:BUILD,recentReceipts:journal.slice(-12),effects:[]};
  }
  function optInHost(root, search = global.location?.search || "", snapshot = null) {
    return mount({root, search, ir: global.McelTaskManagerTabWitness, snapshot});
  }
  global.McelTaskManagerNotebookLab = Object.freeze({allow, check, mount, publish, unmount, status, optInHost});
})(typeof window !== "undefined" ? window : globalThis);
