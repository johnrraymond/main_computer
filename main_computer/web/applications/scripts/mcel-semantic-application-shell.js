/* MCEL source-driven application shell v1.
 * Generic layout + read state + local form + declared capability dispatch.
 * The host contributes data, initial input values and an optional input adapter;
 * it does not contribute generated markup, styles, selectors or action lifecycles.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.McelSemanticApplicationShell = api;
})(typeof globalThis !== "undefined" ? globalThis : null, function () {
  "use strict";
  const TYPES = new Set(["summary", "form", "slot", "collection", "notice"]);
  const INPUTS = new Set(["search", "text", "number", "checkbox"]);
  const safe = value => typeof value === "string" && /^(?!__proto__$|prototype$|constructor$)[a-z][a-z0-9_-]{0,63}$/i.test(value);
  const semanticId = value => typeof value === "string" && /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._:@/-]*$/.test(value);
  const fail = code => { throw new Error(`MCEL_APPLICATION_SHELL_${code}`); };
  const labelNode = (doc, tag, value, className) => {
    const el = doc.createElement(tag);
    if (className) el.className = className;
    el.textContent = String(value);
    return el;
  };
  function pathValue(input, path) {
    if (!input || typeof input !== "object") return undefined;
    if (typeof path !== "string" || !path || path.split(".").some(s => !safe(s))) return undefined;
    return path.split(".").reduce((o,k) => o && typeof o === "object" ? o[k] : undefined, input);
  }
  function check(ir) {
    const surfaces = (ir?.surfaces || []).filter(s => s?.semanticSurface?.applicationShell);
    if (surfaces.length !== 1) fail("EXACTLY_ONE_SHELL");
    const spec = surfaces[0].semanticSurface.applicationShell;
    if (spec.schema !== "mcel.semantic-application-shell.v1" ||
        spec.layout !== "sidebar-workspace" || !Array.isArray(spec.regions) ||
        spec.regions.length < 2 || spec.regions.length > 24 || !safe(spec.id)) fail("INVALID_SPEC");
    const ids = new Set(), slots = new Set(), fields = new Set(), actions = new Set();
    const allowedActions = new Set((ir.intents || []).map(intent => intent.id));
    for (const region of spec.regions) {
      if (!semanticId(region.id) || ids.has(region.id) || !TYPES.has(region.kind) ||
          !["sidebar", "main"].includes(region.zone) ||
          typeof region.title !== "string" || region.title.length > 100) fail("INVALID_REGION");
      ids.add(region.id);
      if (region.kind === "slot") {
        if (!safe(region.slot) || slots.has(region.slot)) fail("INVALID_SLOT");
        slots.add(region.slot);
      }
      if (region.kind === "summary" || region.kind === "notice") {
        const data = region.items || [];
        if (!Array.isArray(data) || data.length > 24 || data.some(item =>
            !safe(item.id) || (typeof item.label !== "string" || !item.label) ||
            typeof item.path !== "string" || item.path.split(".").some(s => !safe(s)))) fail("INVALID_DATA_BINDING");
      }
      if (region.kind === "collection" &&
          (typeof region.path !== "string" || region.path.split(".").some(s=>!safe(s)) ||
           !Array.isArray(region.columns) || !region.columns.length || region.columns.length > 12 ||
           region.columns.some(c=>!safe(c)) ||
           (region.rowKey !== undefined && !safe(region.rowKey)) ||
           (region.selectControl !== undefined && (!safe(region.selectControl) ||
             !(surfaces[0].semanticSurface.controls || []).some(c => c.id === region.selectControl && c.role === "capability-action"))))) fail("INVALID_COLLECTION");
      if (region.kind === "form") {
        if (!Array.isArray(region.fields) || region.fields.length > 20) fail("INVALID_FORM");
        for (const field of region.fields) {
          if (!safe(field.id) || fields.has(field.id) || !INPUTS.has(field.type) ||
              typeof field.label !== "string" || !field.label ||
              (field.type === "number" && (!Number.isFinite(field.min) || !Number.isFinite(field.max) || field.min > field.max))) fail("INVALID_FIELD");
          fields.add(field.id);
        }
      }
      for (const action of region.actions || []) {
        if (!safe(action.id) || actions.has(action.id) || typeof action.label !== "string" || !action.label ||
            (action.intent !== null && (!semanticId(action.intent) || !allowedActions.has(action.intent))) ||
            !["external-read", "not-admitted"].includes(action.authority) ||
            (action.authority === "not-admitted" && action.intent !== null) ||
            (action.authority === "external-read" && (
              !safe(action.control) || !(surfaces[0].semanticSurface.controls || []).some(c =>
                c.id === action.control && c.intent === (ir.intents || []).find(i => i.id === action.intent)?.sourceName)
            ))) fail("INVALID_ACTION");
        actions.add(action.id);
      }
    }
    if (!slots.has("notebook") && !spec.regions.some(r => r.kind === "collection" && r.zone === "main")) fail("MISSING_PRIMARY_REGION");
    return spec;
  }
  // Presentation is a generic rendering decision, never a domain permission or
  // application IR mutation. Region emphasis derives from semantic kind/zone.
  const PRESENTATIONS = Object.freeze(["operational", "analytical"]);
  function designPlan(spec, presentation="operational") {
    if (!PRESENTATIONS.includes(presentation)) fail("UNKNOWN_PRESENTATION");
    const regions = spec.regions.map(region => {
      const role = region.kind === "slot" || (region.kind === "collection" && region.zone === "main") ? "workspace" :
        region.kind === "collection" ? "records" :
        region.kind === "notice" ? "notice" :
        region.kind === "summary" ? "insight" :
        (region.actions || []).some(action => action.authority === "external-read") ? "controls" : "settings";
      // Complexity is deliberately tied to actual declared meaning, not names.
      const complexity = region.kind === "summary" || region.kind === "notice" ? (region.items || []).length :
        region.kind === "collection" ? region.columns.length :
        region.kind === "form" ? region.fields.length + (region.actions || []).length : 1;
      return Object.freeze({id:region.id, role, zone:region.zone, complexity,
        prominence:role === "workspace" ? "primary" : complexity >= 4 ? "high" : "normal"});
    });
    const workspaceCount=regions.filter(r=>r.role === "workspace").length;
    const sidebarComplexity=regions.filter(r=>r.zone === "sidebar").reduce((n,r)=>n+r.complexity,0);
    return Object.freeze({presentation,regions:Object.freeze(regions),workspaceCount,sidebarComplexity,
      sidebarWidth:sidebarComplexity >= 10 ? "wide" : "standard"});
  }
  // Patch 07A candidate: classify relationships from existing semantics and
  // characterize delivered content without changing author-owned IR.
  // No application identifiers or human productivity heuristics are consulted.
  function profileContent(spec, snapshot) {
    const result = {};
    for (const region of spec.regions) {
      if (region.kind !== "collection") continue;
      const data = pathValue(snapshot, region.path);
      if (!Array.isArray(data) || !data.length) continue;
      const count = data.length;
      const indices = new Set();
      for (let i=0;i<Math.min(12,count);i++) indices.add(i);
      for (let i=Math.max(0,count-12);i<count;i++) indices.add(i);
      if (count>24) for (let i=0;i<8;i++) indices.add(Math.floor((i+.5)*count/8));
      let maxLength = 0;
      for (const index of indices) {
        const row = data[index];
        if (!row || typeof row!=="object") continue;
        for (const column of region.columns) {
          const value=row[column];
          if (value===undefined || value===null || typeof value==="object") continue;
          maxLength=Math.max(maxLength,String(value).length);
        }
      }
      result[region.id] = Object.freeze({
        size:count<=8?"small":count<=40?"medium":"large",
        text:maxLength>=72?"long":maxLength>=30?"medium":"short",
        sampled:indices.size
      });
    }
    return Object.freeze(result);
  }
  function taskAwarePlan(spec, presentation, content={}) {
    const legacy=designPlan(spec,presentation);
    let selectedPrimary=false;
    const regions=spec.regions.map((region,index)=>{
      const prior=legacy.regions[index], profile=content[region.id]||null;
      const isCollection=region.kind==="collection";
      const selection=Boolean(region.selectControl);
      const planRole=region.kind==="slot"?"embedded-workspace":
        isCollection && region.zone==="main"?"primary-collection":
        isCollection && selection?"selectable-navigation":
        isCollection?"supporting-records":
        region.kind==="summary"?"context-summary":
        region.kind==="notice"?"status-notice":
        (region.actions||[]).length?"action-controls":"input-controls";
      const priority=prior.role==="workspace" && !selectedPrimary?"primary":
        region.zone==="main"?"supporting":"contextual";
      if (priority==="primary") selectedPrimary=true;
      const density=isCollection && (region.columns.length>=4 || profile?.size==="large")?"compact":
        isCollection && profile?.text==="long"?"readable":"comfortable";
      const extent=!isCollection?"none":
        (planRole==="primary-collection" && profile?.size!=="small")?"expanded":
        (profile?.size==="large" && region.zone==="sidebar")?"navigation":"standard";
      const reasons=[`semantic-kind:${region.kind}`,`declared-zone:${region.zone}`];
      if(selection)reasons.push("authorized-selection-control");
      if(region.kind==="slot")reasons.push("embedded-content-boundary");
      if(isCollection){
        reasons.push(`declared-columns:${region.columns.length}`);
        if(profile)reasons.push(`observed-size:${profile.size}`,`observed-text:${profile.text}`);
        else reasons.push("content-not-yet-observed");
      }
      return Object.freeze({...prior,planRole,priority,density,extent,
        reasons:Object.freeze(reasons)});
    });
    return Object.freeze({...legacy,planner:"task-aware",regions:Object.freeze(regions),
      contentLatched:Object.freeze(Object.keys(content).sort()),
      decisionVersion:"mcel.task-aware-design.v1"});
  }
  function mount(host, ir, options={}) {
    if (!host || typeof host.replaceChildren !== "function") fail("MISSING_HOST");
    const spec = check(ir), doc = host.ownerDocument;
    const planner=options.planner || "legacy";
    if(!["legacy","task-aware"].includes(planner)) fail("UNKNOWN_PLANNER");
    let contentLatch=Object.freeze({});
    let design=planner==="legacy"?designPlan(spec, options.presentation || "operational"):
      taskAwarePlan(spec,options.presentation || "operational",contentLatch);
    const slots = options.slots || {};
    for (const region of spec.regions) if (region.kind === "slot" && !slots[region.slot]) fail("SLOT_NOT_PROVIDED");
    const shell = labelNode(doc,"section","","mcel-application-shell");
    shell.textContent = "";
    shell.dataset.mcelGenerated = "semantic-application-shell-v1";
    shell.dataset.mcelPlanner=planner;
    shell.dataset.mcelPresentation = design.presentation;
    shell.dataset.mcelSidebarWidth = design.sidebarWidth;
    shell.setAttribute("aria-label",spec.label || ir.application?.title || "Application");
    const heading = doc.createElement("header");heading.className="mcel-application-shell__heading";
    heading.append(labelNode(doc,"h1",spec.label || ir.application?.title || "Application"));
    const headingTools=doc.createElement("div");headingTools.className="mcel-application-shell__heading-tools";
    headingTools.append(labelNode(doc,"span","Generated from MCEL semantic source","mcel-application-shell__origin"));
    const styleLabel=doc.createElement("label");styleLabel.className="mcel-application-shell__design-picker";
    styleLabel.append(labelNode(doc,"span","Presentation"));
    const styleSelect=doc.createElement("select");
    styleSelect.dataset.mcelPresentationPicker="";
    styleSelect.setAttribute("aria-label","Application presentation");
    for(const [value,label] of [["operational","Operational console"],["analytical","Analytical workspace"]]) {
      const option=doc.createElement("option");option.value=value;option.textContent=label;
      styleSelect.append(option);
    }
    styleSelect.value=design.presentation;
    styleLabel.append(styleSelect);headingTools.append(styleLabel);heading.append(headingTools);
    shell.append(heading);
    const layout = doc.createElement("div");layout.className="mcel-application-shell__layout";
    const columns = {};
    for (const zone of ["sidebar","main"]) {
      columns[zone] = doc.createElement("div");
      columns[zone].className = `mcel-application-shell__${zone}`;
      layout.append(columns[zone]);
    }
    shell.append(layout);
    const nodes = new Map(), inputNodes = new Map(), buttons = new Map(), collectionSignatures = new Map();
    const selectableRecords=new WeakMap();
    let model={}, disposed=false, form={...options.initialValues}, unsubscribe=null;
    for (const region of spec.regions) {
      const card = doc.createElement("section");card.className="mcel-application-shell__region";
      card.dataset.mcelRegion=region.id;
      const semanticDesign=design.regions.find(item=>item.id===region.id);
      card.dataset.mcelRole=semanticDesign.role;
      card.dataset.mcelProminence=semanticDesign.prominence;
      if (region.title) card.append(labelNode(doc,"h2",region.title));
      const content = doc.createElement("div");content.className="mcel-application-shell__body";
      card.append(content);columns[region.zone].append(card);
      nodes.set(region.id,{region,content,card});
      if (region.kind === "slot") content.append(slots[region.slot]);
      if (region.kind === "summary" || region.kind === "notice") {
        const dl = doc.createElement("dl");dl.className="mcel-application-shell__facts";
        for (const item of region.items || []) {
          const dt=labelNode(doc,"dt",item.label), dd=labelNode(doc,"dd","—");
          dd.dataset.mcelBinding=item.id;
          dl.append(dt,dd);
        }
        content.append(dl);
      }
      if (region.kind === "collection") {
        const frame=doc.createElement("div");frame.className="mcel-application-shell__table-scroll";
        frame.tabIndex=0;frame.append(labelNode(doc,"p","No data loaded","mcel-application-shell__empty"));
        content.append(frame);
      }
      if (region.kind === "form") {
        const group=doc.createElement("div");group.className="mcel-application-shell__fields";
        for (const field of region.fields) {
          const wrapper=doc.createElement("label");wrapper.className="mcel-application-shell__field";
          const caption=labelNode(doc,"span",field.label);wrapper.append(caption);
          const input=doc.createElement("input");input.type=field.type;
          input.id=`mcel-shell-${spec.id}-${field.id}`;
          input.dataset.mcelField=field.id;
          if (field.type === "number") {input.min=String(field.min);input.max=String(field.max);}
          if (field.placeholder) input.placeholder=String(field.placeholder);
          if (field.type === "checkbox") input.checked=Boolean(form[field.id] ?? field.initial);
          else input.value=String(form[field.id] ?? field.initial ?? "");
          input.addEventListener(field.type==="checkbox"?"change":"input",()=>{
            if(disposed)return;
            const value=field.type==="checkbox"?input.checked:field.type==="number"?
              Math.min(field.max,Math.max(field.min,Number(input.value)||field.min)):input.value;
            form[field.id]=value;
            options.onFieldChange?.(field.id,value);
          });
          wrapper.append(input);group.append(wrapper);inputNodes.set(field.id,input);
        }
        content.append(group);
      }
      if ((region.actions||[]).length) {
        const actionBar=doc.createElement("div");actionBar.className="mcel-application-shell__actions";
        for (const action of region.actions) {
          const button=labelNode(doc,"button",action.label);button.type="button";
          button.dataset.mcelShellAction=action.id;
          if(action.authority==="not-admitted"){
            button.disabled=true;button.title="Not admitted: capability has no authorized semantic effect";
          }else{
            button.disabled=!options.effectRuntime?.available(action.control);
            button.addEventListener("click",()=>{if(!disposed)void options.effectRuntime?.dispatch(action.control);});
          }
          buttons.set(action.id,{button,action});actionBar.append(button);
        }
        content.append(actionBar);
      }
    }
    function applyDesign(next) {
      // Pure attribute and style updates. Never replace/reappend cards or rows.
      design=next;
      shell.dataset.mcelPresentation=next.presentation;
      shell.dataset.mcelSidebarWidth=next.sidebarWidth;
      for (const item of next.regions) {
        const node=nodes.get(item.id);
        if(!node)continue;
        node.card.dataset.mcelRole=item.role;
        node.card.dataset.mcelProminence=item.prominence;
        if(planner==="task-aware"){
          node.card.dataset.mcelPlanRole=item.planRole;
          node.card.dataset.mcelPlanPriority=item.priority;
          node.card.dataset.mcelPlanDensity=item.density;
          node.card.dataset.mcelPlanExtent=item.extent;
        }
      }
      sizeCollections();
    }
    function sizeCollections() {
      if (planner!=="task-aware")return;
      // Measure the embedded shell, NEVER the browser viewport. The table
      // frame remains its scroll owner and the shell retains its own scroll.
      const height=shell.getBoundingClientRect().height;
      if(!Number.isFinite(height)||height<180)return;
      const expanded=Math.max(220,Math.min(620,Math.round(height*.58)));
      const navigation=Math.max(160,Math.min(380,Math.round(height*.40)));
      shell.style.setProperty("--mcel-plan-expanded-height",expanded+"px");
      shell.style.setProperty("--mcel-plan-navigation-height",navigation+"px");
    }
    const onHostResize=()=>{if(!disposed)sizeCollections();};
    doc.defaultView?.addEventListener("resize",onHostResize);
    const resizeObserver=typeof ResizeObserver!=="undefined"?new ResizeObserver(onHostResize):null;
    resizeObserver?.observe(host);
    function setPresentation(next) {
      if(disposed) return {ok:false,code:"destroyed"};
      if(!PRESENTATIONS.includes(next)) return {ok:false,code:"unknown-presentation"};
      applyDesign(planner==="task-aware"?taskAwarePlan(spec,next,contentLatch):designPlan(spec,next));
      styleSelect.value=next;
      // No DOM remount: fields, process rows, focus, and effect runtime survive.
      return {ok:true,code:"presentation-changed",presentation:next};
    }
    styleSelect.addEventListener("change",()=>{setPresentation(styleSelect.value);});
    const operation=labelNode(doc,"p","","mcel-application-shell__operation");
    operation.setAttribute("role","status");operation.setAttribute("aria-live","polite");
    shell.append(operation);
    host.replaceChildren(shell);
    if(planner==="task-aware")applyDesign(design);
    if(options.effectRuntime)unsubscribe=options.effectRuntime.subscribe(s=>{
      if(disposed)return;
      for(const {button,action} of buttons.values())if(action.authority!=="not-admitted")
        button.disabled=s.pending||!options.effectRuntime.available(action.control);
      operation.textContent=s.phase==="pending"?"Operation pending…":s.receipt?`Operation ${s.phase}: ${s.receipt.code}`:"";
      operation.dataset.mcelOperationPhase=s.phase;
    });
    function update(snapshot,status="available") {
      if(disposed)return {ok:false,code:"destroyed"};
      if(!snapshot || typeof snapshot!=="object" || Array.isArray(snapshot))return {ok:false,code:"invalid-data"};
      model=snapshot;
      if(planner==="task-aware"){
        const measured=profileContent(spec,snapshot);
        const additions=Object.entries(measured).filter(([id])=>!contentLatch[id]);
        // One meaningful assessment per collection per mount, never relayout
        // after ordinary live telemetry crosses a count or text threshold.
        if(additions.length){
          contentLatch=Object.freeze({...contentLatch,...Object.fromEntries(additions)});
          applyDesign(taskAwarePlan(spec,design.presentation,contentLatch));
        }
      }
      for(const {region,content} of nodes.values()) {
        if(region.kind==="summary" || region.kind==="notice"){
          for(const item of region.items||[]){
            const value=pathValue(snapshot,item.path);
            const text=value===undefined||value===null?"—":typeof value==="object"?"—":String(value);
            const node=content.querySelector(`[data-mcel-binding="${item.id}"]`);
            if(node && node.textContent!==text) node.textContent=text;
          }
        }
        if(region.kind==="collection"){
          const frame=content.firstElementChild;
          const data=pathValue(snapshot,region.path);
          // Semantic collections must faithfully represent every delivered record.
          // A silent fixed-size slice loses valid source entries and makes
          // selected-record identity and parity observably incorrect. If a
          // future collection needs pagination or virtualization, that must
          // be an explicit, source-visible contract rather than data loss.
          const rows=Array.isArray(data)?data:[];
          // Stable collections must not remount on ordinary live telemetry ticks.
          const normalized=rows.map(row=>region.columns.map(col=>{
            const value=row&&typeof row==="object"?row[col]:undefined;
            return value===undefined||value===null?"—":typeof value==="object"?"—":String(value);
          }));
          // Identity is part of collection state, even when two different
          // records happen to render identical cell text. Omitting it leaves
          // stale selection targets and violates the authored rowKey contract.
          const keys=region.rowKey?rows.map(r=>r && r[region.rowKey] !== undefined &&
            r[region.rowKey] !== null?String(r[region.rowKey]):null):[];
          const keyed=Boolean(region.rowKey) && keys.every(k=>k!==null) &&
            new Set(keys).size===keys.length;
          const signature=JSON.stringify([normalized,region.rowKey?keys:null,status]);
          if (collectionSignatures.get(region.id)===signature) continue;
          collectionSignatures.set(region.id,signature);
          let table=frame.querySelector("table"), body=table?.querySelector("tbody");
          if(!table || !body){
            table=doc.createElement("table");const head=doc.createElement("thead"),hr=doc.createElement("tr");
            for(const col of region.columns)hr.append(labelNode(doc,"th",col));
            head.append(hr);table.append(head);body=doc.createElement("tbody");table.append(body);
          }
          const previous=keyed?new Map([...body.children].filter(tr=>tr.dataset.mcelRowKey !== undefined)
            .map(tr=>[tr.dataset.mcelRowKey,tr])):new Map();
          const ordered=[];
          for(const [index,cells] of normalized.entries()){
            const record=rows[index];const key=keyed?keys[index]:null;
            const tr=(keyed&&previous.get(key))||doc.createElement("tr");
            if(keyed)tr.dataset.mcelRowKey=key;
            else delete tr.dataset.mcelRowKey;
            for(const [columnIndex,value] of cells.entries()){
              let td=tr.children[columnIndex];
              if(!td){td=doc.createElement("td");tr.append(td);}
              if(columnIndex===0 && region.selectControl){
                let select=td.querySelector("button[data-mcel-row-select]");
                if(!select){
                  select=labelNode(doc,"button",value,"mcel-application-shell__row-action");
                  select.type="button";select.dataset.mcelRowSelect=region.id;
                  select.addEventListener("click",()=>{
                    if(disposed || !options.effectRuntime?.available(region.selectControl))return;
                    const latest=selectableRecords.get(select);
                    if(!latest)return;
                    const allowed=options.onCollectionSelect?.(region.id,{...latest.record},latest.index);
                    if(allowed!==false)void options.effectRuntime.dispatch(region.selectControl);
                  });
                  td.replaceChildren(select);
                }
                select.disabled=!options.effectRuntime?.available(region.selectControl);
                if(select.textContent!==value)select.textContent=value;
                selectableRecords.set(select,{record,index});
              }else if(td.textContent!==value)td.textContent=value;
            }
            while(tr.children.length>cells.length)tr.lastElementChild.remove();
            ordered.push(tr);
          }
          // Avoid detaching surviving rows. Their focus survives value-only updates.
          for(const [index,tr] of ordered.entries()){
            if(body.children[index]!==tr)body.insertBefore(tr,body.children[index]||null);
          }
          while(body.children.length>ordered.length)body.lastElementChild.remove();
          if(rows.length){if(frame.firstElementChild!==table)frame.replaceChildren(table);}
          else frame.replaceChildren(labelNode(doc,"p",status==="failed"?
            "Snapshot unavailable":"No data loaded","mcel-application-shell__empty"));
        }
      }
      return {ok:true,code:"shell-updated"};
    }
    return Object.freeze({update,setPresentation,getState:()=>({form:{...form},disposed,
      presentation:design.presentation,design}),
      destroy:()=>{disposed=true;resizeObserver?.disconnect();
        doc.defaultView?.removeEventListener("resize",onHostResize);
        unsubscribe?.();host.replaceChildren();}});
  }
  return Object.freeze({check,designPlan,profileContent,taskAwarePlan,mount});
});
