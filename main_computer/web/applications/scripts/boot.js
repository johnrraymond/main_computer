function bindApplicationShellControls() {
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
}

bindApplicationShellControls();
setActiveApp(applicationFromPath(window.location.pathname), {replaceRoute: true});
