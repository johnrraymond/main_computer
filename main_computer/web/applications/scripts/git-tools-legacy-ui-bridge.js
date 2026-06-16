(function (global) {
  "use strict";

  const VERSION = "0.2.0";
  const SURFACE_ID = "git-tools.legacy-ui-bridge";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-legacy-ui-bridge.js";

  function gitToolsLegacyBridgeModules() {
    return Object.freeze({
      projectShared: global.GitToolsProjectShared || null,
      commitWorkbench: global.GitToolsCommitWorkbench || null,
      secretsFilterWorkbench: global.GitToolsSecretsFilterWorkbench || null,
      archiveWorkbench: global.GitToolsArchiveWorkbench || null,
      projectCardSubscreen: global.GitToolsProjectCardSubscreen || null,
      projectWizardRendering: global.GitToolsProjectWizardRendering || null,
      statusRefreshBridge: global.GitToolsStatusRefreshBridge || null,
      pageWizard: global.GitToolsPageWizard || null,
      shimConsole: global.GitToolsShimConsole || null,
    });
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
    modules: gitToolsLegacyBridgeModules,
    readiness: gitToolsLegacyBridgeReadiness,
  });
})(window);
