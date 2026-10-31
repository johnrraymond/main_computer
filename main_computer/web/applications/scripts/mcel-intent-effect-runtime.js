/* MCEL shared source-bound, external-read intent/effect/receipt executor v1.
 * No networking, host globals, or application-specific selectors. Execution is
 * possible only through explicitly injected capability adapters and an observed
 * monotonically advancing application data revision.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.McelIntentEffectRuntime = api;
})(typeof globalThis !== "undefined" ? globalThis : null, function () {
  "use strict";
  const refuse = code => ({ok:false, code, effects:[]});
  function check(ir) {
    if (!ir || !Array.isArray(ir.intents) || !Array.isArray(ir.capabilities) ||
        !Array.isArray(ir.effects)) throw Error("MCEL_EFFECT_INVALID_IR");
    const controls = (ir.surfaces || []).flatMap(surface =>
      (surface.semanticSurface?.controls || []).filter(control => control.role === "capability-action"));
    if (controls.length !== ir.effects.length || controls.length !== ir.capabilities.length ||
        new Set(controls.map(c=>c.id)).size !== controls.length ||
        new Set(controls.map(c=>c.intent)).size !== controls.length) throw Error("MCEL_EFFECT_CONTROL_CARDINALITY");
    const actions = [];
    for (const control of controls) {
      const intent = ir.intents.find(i => i.sourceName === control.intent && i.operationKind === "capability");
      if (!intent || intent.lane !== "external-read" || intent.risk !== "external-read" ||
          control.risk !== "external-read" || typeof control.label !== "string" || !control.label ||
          (intent.input || []).length || (intent.writes || []).length || (intent.viewWrites || []).length ||
          (intent.effectRefs || []).length !== 1 ||
          !["completed","failed","refused"].every(o => (intent.outcomes || []).includes(o)))
        throw Error("MCEL_EFFECT_INTENT_AUTHORITY");
      const effect = ir.effects.find(e => e.id === intent.effectRefs[0].ref);
      const capability = effect && ir.capabilities.find(c => c.id === effect.target?.ref);
      if (!effect || effect.owner?.ref !== intent.id || effect.authority?.ref !== capability?.id ||
          effect.effectKind !== "capability-request" || effect.risk !== "external-read" ||
          effect.cardinality?.maximum !== 1 || !capability || capability.risk !== "external-read" ||
          !Array.isArray(capability.operations) || capability.operations.length !== 1 ||
          capability.operations[0].name !== intent.runtimeMethod ||
          !["operation-receipt","capability-response","visible-outcome"].every(
            v => (effect.requiredEvidence || []).includes(v))) throw Error("MCEL_EFFECT_INVALID_GRAPH");
      actions.push(Object.freeze({id:control.id, label:control.label, intentId:intent.id,
        capabilityId:capability.id, method:capability.operations[0].runtimeMethod,
        effectId:effect.id}));
    }
    if (ir.intents.some(i=>i.operationKind==="capability" && !actions.some(a=>a.intentId===i.id)))
      throw Error("MCEL_EFFECT_UNBOUND_INTENT");
    return Object.freeze(actions);
  }
  function mount(ir, {adapters = {}, observe} = {}) {
    const actions = check(ir);
    let disposed = false, pending = null, sequence = 0;
    const listeners = new Set(), receipts = [];
    let state = {phase:"idle", actionId:null, receipt:null};
    function notify() {
      const value = status();
      for (const callback of listeners) { try {callback(value);} catch (_) {/* observer failure isn't effect failure */} }
    }
    function status() {return {phase:state.phase, actionId:state.actionId,
      receipt:state.receipt, pending:!!pending, receipts:receipts.slice(-16)};}
    function adapterFor(action) {return adapters?.[action.capabilityId]?.[action.method];}
    function available(id) {const action=actions.find(a=>a.id===id);
      return !disposed && !!action && typeof observe === "function" && typeof adapterFor(action)==="function";}
    async function dispatch(id) {
      const action = actions.find(a=>a.id===id);
      if (!action) return refuse("undeclared-intent");
      if (disposed) return refuse("disposed");
      if (pending) return refuse("already-pending");
      if (!available(id)) {const result=refuse("capability-unavailable");
        state={phase:"refused",actionId:id,receipt:result};notify();return result;}
      let before;
      try {before=observe();} catch (_) {return refuse("observation-unavailable");}
      if (!Number.isSafeInteger(before) || before < 0) return refuse("invalid-observation");
      const token = ++sequence;
      pending = token;
      state={phase:"pending",actionId:id,receipt:null};notify();
      let result, phase = "failed";
      try {
        // Adapter must await the actual host capability and return its response;
        // an inferred success from merely dispatching is never accepted.
        const response = await adapterFor(action)();
        const after = observe();
        if (response?.ok === true && Number.isSafeInteger(after) && after > before &&
            response.delivery?.ok === true && ["available","partial","empty"].includes(response.delivery.status) &&
            Number.isSafeInteger(response.delivery.version) &&
            response.delivery.version >= after) {
          phase = "completed";
          result = {ok:true,code:"completed",intentId:action.intentId,effectId:action.effectId,
            capabilityId:action.capabilityId,beforeVersion:before,afterVersion:after,
            deliveryStatus:response.delivery.status, effects:[{effectId:action.effectId,disposition:"completed"}]};
        } else {
          phase = response?.ok === false && response.code === "refused" ? "refused" : "failed";
          result = {ok:false,code:response?.code === "refused" ? "refused" :
            (!Number.isSafeInteger(after) || after<=before ? "snapshot-not-advanced" :
             response?.code || "unverified-capability-response"),
            intentId:action.intentId,beforeVersion:before,afterVersion:after,
            effects:[{effectId:action.effectId,disposition:phase==="refused"?"refused-before-attempt":"failed"}]};
        }
      } catch (error) {
        result = {ok:false,code:"capability-failed",intentId:action.intentId,
          reason:String(error?.message || error).slice(0,200),
          effects:[{effectId:action.effectId,disposition:"failed"}]};
      }
      // Never publish stale results after unmount or a new owner generation.
      if (disposed || pending !== token) return refuse("cancelled-after-unmount");
      pending=null;
      result = Object.freeze({...result, attempt:token});
      receipts.push(result);if(receipts.length>32)receipts.shift();
      state={phase,actionId:id,receipt:result};notify();
      return result;
    }
    return Object.freeze({actions,dispatch,status,available,
      subscribe(callback) {if(typeof callback!=="function")throw Error("MCEL_EFFECT_OBSERVER_INVALID");
        listeners.add(callback); callback(status());return ()=>listeners.delete(callback);},
      destroy() {disposed=true; pending=null; sequence++;listeners.clear();}
    });
  }
  return Object.freeze({version:"mcel-intent-effect-runtime-v1",check,mount});
});
