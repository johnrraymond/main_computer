/* MCEL unified presentation controller — system-wide operational rollout.
 *
 * All promoted semantic MCEL applications resolve a shared registry policy,
 * then use the same existing Theme, Form, and Chrome laws. Operational mount
 * authority stays separate from presentation: visual failure cannot crash it.
 * The Lab uses the same controller with explicit temporary profile choices.
 */
(function(global) {
  "use strict";
  if (global.McelUnifiedPresentationController) return;

  const schema = "mcel.unified-presentation-controller.v1";
  const owners = new WeakMap();
  const operationalColors = new WeakMap();
  const properties = Object.freeze(["theme", "form", "chrome"]);
  const laws = Object.freeze({
    theme: "McelSemanticColorPresentation",
    form: "McelSemanticFormPresentationLaw",
    chrome: "McelSemanticChromePresentationLaw"
  });
  const phases = Object.freeze({theme: "operational-mount", form: "post-mount", chrome: "post-mount"});

  function refused(reason) {
    return Object.freeze({schema, status: "refused-no-op", reason, applied: false});
  }
  function validProfiles(profiles) {
    return !!profiles && typeof profiles === "object" &&
      properties.every(key => typeof profiles[key] === "string" &&
        new RegExp("^" + key + "-[a-z0-9]+(?:-[a-z0-9]+)*$").test(profiles[key]));
  }
  function prepare({root, appId, profiles, rolloutEnabled = false, surface = "deployed"} = {}) {
    if (!root || root.nodeType !== 1 || !root.ownerDocument)
      return refused("mounted-root-required");
    if (typeof appId !== "string" || !/^[a-z][a-z0-9-]*$/.test(appId))
      return refused("canonical-app-id-required");
    if (!validProfiles(profiles)) return refused("resolved-profiles-required");
    if (surface !== "deployed" && surface !== "lab") return refused("unknown-surface");

    const prior = owners.get(root);
    if (prior && prior.appId !== appId)
      return refused("root-already-owned-by-other-application");
    if (prior && prior.surface !== surface)
      return refused("root-already-owned-by-other-surface");
    // Each selection is a read-only plan.  Re-preparing cannot re-style or
    // remount anything, and a changed registry policy simply replaces the plan.
    if (prior && prior.surface === surface && prior.rolloutEnabled === (rolloutEnabled === true) &&
        properties.every(key => prior.profiles[key] === profiles[key])) return prior;

    const frozenProfiles = Object.freeze(Object.fromEntries(properties.map(key => [key, profiles[key]])));
    const steps = Object.freeze(properties.map(property => Object.freeze({
      property, profile: frozenProfiles[property], law: laws[property], phase: phases[property],
      // The existing Color Law is now executed by the shared controller.
      // This prepared registry selection still cannot override its palette.
      stage2Action: property === "theme" ? "existing-color-delegated" : "deferred"
    })));
    const plan = Object.freeze({schema, status: "prepared-no-op", appId, surface,
      profiles: frozenProfiles, rolloutEnabled: rolloutEnabled === true,
      steps, applied: false, executionAvailable: false});
    owners.set(root, plan);
    return plan;
  }
  function inspect(root) {
    return owners.get(root) || null;
  }
  function release(root) {
    if (!root) return false;
    const hadOwner = owners.has(root);
    // The operational runtime explicitly owns its unmount timing. A second
    // release call is safe and cannot unmount another application's root.
    global.McelSemanticIconCore?.release?.(root);
    releaseOperationalColor(root);
    global.McelSemanticChromePresentationLaw?.clear?.(root);
    appliedOperational.delete(root);
    owners.delete(root);
    return hadOwner;
  }

  // One operational Color Law owner, regardless of which MCEL application
  // supplied the canonical IR. Validation remains before DOM mutation.
  function validateOperationalColor(input) {
    const law = global.McelSemanticColorPresentation;
    return law?.validate?.(input) || null;
  }
  function mountOperationalColor(input = {}) {
    const root = input.root;
    if (!root || root.nodeType !== 1) return refused("mounted-root-required");
    const previous = operationalColors.get(root);
    if (previous) return previous;
    const law = global.McelSemanticColorPresentation;
    if (!law?.mount) return refused("color-law-unavailable");
    const result = law.mount(input);
    if (result?.status === "applied") operationalColors.set(root, result);
    return result;
  }
  function releaseOperationalColor(root) {
    const current = root && operationalColors.get(root);
    if (!current) return false;
    operationalColors.delete(root);
    current.unmount?.();
    return true;
  }

  function refreshOperationalColor(root) {
    return operationalColors.get(root)?.refresh?.() || null;
  }
  function operationalColorReport(root) {
    return operationalColors.get(root)?.report?.() || null;
  }
  function setOperationalColorTheme(root, theme, palette) {
    return operationalColors.get(root)?.setTheme?.(theme, palette) ||
      {status:"legacy-unmigrated"};
  }

  // Stage 3: one canonical registry-selected execution path for ALL MCEL
  // operational applications. Native semantics and shared laws are the only
  // inputs; neither app names nor app CSS select a presentation treatment.
  const appliedOperational = new WeakMap();
  function applyOperationalPresentation({root, ir, composition=null, appId, profiles, readState=null} = {}) {
    const policy = prepare({root,appId,profiles,rolloutEnabled:true,surface:"deployed"});
    if (policy.status !== "prepared-no-op") return policy;
    const canonicalBridge = global.McelSemanticPresentationBridge;
    const bridge = canonicalBridge && Object.freeze({
      observeRuntimePresentationTargets:input => canonicalBridge.observeRuntimePresentationTargets({...input,composition})
    });
    const styleLaw = global.McelLabStyleLaw;
    const statuses = {};
    const color = operationalColorReport(root);
    statuses.theme = color?.status === "applied" ? "applied" : "refused";
    if (!bridge || !styleLaw) {
      statuses.form = "refused"; statuses.chrome = "refused";
      return {schema,status:"partial",profiles,steps:statuses,reason:"presentation-laws-unavailable"};
    }
    try {
      const result=global.McelSemanticFormPresentationLaw?.apply({
        root,ir,bridge,profile:profiles.form,bindingModel:"operational-runtime"});
      statuses.form=result?.status === "applied-in-isolated-lab-frame" ? "applied" : "refused";
      if (statuses.form !== "applied") statuses.formReason=result?.reason || result?.status;
    } catch (error) { statuses.form="refused"; statuses.formReason=String(error); }
    try {
      const result=global.McelSemanticChromePresentationLaw?.apply({
        root,ir,bridge,profile:profiles.chrome,theme:profiles.theme,styleLaw});
      statuses.chrome=["applied","unchanged"].includes(result?.status) ? "applied" : "refused";
      if(statuses.chrome !== "applied") statuses.chromeReason=result?.reason || result?.status;
    } catch (error) { statuses.chrome="refused"; statuses.chromeReason=String(error); }
    // Icons are a core semantic resource, not another Theme/Form/Chrome law.
    // Failure is isolated from the three presentation authorities and mount.
    let icons={status:"unavailable",reason:"semantic-icon-core-unavailable"};
    try {
      icons=global.McelSemanticIconCore?.mount?.({root,ir,bridge,profiles,readState}) || icons;
    } catch(error) {icons={status:"unavailable",reason:String(error)};}
    const status = properties.every(name => statuses[name] === "applied") ? "applied" : "partial";
    const report={schema,status,profiles:Object.freeze({...profiles}),steps:Object.freeze(statuses),appId,iconCore:icons};
    appliedOperational.set(root,report);
    return report;
  }
  function operationalReport(root) {return appliedOperational.get(root)||null;}
  // A region-only semantic host (e.g. the canonical notebook) uses the same
  // controller and laws. This adapter binds existing source region IDs to the
  // operational Color/Shape/Material laws without inventing new intent data.
  function applyNativePresentation({root,ir,appId,profiles} = {}) {
    if (ir?.schema !== "mcel.application-ir.v1" || !root?.ownerDocument || !root.isConnected)
      return refused("canonical-native-semantic-root-required");
    const regions=ir.surfaces?.[0]?.interfaceIntent?.regions;
    if (!Array.isArray(regions) || !regions.length || (ir.surfaces?.[0]?.nodes || []).length)
      return refused("region-only-semantic-host-required");
    const elements=[];
    for (const region of regions) {
      const hits=[...(root.getAttribute("data-mcel-region-id")===region.id?[root]:[]),
        ...[...root.querySelectorAll('[data-mcel-region-id]')].filter(element=>
          element.getAttribute('data-mcel-region-id')===region.id)];
      if(hits.length!==1)return refused("missing-source-bound-region:"+region.id);
      elements.push([hits[0],region.id]);
    }
    for(const [element,id] of elements) element.setAttribute('data-mcel-region',id);
    // Same Color Law inference and common controller used by operational IR.
    const color=mountOperationalColor({root,ir,law:global.McelLabStyleLaw,
      theme:profiles.theme,inference:true});
    if(color.status!=="applied")return {status:"partial",reason:color.reason||color.status};
    return applyOperationalPresentation({root,ir,appId,profiles});
  }


  // Lab and deployed roots share controller ownership. Lab preview is an
  // explicit request (not registry rollout); dispatch preserves the existing
  // Theme → Form → Chrome timing and each law's return contract.
  function labOwner(input) {
    const plan = prepare({root:input.root, appId:input.appId,
      profiles:input.profiles, rolloutEnabled:false, surface:"lab"});
    return plan.status === "prepared-no-op" ? null : plan;
  }
  function applyLabTheme(input = {}) {
    const refusedPlan = labOwner(input);
    if (refusedPlan) return refusedPlan;
    const law = global.McelSemanticThemePreview;
    if (!law?.apply) return refused("theme-preview-law-unavailable");
    return law.apply({root:input.root, ir:input.ir, bindingModel:input.bindingModel,
      bridge:input.bridge, styleLaw:input.styleLaw, theme:input.theme,
      tokens:input.tokens});
  }
  function applyLabForm(input = {}) {
    const refusedPlan = labOwner(input);
    if (refusedPlan) return refusedPlan;
    const law = global.McelSemanticFormPresentationLaw;
    if (!law?.apply) return refused("form-law-unavailable");
    return law.apply({root:input.root, ir:input.ir, bridge:input.bridge,
      bindingModel:input.bindingModel, profile:input.profile});
  }
  function applyLabChrome(input = {}) {
    const refusedPlan = labOwner(input);
    if (refusedPlan) return refusedPlan;
    const law = global.McelSemanticChromePresentationLaw;
    if (!law?.apply) return refused("chrome-law-unavailable");
    return law.apply({root:input.root, ir:input.ir, bridge:input.bridge,
      profile:input.profile, theme:input.theme, styleLaw:input.styleLaw});
  }

  Object.defineProperty(global, "McelUnifiedPresentationController", {
    value: Object.freeze({schema, prepare, inspect, release,
      validateOperationalColor, mountOperationalColor, refreshOperationalColor,
      operationalColorReport, setOperationalColorTheme, releaseOperationalColor,
      applyLabTheme, applyLabForm, applyLabChrome,
      applyOperationalPresentation, applyNativePresentation, operationalReport}),
    writable: false, configurable: false
  });
})(window);
