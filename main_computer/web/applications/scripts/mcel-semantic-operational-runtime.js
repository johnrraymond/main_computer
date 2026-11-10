"use strict";
/* MCEL Phase 2B — semantic operational runtime, experimental opt-in consumer.
 * Uses canonical IR only, not app-specific names, host-bound markup, or global
 * capability lookup. Capability authority is delegated to the explicit host port.
 * No HTML string injection, eval, or implicit filesystem/network access.
 */
(function (global) {
  const own = (x, k) => Object.prototype.hasOwnProperty.call(x, k);
  const fail = (why) => { throw Error("MCEL_OPERATIONAL_REFUSED:" + why); };
  const assert = (ok, why) => { if (!ok) fail(why); };
  const ref = (x) => x && typeof x.ref === "string" ? x.ref : "";
  const fmt = (s) => String(s || "").replace(/^(?:surface-node:|interface-region:|intent:|state:)/, "").split(".").pop().replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[-_]/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  const clone = (x) => x === undefined ? undefined : JSON.parse(JSON.stringify(x));
  function checkSchema(schema, x, path) {
    assert(schema && typeof schema === "object", "missing-schema:" + path);
    switch (schema.kind) {
      case "string":
        assert(typeof x === "string", "type:" + path);
        if (own(schema, "minLength")) assert(x.length >= schema.minLength, "min-length:" + path);
        if (schema.enum) assert(Array.isArray(schema.enum) && schema.enum.includes(x), "enum:" + path);
        return;
      case "integer":
        assert(Number.isSafeInteger(x), "integer:" + path);
        if (own(schema, "minimum")) assert(x >= schema.minimum, "minimum:" + path);
        if (own(schema, "maximum")) assert(x <= schema.maximum, "maximum:" + path);
        return;
      case "number":
        assert(typeof x === "number" && Number.isFinite(x), "number:" + path); return;
      case "boolean": assert(typeof x === "boolean", "boolean:" + path); return;
      case "record":
        assert(x && typeof x === "object" && !Array.isArray(x), "record:" + path);
        const fields = schema.fields || {};
        assert(Object.keys(x).length === Object.keys(fields).length, "record-fields:" + path);
        for (const [k, shape] of Object.entries(fields)) {
          assert(own(x, k), "missing-field:" + path + "." + k);
          checkSchema(shape, x[k], path + "." + k);
        }
        return;
      case "list":
        assert(Array.isArray(x), "list:" + path);
        const keys = new Set();
        for (const item of x) {
          checkSchema(schema.items, item, path + "[]");
          if (schema.key) {
            assert(item && (typeof item[schema.key] === "string" || Number.isSafeInteger(item[schema.key])), "key:" + path);
            const key = String(item[schema.key]);
            assert(!keys.has(key), "duplicate-key:" + path); keys.add(key);
          }
        }
        return;
      default: fail("unsupported-schema:" + path + ":" + schema.kind);
    }
  }
  function index(items, kind) {
    const map = new Map();
    for (const item of items || []) {
      assert(item && typeof item.id === "string" && !map.has(item.id), "duplicate-or-invalid:" + kind);
      map.set(item.id, item);
    }
    return map;
  }
  function validateVisibility(when, states, modeStates, depth=0) {
    assert(when && typeof when==="object" && !Array.isArray(when) && depth<=4,
      "visibility-invalid");
    const keys=Object.keys(when);
    if(keys.length===1 && (keys[0]==="all" || keys[0]==="any")){
      const clauses=when[keys[0]];
      assert(Array.isArray(clauses) && clauses.length>=1 && clauses.length<=12,"visibility-compound-invalid");
      for(const clause of clauses)validateVisibility(clause,states,modeStates,depth+1);
      return;
    }
    assert(keys.every(k=>["state","path","equals","maxAgeSeconds"].includes(k)) &&
      own(when,"state") && own(when,"equals"),"visibility-fields-invalid");
    const s=states.get(ref(when.state));
    assert(s && ["derived","renderer-local"].includes(s.authority),"visibility-state-invalid");
    let schema=s.schema;
    if(own(when,"maxAgeSeconds"))assert(s.authority==="derived" &&
      s.schema.kind==="record" && s.schema.fields?.observedAt?.kind==="string" &&
      Number.isSafeInteger(when.maxAgeSeconds) && when.maxAgeSeconds>=1 && when.maxAgeSeconds<=600,
      "visibility-age-invalid");
    if(own(when,"path")){
      assert(typeof when.path==="string" && schema?.kind==="record" &&
        own(schema.fields||{},when.path),"visibility-path-invalid");
      schema=schema.fields[when.path];
    }
    const v=when.equals;
    assert((schema?.kind==="string" && typeof v==="string" &&
      (!schema.enum || schema.enum.includes(v))) ||
      (schema?.kind==="boolean" && typeof v==="boolean") ||
      (schema?.kind==="integer" && Number.isSafeInteger(v)),"visibility-type-invalid");
    if(s.authority==="renderer-local")assert(modeStates.has(s.id) &&
      !own(when,"path") && s.schema.kind==="string" && s.schema.enum?.includes(v),
      "visibility-view-invalid");
  }
  function contract(ir, composition) {
    assert(ir && ir.schema === "mcel.application-ir.v1", "ir-schema");
    assert(ir.application && typeof ir.application.appId === "string", "application");
    const states = index(ir.states, "state"), intents = index(ir.intents, "intent");
    const effects = index(ir.effects, "effect"), capabilities = index(ir.capabilities, "capability");
    assert((ir.surfaces || []).length === 1, "single-semantic-surface-required");
    const surface = ir.surfaces[0];
    const generated = composition !== undefined;
    if (generated) {
      assert(!surface.interfaceIntent?.hierarchy && !surface.htmlBindings && !(ir.layouts || []).length,
        "generated-layout-source-not-independent");
      assert(composition && composition.schema === "mcel.generated-composition.v1" &&
        ["mcel.semantic-structure.p1","mcel.semantic-structure.p3"].includes(composition.policy) &&
        composition.sourceIrHash === ir.fingerprints?.semantic &&
        composition.purpose === surface.purpose &&
        Array.isArray(composition.regions) && Array.isArray(composition.variants) &&
        Array.isArray(composition.relations) && composition.relations.length === 0 &&
        Array.isArray(composition.unresolved) && composition.unresolved.length === 0,
        "generated-composition-invalid");
      const tags={root:"main",queries:"section",collection:"section","selection-echo":"aside",
        evidence:"footer",results:"section",controls:"section",operations:"section",document:"section"};
      const allowedPurposes=new Set(["monitor","compare","inspect","navigate","manage"]);
      assert(allowedPurposes.has(composition.purpose),"generated-purpose-invalid");
      const usedRecipes=new Set();
      const modeStates=new Set([...intents.values()].filter(i=>i.viewTransition)
        .map(i=>ref(i.viewTransition.target)));
      for (const n of surface.nodes || []) if (n.visibleWhen)
        validateVisibility(n.visibleWhen,states,modeStates);
      for (const r of composition.regions) {
        const expectedTasks=composition.policy==="mcel.semantic-structure.p3" &&
          composition.purpose==="monitor" && ["collection","results"].includes(r.recipe)
          ? ["monitor"] : [];
        assert(r && tags[r.recipe] === r.nativeTag &&
          (!usedRecipes.has(r.recipe)) && Array.isArray(r.members) &&
          Array.isArray(r.tasks) && JSON.stringify(r.tasks)===JSON.stringify(expectedTasks) &&
          Array.isArray(r.reasons) && r.reasons.length > 0 &&
          r.id === "interface-region:generated-" + r.recipe,
          "generated-region-invalid");
        usedRecipes.add(r.recipe);
      }
      assert(usedRecipes.has("root") && usedRecipes.has("collection") &&
        composition.rootId === "interface-region:generated-root", "generated-root-invalid");
      const permittedRisks=composition.policy==="mcel.semantic-structure.p3"
        ? ["local-state","external-read","consequential"] : ["local-state","external-read"];
      const capabilityRisks=composition.policy==="mcel.semantic-structure.p3"
        ? ["external-read","consequential"] : ["external-read"];
      assert([...intents.values()].every(i=>permittedRisks.includes(i.risk)) &&
        [...capabilities.values()].every(c=>capabilityRisks.includes(c.risk)),"generated-authority-invalid");
    } else {
      assert(surface.interfaceIntent?.hierarchy && Array.isArray(surface.interfaceIntent.regions), "paired-html-hierarchy-required");
    }
    const nodes = index(surface.nodes, "surface-node");
    const regions = index(generated ? composition.regions : surface.interfaceIntent.regions, "region");
    const rootId = generated ? composition.rootId : surface.interfaceIntent.hierarchy.rootId;
    if (generated) {
      const expected={form:"queries",collection:"collection",control:"controls",
        "operation-evidence":"evidence","document-editor":"document"};
      const selected=new Set();
      for(const n of nodes.values())if(n.nodeKind === "collection" && n.selectionIntent){
        const i=intents.get(ref(n.selectionIntent));
        if(i?.collectionSelection && ref(i.collectionSelection.source)===ref(n.state))
          selected.add(ref(i.collectionSelection.target));
      }
      for(const r of regions.values())for(const binding of r.members){
        const n=nodes.get(ref(binding));
        assert(n,"generated-member-unknown");
        const referenced=n.intent && intents.get(ref(n.intent));
        const recipe=composition.policy==="mcel.semantic-structure.p3" &&
          ["form","control"].includes(n.nodeKind) && referenced?.risk==="consequential"
          ? "operations" : n.nodeKind==="state-value"
          ? (selected.has(ref(n.value?.state)) ? "selection-echo" : "results") : expected[n.nodeKind];
        assert(recipe===r.recipe,"generated-recipe-incompatible:"+n.id);
      }
    }
    assert(regions.has(rootId), "root-missing");
    const roots = [...regions.values()].filter(r => r.parentId === null);
    assert(roots.length === 1 && roots[0].id === rootId, "single-root-required");
    const members = new Set();
    const children = new Map([...regions.keys()].map(k => [k, []]));
    for (const region of regions.values()) {
      if (region.id !== rootId) {
        assert(regions.has(region.parentId), "orphan-region:" + region.id);
        children.get(region.parentId).push(region);
      }
      for (const binding of region.members || []) {
        const id = ref(binding);
        assert(nodes.has(id) && !members.has(id), "unknown-or-repeated-member:" + id);
        members.add(id);
      }
    }
    assert(members.size === nodes.size, "unbound-surface-node");
    const visited = new Set(), visiting = new Set();
    function visit(id) {
      assert(!visiting.has(id), "region-cycle");
      if (visited.has(id)) return;
      visiting.add(id);
      for (const r of children.get(id)) visit(r.id);
      visiting.delete(id); visited.add(id);
    }
    visit(rootId); assert(visited.size === regions.size, "unreachable-region");
    for (const arr of children.values()) {
      const ord = new Set();
      for (const r of arr) { assert(Number.isSafeInteger(r.ordinal) && !ord.has(r.ordinal), "duplicate-ordinal"); ord.add(r.ordinal); }
      arr.sort((a,b) => a.ordinal - b.ordinal);
    }
    for (const r of regions.values()) {
      if (r.visibleWhen) {
        const s=states.get(ref(r.visibleWhen.state));
        assert(s?.authority === "renderer-local" && s.schema?.kind === "string" &&
          Array.isArray(s.schema.enum) && s.schema.enum.includes(r.visibleWhen.equals),
          "invalid-region-visibility:" + r.id);
      }
    }
    for (const n of nodes.values()) {
      if (n.nodeKind === "form") {
        for(const [field,stateRef] of Object.entries(n.fieldState||{})){
          const state=states.get(ref(stateRef));
          assert(own(n.fields||{},field) && state?.authority==="renderer-local" &&
            JSON.stringify(state.schema)===JSON.stringify(n.fields[field]),"invalid-form-state:"+n.id);
        }
        for(const item of Object.values(n.acceptResult||{})){
          const source=states.get(ref(item.state));
          const target=n.fields?.[item.field];
          const picked=item.pick?source?.schema?.fields?.[item.pick]:source?.schema;
          assert(source?.authority==="derived" && target &&
            JSON.stringify(picked)===JSON.stringify(target),"invalid-form-result:"+n.id);
          if(item.whenIntent){
            const writer=intents.get(ref(item.whenIntent));
            assert(writer?.operationKind==="capability" && writer.resultBindings?.some(binding=>
              ref(binding.target)===ref(item.state)),"invalid-form-result-source:"+n.id);
          }
          if(item.invoke){
            const action=intents.get(ref(item.invoke));
            assert(action?.operationKind==="capability" && action.input?.length===0 &&
              action.resultBindings?.length,"invalid-form-result-invoke:"+n.id);
          }
        }
        if(n.clearIntent){
          const clear=intents.get(ref(n.clearIntent));
          assert(clear?.operationKind==="capability" && clear.input?.length===0,"invalid-form-clear:"+n.id);
        }
        if(n.editor){
          const editor=intents.get(ref(n.editor.intent));
          assert(own(n.fields||{},"expression") && editor?.operationKind==="capability" &&
            ["expression","action","token","selectionStart","selectionEnd"].every(key=>editor.input?.some(input=>input.sourceName===key)) &&
            editor.input.length===5 && Array.isArray(n.editor.templates) &&
            n.editor.templates.length<=32,"invalid-form-editor:"+n.id);
        }
      }
      if (n.nodeKind === "form" && n.defaults) {
        for (const [field,value] of Object.entries(n.defaults)) {
          assert(Object.prototype.hasOwnProperty.call(n.fields || {},field), "undeclared-form-default:" + field);
          checkSchema(n.fields[field],value,n.id + ".defaults." + field);
        }
      }
      assert(["form","control","state-value","collection","plot","operation-evidence","document-editor","embedded-chat"].includes(n.nodeKind), "unsupported-surface-node:" + n.nodeKind);
      if (n.intent) assert(intents.has(ref(n.intent)), "unbound-intent:" + n.id);
      for(const item of n.resetOutputs||[]){
        const target=states.get(ref(item.state));
        assert(n.nodeKind==="control" && target?.authority==="derived" &&
          target.schema?.kind==="string" && typeof item.value==="string" &&
          item.value.length<=4096,"invalid-control-reset:"+n.id);
      }
      if (n.state) assert(states.has(ref(n.state)), "unbound-state:" + n.id);
      if (n.selectionIntent) assert(intents.has(ref(n.selectionIntent)), "unbound-selection:" + n.id);
      if (n.nodeKind === "state-value") assert(states.has(ref(n.value?.state)), "unbound-display:" + n.id);
      if (n.nodeKind === "document-editor") {
        const source=states.get(ref(n.contentState)), path=states.get(ref(n.path)), draft=states.get(ref(n.draft));
        assert(source?.schema?.kind==="string" && source.authority==="derived" &&
          path?.schema?.kind==="string" && path.authority==="renderer-local" &&
          draft?.schema?.kind==="string" && draft.authority==="renderer-local", "document-state-invalid:" + n.id);
        const load=intents.get(ref(n.loadIntent)), save=n.saveIntent && intents.get(ref(n.saveIntent));
        assert(load?.operationKind==="capability" && load.resultBindings?.some(b=>ref(b.target)===source.id),
          "document-load-invalid:"+n.id);
        if(n.saveIntent) assert(save?.operationKind==="capability" && save.risk==="source-write" &&
          save.requestArguments?.some(x=>ref(x.state)===path.id) &&
          save.requestArguments?.some(x=>ref(x.state)===draft.id),"document-save-invalid:"+n.id);
      }
      if (n.nodeKind === "embedded-chat") {
        const intent=intents.get(ref(n.intent));
        assert(intent?.operationKind==="capability" && intent.risk==="external-read" &&
          typeof n.title==="string" && typeof n.targetKind==="string" &&
          typeof n.targetId==="string","embedded-chat-contract-invalid:"+n.id);
      }
      if (n.nodeKind === "plot") {
        const schema = states.get(ref(n.state))?.schema;
        assert(schema?.kind === "list" && schema.items?.kind === "record" &&
          schema.items.fields?.[n.x]?.kind === "number" &&
          schema.items.fields?.[n.y]?.kind === "number" && n.x !== n.y,
          "invalid-plot-schema:" + n.id);
      }
    }
    for (const i of intents.values()) {
      if (i.operationKind !== "capability") continue;
      assert(i.effectRefs?.length === 1, "single-effect-required:" + i.id);
      const e = effects.get(ref(i.effectRefs[0]));
      assert(e?.effectKind === "capability-request" && ref(e.owner) === i.id, "invalid-effect:" + i.id);
      const cap = capabilities.get(ref(e.authority));
      assert(cap && ref(e.target) === cap.id && e.risk === i.risk, "invalid-capability:" + i.id);
      const op = (cap.operations || []).find(o => o.name === i.operation && o.runtimeMethod === i.runtimeMethod);
      assert(op && op.risk === i.risk, "invalid-operation:" + i.id);
      for (const binding of i.resultBindings || []) {
        const target = states.get(ref(binding.target));
        assert(binding.source === "capability-response" && target?.authority === "derived", "invalid-result-binding:" + i.id);
        assert(op.resultSchema && JSON.stringify(op.resultSchema) === JSON.stringify(target.schema), "result-schema-mismatch:" + i.id);
      }
    }
    return {states,intents,effects,capabilities,nodes,regions,children,rootId,generated};
  }
  // Presentation-only contract. This never changes IR authority, effect paths,
  // or authored visibility. Enforce coverage before touching the DOM.
  function workspaceContract(ir, c, plan) {
    if (plan === undefined) return null;
    assert(!c.generated && plan && plan.schema === "mcel.workspace-plan.v1" &&
      plan.policy === "mcel.workspace-planning.v1" && plan.runtimeEnabled === true &&
      plan.scheme?.resolved === "workspace_v1" &&
      plan.sourceIrHash === ir.fingerprints?.semantic && plan.rootId === c.rootId &&
      typeof plan.fingerprint === "string" && /^sha256:[a-f0-9]{64}$/.test(plan.fingerprint),
      "workspace-plan-version-or-source");
    assert(Array.isArray(plan.zones) && plan.zones.length > 0, "workspace-plan-zones");
    const kinds = {controls:"toolbar", navigator:"dock", workspace:"protected-anchor",
      tools:"tab-group", status:"pinned-compact"};
    const names = new Set(), covered = new Set();
    for (const zone of plan.zones) {
      assert(zone && own(kinds, zone.id) && !names.has(zone.id) &&
        (zone.id === "tools" ? ["tab-group","inspector"].includes(zone.kind) : zone.kind === kinds[zone.id]) &&
        Array.isArray(zone.members) && zone.members.length > 0 &&
        zone.ladder && ["wide", "medium", "narrow"].every(k=>typeof zone.ladder[k] === "string"),
        "workspace-zone-invalid");
      names.add(zone.id);
      for (const id of zone.members) {
        assert(typeof id === "string" && c.regions.has(id) && id !== c.rootId && !covered.has(id),
          "workspace-region-unknown-or-repeated");
        const region = c.regions.get(id);
        assert(zone.id === "navigator" ? region.purpose === "navigation" :
          zone.id === "status" ? region.purpose === "status" :
          zone.id === "controls" ? ["query-controls","mode-control"].includes(region.purpose) :
          zone.id === "workspace" ? region.priority === "primary" || !!region.visibleWhen :
          region.priority !== "primary" && !["status","navigation","query-controls","mode-control"].includes(region.purpose),
          "workspace-role-disagreement");
        covered.add(id);
      }
      if(zone.id === "tools")
        assert(zone.members.includes(zone.default) &&
          (zone.members.length > 1 ? zone.kind === "tab-group" : zone.kind === "inspector"),
          "workspace-tool-default");
    }
    assert(names.has("workspace") && covered.size === c.regions.size - 1,
      "workspace-plan-incomplete");
    return plan;
  }
  function elt(tag, attrs, text) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
    if (text !== undefined) n.textContent = String(text);
    return n;
  }
  function mount(root, ir, options = {}) {
    assert(root && root.nodeType === 1, "missing-root");
    assert(!root.hasAttribute("data-mcel-semantic-operational"), "already-mounted");
    const c = contract(ir, options.composition); // Refuse before modifying DOM.
    const workspacePlan = workspaceContract(ir, c, options.workspacePlan);
    if (workspacePlan) assert(global.McelSemanticLayoutInference?.mountWorkspacePlan,
      "workspace-layout-engine-missing");
    if(c.generated) assert(global.McelSemanticLayoutInference?.inferWorkspaceLayout &&
      global.McelSemanticLayoutInference?.applyWorkspaceLayout, "generated-layout-engine-missing");
    const port = options.capabilities || {};
    const expert = options.presentation === "expert";
    const state = new Map(), observedTimes=new Map(), receipts = [], displays = new Map();
    const directoryForms = new Set();
    const folderTrees = new Map();
    // Read-only navigation dependencies are identified by source/target state,
    // never by app IDs, labels, selectors or fixed root names.
    const navigationRoots = [];
    const guardedFormButtons = [];
    const refreshers = [];
    const taskRefreshPeriodMs = 5000;
    const collectionHosts = new Map(), plotHosts = new Map(), documentHosts = new Map(), actionButtons = [], regionHosts = new Map(), receiptHosts = [], tabSwitchers = [], current = new Map(), targetRuns = new Map(), plotVersions = new WeakMap();
    const conditionedNodes=new Map(), sharedFieldInputs=new Map(), keyedCollectionRows=new WeakMap(), selectedRecordHosts=new Map(), typedRecordHosts=new Map();
    const pendingConsequential=new Set();
    let serial = 0, disposed = false;
    let refreshTimer = null, initialRefreshTimer = null, refreshBusy = false, refreshEnabled = true;
    let inferredLayout=null, generatedResizeObserver=null, generatedResizeListener=null;
    let plannedLayout=null;
    let scheduleGeneratedLayout=()=>{};
    const rememberedInputs = new Map();
    const chatControllers=[];
    const pendingChatMounts=[];
    // Source-declared form/output links, not application-name selectors.
    const resultReceivers = new Map();
    for (const s of c.states.values()) {
      if (own(s,"initial")) { checkSchema(s.schema,s.initial,s.id); state.set(s.id,clone(s.initial)); }
    }
    const read = id => state.get(id);
    // Missing/expired derived state never satisfies operational applicability.
    // A backend-supplied observation timestamp, when present, is checked as
    // well as the time at which this runtime received its validated response.
    function conditionSatisfied(when) {
      if(own(when,"all"))return when.all.every(conditionSatisfied);
      if(own(when,"any"))return when.any.some(conditionSatisfied);
      const id=ref(when.state),descriptor=c.states.get(id),source=read(id);
      if(source===undefined || source===null)return false;
      if(descriptor?.authority==="derived"){
        const received=observedTimes.get(id);
        if(!Number.isFinite(received) || Date.now()-received>60000 || received-Date.now()>5000)return false;
        if(source && typeof source==="object" && !Array.isArray(source)){
          if(source.observationState==="STALE")return false;
          if(own(when,"maxAgeSeconds")){
            const whenObserved=Date.parse(source.observedAt);
            if(!Number.isFinite(whenObserved) ||
               Date.now()-whenObserved>when.maxAgeSeconds*1000 ||
               whenObserved-Date.now()>5000)return false;
          }
        }
      }
      const value=own(when,"path")?source?.[when.path]:source;
      return value!==undefined && value===when.equals;
    }
    const applicable = n => !n?.visibleWhen || conditionSatisfied(n.visibleWhen);
    function notify() {
      for (const [r,el] of regionHosts) {
        if (r.visibleWhen) el.hidden = read(ref(r.visibleWhen.state)) !== r.visibleWhen.equals;
      }
      for (const [n, el] of conditionedNodes) el.hidden = !applicable(n);
      if(c.generated)for(const [r,el] of regionHosts)if(r.recipe!=="root" && r.members?.length) {
        el.hidden=r.members.every(member=>{
          const node=c.nodes.get(ref(member));
          return !!node?.visibleWhen && !applicable(node);
        });
      }
      for (const [n, host] of selectedRecordHosts) updateSelectedRecord(n,host);
      for (const [n, host] of typedRecordHosts) updateTypedRecord(n,host);
      for (const [id, elements] of displays) for (const el of elements) {
        const value = read(id);
        // A bounded, inert image result is a generic semantic visualization.
        // Never insert returned HTML or execute result-owned scripts.
        let viewport=null;
        if(typeof value==="string" && value.length<2500000 && value.startsWith('{"kind":"mcel.sandboxed-viewport-result.v1"')){
          try{viewport=JSON.parse(value);}catch{}
        }
        if(viewport && viewport.kind==="mcel.sandboxed-viewport-result.v1" &&
            ["completed","script-error"].includes(viewport.status) &&
            Number.isInteger(viewport.width) && Number.isInteger(viewport.height) &&
            viewport.width>=1 && viewport.height>=1 && viewport.width<=1024 && viewport.height<=1024 &&
            typeof viewport.png==="string" && viewport.png.length<=2400000 &&
            /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(viewport.png) &&
            Array.isArray(viewport.logs) && viewport.logs.length<=80){
          const img=elt("img",{alt:"Sandboxed pixel viewport result","data-mcel-sandboxed-image":""});
          img.src=viewport.png;img.style.maxWidth="100%";img.style.height="auto";
          const caption=elt("p",{"role":"status"},viewport.status+" · "+viewport.width+"×"+viewport.height);
          const log=elt("pre",{"data-mcel-sandboxed-log":""});
          log.textContent=viewport.logs.join("\n");
          el.replaceChildren(img,caption,log);
        }else el.textContent = value === undefined ? "" : (value !== null && typeof value === "object" ? JSON.stringify(value,null,2) : String(value));
      }
      for (const view of tabSwitchers) {
        const selected=read(view.stateId);
        view.select.value=selected === undefined ? view.select.value : selected;
        for (const button of view.buttons) {
          const active = button.dataset.value === selected;
          button.setAttribute("aria-pressed",String(active));
        }
      }
      for (const [n, host] of collectionHosts) updateCollection(n,host);
      for (const [n, host] of plotHosts) updatePlot(n, host);
      for (const [n, session] of documentHosts) updateDocument(n,session);
      for(const {button,intent} of [...actionButtons,...guardedFormButtons]){
        button.disabled=(intent.requestArguments||[]).some(arg=>{
          const value=read(ref(arg.state));return value===undefined || value===null || value==="";
        });
      }
      for (const host of receiptHosts) {
        const recent = expert ? receipts.filter(r => c.intents.get(r.intentId)?.operationKind === "capability").slice(-6).reverse() : receipts;
        const list = host.list || host;
        list.replaceChildren(...recent.map(r => {
          const nodes=[...c.nodes.values()].filter(n=>ref(n.intent)===r.intentId);
          // A receipt is evidence, not an available action. Preserve the
          // legacy intent selector while applicable; retired conditional
          // actions retain their auditable receipt under a distinct attribute.
          const attrs={"data-operation-intent":r.intentId,"data-disposition":r.disposition};
          if(!nodes.length || nodes.some(applicable))attrs["data-intent"]=r.intentId;
          return elt("li",attrs,fmt(r.intentId) + ": " + r.disposition +
            (r.reason ? " (" + r.reason + ")" : ""));
        }));
        if (host.details) {
          host.summary.textContent = recent.length ? "Activity · " + recent.length + " recent operation" + (recent.length===1 ? "" : "s") : "Activity · No operations yet";
          if (recent.some(r => r.disposition === "failed" || r.disposition === "refused-before-attempt")) host.details.open=true;
        }
      }
      if(c.generated)scheduleGeneratedLayout();
    }
    function traceOperation(intentId,phase,disposition) {
      if (typeof global.CustomEvent !== "function") return;
      root.dispatchEvent(new global.CustomEvent("mcel:parity-operation", {bubbles:true,
        detail:{appId:ir.application.appId,intentId,phase,disposition}}));
    }
    function receipt(intent, disposition, reason="") {
      const r = Object.freeze({sequence:receipts.length+1,intentId:intent.id,disposition,reason});
      receipts.push(r); notify(); traceOperation(intent.id,"settled",disposition); return r;
    }
    function parseInput(s, element) {
      const raw = element.value;
      if (s.kind === "integer") return raw.trim() === "" ? null : Number(raw);
      if (s.kind === "number") return raw.trim() === "" ? null : Number(raw);
      if (s.kind === "boolean") return element.checked;
      return raw;
    }
    async function execute(id, given={}) {
      assert(!disposed, "disposed");
      const intent = c.intents.get(id); assert(intent,"unknown-intent:"+id);
      traceOperation(id,"dispatch","");
      let attempted = false;
      try {
        // The same declarative applicability gate governs the generated
        // control and programmatic execute(). This is NOT backend authority.
        const controls=[...c.nodes.values()].filter(n=>ref(n.intent)===id);
        if(controls.length && !controls.some(applicable))fail("operational-condition-not-satisfied");
        const input = {};
        for (const parameter of intent.input || []) {
          assert(own(given,parameter.sourceName),"missing-input:"+parameter.sourceName);
          checkSchema(parameter.schema,given[parameter.sourceName],parameter.id);
          input[parameter.sourceName] = clone(given[parameter.sourceName]);
        }
        assert(Object.keys(input).length === Object.keys(given).length,"undeclared-input");
        if (intent.operationKind === "interaction") {
          if (intent.collectionSelection) {
            for(const session of documentHosts.values())if(session.savePending)
              fail("source-write-in-progress");
            const sel = intent.collectionSelection, source = read(ref(sel.source));
            assert(Array.isArray(source),"selection-source-not-loaded");
            const selected = input[(intent.input || []).find(p => p.id === ref(sel.input))?.sourceName];
            assert(source.some(row => row && String(row[sel.key]) === String(selected)),"selection-not-in-collection");
            state.set(ref(sel.target),clone(selected));
            // A new root invalidates both the visible hierarchy and every
            // derived result scoped to the old root, including file previews.
            // Do not leave selectable stale rows while a new root loads.
            for(const [node,tree] of folderTrees){
              if(ref(sel.target)===tree.rootState && tree.root!==String(selected)){
                tree.root=String(selected);tree.paths.clear();tree.current="";tree.searchResults=false;
                const invalidated=new Set();
                for(const dependent of c.intents.values()){
                  if(dependent.operationKind!=="capability" ||
                      !(dependent.requestArguments||[]).some(arg=>ref(arg.state)===tree.rootState))continue;
                  for(const binding of dependent.resultBindings||[]){
                    const target=ref(binding.target);
                    if(c.states.get(target)?.authority==="derived"){
                      invalidated.add(target);state.delete(target);
                    }
                  }
                }
                for(const dependent of c.intents.values()){
                  if(dependent.collectionSelection && invalidated.has(ref(dependent.collectionSelection.source))){
                    const target=ref(dependent.collectionSelection.target), schema=c.states.get(target)?.schema;
                    if(schema?.kind==="string")state.set(target,"");
                    else state.delete(target);
                  }
                }
                for(const nav of directoryForms){
                  nav.input.value="";nav.current="";nav.up.disabled=true;nav.path.textContent="/";
                }
              }
            }
            notify();
            const result=receipt(intent,"selected");
            // Selecting an observed directory root must actually list it.
            // This is an existing read-only capability, not an inferred
            // filesystem permission or an application-specific handler.
            const navigation=navigationRoots.find(nav=>nav.selection.id===id);
            if(navigation && !disposed)await execute(navigation.browse.id);
            // A document editor's declared path/load bindings turn file choice
            // into an actual source-open operation. The capability port still
            // authorizes the selected path and performs the governed read.
            // Non-document collections retain selection-only behavior.
            for(const [node,session] of documentHosts){
              if(ref(node.path)!==ref(sel.target) || session.activePath===selected)continue;
              // Preserve each open document's independent draft. Loading still
              // goes through the declared capability, with fresh source checks.
              void execute(ref(node.loadIntent));
            }
            return result;
          }
          if (intent.viewTransition) {
            const t = intent.viewTransition, key = (intent.input || []).find(p => p.id === ref(t.value?.input))?.sourceName;
            assert(key && c.states.get(ref(t.target))?.authority === "renderer-local", "invalid-view-transition");
            state.set(ref(t.target),clone(input[key]));
            for(const control of c.nodes.values())if(control.nodeKind==="control" && ref(control.intent)===id)
              for(const item of control.resetOutputs||[])state.set(ref(item.state),item.value);
            notify(); return receipt(intent,"completed");
          }
          fail("unsupported-interaction:"+id);
        }
        assert(intent.operationKind === "capability", "unsupported-intent:"+id);
        if(intent.risk === "consequential") {
          assert(!pendingConsequential.has(id),"consequential-request-already-pending");
          pendingConsequential.add(id);
        }
        const e = c.effects.get(ref(intent.effectRefs[0]));
        const cap = c.capabilities.get(ref(e.authority));
        const op = cap.operations.find(o => o.name === intent.operation && o.runtimeMethod === intent.runtimeMethod);
        const args = {...input};
        for (const item of intent.requestArguments || []) {
          const source = ref(item.state), value = read(source);
          assert(value !== undefined,"request-state-unset:"+source);
          assert(!own(args,item.name),"request-argument-conflict:"+item.name);
          args[item.name] = clone(value);
        }
        for (const [name, schema] of Object.entries(op.inputs || {})) {
          assert(own(args,name),"missing-operation-input:"+name);
          checkSchema(schema,args[name],"operation."+name);
        }
        assert(Object.keys(args).length === Object.keys(op.inputs || {}).length,"undeclared-operation-argument");
        // The renderer owns drafts, while the port owns source authority.
        // Verify both before permitting a document mutation or an Aider job.
        if(id==="intent:run-aider"){
          const files=String(args.files||"").trim()
            ? String(args.files).split(/[\n,]+/).map(x=>x.trim()).filter(Boolean)
            : [args.path];
          assert(files.includes(args.path),"aider-current-file-required");
          for(const [,session] of documentHosts){
            assert(!session.savePending,"source-write-in-progress");
            for(const file of files){
              const tab=session.tabs.get(file);
              if(tab)assert(!tab.dirty && !tab.conflict && !tab.aiderPending,
                "aider-file-has-unsaved-or-conflicted-draft:"+file);
            }
          }
        }
        if(intent.risk==="source-write"){
          for(const [node,session] of documentHosts){
            if(ref(node.path)!==ref((intent.requestArguments||[]).find(x=>x.name==="path")?.state))continue;
            const tab=session.tabs.get(args.path);
            assert(tab && session.activePath===args.path && !tab.conflict && !tab.aiderPending &&
              tab.editable && tab.draft===args.text &&
              (!session.savePending || id===ref(node.saveIntent)),
              "source-write-document-not-current");
          }
        }
        const request = Object.freeze({appId:ir.application.appId,intentId:id,effectId:e.id,
          capabilityId:cap.id,operation:op.name,runtimeMethod:op.runtimeMethod,risk:e.risk,arguments:clone(args)});
        assert(typeof port.authorize === "function" && typeof port.invoke === "function", "capability-port-not-installed");
        const granted = await port.authorize(request);
        assert(granted === true,"authority-denied");
        const run = ++serial; current.set(id,run);
        for (const binding of intent.resultBindings || []) targetRuns.set(ref(binding.target),run);
        attempted = true;
        const value = await port.invoke(request);
        // A root-scoped read belongs to the exact selected root that issued it.
        // A late response from a previous root must never repopulate a new
        // root's collection, inspection, or navigation authority.
        const rootScoped=(intent.requestArguments||[]).some(item=>
          [...folderTrees.values()].some(tree=>ref(item.state)===tree.rootState));
        if(rootScoped && (intent.requestArguments||[]).some(item=>
          JSON.stringify(read(ref(item.state)))!==JSON.stringify(args[item.name])))
          return {disposition:"superseded"};
        // For external reads, a response scoped to selected local state must
        // not overwrite the visible result after that selection changes. This
        // generalizes the same freshness rule used for root and editor reads,
        // without changing any consequential action/receipt lifecycle.
        if(intent.risk==="external-read" && (intent.requestArguments||[]).some(item=>
          JSON.stringify(read(ref(item.state)))!==JSON.stringify(args[item.name])))
          return {disposition:"superseded"};
        // A source request can finish after a different file was selected.
        // Never allow an old response to replace the current Monaco document.
        if ([...documentHosts.keys()].some(node=>ref(node.loadIntent)===id && read(ref(node.path))!==args.path))
          return {disposition:"superseded"};
        if (intent.risk !== "consequential" && (disposed || current.get(id) !== run ||
            (intent.resultBindings || []).some(binding=>targetRuns.get(ref(binding.target)) !== run)))
          return {disposition:"superseded"};
        if (op.resultSchema) checkSchema(op.resultSchema,value,"capability-response");
        for (const binding of intent.resultBindings || []){
          const target=ref(binding.target);
          state.set(target,clone(value));
          observedTimes.set(target,Date.now());
          for(const receiver of resultReceivers.get(target)||[]){
            if(!receiver.input.isConnected || receiver.whenIntent && receiver.whenIntent!==id)continue;
            const next=receiver.pick?value?.[receiver.pick]:value;
            receiver.input.value=String(next);
            receiver.input.dispatchEvent(new Event("input",{bubbles:true}));
            if(value && typeof value==="object" && Number.isInteger(value.selectionStart) &&
               Number.isInteger(value.selectionEnd) && typeof receiver.input.setSelectionRange==="function") {
              receiver.input.focus({preventScroll:true});
              receiver.input.setSelectionRange(value.selectionStart,value.selectionEnd);
            }
            if(receiver.invoke)void execute(receiver.invoke);
          }
        }
        for(const [node,session] of documentHosts){
          if(ref(node.loadIntent)===id){
            const previous=session.tabs.get(args.path);
            const editable=typeof value==="string" && !value.includes("[truncated at 200000 characters]") && !value.includes("\uFFFD");
            // If another writer changed the source during a tab switch, do
            // not silently rebase a dirty draft onto the new source hash.
            const conflict=!!(previous?.dirty && previous.loadedValue!==value);
            const tab=previous?.dirty
              ? {...previous,editable:previous.editable && editable && !conflict,conflict}
              : {loadedValue:value,draft:value,dirty:false,editable,conflict:false};
            session.tabs.set(args.path,tab);
            if(conflict)traceOperation("editor.document.conflict","settled","stale-baseline");
            session.activePath=args.path;
            session.loadedValue=tab.loadedValue;
            session.editable=tab.editable;
            session.conflict=tab.conflict;
            session.mountedPath="";
            session.dirty=tab.dirty;
            state.set(ref(node.draft),tab.draft);
          } else if(id==="intent:run-aider"){
            for(const [path,tab] of session.tabs){
              if(port.documentWriteStatus?.(path)!=="aider-pending")continue;
              tab.aiderPending=true;tab.editable=false;
              if(session.activePath===path){
                session.editable=false;
                session.status.textContent="Aider accepted; job still running. Refresh Aider jobs before reopening source.";
                global.MainComputerMonacoAdapter?.setModel?.({path,value:tab.draft,readOnly:true});
              }
            }
          } else if(ref(node.saveIntent)===id){
            const tab=session.tabs.get(args.path);
            if(tab){
              tab.loadedValue=args.text;
              // An edit made while the save was in flight remains dirty.
              tab.dirty=tab.draft!==args.text;
              tab.conflict=false;
              if(session.activePath===args.path){
                session.loadedValue=args.text;
                session.dirty=tab.dirty;
                session.conflict=false;
                session.status.textContent="Saved: "+session.activePath;
              }
            }
          }
        }
        if(id==="intent:search"){
          for(const [node,tree] of folderTrees){
            if(intent.resultBindings?.some(b=>ref(b.target)===ref(node.state)))tree.searchResults=true;
          }
        }
        // Retain previously visited branches for every semantic directory
        // collection. A fresh parent listing prunes absent child directories.
        // Cached file rows are only navigation hints: selection re-lists the
        // parent through the original capability before granting file access.
        let enteredDirectory=null;
        for(const [node,tree] of folderTrees){
          if(!intent.resultBindings?.some(b=>ref(b.target)===ref(node.state)))continue;
          const isRoot=tree.rootListingIntent===id || (id==="intent:browse" && tree.pathParameter==="relativePath");
          if(!isRoot && tree.intent.id!==id)continue;
          const entered=isRoot?"":args[tree.pathParameter];
          const selectedRoot=tree.rootState?read(tree.rootState):"";
          if(tree.root!==selectedRoot){tree.paths.clear();tree.root=selectedRoot;}
          if(isRoot && tree.rootState)tree.paths.clear();
          tree.searchResults=false;
          const children=new Set(value.filter(row=>row && (row.kind==="directory" || row.kind==="dir"))
            .map(row=>String(row[node.key])));
          const previous=tree.paths.get(entered)||[];
          for(const old of previous){
            const path=String(old[node.key]);
            if((old.kind!=="directory" && old.kind!=="dir") || children.has(path))continue;
            for(const cached of [...tree.paths.keys()])
              if(cached===path || cached.startsWith(path+"/"))tree.paths.delete(cached);
          }
          tree.paths.set(entered,clone(value));tree.current=entered;
          enteredDirectory=entered;
        }
        if(enteredDirectory!==null)for(const item of directoryForms){
          item.input.value=enteredDirectory;
          item.current=enteredDirectory;
          item.up.disabled=!enteredDirectory;
          item.path.textContent=enteredDirectory?"/"+enteredDirectory:"/";
        }
        if (intent.risk==="source-write" && id!=="intent:save-source" && args.review?.path) {
          for(const [node,session] of documentHosts) {
            if(session.activePath!==args.review.path || typeof args.text!=="string")continue;
            session.loadedValue=args.text;session.dirty=false;session.conflict=false;
            session.tabs.set(session.activePath,{loadedValue:args.text,draft:args.text,dirty:false,editable:true,conflict:false});
            state.set(ref(node.contentState),args.text);state.set(ref(node.draft),args.text);
            session.status.textContent="Reviewed change applied: "+session.activePath;
          }
        }
        notify();
        // On a terminal Aider job receipt, reread the current affected file
        // through the original port. Never assume job acceptance means success.
        if(id==="intent:aider-jobs")for(const [node,session] of documentHosts){
          const path=session.activePath,tab=session.tabs.get(path);
          if(tab?.aiderPending && port.documentWriteStatus?.(path)!=="aider-pending" &&
             read(ref(node.path))===path){
            tab.aiderPending=false;session.mountedPath="";
            void execute(ref(node.loadIntent));
          }
        }
        const disposition = id==="intent:run-aider" && typeof value==="string" &&
          value.startsWith("Aider job accepted (not completed): ")?"accepted-deferred":
          (intent.risk === "consequential" && value && typeof value === "object" &&
          ["accepted-deferred","command-returned","recorded","unknown","refused"].includes(value.outcome))
          ? value.outcome : "completed";
        const completed=receipt(intent,disposition,
          disposition === "completed" ? "" :
            (typeof value==="string" ? value : String(value.message||"")));
        if(disposition==="completed")for(const navigation of navigationRoots){
          if(navigation.loader.id!==id)continue;
          // Reusing a valid root is preferable to resetting a user's location;
          // otherwise the first *observed* root is the safe default. Its
          // existence is verified by the collection-selection intent.
          const roots=read(navigation.rootsState);
          if(!Array.isArray(roots))continue;
          const previous=read(navigation.rootState);
          const current=roots.find(item=>String(item?.[navigation.key])===String(previous))||roots[0];
          if(current){
            // Even if the root ID is unchanged, its earlier directory and
            // preview evidence is stale after a fresh discovery. Force the
            // existing root-switch invalidation path before browsing again.
            for(const tree of folderTrees.values())if(tree.rootState===navigation.rootState)
              tree.root="";
            await execute(navigation.selection.id,{
              [navigation.inputName]:String(current[navigation.key])});
          }
          else {
            // Roots may disappear on refresh. No stale directory contents or
            // inspection authority may survive an empty observed root list.
            state.set(navigation.rootState,"");
            for(const [node,tree] of folderTrees){
              if(tree.rootState!==navigation.rootState)continue;
              tree.root="";tree.paths.clear();tree.current="";tree.searchResults=false;
              state.delete(ref(node.state));
            }
            for(const dependent of c.intents.values()){
              if(dependent.operationKind!=="capability" ||
                  !(dependent.requestArguments||[]).some(arg=>ref(arg.state)===navigation.rootState))continue;
              for(const binding of dependent.resultBindings||[])state.delete(ref(binding.target));
            }
            for(const dependent of c.intents.values()){
              if(!dependent.collectionSelection ||
                  c.states.get(ref(dependent.collectionSelection.target))?.authority!=="renderer-local")continue;
              if(!read(ref(dependent.collectionSelection.source)))
                state.set(ref(dependent.collectionSelection.target),"");
            }
            for(const nav of directoryForms){
              nav.input.value="";nav.current="";nav.up.disabled=true;nav.path.textContent="/";
            }
            notify();
          }
        }
        return completed;
      } catch (ex) {
        if(id==="intent:run-aider")for(const [node,session] of documentHosts){
          for(const [path,tab] of session.tabs){
            if(port.documentWriteStatus?.(path)!=="aider-outcome-unverified")continue;
            tab.aiderPending=true;tab.editable=false;
            if(session.activePath===path){
              session.editable=false;
              global.MainComputerMonacoAdapter?.setModel?.({path,value:tab.draft,readOnly:true});
            }
          }
          updateDocument(node,session);
        }
        for(const [node,session] of documentHosts){
          if(id===ref(node.loadIntent) && session.activePath && read(ref(node.path))!==session.activePath){
            state.set(ref(node.path),session.activePath);
          }
        }
        const reason = String(ex?.message || ex).replace(/^MCEL_OPERATIONAL_REFUSED:/,"");
        return receipt(intent, attempted ? "failed" : "refused-before-attempt", reason);
      } finally {
        pendingConsequential.delete(id);
      }
    }
    function renderInput(name,schema,displayLabel) {
      const wrap = elt("label",{"data-field":name},displayLabel || fmt(name));
      let input;
      if (Array.isArray(schema.enum)) {
        input=elt("select",{"name":name});
        for (const choice of schema.enum) input.append(elt("option",{value:choice},fmt(choice)));
      } else if (schema.kind === "boolean") input=elt("input",{name,type:"checkbox"});
      else if (schema.kind === "integer" || schema.kind === "number") input=elt("input",{name,type:"number",step:schema.kind === "integer" ? "1" : "any"});
      else if(["script","code"].includes(name))input=elt("textarea",{name,rows:"8",spellcheck:"false"});
      else input=elt("input",{name,type:"text"});
      wrap.append(input);return {wrap,input};
    }
    let region = null;
    function renderNode(n) {
      const holder=elt("div",{"data-mcel-node":n.id,"data-node-kind":n.nodeKind});
      if(n.visibleWhen)conditionedNodes.set(n,holder);
      if (n.nodeKind === "state-value") {
        const id=ref(n.value.state),el=elt("output",{"data-state":id});
        holder.append(el);
        if(c.states.get(id)?.schema?.kind === "string" &&
          [...c.intents.values()].some(i=>i.collectionSelection && ref(i.collectionSelection.target)===id)) {
          selectedRecordHosts.set(n,el);
        } else if(c.states.get(id)?.schema?.kind === "record") {
          typedRecordHosts.set(n,el);
        } else {if (!displays.has(id)) displays.set(id,[]); displays.get(id).push(el);}
      } else if (n.nodeKind === "operation-evidence") {
        const ol=elt("ol",{"aria-label":"Operation receipts"});
        if (expert) {
          const details=elt("details",{"data-mcel-activity":""}),summary=elt("summary",{},"Activity · No operations yet");
          details.append(summary,ol);holder.append(details);receiptHosts.push({details,summary,list:ol});
        } else {holder.append(ol);receiptHosts.push(ol);}
      } else if (n.nodeKind === "collection") {
        const list=elt("div",{"data-collection":n.id});
        // Structured multi-field monitoring collections are primary records,
        // not narrow navigation widgets. This presentation hint is derived
        // from IR purpose/tasks/fields, never from an application or region ID.
        if(region?.purpose==="workspace" && region.tasks?.includes("monitor") &&
           Array.isArray(n.fields) && n.fields.length>=4){
          list.setAttribute("data-mcel-structured-list","");
          list.style.setProperty("--mcel-list-columns",String(n.fields.length));
        }
        holder.append(list); collectionHosts.set(n,list);
        // Any semantic navigation collection backed by a one-path directory
        // read can retain a hierarchy. The rule is based on the region and IR,
        // never on an application ID or a authored layout.
        if((region?.tasks?.includes("browse") || region?.purpose==="navigation") && n.fields?.includes("kind")){
          const directoryIntent=[...c.intents.values()].find(i=>i.operationKind==="capability" &&
            i.input?.some(p=>["relativePath","path"].includes(p.sourceName)) &&
            i.resultBindings?.some(b=>ref(b.target)===ref(n.state)));
          const pathParameter=directoryIntent?.input?.find(p=>["relativePath","path"].includes(p.sourceName))?.sourceName;
          const rootArg=directoryIntent?.requestArguments?.find(a=>a.name==="rootId");
          const rootListing=[...c.intents.values()].find(i=>i.operationKind==="capability" &&
            !(i.input||[]).length && !i.requestArguments?.length &&
            i.resultBindings?.some(b=>ref(b.target)===ref(n.state)));
          if(directoryIntent && pathParameter)
            folderTrees.set(n,{paths:new Map(),current:"",root:"",searchResults:false,
              rootState:rootArg?ref(rootArg.state):null,intent:directoryIntent,pathParameter,
              rootListingIntent:rootListing?.id||null});
        }
      } else if (n.nodeKind === "document-editor") {
        const outer=elt("div",{"data-mcel-document-editor":n.id});
        const bar=elt("div",{"data-mcel-document-actions":""});
        const label=elt("span",{"data-mcel-document-label":""},"Select a source file and open it");
        const save=elt("button",{type:"button","data-mcel-document-save":""},"Save source");
        const discard=elt("button",{type:"button","data-mcel-document-discard":""},"Discard draft");
        const close=elt("button",{type:"button","data-mcel-document-close":""},"Close file");
        const tabBar=elt("div",{"data-mcel-document-tabs":"","role":"tablist","aria-label":"Open documents"});
        const surface=elt("div",{"data-mcel-document-surface":"","aria-label":"Monaco source editor"});
        const status=elt("p",{"role":"status","data-mcel-document-dirty":"false"},"No document opened");
        bar.append(label);
        if(n.saveIntent)bar.append(save,discard);bar.append(close);
        outer.append(tabBar,bar,surface,status);holder.append(outer);
        const session={surface,tabBar,status,label,save,discard,close,tabs:new Map(),activePath:"",mountedPath:"",loadedValue:"",dirty:false,editable:false,conflict:false,starting:false,savePending:false};
        documentHosts.set(n,session);
        function closeDocument(path){
          if(session.savePending)return;
          const tab=session.tabs.get(path);
          if(!tab)return;
          if(tab.dirty && typeof global.confirm==="function" &&
              !global.confirm("Discard unsaved changes and close "+path+"?"))return;
          const closingActive=session.activePath===path;
          session.tabs.delete(path);
          // Every tab closure revokes its underlying document authority, even
          // when a different document owns the visible Monaco surface.
          port.closeDocument?.(path);
          traceOperation("editor.document.close","settled","closed");
          if(closingActive){
            global.MainComputerMonacoAdapter?.dispose?.("document-close");
            session.activePath="";session.mountedPath="";session.loadedValue="";
            session.dirty=false;session.editable=false;session.conflict=false;session.starting=false;
            session.surface.replaceChildren();
            state.set(ref(n.draft),"");state.set(ref(n.contentState),"");
            state.set(ref(n.path),"");
            session.status.textContent="Document closed";
            const next=[...session.tabs.keys()].at(-1);
            if(next){state.set(ref(n.path),next);void execute(ref(n.loadIntent));}
          }
          notify();
        }
        close.addEventListener("click",()=>closeDocument(session.activePath));
        session.closeDocument=closeDocument;
        save.addEventListener("click",async()=>{
          if(!session.activePath || !session.editable || session.conflict || !session.dirty ||
             read(ref(n.path))!==session.activePath ||
             (port.documentWriteStatus && port.documentWriteStatus(session.activePath)!=="ready"))return;
          if(typeof global.confirm==="function" && !global.confirm("Save changes to "+session.activePath+"?"))return;
          session.savePending=true;updateDocument(n,session);
          try{
            const result=await execute(ref(n.saveIntent));
            if(result.disposition!=="completed")session.status.textContent="Save failed: "+(result.reason||result.disposition);
          }finally{session.savePending=false;updateDocument(n,session);}
        });
        discard.addEventListener("click",()=>{
          if(!session.activePath || session.savePending)return;
          if(session.conflict){
            // Discard must not silently turn an old baseline into a writable
            // draft after another writer changed the underlying file.
            session.tabs.delete(session.activePath);
            session.mountedPath="";session.conflict=false;
            void execute(ref(n.loadIntent));return;
          }
          const adapter=global.MainComputerMonacoAdapter;
          session.starting=true;
          const result=adapter?.setModel?.({path:session.activePath,value:session.loadedValue,readOnly:false});
          session.starting=false;
          if(result?.ok){
            state.set(ref(n.draft),session.loadedValue);session.dirty=false;session.conflict=false;
            const tab=session.tabs.get(session.activePath);
            if(tab){tab.draft=session.loadedValue;tab.dirty=false;tab.conflict=false;tab.editable=true;}
            updateDocument(n,session);
          }
        });
      } else if (n.nodeKind === "embedded-chat") {
        assert(typeof port.mountEmbeddedChat==="function","embedded-chat-port-unavailable");
        const host=elt("section",{"data-mcel-embedded-chat":n.id,"aria-label":n.title});
        holder.append(host);
        // The incumbent Chat Console discovers notebook targets through
        // document-level selectors. Mount only after this node is attached.
        pendingChatMounts.push({host,node:n});
      } else if (n.nodeKind === "plot") {
        const plot=elt("div", {"data-mcel-plot":n.id, role:"img", "aria-label":fmt(n.sourceName || n.id) + " function plot"});
        holder.append(plot); plotHosts.set(n,plot);
      } else if (n.nodeKind === "form") {
        const intent=c.intents.get(ref(n.intent)),form=elt("form",{"data-intent":intent.id});
        const controls={};
        // An edited compute input makes the previous derived answer obsolete.
        // Supersede an in-flight result as well, so a late response cannot
        // present a calculation for an expression the user has since changed.
        // This is driven by semantic compute tasks, never by an app identity.
        const isComputeInput=!!region?.tasks?.includes("compute");
        function supersedeComputeRequest(clearResult=false) {
          if (!isComputeInput) return;
          // Editing supersedes an in-flight response without discarding the
          // last successfully computed result. Only an explicit Clear/Reset
          // action revokes that result, preserving established Calculator
          // behavior when a replacement expression fails validation.
          const run=++serial;
          current.set(intent.id,run);
          for (const binding of intent.resultBindings || []) {
            const id=ref(binding.target), descriptor=c.states.get(id);
            if (descriptor?.authority !== "derived") continue;
            targetRuns.set(id,run);
            if (clearResult) {
              if (own(descriptor,"initial")) state.set(id,clone(descriptor.initial));
              else {state.delete(id);observedTimes.delete(id);}
            }
          }
          if (clearResult) notify();
        }
        form.addEventListener("input",()=>supersedeComputeRequest());
        form.addEventListener("reset",event=>{
          if(n.resetAndSubmit){
            // Graph-range resets preserve the authored expression while
            // applying declared defaults, then redraw from the same form.
            event.preventDefault();
            for(const [key,value] of Object.entries(n.defaults||{})){
              controls[key].value=String(value);
              controls[key].dispatchEvent(new Event("input",{bubbles:true}));
            }
            supersedeComputeRequest();
            form.requestSubmit();
          }else supersedeComputeRequest(true);
        });
        const formFieldNames=Object.keys(n.fields || {});
        // Keep the domain's canonical inputs together; pair range bounds as
        // X Min / X Max and Y Min / Y Max rather than alphabetical X Max / X Min.
        const bounds=name=>/^(.+)(Min|Max)$/.exec(name);
        formFieldNames.sort((a,b)=>{
          if (a === "expression") return b === "expression" ? 0 : -1;
          if (b === "expression") return 1;
          const left=bounds(a),right=bounds(b);
          if (left && right) return left[1].localeCompare(right[1]) || (left[2] === right[2] ? 0 : left[2] === "Min" ? -1 : 1);
          return 0;
        });
        for (const k of formFieldNames) {
          const schema=n.fields[k];
          const item=renderInput(k,schema);
          if (Object.prototype.hasOwnProperty.call(n.defaults || {},k)) {
            item.input.value=String(n.defaults[k]);
            item.input.defaultValue=String(n.defaults[k]);
          }
          form.append(item.wrap);controls[k]=item.input;
          const mappedState=ref(n.fieldState?.[k]);
          if(mappedState){
            if(!sharedFieldInputs.has(mappedState))sharedFieldInputs.set(mappedState,new Set());
            sharedFieldInputs.get(mappedState).add(item.input);
            const syncField=(next)=>{
              state.set(mappedState,next);
              for(const sibling of sharedFieldInputs.get(mappedState))if(sibling!==item.input){
                if(schema.kind==="boolean")sibling.checked=next;
                else sibling.value=String(next);
              }
            };
            const currentValue=read(mappedState);
            if(currentValue!==undefined && currentValue!==null)item.input.value=String(currentValue);
            item.input.addEventListener("input",()=>{
              let next=parseInput(schema,item.input);
              if(schema.kind==="number" && !Number.isFinite(next))return;
              syncField(next);
            });
            item.input.addEventListener("change",()=>{
              const next=parseInput(schema,item.input);
              if(schema.kind!=="number" || Number.isFinite(next))syncField(next);
            });
          }
        }
        for(const item of Object.values(n.acceptResult||{})){
          const key=ref(item.state),receiver={input:controls[item.field],invoke:ref(item.invoke),pick:item.pick,whenIntent:ref(item.whenIntent)};
          if(!resultReceivers.has(key))resultReceivers.set(key,[]);
          resultReceivers.get(key).push(receiver);
        }
        const button=elt("button",{type:"submit"},fmt(intent.sourceName || intent.id));form.append(button);
        guardedFormButtons.push({button,intent});
        if (n.defaults && Object.keys(n.defaults).length) form.append(elt("button",{type:"reset"},n.resetAndSubmit?"Reset graph":"Reset inputs"));
        if(n.clearIntent){
          const button=elt("button",{type:"button","data-mcel-clear-intent":ref(n.clearIntent)},"Clear expression and result");
          button.addEventListener("click",()=>{
            for(const [key,input] of Object.entries(controls)){
              input.value="";input.dispatchEvent(new Event("input",{bubbles:true}));
            }
            supersedeComputeRequest(true);
            void execute(ref(n.clearIntent));
          });form.append(button);
        }
        if(n.editor){
          const tools=elt("div",{"data-mcel-expression-editor":"","role":"group","aria-label":"Expression editor"});
          const append=(label,action,token="")=>{
            const btn=elt("button",{type:"button","data-editor-action":action,"data-editor-token":token},label);
            btn.addEventListener("click",()=>void execute(ref(n.editor.intent),{
              expression:controls.expression.value,action,token,
              selectionStart:controls.expression.selectionStart??controls.expression.value.length,
              selectionEnd:controls.expression.selectionEnd??controls.expression.value.length}));
            tools.append(btn);
          };
          append("Clear graph","clear");append("Backspace graph","backspace");
          for(const token of n.editor.templates)append(token,"insert",token);
          form.append(tools);
        }
        // A semantic directory-navigation form may expose a parent action.
        // The host port must still authorize the target directory.
        const directoryParameter=Object.keys(n.fields||{}).length===1 &&
          (own(n.fields||{},"relativePath")?"relativePath":own(n.fields||{},"path")?"path":null);
        if(directoryParameter && (region?.tasks?.includes("browse") || region?.purpose==="navigation")) {
          const input=controls[directoryParameter], path=elt("span",{"data-mcel-current-directory":""},"/");
          const up=elt("button",{type:"button","data-mcel-parent-directory":""},"Parent directory");up.disabled=true;
          const nav={input,path,up,current:""};directoryForms.add(nav);
          up.addEventListener("click",()=>{
            const parts=nav.current.split("/").filter(Boolean);parts.pop();
            void execute(intent.id,{[directoryParameter]:parts.join("/")});
          });form.append(up,path);
        }
        // A general compute task with a single textual expression gets a
        // generated arithmetic entry surface. This uses only semantic tasks
        // and field schema; no application names or backend evaluations.
        if (expert && region.tasks?.includes("compute") && region.priority === "primary" &&
            Object.keys(n.fields || {}).length === 1 && n.fields.expression?.kind === "string") {
          const field=controls.expression;
          form.dataset.mcelComposition="expression-workbench";
          const pad=elt("div",{"data-mcel-expression-pad":"","role":"group","aria-label":"Expression keypad"});
          const keys=[['C','clear'],['⌫','backspace'],['(','('],[')',')'],
            ['7','7'],['8','8'],['9','9'],['÷','/'],
            ['4','4'],['5','5'],['6','6'],['×','*'],
            ['1','1'],['2','2'],['3','3'],['−','-'],
            ['0','0'],['.','.'],['%','%'],['+','+'],['=','submit']];
          for (const [label,action] of keys) {
            const key=elt("button",{type:"button","data-pad-action":action,"aria-label":action === "submit" ? "Evaluate expression" : action === "clear" ? "Clear expression" : action === "backspace" ? "Backspace" : "Insert " + label},label);
            key.addEventListener("click",()=>{
              if (action === "submit") {form.requestSubmit();return;}
              const from=field.selectionStart ?? field.value.length, to=field.selectionEnd ?? from;
              if (action === "clear") {field.value="";supersedeComputeRequest(true);}
              else if (action === "backspace") {
                const start=from === to ? Math.max(0,from-1):from;
                field.setRangeText("",start,to,"end");
              } else field.setRangeText(action,from,to,"end");
              field.dispatchEvent(new Event("input",{bubbles:true}));field.focus();
            });pad.append(key);
          }
          form.append(pad);
        }
        form.addEventListener("submit",e=>{
          e.preventDefault();
          const input={};for(const [k,schema] of Object.entries(n.fields || {}))input[k]=parseInput(schema,controls[k]);
          if(intent.confirmationRequired !== false &&
             (intent.risk==="consequential" || intent.risk==="source-write") &&
             (typeof global.confirm!=="function" || !global.confirm(
               "Confirm "+fmt(intent.sourceName || intent.id)+"?\n"+
               Object.entries(input).map(([key,value])=>fmt(key)+": "+String(value)).join("\n")+
               "\nThis action may stop a process or server. Continue?")))return;
          if((intent.resultBindings||[]).some(b=>[...c.regions.values()].some(r=>
            r.tasks?.includes("monitor") && (r.members||[]).some(m=>{
              const node=c.nodes.get(ref(m));
              return ref(node?.state)===ref(b.target) || ref(node?.value?.state)===ref(b.target);
            }))))rememberedInputs.set(intent.id,input);
          void execute(intent.id,input);});
        holder.append(form);
      } else if (n.nodeKind === "control") {
        const intent=c.intents.get(ref(n.intent));
        const params=intent.input || [];
        if (intent.viewTransition && params.length===1 && Array.isArray(params[0].schema?.enum)) {
          const item=renderInput(params[0].sourceName,params[0].schema,"Mode");
          item.input.setAttribute("data-intent",intent.id);
          item.input.addEventListener("change",()=>void execute(intent.id,{[params[0].sourceName]:item.input.value}));
          holder.append(item.wrap);
          if (expert && params[0].schema.enum.length <= 6) {
            const bar=elt("div",{"role":"group","aria-label":"Application modes","data-mcel-mode-tabs":""});
            const buttons=[];
            for (const value of params[0].schema.enum) {
              const tab=elt("button",{type:"button","data-value":value,"aria-pressed":"false"},fmt(value));
              tab.addEventListener("click",()=>{
                item.input.value=value;
                item.input.dispatchEvent(new Event("change",{bubbles:true}));
              });
              buttons.push(tab);bar.append(tab);
            }
            holder.append(bar);tabSwitchers.push({stateId:ref(intent.viewTransition.target),select:item.input,buttons});
          }
        } else {
          const button=elt("button",{type:"button","data-intent":intent.id},fmt(intent.sourceName || intent.id));
          if (params.length) {button.disabled=true;button.title="Requires selected record or form input";}
          if(intent.requestArguments?.length)actionButtons.push({button,intent});
          button.addEventListener("click",()=>{
            if(intent.confirmationRequired !== false &&
               (intent.risk==="source-write" || intent.risk==="consequential")){
              if(typeof global.confirm!=="function" ||
                !global.confirm("Confirm consequential operation: "+fmt(intent.sourceName || intent.id)+
                  (intent.requestArguments?.length ? "\nTarget: "+intent.requestArguments.map(arg=>String(read(ref(arg.state))||"unselected")).join(", ") : "")+
                  "\nThis may stop a process or server. Continue?"))return;
            }
            void execute(intent.id);
          });holder.append(button);
        }
      }
      return holder;
    }
    // Generic typed numeric-series presentation; no Calculator-specific code.
    function updatePlot(n, holder) {
      const rows = read(ref(n.state));
      if (plotVersions.get(holder) === rows) return;
      plotVersions.set(holder,rows);
      holder.replaceChildren();
      if (!Array.isArray(rows) || rows.length < 2) {
        holder.append(elt("p",{},"No plotted series yet")); return;
      }
      // A large response cannot grow the SVG without bound.
      const samples=rows.slice(0,513).filter(row=>row && Number.isFinite(row[n.x]) && Number.isFinite(row[n.y]));
      if (samples.length<2) {holder.append(elt("p",{},"No finite graph points"));return;}
      const xs=samples.map(v=>v[n.x]), ys=samples.map(v=>v[n.y]);
      let x0=Math.min(...xs), x1=Math.max(...xs), y0=Math.min(...ys), y1=Math.max(...ys);
      if (x1===x0) {x0-=1;x1+=1;} if (y1===y0) {y0-=1;y1+=1;}
      const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
      svg.setAttribute("viewBox","0 0 640 300");svg.setAttribute("width","100%");
      svg.setAttribute("role","presentation"); svg.setAttribute("preserveAspectRatio","xMidYMid meet");
      const add=(tag,attrs)=>{const e=document.createElementNS(svg.namespaceURI,tag);
        for(const [key,value] of Object.entries(attrs)) e.setAttribute(key,String(value));svg.append(e);return e;};
      const px=x=>40+(x-x0)/(x1-x0)*580;
      const py=y=>270-(y-y0)/(y1-y0)*240;
      if(x0<=0 && x1>=0) add("line",{x1:px(0),x2:px(0),y1:25,y2:270,stroke:"currentColor","stroke-opacity":0.32});
      if(y0<=0 && y1>=0) add("line",{x1:40,x2:620,y1:py(0),y2:py(0),stroke:"currentColor","stroke-opacity":0.32});
      // Do not interpolate across missing or clipped samples (for example,
      // across a pole). Use the typical positive spacing of the source series
      // to recognize a gap; arbitrary domain-specific identities are not needed.
      const strides=[];
      for(let i=1;i<samples.length;i++) {
        const distance=Math.abs(samples[i][n.x]-samples[i-1][n.x]);
        if(distance>0 && Number.isFinite(distance))strides.push(distance);
      }
      strides.sort((a,b)=>a-b);
      const typical=strides.length ? strides[Math.floor((strides.length-1)/2)] : 0;
      let d="";
      for(let i=0;i<samples.length;i++) {
        const value=samples[i],gap=i>0 && typical>0 &&
          Math.abs(value[n.x]-samples[i-1][n.x])>typical*1.5;
        d+=(i===0 || gap?"M":" L")+px(value[n.x]).toFixed(2)+" "+py(value[n.y]).toFixed(2);
      }
      add("path",{d,fill:"none",stroke:"#267db9","stroke-width":2.5,"stroke-linejoin":"round"});
      holder.append(svg,elt("p",{"data-plot-range":""},
        `${n.x}: ${x0.toPrecision(4)} to ${x1.toPrecision(4)} · ${n.y}: ${y0.toPrecision(4)} to ${y1.toPrecision(4)} · ${samples.length} points`));
    }
    function updateDocumentTabs(n,session){
      session.tabBar.replaceChildren();
      for(const [path,tab] of session.tabs){
        const item=elt("span",{"data-mcel-open-tab":path});
        const activate=elt("button",{type:"button",role:"tab","data-mcel-tab-path":path,
          "aria-selected":String(path===session.activePath)},path.split("/").pop()+(tab.dirty?" *":"")+(tab.conflict?" !":""));
        activate.addEventListener("click",()=>{
          if(path===session.activePath || session.savePending)return;
          traceOperation("editor.document.switch","dispatch","");
          state.set(ref(n.path),path);notify();void execute(ref(n.loadIntent));
        });
        const dismiss=elt("button",{type:"button","data-mcel-close-tab":path,"aria-label":"Close "+path},"×");
        dismiss.disabled=session.savePending;
        dismiss.addEventListener("click",()=>session.closeDocument(path));
        item.append(activate,dismiss);session.tabBar.append(item);
      }
    }
    function updateDocument(n,session){
      const path=read(ref(n.path));
      const selectedMismatch=path && session.activePath && path!==session.activePath;
      session.label.textContent=session.activePath || "No document opened";
      session.close.disabled=!session.activePath || session.savePending;
      const aiderStatus=session.activePath && port.documentWriteStatus?.(session.activePath);
      const pendingAider=aiderStatus==="aider-pending" || aiderStatus==="aider-outcome-unverified";
      const lostAuthority=aiderStatus==="reopen-required" && session.tabs.has(session.activePath);
      session.save.disabled=!session.editable || session.savePending || session.conflict || pendingAider || lostAuthority || !session.dirty || !session.activePath || selectedMismatch;
      session.discard.disabled=session.savePending || !session.dirty || !session.activePath || selectedMismatch;
      updateDocumentTabs(n,session);
      session.status.dataset.mcelDocumentDirty=String(session.dirty);
      if(pendingAider){
        session.status.textContent=aiderStatus==="aider-outcome-unverified"
          ? "Aider submission outcome unverified. Source writes are blocked for this session."
          : "Aider job pending. Refresh Aider jobs before reopening source; saving is blocked.";
      }else if(session.conflict){
        session.status.textContent="Source changed outside this tab. Copy your draft before closing; saving is blocked.";
      }else if(lostAuthority){
        session.status.textContent="Source write authority was revoked. Reopen and verify this file before saving.";
      }
      if(selectedMismatch){session.status.textContent="Different file selected. Opening it…";return;}
      if(!session.activePath || session.mountedPath===session.activePath || session.starting)return;
      const adapter=global.MainComputerMonacoAdapter;
      if(!adapter?.mount){
        session.status.textContent="Monaco adapter unavailable. Source editing cannot start.";
        traceOperation("editor.monaco.mount","settled","adapter-unavailable");
        return;
      }
      session.starting=true;
      const openingPath=session.activePath;
      session.status.textContent="Opening Monaco: "+openingPath;
      traceOperation("editor.monaco.mount","dispatch","");
      adapter.mount({host:session.surface,path:openingPath,value:session.tabs.get(openingPath)?.draft ?? session.loadedValue,readOnly:!session.editable || pendingAider,
        onReceipt:receipt=>{
          if(receipt?.effect==="editor.monaco.load")
            traceOperation("editor.monaco.load","settled",receipt.externalOutcome||"unknown");
        },
        onChange:content=>{
          if(session.starting)return;
          if(session.activePath!==openingPath || session.conflict ||
             ["aider-pending","aider-outcome-unverified"].includes(port.documentWriteStatus?.(openingPath)))return;
          state.set(ref(n.draft),content);session.dirty=content!==session.loadedValue;
          const tab=session.tabs.get(openingPath);
          if(tab){tab.draft=content;tab.dirty=session.dirty;}
          session.status.textContent=session.dirty?"Unsaved changes":"No unsaved changes";
          updateDocumentTabs(n,session);
          session.status.dataset.mcelDocumentDirty=String(session.dirty);
          session.close.disabled=!session.activePath || session.savePending;
          session.save.disabled=!session.editable || session.conflict || session.savePending ||
            (port.documentWriteStatus && port.documentWriteStatus(openingPath)!=="ready") ||
            !session.dirty || read(ref(n.path))!==session.activePath;
          session.discard.disabled=!session.dirty || read(ref(n.path))!==session.activePath;
        }
      }).then(receipt=>{
        session.starting=false;
        if(disposed)return;
        if(session.activePath!==openingPath){
          // A superseded completion must never dispose the newer editor.
          // The adapter itself rejects stale asynchronous mounts.
          traceOperation("editor.monaco.mount","settled","superseded");
          if(session.activePath)updateDocument(n,session);
          else session.surface.replaceChildren();
          return;
        }
        if(receipt?.ok){session.mountedPath=openingPath;session.status.textContent=session.conflict?
          "Source changed outside this tab; saving blocked":session.editable?"Opened: "+openingPath:"Source cannot be saved safely (truncated or invalid encoding).";}
        else session.status.textContent="Monaco unavailable: "+(receipt?.externalOutcome||"unknown");
        traceOperation("editor.monaco.mount","settled",receipt?.ok?"mounted":(receipt?.externalOutcome||"failed"));
      }).catch(error=>{
        session.starting=false;
        if(session.activePath!==openingPath){
          traceOperation("editor.monaco.mount","settled","superseded");
          if(session.activePath)updateDocument(n,session);
          return;
        }
        session.status.textContent="Monaco failed: "+String(error);
        traceOperation("editor.monaco.mount","settled","failed");
      });
    }
    function updateFolderTree(n,holder,tree){
      const layer=elt("div",{"data-mcel-folder-tree":"","role":"tree","aria-label":"Folder hierarchy"});
      const selectedRoot=tree.rootState?read(tree.rootState):"";
      if(tree.root!==selectedRoot){tree.root=selectedRoot;tree.paths.clear();tree.current="";}
      const select=n.selectionIntent && c.intents.get(ref(n.selectionIntent));
      const parameter=select?.input?.find(p=>p.id===ref(select.collectionSelection?.input));
      const visited=new Set();
      function branch(path,depth){
        if(depth>32 || visited.has(path))return;
        visited.add(path);
        const entries=tree.paths.get(path);if(!Array.isArray(entries))return;
        const group=elt("div",{"role":"group","data-mcel-tree-branch":path});
        for(const item of entries){
          const id=String(item[n.key]);
          const directory=item.kind==="directory" || item.kind==="dir";
          const row=elt("div",{"data-record-key":id,"data-mcel-tree-depth":depth,"role":"treeitem",
            "aria-expanded":directory?String(tree.paths.has(id)):"false"});
          const label=elt("span",{"data-record-field":"name"},String(item.name||id));
          const button=elt("button",{type:"button","data-select-key":id},directory?"Open folder":"Select");
          if(directory){
            button.addEventListener("click",()=>void execute(tree.intent.id,{[tree.pathParameter]:id}));
            // Inspection is a local semantic selection of an observed folder,
            // not filesystem read authority or a navigation action.
            if(select && parameter){
              const inspect=elt("button",{type:"button","data-mcel-inspect-folder":id},"Inspect folder");
              inspect.addEventListener("click",async()=>{
                if(path!==tree.current){
                  const reopened=await execute(tree.intent.id,{[tree.pathParameter]:path});
                  const fresh=read(ref(n.state));
                  if(reopened.disposition!=="completed" || !Array.isArray(fresh) ||
                      !fresh.some(row=>row && String(row[n.key])===id))return;
                }
                await execute(select.id,{[parameter.sourceName]:item[n.key]});
              });
              row.append(inspect);
            }
          }else if(path===tree.current && select && parameter){
            button.addEventListener("click",()=>void execute(select.id,{[parameter.sourceName]:item[n.key]}));
          }else if(select && parameter){
            // A cached branch is a navigation affordance, not current file
            // authority. Reopen its parent through the declared capability;
            // select only if the fresh listing still contains this exact key.
            button.title="Reopen this folder and select the file";
            button.addEventListener("click",async()=>{
              const reopened=await execute(tree.intent.id,{[tree.pathParameter]:path});
              const fresh=read(ref(n.state));
              if(reopened.disposition!=="completed" || !Array.isArray(fresh) ||
                  !fresh.some(row=>row && String(row[n.key])===id))return;
              await execute(select.id,{[parameter.sourceName]:item[n.key]});
            });
          }else{button.disabled=true;}
          row.append(label,button);group.append(row);
          if(directory && tree.paths.has(id))branchInto(id,depth+1,group);
        }
        return group;
      }
      function branchInto(path,depth,parent){const child=branch(path,depth);if(child)parent.append(child);}
      const top=branch("",0);if(top)layer.append(top);
      holder.replaceChildren(layer);
    }
    // The generated runtime owns ordinary collection presentation mechanics.
    // Reconcile DOM by declared stable record key; never re-create every row on
    // refresh. That preserves open inline detail, focus and scroll anchors.
    function plainValue(value) {
      if(value===undefined || value===null)return "Unavailable";
      if(typeof value==="object")return JSON.stringify(value);
      return String(value);
    }
    function updateTypedRecord(n,host) {
      const value=read(ref(n.value.state));
      const hadRecord=host.hasAttribute('data-mcel-structured-record');
      const expanded=new Set([...host.querySelectorAll('[data-mcel-measurement-detail]')]
        .filter(node=>node.open).map(node=>node.getAttribute('data-mcel-measurement-detail')));
      host.replaceChildren();
      if(value===undefined){host.append(elt('p',{'role':'status'},'No measurements loaded.'));return;}
      if(!value || typeof value!=='object' || Array.isArray(value)){
        host.textContent=plainValue(value);return;
      }
      host.setAttribute('data-mcel-structured-record','');
      const summary=typeof value.summary==='string'?value.summary:'';
      if(summary)host.append(elt('p',{'data-mcel-record-summary':''},summary));
      // Numeric percentages are measurements, never opaque JSON strings.
      // Associated availability flags distinguish a real zero from missing data.
      const metrics=elt('div',{'data-mcel-metric-board':''});
      const rendered=new Set();
      for(const [name,field] of Object.entries(value)){
        if(!name.endsWith('Percent') || typeof field!=='number')continue;
        const base=name.slice(0,-'Percent'.length);
        const hasValue=Number.isFinite(field) && value[name+'Available']!==false && value[base+'Available']!==false;
        const label=fmt(base).replace(/^Cpu$/,'CPU').replace(/^Gpu$/,'GPU');
        const card=elt('div',{'data-mcel-metric':name,'data-mcel-metric-available':String(hasValue)});
        const head=elt('div',{'data-mcel-metric-head':''});
        head.append(elt('span',{'data-mcel-metric-label':''},label),
          elt('strong',{'data-mcel-metric-value':''},hasValue?String(Math.round(field*10)/10)+'%':'Unavailable'));
        card.append(head);
        if(hasValue){
          const meter=elt('div',{'data-mcel-metric-meter':'','role':'meter','aria-label':label+' utilization',
            'aria-valuemin':'0','aria-valuemax':'100','aria-valuenow':String(Math.min(100,Math.max(0,field)))});
          const fill=elt('div',{'data-mcel-metric-fill':''});
          fill.style.width=String(Math.min(100,Math.max(0,field)))+'%';
          meter.append(fill);card.append(meter);
        }
        metrics.append(card);rendered.add(name);
      }
      if(metrics.childElementCount)host.append(metrics);
      const dl=elt('dl',{'data-mcel-measurements':''});
      const detailsList=[];
      for(const [name,field] of Object.entries(value)) {
        if(name==='summary' || rendered.has(name))continue;
        // These flags determine whether a metric is available and are reflected
        // in its visible value; redundant true/false fields are not telemetry.
        if(name.endsWith('Available') && typeof field==='boolean' &&
            (rendered.has(name.slice(0,-'Available'.length)) ||
             rendered.has(name.slice(0,-'Available'.length)+'Percent')))continue;
        if(Array.isArray(field) || (name.endsWith('Raw') && typeof field==='string') || (typeof field==='string' && field.length>220) ||
          (field && typeof field==='object')){
          const details=elt('details',{'data-mcel-measurement-detail':name});
          const label=Array.isArray(field)?`${fmt(name)} (${field.length})`:fmt(name);
          const detailBody=elt('div',{'data-mcel-measurement-body':''});
          details.append(elt('summary',{},label),detailBody);
          const paintDetails=()=>{
            if(!details.open)return;
            if(Array.isArray(field)){
              if(!field.length){detailBody.textContent='No entries reported.';return;}
              const entries=elt('ol',{});
              for(const item of field){const li=elt('li',{});
                if(item && typeof item==='object'){
                  const inner=elt('dl',{});
                  for(const [k,v] of Object.entries(item)){
                    if(k.endsWith('Available') && typeof v==='boolean' &&
                      Object.prototype.hasOwnProperty.call(item,k.slice(0,-'Available'.length)))continue;
                    const available=item[k+'Available']!==false;
                    inner.append(elt('dt',{},fmt(k)),elt('dd',{},available?plainValue(v):'Unavailable'));
                  }
                  li.append(inner);
                }else li.textContent=plainValue(item);
                entries.append(li);
              }
              detailBody.replaceChildren(entries);
            }else detailBody.textContent=plainValue(field);
          };
          details.addEventListener('toggle',paintDetails);
          // A small device collection is visible on first observation; long
          // per-core inventories remain discoverable without dominating the page.
          if(expanded.has(name) || (!hadRecord && Array.isArray(field) && field.length>0 && field.length<=4)){
            details.open=true;paintDetails();
          }
          detailsList.push(details);
        }else dl.append(elt('dt',{},fmt(name)),elt('dd',{},plainValue(field)));
      }
      if(dl.childElementCount)host.append(dl);
      host.append(...detailsList);
    }
    function updateSelectedRecord(n,host) {
      const selected=read(ref(n.value.state));
      const selections=[...c.intents.values()].filter(i=>i.collectionSelection &&
        ref(i.collectionSelection.target)===ref(n.value.state));
      host.replaceChildren();
      if(!selected){host.append(elt("p",{},"Select a record to examine its available details."));return;}
      // Multiple semantic collections may share the same selected identity.
      // Never bind inspection to whichever collection intent happened to be
      // declared first; find the actually observed identity in its source.
      let record=null;
      for(const selection of selections){
        const source=selection.collectionSelection;
        const values=read(ref(source.source));
        if(!Array.isArray(values))continue;
        record=values.find(v=>v && String(v[source.key])===String(selected))||null;
        if(record)break;
      }
      if(!record){host.append(elt("p",{"data-mcel-selection-stale":"true"},
        "Selected identity is no longer present in this observation: "+selected));return;}
      const list=elt("dl",{"data-mcel-selected-details":selected});
      for(const [key,value] of Object.entries(record)) {
        list.append(elt("dt",{},fmt(key)),elt("dd",{},plainValue(value)));
      }
      host.append(list);
    }
    function updateGeneratedCollection(n,holder,list) {
      if(!Array.isArray(list)) {
        keyedCollectionRows.delete(holder);
        holder.replaceChildren(elt("p",{"role":"status"},"No observation loaded. Use the query above to retrieve records."));
        return;
      }
      const selection=n.selectionIntent && c.intents.get(ref(n.selectionIntent));
      const selectionValue=selection?.collectionSelection && read(ref(selection.collectionSelection.target));
      let existing=keyedCollectionRows.get(holder);
      if(!existing)existing=new Map();
      const next=new Map(), ordered=[];
      const box=holder.getBoundingClientRect();
      const anchor=[...holder.children].find(row=>row.hasAttribute("data-record-key") &&
        row.getBoundingClientRect().bottom>box.top);
      const anchorKey=anchor?.getAttribute("data-record-key");
      const anchorTop=anchor?.getBoundingClientRect().top;
      const previouslyFocused=holder.contains(document.activeElement)?document.activeElement:null;
      for(const record of list) {
        const key=String(record[n.key]);
        let row=existing.get(key);
        if(!row) {
          row=elt("div",{"data-record-key":key,"data-mcel-record-row":""});
          for(const field of n.fields || [])row.append(elt("span",{"data-record-field":field,"data-field-label":fmt(field)}));
          if(selection?.collectionSelection){
            const parameter=selection.input.find(p=>p.id===ref(selection.collectionSelection.input));
            const choose=elt("button",{type:"button","data-select-key":key},"Select");
            choose.addEventListener("click",()=>void execute(selection.id,{[parameter.sourceName]:key}));
            row.append(choose);
          }
          const expand=elt("details",{"data-mcel-inline-details":key});
          expand.append(elt("summary",{},"More details"),elt("dl",{"data-mcel-detail-fields":""}));
          row.append(expand);
        }
        for(const field of n.fields || []) {
          const el=[...row.querySelectorAll("[data-record-field]")].find(e=>e.getAttribute("data-record-field")===field);
          if(el)el.textContent=plainValue(record[field]);
        }
        const expanded=row.querySelector("[data-mcel-inline-details]");
        const more=Object.entries(record).filter(([key])=>key!==n.key && !(n.fields||[]).includes(key));
        expanded.hidden=!more.length;
        const detail=expanded.querySelector("[data-mcel-detail-fields]");
        if(expanded.open || !detail.childElementCount){
          detail.replaceChildren(...more.flatMap(([key,val])=>[elt("dt",{},fmt(key)),elt("dd",{},plainValue(val))]));
        }
        row.setAttribute("aria-selected",String(selectionValue!==undefined && selectionValue!=="" && key===String(selectionValue)));
        next.set(key,row);ordered.push(row);
      }
      // Insert only moved or newly observed nodes. Unchanged nodes never leave
      // the DOM; neither their expansion nor their tab focus is reset.
      for(let i=0;i<ordered.length;i++)if(holder.children[i]!==ordered[i])
        holder.insertBefore(ordered[i],holder.children[i]||null);
      for(const [key,row] of existing)if(!next.has(key))row.remove();
      if(!ordered.length) {
        holder.replaceChildren(elt("p",{"role":"status"},"No records match the current observation."));
      }else{
        for(const child of [...holder.children])if(!child.hasAttribute("data-record-key"))child.remove();
      }
      keyedCollectionRows.set(holder,next);
      if(anchorKey && next.has(anchorKey) && Number.isFinite(anchorTop)) {
        const moved=next.get(anchorKey).getBoundingClientRect().top-anchorTop;
        if(Math.abs(moved)>0.5)holder.scrollTop+=moved;
      }
      if(previouslyFocused && holder.contains(previouslyFocused) &&
         document.activeElement!==previouslyFocused)previouslyFocused.focus({preventScroll:true});
    }
    function updateCollection(n,holder) {
      const list=read(ref(n.state));
      if(c.generated){updateGeneratedCollection(n,holder,list);return;}
      holder.replaceChildren();
      if (!Array.isArray(list)) {
        const location=navigationRoots.some(nav=>ref(n.state)===nav.entriesState);
        holder.append(elt("p",{role:"status"},location?"Loading directory or waiting for an accessible location…":"No records loaded"));return;
      }
      const tree=folderTrees.get(n);
      if(tree?.paths.size && !tree.searchResults){updateFolderTree(n,holder,tree);return;}
      const select = n.selectionIntent && c.intents.get(ref(n.selectionIntent));
      for (const item of list) {
        const row=elt("div",{"data-record-key":String(item[n.key])});
        for (const field of n.fields || []) row.append(elt("span",{"data-record-field":field,"data-field-label":fmt(field)},String(item[field]??"")));
        if (select?.collectionSelection) {
          const parameter=select.input.find(p=>p.id===ref(select.collectionSelection.input));
          const opensDocument=[...documentHosts.keys()].some(doc=>ref(doc.path)===ref(select.collectionSelection.target));
          const navigation=navigationRoots.find(nav=>nav.selection.id===select.id);
          const selected=navigation && String(read(navigation.rootState))===String(item[n.key]);
          if(navigation)row.setAttribute("aria-selected",String(!!selected));
          const button=elt("button",{type:"button","data-select-key":String(item[n.key])},
            navigation?(selected?"Current · refresh":"Open location"):opensDocument?"Open":"Select");
          const regionId=holder.closest("[data-mcel-region]")?.getAttribute("data-mcel-region");
          const region=c.regions.get(regionId);
          const directoryForm=(region?.members||[]).map(m=>c.nodes.get(ref(m))).find(node=>{
            const action=c.intents.get(ref(node?.intent));
            return node?.nodeKind==="form" && action?.operationKind==="capability" &&
              Object.keys(node.fields||{}).length===1 &&
              (node.fields.path?.kind==="string" || node.fields.relativePath?.kind==="string");
          });
          const directory=item.kind==="dir"||item.kind==="directory";
          if(directory && directoryForm && (typeof item.path==="string" || typeof item.id==="string")){
            button.textContent="Open folder";
            const field=directoryForm.fields.path ? "path" : "relativePath";
            button.addEventListener("click",()=>void execute(ref(directoryForm.intent),{[field]:item.path??item.id}));
          }else{
            button.addEventListener("click",()=>void execute(select.id,{[parameter.sourceName]:item[n.key]}));
          }
          row.append(button);
        }
        holder.append(row);
      }
    }
    const allowedTags=new Set(["main","section","nav","aside","header","footer","article","output","div"]);
    function renderRegion(id) {
      const r=c.regions.get(id);
      region=r;
      const tag=allowedTags.has(r.nativeTag) ? r.nativeTag : "section";
      const inferredContent=r.content?.kind ||
        (r.members?.some(member=>c.nodes.get(ref(member))?.nodeKind==="collection") ? "collection" : "");
      const el=elt(tag,{"data-mcel-region":r.id,"data-mcel-purpose":r.purpose || "","data-mcel-priority":r.priority || "", "data-mcel-tasks":(r.tasks || []).join(" "),"data-mcel-content-kind":inferredContent});
      regionHosts.set(r,el);
      if(c.generated && r.recipe!=="root") {
        const titles={queries:"Observation query",collection:"Observed records",
          "selection-echo":"Selected record",evidence:"Operation evidence",
          results:"Measurements and results",controls:"Available controls",document:"Document"};
        if(r.recipe==="controls" && r.members.every(m=>c.intents.get(ref(c.nodes.get(ref(m))?.intent))?.viewTransition))
          titles.controls="Monitoring views";
        el.append(elt("h2",{},titles[r.recipe]));
      }
      for (const annotation of r.staticContent || []) {
        if (["h1","h2","h3","h4","h5","h6","p","span","strong","em","small","label"].includes(annotation.tag))
          el.append(elt(annotation.tag, {}, annotation.text));
      }
      for (const m of r.members || []) el.append(renderNode(c.nodes.get(ref(m))));
      for (const child of c.children.get(id)) el.append(renderRegion(child.id));
      return el;
    }
    // Poll only explicitly declared read-only monitoring regions. The
    // currently selected view determines which result state is refreshed.
    // No autonomous writes, scheduling, process controls, or AI requests.
    function selectedMonitorIntent(){
      const visibleRegions=[...c.regions.values()].filter(r=>r.tasks?.includes("monitor") &&
        (!r.visibleWhen || read(ref(r.visibleWhen.state))===r.visibleWhen.equals));
      const targets=new Set();
      const viewBound=[...c.intents.values()].some(i=>!!i.viewTransition);
      for(const r of visibleRegions)for(const member of r.members||[]){
        const n=c.nodes.get(ref(member));
        // A selected monitor region is sufficient visibility evidence even
        // when its children do not duplicate the region's visibility rule.
        // Still refuse an unscoped/always-visible result as an implicit poll
        // target when the app has an explicit mode selector.
        if(viewBound && !n?.visibleWhen && !r.visibleWhen)continue;
        if(n?.visibleWhen && !applicable(n))continue;
        if(n?.nodeKind==="collection")targets.add(ref(n.state));
        if(n?.nodeKind==="state-value")targets.add(ref(n.value?.state));
      }
      const eligible=[...c.intents.values()].filter(i=>i.operationKind==="capability" &&
        i.risk==="external-read" && i.resultBindings?.some(b=>targets.has(ref(b.target))) &&
        i.input?.every(p=>p.schema?.kind==="string" &&
          ["query","limit","includeConnections"].includes(p.sourceName)));
      return eligible.length===1?eligible[0]:null;
    }
    function installMonitorRefresh(){
      if(!selectedMonitorIntent())return;
      const controls=elt("div",{"data-mcel-auto-refresh":"","role":"group","aria-label":"Automatic monitoring refresh"});
      const button=elt("button",{type:"button","data-mcel-refresh-toggle":"","aria-pressed":"true"},"Pause automatic refresh");
      const status=elt("span",{"role":"status","data-mcel-refresh-status":""},"Refresh every 5 seconds when visible");
      button.addEventListener("click",()=>{
        refreshEnabled=!refreshEnabled;button.setAttribute("aria-pressed",String(refreshEnabled));
        traceOperation("monitor.auto-refresh","settled",refreshEnabled?"resumed":"paused");
        button.textContent=refreshEnabled?"Pause automatic refresh":"Resume automatic refresh";
        status.textContent=refreshEnabled?"Refresh every 5 seconds when visible":"Automatic refresh paused";
      });
      controls.append(button,status);root.prepend(controls);
      const pollMonitor=async()=>{
        if(disposed||!refreshEnabled||refreshBusy||document.visibilityState==="hidden")return;
        const intent=selectedMonitorIntent();if(!intent)return;
        refreshBusy=true;
        try{
          // Generate polling inputs from the *semantic form defaults* when no
          // operator request has yet supplied remembered inputs. This works
          // for monitoring surfaces of any application, without fixed geometry.
          const form=[...c.nodes.values()].find(n=>n.nodeKind==="form" && ref(n.intent)===intent.id);
          const defaults=form?.defaults||{};
          const inputs=rememberedInputs.get(intent.id)||Object.fromEntries((intent.input||[]).map(p=>
            [p.sourceName,String(defaults[p.sourceName]??"")]));
          const result=await execute(intent.id,inputs);
          traceOperation("monitor.auto-refresh","settled",result.disposition);
          status.textContent="Last refresh: "+result.disposition;
        }finally{refreshBusy=false;}
      };
      refreshTimer=global.setInterval?.(pollMonitor,taskRefreshPeriodMs);
      // An authored monitoring view should show its initial snapshot
      // without a manual action or waiting for the next polling period.
      initialRefreshTimer=global.setTimeout?.(()=>{
        initialRefreshTimer=null;
        const intent=selectedMonitorIntent();
        if(intent && !receipts.some(r=>r.intentId===intent.id))void pollMonitor();
      },75);
      refreshers.push(controls);
    }
    const tree=renderRegion(c.rootId);
    // Identify the read-only navigation chain by IR relationships only.
    for(const [node,directory] of folderTrees){
      if(!directory.rootState)continue;
      const selection=[...c.intents.values()].find(i=>i.collectionSelection &&
        ref(i.collectionSelection.target)===directory.rootState);
      if(!selection)continue;
      const rootsState=ref(selection.collectionSelection.source);
      const loader=[...c.intents.values()].find(i=>i.operationKind==="capability" &&
        i.risk==="external-read" && !(i.input||[]).length && !(i.requestArguments||[]).length &&
        (i.resultBindings||[]).some(b=>ref(b.target)===rootsState));
      const browse=[...c.intents.values()].find(i=>i.operationKind==="capability" &&
        i.risk==="external-read" && !(i.input||[]).length && i.requestArguments?.length===1 &&
        ref(i.requestArguments[0].state)===directory.rootState &&
        (i.resultBindings||[]).some(b=>ref(b.target)===ref(node.state)));
      const inputName=selection.input?.find(p=>p.id===ref(selection.collectionSelection.input))?.sourceName;
      if(loader && browse && inputName)navigationRoots.push({
        loader,selection,browse,rootsState,rootState:directory.rootState,
        entriesState:ref(node.state),key:selection.collectionSelection.key,inputName});
    }
    if(workspacePlan) plannedLayout=global.McelSemanticLayoutInference.mountWorkspacePlan(
      tree, workspacePlan, new Map([...regionHosts].map(([region,el])=>[region.id,el])));
    root.replaceChildren(tree);
    for(const {host,node} of pendingChatMounts){
      const controller=port.mountEmbeddedChat(host,node,read);
      assert(controller,"embedded-chat-mount-failed");
      chatControllers.push(controller);
    }
    root.setAttribute("data-mcel-semantic-operational",ir.application.appId);
    if (expert) root.setAttribute("data-mcel-presentation","expert");
    notify();
    if(c.generated) {
      // Build the shared layout evaluator's inputs from *generated* region
      // semantics, never from application markup, selectors or a DSL layout.
      const engine=global.McelSemanticLayoutInference;
      const rootRegion=c.regions.get(c.rootId);
      const selector=id=>'[data-mcel-region="'+id+'"]';
      const layoutGrammar={regions:[...c.regions.values()].map(r=>({id:r.id,
        selector:selector(r.id),children:(c.children.get(r.id)||[]).map(child=>child.id)})),constraints:[]};
      const layoutHost=root.querySelector(selector(rootRegion.id));
      let layoutScheduled=false;
      const updateGeometry=()=>{
        if(disposed)return;
        const width=Math.max(1,layoutHost.getBoundingClientRect().width);
        const visible=[...c.regions.values()].filter(r=>!regionHosts.get(r)?.hidden);
        const visibleIds=new Set(visible.map(r=>r.id));
        // Choose primary by *visible semantic content*; the selected notebook
        // changes focus, not the application's structural composition.
        const primary=visible.find(r=>r.recipe==="collection") ||
          visible.find(r=>r.recipe==="document") || visible.find(r=>r.recipe==="results");
        const semanticSurface={regions:[...c.regions.values()].map(r=>({
          id:r.id,selector:selector(r.id),primary:r.id===primary?.id,
          presentationRole:r.recipe==="selection-echo"?"context":"supporting",
          focusModes:r.id===primary?.id?[options.composition.purpose]:[]
        }))};
        if(!primary)return;
        const next=engine.inferWorkspaceLayout({semanticSurface,layoutGrammar,
          visibleRegions:[...visibleIds],mode:options.composition.purpose,width});
        inferredLayout=engine.applyWorkspaceLayout(root,next,inferredLayout);
      };
      updateGeometry();
      const scheduleGeometry=()=>{
        if(layoutScheduled||disposed)return;
        layoutScheduled=true;
        global.requestAnimationFrame?.(()=>{layoutScheduled=false;updateGeometry();});
      };
      scheduleGeneratedLayout=scheduleGeometry;
      if(typeof global.ResizeObserver==="function"){
        generatedResizeObserver=new global.ResizeObserver(scheduleGeometry);
        generatedResizeObserver.observe(layoutHost);
      } else if(typeof global.addEventListener==="function") {
        generatedResizeListener=scheduleGeometry;
        global.addEventListener("resize",generatedResizeListener);
      }
    }
    installMonitorRefresh();
    // The incumbent automatically discovers and lists its preferred root.
    // Equivalent read-only startup is safe only with one unambiguous,
    // source-declared navigation chain; all requests pass the same port gates.
    if(navigationRoots.length===1)void execute(navigationRoots[0].loader.id);
    return Object.freeze({execute,readState:id=>clone(read(id)),receipts:()=>receipts.map(x=>({...x})),
      getState:()=>Object.fromEntries([...state.entries()].map(([k,v])=>[k,clone(v)])),
      unmount:()=>{disposed=true;if(refreshTimer!==null)global.clearInterval?.(refreshTimer);
        if(initialRefreshTimer!==null)global.clearTimeout?.(initialRefreshTimer);
        plannedLayout?.unmount();
        generatedResizeObserver?.disconnect();
        if(generatedResizeListener)global.removeEventListener?.("resize",generatedResizeListener);
        if(inferredLayout)global.McelSemanticLayoutInference?.clearWorkspaceLayout?.(root,inferredLayout);
        current.clear();targetRuns.clear();
        port.closeDocument?.();
        for(const controller of chatControllers)controller.destroy?.();
        if(documentHosts.size) global.MainComputerMonacoAdapter?.dispose?.("semantic-unmount");
        root.replaceChildren();root.removeAttribute("data-mcel-semantic-operational");}});
  }
  global.McelSemanticOperationalRuntime = Object.freeze({mount,checkSchema,contract});
})(typeof globalThis !== "undefined" ? globalThis : this);
