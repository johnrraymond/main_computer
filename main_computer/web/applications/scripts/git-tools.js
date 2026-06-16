(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.entrypoint";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools.js";

  function gitToolsEntrypointModules() {
    return Object.freeze({
      statusApi: global.GitToolsStatusApi || null,
      fileBasket: global.GitToolsFileBasket || null,
      projectWorkflow: global.GitToolsProjectWorkflow || null,
      serverPanel: global.GitToolsServerPanel || null,
      projectPanel: global.GitToolsProjectPanel || null,
      patchInventory: global.GitToolsPatchInventory || null,
      gitignoreWorkbench: global.GitToolsGitignoreWorkbench || null,
    });
  }

  function gitToolsEntrypointReadiness() {
    const modules = gitToolsEntrypointModules();
    return Object.freeze({
      statusApi: Boolean(modules.statusApi),
      fileBasket: Boolean(modules.fileBasket),
      projectWorkflow: Boolean(modules.projectWorkflow),
      serverPanel: Boolean(modules.serverPanel),
      projectPanel: Boolean(modules.projectPanel),
      patchInventory: Boolean(modules.patchInventory),
      gitignoreWorkbench: Boolean(modules.gitignoreWorkbench),
    });
  }

  function bindGitToolsControl(control, eventName, handler) {
    if (!control) return;
    control.addEventListener(eventName, handler);
  }

  function initGitToolsApp() {
    if (gitToolsInitialized) {
      refreshGitTools();
      return;
    }
    gitToolsInitialized = true;
    bindGitToolsControl(gitStatusRefresh, "click", refreshGitStatus);
    bindGitToolsControl(gitPatchesRefresh, "click", refreshGitPatches);
    bindGitToolsControl(gitProjectAdd, "click", addGitProjectFromInput);
    bindGitToolsControl(gitProjectRescan, "click", inspectSelectedGitProject);
    bindGitToolsControl(gitProjectLock, "click", () => setSelectedGitProjectLock(true));
    bindGitToolsControl(gitProjectUnlock, "click", () => setSelectedGitProjectLock(false));
    bindGitToolsControl(gitPatchPreview, "click", previewGitPatch);
    bindGitToolsControl(gitPatchDryRun, "click", runGitPatchDryRun);
    bindGitToolsControl(gitDryRunRefresh, "click", loadGitDryRun);
    bindGitToolsControl(gitConsoleRun, "click", runGitConsoleCommand);
    bindGitToolsControl(gitConsoleExtract, "click", extractGitConsoleShims);
    bindGitToolsControl(gitAiShim, "click", askGitAiForShim);
    bindGitToolsControl(gitControlPlan, "click", createGitPlanShim);
    bindGitToolsControl(gitShimView, "click", viewGitShim);
    bindGitToolsControl(gitShimRun, "click", runGitShim);
    bindGitToolsControl(gitShimOrdain, "click", () => setGitShimOrdination(true));
    bindGitToolsControl(gitShimUnordain, "click", () => setGitShimOrdination(false));
    bindGitToolsControl(gitShimDelete, "click", deleteGitShim);
    bindGitToolsControl(gitPageWizardNext, "click", advanceGitPageWizard);
    bindGitToolsControl(gitPageWizardReset, "click", resetGitPageWizard);
    bindGitToolsControl(gitPageWizardSendConsole, "click", sendGitPageWizardToConsole);
    bindGitToolsControl(gitServerStatusRefresh, "click", refreshGitServerStatus);
    bindGitToolsControl(gitServerStart, "click", () => runGitServerAction("start"));
    bindGitToolsControl(gitServerRestart, "click", () => runGitServerAction("restart"));
    bindGitToolsControl(gitServerStop, "click", () => runGitServerAction("stop"));
    bindGitToolsControl(gitServerLogs, "click", () => runGitServerAction("logs"));
    gitServerRemotePresetButtons.forEach((button) => {
      bindGitToolsControl(button, "click", () => fillGitServerRemoteCommand(button.dataset.gitServerRemotePreset || ""));
    });
    bindGitToolsControl(gitServerUseLocal, "click", useLocalGitServerRemote);
    bindGitToolsControl(gitServerRemoteApplyLocal, "click", applyLocalGitServerRemote);
    bindGitToolsControl(gitServerPushLocal, "click", pushLocalGitServerRemote);
    bindGitToolsControl(gitServerOperationCancel, "click", cancelGitServerOperation);
    bindGitToolsControl(gitServerOperationRefresh, "click", () => refreshGitOperationStatus({renderOutput: true}));
    bindGitToolsControl(gitServerUseExternal, "click", useExternalGitRemoteDirect);
    bindGitToolsControl(gitServerMirrorPlan, "click", planGiteaPushMirror);
    bindGitToolsControl(gitServerMirrorSetup, "click", setupGiteaPushMirror);
    bindGitToolsControl(gitServerRemoteMode, "change", updateGitServerRemoteMode);
    bindGitToolsControl(gitServerRemoteRun, "click", runGitServerRemoteCommand);
    bindGitToolsControl(gitServerRemoteCopyConsole, "click", copyGitServerRemoteCommandToConsole);
    if (gitPageWizardInput) {
      gitPageWizardInput.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
          event.preventDefault();
          advanceGitPageWizard();
        }
      });
    }
    initializeGitWorkflowDisclosure();
    initializeGitServerHiddenPane();
    initializeGitServerRemoteComposer();
    refreshGitOperationStatus({renderOutput: false}).catch(() => null);
    renderGitPageWizard();
    loadGitProjects().catch(() => null);
    refreshGitTools();
  }

  global.bindGitToolsControl = bindGitToolsControl;
  global.initGitToolsApp = initGitToolsApp;
  global.GitToolsEntrypoint = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    modules: gitToolsEntrypointModules,
    readiness: gitToolsEntrypointReadiness,
    bindControl: bindGitToolsControl,
    init: initGitToolsApp,
  });
})(window);
