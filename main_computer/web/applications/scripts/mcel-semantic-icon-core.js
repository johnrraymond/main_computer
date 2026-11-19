/* MCEL semantic icon core v1.
 * No application inventory, label matching, CSS-class classification, or
 * test-oracle lookup. Canonical IR + canonical icon registry are the only
 * meaning authorities. Theme/Form/Chrome own presentation, not icon identity.
 */
(function(global, factory) {
  "use strict";
  const api=factory();
  if(typeof module==="object" && module.exports)module.exports=api;
  if(global && !global.McelSemanticIconCore)global.McelSemanticIconCore=api;
})(typeof globalThis!=="undefined"?globalThis:null,function(){
  "use strict";
  const schema="mcel.semantic-icon-core.v1";
  const mounts=new WeakMap();
  const records=value=>Array.isArray(value)?value:[];
  const ref=value=>typeof value==="string"?value:value?.ref;
  const words=value=>typeof value==="string" ? value.replace(/([a-z0-9])([A-Z])/g,"$1-$2").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean) : [];
  // General vocabulary conjugations, not application/element assignments.
  // Omit verbs without a defensible representation in the canonical registry.
  const semanticVerb=Object.freeze({
    start:"run",launch:"run",execute:"run",terminate:"stop",shutdown:"stop",
    preview:"inspect",examine:"inspect",reload:"refresh",
    remove:"remove",erase:"delete",create:"add"
  });
  // Only synonyms that have a registry entry are ever eligible; the icon
  // registry, not this table, decides the actual set of supported drawings.
  function verbToken(value) {
    const segments=words(value);
    if(!segments.length)return null;
    // First token is the action in the canonical operation/intent grammar.
    // A noun appearing later must never accidentally become a command icon.
    const first=segments[0];
    return semanticVerb[first]||first;
  }
  function resolveIntent(intent, icons) {
    if(!intent || !icons?.has || !["capability","interaction"].includes(intent.operationKind))
      return {status:"unresolved",reason:"canonical-intent-unavailable"};
    if(intent.viewTransition || intent.collectionSelection)
      return {status:"unresolved",reason:"navigation-or-selection-requires-destination-semantics"};
    // A validated typed operation is authoritative. Never fall back to raw
    // operation names if its semantic kind has no icon in the current library.
    if(Object.prototype.hasOwnProperty.call(intent,"typedOperation")) {
      const typed=intent.typedOperation;
      if(!typed || typed.schema!=="mcel.typed-operation.v1" ||
         typeof typed.kind!=="string" || typeof typed.target!=="string")
        return {status:"unresolved",reason:"invalid-typed-operation"};
      // These are deliberate typed-semantic-to-glyph translations, not text
      // heuristics. Other kinds use their canonical action family identity.
      const presentations={start:"run",launch:"run",terminate:"stop",shutdown:"stop",create:"add"};
      const action=presentations[typed.kind]||typed.kind;
      // "Open" is a typed action whose resource target determines its
      // presentation. It is not a fallback to executable operation names.
      const specialized=typed.kind==="open" ? ({
        folder:"resource.folder-open",application:"navigation.external"
      })[typed.target] : null;
      const id=specialized || "action."+action;
      return icons.has(id)
        ? {status:"resolved",iconId:id,source:"typed-operation",target:typed.target}
        : {status:"unresolved",reason:"typed-action-icon-unavailable"};
    }
    const candidates=[];
    // The declared capability operation has priority over source-name grammar.
    // A generic transport verb (e.g. 'list') cannot override specific intent.
    const operation=verbToken(intent.operation);
    if(operation && icons.has("action."+operation))candidates.push({id:"action."+operation,source:"canonical-operation"});
    const source=verbToken(intent.sourceName);
    if(source && icons.has("action."+source))candidates.push({id:"action."+source,source:"canonical-intent"});
    // If an operation is valid but the source signals a *different* action,
    // require explicit semantic disambiguation instead of silently choosing.
    const ids=[...new Set(candidates.map(c=>c.id))];
    if(ids.length>1)return {status:"ambiguous",reason:"conflicting-semantic-verbs",candidates:ids};
    if(ids.length===1)return {status:"resolved",iconId:ids[0],source:candidates[0].source};
    return {status:"unresolved",reason:"icon-vocabulary-has-no-unambiguous-action"};
  }
  // Legacy presentation contracts can declare a state-dependent action label.
  // This is source-authored DSL meaning, not a search of rendered button text.
  // Only exact, unambiguous action labels are admitted; unknown states or labels
  // never inherit an action from a stale label or an unrelated transport verb.
  const legacyActionLabels=Object.freeze({
    Start:"action.run", Stop:"action.stop",
    Enable:"action.enable", Disable:"action.disable", Apply:"action.apply"
  });
  function resolve(ir, node, icons, {readState=null}={}) {
    if(ir?.schema!=="mcel.application-ir.v1" || !["control","form"].includes(node?.nodeKind))
      return {status:"unresolved",reason:"unsupported-canonical-node"};
    const id=ref(node.intent);
    const matches=records(ir.intents).filter(intent=>intent.id===id);
    if(!id || matches.length!==1)return {status:"unresolved",reason:"unique-canonical-intent-required"};
    const intent=matches[0];
    // Typed operation semantics always take precedence over legacy labels.
    if(Object.prototype.hasOwnProperty.call(intent,"typedOperation"))
      return resolveIntent(intent,icons);
    if(node.nodeKind==="control" && node.labelByState){
      const descriptor=node.labelByState, stateId=ref(descriptor.state);
      if(!stateId || records(ir.states).filter(state=>state.id===stateId && state.authority==="derived").length!==1 ||
         typeof descriptor.path!=="string" || !descriptor.path || !descriptor.labels ||
         typeof readState!=="function")
        return {status:"unresolved",reason:"authoritative-state-required"};
      let record;
      try{record=readState(stateId);}catch(error){return {status:"unresolved",reason:"state-observation-unavailable"};}
      const value=record && typeof record==="object" ? record[descriptor.path] : undefined;
      if(typeof value!=="string" || !Object.prototype.hasOwnProperty.call(descriptor.labels,value))
        return {status:"unresolved",reason:"state-action-not-declared"};
      const iconId=legacyActionLabels[descriptor.labels[value]];
      return iconId && icons.has(iconId)
        ? {status:"resolved",iconId,source:"canonical-state-action",stateId}
        : {status:"unresolved",reason:"state-action-icon-unavailable"};
    }
    // An authored Apply form is a form submission, not a name token in the
    // executable operation. Other forms retain their legacy inference.
    if(node.nodeKind==="form" && node.submitLabel==="Apply" && icons.has("action.apply"))
      return {status:"resolved",iconId:"action.apply",source:"canonical-form-submit"};
    return resolveIntent(intent,icons);
  }
  function targetElement(binding, node) {
    const holder=binding?.element;
    if(!holder || holder.getAttribute?.("data-mcel-node")!==node.id)return null;
    if(node.nodeKind==="form")return holder.querySelector('form button[type="submit"]');
    if(node.nodeKind==="control") {
      // A navigation destination is not an ordinary capability control.
      // Do not decorate a hidden select or multiple generated tabs.
      const direct=holder.querySelector('button[data-intent]');
      if(direct && holder.querySelectorAll('button[data-intent]').length===1)return direct;
    }
    return null;
  }
  // Renderer-authored semantic controls (e.g. a native form reset or
  // collection-navigation control) are not application capability requests.
  // They have exact built-in meanings rather than operation-name heuristics.
  const generatedActions=Object.freeze({
    "reset-inputs":"action.reset",
    "document-save":"action.save",
    "document-discard":"action.reset",
    "document-close":"navigation.close",
    "parent-directory":"navigation.up",
    "open-location":"resource.folder-open",
    "refresh-location":"action.refresh"
  });
  function apply({root,ir,bridge,profiles={},icons=null,readState=null}={}) {
    const unavailable=reason=>({schema,status:"unavailable",reason,resolved:0,unresolved:0,ambiguous:0});
    if(!root?.querySelectorAll || !root.isConnected || ir?.schema!=="mcel.application-ir.v1")
      return unavailable("mounted-canonical-root-required");
    let adapter=icons;
    try{if(!adapter)adapter=globalThis.MCELIcon?.fromDocument?.();}catch(e){return unavailable("canonical-icon-registry-unavailable:"+String(e));}
    if(!adapter?.has || !adapter?.element)return unavailable("canonical-icon-adapter-unavailable");
    if(!bridge?.observeRuntimePresentationTargets)return unavailable("semantic-binding-bridge-unavailable");
    const observed=bridge.observeRuntimePresentationTargets({root,ir});
    if(observed.status!=="source-bound")return unavailable("exact-runtime-bindings-required:"+observed.reason);
    const knownNodes=new Map(records(ir.surfaces?.[0]?.nodes).map(node=>[node.id,node]));
    const seen=new Set(), report={schema,status:"applied",resolved:0,unresolved:0,ambiguous:0,skipped:0,updated:0};
    // Selection is driven by canonical node ID, not app names, text, class, or DOM order.
    for(const binding of observed.targets) {
      if(binding.kind!=="control")continue;
      const node=knownNodes.get(binding.nodeId);
      if(!node || !["control","form"].includes(node.nodeKind))continue;
      const resolution=resolve(ir,node,adapter,{readState});
      if(resolution.status!=="resolved"){
        report[resolution.status==="ambiguous"?"ambiguous":"unresolved"]++;
        continue;
      }
      const target=targetElement(binding,node);
      if(!target ||
         target.querySelector('svg:not([data-mcel-icon-core])') ||
         target.querySelector('[data-mcel-icon-id]:not([data-mcel-icon-core])')){
        report.skipped++;continue;
      }
      const iconId=resolution.iconId;
      const existing=target.querySelector(':scope > [data-mcel-icon-core="slot"]');
      if(existing?.getAttribute("data-mcel-icon-identity")===iconId){seen.add(existing);report.resolved++;continue;}
      // Only replace MCEL-managed icons. Native app-owned content and listeners
      // are never reconstructed, even during profile switching.
      const slot=existing||target.ownerDocument.createElement('span');
      slot.setAttribute('data-mcel-icon-core','slot');
      slot.setAttribute('data-mcel-icon-identity',iconId);
      slot.className='mcel-icon-slot mcel-semantic-icon-slot';
      slot.setAttribute('aria-hidden','true');
      const svg=adapter.element(iconId,{size:16},target.ownerDocument);
      svg.setAttribute('data-mcel-icon-core','glyph');
      slot.replaceChildren(svg);
      if(!existing)target.insertBefore(slot,target.firstChild);
      seen.add(slot);report.resolved++;report.updated++;
    }
    // The shared renderer identifies its *own* generated control kinds. These
    // are core DSL widget semantics, never application names or button labels.
    // Keep their count separate from canonical application intent coverage.
    report.generatedResolved=0;
    for(const target of root.querySelectorAll('button[data-mcel-generated-action]')) {
      const kind=target.getAttribute('data-mcel-generated-action');
      if(!Object.prototype.hasOwnProperty.call(generatedActions,kind))continue;
      const iconId=generatedActions[kind];
      if(!adapter.has(iconId))continue;
      if(target.querySelector('svg:not([data-mcel-icon-core])') ||
         target.querySelector('[data-mcel-icon-id]:not([data-mcel-icon-core])'))continue;
      const existing=target.querySelector(':scope > [data-mcel-icon-core="slot"]');
      if(existing?.getAttribute('data-mcel-icon-identity')===iconId){
        seen.add(existing);report.generatedResolved++;continue;
      }
      const slot=existing||target.ownerDocument.createElement('span');
      slot.setAttribute('data-mcel-icon-core','slot');
      slot.setAttribute('data-mcel-icon-identity',iconId);
      slot.className='mcel-icon-slot mcel-semantic-icon-slot';
      slot.setAttribute('aria-hidden','true');
      const svg=adapter.element(iconId,{size:16},target.ownerDocument);
      svg.setAttribute('data-mcel-icon-core','glyph');
      slot.replaceChildren(svg);
      if(!existing)target.insertBefore(slot,target.firstChild);
      seen.add(slot);report.generatedResolved++;report.updated++;
    }
    // Remove only slots installed by this core if their semantic binding has
    // disappeared or become ambiguous. Never touch authored/native icons.
    for(const slot of root.querySelectorAll('[data-mcel-icon-core="slot"]')){
      if(!seen.has(slot)){slot.remove();report.updated++;}
    }
    root.setAttribute('data-mcel-icon-core','active');
    // Theme/Form/Chrome are independent profile authorities; profile ids are
    // exposed for their CSS/semantic treatment without a fourth icon profile.
    for(const name of ['theme','form','chrome']){
      if(typeof profiles[name]==='string' && /^[a-z0-9-]+$/.test(profiles[name]))
        root.setAttribute('data-mcel-icon-'+name,profiles[name]);
    }
    return Object.freeze(report);
  }
  function mount(input) {
    const {root}=input||{};
    const previous=root&&mounts.get(root);
    if(previous){previous.input=input;return previous.refresh();}
    const state={input,observer:null,busy:false,refresh(){return apply(state.input);},unmount(){
      state.observer?.disconnect();state.observer=null;
      for(const slot of root.querySelectorAll('[data-mcel-icon-core="slot"]'))slot.remove();
      root.removeAttribute('data-mcel-icon-core');
      for(const p of ['theme','form','chrome'])root.removeAttribute('data-mcel-icon-'+p);
      mounts.delete(root);
    }};
    mounts.set(root,state);
    const result=state.refresh();
    if(result.status!=='applied'){mounts.delete(root);return result;}
    // Reconcile only when the operational renderer replaces a canonical node.
    // Mutations created by the icon core itself settle after one extra no-op.
    if(typeof MutationObserver==='function'){
      state.observer=new MutationObserver(changes=>{
        if(!changes.some(change=>[...change.addedNodes,...change.removedNodes].some(node=>
          node.nodeType===1 && !node.hasAttribute?.('data-mcel-icon-core'))))return;
        if(!state.busy){state.busy=true;queueMicrotask(()=>{state.busy=false;
          if(mounts.get(root)===state)state.refresh();});}
      });
      state.observer.observe(root,{childList:true,subtree:true});
    }
    return result;
  }
  function release(root){const state=root&&mounts.get(root);if(!state)return false;state.unmount();return true;}
  function inspect(root){return root&&mounts.has(root)?mounts.get(root).refresh():null;}
  return Object.freeze({schema,resolveIntent,resolve,apply,mount,release,inspect});
});
