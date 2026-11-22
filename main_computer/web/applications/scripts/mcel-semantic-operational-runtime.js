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
      !own(when,"path") && s.schema.kind==="string" &&
      (s.schema.enum?.includes(v) || !s.schema.enum && typeof v==="string"),
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
    const modeStates=new Set([...intents.values()].flatMap(i=>
      i.viewTransition?[ref(i.viewTransition.target)]:
      i.collectionSelection?[ref(i.collectionSelection.target)]:[]));
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
        evidence:"footer",results:"section",controls:"section",operations:"section",document:"section",navigation:"nav",
        task:"section",commands:"section",inspection:"aside"};
      const allowedPurposes=new Set(["monitor","compare","inspect","navigate","manage"]);
      assert(allowedPurposes.has(composition.purpose),"generated-purpose-invalid");
      const usedRecipes=new Set();
      const usedRegionIds=new Set();
      const authoredNavigationIds=new Set((surface.interfaceIntent?.regions||[])
        .filter(r=>["navigation","mode-selector"].includes(r.role))
        .map(r=>"interface-region:generated-navigation-"+r.id.split(".").pop()));
      const authoredTaskIds=new Set((surface.interfaceIntent?.regions||[])
        .filter(r=>["task","commands","inspector"].includes(r.role))
        .map(r=>"interface-region:generated-"+(r.role==="inspector"?"inspection":r.role)+"-"+r.id.split(".").pop()));
      for (const n of surface.nodes || []) {
        if(n.visibleWhen)validateVisibility(n.visibleWhen,states,modeStates);
        if(n.enabledWhen){
          assert(n.nodeKind === "control", "enablement-control-required");
          validateVisibility(n.enabledWhen,states,modeStates);
        }
      }
      for (const r of composition.regions) {
        const expectedTasks=composition.policy==="mcel.semantic-structure.p3" &&
          composition.purpose==="monitor" && ["collection","results"].includes(r.recipe)
          ? ["monitor"] : [];
        if(["task","commands","inspection"].includes(r.recipe))assert(typeof r.semanticTitle==="string" &&
          /^[a-z][a-z0-9-]*$/.test(r.semanticTitle),"generated-task-title-invalid");
        const namedNavigation=r?.recipe==="navigation" && authoredNavigationIds.has(r.id);
        const namedTask=["task","commands","inspection"].includes(r?.recipe) && authoredTaskIds.has(r.id);
        assert(r && tags[r.recipe] === r.nativeTag && !usedRegionIds.has(r.id) &&
          (["navigation","task","commands","inspection"].includes(r.recipe) || !usedRecipes.has(r.recipe)) &&
          Array.isArray(r.members) && Array.isArray(r.tasks) &&
          JSON.stringify(r.tasks)===JSON.stringify(expectedTasks) &&
          Array.isArray(r.reasons) && r.reasons.length > 0 &&
          (r.id === "interface-region:generated-" + r.recipe || namedNavigation || namedTask),
          "generated-region-invalid");
        usedRegionIds.add(r.id);
        usedRecipes.add(r.recipe);
      }
      // A configuration task is a valid primary workspace without any
      // collection. Do not synthesize a fake record list to satisfy layout.
      assert(usedRecipes.has("root") &&
        (usedRecipes.has("collection") || usedRecipes.has("task") || usedRecipes.has("document")) &&
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
      const expected={form:"queries",collection:"collection","record-notebook":"collection","operation-notebook":"collection",control:"controls",
        "operation-evidence":"evidence","document-editor":"document"};
      const navigationMembers=new Set();
      for(const authored of surface.interfaceIntent?.regions || []) {
        if(["navigation","mode-selector"].includes(authored.role))
          for(const member of authored.members || [])navigationMembers.add(ref(member));
      }
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
        const declared=(surface.interfaceIntent?.regions||[]).find(a=>
          (a.members||[]).some(m=>ref(m)===n.id));
        const recipe=navigationMembers.has(n.id) ? "navigation" :
          declared?.role==="task" ? "task" : declared?.role==="commands" ? "commands" :
          declared?.role==="inspector" ? "inspection" :
          composition.policy==="mcel.semantic-structure.p3" &&
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
        for(const [field,hint] of Object.entries(n.placeholders||{})){
          assert(n.fields?.[field]?.kind==="string" &&
            typeof hint==="string" && hint.trim().length>0 && hint.length<=256,
            "invalid-form-placeholder:"+n.id+":"+field);
        }
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
      assert(["form","control","state-value","collection","record-notebook","operation-notebook","plot","operation-evidence","document-editor","embedded-chat"].includes(n.nodeKind), "unsupported-surface-node:" + n.nodeKind);
      if(n.nodeKind==="operation-evidence" && own(n,"activityEntries")){
        assert(Array.isArray(n.activityEntries),"invalid-activity-entries:"+n.id);
        const keys=new Set();
        const dispositions=new Set(["completed","accepted-deferred","command-returned",
          "recorded","refused","refused-before-attempt","failed","cancelled","unknown"]);
        for(const item of n.activityEntries){
          assert(item && item.kind==="activity-entry" && ["info","warn","error"].includes(item.severity) &&
            typeof item.message==="string" && item.message.trim().length>0 && item.message.length<=200 &&
            intents.get(ref(item.intent))?.operationKind==="capability" &&
            Array.isArray(item.dispositions) && item.dispositions.length>0 &&
            item.dispositions.every(d=>dispositions.has(d)),"invalid-activity-entry:"+n.id);
          for(const disposition of item.dispositions){
            const key=ref(item.intent)+":"+disposition;
            assert(!keys.has(key),"duplicate-activity-entry:"+n.id);keys.add(key);
          }
        }
      }
      if (n.intent) assert(intents.has(ref(n.intent)), "unbound-intent:" + n.id);
      for(const item of n.resetOutputs||[]){
        const target=states.get(ref(item.state));
        assert(n.nodeKind==="control" && target?.authority==="derived" &&
          target.schema?.kind==="string" && typeof item.value==="string" &&
          item.value.length<=4096,"invalid-control-reset:"+n.id);
      }
      if (n.state) assert(states.has(ref(n.state)), "unbound-state:" + n.id);
      if(n.rowLayout){
        const layout=n.rowLayout, fields=n.fields||[];
        const ordered=[...(layout.identity||[]),layout.description,...(layout.metadata||[])];
        assert(n.nodeKind==="collection" && layout.selectOnRow===true &&
          Array.isArray(layout.identity) && layout.identity.length>=1 && layout.identity.length<=3 &&
          Array.isArray(layout.metadata) && layout.metadata.length<=4 &&
          typeof layout.description==="string" && n.selectionIntent &&
          ordered.length===fields.length && new Set(ordered).size===ordered.length &&
          ordered.every(f=>typeof f==="string" && fields.includes(f)),
          "invalid-collection-row-layout:"+n.id);
      }
      if (n.selectionIntent) assert(intents.has(ref(n.selectionIntent)), "unbound-selection:" + n.id);
      if(n.clearable)assert(n.nodeKind==="collection" && n.selectionIntent &&
        intents.get(ref(n.selectionIntent))?.collectionSelection?.empty==="clear",
        "clearable-selection-contract-invalid:"+n.id);
      if(n.roleFrom){
        const src=states.get(ref(n.roleFrom.state));
        assert(n.nodeKind==="collection" && src?.authority==="derived" && src.schema?.kind==="list" &&
          src.schema.items?.fields?.[n.roleFrom.key]?.kind==="string" &&
          src.schema.items?.fields?.[n.roleFrom.roleField]?.kind==="string",
          "role-projection-contract-invalid:"+n.id);
      }
      if (n.nodeKind === "record-notebook") {
        assert(typeof n.title==="string" && Array.isArray(n.tabs) && n.tabs.length>=2 && n.tabs.length<=8,
          "invalid-notebook:"+n.id);
        for(const tab of n.tabs){
          const source=states.get(ref(tab.state)), refresh=intents.get(ref(tab.refresh));
          assert(source?.authority==="derived" && source.schema?.kind==="list" &&
            source.schema.key===tab.key && refresh?.risk==="external-read" &&
            refresh.resultBindings?.some(b=>ref(b.target)===source.id),"invalid-notebook-tab:"+n.id);
          for(const action of Object.values(tab.actions||{})){
            const intent=intents.get(ref(action.intent));
            assert(intent?.risk==="consequential" && intent.input?.length===1 &&
              typeof intent.input[0].sourceName==="string" && intent.input[0].sourceName.length>0 &&
              intent.input[0].schema?.kind==="string",
              "invalid-notebook-action:"+n.id);
          }
        }
        if(n.updates)assert(n.updates.rows===true && n.updates.status===true &&
          Object.keys(n.updates).every(key=>["rows","status","retention"].includes(key)) &&
          (!own(n.updates,"retention") || (n.updates.retention===true && !!n.retention)),
          "invalid-notebook-updates:"+n.id);
        if(n.retention){
          const policy=states.get(ref(n.retention.state)), readPolicy=intents.get(ref(n.retention.refresh));
          const updatePolicy=intents.get(ref(n.retention.update));
          assert(policy?.authority==="derived" && policy.schema?.fields?.enabled?.kind==="boolean" &&
            readPolicy?.risk==="external-read" && readPolicy.resultBindings?.some(b=>ref(b.target)===policy.id) &&
            updatePolicy?.risk==="consequential" && updatePolicy.input?.length===1 &&
            updatePolicy.input[0].schema?.kind==="boolean",
            "invalid-notebook-retention:"+n.id);
        }
        if(n.empty){
          const empty=intents.get(ref(n.empty));
          assert(empty?.risk==="consequential" && empty.input?.length===0,
            "invalid-notebook-empty:"+n.id);
        }
      }
      if (n.nodeKind === "operation-notebook") {
        assert(typeof n.title==="string" && n.title.length>0 && n.title.length<=120 &&
          Array.isArray(n.tabs) && n.tabs.length>=2 && n.tabs.length<=8,
          "invalid-operation-notebook:" + n.id);
        if(n.authentication){
          const auth=intents.get(ref(n.authentication.intent));
          assert(auth?.operationKind==="capability" && auth.risk==="consequential" &&
            auth.input?.length===0 && typeof n.authentication.label==="string" &&
            n.authentication.label.length>0 && n.authentication.label.length<=80,
            "invalid-operation-notebook-auth:"+n.id);
        }
        const seen=new Set();
        for(const tab of n.tabs){
          const source=states.get(ref(tab.state)), refresh=intents.get(ref(tab.refresh));
          assert(typeof tab.id==="string" && /^[a-z][a-z0-9-]*$/.test(tab.id) &&
            !seen.has(tab.id) && typeof tab.label==="string" && tab.label.length>0 && tab.label.length<=80 &&
            source?.authority==="derived" && source.schema?.kind==="record" &&
            Array.isArray(tab.fields) && tab.fields.length>0 &&
            tab.fields.every(field=>typeof field==="string" && own(source.schema.fields||{},field)) &&
            refresh?.operationKind==="capability" && refresh.risk==="external-read" &&
            refresh.resultBindings?.some(binding=>ref(binding.target)===source.id) &&
            (tab.note===undefined || typeof tab.note==="string" && tab.note.length<=400),
            "invalid-operation-notebook-tab:" + n.id);
          seen.add(tab.id);
          if(tab.updates)assert(Array.isArray(tab.updates.fields) &&
            tab.updates.fields.length===tab.fields.length &&
            tab.updates.fields.every(field=>tab.fields.includes(field)) &&
            Array.isArray(tab.updates.actions) &&
            tab.updates.status===true && typeof tab.updates.forms==="boolean" &&
            tab.updates.forms===!!tab.forms?.length &&
            Object.keys(tab.updates).length===4,
            "invalid-operation-notebook-updates:"+n.id+":"+tab.id);
          assert(tab.actions && typeof tab.actions==="object" && !Array.isArray(tab.actions) &&
            Object.keys(tab.actions).length<=6,"invalid-operation-notebook-actions:" + n.id);
          if(tab.updates)assert(tab.updates.actions.length===Object.keys(tab.actions).length &&
            tab.updates.actions.every(label=>Object.hasOwn(tab.actions,label)),
            "invalid-operation-notebook-update-actions:"+n.id+":"+tab.id);
          for(const [label,action] of Object.entries(tab.actions)){
            const intent=intents.get(ref(action?.intent));
            assert(label.length>0 && label.length<=40 && intent?.operationKind==="capability" &&
              intent.risk==="consequential" && intent.input?.length===0 &&
              (action.alwaysEnabled===undefined || action.alwaysEnabled===true) &&
              !(action.alwaysEnabled && action.enabledWhen) &&
              (action.confirm===undefined || typeof action.confirm==="string" && action.confirm.length<=240),
              "invalid-operation-notebook-action:" + n.id);
            if(action.enabledWhen)validateVisibility(action.enabledWhen,states,modeStates);
          }
        }
      }
      if (n.nodeKind === "state-value") assert(states.has(ref(n.value?.state)), "unbound-display:" + n.id);
      if (n.nodeKind === "document-editor") {
        if(n.externalJob){
          const job=n.externalJob,launch=intents.get(ref(job.launchIntent)),refresh=intents.get(ref(job.statusIntent));
          assert(launch?.operationKind==="capability" && launch.risk==="consequential" &&
            refresh?.operationKind==="capability" && refresh.risk==="external-read" &&
            typeof job.pathArgument==="string" &&
            launch.requestArguments?.some(a=>a.name===job.pathArgument) &&
            typeof job.filesArgument==="string" &&
            launch.input?.some(a=>a.sourceName===job.filesArgument && a.schema?.kind==="string") &&
            [job.pendingStatus,job.unverifiedStatus,job.acceptedPrefix].every(s=>typeof s==="string" && s.length>0),
            "invalid-document-external-job:"+n.id);
        }
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
      tools:"tab-group", details:"inspector", status:"pinned-compact"};
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
          zone.id === "details" ? region.purpose === "inspection" :
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
    // Authored descendants belong to their top-level zone. A nested group
    // cannot be promoted to an independent dock by insertion order or DOM
    // position; its parent region owns it and renderRegion retains the tree.
    const rootChildren=c.children.get(c.rootId)||[];
    assert(names.has("workspace") && covered.size===rootChildren.length &&
      rootChildren.every(r=>covered.has(r.id)) &&
      [...covered].every(id=>c.regions.get(id).parentId===c.rootId),
      "workspace-plan-incomplete");
    const nested=[...c.regions.values()].filter(r=>r.id!==c.rootId&&r.parentId!==c.rootId)
      .map(r=>({id:r.id,parentId:r.parentId}));
    assert(JSON.stringify(plan.nestedGroups||[])===JSON.stringify(nested),
      "workspace-nested-group-mismatch");
    if(plan.documentWorkbench){
      const layout=plan.documentWorkbench;
      assert(layout.schema==="mcel.document-workbench.v1" &&
        typeof layout.document==="string" && c.regions.has(layout.document) &&
        ["navigation","companion","bottom","status"].every(k=>Array.isArray(layout[k]) && layout[k].length>0),
        "document-workbench-invalid");
      const doc=c.regions.get(layout.document);
      assert(doc.priority==="primary" && doc.members.some(m=>c.nodes.get(ref(m))?.nodeKind==="document-editor"),
        "document-workbench-anchor-invalid");
      const groups=[[layout.document],layout.navigation,layout.companion,layout.bottom,layout.status];
      const ids=groups.flat();
      assert(ids.length===rootChildren.length && new Set(ids).size===ids.length &&
        ids.every(id=>c.regions.has(id) && c.regions.get(id).parentId===c.rootId) &&
        layout.navigation.every(id=>c.regions.get(id).purpose==="navigation" && c.regions.get(id).preferences?.side==="start") &&
        layout.companion.every(id=>c.regions.get(id).preferences?.side==="end") &&
        layout.bottom.every(id=>c.regions.get(id).preferences?.side==="bottom") &&
        layout.status.every(id=>c.regions.get(id).purpose==="status" && c.regions.get(id).preferences?.side==="bottom"),
        "document-workbench-coverage-invalid");
      assert(global.McelSemanticDocumentWorkbench?.mount,
        "document-workbench-engine-missing");
    }
    // The semantic template binding is part of the source-verified workspace
    // plan, not a post-mount application script. The host recomputes policy and
    // arguments from canonical IR; the browser independently checks exact
    // coverage and parent/zone ownership before constructing any DOM.
    if(plan.semanticTemplate){
      const t=plan.semanticTemplate,a=t.arguments;
      assert(t.schema==="mcel.semantic-template-binding.v1" &&
        typeof t.profile==="string" && /^[a-z][a-z0-9-]*$/.test(t.profile) &&
        t.sourceIrHash===ir.fingerprints?.semantic &&
        typeof t.argumentHash==="string" && /^sha256:[a-f0-9]{64}$/.test(t.argumentHash) &&
        t.authority==="canonical-ir-and-deterministic-graph-match" &&
        a?.profile===t.profile && plan.documentWorkbench &&
        JSON.stringify(a.docks)===JSON.stringify(plan.documentWorkbench),
        "semantic-template-source-or-layout-mismatch");
      assert(["authored","inferred"].includes(a.grouping),"semantic-template-grouping-unknown");
      if(a.grouping==="inferred"){
        const region=c.regions.get(a.region), groups=a.groups;
        assert(region && region.purpose===a.regionPurpose &&
          plan.documentWorkbench.companion.includes(a.region) &&
          groups && typeof groups==="object" && !Array.isArray(groups) &&
          Object.keys(groups).length>=1 && Object.keys(groups).length<=16,
          "semantic-template-region-invalid");
        const expected=(region.members||[]).map(m=>ref(m));
        const actual=[];
        for(const [name, ids] of Object.entries(groups)){
          assert(/^[a-z][a-z0-9-]*$/.test(name) && Array.isArray(ids) && ids.length>0 &&
            ids.every(id=>typeof id==="string" && c.nodes.has(id)),
            "semantic-template-argument-invalid");
          actual.push(...ids);
        }
        assert(actual.length===expected.length && new Set(actual).size===actual.length &&
          actual.every(id=>expected.includes(id)),"semantic-template-coverage-invalid");
      } else {
        assert(a.groups && Object.keys(a.groups).length===0,
          "semantic-template-authored-groups-conflict");
      }
    }
    return plan;
  }
  function elt(tag, attrs, text) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
    if (text !== undefined) n.textContent = String(text);
    return n;
  }
  // Deterministic candidate scheduler, first certified in independent isolated
  // comparisons. Patch 4 additionally permits an explicitly opted-in normal
  // application mount; without that opt-in the incumbent remains authoritative.
  // Its graph stores dependency identifiers only, not state values or receipts.
  function createSignalCandidate(config={}) {
    const isolated=config.isolated===true;
    const integrated=config.integrated===true&&!isolated;
    const selectiveNotify=(isolated||integrated)&&config.selectiveInNotify===true;
    const selectiveKinds=selectiveNotify&&Array.isArray(config.selectiveKinds)
      ?new Set(config.selectiveKinds.filter(x=>typeof x==="string")):new Set();
    const getState=typeof config.getState==="function"?config.getState:()=>undefined;
    const onSchedulerFault=typeof config.onSchedulerFault==="function"
      ?config.onSchedulerFault:()=>{};
    const limit=Number.isSafeInteger(config.historyLimit)
      ?Math.max(1,Math.min(config.historyLimit,512)):128;
    const consumers=new Map(),history=[];
    let current=null,pending=null,lastSnapshot=null,lastReceipt=null;
    const pendingStack=[];
    let serial=0,lastIsolatedRevision=0,disposed=false;
    const stats={observations:0,executions:0,skips:0,unsafeFallbacks:0,unknownInvalidations:0};
    const signature=value=>{
      try {
        const encoded=JSON.stringify(value);
        return encoded===undefined?"<undefined>":encoded;
      }catch(_){return null;}
    };
    const name=value=>typeof value==="string"&&value.length>0&&value.length<=512?value:null;
    const names=values=>{
      if(!Array.isArray(values)&&!(values instanceof Set))return null;
      const result=new Set();
      for(const value of values){const key=name(value);if(!key)return null;result.add(key);}
      return result;
    };
    const emit=value=>{history.push(value);if(history.length>limit)history.shift();};
    function ensure(key,render=null,opts={}) {
      if(disposed)throw Error("MCEL_SIGNAL_CANDIDATE_DISPOSED");
      if(!name(key))throw Error("MCEL_SIGNAL_CANDIDATE_INVALID_CONSUMER");
      let item=consumers.get(key);
      if(!item){
        item={key,render:null,reads:new Set(),externals:new Set(),observed:false,
          safeToSkip:false,constant:false,externalContracts:new Set(),generation:0,uncertain:false};
        consumers.set(key,item);
      }
      if(render!==null){
        if(typeof render!=="function")throw Error("MCEL_SIGNAL_CANDIDATE_INVALID_RENDER");
        item.render=render;
      }
      if(opts.register===true){
        // Rebinding even a stable key changes its possible dependency set.
        // Force a fresh observation rather than reusing stale reads.
        item.observed=false;item.uncertain=true;
        // Only an isolated test instance can authorize selective execution.
        // A mounted production observer cannot opt into suppression.
        item.safeToSkip=(isolated||integrated) && opts.safeToSkip===true;
        item.constant=opts.constant===true;
        item.externalContracts=names(opts.externalContracts||[])||new Set();
        item.generation++;
      }
      return item;
    }
    function trackStateRead(id){if(current){const key=name(id);if(key)current.reads.add(key);else current.uncertain=true;}}
    function trackExternalRead(source){if(current){const key=name(source);if(key)current.externals.add(key);else current.uncertain=true;}}
    function read(id){trackStateRead(id);return getState(id);}
    function run(item,render,externals=[]) {
      const previous=current;
      const capture={reads:new Set(),externals:new Set(),uncertain:false};
      current=capture;
      let successful=false;
      try {
        const staticNames=names(externals);
        if(staticNames===null)capture.uncertain=true;
        else for(const source of staticNames)capture.externals.add(source);
        const result=render();
        // Asynchronous reads cannot be tracked by this synchronous observer.
        // Do not allow a promise-returning consumer to become skippable.
        if(result&&typeof result.then==="function")capture.uncertain=true;
        successful=true;return result;
      }finally{
        current=previous;
        item.reads=capture.reads;item.externals=capture.externals;
        item.uncertain=capture.uncertain||!successful;
        item.observed=successful;
        stats.executions++;
      }
    }
    function decision(item,changed,invalidated,unknown=false){
      if(unknown)return {run:true,reason:"unknown-invalidation"};
      if(!item.observed||item.uncertain)return {run:true,reason:"unobserved-or-uncertain"};
      if(!item.safeToSkip)return {run:true,reason:"uncertified-consumer"};
      if(!item.reads.size&&!item.externals.size&&!item.constant)
        return {run:true,reason:"undeclared-inputs"};
      for(const source of item.externals)if(!item.externalContracts.has(source))
        return {run:true,reason:"uncontracted-external:"+source};
      for(const source of item.reads)if(changed.has(source))
        return {run:true,reason:"state-changed:"+source};
      for(const source of item.externals)if(invalidated.has(source))
        return {run:true,reason:"external-invalidated:"+source};
      return {run:false,reason:"unchanged-closed-inputs"};
    }
    function beginNotify(state,exactRevision=null,receiptSequence=null){
      if(disposed||!state||typeof state.entries!=="function")return;
      const snapshot=new Map(),changed=new Set();let unknown=lastSnapshot===null;
      try{
        for(const [id,value] of state.entries()){
          const key=name(id),encoded=signature(value);
          if(!key||encoded===null){unknown=true;continue;}
          snapshot.set(key,encoded);
          if(!lastSnapshot||lastSnapshot.get(key)!==encoded)changed.add(key);
        }
        if(lastSnapshot)for(const key of lastSnapshot.keys())if(!snapshot.has(key))changed.add(key);
      }catch(_){unknown=true;}
      if(unknown)stats.unknownInvalidations++;
      const invalidated=new Set(["clock"]);
      if(lastReceipt!==null&&receiptSequence!==lastReceipt)invalidated.add("receipts");
      lastSnapshot=snapshot;lastReceipt=receiptSequence;
      pendingStack.push(pending);
      pending={seen:new Set(),revision:++serial,exactRevision:Number.isSafeInteger(exactRevision)?exactRevision:null,
        changed,invalidated,unknown,executed:0,skipped:0,actualSkipConsumers:[]};
    }
    function observe(key,render,externals=[]){
      let item;
      try {
        item=ensure(key);
        if(selectiveNotify)item.safeToSkip=selectiveKinds.has(key.split("/",1)[0]);
        const d=pending?decision(item,pending.changed,pending.invalidated,pending.unknown)
          :{run:true,reason:"outside-notification"};
        if(pending){
          pending.seen.add(key);
          if(selectiveNotify){
            if(!d.run){
              pending.skipped++;stats.skips++;
              if(pending.actualSkipConsumers.length<256)
                pending.actualSkipConsumers.push({id:item.key,reason:d.reason,
                  stateReads:[...item.reads].sort(),externalReads:[...item.externals].sort()});
              return;
            }
            pending.executed++;
          }else{
            pending.executed++;
            if(!d.run)pending.skipped++;
          }
        }
      } catch(error) {
        if(!integrated)throw error;
        // The scheduler failed BEFORE calling the renderer. Discard its graph
        // and invoke the incumbent renderer exactly once. Errors originating
        // inside render itself remain normal application errors, never retries.
        stats.unsafeFallbacks++;
        try{onSchedulerFault("CONSUMER_DECISION:"+String(error));}catch(_){}
        return render();
      }
      return run(item,render,externals);
    }
    function endNotify(){
      if(!pending)return;
      stats.observations++;
      // Mounted observers are replaced with DOM nodes; old keys must not
      // retain subscriptions after those nodes disappear. Isolated fixture
      // registrations are explicitly disposed instead.
      if(!isolated){
        const parent=pendingStack[pendingStack.length-1];
        if(parent)for(const key of pending.seen)parent.seen.add(key);
        else for(const key of consumers.keys())if(!pending.seen.has(key))consumers.delete(key);
      }
      emit({revision:pending.revision,exactRevision:pending.exactRevision,
        changedStateKeys:[...pending.changed].sort(),
        invalidatedExternalSources:[...pending.invalidated].sort(),unknown:pending.unknown,
        incumbentExecutions:selectiveNotify?null:pending.executed,
        candidateExecutions:selectiveNotify?pending.executed:null,
        actualSkips:selectiveNotify?pending.skipped:0,
        actualSkipConsumers:selectiveNotify?pending.actualSkipConsumers:[],
        skippedConsumerDetailTruncated:selectiveNotify&&pending.skipped>pending.actualSkipConsumers.length,
        hypotheticalSkips:selectiveNotify?0:pending.skipped});
      pending=pendingStack.pop()||null;
    }
    function register(key,render,opts={}){
      if(!isolated)throw Error("MCEL_SIGNAL_CANDIDATE_ISOLATED_ONLY");
      const item=ensure(key,render,{...opts,register:true});
      const generation=item.generation;
      return ()=>{if(consumers.get(key)===item&&item.generation===generation)consumers.delete(key);};
    }
    function flush(input={}){
      // No mounted runtime ever calls flush. Only the isolated harness may
      // suppress a consumer. Every unknown input forces full execution.
      if(!isolated)throw Error("MCEL_SIGNAL_CANDIDATE_ISOLATED_ONLY");
      if(disposed)throw Error("MCEL_SIGNAL_CANDIDATE_DISPOSED");
      const changed=names(input.changedStateIds||[]),invalidated=names(input.externalInvalidations||[]);
      const unknown=input.unknown===true||changed===null||invalidated===null;
      const rev=input.revision;
      if(!Number.isSafeInteger(rev)||rev<=0||rev<lastIsolatedRevision)
        throw Error("MCEL_SIGNAL_CANDIDATE_REVISION_INVALID");
      // Repeated identical revision batches are deduplicated. A batch adding
      // new invalidations within that revision is evaluated again, never lost.
      const fingerprint=JSON.stringify([rev,[...(changed||[])].sort(),[...(invalidated||[])].sort(),unknown]);
      const duplicate=rev===lastIsolatedRevision&&fingerprint===flush.lastFingerprint;
      lastIsolatedRevision=rev;flush.lastFingerprint=fingerprint;
      const executed=[],skipped=[],reasons={};
      for(const item of consumers.values()){
        // Repeated revision batches may skip only previously observed,
        // fully contracted consumers. Unknown and newly rebound consumers
        // must still execute. A duplicate cannot silently drop work.
        const closed=item.observed&&!item.uncertain&&item.safeToSkip&&
          (item.reads.size>0||item.externals.size>0||item.constant)&&
          [...item.externals].every(source=>item.externalContracts.has(source));
        const d=duplicate&&closed?{run:false,reason:"duplicate-closed-revision"}:
          duplicate?{run:true,reason:"duplicate-unsafe-fallback"}:
          decision(item,changed||new Set(),invalidated||new Set(),unknown);
        reasons[item.key]=d.reason;
        if(!d.run){skipped.push(item.key);stats.skips++;continue;}
        if(d.reason!=="state-changed"&&d.reason!=="external-invalidated"&&
          d.reason.startsWith("uncontracted"))stats.unsafeFallbacks++;
        executed.push(item.key);
        run(item,item.render,input.externalsByConsumer?.[item.key]||[]);
      }
      emit({revision:rev,executed:executed.slice(),skipped:skipped.slice(),unknown});
      return {revision:rev,executed,skipped,reasons,duplicate};
    }
    function report(){return {schema:"mcel.signal-candidate.v1",
      mode:integrated&&selectiveNotify?"integrated-opt-in":selectiveNotify?"isolated-differential":isolated?"isolated":"observe",
      certified:false,productionSchedulerChanged:integrated&&selectiveNotify,consumerCount:consumers.size,
      statistics:{...stats},consumers:[...consumers.values()].map(item=>({
        id:item.key,reads:[...item.reads].sort(),externalReads:[...item.externals].sort(),
        observed:item.observed,uncertain:item.uncertain,
        skipAuthorized:item.safeToSkip&&(isolated||integrated)})),history:history.slice()};}
    function release(){disposed=true;consumers.clear();history.length=0;current=null;pending=null;pendingStack.length=0;lastSnapshot=null;}
    return Object.freeze({register,flush,read,trackStateRead,trackExternalRead,
      beginNotify,observe,endNotify,report,release});
  }

  function mount(root, ir, options = {}) {
    assert(root && root.nodeType === 1, "missing-root");
    assert(!root.hasAttribute("data-mcel-semantic-operational"), "already-mounted");
    const c = contract(ir, options.composition); // Refuse before modifying DOM.
    // The inference engine uses one canonical IR contract across all apps.
    // The Lab enables preview on its iframe route; deployed routes only
    // activate when the shared application-registry rollout permits it.
    const isLabPreview = global.__mcelSemanticColorLabPreview === true ||
      new URLSearchParams(global.location?.search || "").get("mcel_lab_theme_preview") === "1";
    const selected = global.__mcelSystemPresentationSelection;
    const activeSelection = selected?.schema === "mcel.presentation-selection.v1" &&
      selected.eligible === true && selected.rolloutEnabled === true &&
      selected.profiles && typeof selected.profiles.theme === "string" ? selected : null;
    const inferColor = isLabPreview || !!activeSelection;
    const controller = global.McelUnifiedPresentationController;
    // Existence must be checked, never inferred from undefined === undefined.
    const colorLaw = controller && typeof controller.mountOperationalColor === "function" &&
      typeof controller.refreshOperationalColor === "function" ? controller :
      global.McelSemanticColorPresentation;
    const usingController = !!controller && colorLaw === controller;
    const colorPlan = inferColor
      ? (usingController
          ? colorLaw.validateOperationalColor({ir,law:global.McelLabStyleLaw,composition:options.composition,inference:true})
          : colorLaw?.validate({ir,law:global.McelLabStyleLaw,composition:options.composition,inference:true}))
      : null;
    // Presentation is non-operational. A missing or refusing Theme law must
    // never prevent the semantic application from mounting (including SLH).
    // Record the condition; the shared controller reports partial coverage.
    // A supplied but invalid semantic contract is a source error, not a
    // missing optional palette. Keep canonical pre-mount validation intact.
    if (colorPlan && colorPlan.status !== "valid")
      fail("semantic-color-"+colorPlan.reason);
    const colorReady = !inferColor ? false : !!colorPlan && colorPlan.status === "valid" &&
      !!(options.colorPalette ||
        global.McelLabStyleLaw?.themePaletteFor?.(activeSelection?.profiles.theme||options.colorTheme||"default") ||
        global.McelLabStyleLaw?.semanticPaletteFor?.(root,global.getComputedStyle?.bind(global)));
    if (inferColor && !colorReady) {
      global.__mcelPresentationDiagnostic = {status:"refused", property:"theme",
        reason:!colorPlan ? "semantic-color-inference-engine-missing" :
          colorPlan.status!=="valid" ? "semantic-color-"+colorPlan.reason : "semantic-color-theme-palette-missing"};
    }
    const workspacePlan = workspaceContract(ir, c, options.workspacePlan);
    if (workspacePlan) assert(global.McelSemanticLayoutInference?.mountWorkspacePlan,
      "workspace-layout-engine-missing");
    if(c.generated) assert(global.McelSemanticLayoutInference?.inferWorkspaceLayout &&
      global.McelSemanticLayoutInference?.applyWorkspaceLayout, "generated-layout-engine-missing");
    const port = options.capabilities || {};
    const expert = options.presentation === "expert";
    const state = new Map(), observedTimes=new Map(), receipts = [], displays = new Map();
    // UI history is bounded. The sequence remains monotonic even after eviction;
    // authoritative durable evidence, when needed, belongs to the capability.
    const receiptLimit=256;
    let nextReceiptSequence=0;
    const directoryForms = new Set();
    const folderTrees = new Map();
    // Read-only navigation dependencies are identified by source/target state,
    // never by app IDs, labels, selectors or fixed root names.
    const navigationRoots = [];
    const guardedFormButtons = [];
    const navigationHistoryListeners = [];
    const refreshers = [];
    const taskRefreshPeriodMs = 5000;
    const collectionHosts = new Map(), plotHosts = new Map(), documentHosts = new Map(), actionButtons = [], regionHosts = new Map(), receiptHosts = [], tabSwitchers = [], current = new Map(), targetRuns = new Map(), plotVersions = new WeakMap(), actionLabelStates = new WeakMap();
    const conditionedNodes=new Map(), sharedFieldInputs=new Map(), keyedCollectionRows=new WeakMap(), selectedRecordHosts=new Map(), typedRecordHosts=new Map();
    const recordRenderSignatures=new WeakMap(), receiptRenderSignatures=new WeakMap();
    const declaredUpdates=[...c.nodes.values()].some(node=>!!node.updates);
    let lastLayoutFingerprint=null;
    const pendingConsequential=new Set();
    let serial = 0, disposed = false;
    let refreshTimer = null, initialRefreshTimer = null, refreshBusy = false, refreshEnabled = true;
    let inferredLayout=null, generatedResizeObserver=null, generatedResizeListener=null;
    let naturalInspector=null, naturalWorkspace=null, naturalTree=null;
    let plannedLayout=null,colorPresentation=null;
    const colorOperations=new Set(),colorOutcomes=new Map();
    // Diagnostic-only DOM producer bracket. A diagnostic error must never
    // re-run the incumbent mutation or prevent the existing UI from updating.
    const shadowDOMSource=(name,fn,revision=null,scheduled=null)=>{
      const oracle=global.MCELReactivityShadowOracle;
      let token=null;
      try{token=oracle?.beginMutationSource?.(root,name,revision,scheduled)||null;}catch(_){}
      try{return fn();}finally{
        try{oracle?.endMutationSource?.(root,token);}catch(_){}
      }
    };
    // All promoted registry apps dispatch Color Law updates through the one
    // controller. Isolated experimental runtimes retain the legacy adapter.
    const refreshColor = () => shadowDOMSource('presentation-refresh',()=>usingController
      ? colorLaw.refreshOperationalColor(root) : colorPresentation?.refresh());
    let scheduleGeneratedLayout=()=>{};
    const rememberedInputs = new Map();
    const chatControllers=[];
    const pendingChatMounts=[];
    // Source-declared form/output links, not application-name selectors.
    const resultReceivers = new Map();
    for (const s of c.states.values()) {
      if (own(s,"initial")) { checkSchema(s.schema,s.initial,s.id); state.set(s.id,clone(s.initial)); }
    }
    // The browser differential gate remains test-only. Normal application
    // mounts can opt into the SAME engine only through the explicit per-app
    // integration contract. All existing mounts default to incumbent execution.
    const isolatedSignalTest=global.__MCEL_SIGNAL_DIFFERENTIAL_ISOLATED__===true &&
      options.signalCandidateIsolatedTest===true;
    const experimentalKinds=["condition","display","selected-record","typed-record"];
    const requestedKinds=Array.isArray(options.signalCandidateTestKinds)
      ?options.signalCandidateTestKinds.filter(kind=>experimentalKinds.includes(kind))
      :experimentalKinds;
    const integratedAllowed=!isolatedSignalTest && options.signalSchedulerIntegration===true &&
      (ir.application.appId==="slh"||ir.application.appId==="semantic-task-manager"||
       ir.application.appId==="semantic-file-manager"||
       (ir.application.appId==="semantic-calculator-expanded" &&
        global.__MCEL_CALCULATOR_SIGNAL_ACCEPTANCE__===true));
    let integratedActive=integratedAllowed&&options.signalSchedulerStartEnabled===true;
    let signalFallbacks=0,lastSignalFallback=null;
    const integratedRequestedKinds=Array.isArray(options.signalSchedulerCertifiedKinds)
      ?options.signalSchedulerCertifiedKinds.filter(kind=>experimentalKinds.includes(kind))
      :experimentalKinds;
    const newIntegratedCandidate=()=>createSignalCandidate({integrated:true,
      selectiveInNotify:true,selectiveKinds:integratedRequestedKinds,
      onSchedulerFault:signalFailClosed});
    let signalCandidate=isolatedSignalTest
      ?createSignalCandidate({isolated:true,selectiveInNotify:true,selectiveKinds:requestedKinds})
      :integratedActive?newIntegratedCandidate()
      :options.signalCandidateDiagnostics===true?createSignalCandidate():null;
    function signalSchedulingReport(){
      const base=signalCandidate?.report()||{schema:"mcel.signal-candidate.v1",mode:"disabled",
        certified:false,productionSchedulerChanged:false};
      return {...base,integratedSupported:integratedAllowed,
        integrationEnabled:integratedActive,fallbacks:signalFallbacks,lastFallback:lastSignalFallback};
    }
    function signalFailClosed(reason){
      signalFallbacks++;lastSignalFallback=String(reason).slice(0,160);
      try{signalCandidate?.release();}catch(_){}
      integratedActive=false;
      signalCandidate=options.signalCandidateDiagnostics===true?createSignalCandidate():null;
    }
    // Explicit runtime control, including immediate rollback. Re-enabling
    // installs an empty graph, forcing one complete bootstrap execution.
    function setSignalSchedulingMode(mode){
      if(mode!=="incumbent"&&mode!=="certified")return {status:"REFUSED",reason:"UNKNOWN_MODE"};
      if(disposed)return {status:"REFUSED",reason:"UNMOUNTED"};
      if(mode==="certified"&&!integratedAllowed)return {status:"REFUSED",reason:"INTEGRATION_NOT_ALLOWED"};
      if(isolatedSignalTest)return {status:"REFUSED",reason:"ISOLATED_TEST_OWNS_SCHEDULER"};
      if(integratedActive===(mode==="certified"))return {status:"UNCHANGED",report:signalSchedulingReport()};
      try{signalCandidate?.release();}catch(_){}
      integratedActive=mode==="certified";
      signalCandidate=integratedActive?newIntegratedCandidate():
        options.signalCandidateDiagnostics===true?createSignalCandidate():null;
      notify(); // Fresh complete reconciliation; changed dependency graphs cannot leak across modes.
      return {status:integratedActive?"ENABLED":"ROLLED_BACK",report:signalSchedulingReport()};
    }
    const signalTargetIds=new WeakMap();let nextSignalTargetId=0;
    const signalConsumerKey=(kind,id,targets)=>{
      const target=Array.isArray(targets)?targets.find(item=>item?.nodeType===1):targets;
      if(!target||target.nodeType!==1)return kind+'/'+id;
      let key=signalTargetIds.get(target);
      if(!key){key=++nextSignalTargetId;signalTargetIds.set(target,key);}
      return kind+'/'+id+'#'+key;
    };
    // Diagnostic-only observation. The incumbent state and renderer remain
    // authoritative; failures in the optional oracle cannot affect either.
    let shadowCurrentConsumer = null;
    const read = id => {
      signalCandidate?.trackStateRead(id);
      if (shadowCurrentConsumer) try {shadowCurrentConsumer.read(id);} catch (_) {}
      return state.get(id);
    };
    function shadowObserve(kind,id,render,targets=null,externalSources=[]) {
      let observation=null;
      try {observation=global.MCELReactivityShadowOracle?.capture?.(root,kind,id,targets)||null;}catch (_) {}
      const invoke=()=>{
        if(!observation)return render();
        const previous=shadowCurrentConsumer;shadowCurrentConsumer=observation;
        try {
          for(const source of externalSources)observation.readExternal(source);
          return render();
        } finally {shadowCurrentConsumer=previous;try {observation.finish();}catch (_) {}}
      };
      return signalCandidate?signalCandidate.observe(signalConsumerKey(kind,id,targets),invoke,externalSources):invoke();
    }
    const shadowExternal=name=>{
      signalCandidate?.trackExternalRead(name);
      if(shadowCurrentConsumer)try {shadowCurrentConsumer.readExternal(name);}catch (_) {}
    };
    // Missing/expired derived state never satisfies operational applicability.
    // A backend-supplied observation timestamp, when present, is checked as
    // well as the time at which this runtime received its validated response.
    function conditionSatisfied(when) {
      if(own(when,"all"))return when.all.every(conditionSatisfied);
      if(own(when,"any"))return when.any.some(conditionSatisfied);
      const id=ref(when.state),descriptor=c.states.get(id),source=read(id);
      if(source===undefined || source===null)return false;
      if(descriptor?.authority==="derived"){
        // Derived observations can expire between state writes. A signal graph
        // must observe the freshness clock, not only the cached state value.
        shadowExternal('clock');
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
    const enabled = n => !n?.enabledWhen || conditionSatisfied(n.enabledWhen);
    function notify(exactRevision=null) {
      // A failed candidate preflight must never reuse an earlier dependency
      // snapshot. Return immediately to the incumbent before any consumer runs.
      if(signalCandidate)try{signalCandidate.beginNotify(state,exactRevision,nextReceiptSequence);}
        catch(error){if(integratedActive)signalFailClosed("BEGIN_NOTIFY:"+String(error));}
      try {
      try {global.MCELReactivityShadowOracle?.beforeNotify?.(
        root,ir.application.appId,state,nextReceiptSequence,exactRevision);}catch (_) {}
      for (const [r,el] of regionHosts) shadowObserve('region',r.id,()=>{
        if(r.visibleWhen){
          const hidden=read(ref(r.visibleWhen.state)) !== r.visibleWhen.equals;
          if(el.hidden!==hidden)el.hidden=hidden;
        }
      },el);
      for (const [n, el] of conditionedNodes) shadowObserve('condition',n.id,()=>{
        const hidden=!applicable(n);if(el.hidden!==hidden)el.hidden=hidden;
      },el);
      if(c.generated)for(const [r,el] of regionHosts)if(r.recipe!=="root" && r.members?.length)
        shadowObserve('generated-visibility',r.id,()=>{
          const hidden=r.members.every(member=>{
            const node=c.nodes.get(ref(member));
            return !!node?.visibleWhen && !applicable(node);
          });
          if(el.hidden!==hidden)el.hidden=hidden;
        },el,['generated-layout']);
      for (const [n, host] of selectedRecordHosts) shadowObserve('selected-record',n.id,()=>{
        const selections=[...c.intents.values()].filter(i=>i.collectionSelection &&
          ref(i.collectionSelection.target)===ref(n.value.state));
        const signature=JSON.stringify([read(ref(n.value.state)),...selections.map(i=>
          read(ref(i.collectionSelection.source)))]);
        if(recordRenderSignatures.get(host)!==signature){
          updateSelectedRecord(n,host);recordRenderSignatures.set(host,signature);
        }
      },host);
      for (const [n, host] of typedRecordHosts) shadowObserve('typed-record',n.id,()=>{
        const signature=JSON.stringify(read(ref(n.value.state)));
        if(!recordRenderSignatures.has(host) || recordRenderSignatures.get(host)!==signature){
          updateTypedRecord(n,host);recordRenderSignatures.set(host,signature);
        }
      },host);
      for (const [id, elements] of displays) for (const [index,el] of elements.entries()) shadowObserve('display',id+':'+index,()=>{
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
          if(el.querySelector('[data-mcel-sandboxed-image]')?.getAttribute('src')!==viewport.png ||
            el.querySelector('[data-mcel-sandboxed-log]')?.textContent!==viewport.logs.join("\n"))
            el.replaceChildren(img,caption,log);
        }else {
          const next=value === undefined ? "" : (value !== null && typeof value === "object" ? JSON.stringify(value,null,2) : String(value));
          if(el.textContent!==next || el.querySelector('[data-mcel-sandboxed-image]'))el.textContent=next;
        }
      },el);
      for (const view of tabSwitchers) shadowObserve('tab-switch',view.stateId,()=>{
        const selected=read(view.stateId);
        view.select.value=selected === undefined ? view.select.value : selected;
        if(view.subtitle){
          const choice=read(ref(view.subtitle.state));
          const label=view.subtitle.labels?.[choice] ?? view.subtitle.fallback;
          const target=view.buttons.find(button=>button.dataset.value===view.subtitle.forValue)
            ?.querySelector('[data-mcel-navigation-subtitle]');
          if(target && target.textContent!==label)target.textContent=label;
        }
        for (const button of view.buttons) {
          const active = button.dataset.value === selected;
          if(button.getAttribute("aria-pressed")!==String(active))
            button.setAttribute("aria-pressed",String(active));
          if(button.tagName==="A"){
            if(active && button.getAttribute("aria-current")!=="page")
              button.setAttribute("aria-current","page");
            else if(!active && button.hasAttribute("aria-current"))
              button.removeAttribute("aria-current");
          }
        }
      },[view.select,...view.buttons],['local-control']);
      for (const [n, host] of collectionHosts)
        shadowObserve('collection',n.id,()=>updateCollection(n,host),host,['local-dom']);
      for(const [index,refresh] of [...deselectionControls].entries())
        shadowObserve('deselection-control',String(index),refresh,refresh.shadowTargets,['local-control']);
      for (const session of notebooks.values())
        shadowObserve('notebook',session.node.id,()=>session.redraw(),session.root,['notebook-local']);
      for (const session of operationNotebooks.values())
        shadowObserve('operation-notebook',session.node.id,()=>session.redraw(),session.root,['notebook-local']);
      for (const [n, host] of plotHosts)
        shadowObserve('plot',n.id,()=>updatePlot(n,host),host);
      for (const [n, session] of documentHosts)
        shadowObserve('document',n.id,()=>updateDocument(n,session),session.surface,['editor-local']);
      let semanticActionChanged=false;
      for(const {button,intent,node} of [...actionButtons,...guardedFormButtons])
        shadowObserve('action-control',node?.id||intent.id,()=>{
        const disabled=!enabled(node) || (intent.requestArguments||[]).some(arg=>{
          const value=read(ref(arg.state));return value===undefined || value===null || (value==="" && arg.allowEmpty!==true);
        });
        if(button.disabled!==disabled)button.disabled=disabled;
        if(node?.enabledWhen && button.getAttribute("aria-disabled")!==String(disabled))
          button.setAttribute("aria-disabled",String(disabled));
        if(node?.labelByState){
          const descriptor=node.labelByState, record=read(ref(descriptor.state));
          const value=descriptor.path?record?.[descriptor.path]:record;
          // State transitions can change icon meaning even when the visible
          // label stays the same (including an initial fallback label).
          const signature=JSON.stringify([typeof value,value??null]);
          if(actionLabelStates.get(button)!==signature){
            actionLabelStates.set(button,signature);semanticActionChanged=true;
          }
          const label=descriptor.labels?.[value] ?? descriptor.fallback;
          if(typeof label==="string"){
            // Dynamic labels and managed icon slots are independent children.
            // textContent= would destroy the SVG every time the lifecycle changes.
            let textNode=[...button.childNodes].find(child=>child.nodeType===3);
            if(!textNode){textNode=button.ownerDocument.createTextNode("");button.appendChild(textNode);}
            if(textNode.nodeValue!==label){textNode.nodeValue=label;semanticActionChanged=true;}
          }
        }
      },button,['local-control']);
      if(semanticActionChanged)shadowObserve('semantic-icons','action-labels',
        ()=>global.McelSemanticIconCore?.inspect?.(root),root,['theme']);
      shadowObserve('color','presentation',refreshColor,root,['theme']);
      shadowObserve('activity','receipts',()=>{
        shadowExternal('receipts');
        renderActivities();
      },receiptHosts.map(host=>host.list),['activity-filter']);
      if(c.generated)shadowObserve('generated-layout','workspace',()=>{
        if(!declaredUpdates)scheduleGeneratedLayout();
        else {
          const fingerprint=JSON.stringify({regions:[...regionHosts].map(([r,el])=>
            [r.id,el.hidden,el.childElementCount,el.scrollHeight,el.clientHeight]),
            tabs:[...notebooks.values(),...operationNotebooks.values()].map(session=>session.active)});
          if(lastLayoutFingerprint!==fingerprint){
            lastLayoutFingerprint=fingerprint;scheduleGeneratedLayout();
          }
        }
      },root,['theme','generated-layout']);
      try {global.MCELReactivityShadowOracle?.afterNotify?.(root);}catch (_) {}
      } finally {if(signalCandidate)try{signalCandidate.endNotify();}
        catch(error){if(integratedActive)signalFailClosed("END_NOTIFY:"+String(error));}}
    }
    // Activity is a generic receipt view. The app may author a specific set of
    // intent/outcome reports using dsl.activity.info/warn/error. Without such
    // declarations the legacy receipt feed remains available as Info entries.
    // Severity and relevance are never inferred from an intent's risk or name.
    function renderActivities() {
      for (const host of receiptHosts) {
        const all = [];
        for(let i=receipts.length-1;i>=0;i--){
          const r=receipts[i],intent=c.intents.get(r.intentId);
          if(expert && intent?.operationKind!=="capability")continue;
          const declaration=host.rules ? host.rules.get(r.intentId+":"+r.disposition) : null;
          if(host.rules && !declaration)continue;
          const severity=declaration?.severity||"info";
          const message=declaration?.message || (fmt(r.intentId)+": "+r.disposition);
          all.push({receipt:r,severity,message:message+(r.reason?" ("+r.reason+")":"")});
        }
        // Severity visibility is three independent user-owned choices, not a
        // cumulative threshold. No filter action removes a receipt from history.
        const shown=all.filter(item=>host.levels.has(item.severity));
        const signature=JSON.stringify(shown.map(({receipt:r,severity,message})=>[
          r.sequence,severity,message,[...c.nodes.values()].filter(n=>ref(n.intent)===r.intentId).some(applicable)]));
        if(receiptRenderSignatures.get(host.list)!==signature){
          // Maintain a stable DOM node per receipt, even across filter changes.
          const rows=[];
          for(const {receipt:r,severity,message} of shown){
            let row=host.rows.get(r.sequence);
            const nodes=[...c.nodes.values()].filter(n=>ref(n.intent)===r.intentId);
            const attrs={"data-operation-intent":r.intentId,"data-disposition":r.disposition,
              "data-activity-severity":severity};
            if(!nodes.length || nodes.some(applicable))attrs["data-intent"]=r.intentId;
            if(!row){
              row=elt("li",attrs);
              row.append(elt("span",{"data-activity-level":""},severity.toUpperCase()),
                elt("span",{"data-activity-message":""}," "+message));
              host.rows.set(r.sequence,row);
            } else if(attrs["data-intent"]===undefined)row.removeAttribute("data-intent");
            else if(row.getAttribute("data-intent")!==r.intentId)row.setAttribute("data-intent",r.intentId);
            rows.push(row);
          }
          // The newest matching receipt is always the first child; unchanged
          // rows retain their identity, focus, and theme ownership.
          const desired=new Set(rows);
          for(const child of [...host.list.children])if(!desired.has(child))child.remove();
          for(let i=0;i<rows.length;i++)if(host.list.children[i]!==rows[i])
            host.list.insertBefore(rows[i],host.list.children[i]||null);
          receiptRenderSignatures.set(host.list,signature);
        }
        if(host.summary){
          const summary=!all.length ? "Activity · No operations yet" :
            host.levels.size===3 ? "Activity · "+all.length+" recent operation"+(all.length===1?"":"s") :
              "Activity · "+shown.length+" of "+all.length+" operations";
          if(host.summary.textContent!==summary)host.summary.textContent=summary;
        }
        if(host.latest){
          const result=host.latest.querySelector('[data-mcel-request-result]');
          const latest=all[0]?.message||"No request yet";
          if(result && result.textContent!==latest)result.textContent=latest;
        }
        // Receipts and filter updates never alter the user's disclosure state.
      }
    }
    function traceOperation(intentId,phase,disposition) {
      if (typeof global.CustomEvent !== "function") return;
      root.dispatchEvent(new global.CustomEvent("mcel:parity-operation", {bubbles:true,
        detail:{appId:ir.application.appId,intentId,phase,disposition}}));
    }
    function receipt(intent, disposition, reason="") {
      const r = Object.freeze({sequence:++nextReceiptSequence,intentId:intent.id,disposition,reason});
      // Exact local receipt sequence connects the settled operation to a later
      // callback without relying on whichever request happens to be pending.
      let receiptRevision=null;
      try {receiptRevision=global.MCELReactivityShadowOracle?.operationReceipt?.(
        root,intent.id,disposition,r.sequence)||null;}catch (_) {}
      // Record reads can also originate outside the notebook (for example a
      // programmatic inspect). A valid current result must reconcile its
      // notebook message as well as its data; a superseded result does neither.
      for(const session of notebooks.values()){
        if(!session.available)continue;
        const active=session.node.tabs.find(t=>t.id===session.active);
        if(ref(active?.refresh)!==intent.id)continue;
        if(disposition==="completed")session.error="";
        else if(["failed","refused-before-attempt"].includes(disposition)){
          state.delete(ref(active.state));
          session.error=(session.node.messages?.errorPrefix||"Records unavailable: ")+String(reason||disposition);
        }
      }
      colorOutcomes.set(intent.id, disposition === "completed" ? "succeeded" :
        disposition === "failed" || disposition === "refused-before-attempt" ? "failed" : "idle");
      receipts.push(r);
      if(receipts.length>receiptLimit)receipts.splice(0,receipts.length-receiptLimit);
      // DOM-row caches must not retain evicted receipts indefinitely.
      for(const host of receiptHosts)for(const key of host.rows.keys())
        if(key<=nextReceiptSequence-receiptLimit)host.rows.delete(key);
      notify(receiptRevision); traceOperation(intent.id,"settled",disposition); return r;
    }
    function parseInput(s, element) {
      const raw = element.value;
      if (s.kind === "integer") return raw.trim() === "" ? null : Number(raw);
      if (s.kind === "number") return raw.trim() === "" ? null : Number(raw);
      if (s.kind === "boolean") return element.checked;
      return raw;
    }
    let identityContextEpoch=0;
    // Record-notebook authorization has its own generation. Revoking it must
    // invalidate protected reads without cancelling unrelated operations.
    let recordAuthorizationEpoch=0;
    // An external caller and a notebook may request the same protected read.
    // Track requests before authorization (which may emit an authentication
    // callback), not only after the port has been authorized.
    const notebookReadRuns=new Map();
    let notebookReadSequence=0;
    const notebookReadPending=id=>notebookReadRuns.get(id)?.context===identityContextEpoch;
    // One application declaration controls both the button's disabled state
    // and direct dispatch. MCEL evaluates conditions; the application owns them.
    function operationActionEnabled(session,tab,action){
      return !!action.alwaysEnabled || (session.available && !session.busy && !session.error &&
        applicable(session.node) && !!read(ref(tab.state)) &&
        (!action.enabledWhen || conditionSatisfied(action.enabledWhen)));
    }
    function operationActionDispatchAllowed(intentId){
      let declared=false;
      for(const session of operationNotebooks.values())for(const tab of session.node.tabs){
        for(const action of Object.values(tab.actions||{})){
          if(ref(action.intent)!==intentId)continue;
          declared=true;
          if(operationActionEnabled(session,tab,action))return true;
        }
      }
      // Intents not declared as operation-notebook actions retain their own
      // contracts (including application-initiated reads and other operations).
      return !declared;
    }
    async function execute(id, given={}, diagnosticOrigin="dispatch") {
      assert(!disposed, "disposed");
      const intent = c.intents.get(id); assert(intent,"unknown-intent:"+id);
      // Revision tracking is optional and cannot change operation outcomes.
      let shadowRevision=null;
      try {shadowRevision=global.MCELReactivityShadowOracle?.beginOperation?.(
        root,ir.application.appId,id,intent.operationKind,intent.risk,diagnosticOrigin)||null;}catch (_) {}
      const shadowPhase=phase=>{
        try {global.MCELReactivityShadowOracle?.operationPhase?.(root,shadowRevision,phase);}catch (_) {}
      };
      traceOperation(id,"dispatch","");
      let attempted = false;
      let notebookReadToken=null;
      let ownsConsequential=false;
      let ownsColorOperation=false;
      let recordScopedRead=false;
      let requestRecordAuthorizationEpoch=recordAuthorizationEpoch;
      try {
        // The same declarative applicability gate governs the generated
        // control and programmatic execute(). This is NOT backend authority.
        const controls=[...c.nodes.values()].filter(n=>ref(n.intent)===id);
        if(controls.length && !controls.some(n=>applicable(n) && enabled(n)))
          fail("operational-condition-not-satisfied");
        if(!operationActionDispatchAllowed(id))
          fail("operational-condition-not-satisfied");
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
            assert(selected==="" && sel.empty==="clear" ||
              source.some(row => row && String(row[sel.key]) === String(selected)),"selection-not-in-collection");
            const priorSelection=read(ref(sel.target));
            if(priorSelection!==selected){
              ++identityContextEpoch;
              // Role projections are derived only from the selected target's
              // authenticated active device directory; never keep them over
              // an identity switch or explicit deselection.
              for(const node of c.nodes.values())if(node.roleFrom && ref(node.selectionIntent)===id)
                state.delete(ref(node.roleFrom.state));
            }
            state.set(ref(sel.target),clone(selected));
            if(priorSelection!==selected && typeof global.CustomEvent==="function" &&
                typeof root.dispatchEvent==="function")
              root.dispatchEvent(new global.CustomEvent("mcel:collection-selection",{detail:{
                intentId:id,stateId:ref(sel.target),value:String(selected)
              }}));
            // A new root invalidates both the visible hierarchy and every
            // derived result scoped to the old root, including file previews.
            // Do not leave selectable stale rows while a new root loads.
            for(const [node,tree] of folderTrees){
              if(ref(sel.target)===tree.rootState && tree.root!==String(selected)){
                tree.root=String(selected);tree.paths.clear();tree.collapsed.clear();tree.current="";tree.searchResults=false;
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
            notify(shadowRevision);
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
            if(read(ref(t.target))!==input[key])++identityContextEpoch;
            state.set(ref(t.target),clone(input[key]));
            for(const control of c.nodes.values())if(control.nodeKind==="control" && ref(control.intent)===id)
              for(const item of control.resetOutputs||[])state.set(ref(item.state),item.value);
            notify(shadowRevision);
            // An observational lifecycle event: the rendered view changed.
            // Hosts may prepare optional capabilities without making the
            // selection itself a capability intent or changing its receipt.
            if(typeof global.CustomEvent==="function" && typeof root.dispatchEvent==="function")
              root.dispatchEvent(new global.CustomEvent("mcel:view-transition",{detail:{
                intentId:id,stateId:ref(t.target),value:clone(input[key])
              }}));
            return receipt(intent,"completed");
          }
          fail("unsupported-interaction:"+id);
        }
        assert(intent.operationKind === "capability", "unsupported-intent:"+id);
        if(intent.risk === "consequential") {
          assert(!pendingConsequential.has(id),"consequential-request-already-pending");
          pendingConsequential.add(id);
          ownsConsequential=true;
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
        for(const [node,session] of documentHosts){
          const job=node.externalJob;
          if(!job || ref(job.launchIntent)!==id)continue;
          const path=args[job.pathArgument],raw=String(args[job.filesArgument]||"");
          const files=raw.trim()?raw.split(/[\n,]+/).map(x=>x.trim()).filter(Boolean):[path];
          assert(files.includes(path),"document-job-current-file-required");
          assert(!session.savePending,"source-write-in-progress");
          for(const file of files){
            const tab=session.tabs.get(file);
            if(tab)assert(!tab.dirty && !tab.conflict && !tab.aiderPending,
              "document-job-file-has-unsaved-or-conflicted-draft:"+file);
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
        const requestContextEpoch=identityContextEpoch;
        recordScopedRead=intent.risk==="external-read" && [...notebooks.values()].some(session=>
          session.node.tabs.some(tab=>ref(tab.refresh)===id) ||
          (session.node.retention && ref(session.node.retention.refresh)===id));
        requestRecordAuthorizationEpoch=recordAuthorizationEpoch;
        if(recordScopedRead){
          notebookReadToken=++notebookReadSequence;
          notebookReadRuns.set(id,{token:notebookReadToken,context:requestContextEpoch});
        }
        const obsoleteNotebookRead=()=>notebookReadToken!==null &&
          notebookReadRuns.get(id)?.token!==notebookReadToken;
        const revokedRecordRead=()=>recordScopedRead &&
          requestRecordAuthorizationEpoch!==recordAuthorizationEpoch;
        // This is operational state already owned by the runtime, not a
        // browser pointer event. The Color Law may project it as pending.
        colorOperations.add(id);ownsColorOperation=true;
        colorOutcomes.delete(id);refreshColor();
        shadowPhase('authorizing');
        const granted = await port.authorize(request);
        if(requestContextEpoch!==identityContextEpoch || obsoleteNotebookRead() ||
           revokedRecordRead())return {disposition:"superseded"};
        assert(granted === true,"authority-denied");
        const run = ++serial; current.set(id,run);
        for (const binding of intent.resultBindings || []) targetRuns.set(ref(binding.target),run);
        attempted = true;
        shadowPhase('invoking');
        const value = await port.invoke(request);
        shadowPhase('response-received');
        if(requestContextEpoch!==identityContextEpoch || obsoleteNotebookRead() ||
           revokedRecordRead())return {disposition:"superseded"};
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
        // Read response settlement is an external invalidation source; only
        // diagnostic epochs are advanced, not the production scheduler.
        try {global.MCELReactivityShadowOracle?.invalidate?.(root,'async-commit',shadowRevision);}catch (_) {}
        for (const binding of intent.resultBindings || []){
          const target=ref(binding.target);
          state.set(target,clone(value));
          observedTimes.set(target,Date.now());
          for(const receiver of resultReceivers.get(target)||[]){
            if(!receiver.input.isConnected || receiver.whenIntent && receiver.whenIntent!==id)continue;
            const next=receiver.pick?value?.[receiver.pick]:value;
            // A boolean form is a checkbox: value is the form-control's
            // submission label, whereas checked is its actual semantic state.
            // Hydrating value alone can silently submit the opposite policy.
            if(receiver.input.type==="checkbox")receiver.input.checked=(next===true);
            else receiver.input.value=String(next);
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
          } else if(node.externalJob && ref(node.externalJob.launchIntent)===id){
            for(const [path,tab] of session.tabs){
              if(port.documentWriteStatus?.(path)!==node.externalJob.pendingStatus)continue;
              tab.aiderPending=true;tab.editable=false;
              if(session.activePath===path){
                session.editable=false;
                session.status.textContent=node.externalJob.acceptedMessage||
                  "External job accepted; verify completion before reopening source.";
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
          // A directory explicitly opened by the operator must remain visible
          // in the retained hierarchy even after a previous local collapse.
          for(let path=entered;path;path=path.includes("/")?path.slice(0,path.lastIndexOf("/")):"")
            tree.collapsed.delete(path);
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
        notify(shadowRevision);
        // On a terminal Aider job receipt, reread the current affected file
        // through the original port. Never assume job acceptance means success.
        for(const [node,session] of documentHosts){
          if(!node.externalJob || ref(node.externalJob.statusIntent)!==id)continue;
          const path=session.activePath,tab=session.tabs.get(path);
          if(tab?.aiderPending && port.documentWriteStatus?.(path)!==node.externalJob.pendingStatus &&
             read(ref(node.path))===path){
            tab.aiderPending=false;session.mountedPath="";
            void execute(ref(node.loadIntent));
          }
        }
        const jobDeclaration=[...documentHosts.keys()].map(n=>n.externalJob)
          .find(job=>job && ref(job.launchIntent)===id);
        const disposition = jobDeclaration && typeof value==="string" &&
          value.startsWith(jobDeclaration.acceptedPrefix)?"accepted-deferred":
          (intent.risk === "consequential" && value && typeof value === "object" &&
          ["accepted-deferred","command-returned","recorded","unknown","refused",
           "refused-before-attempt","failed","cancelled"].includes(value.outcome))
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
              tree.root="";tree.paths.clear();tree.collapsed.clear();tree.current="";tree.searchResults=false;
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
            notify(shadowRevision);
          }
        }
        return completed;
      } catch (ex) {
        // An obsolete protected read must not publish a late failure after a
        // newer read or a changed identity has already taken ownership.
        if(notebookReadToken!==null &&
            (notebookReadRuns.get(id)?.token!==notebookReadToken ||
             notebookReadRuns.get(id)?.context!==identityContextEpoch ||
             requestRecordAuthorizationEpoch!==recordAuthorizationEpoch))
          return {disposition:"superseded"};
        for(const [node,session] of documentHosts){
          if(!node.externalJob || ref(node.externalJob.launchIntent)!==id)continue;
          for(const [path,tab] of session.tabs){
            if(port.documentWriteStatus?.(path)!==node.externalJob.unverifiedStatus)continue;
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
        if(notebookReadToken!==null && notebookReadRuns.get(id)?.token===notebookReadToken)
          notebookReadRuns.delete(id);
        // A rejected duplicate never acquired either resource: it must not
        // unlock the original invocation or erase its pending color state.
        if(ownsConsequential)pendingConsequential.delete(id);
        if(ownsColorOperation)colorOperations.delete(id);
        shadowDOMSource('operation-finalize',refreshColor,shadowRevision);
        try {global.MCELReactivityShadowOracle?.finishOperation?.(root,shadowRevision);}catch (_) {}
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
    const notebooks=new Map();
    function renderNotebook(n,holder){
      const root=elt("section",{"data-mcel-notebook":n.id,"aria-label":n.title});
      const tabs=elt("div",{"role":"tablist","data-mcel-notebook-tabs":"","aria-label":n.title});
      const records=elt("div",{"data-mcel-notebook-scroll":"","role":"tabpanel"});
      const status=elt("p",{"role":"status","data-mcel-notebook-status":""},(n.messages?.unavailable||"Select a context to access records"));
      const footer=elt("div",{"data-mcel-notebook-footer":""});
      const policyLabel=n.retention ? elt("label",{"data-mcel-notebook-policy":""}) : null;
      const checkbox=n.retention ? elt("input",{type:"checkbox","data-mcel-notebook-retention":""}) : null;
      if(policyLabel){
        policyLabel.append(checkbox,document.createTextNode(n.retention.label||" Enable retention"));
        footer.append(policyLabel);
      }
      const empty=n.empty ? elt("button",{type:"button","data-mcel-notebook-empty":""},
        n.empty.label||"Clear records") : null;
      if(empty)footer.append(empty);
      root.append(tabs,records,status);
      if(policyLabel||empty)root.append(footer);
      holder.append(root);
      const session={node:n,root,records,status,checkbox,empty,footer,active:n.tabs[0].id,
        available:false,busy:false,epoch:0,readRevision:0,refreshTask:null,
        refreshScope:"",loading:false,error:""};
      notebooks.set(n,session);
      const selected=()=>n.tabs.find(t=>t.id===session.active);
      const footerTab=n.tabs[n.tabs.length-1];
      // Keyed record rows and their dynamic fields are explicitly authored by
      // the application. Routine observations do not remount tabs or rows.
      session.renderedTab=null;
      session.rows=new Map();
      session.rowUpdaters=new Map();
      function redrawDeclaredNotebook(){
        const tab=selected();
        if(session.renderedTab!==tab.id){
          for(const updater of session.rowUpdaters.values())updater.dispose();
          session.rows.clear();session.rowUpdaters.clear();
          session.renderedTab=tab.id;
          tabs.replaceChildren(...n.tabs.map(t=>{
            const button=elt("button",{type:"button",role:"tab","data-mcel-notebook-tab":t.id,
              "aria-selected":String(session.active===t.id)},t.label);
            button.addEventListener("click",()=>{
              if(session.active!==t.id){
                session.active=t.id;++session.readRevision;
                session.loading=false;session.error="";redraw();
              }
              // Clicking the already-selected default tab is a real activation:
              // it can initiate the protected read without a remount.
              if(session.available)void refreshNotebook(session);
            });return button;
          }));
          records.setAttribute("aria-label",tab.label+" "+(n.recordLabel||"records"));
          records.replaceChildren();
        }
        const data=read(ref(tab.state));
        const statusValue=!session.available?
          (n.messages?.unavailable||"Select a context to access records"):
          session.error?session.error:
          session.loading && !Array.isArray(data)?"Loading "+tab.label+" records":
          !Array.isArray(data)?"Select "+tab.label+" to request records":
          data.length?"":"No "+tab.label.toLowerCase()+" records";
        if(status.textContent!==statusValue)status.textContent=statusValue;
        const observed=session.available && Array.isArray(data)?data:[];
        const next=new Map(), ordered=[];
        for(const item of observed){
          const key=String(item[tab.key]);
          let row=session.rows.get(key);
          if(!row){
            row=elt("div",{"data-mcel-record-row":"","data-record-key":key});
            for(const field of tab.fields){
              row.append(elt("span",{"data-record-field":field,"data-field-label":fmt(field),
                "data-mcel-updatable":"text","data-mcel-update-key":"field:"+field},
                plainValue(item[field])));
            }
            const actions=elt("div",{"data-mcel-notebook-row-actions":""});
            for(const [label,action] of Object.entries(tab.actions||{})){
              const button=elt("button",{type:"button","data-mcel-notebook-action":label},label);
              button.disabled=session.busy;
              button.addEventListener("click",async()=>{
                if(session.busy||!session.available)return;
                const selectedItem=row.__mcelRecord;
                if(action.confirm && (typeof global.confirm!=="function" ||
                  !global.confirm(action.confirm+"\n\n"+String(selectedItem[tab.key]))))return;
                session.busy=true;redraw();
                const epoch=session.epoch;
                try{
                  const receipt=await execute(ref(action.intent),{[c.intents.get(ref(action.intent)).input[0].sourceName]:String(selectedItem[tab.key])});
                  if(epoch!==session.epoch)return;
                  if(receipt?.disposition!=="completed"){
                    session.error="Operation refused: "+String(receipt?.reason||receipt?.disposition||"UNKNOWN");return;
                  }
                  session.error="";await refreshNotebook(session,true);
                }finally{session.busy=false;redraw();}
              });actions.append(button);
            }
            row.append(actions);
            session.rowUpdaters.set(key,global.McelSemanticValueUpdaters.mount(row));
          }
          row.__mcelRecord=item;
          const proposed={};
          for(const field of tab.fields)proposed["field:"+field]=plainValue(item[field]);
          session.rowUpdaters.get(key).update(proposed);
          for(const button of row.querySelectorAll('[data-mcel-notebook-action]'))
            if(button.disabled!==session.busy)button.disabled=session.busy;
          next.set(key,row);ordered.push(row);
        }
        for(let i=0;i<ordered.length;i++)if(records.children[i]!==ordered[i])
          records.insertBefore(ordered[i],records.children[i]||null);
        for(const [key,row] of session.rows)if(!next.has(key)){
          session.rowUpdaters.get(key)?.dispose();session.rowUpdaters.delete(key);row.remove();
        }
        session.rows=next;
        if(checkbox){
          const policy=read(ref(n.retention.state));
          const checked=policy?.enabled===true;
          if(checkbox.checked!==checked)checkbox.checked=checked;
          const disabled=!session.available||session.busy||typeof policy?.enabled!=="boolean";
          if(checkbox.disabled!==disabled)checkbox.disabled=disabled;
        }
        if(empty){
          const emptyDisabled=!session.available||session.busy||
            !Array.isArray(read(ref(footerTab.state)))||!read(ref(footerTab.state))?.length;
          if(empty.disabled!==emptyDisabled)empty.disabled=emptyDisabled;
        }
        if(footer.hidden!==(session.active!==footerTab.id))footer.hidden=session.active!==footerTab.id;
      }
      function redraw(){
        if(n.updates){redrawDeclaredNotebook();return;}
        tabs.replaceChildren(...n.tabs.map(t=>{
          const button=elt("button",{type:"button",role:"tab","data-mcel-notebook-tab":t.id,
            "aria-selected":String(session.active===t.id)},t.label);
          button.addEventListener("click",()=>{
            if(session.active!==t.id){
              session.active=t.id;++session.readRevision;
              session.loading=false;session.error="";redraw();
            }
            if(session.available)void refreshNotebook(session);
          });return button;
        }));
        const tab=selected();
        records.setAttribute("aria-label",tab.label+" "+(n.recordLabel||"records"));
        records.replaceChildren();
        const data=read(ref(tab.state));
        if(!session.available)status.textContent=(n.messages?.unavailable||"Select a context to access records");
        else if(session.error)status.textContent=session.error;
        else if(session.loading && !Array.isArray(data))status.textContent="Loading "+tab.label+" records";
        else if(!Array.isArray(data))status.textContent="Select "+tab.label+" to request records";
        else status.textContent=data.length?"": "No "+tab.label.toLowerCase()+" records";
        if(session.available && Array.isArray(data))for(const item of data){
          const row=elt("div",{"data-mcel-record-row":"","data-record-key":String(item[tab.key])});
          for(const field of tab.fields){
            const cell=elt("span",{"data-record-field":field,"data-field-label":fmt(field)},plainValue(item[field]));
            row.append(cell);
          }
          const actions=elt("div",{"data-mcel-notebook-row-actions":""});
          for(const [label,action] of Object.entries(tab.actions||{})){
            const button=elt("button",{type:"button","data-mcel-notebook-action":label},label);
            button.disabled=session.busy;
            button.addEventListener("click",async()=>{
              if(session.busy||!session.available)return;
              if(action.confirm && (typeof global.confirm!=="function" || !global.confirm(action.confirm+"\n\n"+String(item[tab.key]))))return;
              session.busy=true;redraw();
              const epoch=session.epoch;
              try{
                const receipt=await execute(ref(action.intent),{[c.intents.get(ref(action.intent)).input[0].sourceName]:String(item[tab.key])});
                if(epoch!==session.epoch)return;
                if(receipt?.disposition!=="completed"){
                  session.error="Operation refused: "+String(receipt?.reason||receipt?.disposition||"UNKNOWN");return;
                }
                session.error="";await refreshNotebook(session,true);
              }finally{session.busy=false;redraw();}
            });actions.append(button);
          }
          row.append(actions);records.append(row);
        }
        if(checkbox){
          const policy=read(ref(n.retention.state));
          checkbox.checked=policy?.enabled===true;
          checkbox.disabled=!session.available||session.busy||typeof policy?.enabled!=="boolean";
        }
        if(empty)empty.disabled=!session.available||session.busy||
          !Array.isArray(read(ref(footerTab.state)))||!read(ref(footerTab.state))?.length;
        footer.hidden=session.active!==footerTab.id;
      }
      session.redraw=redraw;
      // Visibility, identity selection, and loading policy belong to the
      // application. MCEL never infers an authorized read from a mount.
      checkbox?.addEventListener("change",async()=>{
        if(!session.available||session.busy)return;
        const requested=checkbox.checked;session.busy=true;redraw();
        try{
          const receipt=await execute(ref(n.retention.update),{enabled:requested});
          if(receipt?.disposition!=="completed")session.error=
            (n.retention.failureLabel||"Retention change refused: ")+String(receipt?.reason||receipt?.disposition);
          else session.error="";
          await execute(ref(n.retention.refresh));
        }finally{session.busy=false;redraw();}
      });
      empty?.addEventListener("click",async()=>{
        if(session.busy||!session.available||!read(ref(footerTab.state))?.length)return;
        if(typeof global.confirm!=="function" || !global.confirm(
          n.empty.confirm||"Clear these records? This cannot be undone."))return;
        session.busy=true;redraw();
        try{
          const receipt=await execute(ref(n.empty));
          if(receipt?.disposition!=="completed")session.error=
            (n.empty.failureLabel||"Clear records refused: ")+String(receipt?.reason||receipt?.disposition);
          else {session.error="";await refreshNotebook(session,true);}
        }finally{session.busy=false;redraw();}
      });
      redraw();
    }
    function refreshNotebook(session,all=false,options={}){
      if(!session.available)return Promise.resolve({disposition:"unavailable"});
      const epoch=session.epoch,active=session.active;
      const scope=active+":"+(all?"all":"active");
      // Repeated activation/authorization notifications reuse a matching
      // request instead of launching competing observations.
      if(session.refreshTask && session.refreshScope===scope && session.refreshEpoch===epoch)
        return session.refreshTask;
      const revision=++session.readRevision;
      const current=()=>session.epoch===epoch && session.readRevision===revision &&
        session.active===active;
      const n=session.node;
      session.loading=true;session.error="";session.redraw();
      const run=async()=>{
        try{
          const tabs=all?n.tabs:[n.tabs.find(t=>t.id===active)];
          for(const tab of tabs){
            const target=ref(tab.state),intentId=ref(tab.refresh);
            if(options.onlyMissing && (Array.isArray(read(target)) ||
                options.skipInFlight && notebookReadPending(intentId)))continue;
            const outcome=await execute(intentId);
            if(!current())return {disposition:"superseded"};
            if(outcome?.disposition==="superseded"){
              // A newer request owns both the value and any failure. Stop
              // this batch: later unrelated reads must not clear its status.
              return outcome;
            }
            if(outcome?.disposition!=="completed"){
              state.delete(target);
              session.error=(n.messages?.errorPrefix||"Records unavailable: ")+
                String(outcome?.reason||outcome?.disposition||"AUTH_REQUIRED");
              return {disposition:outcome?.disposition||"failed",reason:outcome?.reason||""};
            }
          }
          if(n.retention && (all || active===n.tabs[n.tabs.length-1].id)){
            const policy=ref(n.retention.state),intentId=ref(n.retention.refresh);
            if(!(options.onlyMissing && (read(policy)!==undefined ||
                options.skipInFlight && notebookReadPending(intentId)))){
              const outcome=await execute(intentId);
              if(!current())return {disposition:"superseded"};
              if(outcome?.disposition==="superseded")return outcome;
              if(outcome?.disposition!=="completed"){
                state.delete(policy);
                session.error=(n.retention.loadFailureLabel||"Retention policy unavailable: ")+
                  String(outcome?.reason||outcome?.disposition||"AUTH_REQUIRED");
                return {disposition:outcome?.disposition||"failed",reason:outcome?.reason||""};
              }
            }
          }
          if(current())session.error="";
          return {disposition:"completed"};
        }finally{
          if(current()){
            session.loading=false;
            session.redraw();
          }
        }
      };
      const task=run();
      session.refreshTask=task;session.refreshScope=scope;session.refreshEpoch=epoch;
      void task.finally(()=>{
        if(session.refreshTask===task)session.refreshTask=null;
      });
      return task;
    }
    // Explicit request from the application, not implicit viewport or auth
    // policy. The renderer owns deduplication, request freshness, and updates.
    function activateNotebooks(options={}){
      if(options.scope==="operations")return Promise.resolve([]);
      const tasks=[];
      for(const session of notebooks.values()){
        if(options.notebookId && session.node.id!==options.notebookId)continue;
        if(!session.available || session.busy){
          tasks.push(Promise.resolve({disposition:"unavailable"}));
          continue;
        }
        const tab=session.node.tabs.find(t=>t.id===session.active);
        if(!tab)continue;
        const intentId=ref(tab.refresh);
        if(options.all===true){
          // A batch executes sequentially. Never start another batch while an
          // independently dispatched notebook read is in flight, since its
          // newer read token would supersede the old protected read.
          if(session.refreshTask || session.node.tabs.some(t=>notebookReadPending(ref(t.refresh))) ||
             (session.node.retention && notebookReadPending(ref(session.node.retention.refresh)))){
            tasks.push(Promise.resolve({disposition:"in-flight",reason:"READ_ALREADY_IN_FLIGHT"}));
            continue;
          }
          if(options.force!==true && session.node.tabs.every(t=>Array.isArray(read(ref(t.state)))) &&
              (!session.node.retention || read(ref(session.node.retention.state))!==undefined)){
            tasks.push(Promise.resolve({disposition:"completed"}));
            continue;
          }
          tasks.push(refreshNotebook(session,true,{onlyMissing:options.force!==true,skipInFlight:true}));
          continue;
        }
        if(options.force!==true && notebookReadPending(intentId)){
          tasks.push(Promise.resolve({disposition:"in-flight",reason:"READ_ALREADY_IN_FLIGHT"}));
          continue;
        }
        if(options.force!==true && Array.isArray(read(ref(tab.state)))){
          tasks.push(Promise.resolve({disposition:"completed"}));
          continue;
        }
        tasks.push(refreshNotebook(session,false,{onlyMissing:options.force!==true,skipInFlight:true}));
      }
      // A generic completion receipt allows applications to own recovery
      // policy. MCEL makes no decision about which failures deserve retry.
      return Promise.all(tasks);
    }
    function changeNotebookAuthorization(available,options={}){
      // Withdrawing the record authorization context invalidates in-flight
      // protected responses before they can repopulate cleared state. A new
      // authorization generation cannot revive an earlier request.
      if(options.scope!=="operations" && (!available || options.reset===true))
        ++recordAuthorizationEpoch;
      // Local signer selection establishes eligibility to request records,
      // not proof of administrator authentication. The port remains the sole
      // authority for all protected reads and mutations.
      if(options.scope!=="records")for(const session of operationNotebooks.values()){
        ++session.epoch;session.available=!!available;session.busy=false;session.error="";
        session.operationMessage="";session.readFailure=null;
        for(const tab of session.node.tabs)state.delete(ref(tab.state));
        session.redraw();
        if(available && options.refresh!==false && applicable(session.node))void refreshOperationNotebook(session);
      }
      if(options.scope!=="operations")for(const session of notebooks.values()){
        const next=!!available,changed=session.available!==next;
        if(changed || options.reset===true){
          ++session.epoch;++session.readRevision;
          session.available=next;session.busy=false;session.loading=false;session.error="";
          session.refreshTask=null;
          for(const tab of session.node.tabs)state.delete(ref(tab.state));
          if(session.node.retention)state.delete(ref(session.node.retention.state));
        }
        session.redraw();
        // Availability is presentation/eligibility, not a command to issue
        // protected requests. An application can explicitly request hydration
        // with activateNotebooks; login callbacks must not race the read that
        // caused the callback.
        if(next && options.refresh===true)
          void refreshNotebook(session,false,{onlyMissing:true,skipInFlight:true});
      }
    }
    const operationNotebooks=new Map();
    // A capability acknowledgement is not its final execution receipt.
    // Keep outcome messages separate from observation/authentication errors:
    // neither a deferred acceptance nor a terminal refusal should become a
    // sticky UI permission gate for an unrelated subsequent operation.
    const acknowledgedOperationDispositions=new Set([
      "accepted-deferred","command-returned","recorded"
    ]);
    function reconcileNotebookOutcome(session,receipt){
      const disposition=receipt?.disposition;
      if(disposition==="superseded")return null;
      if(disposition==="completed"){
        session.operationMessage="";
        return true;
      }
      const reason=String(receipt?.reason||disposition||"UNKNOWN");
      if(acknowledgedOperationDispositions.has(disposition))
        session.operationMessage="Operation acknowledged; completion not yet verified: "+reason;
      else if(disposition==="refused" || disposition==="refused-before-attempt")
        session.operationMessage="Operation refused: "+reason;
      else if(disposition==="failed")session.operationMessage="Operation failed: "+reason;
      else if(disposition==="cancelled")session.operationMessage="Operation cancelled: "+reason;
      else session.operationMessage="Operation outcome unverified: "+reason;
      return false;
    }
    function renderOperationNotebook(n,holder){
      const root=elt("section",{"data-mcel-operation-notebook":n.id,"aria-label":n.title});
      const tabs=elt("div",{"role":"tablist","data-mcel-operation-tabs":"","aria-label":n.title});
      const body=elt("div",{"role":"tabpanel","data-mcel-operation-body":""});
      const status=elt("p",{"role":"status","data-mcel-operation-status":""});
      const signIn=n.authentication?elt("button",{type:"button","data-mcel-operation-auth":""},n.authentication.label):null;
      root.append(elt("h3",{},n.title),tabs);
      if(signIn)root.append(signIn);
      root.append(body,status);holder.append(root);
      const session={node:n,root,active:n.tabs[0].id,available:false,busy:false,epoch:0,error:"",
        operationMessage:"",redraw:null,renderedTab:null,semanticUpdater:null,
        formBindings:[],readFailure:null};
      operationNotebooks.set(n,session);
      // The application, not MCEL, declares each tab's changing semantic
      // fields, actions, status and forms. An observation never rebuilds an
      // already-mounted tab with this contract.
      const updatingValues=tab=>!!tab.updates;
      function operationStatus(tab,observation){
        const result=tab.receipt?read(ref(tab.receipt)):null;
        const label=result&&typeof result.outcome==="string"?
          result.outcome+": "+String(result.message||""):"";
        return !session.available?
          (session.error||"Select an identity to access protected deployment information"):
          session.error||(!observation?"Operation status unavailable":session.operationMessage||label);
      }
      function redraw(){
        if(signIn){
          const hidden=session.available || !applicable(n);
          const disabled=session.busy || !applicable(n);
          if(signIn.hidden!==hidden)signIn.hidden=hidden;
          if(signIn.disabled!==disabled)signIn.disabled=disabled;
        }
        const tab=n.tabs.find(t=>t.id===session.active);
        // A newer successful observation resolves a prior transient read
        // failure even if the successful read came from background polling.
        const failure=session.readFailure;
        if(failure && failure.stateId===ref(tab.state) &&
           read(failure.stateId)!==failure.observation){
          if(session.error===failure.message)session.error="";
          session.readFailure=null;
        }
        if(updatingValues(tab) && session.renderedTab===tab.id){
          if(!session.semanticUpdater)
            session.semanticUpdater=global.McelSemanticValueUpdaters.mount(root);
          const observation=read(ref(tab.state));
          // A failed observation may leave the last verified display visible,
          // but ordinary consequential controls require a verified observation.
          // An application-declared always-enabled action is an exception.
          const allowed=session.available && !session.busy && !session.error &&
            applicable(n) && !!observation;
          const proposed={"notebook:status":operationStatus(tab,observation)};
          for(const field of tab.updates.fields)
            proposed["field:"+field]=plainValue(observation?.[field]);
          for(const label of tab.updates.actions){
            const action=tab.actions[label];
            proposed["action:"+label]=!operationActionEnabled(session,tab,action);
          }
          session.semanticUpdater.update(proposed);
          // Form controls and their choices are also application-declared
          // dynamic elements. Preserve typed input and select DOM on reads.
          if(tab.updates.forms)for(const {submit,choices} of session.formBindings){
            if(submit.disabled!==!allowed)submit.disabled=!allowed;
            for(const {input,choice} of choices){
              const source=read(ref(choice.state));
              const rows=source?.[choice.path];
              const options=Array.isArray(rows)?rows.filter(row=>
                typeof row?.[choice.valueKey]==="string" &&
                typeof row?.[choice.labelKey]==="string") : [];
              const expected=[{value:"",label:"Select a saved controller"},
                ...options.map(row=>({value:row[choice.valueKey],label:row[choice.labelKey]}))];
              const existing=[...input.options];
              if(existing.length!==expected.length || expected.some((item,i)=>
                existing[i]?.value!==item.value || existing[i]?.textContent!==item.label)){
                const previous=input.value;
                input.replaceChildren(...expected.map(item=>elt("option",{value:item.value},item.label)));
                if(expected.some(item=>item.value===previous))input.value=previous;
              }
              // Only an observed change in the selected controller changes a
              // user's selection; repeated reads never reset their choice.
              const observed=choice.selectedPath?source?.[choice.selectedPath]:"";
              if(input.__mcelObservedSelection!==observed){
                input.__mcelObservedSelection=observed;
                if(typeof observed==="string" && [...input.options].some(o=>o.value===observed))
                  input.value=observed;
              }
            }
          }
          return;
        }
        session.semanticUpdater?.dispose();session.semanticUpdater=null;
        session.formBindings=[];
        session.renderedTab=tab.id;
        tabs.replaceChildren(...n.tabs.map(t=>{
          const button=elt("button",{type:"button",role:"tab","data-mcel-operation-tab":t.id,
            "aria-selected":String(t.id===session.active)},t.label);
          button.addEventListener("click",()=>{
            if(session.busy)return;
            session.active=t.id;session.error="";session.readFailure=null;
            session.operationMessage="";redraw();
            if(session.available && applicable(n))void refreshOperationNotebook(session);
          });return button;
        }));
        body.replaceChildren();
        const observation=read(ref(tab.state));
        const marked=updatingValues(tab);
        const fields=elt("dl",{"data-mcel-operation-fields":""});
        if((observation && typeof observation==="object" && !Array.isArray(observation)) ||
            marked){
          for(const field of tab.fields){
            fields.append(elt("dt",{},fmt(field)),elt("dd",{
              "data-mcel-operation-field":field,
              ...(marked?{"data-mcel-updatable":"text","data-mcel-update-key":"field:"+field}:{})
            },plainValue(observation?.[field])));
          }
        }
        body.append(fields);
        if(tab.note)body.append(elt("p",{"data-mcel-operation-note":""},tab.note));
        const actions=elt("div",{"data-mcel-operation-actions":""});
        for(const [label,action] of Object.entries(tab.actions||{})){
          const button=elt("button",{type:"button","data-mcel-operation-action":label,
            ...(marked?{"data-mcel-updatable":"disabled","data-mcel-update-key":"action:"+label}:{})},label);
          button.disabled=!operationActionEnabled(session,tab,action);
          button.addEventListener("click",async()=>{
            // A declared always-enabled action is an attempt, not an authorization
            // grant. Dispatch even without a fresh observation; the capability
            // implementation owns refusal and concurrency policy.
            if(!action.alwaysEnabled &&
                (button.disabled||session.busy||!session.available||!applicable(n)))return;
            if(action.confirm && (typeof global.confirm!=="function" || !global.confirm(action.confirm)))return;
            session.operationMessage="";
            // Dispatch checks the current semantic gate synchronously before
            // the click's presentation-only busy flag is raised.
            const pending=execute(ref(action.intent));
            session.busy=true;redraw();const epoch=session.epoch;
            try{
              const receipt=await pending;
              if(epoch!==session.epoch)return;
              if(reconcileNotebookOutcome(session,receipt))
                await refreshOperationNotebook(session);
            }finally{if(epoch===session.epoch){session.busy=false;redraw();}}
          });
          actions.append(button);
        }
        body.append(actions);
        for(const spec of tab.forms||[]){
          const intent=c.intents.get(ref(spec.intent));
          assert(intent && intent.operationKind==="capability" && intent.risk==="consequential",
            "operation-notebook-form-intent-invalid");
          const form=elt("form",{"data-mcel-operation-form":ref(spec.intent)});
          form.append(elt("h4",{},spec.label));
          const inputs={};
          for(const arg of intent.input||[]){
            const name=arg.sourceName, choice=spec.choicesFrom?.[name];
            let field,input;
            if(choice){
              field=elt("label",{"data-field":name},fmt(name));
              input=elt("select",{name,required:true});
              const rows=read(ref(choice.state))?.[choice.path];
              input.append(elt("option",{value:""},"Select a saved controller"));
              if(Array.isArray(rows))for(const row of rows){
                if(typeof row?.[choice.valueKey]!=="string"||typeof row?.[choice.labelKey]!=="string")continue;
                input.append(elt("option",{value:row[choice.valueKey]},row[choice.labelKey]));
              }
              const selected=choice.selectedPath?read(ref(choice.state))?.[choice.selectedPath]:"";
              if(typeof selected==="string" && [...input.options].some(o=>o.value===selected))
                input.value=selected;
              input.__mcelObservedSelection=selected;
              field.append(input);
            }else{
              const built=renderInput(name,arg.schema);field=built.wrap;input=built.input;
              input.required=true;
              if(spec.secretFields?.includes(name)){
                input.type="password";input.autocomplete="new-password";
              }
            }
            inputs[name]=input;form.append(field);
          }
          const submit=elt("button",{type:"submit"},spec.submitLabel);
          submit.disabled=!session.available||session.busy||!!session.error||
            !applicable(n)||!observation;
          form.append(submit);
          if(marked)session.formBindings.push({submit,choices:Object.entries(inputs)
            .filter(([name])=>!!spec.choicesFrom?.[name])
            .map(([name,input])=>({input,choice:spec.choicesFrom[name]}))});
          form.addEventListener("submit",async event=>{
            event.preventDefault();
            if(submit.disabled||session.busy||!session.available||!applicable(n))return;
            const given={};
            for(const arg of intent.input||[]){
              const field=inputs[arg.sourceName];
              given[arg.sourceName]=arg.schema.kind==="boolean"?field.checked:
                arg.schema.kind==="integer"?Number(field.value):field.value;
            }
            if(spec.confirm&&(typeof global.confirm!=="function"||!global.confirm(spec.confirm)))return;
            session.operationMessage="";
            const pending=execute(ref(spec.intent),given);
            session.busy=true;const epoch=session.epoch;redraw();
            try{
              const receipt=await pending;
              if(epoch!==session.epoch)return;
              if(reconcileNotebookOutcome(session,receipt))
                await refreshOperationNotebook(session);
            }finally{if(epoch===session.epoch){session.busy=false;redraw();}}
          });
          body.append(form);
        }
        if(marked && tab.updates.status){
          status.setAttribute("data-mcel-updatable","text");
          status.setAttribute("data-mcel-update-key","notebook:status");
        }
        status.textContent=operationStatus(tab,observation);
      }
      if(signIn)signIn.addEventListener("click",async()=>{
        if(signIn.disabled || session.busy || session.available || !applicable(n))return;
        const epoch=session.epoch;
        session.busy=true;redraw();
        try{
          const receipt=await execute(ref(n.authentication.intent));
          if(epoch===session.epoch && receipt?.disposition!=="completed"){
            session.error="Sign-in refused: "+String(receipt?.reason||receipt?.disposition||"UNKNOWN");
          }
        }finally{
          if(epoch===session.epoch){session.busy=false;redraw();}
        }
      });
      session.redraw=redraw;redraw();
    }
    async function refreshOperationNotebook(session){
      if(!session.available||!applicable(session.node))return;
      const epoch=session.epoch,tab=session.node.tabs.find(t=>t.id===session.active);
      const stateId=ref(tab.state),before=read(stateId);
      const result=await execute(ref(tab.refresh));
      // Tab switches and authorization changes supersede this read's UI
      // status; the shared capability runtime arbitrates actual responses.
      if(epoch!==session.epoch || session.active!==tab.id)return;
      const after=read(stateId);
      if(result?.disposition==="completed" || after!==before){
        session.error="";session.readFailure=null;
      }else if(result?.disposition!=="superseded"){
        // A transient failed read is not a revocation. Keep the last
        // verified observation in place, explicitly marked as historical,
        // and disable its ordinary actions until the next successful observation.
        // Explicitly always-enabled actions remain clickable.
        // Explicit authorization changes still invalidate state above.
        const reason=String(result?.reason||result?.disposition||"AUTH_REQUIRED");
        session.error=before===undefined?
          "Observation unavailable: "+reason:
          "Observation refresh failed; showing last observed values (read-only): "+reason;
        session.readFailure={stateId,observation:after,message:session.error};
      }
      session.redraw();
    }
    const deselectionControls=new Set();
    function renderNode(n) {
      const holder=elt("div",{"data-mcel-node":n.id,"data-node-kind":n.nodeKind});

      if(n.visibleWhen)conditionedNodes.set(n,holder);
      if (n.nodeKind === "state-value") {
        const id=ref(n.value.state),el=elt("output",n.label && !c.generated ?
          {"data-mcel-state-projection":id} : {"data-state":id});
        // Explicitly labelled supplemental projections must not masquerade
        // as the primary result in selectors or accessibility contracts.
        // Authored read-only values may provide a semantic caption. Keep it
        // with the value so context remains understandable without styling.
        if(!c.generated && n.label)holder.append(elt("span",{"data-mcel-value-label":""},n.label));
        holder.append(el);
        if(c.states.get(id)?.schema?.kind === "string" &&
          [...c.intents.values()].some(i=>i.collectionSelection && ref(i.collectionSelection.target)===id)) {
          selectedRecordHosts.set(n,el);
        } else if(c.states.get(id)?.schema?.kind === "record") {
          typedRecordHosts.set(n,el);
        } else {if (!displays.has(id)) displays.set(id,[]); displays.get(id).push(el);}
      } else if (n.nodeKind === "operation-evidence") {
        const ol=elt("ol",{"aria-label":"Operation receipts","data-mcel-activity-history":""});
        const filterBar=elt("div",{"data-mcel-activity-filters":"",role:"group",
          "aria-label":"Show activity levels"});
        const rules=Array.isArray(n.activityEntries) ? new Map() : null;
        if(rules)for(const entry of n.activityEntries)for(const disposition of entry.dispositions)
          rules.set(ref(entry.intent)+":"+disposition,entry);
        const host={node:n,list:ol,levels:new Set(["info","warn","error"]),
          rules,rows:new Map(),summary:null,latest:null};
        for(const [level,label] of [["info","Info"],["warn","Warn"],["error","Error"]]){
          const button=elt("button",{type:"button","data-mcel-activity-level-toggle":level,
            "aria-pressed":"true"},label);
          button.addEventListener("click",()=>shadowDOMSource('activity-filter-toggle',()=>{
            if(host.levels.has(level))host.levels.delete(level);
            else host.levels.add(level);
            button.setAttribute("aria-pressed",String(host.levels.has(level)));
            renderActivities();
          }));
          filterBar.append(button);
        }
        if(expert){
          const details=elt("details",{"data-mcel-activity":""}),
            summary=elt("summary",{},"Activity · No operations yet");
          host.summary=summary;
          const latest=c.generated && [...c.regions.values()].some(r=>r.purpose==="inspection")
            ? elt("section",{"data-mcel-latest-request":"","aria-label":"Latest operation"}) : null;
          if(latest){
            latest.append(elt("h3",{},"Latest request"),
              elt("p",{"data-mcel-request-result":"","role":"status"},"No request yet"));
            holder.append(latest);host.latest=latest;
          }
          details.append(summary,filterBar,ol);holder.append(details);
        } else {
          const activity=elt("section",{"data-mcel-activity":""});
          activity.append(filterBar,ol);holder.append(activity);
        }
        receiptHosts.push(host);
      } else if (n.nodeKind === "record-notebook") {
        renderNotebook(n,holder);
      } else if (n.nodeKind === "operation-notebook") {
        renderOperationNotebook(n,holder);
      } else if (n.nodeKind === "collection") {
        const list=elt("div",{"data-collection":n.id});
        // A small, selectable navigation list is a list of destinations, not
        // a stack of record cards. The hint follows its verified collection
        // shape and region purpose, never an application or field name.
        if(region?.purpose==="navigation" && n.selectionIntent &&
           Array.isArray(n.fields) && n.fields.length<=2)
          list.setAttribute("data-mcel-navigation-list","compact");
        // Structured multi-field monitoring collections are primary records,
        // not narrow navigation widgets. This presentation hint is derived
        // from IR purpose/tasks/fields, never from an application or region ID.
        if(n.rowLayout){
          list.setAttribute("data-mcel-structured-list","");
          list.setAttribute("data-mcel-row-layout","identity-description-metadata");
          list.setAttribute("role","listbox");
          list.setAttribute("aria-label",region?.semanticTitle||"Selectable records");
        }else if(region?.purpose==="workspace" && region.tasks?.includes("monitor") &&
           Array.isArray(n.fields) && n.fields.length>=4){
          list.setAttribute("data-mcel-structured-list","");
          list.style.setProperty("--mcel-list-columns",String(n.fields.length));
        }
        if(n.selectionPresentation==="radio") {
          assert(n.selectionIntent && n.fields?.length===2 &&
            !!c.intents.get(ref(n.selectionIntent))?.collectionSelection,
            "radio-collection-contract-invalid");
          list.setAttribute("role","radiogroup");
          list.setAttribute("aria-label",n.label||"Choice");
          list.setAttribute("data-mcel-radio-choices","");
        }
        holder.append(list); collectionHosts.set(n,list);
        if(n.clearable && n.selectionIntent){
          const selection=c.intents.get(ref(n.selectionIntent));
          const parameter=selection?.input?.find(v=>v.id===ref(selection.collectionSelection?.input));
          if(parameter){
            const clear=elt("button",{type:"button","data-mcel-deselect":"",
              "aria-label":"Deselect identity"},"Deselect identity");
            clear.addEventListener("click",()=>void execute(selection.id,{[parameter.sourceName]:""}));
            holder.append(clear);
            const empty=elt("p",{"data-mcel-empty-selection":"","role":"status"},"No identity selected");
            holder.append(empty);
            const refresh=()=>{
              const value=read(ref(selection.collectionSelection.target));
              if(clear.disabled!==!value)clear.disabled=!value;
              if(empty.hidden!==!!value)empty.hidden=!!value;
            };
            refresh.shadowTargets=[clear,empty];
            deselectionControls.add(refresh);
          }
        }
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
            folderTrees.set(n,{paths:new Map(),collapsed:new Set(),current:"",root:"",searchResults:false,
              rootState:rootArg?ref(rootArg.state):null,intent:directoryIntent,pathParameter,
              rootListingIntent:rootListing?.id||null});
        }
      } else if (n.nodeKind === "document-editor") {
        const outer=elt("div",{"data-mcel-document-editor":n.id});
        const bar=elt("div",{"data-mcel-document-actions":""});
        const label=elt("span",{"data-mcel-document-label":""},"Select a source file and open it");
        const save=elt("button",{type:"button","data-mcel-document-save":"","data-mcel-generated-action":"document-save"},"Save source");
        const discard=elt("button",{type:"button","data-mcel-document-discard":"","data-mcel-generated-action":"document-discard"},"Discard draft");
        const close=elt("button",{type:"button","data-mcel-document-close":"","data-mcel-generated-action":"document-close"},"Close file");
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
        const intent=n.intent ? c.intents.get(ref(n.intent)) : null;
        const form=elt("form",intent ? {"data-intent":intent.id} : {"data-mcel-draft-only":""});
        const controls={};
        // An edited compute input makes the previous derived answer obsolete.
        // Supersede an in-flight result as well, so a late response cannot
        // present a calculation for an expression the user has since changed.
        // This is driven by semantic compute tasks, never by an app identity.
        const isComputeInput=!!intent && !!region?.tasks?.includes("compute");
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
        // Typed configuration forms with exactly two choices and two boolean
        // flags need an ordered form, not a four-column wrap. This is a
        // presentation shape derived entirely from the admitted field schemas.
        const fieldSchemas=Object.values(n.fields||{});
        const booleanEnumPairs=fieldSchemas.length===4 &&
          fieldSchemas.filter(s=>s.kind==="boolean").length===2 &&
          fieldSchemas.filter(s=>s.kind==="string" && Array.isArray(s.enum) && s.enum.length).length===2;
        if(booleanEnumPairs){
          form.setAttribute("data-mcel-form-pattern","boolean-enum-pairs");
          formFieldNames.sort((a,b)=>Number(n.fields[a].kind==="boolean")-
            Number(n.fields[b].kind==="boolean"));
        }
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
          if(Object.prototype.hasOwnProperty.call(n.placeholders||{},k))
            item.input.setAttribute("placeholder",n.placeholders[k]);
          if (Object.prototype.hasOwnProperty.call(n.defaults || {},k)) {
            if(schema.kind==="boolean"){
              item.input.checked=n.defaults[k]===true;
              item.input.defaultChecked=n.defaults[k]===true;
            }else{
              item.input.value=String(n.defaults[k]);
              item.input.defaultValue=String(n.defaults[k]);
            }
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
            if(currentValue!==undefined && currentValue!==null){
              if(schema.kind==="boolean")item.input.checked=currentValue===true;
              else item.input.value=String(currentValue);
            }
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
        if (intent) {
          const button=elt("button",{type:"submit"},n.submitLabel || fmt(intent.sourceName || intent.id));
          form.append(button);guardedFormButtons.push({button,intent});
        } else {
          // An unbound form is a configuration draft, never an executable
          // operation. No capability may be invoked until semantic authorship
          // explicitly supplies an intent in a later revision.
          form.append(elt("button",{type:"button",disabled:true,"aria-disabled":"true"},
            n.submitLabel || fmt(n.sourceName || n.id)));
        }
        const operationStatus=(intent?.risk==="consequential")
          ? elt("p",{role:"status","data-mcel-form-result":n.id},"") : null;
        if(operationStatus)form.append(operationStatus);
        if (intent && n.resetControl!==false && n.defaults && Object.keys(n.defaults).length)
          form.append(elt("button",{type:"reset","data-mcel-generated-action":"reset-inputs"},n.resetAndSubmit?"Reset graph":"Reset inputs"));
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
        if(intent && directoryParameter && (region?.tasks?.includes("browse") || region?.purpose==="navigation")) {
          const input=controls[directoryParameter], path=elt("span",{"data-mcel-current-directory":""},"/");
          const up=elt("button",{type:"button","data-mcel-parent-directory":"","data-mcel-generated-action":"parent-directory"},"Parent directory");up.disabled=true;
          const nav={input,path,up,current:""};directoryForms.add(nav);
          up.addEventListener("click",()=>{
            const parts=nav.current.split("/").filter(Boolean);parts.pop();
            void execute(intent.id,{[directoryParameter]:parts.join("/")});
          });form.append(up,path);
        }
        // A general compute task with a single textual expression gets a
        // generated arithmetic entry surface. This uses only semantic tasks
        // and field schema; no application names or backend evaluations.
        if (intent && expert && region.tasks?.includes("compute") && region.priority === "primary" &&
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
          if (!intent) return; // An intentless form can never dispatch work.
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
          if(operationStatus)operationStatus.textContent="Applying…";
          void execute(intent.id,input).then(receipt=>{
            if(!operationStatus)return;
            const result=(intent.resultBindings||[]).length
              ? read(ref(intent.resultBindings[0].target)):null;
            operationStatus.textContent=receipt.disposition==="completed"
              ? (typeof result?.message==="string"?result.message:"Operation completed")
              : "Operation refused: "+String(receipt.reason||receipt.disposition);
          });});
        holder.append(form);
      } else if (n.nodeKind === "control") {
        const intent=c.intents.get(ref(n.intent));
        const params=intent.input || [];
        if (intent.viewTransition && params.length===1 && Array.isArray(params[0].schema?.enum)) {
          const item=renderInput(params[0].sourceName,params[0].schema,fmt(ref(intent.viewTransition.target)));
          item.input.setAttribute("data-intent",intent.id);
          item.input.addEventListener("change",()=>void execute(intent.id,{[params[0].sourceName]:item.input.value}));
          // When the author declares a navigation landmark, its individual
          // semantic destinations are the control. Keep the selector as a
          // private event/state adapter instead of displaying duplicate UI.
          const navigationSwitcher=expert && region?.purpose==="navigation" &&
            params[0].schema.enum.length<=6;
          if(navigationSwitcher){
            // Keep the canonical source control mounted as a hidden state
            // adapter. Color Law must bind the intent's operation status here,
            // not to the first visible navigation destination.
            item.input.setAttribute("data-mcel-navigation-state-adapter","");
            item.wrap.hidden=true;
            holder.append(item.wrap);
          } else holder.append(item.wrap);
          if (expert && params[0].schema.enum.length <= 6) {
            const bar=elt("div",{"role":"group","aria-label":"Application "+fmt(ref(intent.viewTransition.target))+"s","data-mcel-mode-tabs":""});
            // Group semantics belong to the container; item semantics belong
            // to the individual switchers. Pointer interaction is not inherited.
            const buttons=[];
            for (const value of params[0].schema.enum) {
              // A host may supply canonical browser addresses for semantic
              // destinations. The same view intent still owns the transition;
              // URLs are navigation metadata, never authorization or state.
              const route=options.navigationRoutes?.[value];
              assert(!route || (typeof route==="string" && /^\/(?!\/)[A-Za-z0-9/_-]*$/.test(route)),
                "invalid-canonical-navigation-route");
              // Every generated destination executes this source intent. Keep
              // the binding on the actual interactive element so the Color
              // Law can resolve it even when the backing selector is hidden.
              const tab=route ? elt("a",{href:route,role:"button","data-intent":intent.id,
                "data-value":value,"aria-pressed":"false"},fmt(value)) :
                elt("button",{type:"button","data-intent":intent.id,
                  "data-value":value,"aria-pressed":"false"},fmt(value));
              tab.addEventListener("click",event=>{
                if(route) {
                  // Native modified clicks, window-opening and copy-link remain
                  // browser-owned. Only the ordinary in-page transition is intercepted.
                  if(event.defaultPrevented || event.button!==0 || event.metaKey || event.ctrlKey ||
                     event.shiftKey || event.altKey)return;
                  event.preventDefault();
                  if(global.location?.protocol?.startsWith("http") && global.history?.pushState &&
                     global.location.pathname!==route)global.history.pushState(null,"",route);
                }
                item.input.value=value;
                item.input.dispatchEvent(new Event("change",{bubbles:true}));
              });
              // A semantic navigation subtitle is a projection of a second
              // renderer-local state, not a new destination or an authorization.
              // Keep its DOM stable: only its text changes when the choice does.
              if(n.navigationSubtitle?.forValue===value){
                tab.replaceChildren(
                  elt("span",{"data-mcel-navigation-label":""},fmt(value)),
                  elt("span",{"data-mcel-navigation-subtitle":""},n.navigationSubtitle.fallback)
                );
              }
              buttons.push(tab);bar.append(tab);
            }
            holder.append(bar);tabSwitchers.push({stateId:ref(intent.viewTransition.target),
              select:item.input,buttons,subtitle:n.navigationSubtitle||null});
            if(options.navigationRoutes && global.addEventListener) {
              const syncHistory=()=>{
                const value=Object.keys(options.navigationRoutes).find(key=>
                  options.navigationRoutes[key]===global.location?.pathname);
                if(value && item.input.value!==value) {
                  item.input.value=value;
                  item.input.dispatchEvent(new Event("change",{bubbles:true}));
                }
              };
              global.addEventListener("popstate",syncHistory);
              navigationHistoryListeners.push(syncHistory);
            }
          }
        } else {
          const button=elt("button",{type:"button","data-intent":intent.id},fmt(intent.sourceName || intent.id));
          if (params.length) {button.disabled=true;button.title="Requires selected record or form input";}
          if(intent.requestArguments?.length || n.enabledWhen)actionButtons.push({button,intent,node:n});
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
      add("path",{d,fill:"none",stroke:"var(--mcel-color-accent, currentColor)","data-mcel-series-role":"primary","stroke-width":2.5,"stroke-linejoin":"round"});
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
      const jobPolicy=n.externalJob;
      const pendingAider=!!jobPolicy &&
        [jobPolicy.pendingStatus,jobPolicy.unverifiedStatus].includes(aiderStatus);
      const lostAuthority=aiderStatus==="reopen-required" && session.tabs.has(session.activePath);
      session.save.disabled=!session.editable || session.savePending || session.conflict || pendingAider || lostAuthority || !session.dirty || !session.activePath || selectedMismatch;
      session.discard.disabled=session.savePending || !session.dirty || !session.activePath || selectedMismatch;
      updateDocumentTabs(n,session);
      session.status.dataset.mcelDocumentDirty=String(session.dirty);
      if(pendingAider){
        session.status.textContent=aiderStatus===jobPolicy.unverifiedStatus
          ? (jobPolicy.unverifiedMessage||"Job outcome unverified; source writes blocked.")
          : (jobPolicy.pendingMessage||"Job pending; source writes blocked.");
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
             (jobPolicy && [jobPolicy.pendingStatus,jobPolicy.unverifiedStatus]
               .includes(port.documentWriteStatus?.(openingPath))))return;
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
    // Use the source-proven roots -> directory dependency to attach the
    // hierarchy to its authored navigation region. Do not synthesize another
    // location list or infer ownership from the application name.
    function directoryNavigationHost(n){
      const chain=navigationRoots.find(nav=>nav.entriesState===ref(n.state));
      if(!chain)return null;
      const region=[...c.regions.values()].find(r=>r.purpose==="navigation" &&
        (r.members||[]).some(member=>{
          const node=c.nodes.get(ref(member));
          return node?.nodeKind==="collection" && ref(node.state)===chain.rootsState;
        }));
      return region ? regionHosts.get(region) : null;
    }
    function updateFolderTree(n,holder,tree){
      const navigationHost=directoryNavigationHost(n);
      const previousTree=navigationHost?.querySelector("[data-mcel-folder-tree]") ||
        holder.querySelector("[data-mcel-folder-tree]");
      const previousScroll=previousTree?.scrollTop||0;
      const previousDetailsScroll=holder.querySelector("[data-mcel-directory-contents]")?.scrollTop||0;
      const previousFocus=(holder.contains(document.activeElement) ||
        navigationHost?.contains(document.activeElement))
        ?document.activeElement.getAttribute("data-select-key") ||
         document.activeElement.getAttribute("data-mcel-tree-toggle"):null;
      const layer=elt("div",{"data-mcel-folder-tree":"",role:"tree","aria-label":"Folder hierarchy"});
      const selectedRoot=tree.rootState?read(tree.rootState):"";
      if(tree.root!==selectedRoot){tree.root=selectedRoot;tree.paths.clear();tree.collapsed.clear();tree.current="";}
      const select=n.selectionIntent && c.intents.get(ref(n.selectionIntent));
      const parameter=select?.input?.find(p=>p.id===ref(select.collectionSelection?.input));
      const explorer=Array.isArray(n.fields) && n.fields.includes("bytes") && n.fields.includes("pathDisplay");
      const visited=new Set();
      function selectObservedFile(item,parent){
        if(!select || !parameter)return;
        const id=String(item[n.key]);
        if(parent===tree.current)return void execute(select.id,{[parameter.sourceName]:item[n.key]});
        // A retained branch is a navigation hint. Re-fetch before selecting.
        void (async()=>{
          const reopened=await execute(tree.intent.id,{[tree.pathParameter]:parent});
          const fresh=read(ref(n.state));
          if(reopened.disposition!=="completed" || !Array.isArray(fresh) ||
             !fresh.some(row=>row && String(row[n.key])===id))return;
          await execute(select.id,{[parameter.sourceName]:item[n.key]});
        })();
      }
      async function inspectObservedFolder(item,parent){
        if(!select || !parameter)return;
        const id=String(item[n.key]);
        if(parent!==tree.current){
          const reopened=await execute(tree.intent.id,{[tree.pathParameter]:parent});
          const fresh=read(ref(n.state));
          if(reopened.disposition!=="completed" || !Array.isArray(fresh) ||
              !fresh.some(row=>row && String(row[n.key])===id))return;
        }
        await execute(select.id,{[parameter.sourceName]:item[n.key]});
      }
      function branch(path,depth){
        if(depth>32 || visited.has(path))return;
        visited.add(path);
        const entries=tree.paths.get(path);if(!Array.isArray(entries))return;
        const group=elt("div",{role:"group","data-mcel-tree-branch":path});
        const ordered=explorer?[...entries].sort((a,b)=>{
          const ad=a.kind==="directory"||a.kind==="dir",bd=b.kind==="directory"||b.kind==="dir";
          return Number(bd)-Number(ad) || String(a.name||"").localeCompare(String(b.name||""));
        }):entries;
        for(const item of ordered){
          const id=String(item[n.key]);
          const directory=item.kind==="directory" || item.kind==="dir";
          const loaded=tree.paths.has(id),expanded=loaded&&!tree.collapsed.has(id);
          const row=elt("div",{"data-mcel-tree-depth":depth,role:"treeitem",
            "aria-level":depth+1,"aria-selected":String(id===tree.current)});
          if(directory)row.setAttribute("aria-expanded",String(expanded));
          if(explorer){
            if(directory){
              const toggle=elt("button",{type:"button","data-mcel-tree-toggle":id,
                "aria-label":(expanded?"Collapse ":"Expand ")+String(item.name||id),
                "aria-expanded":String(expanded),title:expanded?"Collapse folder":"Expand folder"},expanded?"▾":"▸");
              toggle.addEventListener("click",()=>{
                if(!loaded){void execute(tree.intent.id,{[tree.pathParameter]:id});return;}
                if(expanded)tree.collapsed.add(id);else tree.collapsed.delete(id);
                updateFolderTree(n,holder,tree);
              });row.append(toggle);
            }else row.append(elt("span",{"data-mcel-tree-leaf":"","aria-hidden":"true"},"·"));
            row.append(elt("span",{"data-mcel-tree-icon":"","aria-hidden":"true"},directory?"▣":"▤"));
          }
          const label=elt("span",{"data-record-field":"name"},String(item.name||id));
          const button=elt("button",{type:"button","data-select-key":id},
            explorer?String(item.name||id):directory?"Open folder":"Select");
          if(directory){
            button.addEventListener("click",()=>void execute(tree.intent.id,{[tree.pathParameter]:id}));
            if(select && parameter){
              const inspect=elt("button",{type:"button","data-mcel-inspect-folder":id,
                "aria-label":"Inspect folder "+String(item.name||id),title:"Inspect folder"},explorer?"ⓘ":"Inspect folder");
              inspect.addEventListener("click",()=>void inspectObservedFolder(item,path));
              row.append(inspect);
            }
          }else if(select && parameter){
            button.addEventListener("click",()=>selectObservedFile(item,path));
          }else button.disabled=true;
          if(explorer){
            // The name is the navigation control; don't repeat the same text
            // next to a second oversized button on every tree row.
            label.hidden=true;
            const nameCell=elt("span",{"data-record-key":id,"data-mcel-tree-depth":depth,
              "data-mcel-tree-name-cell":""});
            nameCell.append(button);
            row.insertBefore(nameCell,row.querySelector("[data-mcel-inspect-folder]"));
          }else{
            const nameCell=elt("span",{"data-record-key":id,"data-mcel-tree-depth":depth});
            nameCell.append(button);row.append(label,nameCell);
          }
          group.append(row);
          if(directory && expanded){const child=branch(id,depth+1);if(child)group.append(child);}
        }
        return group;
      }
      const top=branch("",0);if(top)layer.append(top);
      if(explorer)layer.addEventListener("keydown",event=>{
        if(!["ArrowDown","ArrowUp","ArrowRight","ArrowLeft","Home","End"].includes(event.key))return;
        const names=[...layer.querySelectorAll("[data-select-key]")];
        if(!names.length)return;
        const currentRow=event.target.closest?.('[role="treeitem"]');
        const name=currentRow?.querySelector("[data-select-key]");
        const index=names.indexOf(name);
        const focus=target=>target?.focus({preventScroll:false});
        if(event.key==="ArrowDown"||event.key==="ArrowUp"){
          event.preventDefault();focus(names[Math.max(0,Math.min(names.length-1,index+(event.key==="ArrowDown"?1:-1)))]);return;
        }
        if(event.key==="Home"||event.key==="End"){
          event.preventDefault();focus(event.key==="Home"?names[0]:names[names.length-1]);return;
        }
        const toggle=currentRow?.querySelector("[data-mcel-tree-toggle]");
        if(event.key==="ArrowRight"){
          if(toggle && toggle.getAttribute("aria-expanded")==="false"){
            event.preventDefault();toggle.click();return;
          }
          const sibling=currentRow?.nextElementSibling;
          if(sibling?.getAttribute("role")==="group"){
            event.preventDefault();focus(sibling.querySelector("[data-select-key]"));
          }
        }else if(event.key==="ArrowLeft"){
          if(toggle && toggle.getAttribute("aria-expanded")==="true"){
            event.preventDefault();toggle.click();return;
          }
          const parentPath=currentRow?.closest("[data-mcel-tree-branch]")?.getAttribute("data-mcel-tree-branch");
          if(parentPath){
            const parent=names.find(node=>node.getAttribute("data-select-key")===parentPath);
            if(parent){event.preventDefault();focus(parent);}
          }
        }
      });
      if(!explorer){holder.replaceChildren(layer);return;}
      // The same authorized directory evidence projects into two semantic
      // owners: navigable hierarchy beneath Locations, and current entries in
      // the primary workspace. Selection/inspection bindings are unchanged.
      const view=elt("div",{"data-mcel-explorer-view":""});
      holder.setAttribute("data-mcel-explorer-list","");
      const treePane=elt("section",{"data-mcel-explorer-tree-pane":"",
        "data-mcel-explorer-navigation":"","aria-label":"Folders"});
      treePane.append(elt("h3",{},"Folders"),layer);
      const contentPane=elt("section",{"data-mcel-explorer-content-pane":"","aria-label":"Directory entries"});
      const heading=elt("div",{"data-mcel-directory-head":""});
      heading.append(elt("h3",{},tree.searchResults?"Search results":"Current directory"),
        elt("span",{"data-mcel-directory-path":""},tree.current?"/"+tree.current:"/"));
      contentPane.append(heading);
      const columns=elt("div",{"data-mcel-directory-columns":"",role:"presentation"});
      columns.append(elt("span",{},"Name"),elt("span",{},"Type"),elt("span",{},"Size"));
      contentPane.append(columns);
      const list=elt("div",{"data-mcel-directory-contents":"","aria-label":"Current directory contents"});
      const currentEntries=tree.searchResults?read(ref(n.state)):(tree.paths.get(tree.current)||[]);
      const visibleTreeKeys=new Set([...layer.querySelectorAll("[data-record-key]")]
        .map(row=>row.getAttribute("data-record-key")));
      const sorted=[...(Array.isArray(currentEntries)?currentEntries:[])].sort((a,b)=>{
        const ad=a.kind==="directory"||a.kind==="dir",bd=b.kind==="directory"||b.kind==="dir";
        return Number(bd)-Number(ad) || String(a.name||"").localeCompare(String(b.name||""));
      });
      function sizeOf(item){
        if(item.kind==="directory"||item.kind==="dir")return "—";
        const bytes=item.bytes;
        if(!Number.isFinite(bytes)||bytes<0)return "—";
        if(bytes<1024)return bytes+" B";
        if(bytes<1048576)return (bytes/1024).toFixed(bytes<10240?1:0)+" KB";
        return (bytes/1048576).toFixed(bytes<10485760?1:0)+" MB";
      }
      for(const item of sorted){
        const id=String(item[n.key]),directory=item.kind==="directory"||item.kind==="dir";
        const row=elt("div",{"data-mcel-directory-entry":id,"data-mcel-directory-kind":directory?"folder":"file",
          ...(tree.searchResults&&!visibleTreeKeys.has(id)?{"data-record-key":id}:{})});
        const open=elt("button",{type:"button","data-mcel-directory-open":id,
          title:(directory?"Open folder: ":"Select file: ")+String(item.name||id)},String(item.name||id));
        open.prepend(elt("span",{"aria-hidden":"true","data-mcel-directory-icon":""},directory?"▣":"▤"));
        if(directory)open.addEventListener("click",()=>void execute(tree.intent.id,{[tree.pathParameter]:id}));
        else if(select&&parameter)open.addEventListener("click",()=>void execute(select.id,{[parameter.sourceName]:item[n.key]}));
        else open.disabled=true;
        row.append(open,elt("span",{"data-mcel-directory-type":""},directory?"Folder":
          String(item.category && item.category!=="other"?item.category:"File")),
          elt("span",{"data-mcel-directory-size":""},sizeOf(item)));
        list.append(row);
      }
      if(!sorted.length)list.append(elt("p",{role:"status"},"This directory is empty."));
      contentPane.append(list);view.append(contentPane);
      holder.replaceChildren(view);
      if(navigationHost){
        const existing=navigationHost.querySelector("[data-mcel-explorer-navigation]");
        if(existing)existing.replaceWith(treePane);
        else navigationHost.append(treePane);
      }else view.prepend(treePane); // Non-planned semantic host fallback.
      layer.scrollTop=previousScroll;list.scrollTop=previousDetailsScroll;
      if(previousFocus){
        const focused=[...(navigationHost||holder).querySelectorAll("[data-select-key],[data-mcel-tree-toggle]"),
          ...holder.querySelectorAll("[data-select-key],[data-mcel-tree-toggle]")]
          .find(element=>element.getAttribute("data-select-key")===previousFocus ||
            element.getAttribute("data-mcel-tree-toggle")===previousFocus);
        focused?.focus({preventScroll:true});
      }
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
      // Task-owned selected references are parameter context, not a second
      // full inspection panel. Their original identity and binding remain in
      // the persistent navigation; a task shows the selected label only.
      if(host.closest('[data-mcel-purpose="task"]')) {
        const compact=elt("div",{"data-mcel-reference":"selected","role":"note"});
        compact.append(elt("span",{"data-mcel-reference-caption":""},n.label || "Selected reference"),
          elt("strong",{"data-mcel-reference-label":""},plainValue(record.label ?? record.name ?? selected)));
        host.append(compact);
        return;
      }
      const list=elt("dl",{"data-mcel-selected-details":selected});
      for(const [key,value] of Object.entries(record)) {
        list.append(elt("dt",{},fmt(key)),elt("dd",{},plainValue(value)));
      }
      host.append(list);
    }
    function updateGeneratedCollection(n,holder,list) {
      if(!Array.isArray(list)) {
        keyedCollectionRows.delete(holder);
        const message="No observation loaded. Use the query above to retrieve records.";
        if(holder.textContent!==message || holder.querySelector('[data-record-key]'))
          holder.replaceChildren(elt("p",{"role":"status"},message));
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
          if(n.selectionPresentation==="radio") {
            const parameter=selection?.input.find(p=>p.id===ref(selection.collectionSelection.input));
            assert(parameter && selection?.collectionSelection && n.fields?.length===2,
              "radio-collection-selection-contract-invalid");
            const labelField=n.fields.find(field=>field!==n.key);
            row=elt("label",{"data-record-key":key,"data-mcel-radio-choice":""});
            const input=elt("input",{type:"radio",name:n.id,value:key,"data-select-key":key});
            input.addEventListener("change",()=>{
              if(input.checked)void execute(selection.id,{[parameter.sourceName]:key});
            });
            row.append(input,elt("span",{"data-record-field":labelField}));
          }
        }
        if(!row) {
          const rowSelection=!!n.rowLayout?.selectOnRow && !!selection?.collectionSelection;
          row=elt("div",{"data-record-key":key,"data-mcel-record-row":"",
            ...(n.credentialDisclosure?{"data-mcel-credential-row":""}:{}),
            ...(rowSelection?{role:"option",tabindex:"0","data-mcel-row-selectable":"",
              "data-select-key":key}:{})});
          const navList=!!holder.closest('[data-mcel-purpose="navigation"]');
          if(n.rowLayout){
            const identity=elt("div",{"data-mcel-row-identity":""});
            const description=elt("div",{"data-mcel-row-description":""});
            const metadata=elt("div",{"data-mcel-row-metadata":""});
            for(const field of n.fields||[]){
              const role=n.rowLayout.identity.includes(field)?"identity":
                field===n.rowLayout.description?"description":"metadata";
              const cell=elt("span",{"data-record-field":field,"data-field-label":fmt(field),
                "data-mcel-field-role":role});
              ({identity,description,metadata})[role].append(cell);
            }
            row.append(identity,description,metadata);
          }else{
            for(const field of n.fields || [])if(!(navList && field===n.fields[0]))
              row.append(elt("span",{"data-record-field":field,"data-field-label":fmt(field)}));
          }
          if(selection?.collectionSelection){
            const parameter=selection.input.find(p=>p.id===ref(selection.collectionSelection.input));
            if(rowSelection){
              const selectRow=()=>void execute(selection.id,{[parameter.sourceName]:key});
              row.addEventListener("click",event=>{
                if(event.target.closest("button,a,input,select,textarea,summary,details"))return;
                selectRow();
              });
              row.addEventListener("keydown",event=>{
                if(event.target!==row || (event.key!=="Enter" && event.key!==" "))return;
                event.preventDefault();selectRow();
              });
            }else{
              const choose=elt("button",{type:"button","data-select-key":key},"Select");
              choose.addEventListener("click",()=>void execute(selection.id,{[parameter.sourceName]:key}));
              row.append(choose);
            }
          }
          if(!n.rowLayout){
            const expand=elt("details",{"data-mcel-inline-details":key});
            if(n.credentialDisclosure){
              const credential=elt("section",{"data-mcel-credential-detail":key,
                "aria-label":"Identity current passkey"});
              const state=elt("span",{"data-mcel-passkey-status":"","role":"status"},"Not checked");
              const code=elt("output",{"data-mcel-passkey-code":"","aria-label":"Current passkey"},"•••• ••••");
              const seconds=elt("span",{"data-mcel-passkey-remaining":""},"—");
              const copy=elt("button",{type:"button","data-mcel-passkey-copy":"",disabled:true},"Copy");
              credential.append(state,code,seconds,copy);
              expand.append(elt("summary",{},"More details"),credential);
              expand.addEventListener("toggle",async()=>{
                if(!expand.open){
                  code.textContent="•••• ••••";
                  copy.disabled=true;
                  state.textContent="Closed";seconds.textContent="—";
                  return;
                }
                state.textContent="Checking passkey…";
                const current=selection?.collectionSelection && read(ref(selection.collectionSelection.target));
                if(current!==key && selection?.collectionSelection){
                  const parameter=selection.input.find(p=>p.id===ref(selection.collectionSelection.input));
                  const chosen=await execute(selection.id,{[parameter.sourceName]:key});
                  if(!expand.open)return;
                  if(read(ref(selection.collectionSelection.target))!==key && chosen?.disposition!=="completed"){
                    state.textContent="Unavailable — identity selection refused";return;
                  }
                }
                const receipt=await execute(ref(n.credentialDisclosure.intent),{adminDeviceId:key});
                if(!expand.open)return;
                if(receipt?.disposition!=="completed" && state.textContent==="Checking passkey…")
                  state.textContent="Unavailable — "+String(receipt?.reason||receipt?.disposition||"authorization required");
              });
            }else{
              expand.append(elt("summary",{},"More details"),elt("dl",{"data-mcel-detail-fields":""}));
            }
            row.append(expand);
          }
        }
        for(const field of n.fields || []) {
          const el=[...row.querySelectorAll("[data-record-field]")].find(e=>e.getAttribute("data-record-field")===field);
          if(el){const value=plainValue(record[field]);if(el.textContent!==value)el.textContent=value;}
        }
        if(n.roleFrom){
          const source=read(ref(n.roleFrom.state));
          const matched=Array.isArray(source)?source.find(item=>item?.[n.roleFrom.key]===key):null;
          const verified=matched?.[n.roleFrom.roleField]==="admin";
          let badge=row.querySelector("[data-mcel-verified-role]");
          if(!badge){badge=elt("span",{"data-mcel-verified-role":""});row.append(badge);}
          const roleText=verified?"Administrator":"Role not verified";
          if(badge.textContent!==roleText)badge.textContent=roleText;
          if(badge.getAttribute("data-role-verified")!==String(verified))
            badge.setAttribute("data-role-verified",String(verified));
        }
        const expanded=row.querySelector("[data-mcel-inline-details]");
        if(expanded){
          const more=n.credentialDisclosure?[]:
            Object.entries(record).filter(([key])=>key!==n.key && !(n.fields||[]).includes(key));
          expanded.hidden=!n.credentialDisclosure && !more.length;
          const detail=expanded.querySelector("[data-mcel-detail-fields]");
          if(detail){
            const detailsSignature=JSON.stringify(more);
            if((expanded.open || !detail.childElementCount) &&
                detail.__mcelDetailsSignature!==detailsSignature){
              detail.replaceChildren(...more.flatMap(([key,val])=>[elt("dt",{},fmt(key)),elt("dd",{},plainValue(val))]));
              detail.__mcelDetailsSignature=detailsSignature;
            }
          }
        }
        const inNavigation=!!holder.closest('[data-mcel-purpose="navigation"]');
        const chooseButton=row.querySelector("button[data-select-key]");
        if(chooseButton && inNavigation){
          const label=plainValue(record[n.fields?.[0]] ?? key);
          if(chooseButton.textContent!==label)chooseButton.textContent=label;
        }
        const selected=String(selectionValue!==undefined && selectionValue!=="" && key===String(selectionValue));
        if(n.selectionPresentation==="radio") {
          const input=row.querySelector('input[type="radio"]');
          if(input && input.checked!==(selected==="true"))input.checked=(selected==="true");
        }
        if(row.getAttribute("aria-selected")!==selected)row.setAttribute("aria-selected",selected);
        next.set(key,row);ordered.push(row);
      }
      // Insert only moved or newly observed nodes. Unchanged nodes never leave
      // the DOM; neither their expansion nor their tab focus is reset.
      for(let i=0;i<ordered.length;i++)if(holder.children[i]!==ordered[i])
        holder.insertBefore(ordered[i],holder.children[i]||null);
      for(const [key,row] of existing)if(!next.has(key))row.remove();
      if(!ordered.length) {
        const message="No records match the current observation.";
        if(holder.textContent!==message || holder.querySelector('[data-record-key]'))
          holder.replaceChildren(elt("p",{"role":"status"},message));
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
      if(c.generated || n.rowLayout){updateGeneratedCollection(n,holder,list);return;}
      holder.replaceChildren();
      if (!Array.isArray(list)) {
        if(folderTrees.has(n))directoryNavigationHost(n)
          ?.querySelector("[data-mcel-explorer-navigation]")?.remove();
        const location=navigationRoots.some(nav=>ref(n.state)===nav.entriesState);
        holder.append(elt("p",{role:"status"},location?"Loading directory or waiting for an accessible location…":"No records loaded"));return;
      }
      const tree=folderTrees.get(n);
      if(tree?.paths.size){updateFolderTree(n,holder,tree);return;}
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
          const button=elt("button",{type:"button","data-select-key":String(item[n.key]),
            ...(navigation?{"data-mcel-generated-action":selected?"refresh-location":"open-location"}:{})},
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
        if(n.roleFrom){
          const source=read(ref(n.roleFrom.state));
          const role=Array.isArray(source)?source.find(value=>value?.[n.roleFrom.key]===String(item[n.key])):null;
          row.append(elt("span",{"data-mcel-verified-role":"",
            "data-role-verified":String(role?.[n.roleFrom.roleField]==="admin")},
            role?.[n.roleFrom.roleField]==="admin"?"Administrator":"Role not verified"));
        }
        holder.append(row);
      }
    }
    const allowedTags=new Set(["main","section","nav","aside","header","footer","article","output","div","details"]);
    function renderRegion(id) {
      const r=c.regions.get(id);
      region=r;
      const isRedundantFormObservation=c.generated && r.recipe==="results" &&
        r.members?.length===1 && (()=>{
          const item=c.nodes.get(ref(r.members[0]));
          if(item?.nodeKind!=="state-value" ||
             c.states.get(ref(item.value?.state))?.schema?.kind!=="record")return false;
          const stateId=ref(item.value.state);
          return [...c.nodes.values()].some(node=>node.nodeKind==="form" &&
            Object.values(node.acceptResult||{}).some(binding=>ref(binding.state)===stateId));
        })();
      const tag=isRedundantFormObservation?"details":
        allowedTags.has(r.nativeTag) ? r.nativeTag : "section";
      const inferredContent=r.content?.kind ||
        (r.members?.some(member=>["collection","record-notebook","operation-notebook"].includes(c.nodes.get(ref(member))?.nodeKind)) ? "collection" : "");
      const el=elt(tag,{"data-mcel-region":r.id,"data-mcel-purpose":r.purpose || "","data-mcel-priority":r.priority || "", "data-mcel-tasks":(r.tasks || []).join(" "),"data-mcel-content-kind":inferredContent,
        ...(r.preferences?.density ? {"data-mcel-density":r.preferences.density} : {}),
        ...(r.preferences?.widthGroup ? {"data-mcel-width-group":r.preferences.widthGroup} : {}),
        ...(r.preferences?.sizing ? {"data-mcel-sizing":r.preferences.sizing} : {}),
        ...(r.preferences?.align ? {"data-mcel-align":r.preferences.align} : {}),
        ...(isRedundantFormObservation?{"data-mcel-observed-form-disclosure":""}:{})});
      regionHosts.set(r,el);
      if(c.generated && r.recipe!=="root") {
        const titles={navigation:"Navigation",queries:"Observation query",collection:"Observed records",
          "selection-echo":"Selected record",evidence:"Operation evidence",
          results:"Measurements and results",controls:"Available controls",operations:"Operations",document:"Document",
          task:"Task",commands:"Commands",inspection:"Service details"};
        if(["task","commands","inspection"].includes(r.recipe))titles[r.recipe]=fmt(r.semanticTitle);
        if(r.recipe==="operations" && r.members.some(m=>c.nodes.get(ref(m))?.nodeKind==="form"))
          titles.operations="Configuration";
        if(r.recipe==="collection" && r.members.length===1){
          const node=c.nodes.get(ref(r.members[0]));
          if(["record-notebook","operation-notebook"].includes(node?.nodeKind))titles.collection=node.title;
          if(node?.selectionPresentation==="radio")titles.collection=node.label||"Choice";
        }
        if(r.recipe==="results" && r.members.length===1){
          const node=c.nodes.get(ref(r.members[0]));
          if(typeof node?.label==="string" && node.label)titles.results=node.label;
        }
        if(r.recipe==="controls" && r.members.every(m=>c.intents.get(ref(c.nodes.get(ref(m))?.intent))?.viewTransition))
          titles.controls="Monitoring views";
        if(r.recipe==="queries" && r.members.every(m=>{
          const node=c.nodes.get(ref(m));return node?.nodeKind==="form" && !node.intent;
        })) titles.queries="Configuration";
        if(r.recipe==="selection-echo" && r.members.length===1){
          const selected=c.nodes.get(ref(r.members[0]));
          if(typeof selected?.label==="string") titles["selection-echo"]=selected.label;
        }
        if(r.recipe==="navigation") {
          const source=(ir.surfaces[0]?.interfaceIntent?.regions || []).find(a=>
            a.role==="navigation" && (a.members || []).some(m=>r.members.some(b=>ref(b)===ref(m))));
          if(source)titles.navigation=fmt(source.id);
        }
        el.append(elt(isRedundantFormObservation?"summary":"h2",{},titles[r.recipe]));
      }
      for (const annotation of r.staticContent || []) {
        if (!["h1","h2","h3","h4","h5","h6","p","span","strong","em","small","label"].includes(annotation.tag))continue;
        // A native semantic <details> region is an opt-in disclosure. Its
        // authored heading becomes the keyboard-operable summary; descendants
        // keep their original node identities, intents and authority.
        if(tag==="details" && /^h[1-6]$/.test(annotation.tag) && !el.querySelector("summary"))
          el.append(elt("summary",{"data-mcel-disclosure-title":""},annotation.text));
        else el.append(elt(annotation.tag, {}, annotation.text));
      }
      // Only consecutive, zero-input read capabilities are ordinary action
      // peers. Keep view selectors, parameter forms and consequential commands
      // outside the flow, in their authored order. The control nodes, intents,
      // guards and effect handlers remain identical to the ungrouped version.
      // A verified semantic task groups the consequential form and its chosen
      // reference in one form. This is the only DOM nesting policy added here:
      // no names, app IDs, layout coordinates, handlers or authority change.
      if(c.generated && r.recipe==="task") {
        const members=(r.members||[]).map(m=>c.nodes.get(ref(m)));
        const formNodes=members.filter(n=>n.nodeKind==="form");
        const references=members.filter(n=>n.nodeKind==="state-value");
        const controls=members.filter(n=>n.nodeKind==="control");
        // Task actions are authored consequential controls. Their semantic
        // membership, not application-specific names, admits them to the
        // configuration form's footer.
        assert(formNodes.length===1 && references.length===1 &&
          members.length===2+controls.length && controls.every(n=>
            c.intents.get(ref(n.intent))?.risk==="consequential"),
          "generated-task-members-invalid");
        const formNode=formNodes[0],referenceNode=references[0];
        const formHolder=renderNode(formNode),form=formHolder.querySelector('form');
        // A paired text/numeric parameter set has a wide identifier/address and
        // a compact numeric parameter. Derive this from validated field types,
        // never an application name or the authored field identifiers.
        const fields=Object.values(formNode.fields||{});
        const paired=fields.length===2 && fields[0]?.kind==="string" &&
          ["integer","number"].includes(fields[1]?.kind);
        const numericReference=fields.length===1 &&
          ["integer","number"].includes(fields[0]?.kind);
        form.setAttribute("data-mcel-task-fields",paired?"text-number":
          numericReference?"number-reference":"stacked");
        const reference=renderNode(referenceNode);
        form.insertBefore(reference,form.querySelector('button[type="submit"]'));
        // Primary and reset buttons are a footer; a consequential result is
        // feedback below that footer, never a flex peer competing with it.
        const actions=elt("div",{"data-mcel-task-actions":"",role:"group","aria-label":"Task actions"});
        for(const child of [...form.children])if(child.matches('button'))actions.append(child);
        for(const control of controls)actions.append(renderNode(control));
        form.append(actions);
        for(const child of [...form.children])if(child.matches('[data-mcel-form-result]'))form.append(child);
        el.append(formHolder);
        for(const child of c.children.get(id))el.append(renderRegion(child.id));
        return el;
      }
      // Resolved template parameters govern the initial semantic region
      // construction. They do not move previously mounted nodes, create
      // handlers, or introduce new scroll owners. No group identity is
      // inferred from DOM order, labels, or application names.
      const template=workspacePlan?.semanticTemplate?.arguments;
      const grouped=template?.grouping==="inferred" && template.region===r.id;
      const groupTargets=new Map();
      if(grouped){
        for(const [name,ids] of Object.entries(template.groups)){
          const section=elt("section",{"data-mcel-semantic-template-group":name,
            role:"group","aria-label":fmt(name)});
          el.append(section);
          for(const memberId of ids)groupTargets.set(memberId,section);
        }
      }
      let actionFlow=null;
      for (const m of r.members || []) {
        const node=c.nodes.get(ref(m));
        // A derived inspector field retains its own semantic caption; no
        // application-specific labels or hardcoded data fields are needed.
        // Put the caption inside its conditioned node, not beside it. A
        // target-specific inspector row and its caption share visibility.
        const inspectorLabel=c.generated && r.recipe==="inspection" && node?.label
          ? elt("h3",{"data-mcel-inspector-label":""},node.label) : null;
        const intent=c.intents.get(ref(node?.intent));
        const plainReadAction=c.generated && r.recipe==="controls" &&
          node?.nodeKind==="control" && intent?.operationKind==="capability" &&
          intent.risk==="external-read" && !intent.viewTransition &&
          !(intent.input||[]).length && !(intent.requestArguments||[]).length;
        if(plainReadAction) {
          if(!actionFlow) {
            actionFlow=elt("div",{"data-mcel-action-flow":"",role:"group","aria-label":"Available actions"});
            el.append(actionFlow);
          }
          actionFlow.append(renderNode(node));
        } else {
          actionFlow=null;
          const rendered=renderNode(node);
          if(inspectorLabel)rendered.prepend(inspectorLabel);
          (grouped ? groupTargets.get(node.id) : el).append(rendered);
        }
      }
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
      button.addEventListener("click",()=>shadowDOMSource('monitor-toggle',()=>{
        refreshEnabled=!refreshEnabled;button.setAttribute("aria-pressed",String(refreshEnabled));
        traceOperation("monitor.auto-refresh","settled",refreshEnabled?"resumed":"paused");
        button.textContent=refreshEnabled?"Pause automatic refresh":"Resume automatic refresh";
        status.textContent=refreshEnabled?"Refresh every 5 seconds when visible":"Automatic refresh paused";
      }));
      controls.append(button,status);
      // Automatic monitoring is a navigation-side capability, not a new
      // full-width header that consumes workspace height.
      const rail=expert && root.querySelector('[data-mcel-zone="navigator"]');
      if(rail)rail.append(controls);
      else root.prepend(controls);
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
          const result=await execute(intent.id,inputs,"monitor-poll");
          traceOperation("monitor.auto-refresh","settled",result.disposition);
          let pollRevision=null;
          try{pollRevision=global.MCELReactivityShadowOracle?.revisionForReceipt?.(root,result.sequence)||null;}catch(_){}
          shadowDOMSource('monitor-settlement-status',()=>{
            status.textContent="Last refresh: "+result.disposition;
          },pollRevision);
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
    if(workspacePlan) {
      const regions=new Map([...regionHosts].map(([region,el])=>[region.id,el]));
      plannedLayout=workspacePlan.documentWorkbench
        ? global.McelSemanticDocumentWorkbench.mount(tree,workspacePlan,regions)
        : global.McelSemanticLayoutInference.mountWorkspacePlan(tree,workspacePlan,regions);
    }
    else if (expert) {
      // Assemble scroll owners from semantic roles, not app names or CSS
      // fractions. Ordinary regions remain the same live DOM nodes so their
      // actions, focus, state, and result bindings retain their authority.
      const children=[...tree.children];
      const navigation=elt("div",{"data-mcel-zone":"navigator", "data-mcel-natural-scroller":"navigation", role:"navigation", "aria-label":"Application navigation"});
      const workspace=elt("div",{"data-mcel-zone":"workspace", "data-mcel-natural-scroller":"workspace", role:"main", "aria-label":"Application workspace"});
      const hasInspector=children.some(child=>child.getAttribute("data-mcel-purpose")==="inspection");
      const hasEvidence=children.some(child=>child.getAttribute("data-mcel-purpose")==="status");
      const inspector=hasInspector||hasEvidence ? elt("aside",{"data-mcel-zone":"tools",
        "data-mcel-zone-kind":"inspector","data-mcel-placement":"dock-end",
        "data-mcel-natural-scroller":"inspection", "aria-label":"Application details and activity"}) : null;
      // Explicit navigation semantics win over the legacy generic-control
      // fallback. A refresh or query button is not a navigation destination.
      const hasAuthoredNavigation=children.some(child=>
        ["navigation","mode-control"].includes(child.getAttribute("data-mcel-purpose")));
      // Primary tasks precede secondary configuration/read controls. A
      // primary result wins when present (Calculator). Sorting uses semantic
      // purpose/priority, not app identities or a hand-authored coordinate.
      const choiceRegions=new Set([...c.regions.values()].filter(r=>
        r.recipe==="collection" && r.members?.some(m=>
          c.nodes.get(ref(m))?.selectionPresentation==="radio")).map(r=>r.id));
      const focusRank=child=>{
        if(choiceRegions.has(child.getAttribute("data-mcel-region")))return 5;
        const rank=child.getAttribute("data-mcel-priority")!=="primary"?0:
          child.getAttribute("data-mcel-purpose")==="results"?3:
          child.getAttribute("data-mcel-purpose")==="task"?2:1;
        // A choice collection is the primary input. Dependent configuration
        // follows the choices rather than appearing above them.
        if(choiceRegions.size && c.regions.get(child.getAttribute("data-mcel-region"))?.recipe==="operations")return 4;
        return rank;
      };
      const orderedChildren=[...children].sort((a,b)=>focusRank(b)-focusRank(a));
      for (const child of orderedChildren) {
        const purpose=child.getAttribute("data-mcel-purpose")||"";
        if (["navigation","mode-control"].includes(purpose) ||
            (!hasAuthoredNavigation && purpose==="query-controls"))navigation.append(child);
        else if(inspector && ["inspection","status"].includes(purpose))inspector.append(child);
        else workspace.append(child);
      }
      // Reify validated, opt-in semantic width groups using the original
      // live region elements. No other regions or application state move.
      const widthGroups=new Map();
      for(const child of [...workspace.children]) {
        const key=child.getAttribute("data-mcel-width-group");
        if(!key)continue;
        let unit=widthGroups.get(key);
        if(!unit) {
          unit=elt("div",{"data-mcel-working-unit":key,
            "data-mcel-sizing":child.getAttribute("data-mcel-sizing")||"automatic"});
          workspace.insertBefore(unit,child);
          widthGroups.set(key,unit);
        }
        unit.append(child);
      }
      if(!hasAuthoredNavigation)navigation.setAttribute("data-mcel-navigation-legacy-fallback","");
      // Even an application with no authored navigation needs a stable
      // identity-bearing rail. Empty navigation is not an excuse to create a
      // separate, full-width application header.
      tree.setAttribute("data-mcel-natural-layout", "");
      tree.replaceChildren(navigation,workspace,...(inspector?[inspector]:[]));
      naturalTree=tree;naturalWorkspace=workspace;naturalInspector=inspector;
    }
    if(expert) {
      const rail=tree.querySelector('[data-mcel-zone="navigator"]');
      assert(rail,"application-navigation-missing");
      const title=ir.application?.title;
      assert(typeof title==="string" && title.trim().length>0,"application-title-missing");
      const chrome=elt("div",{"data-mcel-navigation-chrome":""});
      // Application identity belongs to the application's semantic rail in
      // every mount context. A launcher/iframe is a navigation boundary, not
      // permission for the shell to replace or suppress application chrome.
      chrome.append(elt("h1",{"data-mcel-application-title":""},title.trim()));
      if(typeof options.navigationContextLabel==="string" && options.navigationContextLabel.trim())
        chrome.append(elt("small",{"data-mcel-application-context":""},options.navigationContextLabel.trim()));
      if(options.navigationChrome!==undefined) {
        const template=options.navigationChrome;
        assert(template?.tagName==="TEMPLATE" && template.ownerDocument===root.ownerDocument,
          "navigation-chrome-must-be-same-document-template");
        chrome.append(template.content.cloneNode(true));
      }
      if(chrome.childNodes.length)rail.prepend(chrome);
    }
    root.replaceChildren(tree);
    for(const {host,node} of pendingChatMounts){
      const controller=port.mountEmbeddedChat(host,node,read);
      assert(controller,"embedded-chat-mount-failed");
      chatControllers.push(controller);
    }
    root.setAttribute("data-mcel-semantic-operational",ir.application.appId);
    // Semantic Color Law: the same canonical source→meaning→Theme binding
    // is applied to every operational mount, regardless of application ID.
    if(colorReady && colorPlan?.declarations?.length) {
      const colorInput = {root,ir,law:global.McelLabStyleLaw,
        theme:activeSelection?.profiles.theme || options.colorTheme || "default",palette:options.colorPalette||null,
        operationForIntent:id=>colorOperations.has(id)?"pending":colorOutcomes.get(id)||"idle",
        inference:true,composition:options.composition||null};
      colorPresentation = usingController
        ? colorLaw.mountOperationalColor(colorInput) : colorLaw.mount(colorInput);
      if(colorPresentation?.status!=="applied")
        global.__mcelPresentationDiagnostic = {status:"refused",property:"theme",
          reason:"semantic-color-"+(colorPresentation?.status||"unavailable")};
    }
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
      const placeNaturalInspector=()=>{
        if(!naturalInspector || !naturalWorkspace || !naturalTree)return;
        const width=layoutHost.getBoundingClientRect().width || global.innerWidth || 320;
        // At constrained widths an inspector below a 200px-high workarea
        // creates two independently scrolling fragments of the same task.
        // Keep the original live nodes and put all non-navigation content in
        // the one workspace scroll owner. Restore the dock at wide widths.
        const host=width<1160?naturalWorkspace:naturalTree;
        if(naturalInspector.parentElement!==host)host.append(naturalInspector);
        naturalInspector.setAttribute("data-mcel-placement",width>=1160?"dock-end":
          width>=680?"dock-bottom":"disclosure-after-anchor");
      };
      let layoutScheduled=false;
      const updateGeometry=()=>{
        if(disposed)return;
        // A viewport-owned natural workspace replaces the old per-card grid.
        // Do not apply an inferred grid to the root (its regions are now
        // composed under navigator/workspace scroll owners).
        if (layoutHost.hasAttribute("data-mcel-natural-layout")) {
          placeNaturalInspector();
          engine.reflowAdaptiveControlGrids?.(root);
          return;
        }
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
      const scheduleGeometry=(source='resize-observer')=>{
        if(layoutScheduled||disposed)return;
        layoutScheduled=true;
        let scheduling=null;
        try{scheduling=global.MCELReactivityShadowOracle?.captureSchedule?.(root,source)||null;}catch(_){}
        global.requestAnimationFrame?.(()=>shadowDOMSource('generated-layout-frame',()=>{
          layoutScheduled=false;updateGeometry();
        },null,scheduling));
      };
      scheduleGeneratedLayout=()=>scheduleGeometry('semantic-notify');
      if(typeof global.ResizeObserver==="function"){
        generatedResizeObserver=new global.ResizeObserver(()=>scheduleGeometry('resize-observer'));
        generatedResizeObserver.observe(layoutHost);
      } else if(typeof global.addEventListener==="function") {
        generatedResizeListener=()=>scheduleGeometry('window-resize');
        global.addEventListener("resize",generatedResizeListener);
      }
    }
    // The presentation controller is part of the operational MCEL runtime.
    // Apply only after semantic DOM and layout have mounted. A presentation
    // refusal is diagnostic, never an operational application exception.
    if (activeSelection && usingController) {
      try {
        global.__mcelPresentationReport = controller.applyOperationalPresentation({
          root, ir, composition:options.composition||null,
          appId: activeSelection.appId, profiles: activeSelection.profiles,
          readState:id=>clone(read(id))
        });
      } catch (error) {
        global.__mcelPresentationReport = {status:"refused", reason:String(error)};
      }
    }
    installMonitorRefresh();
    // The incumbent automatically discovers and lists its preferred root.
    // Equivalent read-only startup is safe only with one unambiguous,
    // source-declared navigation chain; all requests pass the same port gates.
    if(navigationRoots.length===1)void execute(navigationRoots[0].loader.id);
    return Object.freeze({execute,setNotebookAuthorization:changeNotebookAuthorization,activateNotebooks,readState:id=>clone(read(id)),receipts:()=>receipts.map(x=>({...x})),
      getState:()=>Object.fromEntries([...state.entries()].map(([k,v])=>[k,clone(v)])),
      signalCandidateReport:signalSchedulingReport,
      setSignalSchedulingMode,
      colorPresentation:()=> (usingController
        ? colorLaw.operationalColorReport(root) : colorPresentation?.report?.()) ||
        {schema:"mcel.semantic-color-presentation.v1",status:"legacy-unmigrated",authored:0},
      setColorTheme:(theme,palette)=> (usingController
        ? colorLaw.setOperationalColorTheme(root,theme,palette)
        : colorPresentation?.setTheme?.(theme,palette)) || {status:"legacy-unmigrated"},
      unmount:()=>{disposed=true;
        try {global.MCELReactivityShadowOracle?.release?.(root);}catch (_) {}
        signalCandidate?.release();
        for(const session of operationNotebooks.values())session.semanticUpdater?.dispose();
        if(usingController)
          colorLaw.releaseOperationalColor(root);
        else colorPresentation?.unmount();
        global.McelUnifiedPresentationController?.release(root);
        if(refreshTimer!==null)global.clearInterval?.(refreshTimer);
        if(initialRefreshTimer!==null)global.clearTimeout?.(initialRefreshTimer);
        plannedLayout?.unmount();
        generatedResizeObserver?.disconnect();
        if(generatedResizeListener)global.removeEventListener?.("resize",generatedResizeListener);
        for(const listener of navigationHistoryListeners)
          global.removeEventListener?.("popstate",listener);
        if(inferredLayout)global.McelSemanticLayoutInference?.clearWorkspaceLayout?.(root,inferredLayout);
        current.clear();targetRuns.clear();
        port.closeDocument?.();
        for(const controller of chatControllers)controller.destroy?.();
        if(documentHosts.size) global.MainComputerMonacoAdapter?.dispose?.("semantic-unmount");
        root.replaceChildren();root.removeAttribute("data-mcel-semantic-operational");}});
  }
  global.McelSemanticOperationalRuntime = Object.freeze({mount,checkSchema,contract,createSignalCandidate});
})(typeof globalThis !== "undefined" ? globalThis : this);
