(function (global) {
  "use strict";

  const VERSION = "0.2.1";
  const SURFACE_ID = "git-tools.legacy-ui-bridge";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-legacy-ui-bridge.js";
  const ROLE = "post-split shared glue and readiness only";
  const MODULE_GLOBALS = Object.freeze({
    projectShared: "GitToolsProjectShared",
    commitWorkbench: "GitToolsCommitWorkbench",
    secretsFilterWorkbench: "GitToolsSecretsFilterWorkbench",
    archiveWorkbench: "GitToolsArchiveWorkbench",
    projectCardSubscreen: "GitToolsProjectCardSubscreen",
    projectWizardRendering: "GitToolsProjectWizardRendering",
    statusRefreshBridge: "GitToolsStatusRefreshBridge",
    pageWizard: "GitToolsPageWizard",
    shimConsole: "GitToolsShimConsole",
  });

  function gitToolsLegacyBridgeModules() {
    return Object.freeze(Object.fromEntries(
      Object.entries(MODULE_GLOBALS).map(([key, globalName]) => [key, global[globalName] || null])
    ));
  }

  function gitToolsLegacyBridgeReadiness() {
    const modules = gitToolsLegacyBridgeModules();
    return Object.freeze(Object.fromEntries(
      Object.entries(modules).map(([key, value]) => [key, Boolean(value)])
    ));
  }

  global.GitToolsLegacyUiBridge = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    role: ROLE,
    moduleGlobals: MODULE_GLOBALS,
    modules: gitToolsLegacyBridgeModules,
    readiness: gitToolsLegacyBridgeReadiness,
  });
})(window);
