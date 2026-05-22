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

    const restartGameSurface = document.querySelector("#restart-gl");
    const pauseGameSurfaceButton = document.querySelector("#pause-gl");
    const resumeGameSurface = document.querySelector("#resume-gl");
    if (restartGameSurface) {
      restartGameSurface.addEventListener("click", initWebgl);
    }
    if (pauseGameSurfaceButton) {
      pauseGameSurfaceButton.addEventListener("click", () => {
        pauseGameSurface();
        glStatus.textContent = "game surface empty";
      });
    }
    if (resumeGameSurface) {
      resumeGameSurface.addEventListener("click", initWebgl);
    }
    window.addEventListener("resize", () => {
      fitXterm();
      layoutDesktopIcons(currentApp);
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh({delay: 60});
    });
    window.addEventListener("popstate", () => {
      const nextApp = applicationFromPath(window.location.pathname);
      setActiveApp(nextApp, {syncRoute: false});
      if (nextApp === "task-manager") {
        setTaskNotebookTab(taskNotebookTabFromPath(window.location.pathname), {syncRoute: false});
      }
    });
