/* Executable acid witnesses at existing public MCEL registry and toolkit boundaries.
 * This file supplies assertions ONLY. It is not a renderer or validator substitute.
 * In particular, metadata/recipe existence is never evidence that a widget works.
 */
(function (global) {
  "use strict";
  const CORE_PARENTS = [
    "element.core.app", "element.core.region", "element.core.panel",
    "element.core.toolbar", "element.core.workflow", "element.core.collection"
  ];
  const TOOLKIT_IDS = [
    "element.toolkit.foundation-token", "element.toolkit.collection-view",
    "element.toolkit.contract-pattern", "element.toolkit.view-resolver"
  ];
  function witness(id, scope, observations, details) {
    const failed = Object.keys(observations).filter(key => observations[key] !== true);
    return {id, scope, status: failed.length ? "FAIL" : "PASS", observations,
      failedObservations: failed, fullElementCertification: false, details};
  }
  function run() {
    const reg = global.McelElementRegistry;
    const tk = global.McelToolkitCore;
    if (!reg || !tk) throw new Error("Actual MCEL registry/toolkit required");
    const rows = [];
    for (const id of CORE_PARENTS) {
      const definition = reg.get(id);
      if (!definition || !(definition.allowedChildren || []).length) {
        rows.push(witness(id, "registry-serializer-child-policy", {definitionAvailable: false},
          "Required explicit allowedChildren contract missing"));
        continue;
      }
      const allowed = definition.allowedChildren[0];
      const rejected = reg.all().find(x => x.id !== id && !definition.allowedChildren.includes(x.id));
      const allowedChild = {id:"accepted", elementId:allowed};
      const forbiddenChild = {id:"forbidden", elementId:rejected.id};
      let valid, invalid, error = null;
      try {
        valid = reg.serializeElement(id, {id:"acid-valid", children:[allowedChild]});
        invalid = reg.serializeElement(id, {id:"acid-invalid", children:[forbiddenChild]});
      } catch(e) { error = String(e); }
      const acceptedValid = valid?.children?.some(x=>x.elementId===allowed) === true;
      const refusedInvalid = error !== null || !(invalid?.children || [])
        .some(x => x.elementId === rejected.id);
      rows.push(witness(id, "registry-serializer-child-policy", {
        legalChildRetained:acceptedValid, forbiddenChildRefused:refusedInvalid
      }, {legalChild:allowed, forbiddenChild:rejected.id,
        invalidChildPreserved:invalid?.children?.some(x=>x.elementId===rejected.id) === true,
        exception:error,
        boundary:"McelElementRegistry.serializeElement only; compiler enforcement not asserted"}));
    }
    const token = tk.getPrimitive("foundation.focus-ring");
    const before = tk.getPrimitive("foundation.focus-ring");
    if (token) token.label="tampered";
    const later = tk.getPrimitive("foundation.focus-ring");
    const search = tk.listPrimitives({elementId:"element.toolkit.foundation-token"});
    rows.push(witness("element.toolkit.foundation-token", "toolkit-primitive-catalog", {
      primitiveLinked:before?.elementId==="element.toolkit.foundation-token",
      filterConsistent:search.length>0 && search.every(x=>x.elementId==="element.toolkit.foundation-token"),
      readsIsolated:later?.label===before?.label
    }, {primitiveId:"foundation.focus-ring", primitiveCount:search.length,
      limitation:"Catalog behavior only; no token CSS/focus geometry is certified"}));
    const recipe = tk.getViewRecipe("plain-tree");
    const original = tk.getViewRecipe("plain-tree");
    if(recipe) recipe.label="tampered";
    rows.push(witness("element.toolkit.collection-view", "toolkit-view-recipe-catalog", {
      recipeExists:!!original && Array.isArray(original.capabilities),
      hasPrimitiveRequirements:Array.isArray(original?.primitiveIds) && original.primitiveIds.length>0,
      readsIsolated:tk.getViewRecipe("plain-tree")?.label===original?.label
    }, {recipeId:"plain-tree", limitation:"Recipe catalog only; list/table widgets not exercised"}));
    const pattern = tk.CONTRACT_PATTERNS.fileBasket;
    const explicitlyRejected = tk.evaluateView(pattern, tk.getViewRecipe("plain-tree"));
    const fullView={id:"synthetic-eligible",label:"Synthetic eligible",
      capabilities:[...(pattern?.requires || [])],primitiveIds:[...(pattern?.requiredPrimitives || [])]};
    const eligible = tk.evaluateView(pattern, fullView);
    rows.push(witness("element.toolkit.contract-pattern", "toolkit-pattern-evaluation", {
      patternRequiredFields:Array.isArray(pattern?.fields) && pattern.fields.length>0,
      patternRequirements:Array.isArray(pattern?.requires) && pattern.requires.length>0,
      explicitlyRejected:explicitlyRejected.eligible===false,
      matchingContractEligible:eligible.eligible===true && eligible.missingPrimitives.length===0
    }, {contractId:pattern?.id, rejectedView:"plain-tree",
      limitation:"Pattern evaluator only; no view composition or effects certified"}));
    const requiredPrimitive="acid.required.primitive";
    const requiredCapability="acid.required.capability";
    const contract={id:"acid-contract",label:"Acid contract",
      requires:[requiredCapability],requiredPrimitives:[requiredPrimitive],mustReject:["blocked"]};
    const full={id:"complete",label:"Complete",capabilities:[requiredCapability],
      primitiveIds:[requiredPrimitive]};
    const missingCap={...full,capabilities:[]};
    const missingPrimitive={...full,primitiveIds:[]};
    const blocked={...full,id:"blocked"};
    const completeResult=tk.evaluateView(contract,full);
    const noCapabilityResult=tk.evaluateView(contract,missingCap);
    const noPrimitiveResult=tk.evaluateView(contract,missingPrimitive);
    const blockedResult=tk.evaluateView(contract,blocked);
    const ranked=tk.resolveViews(pattern);
    rows.push(witness("element.toolkit.view-resolver", "toolkit-public-capability-and-primitive-gates", {
      completeEligible:completeResult.eligible===true,
      missingCapabilityRefused:noCapabilityResult.eligible===false,
      missingPrimitiveRefused:noPrimitiveResult.eligible===false,
      explicitExclusionRefused:blockedResult.eligible===false,
      rejectionReasonConsistent:noPrimitiveResult.eligible===false &&
        typeof noPrimitiveResult.reason==="string" &&
        noPrimitiveResult.reason.toLowerCase().includes("primitive"),
      rankedViewsWellFormed:ranked.length>0 && ranked.every(x=>typeof x.eligible==="boolean")
    }, {missingPrimitiveResult:noPrimitiveResult,
      limitation:"Pure resolver only; does not certify selected view rendering"}));
    return {schema:"mcel.core-toolkit.public-boundary.v1", roster:[...CORE_PARENTS,...TOOLKIT_IDS],
      elements: rows, status: rows.some(r=>r.status==="FAIL") ? "FAIL" : "PASS",
      confirmedBoundaryDefects:rows.filter(r=>r.status==="FAIL").map(r=>({id:r.id,scope:r.scope,
        failedObservations:r.failedObservations,details:r.details}))};
  }
  global.McelAcidCoreToolkit = {run, CORE_PARENTS:[...CORE_PARENTS],TOOLKIT_IDS:[...TOOLKIT_IDS]};
})(window);
