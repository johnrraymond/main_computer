/* MCEL application presentation registry — policy-to-controller handoff.
 * Shared MCEL hosts pre-load the controller and all presentation laws.
 * The registry determines defaults and sparse per-app overrides, not styling.
 */
(function(global) {
  "use strict";
  if (global.McelApplicationPresentationRegistry) return;
  function connect(request) {
    try {
      const node = global.document.getElementById("mcel-application-presentation-selection");
      const selection = JSON.parse(node?.textContent || "null");
      const appId = request?.appId;
      const root = global.document.getElementById(request?.rootId);
      if (!root || !selection || selection.schema !== "mcel.presentation-selection.v1" ||
          selection.appId !== appId || !selection.eligible || !selection.profiles) {
        return Object.freeze({status: "registry-unavailable", appId: appId || null});
      }
      // Stage 2 does not change the original mount.  It only prepares a
      // shared lifecycle record for Theme, Form, and Chrome.  If the optional
      // controller is missing/refuses, the successful app mount is unaffected.
      let coordination = null;
      try {
        coordination = global.McelUnifiedPresentationController?.prepare({
          root, appId: selection.appId, profiles: selection.profiles,
          rolloutEnabled: selection.rolloutEnabled === true, surface: "deployed"
        });
      } catch (_) { /* Optional preparation never blocks an app. */ }
      if (selection.rolloutEnabled && !global.__mcelPresentationReport &&
          !root.hasAttribute("data-mcel-semantic-operational") &&
          typeof global.McelUnifiedPresentationController?.applyNativePresentation === "function") {
        for (const node of global.document.querySelectorAll('script[type="application/json"]')) {
          try {
            const ir=JSON.parse(node.textContent||"null");
            if (ir?.schema === "mcel.application-ir.v1" && ir.application?.appId) {
              global.__mcelPresentationReport=global.McelUnifiedPresentationController.applyNativePresentation({
                root,ir,appId:selection.appId,profiles:selection.profiles});
              break;
            }
          } catch (_) { /* Not a canonical application IR; ignore. */ }
        }
      }
      return Object.freeze({
        status: global.__mcelPresentationReport?.status || "resolved-no-op",
        appId: selection.appId,
        profiles: Object.freeze({...selection.profiles}),
        rolloutEnabled: selection.rolloutEnabled === true,
        controllerStatus: coordination?.status || "controller-unavailable",
        applied: global.__mcelPresentationReport?.status === "applied",
        application: global.__mcelPresentationReport || null
      });
    } catch (error) {
      return Object.freeze({status: "registry-unavailable", reason: String(error)});
    }
  }
  Object.defineProperty(global, "McelApplicationPresentationRegistry", {
    value: Object.freeze({connect}), writable: false, configurable: false
  });
})(window);
